"""v0.19 Usage Phase: evidence-backed, marketing-ready feature catalog for one module.

Consumes the module-scoped v0.13 registry, v0.14 API mapping and v0.15 DB mapping,
plus existing v0.17 knowledge (from the per-screen JSON directory or from the Qdrant
KMS). No LLM is called.

Every claim in the catalog carries a status:
  VERIFIED - derived directly from source evidence (form definition, BLL validation,
             endpoint, database object) with its evidence attached.
  REVIEW   - inferred, low-confidence or AI-drafted; must not be published by
             marketing without product sign-off.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api_mapping import endpoint_module_entity
from .erp_source import read_text

USAGE_VERSION = "0.19.0"
VERIFIED, REVIEW = "VERIFIED", "REVIEW"
READY, PARTIAL, NEEDS_EVIDENCE = "READY", "PARTIAL", "NEEDS_EVIDENCE"

# Candidate capability taxonomy per module. Matched against the compact screen
# name and its frontend folder group; first match wins. Results are labelled
# `keyword_taxonomy_candidate` until confirmed in a capability map file.
MODULE_TAXONOMY: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "inventory": [
        ("Bill of Materials & Engineering", ("bom", "billofresource", "assembly", "ecr", "ecn", "engineering", "routing", "formula", "diecut", "casting", "design")),
        ("Quality & Inspection", ("inspection", "ncr", "defect", "ndt", "itemtest", "mtr", "certification", "quality", "heat", "grade", "checklist")),
        ("Planning & MRP", ("mrp", "forecast", "schedule", "planning", "materialplan", "capacity", "demand")),
        ("Receiving & Procurement", ("gate", "indent", "grn", "receipt", "inward", "delivery", "purchase", "vendor")),
        ("Stock, Batch & Warehouse", ("stock", "bin", "batch", "lot", "pack", "transfer", "putaway", "count", "cycle", "location", "warehouse", "allocation", "reservation", "serial", "issue")),
        ("Costing, Duty & Tax", ("duty", "excise", "tax", "tariff", "charge", "costing", "discount", "valuation", "price", "rate")),
        ("Production & Shop Floor", ("production", "machine", "processoperation", "operation", "shopfloor", "jobcard", "workorder", "shape")),
        ("Item & Catalogue Management", ("item", "category", "group", "brand", "model", "alias", "sku", "template", "attribute", "feature", "color", "colour", "uom", "measure", "unit", "party", "material")),
        ("Configuration & Setup", ("lov", "parameter", "setting", "setup", "term", "config", "counter", "store")),
    ],
}
MODULE_TAXONOMY["ess"] = [
    ("Leave & On-Duty", ("leave", "onduty", "ondutydaywise")),
    ("Advances, Claims & Encashment", ("advance", "encash", "ltaclaim", "lta", "claim", "loan", "reimburse")),
    ("Payslip & Income Tax", ("payslip", "itdeclaration", "declaration", "tds", "tax", "salary")),
    ("Exit & Separation", ("separation", "relieving", "resignation", "exit")),
    ("Attendance, Punch & Time", ("attendance", "punch", "permission", "calender", "calendar", "timeslip", "overtime", "shift")),
    ("Profile & Directory", ("personal", "directory", "profile", "outofoffice", "employee")),
]
# The remaining modules, built from their screen names (keyword candidates, first match wins;
# confirm or override per screen in capability_map.json).
MODULE_TAXONOMY.update({
    "admin": [
        ("Day & Period Close", ("dayclose", "dayopen", "monthclose", "monthopen")),
        ("Users, Roles & Security", ("role", "rights", "security", "password", "menu", "lock", "session", "ecm", "securitykey")),
        ("Licensing, Plans & Partners", ("entaudit", "entbeta", "entbundle", "entchange", "entdash", "ententitle", "entfeature", "entflag",
                                         "entlicen", "entmenu", "entplan", "entportlet", "entsubscr", "partner", "subscription", "license")),
        ("Integration & Automation", ("eip", "gop", "dlq", "flow", "schedule", "job", "webhook", "sync", "replicate", "ice", "import", "export")),
        ("Monitoring & Health", ("health", "monitor", "diagnostic", "slow", "error", "execution", "audit", "history", "comparison", "live", "topology")),
        ("Mail, Alerts & Events", ("mail", "alert", "event", "notification")),
        ("Reports & Queries Setup", ("report", "query", "search", "dbjoin", "updatebi", "setting")),
        ("Transactions & Documents Setup", ("biztransaction", "transactiontype", "document", "reason", "formula", "paymentterm", "taxtype",
                                            "chargeelement", "allocation", "qualifier", "copy", "template", "dynamicform", "target")),
        ("Organization & Masters", ("company", "branch", "division", "department", "organization", "region", "cluster", "city", "country",
                                    "state", "port", "period", "calendar", "financebook", "currency", "exchange", "bank", "color", "delivery", "gcm", "app")),
    ],
    "hrms": [
        ("Income Tax & TDS", ("tds", "declaration", "tax")),
        ("Payroll Processing", ("payroll", "payprocess", "payslip", "payelement", "payconfig", "paygroup", "payperiod", "wage", "perquisite",
                                "accountposting", "register", "undopay", "gratuity")),
        ("Leave Management", ("leave", "holiday")),
        ("Attendance, Shifts & Punch", ("attendance", "punch", "shift", "overtime", "pickup")),
        ("Advances & Loans", ("advance", "emi", "loan")),
        ("Periodic Additions & Deductions", ("periodic", "deduction", "addition", "dagroup", "dapoint")),
        ("Performance & Goals", ("appraisal", "goal", "keyresult", "kra", "performance", "measure")),
        ("Recruitment & Job Profiles", ("recruit", "manpower", "selection", "jobprofile", "subject", "worktype")),
        ("Employee Master & Records", ("employee", "nominee", "qualification", "experience", "reference", "document", "noc", "caste",
                                       "reportinghead", "reportingto", "skill", "working")),
        ("HR Setup & Policy", ("policy", "setting", "oulevel", "oulevl", "periodupdate", "overall")),
    ],
    "finance": [
        ("Vouchers & Accounting Entries", ("voucher", "narration", "recurring", "brs", "billallocation", "accountposting")),
        ("Cost Centers & Budgets", ("costcenter", "costcategory", "budget")),
        ("Chart of Accounts", ("account", "group", "schedule")),
        ("Banking & Instruments", ("cheque", "instrument", "letterofcredit", "bank")),
        ("Tax & TDS", ("tds", "tax")),
        ("Deferrals, Payment Plans & Insurance", ("deferal", "deferral", "paymentplan", "plan", "insurance", "distribution")),
        ("Partners & Parties", ("partner", "party")),
    ],
    "sales": [
        ("Accounting & Tax Posting", ("accountposting", "tds", "billallocation", "outstanding")),
        ("Invoicing & Billing", ("invoice", "billpass", "customerbill", "documentprinting", "documentcharges", "totalcharges", "charges", "particulars")),
        ("Pricing & Discounts", ("price", "discount", "rate", "schema")),
        ("Sales Targets", ("target",)),
        ("Orders & Material Management", ("materialmanagement", "mm", "purchase", "order", "itemschedule", "schedule", "expiry", "return", "attachment")),
        ("Stock, Lot & Packing", ("lot", "pack", "bin", "process", "quality", "load", "extendedsearch")),
    ],
    "skillmanagement": [
        ("Work Instructions", ("wi", "workinstruction", "acknowledg")),
        ("Skills & Assessment", ("skill", "assessment", "matrix", "coverage", "entrycard")),
        ("Production Lines & Workstations", ("productionline", "line", "workstation", "station", "roaster", "staffing", "execution", "assignment", "scancard")),
    ],
    "training": [
        ("Assessments & Question Bank", ("assessment", "question", "evaluation", "paper", "response")),
        ("Enrollment, Attendance & Certificates", ("enrol", "attendance", "completion", "certificate")),
        ("Trainers & Venues", ("trainer", "venue", "vendor")),
        ("Programmes & Sessions", ("programme", "program", "session", "schedule", "instance", "topic", "domain", "need")),
    ],
    "framework": [
        ("Data Sync & Validation", ("datasync", "validation", "vallidation", "checking", "dataalert", "reconciliation")),
        ("Web Services", ("webservice",)),
        ("Platform Setup (Modules, Menus, Pages)", ("module", "menu", "page", "portlet", "program", "entity", "enum", "webform", "file", "client",
                                                    "dashboard", "eventclass", "eventtype", "paytaxtype")),
    ],
    "foundry": [
        ("Melting & Pouring", ("melt", "pour", "heat", "charge", "knockoff")),
        ("Moulding, Patterns & Cores", ("mould", "pattern", "core")),
        ("Testing, NDT & Certificates", ("ndt", "mtr", "materialtest", "inspection", "grade", "certificate")),
        ("Production Planning", ("productionplan", "proudction", "foundaryproduction", "ordermanagement", "pcs")),
        ("Checklists & Rules", ("checklist", "applicability", "excecut", "execution", "log")),
    ],
    "performance": [
        ("Goals, OKRs & KRAs", ("okr", "objective", "kra", "anchor", "measure", "actionitem", "checkin", "cycle", "cmgm")),
        ("Appraisals & Ratings", ("appraisal", "bellcurve", "rating", "perm", "pip", "memo")),
        ("Feedback & Recognition", ("feedback", "fbck", "recognition", "oneonone")),
        ("Career & Skill Development", ("career", "skill", "tna", "profile", "framework", "trend", "ess")),
    ],
    "production": [
        ("Routing & Process Setup", ("routing", "process", "parameterset", "measuretype", "countmaster", "diemaster", "machinetype", "resource",
                                     "workcenter", "floorplan", "packset")),
        ("Production Planning & Work Orders", ("plan", "workorder", "jobcard")),
        ("Shop Floor Execution", ("production", "entry", "batchposting", "operatorconsole", "melting", "packing", "defect")),
    ],
    "gb ent client": [   # module "gb-ent-client" (keys are _norm()-ed)
        ("Onboarding & Provisioning", ("quickstart", "provisioning", "requests")),
        ("Plans, Pricing & Trials", ("pricing", "subscription", "billing", "trial", "demo", "contactsales", "features", "beta")),
        ("Client Portal & Account", ("client", "login", "password", "profile", "users", "home", "shell")),
    ],
    "attendanceandpayroll": [
        ("Periodic & Fixed Additions/Deductions", ("periodic", "fixed", "additiondeduction")),
        ("Attendance Adjustment & Contract Work", ("attendance", "attendence", "contractwork")),
        ("Transfers & Pay Revision", ("transfer", "payrevision")),
        ("Separation & Settlement", ("separation", "settlement")),
    ],
    "maintenance": [
        ("Assets & Asset Types", ("asset",)),
        ("Activities & Workbench", ("activity", "workbench", "problem", "solution")),
        ("Meters & Readings", ("meter", "reading")),
        ("Resources & Scanning", ("resource", "scan")),
    ],
    "analytics": [
        ("KPIs & OEE", ("kpi", "oee")),
        ("Analysis & Query Design", ("analysis", "query", "workspace", "datasource", "reportconfig", "report")),
        ("Dimensions & Hierarchies", ("dimension", "hierarchy", "allocation")),
    ],
    "sqlworkbench": [
        ("SQL Editor & Queries", ("sql", "query", "editor")),
        ("Change & Release Management", ("change", "upgrade", "provisioning", "branch", "sync", "projects")),
        ("Database Models & Scripts", ("dbmodel", "ddl", "metadata", "objectgroup", "tag", "database", "server")),
    ],
    "planning": [
        ("MRP", ("mrp",)),
        ("Master Production Scheduling", ("mps", "masterschedule")),
        ("Forecasting", ("forecast",)),
        ("Capacity & Planner Workbench", ("capacity", "planner", "activity", "pendingdocument")),
    ],
    "enablement": [
        ("Show Channels & Rules", ("show",)),
        ("Content Links & Context", ("content", "context", "link")),
        ("Portal & Feed", ("portal", "feed")),
    ],
    "fam": [
        ("Asset Acquisition & Addition", ("acquisition", "acaquisition", "addition", "opening")),
        ("Depreciation & Adjustment", ("depreciation", "adjustment", "deduction", "schedule")),
        ("Transfer & Retirement", ("transfer", "retirement")),
        ("Asset Setup (Brands, Locations)", ("brand", "location")),
    ],
    "apiplatform": [
        ("API Registration & Requests", ("register", "request")),
        ("API Catalogue & Design", ("capability", "domain", "interface", "operation", "parameter", "schema", "version")),
    ],
    "cms": [
        ("Channels, Campaigns & Portal", ("channel", "campaign", "portal", "learning")),
        ("Content Authoring & Catalogue", ("content", "block", "field", "ontology", "collection", "render")),
    ],
    "pay": [
        ("Loyalty & Cashback", ("loyalty", "cashback")),
        ("Checkout & Payment Gateway", ("checkout", "gateway", "webhook", "order")),
    ],
    "costing": [
        ("Cost Heads & Overheads", ("costhead", "overhead", "costover", "setting")),
        ("Product Costing & Estimates", ("productcost", "product", "resourcetype")),
        ("Budget & Cost Analysis", ("budget", "analysis", "distribution")),
    ],
    "dealerportal": [("Dealer Onboarding", ("onboarding",)), ("Dealer Profile & Queries", ("profile", "query", "dashboard"))],
    "pie": [
        ("Outcomes, Proposals & QC", ("outcome", "proposal", "qc", "aeo")),
        ("Profiles & Estimation", ("profile", "estimation", "benchmark", "variant", "configitem", "module", "kpi")),
    ],
    "projects": [("Tasks & My Day", ("task", "myday", "worklist")), ("Projects", ("project",))],
    "quality": [
        ("Inspection Planning & NCR", ("matrix", "ncr", "serial")),
        ("Material Grades", ("grade",)),
        ("Standards & Specifications", ("standard", "specification", "certification", "attribute")),
    ],
    "recruitment": [
        ("Applicants & Profiles", ("applicant", "profile")),
        ("Interviews & Selection", ("interview", "selection")),
        ("Job Openings & Requisitions", ("job", "opening", "description", "manpower")),
    ],
    "crmsales": [("Leads", ("lead",)), ("Contacts & Addresses", ("contact", "address")), ("Tasks & Expense Claims", ("task", "expense"))],
    "gbservicedesk": [
        ("Service Requests & Tickets", ("ticket", "servicerequest", "request", "callauto")),
        ("Service Catalog & SLAs", ("catalog", "definition", "servicelevel", "asset")),
    ],
    "tcms": [("Test Cases & Runs", ("test", "traceability")), ("Test Projects & Modules", ("project", "module", "import", "export", "dashboard"))],
    "gbgateway": [("Document Ingestion", ("upload", "ingestion", "queue", "document", "vocher", "canonical", "order", "details"))],
    "automationhub": [("Automation Scripts & Runs", ("script", "catalog", "run", "release", "activation"))],
    "fls": [("Surveys & Feedback", ("survey", "rating", "registration", "instance"))],
    "logistics": [("Routes, Drivers & Locations", ("route", "driver", "location", "documenttype"))],
    "checklist": [("Checklists", ("checklist", "applicability", "execute", "log", "dashboard"))],
    "competencymanagement": [("Competency Framework", ("competenc", "taxonomy"))],
    "crmservice": [("Service Knowledge & Tasks", ("problem", "solution", "product", "task"))],
    "security": [("Visitors & Vehicles", ("visitor", "vehicle"))],
    "wms": [("Warehouse & Bins", ("bin", "warehouse", "ware", "measure"))],
    "dms": [("Documents & Spaces", ("folder", "project", "space"))],
    "purchase": [("Vendor Selection & Rating", ("vendor", "rating"))],
    "facility": [("Facility Devices", ("device",))],
})
# Readable module names (the default humanizes the folder name).
MODULE_NAMES = {
    "hrms": "HRMS", "ess": "ESS", "fam": "Fixed Assets (FAM)", "wms": "Warehouse (WMS)", "tcms": "Test Case Management (TCMS)",
    "dms": "Document Management (DMS)", "cms": "Content Management (CMS)", "fls": "Feedback & Surveys (FLS)", "pie": "PIE",
    "crmsales": "CRM Sales", "crmservice": "CRM Service", "gbservicedesk": "Service Desk", "gbgateway": "GB Gateway",
    "gb-ent-client": "Enterprise Client Portal", "sqlworkbench": "SQL Workbench", "apiplatform": "API Platform",
    "automationhub": "Automation Hub", "attendanceandpayroll": "Attendance & Payroll", "skillmanagement": "Skill Management",
    "competencymanagement": "Competency Management", "dealerportal": "Dealer Portal", "gbstudio": "GB Studio",
}
# Modules whose report/dashboard screens are self-service features first (Payslip View,
# Attendance Calendar): the taxonomy decides before the generic reports bucket.
TAXONOMY_BEFORE_CATEGORY = {"ess"}
REPORTS_CAPABILITY = "Reports & Analytics"
OTHER_CAPABILITY = "Other Functions"

CATEGORY_LABELS = {"master": "Master Data", "transaction": "Transaction", "report": "Report",
                   "dashboard": "Dashboard", "others": "Utility"}
ACRONYMS = {"ess", "hrms", "crm", "tds", "lta", "it", "bom", "uom", "sku", "ou", "lov", "wo", "ecr", "ecn", "mrp", "grn", "ncr", "ndt", "mtr", "gst", "abc", "fifo", "qc",
            "mps", "oee", "kpi", "okr", "kra", "tna", "pip", "wms", "fam", "sql", "api", "dms", "tcms", "cms", "sla", "brs", "emi", "noc", "ddl", "wi", "pcs",
            "dlq", "eip", "gop", "ecm", "aeo", "fls", "gcm", "ice"}

NOT_EMPTY_RE = re.compile(r"_Validation\s*\.\s*NotEmpty\s*\(\s*[\w.]*?\.?(\w+)\s*,")
THROW_MSG_RE = re.compile(r'throw\s+new\s+\w*Exception\s*\(\s*"([^"{}]{8,160})"')
# Technical messages (failures, method names, platform/plumbing terms) are not business rules.
TECHNICAL_MSG_RE = re.compile(r"^(?:an?\s+)?(?:error|failed|failure|exception|unable to|something went wrong|unexpected)\b|\b(?:Get|Save|Delete|Update|Fetch|Load)[A-Z]\w+|\b(?:sql|procedure|stored|server|clients?|gb4|ids?|null|criteria|payload|request|dto|json|api|endpoint|cache|login|token)\b", re.I)
AUTONUMBER_RE = re.compile(r"_AutoNumber\s*\.\s*(?:GetNumberAsync|GetAutoNumber)\s*\(")
# Tenant/user/entity tables joined by nearly every GB5 query for audit columns.
PLATFORM_TABLES = {"MCLIENT", "MUSER", "MENTITY"}
MIN_API_CONFIDENCE = .80       # form-action / exact route evidence
MIN_DB_CONFIDENCE = .85        # exact object-name match in reached source
SHARED_TABLE_SHARE = .40       # tables on >40% of mapped screens are platform tables


def _load(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _compact(s: str) -> str:
    return _norm(s).replace(" ", "")


def humanize(identifier: str) -> str:
    """'ItemCategoryCode' -> 'Item Category Code'; 'Assembly Bom' -> 'Assembly BOM'."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier or "")
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = re.sub(r"[_\-]+", " ", s)
    words = [w for w in s.split() if w]
    return " ".join(w.upper() if w.lower() in ACRONYMS else (w if w.isupper() and len(w) > 1 else w.capitalize()) for w in words)


