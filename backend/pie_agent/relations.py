"""Relationship explorer: screen -> API -> backend endpoint -> service -> database table.

Reads the Phase 16 evidence graphs (outputs/modules/<module>/phase16-evidence-graph) and
answers, for one screen, which APIs it calls, which backend endpoints and services
implement them, which tables they read or write, and the evidence (file, SQL, form
action) behind every link. It also joins the screen's field dictionary (which fields are
stored in which table) and, when a KMS snapshot is supplied, the Qdrant knowledge held
for the screen. Nothing is inferred here: every relationship is one the graph recorded,
with the confidence and status (OBSERVED / CANDIDATE) it recorded.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import from_root

GRAPH_NAME = "phase16-evidence-graph/evidence_graph_v16.json"
SERVICE_RELS = {"USES_SERVICE", "CALLS_SERVICE"}


@dataclass
class Graph:
    module: str
    path: Path
    nodes: dict[str, dict[str, Any]]
    out: dict[str, list[dict[str, Any]]]
    generated_at: str = ""
    table_screens: dict[str, set[str]] = field(default_factory=dict)  # table name -> screen ids reaching it


_CACHE: dict[str, tuple[float, Graph]] = {}


def _load(path: Path, module: str) -> Graph:
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in data.get("nodes", [])}
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in data.get("edges", []):
        out[e["source"]].append(e)
    g = Graph(module, path, nodes, out, data.get("generatedAt", ""))
    for sid in _module_screens(g):
        for t in _screen_tables(g, sid):
            g.table_screens.setdefault(t, set()).add(sid)
    return g


def graphs(modules_root: str | Path = "outputs/modules") -> list[Graph]:
    """One graph per module folder, cached until the file changes."""
    result = []
    for path in sorted(from_root(modules_root).glob(f"*/{GRAPH_NAME}")):
        key, mtime = str(path), path.stat().st_mtime
        hit = _CACHE.get(key)
        if not hit or hit[0] != mtime:
            hit = (mtime, _load(path, path.parents[1].name))
            _CACHE[key] = hit
        result.append(hit[1])
    return result


def _module_screens(g: Graph) -> list[str]:
    # A module graph also carries ERP-wide screen nodes; keep the module's own screens.
    return [nid for nid, n in g.nodes.items()
            if n.get("type") == "Screen" and (n.get("properties") or {}).get("module") == g.module]


def _targets(g: Graph, nid: str, rels: set[str], node_type: str | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    res = []
    for e in g.out.get(nid, []):
        if e.get("relationship") in rels:
            t = g.nodes.get(e["target"])
            if t and (node_type is None or t.get("type") == node_type):
                res.append((e, t))
    return res


def _table_name(node: dict[str, Any]) -> str:
    p = node.get("properties") or {}
    return str(p.get("name") or node.get("label") or "").upper()


def _screen_tables(g: Graph, sid: str) -> set[str]:
    tables = set()
    for _, api in _targets(g, sid, {"CALLS"}, "API"):
        for _, ep in _targets(g, api["id"], {"IMPLEMENTED_BY"}, "BackendEndpoint"):
            for owner in [ep] + [s for _, s in _targets(g, ep["id"], SERVICE_RELS, "BackendService")]:
                tables |= {_table_name(t) for _, t in _targets(g, owner["id"], {"READS_OR_WRITES"}, "DatabaseObject")}
    return tables


def _evidence(g: Graph, nid: str) -> list[dict[str, Any]]:
    """Evidence nodes attached with SUPPORTED_BY: the file or fact that proves the node exists."""
    out = []
    for e, t in _targets(g, nid, {"SUPPORTED_BY"}, "Evidence"):
        p = t.get("properties") or {}
        out.append({"source": p.get("source") or t.get("label"), "fact": p.get("fact") or "", "confidence": e.get("confidence")})
    return out


def _edge_evidence(e: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    """Evidence recorded on a relationship: form-action URL, SQL statement, DDL file..."""
    seen, out = set(), []
    for ev in e.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        item = {"where": ev.get("file") or ev.get("source") or "", "what": ev.get("evidence") or ev.get("url") or ev.get("fact") or "",
                "kind": ev.get("kind") or "", "operations": ev.get("operations") or []}
        key = (item["where"], item["what"])
        if key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _operations(e: dict[str, Any]) -> set[str]:
    ops = set()
    for ev in e.get("evidence") or []:
        if isinstance(ev, dict):
            ops |= {str(o).upper() for o in ev.get("operations") or []}
    return ops


# screenId per dictionary file, read once per file version: with every module built there are
# hundreds of large dictionaries, and only one is needed for a screen.
_DICT_INDEX: dict[str, tuple[float, str]] = {}
_DICT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _dictionary_index(modules_root: str | Path = "outputs/modules") -> dict[str, Path]:
    index: dict[str, Path] = {}
    for f in from_root(modules_root).glob("*/phase20-fields/*_fields_v20.json"):
        key, mtime = str(f), f.stat().st_mtime
        hit = _DICT_INDEX.get(key)
        if not hit or hit[0] != mtime:
            try:
                m = re.search(r'"screenId":\s*"([^"]+)"', f.read_text(encoding="utf-8")[:4000])
            except OSError:
                continue
            hit = (mtime, m.group(1) if m else "")
            _DICT_INDEX[key] = hit
        if hit[1]:
            index[hit[1]] = f
    return index


def _field_dictionary(screen_id: str, modules_root: str | Path = "outputs/modules") -> dict[str, Any] | None:
    path = _dictionary_index(modules_root).get(screen_id)
    if not path:
        return None
    key, mtime = str(path), path.stat().st_mtime
    hit = _DICT_CACHE.get(key)
    if not hit or hit[0] != mtime:
        try:
            hit = (mtime, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None
        if len(_DICT_CACHE) > 24:
            _DICT_CACHE.pop(next(iter(_DICT_CACHE)))
        _DICT_CACHE[key] = hit
    return hit[1]


_LIST_CACHE: dict[str, tuple[tuple, dict[str, Any]]] = {}


def list_screens(modules_root: str | Path = "outputs/modules") -> dict[str, Any]:
    dicts = _dictionary_index(modules_root)
    all_graphs = graphs(modules_root)
    version = (tuple((str(g.path), id(g)) for g in all_graphs), tuple(sorted(dicts)))
    hit = _LIST_CACHE.get(str(modules_root))
    if hit and hit[0] == version:
        return hit[1]
    screens = []
    for g in all_graphs:
        for sid in _module_screens(g):
            n = g.nodes[sid]
            p = n.get("properties") or {}
            apis = _targets(g, sid, {"CALLS"}, "API")
            endpoints = {ep["id"] for _, a in apis for _, ep in _targets(g, a["id"], {"IMPLEMENTED_BY"}, "BackendEndpoint")}
            screens.append({"screenId": p.get("screenId") or sid.split(":", 1)[-1], "label": n.get("label"), "module": g.module,
                            "feature": p.get("feature"), "status": p.get("status"), "apis": len(apis), "endpoints": len(endpoints),
                            "tables": len(_screen_tables(g, sid)), "evidence": len(_evidence(g, sid)),
                            "hasFields": (p.get("screenId") or "") in dicts})
    screens.sort(key=lambda s: (s["module"], -(s["apis"] > 0), s["label"] or ""))
    result = {"screens": screens, "modules": sorted({s["module"] for s in screens}),
              "graphs": [{"module": g.module, "file": str(g.path), "generatedAt": g.generated_at,
                          "nodes": len(g.nodes), "edges": sum(len(v) for v in g.out.values())} for g in all_graphs]}
    _LIST_CACHE[str(modules_root)] = (version, result)
    return result


def _kms_for_screen(points: list[dict[str, Any]], screen_id: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in points:
        if p.get("screen_id") == screen_id:
            groups[p.get("knowledge_type") or "?"].append(p)
    return {kt: {"count": len(ps), "items": [{"title": p.get("title"), "knowledgeId": p.get("knowledge_id"),
                                              "status": p.get("publication_status"), "readiness": p.get("readiness")}
                                             for p in ps[:12]]}
            for kt, ps in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def screen_relations(screen_id: str, kms_points: list[dict[str, Any]] | None = None,
                     modules_root: str | Path = "outputs/modules") -> dict[str, Any] | None:
    nid = f"screen:{screen_id}"
    all_graphs = graphs(modules_root)
    g = next((g for g in all_graphs if nid in g.nodes and (g.nodes[nid].get("properties") or {}).get("module") == g.module),
             next((g for g in all_graphs if nid in g.nodes), None))
    if g is None:
        return None
    node = g.nodes[nid]
    props = node.get("properties") or {}
    apis, services, tables = [], {}, {}
    for e, api in _targets(g, nid, {"CALLS"}, "API"):
        ap = api.get("properties") or {}
        endpoints = []
        for e2, ep in _targets(g, api["id"], {"IMPLEMENTED_BY"}, "BackendEndpoint"):
            pp = ep.get("properties") or {}
            svc_list = []
            owners = [ep]
            for e3, svc in _targets(g, ep["id"], SERVICE_RELS, "BackendService"):
                sp = svc.get("properties") or {}
                svc_list.append({"id": svc["id"], "name": sp.get("className") or svc.get("label"), "file": sp.get("file") or "",
                                 "relationship": e3.get("relationship"), "confidence": e3.get("confidence"), "status": e3.get("status")})
                services.setdefault(svc["id"], {"id": svc["id"], "name": sp.get("className") or svc.get("label"), "file": sp.get("file") or "",
                                                "interfaces": sp.get("interfaces") or []})
                owners.append(svc)
            ep_tables = []
            for owner in owners:
                for e4, t in _targets(g, owner["id"], {"READS_OR_WRITES"}, "DatabaseObject"):
                    name = _table_name(t)
                    ops = _operations(e4)
                    ep_tables.append({"id": t["id"], "name": name, "operations": sorted(ops)})
                    tb = tables.setdefault(t["id"], {"id": t["id"], "name": name, "schema": (t.get("properties") or {}).get("schema"),
                                                     "objectType": (t.get("properties") or {}).get("objectType"), "operations": set(),
                                                     "statuses": set(), "confidence": 0.0, "via": [], "evidence": [],
                                                     "definedIn": [x["source"] for x in _evidence(g, t["id"])][:3]})
                    tb["operations"] |= ops
                    tb["statuses"].add(e4.get("status") or "")
                    tb["confidence"] = max(tb["confidence"], float(e4.get("confidence") or 0))
                    via = f"{ap.get('method') or ''} {ap.get('route') or api.get('label')}".strip()
                    if via not in tb["via"]:
                        tb["via"].append(via)
                    for ev in _edge_evidence(e4):
                        if ev not in tb["evidence"] and len(tb["evidence"]) < 8:
                            tb["evidence"].append(ev)
            endpoints.append({"id": ep["id"], "name": pp.get("className") or ep.get("label"), "file": pp.get("file") or "",
                              "namespace": pp.get("namespace") or "", "confidence": e2.get("confidence"), "status": e2.get("status"),
                              "evidence": _edge_evidence(e2), "services": svc_list,
                              "tables": sorted({t["name"] for t in ep_tables})})
        apis.append({"id": api["id"], "method": ap.get("method") or "", "route": ap.get("route") or api.get("label"),
                     "confidence": e.get("confidence"), "status": e.get("status"), "evidence": _edge_evidence(e),
                     "apiEvidence": _evidence(g, api["id"])[:4], "endpoints": endpoints})

    # Which fields of this screen are stored in which table (from its field dictionary).
    fields_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fd = _field_dictionary(props.get("screenId") or screen_id, modules_root)
    for r in (fd or {}).get("fields", []):
        db = r.get("database") or {}
        if db.get("table") and db.get("column"):
            fields_by_table[str(db["table"]).upper()].append({"label": r.get("label"), "field": r.get("fieldName"),
                                                               "column": db.get("column"), "dataType": db.get("dataType")})
    others: dict[str, list[dict[str, Any]]] = {}
    for gg in all_graphs:
        for tname, sids in gg.table_screens.items():
            for sid in sids:
                if sid != nid:
                    others.setdefault(tname, []).append({"screenId": sid.split(":", 1)[-1], "label": gg.nodes[sid].get("label"),
                                                         "module": gg.module})
    table_list = []
    for tb in tables.values():
        tb["operations"] = sorted(tb["operations"])
        tb["statuses"] = sorted(s for s in tb["statuses"] if s)
        tb["fields"] = fields_by_table.get(tb["name"], [])
        tb["alsoUsedBy"] = sorted(others.get(tb["name"], []), key=lambda s: (s["module"], s["label"] or ""))[:30]
        tb["alsoUsedByCount"] = len(others.get(tb["name"], []))
        table_list.append(tb)
    # Tables the screen writes and whose fields it stores come first, then by name.
    table_list.sort(key=lambda t: (not t["fields"], "WRITE" not in t["operations"], t["name"]))

    return {
        "screen": {"screenId": props.get("screenId") or screen_id, "label": node.get("label"), "module": g.module,
                   "feature": props.get("feature"), "status": props.get("status"), "confidence": props.get("discoveryConfidence")},
        "graph": {"module": g.module, "file": str(g.path), "generatedAt": g.generated_at},
        "evidence": _evidence(g, nid),
        "apis": apis,
        "services": sorted(services.values(), key=lambda s: s["name"] or ""),
        "tables": table_list,
        "fieldDictionary": {"form": fd.get("form"), "entity": fd.get("entity"), "table": fd.get("table"),
                            "fields": len(fd.get("fields", []))} if fd else None,
        "knowledge": _kms_for_screen(kms_points, props.get("screenId") or screen_id) if kms_points is not None else None,
        "stats": {"apis": len(apis), "endpoints": sum(len(a["endpoints"]) for a in apis), "services": len(services),
                  "tables": len(table_list), "writes": sum(1 for t in table_list if "WRITE" in t["operations"]),
                  "evidence": len(_evidence(g, nid)), "fieldsMapped": sum(len(v) for v in fields_by_table.values())},
    }
