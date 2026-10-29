from __future__ import annotations
import hashlib, json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


def _load(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8', 'ignore')).hexdigest()[:16]


def _node(nodes, node_id, node_type, label, **props):
    if node_id not in nodes:
        nodes[node_id] = {"id": node_id, "type": node_type, "label": label, "properties": props}
    else:
        nodes[node_id]["properties"].update({k:v for k,v in props.items() if v not in (None, '', [], {})})


def _edge(edges, seen, src, rel, dst, confidence=0.0, evidence=None, status="OBSERVED"):
    key=(src, rel, dst)
    if key in seen:
        return
    seen.add(key)
    edges.append({"source":src,"relationship":rel,"target":dst,"confidence":round(float(confidence),4),"status":status,"evidence":evidence or []})


def build_evidence_graph(api_mapping_path: str | Path,
                         db_mapping_path: str | Path,
                         screen_registry_path: str | Path | None = None,
                         output: str | Path = "outputs/phase16-evidence-graph") -> dict[str, Any]:
    """Build a normalized Screen -> API -> Backend -> DB evidence graph.

    v0.14/v0.15 currently emit top-level lists, while older dialects used dictionaries.
    This implementation accepts both so the graph remains cumulative and backward compatible.
    """
    api_path=Path(api_mapping_path); db_path=Path(db_mapping_path); out=Path(output); out.mkdir(parents=True, exist_ok=True)
    api=_load(api_path); db=_load(db_path)
    screens=_load(screen_registry_path) if screen_registry_path and Path(screen_registry_path).exists() else []
    nodes={}; edges=[]; seen=set()

    def as_rows(value: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        if isinstance(value, list): return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            for k in keys:
                v=value.get(k)
                if isinstance(v, list): return [x for x in v if isinstance(x, dict)]
            return [value]
        return []

    screen_rows=as_rows(screens, ('screens','registry'))
    api_rows=as_rows(api, ('mappings','screenApiMappings','screenMappings'))
    db_rows=as_rows(db, ('mappings','screenDatabaseMappings','screenApiDatabaseMappings'))

    for s in screen_rows:
        sid=s.get('screenId') or s.get('id')
        if not sid: continue
        _node(nodes, f"screen:{sid}", "Screen", s.get('screenName') or s.get('feature') or sid,
              screenId=sid,module=s.get('module',''),functionality=s.get('functionality',''),feature=s.get('feature',''),status=s.get('status',''),discoveryConfidence=s.get('discoveryConfidence',s.get('confidence',0)))
        for ev in s.get('evidence',[]) or []:
            src=ev.get('source') if isinstance(ev,dict) else str(ev)
            fact=ev.get('fact','') if isinstance(ev,dict) else ''
            if src:
                eid=f"evidence:{_hash(str(src)+'|'+str(fact))}"
                _node(nodes,eid,'Evidence',str(src),source=str(src),fact=str(fact))
                _edge(edges,seen,f"screen:{sid}",'SUPPORTED_BY',eid,_confidence_value(s.get('discoveryConfidence',s.get('confidence',0))),[], 'OBSERVED')

    # v0.14 list shape: screen -> apiMappings -> serviceImplementations.
    for m in api_rows:
        sid=m.get('screenId') or m.get('screen') or m.get('id')
        if not sid: continue
        sref=f"screen:{sid}"
        if sref not in nodes: _node(nodes,sref,'Screen',m.get('screenName') or sid,screenId=sid,module=m.get('module',''),feature=m.get('feature',''))
        apis=m.get('apiMappings') or m.get('apis') or m.get('apiMappings',[])
        if isinstance(apis,dict): apis=[apis]
        for a in apis:
            route=a.get('route') or a.get('endpoint') or a.get('api') or a.get('url')
            if not route: continue
            aid=f"api:{_hash(str(a.get('method','GET'))+' '+str(route))}"
            _node(nodes,aid,'API',route,method=a.get('method',''),route=route)
            conf=_confidence_value(a.get('confidence',m.get('confidence',0)))
            evlist=_collect_evidence(a)
            _edge(edges,seen,sref,'CALLS',aid,conf,evlist,a.get('mappingStatus',a.get('status','OBSERVED')))
            legacy_backend=a.get('backend') if isinstance(a.get('backend'),dict) else {}
            bclass=a.get('endpointClass') or a.get('backendEndpoint') or a.get('className') or legacy_backend.get('class') or legacy_backend.get('endpointClass')
            bid=_backend_id(bclass,a.get('endpointFile',''))
            if bclass:
                _node(nodes,bid,'BackendEndpoint',bclass,className=bclass,file=a.get('endpointFile',''),namespace=a.get('endpointNamespace',''))
                _edge(edges,seen,aid,'IMPLEMENTED_BY',bid,conf,evlist,'OBSERVED')
            legacy_service=legacy_backend.get('service')
            if legacy_service:
                name=legacy_service.get('name') or legacy_service.get('class') if isinstance(legacy_service,dict) else str(legacy_service)
                if name:
                    sid2=f'service:{name}'; _node(nodes,sid2,'BackendService',name,className=name)
                    _edge(edges,seen,bid if bclass else aid,'USES_SERVICE',sid2,conf,evlist,'OBSERVED')
            for si in a.get('serviceImplementations',[]) or []:
                if not isinstance(si,dict): continue
                name=si.get('className') or si.get('name') or si.get('class')
                if not name: continue
                sid2=f"service:{name}"
                _node(nodes,sid2,'BackendService',name,className=name,file=si.get('file',''),interfaces=si.get('interfaces',[]))
                parent=bid if bclass else aid
                _edge(edges,seen,parent,'USES_SERVICE',sid2,_confidence_value(si.get('confidence',conf)),_collect_evidence(si),'OBSERVED')
            for sc in a.get('serviceCalls',[]) or []:
                if isinstance(sc,dict) and sc.get('serviceType'):
                    name=sc['serviceType']; sid2=f"service-call:{name}"
                    _node(nodes,sid2,'BackendService',name,className=name)
                    parent=bid if bclass else aid
                    _edge(edges,seen,parent,'CALLS_SERVICE',sid2,conf,[], 'OBSERVED')
            # Frontend source evidence is attached to the API edge.
            for fe in a.get('frontendEvidence',[]) or []:
                src=fe.get('file') if isinstance(fe,dict) else str(fe)
                if src:
                    fact=fe.get('url') or fe.get('source') or '' if isinstance(fe,dict) else ''
                    eid=f"evidence:{_hash(str(src)+'|'+str(fact))}"
                    _node(nodes,eid,'Evidence',str(src),source=str(src),fact=str(fact))
                    _edge(edges,seen,aid,'SUPPORTED_BY',eid,conf,[], 'OBSERVED')

    # v0.15 DB mapping is nested under each API mapping.
    for m in db_rows:
        sid=m.get('screenId') or m.get('screen') or m.get('id')
        if not sid: continue
        sref=f"screen:{sid}"
        if sref not in nodes: _node(nodes,sref,'Screen',m.get('screenName') or sid,screenId=sid,module=m.get('module',''),feature=m.get('feature',''))
        for a in m.get('apiMappings',[]) or []:
            if not isinstance(a,dict): continue
            route=a.get('route',''); method=a.get('method','GET')
            aid=f"api:{_hash(str(method)+' '+str(route))}" if route else None
            if aid and aid not in nodes:
                _node(nodes,aid,'API',route,method=method,route=route)
                _edge(edges,seen,sref,'CALLS',aid,_confidence_value(a.get('confidence',m.get('confidence',0))),_collect_evidence(a),'OBSERVED')
            parent=aid or sref
            bclass=a.get('endpointClass')
            if bclass:
                bid=_backend_id(bclass,a.get('endpointFile',''))
                _node(nodes,bid,'BackendEndpoint',bclass,className=bclass,file=a.get('endpointFile',''))
                if aid: _edge(edges,seen,aid,'IMPLEMENTED_BY',bid,_confidence_value(a.get('confidence',0)),_collect_evidence(a),'OBSERVED')
                parent=bid
            for o in a.get('databaseMappings',[]) or []:
                if not isinstance(o,dict): continue
                name=o.get('name') or o.get('objectName') or o.get('table')
                if not name: continue
                schema=o.get('schema','dbo'); typ=o.get('type','TABLE')
                oid=f"db:{schema}.{name}".lower()
                _node(nodes,oid,'DatabaseObject',f"{schema}.{name}",schema=schema,name=name,objectType=typ)
                conf=_confidence_value(o.get('confidence',a.get('confidence',0)))
                ev=_collect_evidence(o)
                _edge(edges,seen,parent,'READS_OR_WRITES',oid,conf,ev,o.get('databaseMappingStatus',o.get('status','CANDIDATE')))
                for raw in (o.get('databaseEvidence',[]) or []) + (o.get('backendEvidence',[]) or []):
                    if isinstance(raw,dict): src=raw.get('source') or raw.get('file'); fact=raw.get('fact') or raw.get('evidence','')
                    else: src=str(raw); fact=''
                    if src:
                        eid=f"evidence:{_hash(str(src)+'|'+str(fact))}"
                        _node(nodes,eid,'Evidence',str(src),source=str(src),fact=str(fact))
                        _edge(edges,seen,oid,'SUPPORTED_BY',eid,conf,[], 'OBSERVED')
                for src in o.get('evidenceFiles',[]) or []:
                    eid=f"evidence:{_hash(str(src))}"
                    _node(nodes,eid,'Evidence',str(src),source=str(src),fact='backend/database evidence file')
                    _edge(edges,seen,oid,'SUPPORTED_BY',eid,conf,[], 'OBSERVED')

    # Legacy v0.16-compatible dialect: screen -> databaseObjects directly.
    for m in db_rows:
        sid=m.get('screenId') or m.get('screen') or m.get('id')
        if not sid: continue
        sref=f"screen:{sid}"
        for o in (m.get('databaseObjects') or m.get('objects') or m.get('dbObjects') or []):
            if not isinstance(o,dict): continue
            name=o.get('name') or o.get('objectName') or o.get('table')
            if not name: continue
            schema=o.get('schema','dbo'); typ=o.get('type','TABLE'); oid=f"db:{schema}.{name}".lower()
            _node(nodes,oid,'DatabaseObject',f"{schema}.{name}",schema=schema,name=name,objectType=typ)
            _edge(edges,seen,sref,'USES_DATABASE_OBJECT',oid,_confidence_value(o.get('confidence',m.get('confidence',0))),_collect_evidence(o),o.get('status','OBSERVED'))

    # Promote edge-level provenance to explicit evidence nodes for retrieval.
    for e in list(edges):
        for raw in e.get('evidence',[]) or []:
            if isinstance(raw,dict): src=raw.get('source') or raw.get('file') or raw.get('sourceFile'); fact=raw.get('fact') or raw.get('quote_or_fact') or ''
            else: src=str(raw); fact=''
            if not src: continue
            eid=f"evidence:{_hash(str(src)+'|'+str(fact))}"
            _node(nodes,eid,'Evidence',str(src),source=str(src),fact=str(fact))
            _edge(edges,seen,e['source'],'SUPPORTED_BY',eid,e.get('confidence',0),[], 'OBSERVED')

    graph={
        'graphVersion':'0.16.0','generatedAt':datetime.now(timezone.utc).isoformat(),
        'nodes':list(nodes.values()),'edges':edges,
        'statistics':{
            'nodes':len(nodes),'edges':len(edges),
            'screens':sum(n['type']=='Screen' for n in nodes.values()),
            'apis':sum(n['type']=='API' for n in nodes.values()),
            'backendEndpoints':sum(n['type']=='BackendEndpoint' for n in nodes.values()),
            'backendServices':sum(n['type']=='BackendService' for n in nodes.values()),
            'databaseObjects':sum(n['type']=='DatabaseObject' for n in nodes.values()),
            'evidenceNodes':sum(n['type']=='Evidence' for n in nodes.values())
        },
        'provenance':{'apiMapping':str(api_path).replace('\\','/'),'dbMapping':str(db_path).replace('\\','/'),'screenRegistry':str(screen_registry_path).replace('\\','/') if screen_registry_path else None}
    }
    (out/'evidence_graph_v16.json').write_text(json.dumps(graph,indent=2),encoding='utf-8')
    adjacency={}
    for e in edges: adjacency.setdefault(e['source'],[]).append({'relationship':e['relationship'],'target':e['target'],'confidence':e['confidence']})
    (out/'evidence_graph_index_v16.json').write_text(json.dumps(adjacency,indent=2),encoding='utf-8')
    report={'status':'COMPLETED','graphVersion':'0.16.0',**graph['statistics'],'output':str(out.resolve())}
    (out/'evidence_graph_report_v16.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def _backend_id(class_name: str | None, file: str | None) -> str:
    # FastEndpoints class names repeat across modules (e.g. GetX in two
    # solutions), so the endpoint file is the identity when it is known.
    return f"backend:{file}" if file else f"backend:{class_name}"


def _confidence_value(v: Any) -> float:
    try:
        x=float(v or 0); return x/100 if x>1 else max(0,min(1,x))
    except Exception: return 0.0


def _collect_evidence(row: dict[str, Any]) -> list[Any]:
    out=[]
    for key in ('evidence','backendEvidence','databaseEvidence','frontendEvidence'):
        value=row.get(key,[]) or []
        if isinstance(value,dict): value=[value]
        out.extend(value if isinstance(value,list) else [value])
    return out

