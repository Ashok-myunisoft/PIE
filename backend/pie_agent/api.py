from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from .pipeline import run
from .generation import run_generation
from .erp_discovery import run_erp_discovery
from .erp_generation import generate_from_erp
from .screen_identity import run_screen_identity
from .api_mapping import run_api_mapping
from .db_mapping import run_db_mapping
from .db_discovery import load_dotenv
from .ai_knowledge import generate_ai_knowledge
from .qdrant_kms import QdrantHTTP, QdrantKMS
from .paths import from_root
from .activity_log import track, log_api_request
from .logs_api import router as logs_router
import tempfile, shutil, json, os, re, time

load_dotenv()
app=FastAPI(title="PIE Agent",version="0.19.0")

# The shareable chat file runs from disk (browser Origin "null") or another host and
# calls this API across origins. Only the usage/chat routes need it; set
# PIE_CORS_ORIGINS to a comma-separated list to restrict or extend it.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    # Default: the frontend dev server (port 5500) and pages opened from disk ("null").
    allow_origins=[o.strip() for o in os.getenv("PIE_CORS_ORIGINS",
                   "http://localhost:5500,http://127.0.0.1:5500,null").split(",") if o.strip()],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-PIE-Log-Token"],
)


# Every API request -> api_requests table (method, path, status, duration, client). The log API itself is skipped.
from starlette.concurrency import run_in_threadpool


@app.middleware("http")
async def _log_request(request, call_next):
    t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        path = request.url.path
        if not path.startswith("/logs/") and request.method != "OPTIONS":
            client = request.client.host if request.client else ""
            await run_in_threadpool(log_api_request, request.method, path, status,
                                    int((time.perf_counter() - t0) * 1000), client)


app.include_router(logs_router)

from .intelligence_api import router as intelligence_router
app.include_router(intelligence_router)


def _upload_path(tmp: Path, filename: str | None) -> Path:
    # Keep only the base name so a crafted filename such as "../../x" cannot
    # write outside the per-request temporary directory.
    name=Path((filename or "").replace("\\","/")).name or "upload.bin"
    return tmp/name

@app.get("/health")
def health():
    return {"status":"ok","service":"pie-agent","phase":"generation"}

@app.post("/analyze")
async def analyze(file: UploadFile=File(...)):
    tmp=Path(tempfile.mkdtemp(prefix="pie-api-")); inp=_upload_path(tmp,file.filename)
    with inp.open("wb") as f: shutil.copyfileobj(file.file,f)
    out=tmp/"output"
    try:
        pie,val,ev=run(str(inp),str(out),False)
        return {"validation":val,"evaluation":ev,"pie":pie.model_dump(mode="json")}
    except Exception as exc:
        raise HTTPException(500,str(exc))

@app.post("/generation/discover")
async def generation_discover(file: UploadFile=File(...)):
    tmp=Path(tempfile.mkdtemp(prefix="pie-discovery-")); inp=_upload_path(tmp,file.filename)
    with inp.open("wb") as f: shutil.copyfileobj(file.file,f)
    out=tmp/"generation"
    try:
        report,_=run_generation(str(inp),str(out))
        return report.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(500,str(exc))

@app.post("/generation/generate")
async def generation_generate(file: UploadFile=File(...)):
    tmp=Path(tempfile.mkdtemp(prefix="pie-generation-")); inp=_upload_path(tmp,file.filename)
    with inp.open("wb") as f: shutil.copyfileobj(file.file,f)
    out=tmp/"generation"
    try:
        report,packages=run_generation(str(inp),str(out))
        return {"status":"COMPLETED","report":report.model_dump(mode="json"),"packages":[p.model_dump(mode="json") for p in packages]}
    except Exception as exc:
        raise HTTPException(500,str(exc))


@app.post("/erp/discover")
def erp_discover():
    frontend=os.getenv("ERP_FRONTEND_PATH", "./data/erp/frontend")
    backend=os.getenv("ERP_BACKEND_PATH", "./data/erp/backend")
    out=os.getenv("PIE_DISCOVERY_OUTPUT", "outputs/phase12-discovery")
    return run_erp_discovery(frontend, backend, out)

