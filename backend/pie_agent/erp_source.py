from __future__ import annotations
import hashlib, json, os, re, shutil, subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

IGNORED_DIRS={'.git','node_modules','dist','build','coverage','.angular','.idea','.vscode','bin','obj','out','TestResults','packages','__pycache__','.pytest_cache'}
TEXT_EXT={'.ts','.tsx','.js','.jsx','.html','.scss','.css','.cs','.sql','.json','.md','.xml','.yml','.yaml','.config','.txt'}

@dataclass
class SourceFile:
    path:str
    category:str
    project:str=''
    module:str=''
    sizeBytes:int=0
    sha256:str=''

@dataclass
class ScreenCandidate:
    screenId:str
    screenName:str
    module:str
    functionality:str
    feature:str
    project:str
    componentPath:str=''
    templatePath:str=''
    route:str=''
    routeFile:str=''
    menuKey:str=''
    componentExport:str=''
    remote:str=''
    confidence:float=0.0
    evidence:list[dict[str,Any]]|None=None
    apiRefs:list[dict[str,Any]]|None=None
    dbRefs:list[str]|None=None
    formDefinitionPath:str=''
    fields:list[dict[str,Any]]|None=None


def iter_files(root:Path):
    if not root.exists(): return
    for p in root.rglob('*'):
        if not p.is_file(): continue
        try: rel=p.relative_to(root)
        except ValueError: continue
        if any(part in IGNORED_DIRS for part in rel.parts): continue
        if p.suffix.lower() not in TEXT_EXT: continue
        yield p

def read_text(path:Path, limit:int=300_000)->str:
    try:
        with path.open('r',encoding='utf-8',errors='ignore') as f: return f.read(limit)
    except Exception: return ''

def sha256(path:Path)->str:
    h=hashlib.sha256()
    try:
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
        return h.hexdigest()
    except Exception: return ''

def project_from_path(rel:Path)->str:
    parts=rel.parts
    if len(parts)>=2 and parts[0]=='projects': return parts[1]
    return parts[0] if parts else ''

def module_from_path(rel:Path)->str:
    parts=[p for p in rel.parts]
    if 'projects' in parts:
        i=parts.index('projects')
        if i+1<len(parts): return parts[i+1]
    return ''

def scan_source_inventory(root:Path, category:str)->list[SourceFile]:
    result=[]
    for p in iter_files(root):
        rel=p.relative_to(root)
        result.append(SourceFile(str(rel).replace('\\','/'),category,project_from_path(rel),module_from_path(rel),p.stat().st_size,sha256(p)))
    return result

def clean_name(s:str)->str:
    s=re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',s)
    s=re.sub(r'[_\-/]+',' ',s)
    s=re.sub(r'\.(component|page|screen|view|form)$','',s,flags=re.I)
    return re.sub(r'\s+',' ',s).strip(' ._-:').title()

def stable_id(module:str, feature:str, component:str='')->str:
    raw=f'{module}.{feature}'
    if component: raw += '.'+component
    return re.sub(r'[^A-Za-z0-9]+','_',raw).strip('_').upper()[:160]

def infer_feature_from_component(path:Path, text:str)->str:
    stem=path.stem
    if stem.lower().endswith('component'): stem=stem[:-9]
    # prefer explicit class name
    m=re.search(r'export\s+(?:default\s+)?class\s+(\w+)',text)
    if m: stem=re.sub(r'(Component|Page|Screen|View)$','',m.group(1))
    return clean_name(stem) or 'Unclassified'

def infer_functionality(module:str, feature:str, text:str)->str:
    s=(module+' '+feature+' '+text[:5000]).lower()
    known={'ess':'ESS','hrms':'HRMS','admin':'Administration','accounts':'Accounts','finance':'Finance','sales':'Sales','purchase':'Purchase','inventory':'Inventory','production':'Production','security':'Security','crmservice':'CRM Service','crmsales':'CRM Sales','analytics':'Analytics','projects':'Projects','qms':'QMS','tcms':'TCMS','maintenance':'Maintenance','subcontract':'Subcontract','planning':'Planning','fam':'Fixed Assets','costing':'Costing','competencemanagement':'Competency Management','recruitment':'Recruitment','training':'Training','wms':'WMS','cms':'CMS','apiplatform':'API Platform','automationhub':'Automation Hub'}
    if module.lower() in known: return known[module.lower()]
    for k,v in known.items():
        if k in s:return v
    return clean_name(module) or 'Unclassified'