def build_vocabulary(registry: list[dict[str, Any]]) -> set[str]:
    """Words the ERP itself uses: readable screen names and humanized form field names."""
    vocab = set(ACRONYMS)
    for s in registry:
        for name in (s.get("screenName"), s.get("feature")):
            if name and " " in name:
                vocab.update(w for w in _norm(name).split() if len(w) > 1)
        for f in s.get("fields") or []:
            vocab.update(w for w in _norm(humanize(str(f.get("Name", "")))).split() if len(w) > 1)
    # Two-letter words ("sh", "op") make false splits; keep them only as acronyms.
    return {w for w in vocab if not w.isdigit() and (len(w) >= 3 or w in ACRONYMS)}


def segment(word: str, vocab: set[str]) -> str:
    """'itemsubcategory' -> 'item sub category' using known words; unchanged unless the
    whole word is explained by known words."""
    s = word.lower()
    n = len(s)
    # best[i] = (cost, pieces) for s[:i]; each piece is (text, known).
    best: list[tuple[int, list[tuple[str, bool]]] | None] = [(0, [])] + [None] * n
    for i in range(1, n + 1):
        for j in range(max(0, i - 24), i):
            if best[j] is None:
                continue
            piece = s[j:i]
            known = piece in vocab
            if not known and len(piece) > 1:
                continue
            cost = best[j][0] + (1 if known else 6)
            if best[i] is None or cost < best[i][0]:
                best[i] = (cost, best[j][1] + [(piece, known)])
    if best[n] is None:
        return word
    parts: list[tuple[str, bool]] = []
    for text, known in best[n][1]:
        if not known and parts and not parts[-1][1]:
            parts[-1] = (parts[-1][0] + text, False)
        else:
            parts.append((text, known))
    # Only split when every piece is a known word; a partial split such as
    # "Measure M Ent" is worse than the original.
    if any(not k for _, k in parts):
        return word
    return " ".join(t for t, _ in parts)