@app.post("/erp/screen-identity")
def erp_screen_identity():
    inp=os.getenv("PIE_DISCOVERY_REPORT", "outputs/phase12-discovery/erp_discovery_report.json")
    out=os.getenv("PIE_IDENTITY_OUTPUT", "outputs/phase13-identity")
    return run_screen_identity(inp, out)

@app.post("/erp/map-api")
def erp_map_api():
    registry=os.getenv("PIE_IDENTITY_REGISTRY", "outputs/phase13-identity/screen_registry_v13.json")
    frontend=os.getenv("ERP_FRONTEND_PATH", "./data/erp/frontend")
    backend=os.getenv("ERP_BACKEND_PATH", "./data/erp/backend")
    out=os.getenv("PIE_API_MAPPING_OUTPUT", "outputs/phase14-api-mapping")
    return run_api_mapping(registry, frontend, backend, out)

@app.post("/erp/map-db")
def erp_map_db():
    api_mapping=os.getenv("PIE_API_MAPPING_REGISTRY", "outputs/phase14-api-mapping/screen_api_backend_mapping_v14.json")
    backend=os.getenv("ERP_BACKEND_PATH", "./data/erp/backend")
    out=os.getenv("PIE_DB_MAPPING_OUTPUT", "outputs/phase15-db-mapping")
    return run_db_mapping(api_mapping, backend, out, os.getenv("DATABASE_URL") or None)

@app.post("/erp/generate")
def erp_generate():
    frontend=os.getenv("ERP_FRONTEND_PATH", "./data/erp/frontend")
    backend=os.getenv("ERP_BACKEND_PATH", "./data/erp/backend")
    out=os.getenv("PIE_GENERATION_OUTPUT", "outputs/phase12-generation")
    report, packages=generate_from_erp(frontend, backend, out)
    return {"status":"COMPLETED", "report":report, "packageCount":len(packages)}

@app.post("/erp/generate-ai-knowledge")
def erp_generate_ai_knowledge():
    graph=os.getenv("PIE_EVIDENCE_GRAPH", "outputs/phase16-evidence-graph/evidence_graph_v16.json")
    out=os.getenv("PIE_AI_KNOWLEDGE_OUTPUT", "outputs/phase17-ai-knowledge")
    provider=os.getenv("PIE_LLM_PROVIDER", "offline")
    workers=int(os.getenv("PIE_AI_WORKERS", "1"))
    return generate_ai_knowledge(graph, out, provider, None, None, workers)


def _kms() -> QdrantKMS:
    return QdrantKMS(
        QdrantHTTP(os.getenv("QDRANT_URL", "http://localhost:6333"), os.getenv("QDRANT_API_KEY", "")),
        os.getenv("QDRANT_COLLECTION", "pie_knowledge"),
    )

@app.get("/kms/health")
def kms_health():
    try:
        kms = _kms()
        return {"status": "ok", "kms": "qdrant", "collection": kms.collection, "health": kms.health()}
    except Exception as exc:
        raise HTTPException(503, str(exc))

@app.get("/kms/stats")
def kms_stats():
    try:
        kms = _kms()
        return kms.client.collection(kms.collection)
    except Exception as exc:
        raise HTTPException(503, str(exc))

@app.get("/kms/knowledge/{knowledge_id}")
def kms_get(knowledge_id: str):
    try:
        return _kms().get(knowledge_id)
    except Exception as exc:
        raise HTTPException(404, str(exc))

@app.get("/kms/search")
def kms_search(q: str, limit: int = 10, include_review: bool = False):
    with track("retrieval", command="kms-search", source="api",
               params={"q": q[:500], "limit": limit, "includeReview": include_review}) as run:
        try:
            filt = None if include_review else {"must": [{"key": "publication_status", "match": {"value": "PUBLISHED"}}]}
            result = _kms().search(q, limit, filt)
        except Exception as exc:
            raise HTTPException(503, str(exc))
        run.result(result)
        return result


# ---- v0.19 Usage Phase (marketing UI) ----------------------------------------

def _modules_root() -> Path:
    return from_root(os.getenv("PIE_MODULES_OUTPUT", "outputs/modules"))


def _catalog_path(module: str) -> Path:
    # Module names come from the URL; keep them to a single safe path segment.
    if not module.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "invalid module name")
    return _modules_root() / module / "phase19-usage" / "usage_catalog_v19.json"


