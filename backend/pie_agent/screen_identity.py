from __future__ import annotations
import hashlib, json, re
from dataclasses import asdict
from pathlib import Path
from typing import Any
from .erp_source import ScreenCandidate, clean_name, stable_id, read_text, IGNORED_DIRS, infer_functionality, project_from_path, module_from_path

SCREENY_WORDS = re.compile(r"(screen|page|form|view|master|transaction|report|dashboard|setup|admin|application)", re.I)

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _norm(s)))

def _score(candidate: ScreenCandidate) -> tuple[float, list[str], str]:
    evidence = candidate.evidence or []
    kinds = {str(e.get('kind','')).lower() for e in evidence}
    facts = [str(e.get('fact','')) for e in evidence]
    # The score is built only from observed evidence. The discovery-time prior
    # (candidate.confidence, a fixed .78 for every component) is deliberately not
    # used, otherwise every component is LIKELY regardless of evidence.
    score = 0.0
    reasons=[]
    registry = any('registry' in str(e.get('source','')).lower() for e in evidence) or 'registry' in ' '.join(facts).lower()
    if candidate.formDefinitionPath:
        score += .40; reasons.append('form definition')
    if registry:
        score += .40; reasons.append('GB form registry')
    if candidate.menuKey:
        score += .30; reasons.append('menu evidence')
    if candidate.componentPath:
        score += .30; reasons.append('frontend component')
    if candidate.route and not candidate.route.startswith('dev/'):
        score += .20; reasons.append('production-like route')
    elif candidate.route:
        score += .05; reasons.append('dev/test route only')
    if candidate.apiRefs:
        score += .05; reasons.append('API evidence')
    if candidate.dbRefs:
        score += .05; reasons.append('DB evidence')
    score=min(1.0, score)
    strong = bool(candidate.formDefinitionPath or registry or candidate.menuKey)
    if strong and score >= .70:
        status='CONFIRMED'
    elif score >= .50:
        status='LIKELY'
    else:
        status='CANDIDATE'
    return round(score,3), reasons, status

def _readability(name: str, module: str) -> tuple[int, int, int]:
    # "Batch Detail" beats "Inventory Batch Detail" (module repeated), and
    # "Assembly Bom" (component-derived) beats "Assemblybom" (file stem).
    mod = _compact(module)
    no_prefix = not (mod and _compact(name).startswith(mod))
    return (int(no_prefix), name.count(' '), -len(name))

def _merge(a: ScreenCandidate,b: ScreenCandidate):
    # Prefer richer values without discarding source evidence.
    if _readability(b.feature or b.screenName, a.module) > _readability(a.feature or a.screenName, a.module):
        a.feature, a.screenName = b.feature, b.screenName
    for attr in ('componentPath','templatePath','route','routeFile','menuKey','componentExport','remote','formDefinitionPath','project'):
        av=getattr(a,attr); bv=getattr(b,attr)
        if not av and bv: setattr(a,attr,bv)
    if b.fields and not a.fields: a.fields=b.fields
    a.apiRefs=sorted({(x.get('method'),x.get('route')):x for x in (a.apiRefs or []) + (b.apiRefs or [])}.values(), key=lambda x:x.get('score',0), reverse=True)[:20]
    a.dbRefs=sorted(set((a.dbRefs or [])+(b.dbRefs or [])))[:20]
    ev={(str(e.get('source')),str(e.get('fact'))):e for e in (a.evidence or [])+(b.evidence or [])}
    a.evidence=list(ev.values())
    a.confidence=max(a.confidence,b.confidence)

def _compact(s: str) -> str:
    return _norm(s).replace(' ', '')

def _canonical_key(c: ScreenCandidate) -> tuple[str,str]:
    # For GB5, module + feature is more stable than component class names.
    # Spaces are removed so the component name "Assembly Bom" and the form file
    # stem "assemblybom" resolve to one screen.
    module, feature = _norm(c.module), _compact(c.feature or c.screenName)
    # Registry keys often repeat the module ("InventoryBatchDetail" in module
    # inventory); strip it so it matches "Batch Detail".
    mod = module.replace(' ', '')
    if mod and feature.startswith(mod) and len(feature) > len(mod):
        feature = feature[len(mod):]
    return (module, feature)

