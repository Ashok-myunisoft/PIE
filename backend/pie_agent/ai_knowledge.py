from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm import get_provider, extract_json
from .models import Evidence, KnowledgePackage, KnowledgeRecord, ScreenRegistryEntry


KNOWLEDGE_TYPES = [
    "Functionality", "Feature", "BusinessRule", "Configuration", "Variant",
    "Workflow", "Training", "UAT", "BusinessSummary"
]


BUNDLE_NODE_TYPES = {"API", "BackendEndpoint", "BackendService", "DatabaseObject", "Evidence"}


def _load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _confidence(value: Any, default: float = 0.5) -> float:
    try:
        v = float(value)
        if v > 1:
            v /= 100.0
        return max(0.0, min(1.0, v))
    except Exception:
        return default


def _graph_rows(graph: dict[str, Any]) -> tuple[dict[str, dict], list[dict]]:
    nodes = {n["id"]: n for n in graph.get("nodes", []) if isinstance(n, dict) and n.get("id")}
    edges = [e for e in graph.get("edges", []) if isinstance(e, dict)]
    return nodes, edges


def _evidence_id_from_node(node: dict) -> str:
    return str(node.get("id", "")).removeprefix("evidence:")


def build_screen_bundles(graph: dict[str, Any], screen_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Create compact, evidence-first context bundles from the v0.16 graph."""
    nodes, edges = _graph_rows(graph)
    allowed = set(screen_ids or [])
    bundles = []
    # The graph is directed Screen -> API -> Endpoint -> Service/DB -> Evidence.
    # Following outgoing edges only keeps a bundle to its own screen's chain;
    # walking edges backwards through a shared table or service would pull in
    # other screens' endpoints and evidence.
    outgoing: dict[str, list[dict]] = {}
    for e in edges:
        outgoing.setdefault(e.get("source"), []).append(e)
    screen_nodes = [n for n in nodes.values() if n.get("type") == "Screen"]
    for sn in sorted(screen_nodes, key=lambda n: str(n.get("id"))):
        sid = sn.get("properties", {}).get("screenId") or str(sn["id"]).removeprefix("screen:")
        if allowed and sid not in allowed:
            continue
        node_ids = {sn["id"]}
        selected_edges = []
        frontier = [sn["id"]]
        # Bounded hops keep prompts small for ERP-wide runs.
        for _ in range(5):
            nxt = []
            for nid in frontier:
                for e in outgoing.get(nid, []):
                    other = e.get("target")
                    if other not in nodes or nodes[other].get("type") not in BUNDLE_NODE_TYPES:
                        continue
                    selected_edges.append(e)
                    if other not in node_ids:
                        node_ids.add(other); nxt.append(other)
            frontier = nxt
            if not frontier:
                break
        # De-duplicate edges while preserving deterministic order.
        uniq = {(e.get("source"), e.get("relationship"), e.get("target")): e for e in selected_edges}
        selected_edges = list(uniq.values())
        bundle_nodes = [nodes[nid] for nid in sorted(node_ids) if nid in nodes and nodes[nid].get("type") != "Evidence"]
        evidence = []
        seen_ev = set()
        for e in selected_edges:
            for eid in [e.get("target"), e.get("source")]:
                if not str(eid).startswith("evidence:") or eid in seen_ev or eid not in nodes:
                    continue
                en = nodes[eid]
                ev = {
                    "evidenceId": _evidence_id_from_node(en),
                    "sourceFile": en.get("properties", {}).get("source") or en.get("label", ""),
                    "fact": en.get("label", ""),
                    "confidence": _confidence(e.get("confidence"), 0.5),
                    "relationship": e.get("relationship", "SUPPORTED_BY")
                }
                evidence.append(ev); seen_ev.add(eid)
        # Some graphs carry provenance in edge evidence without creating nodes; preserve it.
        for e in selected_edges:
            for raw in e.get("evidence", []) or []:
                if isinstance(raw, dict):
                    src = raw.get("source") or raw.get("file") or raw.get("sourceFile")
                    fact = raw.get("fact") or raw.get("quote_or_fact") or raw.get("quoteOrFact") or ""
                else:
                    src, fact = str(raw), ""
                if not src:
                    continue
                evid = _sha(str(src) + "|" + str(fact))
                if evid not in seen_ev:
                    evidence.append({"evidenceId": evid, "sourceFile": str(src), "fact": _norm_text(fact) or str(src), "confidence": _confidence(e.get("confidence"), .5), "relationship": e.get("relationship", "SUPPORTED_BY")})
                    seen_ev.add(evid)
        props = sn.get("properties", {})
        bundles.append({
            "screen": {"screenId": sid, "screenName": sn.get("label", sid), **props},
            "nodes": bundle_nodes,
            "edges": [{k: e.get(k) for k in ("source", "relationship", "target", "confidence", "status")} for e in selected_edges],
            "evidence": evidence,
            "bundleHash": _sha(json.dumps({"screen": props, "nodes": bundle_nodes, "edges": selected_edges}, sort_keys=True, default=str))
        })
    return bundles


def _deterministic_records(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    screen = bundle["screen"]
    sid = screen["screenId"]
    evidence = bundle.get("evidence", [])
    evidence_objs = [Evidence(evidence_id=e["evidenceId"], source_file=e.get("sourceFile", ""), source_type="Evidence Graph", locator=e.get("relationship", ""), quote_or_fact=e.get("fact", ""), evidence_type="observed", confidence=_confidence(e.get("confidence"))) for e in evidence]
    records = []
    def add(typ, title, content, conf=.85, review=False, claim="observed", src=None):
        src = src or [e["evidenceId"] for e in evidence]
        ev = [x for x in evidence_objs if x.evidence_id in src]
        kid = "KNO-" + _sha(f"{sid}|{typ}|{title}")
        records.append({"knowledgeId":kid,"screenId":sid,"module":screen.get("module", ""),"functionality":screen.get("functionality", ""),"feature":screen.get("feature", ""),"knowledgeType":typ,"title":title,"content":content,"confidence":conf,"needsReview":review,"evidence":ev,"sourceFactIds":src,"generationMethod":"deterministic","claimType":claim})
    identity_evs = evidence[:3]
    add("Functionality", screen.get("functionality") or "Screen functionality", f"The screen belongs to the {screen.get('functionality') or screen.get('module') or 'identified'} functionality.", _confidence(screen.get("discoveryConfidence"), .7), not bool(identity_evs), "derived", [e["evidenceId"] for e in identity_evs])
    add("Feature", screen.get("feature") or screen.get("screenName") or sid, f"The canonical feature is {screen.get('feature') or screen.get('screenName') or sid}.", _confidence(screen.get("discoveryConfidence"), .7), not bool(identity_evs), "derived", [e["evidenceId"] for e in identity_evs])
    api_nodes = [n for n in bundle.get("nodes", []) if n.get("type") == "API"]
    if api_nodes:
        routes = [f"{n.get('properties',{}).get('method','')} {n.get('properties',{}).get('route',n.get('label',''))}".strip() for n in api_nodes]
        add("Workflow", "Observed API interaction points", "The screen has observed API interaction points: " + "; ".join(routes[:20]) + ".", .8, False, "observed")
    db_nodes = [n for n in bundle.get("nodes", []) if n.get("type") == "DatabaseObject"]
    if db_nodes:
        objs = [n.get("label", "") for n in db_nodes]
        add("BusinessRule", "Observed database dependencies", "The evidence graph links this screen to these database objects: " + ", ".join(objs[:30]) + ".", .75, True, "derived")
    if screen.get("screenName"):
        add("BusinessSummary", "Screen summary", f"{screen.get('screenName')} is an ERP screen identified under module {screen.get('module') or 'Unknown'}.", .75, False, "derived", [e["evidenceId"] for e in identity_evs])
    return records


AI_SCHEMA = {
    "records": [
        {
            "knowledgeType": "Functionality|Feature|BusinessRule|Configuration|Variant|Workflow|Training|UAT|BusinessSummary",
            "title": "short title",
            "content": "evidence-grounded statement",
            "confidence": 0.0,
            "claimType": "observed|derived|inferred",
            "needsReview": False,
            "reviewReason": "",
            "evidenceIds": ["evidence id from bundle only"]
        }
    ]
}


def _prompt(bundle: dict[str, Any]) -> str:
    return f"""You are the PIE ERP Knowledge Generation engine. Produce only evidence-grounded knowledge for ONE ERP screen.\n\nSTRICT RULES:\n1. Use only the supplied evidence bundle. Never invent business rules, defaults, roles, validations, workflow steps, or database meaning.\n2. Every record MUST cite one or more evidenceIds from the supplied bundle.\n3. If a statement is inferred or incomplete, set claimType='inferred', needsReview=true, and explain why.\n4. Do not treat filenames, names, or token similarity as proof of business behavior.\n5. Prefer fewer accurate records over speculative records.\n6. Valid knowledgeType values: {', '.join(KNOWLEDGE_TYPES)}.\n7. Return JSON only, matching this shape: {json.dumps(AI_SCHEMA)}\n\nEVIDENCE BUNDLE:\n{json.dumps(bundle, ensure_ascii=False, indent=2)}\n"""


def _safe_ai_records(raw: str, bundle: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        data = extract_json(raw)
    except Exception:
        # common fenced/preamble fallback
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return []
        try: data = json.loads(m.group(0))
        except Exception: return []
    rows = data.get("records", []) if isinstance(data, dict) else []
    valid_eids = {e["evidenceId"]: e for e in bundle.get("evidence", [])}
    out=[]
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict): continue
        typ = str(row.get("knowledgeType", "")).strip()
        if typ not in KNOWLEDGE_TYPES: continue
        eids = [str(x) for x in (row.get("evidenceIds") or []) if str(x) in valid_eids]
        content = _norm_text(row.get("content"))
        title = _norm_text(row.get("title"))
        if not title or not content or not eids:
            continue
        claim = str(row.get("claimType", "derived"))
        if claim not in {"observed", "derived", "inferred"}: claim = "inferred"
        review = bool(row.get("needsReview", False)) or claim == "inferred"
        conf = _confidence(row.get("confidence"), .5)
        out.append({**row, "knowledgeType":typ,"title":title,"content":content,"evidenceIds":eids,"confidence":conf,"claimType":claim,"needsReview":review,"reviewReason":_norm_text(row.get("reviewReason"))})
    return out


def _records_from_ai(bundle: dict[str, Any], rows: list[dict[str, Any]]) -> list[KnowledgeRecord]:
    screen=bundle["screen"]; sid=screen["screenId"]
    ev_by_id={e["evidenceId"]:e for e in bundle.get("evidence", [])}
    out=[]
    for r in rows:
        evid=[]
        for eid in r["evidenceIds"]:
            e=ev_by_id[eid]
            evid.append(Evidence(evidence_id=eid,source_file=e.get("sourceFile", ""),source_type="Evidence Graph",locator=e.get("relationship", ""),quote_or_fact=e.get("fact", ""),evidence_type="inferred" if r.get("claimType")=="inferred" else "observed",confidence=_confidence(e.get("confidence"))))
        out.append(KnowledgeRecord(knowledgeId="KNO-"+_sha(f"{sid}|{r['knowledgeType']}|{r['title']}"),screenId=sid,module=screen.get("module", ""),functionality=screen.get("functionality", ""),feature=screen.get("feature", ""),knowledgeType=r["knowledgeType"],title=r["title"],content=r["content"],confidence=r["confidence"],needsReview=r["needsReview"],evidence=evid,sourceFactIds=r["evidenceIds"],generationMethod="ai",claimType=r.get("claimType", "derived")))
    return out


def _package(bundle: dict[str, Any], records: list[KnowledgeRecord], method: str, graph_version: str) -> KnowledgePackage:
    s=bundle["screen"]
    screen=ScreenRegistryEntry(screenId=s["screenId"],module=s.get("module", ""),functionality=s.get("functionality", ""),feature=s.get("feature", ""),screenName=s.get("screenName", s["screenId"]),status="READY",discoveryConfidence=_confidence(s.get("discoveryConfidence"), .5),sourceFiles=[],sourceCategories=["Evidence Graph"])
    reasons=[]
    if not bundle.get("evidence"): reasons.append("No provenance evidence was available in the graph bundle.")
    if not records: reasons.append("No evidence-grounded knowledge records were generated.")
    for r in records:
        if r.needsReview:
            reasons.append(f"{r.knowledgeType}: {r.title} requires review.")
    status="REVIEW_REQUIRED" if reasons else "READY"
    return KnowledgePackage(packageVersion="1.1",screen=screen,records=records,generatedAt=datetime.now(timezone.utc).isoformat(),sourceHash=bundle["bundleHash"],status=status,generationPhase="ERP-wide AI Knowledge Generation",graphVersion=graph_version,reviewReasons=sorted(set(reasons)))


def generate_ai_knowledge(graph_path: str | Path, output_dir: str | Path, provider_name: str | None = None, limit: int | None = None, screen_id: str | None = None, workers: int = 1) -> dict[str, Any]:
    graph=_load(graph_path); out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); kd=out/"knowledge"; kd.mkdir(exist_ok=True)
    ids=[screen_id] if screen_id else None
    bundles=build_screen_bundles(graph, ids)
    if limit is not None: bundles=bundles[:max(0,limit)]
    provider=get_provider(provider_name)
    ai_enabled=(provider_name or os.getenv("PIE_LLM_PROVIDER","offline")).lower() in {"openai", "azure", "azure_openai"}
    def one(bundle):
        if ai_enabled:
            try:
                rows=_safe_ai_records(provider.generate(_prompt(bundle)), bundle)
                records=_records_from_ai(bundle, rows)
                method="ai" if records else "hybrid"
                if not records:
                    records=[KnowledgeRecord.model_validate(x) for x in _deterministic_records(bundle)]
            except Exception as exc:
                records=[KnowledgeRecord.model_validate(x) for x in _deterministic_records(bundle)]
                method="hybrid"
                # mark fallback records for review without inventing failure as source evidence
                for r in records: r.needsReview=True
        else:
            records=[KnowledgeRecord.model_validate(x) for x in _deterministic_records(bundle)]
            method="deterministic"
        pkg=_package(bundle,records,method,str(graph.get("graphVersion","0.16.0")))
        if method=="hybrid":
            pkg.reviewReasons.append("AI generation was unavailable or returned no valid evidence-grounded records; deterministic fallback used.")
            pkg.status="REVIEW_REQUIRED"
        # Backslashes inside f-string expressions are a SyntaxError before Python 3.12.
        safe_id=re.sub(r"[\\/]", "_", bundle['screen']['screenId'])
        path=kd/f"{safe_id}.json"
        path.write_text(json.dumps(pkg.model_dump(mode="json"),indent=2,ensure_ascii=False),encoding="utf-8")
        return pkg, method
    results=[]
    if workers and workers > 1 and len(bundles) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            results=list(ex.map(one,bundles))
    else:
        results=[one(b) for b in bundles]
    packages=[x[0] for x in results]
    stats={"screensRequested":len(bundles),"packagesGenerated":len(packages),"ready":sum(p.status=="READY" for p in packages),"reviewRequired":sum(p.status=="REVIEW_REQUIRED" for p in packages),"errors":sum(p.status=="ERROR" for p in packages),"aiPackages":sum(m=="ai" for _,m in results),"hybridPackages":sum(m=="hybrid" for _,m in results),"deterministicPackages":sum(m=="deterministic" for _,m in results)}
    manifest={"generationVersion":"0.17.0","status":"COMPLETED","graphVersion":graph.get("graphVersion","0.16.0"),"generatedAt":datetime.now(timezone.utc).isoformat(),"statistics":stats,"knowledgePackages":[p.model_dump(mode="json") for p in packages]}
    (out/"knowledge_manifest_v17.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
    (out/"generation_report_v17.json").write_text(json.dumps({"phase":"0.17.0",**stats,"output":str(out.resolve())},indent=2),encoding="utf-8")
    (out/"screen_evidence_bundles_v17.json").write_text(json.dumps(bundles,indent=2,ensure_ascii=False),encoding="utf-8")
    return {"phase":"0.17.0",**stats,"output":str(out.resolve())}
