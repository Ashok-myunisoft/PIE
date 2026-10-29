from __future__ import annotations
import json, re, hashlib
from pathlib import Path
from typing import Any
from .erp_source import iter_files, read_text, IGNORED_DIRS, write_json

HTTP_CALL_RE = re.compile(r'\b(this\.)?(\w+)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*[`\'\"]([^`\'\"]+)', re.I)
HTTP_DIRECT_RE = re.compile(r'\b(?:http|httpClient)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*[`\'\"]([^`\'\"]+)', re.I)
ROUTE_LITERAL_RE = re.compile(r'[\'\"](/[^\'\"]{2,180})[\'\"]')
ENDPOINT_ROUTE_RE = re.compile(r'\b(Get|Post|Put|Patch|Delete)\s*\(\s*[\'\"]([^\'\"]+)', re.I)
CLASS_RE = re.compile(r'\bclass\s+(\w+)\s*(?::\s*([^\{]+))?')
INJECT_RE = re.compile(r'(?:private|public|protected|readonly)?\s*(?:readonly\s+)?(\w+)\s+(_\w+)\s*(?:,|\))')
BYPASS_RE = re.compile(r'\b(?:Http|HttpClient)\s*\.\s*(Get|Post|Put|Patch|Delete)\s*<', re.I)
FIELD_DEP_RE = re.compile(r'\b(?:private|protected|public|internal)\s+(?:readonly\s+)?([A-Za-z_][\w.]*(?:<[\w<>.,\s?]*>)?\??)\s+(_[A-Za-z_]\w*)\s*[;=]')
FORM_ACTION_RE = re.compile(r'public\s+static\s+(\w+)\s*=\s*[`\'"]([^`\'"]*)[`\'"]')
FORM_NAME_RES = (
    re.compile(r'GBBaseFormGroup\s*\(\s*this\.injector\s*,\s*[\'"](\w+)[\'"]'),
    re.compile(r'\.form(?:load|save|delete)service\s*\(\s*[\'"](\w+)[\'"]'),
)
FORM_ACTION_FILES = {'get': 'geturl.ts', 'save': 'saveurl.ts', 'delete': 'deleteurl.ts'}
FORM_ACTION_DIR = 'projects/gbhost/public/formactionurls'
# Frontend module name -> backend GB5Solution folder, where the two differ.
# Only verified pairs belong here; extend as other modules are checked.
MODULE_ALIASES = {'inventory': {'mm'}}


def module_matches(screen_module: str, endpoint_module: str) -> bool:
    sm, em = _norm(screen_module), _norm(endpoint_module)
    if not sm or not em:
        return False
    return sm in em or em in MODULE_ALIASES.get(sm, set())


def endpoint_module_entity(rel: str) -> tuple[str, str]:
    """GB5Solution/MM/MMSL/EndPoints/ItemCategory/X.cs -> ('MM', 'ItemCategory')."""
    parts = rel.replace('\\', '/').split('/')
    module = ''
    if 'GB5Solution' in parts:
        i = parts.index('GB5Solution')
        module = parts[i + 1] if i + 1 < len(parts) else ''
    elif parts and parts[0] == 'GB5Framework':
        module = 'framework'
    entity = ''
    low = [p.lower() for p in parts]
    if 'endpoints' in low:
        i = low.index('endpoints')
        # Only a folder between EndPoints/ and the file is an entity.
        if i + 2 < len(parts):
            entity = parts[i + 1]
    return module, entity


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()

def _tokens(s: str) -> set[str]:
    return set(re.findall(r'[a-z0-9]+', _norm(s)))

def _rel(root: Path, p: Path) -> str:
    try: return p.relative_to(root).as_posix()
    except Exception: return str(p).replace('\\','/')

def _confidence(score: float) -> str:
    return 'HIGH' if score >= .80 else ('MEDIUM' if score >= .55 else 'LOW')