def marketing_name(screen: dict[str, Any], vocab: set[str] | None = None) -> str:
    name = screen.get("screenName") or screen.get("feature") or screen.get("screenId", "")
    module = screen.get("module", "")
    mod = _compact(module)
    if module and _norm(name).startswith(_norm(module) + " "):
        name = name[len(module):].strip()
    elif mod and " " not in name and name.lower().startswith(mod) and len(name) > len(mod) + 2:
        name = name[len(mod):]
    if vocab and " " not in name and len(name) > 6 and not re.search(r"[a-z][A-Z]", name):
        name = segment(name, vocab)
    return humanize(name.replace(" ", "_")) if " " in name else humanize(name)


def _category_and_group(screen: dict[str, Any]) -> tuple[str, str]:
    parts = (screen.get("componentPath") or "").split("/")
    if len(parts) > 3 and parts[0] == "projects":
        cat = CATEGORY_LABELS.get(parts[2].lower(), humanize(parts[2]))
        group = humanize(parts[3]) if len(parts) > 4 else ""
        return cat, group
    if screen.get("formDefinitionPath"):
        return "Form", ""
    return "Other", ""


def classify_capability(module: str, screen: dict[str, Any], category: str, group: str,
                        overrides: dict[str, Any]) -> dict[str, Any]:
    ids = [screen.get("screenId", "")] + list(screen.get("aliasScreenIds") or [])
    for sid in ids:
        ov = overrides.get(sid)
        if isinstance(ov, dict) and ov.get("capability"):
            basis = ov.get("basis") or "explicit"
            return {"name": ov["capability"], "basis": basis,
                    "needsReview": basis not in {"human_confirmed", "source_evidenced", "explicit"}}
    hay = _compact(marketing_name(screen)) + " " + _compact(group)
    by_taxonomy = next(({"name": capability, "basis": "keyword_taxonomy_candidate", "needsReview": True}
                        for capability, keywords in MODULE_TAXONOMY.get(_norm(module), []) if any(k in hay for k in keywords)), None)
    if by_taxonomy and _norm(module) in TAXONOMY_BEFORE_CATEGORY:
        return by_taxonomy
    if category in {"Report", "Dashboard"}:
        return {"name": REPORTS_CAPABILITY if _norm(module) not in TAXONOMY_BEFORE_CATEGORY else "Dashboards & Reports",
                "basis": "screen_category", "needsReview": False}
    if by_taxonomy:
        return by_taxonomy
    return {"name": OTHER_CAPABILITY, "basis": "unclassified", "needsReview": True}


