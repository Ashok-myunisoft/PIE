from __future__ import annotations
from pathlib import Path
from .models import SourceRecord, RelevanceScore

def build_context(records: list[SourceRecord], relevance: list[RelevanceScore], text: dict[str,str], max_chars_per_source=8000):
    selected={x.source for x in relevance if x.selected}
    chunks=[]
    for r in records:
        if r.fileName not in selected: continue
        body=text.get(r.fileName,"")
        chunks.append({
            "source": r.fileName,
            "category": r.sourceCategory,
            "relevance": next((x.score for x in relevance if x.source==r.fileName),0),
            "content": body[:max_chars_per_source]
        })
    return chunks

def render_context(chunks, target):
    parts=[f"TARGET\\n{target}\\n"]
    for i,c in enumerate(chunks,1):
        parts.append(f"SUPPORTING SOURCE {i}\\nFile: {c['source']}\\nCategory: {c['category']}\\nRelevance: {c['relevance']}\\n{c['content']}\\n")
    return "\n".join(parts)

def build_dynamic_prompt(target, context, playbook_rules):
    return f"""SYSTEM CONTEXT
You are a PIE Product Intelligence Analyst for GoodBooks GB5.

TARGET
{target}

PLAYBOOK RULES
{playbook_rules}

EVIDENCE CONTEXT
{context}

TASK
Generate evidence-backed PIE product intelligence. Do not use unsupported business facts.
Separate observed evidence from derived and inferred claims. For every inferred or unresolved
claim set needsReview=true. Preserve exact PIE DTO terminology.

Return one JSON object:
{{
  "module": {{}},
  "screen": {{}},
  "configItems": [],
  "variants": [],
  "trainingTopics": [],
  "uatScenarios": [],
  "estimationRules": [],
  "kpis": [],
  "businessSummary": [],
  "sourceEvidence": [],
  "reviewItems": [],
  "conflicts": []
}}

Every generated entity must include an `evidence` array when evidence exists. Each evidence record
must use only a source filename present in the supplied context and should contain a concise fact or
quote. If evidence is insufficient, set `needsReview=true` and explain why. Never invent source
filenames, defaults, mandatory status, workflow behavior, implementation hours or business rules.
"""
