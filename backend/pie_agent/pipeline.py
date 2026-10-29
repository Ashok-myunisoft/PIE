from __future__ import annotations
import json
from pathlib import Path
from .ingestion import ingest
from .classification import classify_all
from .relevance import rank_sources
from .prompt_router import route
from .context import build_context, render_context, build_dynamic_prompt
from .offline_synthesis import build_offline_pie
from .llm import get_provider, extract_json
from .models import PIEOutput
from .validation import validate
from .exporter import *
from .evaluation import load_reference_outputs, evaluate
import tempfile, zipfile

PLAYBOOK_RULES = """Use exact PIE DTO terminology. Screen analysis identifies fields/toggles/dropdowns/
checkboxes/radio buttons and classifies ConfigItem vs Variant. ItemCategory is one of
BizTransaction, Workflow, Role, DocumentFormat, MailScheduler, Infrastructure. EstimatedHours uses
0.25/0.5/1/2/4 where applicable. User manuals produce TrainingTopics and UATScenarios. Uncertain
items use needsReview=true. Do not invent mandatory/default/workflow/business rules."""

def run(input_path: str, output_dir: str, use_llm: bool=False, provider_name: str|None=None):
    outdir=Path(output_dir); outdir.mkdir(parents=True,exist_ok=True)
    ing=ingest(input_path)
    classify_all(ing.records,ing.text)
    relevance=rank_sources(ing.records,ing.text)
    categories=[r.sourceCategory for r in ing.records if r.sourceCategory!="Other" and next((x.selected for x in relevance if x.source==r.fileName),False)]
    routes=route(categories,Path("prompts"))
    chunks=build_context(ing.records,relevance,ing.text)
    context=render_context(chunks,"Leave Application / Leave Request")
    prompt=build_dynamic_prompt("Leave Application / Leave Request",context,PLAYBOOK_RULES)

    # Offline baseline always runs. LLM output is validated and merged only if it parses.
    pie=build_offline_pie(ing.text,ing.records)
    llm_raw=""
    if use_llm:
        provider=get_provider(provider_name)
        llm_raw=provider.generate(prompt)
        if llm_raw:
            try:
                parsed=extract_json(llm_raw)
                candidate=PIEOutput.model_validate(parsed)
                # Candidate overlay: retain the deterministic evidence baseline and add only
                # semantically new LLM entities. Evidence-free LLM entities are review-only.
                existing_cfg={x.ItemName.lower() for x in pie.configItems}
                existing_var={x.VariantName.lower() for x in pie.variants}
                existing_trn={x.TopicName.lower() for x in pie.trainingTopics}
                existing_uat={x.ScenarioTitle.lower() for x in pie.uatScenarios}
                for x in candidate.configItems:
                    if x.ItemName.lower() not in existing_cfg:
                        x.needsReview=True
                        x.reviewReason=x.reviewReason or "LLM candidate requires evidence-backed human validation."
                        pie.configItems.append(x)
                for x in candidate.variants:
                    if x.VariantName.lower() not in existing_var:
                        x.needsReview=True
                        x.reviewReason=x.reviewReason or "LLM candidate requires evidence-backed human validation."
                        pie.variants.append(x)
                for x in candidate.trainingTopics:
                    if x.TopicName.lower() not in existing_trn:
                        x.needsReview=True
                        x.reviewReason=x.reviewReason or "LLM candidate requires evidence-backed human validation."
                        pie.trainingTopics.append(x)
                for x in candidate.uatScenarios:
                    if x.ScenarioTitle.lower() not in existing_uat:
                        x.needsReview=True
                        x.reviewReason=x.reviewReason or "LLM candidate requires evidence-backed human validation."
                        pie.uatScenarios.append(x)
                pie.traceability.postProcessing.append("LLM candidate overlay applied; deterministic evidence baseline retained.")
            except Exception as exc:
                pie.traceability.postProcessing.append(f"LLM parse/validation failed: {type(exc).__name__}; baseline retained.")
    # Rebuild review queue after optional LLM candidate overlay.
    seen={(r.entity,r.field,str(r.currentValue)) for r in pie.reviewItems}
    for seq, etype, name_field in [
        (pie.configItems, "ConfigItem", "ItemName"),
        (pie.variants, "Variant", "VariantName"),
        (pie.trainingTopics, "TrainingTopic", "TopicName"),
        (pie.uatScenarios, "UATScenario", "ScenarioTitle"),
    ]:
        for item in seq:
            if getattr(item,"needsReview",False):
                key=(etype,"entity",str(getattr(item,name_field)))
                if key not in seen:
                    from .models import ReviewItem
                    pie.reviewItems.append(ReviewItem(
                        entity=etype, field="entity", currentValue=getattr(item,name_field),
                        reason=getattr(item,"reviewReason","Human validation required."),
                        evidence=[e.evidence_id for e in getattr(item,"evidence",[])[:1]]
                    ))
                    seen.add(key)

    pie.traceability.generatedPrompt=prompt
    pie.traceability.modelOutput=llm_raw
    validation=validate(pie)
    pie.traceability.validationResult=validation

    (outdir/"source_inventory.json").write_text(json.dumps([r.model_dump(mode="json") for r in ing.records],indent=2),encoding="utf-8")
    (outdir/"relevance_report.json").write_text(json.dumps([x.model_dump(mode="json") for x in relevance],indent=2),encoding="utf-8")
    (outdir/"generated_prompt.txt").write_text(prompt,encoding="utf-8")
    export_json(pie,outdir/"pie_output.json")
    export_traceability(pie,outdir/"traceability.json")
    export_review(pie,outdir/"review_queue.json")
    export_validation(validation,outdir/"validation_report.json")
    export_markdown(pie,outdir/"business_summary.md")
    export_excel(pie,outdir/"business_summary.xlsx",prompt)

    ref_root=Path(input_path).resolve().parent/"PIE_Data"
    refs=load_reference_outputs(ref_root)
    if not refs and Path(input_path).suffix.lower()==".zip":
        ref_tmp=Path(tempfile.mkdtemp(prefix="pie-eval-ref-"))
        with zipfile.ZipFile(input_path) as z: z.extractall(ref_tmp)
        refs=load_reference_outputs(ref_tmp/"PIE_Data")
    evaluation=evaluate(pie,refs,validation)
    (outdir/"evaluation_report.json").write_text(json.dumps(evaluation,indent=2),encoding="utf-8")
    return pie, validation, evaluation
