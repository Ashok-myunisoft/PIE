"""v0.19 Marketing Intelligence chat: retrieval-grounded answers from the Qdrant KMS.

Flow per question:
  1. understand  - intents, modules, industry and persona (deterministic, transparent)
  2. plan        - optional LLM call that turns the question into ERP search terms
  3. retrieve    - Qdrant vector search plus aggregations over the KMS (feature lists,
                   ticket statistics, internal coverage gaps)
  4. coverage    - decide what the knowledge base can and cannot answer
  5. answer      - LLM writes a marketing answer from numbered knowledge items only
  6. validate    - citations must refer to supplied items; sources are returned

Nothing is answered from the model's own knowledge: when the KMS holds no supporting
knowledge the response says so, and ticket questions without ticket data never reach
the LLM.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .llm import LLMProvider, OfflineProvider, extract_json
from .qdrant_kms import QdrantKMS

CHAT_VERSION = "0.19.0"
SNAPSHOT_TTL_SECONDS = 1800
MAX_ITEMS = 40
MAX_HISTORY_TURNS = 6
MAX_MESSAGE_CHARS = 2000

INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # v0.25: developer code questions, content creation, how-to, support and processes.
    ("code", re.compile(r"\b(bugs?|exceptions?|stack ?trace|debug\w*|null ?reference|crash\w*|which line|line \d+|lines? of code|source code|"
                        r"in the code|code behind|show (?:me )?(?:the )?(?:source )?code|implementation|implemented|refactor\w*|unit tests?|"
                        r"bll|dal|dto|query ?builder|endpoint class|stored procedure)\b", re.I)),
    ("content", re.compile(r"\b(brochures?|leaflets?|flyers?|data ?sheets?|campaigns?|blog|articles?|linkedin|social media|posts?|newsletters?|"
                           r"emails?|taglines?|headlines?|copy ?writing|web ?copy|website (?:copy|content|text)|landing page|press release|"
                           r"case stud(?:y|ies)|one[- ]pager|sales pitch|marketing content|content (?:for|about|on)|write (?:a|an|the|me)|draft (?:a|an|the))\b", re.I)),
    ("field", re.compile(r"\b(fields?|sections?|mandatory|required|validations?|columns?|mapped|default value|allowed values|dropdown|data type|"
                         r"what happens if (?:i|we|you) change|depends on|dependenc\w+)\b", re.I)),
    ("howto", re.compile(r"\b(how (?:do|can|should|would) (?:i|we|you|users?|a user)|how to|step[- ]by[- ]step|steps? (?:to|for)|procedure|"
                         r"user guide|guide (?:to|for|me)|walk ?me|tutorial|training|sop|user manual|set ?up|configure)\b", re.I)),
    ("support", re.compile(r"\b(why (?:can't|cannot|can not|does|doesn't|is|isn't|am i|won't)|error messages?|not (?:saving|working|allowed|showing)|"
                           r"unable to|cannot save|can't save|validation messages?|faqs?|troubleshoot\w*)\b", re.I)),
    ("process", re.compile(r"\b(business process(?:es)?|processes|end[- ]to[- ]end|life ?cycle|procure[- ]to[- ]pay|order[- ]to[- ]cash|"
                           r"process flow|business flow|sequence of screens)\b", re.I)),
    ("tickets", re.compile(r"\b(tickets?|issues?|complaints?|bugs?|incidents?|reported|support calls?|problems? (?:users|customers)|recurring)\b", re.I)),
    ("market_gap", re.compile(r"\b(missing|gaps?|in demand|demand|future development|roadmap|lack(?:ing)?|should we build|not available in our)\b", re.I)),
    ("differentiation", re.compile(r"\b(competitors?|competition|differentiat\w*|unique|usp|stand out|advanced|versus|vs\.?)\b", re.I)),
    ("demo", re.compile(r"\b(demo\w*|walk ?through|showcase|presentation|video)\b", re.I)),
    ("industry", re.compile(r"\b(industry|industries|sector|vertical|segment|manufactur\w*|companies|targeting)\b", re.I)),
    ("feature_discovery", re.compile(r"\b(features?|capabilit\w*|functionalit\w*|modules?|what can|offer|list)\b", re.I)),
]
INDUSTRY_RE = re.compile(r"(?:targeting|for|in|serve|serving)?\s*(?:the\s+|a\s+|an\s+)?((?:[a-z][a-z&-]+\s){0,2}[a-z][a-z&-]+)\s+(?:industry|industries|sector|manufactur\w*|companies|company|vertical|segment|makers?)\b", re.I)
PERSONA_RE = re.compile(r"\b((?:it|finance|production|plant|operations?|purchase|procurement|store|stores|warehouse|quality|hr|sales|supply chain|maintenance)\s+(?:manager|head|director|lead)|cio|cto|cfo|ceo|coo|owner|managing director|md)\b", re.I)
STOPWORDS = {"the", "a", "an", "our", "we", "i", "am", "to", "for", "in", "of", "and", "or", "what", "which", "are", "is", "top",
             "can", "me", "give", "list", "that", "this", "with", "on", "by", "most", "should", "be", "do", "does", "how"}
# Claim words a model tends to add on its own. If an answer uses one that no knowledge
# item contains, the answer is flagged so Marketing checks it before use.
RISKY_TERMS = ("real-time", "real time", "dashboard", "analytics", "ai-powered", "artificial intelligence", "machine learning",
               "mobile", "cloud", "integration", "integrates", "api", "predictive", "forecasting", "iot", "blockchain",
               "compliance", "certified", "iso", "gdpr", "encryption", "single sign-on", "sso", "workflow automation")
CITATION_RE = re.compile(r"\[\s*(K\d+(?:\s*[,;]\s*K\d+)*)\s*\]")

INTENT_LABELS = {
    "feature_discovery": "Feature discovery", "industry": "Industry targeting", "demo": "Demo preparation",
    "differentiation": "Competitive differentiation", "market_gap": "Market gaps", "tickets": "Ticket analysis",
    "general": "Product question", "field": "Field knowledge", "code": "Source code", "content": "Content creation",
    "howto": "How-to guide", "support": "Support / troubleshooting", "process": "Business process",
}
# v0.25 knowledge types and how the chat presents them.
BUSINESS_KINDS = {"BusinessValue": "business", "HowTo": "howto", "FAQ": "faq", "MarketingCopy": "copy",
                  "ModuleBrochure": "brochure", "Process": "process", "Document": "document"}
INTERNAL_TYPES = ["DepartmentKnowledge", "DepartmentManifest"]
CONTENT_TYPES = ["ModuleBrochure", "MarketingCopy", "BusinessValue", "UsageFeature", "UsageCapability", "Document"]
HOWTO_TYPES = ["HowTo", "Document", "FAQ", "SectionKnowledge", "Process"]
SUPPORT_TYPES = ["FAQ", "Document", "HowTo", "SectionKnowledge"]
PROCESS_TYPES = ["Process", "HowTo", "Document", "UsageFeature", "BusinessValue"]
VALUE_TYPES = ["BusinessValue", "ModuleBrochure", "MarketingCopy"]
MAX_CODE_ITEMS = 6

# ---------------------------------------------------------------- understanding


@dataclass
class Plan:
    intents: list[str]
    modules: list[str]
    industry: str = ""
    persona: str = ""
    search_queries: list[str] = field(default_factory=list)
    planner: str = "rules"
    raw_message: str = ""
    audience: str = "marketing"   # who the answer is written for (AUDIENCES)
    # The answer trace: one entry per pipeline step (understand, plan, retrieve, ...).
    steps: list[dict[str, Any]] = field(default_factory=list)

    def step(self, name: str, summary: str, **details: Any) -> None:
        self.steps.append({"step": name, "summary": summary, **details})

    @property
    def primary(self) -> str:
        return self.intents[0] if self.intents else "general"


def detect_intents(message: str) -> list[str]:
    found = [name for name, rx in INTENT_PATTERNS if rx.search(message)]
    # "features for the pump industry" is industry targeting, not a plain list, and
    # "all the features of each field" is a field question.
    if ("industry" in found or "field" in found) and "feature_discovery" in found:
        found.remove("feature_discovery")
    return found or ["general"]


def detect_industry(message: str) -> str:
    m = INDUSTRY_RE.search(message)
    if not m:
        return ""
    # Read backwards from "industry" until a stopword: "relevant to pump" -> "pump",
    # while "oil and gas" keeps its "and".
    stop = STOPWORDS - {"and"} | {"targeting", "serve", "serving", "relevant", "features", "feature"}
    words: list[str] = []
    for w in reversed(m.group(1).lower().split()):
        if w in stop or len(words) >= 3:
            break
        words.insert(0, w)
    while words and words[0] in {"and", "&"}:
        words.pop(0)
    return " ".join(words)


def detect_persona(message: str) -> str:
    m = PERSONA_RE.search(message)
    return m.group(1).strip() if m else ""


def detect_modules(message: str, known_modules: set[str]) -> list[str]:
    low = message.lower()
    return sorted(m for m in known_modules if m and len(m) > 2 and re.search(rf"\b{re.escape(m)}\b", low))


def selected_modules(module: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """The module scope chosen by the user: None/"" = all, "ess" or "ess,inventory" or a list."""
    parts = module.split(",") if isinstance(module, str) else list(module or [])
    return list(dict.fromkeys(str(m).strip().lower() for m in parts if str(m).strip()))


def keyword_query(message: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z0-9]+", message.lower()) if w not in STOPWORDS)


# ---------------------------------------------------------------- KMS snapshot

_SNAPSHOTS: dict[tuple[str, str], tuple[float, "Snapshot"]] = {}
KEEP_FIELDS = ("knowledge_id", "knowledge_type", "module", "capability", "capability_needs_review", "feature", "screen_id",
               "screen_name", "title", "content", "publication_status", "readiness", "category", "verified_claims",
               "review_claims", "ai_summary", "evidence", "migration_gaps", "features", "ready_features", "statistics",
               "capabilities", "ticket_id", "ticket_status", "ticket_priority", "ticket_category", "ticket_created_at",
               "ticket_source", "source_types", "generation_method", "confidence",
               "field_name", "field_label", "field_page", "field_section", "field_mandatory", "field_column", "field",
               "section_page", "section_name", "section_fields", "section_mandatory",
               "review_note", "source_file", "location", "process_screens")


@dataclass
class Snapshot:
    points: list[dict[str, Any]]

    def of_type(self, *types: str) -> list[dict[str, Any]]:
        return [p for p in self.points if p.get("knowledge_type") in types]

    @property
    def modules(self) -> set[str]:
        return {p.get("module", "") for p in self.points if p.get("module")}

    @property
    def usage_modules(self) -> set[str]:
        return {p["module"] for p in self.of_type("UsageModule")}

    def has(self, *types: str) -> bool:
        return any(p.get("knowledge_type") in types for p in self.points)


_REFRESHING: set[tuple[str, str]] = set()
_LOAD_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def _fetch_snapshot(kms: QdrantKMS, key: tuple[str, str]) -> Snapshot:
    try:
        points = [{k: p.get("payload", {}).get(k) for k in KEEP_FIELDS} | {"_id": p.get("id")} for p in kms.scroll_all()]
        snap = Snapshot(points)
        _SNAPSHOTS[key] = (time.time(), snap)
        return snap
    finally:
        _REFRESHING.discard(key)


def load_snapshot(kms: QdrantKMS, refresh: bool = False) -> Snapshot:
    """Aggregations need every point; scrolling a remote collection takes seconds, so the
    snapshot is cached and, once stale, served while a background refresh runs."""
    key = (getattr(kms.client, "url", "local"), kms.collection)
    hit = _SNAPSHOTS.get(key)
    if hit and not refresh:
        if time.time() - hit[0] > SNAPSHOT_TTL_SECONDS and key not in _REFRESHING:
            _REFRESHING.add(key)
            threading.Thread(target=_fetch_snapshot, args=(kms, key), daemon=True).start()
        return hit[1]
    # The first load (no snapshot yet) is the slow one. Requests arriving meanwhile (startup warm-up,
    # chat, status, the module list) wait for that one download instead of each starting their own.
    with _LOAD_LOCKS.setdefault(key, threading.Lock()):
        hit = _SNAPSHOTS.get(key)
        if hit and not refresh:
            return hit[1]
        return _fetch_snapshot(kms, key)


# ---------------------------------------------------------------- knowledge items


def _item(kind: str, title: str, text: str, status: str, payload: dict[str, Any] | None = None, score: float | None = None) -> dict[str, Any]:
    p = payload or {}
    return {"kind": kind, "title": title, "text": text, "status": status, "score": score,
            "module": p.get("module", ""), "capability": p.get("capability", ""), "knowledgeType": p.get("knowledge_type", kind),
            "knowledgeId": p.get("knowledge_id", ""), "screenId": p.get("screen_id", ""), "readiness": p.get("readiness", ""),
            "evidence": [e for e in (p.get("evidence") or []) if isinstance(e, dict)][:6]}


def item_from_point(p: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    kt = p.get("knowledge_type", "")
    if kt == "UsageFeature":
        verified = p.get("verified_claims") or []
        review = p.get("review_claims") or []
        text = f"{p.get('title')} — {p.get('category', '')} screen in {p.get('module')}, capability {p.get('capability')}"
        text += f" (readiness {p.get('readiness')}). Verified: " + " ".join(verified[:6]) if verified else ". No verified highlights."
        if review:
            text += " Needs review: " + " ".join(review[:3])
        if p.get("ai_summary"):
            text += f" AI-drafted description (unconfirmed): {p['ai_summary']}"
        return _item("feature", p.get("title", ""), text, "VERIFIED" if verified else "REVIEW", p, score)
    if kt == "SectionKnowledge":
        return _item("section", p.get("title", ""), p.get("content", ""), "VERIFIED", p, score)
    if kt == "FieldKnowledge":
        it = field_item(p)
        it["score"] = score
        return it
    if kt in BUSINESS_KINDS:
        status = "VERIFIED" if kt == "Document" else ("REVIEW" if p.get("publication_status") != "PUBLISHED" else "AI")
        where = f"{p.get('module')}" + (f", {p.get('screen_name')}" if p.get("screen_name") else "") if p.get("module") else ""
        text = (f"({kt}{'; ' + where if where else ''}" + (f"; document {p.get('source_file')} {p.get('location') or ''}".rstrip() if kt == "Document" else "")
                + f") {p.get('content', '')}"[:2400])
        if p.get("review_note"):
            text += f" Review note: {p['review_note']}."
        return _item(BUSINESS_KINDS[kt], p.get("title", ""), text, status, p, score)
    if kt == "Ticket":
        meta =", ".join(f"{k}={p.get(f'ticket_{k}')}" for k in ("status", "priority", "category") if p.get(f"ticket_{k}"))
        return _item("ticket", f"Ticket {p.get('ticket_id')}: {p.get('title')}", f"[{p.get('module') or 'no module'}; {meta}] {p.get('content', '')}"[:700], "OBSERVED", p, score)
    status = "REVIEW" if p.get("publication_status") != "PUBLISHED" else ("AI" if p.get("generation_method") == "ai" else "VERIFIED")
    return _item("knowledge", p.get("title", ""), f"({kt}, module {p.get('module') or 'unknown'}) {p.get('content', '')}"[:600], status, p, score)


def _search_filter(modules: list[str], include_review: bool, types: list[str] | None = None, exclude_types: list[str] | None = None) -> dict[str, Any] | None:
    must: list[dict[str, Any]] = []
    if not include_review:
        must.append({"key": "publication_status", "match": {"value": "PUBLISHED"}})
    if modules:
        must.append({"key": "module", "match": {"any": modules}})
    if types:
        must.append({"key": "knowledge_type", "match": {"any": types}})
    flt: dict[str, Any] = {}
    if must:
        flt["must"] = must
    if exclude_types:
        flt["must_not"] = [{"key": "knowledge_type", "match": {"any": exclude_types}}]
    return flt or None


TYPE_WEIGHT = {"UsageFeature": 1.35, "UsageCapability": 1.15, "UsageModule": 1.1, "BusinessRule": 1.05, "Workflow": 1.05,
               "Ticket": 1.0, "BusinessSummary": .85, "Configuration": 1.0, "Functionality": .8, "Feature": .75,
               "ModuleBrochure": 1.25, "Process": 1.2, "HowTo": 1.2, "Document": 1.2, "BusinessValue": 1.15, "FAQ": 1.15,
               "MarketingCopy": 1.1}
# Records about the same screen collapse to one item only within a family: the Usage feature beats the
# thin v0.17 records, but a screen's how-to, FAQs and business value are different knowledge.
SCREEN_FAMILY = {"UsageFeature": "usage", "Functionality": "usage", "Feature": "usage", "BusinessSummary": "usage",
                 "Workflow": "usage", "BusinessRule": "usage", "Configuration": "usage"}


def retrieve(kms: QdrantKMS, queries: list[str], modules: list[str], include_review: bool,
             types: list[str] | None = None, exclude_types: list[str] | None = None, per_query: int = 12) -> list[dict[str, Any]]:
    flt = _search_filter(modules, include_review, types, exclude_types)
    best: dict[str, dict[str, Any]] = {}
    queries = list(dict.fromkeys(q for q in queries if q.strip()))
    from .embeddings import prime
    prime(kms.embedder, queries)   # one batched embedding call for all queries (semantic search)
    for q in queries:
        for hit in kms.search(q, per_query, flt).get("result", []):
            p = hit.get("payload", {})
            score = float(hit.get("score", 0)) * TYPE_WEIGHT.get(p.get("knowledge_type", ""), 1.0)
            pid = str(hit.get("id"))
            if pid not in best or score > best[pid]["score"]:
                best[pid] = {"score": score, "payload": p}
    # One item per screen: the Usage feature beats thinner v0.17 records for it.
    per_screen: dict[str, dict[str, Any]] = {}
    rest = []
    for row in sorted(best.values(), key=lambda r: -r["score"]):
        kt = row["payload"].get("knowledge_type")
        sid = row["payload"].get("screen_id")
        if not sid or kt == "Ticket" or kt == "FAQ":
            rest.append(row)
            continue
        key = f"{sid}|{SCREEN_FAMILY.get(kt, kt)}"
        cur = per_screen.get(key)
        if cur is None or (kt == "UsageFeature" and cur["payload"].get("knowledge_type") != "UsageFeature"):
            per_screen[key] = row
    rows = sorted(list(per_screen.values()) + rest, key=lambda r: -r["score"])
    return [item_from_point(r["payload"], round(r["score"], 4)) for r in rows]


# ---------------------------------------------------------------- aggregations


def module_overview_items(snap: Snapshot, modules: list[str]) -> list[dict[str, Any]]:
    items = []
    usage = {p["module"]: p for p in snap.of_type("UsageModule")}
    caps = snap.of_type("UsageCapability")
    wanted = modules or sorted(snap.modules)
    screens_by_module: dict[str, set[str]] = {}
    for p in snap.points:
        if p.get("module") and p.get("screen_name") and p.get("knowledge_type") not in {"Ticket"}:
            screens_by_module.setdefault(p["module"], set()).add(p["screen_name"])
    basic_summary = []
    for m in wanted:
        if m in usage:
            items.append(_item("module", usage[m].get("title", m), usage[m].get("content", ""), "VERIFIED", usage[m]))
            for c in caps:
                if c.get("module") == m:
                    ready = c.get("ready_features", 0)
                    label = " (candidate grouping)" if c.get("capability_needs_review") else ""
                    items.append(_item("capability", c.get("capability", ""), f"{c.get('content', '')} {ready} marketing-ready.{label}", "VERIFIED", c))
        elif m in screens_by_module and not modules:
            # All-modules overview: one line per module keeps room for real features.
            basic_summary.append(f"{m} ({len(screens_by_module[m])} screens)")
        elif m in screens_by_module:
            names = sorted(screens_by_module[m])
            items.append(_item("module", f"{m} module (basic knowledge only)",
                               f"Module {m}: {len(names)} screens known from basic AI-generated screen descriptions (no verified feature analysis yet). "
                               f"Screens include: {', '.join(names[:30])}{' …' if len(names) > 30 else ''}.",
                               "AI", {"module": m, "knowledge_type": "ModuleCoverage"}))
    if basic_summary:
        items.append(_item("module", "Other modules (basic knowledge only)",
                           f"{len(basic_summary)} further modules are known from basic AI-generated screen descriptions, "
                           f"without verified feature analysis: {', '.join(basic_summary)}.",
                           "AI", {"knowledge_type": "ModuleCoverage"}))
    return items


def top_feature_items(snap: Snapshot, modules: list[str], per_capability: int = 3) -> list[dict[str, Any]]:
    """The strongest verified features of each capability, so feature lists can quote
    real highlights instead of capability names only."""
    feats = [p for p in snap.of_type("UsageFeature") if p.get("verified_claims") and (not modules or p.get("module") in modules)]
    by_cap: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for p in feats:
        by_cap.setdefault((p.get("module", ""), p.get("capability", "")), []).append(p)
    items = []
    for _, fs in sorted(by_cap.items()):
        fs.sort(key=lambda p: (p.get("readiness") != "READY", -len(p.get("verified_claims") or [])))
        items += [item_from_point(p) for p in fs[:per_capability]]
    return items


GENERIC_LABELS = {"code", "name", "type", "status", "category", "group", "remarks", "description", "date", "value", "id"}
ALL_FIELDS_RE = re.compile(r"\b(all|each|every|list|how many)\b.{0,40}\bfields?\b|\bfields\s+(?:of|in|on)\b", re.I)
MANDATORY_LIST_RE = re.compile(r"\b(which|what|list|show|give)\b.{0,40}\b(mandatory|required|compulsory)\b|\bmandatory fields\b", re.I)


def _words(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def mentioned_screens(message: str, snap: Snapshot) -> set[str]:
    """Screens with field knowledge named in the question ("... in Leave Request")."""
    low, compact = f" {_words(message)} ", re.sub(r"[^a-z0-9]", "", message.lower())
    names = {p.get("screen_name") for p in snap.of_type("FieldKnowledge") if p.get("screen_name")}
    hits = {n for n in names if f" {_words(n)} " in low or (len(n) > 6 and re.sub(r"[^a-z0-9]", "", n.lower()) in compact)}
    # "Leave Request List" named -> not also "Leave Request".
    return {h for h in hits if not any(h != o and _words(h) in _words(o) for o in hits)}


def in_modules(screens: set[str], snap: Snapshot, modules: list[str] | None) -> set[str]:
    """Keep the named screens that belong to the module scope (all of them when no scope)."""
    if not modules:
        return screens
    return {p.get("screen_name") for p in snap.of_type("FieldKnowledge")
            if p.get("screen_name") in screens and p.get("module") in modules}


def match_fields(message: str, snap: Snapshot, limit: int = 5, screens: set[str] | None = None,
                 modules: list[str] | None = None) -> list[dict[str, Any]]:
    """Fields named in the question by technical name, database column or label, within the
    screens (or modules) the question names."""
    low = message.lower()
    compact = re.sub(r"[^a-z0-9]", "", low)
    scored = []
    for p in snap.of_type("FieldKnowledge"):
        if screens and p.get("screen_name") not in screens:
            continue
        if modules and p.get("module") not in modules:
            continue
        name = (p.get("field_name") or "").lower()
        column = (p.get("field_column") or "").lower()
        label = re.sub(r"\s*\(internal id\)\s*", "", (p.get("field_label") or "").lower()).strip()
        score = 0
        bare = name[4:] if name.startswith("item") else name
        if len(name) > 3 and name in compact:
            score = 3 + len(name) / 100
        elif len(bare) > 5 and bare in compact:
            score = 2.9 + len(bare) / 100
        elif len(column) > 3 and re.search(rf"\b{re.escape(column)}\b", low):
            score = 3
        elif label and len(label) >= 4 and label not in GENERIC_LABELS and re.search(rf"\b{re.escape(label)}\b", low):
            score = 2 + len(label) / 100
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    # Keep the best matches; "item code" should not also pull every "code" field.
    best = scored[0][0] if scored else 0
    top = [p for sc, p in scored if sc >= best - 1]
    if not screens:
        # The same field on many screens: one per screen, so the answer can say where it appears.
        seen, spread = set(), []
        for p in top:
            if p.get("screen_name") not in seen:
                seen.add(p.get("screen_name"))
                spread.append(p)
        top = spread
    return top[:limit]


def field_item(p: dict[str, Any]) -> dict[str, Any]:
    from .field_intel import field_summary_text
    r = p.get("field") or {}
    parts = [field_summary_text(r, 3000) if r else p.get("content", "")]
    e = r.get("explanation") or {}
    if e:
        keys = ["purpose", "businessMeaning", "businessUsage", "transactionImpact", "example", "businessRules"]
        withheld = ""
        if e.get("unverifiedMentions"):
            # Usage/impact text that names modules absent from the evidence is not passed
            # to the chat model at all; asking it to repeat a warning is not reliable.
            keys = [k for k in keys if k not in {"businessUsage", "transactionImpact"}]
            withheld = (f" Business usage and transaction impact: not verified (the AI suggestion mentioned "
                        f"{', '.join(e['unverifiedMentions'])}, which the ERP sources do not show).")
        parts.append("AI-derived explanation (from the evidence): " + " ".join(f"{k}: {e.get(k)}" for k in keys if e.get(k))
                     + withheld + f" Confidence: {e.get('confidence')}." + (f" Review note: {e['reviewNote']}." if e.get("reviewNote") else ""))
    fe, be = r.get("frontend") or {}, r.get("backend") or {}
    mapping = []
    if fe.get("control"):
        mapping.append(f"Frontend: {fe['control']} in {fe.get('component')}" + (f", events {', '.join(x['event'] + '->' + x['handler'] for x in fe.get('events', []))}" if fe.get("events") else ""))
    if be.get("dto"):
        mapping.append(f"Backend: {be['dto']} ({be.get('dtoFile')}), queries {', '.join(be.get('queries', []))}")
    if be.get("endpoints"):
        mapping.append("Endpoints: " + ", ".join(f"{x['method']} {x['route']}" for x in be["endpoints"][:4]))
    if mapping:
        parts.append(" ".join(mapping) + ".")
    item = _item("field", p.get("title", ""), " ".join(parts), "VERIFIED", p)
    item["evidence"] = [{"source": ev.get("source", ""), "detail": (f"line {ev['line']}: " if ev.get("line") else "") + ev.get("fact", "")}
                        for ev in (r.get("evidence") or [])][:8]
    return item


SECTION_RE = re.compile(r"\b(sections?|tabs?|pages?|parts?)\b", re.I)
CHANGE_RE = re.compile(r"\b(change|changing|changed|modify|impact|affect|happens|depends?|dependenc\w*)\b", re.I)
IMPACT_KINDS = {"used in calculation of", "changing it updates", "controls visibility of", "lookup sets", "required depending on", "calculated from"}


def related_field_items(matched: list[dict[str, Any]], snap: Snapshot, limit: int = 4) -> list[dict[str, Any]]:
    """Fields a change to the matched field affects (calculations, updates, visibility)."""
    by_key = {(p.get("screen_id"), p.get("field_name")): p for p in snap.of_type("FieldKnowledge")}
    keys: list[tuple[str, str]] = []
    for p in matched:
        for d in (p.get("field") or {}).get("dependencies", []):
            if d.get("kind") in IMPACT_KINDS:
                keys += [(p.get("screen_id"), f) for f in d.get("fields", [])
                         if (p.get("screen_id"), f) in by_key and (p.get("screen_id"), f) not in keys and f != p.get("field_name")]
    return [field_item(by_key[k]) for k in keys[:limit]]


def section_items(snap: Snapshot, screens: set[str]) -> list[dict[str, Any]]:
    return [item_from_point(p) for p in snap.of_type("SectionKnowledge") if p.get("screen_name") in screens][:20]


def all_fields_answer(snap: Snapshot, screens: set[str] | None = None, modules: list[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Deterministic answer for "all fields" questions: exact, complete and cheap.
    With a named screen: every field of it. Without: an overview of all screens."""
    pts = [p for p in snap.of_type("FieldKnowledge") if (not screens or p.get("screen_name") in screens)
           and (not modules or p.get("module") in modules)]
    names = sorted({p.get("screen_name") for p in pts})
    if not screens and len(names) > 1:
        return screens_overview_answer(pts)
    fields = sorted((p.get("field") or {} for p in pts), key=lambda r: (r.get("screen", ""), r.get("order", 0)))
    items = field_overview_items(snap, set(names))
    for i, it in enumerate(items, 1):
        it["ref"] = f"K{i}"
    by_screen: dict[str, list[dict[str, Any]]] = {}
    for r in fields:
        by_screen.setdefault(r.get("screen", ""), []).append(r)
    out = []
    for screen, rs in by_screen.items():
        uniq = len({r["fieldName"] for r in rs})
        mand = sum((r.get("mandatory") or {}).get("required", False) for r in rs)
        calc = [r for r in rs if r.get("calculation")]
        out.append(f"## {screen}: {len(rs)} field definitions ({uniq} unique) [K1]")
        out.append(f"**{mand} mandatory**, **{len(calc)} calculated**, "
                   f"**{sum(bool((r.get('lookup') or {})) for r in rs)} lookups**, "
                   f"**{sum(bool(r.get('allowedValues')) for r in rs)} with fixed options**. Every field below has a full record "
                   f"(purpose, validations and messages, dependencies, database and backend/frontend mapping, usage, example and evidence) "
                   f"in the **Field Explorer** and the **Excel export**.")
        for r in rs:
            if r.get("duplicateDefinition"):
                out.append(f"\n> Source conflict: {r['duplicateDefinition']}.")
                break
        if calc:
            out.append("\n**Calculated fields**")
            out += [f"- {r['label']} (`{r['fieldName']}`) = `{r['calculation']['formula']}`" for r in calc]
        pages: dict[str, list[dict[str, Any]]] = {}
        for r in rs:
            pages.setdefault(_where(r), []).append(r)
        for page, prs in pages.items():
            ref = next((it["ref"] for it in items if it["title"].endswith(f"- {page} page fields")), "K1")
            out.append(f"\n### {page} ({len(prs)} fields) [{ref}]")
            out.append("| Field | Technical name | Type | Mandatory | Stored in |")
            out.append("|---|---|---|---|---|")
            for r in prs:
                m = r.get("mandatory") or {}
                req = "Yes" + (f" (when {m['conditional']['field']} = {m['conditional']['values']})" if m.get("conditional") else "") if m.get("required") else "No"
                db = r.get("database") or {}
                stored = (f"{db['table']}.{db['column']}" if db.get("column") else
                          f"{db['lookupDisplay']['table']}.{db['lookupDisplay']['column']} (lookup)" if db.get("lookupDisplay") else
                          f"{db['childTable']['table']} (grid rows)" if db.get("childTable") else db.get("storage", ""))
                t = r.get("type") or {}
                out.append(f"| {r['label']} | `{r['fieldName']}` | {t.get('ui') or t.get('form')} | {req} | {stored} |")
    return "\n".join(out), items


