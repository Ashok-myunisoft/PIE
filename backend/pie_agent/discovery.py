from __future__ import annotations
import hashlib, re
from pathlib import Path
from datetime import datetime, timezone
from .models import SourceRecord, Evidence, ScreenRegistryEntry, DiscoveryReport

SCREEN_NAME_PATTERNS = [
    re.compile(r"(?i)(?:screen|page|form|view)\s*[:\-]\s*([A-Za-z][A-Za-z0-9 /&_-]{2,80})"),
    re.compile(r"(?i)(?:component|route)\s*[:\-]\s*([A-Za-z][A-Za-z0-9 /&_-]{2,80})"),
]
ROUTE_PATTERNS = [
    re.compile(r"(?i)path\s*:\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"(?i)route\s*[:=]\s*['\"]([^'\"]+)['\"]"),
]
STOP = {"application","request","screen","page","input","output","file","front","end","code","leave","the","and","for"}

def _clean_name(s: str) -> str:
    s=re.sub(r"[_\\/]+", " ", s)
    s=re.sub(r"[-]+", " ", s)
    s=re.sub(r"\.(component|page|screen|view|form)\b", "", s, flags=re.I)
    s=re.sub(r"\.(ts|tsx|js|jsx|html|cs|pdf|txt)$", "", s, flags=re.I)
    s=re.sub(r"\s+", " ", s).strip(" ._-:")
    return s.title() if s else ""

def _tokens(s: str) -> set[str]:
    return {x.lower() for x in re.findall(r"[a-z0-9]+", s) if len(x)>2 and x.lower() not in STOP}

def _infer_module(path: str, content: str) -> str:
    parts=[x for x in re.split(r"[/\\]", path) if x]
    for part in parts:
        if part.lower() in {"hr","finance","payroll","payroll","sales","purchase","inventory","crm","ess","admin"}:
            return part.title()
    m=re.search(r"(?i)\b(HR|Finance|Payroll|Sales|Purchase|Inventory|CRM|ESS|Admin)\b", content[:8000])
    return m.group(1).title() if m else ""

def _infer_screen_from_record(r: SourceRecord, body: str) -> tuple[str,float]:
    name=Path(r.fileName).stem
    low_name=name.lower()
    # Do not treat PIE prompts, generated outputs, summaries, or generic reports as screens.
    noise_terms=("prompt","summary","output","report","response ai","backend code output","json output","training output")
    if any(x in low_name for x in noise_terms) and "front end file-input" not in low_name and "screenshot" not in low_name:
        # A real source file can still win if it contains an explicit route/component declaration.
        if not any(p.search(body[:30000]) for p in ROUTE_PATTERNS+SCREEN_NAME_PATTERNS):
            return "",0.0
    # Strong filename signals for screen artifacts.
    if "screenshot" in name.lower():
        x=re.sub(r"(?i)screenshot", "", name)
        x=re.sub(r"(?i)(input|output|screen|pdf)", "", x)
        n=_clean_name(x)
        return n, .98 if n else .75
    if "front end" in name.lower() or "frontend" in name.lower():
        x=re.sub(r"(?i)(front end|frontend|file|input|output)", "", name)
        n=_clean_name(x)
        return n, .90 if n else .65
    # Route/component declarations inside source.
    for pat in ROUTE_PATTERNS:
        m=pat.search(body[:30000])
        if m:
            route=m.group(1).strip("/")
            seg=route.split("/")[-1] if route else ""
            n=_clean_name(seg)
            if n: return n,.88
    for pat in SCREEN_NAME_PATTERNS:
        m=pat.search(body[:20000])
        if m:
            n=_clean_name(m.group(1))
            if n:return n,.82
    # Common artifact names can still identify a screen.
    low=name.lower()
    if any(x in low for x in ("leave application","leave request","leave approval","leave balance","leave history","leave cancellation")):
        n=_clean_name(re.sub(r"(?i)(front end|file|input|output|json|summary|config|variant|screen|harvesting)","",name))
        return n,.76
    return "",0.0

def _screen_id(module: str, functionality: str, feature: str, screen_name: str) -> str:
    # Screen identity is intentionally module-independent so multiple artifacts for the same
    # feature (for example screenshot + frontend source) collapse into one registry entry.
    parts=[functionality or "UNCLASSIFIED", feature or screen_name or "SCREEN"]
    raw=".".join(re.sub(r"[^A-Za-z0-9]+","_",p).strip("_").upper() for p in parts)
    return raw[:160]

def _functionality(screen: str, body: str) -> str:
    low=(screen+" "+body[:10000]).lower()
    for term,label in [("leave","Leave"),("employee","Employee"),("payroll","Payroll"),("invoice","Invoice"),("payment","Payment"),("purchase","Purchase"),("sales","Sales")]:
        if term in low:return label
    return "Unclassified"

def _feature(screen: str) -> str:
    return screen or "Unclassified Feature"

def discover_screens(records: list[SourceRecord], text: dict[str,str]) -> DiscoveryReport:
    candidates=[]
    for r in records:
        body=text.get(r.fileName,"")
        # Manuals/reports can mention UI labels but are not screen-discovery sources by themselves.
        if r.sourceCategory in {"User Manual","Training","Report","Existing PIE Prompt","Existing PIE Output","Existing Summary"} and not any(k in r.fileName.lower() for k in ("screenshot","front end file-input")):
            continue
        name,conf=_infer_screen_from_record(r,body)
        if not name: continue
        module=_infer_module(r.fileName,body)
        functionality=_functionality(name,body)
        feature=_feature(name)
        sid=_screen_id(module,functionality,feature,name)
        candidates.append((sid,module,functionality,feature,name,conf,r,body))
    # Merge sources into one registry entry when they point to the same screen.
    grouped={}
    for sid,module,functionality,feature,name,conf,r,body in candidates:
        if sid not in grouped:
            grouped[sid]=ScreenRegistryEntry(screenId=sid,module=module,functionality=functionality,feature=feature,screenName=name,discoveryConfidence=conf)
        e=grouped[sid]
        e.discoveryConfidence=max(e.discoveryConfidence,conf)
        if r.fileName not in e.sourceFiles:e.sourceFiles.append(r.fileName)
        if r.sourceCategory not in e.sourceCategories:e.sourceCategories.append(r.sourceCategory)
        if body:
            ev=Evidence(evidence_id=_eid(sid,r.fileName),source_file=r.fileName,locator="discovery",quote_or_fact=f"Source is associated with discovered screen '{name}'.",evidence_type="derived",confidence=conf)
            e.evidence.append(ev)
    screens=list(grouped.values())
    for e in screens:
        e.status="READY" if e.sourceFiles else "ERROR"
    return DiscoveryReport(discoveredAt=datetime.now(timezone.utc).isoformat(),totalFiles=len(records),totalScreens=len(screens),screens=screens)

def _eid(*parts:str)->str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]

def map_sources_to_screen(screen: ScreenRegistryEntry, records: list[SourceRecord], text: dict[str,str]) -> list[dict]:
    st=_tokens(screen.screenName+" "+screen.functionality+" "+screen.feature)
    mapped=[]
    for r in records:
        body=text.get(r.fileName,"")
        rt=_tokens(r.fileName+" "+body[:12000])
        overlap=len(st & rt)
        cat_boost={"Screen":.35,"Frontend Code":.30,"Backend Code":.28,"DB Schema":.25,"API / Service Response":.23,"User Manual":.18,"Training":.16}.get(r.sourceCategory,.05)
        score=min(1.0, overlap/max(1,len(st))*0.65+cat_boost)
        if r.fileName in screen.sourceFiles: score=max(score,.95)
        mapped.append({"source":r.fileName,"category":r.sourceCategory,"score":round(score,3),"selected":score>=.32,"reason":f"token_overlap={overlap}; category_boost={cat_boost:.2f}"})
    return sorted(mapped,key=lambda x:x["score"],reverse=True)
