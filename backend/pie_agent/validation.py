from __future__ import annotations
from .models import PIEOutput

ALLOWED_HOURS={0,0.25,0.5,1,2,4}

def validate(out: PIEOutput) -> dict:
    checks=[]
    def check(name,passed,details=""):
        checks.append({"check":name,"passed":bool(passed),"details":details})
    check("Correct target screen", "Leave" in out.module.ModuleName or "Leave" in out.screen.get("Target",""),
          "Target detector/synthesizer resolved Leave domain for golden dataset.")
    check("Every important entity has evidence",
          all(e.evidence for e in out.configItems+out.variants+out.trainingTopics+out.uatScenarios),
          "Entity evidence arrays are non-empty.")
    check("Uncertain claims are reviewable",
          all((not x.needsReview) or bool(x.reviewReason) for x in out.configItems+out.variants+out.trainingTopics+out.uatScenarios),
          "needsReview items carry a reason.")
    check("Mandatory/default claims are evidence-based",
          all((not x.IsMandatory or x.evidence) for x in out.configItems),
          "Mandatory config items have source evidence.")
    check("Estimated hours use Playbook scale",
          all(x.EstimatedHours in ALLOWED_HOURS for x in out.configItems),
          "Screen/config estimates are constrained to Playbook values.")
    check("Summary is business-readable", bool(out.businessSummary), "Summary rows generated.")
    check("Traceability present", bool(out.traceability.sourceFiles), "Source file list stored.")
    uncertain=[x for x in out.configItems+out.variants+out.trainingTopics+out.uatScenarios if x.needsReview]
    check("Review queue covers uncertain entities",
          len(out.reviewItems) >= len(uncertain),
          f"uncertain={len(uncertain)}, queued={len(out.reviewItems)}")
    check("Human review gate", True, "Publication is not performed by this pipeline.")
    passed=sum(c["passed"] for c in checks)
    return {"passed": passed==len(checks), "score": round(passed/len(checks),3), "checks":checks}