def _where(r: dict[str, Any]) -> str:
    """Tab, else section, else hidden: ESS screens are organised by sections instead of tabs."""
    if r.get("page"):
        return r["page"]
    if r.get("section"):
        return r["section"]
    return "Main form" if r.get("visibleOnScreen") else "Not shown on screen (system / hidden)"


def mandatory_fields_answer(snap: Snapshot, screens: set[str]) -> tuple[str, list[dict[str, Any]]]:
    """Deterministic list of mandatory fields of the named screens, with their conditions and messages."""
    out, items = [], []
    for screen in sorted(screens):
        pts = [p for p in snap.of_type("FieldKnowledge") if p.get("screen_name") == screen]
        rs = sorted((p.get("field") or {} for p in pts), key=lambda r: r.get("order", 0))
        mand = [r for r in rs if (r.get("mandatory") or {}).get("required")]
        ref = f"K{len(items) + 1}"
        items.append(dict(_item("field_overview", f"{screen}: mandatory fields",
                                f"{screen}: {len(mand)} of {len(rs)} fields are mandatory: " + ", ".join(f"{r['label']} ({r['fieldName']})" for r in mand) + ".",
                                "VERIFIED", {"knowledge_type": "FieldOverview", "module": pts[0].get("module", "") if pts else ""}), ref=ref))
        out.append(f"## {screen}: {len(mand)} mandatory fields (of {len(rs)}) [{ref}]")
        if not mand:
            out.append("No field of this screen is marked mandatory in the form definition.")
            continue
        out += ["| Field | Technical name | Where | Condition | Checked by | Message |", "|---|---|---|---|---|---|"]
        for r in mand:
            m = r["mandatory"]
            cond = f"only when {m['conditional']['field']} = {m['conditional']['values']}" if m.get("conditional") else "always"
            layers = ", ".join(sorted({v["layer"] for v in r.get("validations", []) if "equired" in v["rule"] or "empty" in v["rule"] or "NOT NULL" in v["rule"]})) or "frontend"
            msg = "; ".join(f"“{x['message']}”" for x in r.get("messages", [])) or "—"
            out.append(f"| {r['label']} | `{r['fieldName']}` | {_where(r)} | {cond} | {layers} | {msg} |")
    out.append("\nEvery field's full record (validations, dependencies, database mapping, evidence) is in the **Field Explorer**.")
    return "\n".join(out), items


