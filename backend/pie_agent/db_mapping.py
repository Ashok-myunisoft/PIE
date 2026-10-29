from __future__ import annotations
import json, re, hashlib
from pathlib import Path
from typing import Any
from .erp_source import iter_files, read_text, write_json
from .db_discovery import scan_sqlserver

SQL_TABLE_RE = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE\s+TABLE)\s+(?:\[?([A-Za-z_][\w$]*)\]?\.)?\[?([A-Za-z_][\w$]*)\]?", re.I)
SQL_EXEC_RE = re.compile(r"\b(?:EXEC(?:UTE)?\s+)(?:\[?([A-Za-z_][\w$]*)\]?\.)?\[?([A-Za-z_][\w$]*)\]?", re.I)
CREATE_TABLE_RE = re.compile(r"\bCREATE\s+TABLE\s+(?:\[?([A-Za-z_][\w$]*)\]?\.)?\[?([A-Za-z_][\w$]*)\]?", re.I)
CREATE_VIEW_RE = re.compile(r"\bCREATE\s+(?:OR\s+ALTER\s+)?VIEW\s+(?:\[?([A-Za-z_][\w$]*)\]?\.)?\[?([A-Za-z_][\w$]*)\]?", re.I)
CREATE_PROC_RE = re.compile(r"\bCREATE\s+(?:OR\s+ALTER\s+)?PROC(?:EDURE)?\s+(?:\[?([A-Za-z_][\w$]*)\]?\.)?\[?([A-Za-z_][\w$]*)\]?", re.I)
DBSET_RE = re.compile(r"\bDbSet\s*<\s*([A-Za-z_][\w.]*)\s*>\s+(\w+)", re.I)
EF_ENTITY_RE = re.compile(r"\b(?:Set|Find|Add|Update|Remove)\s*<\s*([A-Za-z_][\w.]*)\s*>", re.I)
TABLE_ATTR_RE = re.compile(r"\[Table\s*\(\s*[\"']([^\"']+)[\"'](?:\s*,\s*Schema\s*=\s*[\"']([^\"']+)[\"'])?", re.I)
SQL_STRING_RE = re.compile(r"(?:@\"(?:\"\"|[^\"])+\"|\$?\"(?:\\.|[^\"])*\"|'(?:''|[^'])*')", re.S)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _norm(s)))

def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except Exception:
        return str(p).replace("\\", "/")

def _canonical(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())

def _score_level(score: float) -> str:
    return "HIGH" if score >= .85 else ("MEDIUM" if score >= .60 else "LOW")

def _object_key(schema: str, name: str) -> tuple[str, str]:
    return ((schema or "dbo").lower(), (name or "").lower())


def parse_sql_schema_sources(root: Path) -> dict[str, Any]:
    """Parse SQL migration/DDL files as offline schema evidence.

    This is deliberately a fallback/evidence source. A live SQL Server metadata scan,
    when available, remains authoritative for the current database.
    """
    objects: dict[tuple[str, str], dict[str, Any]] = {}
    for p in iter_files(root):
        if p.suffix.lower() != ".sql":
            continue
        rel = _rel(root, p)
        txt = read_text(p, 500_000)
        for m in CREATE_TABLE_RE.finditer(txt):
            schema, name = m.group(1) or "dbo", m.group(2)
            objects.setdefault(_object_key(schema, name), {"schema": schema, "name": name, "type": "TABLE", "sourceFiles": [], "evidence": []})
            objects[_object_key(schema, name)]["sourceFiles"].append(rel)
            objects[_object_key(schema, name)]["evidence"].append({"source": rel, "fact": "CREATE TABLE"})
        for rx, typ in ((CREATE_VIEW_RE, "VIEW"), (CREATE_PROC_RE, "PROCEDURE")):
            for m in rx.finditer(txt):
                schema, name = m.group(1) or "dbo", m.group(2)
                objects.setdefault(_object_key(schema, name), {"schema": schema, "name": name, "type": typ, "sourceFiles": [], "evidence": []})
                objects[_object_key(schema, name)]["sourceFiles"].append(rel)
                objects[_object_key(schema, name)]["evidence"].append({"source": rel, "fact": f"CREATE {typ}"})
    return {"objects": list(objects.values()), "fileCount": len({x for o in objects.values() for x in o.get("sourceFiles", [])})}


