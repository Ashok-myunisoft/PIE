from __future__ import annotations
import json, re
from pathlib import Path
from .models import PIEOutput

def norm(s): return re.sub(r"[^a-z0-9]+"," ",s.lower()).strip()

def load_reference_outputs(data_dir: Path):
    refs={}
    for name in [
        "screen-harvesting-leave-application-screen-GB5-config-varient-screen-output.txt",
        "Backend code Output.txt","front end file json output.txt","DB Schema output.txt",
        "Response file output.txt","user-manual-leave-application-screen-GB5-uat-training-output.txt",
    ]:
        p=data_dir/name
        if p.exists():
            refs[name]=p.read_text(encoding="utf-8",errors="replace")
    return refs

def _names(out):
    return [norm(x.ItemName) for x in out.configItems]+[norm(x.VariantName) for x in out.variants]+[norm(x.TopicName) for x in out.trainingTopics]+[norm(x.ScenarioTitle) for x in out.uatScenarios]

def semantic_coverage(out: PIEOutput, refs: dict[str,str]):
    generated=set(_names(out))
    ref_terms=[]
    for body in refs.values():
        # Extract common domain labels from known reference outputs.
        for phrase in re.findall(r'"(?:ItemName|VariantName|ConfigItemName|TopicName|ScenarioName)"\s*:\s*"([^"]+)"',body):
            ref_terms.append(norm(phrase))
    ref=set(ref_terms)
    matched={x for x in ref if any(x in g or g in x for g in generated if x and g)}
    return round(len(matched)/len(ref),3) if ref else 0, len(matched), len(ref)

def evaluate(out: PIEOutput, refs: dict[str,str], validation: dict):
    coverage,matched,total=semantic_coverage(out,refs)
    attributed=sum(bool(x.evidence) for x in out.configItems+out.variants+out.trainingTopics+out.uatScenarios)
    entity_count=len(out.configItems)+len(out.variants)+len(out.trainingTopics)+len(out.uatScenarios)
    return {
        "benchmark":"Leave Application / Leave Request",
        "semanticEntityCoverage":coverage,
        "matchedReferenceEntities":matched,
        "referenceEntityCount":total,
        "sourceAttributionRate":round(attributed/entity_count,3) if entity_count else 0,
        "qualityGateScore":validation["score"],
        "unsupportedClaimRate":"not auto-estimated in offline mode; review queue is the control",
        "needsReviewAccuracy":"requires human-labeled benchmark; offline report exposes review queue for audit",
        "note":"Semantic equivalence is preferred over exact text matching."
    }