@app.get("/")
def service_info():
    """The backend serves the API only; the web pages are the separate frontend project."""
    return {"service": "PIE backend API", "version": app.version, "docs": "/docs", "health": "/health",
            "api": ["/usage/api/chat", "/usage/api/chat/status", "/usage/api/modules", "/usage/api/catalog/{module}",
                    "/usage/api/fields", "/usage/api/fields/{screen}", "/usage/api/fields/{screen}/excel", "/kms/search"],
            "frontend": "Run start_frontend.ps1 and open http://localhost:5500/"}


def _field_dictionaries() -> list[Path]:
    return sorted(_modules_root().glob("*/phase20-fields/*_fields_v20.json"))


def _field_dictionary(screen: str) -> Path:
    if not screen.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "invalid screen name")
    for p in _field_dictionaries():
        if p.name == f"{screen}_fields_v20.json":
            return p
    raise HTTPException(404, f"No field dictionary for '{screen}'. Run: python -m pie_agent.cli fields-build --form {screen} --enrich")


@app.get("/usage/api/fields")
def usage_fields_list():
    rows = []
    for p in _field_dictionaries():
        d = json.loads(p.read_text(encoding="utf-8"))
        rows.append({"screen": p.name.removesuffix("_fields_v20.json"), "screenName": d.get("screen"), "screenId": d.get("screenId"),
                     "module": d.get("module"), "table": d.get("table"), "generatedAt": d.get("generatedAt"),
                     "statistics": d.get("statistics", {}), "excel": p.with_suffix(".xlsx").exists()})
    return rows


@app.get("/usage/api/fields/{screen}")
def usage_fields_dictionary(screen: str):
    return json.loads(_field_dictionary(screen).read_text(encoding="utf-8"))


@app.get("/usage/api/fields/{screen}/excel")
def usage_fields_excel(screen: str):
    x = _field_dictionary(screen).with_suffix(".xlsx")
    if not x.exists():
        raise HTTPException(404, "Excel export not generated yet; run fields-build")
    return FileResponse(x, filename=f"{screen}_field_dictionary.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/usage/api/modules")
def usage_modules():
    rows = []
    for p in sorted(_modules_root().glob("*/phase19-usage/usage_catalog_v19.json")):
        c = json.loads(p.read_text(encoding="utf-8"))
        rows.append({"module": c["module"], "moduleName": c.get("moduleName", c["module"]),
                     "generatedAt": c.get("generatedAt"), "statistics": c.get("statistics", {})})
    return rows


@app.get("/usage/api/catalog/{module}")
def usage_catalog(module: str):
    p = _catalog_path(module)
    if not p.exists():
        raise HTTPException(404, f"No usage catalog for module '{module}'. Run: python -m pie_agent.cli usage-build --module {module}")
    return json.loads(p.read_text(encoding="utf-8"))


# ---- v0.19 Marketing Intelligence chat -----------------------------------------

HISTORY_TURN_CHARS = 4000
HISTORY_TURNS = 12


class ChatTurn(BaseModel):
    role: str
    content: str = ""

    @field_validator("content")
    @classmethod
    def _trim(cls, v: str) -> str:
        # History only gives follow-up questions context. A long earlier answer (for
        # example the 237-field table) is trimmed rather than rejected.
        return v[:HISTORY_TURN_CHARS]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list)

    @field_validator("history")
    @classmethod
    def _recent(cls, v: list[ChatTurn]) -> list[ChatTurn]:
        return v[-HISTORY_TURNS:]
    module: str | None = None                            # one module (older pages)
    modules: list[str] = Field(default_factory=list, max_length=60)  # any combination; empty = all modules
    includeReview: bool = False
    # Who the answer is for: marketing (default), client_nontechnical, client_technical, developer, stakeholder.
    audience: str | None = Field(default=None, max_length=40)
    # Chat sessions: with sessionId the history comes from that stored session (the browser's history is
    # ignored), and the exchange is saved to it; newSession starts one. Without either, nothing is stored.
    sessionId: str | None = Field(default=None, max_length=64)
    newSession: bool = False
    userId: str | None = Field(default=None, max_length=120)

    def scope(self) -> list[str]:
        return list(dict.fromkeys([m for m in self.modules if m] + ([self.module] if self.module else [])))


