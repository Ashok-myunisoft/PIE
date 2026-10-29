"""v0.20 Field-level intelligence for GB5 form screens (first target: Item Master).

Discovers every field of a form screen and extracts per-field facts from the ERP
sources, each with file and line evidence:

  frontend  formjson ObjectFields, metaform pages/sections/rules, i18n labels,
            picklist definitions, component HTML controls and events, component
            TypeScript handlers (dependencies, calculations), save-time messages
  backend   DTO property and type, BLL validations, SQL insert/update/get queries
            (field -> column, lookup joins), endpoints
  database  live SQL Server metadata snapshot (type, nullability, length, FKs)
  ERP-wide  other modules, screens and reports that reference the column/field

The deterministic record is the source of truth. `enrich_fields` optionally asks an
LLM to explain purpose, business meaning, usage, transaction impact and an example
using only these facts; that text is stored separately and labelled as AI-derived.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .erp_source import IGNORED_DIRS, read_text

FIELD_VERSION = "0.20.0"
NOT_FOUND = "Not found in the analysed sources"
OWN_LABEL_NAMESPACES = {"common", "item", "itemmaster", "inventory", "eventtype", "ess", "leave", "employee"}

GB_CONTROL_TYPES = {
    "gb-input": "Text / number input", "gb-newpicklist": "Lookup (picklist)", "gb-radiobutton": "Radio buttons (option)",
    "gb-checkbox": "Checkbox", "gb-combobox": "Dropdown (combo box)", "gb-date": "Date picker", "gb-formgrid": "Grid (detail rows)",
    "gb-gbimage": "Image", "gb-addon": "Input with add-on", "gb-textarea": "Multi-line text",
}
ROLE_WORDS = {"Id": "internal identifier", "Code": "code", "Name": "name"}


# ------------------------------------------------------------------ helpers

def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _ev(source: str, line: int | None, fact: str) -> dict[str, Any]:
    return {"source": source, "line": line, "fact": fact}


DISPLAY_SUFFIX_RE = re.compile(r"(.+?)(Names?|Codes?|Descriptions?|Desc|Text|Titles?)")


def link_display_fields(records: list[dict[str, Any]], form_source: str) -> None:
    """A name shown on the form is often saved through its ID field: DepartmentName is displayed,
    DepartmentId is stored in MEMPLOYEE.DEPARTMENTID. Record that link on the display field
    (database.storedVia) instead of reporting it as "not stored". The ID field comes from the
    picklist's link field, else from the field of the same name ending in Id / Ids."""
    by_name = {r["fieldName"]: r for r in records}
    for r in records:
        db = r["database"]
        if db.get("column") or db.get("childTable") or db.get("lookupDisplay") or db.get("storedVia"):
            continue
        link = (r.get("lookup") or {}).get("linkField") or ""
        candidates = [x.strip() for x in link.split(",") if x.strip() and x.strip() != r["fieldName"]]
        basis = "picklist link field" if candidates else ""
        if not candidates and (m := DISPLAY_SUFFIX_RE.fullmatch(r["fieldName"])):
            candidates, basis = [m.group(1) + s for s in ("Id", "Ids", "ID")], "ID field of the same name"
        for c in candidates:
            target = by_name.get(c)
            if not target:
                continue
            tdb = target["database"]
            table = tdb.get("table") or (tdb.get("childTable") or {}).get("table", "")
            column = tdb.get("column", "")
            db["storedVia"] = {"field": c, "table": table, "column": column, "dataType": tdb.get("dataType", ""),
                               "foreignKey": tdb.get("foreignKey"), "basis": basis}
            where = f"{table}.{column}" if column else (f"{table} (grid rows)" if table else "")
            db["storage"] = (f"display of {c} (stored as {where})" if where
                             else f"display of {c} ({c} is chosen here but not stored by this screen, e.g. a filter)")
            where = where or f"{c}, not stored by this screen"
            r["relatedFields"] = sorted(set(r.get("relatedFields") or []) | {c})
            r["evidence"].append(_ev(form_source, None, f"{r['fieldName']} shows the value kept in {c} -> {where} ({basis})"))
            break


def _lookup_i18n(en: dict[str, Any], key: str) -> str:
    cur: Any = en
    for part in (key or "").split("."):
        if not isinstance(cur, dict) or part not in cur:
            return ""
        cur = cur[part]
    return cur if isinstance(cur, str) else ""


def _const_blocks(qb_text: str) -> dict[str, tuple[int, str]]:
    """public const string NAME = @"..." -> {NAME: (start_offset, sql)}"""
    out = {}
    for m in re.finditer(r'public\s+const\s+string\s+(\w+)\s*=\s*@"((?:""|[^"])*)"', qb_text, re.S):
        out[m.group(1)] = (m.start(2), m.group(2).replace('""', '"'))
    return out


def parse_insert(sql: str) -> tuple[str, dict[str, str]]:
    """INSERT INTO T (C1, C2) VALUES (@P1, @P2) -> ('T', {'P1': 'C1', ...}) by position."""
    m = re.search(r"INSERT\s+INTO\s+\[?(\w+)\]?\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*;?\s*$", sql, re.S | re.I)
    if not m:
        return "", {}
    cols = [c.strip().strip("[]") for c in re.sub(r"--[^\n]*", "", m.group(2)).split(",") if c.strip()]
    vals = [v.strip() for v in re.sub(r"--[^\n]*", "", m.group(3)).split(",") if v.strip()]
    mapping = {}
    for c, v in zip(cols, vals):
        pm = re.fullmatch(r"@(\w+)", v)
        if pm:
            mapping[pm.group(1)] = c.upper()
    return m.group(1).upper(), mapping


def parse_update(sql: str) -> dict[str, str]:
    return {p: c.upper() for c, p in re.findall(r"\b(\w+)\s*=\s*@(\w+)", sql) if c.upper() not in {"WHERE", "AND", "OR"}}


def parse_select(sql: str, base_table: str) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """SELECT X.COL AS Prop ... FROM T X LEFT JOIN M Y ON ... -> ({Prop: {alias, column}}, {alias: table})."""
    aliases = {a.upper(): t.upper() for t, a in re.findall(r"(?:FROM|JOIN)\s+\[?(\w+)\]?\s+(?:AS\s+)?(\w+)\b", sql, re.I)
               if a.upper() not in {"ON", "WHERE", "LEFT", "INNER", "JOIN", "OUTER", "CROSS", "WITH"}}
    props = {}
    for alias, col, prop in re.findall(r"\b(\w+)\.(\w+)\s+AS\s+\[?(\w+)\]?", sql, re.I):
        props.setdefault(prop, {"alias": alias.upper(), "column": col.upper(), "table": aliases.get(alias.upper(), "")})
    return props, aliases


def parse_dto(text: str) -> dict[str, dict[str, Any]]:
    out = {}
    for m in re.finditer(r"public\s+([\w<>?\[\],. ]+?)\s+(\w+)\s*\{\s*get", text):
        typ, name = m.group(1).strip(), m.group(2)
        if name not in out:
            out[name] = {"type": typ, "line": _line_of(text, m.start())}
    return out


def parse_ts_methods(text: str) -> dict[str, dict[str, Any]]:
    """Method name -> body, line, fields read and fields patched."""
    methods = {}
    for m in re.finditer(r"(?:public|private|protected)?\s*(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*(?::\s*[\w<>\[\]| ]+)?\s*\{", text):
        name = m.group(1)
        if name in {"if", "for", "while", "switch", "catch", "function", "subscribe", "constructor"}:
            continue
        i, depth = m.end(), 1
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        body = text[m.end():i - 1]
        methods[name] = {
            "line": _line_of(text, m.start()),
            "reads": sorted(set(re.findall(r"form\.get\(\s*['\"](\w+)['\"]\s*\)\??\.value", body))),
            "patches": sorted(set(re.findall(r"form\.get\(\s*['\"](\w+)['\"]\s*\)\??\.patchValue\(", body))),
            "body": body.strip(),
        }
    return methods


SECTION_RES = (
    re.compile(r'<div[^>]*class\s*=\s*"[^"]*section-?header[^"]*"[^>]*>(.*?)</div>', re.S | re.I),
    re.compile(r"<legend[^>]*>(.*?)</legend>", re.S | re.I),
    re.compile(r"<h[2-5][^>]*>(.*?)</h[2-5]>", re.S | re.I),
    re.compile(r"<mat-expansion-panel-header[^>]*>(.*?)</mat-expansion-panel-header>", re.S | re.I),
    re.compile(r"<!--\s*([A-Za-z][\w &/().-]{1,50}?)\s+Section\s*-->", re.I),
)