def discover_backend_service_catalog(root: Path) -> list[dict[str, Any]]:
    """Discover endpoint -> injected BLL/service -> method evidence from C# FastEndpoints."""
    rows=[]
    for p in iter_files(root):
        if p.suffix.lower() != '.cs': continue
        rel=_rel(root,p)
        if '/endpoints/' not in ('/'+rel.lower()) and not rel.lower().startswith('endpoints/'):
            continue
        txt=read_text(p,240000)
        route_matches=list(ENDPOINT_ROUTE_RE.finditer(txt))
        if not route_matches: continue
        cls_m=CLASS_RE.search(txt)
        class_name=cls_m.group(1) if cls_m else p.stem
        base=cls_m.group(2).strip() if cls_m and cls_m.group(2) else ''
        namespace=(re.search(r'\bnamespace\s+([\w.]+)',txt) or [None,''])[1]
        # Injected dependencies are held in `_field` members. GB5 constructors take
        # parameters without the underscore (`GetX(IXBLL bll) => _bll = bll;`), so the
        # field declarations are the reliable evidence, with underscore-prefixed
        # constructor parameters kept as a secondary form.
        deps=[]; seen_vars=set()
        for typ,var in FIELD_DEP_RE.findall(txt):
            if var not in seen_vars:
                seen_vars.add(var); deps.append({'type':typ.strip(),'variable':var})
        for cm in re.finditer(r'\b(?:public|protected|private)\s+'+re.escape(class_name)+r'\s*\((.*?)\)',txt,re.S):
            for typ,var in re.findall(r'([A-Za-z_][\w<>.?\[\], ]*)\s+(_[A-Za-z_]\w*)',cm.group(1)):
                if var not in seen_vars:
                    seen_vars.add(var); deps.append({'type':typ.strip(),'variable':var})
        module, entity = endpoint_module_entity(rel)
        calls=[]
        for var,method in re.findall(r'\b(_[A-Za-z_]\w*)\s*\.\s*(\w+)\s*\(',txt):
            calls.append({'variable':var,'method':method})
        for rm in route_matches:
            method=rm.group(1).upper(); route='/'+rm.group(2).lstrip('/')
            dep_calls=[]
            for call in calls:
                dep=next((d for d in deps if d['variable']==call['variable']),None)
                if dep: dep_calls.append({'serviceType':dep['type'],'serviceVariable':call['variable'],'method':call['method']})
            rows.append({
                'method':method,'route':route,'className':class_name,'namespace':namespace,
                'module':module,'entity':entity,
                'file':rel,'baseType':base,'dependencies':deps,'serviceCalls':dep_calls,
                'confidence':.99 if dep_calls else .90
            })
    seen={}
    for r in rows: seen.setdefault((r['method'],r['route']),r)
    return list(seen.values())


def discover_service_implementations(root: Path) -> list[dict[str, Any]]:
    """Build interface/type -> implementation and method catalog from C# source."""
    rows=[]
    for p in iter_files(root):
        if p.suffix.lower() != '.cs': continue
        rel=_rel(root,p).lower()
        # Enterprise repos can contain many C# files. Service implementation discovery
        # only needs BLL/service/interface areas, which keeps this pass tractable.
        if not any(seg in rel for seg in ('bll/', 'service/', 'services/')) and not p.name.lower().endswith(('bll.cs','service.cs')):
            continue
        txt=read_text(p,180000)
        for m in CLASS_RE.finditer(txt):
            cls=m.group(1); bases=(m.group(2) or '')
            interfaces=[x.strip() for x in bases.split(',') if x.strip().startswith('I')]
            if not interfaces and 'BLL' not in str(p):
                continue
            methods=[]
            for mm in re.finditer(r'\b(?:public|protected|internal)\s+(?:async\s+)?[\w<>.?\[\], ]+\s+(\w+)\s*\(',txt):
                methods.append(mm.group(1))
            rows.append({'className':cls,'interfaces':interfaces,'file':_rel(root,p),'methods':sorted(set(methods))})
    return rows


def _looks_like_url(value: str) -> bool:
    return '/' in (value or '')


def _frontend_service_files(root: Path) -> list[Path]:
    return [p for p in iter_files(root) if p.suffix.lower()=='.ts' and ('service' in p.stem.lower() or 'api' in p.stem.lower())]


