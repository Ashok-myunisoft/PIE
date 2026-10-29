"""Semantic embeddings for the Qdrant KMS (v0.25).

The v0.18 hashing vectorizer matches words only: "reduce stock-outs" never finds "reorder
level". HybridEmbedder stores two named vectors per point:

  dense    OpenAI text-embedding-3-small (meaning: synonyms, paraphrases, other wording)
  lexical  the v0.18 hashing vector (exact ERP identifiers: MITEM, ItemCode, /BOM/SaveBOM)

and QdrantKMS.search fuses both. Selected with PIE_EMBEDDINGS=openai, or automatically when
a collection was created with these named vectors. Paid calls are batched: producers call
prime(texts) once, then embed(text) per point reads the primed vector.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from .qdrant_kms import EmbeddingProvider

DENSE, LEXICAL = "dense", "lexical"
DEFAULT_MODEL = "text-embedding-3-small"
MODEL_DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072, "text-embedding-ada-002": 1536}
MAX_INPUT_CHARS = 7000            # ~1,800 tokens; the model limit is 8,191 tokens
BATCH_CHARS = 400_000             # well under the 300k-token request limit
BATCH_ITEMS = 256
DENSE_WEIGHT, LEXICAL_WEIGHT = .7, .3


def _clip(text: str) -> str:
    text = (text or "").strip() or "(empty)"
    return text[:MAX_INPUT_CHARS]


class OpenAIDense:
    """Batched OpenAI embeddings with retries. `client` may be injected (tests)."""

    def __init__(self, model: str | None = None, client: Any = None):
        self.model = model or os.getenv("PIE_EMBEDDING_MODEL") or DEFAULT_MODEL
        self.dimension = MODEL_DIMENSIONS.get(self.model, int(os.getenv("PIE_EMBEDDING_DIM", "1536")))
        self._client = client
        self.calls = 0
        self.tokens = 0

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=float(os.getenv("PIE_LLM_TIMEOUT", "90")))
        return self._client

    def _request(self, inputs: list[str]) -> list[list[float]]:
        for attempt in range(6):
            try:
                resp = self.client.embeddings.create(model=self.model, input=inputs)
                self.calls += 1
                usage = getattr(resp, "usage", None)
                self.tokens += int(getattr(usage, "total_tokens", 0) or 0)
                return [list(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)]
            except Exception as exc:  # rate limits, timeouts, dropped connections
                if attempt == 5:
                    raise
                wait = min(60, 2 ** attempt * (3 if "rate" in type(exc).__name__.lower() else 1))
                time.sleep(wait)
        return []

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        batch: list[str] = []
        size = 0
        for t in map(_clip, texts):
            if batch and (len(batch) >= BATCH_ITEMS or size + len(t) > BATCH_CHARS):
                out += self._request(batch)
                batch, size = [], 0
            batch.append(t)
            size += len(t)
        if batch:
            out += self._request(batch)
        return out


class HybridEmbedder:
    """embed(text) -> {"dense": [...], "lexical": [...]} (Qdrant named vectors)."""

    hybrid = True

    def __init__(self, dense: OpenAIDense | None = None, lexical: EmbeddingProvider | None = None):
        self.dense = dense or OpenAIDense()
        self.lexical = lexical or EmbeddingProvider()
        self.dimension = self.dense.dimension
        self._primed: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def vectors_config(self) -> dict[str, dict[str, Any]]:
        return {DENSE: {"size": self.dense.dimension, "distance": "Cosine"},
                LEXICAL: {"size": self.lexical.dimension, "distance": "Cosine"}}

    def prime(self, texts: list[str]) -> None:
        """Embed many texts in batched requests; later embed() calls for them cost nothing."""
        todo = list(dict.fromkeys(_clip(t) for t in texts))
        with self._lock:
            todo = [t for t in todo if t not in self._primed]
        if not todo:
            return
        vectors = self.dense.embed_many(todo)
        with self._lock:
            self._primed.update(zip(todo, vectors))

    def embed(self, text: str) -> dict[str, list[float]]:
        key = _clip(text)
        with self._lock:
            dense = self._primed.pop(key, None)
        if dense is None:
            dense = self.dense.embed_many([key])[0]
        # 5 decimals: the JSON sent to a remote Qdrant is less than half the size; cosine scores do not change.
        return {DENSE: [round(x, 5) for x in dense], LEXICAL: [round(x, 5) for x in self.lexical.embed(text)]}


def default_embedder() -> Any:
    """PIE_EMBEDDINGS=openai -> HybridEmbedder; anything else -> the v0.18 hashing vectorizer."""
    if os.getenv("PIE_EMBEDDINGS", "").strip().lower() in {"openai", "hybrid"}:
        return HybridEmbedder()
    return EmbeddingProvider()


def is_hybrid(embedder: Any) -> bool:
    return bool(getattr(embedder, "hybrid", False))


def prime(embedder: Any, texts: list[str]) -> None:
    """Batch-embed when the embedder supports it (the hashing vectorizer needs nothing)."""
    fn = getattr(embedder, "prime", None)
    if fn and texts:
        fn(texts)


def fuse(dense_hits: list[dict[str, Any]], lexical_hits: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Weighted fusion of the two searches: meaning first, exact identifiers as a boost."""
    rows: dict[str, dict[str, Any]] = {}
    for hits, weight in ((dense_hits, DENSE_WEIGHT), (lexical_hits, LEXICAL_WEIGHT)):
        for h in hits:
            pid = str(h.get("id"))
            row = rows.setdefault(pid, {"id": h.get("id"), "payload": h.get("payload"), "score": 0.0})
            row["score"] += weight * max(0.0, float(h.get("score") or 0))
    return sorted(rows.values(), key=lambda r: -r["score"])[:limit]
