from __future__ import annotations
import json, hashlib
from pathlib import Path
from datetime import datetime, timezone
from .models import ScreenRegistryEntry, KnowledgePackage, KnowledgeRecord, Evidence
from .erp_discovery import run_erp_discovery

def _ev(screen, source, fact, confidence=.8):
    return Evidence(evidence_id=hashlib.sha1((screen.screenId+source+fact).encode()).hexdigest()[:16],source_file=source,source_type='ERP Source',locator='static-discovery',quote_or_fact=fact,evidence_type='observed',confidence=confidence)

def _record(screen, typ, title, content, evidence, confidence=.8, review=False):
    kid='KNO-'+hashlib.sha1(f'{screen.screenId}|{typ}|{title}'.encode()).hexdigest()[:14]
    return KnowledgeRecord(knowledgeId=kid,screenId=screen.screenId,module=screen.module,functionality=screen.functionality,feature=screen.feature,knowledgeType=typ,title=title,content=content,confidence=confidence,needsReview=review,evidence=evidence)

def generate_from_erp(frontend_root, backend_root, output_dir, database_url=None):
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    report=run_erp_discovery(frontend_root,backend_root,str(out/'discovery'),database_url)
    packages=[]
    kd=out/'knowledge'; kd.mkdir(exist_ok=True)
    for raw in report['screens']:
        screen=ScreenRegistryEntry(screenId=raw['screenId'],module=raw.get('module',''),functionality=raw.get('functionality',''),feature=raw.get('feature',''),screenName=raw.get('screenName',''),status='READY' if raw.get('confidence',0)>=.8 else 'DISCOVERED',discoveryConfidence=raw.get('confidence',0),sourceFiles=[x for x in [raw.get('componentPath'),raw.get('templatePath'),raw.get('routeFile')] if x],sourceCategories=['Frontend Code'] if raw.get('componentPath') else [])
        if raw.get('formDefinitionPath'):
            screen.sourceFiles.append(raw['formDefinitionPath']); screen.sourceCategories.append('Frontend Form Definition')
        ev=[]
        for e in raw.get('evidence',[]): ev.append(_ev(screen,e.get('source',''),e.get('fact',''),raw.get('confidence',.8)))
        if raw.get('apiRefs'): ev.append(_ev(screen,'backend_endpoint_registry.json',f"Mapped {len(raw['apiRefs'])} candidate backend endpoint(s).",.75))
        if raw.get('dbRefs'): ev.append(_ev(screen,'database_registry.json',f"Mapped {len(raw['dbRefs'])} candidate database object(s).",.7))
        records=[]
        if raw.get('fields'):
            records.append(_record(screen,'FormDefinition','Form Fields',f"Detected {len(raw['fields'])} form field definition(s) in {raw.get('formDefinitionPath','')}",[_ev(screen,raw.get('formDefinitionPath',''),'Angular form definition.',.9)],.9,False))
        records.append(_record(screen,'Screen','Screen Identity',f"Discovered '{screen.screenName}' in {screen.module}.",ev,screen.discoveryConfidence,False))
        if raw.get('route'): records.append(_record(screen,'Route','Application Route',raw['route'],[_ev(screen,raw.get('routeFile',''),'Explicit Angular route.',.95)],.95,False))
        if raw.get('apiRefs'):
            routes=', '.join(f"{x['method']} {x['route']}" for x in raw['apiRefs'][:10])
            records.append(_record(screen,'API Mapping','Candidate APIs',routes,[_ev(screen,'backend_endpoint_registry.json','Static endpoint mapping.',.75)],.75,True))
        if raw.get('dbRefs'):
            records.append(_record(screen,'Database Mapping','Candidate DB Objects',', '.join(raw['dbRefs'][:20]),[_ev(screen,'database_registry.json','Name/token based DB metadata mapping.',.7)],.7,True))
        pkg=KnowledgePackage(screen=screen,records=records,generatedAt=datetime.now(timezone.utc).isoformat(),sourceHash=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest(),status='REVIEW_REQUIRED' if any(r.needsReview for r in records) else 'READY')
        packages.append(pkg); (kd/f'{screen.screenId}.json').write_text(json.dumps(pkg.model_dump(mode='json'),indent=2,ensure_ascii=False),encoding='utf-8')
    manifest={'generationVersion':'0.13.0','status':'COMPLETED','totalScreens':len(packages),'knowledgePackages':[p.model_dump(mode='json') for p in packages]}
    (out/'knowledge_manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    return report,packages