def _is_db_relevant_path(rel: str) -> bool:
    low=rel.lower().replace('\\','/')
    if low.endswith('.sql'):
        return True
    if not low.endswith('.cs'):
        return False
    # Focus on application layers where persistence references normally occur. This avoids
    # parsing every generated/UI/helper C# file in very large ERP repositories.
    hints=('endpoint','endpoints','bll','service','services','repository','repositories','repo/','dal/','data/','db/','database/','entity','entities','model','models')
    return any(h in low for h in hints)


CS_CLASS_RE = re.compile(r"\b(?:class|interface)\s+(\w+)\s*(?:<[^>{]*>)?\s*(?::\s*([^{]+))?\{")
CS_FIELD_TYPE_RE = re.compile(r"\b(?:private|protected|public|internal)\s+(?:readonly\s+)?([A-Za-z_][\w.]*)(?:<[^>;]*>)?\??\s+_\w+\s*[;=]")
CS_STATIC_REF_RE = re.compile(r"\b([A-Z]\w*(?:QB|Query|Queries|Sql))\s*\.\s*[A-Z_]\w*")
CHAIN_LAYER_SUFFIXES = ("bll", "dal", "service", "repository", "repo")
SQL_OPERATIONS = (("INSERT", "WRITE"), ("INTO", "WRITE"), ("UPDATE", "WRITE"), ("DELETE", "WRITE"), ("MERGE", "WRITE"),
                  ("TRUNCATE", "WRITE"), ("FROM", "READ"), ("JOIN", "READ"), ("EXEC", "EXECUTE"))


def sql_operation(evidence: str) -> str:
    """'INTO MITEMCATEGORY' -> 'WRITE', 'JOIN MUSER' -> 'READ'. Empty for non-SQL evidence."""
    head = str(evidence or "").lstrip().split(None, 1)
    word = head[0].upper() if head else ""
    return next((op for kw, op in SQL_OPERATIONS if word == kw), "")


def _index_classes(index: dict[str, Any], rel: str, txt: str) -> None:
    """Record classes/interfaces, injected field types and query-builder references."""
    info = index["files"].setdefault(rel.lower(), {"file": rel, "deps": set(), "refs": set()})
    for m in CS_CLASS_RE.finditer(txt):
        name = m.group(1)
        index["classes"].setdefault(name.lower(), rel)
        for base in (m.group(2) or "").split(","):
            base = base.strip().split("<")[0].split(".")[-1]
            if base.startswith("I") and base[1:2].isupper():
                index["interfaces"].setdefault(base.lower(), []).append(rel)
    info["deps"].update(t.split(".")[-1] for t in CS_FIELD_TYPE_RE.findall(txt))
    info["refs"].update(CS_STATIC_REF_RE.findall(txt))


def resolve_persistence_chain(start_files: list[str], class_index: dict[str, Any], max_depth: int = 4) -> list[dict[str, Any]]:
    """Follow endpoint -> BLL -> DAL -> query-builder links through injected field types
    and static query-class references. Returns each reached file with its hop path."""
    if not class_index:
        return []
    reached: dict[str, dict[str, Any]] = {}
    frontier = [(f, [f]) for f in start_files if f]
    for _ in range(max_depth):
        nxt = []
        for f, path in frontier:
            info = class_index["files"].get(str(f).lower())
            if not info:
                continue
            targets = []
            for dep in info["deps"]:
                d = dep.lower()
                # Only application layers; shared infrastructure (IQueryExecutor,
                # AutoNumber, IValidation...) would attach unrelated tables.
                if not d.endswith(CHAIN_LAYER_SUFFIXES):
                    continue
                targets.extend(class_index["interfaces"].get(d, []))
                if d in class_index["classes"]:
                    targets.append(class_index["classes"][d])
                # IFooDAL -> FooDAL when the implementation was not indexed via its bases.
                if d.startswith("i") and d[1:] in class_index["classes"]:
                    targets.append(class_index["classes"][d[1:]])
            for ref in info["refs"]:
                if ref.lower() in class_index["classes"]:
                    targets.append(class_index["classes"][ref.lower()])
            for t in targets:
                if t.lower() in reached or t in path:
                    continue
                reached[t.lower()] = {"file": t, "via": path + [t]}
                nxt.append((t, path + [t]))
        frontier = nxt
        if not frontier:
            break
    return list(reached.values())


