from __future__ import annotations
import json, os, hashlib
from pathlib import Path
from .erp_source import scan_source_inventory, discover_angular, discover_backend, map_apis, map_db_refs, write_json
from .db_discovery import scan_sqlserver

def run_erp_discovery(frontend_root:str, backend_root:str, output_dir:str, database_url:str|None=None):
    fe=Path(frontend_root).resolve(); be=Path(backend_root).resolve(); out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    fe_files=scan_source_inventory(fe,'Frontend Code') if fe.exists() else []
    be_files=scan_source_inventory(be,'Backend Code') if be.exists() else []
    screens,fe_meta=discover_angular(fe) if fe.exists() else ([],{})
    endpoints=discover_backend(be) if be.exists() else []
    screens=map_apis(screens,endpoints,fe)
    db=scan_sqlserver(database_url)
    screens=map_db_refs(screens,db.get('objects',[]))
    for s in screens:
        s.evidence=(s.evidence or [])
        if s.componentPath:s.evidence.append({'source':s.componentPath,'fact':'frontend component'})
        if s.route:s.evidence.append({'source':s.routeFile,'fact':f'route={s.route}'})
        if s.apiRefs:s.evidence.append({'source':'backend endpoint scan','fact':f'{len(s.apiRefs)} candidate API mappings'})
        if s.dbRefs:s.evidence.append({'source':'SQL Server metadata','fact':f'{len(s.dbRefs)} candidate DB objects'})
        s.evidence=list({(e.get('source'),e.get('fact')):e for e in s.evidence}.values())
    screen_json=[]
    for s in screens:
        d=s.__dict__.copy(); screen_json.append(d)
    report={
      'generationVersion':'0.12.0','status':'COMPLETED','frontendRoot':str(fe),'backendRoot':str(be),
      'frontend':{'files':len(fe_files),'projects':sorted({x.project for x in fe_files if x.project}),'metadata':fe_meta},
      'backend':{'files':len(be_files),'endpoints':len(endpoints)},
      'database':{k:v for k,v in db.items() if k!='objects'},
      'totalScreens':len(screens),
      'screens':screen_json,
      'generatedAt':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
    }
    write_json(out/'erp_discovery_report.json',report)
    write_json(out/'screen_registry.json',screen_json)
    write_json(out/'frontend_inventory.json',[x.__dict__ for x in fe_files])
    write_json(out/'backend_inventory.json',[x.__dict__ for x in be_files])
    write_json(out/'backend_endpoint_registry.json',endpoints)
    write_json(out/'database_registry.json',db)
    # A compact mapping file is useful for downstream generation/KMS.
    mapping=[]
    for s in screens:
        mapping.append({'screenId':s.screenId,'screenName':s.screenName,'module':s.module,'functionality':s.functionality,'feature':s.feature,'frontend':{'component':s.componentPath,'template':s.templatePath,'route':s.route,'routeFile':s.routeFile},'backend':s.apiRefs or [],'database':s.dbRefs or [],'confidence':s.confidence})
    write_json(out/'screen_source_mapping.json',mapping)
    return report
