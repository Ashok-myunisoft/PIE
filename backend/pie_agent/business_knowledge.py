"""Business knowledge for Marketing and clients (v0.25), drafted by AI from verified ERP evidence.

Per feature (screen), from its Usage-catalog claims and field dictionary:
  BusinessValue   what it does, who uses it, benefits, use cases, industries (inferred)
  HowTo           step-by-step task guide built from the screen's sections and mandatory fields
  FAQ             questions users ask, answered from validations, messages and calculations
  MarketingCopy   headline, short blurb and bullets for brochures and campaigns
Per module:
  ModuleBrochure  tagline, overview, capabilities with benefits, ideal users, verified key facts
  Process         end-to-end business processes across screens, from the tables screens write and read

Everything is labelled AI-drafted (generation_method "ai"): the chat marks it unconfirmed until
a product owner confirms it. Drafts are cached by a fingerprint of their facts, so a rebuild only
pays for screens whose evidence changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import from_root

BUSINESS_VERSION = "0.25.0"
PROMPT_VERSION = "b2"
BUSINESS_TYPES = ("BusinessValue", "HowTo", "FAQ", "MarketingCopy", "ModuleBrochure", "Process")
PRODUCT = "GoodBooks ERP"

FEATURE_PROMPT = """You write product knowledge for GoodBooks ERP from VERIFIED FACTS about one screen.
Readers: the Marketing team (brochures, campaigns), business clients and implementation staff.

Rules:
- Use only the facts. Do not invent integrations, dashboards, mobile apps, AI, real-time features, reports,
  approvals or workflow steps that the facts do not show. Plain business English, no hype.
- Every benefit must follow from a specific fact (e.g. mandatory checks -> fewer data-entry errors;
  auto-numbering -> no duplicate document numbers; maintains its own records -> one place for this data).
  Do not claim reporting, traceability, compliance or analytics unless a fact shows it.
- Answer a FAQ only from the facts; leave out questions the facts cannot answer.
- "industries" is your recommendation of where the screen is relevant, with a reason; keep it to 0-3
  and only when the screen's subject makes it clear.
- howTo steps follow the screen's sections and fields in order; name fields by their labels; say which are
  mandatory. For a report or dashboard, explain how to view and filter it.
- FAQs (3-6) come from the validations, messages, calculations, lookups and mandatory rules in the facts.

Return JSON only:
{{"overview": "2-3 sentences: what the screen does and for whom",
  "users": ["roles that use it"],
  "benefits": ["3-5 business benefits"],
  "useCases": ["2-4 concrete situations"],
  "industries": [{{"industry": "...", "why": "..."}}],
  "howTo": {{"title": "How to ...", "steps": ["..."]}},
  "faqs": [{{"q": "...", "a": "..."}}],
  "marketing": {{"headline": "max 10 words", "blurb": "40-70 words", "bullets": ["3 short bullets"]}},
  "confidence": "high|medium|low"}}

FACTS:
{facts}"""

MODULE_PROMPT = """You write a product brochure section for the {name} module of GoodBooks ERP from VERIFIED FACTS.
Use only the facts; do not invent integrations, analytics, mobile, AI, real-time, regulation or compliance claims.
Every capability benefit must follow from the verified facts or feature overviews of that capability.
Return JSON only:
{{"tagline": "max 12 words",
  "overview": "80-120 words for a brochure",
  "capabilities": [{{"name": "capability name from the facts", "benefit": "1-2 sentences", "features": ["feature names"]}}],
  "idealFor": ["roles / teams"],
  "keyFacts": ["4-8 verified facts worth highlighting, quoted from the facts"],
  "industries": [{{"industry": "...", "why": "..."}}]}}

FACTS:
{facts}"""

PROCESS_PROMPT = """From the screens of the {name} module of GoodBooks ERP and the data they write and read,
describe the main end-to-end business processes (2-6). A process links screens where one screen's records
are used by the next (shared tables below). Use ONLY screen names from the list, exactly as written.
Return JSON only:
{{"processes": [{{"name": "...", "summary": "1-2 sentences", "steps": [{{"screen": "exact screen name", "action": "what the user does"}}]}}]}}

SCREENS (name | category | writes | reads):
{screens}

SHARED DATA FLOWS (table: written by -> read by):
{flows}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:20]


