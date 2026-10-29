from __future__ import annotations
import json, hashlib
from pathlib import Path
from datetime import datetime, timezone
from .models import KnowledgePackage, KnowledgeRecord, ScreenRegistryEntry, Evidence, PIEOutput
from .discovery import discover_screens, map_sources_to_screen
from .offline_synthesis import build_offline_pie

def _hash_sources(files, text):
    h=hashlib.sha256()
    for f in sorted(files):
        h.update(f.encode()); h.update(text.get(f,"").encode(errors="ignore"))
    return h.hexdigest()

def _record(screen, typ, title, content, evidence, confidence=.8, review=False):
    raw=f"{screen.screenId}|{typ}|{title}"
    kid="KNO-"+hashlib.sha1(raw.encode()).hexdigest()[:14]
    return KnowledgeRecord(knowledgeId=kid,screenId=screen.screenId,module=screen.module,functionality=screen.functionality,feature=screen.feature,knowledgeType=typ,title=title,content=content,confidence=confidence,needsReview=review,evidence=evidence)

def _generic_records(screen: ScreenRegistryEntry, mappings: list[dict], text: dict[str,str]) -> list[KnowledgeRecord]:
    records=[]
    selected=[m for m in mappings if m["selected"]][:12]
    evs=[]
    for m in selected[:8]:
        evs.append(Evidence(evidence_id=hashlib.sha1((screen.screenId+m['source']).encode()).hexdigest()[:16],source_file=m["source"],source_type=m["category"],locator="source-mapping",quote_or_fact=f"Mapped to {screen.screenName} with relevance score {m['score']}.",evidence_type="derived",confidence=m["score"]))
    records.append(_record(screen,"Screen","Screen Identity",f"Discovered screen '{screen.screenName}' under functionality '{screen.functionality}'.",evs,screen.discoveryConfidence,False))
    cats=sorted({m["category"] for m in selected if m["category"]})
    if cats:
        records.append(_record(screen,"SourceCoverage","Source Coverage",f"Mapped evidence categories: {', '.join(cats)}.",evs,.75,False))
    # Extract explicit headings only; never invent business rules.
    for m in selected:
        body=text.get(m["source"],"")
        lines=[ln.strip() for ln in body.splitlines() if ln.strip()]
        headings=[ln for ln in lines if (ln.startswith("#") or ln.endswith(":") or len(ln)<90 and ln.isupper())][:8]
        for hd in headings[:4]:
            if len(hd)<4: continue
            e=next((x for x in evs if x.source_file==m["source"]),None)
            records.append(_record(screen,"ObservedSourceFact",hd.rstrip(":# "),f"Explicit source heading observed in {m['source']}.",[e] if e else [],.65,True))
    return records

def generate_screen_package(screen: ScreenRegistryEntry, records, text, base_pie: PIEOutput|None=None) -> KnowledgePackage:
    mappings=map_sources_to_screen(screen,records,text)
    source_files=[m["source"] for m in mappings if m["selected"]]
    if base_pie is not None:
        out=[]
        for x in base_pie.configItems:
            out.append(_record(screen,"ConfigItem",x.ItemName,x.Description,x.evidence,.95,x.needsReview))
        for x in base_pie.variants:
            out.append(_record(screen,"Variant",x.VariantName,x.PlainLanguageDesc,x.evidence,.93,x.needsReview))
        for x in base_pie.trainingTopics:
            out.append(_record(screen,"TrainingTopic",x.TopicName,"; ".join(x.Steps),x.evidence,.92,x.needsReview))
        for x in base_pie.uatScenarios:
            out.append(_record(screen,"UATScenario",x.ScenarioTitle,x.ExpectedResult,x.evidence,.92,x.needsReview))
        records_out=out
    else:
        records_out=_generic_records(screen,mappings,text)
    status="READY" if records_out and all(r.evidence for r in records_out if not r.needsReview) else "REVIEW_REQUIRED"
    return KnowledgePackage(screen=screen,records=records_out,generatedAt=datetime.now(timezone.utc).isoformat(),sourceHash=_hash_sources(source_files,text),status=status)

def run_generation(input_path: str, output_dir: str):
    from .ingestion import ingest
    from .classification import classify_all
    ing=ingest(input_path)
    classify_all(ing.records,ing.text)
    report=discover_screens(ing.records,ing.text)
    report.input=str(Path(input_path).resolve())
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    mdir=out/"source_mappings"; mdir.mkdir(exist_ok=True)
    (out/"screen_registry.json").write_text(json.dumps(report.model_dump(mode="json"),indent=2,ensure_ascii=False),encoding="utf-8")
    (out/"source_inventory.json").write_text(json.dumps([r.model_dump(mode="json") for r in ing.records],indent=2,ensure_ascii=False),encoding="utf-8")
    packages=[]
    kdir=out/"knowledge"; kdir.mkdir(exist_ok=True)
    for screen in report.screens:
        base=None
        if "leave" in (screen.screenName+screen.functionality).lower():
            try: base=build_offline_pie(ing.text,ing.records)
            except Exception: base=None
        mappings=map_sources_to_screen(screen,ing.records,ing.text)
        (mdir/f"{screen.screenId}.json").write_text(json.dumps(mappings,indent=2,ensure_ascii=False),encoding="utf-8")
        pkg=generate_screen_package(screen,ing.records,ing.text,base)
        packages.append(pkg)
        (kdir/f"{screen.screenId}.json").write_text(json.dumps(pkg.model_dump(mode="json"),indent=2,ensure_ascii=False),encoding="utf-8")
    index={}
    for p in packages:
        index.setdefault(p.screen.functionality or "Unclassified", {}).setdefault(p.screen.feature or p.screen.screenName, []).append(p.screen.screenId)
    (out/"kms_index.json").write_text(json.dumps(index,indent=2,ensure_ascii=False),encoding="utf-8")
    manifest={"generationVersion":"0.11.0","status":"COMPLETED","totalScreens":len(packages),"knowledgePackages":[p.model_dump(mode="json") for p in packages]}
    (out/"knowledge_manifest.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
    return report,packages