def discover_angular(root:Path)->tuple[list[ScreenCandidate],dict[str,Any]]:
    registry=[]
    registry_path=root/'features/gbformviewer/gbformviewer.registry.json'
    # Build a class/export index first. The registry also contains shared widgets/directives
    # (GBINPUT, PICKLIST, etc.); only registry entries that resolve to a real project component
    # are promoted to screen candidates.
    project_components={}
    for cp in root.glob('projects/*/**/*.component.ts'):
        if any(x in IGNORED_DIRS for x in cp.parts): continue
        tx=read_text(cp,120000)
        cm=re.search(r'export\s+class\s+(\w+)',tx)
        if cm: project_components.setdefault(cm.group(1).lower(),cp)
        project_components.setdefault(cp.stem.replace('.component','').lower(),cp)
    if registry_path.exists():
        try:
            d=json.loads(registry_path.read_text(encoding='utf-8'))
            for key,val in d.items():
                remote=val.get('remote',''); expose=val.get('exposeKey',''); export=val.get('exportName','')
                candidates=[export,key,Path(expose).name]
                cp=None
                for cand in candidates:
                    if cand and cand.lower() in project_components: cp=project_components[cand.lower()]; break
                if cp is None: continue
                feature=clean_name(re.sub(r'Component$','',export or key)) or clean_name(key)
                module=remote or module_from_path(cp.relative_to(root)) or 'unknown'
                sid=stable_id(module,feature,export or key)
                registry.append(ScreenCandidate(sid,feature,module,infer_functionality(module,feature,''),feature,project_from_path(cp.relative_to(root)),componentPath=str(cp.relative_to(root)).replace('\\','/'),remote=remote,componentExport=export or key,confidence=.97,evidence=[{'source':str(registry_path.relative_to(root)).replace('\\','/'),'fact':f'registered component {key} -> {remote}:{expose}'}],apiRefs=[],dbRefs=[]))
        except Exception: pass
    by_component={}
    for p in root.rglob('*.component.ts'):
        if any(x in IGNORED_DIRS for x in p.parts): continue
        rel=p.relative_to(root)
        txt=read_text(p)
        feature=infer_feature_from_component(p,txt)
        project=project_from_path(rel); module=module_from_path(rel)
        if not project: continue
        functionality=infer_functionality(module,feature,txt)
        sid=stable_id(module,feature)
        template=''
        mt=re.search(r'templateUrl\s*:\s*[`\'\"]([^`\'\"]+)',txt)
        if mt:
            template=str((p.parent/mt.group(1)).resolve().relative_to(root.resolve())).replace('\\','/') if (p.parent/mt.group(1)).exists() else mt.group(1)
        cls=re.search(r'export\s+class\s+(\w+)',txt)
        c=ScreenCandidate(sid,feature,module,functionality,feature,project, str(rel).replace('\\','/'),template,'','', '',cls.group(1) if cls else '', '',.78,[{'source':str(rel).replace('\\','/'),'fact':'Angular component candidate'}],[],[])
        # component path is strongest for menu-driven GB5 forms.
        by_component[c.componentPath]=c
    # routes, especially explicit dev/test routes
    for rp in list(root.rglob('*.routes.ts'))+list(root.rglob('app.routes.ts')):
        if any(x in IGNORED_DIRS for x in rp.parts): continue
        txt=read_text(rp)
        for m in re.finditer(r'path\s*:\s*[\'\"]([^\'\"]+)[\'\"][\s\S]{0,1200}?import\s*\([\'\"]([^\'\"]+)[\'\"]\)[\s\S]{0,500}?(?:then\s*\([^=]+=>\s*\([^)]*\)\s*=>\s*([^,}\n]+)|then\s*\([^)]*=>\s*([^.)]+))?',txt):
            route=m.group(1); imp=m.group(2); relimp=imp
            matches=[c for c in by_component.values() if Path(c.componentPath).name.replace('.component.ts','') in Path(imp).stem or Path(c.componentPath).stem.replace('.component','') in Path(imp).stem]
            if matches:
                for c in matches: c.route=route; c.routeFile=str(rp.relative_to(root)).replace('\\','/'); c.confidence=max(c.confidence,.94); c.evidence.append({'source':c.routeFile,'fact':f'route {route}'})
    # merge registry components with filesystem components by export name / stem
    reg_by_key={r.componentExport.lower():r for r in registry}
    for c in by_component.values():
        found=None
        for r in registry:
            if c.componentExport and r.componentExport.lower()==c.componentExport.lower(): found=r; break
            if r.screenName.lower()==c.screenName.lower() and r.module.lower()==c.module.lower(): found=r; break
        if found:
            found.componentPath=c.componentPath; found.templatePath=c.templatePath; found.route=c.route; found.routeFile=c.routeFile; found.project=c.project; found.confidence=max(found.confidence,c.confidence); found.evidence=(found.evidence or [])+(c.evidence or [])
        else: registry.append(c)
    registry=dedupe_screens(registry)
    registry, form_files, form_added = enrich_with_formjson(root, registry)
    return registry, {'registryFile':str(registry_path.relative_to(root)).replace('\\','/') if registry_path.exists() else None,'componentCount':len(by_component),'formJsonFiles':form_files,'formOnlyScreensAdded':form_added}

