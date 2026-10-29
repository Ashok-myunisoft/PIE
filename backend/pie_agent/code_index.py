"""Source-code knowledge for developers (v0.25): the GB5 code itself, searchable in Qdrant.

Every C# / TypeScript / HTML / SQL file of the ERP is split into chunks at member
boundaries (methods, properties, SQL statements), 40-150 lines each, and stored in its own
collection (PIE_CODE_COLLECTION, default pie_code_v2) with file, line range, module, layer
(endpoint / bll / dal / query / dto / component / service / sql) and class. The chat gives
developers the real lines, numbered, so answers can point at file:line and reason about the
logic. Re-indexing is incremental: only files whose content changed are re-embedded.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterator

from .erp_source import IGNORED_DIRS
from .paths import from_root
from .qdrant_kms import QdrantHTTP, QdrantKMS

CODE_VERSION = "0.25.0"
DEFAULT_CODE_COLLECTION = "pie_code_v2"
LANGS = {".cs": "csharp", ".ts": "typescript", ".html": "html", ".sql": "sql"}
ROOTS = (("backend/GB5Solution", {".cs"}), ("backend/GB5Shared", {".cs"}), ("backend/GB5Framework", {".cs"}),
         ("backend/DB/Migrations", {".sql"}), ("frontend/projects", {".ts", ".html"}), ("frontend/features", {".ts", ".html"}),
         ("frontend/libs", {".ts"}))
MIN_LINES, MAX_LINES = 40, 150
MAX_FILE_BYTES = 1_500_000
MAX_CODE_CHARS = 12_000
INDEX_FIELDS = ("file", "layer", "language", "class_name", "code_module")

BOUNDARY = {
    "csharp": re.compile(r"^\s*(?:\[[A-Z]\w*|(?:public|private|protected|internal|static|override|async|sealed|abstract|partial)\b|///)"),
    "typescript": re.compile(r"^\s*(?:export\b|(?:public|private|protected|static|async|readonly|get|set)\b|@\w+\(|\w+\s*\([^)]*\)\s*(?::\s*[\w<>\[\]| ]+)?\s*\{\s*$)"),
    "html": re.compile(r"^\s*<(?:div|section|mat-tab|form|ng-container|gb-|table|fieldset|!--)", re.I),
    "sql": re.compile(r"^\s*(?:CREATE|ALTER|INSERT|UPDATE|DELETE|MERGE|GO\b|IF\b|DECLARE|BEGIN\b|--\s*=+)", re.I),
}
CLASS_RE = re.compile(r"\b(?:class|interface|record|enum)\s+(\w+)")
MEMBER_RE = {
    "csharp": re.compile(r"^\s*(?:public|private|protected|internal)\s+(?:static\s+|async\s+|override\s+|virtual\s+|const\s+)*[\w<>\[\],.? ]+?\s+(\w+)\s*(?:\(|=|\{|$)"),
    "typescript": re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*(\w+)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]| .]+)?\s*\{"),
    "sql": re.compile(r"^\s*(?:CREATE|ALTER)\s+(?:OR\s+ALTER\s+)?(?:TABLE|VIEW|PROC(?:EDURE)?|FUNCTION|TRIGGER)\s+(?:\[?\w+\]?\.)?\[?(\w+)", re.I),
}
NOT_MEMBERS = {"if", "for", "foreach", "while", "switch", "catch", "return", "new", "using", "lock", "constructor"}


def code_collection() -> str:
    return os.getenv("PIE_CODE_COLLECTION") or DEFAULT_CODE_COLLECTION


def code_kms(url: str | None = None, api_key: str | None = None, collection: str | None = None) -> QdrantKMS:
    """The code collection always uses hybrid vectors (meaning + exact identifiers)."""
    from .embeddings import HybridEmbedder
    return QdrantKMS(QdrantHTTP(url or os.getenv("QDRANT_URL", "http://localhost:6333"),
                                os.getenv("QDRANT_API_KEY", "") if api_key is None else api_key),
                     collection or code_collection(), HybridEmbedder(), version=CODE_VERSION, index_fields=INDEX_FIELDS)


# ------------------------------------------------------------------ file facts

def layer_of(rel: str) -> str:
    low = rel.lower()
    for key, layer in (("/endpoints/", "endpoint"), ("/query/", "query"), ("/dto/", "dto"), ("dal/", "dal"),
                       ("bll/", "bll"), ("/dbservice/", "dbservice"), ("/service/", "service"), (".component.", "component"),
                       ("/db/migrations/", "sql"), ("/gb5shared/", "shared"), ("/gb5framework/", "framework")):
        if key in low:
            return layer
    return "other"


def module_of(rel: str) -> str:
    parts = rel.split("/")
    for anchor in ("GB5Solution", "projects", "features"):
        if anchor in parts and parts.index(anchor) + 1 < len(parts):
            return parts[parts.index(anchor) + 1].lower()
    return parts[1].lower() if len(parts) > 1 else ""


def entity_of(rel: str) -> str:
    parts = rel.split("/")
    low = [p.lower() for p in parts]
    for anchor in ("endpoints", "customcode", "query", "dto"):
        if anchor in low:
            i = low.index(anchor)
            if i + 2 < len(parts):
                return parts[i + 1]
    return ""


def iter_code_files(erp_root: Path) -> Iterator[tuple[Path, str]]:
    for sub, exts in ROOTS:
        base = erp_root / sub
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            rel = p.relative_to(erp_root).as_posix()
            if any(part in IGNORED_DIRS for part in p.relative_to(erp_root).parts) or rel.endswith((".spec.ts", ".d.ts")):
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES or p.stat().st_size == 0:
                    continue
            except OSError:
                continue
            yield p, rel


# ------------------------------------------------------------------ chunking

def chunk_lines(lines: list[str], language: str) -> list[tuple[int, int]]:
    """(start, end) 1-based inclusive line ranges, cut at member boundaries."""
    rx = BOUNDARY.get(language)
    spans, start = [], 0
    for i in range(len(lines)):
        size = i - start
        boundary = bool(rx and rx.match(lines[i]))
        if (size >= MIN_LINES and boundary) or size >= MAX_LINES:
            # Keep attributes/comments that introduce a member with that member.
            j = i
            while j - 1 > start and lines[j - 1].strip().startswith(("[", "///", "//", "@", "*", "/*")):
                j -= 1
            spans.append((start + 1, j))
            start = j
    if start < len(lines):
        if spans and len(lines) - start < MIN_LINES // 3:
            spans[-1] = (spans[-1][0], len(lines))
        else:
            spans.append((start + 1, len(lines)))
    return [s for s in spans if s[1] >= s[0]]


def file_chunks(text: str, rel: str, sha: str) -> list[dict[str, Any]]:
    language = LANGS.get(Path(rel).suffix.lower(), "text")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ns = (re.search(r"\bnamespace\s+([\w.]+)", text) or [None, ""])[1]
    module, layer, entity = module_of(rel), layer_of(rel), entity_of(rel)
    out = []
    current_class = ""
    member_rx = MEMBER_RE.get(language)
    for idx, (a, b) in enumerate(chunk_lines(lines, language)):
        body = lines[a - 1:b]
        classes = [m.group(1) for ln in body if (m := CLASS_RE.search(ln))]
        cls = classes[0] if classes else current_class
        if classes:
            current_class = classes[-1]
        members = []
        if member_rx:
            for ln in body:
                m = member_rx.match(ln)
                if m and m.group(1) not in NOT_MEMBERS and m.group(1) not in members:
                    members.append(m.group(1))
        code = "\n".join(body)
        if not code.strip():
            continue
        title = f"{rel}:{a}-{b}" + (f" {cls}" if cls else "") + (f" ({', '.join(members[:4])})" if members else "")
        payload = {
            "knowledge_type": "CodeChunk", "knowledge_id": f"CODE-{rel}:{a}", "product": "GoodBooks ERP",
            "file": rel, "language": language, "code_module": module, "module": module, "layer": layer, "entity": entity,
            "namespace": ns, "class_name": cls, "members": members[:20], "start_line": a, "end_line": b,
            "title": title, "content": code[:MAX_CODE_CHARS], "file_sha": sha, "knowledge_version": CODE_VERSION,
            "publication_status": "PUBLISHED", "source_types": ["Source Code"], "generation_method": "deterministic",
        }
        embed = f"{rel}\n{layer} {module} {entity} {ns} {cls} {' '.join(members)}\n{code}"
        out.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"pie://code/{rel}/{a}/{sha[:12]}")), "payload": payload, "text": embed})
    return out


# ------------------------------------------------------------------ index build

def _manifest_path() -> Path:
    return from_root(os.getenv("PIE_CODE_MANIFEST") or "outputs/code-index/code_index_manifest.json")


def build_code_index(erp_root: str | Path = "data/erp", kms: QdrantKMS | None = None, full: bool = False,
                     limit_files: int | None = None, file_batch: int = 150, progress=print) -> dict[str, Any]:
    """Index (or incrementally re-index) the ERP source into the code collection."""
    erp = from_root(erp_root)
    kms = kms or code_kms()
    kms.ensure_collection()
    mpath = _manifest_path()
    manifest: dict[str, Any] = {} if full or not mpath.exists() else json.loads(mpath.read_text(encoding="utf-8"))
    files = list(iter_code_files(erp))
    if limit_files:
        files = files[:limit_files]
    seen, todo = set(), []
    for p, rel in files:
        seen.add(rel)
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if manifest.get(rel, {}).get("sha") != sha:
            todo.append((rel, sha, data.decode("utf-8", errors="replace")))
    removed = [] if limit_files else [rel for rel in manifest if rel not in seen]
    progress(f"{len(files)} code files: {len(todo)} new or changed, {len(removed)} removed, {len(files) - len(todo)} unchanged")
    for rel in removed:
        kms.delete({"must": [{"key": "file", "match": {"value": rel}}]})
        manifest.pop(rel, None)
    chunks_total = 0
    for i in range(0, len(todo), file_batch):
        batch = todo[i:i + file_batch]
        points = []
        for rel, sha, text in batch:
            if rel in manifest:   # changed file: drop its old chunks first
                kms.delete({"must": [{"key": "file", "match": {"value": rel}}]})
            cs = file_chunks(text, rel, sha)
            points += cs
            manifest[rel] = {"sha": sha, "chunks": len(cs), "lines": text.count("\n") + 1}
        chunks_total += kms.upsert_points(points, batch_size=96)
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(json.dumps(manifest, indent=0), encoding="utf-8")   # resumable: done files are skipped next run
        progress(f"indexed {min(i + file_batch, len(todo))}/{len(todo)} files ({chunks_total} chunks)")
    dense = getattr(kms.embedder, "dense", None)
    return {"status": "INDEXED", "collection": kms.collection, "files": len(files), "filesIndexed": len(todo),
            "filesRemoved": len(removed), "chunksWritten": chunks_total,
            "chunksTotal": sum(v.get("chunks", 0) for v in manifest.values()),
            "embeddingTokens": getattr(dense, "tokens", None), "manifest": str(mpath)}


# ------------------------------------------------------------------ retrieval (chat)

def numbered(payload: dict[str, Any], max_chars: int = 4500) -> str:
    """The chunk's code with real line numbers, so answers can cite file:line."""
    start = int(payload.get("start_line") or 1)
    lines = str(payload.get("content") or "").split("\n")
    out, size = [], 0
    for i, ln in enumerate(lines):
        row = f"{start + i:>5}| {ln}"
        size += len(row) + 1
        if size > max_chars:
            out.append("      … (chunk continues)")
            break
        out.append(row)
    return "\n".join(out)