def _clean_heading(raw: str) -> str:
    t = re.sub(r"<[^>]+>", " ", raw)
    t = re.sub(r"\{\{\s*['\"]?([\w.]+)['\"]?\s*\|\s*translate\s*\}\}", r"\1", t)   # {{'Key' | translate}}
    t = re.sub(r"\{\{[^}]*\}\}", " ", t)
    return re.sub(r"\s+", " ", t).strip(" *:").strip()


def parse_html_sections(text: str) -> list[tuple[int, str]]:
    heads = []
    for rx in SECTION_RES:
        for m in rx.finditer(text):
            h = _clean_heading(m.group(1))
            if 1 < len(h) <= 60:
                heads.append((m.start(), h))
    return sorted(heads)


def parse_html_controls(text: str) -> dict[str, dict[str, Any]]:
    """formControlName -> control tag, tab, section, events, visibility condition, read-only binding."""
    tabs = [(m.start(), m.group(1)) for m in re.finditer(r'<mat-tab[^>]*\blabel\s*=\s*"([^"]+)"', text)]
    sections = parse_html_sections(text)
    out = {}
    for m in re.finditer(r"<(gb-[\w-]+)\b([^>]*)>", text, re.S):
        tag, attrs = m.group(1), m.group(2)
        fm = re.search(r"formControlName\s*=\s*['\"](\w+)['\"]", attrs)
        if not fm:
            continue
        tab, tab_pos = "", -1
        for pos, label in tabs:
            if pos < m.start():
                tab, tab_pos = label, pos
        # The nearest heading above the control, within the same tab.
        section = next((h for pos, h in reversed(sections) if tab_pos <= pos < m.start()), "")
        events = [{"event": e, "handler": h} for e, h in re.findall(r"\((\w+)\)\s*=\s*\"(\w+)\(", attrs)]
        cond = re.search(r'\*ngIf\s*=\s*"([^"]+)"', attrs)
        ro = re.search(r'\[gbReadOnly\]\s*=\s*"([^"]+)"', attrs)
        out.setdefault(fm.group(1), {"tag": tag, "tab": tab, "section": section, "events": events, "visibleIf": cond.group(1) if cond else "",
                                     "readOnlyIf": ro.group(1) if ro else "", "line": _line_of(text, m.start())})
    return out


def _condition_fields(expr: str) -> list[str]:
    return sorted(set(re.findall(r"form\.get\(\s*['\"](\w+)['\"]\s*\)", expr or "")))


# ------------------------------------------------------------------ discovery


class ScreenSources:
    """Locates the source files of one GB5 form screen."""

    def __init__(self, erp_root: str | Path, form: str, module: str, entity: str | None = None):
        self.erp = Path(erp_root)
        self.fe = self.erp / "frontend"
        self.be = self.erp / "backend"
        self.form, self.module = form, module
        proj = self.fe / "projects" / module
        self.formjson = proj / "public" / "formjson" / f"{form}.json"
        self.metaform = proj / "public" / "metaform" / f"{form}.meta.json"
        self.component_ts = next(iter(sorted(proj.glob(f"**/{form}/{form}.component.ts"))), None) or \
            next(iter(sorted(p for p in proj.glob(f"**/{form}.component.ts") if not any(x in IGNORED_DIRS for x in p.parts))), None)
        self.component_html = self.component_ts.with_suffix(".html") if self.component_ts else None
        self.service_ts = proj / "service" / f"{form}.service.ts"
        self.i18n = self.fe / "projects" / "gbhost" / "public" / "i18n" / "English.json"
        self.picklists = self.fe / "projects" / "gbhost" / "public" / "picklist" / "allpicklist.json"
        self.validation_cs = self.be / "GB5Shared" / "Validation" / "Validation.cs"
        self.entity_source = "given" if entity else ""
        self.url_entities: list[str] = []
        candidates = [entity] if entity else self._entity_candidates()
        self.entity, self.dto, self.bll, self.qb, self.dal = (candidates[0] if candidates else ""), None, None, None, None
        for cand in candidates:
            found = self._find_backend(cand)
            if found["dto"] or found["qb"]:
                self.entity = cand
                self.dto, self.bll, self.qb, self.dal = found["dto"], found["bll"], found["qb"], found["dal"]
                self.entity_source = self.entity_source or "form action URL"
                break

    def _entity_candidates(self) -> list[str]:
        """Backend entity of a form: from the GB5 form action URLs (leaverequest -> /prs/TLeave.svc -> TLeave)."""
        from .api_mapping import legacy_url_entity, FORM_ACTION_DIR, FORM_ACTION_FILES, FORM_ACTION_RE
        names = []
        for fname in FORM_ACTION_FILES.values():
            path = self.fe / FORM_ACTION_DIR / fname
            if path.exists():
                for key, url in FORM_ACTION_RE.findall(read_text(path)):
                    if key.lower() == self.form.lower() and url:
                        ent = legacy_url_entity(url)[0]
                        if ent and ent not in names:
                            names.append(ent)
        self.url_entities = list(names)
        guess = self.form[:1].upper() + self.form[1:]
        return names + ([guess] if guess not in names else [])

    def _find_backend(self, entity: str) -> dict[str, Path | None]:
        found = {}
        for kind, pattern in (("dto", f"**/DTO/{entity}/{entity}DTO.cs"), ("bll", f"**/{entity}/{entity}BLL.cs"),
                              ("qb", f"**/Query/{entity}/{entity}QB.cs"), ("dal", f"**/Custom*Code/{entity}/{entity}DAL.cs")):
            hits = sorted(p for p in self.be.glob("GB5Solution/*/" + pattern) if not any(x in IGNORED_DIRS for x in p.parts))
            found[kind] = hits[0] if hits else None
        return found

    def rel(self, p: Path | None) -> str:
        return _rel(self.erp, p) if p else ""


def _load_db_table(db_registry: str | Path | None, table: str) -> dict[str, Any]:
    if not db_registry or not Path(db_registry).exists() or not table:
        return {}
    objs = json.loads(Path(db_registry).read_text(encoding="utf-8"))
    obj = next((o for o in objs if (o.get("name") or "").upper() == table), None)
    if not obj:
        return {}
    return {"columns": {c["name"].upper(): c for c in obj.get("columns", [])},
            "fks": {f["column"].upper(): f for f in obj.get("foreignKeys", [])},
            "pks": [p.upper() for p in obj.get("primaryKeys", [])]}


def scan_erp_usage(erp_root: Path, table: str, columns: set[str], properties: set[str], own_files: set[str],
                   max_refs: int = 6) -> tuple[dict[str, Any], dict[str, Any]]:
    """Single-table form of scan_erp_usage_multi (keys are column names)."""
    col_use, prop_use = scan_erp_usage_multi(erp_root, {table: columns}, properties, own_files, max_refs)
    return {k.split(".", 1)[1]: v for k, v in col_use.items()}, prop_use


def scan_erp_usage_multi(erp_root: Path, table_columns: dict[str, set[str]], properties: set[str], own_files: set[str],
                         max_refs: int = 6) -> tuple[dict[str, Any], dict[str, Any]]:
    """Where else the ERP uses each TABLE.COLUMN (backend SQL/C#) and each property (frontend), in one pass.

    A column hit counts for a table only in files that name that table as a whole word
    (MITEM must not match MITEMCATEGORY)."""
    col_use: dict[str, Any] = defaultdict(lambda: {"modules": Counter(), "reports": set(), "refs": []})
    prop_use: dict[str, Any] = defaultdict(lambda: {"screens": set(), "refs": []})
    table_columns = {t: c for t, c in table_columns.items() if t and c}
    all_cols = set().union(*table_columns.values()) if table_columns else set()
    col_rx = re.compile(r"\b(" + "|".join(sorted(map(re.escape, all_cols), key=len, reverse=True)) + r")\b") if all_cols else None
    table_rx = re.compile(r"\b(" + "|".join(sorted(map(re.escape, table_columns), key=len, reverse=True)) + r")\b") if table_columns else None
    prop_rx = re.compile(r"\b(" + "|".join(sorted(map(re.escape, properties), key=len, reverse=True)) + r")\b") if properties else None
    be = erp_root / "backend" / "GB5Solution"
    for p in be.rglob("*") if col_rx else []:
        if p.suffix.lower() not in {".cs", ".sql"} or any(x in IGNORED_DIRS for x in p.parts):
            continue
        rel = _rel(erp_root, p)
        if rel in own_files:
            continue
        txt = read_text(p, 600_000)
        tables_here = set(table_rx.findall(txt))
        if not tables_here:
            continue
        parts = rel.split("/")
        module = parts[2] if len(parts) > 2 else ""
        lines = None
        for m in col_rx.finditer(txt):
            for t in tables_here:
                if m.group(1) not in table_columns[t]:
                    continue
                u = col_use[f"{t}.{m.group(1)}"]
                u["modules"][module] += 1
                if "report" in rel.lower():
                    u["reports"].add(rel)
                if len(u["refs"]) < max_refs and not any(r["source"] == rel for r in u["refs"]):
                    lines = lines or txt.splitlines()
                    line_no = _line_of(txt, m.start())
                    u["refs"].append({"source": rel, "line": line_no, "fact": lines[line_no - 1].strip()[:180] if line_no <= len(lines) else ""})
    fe = erp_root / "frontend" / "projects"
    for p in fe.rglob("*") if prop_rx else []:
        if p.suffix.lower() not in {".ts", ".html", ".json"} or any(x in IGNORED_DIRS for x in p.parts):
            continue
        rel = _rel(erp_root, p)
        if rel in own_files or "/i18n/" in rel:
            continue
        txt = read_text(p, 400_000)
        if not prop_rx.search(txt):
            continue
        screen = "/".join(rel.split("/")[2:4]) + ":" + p.stem.split(".")[0]
        for m in prop_rx.finditer(txt):
            u = prop_use[m.group(1)]
            u["screens"].add(screen)
            if len(u["refs"]) < max_refs and not any(r["source"] == rel for r in u["refs"]):
                u["refs"].append({"source": rel, "line": _line_of(txt, m.start()), "fact": "field referenced"})
    return col_use, prop_use


