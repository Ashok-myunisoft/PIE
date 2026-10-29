"""PIE activity log: every discovery, extraction, mapping, Qdrant ingestion, query and error.

Each CLI command and each chat/search request is one *run*: activity category, command,
module/screen, status (RUNNING -> SUCCESS / WARNING / FAILED), UTC start/finish time,
duration, record counts taken from the command's result, parameters (secrets masked),
error type/message/traceback, and progress *events*. A chat run also stores its answer
trace (plan, every retrieved knowledge item, citations) as an event. Runs are stored in
SQLite and also appended to a rotating JSON-lines file. Qdrant storage status and the
module/screen/field knowledge inventory are snapshotted into their own tables.

With PIE_LOG_REMOTE set (https://<server>/logs/api/ingest), runs made on another machine
are also sent to the server; runs that cannot be sent wait in a spool file.
Logging never breaks the work it records. Standard library only.

    python -m pie_agent.activity_log summary | tail | show <run> | qdrant-status | inventory | prune | flush-remote
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from .paths import from_root

# CLI command -> activity category shown on the dashboard.
ACTIVITIES = {
    "discover": "module_discovery", "discover-erp": "module_discovery",
    "screen-identity": "screen_discovery",
    "fields-build": "field_discovery", "fields-build-module": "field_discovery",  # "field_knowledge" with --enrich
    "run": "knowledge_extraction", "run-golden": "knowledge_extraction", "generate": "knowledge_extraction",
    "generate-erp": "knowledge_extraction", "generate-ai-knowledge": "knowledge_extraction",
    "capability-model": "knowledge_extraction", "usage-build": "knowledge_extraction",
    "map-api": "evidence_mapping", "map-db": "evidence_mapping", "build-evidence-graph": "evidence_mapping",
    "kms-init": "qdrant_ingestion", "kms-publish": "qdrant_ingestion", "kms-publish-usage": "qdrant_ingestion",
    "kms-publish-fields": "qdrant_ingestion", "kms-import-tickets": "qdrant_ingestion", "kms-publish-json": "qdrant_ingestion",
    "kms-stats": "qdrant_status", "kms-search": "retrieval", "chat": "retrieval",
    "usage-export": "export", "usage-export-chat": "export", "fields-export-html": "export",
    "kms-migrate": "qdrant_ingestion", "code-index": "qdrant_ingestion", "kms-publish-business": "qdrant_ingestion",
    "docs-ingest": "qdrant_ingestion", "business-build": "knowledge_extraction",
}
# After these commands the knowledge inventory / Qdrant storage status is refreshed automatically.
INVENTORY_AFTER = {"map-api", "map-db", "build-evidence-graph", "usage-build", "fields-build", "fields-build-module",
                   "kms-publish-usage", "kms-publish-fields"}
QDRANT_AFTER = {"kms-init", "kms-publish", "kms-publish-usage", "kms-publish-fields", "kms-import-tickets"}
# A "status" value inside a finished result that makes the run WARNING instead of SUCCESS.
WARN_STATUSES = {"ERROR", "FAILED", "NOT_CONFIGURED", "UNREACHABLE", "PARTIAL", "RED", "NOTHING_TO_IMPORT"}
ATTR_KEYS = {"status", "module", "screen", "intent", "generator", "planner", "collection", "source", "table", "entity"}
SECRET_WORDS = ("key", "password", "passwd", "pwd", "secret", "token", "database-url", "database_url")
RUN_COLUMNS = ("run_id", "parent_id", "activity", "command", "source", "module", "screen", "status", "started_at",
               "finished_at", "duration_ms", "counts", "attrs", "params", "error_type", "error_message", "traceback",
               "host", "pid")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, parent_id TEXT, activity TEXT NOT NULL, command TEXT, source TEXT,
  module TEXT, screen TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  duration_ms INTEGER, counts TEXT, attrs TEXT, params TEXT, error_type TEXT, error_message TEXT,
  traceback TEXT, host TEXT, pid INTEGER);
CREATE INDEX IF NOT EXISTS ix_runs_started ON runs (started_at);
CREATE INDEX IF NOT EXISTS ix_runs_activity ON runs (activity, status);
CREATE INDEX IF NOT EXISTS ix_runs_module ON runs (module);
CREATE INDEX IF NOT EXISTS ix_runs_parent ON runs (parent_id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT NOT NULL, level TEXT, message TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id);
CREATE TABLE IF NOT EXISTS api_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, method TEXT, path TEXT, status INTEGER,
  duration_ms INTEGER, client TEXT);
CREATE INDEX IF NOT EXISTS ix_api_ts ON api_requests (ts);
CREATE TABLE IF NOT EXISTS qdrant_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, collection TEXT, status TEXT, points INTEGER,
  indexed_vectors INTEGER, segments INTEGER, by_type TEXT, by_module TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS module_inventory (
  module TEXT PRIMARY KEY, stats TEXT, db_source TEXT, field_screens INTEGER, fields INTEGER,
  qdrant_points INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS knowledge_inventory (
  module TEXT, form TEXT, screen TEXT, screen_id TEXT, entity TEXT, tbl TEXT, backend_found INTEGER,
  sections INTEGER, fields INTEGER, unique_fields INTEGER, mandatory INTEGER, mapped_to_column INTEGER,
  explained INTEGER, flagged INTEGER, generated_at TEXT, qdrant_fields INTEGER, qdrant_sections INTEGER,
  updated_at TEXT, PRIMARY KEY (module, form));
"""

