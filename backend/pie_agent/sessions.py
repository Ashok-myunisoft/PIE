"""Ask PIE chat sessions: each conversation is stored with its own id, owner, title, module scope,
audience and messages, so it can be reopened and continued later.

Isolation: the history a question is answered with is read from its own session only; the browser's
copy is not used. Switching to another session (or a new one) therefore never mixes conversations.

Storage: SQLite (PIE_SESSION_DB, default outputs/sessions/pie_sessions.db). Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .paths import from_root

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, title_auto INTEGER NOT NULL DEFAULT 1,
  modules TEXT, audience TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0, message_count INTEGER NOT NULL DEFAULT 0, preview TEXT);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions (user_id, archived, updated_at);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  response TEXT, modules TEXT, audience TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_messages_session ON messages (session_id, id);
"""
NEW_TITLE = "New chat"
_LOCK = threading.Lock()
_READY: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def db_path() -> Path:
    return from_root(os.getenv("PIE_SESSION_DB") or "outputs/sessions/pie_sessions.db")


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 15000")
    if str(path) not in _READY:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)
        _READY.add(str(path))
    try:
        with con:
            yield con
    finally:
        con.close()


def _session_row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["modules"] = json.loads(d["modules"] or "[]")
    d["archived"] = bool(d["archived"])
    d["title_auto"] = bool(d["title_auto"])
    return {"sessionId": d["session_id"], "userId": d["user_id"], "title": d["title"], "titleAuto": d["title_auto"],
            "modules": d["modules"], "audience": d["audience"], "createdAt": d["created_at"], "updatedAt": d["updated_at"],
            "archived": d["archived"], "messageCount": d["message_count"], "preview": d["preview"] or ""}


def create_session(user_id: str, modules: list[str] | None = None, audience: str | None = None,
                   title: str | None = None) -> dict[str, Any]:
    sid, now = uuid.uuid4().hex, _now()
    with _LOCK, _db() as con:
        con.execute("INSERT INTO sessions (session_id, user_id, title, title_auto, modules, audience, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid, user_id, (title or NEW_TITLE)[:120], 0 if title else 1, json.dumps(modules or []), audience or "marketing", now, now))
    return get_session(sid, user_id, with_messages=False)


def get_session(session_id: str, user_id: str, with_messages: bool = True) -> dict[str, Any] | None:
    """A session of this user (None when it does not exist or belongs to someone else)."""
    with _db() as con:
        r = con.execute("SELECT * FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)).fetchone()
        if not r:
            return None
        s = _session_row(r)
        if with_messages:
            s["messages"] = [{"role": m["role"], "content": m["content"], "createdAt": m["created_at"],
                              "modules": json.loads(m["modules"] or "[]"), "audience": m["audience"],
                              "response": json.loads(m["response"]) if m["response"] else None}
                             for m in con.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,))]
    return s