def _parse(raw: str) -> dict[str, Any]:
    from .llm import extract_json
    try:
        return extract_json(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw or "", re.S)
        return json.loads(m.group(0)) if m else {}


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------ facts

def field_facts(dictionary: dict[str, Any] | None, limit: int = 60) -> dict[str, Any]:
    """The parts of a field dictionary a business reader needs: sections, mandatory, rules, messages."""
    if not dictionary:
        return {}
    fields = [r for r in dictionary.get("fields", []) if r.get("visibleOnScreen")] or dictionary.get("fields", [])
    sections: dict[str, list[str]] = {}
    for r in sorted(fields, key=lambda r: r.get("order", 0)):
        where = " > ".join(x for x in (r.get("page"), r.get("section")) if x) or "Main form"
        mark = "*" if (r.get("mandatory") or {}).get("required") else ""
        sections.setdefault(where, []).append(f"{r.get('label')}{mark}")
    mandatory = [r["label"] for r in fields if (r.get("mandatory") or {}).get("required")]
    messages = sorted({m["message"] for r in fields for m in r.get("messages", [])})[:12]
    rules = sorted({v["rule"] for r in fields for v in r.get("validations", []) if v.get("layer") == "backend"})[:8]
    calcs = [f"{r['label']} = {r['calculation']['formula']}" for r in fields if r.get("calculation")][:8]
    options = [f"{r['label']}: " + ", ".join(str(v.get("label")) for v in r["allowedValues"][:8]) for r in fields if r.get("allowedValues")][:8]
    lookups = [f"{r['label']} (chosen from {r['lookup'].get('title') or r['lookup'].get('picklist')})" for r in fields if r.get("lookup")][:12]
    return {"sections": {k: v[:limit] for k, v in list(sections.items())[:15]}, "mandatoryFields": mandatory[:30],
            "validationMessages": messages, "backendRules": rules, "calculations": calcs, "options": options, "lookups": lookups,
            "storedIn": dictionary.get("table") or ""}


def feature_facts(catalog: dict[str, Any], feature: dict[str, Any], dictionary: dict[str, Any] | None) -> dict[str, Any]:
    verified = [c["text"] for c in feature.get("claims", []) if c.get("status") == "VERIFIED"]
    tables = [t.get("name") for t in (feature.get("technical") or {}).get("tables", []) if not t.get("shared")][:10]
    return {"module": catalog.get("moduleName") or catalog.get("module"), "capability": (feature.get("capability") or {}).get("name"),
            "screen": feature.get("name"), "category": feature.get("category"), "group": feature.get("group"),
            "verifiedFacts": verified, "dataTables": tables, "fields": field_facts(dictionary)}


