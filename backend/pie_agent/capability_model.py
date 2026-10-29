from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .qdrant_kms import manifest_from_knowledge_dir


def _norm(value: Any) -> str:
    return str(value or "").strip()


def build_capability_model(knowledge_dir: str | Path, output_dir: str | Path,
                           mapping_file: str | Path | None = None) -> dict[str, Any]:
    """Normalize v0.17 knowledge into the v0.18 hierarchy without another LLM call.

    Explicit capability mappings can be supplied as JSON:
    {
      "SCREEN_ID": {"capability": "Leave Management", "basis": "human_confirmed"}
    }
    Otherwise the existing v0.17 functionality is retained as a *candidate*
    capability and marked for review. v0.18 never silently claims that the two
    concepts are identical.
    """
    source = manifest_from_knowledge_dir(knowledge_dir)
    overrides: dict[str, Any] = {}
    if mapping_file and Path(mapping_file).exists():
        overrides = json.loads(Path(mapping_file).read_text(encoding="utf-8"))

    hierarchy: list[dict[str, Any]] = []
    for pkg in source["knowledgePackages"]:
        screen = pkg["screen"]
        sid = _norm(screen.get("screenId"))
        module = _norm(screen.get("module"))
        functionality = _norm(screen.get("functionality"))
        feature = _norm(screen.get("feature"))
        ov = overrides.get(sid, {}) if isinstance(overrides, dict) else {}
        capability = _norm(ov.get("capability") or screen.get("capability"))
        basis = _norm(ov.get("basis"))
        if capability:
            basis = basis or "explicit"
            needs_review = basis not in {"human_confirmed", "source_evidenced", "explicit"}
        elif functionality:
            capability = functionality
            basis = "derived_from_functionality_candidate"
            needs_review = True
        else:
            basis = "missing"
            needs_review = True

        hierarchy.append({
            "screenId": sid,
            "product": "GoodBooks ERP",
            "module": module,
            "functionality": functionality,
            "capability": capability,
            "capabilityBasis": basis,
            "capabilityNeedsReview": needs_review,
            "feature": feature,
            "screenName": _norm(screen.get("screenName")) or feature or sid,
            "knowledgeStatus": pkg.get("status", "REVIEW_REQUIRED"),
            "sourceHash": pkg.get("sourceHash", ""),
        })

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "phase": "0.18.0",
        "sourcePhase": "0.17.0",
        "status": "COMPLETED",
        "hierarchy": "Product > Module > Functionality > Capability > Feature > Screen",
        "totalScreens": len(hierarchy),
        "capabilityReviewRequired": sum(x["capabilityNeedsReview"] for x in hierarchy),
        "entries": hierarchy,
    }
    (out / "capability_model_v18.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
