from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass
class PromptRoute:
    method: str
    prompt_source: str
    reason: str

METHOD_BY_CATEGORY = {
    "Screen": ("Screen Harvesting", "screen-harvesting-output-and-output-summary-PIE-Prompt.txt"),
    "Backend Code": ("DB & Code Mining", "Back End AI Prompt.txt"),
    "Frontend Code": ("DB & Code Mining", "Front End Code Prompt.txt"),
    "DB Schema": ("DB & Code Mining", "DB Schema AI Prompt.txt"),
    "API / Service Response": ("DB & Code Mining", "Response AI Prompt.txt"),
    "User Manual": ("Document Mining", "document-mining-prompt-for-output-and-output-summary-PIE-Prompt.txt"),
    "Training": ("Document Mining", "document-mining-prompt-for-output-and-output-summary-PIE-Prompt.txt"),
    "Report": ("Screen Harvesting", "screen-harvesting-output-and-output-summary-PIE-Prompt.txt"),
    "Ticket": ("Ticket Analysis", "playbook:ticket-analysis"),
    "Competitor Intelligence": ("Competitor Intelligence", "playbook:competitor-feature-analysis"),
}

def route(categories, prompt_dir: Path) -> list[PromptRoute]:
    routes=[]
    seen=set()
    for cat in categories:
        if cat not in METHOD_BY_CATEGORY: continue
        method,src=METHOD_BY_CATEGORY[cat]
        key=(method,src)
        if key in seen: continue
        seen.add(key)
        if src.startswith("playbook:"):
            reason="Playbook-native route"
        else:
            reason=f"Source category {cat} maps to the supplied prompt artifact"
        routes.append(PromptRoute(method,src,reason))
    if len(routes)>1:
        routes.append(PromptRoute(
            "Multi-source synthesis",
            "dynamic:synthesis",
            "Multiple evidence types require structured cross-source synthesis"
        ))
    return routes
