from __future__ import annotations
import re
from .models import SourceRecord

RULES = [
    (("screen","screenshot","ui","leave application screenshot"), "Screen"),
    (("db schema","schema-input","ddl","table","column_name","database"), "DB Schema"),
    (("backend","back end","bll","dal","endpoint",".cs"), "Backend Code"),
    (("front end","frontend","angular",".ts",".html"), "Frontend Code"),
    (("response","service","api","payload","dto"), "API / Service Response"),
    (("user manual","manual"), "User Manual"),
    (("training","uat"), "Training"),
    (("report","presentation"), "Report"),
    (("ticket","redmine","call register"), "Ticket"),
    (("prompt","ai prompt"), "Existing PIE Prompt"),
    (("summary","output summary"), "Existing Summary"),
    (("pie output","screen-harvesting-output","json output"), "Existing PIE Output"),
    (("competitor","industry standard"), "Competitor Intelligence"),
]

def classify_record(r: SourceRecord, text: str = "") -> SourceRecord:
    hay=(r.fileName+" "+text[:4000]).lower()
    scores=[]
    for keywords, cat in RULES:
        score=sum(1 for k in keywords if k in hay)
        if score: scores.append((score,cat))
    if scores:
        score,cat=max(scores)
        r.sourceCategory=cat
        r.confidence=min(0.55+0.1*score,0.99)
    # More precise rules for known source artifacts.
    n=r.fileName.lower()
    if "screen" in n and "harvesting" not in n and n.endswith(".pdf"):
        r.sourceCategory="Screen"; r.confidence=.98
    elif "leave application screenshot" in n:
        r.sourceCategory="Screen"; r.confidence=.99
    elif "front end" in n:
        r.sourceCategory="Frontend Code"; r.confidence=.99
    elif "backend" in n or "back end" in n:
        r.sourceCategory="Backend Code"; r.confidence=.99
    elif "db schema" in n:
        r.sourceCategory="DB Schema"; r.confidence=.99
    elif "response" in n and "prompt" not in n:
        r.sourceCategory="API / Service Response"; r.confidence=.96
    elif "user manual" in n:
        r.sourceCategory="User Manual"; r.confidence=.99
    elif "uat" in n or "training" in n:
        r.sourceCategory="Training"; r.confidence=.95
    elif "report" in n:
        r.sourceCategory="Report"; r.confidence=.95
    elif "screen-harvesting" in n and "prompt" in n:
        r.sourceCategory="Existing PIE Prompt"; r.confidence=.99
    elif "output" in n and ("screen" in n or "json" in n):
        r.sourceCategory="Existing PIE Output"; r.confidence=.9
    elif "summary" in n:
        r.sourceCategory="Existing Summary"; r.confidence=.9
    return r

def classify_all(records, text):
    for r in records: classify_record(r,text.get(r.fileName,""))
    return records