def _is_entity_file(path: str, entity: str) -> bool:
    if not entity:
        return False
    low, ent = path.replace("\\", "/").lower(), entity.lower()
    stem = Path(low).stem
    # Folder match (…/bom/…) or an exact entity file (BOMQB.cs); a prefix match would
    # let entity "Item" claim ItemCategoryQB.cs.
    return f"/{ent}/" in low or stem in {ent, ent + "qb", ent + "dal", ent + "bll", "i" + ent + "dal"}


def _claim(text: str, kind: str, status: str, evidence: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"text": text, "kind": kind, "status": status, "evidence": evidence, **extra}


def _join(items: list[str], limit: int = 6) -> str:
    items = list(dict.fromkeys(i for i in items if i))
    shown = items[:limit]
    rest = len(items) - len(shown)
    body = ", ".join(shown[:-1]) + (" and " + shown[-1] if len(shown) > 1 else (shown[0] if shown else ""))
    return body + (f" (+{rest} more)" if rest > 0 else "")


def extract_bll_rules(backend_root: Path, files: list[str]) -> dict[str, Any]:
    """Validation rules and behaviours stated in BLL source. Pure static reading."""
    mandatory: dict[str, str] = {}
    rules: dict[str, str] = {}
    autonumber: list[str] = []
    for rel in dict.fromkeys(files):
        p = backend_root / rel
        if not p.exists():
            continue
        txt = read_text(p, 400_000)
        for field in NOT_EMPTY_RE.findall(txt):
            mandatory.setdefault(humanize(field), rel)
        for msg in THROW_MSG_RE.findall(txt):
            if not TECHNICAL_MSG_RE.search(msg.strip()):
                rules.setdefault(msg.strip(), rel)
        if AUTONUMBER_RE.search(txt):
            autonumber.append(rel)
    return {"mandatory": mandatory, "rules": rules, "autonumber": autonumber}