def dedupe_screens(items):
    out={}
    for x in items:
        key=(x.module.lower(),x.screenName.lower(),x.componentExport.lower())
        if key not in out: out[key]=x
        else:
            y=out[key]; y.confidence=max(y.confidence,x.confidence); y.evidence=(y.evidence or [])+(x.evidence or [])
            for a in ('componentPath','templatePath','route','routeFile','project','remote'):
                if not getattr(y,a) and getattr(x,a): setattr(y,a,getattr(x,a))
    return list(out.values())

def enrich_with_formjson(root:Path, screens:list[ScreenCandidate]):
    by_key={(s.module.lower(),s.feature.lower()):s for s in screens}
    by_name={(s.module.lower(),s.screenName.lower()):s for s in screens}
    added=[]; form_files=0
    for p in root.glob('projects/*/public/formjson/**/*.json'):
        if any(x in IGNORED_DIRS for x in p.parts): continue
        try: data=json.loads(read_text(p,500000))
        except Exception: continue
        fields=data.get('ObjectFields') if isinstance(data,dict) else None
        if not isinstance(fields,list) or not fields: continue
        form_files += 1
        rel=p.relative_to(root); module=module_from_path(rel); feature=clean_name(p.stem); key=(module.lower(),feature.lower())
        target=by_key.get(key) or by_name.get((module.lower(),feature.lower()))
        if target is None:
            fn=infer_functionality(module,feature,'')
            target=ScreenCandidate(stable_id(module,feature,p.stem),feature,module,fn,feature,project_from_path(rel),confidence=.84,evidence=[],apiRefs=[],dbRefs=[])
            screens.append(target); by_key[key]=target; by_name[(module.lower(),feature.lower())]=target; added.append(target)
        target.formDefinitionPath=str(rel).replace('\\','/')
        target.fields=[{k:v for k,v in f.items() if k in {'Name','Type','Label','Required','DefaultValue','ReadOnly','MaxLength','PicklistJSON','PicklistCriteria','LinkId','Multiple'}} for f in fields if isinstance(f,dict)][:500]
        target.evidence=(target.evidence or [])+[{'source':target.formDefinitionPath,'fact':f'form definition with {len(fields)} ObjectFields'}]
        target.confidence=max(target.confidence,.9)
    return screens, form_files, len(added)