HOST = socket.gethostname()
_CURRENT: contextvars.ContextVar["Run | None"] = contextvars.ContextVar("pie_activity_run", default=None)
_LOCK = threading.Lock()
_JSONL_LOCK = threading.Lock()
_READY: set[str] = set()
_JSONL = logging.getLogger("pie.activity")
_warned = False


# ---- storage -----------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _since(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_dir() -> Path:
    return from_root(os.getenv("PIE_LOG_DIR") or "outputs/logs")


def db_path() -> Path:
    return from_root(os.environ["PIE_LOG_DB"]) if os.getenv("PIE_LOG_DB") else log_dir() / "pie_activity.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 15000")
    if str(path) not in _READY:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)
        _READY.add(str(path))
    return con


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    con = _connect()
    try:
        with con:  # commit, or roll back on error
            yield con
    finally:
        con.close()


def _rows(sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
    with _db() as con:
        return [dict(r) for r in con.execute(sql, args)]


def _quiet(fn):
    """Logging must never break the pipeline: report the first failure on stderr, then carry on."""
    def wrapper(*args, **kwargs):
        global _warned
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _warned:
                _warned = True
                print(f"[pie-log] activity log write failed ({db_path()}): {type(exc).__name__}: {exc}", file=sys.stderr)
            return None
    wrapper.__name__ = fn.__name__
    return wrapper


def _dumps(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    for k in ("counts", "attrs", "params", "data", "by_type", "by_module", "stats"):
        if isinstance(row.get(k), str):
            try:
                row[k] = json.loads(row[k])
            except ValueError:
                pass
    return row


@_quiet
def _jsonl(kind: str, record: dict[str, Any]) -> None:
    with _JSONL_LOCK:
        if not _JSONL.handlers:
            path = log_dir() / "pie_activity.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=int(os.getenv("PIE_LOG_MAX_MB", "20")) * 1024 * 1024,
                                          backupCount=int(os.getenv("PIE_LOG_BACKUPS", "10")), encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            _JSONL.addHandler(handler)
            _JSONL.setLevel(logging.INFO)
            _JSONL.propagate = False
    _JSONL.info(json.dumps({"kind": kind, **record}, ensure_ascii=False, default=str))


def _write_run(con: sqlite3.Connection, rec: dict[str, Any]) -> None:
    row = [_dumps(rec.get(c)) if c in ("counts", "attrs", "params") else rec.get(c) for c in RUN_COLUMNS]
    con.execute(f"INSERT OR REPLACE INTO runs ({', '.join(RUN_COLUMNS)}) VALUES ({', '.join('?' * len(RUN_COLUMNS))})", row)


@_quiet
def _save_run(rec: dict[str, Any]) -> None:
    with _LOCK, _db() as con:
        _write_run(con, rec)


@_quiet
def _save_event(ev: dict[str, Any]) -> None:
    with _LOCK, _db() as con:
        con.execute("INSERT INTO events (run_id, ts, level, message, data) VALUES (?, ?, ?, ?, ?)",
                    (ev["run_id"], ev["ts"], ev["level"], ev["message"], _dumps(ev.get("data"))))


@_quiet
def log_api_request(method: str, path: str, status: int, duration_ms: int, client: str = "") -> None:
    with _LOCK, _db() as con:
        con.execute("INSERT INTO api_requests (ts, method, path, status, duration_ms, client) VALUES (?, ?, ?, ?, ?, ?)",
                    (_now(), method, path[:300], int(status), int(duration_ms), (client or "")[:64]))


# ---- runs --------------------------------------------------------------------------------

def _secret(name: str) -> bool:
    n = name.lower().lstrip("-")
    return any(w in n for w in SECRET_WORDS)


def redact(value: Any) -> Any:
    """Mask secrets in CLI arguments or request parameters (API keys, passwords, database URLs)."""
    if isinstance(value, dict):
        return {k: ("***" if _secret(str(k)) and v else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        out, hide = [], False
        for item in value:
            s = str(item)
            if hide:
                out.append("***")
                hide = False
            elif s.startswith("--") and _secret(s.split("=", 1)[0]):
                if "=" in s:
                    out.append(s.split("=", 1)[0] + "=***")
                else:
                    out.append(s)
                    hide = True
            else:
                out.append(redact(item))
        return out
    if isinstance(value, str):
        return re.sub(r"(://)[^/@\s]+@", r"\1***@", value)  # user:password@ in URLs
    return value


def _flatten(obj: Any, prefix: str = "", depth: int = 0, counts: dict | None = None, attrs: dict | None = None):
    """Numbers and list lengths -> counts; status/module/screen-like strings -> attrs (dotted keys)."""
    counts = {} if counts is None else counts
    attrs = {} if attrs is None else attrs
    if isinstance(obj, dict) and depth <= 3:
        for k, v in obj.items():
            key = f"{prefix}{k}"
            if isinstance(v, bool):
                attrs[key] = v
            elif isinstance(v, (int, float)):
                counts[key] = v
            elif isinstance(v, str):
                if str(k) in ATTR_KEYS:
                    attrs[key] = v[:300]
            elif isinstance(v, (list, tuple)):
                counts[key] = len(v)
            elif isinstance(v, dict):
                _flatten(v, key + ".", depth + 1, counts, attrs)
    return counts, attrs


class Run:
    """One tracked activity. Created by track(); add counts, attrs and events while it runs."""

    def __init__(self, activity: str, command: str | None = None, source: str = "cli", module: str | None = None,
                 screen: str | None = None, params: Any = None):
        parent = _CURRENT.get()
        self.run_id = uuid.uuid4().hex
        self.parent_id = parent.run_id if parent else None
        self.activity = activity
        self.command = command or activity
        self.source = source
        self.module = module or (parent.module if parent else None)
        self.screen = screen
        self.status = "RUNNING"
        self.started_at = _now()
        self.finished_at: str | None = None
        self.duration_ms: int | None = None
        self.counts: dict[str, Any] = {}
        self.attrs: dict[str, Any] = {}
        self.params = redact(params)
        self.error_type: str | None = None
        self.error_message: str | None = None
        self.traceback: str | None = None
        self.host, self.pid = HOST, os.getpid()
        self.events: list[dict[str, Any]] = []
        self._t0 = time.perf_counter()

    def count(self, **values: Any) -> "Run":
        for k, v in values.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self.counts[k] = v
        return self

    def attr(self, **values: Any) -> "Run":
        self.attrs.update({k: v for k, v in values.items() if v is not None})
        return self

    def result(self, obj: Any) -> "Run":
        """Take record counts and status/module/screen values from a command's result."""
        if isinstance(obj, (list, tuple)):
            obj = {"results": obj}
        counts, attrs = _flatten(obj)
        for k in list(counts)[:200]:
            self.counts[k] = counts[k]
        self.attrs.update(attrs)
        if not self.module and isinstance(attrs.get("module"), str):
            self.module = attrs["module"]
        if not self.screen and isinstance(attrs.get("screen"), str):
            self.screen = attrs["screen"]
        return self

    def event(self, message: Any, level: str = "INFO", **data: Any) -> None:
        ev = {"run_id": self.run_id, "ts": _now(), "level": level.upper(), "message": str(message)[:2000], "data": data or None}
        if len(self.events) < 2000:
            self.events.append(ev)
        _save_event(ev)
        _jsonl("event", {**ev, "activity": self.activity, "module": self.module})

    def record(self) -> dict[str, Any]:
        return {c: getattr(self, c) for c in RUN_COLUMNS}

    def _finish(self, exc: BaseException | None = None) -> None:
        self.finished_at = _now()
        self.duration_ms = int((time.perf_counter() - self._t0) * 1000)
        if exc is not None:
            self.status = "FAILED"
            self.error_type = type(exc).__name__
            self.error_message = (str(exc) or type(exc).__name__)[:4000]
            self.traceback = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-20000:]
        else:
            warnings = [f"{k}={v}" for k, v in self.attrs.items()
                        if k.rsplit(".", 1)[-1] == "status" and str(v).upper() in WARN_STATUSES]
            if warnings:
                self.attrs["warnings"] = warnings
            self.status = "WARNING" if warnings else "SUCCESS"
        rec = self.record()
        _save_run(rec)
        _jsonl("run", rec)
        _ship(rec, self.events)


@contextmanager
def track(activity: str, command: str | None = None, source: str = "cli", module: str | None = None,
          screen: str | None = None, params: Any = None) -> Iterator[Run]:
    """Record one activity: RUNNING on entry, then SUCCESS / WARNING / FAILED. Exceptions are re-raised."""
    run = Run(activity, command, source, module, screen, params)
    _save_run(run.record())
    token = _CURRENT.set(run)
    try:
        yield run
    except SystemExit as exc:  # argparse errors, sys.exit()
        if exc.code in (None, 0):
            run._finish()
        else:
            run.attr(exitCode=exc.code)
            run._finish(exc)
        raise
    except BaseException as exc:
        run._finish(exc)
        raise
    else:
        run._finish()
    finally:
        _CURRENT.reset(token)


def current() -> Run | None:
    return _CURRENT.get()


def record_result(obj: Any) -> None:
    run = _CURRENT.get()
    if run:
        run.result(obj)


def log_event(message: Any, level: str = "INFO", **data: Any) -> None:
    run = _CURRENT.get()
    if run:
        run.event(message, level, **data)
    else:
        _jsonl("event", {"run_id": None, "ts": _now(), "level": level.upper(), "message": str(message)[:2000], "data": data or None})


# ---- remote shipping (CLI on another PC -> server) ---------------------------------------

def _spool_path() -> Path:
    return log_dir() / "remote_spool.jsonl"


def _post(url: str, body: dict[str, Any]) -> bool:
    headers = {"Content-Type": "application/json"}
    if os.getenv("PIE_LOG_TOKEN"):
        headers["X-PIE-Log-Token"] = os.environ["PIE_LOG_TOKEN"]
    req = urllib.request.Request(url, data=json.dumps(body, default=str).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(os.getenv("PIE_LOG_REMOTE_TIMEOUT", "5"))) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def flush_remote(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send spooled runs (plus `extra`) to PIE_LOG_REMOTE; what cannot be sent stays in the spool."""
    url = os.getenv("PIE_LOG_REMOTE", "").strip()
    spool = _spool_path()
    pending: list[dict[str, Any]] = []
    if spool.exists():
        for line in spool.read_text(encoding="utf-8").splitlines():
            try:
                pending.append(json.loads(line))
            except ValueError:
                pass
    if extra is not None:
        pending.append(extra)
    sent = 0
    if url:
        for item in pending:
            if not _post(url, item):
                break
            sent += 1
    left = pending[sent:]
    if left:
        spool.parent.mkdir(parents=True, exist_ok=True)
        spool.write_text("".join(json.dumps(x, default=str) + "\n" for x in left[-5000:]), encoding="utf-8")
    elif spool.exists():
        spool.unlink()
    return {"remote": url or None, "sent": sent, "pending": len(left)}


@_quiet
def _ship(rec: dict[str, Any], events: list[dict[str, Any]]) -> None:
    if os.getenv("PIE_LOG_REMOTE", "").strip():
        flush_remote({"run": rec, "events": events[-500:]})


def ingest_remote(body: dict[str, Any]) -> str:
    """Store a run sent by another machine (POST /logs/api/ingest). Sending the same run again replaces it."""
    run = body["run"]
    rec = {c: run.get(c) for c in RUN_COLUMNS}
    if not (isinstance(rec["run_id"], str) and re.fullmatch(r"[0-9a-f]{32}", rec["run_id"])):
        raise ValueError("run_id must be 32 hex characters")
    if not rec["activity"] or not rec["started_at"]:
        raise ValueError("activity and started_at are required")
    rec["status"] = rec["status"] or "SUCCESS"
    rec["attrs"] = {**(rec["attrs"] if isinstance(rec["attrs"], dict) else {}), "receivedAt": _now()}
    for k, n in (("error_message", 4000), ("traceback", 20000)):
        if rec[k]:
            rec[k] = str(rec[k])[-n:]
    events = [e for e in (body.get("events") or [])[:2000] if isinstance(e, dict)]
    with _LOCK, _db() as con:
        _write_run(con, rec)
        con.execute("DELETE FROM events WHERE run_id = ?", (rec["run_id"],))
        con.executemany("INSERT INTO events (run_id, ts, level, message, data) VALUES (?, ?, ?, ?, ?)",
                        [(rec["run_id"], e.get("ts") or rec["started_at"], str(e.get("level") or "INFO")[:10],
                          str(e.get("message") or "")[:2000], _dumps(e.get("data"))) for e in events])
    return rec["run_id"]


# ---- Qdrant storage status ---------------------------------------------------------------

def _qdrant_client(url: str | None = None, api_key: str | None = None, collection: str | None = None):
    from .db_discovery import load_dotenv
    from .qdrant_kms import QdrantHTTP
    load_dotenv()
    client = QdrantHTTP(url or os.getenv("QDRANT_URL", "http://localhost:6333"),
                        api_key if api_key is not None else os.getenv("QDRANT_API_KEY", ""), timeout=20)
    return client, collection or os.getenv("QDRANT_COLLECTION", "pie_knowledge")


def _match(**values: str) -> dict[str, Any]:
    return {"must": [{"key": k, "match": {"value": v}} for k, v in values.items()]}


def _facet(client, collection: str, key: str, flt: dict | None = None, limit: int = 1000) -> dict[str, int]:
    body: dict[str, Any] = {"key": key, "limit": limit, "exact": True}
    if flt:
        body["filter"] = flt
    res = client._request("POST", f"/collections/{collection}/facet", body).get("result") or {}
    return {str(h["value"]): int(h["count"]) for h in res.get("hits", [])}


def _count(client, collection: str, flt: dict | None = None) -> int:
    body: dict[str, Any] = {"exact": True}
    if flt:
        body["filter"] = flt
    return int((client._request("POST", f"/collections/{collection}/points/count", body).get("result") or {}).get("count", 0))


@_quiet
def _save_qdrant(s: dict[str, Any]) -> None:
    with _LOCK, _db() as con:
        con.execute("INSERT INTO qdrant_status (ts, collection, status, points, indexed_vectors, segments, by_type, by_module, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (s["ts"], s["collection"], s["status"], s["points"], s["indexed_vectors"], s["segments"],
                     _dumps(s["by_type"]), _dumps(s["by_module"]), s["error"]))


def qdrant_snapshot(url: str | None = None, api_key: str | None = None, collection: str | None = None,
                    source: str = "cli") -> dict[str, Any]:
    """Collection status, point count, and points per knowledge type and per module (x type)."""
    client, collection = _qdrant_client(url, api_key, collection)
    with track("qdrant_status", command="qdrant-status", source=source,
               params={"collection": collection, "url": client.url}) as run:
        snap: dict[str, Any] = {"ts": _now(), "collection": collection, "status": "UNREACHABLE", "points": None,
                                "indexed_vectors": None, "segments": None, "by_type": {}, "by_module": {}, "error": None}
        try:
            info = client.collection(collection).get("result") or {}
            snap.update(status=str(info.get("status", "unknown")).upper(), points=info.get("points_count"),
                        indexed_vectors=info.get("indexed_vectors_count"), segments=info.get("segments_count"))
            snap["by_type"] = _facet(client, collection, "knowledge_type")
            for module, n in _facet(client, collection, "module").items():
                snap["by_module"][module] = {"points": n, "types": _facet(client, collection, "knowledge_type", _match(module=module))}
        except Exception as exc:
            snap["error"] = f"{type(exc).__name__}: {exc}"[:2000]
        _save_qdrant(snap)
        run.count(points=snap["points"] or 0, modules=len(snap["by_module"]), knowledgeTypes=len(snap["by_type"]), **{
            f"type.{k}": v for k, v in snap["by_type"].items()})
        run.attr(status=snap["status"], collection=collection, error=snap["error"])
    return snap


def qdrant_history(limit: int = 48) -> dict[str, Any]:
    rows = [_decode(r) for r in _rows("SELECT * FROM qdrant_status ORDER BY id DESC LIMIT ?", (limit,))]
    return {"latest": rows[0] if rows else None,
            "history": [{k: r[k] for k in ("ts", "status", "points", "error")} for r in rows]}


# ---- module / screen / section / field knowledge inventory -------------------------------

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _numbers(d: Any, prefix: str) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    return {f"{prefix}.{k}": v for k, v in d.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def refresh_inventory(modules_root: str | Path = "outputs/modules", qdrant: bool = True, url: str | None = None,
                      api_key: str | None = None, collection: str | None = None, source: str = "cli") -> dict[str, Any]:
    """Per module and per screen: what was discovered, mapped and generated, and what is stored in Qdrant."""
    root = from_root(modules_root)
    with track("knowledge_inventory", command="inventory", source=source, params={"modulesRoot": str(root)}) as run:
        client = None
        if qdrant:
            client, collection = _qdrant_client(url, api_key, collection)
        now = _now()
        modules: list[dict[str, Any]] = []
        screens: list[dict[str, Any]] = []
        for mdir in sorted(p for p in root.glob("*") if p.is_dir()):
            m = mdir.name
            api = _read_json(mdir / "phase14-api-mapping" / "api_mapping_report_v14.json") or {}
            db = _read_json(mdir / "phase15-db-mapping" / "db_mapping_report_v15.json") or {}
            usage = _read_json(mdir / "phase19-usage" / "usage_catalog_v19.json") or {}
            summ = _read_json(mdir / "phase20-fields" / "module_fields_summary_v20.json") or {}
            stats = {**_numbers(api.get("stats"), "api"), **_numbers(db.get("stats"), "db"),
                     **_numbers(usage.get("statistics"), "usage"), **_numbers(summ.get("totals"), "fields")}
            q_ok, q_fields, q_sections, q_points = False, {}, {}, None
            if client:
                try:
                    q_fields = _facet(client, collection, "screen_id", _match(module=m, knowledge_type="FieldKnowledge"), 5000)
                    q_sections = _facet(client, collection, "screen_id", _match(module=m, knowledge_type="SectionKnowledge"), 5000)
                    q_points = _count(client, collection, _match(module=m))
                    q_ok = True
                except Exception as exc:
                    run.event(f"Qdrant counts for {m} failed: {exc}", "WARNING")
            mod_screens = []
            for f in sorted((mdir / "phase20-fields").glob("*_fields_v20.json")):
                d = _read_json(f)
                if not isinstance(d, dict) or not isinstance(d.get("fields"), list):
                    continue
                st = d.get("statistics") or {}
                sid = d.get("screenId")
                # Same grouping as knowledge_ingest.section_points: (tab, section) of visible fields.
                where = {(r.get("page") or "", r.get("section") or "") for r in d["fields"]
                         if isinstance(r, dict) and r.get("visibleOnScreen")}
                mod_screens.append({
                    "module": m, "form": d.get("form") or f.name[: -len("_fields_v20.json")], "screen": d.get("screen"),
                    "screen_id": sid, "entity": d.get("entity"), "tbl": d.get("table"),
                    "backend_found": int(bool(d.get("backendFound"))), "sections": len(where),
                    "fields": st.get("fields", len(d["fields"])), "unique_fields": st.get("uniqueFields"),
                    "mandatory": st.get("mandatory"), "mapped_to_column": st.get("mappedToColumn"),
                    "explained": st.get("explained"), "flagged": st.get("explanationsFlagged"),
                    "generated_at": d.get("generatedAt"),
                    "qdrant_fields": q_fields.get(sid, 0) if q_ok else None,
                    "qdrant_sections": q_sections.get(sid, 0) if q_ok else None, "updated_at": now})
            screens += mod_screens
            modules.append({"module": m, "stats": stats, "db_source": (db.get("database") or {}).get("source"),
                            "field_screens": len(mod_screens), "fields": sum(s["fields"] or 0 for s in mod_screens),
                            "qdrant_points": q_points, "updated_at": now})
        with _LOCK, _db() as con:
            con.execute("DELETE FROM module_inventory")
            con.execute("DELETE FROM knowledge_inventory")
            con.executemany("INSERT INTO module_inventory VALUES (:module, :stats, :db_source, :field_screens, :fields, "
                            ":qdrant_points, :updated_at)", [{**x, "stats": _dumps(x["stats"])} for x in modules])
            con.executemany("INSERT OR REPLACE INTO knowledge_inventory VALUES (:module, :form, :screen, :screen_id, :entity, "
                            ":tbl, :backend_found, :sections, :fields, :unique_fields, :mandatory, :mapped_to_column, "
                            ":explained, :flagged, :generated_at, :qdrant_fields, :qdrant_sections, :updated_at)", screens)
        run.count(modules=len(modules), screens=len(screens), sections=sum(s["sections"] for s in screens),
                  fields=sum(s["fields"] or 0 for s in screens),
                  qdrantFields=sum(s["qdrant_fields"] or 0 for s in screens),
                  screensMissingInQdrant=sum(1 for s in screens if s["qdrant_fields"] == 0))
    return {"modules": len(modules), "screens": len(screens), "updatedAt": now}


def inventory() -> dict[str, Any]:
    mods = [_decode(r) for r in _rows("SELECT * FROM module_inventory ORDER BY module")]
    return {"modules": mods, "screens": _rows("SELECT * FROM knowledge_inventory ORDER BY module, screen"),
            "updatedAt": max((m["updated_at"] for m in mods), default=None)}


# ---- queries for the dashboard / CLI -----------------------------------------------------

def list_runs(activity: str | None = None, status: str | None = None, module: str | None = None,
              source: str | None = None, q: str | None = None, hours: float | None = None,
              limit: int = 100, offset: int = 0, top_only: bool = False) -> dict[str, Any]:
    where, args = [], []
    for col, val in (("activity", activity), ("status", status), ("module", module), ("source", source)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    if hours:
        where.append("started_at >= ?")
        args.append(_since(hours))
    if q:
        where.append("(command LIKE ? OR screen LIKE ? OR error_message LIKE ? OR params LIKE ? OR attrs LIKE ?)")
        args += [f"%{q}%"] * 5
    if top_only:
        where.append("parent_id IS NULL")
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    total = _rows("SELECT COUNT(*) AS n FROM runs" + cond, args)[0]["n"]
    rows = _rows("SELECT run_id, parent_id, activity, command, source, module, screen, status, started_at, finished_at, "
                 "duration_ms, counts, attrs, error_type, error_message, host FROM runs" + cond +
                 " ORDER BY started_at DESC LIMIT ? OFFSET ?", args + [min(limit, 1000), offset])
    return {"total": total, "runs": [_decode(r) for r in rows]}


def get_run(run_id: str) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM runs WHERE run_id LIKE ? ORDER BY started_at DESC LIMIT 1", (run_id + "%",))
    if not rows:
        return None
    r = _decode(rows[0])
    r["events"] = [_decode(e) for e in _rows("SELECT ts, level, message, data FROM events WHERE run_id = ? ORDER BY id", (r["run_id"],))]
    r["children"] = [_decode(c) for c in _rows(
        "SELECT run_id, activity, command, status, started_at, duration_ms, counts, error_message FROM runs "
        "WHERE parent_id = ? ORDER BY started_at", (r["run_id"],))]
    return r


def summary(hours: float = 24) -> dict[str, Any]:
    since = _since(hours)
    acts: dict[str, dict[str, Any]] = {}
    for r in _rows("SELECT activity, status, COUNT(*) AS n, SUM(duration_ms) AS ms, MAX(started_at) AS last "
                   "FROM runs WHERE started_at >= ? GROUP BY activity, status", (since,)):
        a = acts.setdefault(r["activity"], {"activity": r["activity"], "total": 0, "SUCCESS": 0, "WARNING": 0,
                                           "FAILED": 0, "RUNNING": 0, "totalMs": 0, "lastRun": ""})
        a["total"] += r["n"]
        a[r["status"]] = a.get(r["status"], 0) + r["n"]
        a["totalMs"] += r["ms"] or 0
        a["lastRun"] = max(a["lastRun"], r["last"] or "")
    last_ok = {r["activity"]: r["t"] for r in _rows(
        "SELECT activity, MAX(finished_at) AS t FROM runs WHERE status IN ('SUCCESS', 'WARNING') GROUP BY activity")}
    last_fail = {r["activity"]: r["t"] for r in _rows(
        "SELECT activity, MAX(finished_at) AS t FROM runs WHERE status = 'FAILED' GROUP BY activity")}
    activities = []
    for name, a in sorted(acts.items()):
        done = a["total"] - a["RUNNING"]
        total_ms = a.pop("totalMs")
        a["avgMs"] = int(total_ms / done) if done else None
        a["lastSuccess"], a["lastFailure"] = last_ok.get(name), last_fail.get(name)
        activities.append(a)
    totals = {s: sum(a.get(s, 0) for a in activities) for s in ("SUCCESS", "WARNING", "FAILED", "RUNNING")}
    totals["runs"] = sum(a["total"] for a in activities)
    chat = _rows("SELECT COUNT(*) AS questions, AVG(json_extract(counts, '$.totalSeconds')) AS avg_seconds, "
                 "SUM(CASE WHEN json_extract(attrs, '$.grounding') = 'grounded' THEN 1 ELSE 0 END) AS grounded, "
                 "SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed "
                 "FROM runs WHERE activity = 'retrieval' AND command = 'chat' AND started_at >= ?", (since,))[0]
    api = _rows("SELECT COUNT(*) AS requests, SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) AS server_errors, "
                "SUM(CASE WHEN status >= 400 AND status < 500 THEN 1 ELSE 0 END) AS client_errors, "
                "CAST(AVG(duration_ms) AS INTEGER) AS avg_ms FROM api_requests WHERE ts >= ?", (since,))[0]
    failures = _rows("SELECT run_id, activity, command, module, screen, started_at, error_type, error_message, host "
                     "FROM runs WHERE status = 'FAILED' AND started_at >= ? ORDER BY started_at DESC LIMIT 20", (since,))
    stale = _rows("SELECT COUNT(*) AS n FROM runs WHERE status = 'RUNNING' AND started_at < ?", (_since(6),))[0]["n"]
    q = qdrant_history(1)["latest"]
    return {"hours": hours, "since": since, "generatedAt": _now(), "logDb": str(db_path()), "totals": totals,
            "activities": activities, "chat": chat, "api": api, "recentFailures": failures, "staleRunning": stale,
            "qdrant": q and {k: q[k] for k in ("ts", "collection", "status", "points", "by_type", "error")}}


def api_stats(hours: float = 24) -> dict[str, Any]:
    return {"hours": hours, "paths": _rows(
        "SELECT method, path, COUNT(*) AS requests, SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors, "
        "CAST(AVG(duration_ms) AS INTEGER) AS avg_ms, MAX(duration_ms) AS max_ms, MAX(ts) AS last FROM api_requests "
        "WHERE ts >= ? GROUP BY method, path ORDER BY requests DESC LIMIT 200", (_since(hours),))}


def prune(days: int = 90) -> dict[str, Any]:
    """Delete records older than `days`; runs stuck in RUNNING for a day become FAILED (Abandoned)."""
    cutoff = _since(days * 24)
    with _LOCK, _db() as con:
        abandoned = con.execute(
            "UPDATE runs SET status = 'FAILED', error_type = 'Abandoned', error_message = "
            "'The process ended without finishing (killed, crashed or server restarted).' "
            "WHERE status = 'RUNNING' AND started_at < ?", (_since(24),)).rowcount
        runs = con.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,)).rowcount
        events = con.execute("DELETE FROM events WHERE ts < ? OR run_id NOT IN (SELECT run_id FROM runs)", (cutoff,)).rowcount
        api = con.execute("DELETE FROM api_requests WHERE ts < ?", (cutoff,)).rowcount
        qd = con.execute("DELETE FROM qdrant_status WHERE ts < ?", (cutoff,)).rowcount
    return {"days": days, "abandonedRuns": abandoned, "deleted": {"runs": runs, "events": events, "apiRequests": api, "qdrantStatus": qd}}


# ---- command line ------------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> None:
    import argparse
    from .db_discovery import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser(prog="python -m pie_agent.activity_log", description="PIE activity log")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary").add_argument("--hours", type=float, default=24)
    t = sub.add_parser("tail")
    t.add_argument("-n", type=int, default=20)
    t.add_argument("--activity")
    t.add_argument("--status")
    t.add_argument("--module")
    sub.add_parser("show").add_argument("run_id")
    sub.add_parser("qdrant-status")
    sub.add_parser("inventory").add_argument("--no-qdrant", action="store_true")
    sub.add_parser("prune").add_argument("--days", type=int, default=int(os.getenv("PIE_LOG_RETENTION_DAYS", "90")))
    sub.add_parser("flush-remote")
    a = ap.parse_args(argv)
    src = os.getenv("PIE_LOG_SOURCE", "cli")
    if a.cmd == "tail":
        for r in reversed(list_runs(a.activity, a.status, a.module, limit=a.n)["runs"]):
            secs = (r["duration_ms"] or 0) / 1000
            print(f"{r['started_at']}  {r['status']:8} {r['activity']:20} {r['command'] or '':22} "
                  f"{r['module'] or '-':10} {secs:8.1f}s  {r['run_id'][:12]}  {r['error_message'] or ''}"[:240])
        return
    out: Any
    if a.cmd == "summary":
        out = summary(a.hours)
    elif a.cmd == "show":
        out = get_run(a.run_id) or {"error": "run not found"}
    elif a.cmd == "qdrant-status":
        out = qdrant_snapshot(source=src)
    elif a.cmd == "inventory":
        out = refresh_inventory(qdrant=not a.no_qdrant, source=src)
    elif a.cmd == "prune":
        out = prune(a.days)
    else:
        out = flush_remote()
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    _cli()