def load_knowledge_local(knowledge_dir: str | Path) -> dict[str, dict[str, Any]]:
    out = {}
    root = Path(knowledge_dir)
    for p in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("screen"):
            out[d["screen"].get("screenId", p.stem)] = {
                "generatedAt": d.get("generatedAt", ""), "status": d.get("status", ""),
                "records": [{"knowledgeType": r.get("knowledgeType"), "title": r.get("title"), "content": r.get("content"),
                             "needsReview": bool(r.get("needsReview")), "generationMethod": r.get("generationMethod"),
                             "confidence": r.get("confidence", 0), "knowledgeId": r.get("knowledgeId"),
                             "evidence": [e.get("source_file", "") for e in r.get("evidence", []) if isinstance(e, dict)]}
                            for r in d.get("records", [])],
                "source": "v0.17 knowledge directory",
            }
    return out


def load_knowledge_qdrant(kms: Any, module: str) -> dict[str, dict[str, Any]]:
    """Read published and review knowledge for one module from the Qdrant KMS."""
    points = kms.scroll_all({"must": [{"key": "module", "match": {"value": module}}]})
    out: dict[str, dict[str, Any]] = {}
    for pt in points:
        pl = pt.get("payload", {})
        entry = out.setdefault(pl.get("screen_id", ""), {"generatedAt": pl.get("generated_at", ""), "status": "",
                                                          "records": [], "source": f"Qdrant {kms.collection}"})
        entry["records"].append({"knowledgeType": pl.get("knowledge_type"), "title": pl.get("title"), "content": pl.get("content"),
                                 "needsReview": pl.get("publication_status") != "PUBLISHED",
                                 "generationMethod": pl.get("generation_method"), "confidence": pl.get("confidence", 0),
                                 "knowledgeId": pl.get("knowledge_id"), "evidence": pl.get("evidence_ids", [])})
    return out