def list_sessions(user_id: str, q: str | None = None, archived: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    """Newest first. `q` searches titles and the text of the messages."""
    sql = "SELECT * FROM sessions WHERE user_id = ? AND archived = ?"
    args: list[Any] = [user_id, int(archived)]
    if q:
        sql += (" AND (title LIKE ? OR session_id IN (SELECT session_id FROM messages WHERE content LIKE ?))")
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with _db() as con:
        return [_session_row(r) for r in con.execute(sql, args)]


def history(session_id: str, user_id: str, turns: int = 12) -> list[dict[str, str]]:
    """The conversation a follow-up question is answered with: this session's messages only."""
    s = get_session(session_id, user_id)
    if not s:
        raise KeyError(session_id)
    return [{"role": m["role"], "content": m["content"]} for m in s["messages"]][-turns:]


def add_exchange(session_id: str, user_id: str, question: str, response: dict[str, Any],
                 modules: list[str], audience: str | None) -> dict[str, Any]:
    """Save a question and its answer, and remember the scope the session now uses."""
    now = _now()
    stored = dict(response)   # the full answer, trace included, so a reopened chat shows exactly what was answered
    with _LOCK, _db() as con:
        if not con.execute("SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)).fetchone():
            raise KeyError(session_id)
        con.execute("INSERT INTO messages (session_id, role, content, modules, audience, created_at) VALUES (?, 'user', ?, ?, ?, ?)",
                    (session_id, question, json.dumps(modules), audience, now))
        con.execute("INSERT INTO messages (session_id, role, content, response, modules, audience, created_at) "
                    "VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
                    (session_id, response.get("answer") or "", json.dumps(stored, default=str), json.dumps(modules), audience, now))
        con.execute("UPDATE sessions SET updated_at = ?, message_count = message_count + 2, modules = ?, audience = ?, preview = ? "
                    "WHERE session_id = ?", (now, json.dumps(modules), audience or "marketing", question[:160], session_id))
    return get_session(session_id, user_id, with_messages=False)


def import_messages(session_id: str, user_id: str, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Add earlier messages (e.g. a conversation kept only in the browser) to a session, in order."""
    rows = [(m.get("role"), str(m.get("content") or (m.get("response") or {}).get("answer") or "")[:20000],
             json.dumps(m["response"], default=str) if isinstance(m.get("response"), dict) else None)
            for m in messages[:400] if isinstance(m, dict) and m.get("role") in {"user", "assistant"}]
    rows = [r for r in rows if r[1]]
    if not rows:
        return get_session(session_id, user_id, with_messages=False)
    now = _now()
    with _LOCK, _db() as con:
        if not con.execute("SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)).fetchone():
            return None
        con.executemany("INSERT INTO messages (session_id, role, content, response, created_at) VALUES (?, ?, ?, ?, ?)",
                        [(session_id, role, content, resp, now) for role, content, resp in rows])
        first_q = next((c for role, c, _ in rows if role == "user"), "")
        con.execute("UPDATE sessions SET message_count = message_count + ?, updated_at = ?, preview = COALESCE(preview, ?) "
                    "WHERE session_id = ?", (len(rows), now, first_q[:160], session_id))
    return get_session(session_id, user_id, with_messages=False)


def update_session(session_id: str, user_id: str, title: str | None = None, archived: bool | None = None,
                   modules: list[str] | None = None, audience: str | None = None, auto_title: bool = False) -> dict[str, Any] | None:
    sets, args = [], []
    if title is not None:
        sets += ["title = ?", "title_auto = ?"]
        args += [title.strip()[:120] or NEW_TITLE, 1 if auto_title else 0]
    if archived is not None:
        sets.append("archived = ?")
        args.append(int(archived))
    if modules is not None:
        sets.append("modules = ?")
        args.append(json.dumps(modules))
    if audience is not None:
        sets.append("audience = ?")
        args.append(audience)
    if sets:
        with _LOCK, _db() as con:
            n = con.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ? AND user_id = ?",
                            args + [session_id, user_id]).rowcount
        if not n:
            return None
    return get_session(session_id, user_id, with_messages=False)


def delete_session(session_id: str, user_id: str) -> bool:
    with _LOCK, _db() as con:
        n = con.execute("DELETE FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)).rowcount
        if n:
            con.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    return bool(n)


# ---- titles --------------------------------------------------------------------------------

TITLE_STOP = {"what", "which", "who", "how", "why", "when", "where", "is", "are", "the", "a", "an", "of", "for", "to", "in", "on",
              "me", "my", "our", "we", "i", "you", "do", "does", "can", "could", "should", "would", "give", "tell", "show", "please",
              "about", "and", "or", "with", "this", "that", "these", "those", "all", "any", "top", "key", "main", "list", "there",
              "have", "has", "it", "its", "module", "screen", "be", "provide", "provides", "available", "explain", "describe"}
ACRONYM_WORDS = {"ess", "hrms", "bom", "mrp", "crm", "tds", "wms", "fam", "api", "sql", "kpi", "okr", "it", "cms", "uat"}


def _title_case(words: list[str]) -> str:
    return " ".join(w.upper() if w.lower() in ACRONYM_WORDS else w[:1].upper() + w[1:] for w in words)


def rule_title(question: str, scope_label: str) -> str:
    """'What are the top features for the pump industry?' + 'Inventory' -> 'Inventory – Features Pump Industry' shaped titles."""
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9&'-]*", question) if w.lower() not in TITLE_STOP]
    topic = _title_case(words[:5]) or "General Question"
    return f"{scope_label} – {topic}" if scope_label else topic


def scope_label(modules: list[str], audience_label: str | None) -> str:
    """Title prefix: the module(s) in scope, else the audience (e.g. 'Marketing')."""
    names = {"ess": "ESS", "hrms": "HRMS", "fam": "FAM", "wms": "WMS", "crmsales": "CRM Sales", "gbservicedesk": "Service Desk"}
    if modules:
        label = " + ".join(names.get(m, m[:1].upper() + m[1:]) for m in modules[:2])
        return label + (" +" if len(modules) > 2 else "")
    return (audience_label or "Marketing Team").replace(" Team", "")


TITLE_PROMPT = """Write a short title (2 to 5 words, Title Case, no quotes, no punctuation at the end) for a chat that starts with this question about GoodBooks ERP. Name the subject (screen, feature, industry or topic), not the question type, and do not include the words GoodBooks, ERP or the module name. Examples: "Pump Industry Features", "Item Master Configuration", "Leave Application", "Competitor Comparison".
Question: {question}
Title:"""


def make_title(question: str, modules: list[str], audience_label: str | None, llm: Any = None) -> str:
    """'<module or audience> – <topic>'. The topic comes from the AI when it is available (one short call), else from the question's words."""
    prefix = scope_label(modules, audience_label)
    topic = ""
    if llm is not None:
        try:
            topic = str(llm.generate(TITLE_PROMPT.format(question=question[:500]))).strip().strip('"\'').splitlines()[0][:60]
            topic = re.sub(r"[.:;!?]+$", "", topic).strip()
            if len(topic.split()) > 7 or not re.search(r"[A-Za-z]", topic):
                topic = ""
        except Exception:
            topic = ""
    if not topic:
        return rule_title(question, prefix)
    return f"{prefix} – {topic}"
