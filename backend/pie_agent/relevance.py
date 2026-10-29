from __future__ import annotations
import re
from .models import RelevanceScore, SourceRecord

TARGET_TERMS = {
    "leave","leave request","leave application","tleave","leave type","leave detail",
    "employee","leave balance","leave history","leave summary","leave session"
}

CATEGORY_WEIGHT = {
    "Screen": .95, "Frontend Code": .90, "Backend Code": .90, "DB Schema": .88,
    "API / Service Response": .86, "User Manual": .75, "Training": .70, "Report": .55,
    "Existing PIE Output": .65, "Existing PIE Prompt": .55, "Existing Summary": .60,
    "Ticket": .50, "Competitor Intelligence": .20, "Other": .10
}

def score_source(r: SourceRecord, target_text: str, content: str) -> RelevanceScore:
    hay=(r.fileName+" "+content[:12000]).lower()
    terms=set(re.findall(r"[a-z0-9_]+", target_text.lower()))
    target_hits=sum(1 for t in TARGET_TERMS if t in hay)
    exact_hits=sum(1 for t in terms if len(t)>3 and t in hay)
    score=0.35*min(target_hits/6,1)+0.30*min(exact_hits/12,1)+0.35*CATEGORY_WEIGHT.get(r.sourceCategory,.1)
    reasons=[]
    if target_hits: reasons.append(f"{target_hits} target-domain signals")
    if exact_hits: reasons.append(f"{exact_hits} target-term matches")
    reasons.append(f"category weight={CATEGORY_WEIGHT.get(r.sourceCategory,.1):.2f}")
    return RelevanceScore(source=r.fileName,score=round(score,3),reasons=reasons,selected=score>=.55)

def rank_sources(records, text, target_text="Leave Application / Leave Request"):
    return [score_source(r,target_text,text.get(r.fileName,"")) for r in records]