HIGHLIGHT_ORDER = ("Business rule:", "Validates", "Maintains its own records", "Assigns codes", "Supports", "Works with")


def best_highlight(claims: list[str]) -> str:
    """The most telling verified claim: a business rule beats a field count."""
    for prefix in HIGHLIGHT_ORDER:
        for c in claims:
            if c.startswith(prefix):
                return c
    return claims[0] if claims else ""


def feature_list_answer(snap: Snapshot, modules: list[str]) -> tuple[str, list[dict[str, Any]]]:
    """Deterministic feature list: module -> capability -> features, each with its first verified highlight."""
    usage = sorted(snap.usage_modules)
    wanted = [m for m in (modules or usage) if m in usage]
    items: list[dict[str, Any]] = []

    def ref(it: dict[str, Any]) -> str:
        it["ref"] = f"K{len(items) + 1}"
        items.append(it)
        return it["ref"]

    out = []
    for m in wanted:
        mod = next(p for p in snap.of_type("UsageModule") if p.get("module") == m)
        st = mod.get("statistics") or {}
        out.append(f"## {mod.get('title', m).replace(' overview', '')} [{ref(_item('module', mod.get('title', m), mod.get('content', ''), 'VERIFIED', mod))}]")
        out.append(f"**{st.get('features', '?')} features** in **{st.get('capabilities', '?')} capabilities**, "
                   f"**{st.get('ready', '?')} marketing-ready**, {st.get('verifiedClaims', '?')} verified highlights.")
        feats = [p for p in snap.of_type("UsageFeature") if p.get("module") == m]
        caps = [p for p in snap.of_type("UsageCapability") if p.get("module") == m]
        for c in sorted(caps, key=lambda c: -len(c.get("features") or [])):
            cf = sorted((f for f in feats if f.get("capability") == c.get("capability")),
                        key=lambda f: ({"READY": 0, "PARTIAL": 1}.get(f.get("readiness"), 2), f.get("title", "")))
            out.append(f"\n### {c.get('capability')} ({len(cf)} features{', candidate grouping' if c.get('capability_needs_review') else ''})")
            for f in cf:
                hl = best_highlight(f.get("verified_claims") or [])
                tag = {"READY": "✓ marketing-ready", "PARTIAL": "partly verified"}.get(f.get("readiness"), "needs evidence")
                out.append(f"- **{f.get('title')}**, {tag}" + (f": {hl}" if hl else "") + f" [{ref(item_from_point(f))}]")
    basic = sorted(snap.modules - set(usage))
    if basic and not modules:
        out.append(f"\n**Other modules** (basic screen descriptions only, no verified analysis yet): {', '.join(basic)}.")
    return "\n".join(out), items


