from __future__ import annotations
import json
from pathlib import Path
from .models import KnowledgePackage

class KMSStore:
    """Small KMS boundary. Replace the backend without changing generation code."""
    def publish(self, package: KnowledgePackage) -> None:
        raise NotImplementedError

class JsonKMSStore(KMSStore):
    """Local KMS implementation used for Phase 11 validation and demos."""
    def __init__(self, root: str | Path):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
    def publish(self, package: KnowledgePackage) -> None:
        path=self.root/f"{package.screen.screenId}.json"
        path.write_text(json.dumps(package.model_dump(mode="json"),indent=2,ensure_ascii=False),encoding="utf-8")
    def publish_many(self, packages: list[KnowledgePackage]) -> None:
        for package in packages:self.publish(package)