def discover_backend_db_references(root: Path, class_index: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Extract DB object/entity references from persistence-relevant C#/SQL source.

    The scanner is intentionally static: it never executes application SQL. For large ERP
    repositories it narrows C# scanning to endpoint/service/BLL/repository/data/entity/model
    paths, while still scanning SQL DDL/migration files. When `class_index` is given it is
    filled with class/dependency information for `resolve_persistence_chain`.
    """
    rows=[]
    for p in iter_files(root):
        if p.suffix.lower() not in {'.cs','.sql','.config','.xml','.json'}:
            continue
        rel=_rel(root,p)
        if not _is_db_relevant_path(rel):
            continue
        txt=read_text(p,260_000)
        if class_index is not None and p.suffix.lower()=='.cs':
            _index_classes(class_index, rel, txt)
        refs=[]; seen={}
        def add(kind,schema,name,evidence):
            if not name: return
            key=(kind.lower(),(schema or 'dbo').lower(),name.lower())
            op=sql_operation(evidence)
            if key in seen:
                # Keep every operation: the first hit is usually the SELECT, and
                # the INSERT/UPDATE later in the file is what shows ownership.
                if op and op not in seen[key]['operations']: seen[key]['operations'].append(op)
                return
            ref={'kind':kind,'schema':schema or 'dbo','name':name,'evidence':evidence,'operations':[op] if op else []}
            seen[key]=ref; refs.append(ref)
        for m in SQL_TABLE_RE.finditer(txt): add('TABLE_OR_VIEW',m.group(1) or 'dbo',m.group(2),m.group(0))
        for m in SQL_EXEC_RE.finditer(txt): add('PROCEDURE',m.group(1) or 'dbo',m.group(2),m.group(0))
        for m in TABLE_ATTR_RE.finditer(txt): add('ENTITY_TABLE',m.group(2) or 'dbo',m.group(1),'Table attribute')
        for m in DBSET_RE.finditer(txt): add('ENTITY','dbo',m.group(1).split('.')[-1],f'DbSet<{m.group(1)}> {m.group(2)}')
        for m in EF_ENTITY_RE.finditer(txt): add('ENTITY','dbo',m.group(1).split('.')[-1],m.group(0))
        # Embedded SQL is common in this ERP. Inspect quoted strings only when the file has
        # a likely SQL/persistence marker to keep the pass fast.
        if p.suffix.lower()=='.cs' and re.search(r'ExecuteSql|FromSql|SqlCommand|StoredProcedure|SELECT\s+|INSERT\s+INTO|UPDATE\s+|DELETE\s+FROM',txt,re.I):
            for sm in SQL_STRING_RE.finditer(txt):
                frag=sm.group(0)
                for tm in SQL_TABLE_RE.finditer(frag): add('EMBEDDED_SQL',tm.group(1) or 'dbo',tm.group(2),tm.group(0))
                for pm in SQL_EXEC_RE.finditer(frag): add('EMBEDDED_SQL_PROCEDURE',pm.group(1) or 'dbo',pm.group(2),pm.group(0))
        if refs:
            rows.append({'file':rel,'references':refs,'sha256':hashlib.sha256(txt.encode('utf-8','ignore')).hexdigest()})
    return rows

def _load_db_objects(database_url: str | None, backend_root: Path, snapshot: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    live = scan_sqlserver(database_url)
    if live.get("status") == "CONNECTED":
        return live.get("objects", []), {"source": "live_sqlserver", "status": live.get("status"), "database": live.get("database"), "server": live.get("server"), "tableCount": live.get("tableCount", 0), "viewCount": live.get("viewCount", 0), "routineCount": live.get("routineCount", 0)}
    if snapshot and Path(snapshot).exists():
        # A registry saved from an earlier LIVE scan keeps column types, nullability and
        # foreign keys; the SQL-script fallback below has names only.
        objs = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        if any(o.get("columns") for o in objs):
            from datetime import datetime
            taken = datetime.fromtimestamp(Path(snapshot).stat().st_mtime).isoformat(timespec="seconds")
            return objs, {"source": "live_sqlserver_snapshot", "status": live.get("status"), "snapshot": str(snapshot),
                          "snapshotTaken": taken, "liveError": live.get("error", ""), "tableCount": len(objs)}
    fallback = parse_sql_schema_sources(backend_root)
    return fallback.get("objects", []), {"source": "sql_source_fallback", "status": live.get("status", "NOT_CONFIGURED"), "liveError": live.get("error", ""), "ddlFileCount": fallback.get("fileCount", 0)}


def _build_object_indexes(objects: list[dict[str, Any]]):
    by_exact = {}
    by_name = {}
    by_canon = {}
    for o in objects:
        schema, name = o.get("schema", "dbo"), o.get("name", "")
        by_exact[_object_key(schema, name)] = o
        by_name.setdefault(name.lower(), []).append(o)
        by_canon.setdefault(_canonical(name), []).append(o)
    return by_exact, by_name, by_canon


def _match_ref(ref: dict[str, Any], by_exact, by_name, by_canon) -> list[tuple[dict[str, Any], float, str]]:
    schema, name = ref.get("schema", "dbo"), ref.get("name", "")
    exact = by_exact.get(_object_key(schema, name))
    if exact:
        return [(exact, 1.0, "exact schema/object name match")]
    exact_name = by_name.get(name.lower(), [])
    if exact_name:
        return [(o, .92, "exact object name match; schema unresolved") for o in exact_name[:5]]
    canon = _canonical(name)
    cands = by_canon.get(canon, [])
    if cands:
        return [(o, .85, "normalized object name match") for o in cands[:5]]
    return []


def map_backend_files_to_database(backend_refs: list[dict[str, Any]], db_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_exact, by_name, by_canon = _build_object_indexes(db_objects)
    out = []
    for row in backend_refs:
        matches = {}
        for ref in row.get("references", []):
            for obj, score, reason in _match_ref(ref, by_exact, by_name, by_canon):
                key = _object_key(obj.get("schema", "dbo"), obj.get("name", ""))
                existing = matches.get(key)
                if existing is None or score > existing["confidence"]:
                    matches[key] = {"schema": obj.get("schema", "dbo"), "name": obj.get("name"), "type": obj.get("type", "UNKNOWN"), "confidence": score, "confidenceLevel": _score_level(score), "reason": reason, "backendEvidence": [], "databaseEvidence": obj.get("evidence", [])}
                matches[key]["backendEvidence"].append({"kind": ref.get("kind"), "evidence": ref.get("evidence"), "operations": ref.get("operations", [])})
                matches[key]["operations"] = sorted(set(matches[key].get("operations", [])) | set(ref.get("operations", [])))
        out.append({"file": row.get("file"), "sha256": row.get("sha256"), "databaseObjects": sorted(matches.values(), key=lambda x: (-x["confidence"], x["name"] or ""))})
    return out


def _tokens_overlap(a: str, b: str) -> float:
    aa, bb = _tokens(a), _tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, min(len(aa), len(bb)))


def _build_object_token_index(objects: list[dict[str, Any]]):
    index={}
    for obj in objects:
        toks=_tokens(obj.get('name',''))
        for t in toks:
            index.setdefault(t,[]).append(obj)
    return index


def map_api_mappings_to_database(api_mapping_path: str, backend_root: Path, db_objects: list[dict[str, Any]], backend_file_map: list[dict[str, Any]], class_index: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(Path(api_mapping_path).read_text(encoding="utf-8"))
    mappings = data if isinstance(data, list) else data.get("mappings", data.get("screenMappings", []))
    file_index = {str(x.get("file", "")).lower().replace("\\", "/"): x for x in backend_file_map}
    by_exact, by_name, by_canon = _build_object_indexes(db_objects)
    stats = {"screens": len(mappings), "screensWithDatabaseObjects": 0, "apiMappings": 0, "apiMappingsWithDatabase": 0, "apiMappingsViaPersistenceChain": 0, "exactDatabaseMatches": 0, "candidateDatabaseMatches": 0, "unmappedApiMappings": 0, "highConfidence": 0}
    output = []
    object_token_index = _build_object_token_index(db_objects)

    for screen in mappings:
        api_rows = []
        for api in screen.get("apiMappings", []):
            stats["apiMappings"] += 1
            candidate_objects: dict[tuple[str, str], dict[str, Any]] = {}
            evidence_files = [api.get("endpointFile", "")]
            for impl in api.get("serviceImplementations", []):
                if impl.get("file"):
                    evidence_files.append(impl["file"])
            # SQL normally lives two or three hops below the endpoint
            # (endpoint -> BLL -> DAL -> query builder), so follow the chain.
            chain = resolve_persistence_chain(evidence_files, class_index or {})
            via = {c["file"].lower(): c["via"] for c in chain}
            evidence_files = evidence_files + [c["file"] for c in chain]
            chain_hit = False
            for f in evidence_files:
                row = file_index.get(str(f).lower().replace("\\", "/"))
                if row:
                    for obj in row.get("databaseObjects", []):
                        key = _object_key(obj.get("schema", "dbo"), obj.get("name", ""))
                        prev = candidate_objects.get(key, {})
                        entry = {**obj, "evidenceFiles": sorted(set(prev.get("evidenceFiles", []) + [f])),
                                 "operations": sorted(set(prev.get("operations", [])) | set(obj.get("operations", []))),
                                 # Which files write the table: ownership depends on the file, not just the chain.
                                 "writeFiles": sorted(set(prev.get("writeFiles", [])) | ({f} if "WRITE" in obj.get("operations", []) else set())),
                                 "backendEvidence": prev.get("backendEvidence", []) + obj.get("backendEvidence", [])}
                        if str(f).lower() in via:
                            chain_hit = True
                            entry["evidenceChain"] = prev.get("evidenceChain") or via[str(f).lower()]
                            entry["reason"] = f"{obj.get('reason', '')}; reached via persistence chain".strip("; ")
                        candidate_objects[key] = entry
            if chain_hit:
                stats["apiMappingsViaPersistenceChain"] += 1

            # If source-file evidence did not identify an object, use endpoint/service/entity
            # naming as candidate evidence, but never present it as an exact match.
            search_text = " ".join([api.get("endpointClass", ""), api.get("route", ""), api.get("endpointFile", "")] + [str(x.get("method", "")) for x in api.get("serviceCalls", [])])
            if not candidate_objects:
                # Indexed candidate generation prevents O(APIs * all DB objects) scans.
                obj_index = object_token_index
                counts={}
                query_tokens=_tokens(search_text)
                for tok in query_tokens:
                    for obj in obj_index.get(tok,[]):
                        key=_object_key(obj.get('schema','dbo'),obj.get('name',''))
                        counts.setdefault(key,{'obj':obj,'overlap':0})['overlap'] += 1
                scored=[]
                for item in counts.values():
                    obj=item['obj']; ov=item['overlap']/max(1,min(len(query_tokens),len(_tokens(obj.get('name','')))))
                    if ov >= .60: scored.append((ov,obj))
                for ov,obj in sorted(scored,reverse=True,key=lambda x:(x[0],x[1].get('name','')))[:8]:
                    candidate_objects[_object_key(obj.get('schema','dbo'),obj.get('name',''))] = {
                        'schema':obj.get('schema','dbo'),'name':obj.get('name'),'type':obj.get('type','UNKNOWN'),
                        'confidence':round(min(.74,.50+.24*ov),3),'confidenceLevel':_score_level(min(.74,.50+.24*ov)),
                        'reason':f'backend/API name overlap={ov:.2f}','backendEvidence':[],
                        'databaseEvidence':obj.get('evidence',[]),'evidenceFiles':[]
                    }

            db_rows = sorted(candidate_objects.values(), key=lambda x: (-x.get("confidence", 0), x.get("name", "")))[:20]
            if db_rows:
                stats["apiMappingsWithDatabase"] += 1
                stats["highConfidence"] += sum(1 for x in db_rows if x.get("confidence", 0) >= .85)
                stats["exactDatabaseMatches"] += sum(1 for x in db_rows if x.get("confidence", 0) >= 1.0)
                stats["candidateDatabaseMatches"] += sum(1 for x in db_rows if x.get("confidence", 0) < .85)
            else:
                stats["unmappedApiMappings"] += 1
            api_rows.append({**api, "databaseMappings": db_rows, "databaseMappingStatus": "MAPPED" if db_rows else "UNMAPPED"})
        if api_rows:
            stats["screensWithDatabaseObjects"] += 1
        output.append({"screenId": screen.get("screenId"), "screenName": screen.get("screenName"), "module": screen.get("module"), "feature": screen.get("feature"), "status": screen.get("status"), "apiMappings": api_rows, "databaseMappingStatus": "MAPPED" if any(x.get("databaseMappings") for x in api_rows) else "UNMAPPED"})
    return output, stats


def run_db_mapping(api_mapping_path: str, backend_root: str, output_dir: str, database_url: str | None = None, db_snapshot: str | None = None) -> dict[str, Any]:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    be = Path(backend_root)
    db_objects, db_meta = _load_db_objects(database_url, be, db_snapshot)
    class_index: dict[str, Any] = {"classes": {}, "interfaces": {}, "files": {}}
    backend_refs = discover_backend_db_references(be, class_index) if be.exists() else []
    backend_file_map = map_backend_files_to_database(backend_refs, db_objects)
    screen_mappings, stats = map_api_mappings_to_database(api_mapping_path, be, db_objects, backend_file_map, class_index)

    write_json(out / "database_object_registry_v15.json", db_objects)
    write_json(out / "backend_database_reference_registry_v15.json", backend_file_map)
    write_json(out / "screen_api_database_mapping_v15.json", screen_mappings)
    report = {
        "phase": "0.15.0",
        "status": "COMPLETED",
        "apiMappingInput": str(Path(api_mapping_path)),
        "backendRoot": str(be.resolve()),
        "database": db_meta,
        "databaseObjectCount": len(db_objects),
        "backendFilesWithDbReferences": len(backend_refs),
        "stats": stats,
        "evidencePolicy": "Live SQL Server metadata is authoritative when connected. SQL source DDL is an offline fallback. Exact code object references are high-confidence; name-based relationships remain candidates.",
    }
    write_json(out / "db_mapping_report_v15.json", report)
    return report


def run_db_mapping_modules(modules: list[str], modules_root: str, backend_root: str, database_url: str | None = None,
                           db_snapshot: str | None = None, progress=print) -> list[dict[str, Any]]:
    """Phase 15 for several modules: the database is read and the backend scanned once, then each
    module's Phase 14 mapping is mapped exactly as run_db_mapping would, into <modules_root>/<module>/phase15-db-mapping."""
    be = Path(backend_root)
    db_objects, db_meta = _load_db_objects(database_url, be, db_snapshot)
    progress(f"database objects: {len(db_objects)} ({db_meta.get('source')})")
    class_index: dict[str, Any] = {"classes": {}, "interfaces": {}, "files": {}}
    backend_refs = discover_backend_db_references(be, class_index) if be.exists() else []
    backend_file_map = map_backend_files_to_database(backend_refs, db_objects)
    progress(f"backend files with database references: {len(backend_refs)}")
    reports = []
    for m in modules:
        api_path = Path(modules_root) / m / "phase14-api-mapping" / "screen_api_backend_mapping_v14.json"
        if not api_path.exists():
            progress(f"{m}: no Phase 14 mapping, skipped")
            continue
        out = Path(modules_root) / m / "phase15-db-mapping"
        out.mkdir(parents=True, exist_ok=True)
        screen_mappings, stats = map_api_mappings_to_database(str(api_path), be, db_objects, backend_file_map, class_index)
        write_json(out / "database_object_registry_v15.json", db_objects)
        write_json(out / "backend_database_reference_registry_v15.json", backend_file_map)
        write_json(out / "screen_api_database_mapping_v15.json", screen_mappings)
        report = {"phase": "0.15.0", "status": "COMPLETED", "module": m, "apiMappingInput": str(api_path), "backendRoot": str(be.resolve()),
                  "database": db_meta, "databaseObjectCount": len(db_objects), "backendFilesWithDbReferences": len(backend_refs),
                  "stats": stats, "evidencePolicy": "Live SQL Server metadata is authoritative when connected. SQL source DDL is an offline "
                  "fallback. Exact code object references are high-confidence; name-based relationships remain candidates."}
        write_json(out / "db_mapping_report_v15.json", report)
        progress(f"{m}: {stats.get('apiMappingsWithDatabase', 0)}/{stats.get('apiMappings', 0)} API links reach the database")
        reports.append(report)
    return reports