def search_code(kms: QdrantKMS, queries: list[str], files: list[str] | None = None, limit: int = 6,
                layers: list[str] | None = None) -> list[dict[str, Any]]:
    """Code chunks for a question: semantic search, plus the files the question's screens reach."""
    from .embeddings import prime
    must = []
    if layers:
        must.append({"key": "layer", "match": {"any": layers}})
    flt = {"must": must} if must else None
    best: dict[str, dict[str, Any]] = {}
    queries = [q for q in dict.fromkeys(queries) if q.strip()][:4]
    prime(kms.embedder, queries)
    for q in queries:
        for h in kms.search(q, limit, flt).get("result", []):
            pid = str(h.get("id"))
            if pid not in best or h.get("score", 0) > best[pid]["score"]:
                best[pid] = {"score": float(h.get("score") or 0), "payload": h.get("payload") or {}}
    if files:
        # Chunks of the endpoint/BLL/DAL files the relationship graph links to the screens in the question.
        fq = {"must": [{"key": "file", "match": {"any": files[:12]}}]}
        hits = kms.search(" ".join(queries)[:2000], limit, fq).get("result", [])
        for h in hits:
            pid = str(h.get("id"))
            score = float(h.get("score") or 0) + .05
            if pid not in best or score > best[pid]["score"]:
                best[pid] = {"score": score, "payload": h.get("payload") or {}}
    return sorted(best.values(), key=lambda r: -r["score"])[:limit]