def _dictionaries(module_dir: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for p in sorted((module_dir / "phase20-fields").glob("*_fields_v20.json")):
        if " copy" in p.name:
            continue
        d = _load(p)
        if isinstance(d, dict) and d.get("screenId"):
            out[d["screenId"]] = d
    return out


# ------------------------------------------------------------------ checks

def risky_terms(text: str, facts_text: str) -> list[str]:
    from .chat import RISKY_TERMS
    low, known = text.lower(), facts_text.lower()
    out = []
    for term in RISKY_TERMS:
        rx = rf"(?<![a-z]){re.escape(term)}s?(?![a-z])"
        if re.search(rx, low) and not re.search(rx, known):
            out.append(term)
    return out


def _clean_feature(d: dict[str, Any]) -> dict[str, Any]:
    s = lambda v: re.sub(r"\s+", " ", str(v or "")).strip()
    lst = lambda v, n: [s(x) for x in (v if isinstance(v, list) else []) if s(x)][:n]
    how = d.get("howTo") if isinstance(d.get("howTo"), dict) else {}
    mk = d.get("marketing") if isinstance(d.get("marketing"), dict) else {}
    return {"overview": s(d.get("overview")), "users": lst(d.get("users"), 8), "benefits": lst(d.get("benefits"), 6),
            "useCases": lst(d.get("useCases"), 5),
            "industries": [{"industry": s(x.get("industry")), "why": s(x.get("why"))} for x in (d.get("industries") or [])
                           if isinstance(x, dict) and s(x.get("industry"))][:3],
            "howTo": {"title": s(how.get("title")), "steps": lst(how.get("steps"), 15)},
            "faqs": [{"q": s(x.get("q")), "a": s(x.get("a"))} for x in (d.get("faqs") or []) if isinstance(x, dict) and s(x.get("q")) and s(x.get("a"))][:6],
            "marketing": {"headline": s(mk.get("headline")), "blurb": s(mk.get("blurb")), "bullets": lst(mk.get("bullets"), 4)},
            "confidence": s(d.get("confidence")).lower() or "medium"}


# ------------------------------------------------------------------ build

class _Cache:
    """Drafts by fact fingerprint. Worker threads share it, so writes are serialised."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = _load(path, {}) or {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self.data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            # Windows: antivirus/indexer can hold the file for a moment; retry instead of losing the draft.
            for attempt in range(10):
                try:
                    tmp.replace(self.path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(.2 * (attempt + 1))


def build_business_knowledge(module: str, llm: Any, module_dir: str | Path | None = None, workers: int | None = None,
                             limit: int | None = None, progress=print) -> dict[str, Any]:
    mdir = from_root(module_dir or f"outputs/modules/{module}")
    catalog = _load(mdir / "phase19-usage" / "usage_catalog_v19.json")
    if not catalog:
        raise FileNotFoundError(f"No usage catalog for {module}; run usage-build first")
    out_dir = mdir / "phase21-business"
    cache = _Cache(out_dir / "business_cache.json")
    dicts = _dictionaries(mdir)
    features = catalog.get("features", [])[: limit or None]
    workers = workers or int(os.getenv("PIE_AI_WORKERS") or 6)
    results: dict[str, Any] = {}
    failures: list[str] = []

    def one(f: dict[str, Any]) -> tuple[str, Any]:
        facts = feature_facts(catalog, f, dicts.get(f["screenId"]))
        key = f"feature:{PROMPT_VERSION}:{_hash(facts)}"
        hit = cache.get(key)
        if hit is None:
            hit = {"facts": facts, "draft": _clean_feature(_parse(llm.generate(FEATURE_PROMPT.format(facts=json.dumps(facts, ensure_ascii=False)))))}
            cache.put(key, hit)
        return f["screenId"], hit

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(one, f): f for f in features}
        for n, fut in enumerate(as_completed(futs), 1):
            f = futs[fut]
            try:
                sid, hit = fut.result()
                results[sid] = hit
            except Exception as exc:
                failures.append(f"{f.get('screenId')}: {type(exc).__name__}: {exc}"[:300])
            if n % 10 == 0 or n == len(futs):
                progress(f"{module}: {n}/{len(futs)} features drafted ({len(failures)} failed)")

    # Module brochure.
    caps = []
    by_cap: dict[str, list[dict[str, Any]]] = {}
    for f in catalog.get("features", []):
        by_cap.setdefault((f.get("capability") or {}).get("name", ""), []).append(f)
    for c in catalog.get("capabilities", []):
        fs = by_cap.get(c["name"], [])
        caps.append({"name": c["name"], "features": [f["name"] for f in fs][:25],
                     "verified": [c2["text"] for f in fs for c2 in f.get("claims", []) if c2.get("status") == "VERIFIED"][:8],
                     "overviews": [results[f["screenId"]]["draft"]["overview"] for f in fs if f["screenId"] in results][:5]})
    mfacts = {"module": catalog.get("moduleName"), "statistics": {k: v for k, v in catalog.get("statistics", {}).items() if k != "sharedPlatformTables"},
              "capabilities": caps}
    brochure = None
    key = f"module:{PROMPT_VERSION}:{_hash(mfacts)}"
    try:
        brochure = cache.get(key)
        if brochure is None:
            brochure = {"facts": mfacts, "draft": _parse(llm.generate(MODULE_PROMPT.format(name=catalog.get("moduleName"), facts=json.dumps(mfacts, ensure_ascii=False)[:60000])))}
            cache.put(key, brochure)
    except Exception as exc:
        failures.append(f"module brochure: {type(exc).__name__}: {exc}"[:300])

    # Processes from shared data flows.
    processes = None
    pfacts = process_facts(catalog)
    if pfacts["screens"]:
        key = f"process:{PROMPT_VERSION}:{_hash(pfacts)}"
        try:
            processes = cache.get(key)
            if processes is None:
                raw = _parse(llm.generate(PROCESS_PROMPT.format(name=catalog.get("moduleName"), screens="\n".join(pfacts["screens"])[:40000],
                                                                flows="\n".join(pfacts["flows"])[:20000] or "(none found)")))
                processes = {"facts": pfacts, "draft": validate_processes(raw, pfacts["names"])}
                cache.put(key, processes)
        except Exception as exc:
            failures.append(f"processes: {type(exc).__name__}: {exc}"[:300])

    package = {"businessVersion": BUSINESS_VERSION, "module": module, "moduleName": catalog.get("moduleName"),
               "generatedAt": _now(), "features": results, "brochure": brochure, "processes": processes, "failures": failures}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "business_knowledge_v25.json").write_text(json.dumps(package, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"status": "COMPLETED" if not failures else "PARTIAL", "module": module, "features": len(results),
            "brochure": bool(brochure), "processes": len(((processes or {}).get("draft") or {}).get("processes", [])),
            "failures": len(failures), "output": str(out_dir / "business_knowledge_v25.json")}


def process_facts(catalog: dict[str, Any]) -> dict[str, Any]:
    writers: dict[str, list[str]] = {}
    readers: dict[str, list[str]] = {}
    screens, names = [], []
    for f in catalog.get("features", []):
        tables = [t for t in (f.get("technical") or {}).get("tables", []) if not t.get("shared") and t.get("confidence", 0) >= .85]
        w = [t["name"] for t in tables if t.get("role") == "write"]
        r = [t["name"] for t in tables if t.get("role") != "write"]
        if not (w or r):
            continue
        names.append(f["name"])
        screens.append(f"{f['name']} | {f.get('category')} | {', '.join(w[:6]) or '-'} | {', '.join(r[:6]) or '-'}")
        for t in w:
            writers.setdefault(t, []).append(f["name"])
        for t in r:
            readers.setdefault(t, []).append(f["name"])
    flows = [f"{t}: {', '.join(writers[t][:4])} -> {', '.join(sorted(set(readers[t]) - set(writers[t]))[:6])}"
             for t in sorted(writers) if set(readers.get(t, [])) - set(writers[t])]
    return {"screens": screens[:400], "flows": flows[:200], "names": names}


def validate_processes(raw: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """Keep only steps that name a real screen of the module; drop processes left with < 2 steps."""
    known = {n.lower(): n for n in names}
    out = []
    for p in (raw.get("processes") or []) if isinstance(raw, dict) else []:
        if not isinstance(p, dict):
            continue
        steps = [{"screen": known[str(s.get("screen", "")).strip().lower()], "action": str(s.get("action", "")).strip()}
                 for s in p.get("steps") or [] if isinstance(s, dict) and str(s.get("screen", "")).strip().lower() in known]
        if len(steps) >= 2 and p.get("name"):
            out.append({"name": str(p["name"]).strip(), "summary": str(p.get("summary", "")).strip(), "steps": steps})
    return {"processes": out[:6]}


# ------------------------------------------------------------------ Qdrant points

def _pid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pie://business/" + "/".join(parts) + "/" + BUSINESS_VERSION))


def business_points(package: dict[str, Any], catalog: dict[str, Any], contexts: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Points with `text` (embedded by QdrantKMS.upsert_points)."""
    from .knowledge_ingest import _base_payload
    module, mname = package["module"], package.get("moduleName") or package["module"]
    ctx = contexts or {}
    feats = {f["screenId"]: f for f in catalog.get("features", [])}
    points: list[dict[str, Any]] = []

    def add(kid: str, kt: str, title: str, content: str, **kw: Any) -> None:
        facts_text = kw.pop("facts_text", "")
        risky = risky_terms(content, facts_text) if facts_text else []
        payload = _base_payload(knowledge_id=kid, module=module, knowledge_type=kt, title=title, content=content[:4000],
                                generation_method="ai", claim_type="inferred", confidence=.6, review_required=True,
                                knowledge_version=BUSINESS_VERSION, source_types=["AI draft from verified evidence"],
                                review_note=(f"Mentions {', '.join(risky)}, not found in the evidence" if risky else ""), **kw)
        points.append({"id": _pid(kid), "payload": payload, "text": f"{title} | {content} | {mname} | {kw.get('capability', '')} | {kw.get('screen_name', '')}"})

    for sid, hit in (package.get("features") or {}).items():
        f, d = feats.get(sid), hit.get("draft") or {}
        if not f or not d.get("overview"):
            continue
        facts_text = json.dumps(hit.get("facts"), ensure_ascii=False)
        common = {"capability": (f.get("capability") or {}).get("name", ""), "feature": f["name"], "screen_id": sid,
                  "screen_name": f["name"], "functionality": (ctx.get(sid) or {}).get("functionality", ""),
                  "readiness": f.get("readiness", ""), "category": f.get("category", ""), "facts_text": facts_text}
        inds = "; ".join(f"{x['industry']} ({x['why']})" for x in d.get("industries", []))
        value = (f"{f['name']} ({mname}): {d['overview']} Users: {', '.join(d['users']) or 'not stated'}. "
                 f"Benefits: {' '.join('- ' + b for b in d['benefits'])} Use cases: {' '.join('- ' + u for u in d['useCases'])}"
                 + (f" Possible industries (recommendation): {inds}." if inds else ""))
        add(f"BIZ-VALUE-{sid}", "BusinessValue", f"{f['name']}: business value", value, **common)
        if d["howTo"]["steps"]:
            add(f"BIZ-HOWTO-{sid}", "HowTo", d["howTo"]["title"] or f"How to use {f['name']}",
                " ".join(f"{i}. {s}" for i, s in enumerate(d["howTo"]["steps"], 1)), **common)
        for i, q in enumerate(d["faqs"]):
            add(f"BIZ-FAQ-{sid}-{i}", "FAQ", q["q"], f"Q: {q['q']} A: {q['a']}", **common)
        mk = d["marketing"]
        if mk["blurb"]:
            add(f"BIZ-COPY-{sid}", "MarketingCopy", mk["headline"] or f["name"],
                f"{mk['headline']}. {mk['blurb']} " + " ".join("- " + b for b in mk["bullets"]), **common)

    b = (package.get("brochure") or {}).get("draft") or {}
    if b.get("overview"):
        caps = " ".join(f"{c.get('name')}: {c.get('benefit')} (features: {', '.join((c.get('features') or [])[:8])})."
                        for c in b.get("capabilities") or [] if isinstance(c, dict))
        inds = "; ".join(f"{x.get('industry')} ({x.get('why')})" for x in b.get("industries") or [] if isinstance(x, dict))
        content = (f"{mname} module brochure. {b.get('tagline', '')}. {b['overview']} Capabilities: {caps} "
                   f"Ideal for: {', '.join(b.get('idealFor') or [])}. Key facts: {' '.join('- ' + str(k) for k in b.get('keyFacts') or [])}"
                   + (f" Possible industries (recommendation): {inds}." if inds else ""))
        add(f"BIZ-BROCHURE-{module}", "ModuleBrochure", f"{mname} module brochure", content,
            facts_text=json.dumps((package.get("brochure") or {}).get("facts"), ensure_ascii=False))
    for i, p in enumerate((((package.get("processes") or {}).get("draft") or {}).get("processes") or [])):
        steps = " ".join(f"{n}. {s['screen']}: {s['action']}" for n, s in enumerate(p["steps"], 1))
        add(f"BIZ-PROCESS-{module}-{i}", "Process", f"{p['name']} ({mname})", f"{p['summary']} Steps: {steps}",
            process_screens=[s["screen"] for s in p["steps"]])
    return points


def publish_business(kms: Any, module: str, module_dir: str | Path | None = None) -> dict[str, Any]:
    from .knowledge_ingest import screen_contexts
    mdir = from_root(module_dir or f"outputs/modules/{module}")
    package = _load(mdir / "phase21-business" / "business_knowledge_v25.json")
    catalog = _load(mdir / "phase19-usage" / "usage_catalog_v19.json")
    if not package or not catalog:
        raise FileNotFoundError(f"Run business-build --module {module} first")
    points = business_points(package, catalog, screen_contexts(mdir))
    kms.ensure_collection()
    # Drafts are replaced as a whole: a rebuilt screen may have fewer FAQs than before.
    kms.delete({"must": [{"key": "module", "match": {"value": module}},
                         {"key": "knowledge_type", "match": {"any": list(BUSINESS_TYPES)}}]})
    n = kms.upsert_points(points)
    kinds: dict[str, int] = {}
    for p in points:
        kinds[p["payload"]["knowledge_type"]] = kinds.get(p["payload"]["knowledge_type"], 0) + 1
    return {"status": "PUBLISHED", "module": module, "collection": kms.collection, "points": n, "byType": kinds,
            "flagged": sum(bool(p["payload"].get("review_note")) for p in points)}
