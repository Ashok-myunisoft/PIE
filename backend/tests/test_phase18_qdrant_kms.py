import json
from pathlib import Path

from pie_agent.qdrant_kms import (
    EmbeddingProvider, QdrantKMS, package_points, manifest_from_knowledge_dir
)
from pie_agent.capability_model import build_capability_model


class FakeQdrant:
    def __init__(self):
        self.collections = {}
        self.points = {}

    def health(self):
        return {"title": "fake-qdrant"}

    def collection(self, name):
        return {"result": {"status": "green", "points_count": len(self.points.get(name, {}))}}

    def create_collection(self, name, vector_size):
        self.collections[name] = vector_size
        self.points.setdefault(name, {})
        return {"result": True}

    def ensure_collection(self, name, vector_size):
        if name not in self.collections:
            self.create_collection(name, vector_size)
        return self.collection(name)

    def upsert(self, name, points):
        self.points.setdefault(name, {})
        for p in points:
            self.points[name][p["id"]] = p
        return {"status": "ok"}

    def search(self, name, vector, limit=10, query_filter=None):
        rows = list(self.points.get(name, {}).values())
        def ok(p):
            if not query_filter:
                return True
            for cond in query_filter.get("must", []):
                key = cond["key"]
                val = cond["match"]["value"]
                if p["payload"].get(key) != val:
                    return False
            return True
        rows = [p for p in rows if ok(p)]
        def sim(p):
            a, b = vector, p["vector"]
            return sum(x*y for x,y in zip(a,b))
        rows.sort(key=sim, reverse=True)
        return {"result": [{"id": p["id"], "score": sim(p), "payload": p["payload"]} for p in rows[:limit]]}

    def retrieve(self, name, point_id):
        p = self.points.get(name, {}).get(point_id)
        return {"result": [p] if p else []}

    def delete_by_filter(self, name, query_filter):
        return {"status": "ok"}


def _pkg():
    return {
        "screen": {
            "screenId": "LEAVE.LEAVE_APPLICATION",
            "module": "leave",
            "functionality": "Leave Management",
            "feature": "Leave Application",
            "screenName": "Leave Application",
        },
        "status": "READY",
        "sourceHash": "abc",
        "generatedAt": "2026-09-22T00:00:00Z",
        "records": [{
            "knowledgeId": "KNO-1",
            "screenId": "LEAVE.LEAVE_APPLICATION",
            "functionality": "Leave Management",
            "feature": "Leave Application",
            "knowledgeType": "BusinessSummary",
            "title": "Leave Application",
            "content": "Employee leave application knowledge.",
            "confidence": 0.95,
            "needsReview": False,
            "sourceFactIds": ["E1"],
            "evidence": [{"source_type": "Evidence Graph"}],
            "generationMethod": "ai",
            "claimType": "observed",
        }]
    }


def test_qdrant_kms_idempotent_and_filterable():
    fake = FakeQdrant()
    kms = QdrantKMS(fake, "pie_knowledge")
    r1 = kms.publish_packages([_pkg()])
    r2 = kms.publish_packages([_pkg()])
    assert r1["records"] == 1
    assert r2["records"] == 1
    assert fake.collection("pie_knowledge")["result"]["points_count"] == 1
    result = kms.search("employee leave application")
    assert result["result"]
    assert result["result"][0]["payload"]["screen_id"] == "LEAVE.LEAVE_APPLICATION"


def test_capability_review_does_not_block_publication():
    p = _pkg()
    points = package_points(p, EmbeddingProvider())
    assert points[0]["payload"]["capability_needs_review"] is True
    assert points[0]["payload"]["review_required"] is False
    assert points[0]["payload"]["publication_status"] == "PUBLISHED"


def test_review_records_are_stored_but_default_retrieval_can_exclude_them():
    p = _pkg()
    p["records"][0]["needsReview"] = True
    points = package_points(p, EmbeddingProvider())
    assert points[0]["payload"]["publication_status"] == "REVIEW_REQUIRED"
    assert points[0]["payload"]["knowledge_review_required"] is True


def test_v17_directory_can_be_ingested_without_openai():
    # The fixture is deliberately tiny; the production command scans all v0.17
    # per-screen JSON files.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "knowledge"
        d.mkdir()
        (d / "S1.json").write_text(json.dumps(_pkg()), encoding="utf-8")
        m = manifest_from_knowledge_dir(d)
        assert m["totalScreens"] == 1
        assert m["totalRecords"] == 1


def test_capability_candidate_is_explicitly_reviewed(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    (d / "S1.json").write_text(json.dumps(_pkg()), encoding="utf-8")
    out = tmp_path / "out"
    r = build_capability_model(d, out)
    assert r["entries"][0]["capability"] == "Leave Management"
    assert r["entries"][0]["capabilityBasis"] == "derived_from_functionality_candidate"
    assert r["entries"][0]["capabilityNeedsReview"] is True