_CHAT = None


def _chat():
    # One engine per process, so the KMS snapshot is cached between questions.
    global _CHAT
    if _CHAT is None:
        from .chat import MarketingChat
        from .llm import get_provider
        code = None
        if os.getenv("PIE_CODE_COLLECTION"):
            # Developer answers read the real source lines (v0.25 code index).
            from .code_index import code_kms
            code = code_kms()
        _CHAT = MarketingChat(_kms(), get_provider(os.getenv("PIE_CHAT_LLM_PROVIDER", os.getenv("PIE_LLM_PROVIDER", "offline"))), code)
    return _CHAT


@app.on_event("startup")
def _warm_chat_snapshot():
    # Load the KMS snapshot in the background so the first question is not slow.
    import threading
    from .chat import load_snapshot

    def warm():
        try:
            load_snapshot(_chat().kms)
        except Exception:
            pass  # reported by /usage/api/chat/status when the UI loads
    threading.Thread(target=warm, daemon=True).start()


@app.post("/usage/api/chat")
def usage_chat(req: ChatRequest, x_pie_user: str | None = Header(default=None)):
    scope = req.scope()
    if any(len(m) > 60 or not m.replace("-", "").replace("_", "").isalnum() for m in scope):
        raise HTTPException(400, "invalid module name")
    from .chat import AUDIENCES, trace_digest
    if req.audience and req.audience.strip().lower().replace("-", "_") not in AUDIENCES:
        raise HTTPException(400, f"unknown audience; use one of: {', '.join(AUDIENCES)}")
    from . import sessions as S
    user = _user(x_pie_user, req.userId)
    session = None
    history = [t.model_dump() for t in req.history]
    if req.sessionId:
        session = S.get_session(req.sessionId, user, with_messages=False)
        if not session:
            raise HTTPException(404, "chat session not found")
        history = S.history(req.sessionId, user, HISTORY_TURNS)       # this session's conversation only
    elif req.newSession:
        session = S.create_session(user, scope, req.audience)
        history = []
    audience = req.audience or (session["audience"] if session else None)   # a continued chat keeps its audience
    with track("retrieval", command="chat", source="api", module=",".join(scope) or None,
               params={"message": req.message[:500], "historyTurns": len(history), "includeReview": req.includeReview,
                       "modules": scope, "audience": audience, "sessionId": session and session["sessionId"]}) as run:
        try:
            result = _chat().answer(req.message, history, scope or None, req.includeReview, audience)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            # Qdrant unreachable or rejected the request.
            raise HTTPException(503, f"Knowledge base unavailable: {exc}")
        grounding, plan = result.get("grounding") or {}, result.get("plan") or {}
        run.count(retrievedItems=grounding.get("retrievedItems", 0), citedItems=grounding.get("citedItems", 0),
                  sources=len(result.get("sources") or []), **(result.get("timings") or {}))
        run.attr(question=req.message[:300], audience=(result.get("audience") or {}).get("label"),
                 intent=result.get("intent"), grounding=grounding.get("status"),
                 generator=plan.get("generator"), planner=plan.get("planner"), answerPreview=(result.get("answer") or "")[:300])
        # How the answer was reached: plan, every retrieved knowledge item, citations (no prompt text).
        run.event("answer trace", trace=trace_digest(result))
        result["runId"] = run.run_id
        if session:
            sid = session["sessionId"]
            saved = S.add_exchange(sid, user, req.message, result, scope, (result.get("audience") or {}).get("key"))
            if saved["titleAuto"] and saved["messageCount"] <= 2:
                bot = _chat()
                title = S.make_title(req.message, result.get("plan", {}).get("modules") or scope,
                                     (result.get("audience") or {}).get("label"), bot.llm if bot.llm_enabled else None)
                saved = S.update_session(sid, user, title=title, auto_title=True) or saved
            result["session"] = saved
        return result


# ---- chat sessions -------------------------------------------------------------------------

def _user(header_user: str | None, supplied: str | None) -> str:
    """Who owns the sessions: the signed-in user passed by the web server (X-PIE-User, e.g. nginx basic-auth
    $remote_user), else the browser's own id, else a shared anonymous owner."""
    import re as _re
    u = (header_user or supplied or "anonymous").strip()[:120]
    if not _re.fullmatch(r"[\w.@+-]{1,120}", u):
        raise HTTPException(400, "invalid user id")
    return u