def discover_frontend_api_calls(root: Path) -> list[dict[str, Any]]:
    rows=[]
    for p in iter_files(root):
        if p.suffix.lower() not in {'.ts','.tsx','.js','.jsx'}: continue
        rel=_rel(root,p); txt=read_text(p,220000)
        # Ignore generated/build-ish content already excluded by iter_files; keep application TS.
        cls=(re.search(r'\bclass\s+(\w+)',txt) or [None,p.stem])[1]
        calls=[]
        for m in HTTP_DIRECT_RE.finditer(txt):
            if _looks_like_url(m.group(2)):
                calls.append({'clientMethod':m.group(1).upper(),'url':m.group(2),'source':'direct-http'})
        for m in HTTP_CALL_RE.finditer(txt):
            # `this.form.get('ItemName')` matches the same shape as an HTTP call;
            # only path-like arguments are API evidence.
            if _looks_like_url(m.group(4)):
                calls.append({'serviceVariable':m.group(2),'clientMethod':m.group(3).upper(),'url':m.group(4),'source':'service-http'})
        # Also capture API-like string constants and common environment base URLs.
        if calls:
            for c in calls:
                c.update({'file':rel,'className':cls})
                rows.append(c)
    # deterministic de-dupe
    seen={}
    for r in rows: seen.setdefault((r['file'],r['clientMethod'],r['url'],r.get('serviceVariable','')),r)
    return list(seen.values())


def _route_tokens(route: str) -> set[str]:
    # Split camel case and URL segments; ignore generic verbs.
    return {x for x in _tokens(route) if x not in {'api','get','post','put','patch','delete','v1','v2'}}


def load_form_action_urls(frontend_root: Path) -> dict[str, list[dict[str, str]]]:
    """Read the GB5 form action URL maps (GetUrl/SaveUrl/DeleteUrl) keyed by form name.

    GB5 form screens do not call URLs directly: `service.formloadservice('assemblybom', id)`
    resolves `GetUrl['assemblybom']`. These maps are therefore exact screen -> API evidence.
    """
    actions: dict[str, list[dict[str, str]]] = {}
    base = frontend_root / FORM_ACTION_DIR
    for kind, fname in FORM_ACTION_FILES.items():
        p = base / fname
        if not p.exists():
            continue
        rel = _rel(frontend_root, p)
        for name, url in FORM_ACTION_RE.findall(read_text(p)):
            if url:
                actions.setdefault(name.lower(), []).append({'kind': kind, 'url': url, 'file': rel, 'formName': name})
    return actions


def screen_form_names(screen: dict[str, Any], frontend_root: Path) -> set[str]:
    names = set()
    fd = screen.get('formDefinitionPath') or ''
    if fd:
        names.add(Path(fd).stem.lower())
    cp = screen.get('componentPath') or ''
    if cp and (frontend_root / cp).exists():
        txt = read_text(frontend_root / cp, 180000)
        for rx in FORM_NAME_RES:
            names.update(x.lower() for x in rx.findall(txt))
    return names


def legacy_url_entity(url: str) -> tuple[str, str]:
    """'/mms/ItemCategory.svc/?ItemCategoryId=' -> ('ItemCategory', '');
    '/mms/PackNumber.svc/SavePutAwayBinLedger' -> ('PackNumber', 'SavePutAwayBinLedger')."""
    m = re.search(r'/(\w+)\.svc(?:/([A-Za-z]\w*))?', url or '')
    if m:
        return m.group(1), m.group(2) or ''
    segs = [s for s in re.split(r'[/?]', url or '') if s and not s.startswith('$')]
    return (segs[0], segs[1] if len(segs) > 1 else '') if segs else ('', '')


