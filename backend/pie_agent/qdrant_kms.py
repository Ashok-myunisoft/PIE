from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_COLLECTION = "pie_knowledge"
DEFAULT_VECTOR_SIZE = 384


class EmbeddingProvider:
    """Dependency-free local vectorizer.

    This is a deterministic hashing embedding, so v0.18 never spends OpenAI
    tokens merely to store/retrieve existing v0.17 knowledge. It is intentionally
    replaceable by SentenceTransformers later without changing the KMS contract.
    """

    def __init__(self, dimension: int = DEFAULT_VECTOR_SIZE):
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        tokens = _tokens(text)
        vec = [0.0] * self.dimension
        if not tokens:
            return vec
        for token in tokens:
            h = hashlib.sha256(token.encode("utf-8")).digest()
            for i in range(0, 4):
                idx = int.from_bytes(h[i * 4:(i + 1) * 4], "little") % self.dimension
                sign = 1.0 if h[16 + i] & 1 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec


def _tokens(text: str) -> list[str]:
    import re
    raw = re.findall(r"[a-zA-Z0-9_]+", (text or "").lower())
    # Add a few word-pair features to improve local lexical retrieval.
    return raw + [f"{a}__{b}" for a, b in zip(raw, raw[1:])]


class QdrantHTTP:
    """Small Qdrant REST client using only Python's standard library."""

    def __init__(self, url: str = "http://localhost:6333", api_key: str = "", timeout: int = 30):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Any | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        # Qdrant compresses responses on request: a 1000-point scroll page drops from ~3.6 MB to ~0.5 MB,
        # which is what makes loading the chat snapshot over a slow link bearable.
        headers = {"Content-Type": "application/json", "Accept-Encoding": "gzip"}
        if self.api_key:
            headers["api-key"] = self.api_key
        # A remote Qdrant occasionally drops a connection mid-request; every PIE request is safe to
        # repeat (reads, and upserts of points with fixed ids), so dropped connections are retried.
        import time
        for attempt in range(3):
            req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    raw = raw.decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Qdrant HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                refused = isinstance(getattr(exc, "reason", exc), ConnectionRefusedError)
                if attempt == 2 or refused:
                    raise RuntimeError(f"Qdrant connection failed: {exc}") from exc
                time.sleep(1.5 * (attempt + 1))

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/")

    def collection(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/collections/{name}")

    def create_collection(self, name: str, vector_size: int | dict[str, Any]) -> dict[str, Any]:
        # An int is one unnamed vector (v0.18); a dict is named vectors (v0.25 hybrid: dense + lexical).
        vectors = vector_size if isinstance(vector_size, dict) else {"size": vector_size, "distance": "Cosine"}
        return self._request("PUT", f"/collections/{name}", {"vectors": vectors})

    def create_payload_index(self, name: str, field_name: str, field_schema: str = "keyword") -> dict[str, Any]:
        return self._request(
            "PUT", f"/collections/{name}/index",
            {"field_name": field_name, "field_schema": field_schema, "wait": True}
        )

    def ensure_collection(self, name: str, vector_size: int | dict[str, Any],
                          index_fields: tuple[str, ...] = ()) -> dict[str, Any]:
        try:
            result = self.collection(name)
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
            self.create_collection(name, vector_size)
            result = self.collection(name)
        # These indexes make PIE's structured filters efficient. Existing
        # indexes are harmlessly skipped on servers that report a conflict.
        if hasattr(self, "create_payload_index"):
            for field in ("product", "module", "functionality", "capability", "feature",
                          "screen_id", "knowledge_type", "publication_status", "knowledge_version") + tuple(index_fields):
                try:
                    self.create_payload_index(name, field)
                except RuntimeError:
                    # Index creation is an optimization; never make the KMS
                    # unusable if an index already exists or the server version
                    # does not support this schema.
                    pass
        return result

    def upsert(self, name: str, points: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("PUT", f"/collections/{name}/points?wait=true", {"points": points})

    def retrieve(self, name: str, point_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/collections/{name}/points",
            {"ids": [point_id], "with_payload": True, "with_vector": False}
        )

    def search(self, name: str, vector: list[float] | dict[str, Any], limit: int = 10,
               query_filter: dict[str, Any] | None = None) -> dict[str, Any]:
        # A named vector is passed as {"name": "dense", "vector": [...]}.
        body: dict[str, Any] = {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        if query_filter:
            body["filter"] = query_filter
        # Qdrant supports /points/search on current REST API.
        return self._request("POST", f"/collections/{name}/points/search", body)

    def scroll(self, name: str, query_filter: dict[str, Any] | None = None,
               limit: int = 256, offset: Any = None) -> dict[str, Any]:
        body: dict[str, Any] = {"limit": limit, "with_payload": True, "with_vector": False}
        if query_filter:
            body["filter"] = query_filter
        if offset is not None:
            body["offset"] = offset
        return self._request("POST", f"/collections/{name}/points/scroll", body)

    def delete_by_filter(self, name: str, query_filter: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", f"/collections/{name}/points/delete?wait=true",
            {"filter": query_filter}
        )


def _stable_uuid(knowledge_id: str, version: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pie://knowledge/{knowledge_id}/{version}"))


def _knowledge_text(pkg: dict[str, Any], record: dict[str, Any]) -> str:
    screen = pkg.get("screen", {})
    parts = [
        record.get("title", ""),
        record.get("content", ""),
        screen.get("module", ""),
        screen.get("functionality", ""),
        screen.get("feature", ""),
        screen.get("screenName", ""),
    ]
    return " | ".join(str(x) for x in parts if x)


def _capability(pkg: dict[str, Any]) -> tuple[str, str, bool]:
    screen = pkg.get("screen", {})
    explicit = screen.get("capability") or ""
    if explicit:
        return str(explicit), "explicit", False
    # Do not pretend functionality and capability are identical. This is only
    # a candidate until evidence or a human mapping confirms it.
    functionality = str(screen.get("functionality") or "")
    if functionality:
        return functionality, "derived_from_functionality_candidate", True
    return "", "missing", True


def package_points(pkg: dict[str, Any], embedder: EmbeddingProvider,
                   version: str = "0.18.0", include_review: bool = True) -> list[dict[str, Any]]:
    points = []
    screen = pkg.get("screen", {})
    capability, capability_basis, capability_review = _capability(pkg)
    from .embeddings import prime
    prime(embedder, [_knowledge_text(pkg, r) for r in pkg.get("records", [])])
    for record in pkg.get("records", []):
        if not include_review and (pkg.get("status") != "READY" or record.get("needsReview")):
            continue
        knowledge_id = record.get("knowledgeId") or hashlib.sha256(
            json.dumps(record, sort_keys=True).encode()
        ).hexdigest()[:24]
        point_id = _stable_uuid(knowledge_id, version)
        payload = {
            "knowledge_id": knowledge_id,
            "product": "GoodBooks ERP",
            "module": screen.get("module", ""),
            "functionality": record.get("functionality") or screen.get("functionality", ""),
            "capability": capability,
            "capability_basis": capability_basis,
            "capability_needs_review": capability_review,
            "feature": record.get("feature") or screen.get("feature", ""),
            "screen_id": record.get("screenId") or screen.get("screenId", ""),
            "screen_name": screen.get("screenName", ""),
            "knowledge_type": record.get("knowledgeType", ""),
            "title": record.get("title", ""),
            "content": record.get("content", ""),
            "evidence_ids": record.get("sourceFactIds", []),
            "source_types": sorted({e.get("source_type", "") for e in record.get("evidence", []) if e.get("source_type")}),
            "confidence": float(record.get("confidence", 0.0)),
            # Capability uncertainty is metadata, not a reason to suppress
            # otherwise evidence-approved knowledge from the KMS. The
            # publication gate applies to the knowledge claim itself.
            "review_required": bool(record.get("needsReview", False)),
            "knowledge_review_required": bool(record.get("needsReview", False)),
            "publication_status": "REVIEW_REQUIRED" if record.get("needsReview", False) else "PUBLISHED",
            "generation_method": record.get("generationMethod", ""),
            "claim_type": record.get("claimType", ""),
            "source_hash": pkg.get("sourceHash", ""),
            "knowledge_version": version,
            "generated_at": pkg.get("generatedAt", ""),
        }
        points.append({"id": point_id, "vector": embedder.embed(_knowledge_text(pkg, record)), "payload": payload})
    return points


def manifest_from_knowledge_dir(knowledge_dir: str | Path) -> dict[str, Any]:
    """Build a v0.18 ingestion manifest from every v0.17 screen JSON.

    This intentionally scans the per-screen knowledge directory instead of
    relying on v0.17's limited manifest, which may have been generated with
    --limit during testing.
    """
    root = Path(knowledge_dir)
    packages = []
    for path in sorted(root.glob("*.json")):
        if path.name.startswith(("knowledge_manifest", "generation_report", "screen_evidence")):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("screen") and isinstance(data.get("records"), list):
                packages.append(data)
        except Exception:
            continue
    return {
        "phase": "0.18.0",
        "sourcePhase": "0.17.0",
        "status": "COMPLETED",
        "sourceDirectory": str(root.resolve()),
        "totalScreens": len(packages),
        "totalRecords": sum(len(p.get("records", [])) for p in packages),
        "knowledgePackages": packages,
    }


@dataclass
class QdrantKMS:
    client: QdrantHTTP
    collection: str = DEFAULT_COLLECTION
    embedder: EmbeddingProvider | None = None
    version: str = "0.18.0"

    index_fields: tuple[str, ...] = ()

    def __post_init__(self):
        from .embeddings import default_embedder
        self._explicit_embedder = self.embedder is not None
        self.embedder = self.embedder or default_embedder()
        self._collection_ready: dict[str, Any] | None = None

    @property
    def hybrid(self) -> bool:
        from .embeddings import is_hybrid
        return is_hybrid(self.embedder)

    def ensure_collection(self) -> dict[str, Any]:
        # The collection check plus nine payload-index requests only need to run
        # once per KMS instance, not on every search.
        if self._collection_ready is None:
            size = self.embedder.vectors_config() if self.hybrid else self.embedder.dimension
            self._collection_ready = self.client.ensure_collection(self.collection, size, *([self.index_fields] if self.index_fields else []))
            self._adopt_collection_vectors(self._collection_ready)
        return self._collection_ready

    def _adopt_collection_vectors(self, info: Any) -> None:
        """A collection created with named dense/lexical vectors is searched with the hybrid
        embedder, whatever PIE_EMBEDDINGS says, so readers always match how it was written."""
        if self._explicit_embedder or self.hybrid or not isinstance(info, dict):
            return
        vectors: Any = info
        for key in ("result", "config", "params", "vectors"):
            vectors = vectors.get(key) if isinstance(vectors, dict) else None
        from .embeddings import DENSE, MODEL_DIMENSIONS, HybridEmbedder, OpenAIDense
        if isinstance(vectors, dict) and DENSE in vectors:
            size = int((vectors[DENSE] or {}).get("size") or 1536)
            model = os.getenv("PIE_EMBEDDING_MODEL") or next((m for m, d in MODEL_DIMENSIONS.items() if d == size), None)
            self.embedder = HybridEmbedder(OpenAIDense(model))

    def scroll_all(self, query_filter: dict[str, Any] | None = None, page_size: int = 1000) -> list[dict[str, Any]]:
        # 1000 per page: with every module built (~21k points) this halves the load time from a remote Qdrant.
        points: list[dict[str, Any]] = []
        offset = None
        while True:
            res = self.client.scroll(self.collection, query_filter, page_size, offset).get("result", {})
            points.extend(res.get("points", []))
            offset = res.get("next_page_offset")
            if offset is None:
                return points

    def publish_packages(self, packages: list[dict[str, Any]], include_review: bool = True,
                         batch_size: int = 64) -> dict[str, Any]:
        self.ensure_collection()
        total = 0
        published = 0
        review = 0
        for pkg in packages:
            points = package_points(pkg, self.embedder, self.version, include_review=include_review)
            for i in range(0, len(points), batch_size):
                batch = points[i:i + batch_size]
                self.client.upsert(self.collection, batch)
                total += len(batch)
                published += sum(p["payload"]["publication_status"] == "PUBLISHED" for p in batch)
                review += sum(p["payload"]["publication_status"] == "REVIEW_REQUIRED" for p in batch)
        return {
            "status": "PUBLISHED",
            "collection": self.collection,
            "screens": len(packages),
            "records": total,
            "published": published,
            "reviewRequired": review,
            "version": self.version,
        }

    def search(self, query: str, limit: int = 10,
               query_filter: dict[str, Any] | None = None) -> dict[str, Any]:
        self.ensure_collection()
        vector = self.embedder.embed(query)
        if not isinstance(vector, dict):
            return self.client.search(self.collection, vector, limit, query_filter)
        # Hybrid: one search per named vector, fused (meaning first, exact identifiers as a boost).
        from .embeddings import DENSE, LEXICAL, fuse
        hits = {name: self.client.search(self.collection, {"name": name, "vector": vector[name]}, limit * 2,
                                         query_filter).get("result", []) for name in (DENSE, LEXICAL)}
        return {"result": fuse(hits[DENSE], hits[LEXICAL], limit)}

    def upsert_points(self, points: list[dict[str, Any]], batch_size: int = 64) -> int:
        """Upsert points that carry `text` instead of `vector`: texts are embedded in batches first."""
        from .embeddings import prime
        self.ensure_collection()
        prime(self.embedder, [p["text"] for p in points if "vector" not in p])
        n = 0
        for i in range(0, len(points), batch_size):
            batch = [{"id": p["id"], "payload": p["payload"],
                      "vector": p["vector"] if "vector" in p else self.embedder.embed(p["text"])}
                     for p in points[i:i + batch_size]]
            self.client.upsert(self.collection, batch)
            n += len(batch)
        return n

    def delete(self, query_filter: dict[str, Any]) -> dict[str, Any]:
        return self.client.delete_by_filter(self.collection, query_filter)

    def health(self) -> dict[str, Any]:
        return self.client.health()

    def get(self, knowledge_id: str, version: str | None = None) -> dict[str, Any]:
        pid = _stable_uuid(knowledge_id, version or self.version)
        return self.client.retrieve(self.collection, pid)

    def delete_screen(self, screen_id: str) -> dict[str, Any]:
        return self.client.delete_by_filter(
            self.collection,
            {"must": [{"key": "screen_id", "match": {"value": screen_id}}]},
        )