class SessionCreate(BaseModel):
    userId: str | None = Field(default=None, max_length=120)
    modules: list[str] = Field(default_factory=list, max_length=60)
    audience: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=120)
    messages: list[dict] = Field(default_factory=list, max_length=400)   # import a conversation kept in the browser


class SessionUpdate(BaseModel):
    userId: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    archived: bool | None = None


@app.get("/usage/api/sessions")
def usage_sessions(userId: str | None = None, q: str | None = Query(None, max_length=200), archived: bool = False,
                   x_pie_user: str | None = Header(default=None)):
    from . import sessions as S
    return {"sessions": S.list_sessions(_user(x_pie_user, userId), q, archived)}


@app.post("/usage/api/sessions")
def usage_session_create(body: SessionCreate, x_pie_user: str | None = Header(default=None)):
    from . import sessions as S
    user = _user(x_pie_user, body.userId)
    s = S.create_session(user, body.modules, body.audience, body.title)
    if body.messages:
        s = S.import_messages(s["sessionId"], user, body.messages) or s
        first = next((m.get("content") for m in body.messages if isinstance(m, dict) and m.get("role") == "user"), "")
        if first and not body.title:
            s = S.update_session(s["sessionId"], user, title=S.rule_title(first, S.scope_label(body.modules, None)), auto_title=True) or s
    return s


@app.get("/usage/api/sessions/{session_id}")
def usage_session_get(session_id: str, userId: str | None = None, x_pie_user: str | None = Header(default=None)):
    from . import sessions as S
    s = S.get_session(session_id, _user(x_pie_user, userId))
    if not s:
        raise HTTPException(404, "chat session not found")
    return s


@app.patch("/usage/api/sessions/{session_id}")
def usage_session_update(session_id: str, body: SessionUpdate, x_pie_user: str | None = Header(default=None)):
    from . import sessions as S
    s = S.update_session(session_id, _user(x_pie_user, body.userId), title=body.title, archived=body.archived)
    if not s:
        raise HTTPException(404, "chat session not found")
    return s


@app.delete("/usage/api/sessions/{session_id}")
def usage_session_delete(session_id: str, userId: str | None = None, x_pie_user: str | None = Header(default=None)):
    from . import sessions as S
    if not S.delete_session(session_id, _user(x_pie_user, userId)):
        raise HTTPException(404, "chat session not found")
    return {"deleted": session_id}


# ---- relationship explorer (Phase 16 evidence graphs) ----------------------------

@app.get("/usage/api/relations/screens")
def usage_relations_screens():
    from .relations import list_screens
    return list_screens(_modules_root())


@app.get("/usage/api/relations/screen/{screen_id}")
def usage_relations_screen(screen_id: str):
    from .chat import load_snapshot
    from .relations import screen_relations
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", screen_id):
        raise HTTPException(400, "invalid screen id")
    try:
        points = load_snapshot(_chat().kms).points  # the KMS knowledge held for the screen
    except RuntimeError:
        points = None
    result = screen_relations(screen_id, points, _modules_root())
    if result is None:
        raise HTTPException(404, f"no relationship graph contains screen {screen_id}")
    return result


@app.get("/usage/api/chat/status")
def usage_chat_status():
    from .chat import load_snapshot
    bot = _chat()
    try:
        snap = load_snapshot(bot.kms)
        bot.kms.ensure_collection()   # also detects whether the collection uses semantic vectors
    except RuntimeError as exc:
        raise HTTPException(503, f"Knowledge base unavailable: {exc}")
    kinds: dict[str, int] = {}
    for p in snap.points:
        k = p.get("knowledge_type") or ""
        kinds[k] = kinds.get(k, 0) + 1
    return {"kms": "qdrant", "collection": bot.kms.collection, "points": len(snap.points), "byType": kinds,
            "verifiedModules": sorted(snap.usage_modules), "modules": sorted(snap.modules),
            "llm": getattr(bot.llm, "name", "offline"), "tickets": kinds.get("Ticket", 0),
            "search": "semantic (hybrid)" if bot.kms.hybrid else "keyword (hashing)",
            "codeCollection": bot.code_kms.collection if bot.code_kms else None}