def _form_action_endpoints(action: dict[str, str], screen_module: str, by_entity: dict[str, list[dict[str, Any]]]) -> list[tuple[dict[str, Any], float, str]]:
    entity, action_name = legacy_url_entity(action['url'])
    pool = by_entity.get(entity.lower(), [])
    same_module = [ep for ep in pool if module_matches(screen_module, ep.get('module', ''))]
    pool = same_module or pool
    if action_name:
        exact = [ep for ep in pool if ep.get('className', '').lower() == action_name.lower()]
        if exact:
            return [(ep, .95, f"form action {action['kind']} URL {action['url']} names endpoint {ep['className']}") for ep in exact]
    kind = action['kind']
    ent = entity.lower()
    def verb_ok(ep):
        cls = ep.get('className', '').lower()
        if kind == 'get':
            return ep['method'] == 'GET' and cls.startswith('get') and 'selectlist' not in cls
        if kind == 'save':
            return cls.startswith('save') or (ep['method'] in {'POST', 'PUT'} and cls.startswith(('create', 'update')))
        return ep['method'] == 'DELETE' or cls.startswith('delete')
    hits = [ep for ep in pool if verb_ok(ep)]
    # Prefer the canonical Get<Entity>/Save<Entity>/Delete<Entity> endpoint when present.
    canonical = [ep for ep in hits if ep.get('className', '').lower() == kind + ent]
    hits = canonical or hits[:3]
    score = .92 if canonical else .82
    return [(ep, score, f"form action {kind} URL {action['url']} -> endpoint entity {ep.get('entity')}") for ep in hits]