def grid_child_tables(qb_root: Path, erp_root: Path) -> list[dict[str, Any]]:
    """Every INSERT in the module's query builders: table, parameters, source."""
    out = []
    for p in qb_root.rglob("*QB.cs"):
        txt = read_text(p, 3_000_000)
        for name, (pos, sql) in _const_blocks(txt).items():
            table, mapping = parse_insert(sql)
            if table and mapping:
                out.append({"table": table, "params": mapping, "const": name, "source": _rel(erp_root, p), "line": _line_of(txt, pos)})
    return out


def _page_name(tab: str) -> str:
    """'Classification*' (tab with mandatory fields) -> 'Classification'; 'Quality&Service' -> 'Quality & Service'."""
    return re.sub(r"\s*&\s*", " & ", (tab or "").rstrip("*").strip())


def _readable(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s).strip()


def _pick_const(consts: dict[str, tuple[int, str]], entity: str, prefixes: tuple[str, ...], sql_kind: str) -> str:
    """The query builder's save/update/get statement: <PREFIX>_<ENTITY> first, else the first of that kind."""
    ent = (entity or "").upper()
    for pre in prefixes:
        if f"{pre}_{ent}" in consts:
            return f"{pre}_{ent}"
    for name, (_, sql) in consts.items():
        if name.upper().startswith(prefixes) and re.search(sql_kind, sql, re.I):
            return name
    return ""