def consolidate(candidates: list[ScreenCandidate]) -> tuple[list[dict[str,Any]], dict[str,Any]]:
    groups: dict[tuple[str,str],ScreenCandidate]={}
    aliases: dict[tuple[str,str],set[str]]={}
    duplicates=0
    for c in candidates:
        key=_canonical_key(c)
        if not key[0]:
            key=('unknown',key[1])
        aliases.setdefault(key,set()).add(stable_id(c.module or 'Unknown', c.feature or c.screenName))
        if key in groups:
            _merge(groups[key],c); duplicates += 1
        else:
            groups[key]=c
    alias_by_obj={id(groups[k]):v for k,v in aliases.items()}
    registry=[]
    counts={'CONFIRMED':0,'LIKELY':0,'CANDIDATE':0}
    for c in sorted(groups.values(), key=lambda x:(x.module.lower(),x.feature.lower(),x.screenName.lower())):
        # Canonical ID intentionally excludes component class so refactors do not create new screens.
        c.screenId=stable_id(c.module or 'Unknown', c.feature or c.screenName)
        score,reasons,status=_score(c)
        src=[]
        for p in (c.componentPath,c.templatePath,c.routeFile,c.formDefinitionPath):
            if p and p not in src: src.append(p)
        for e in c.evidence or []:
            s=e.get('source')
            if s and s not in src: src.append(s)
        cats=['Frontend Code'] if c.componentPath else []
        if c.route: cats.append('Angular Route')
        if c.formDefinitionPath: cats.append('Frontend Form Definition')
        if c.apiRefs: cats.append('Backend API Evidence')
        if c.dbRefs: cats.append('Database Evidence')
        rec={
            'screenId':c.screenId,'screenName':c.screenName or c.feature,'module':c.module,'functionality':c.functionality or infer_functionality(c.module,c.feature,''),'feature':c.feature or c.screenName,
            'project':c.project,'status':status,'discoveryConfidence':score,'confidenceReasons':reasons,
            'componentPath':c.componentPath,'templatePath':c.templatePath,'route':c.route,'routeFile':c.routeFile,'menuKey':c.menuKey,'componentExport':c.componentExport,'remote':c.remote,
            'formDefinitionPath':c.formDefinitionPath,'fieldCount':len(c.fields or []),'fields':c.fields or [],'apiRefs':c.apiRefs or [],'dbRefs':c.dbRefs or [],
            'sourceFiles':src,'sourceCategories':cats,'evidence':c.evidence or [],
            'identityKey':'::'.join(_canonical_key(c)),
            # Earlier IDs of merged artifacts, so downstream knowledge keyed by an
            # old ID (e.g. INVENTORY_ASSEMBLYBOM) can still be found.
            'aliasScreenIds':sorted(alias_by_obj.get(id(c),set())-{c.screenId}),
            'discoveryPrior':c.confidence,
        }
        registry.append(rec); counts[status]+=1
    report={'phase':'0.13.0','status':'COMPLETED','inputCandidates':len(candidates),'uniqueScreens':len(registry),'duplicatesConsolidated':duplicates,'statusCounts':counts,
            'confirmedCriteria':'CONFIRMED needs a strong signal (GB form registry, form definition or menu evidence) and an evidence score >= 0.70; LIKELY needs >= 0.50; route/component alone is CANDIDATE.','registry':registry}
    return registry,report

def run_screen_identity(discovery_json: str, output_dir: str):
    data=json.loads(Path(discovery_json).read_text(encoding='utf-8'))
    candidates=[]
    for raw in data.get('screens',[]):
        candidates.append(ScreenCandidate(screenId=raw.get('screenId',''),screenName=raw.get('screenName',''),module=raw.get('module',''),functionality=raw.get('functionality',''),feature=raw.get('feature',''),project=raw.get('project',''),componentPath=raw.get('componentPath',''),templatePath=raw.get('templatePath',''),route=raw.get('route',''),routeFile=raw.get('routeFile',''),menuKey=raw.get('menuKey',''),componentExport=raw.get('componentExport',''),remote=raw.get('remote',''),confidence=raw.get('confidence',0),evidence=raw.get('evidence',[]),apiRefs=raw.get('apiRefs',[]),dbRefs=raw.get('dbRefs',[]),formDefinitionPath=raw.get('formDefinitionPath',''),fields=raw.get('fields',[])))
    registry,report=consolidate(candidates)
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    (out/'screen_registry_v13.json').write_text(json.dumps(registry,indent=2,ensure_ascii=False),encoding='utf-8')
    (out/'screen_identity_report.json').write_text(json.dumps({k:v for k,v in report.items() if k!='registry'},indent=2,ensure_ascii=False),encoding='utf-8')
    # Review queue contains only non-confirmed identities; no automatic invention of screens.
    review=[{'reviewId':'SCR-'+hashlib.sha1(r['screenId'].encode()).hexdigest()[:10].upper(),'screenId':r['screenId'],'screenName':r['screenName'],'module':r['module'],'status':r['status'],'confidence':r['discoveryConfidence'],'reasons':r['confidenceReasons'],'componentPath':r['componentPath'],'route':r['route']} for r in registry if r['status']!='CONFIRMED']
    (out/'screen_identity_review_queue.json').write_text(json.dumps(review,indent=2,ensure_ascii=False),encoding='utf-8')
    return report