def map_screen_to_api_backend(registry: list[dict[str, Any]], endpoint_rows: list[dict[str, Any]], frontend_calls: list[dict[str, Any]], implementations: list[dict[str, Any]], form_actions: dict[str, list[dict[str, str]]] | None = None, frontend_root: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Map screens to APIs and APIs to backend services using indexed evidence."""
    form_actions = form_actions or {}
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for ep in endpoint_rows:
        if ep.get('entity'):
            by_entity.setdefault(ep['entity'].lower(), []).append(ep)
    impl_by_interface={}; impl_by_class={}
    for i in implementations:
        impl_by_class.setdefault(i['className'].lower(),[]).append(i)
        for iface in i.get('interfaces',[]): impl_by_interface.setdefault(iface.lower(),[]).append(i)

    # Inverted indexes avoid scanning thousands of endpoints/calls for every screen.
    ep_index={}; fe_index={}
    for ep in endpoint_rows:
        toks=_route_tokens(ep['route']+' '+ep.get('className','')+' '+' '.join(x.get('method','') for x in ep.get('serviceCalls',[])))
        for t in toks: ep_index.setdefault(t,[]).append(ep)
    for fc in frontend_calls:
        toks=_route_tokens(fc.get('url',''))
        for t in toks: fe_index.setdefault(t,[]).append(fc)

    out=[]; stats={'screens':len(registry),'screensWithApis':0,'exactMappings':0,'formActionMappings':0,'unresolvedFormActions':0,'candidateMappings':0,'unmappedScreens':0,'highConfidence':0,'frontendCalls':len(frontend_calls),'endpointCount':len(endpoint_rows)}
    for screen in registry:
        stokens=_tokens(' '.join(str(screen.get(k,'')) for k in ('screenName','module','functionality','feature','route')))
        module=screen.get('module','')
        source_files={str(x).lower() for x in (screen.get('sourceFiles') or [])}
        # Gather only endpoints sharing at least one semantic token; same-module endpoints are added as a bounded fallback.
        candidates={}
        # Form action URL maps: structural evidence that outranks name overlap.
        form_hits: dict[tuple[str,str], tuple[float,str,dict]] = {}
        forms = screen_form_names(screen, frontend_root) if frontend_root is not None else set()
        unresolved_actions=[]
        for form in sorted(forms):
            for action in form_actions.get(form, []):
                hits=_form_action_endpoints(action, module, by_entity)
                if not hits:
                    # Typically a legacy WCF (.svc) service with no FastEndpoints
                    # equivalent yet: a migration gap, not a mapping to guess at.
                    unresolved_actions.append({**action,'entity':legacy_url_entity(action['url'])[0]})
                for ep, score, reason in hits:
                    key=(ep['method'],ep['route'])
                    candidates[key]=ep
                    if key not in form_hits or score > form_hits[key][0]:
                        form_hits[key]=(score, reason, action)
        for tok in stokens:
            for ep in ep_index.get(tok,[]): candidates[(ep['method'],ep['route'])]=ep
        # Route can be the strongest key even if names do not overlap.
        if screen.get('route'):
            rt=str(screen['route']).rstrip('/').lower()
            for ep in endpoint_rows:
                if ep['route'].rstrip('/').lower()==rt: candidates[(ep['method'],ep['route'])]=ep
        # Frontend API evidence, restricted by screen source files where possible.
        related_fe=[]
        fc_candidates={}
        for tok in stokens:
            for fc in fe_index.get(tok,[]): fc_candidates[(fc['file'],fc['clientMethod'],fc['url'])]=fc
        for fc in fc_candidates.values():
            if source_files and str(fc.get('file','')).lower() in source_files:
                related_fe.append(fc)
            elif not source_files:
                related_fe.append(fc)
        for fc in related_fe:
            url=fc.get('url','').rstrip('/').lower()
            for ep in endpoint_rows:
                if ep['method']==fc['clientMethod'] and (url==ep['route'].rstrip('/').lower() or ep['route'].rstrip('/').lower() in url or url in ep['route'].rstrip('/').lower()):
                    candidates[(ep['method'],ep['route'])]=ep

        api_rows=[]
        for ep in candidates.values():
            etokens=_route_tokens(ep['route']+' '+ep.get('className','')+' '+' '.join(x.get('method','') for x in ep.get('serviceCalls',[])))
            overlap=len(stokens & etokens)
            name_score=overlap/max(1,len(stokens))
            module_score=1.0 if module_matches(module, ep.get('module','')) else 0.0
            route_exact=any(fc['clientMethod']==ep['method'] and (fc['url'].rstrip('/').lower()==ep['route'].rstrip('/').lower() or ep['route'].rstrip('/').lower() in fc['url'].rstrip('/').lower() or fc['url'].rstrip('/').lower() in ep['route'].rstrip('/').lower()) for fc in related_fe)
            best=0.0; reasons=[]; form_action=None
            key=(ep['method'],ep['route'])
            if route_exact:
                best=.98; reasons.append('frontend URL matches backend route'); stats['exactMappings']+=1
            elif key in form_hits:
                best, reason, form_action = form_hits[key]
                reasons.append(reason); stats['formActionMappings']+=1
            else:
                if module_score: best=max(best,.50); reasons.append('same module')
                if name_score: best=max(best,min(.78,.22+name_score*.70)); reasons.append(f'name/token overlap={name_score:.2f}')
                if related_fe and any(_route_tokens(fc.get('url','')) & etokens for fc in related_fe):
                    best=max(best,.62); reasons.append('frontend API call evidence')
            if best < .40: continue
            # With structural form-action evidence, weak name/module guesses add noise.
            if form_hits and not form_action and not route_exact and best < .55: continue
            service_calls=ep.get('serviceCalls',[]); service_links=[]
            for sc in service_calls:
                candidates_impl=impl_by_interface.get(sc['serviceType'].lower(),[]) + impl_by_class.get(sc['serviceType'].lower(),[])
                for impl in candidates_impl:
                    method_match=sc['method'] in impl.get('methods',[])
                    service_links.append({**impl,'interface':sc['serviceType'],'endpointMethod':sc['method'],'methodFound':method_match,'confidence':.98 if method_match else .78})
            api_rows.append({'method':ep['method'],'route':ep['route'],'endpointClass':ep['className'],'endpointFile':ep['file'],'endpointNamespace':ep.get('namespace',''),'serviceCalls':service_calls,'serviceImplementations':service_links,'confidence':round(best,3),'confidenceLevel':_confidence(best),'reasons':reasons,'frontendEvidence':related_fe[:10]+([{'file':form_action['file'],'url':form_action['url'],'source':f"form-action:{form_action['kind']}:{form_action['formName']}"}] if form_action else [])})
        api_rows=sorted(api_rows,key=lambda x:x['confidence'],reverse=True)[:30]
        stats['unresolvedFormActions']+=len(unresolved_actions)
        if api_rows:
            stats['screensWithApis']+=1
            stats['highConfidence']+=sum(1 for x in api_rows if x['confidence']>=.80)
            if any(x['confidence']<.80 for x in api_rows): stats['candidateMappings']+=1
        else: stats['unmappedScreens']+=1
        out.append({'screenId':screen.get('screenId'),'screenName':screen.get('screenName'),'module':screen.get('module'),'feature':screen.get('feature'),'status':screen.get('status'),'formNames':sorted(forms),'unresolvedFormActions':unresolved_actions,'apiMappings':api_rows,'mappingStatus':'MAPPED' if api_rows else 'UNMAPPED'})
    return out,stats

def run_api_mapping(registry_path: str, frontend_root: str, backend_root: str, output_dir: str, module: str | None = None) -> dict[str, Any]:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    registry=json.loads(Path(registry_path).read_text(encoding='utf-8'))
    if isinstance(registry,dict): registry=registry.get('registry',registry.get('screens',[]))
    if module:
        registry=[s for s in registry if _norm(s.get('module',''))==_norm(module)]
    fe=Path(frontend_root); be=Path(backend_root)
    endpoint_rows=discover_backend_service_catalog(be) if be.exists() else []
    implementations=discover_service_implementations(be) if be.exists() else []
    frontend_calls=discover_frontend_api_calls(fe) if fe.exists() else []
    form_actions=load_form_action_urls(fe) if fe.exists() else {}
    mappings,stats=map_screen_to_api_backend(registry,endpoint_rows,frontend_calls,implementations,form_actions,fe if fe.exists() else None)
    write_json(out/'api_endpoint_registry_v14.json',endpoint_rows)
    write_json(out/'frontend_api_call_registry_v14.json',frontend_calls)
    write_json(out/'backend_service_registry_v14.json',implementations)
    write_json(out/'screen_api_backend_mapping_v14.json',mappings)
    report={'phase':'0.14.0','status':'COMPLETED','module':module or 'ALL','registryInput':str(Path(registry_path)),'formActionForms':len(form_actions),'frontendRoot':str(fe.resolve()),'backendRoot':str(be.resolve()),'endpointCount':len(endpoint_rows),'frontendApiCalls':len(frontend_calls),'backendServices':len(implementations),'stats':stats}
    write_json(out/'api_mapping_report_v14.json',report)
    return report


def run_api_mapping_modules(registry_path: str, frontend_root: str, backend_root: str, modules: list[str], modules_root: str,
                            progress=print) -> list[dict[str, Any]]:
    """Phase 14 for several modules: the frontend and backend are scanned once, then each module is
    mapped exactly as run_api_mapping(module=...) would, into <modules_root>/<module>/phase14-api-mapping."""
    registry=json.loads(Path(registry_path).read_text(encoding='utf-8'))
    if isinstance(registry,dict): registry=registry.get('registry',registry.get('screens',[]))
    fe=Path(frontend_root); be=Path(backend_root)
    endpoint_rows=discover_backend_service_catalog(be) if be.exists() else []
    implementations=discover_service_implementations(be) if be.exists() else []
    frontend_calls=discover_frontend_api_calls(fe) if fe.exists() else []
    form_actions=load_form_action_urls(fe) if fe.exists() else {}
    progress(f"scanned once: {len(endpoint_rows)} endpoints, {len(frontend_calls)} frontend calls, {len(form_actions)} form-action forms")
    reports=[]
    for m in modules:
        screens=[s for s in registry if _norm(s.get('module',''))==_norm(m)]
        out=Path(modules_root)/m/'phase14-api-mapping'; out.mkdir(parents=True,exist_ok=True)
        mappings,stats=map_screen_to_api_backend(screens,endpoint_rows,frontend_calls,implementations,form_actions,fe if fe.exists() else None)
        write_json(out/'api_endpoint_registry_v14.json',endpoint_rows)
        write_json(out/'frontend_api_call_registry_v14.json',frontend_calls)
        write_json(out/'backend_service_registry_v14.json',implementations)
        write_json(out/'screen_api_backend_mapping_v14.json',mappings)
        report={'phase':'0.14.0','status':'COMPLETED','module':m,'registryInput':str(Path(registry_path)),'formActionForms':len(form_actions),'frontendRoot':str(fe.resolve()),'backendRoot':str(be.resolve()),'endpointCount':len(endpoint_rows),'frontendApiCalls':len(frontend_calls),'backendServices':len(implementations),'stats':stats}
        write_json(out/'api_mapping_report_v14.json',report)
        progress(f"{m}: {stats.get('screensWithApis',0)}/{stats.get('screens',0)} screens mapped to APIs")
        reports.append(report)
    return reports