def _summary(knowledge: dict[str, Any] | None) -> dict[str, Any] | None:
    if not knowledge:
        return None
    records = knowledge.get("records", [])
    for kt in ("BusinessSummary", "Functionality", "Feature"):
        for r in records:
            if r.get("knowledgeType") == kt and r.get("content"):
                return {"text": r["content"], "knowledgeType": kt, "knowledgeId": r.get("knowledgeId"),
                        "generationMethod": r.get("generationMethod"), "generatedAt": knowledge.get("generatedAt", ""),
                        # AI copy written from thin evidence is a draft for marketing,
                        # even when the knowledge gate passed it.
                        "status": REVIEW, "label": "AI-drafted description",
                        "knowledgeNeedsReview": bool(r.get("needsReview")), "evidence": r.get("evidence", [])}
    return None


def build_feature(screen: dict[str, Any], api_row: dict[str, Any] | None, db_row: dict[str, Any] | None,
                  knowledge: dict[str, Any] | None, capability: dict[str, Any], category: str, group: str,
                  shared_tables: set[str], backend_root: Path, vocab: set[str] | None = None) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    fields = screen.get("fields") or []
    if fields and screen.get("formDefinitionPath"):
        required = [f for f in fields if f.get("Required") is True]
        ev = [{"source": screen["formDefinitionPath"], "detail": f"{len(fields)} ObjectFields, {len(required)} Required"}]
        text = f"Captures {len(fields)} data fields" + (f", {len(required)} of them mandatory" if required else "") + "."
        claims.append(_claim(text, "form", VERIFIED, ev))

    apis = (db_row or api_row or {}).get("apiMappings", []) or []
    strong = [a for a in apis if a.get("confidence", 0) >= MIN_API_CONFIDENCE]
    ops = []
    for a in strong:
        src = " ".join(str(x.get("source", "")) for x in a.get("frontendEvidence", []) or [])
        cls = a.get("endpointClass", "").lower()
        if "form-action:get" in src or cls.startswith("get"):
            ops.append("View")
        if "form-action:save" in src or cls.startswith("save"):
            ops.append("Create & update")
        if "form-action:delete" in src or cls.startswith("delete"):
            ops.append("Delete")
    ops = [o for o in ("View", "Create & update", "Delete") if o in ops]
    if ops:
        ev = [{"source": a.get("endpointFile", ""), "detail": f"{a['method']} {a['route']}"} for a in strong][:6]
        claims.append(_claim(f"Supports {_join(ops)} through {len(strong)} verified backend service"
                             f"{'s' if len(strong) != 1 else ''}.", "operations", VERIFIED, ev))

    impl_files = [i.get("file", "") for a in strong for i in a.get("serviceImplementations", []) or []]
    rules = extract_bll_rules(backend_root, impl_files)
    if rules["mandatory"]:
        names = list(rules["mandatory"])
        claims.append(_claim(f"Validates that {_join(names)} {'is' if len(names) == 1 else 'are'} filled in before saving.",
                             "validation", VERIFIED,
                             [{"source": f, "detail": f"_Validation.NotEmpty({n})"} for n, f in list(rules["mandatory"].items())[:6]]))
    for msg, f in list(rules["rules"].items())[:4]:
        claims.append(_claim(f"Business rule: {msg.rstrip('.')}.", "business_rule", VERIFIED,
                             [{"source": f, "detail": "exception message in business logic"}], quote=msg))
    if rules["autonumber"]:
        claims.append(_claim("Assigns codes and document numbers automatically (auto-numbering).", "automation", VERIFIED,
                             [{"source": f, "detail": "_AutoNumber usage"} for f in rules["autonumber"][:3]]))

    tables: dict[str, dict[str, Any]] = {}
    for a in apis:
        entity = endpoint_module_entity(a.get("endpointFile", ""))[1]
        for o in a.get("databaseMappings", []) or []:
            key = f"{o.get('schema', 'dbo')}.{o.get('name')}"
            t = tables.setdefault(key, {"name": o.get("name"), "schema": o.get("schema", "dbo"), "type": o.get("type", ""),
                                        "confidence": 0, "role": "read",
                                        "shared": (o.get("name") or "").upper() in shared_tables | PLATFORM_TABLES,
                                        "evidenceFiles": [], "writeFiles": []})
            t["confidence"] = max(t["confidence"], o.get("confidence", 0))
            t["evidenceFiles"] = sorted(set(t["evidenceFiles"]) | set(o.get("evidenceFiles", []) or []))
            writes = o.get("writeFiles") or []
            t["writeFiles"] = sorted(set(t["writeFiles"]) | set(writes))
            # A write in the feature's own entity code (…/BOM/… for BOM) is ownership;
            # a write reached through another entity's logic (…/MRPRun/…) is only related.
            if any(_is_entity_file(f, entity) for f in writes):
                t["role"] = "write"
            elif writes and t["role"] != "write":
                t["role"] = "related_write"
    own = [t for t in tables.values() if not t["shared"]]
    exact = [t for t in own if t["confidence"] >= MIN_DB_CONFIDENCE]
    written = [t for t in exact if t["role"] == "write"]
    related = [t for t in exact if t["role"] == "related_write"]
    read = [t for t in exact if t["role"] == "read"]
    def ev(ts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"source": ((t["writeFiles"] if t["role"] != "read" else []) or t["evidenceFiles"] or [""])[-1],
                 "detail": f"{t['schema']}.{t['name']} ({t['role'].replace('_', ' ')})"} for t in ts][:6]
    if written:
        claims.append(_claim(f"Maintains its own records ({_join([t['name'] for t in written], 4)}).",
                             "data", VERIFIED, ev(written), tables=[t["name"] for t in written]))
    if read:
        claims.append(_claim(f"Works with shared master data: {_join([t['name'] for t in read], 5)}.",
                             "integration", VERIFIED, ev(read), tables=[t["name"] for t in read]))
    if related:
        claims.append(_claim(f"May also update related records through connected logic ({_join([t['name'] for t in related], 4)}).",
                             "related_data", REVIEW, ev(related), tables=[t["name"] for t in related]))
    candidate_tables = [t for t in own if t["confidence"] < MIN_DB_CONFIDENCE]
    if candidate_tables:
        claims.append(_claim(f"May also use {_join([t['name'] for t in candidate_tables], 4)} (name-based match only).",
                             "data_candidate", REVIEW, [{"source": "name/token overlap", "detail": t["name"]} for t in candidate_tables][:6]))

    gaps = [{"formName": g.get("formName"), "kind": g.get("kind"), "url": g.get("url"), "entity": g.get("entity")}
            for g in (api_row or {}).get("unresolvedFormActions", []) or []]
    verified = sum(c["status"] == VERIFIED for c in claims)
    if screen.get("status") == "CONFIRMED" and verified >= 2:
        readiness = READY
    elif verified >= 1:
        readiness = PARTIAL
    else:
        readiness = NEEDS_EVIDENCE
    return {
        "screenId": screen.get("screenId"),
        "aliasScreenIds": screen.get("aliasScreenIds", []),
        "name": marketing_name(screen, vocab),
        "category": category,
        "group": group,
        "capability": capability,
        "identityStatus": screen.get("status"),
        "readiness": readiness,
        "summary": _summary(knowledge),
        "claims": claims,
        "verifiedClaims": verified,
        "reviewClaims": len(claims) - verified,
        "migrationGaps": gaps,
        "technical": {
            "apis": [{"method": a.get("method"), "route": a.get("route"), "confidence": a.get("confidence"),
                      "confidenceLevel": a.get("confidenceLevel"), "endpointFile": a.get("endpointFile"),
                      "reasons": a.get("reasons", [])} for a in apis][:20],
            "tables": sorted(tables.values(), key=lambda t: (t["shared"], -t["confidence"], t["name"] or ""))[:20],
            "componentPath": screen.get("componentPath", ""),
            "formDefinitionPath": screen.get("formDefinitionPath", ""),
            "fieldCount": len(fields),
        },
    }