def screens_overview_answer(pts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = {}
    for p in pts:
        by.setdefault(p.get("screen_name", ""), []).append(p.get("field") or {})
    mods = sorted({p.get("module", "") for p in pts})
    items = []
    lines = [f"## Field-level knowledge: {len(by)} screens, {len(pts)} field definitions ({', '.join(mods)})", "",
             "Ask about one screen to get every field in detail, e.g. *\"all fields of Leave Request\"*. "
             "The **Field Explorer** has every field of every screen with all attributes and evidence.", "",
             "| Screen | Module | Fields | Mandatory | Calculated | Stored in | Tabs / sections |", "|---|---|---|---|---|---|---|"]
    for i, (screen, rs) in enumerate(sorted(by.items(), key=lambda x: (x[1][0].get("module", ""), x[0])), 1):
        mand = sum((r.get("mandatory") or {}).get("required", False) for r in rs)
        calc = sum(bool(r.get("calculation")) for r in rs)
        tables = sorted({(r.get("database") or {}).get("table") for r in rs if (r.get("database") or {}).get("table")})
        secs = sorted({" > ".join(x for x in (r.get("page"), r.get("section")) if x) for r in rs if r.get("page") or r.get("section")})
        ref = f"K{i}"
        text = (f"{screen} ({rs[0].get('module')}): {len(rs)} fields, {mand} mandatory, {calc} calculated, stored in {', '.join(tables) or 'no table found'}; "
                f"tabs/sections: {', '.join(secs) or 'none'}.")
        items.append(dict(_item("field_overview", f"{screen}: field overview", text, "VERIFIED", {"knowledge_type": "FieldOverview", "module": rs[0].get("module", "")}), ref=ref))
        lines.append(f"| {screen} | {rs[0].get('module')} | {len(rs)} | {mand} | {calc} | {', '.join(tables) or '—'} | {', '.join(secs[:4]) + (' …' if len(secs) > 4 else '') or '—'} [{ref}] |")
    return "\n".join(lines), items


def field_overview_items(snap: Snapshot, screens: set[str] | None = None, modules: list[str] | None = None) -> list[dict[str, Any]]:
    fields = [p.get("field") or {} for p in snap.of_type("FieldKnowledge")
              if (not screens or p.get("screen_name") in screens) and (not modules or p.get("module") in modules)]
    by_screen: dict[str, list[dict[str, Any]]] = {}
    for r in fields:
        if r:
            by_screen.setdefault(r.get("screen", ""), []).append(r)
    items = []
    for screen, rs in by_screen.items():
        rs.sort(key=lambda r: r.get("order", 0))
        pages = Counter(_where(r) for r in rs)
        mandatory = [f"{r['label']} ({r['fieldName']})" for r in rs if (r.get("mandatory") or {}).get("required")]
        calculated = [f"{r['label']} = {r['calculation']['formula']}" for r in rs if r.get("calculation")]
        stored = Counter((r.get("database") or {}).get("storage", "") for r in rs)
        dup = sorted({r["duplicateDefinition"] for r in rs if r.get("duplicateDefinition")})
        items.append(_item("field_overview", f"{screen}: field overview",
                           f"{screen} has {len(rs)} field definitions ({len(set(r['fieldName'] for r in rs))} unique). Fields per page: "
                           + ", ".join(f"{k} {v}" for k, v in pages.most_common()) + f". Mandatory fields: {', '.join(mandatory)}. "
                           + f"Calculated fields: {'; '.join(calculated) or 'none'}. Storage: " + ", ".join(f"{k} {v}" for k, v in stored.items())
                           + (f". Source conflict: {'; '.join(dup)}" if dup else "") + ".", "VERIFIED", {"knowledge_type": "FieldOverview", "module": rs[0].get("module", "")}))
        by_page: dict[str, list[str]] = {}
        for r in rs:
            by_page.setdefault(_where(r), []).append(
                f"{r['label']}{'*' if (r.get('mandatory') or {}).get('required') else ''} [{(r.get('type') or {}).get('ui') or (r.get('type') or {}).get('form')}]")
        for page, labels in by_page.items():
            items.append(_item("field_overview", f"{screen} - {page} page fields",
                               f"{page} page of {screen}, {len(labels)} fields (* = mandatory): " + "; ".join(labels) + ".", "VERIFIED",
                               {"knowledge_type": "FieldOverview", "module": rs[0].get("module", "")}))
    return items


def ticket_stat_items(snap: Snapshot, modules: list[str]) -> list[dict[str, Any]]:
    tickets = [p for p in snap.of_type("Ticket") if not modules or p.get("module") in modules]
    if not tickets:
        return []
    by = lambda k: Counter((p.get(k) or "(not set)") for p in tickets).most_common(10)
    words = Counter()
    for p in tickets:
        toks = [w for w in re.findall(r"[a-z]{3,}", (p.get("title") or "").lower()) if w not in STOPWORDS]
        words.update(f"{a} {b}" for a, b in zip(toks, toks[1:]))
    themes = [f"'{w}' ({n})" for w, n in words.most_common(10) if n > 1]
    text = (f"{len(tickets)} tickets in the knowledge base. By module: {by('module')}. By status: {by('ticket_status')}. "
            f"By priority: {by('ticket_priority')}. By category: {by('ticket_category')}. "
            f"Recurring title phrases: {', '.join(themes) if themes else 'none repeated'}.")
    return [_item("ticket_stats", "Ticket statistics", text, "OBSERVED", {"knowledge_type": "TicketStatistics"})]


def internal_gap_items(snap: Snapshot, modules: list[str]) -> list[dict[str, Any]]:
    items = []
    feats = [p for p in snap.of_type("UsageFeature") if not modules or p.get("module") in modules]
    by_mod: dict[str, list[dict[str, Any]]] = {}
    for p in feats:
        by_mod.setdefault(p["module"], []).append(p)
    for m, fs in by_mod.items():
        needs = [p["title"] for p in fs if p.get("readiness") == "NEEDS_EVIDENCE"]
        legacy = [p["title"] for p in fs if (p.get("migration_gaps") or 0) > 0]
        caps = Counter(p.get("capability") for p in fs)
        thin = [f"{c} ({n})" for c, n in caps.items() if n <= 1]
        items.append(_item("gaps", f"Internal coverage observations for {m}",
                           f"Module {m}: {len(needs)} features without verified evidence ({', '.join(needs[:12])}); "
                           f"{len(legacy)} features still use legacy backend services not found in the current backend ({', '.join(legacy[:12])}); "
                           f"capabilities with only one feature: {', '.join(thin) or 'none'}.",
                           "OBSERVED", {"module": m, "knowledge_type": "CoverageGaps"}))
    all_modules = snap.modules
    basic = sorted(all_modules - snap.usage_modules)
    if basic and not modules:
        items.append(_item("gaps", "Modules without verified feature analysis",
                           f"{len(basic)} modules have only basic AI-generated screen descriptions and no verified feature analysis yet: {', '.join(basic[:40])}.",
                           "OBSERVED", {"knowledge_type": "CoverageGaps"}))
    return items


def differentiator_items(snap: Snapshot, modules: list[str]) -> list[dict[str, Any]]:
    feats = [p for p in snap.of_type("UsageFeature") if p.get("readiness") == "READY" and (not modules or p.get("module") in modules)]
    def strength(p: dict[str, Any]) -> int:
        claims = " ".join(p.get("verified_claims") or []).lower()
        return len(p.get("verified_claims") or []) + 2 * claims.count("business rule") + claims.count("auto-numbering") + claims.count("validates")
    return [item_from_point(p) for p in sorted(feats, key=lambda p: -strength(p))[:10]]


# ---------------------------------------------------------------- prompts

# ---------------------------------------------------------------- audience (who the answer is for)
# The audience changes only how the knowledge is presented: priorities, technical depth,
# terminology, structure and length. The grounding rules and the knowledge are the same for all.
AUDIENCES: dict[str, dict[str, Any]] = {
    "marketing": {
        "label": "Marketing Team", "technical": False,
        "intro": "You are PIE, the Marketing Intelligence Assistant for GoodBooks ERP. You help the Marketing team understand the product, target industries, prepare demos and position the product.",
        "language": "Translate technical names into business language (e.g. table MITEM -> item master data); keep screen/feature names as given.",
        "style": "clear, concise, marketing-friendly English. Use Markdown: short headings, bullet lists or tables. No preamble, no closing questions. Keep answers under about 450 words unless a list of features is requested.",
        "focus": "product capabilities, business value, industry relevance, differentiators, use cases and customer-facing talking points",
    },
    "client_nontechnical": {
        "label": "Client (non-technical)", "technical": False,
        "intro": "You are PIE, the knowledge assistant for GoodBooks ERP. You are answering a client who is a business user, not a technical person, and wants to understand what the product does for them.",
        "language": "Use plain business language. Do not mention table, column, API, file, class or code names and avoid technical terms; explain what a screen does for its user and why it helps. Give technical details only if the question explicitly asks for them.",
        "style": "friendly, plain English a business user understands. Use Markdown: short paragraphs and bullet lists; lead with what it does and the benefit. No sales hype, no preamble, no closing questions. Keep answers under about 350 words unless a list is requested.",
        "focus": "what the module or screen does, how it helps day-to-day work and the business benefits",
    },
    "client_technical": {
        "label": "Client (technical)", "technical": True,
        "intro": "You are PIE, the knowledge assistant for GoodBooks ERP. You are answering a technical person at a client (IT or implementation lead) who is evaluating how the product works.",
        "language": "Use technical terms where they help: screens and workflows, configuration, validations, APIs, which tables store the data and integrations, but only as the items state them. Name APIs and tables when the items give them; leave out source-file paths and class names unless asked.",
        "style": "precise, structured technical English. Use Markdown: headings, bullet lists and tables. No marketing language, no preamble, no closing questions. Keep answers under about 550 words unless a list is requested.",
        "focus": "workflows, configuration, validations, data storage, APIs and integrations",
    },
    "developer": {
        "label": "Developer", "technical": True,
        "intro": "You are PIE, the knowledge assistant for GoodBooks ERP. You are answering a GoodBooks developer who needs implementation detail.",
        "language": "Keep technical names exactly as the items give them: API routes, endpoint and service classes, source files, tables and columns, DTO fields, validations and dependencies. Format identifiers as `code`. Never guess names, signatures or logic that the items do not state.",
        "style": "developer documentation style. Use Markdown: headings, bullet lists, tables and `code` for identifiers. No marketing language, no preamble, no closing questions. Keep answers under about 650 words unless a full list is requested.",
        "focus": "architecture, APIs, backend classes, source references, database tables and columns, validations, dependencies and technical logic",
    },
    "stakeholder": {
        "label": "Stakeholder", "technical": False,
        "intro": "You are PIE, the knowledge assistant for GoodBooks ERP. You are answering a business stakeholder or manager who needs a decision-ready summary.",
        "language": "Use business language; avoid low-level technical names (tables, files, classes, APIs) unless the question asks for them. Verification status, readiness and gaps matter more than implementation detail.",
        "style": "concise executive summary. Start with a 2-3 line bottom line, then short bullets: what it covers, business value, status and readiness (verified vs unconfirmed, marketing-ready counts when items give them), risks and gaps, and effort only if an item states it. Use Markdown. No preamble, no closing questions. Keep answers under about 300 words.",
        "focus": "functionality, business value, implementation status and readiness, impact, risks, gaps and outcomes",
    },
}
DEFAULT_AUDIENCE = "marketing"
FEATURE_LIST_RE = re.compile(r"\b(list|all (?:the )?features|every feature|feature list|features list)\b", re.I)


def audience_of(key: str | None) -> tuple[str, dict[str, Any]]:
    k = (key or DEFAULT_AUDIENCE).strip().lower().replace("-", "_")
    return (k, AUDIENCES[k]) if k in AUDIENCES else (DEFAULT_AUDIENCE, AUDIENCES[DEFAULT_AUDIENCE])


def system_prompt(audience: str | None = None) -> str:
    """Grounding rules are fixed; the introduction, language rule and style follow the audience."""
    a = audience_of(audience)[1]
    return (SYSTEM_PROMPT_TEMPLATE.replace("{intro}", a["intro"]).replace("{language}", a["language"])
            .replace("{style}", a["style"]))


SYSTEM_PROMPT_TEMPLATE = """{intro}

GROUNDING RULES (strict):
1. Use ONLY the numbered KNOWLEDGE ITEMS supplied with the question. Never use outside knowledge about GoodBooks, its customers, competitors, tickets or the market.
2. Cite the items you use with their reference, e.g. [K3], right after the statement. Every product feature, capability, number or ticket you mention must have a citation.
3. Never invent features, capabilities, integrations, customers, metrics, prices, tickets or competitor facts. If the items do not support something, say it is not in the PIE knowledge base.
4. You MAY explain why a cited feature matters for an industry or a persona, and what business value it brings. Phrase this as recommendation ("relevant because…", "you can position…"), not as a product fact. Do not claim a feature is specific to an industry unless an item says so.
5. Items marked REVIEW or AI are unconfirmed: write "(unconfirmed)" after the statement that uses them. If an item has a "Review note" or "Source conflict", state it next to the statement it concerns. Prefer VERIFIED items; use unconfirmed items only when no verified item covers the point. In a marketing piece (brochure, web copy, post, email) you may instead list every statement taken from unconfirmed items, with its citation, in a final "To confirm before publishing" section.
6. When COVERAGE NOTES say knowledge is missing (for example no competitor, market or ticket data), state that clearly and briefly, then give what the knowledge does support.
7. {language}
8. Describe only what an item says a feature does. Do not add capabilities an item does not state (for example dashboards, real-time tracking, analytics, mobile access, integrations).
9. Cite each item separately, e.g. [K2][K5], never as a comma list.

STYLE: {style}"""
SYSTEM_PROMPT = system_prompt(DEFAULT_AUDIENCE)

INTENT_GUIDE = {
    "feature_discovery": "Group features by module and capability. For each capability give the key features with one highlight taken from that feature's own verified text (feature items). A feature that appears only by name in a capability item gets its name only, no description. Say which modules have verified analysis and which only basic descriptions.",
    "industry": "For the industry '{industry}': list the most relevant features. For each give: why it is relevant for this industry, the business value, and a demo/pitch angle, with citations. A table is fine. Finish with 1-2 lines on what knowledge would sharpen industry targeting if relevant.",
    "demo": "Suggest a demo flow for the persona '{persona}' in the '{industry}' context: 5-8 numbered steps, each with the feature/screen, what to show (only what its item states) and the talking point for this persona, with citations. Order steps by what this persona evaluates first; for technical personas (IT manager, CIO, CTO) start with configuration and setup items and data controls such as validations. If the knowledge has nothing on topics this persona usually asks about (security, user roles, integration, APIs), say so in one line. Add a one-line opening and closing message.",
    "differentiation": "Present candidate differentiators found in the product knowledge with citations. Lead with verified business rules (quote them), then automation (auto-numbering, validations), then breadth of a capability. Field counts alone are not differentiators; mention them at most as supporting detail. State clearly that PIE has no competitor data, so these are strengths to validate against competitors, not confirmed differentiators.",
    "market_gap": "State that PIE has no market-demand data. Then list internal gaps observed in the knowledge (features without evidence, legacy services, thin capabilities, modules without verified analysis) with citations, and suggest what knowledge would be needed to assess market gaps.",
    "tickets": "Summarise the ticket knowledge: totals, modules with most tickets, common problems and recurring themes, with citations. Note what Marketing should be aware of.",
    "general": "Answer the question directly from the knowledge items with citations.",
    "field": ("Put a citation such as [K1] at the end of every table row and every bullet. Use the attribute values exactly as the item states them; do not reinterpret them. "
              "Text after 'AI-derived explanation' is AI-derived: label it so, and repeat its review note. Never add consequences of a change that the item does not state. "
              "If the question is about specific fields: for each field give a structured answer (short headings or a table) with the attributes the question asks for, "
              "or all available ones: purpose and business meaning, type and allowed values, default, mandatory and validations (quote the messages), "
              "dependencies and calculation, data source/lookup, database table and column, backend and frontend mapping, related masters and modules, "
              "transaction impact, example, business rules, configuration dependency. Include only what the items contain; say 'not found in the analysed sources' "
              "for an asked attribute that is missing. Mark explanation text as AI-derived and repeat any review note or source conflict. "
              "If the question asks about all or many fields: summarise by page with field counts, list mandatory and calculated fields, and say that the complete "
              "per-field dictionary is in the Field Explorer and the Excel export."),
    "code": ("Items of kind code are real GB5 source lines shown as 'NNNN| code' with the file path in the title. Answer with exact "
             "locations as `path:line` taken from those items, and put the item's citation (e.g. [K2]) after every location and "
             "every quoted line. Explain what the code does step by step. If the question is about an "
             "error, a bug or wrong behaviour: name the most likely line(s), quote them, explain why they cause it, and propose a concrete "
             "fix as a code block. Say clearly when the supplied code is not enough to be sure, and name the file or method to inspect "
             "next. Never invent code, file names, methods or line numbers that are not in the items."),
    "content": ("Write the requested piece (brochure section, web copy, social post, email, headline, one-pager...) ready to use: a strong "
                "headline, short sections or bullets, benefit-led wording, a call to action when it fits. Build it from the items: verified "
                "facts and brochure/business-value/marketing-copy items. Keep the [K#] citations after statements. End with a 'To confirm "
                "before publishing' section listing statements that came from AI or REVIEW items."),
    "howto": ("Give numbered step-by-step instructions for the task, naming screens, tabs/sections and fields as the items give them, "
              "marking mandatory fields and adding tips from FAQ or validation items. Prefer Document (manual) items, then HowTo items; "
              "say when steps are AI-drafted (unconfirmed)."),
    "support": ("Answer the user's problem: the likely cause taken from validations, messages, mandatory rules or FAQ items (quote "
                "exact messages), then what to do. If the items do not explain the cause, say so and suggest what to check."),
    "process": ("Describe the end-to-end business process as ordered steps across screens (screen -> what the user does -> what is "
                "passed on to the next screen), citing Process items and the features of each step. Mention which steps are AI-drafted."),
}

PLANNER_PROMPT = """You turn a question about GoodBooks ERP (from marketing, a client or a developer) into search queries for the knowledge search.
The knowledge base describes ERP screens and features using ERP vocabulary.
Available modules: {modules}
Capabilities with detailed knowledge: {capabilities}

Question: {question}

If the question names a persona, include queries for what that role cares about, e.g. IT Manager: user roles, security, configuration, parameters, integration, API, data import; Finance Manager: costing, tax, accounts.
Return JSON only: {{"searchQueries": ["4 to 8 short keyword queries in ERP vocabulary that would find relevant features, e.g. for a manufacturing industry: bill of materials, batch traceability, quality inspection"], "industry": "industry named in the question or empty", "persona": "role named in the question or empty"}}"""


def technical_items(items: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    """For technical audiences: the APIs, backend classes and tables behind the screens the answer is about,
    taken from the relationship graph. Screens without a graph (or without APIs) add nothing."""
    from .relations import screen_relations
    out, seen = [], set()
    for it in items:
        sid = it.get("screenId")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        try:
            r = screen_relations(sid)
        except Exception:
            r = None
        if not r or not r["apis"]:
            continue
        apis = "; ".join(
            f"{a['method']} {a['route']} -> " + (", ".join(
                f"{e['name']} ({e['file']})" + (f" using {', '.join(s['name'] for s in e['services'])}" if e["services"] else "")
                for e in a["endpoints"]) or "no backend endpoint found") for a in r["apis"][:6])
        writes = [t["name"] for t in r["tables"] if "WRITE" in t["operations"]]
        reads = [t["name"] for t in r["tables"] if "WRITE" not in t["operations"]][:12]
        stored = "; ".join(f"{t['name']}: " + ", ".join(f"{f['label']} = {f['column']}" for f in t["fields"][:8])
                           for t in r["tables"] if t["fields"])
        text = (f"Technical relationships of {r['screen']['label']} (from code analysis): APIs called: {apis}. "
                f"Tables written: {', '.join(writes) or 'none found'}. Tables read: {', '.join(reads) or 'none found'}."
                + (f" Screen fields stored in: {stored}." if stored else "")
                + " Table links are inferred from the SQL in the backend code.")
        out.append(_item("technical", f"{r['screen']['label']}: APIs, backend and tables", text[:1800], "OBSERVED",
                         {"module": r["screen"]["module"], "screen_id": sid, "knowledge_type": "RelationshipGraph",
                          "knowledge_id": f"REL-{sid}"}))
        if len(out) >= limit:
            break
    return out


def screen_code_files(items: list[dict[str, Any]], limit: int = 12) -> list[str]:
    """Endpoint and service source files behind the screens the answer is about (relationship graph),
    as paths in the code index (relative to data/erp)."""
    from .relations import screen_relations
    files: list[str] = []
    for sid in dict.fromkeys(it.get("screenId") for it in items if it.get("screenId")):
        try:
            r = screen_relations(sid)
        except Exception:
            r = None
        for a in (r or {}).get("apis", []):
            for e in a.get("endpoints", []):
                for f in [e.get("file")] + [s.get("file") for s in e.get("services", [])]:
                    if f:
                        f = f if f.startswith(("backend/", "frontend/")) else "backend/" + f
                        if f not in files:
                            files.append(f)
        if len(files) >= limit:
            break
    return files[:limit]


def code_items(code_kms: QdrantKMS, queries: list[str], files: list[str]) -> list[dict[str, Any]]:
    from .code_index import numbered, search_code
    out = []
    for row in search_code(code_kms, queries, files, MAX_CODE_ITEMS):
        p = row["payload"]
        title = f"{p.get('file')}:{p.get('start_line')}-{p.get('end_line')}" + (f" ({p.get('class_name')})" if p.get("class_name") else "")
        text = (f"[{p.get('language')}; layer {p.get('layer')}; module {p.get('code_module')}"
                + (f"; members {', '.join(p.get('members') or [])}" if p.get("members") else "") + "]\n" + numbered(p))
        it = _item("code", title, text, "OBSERVED", {"module": p.get("code_module", ""), "knowledge_type": "CodeChunk",
                                                     "knowledge_id": p.get("knowledge_id", "")}, round(row["score"], 4))
        it["evidence"] = [{"source": p.get("file", ""), "detail": f"lines {p.get('start_line')}-{p.get('end_line')}"}]
        out.append(it)
    return out


def build_context(items: list[dict[str, Any]], coverage: list[str], technical: bool = False) -> str:
    lines = ["KNOWLEDGE ITEMS:"]
    for it in items:
        line = f"[{it['ref']}] ({it['status']}; {it['kind']}{'; module ' + it['module'] if it['module'] else ''}) {it['title']}: {it['text']}"
        if technical and it.get("evidence"):
            # Technical readers may cite where a fact comes from (file, query, form definition).
            line += " Evidence: " + "; ".join(f"{e.get('source', '')} ({e.get('detail', '')})".replace(" ()", "")
                                              for e in it["evidence"][:4] if e.get("source"))
        lines.append(line)
    lines.append("\nCOVERAGE NOTES:")
    lines.extend(f"- {c}" for c in coverage)
    return "\n".join(lines)


# ---------------------------------------------------------------- engine


class MarketingChat:
    def __init__(self, kms: QdrantKMS, llm: LLMProvider | None = None, code_kms: QdrantKMS | None = None):
        self.kms = kms
        self.llm = llm or OfflineProvider()
        self.code_kms = code_kms   # source-code collection (v0.25); None = no code answers

    @property
    def llm_enabled(self) -> bool:
        return not isinstance(self.llm, OfflineProvider)

    # 1-2. understand and plan
    def plan(self, message: str, history: list[dict[str, str]], snap: Snapshot, module: str | list[str] | None,
             audience: str | None = None) -> Plan:
        intents = detect_intents(message)
        prev = next((h["content"] for h in reversed(history) if h.get("role") == "user"), "")
        # Short follow-ups ("and for an IT manager?") inherit the previous question's context.
        context_msg = f"{prev} {message}" if prev and len(message.split()) <= 8 else message
        if intents == ["general"] and prev:
            intents = detect_intents(context_msg)
        # Modules chosen in the picker are the scope; a question naming some of them narrows it further.
        selected = selected_modules(module)
        named_modules = detect_modules(context_msg, snap.modules)
        if selected:
            within = [m for m in named_modules if m in selected]
            modules = within or selected
            module_source = ("named in the question, within the selected modules" if within else
                             "module selector" + (f" ({len(selected)} modules)" if len(selected) > 1 else ""))
        else:
            modules = named_modules
            module_source = "named in the question" if modules else "none named: all modules"
        plan = Plan(intents=intents, modules=modules, industry=detect_industry(context_msg), persona=detect_persona(context_msg),
                    audience=audience_of(audience)[0])
        plan.search_queries = [keyword_query(context_msg)]
        plan.raw_message = context_msg
        if plan.industry:
            plan.search_queries.append(plan.industry)
        triggers = {name: m.group(0) for name, rx in INTENT_PATTERNS if name in intents and (m := rx.search(context_msg))}
        plan.step("understand", "Read the question with fixed rules (no AI): intents, modules, industry and audience.",
                  intents=[{"intent": i, "label": INTENT_LABELS.get(i, i), "matchedWord": triggers.get(i, "")} for i in intents],
                  modules=modules, selectedModules=selected, moduleSource=module_source,
                  industry=plan.industry, persona=plan.persona,
                  audience=AUDIENCES[plan.audience]["label"],
                  followUp=context_msg != message, contextMessage=context_msg if context_msg != message else "")
        needs_planner = self.llm_enabled and bool(set(plan.intents) & {"industry", "demo", "differentiation", "market_gap", "general",
                                                                         "content", "howto", "support", "process", "code"})
        llm_queries: list[str] = []
        if needs_planner:
            caps = sorted({p.get("capability", "") for p in snap.of_type("UsageCapability")})
            prompt = PLANNER_PROMPT.format(modules=", ".join(sorted(snap.modules)[:60]), capabilities=", ".join(caps), question=context_msg)
            try:
                data = extract_json(self.llm.generate(prompt))
                llm_queries = [str(q) for q in data.get("searchQueries", [])][:8]
                plan.search_queries += llm_queries
                plan.industry = plan.industry or str(data.get("industry") or "")
                plan.persona = plan.persona or str(data.get("persona") or "")
                plan.planner = "llm"
            except Exception:
                plan.planner = "rules (planner failed)"
        plan.step("plan", "Turned the question into search queries for the knowledge base." if not needs_planner else
                  "Asked the AI planner for ERP search terms (it sees the question and the module/capability names only).",
                  planner=plan.planner, keywordQuery=plan.search_queries[0], plannerQueries=llm_queries,
                  searchQueries=list(plan.search_queries), industry=plan.industry, persona=plan.persona)
        return plan

    # 3-4. retrieve and assess coverage
    def gather(self, plan: Plan, snap: Snapshot, include_review: bool) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        items: list[dict[str, Any]] = []
        coverage: list[str] = []
        missing: list[str] = []
        intents = set(plan.intents)
        routes: list[dict[str, Any]] = []

        def add(new: list[dict[str, Any]], route: str) -> None:
            # Every item remembers how it was found, for the answer trace.
            for it in new:
                it.setdefault("route", route)
            items.extend(new)
            routes.append({"route": route, "items": len(new)})

        if "tickets" in intents:
            stats = ticket_stat_items(snap, plan.modules)
            if stats:
                add(stats, "ticket statistics (aggregated over all tickets)")
                add(retrieve(self.kms, plan.search_queries, plan.modules, True, types=["Ticket"], per_query=15)[:15],
                    "vector search: tickets")
            else:
                missing.append("ticket data")
        matched_fields: list[dict[str, Any]] = []
        named: set[str] = set()
        if "field" in intents:
            if not snap.has("FieldKnowledge"):
                missing.append("field-level knowledge")
            else:
                named = in_modules(mentioned_screens(plan.raw_message or "", snap), snap, plan.modules)
                matched_fields = match_fields(plan.raw_message or plan.search_queries[0], snap, screens=named or None,
                                              modules=plan.modules or None) or []
                add([field_item(p) for p in matched_fields],
                    "field name/label match" + (f" on {', '.join(sorted(named))}" if named else " (all screens)"))
                if matched_fields and CHANGE_RE.search(plan.raw_message or ""):
                    add(related_field_items(matched_fields, snap), "fields affected by a change (dependencies)")
                if named and SECTION_RE.search(plan.raw_message or ""):
                    add(section_items(snap, named), "section knowledge of the named screen")
                if not matched_fields:
                    add(field_overview_items(snap, named or None, plan.modules or None)[:12], "field overview (no field name matched)")
                    add(retrieve(self.kms, plan.search_queries, plan.modules, True, types=["FieldKnowledge", "SectionKnowledge"], per_query=8)[:8],
                        "vector search: field and section knowledge")
                screens = sorted({p.get("screen_name", "") for p in snap.of_type("FieldKnowledge")})
                coverage.append(f"Field-level knowledge exists for {len(screens)} screens"
                                + (f" ({', '.join(sorted({p.get('module') for p in snap.of_type('FieldKnowledge')}))})" if screens else "")
                                + ". The complete per-field dictionary (every field with all attributes and evidence) is in the Field Explorer page and the "
                                "Excel export; this chat explains individual fields in depth." + (f" The question names: {', '.join(sorted(named))}." if named else
                                " No screen is named: if a field exists on several screens, say which screens it appears on."))
        if "feature_discovery" in intents:
            add(module_overview_items(snap, plan.modules), "module and capability overview (aggregated)")
            add(top_feature_items(snap, plan.modules), "top features per capability (aggregated)")
        if "market_gap" in intents:
            missing.append("market demand / market research data")
            add(internal_gap_items(snap, plan.modules), "internal coverage gaps (aggregated)")
        if "differentiation" in intents:
            missing.append("competitor product information")
            add(differentiator_items(snap, plan.modules), "strongest verified features (aggregated)")
        # v0.25 routes: content creation, how-to, support and processes use the business knowledge and documents.
        if "content" in intents:
            brochures = [item_from_point(p) for p in snap.of_type("ModuleBrochure") if not plan.modules or p.get("module") in plan.modules]
            add(brochures[:6], "module brochures (aggregated)")
            add(retrieve(self.kms, plan.search_queries, plan.modules, include_review, types=CONTENT_TYPES, per_query=10)[:20],
                "vector search: brochures, marketing copy, business value, documents")
        if "howto" in intents:
            add(retrieve(self.kms, plan.search_queries, plan.modules, include_review, types=HOWTO_TYPES, per_query=8)[:14],
                "vector search: how-to guides, manuals, FAQs")
        if "support" in intents:
            add(retrieve(self.kms, plan.search_queries, plan.modules, include_review, types=SUPPORT_TYPES, per_query=8)[:14],
                "vector search: FAQs, manuals, how-to guides")
        if "process" in intents:
            procs = [item_from_point(p) for p in snap.of_type("Process") if plan.modules and p.get("module") in plan.modules]
            add(procs[:8], "business processes of the selected modules (aggregated)")
            add(retrieve(self.kms, plan.search_queries, plan.modules, include_review, types=PROCESS_TYPES, per_query=8)[:12],
                "vector search: processes, how-to guides, features")
        if intents & {"industry", "demo", "differentiation", "general"}:
            add(retrieve(self.kms, plan.search_queries, plan.modules, include_review, types=["UsageFeature"], per_query=8)[:20],
                "vector search: Usage features")
            if plan.audience != "developer":
                add(retrieve(self.kms, plan.search_queries, plan.modules, include_review, types=VALUE_TYPES, per_query=6)[:10],
                    "vector search: business value and brochures")
        if intents != {"tickets"} and not (intents == {"field"} and items):
            add(retrieve(self.kms, plan.search_queries, plan.modules, include_review,
                         exclude_types=["Ticket", "FieldKnowledge"] + INTERNAL_TYPES),
                "vector search: all knowledge types")
        if AUDIENCES[plan.audience]["technical"]:
            # Technical readers also get the screen -> API -> backend -> table chain (Phase 16 graph).
            add(technical_items(items), "relationship graph (technical detail for this audience)")
        # Source code: developers only (it is proprietary), for code questions or any technical question.
        wants_code = "code" in intents or (plan.audience == "developer" and bool(intents & {"general", "field", "process", "support", "howto"}))
        if wants_code and plan.audience != "developer":
            coverage.append("Source code is only shown to the Developer audience; this answer uses the documented knowledge.")
        elif wants_code and self.code_kms is None:
            missing.append("source-code index")
        elif wants_code:
            try:
                files = screen_code_files(items)
                add(code_items(self.code_kms, [plan.raw_message or ""] + plan.search_queries[1:], files),
                    "source code: semantic search" + (f" + {len(files)} files behind the screens in question" if files else ""))
            except RuntimeError as exc:
                coverage.append(f"The source-code index could not be searched ({str(exc)[:120]}).")
                missing.append("source-code index")
        if plan.industry and "industry" in intents:
            coverage.append(f"PIE has no industry-specific knowledge for '{plan.industry}'. Relevance to this industry must be reasoned from general feature knowledge and labelled as recommendation.")
            missing.append(f"industry-specific knowledge ({plan.industry})")

        # De-duplicate and cap, keeping aggregate items first.
        seen, unique = set(), []
        for it in items:
            key = (it["knowledgeId"] or it["title"], it["kind"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(it)
        # Aggregates first, then verified knowledge, then unconfirmed: the model sees
        # and cites the strongest evidence first.
        rank = {"field": 0, "module": 0, "capability": 0, "gaps": 0, "ticket_stats": 0, "field_overview": 0, "technical": 0,
                "code": 0, "brochure": 0 if "content" in intents else 1, "process": 1}
        order = {"VERIFIED": 1, "OBSERVED": 1, "AI": 2, "REVIEW": 3}
        unique.sort(key=lambda it: rank.get(it["kind"], order.get(it["status"], 2)))
        dropped = unique[MAX_ITEMS:]
        unique = unique[:MAX_ITEMS]
        for i, it in enumerate(unique, 1):
            it["ref"] = f"K{i}"
        plan.step("retrieve", f"Collected {len(items)} candidate items from {len(routes)} retrieval route(s); "
                  f"kept {len(unique)} after removing {len(items) - len(unique) - len(dropped)} duplicate(s)"
                  + (f" and {len(dropped)} beyond the {MAX_ITEMS}-item limit." if dropped else "."),
                  routes=routes, candidates=len(items), duplicates=len(items) - len(unique) - len(dropped), kept=len(unique),
                  ordering="aggregates and matched fields first, then verified, then AI-drafted, then unreviewed knowledge",
                  reviewKnowledgeIncluded=include_review, namedScreens=sorted(named),
                  droppedOverLimit=[{"title": d["title"], "kind": d["kind"], "status": d["status"], "score": d.get("score")} for d in dropped[:25]])

        usage = sorted(snap.usage_modules)
        coverage.append(f"Verified feature analysis exists for: {', '.join(usage) if usage else 'no module yet'}. Other modules have only basic AI-generated screen descriptions.")
        if "ticket data" in missing:
            coverage.append("The PIE knowledge base contains no ticket data.")
        if "competitor product information" in missing:
            coverage.append("The PIE knowledge base contains no competitor information.")
        if "market demand / market research data" in missing:
            coverage.append("The PIE knowledge base contains no market demand or market research data.")
        plan.step("coverage", "Decided what the knowledge base can and cannot answer; these notes are given to the model.",
                  missing=list(missing), notes=list(coverage))
        return unique, coverage, missing

    # 5-6. answer and validate
    def answer(self, message: str, history: list[dict[str, str]] | None = None, module: str | list[str] | None = None,
               include_review: bool = False, audience: str | None = None) -> dict[str, Any]:
        """`module`: None for all modules, one module, or several ("ess,inventory" or a list).
        `audience`: who the answer is for (AUDIENCES key, default marketing); it changes presentation, never the facts."""
        t0 = time.time()
        history = [h for h in (history or []) if h.get("role") in {"user", "assistant"} and h.get("content")][-MAX_HISTORY_TURNS:]
        message = (message or "").strip()[:MAX_MESSAGE_CHARS]
        if not message:
            raise ValueError("Empty question")
        snap = load_snapshot(self.kms)
        plan = self.plan(message, history, snap, module, audience)
        items, coverage, missing = self.gather(plan, snap, include_review)
        t_retrieval = time.time() - t0

        named = (in_modules(mentioned_screens(plan.raw_message or "", snap), snap, plan.modules)
                 if snap.has("FieldKnowledge") else set())

        def structured(text: str, overview: list[dict[str, Any]], rule: str) -> dict[str, Any]:
            for it in overview:
                it.setdefault("route", f"structured answer: {rule}")
            plan.step("answer", f"Answered without AI: {rule}. The answer is built directly from the knowledge records listed, "
                      "so every line comes from them.", generator="structured", rule=rule)
            return self._response(plan, text, overview, missing, coverage, "grounded", "structured", t0, t_retrieval,
                                  retrieved=len(overview), all_items=overview)

        if plan.intents == ["field"] and named and MANDATORY_LIST_RE.search(plan.raw_message or ""):
            return structured(*mandatory_fields_answer(snap, named), "mandatory-field list of the named screen")
        # The fixed feature list is the Marketing catalog view; other audiences get it only when they ask for a list.
        wants_list = plan.audience == DEFAULT_AUDIENCE or bool(FEATURE_LIST_RE.search(plan.raw_message or ""))
        if plan.intents == ["feature_discovery"] and wants_list and (set(plan.modules) & snap.usage_modules or (not plan.modules and snap.usage_modules)):
            return structured(*feature_list_answer(snap, plan.modules), "feature list from the verified Usage catalog")
        if plan.intents == ["field"] and ALL_FIELDS_RE.search(plan.raw_message or "") and snap.has("FieldKnowledge"):
            return structured(*all_fields_answer(snap, named or None, plan.modules or None),
                              "complete field list")
        if plan.intents == ["tickets"] and "ticket data" in missing:
            plan.step("answer", "Stopped: the question needs ticket data and the knowledge base has none, so the AI was not asked.",
                      generator="none")
            return self._response(plan, NO_TICKETS_ANSWER, [], missing, coverage, "not_found", "none", t0, t_retrieval, all_items=items)
        if not items:
            plan.step("answer", "Stopped: no knowledge matched, so the AI was not asked (it may not answer from its own knowledge).",
                      generator="none")
            return self._response(plan, NOT_FOUND_ANSWER, [], missing, coverage, "not_found", "none", t0, t_retrieval, all_items=items)

        guide = INTENT_GUIDE.get(plan.primary, INTENT_GUIDE["general"]).format(industry=plan.industry or "the target industry",
                                                                                persona=plan.persona or "the audience")
        if plan.primary == "code" and plan.audience != "developer":
            guide = ("Source code is not shared with this audience. Say so in one line, then explain from the items which screens, APIs, "
                     "backend classes and tables handle this. Do not write code or pseudocode.")
        extra = [INTENT_GUIDE[i].format(industry=plan.industry or "the target industry", persona=plan.persona or "the audience")
                 for i in plan.intents[1:] if i in INTENT_GUIDE]
        if plan.primary != "code" and "code" not in plan.intents and any(it["kind"] == "code" for it in items):
            extra.append(INTENT_GUIDE["code"])
        aud = AUDIENCES[plan.audience]
        audience_note = (f"AUDIENCE: {aud['label']}. Prioritise {aud['focus']}. Adapt the answer guide to this audience "
                         "(its pitch or talking-point wording is for Marketing). The facts stay the same, and every statement "
                         "still ends with its [K#] citation, including in tables and headings' bullet points.")
        user_turn = (f"QUESTION: {message}\n\n{audience_note}\n\nANSWER GUIDE: {guide}" + ("".join(f"\nALSO: {e}" for e in extra)) +
                     f"\n\n{build_context(items, coverage, technical=aud['technical'])}")
        generator = "offline"
        text = ""
        llm_error = ""
        history_turns = len(history)
        if self.llm_enabled:
            msgs = [{"role": h["role"], "content": h["content"][:1500]} for h in history] + [{"role": "user", "content": user_turn}]
            try:
                text = self.llm.chat(system_prompt(plan.audience), msgs).strip()
                generator = getattr(self.llm, "name", "llm")
            except Exception as exc:
                llm_error = type(exc).__name__
                coverage.append(f"The language model was unavailable ({type(exc).__name__}); showing the retrieved knowledge instead.")
        if not text:
            text = extractive_answer(plan, items, missing)
            generator = "extractive"
        plan.step("answer", (f"The AI ({generator}) wrote the answer from the {len(items)} numbered knowledge items and the coverage "
                             "notes only, following the answer guide for this intent." if generator not in {"extractive", "offline"} else
                             "No AI answer" + (f" (the model failed: {llm_error})" if llm_error else " (AI is switched off)") +
                             ": the retrieved knowledge is listed with citations instead."),
                  generator=generator, answerGuide=guide, extraGuides=extra, itemsGiven=len(items), historyTurns=history_turns,
                  contextChars=len(user_turn), llmError=llm_error, audience=aud["label"], audienceNote=audience_note)

        text, cited, invalid = validate_citations(text, items)
        status = "grounded" if cited and not missing else ("partial" if cited else "unverified")
        fallback = False
        if not cited and "field" in plan.intents and any(it["kind"] == "field" for it in items):
            # The answer can only have come from the field records supplied; show them.
            cited = [it["ref"] for it in items if it["kind"] == "field"]
            status = "partial"
            fallback = True
            coverage.append("The model did not cite its sources; the field knowledge it was given is listed below.")
        elif not cited and any(it["kind"] == "code" for it in items):
            # A code answer can only quote the source lines it was given; show them as its sources.
            cited = [it["ref"] for it in items if it["kind"] == "code"]
            status = "partial"
            fallback = True
            coverage.append("The model did not cite its sources; the source code it was given is listed below.")
        if invalid:
            coverage.append(f"Removed {len(invalid)} citation(s) that did not match a knowledge item.")
        risky = unsupported_terms(text, items) if generator != "extractive" else []
        if risky:
            coverage.append("Check before use: the answer mentions " + ", ".join(f"'{t}'" for t in risky) +
                            ", which is not found in the PIE knowledge used.")
            if status == "grounded":
                status = "partial"
        plan.step("validate", f"Checked the answer: {len(cited)} of {len(items)} items cited"
                  + (f", {len(invalid)} invalid citation(s) removed" if invalid else "")
                  + (f", {len(risky)} claim word(s) not found in the knowledge" if risky else "")
                  + f". Result: {status}.",
                  cited=cited, invalidCitations=invalid, unsupportedTerms=risky, citationFallback=fallback,
                  missing=list(missing), status=status)
        return self._response(plan, text, [it for it in items if it["ref"] in cited], missing, coverage, status, generator,
                              t0, t_retrieval, retrieved=len(items), all_items=items, context=user_turn)

    def _response(self, plan: Plan, text: str, sources: list[dict[str, Any]], missing: list[str], coverage: list[str],
                  status: str, generator: str, t0: float, t_retrieval: float, retrieved: int = 0,
                  all_items: list[dict[str, Any]] | None = None, context: str | None = None) -> dict[str, Any]:
        cited = {s.get("ref") for s in sources}
        trace = {
            "steps": plan.steps,
            # Every item the answer could draw on, in the order the model saw them, and whether it was cited.
            "retrieved": [{**{k: it.get(k) for k in ("ref", "title", "kind", "status", "knowledgeType", "knowledgeId", "module",
                                                    "capability", "screenId", "score", "route")},
                           "cited": it.get("ref") in cited, "text": (it.get("text") or "")[:400]} for it in (all_items or [])],
            # The exact knowledge text given to the model (question, answer guide, items, coverage notes).
            "context": context[:60000] if context else None,
            "systemPrompt": system_prompt(plan.audience) if context else None,
        }
        return {
            "chatVersion": CHAT_VERSION,
            "answer": text,
            "intent": plan.primary, "intentLabel": INTENT_LABELS.get(plan.primary, plan.primary), "intents": plan.intents,
            "grounding": {"status": status, "retrievedItems": retrieved, "citedItems": len(sources), "missing": missing, "notes": coverage},
            "sources": [{k: s.get(k) for k in ("ref", "title", "kind", "status", "module", "capability", "knowledgeType",
                                               "knowledgeId", "screenId", "readiness", "evidence", "text")} for s in sources],
            "audience": {"key": plan.audience, "label": AUDIENCES[plan.audience]["label"]},
            "plan": {"modules": plan.modules, "industry": plan.industry, "persona": plan.persona, "audience": plan.audience,
                     "searchQueries": plan.search_queries, "planner": plan.planner, "generator": generator},
            "timings": {"retrievalSeconds": round(t_retrieval, 2), "totalSeconds": round(time.time() - t0, 2)},
            "trace": trace,
        }


def trace_digest(response: dict[str, Any]) -> dict[str, Any]:
    """The answer trace without the bulky prompt text: what the activity log keeps for each question."""
    tr = response.get("trace") or {}
    return {"steps": tr.get("steps", []),
            "retrieved": [{k: v for k, v in it.items() if k != "text"} for it in tr.get("retrieved", [])],
            "contextChars": len(tr.get("context") or "")}


NO_TICKETS_ANSWER = ("**No ticket data is available in the PIE knowledge base yet**, so I can't report top tickets, "
                     "issue counts by module or recurring problems without inventing them.\n\n"
                     "To enable ticket analysis, import your support tickets:\n"
                     "- From an export file (CSV/Excel): `python -m pie_agent.cli kms-import-tickets --file tickets.xlsx`\n"
                     "- From SQL Server: `python -m pie_agent.cli kms-import-tickets --sql-file your_query.sql` (example: `config/tickets_gb5_crm_calls.sql`)\n\n"
                     "Once imported, you can ask about the top issues, the modules with most tickets and recurring themes.")
NOT_FOUND_ANSWER = ("**I couldn't find knowledge in PIE that answers this question.** "
                    "Try naming a module or feature (for example *Inventory*, *BOM*, *Gate Entry*), or include knowledge that "
                    "still needs review using the toggle above.")


def validate_citations(text: str, items: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    valid = {it["ref"] for it in items}
    cited: list[str] = []
    invalid: list[str] = []

    def repl(m: re.Match[str]) -> str:
        keep = []
        for ref in re.split(r"\s*[,;]\s*", m.group(1)):
            if ref in valid:
                keep.append(ref)
                if ref not in cited:
                    cited.append(ref)
            else:
                invalid.append(ref)
        return "".join(f"[{r}]" for r in keep)
    return CITATION_RE.sub(repl, text), cited, invalid


def unsupported_terms(text: str, items: list[dict[str, Any]]) -> list[str]:
    known = " ".join(f"{it['title']} {it['text']}" for it in items).lower()
    low = text.lower()
    found = []
    for term in RISKY_TERMS:
        rx = rf"(?<![a-z]){re.escape(term)}s?(?![a-z])"
        if re.search(rx, low) and not re.search(rx, known):
            found.append(term)
    return found


def extractive_answer(plan: Plan, items: list[dict[str, Any]], missing: list[str]) -> str:
    """No-LLM answer: the retrieved knowledge, grouped, with citations."""
    lines = []
    if missing:
        lines.append("**Not in the PIE knowledge base:** " + ", ".join(missing) + ".\n")
    lines.append(f"**{INTENT_LABELS.get(plan.primary, 'Relevant knowledge')}: what PIE knows**\n")
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it.get("capability") or it["kind"].replace("_", " ").title(), []).append(it)
    for g, its in groups.items():
        lines.append(f"### {g}")
        for it in its[:8]:
            text = it["text"]
            if it["kind"] == "feature":
                m = re.search(r"Verified: (.+?)(?: Needs review:| AI-drafted|$)", text)
                text = (m.group(1).split(". ")[0] + ".") if m else text
            lines.append(f"- **{it['title']}**: {text[:220]} [{it['ref']}]")
        lines.append("")
    return "\n".join(lines).strip()