def extract_screen_fields(erp_root: str | Path, form: str = "itemmaster", module: str = "inventory", entity: str | None = "Item",
                          screen_id: str | None = "INVENTORY_ITEM_MASTER", screen_name: str | None = "Item Master",
                          db_registry: str | Path | None = None, api_mapping: str | Path | None = None,
                          scan_usage: bool = True, usage: tuple[dict[str, Any], dict[str, Any]] | None = None) -> dict[str, Any]:
    """One screen's field dictionary. `entity`, `screen_id` and `screen_name` are discovered when None.
    `usage` is a precomputed scan_erp_usage_multi result (keys TABLE.COLUMN) shared by a module run."""
    src = ScreenSources(erp_root, form, module, entity)
    if not (src.dto or src.qb) and api_mapping and Path(api_mapping).exists() and screen_id:
        # Screens without their own form-action URL reach the backend through the
        # component's MAIN form (GBBaseFormGroup); lookup forms it also loads do not count.
        main = re.search(r"GBBaseFormGroup\s*\(\s*this\.injector\s*,\s*['\"](\w+)['\"]",
                         read_text(src.component_ts, 400_000) if src.component_ts else "")
        row = next((r for r in json.loads(Path(api_mapping).read_text(encoding="utf-8")) if r.get("screenId") == screen_id), None)
        action_forms = {x.split(":")[-1] for a in (row or {}).get("apiMappings", []) for x in
                        (str(e.get("source", "")) for e in a.get("frontendEvidence", []) or []) if x.startswith("form-action:")}
        # Without a GBBaseFormGroup call, a screen with no form-action URL of its own that uses
        # exactly one form is a variant of that form (on-duty hour-wise -> permissionrequest).
        use_form = main.group(1) if main else (next(iter(action_forms)) if len(action_forms) == 1 and not src.url_entities else None)
        for a in (row or {}).get("apiMappings", []):
            srcs = {str(x.get("source", "")) for x in a.get("frontendEvidence", []) or []}
            if use_form and any(x.startswith("form-action:") and x.endswith(f":{use_form}") for x in srcs):
                ent = a.get("endpointFile", "").split("/EndPoints/")[-1].split("/")[0]
                found = src._find_backend(ent) if ent else {}
                if found.get("dto") or found.get("qb"):
                    src.entity, src.entity_source = ent, f"{'main' if main else 'shared'} form '{use_form}' endpoints"
                    src.dto, src.bll, src.qb, src.dal = found["dto"], found["bll"], found["qb"], found["dal"]
                    break
    entity = src.entity
    screen_id = screen_id or f"{module.upper()}_{form.upper()}"
    screen_name = screen_name or _readable(form[:1].upper() + form[1:])
    if not src.formjson.exists():
        raise FileNotFoundError(f"Form definition not found: {src.formjson}")
    formjson_txt = read_text(src.formjson, 5_000_000)
    # Some form definitions contain entries without a field name (separators, placeholders): skip those.
    fields = [f for f in json.loads(formjson_txt)["ObjectFields"] if isinstance(f, dict) and isinstance(f.get("Name"), str) and f["Name"]]
    meta = json.loads(read_text(src.metaform, 5_000_000)) if src.metaform.exists() else {}
    en = json.loads(read_text(src.i18n, 20_000_000)) if src.i18n.exists() else {}
    picklists = json.loads(read_text(src.picklists, 20_000_000)) if src.picklists.exists() else {}
    html_txt = read_text(src.component_html, 2_000_000) if src.component_html and src.component_html.exists() else ""
    ts_txt = read_text(src.component_ts, 2_000_000) if src.component_ts and src.component_ts.exists() else ""
    svc_txt = read_text(src.service_ts, 2_000_000) if src.service_ts.exists() else ""
    dto_txt = read_text(src.dto, 5_000_000) if src.dto else ""
    bll_txt = read_text(src.bll, 2_000_000) if src.bll else ""
    qb_txt = read_text(src.qb, 5_000_000) if src.qb else ""
    val_txt = read_text(src.validation_cs, 500_000) if src.validation_cs.exists() else ""

    controls = parse_html_controls(html_txt)
    methods = parse_ts_methods(ts_txt)
    dto = parse_dto(dto_txt)
    consts = _const_blocks(qb_txt)
    save_name = _pick_const(consts, entity, ("SAVE", "INSERT"), r"\bINSERT\s+INTO\b")
    update_name = _pick_const(consts, entity, ("UPDATE",), r"\bUPDATE\b")
    get_name = _pick_const(consts, entity, ("GET",), r"\bAS\s+\w+")
    table, insert_map = parse_insert(consts[save_name][1]) if save_name else ("", {})
    update_map = parse_update(consts[update_name][1]) if update_name else {}
    get_props, get_aliases = parse_select(consts[get_name][1], table) if get_name else ({}, {})
    base_alias = next((a for a, t in get_aliases.items() if t == table), "")
    db = _load_db_table(db_registry, table)

    # Meta form: page/section per field, rules (calculations), status actions.
    meta_pos: dict[str, dict[str, str]] = {}
    for page in meta.get("pages", []):
        for sec in page.get("sections", []) or []:
            for f in sec.get("fields", []) or []:
                meta_pos.setdefault(f.get("Name"), {"page": page.get("pageName", ""), "section": sec.get("sectionName", ""),
                                                    "metaLabel": f.get("Label", "")})
    calc_rules = {r["target"]: r for r in meta.get("rules", []) if r.get("type") == "calculate" and r.get("target")}
    rule_triggers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in calc_rules.values():
        for t in r.get("trigger", []):
            rule_triggers[t].append(r)
    quick_actions = {f["Name"]: (qa, f) for qa in meta.get("quickActions", []) if isinstance(qa, dict)
                     for f in qa.get("fields", []) or [] if isinstance(f, dict) and f.get("Name")}

    # Backend validations in the save path.
    notempty = {m.group(1): _line_of(bll_txt, m.start()) for m in re.finditer(r"_Validation\s*\.\s*NotEmpty\(\s*\w+\.(\w+)\s*,", bll_txt)
                if not bll_txt[max(0, bll_txt.rfind("\n", 0, m.start())):m.start()].lstrip().startswith("//")}
    notempty_msg = re.search(r'NotEmpty\(string value.*?throw new ValidationException\(\$"\{field\}([^"]*)"', val_txt, re.S)
    notempty_msg = "{field}" + notempty_msg.group(1) if notempty_msg else "{field} cannot be empty or whitespace."
    fe_required_msg = "<Label> Should not be Empty" if "Should not be Empty" in svc_txt else ""
    fe_pct_msg = "Please Provide Valid Percentge - <Label>" if "Valid Percentge" in svc_txt else ""

    # Endpoints serving this screen (phase 14 mapping, if available).
    endpoints = []
    if api_mapping and Path(api_mapping).exists():
        rows = json.loads(Path(api_mapping).read_text(encoding="utf-8"))
        row = next((r for r in rows if r.get("screenId") == screen_id), None)
        for a in (row or {}).get("apiMappings", []):
            if a.get("confidence", 0) >= .8:
                endpoints.append({"method": a["method"], "route": a["route"], "file": a.get("endpointFile", "")})

    names = [f["Name"] for f in fields]
    name_set = set(names)
    inserts = grid_child_tables(src.qb.parent.parent, src.erp) if src.qb else []
    # Hidden ID fields are filled by a lookup: label them after that lookup.
    link_label: dict[str, str] = {}
    for f in fields:
        for link in (f.get("LinkId") or "").split(","):
            if link and link != f["Name"] and f.get("Label"):
                link_label.setdefault(link, (_lookup_i18n(en, f["Label"]) or _readable(f["Name"])).rstrip("*").strip())
    own = {src.rel(p) for p in (src.formjson, src.metaform, src.component_ts, src.component_html, src.service_ts,
                                 src.dto, src.bll, src.qb, src.dal) if p}
    col_use, prop_use = ({}, {})
    if usage is not None:
        col_use = {k.split(".", 1)[1]: v for k, v in usage[0].items() if k.startswith(f"{table}.")} if table else {}
        prop_use = usage[1]
    elif scan_usage and table:
        columns = set(insert_map.values()) | set(update_map.values())
        col_use, prop_use = scan_erp_usage(src.erp, table, columns, name_set, own)
    report_consts = {n: sql for n, (_, sql) in consts.items() if "REPORT" in n.upper()}

    records = []
    for order, f in enumerate(fields, 1):
        name = f["Name"]
        ev: list[dict[str, Any]] = []
        fj_line = _line_of(formjson_txt, formjson_txt.find(f'"{name}"')) if f'"{name}"' in formjson_txt else None
        ev.append(_ev(src.rel(src.formjson), fj_line, "form field definition (ObjectFields)"))
        label_key = f.get("Label") or ""
        meta_info = meta_pos.get(name, {})
        label_text = _lookup_i18n(en, label_key) or _lookup_i18n(en, meta_info.get("metaLabel", ""))
        label_source = src.rel(src.i18n) if label_text else ""
        if not label_text and label_key and not re.fullmatch(r"[A-Za-z]\w*(\.[A-Za-z]\w*)+", label_key):
            # Not a translation key: the form definition holds the label text itself.
            label_text, label_source = label_key, src.rel(src.formjson) + " (label text)"
        label_clean = label_text.rstrip("*").strip()
        if not label_clean and name in link_label:
            label_clean, label_source = f"{link_label[name]} (internal ID)", "label of the lookup that fills this ID"
        if not label_clean:
            label_clean, label_source = _readable(name), "derived from field name"
        ctrl = controls.get(name, {})

        # Column mapping: insert/update parameters, get aliases, then lookup joins.
        column = insert_map.get(name) or update_map.get(name) or ""
        gp = get_props.get(name, {})
        if not column and gp and gp.get("alias") == base_alias:
            column = gp["column"]
        lookup_display = {}
        if gp and gp.get("alias") != base_alias and gp.get("table"):
            lookup_display = {"table": gp["table"], "column": gp["column"]}
        # Grids (detail rows) are saved to a child table.
        child_table = None
        # Some forms (Finance, HRMS) give a grid column's value as a list or object instead of a field name.
        grid_cols = [g.get("value") for g in (f.get("Grid") if isinstance(f.get("Grid"), list) else []) if isinstance(g, dict) and isinstance(g.get("value"), str) and g.get("value")]
        if grid_cols:
            best = max(inserts, key=lambda ins: len(set(grid_cols) & set(ins["params"])), default=None)
            hit = len(set(grid_cols) & set(best["params"])) if best else 0
            if best and hit >= max(2, len(grid_cols) // 3):
                child_table = {"table": best["table"], "query": best["const"], "source": best["source"], "matchedColumns": hit,
                               "columns": {c: best["params"][c] for c in grid_cols if c in best["params"]}}
                ev.append(_ev(best["source"], best["line"], f"{best['const']}: grid rows saved to {best['table']} ({hit} matching columns)"))
        col_meta = db.get("columns", {}).get(column, {}) if column else {}
        fk = db.get("fks", {}).get(column) if column else None
        if column:
            ev.append(_ev(src.rel(src.qb), _line_of(qb_txt, consts[save_name][0]) if save_name else None,
                          f"{save_name or update_name}: @{name} -> {table}.{column}"))
        if lookup_display:
            ev.append(_ev(src.rel(src.qb), _line_of(qb_txt, consts[get_name][0]), f"{get_name}: {name} = {lookup_display['table']}.{lookup_display['column']} (lookup join)"))

        # Picklist / lookup details.
        pk_key = f.get("PicklistJSON") or ""
        pk_def = picklists.get(pk_key) if isinstance(picklists, dict) else None
        lookup = {}
        if pk_key:
            lookup = {"picklist": pk_key, "linkField": f.get("LinkId", ""), "title": f.get("PicklistTitle", ""),
                      "criteria": f.get("PicklistCriteria", ""), "multiple": bool(f.get("Multiple")), "allowAddNew": bool(f.get("AddNew")),
                      "source": (pk_def or {}).get("PicklistUrl", ""), "displayColumns": (pk_def or {}).get("PicklistDisplayLabel", "").strip(" ,")}
            ev.append(_ev(src.rel(src.picklists), None, f"picklist '{pk_key}' -> source '{lookup['source'] or 'n/a'}'"))

        # Allowed values.
        allowed = [{"value": o.get("key"), "label": o.get("value")} for o in (f.get("PickLists") or [])]
        if not allowed and name in quick_actions:
            allowed = [{"value": o.get("key"), "label": o.get("value")} for o in quick_actions[name][1].get("PickLists", [])]

        # Mandatory.
        required = bool(f.get("Required"))
        cond_req = {"field": f["RequireBasedOnFieldName"], "values": f.get("RequireBasedOnFieldValue")} if f.get("RequireBasedOnFieldName") else None
        backend_req = name in notempty
        db_not_null = col_meta.get("nullable") is False if col_meta else None
        if backend_req:
            ev.append(_ev(src.rel(src.bll), notempty[name], f"_Validation.NotEmpty({entity}DTO.{name})"))

        validations, messages = [], []
        if required:
            validations.append({"rule": "Required on save" + (f" when {cond_req['field']} is {cond_req['values']}" if cond_req else ""), "layer": "frontend", "source": src.rel(src.formjson)})
            if fe_required_msg:
                messages.append({"message": fe_required_msg.replace("<Label>", label_clean), "layer": "frontend", "source": src.rel(src.service_ts)})
        if backend_req:
            validations.append({"rule": "Must not be empty or whitespace", "layer": "backend", "source": src.rel(src.bll)})
            messages.append({"message": notempty_msg.replace("{field}", name), "layer": "backend", "source": src.rel(src.validation_cs)})
        if f.get("MaxLength"):
            validations.append({"rule": f"Maximum length {f['MaxLength']} characters", "layer": "frontend", "source": src.rel(src.formjson)})
        if col_meta.get("maxLength"):
            validations.append({"rule": f"Database column length {col_meta['maxLength']}", "layer": "database", "source": f"{table}.{column}"})
        if db_not_null:
            validations.append({"rule": "Database column is NOT NULL", "layer": "database", "source": f"{table}.{column}"})
        if f.get("ValidRegEx"):
            validations.append({"rule": f"Must match pattern {f['ValidRegEx']}", "layer": "frontend", "source": src.rel(src.formjson)})
        if f.get("Type") == "Percentage":
            validations.append({"rule": "Must be a valid percentage", "layer": "frontend", "source": src.rel(src.service_ts)})
            if fe_pct_msg:
                messages.append({"message": fe_pct_msg.replace("<Label>", label_clean), "layer": "frontend", "source": src.rel(src.service_ts)})
        if fk:
            validations.append({"rule": f"Must reference an existing {fk['refTable']}.{fk['refColumn']} (foreign key)", "layer": "database", "source": f"{table}.{column}"})

        # Dependencies and calculations.
        deps: list[dict[str, Any]] = []
        calc = None
        if name in calc_rules:
            r = calc_rules[name]
            calc = {"formula": r["formula"], "inputs": r.get("trigger", []), "readOnly": bool(r.get("targetReadOnly")), "source": src.rel(src.metaform)}
            ev.append(_ev(src.rel(src.metaform), None, f"calculated: {r['formula']}"))
            deps.append({"kind": "calculated from", "fields": r.get("trigger", []), "detail": r["formula"]})
        for r in rule_triggers.get(name, []):
            deps.append({"kind": "used in calculation of", "fields": [r["target"]], "detail": r["formula"]})
        if cond_req:
            deps.append({"kind": "required depending on", "fields": [cond_req["field"]], "detail": f"required when {cond_req['field']} in {cond_req['values']}"})
        if ctrl.get("visibleIf"):
            deps.append({"kind": "shown only when", "fields": _condition_fields(ctrl["visibleIf"]), "detail": ctrl["visibleIf"]})
        if ctrl.get("readOnlyIf"):
            deps.append({"kind": "read-only when", "fields": [], "detail": ctrl["readOnlyIf"]})
        if lookup.get("linkField") and lookup["linkField"] != name:
            deps.append({"kind": "lookup sets", "fields": [x for x in lookup["linkField"].split(",") if x in name_set], "detail": f"selecting a value fills {lookup['linkField']}"})
        handler_logic = []
        for e in ctrl.get("events", []):
            m = methods.get(e["handler"])
            if not m:
                continue
            patched = [x for x in m["patches"] if x != name]
            handler_logic.append({"event": e["event"], "handler": e["handler"], "line": m["line"], "updates": patched, "reads": m["reads"],
                                  "code": m["body"][:600]})
            ev.append(_ev(src.rel(src.component_ts), m["line"], f"{e['event']} -> {e['handler']}()"))
            if patched:
                deps.append({"kind": "changing it updates", "fields": patched, "detail": f"via {e['handler']}()"})
        for mname, m in methods.items():
            if name in m["patches"] and not any(h["handler"] == mname for h in handler_logic):
                deps.append({"kind": "set by logic", "fields": [x for x in m["reads"] if x != name], "detail": f"{mname}() (line {m['line']})"})
        for other, c in controls.items():
            if other != name and name in _condition_fields(c.get("visibleIf", "")):
                deps.append({"kind": "controls visibility of", "fields": [other], "detail": c["visibleIf"]})

        config = []
        if name == (meta.get("codeDefine") or {}).get("generationTypeFieldId") or (cond_req and cond_req["field"] == (meta.get("codeDefine") or {}).get("generationTypeFieldId")):
            config.append("Code Define configuration: code generation type (automatic or manual) decides whether the code is entered or generated.")
        if ctrl.get("readOnlyIf"):
            config.append(f"Read-only state is controlled by '{ctrl['readOnlyIf']}' in the screen logic.")

        related_masters = sorted({x for x in [fk["refTable"] if fk else "", lookup_display.get("table", ""), lookup.get("source", "")] if x and x != table})
        related_fields = sorted({x for d in deps for x in d["fields"] if x in name_set and x != name})
        cu = col_use.get(column, {}) if column else {}
        pu = prop_use.get(name, {})
        modules_using = dict(cu.get("modules", Counter()).most_common(15)) if cu else {}
        reports = sorted(cu.get("reports", set()))[:12] if cu else []
        # Report queries of the screen's own query builder that select this column.
        if column:
            reports = [f"{src.rel(src.qb)} ({n})" for n, sql in report_consts.items() if re.search(rf"\b{re.escape(column)}\b", sql)] + reports

        form_type = f.get("Type", "")
        ui_type = GB_CONTROL_TYPES.get(ctrl.get("tag", ""), "")
        if not ui_type:
            ui_type = "Lookup (picklist)" if pk_key else ("Option list" if allowed else ("Grid (detail rows)" if isinstance(f.get("Grid"), list) and f["Grid"] else ("Hidden / system field" if not label_key and not ctrl else form_type)))

        records.append({
            "screenId": screen_id, "screen": screen_name, "module": module, "order": order,
            "fieldName": name, "technicalName": name,
            "label": label_clean, "labelKey": label_key, "labelSource": label_source,
            "labelNote": (f"Label text comes from the '{label_key.split('.')[0]}' translations of another screen"
                          if label_source.endswith("English.json") and "." in label_key and label_key.split(".")[0].lower() not in OWN_LABEL_NAMESPACES else ""),
            "page": _page_name(ctrl.get("tab") or meta_info.get("page") or ""), "section": meta_info.get("section") or ctrl.get("section", ""),
            "metaPage": meta_info.get("page", ""),
            "visibleOnScreen": bool(ctrl) or bool(meta_info),
            "type": {"form": form_type, "ui": ui_type, "backend": dto.get(name, {}).get("type", ""),
                     "database": (f"{col_meta.get('dataType')}" + (f"({col_meta['maxLength']})" if col_meta.get("maxLength") else "")) if col_meta else ""},
            "allowedValues": allowed,
            "defaultValue": f.get("DefaultValue", None),
            "mandatory": {"required": required, "conditional": cond_req, "backendValidated": backend_req, "databaseNotNull": db_not_null},
            "maxLength": {"form": f.get("MaxLength"), "database": col_meta.get("maxLength") if col_meta else None},
            "validations": validations, "messages": messages,
            "dependencies": deps, "calculation": calc, "handlerLogic": handler_logic,
            "lookup": lookup,
            "database": {"table": table if column else "", "column": column, "dataType": col_meta.get("dataType", "") if col_meta else "",
                         "nullable": col_meta.get("nullable") if col_meta else None,
                         "foreignKey": {"table": fk["refTable"], "column": fk["refColumn"]} if fk else None,
                         "lookupDisplay": lookup_display or None, "primaryKey": column in db.get("pks", []) if column else False,
                         "childTable": child_table,
                         "storage": ("column" if column else "child table" if child_table else "related master (display)" if lookup_display
                                     else ("backend only (not stored in " + table + ")" if table else "backend only (no table found)")
                                     if name in dto else "screen only (not stored)")},
            "backend": {"dto": f"{entity}DTO.{name}" if name in dto else "", "dtoFile": src.rel(src.dto) if name in dto else "",
                        "dtoLine": dto.get(name, {}).get("line"),
                        "queries": [q for q, used in ((save_name, name in insert_map), (update_name, name in update_map), (get_name, name in get_props)) if used and q],
                        "endpoints": endpoints, "bll": src.rel(src.bll) if backend_req else ""},
            "frontend": {"component": src.rel(src.component_ts) if ctrl else "", "control": ctrl.get("tag", ""), "htmlLine": ctrl.get("line"),
                         "events": ctrl.get("events", []), "formDefinition": src.rel(src.formjson), "metaForm": src.rel(src.metaform) if meta_info else "",
                         "postData": f.get("PostData"), "placeholder": f.get("PlaceHolder") or f.get("Placeholder") or "",
                         "grid": [{"column": g.get("value") if isinstance(g.get("value"), str) else "",
                                   "label": (_lookup_i18n(en, g["Label"]) or g["Label"]) if isinstance(g.get("Label"), str) else "",
                                   "type": g.get("Type")} for g in f["Grid"] if isinstance(g, dict)] if isinstance(f.get("Grid"), list) else []},
            "relatedMasters": related_masters,
            "usage": {"modules": modules_using, "reports": reports, "backendReferences": cu.get("refs", []) if cu else [],
                      "screens": sorted(pu.get("screens", set()))[:25] if pu else [], "frontendReferences": pu.get("refs", []) if pu else []},
            "configuration": config,
            "relatedFields": related_fields,
            "evidence": ev,
        })
    # A field name defined twice in the form definition is a source defect: keep both,
    # give each its own key, and state the conflict.
    name_counts = Counter(r["fieldName"] for r in records)
    for r in records:
        r["fieldKey"] = r["fieldName"] if name_counts[r["fieldName"]] == 1 else f"{r['fieldName']}#{r['order']}"
        r["duplicateDefinition"] = ""
        if name_counts[r["fieldName"]] > 1:
            twins = [x for x in records if x["fieldName"] == r["fieldName"]]
            diffs = [k for k in ("label", "mandatory", "defaultValue", "type") if len({json.dumps(t[k], sort_keys=True, default=str) for t in twins}) > 1]
            r["duplicateDefinition"] = (f"'{r['fieldName']}' is defined {len(twins)} times in {src.rel(src.formjson)} (entries "
                                        f"{', '.join(str(t['order']) for t in twins)})" + (f" with different {', '.join(diffs)}" if diffs else ""))
    label_counts = Counter(r["label"] for r in records if r["visibleOnScreen"])
    for r in records:
        if r["visibleOnScreen"] and label_counts[r["label"]] > 1:
            others = [x["fieldName"] for x in records if x["label"] == r["label"] and x["fieldName"] != r["fieldName"]]
            r["labelNote"] = (r["labelNote"] + "; " if r["labelNote"] else "") + f"Same label also used by {', '.join(others[:4])}"
    for r in records:
        r["factHash"] = hashlib.sha256(json.dumps({k: v for k, v in r.items() if k != "evidence"}, sort_keys=True, default=str).encode()).hexdigest()[:16]
    link_display_fields(records, src.rel(src.formjson))
    stats = {
        "fields": len(records), "withLabel": sum(r["labelSource"] != "derived from field name" for r in records),
        "childTables": sum(bool(r["database"]["childTable"]) for r in records),
        "onScreen": sum(r["visibleOnScreen"] for r in records), "mappedToColumn": sum(bool(r["database"]["column"]) for r in records),
        "lookupDisplay": sum(bool(r["database"]["lookupDisplay"]) for r in records), "lookups": sum(bool(r["lookup"]) for r in records),
        "withAllowedValues": sum(bool(r["allowedValues"]) for r in records), "mandatory": sum(r["mandatory"]["required"] for r in records),
        "calculated": sum(bool(r["calculation"]) for r in records),
        "uniqueFields": len(set(r["fieldName"] for r in records)),
        "duplicateDefinitions": sum(bool(r["duplicateDefinition"]) for r in records),
        "labelNotes": sum(bool(r["labelNote"]) for r in records), "withDependencies": sum(bool(r["dependencies"]) for r in records),
        "usedByOtherModules": sum(bool(r["usage"]["modules"]) for r in records),
        "storedViaIdField": sum(bool(r["database"].get("storedVia")) for r in records),
    }
    return {"fieldVersion": FIELD_VERSION, "screenId": screen_id, "screen": screen_name, "module": module, "form": form,
            "entity": entity, "entitySource": src.entity_source or "not found", "backendFound": bool(src.dto or src.qb),
            "table": table, "generatedAt": datetime.now(timezone.utc).isoformat(),
            "sources": {k: src.rel(v) for k, v in (("formDefinition", src.formjson), ("metaForm", src.metaform), ("componentTs", src.component_ts),
                                                    ("componentHtml", src.component_html), ("service", src.service_ts), ("dto", src.dto),
                                                    ("bll", src.bll), ("queryBuilder", src.qb), ("dal", src.dal), ("labels", src.i18n),
                                                    ("picklists", src.picklists)) if v},
            "pages": [p.get("pageName") for p in meta.get("pages", [])],
            "statistics": stats, "fields": records}


# ------------------------------------------------------------------ module run


def _registry_screen(registry: list[dict[str, Any]], module: str, form: str) -> dict[str, Any] | None:
    """The identity-registry screen for a form: by form definition file, else by component folder."""
    rows = [r for r in registry if (r.get("module") or "").lower() == module.lower()]
    for r in rows:
        if (r.get("formDefinitionPath") or "").lower().endswith(f"/{form.lower()}.json"):
            return r
    for r in rows:
        if f"/{form.lower()}/" in (r.get("componentPath") or "").lower():
            return r
    return None


def write_module_summary(fields_dir: str | Path, module: str) -> dict[str, Any]:
    """module_fields_summary_v20.json from EVERY dictionary in the folder, so a partial rebuild
    (some forms only, or a screen built on its own like Item Master) keeps the module totals complete."""
    out = Path(fields_dir)
    dictionaries = []
    for p in sorted(out.glob("*_fields_v20.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and isinstance(d.get("fields"), list) and d.get("statistics"):
            dictionaries.append(d)
    summary = {"fieldVersion": FIELD_VERSION, "module": module, "generatedAt": datetime.now(timezone.utc).isoformat(),
               "screens": [{"form": d.get("form"), "screenId": d.get("screenId"), "screen": d.get("screen"), "entity": d.get("entity"),
                            "table": d.get("table"), "backendFound": d.get("backendFound"), **d["statistics"]} for d in dictionaries],
               "totals": {k: sum(d["statistics"].get(k, 0) or 0 for d in dictionaries) for k in
                          ("fields", "mandatory", "mappedToColumn", "lookups", "calculated", "withDependencies", "usedByOtherModules", "explained")}}
    (out / "module_fields_summary_v20.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    return summary


def build_module_fields(erp_root: str | Path, module: str, module_dir: str | Path, llm: Any = None,
                        forms: list[str] | None = None, progress: Any = None) -> list[dict[str, Any]]:
    """Field dictionaries for every form screen of a module, with ONE ERP-wide usage scan.

    Pass 1 extracts each form without usage; the tables, columns and field names of all
    forms are then scanned once; pass 2 re-extracts with that shared usage index."""
    erp, mdir = Path(erp_root), Path(module_dir)
    say = progress or (lambda m: None)
    out = mdir / "phase20-fields"
    out.mkdir(parents=True, exist_ok=True)
    reg_path = mdir / "phase13-identity" / "screen_registry_v13.json"
    registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else []
    db_registry = mdir / "phase15-db-mapping" / "database_object_registry_v15.json"
    api_mapping = mdir / "phase14-api-mapping" / "screen_api_backend_mapping_v14.json"
    fdir = erp / "frontend" / "projects" / module / "public" / "formjson"
    all_forms = sorted(p.stem for p in fdir.glob("*.json")) if fdir.exists() else []
    forms = [f for f in all_forms if not forms or f in forms]

    from .usage import build_vocabulary, marketing_name
    vocab = build_vocabulary(registry)
    specs, table_cols, props, own = [], defaultdict(set), set(), set()
    for form in forms:
        try:
            if not json.loads(read_text(fdir / f"{form}.json", 5_000_000)).get("ObjectFields"):
                continue
        except Exception:
            continue
        reg = _registry_screen(registry, module, form)
        sid = reg["screenId"] if reg else None
        sname = marketing_name(reg or {"screenName": form, "module": module}, vocab)
        d = extract_screen_fields(erp, form, module, None, sid, sname, db_registry, api_mapping, scan_usage=False)
        specs.append((form, sid, sname))
        if d["table"]:
            table_cols[d["table"]] |= {r["database"]["column"] for r in d["fields"] if r["database"]["column"]}
        props |= {r["fieldName"] for r in d["fields"]}
        own |= set(d["sources"].values())
        say(f"pass 1: {form} ({len(d['fields'])} fields, entity {d['entity'] or '-'}, table {d['table'] or '-'})")
    say(f"usage scan: {len(table_cols)} tables, {sum(map(len, table_cols.values()))} columns, {len(props)} field names")
    usage = scan_erp_usage_multi(erp, dict(table_cols), props, own)

    dictionaries = []
    for form, sid, sname in specs:
        d = extract_screen_fields(erp, form, module, None, sid, sname, db_registry, api_mapping, usage=usage)
        if llm is not None:
            d = validate_explanations(enrich_fields(d, llm, out / f"{form}_explanations_cache.json"))
        base = out / f"{form}_fields_v20"
        base.with_suffix(".json").write_text(json.dumps(d, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        export_fields_xlsx(d, base.with_suffix(".xlsx"))
        dictionaries.append(d)
        say(f"pass 2: {d['screen']} done")
    write_module_summary(out, module)
    return dictionaries


# ------------------------------------------------------------------ LLM enrichment

ENRICH_SYSTEM = """You document ERP form fields for GoodBooks ERP. You receive extracted facts about fields of one screen.
Write ONLY what the facts support. For each field return:
- purpose: why the field exists (1-2 sentences)
- businessMeaning: what business information it stores
- businessUsage: where and how it is used, based on the usage/references facts
- transactionImpact: how it affects transactions or downstream processes, based on usage/dependency facts; "Not determinable from sources" if no usage facts
- example: one realistic example value consistent with type, length and allowed values, and what it means
- businessRules: business logic stated or implied by validations, dependencies and calculations
- confidence: high (facts state it directly), medium (reasonable reading of names and usage), low (mostly from the field name)
Do not invent modules, reports, values, rules or behaviour that are not in the facts. Plain business English.
Return JSON only: {"fields": [{"fieldName": "...", "purpose": "...", "businessMeaning": "...", "businessUsage": "...", "transactionImpact": "...", "example": "...", "businessRules": "...", "confidence": "high|medium|low"}]}"""


def _facts_for_llm(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "fieldName": r["fieldName"], "label": r["label"], "page": r["page"], "section": r["section"], "type": r["type"],
        "allowedValues": r["allowedValues"], "default": r["defaultValue"], "mandatory": r["mandatory"], "maxLength": r["maxLength"],
        "validations": [v["rule"] for v in r["validations"]], "dependencies": [f"{d['kind']} {', '.join(d['fields'])}: {d['detail'][:160]}" for d in r["dependencies"]][:8],
        "calculation": r["calculation"]["formula"] if r["calculation"] else None,
        "lookup": {k: r["lookup"].get(k) for k in ("picklist", "source", "displayColumns")} if r["lookup"] else None,
        "database": {k: r["database"].get(k) for k in ("table", "column", "foreignKey", "lookupDisplay")},
        "relatedMasters": r["relatedMasters"],
        "usedByModules": r["usage"]["modules"], "reports": r["usage"]["reports"][:5],
        "backendReferences": [x["fact"] for x in r["usage"]["backendReferences"][:4]],
        "otherScreens": r["usage"]["screens"][:8],
        "handlerCode": [h["code"][:300] for h in r["handlerLogic"][:2]],
    }


def enrich_fields(dictionary: dict[str, Any], llm: Any, cache_file: str | Path | None = None, batch_size: int = 8,
                  progress: Any = None) -> dict[str, Any]:
    """Add AI-derived explanations per field, bound to the extracted facts. Cached by fact hash."""
    from .llm import extract_json
    cache: dict[str, Any] = {}
    cpath = Path(cache_file) if cache_file else None
    if cpath and cpath.exists():
        cache = json.loads(cpath.read_text(encoding="utf-8"))
    # Keyed by fact hash, with a name key as fallback so presentation-only changes
    # (tab names, notes) do not trigger paid regeneration.
    name_key = lambda r: f"name:{r['screenId']}:{r.get('fieldKey') or r['fieldName']}"
    for r in dictionary["fields"]:
        if cache.get(r["factHash"]) is None and cache.get(name_key(r)) is not None:
            cache[r["factHash"]] = cache[name_key(r)]
    todo = [r for r in dictionary["fields"] if cache.get(r["factHash"]) is None]
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]

    def ask(batch: list[dict[str, Any]]) -> dict[str, Any]:
        prompt = ENRICH_SYSTEM + f"\n\nSCREEN: {dictionary['screen']} (module {dictionary['module']}, table {dictionary['table']})\nFACTS:\n" + \
            json.dumps([_facts_for_llm(r) for r in batch], ensure_ascii=False, default=str)
        data = extract_json(llm.generate(prompt))
        return {x.get("fieldName"): x for x in data.get("fields", []) if isinstance(x, dict)}

    # Batches are independent: several go to the model at once (PIE_AI_WORKERS, default 4);
    # results and the cache are written here, one batch at a time.
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    workers = max(1, min(int(os.getenv("PIE_AI_WORKERS") or 4), len(batches) or 1))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(ask, b): n for n, b in enumerate(batches)}
        for fut in as_completed(futures):
            n, batch = futures[fut], batches[futures[fut]]
            try:
                got = fut.result()
            except Exception as exc:
                got = {}
                if progress:
                    progress(f"batch {n + 1} failed: {type(exc).__name__}")
            for r in batch:
                x = got.get(r["fieldName"])
                if x:
                    cache[r["factHash"]] = cache[name_key(r)] = {k: str(x.get(k, "")).strip() for k in (
                        "purpose", "businessMeaning", "businessUsage", "transactionImpact", "example", "businessRules", "confidence")}
            done += len(batch)
            if cpath:
                cpath.parent.mkdir(parents=True, exist_ok=True)
                cpath.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
            if progress:
                progress(f"enriched {done}/{len(todo)}")
    for r in dictionary["fields"]:
        e = cache.get(r["factHash"])
        r["explanation"] = ({**e, "basis": "AI-derived from the extracted evidence; review before external use"} if e else None)
    dictionary["statistics"]["explained"] = sum(bool(r["explanation"]) for r in dictionary["fields"])
    return dictionary


ERP_MODULE_WORDS = {
    "sales": "Sales", "purchase": "Purchase", "procurement": "Procurement", "production": "Production", "manufacturing": "Manufacturing",
    "costing": "Costing", "quality": "Quality", "qms": "QMS", "finance": "Finance", "accounts": "Accounts", "accounting": "Accounting",
    "payroll": "Payroll", "hr": "HR", "maintenance": "Maintenance", "logistics": "Logistics", "crm": "CRM", "planning": "Planning",
    "mrp": "MRP", "warehouse": "Warehouse", "store": "Store", "tax": "Tax", "gst": "GST", "service": "Service",
}
# Where a module word may legitimately come from in the facts.
MODULE_EVIDENCE_ALIASES = {"sales": {"sales", "crm"}, "purchase": {"purchase", "mm"}, "procurement": {"purchase", "mm"},
                           # MM is materials/inventory, not production.
                           "production": {"production", "workinstruction"}, "manufacturing": {"production", "workinstruction"},
                           "quality": {"qms", "quality"}, "qms": {"qms"}, "accounts": {"accounts", "finance"},
                           "accounting": {"accounts", "finance"}, "finance": {"finance", "accounts"}, "payroll": {"payroll", "pay"},
                           "mrp": {"mrp", "planning"}, "planning": {"planning", "mrp"}}


def validate_explanations(dictionary: dict[str, Any]) -> dict[str, Any]:
    """Flag AI explanations that name modules or usage the extracted facts do not show.

    The model is told to use only the facts; this check makes that verifiable. Mentions
    of a module absent from the field's usage evidence and page are listed, confidence
    is capped, and a review note is added.
    """
    flagged = 0
    for r in dictionary["fields"]:
        e = r.get("explanation")
        if not e:
            continue
        # Usage claims are checked against usage evidence only: the Item Master tab
        # called "Sales" is not evidence that the Sales module uses the field.
        facts = re.sub(r"[^a-z0-9]+", " ", " ".join([" ".join(r["usage"]["modules"]), " ".join(r["usage"]["screens"]),
                                                    " ".join(r["usage"]["reports"]), " ".join(r["relatedMasters"])]).lower())
        text = " ".join(e.get(k, "") for k in ("businessUsage", "transactionImpact")).lower()
        unverified = []
        for word, label in ERP_MODULE_WORDS.items():
            if re.search(rf"\b{word}\b", text):
                allowed = MODULE_EVIDENCE_ALIASES.get(word, {word})
                if not any(re.search(rf"\b{a}\b", facts) for a in allowed):
                    unverified.append(label)
        has_usage = bool(r["usage"]["modules"] or r["usage"]["reports"] or r["usage"]["backendReferences"])
        notes = []
        if unverified:
            notes.append(f"Mentions {', '.join(sorted(set(unverified)))}, which is not in the extracted usage evidence")
        if not has_usage and e.get("transactionImpact", "").lower().strip(". ") != "not determinable from sources":
            notes.append("Transaction impact is inferred: no ERP-wide usage was found for this field")
        e["unverifiedMentions"] = sorted(set(unverified))
        e["reviewNote"] = "; ".join(notes)
        if notes:
            flagged += 1
            e["confidence"] = "low" if unverified else ("medium" if e.get("confidence") == "high" else e.get("confidence"))
    dictionary["statistics"]["explanationsFlagged"] = flagged
    return dictionary


# ------------------------------------------------------------------ text views

def field_summary_text(r: dict[str, Any], max_len: int = 2400) -> str:
    """One readable paragraph per field, used for Qdrant content and chat context."""
    parts = [f"Field '{r['label']}' ({r['fieldName']}) on {r['screen']}"
             + (f", page {r['page']}" if r["page"] else "") + (f", section {r['section']}" if r["section"] else "") + "."]
    t = r["type"]
    parts.append(f"Type: {t['ui'] or t['form']}" + (f"; backend {t['backend']}" if t["backend"] else "") + (f"; database {t['database']}" if t["database"] else "") + ".")
    m = r["mandatory"]
    parts.append("Mandatory: " + ("yes" if m["required"] else "no") + (f" (only when {m['conditional']['field']} is {m['conditional']['values']})" if m["conditional"] else "")
                 + ("; validated by the backend" if m["backendValidated"] else "") + ".")
    if r["defaultValue"] not in (None, ""):
        parts.append(f"Default: {r['defaultValue']}.")
    if r["allowedValues"]:
        parts.append("Allowed values: " + ", ".join(f"{v['value']} = {v['label']}" for v in r["allowedValues"]) + ".")
    if r["lookup"]:
        parts.append(f"Lookup from '{r['lookup'].get('source') or r['lookup']['picklist']}'" + (f" showing {r['lookup']['displayColumns']}" if r['lookup'].get('displayColumns') else "") + ".")
    db = r["database"]
    if db["column"]:
        parts.append(f"Stored in {db['table']}.{db['column']}" + (f" (references {db['foreignKey']['table']}.{db['foreignKey']['column']})" if db["foreignKey"] else "") + ".")
    elif db["lookupDisplay"]:
        parts.append(f"Displays {db['lookupDisplay']['table']}.{db['lookupDisplay']['column']} (not stored on {r['screen']} itself).")
    elif db.get("childTable"):
        parts.append(f"Grid rows are saved to {db['childTable']['table']}.")
    elif db.get("storedVia"):
        sv = db["storedVia"]
        parts.append(f"Shown for display; the value is kept in {sv['field']}"
                     + (f", stored in {sv['table']}.{sv['column']}" if sv.get("column") else (f" (grid rows in {sv['table']})" if sv.get("table") else ""))
                     + (f" (references {sv['foreignKey']['table']}.{sv['foreignKey']['column']})" if sv.get("foreignKey") else "") + ".")
    else:
        parts.append(f"Storage: {db.get('storage', 'unknown')}.")
    if r["frontend"].get("grid"):
        # Grid definitions can have columns without label or name; skip those.
        cols = [str(g.get("label") or g.get("column")) for g in r["frontend"]["grid"] if g.get("label") or g.get("column")]
        if cols:
            parts.append("Grid columns: " + ", ".join(cols) + ".")
    if r["validations"]:
        parts.append("Validations: " + "; ".join(v["rule"] for v in r["validations"]) + ".")
    if r["messages"]:
        parts.append("Messages: " + "; ".join(f"\"{x['message']}\"" for x in r["messages"]) + ".")
    if r["calculation"]:
        parts.append(f"Calculated: {r['calculation']['formula']}.")
    if r["dependencies"]:
        parts.append("Dependencies: " + "; ".join(f"{d['kind']} {', '.join(d['fields']) or d['detail'][:80]}" for d in r["dependencies"][:6]) + ".")
    if r["relatedMasters"]:
        parts.append("Related masters: " + ", ".join(r["relatedMasters"]) + ".")
    if r["usage"]["modules"]:
        parts.append("Used by modules: " + ", ".join(f"{k} ({v})" for k, v in r["usage"]["modules"].items()) + ".")
    if r["usage"]["reports"]:
        parts.append(f"Appears in {len(r['usage']['reports'])} report source(s).")
    if r.get("duplicateDefinition"):
        parts.append(f"Source conflict: {r['duplicateDefinition']}.")
    if r.get("labelNote"):
        parts.append(f"Label note: {r['labelNote']}.")
    e = r.get("explanation")
    if e:
        impact = e["transactionImpact"] if not e.get("unverifiedMentions") else "not verified"
        parts.append(f"Purpose (AI-derived): {e['purpose']} Business meaning: {e['businessMeaning']} Transaction impact: {impact}"
                     + (f" [Review: {e['reviewNote']}]" if e.get("reviewNote") else ""))
    return " ".join(parts)[:max_len]


# ------------------------------------------------------------------ Excel export

EXCEL_COLUMNS = [
    ("#", lambda r: r["order"]), ("Field name", lambda r: r["fieldName"]), ("Label", lambda r: r["label"]),
    ("Page", lambda r: r["page"]), ("Section", lambda r: r["section"]),
    ("Purpose (AI-derived)", lambda r: (r.get("explanation") or {}).get("purpose", "")),
    ("Business meaning (AI-derived)", lambda r: (r.get("explanation") or {}).get("businessMeaning", "")),
    ("Business usage (AI-derived)", lambda r: (r.get("explanation") or {}).get("businessUsage", "")),
    ("UI type", lambda r: r["type"]["ui"]), ("Form type", lambda r: r["type"]["form"]), ("Backend type", lambda r: r["type"]["backend"]),
    ("DB type", lambda r: r["type"]["database"]),
    ("Allowed values", lambda r: "; ".join(f"{v['value']}={v['label']}" for v in r["allowedValues"])),
    ("Default", lambda r: "" if r["defaultValue"] is None else str(r["defaultValue"])),
    ("Mandatory", lambda r: ("Yes" if r["mandatory"]["required"] else "No") + (f" (when {r['mandatory']['conditional']['field']} in {r['mandatory']['conditional']['values']})" if r["mandatory"]["conditional"] else "")),
    ("Validations", lambda r: "; ".join(f"[{v['layer']}] {v['rule']}" for v in r["validations"])),
    ("Messages", lambda r: "; ".join(x["message"] for x in r["messages"])),
    ("Dependencies", lambda r: "; ".join(f"{d['kind']} {', '.join(d['fields'])} ({d['detail'][:120]})" for d in r["dependencies"])),
    ("Calculation", lambda r: r["calculation"]["formula"] if r["calculation"] else ""),
    ("Data source / lookup", lambda r: (f"{r['lookup'].get('source') or r['lookup'].get('picklist')}" if r["lookup"] else "")),
    ("Storage", lambda r: r["database"].get("storage", "")),
    ("DB table", lambda r: r["database"]["table"] or ((r["database"]["lookupDisplay"] or {}).get("table", "")) or ((r["database"].get("childTable") or {}).get("table", ""))
                           or ((r["database"].get("storedVia") or {}).get("table", ""))),
    ("DB column", lambda r: r["database"]["column"] or ((r["database"]["lookupDisplay"] or {}).get("column", ""))
                            or (f"{r['database']['storedVia']['column']} (via {r['database']['storedVia']['field']})" if (r["database"].get("storedVia") or {}).get("column") else "")),
    ("Foreign key", lambda r: f"{r['database']['foreignKey']['table']}.{r['database']['foreignKey']['column']}" if r["database"]["foreignKey"] else ""),
    ("Backend (DTO / queries)", lambda r: "; ".join(x for x in [r["backend"]["dto"]] + r["backend"]["queries"] if x)),
    ("Frontend (control / events)", lambda r: "; ".join(x for x in [r["frontend"]["control"]] + [f"{e['event']}->{e['handler']}" for e in r["frontend"]["events"]] if x)),
    ("Related masters", lambda r: ", ".join(r["relatedMasters"])),
    ("Used by modules", lambda r: ", ".join(f"{k} ({v})" for k, v in r["usage"]["modules"].items())),
    ("Transaction impact (AI-derived)", lambda r: (r.get("explanation") or {}).get("transactionImpact", "")),
    ("Example (AI-derived)", lambda r: (r.get("explanation") or {}).get("example", "")),
    ("Business rules (AI-derived)", lambda r: (r.get("explanation") or {}).get("businessRules", "")),
    ("Configuration dependency", lambda r: "; ".join(r["configuration"])),
    ("Reports", lambda r: "; ".join(r["usage"]["reports"][:6])),
    ("Related fields", lambda r: ", ".join(r["relatedFields"])),
    ("AI confidence", lambda r: (r.get("explanation") or {}).get("confidence", "")),
    ("AI review note", lambda r: (r.get("explanation") or {}).get("reviewNote", "")),
    ("Label note", lambda r: r.get("labelNote", "")),
    ("Evidence", lambda r: "; ".join(f"{e['source']}{':' + str(e['line']) if e['line'] else ''} {e['fact']}" for e in r["evidence"][:8])),
]


def export_fields_xlsx(dictionary: dict[str, Any], path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "Fields"
    ws.append([c for c, _ in EXCEL_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2457D6")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for r in dictionary["fields"]:
        ws.append([fn(r) for _, fn in EXCEL_COLUMNS])
    widths = {"#": 5, "Field name": 28, "Label": 24, "Evidence": 60}
    for i, (c, _) in enumerate(EXCEL_COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(c, 34 if "AI" in c else 22)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    info = wb.create_sheet("About")
    about = [["Screen", dictionary["screen"]], ["Table", dictionary["table"]], ["Generated", dictionary["generatedAt"]],
             ["Fields", dictionary["statistics"]["fields"]],
             ["Note", "Columns marked AI-derived are written by an LLM from the extracted evidence only; review before "
                      "external use. All other columns come directly from the ERP sources."]]
    for row in about + [[k, v] for k, v in dictionary["sources"].items()]:
        info.append(row)
    info.column_dimensions["A"].width = 18
    info.column_dimensions["B"].width = 110
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
