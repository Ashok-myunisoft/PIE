"""Copy a KMS collection into a new one with semantic (hybrid) vectors (v0.25).

Every point keeps its id and payload; only the vectors change. The source collection is
read only, so the live chat keeps working until QDRANT_COLLECTION is switched.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .qdrant_kms import QdrantKMS


def embed_text(payload: dict[str, Any]) -> str:
    """The text a point is found by: what it says plus where it belongs."""
    kt = payload.get("knowledge_type", "")
    if kt in {"DepartmentKnowledge", "DepartmentManifest"}:
        return str(payload.get("content") or payload.get("title") or "")
    parts = [payload.get("title"), payload.get("content")]
    if kt == "FieldKnowledge":
        parts += [payload.get("field_name"), payload.get("field_label"), payload.get("field_column")]
    parts += [payload.get(k) for k in ("module", "functionality", "capability", "feature", "screen_name")]
    return " | ".join(str(x) for x in parts if x)


def migrate(source: QdrantKMS, target: QdrantKMS, batch: int = 512, progress=print, resume: bool = True) -> dict[str, Any]:
    if source.collection == target.collection:
        raise ValueError("source and target collections must differ")
    target.ensure_collection()
    points = source.scroll_all()
    # Resumable: points already in the target (same id) are not embedded or uploaded again.
    done_ids = {str(p.get("id")) for p in target.scroll_all()} if resume else set()
    total = len(points)
    points = [p for p in points if str(p.get("id")) not in done_ids]
    progress(f"read {total} points from {source.collection}; {total - len(points)} already in {target.collection}, {len(points)} to migrate")
    kinds: Counter[str] = Counter()
    done = 0
    for i in range(0, len(points), batch):
        chunk = points[i:i + batch]
        done += target.upsert_points([{"id": p["id"], "payload": p.get("payload") or {}, "text": embed_text(p.get("payload") or {})}
                                      for p in chunk])
        kinds.update((p.get("payload") or {}).get("knowledge_type", "") for p in chunk)
        progress(f"migrated {done}/{len(points)}")
    stats = target.client.collection(target.collection).get("result", {})
    dense = getattr(target.embedder, "dense", None)
    return {"status": "MIGRATED", "source": source.collection, "target": target.collection, "points": done,
            "alreadyPresent": total - len(points),
            "targetPoints": stats.get("points_count"), "byType": dict(kinds),
            "embeddingModel": getattr(dense, "model", None), "embeddingTokens": getattr(dense, "tokens", None)}