def build_usage_catalog(module: str, module_dir: str | Path, backend_root: str | Path,
                        knowledge: dict[str, dict[str, Any]], capability_map: str | Path | None = None,
                        output_dir: str | Path | None = None) -> dict[str, Any]:
    mdir = Path(module_dir)
    registry = _load(mdir / "phase13-identity" / "screen_registry_v13.json", [])
    api_rows = {r["screenId"]: r for r in _load(mdir / "phase14-api-mapping" / "screen_api_backend_mapping_v14.json", [])}
    db_rows = {r["screenId"]: r for r in _load(mdir / "phase15-db-mapping" / "screen_api_database_mapping_v15.json", [])}
    overrides = _load(capability_map, {}) if capability_map else {}
    screens = [s for s in registry if _norm(s.get("module", "")) == _norm(module)]
    vocab = build_vocabulary(registry)

    # Platform tables (tenant, user...) are joined by nearly every query; they say
    # nothing about a feature, so they are flagged rather than claimed.
    table_counts: Counter[str] = Counter()
    mapped = 0
    for row in db_rows.values():
        names = {o.get("name", "").upper() for a in row.get("apiMappings", []) for o in a.get("databaseMappings", []) or []}
        if names:
            mapped += 1
            table_counts.update(names)
    shared = {n for n, c in table_counts.items() if mapped >= 5 and c / mapped > SHARED_TABLE_SHARE}

    features = []
    for s in screens:
        category, group = _category_and_group(s)
        cap = classify_capability(module, s, category, group, overrides)
        kn = None
        for sid in [s.get("screenId")] + list(s.get("aliasScreenIds") or []):
            if sid in knowledge:
                kn = knowledge[sid]
                break
        feat = build_feature(s, api_rows.get(s["screenId"]), db_rows.get(s["screenId"]), kn, cap,
                             category, group, shared, Path(backend_root), vocab)
        # Product can correct display names (e.g. source typos) in the same map file.
        for sid in [s.get("screenId")] + list(s.get("aliasScreenIds") or []):
            ov = overrides.get(sid) if isinstance(overrides, dict) else None
            if isinstance(ov, dict) and ov.get("name"):
                feat["name"] = ov["name"]
                break
        features.append(feat)
    features.sort(key=lambda f: (f["capability"]["name"], f["name"]))

    caps: dict[str, dict[str, Any]] = {}
    for f in features:
        c = caps.setdefault(f["capability"]["name"], {"name": f["capability"]["name"], "basis": set(), "needsReview": False,
                                                      "features": 0, "ready": 0, "partial": 0, "needsEvidence": 0,
                                                      "verifiedClaims": 0, "categories": Counter()})
        c["basis"].add(f["capability"]["basis"])
        c["needsReview"] = c["needsReview"] or f["capability"]["needsReview"]
        c["features"] += 1
        c["verifiedClaims"] += f["verifiedClaims"]
        c[{"READY": "ready", "PARTIAL": "partial", "NEEDS_EVIDENCE": "needsEvidence"}[f["readiness"]]] += 1
        c["categories"][f["category"]] += 1
    capabilities = [{**c, "basis": sorted(c["basis"]), "categories": dict(c["categories"])}
                    for c in sorted(caps.values(), key=lambda c: (-c["features"], c["name"]))]

    readiness = Counter(f["readiness"] for f in features)
    catalog = {
        "usageVersion": USAGE_VERSION,
        "module": module,
        "moduleName": MODULE_NAMES.get(module, humanize(module)),
        "product": "GoodBooks ERP",
        "audience": "Marketing",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "knowledgeSource": next((k.get("source") for k in knowledge.values()), "none"),
        "claimPolicy": {
            VERIFIED: "Derived directly from source evidence (form definition, business-logic validation, backend endpoint or database object). Safe to use with normal copy review.",
            REVIEW: "Inferred, low-confidence or AI-drafted. Do not publish without product sign-off.",
            "capabilities": "Capability groupings marked 'candidate' come from a keyword taxonomy and need product confirmation (capability_map.json).",
        },
        "statistics": {
            "features": len(features), "capabilities": len(capabilities),
            "ready": readiness.get(READY, 0), "partial": readiness.get(PARTIAL, 0), "needsEvidence": readiness.get(NEEDS_EVIDENCE, 0),
            "verifiedClaims": sum(f["verifiedClaims"] for f in features),
            "reviewClaims": sum(f["reviewClaims"] for f in features),
            "aiDraftsToReview": sum(bool(f["summary"]) for f in features),
            "featuresWithMigrationGaps": sum(bool(f["migrationGaps"]) for f in features),
            "sharedPlatformTables": sorted(shared),
        },
        "capabilities": capabilities,
        "features": features,
    }
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "usage_catalog_v19.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / "usage_feature_sheet_v19.csv").write_text(feature_sheet_csv(catalog), encoding="utf-8")
        report = {"phase": USAGE_VERSION, "status": "COMPLETED", "module": module, **catalog["statistics"],
                  "output": str(out.resolve())}
        (out / "usage_report_v19.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return catalog


def feature_sheet_csv(catalog: dict[str, Any], verified_only: bool = True) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Capability", "Feature", "Category", "Readiness", "Verified highlights", "Needs review", "Screen ID"])
    for f in catalog["features"]:
        verified = [c["text"] for c in f["claims"] if c["status"] == VERIFIED]
        review = [c["text"] for c in f["claims"] if c["status"] != VERIFIED]
        w.writerow([f["capability"]["name"], f["name"], f["category"], f["readiness"], " | ".join(verified),
                    "" if verified_only and not review else " | ".join(review), f["screenId"]])
    return buf.getvalue()


# The exporters read the page templates from the frontend project (<root>/frontend).
from .paths import FRONTEND_DIR as UI_DIR


def _inline_scripts(html: str) -> str:
    """An exported page travels as one file: inline the shared scripts it loads (module picker)."""
    js = (UI_DIR / "modules.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    return html.replace('<script src="modules.js"></script>', f"<script>{js}</script>", 1)


def export_static_ui(catalogs: list[dict[str, Any]], output_file: str | Path) -> Path:
    """Write the marketing UI as one self-contained HTML file with the catalogs embedded,
    so it can be opened or shared without running the PIE API."""
    html = (UI_DIR / "catalog.html").read_text(encoding="utf-8")
    payload = json.dumps(catalogs, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("<!--PIE_EMBEDDED_DATA-->", f"<script>window.__PIE_USAGE__={payload};</script>", 1)
    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_inline_scripts(html), encoding="utf-8")
    return out


def export_chat_ui(server_url: str, output_file: str | Path) -> Path:
    """Write the Ask PIE chat as one HTML file that talks to a running PIE server.

    Only the server address is embedded: the OpenAI key and Qdrant access stay on the
    server. The server must allow the file's origin (PIE_CORS_ORIGINS, default allows
    pages opened from disk)."""
    from urllib.parse import urlparse
    u = urlparse(server_url.strip())
    if u.scheme not in {"http", "https"} or not u.netloc:
        raise ValueError(f"--server must be an http(s) address such as http://pie-server:8000, got {server_url!r}")
    base = f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"
    if not base.endswith("/usage"):
        base += "/usage"
    config = json.dumps({"apiBase": base + "/"}).replace("</", "<\\/")
    html = (UI_DIR / "chat.html").read_text(encoding="utf-8")
    html = html.replace("<!--PIE_CHAT_CONFIG-->", f"<script>window.__PIE_CHAT__={config};</script>", 1)
    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_inline_scripts(html), encoding="utf-8")
    return out


def export_fields_ui(dictionaries: list[dict[str, Any]], output_file: str | Path) -> Path:
    """Write the Field Explorer as one self-contained HTML file with the dictionaries embedded."""
    html = (UI_DIR / "fields.html").read_text(encoding="utf-8")
    payload = json.dumps(dictionaries, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = html.replace("<!--PIE_EMBEDDED_DATA-->", f"<script>window.__PIE_FIELDS__={payload};</script>", 1)
    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_inline_scripts(html), encoding="utf-8")
    return out