def discover_backend(root:Path)->list[dict[str,Any]]:
    eps=[]
    # Restrict parsing to endpoint paths before reading files. The ERP backend contains
    # ~14k C# files, while only a small subset are API endpoints. This keeps discovery
    # practical for enterprise repositories without changing the evidence rule.
    for p in iter_files(root):
        if p.suffix.lower() != '.cs': continue
        rel=p.relative_to(root)
        rel_s=str(rel).replace('\\','/')
        if '/EndPoints/' not in ('/'+rel_s) and not rel_s.startswith('EndPoints/') and '/Endpoints/' not in ('/'+rel_s) and not rel_s.startswith('Endpoints/'):
            continue
        s=read_text(p)
        methods=list(re.finditer(r'\b(Get|Post|Put|Patch|Delete)\s*\(\s*[\'\"]([^\'\"]+)[\'\"]',s,re.I))
        if not methods: continue
        cls=(re.search(r'class\s+(\w+)',s) or [None,p.stem])[1]
        ns=(re.search(r'namespace\s+([\w.]+)',s) or [None,''])[1]
        module=''
        parts=rel.parts
        for i,part in enumerate(parts):
            if part=='GB5Solution' and i+1<len(parts): module=parts[i+1]
        for m in methods:
            route='/'+m.group(2).lstrip('/')
            called=re.findall(r'_\w+\s*\.\s*(\w+)\s*\(',s)
            dto=re.findall(r'\[property:\s*FromBody\]\s+[\w?\[\]<>]+\s+(\w+)',s)
            eps.append({'method':m.group(1).upper(),'route':route,'className':cls,'namespace':ns,'module':module,'file':str(rel).replace('\\','/'),'bllMethods':sorted(set(called)),'bodyParams':dto,'confidence':.96})
    seen={};
    for e in eps: seen.setdefault((e['method'],e['route']),e)
    return list(seen.values())

def map_apis(screens:list[ScreenCandidate], endpoints:list[dict[str,Any]], root:Path):
    # Pre-index endpoints once. Avoid O(screens * all_endpoints) regex work on large ERPs.
    ep_pre=[]
    by_module={}
    for ep in endpoints:
        token_text=(ep.get('route','')+' '+ep.get('className','')+' '+' '.join(ep.get('bllMethods',[]))).lower()
        item=(ep,set(re.findall(r'[a-z0-9]+',token_text)))
        ep_pre.append(item)
        mod=str(ep.get('module','')).lower()
        by_module.setdefault(mod,[]).append(item)

    for c in screens:
        source_paths=[]
        if c.componentPath:
            cp=root/c.componentPath
            source_paths.append(cp)
            txt=read_text(cp,180000)
            for imp in re.findall(r"from\s*[\'\"]([^\'\"]+service[^\'\"]*)[\'\"]",txt,re.I):
                if imp.startswith('.'):
                    base=(cp.parent/imp)
                    for candidate in [base,Path(str(base)+'.ts'),base/'index.ts']:
                        if candidate.exists(): source_paths.append(candidate); break
        literals=set()
        for p in source_paths:
            txt=read_text(p,180000)
            literals.update(x.lower() for x in re.findall(r'[\'\"](/[^\'\"]{2,120})[\'\"]',txt))
        tokens=set(re.findall(r'[a-z0-9]+',(c.screenName+' '+c.module+' '+c.feature).lower()))
        # Prefer same-module endpoints; if no module match, use a small global fallback.
        pool=by_module.get(c.module.lower(), []) or ep_pre
        candidates=[]
        for ep, et in pool:
            route=ep['route'].lower()
            score=0.0
            if any(route in lit or lit in route for lit in literals): score=.98
            overlap=len(tokens & et)
            if overlap: score=max(score,min(.82,.14*overlap))
            if score>=.28:
                candidates.append({**ep,'score':round(score,3),'matchedSource':str(source_paths[0].relative_to(root)).replace('\\','/') if source_paths else ''})
        # De-duplicate and retain strongest mappings.
        seen={}
        for e in candidates:
            key=(e.get('method'),e.get('route'))
            if key not in seen or e['score']>seen[key]['score']: seen[key]=e
        c.apiRefs=sorted(seen.values(),key=lambda x:x['score'],reverse=True)[:20]
    return screens

def map_db_refs(screens, db_objects:list[dict[str,Any]]):
    for c in screens:
        tokens=set(re.findall(r'[a-z0-9]+',(c.screenName+' '+c.feature+' '+c.module).lower()))
        scored=[]
        for o in db_objects:
            name=o.get('name','')
            ot=set(re.findall(r'[a-z0-9]+',name.lower()))
            overlap=len(tokens&ot)
            if overlap: scored.append((overlap,name))
        c.dbRefs=[n for _,n in sorted(scored,reverse=True)[:20]]
    return screens

def write_json(path:Path,obj):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(obj,indent=2,ensure_ascii=False),encoding='utf-8')
