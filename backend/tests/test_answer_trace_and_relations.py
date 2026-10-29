"""Seeing how PIE answered (the answer trace), the relationships behind a screen, and traces kept in the activity log."""
import json

import pytest

from pie_agent import activity_log as log
from pie_agent import relations
from pie_agent.chat import MarketingChat, trace_digest
from test_phase19_chat import FakeLLM, kms  # noqa: F401  (kms is a fixture)


def test_llm_answer_trace_shows_every_step_item_and_citation(kms):
    llm = FakeLLM("BOM enforces date rules [K2]. Also a made-up feature [K99].")
    r = MarketingChat(kms, llm).answer("Tell me about the BOM business rules and gate entry")
    tr = r["trace"]
    assert [s["step"] for s in tr["steps"]] == ["understand", "plan", "retrieve", "coverage", "answer", "validate"]
    understand = tr["steps"][0]
    assert understand["intents"] and all("matchedWord" in i for i in understand["intents"])
    retrieve = tr["steps"][2]
    assert retrieve["kept"] == len(tr["retrieved"]) and retrieve["routes"] and all(it["route"] for it in tr["retrieved"])
    cited = [it["ref"] for it in tr["retrieved"] if it["cited"]]
    assert cited == [s["ref"] for s in r["sources"]] == ["K2"]
    validate = tr["steps"][-1]
    assert validate["invalidCitations"] == ["K99"] and validate["cited"] == ["K2"]
    # The exact text given to the model is visible, and the log digest leaves it (and item text) out.
    assert "KNOWLEDGE ITEMS:" in tr["context"] and "[K2]" in tr["context"] and tr["systemPrompt"]
    d = trace_digest(r)
    assert "context" not in d and d["contextChars"] == len(tr["context"]) and all("text" not in it for it in d["retrieved"])


def test_structured_and_stopped_answers_say_why(kms):
    r = MarketingChat(kms).answer("Give me the feature list of the inventory module")
    ans = [s for s in r["trace"]["steps"] if s["step"] == "answer"][0]
    assert ans["generator"] == "structured" and "feature list" in ans["rule"]
    assert r["trace"]["retrieved"] and all(it["cited"] for it in r["trace"]["retrieved"])
    llm = FakeLLM("unused")
    r = MarketingChat(kms, llm).answer("What are the top tickets or issues reported for our software?")
    ans = [s for s in r["trace"]["steps"] if s["step"] == "answer"][0]
    assert ans["generator"] == "none" and "ticket data" in ans["summary"] and llm.calls == []


def test_chat_api_keeps_the_trace_in_the_activity_log(kms, monkeypatch):
    from fastapi.testclient import TestClient
    import pie_agent.api as api
    monkeypatch.setattr(api, "_CHAT", MarketingChat(kms))
    c = TestClient(api.app)
    r = c.post("/usage/api/chat", json={"message": "What are the key features in Inventory?", "history": []}).json()
    run = log.get_run(r["runId"])
    assert run["activity"] == "retrieval" and run["status"] == "SUCCESS" and run["attrs"]["question"].startswith("What are")
    ev = [e for e in run["events"] if e["data"] and "trace" in e["data"]][0]
    assert [it["ref"] for it in ev["data"]["trace"]["retrieved"]] == [it["ref"] for it in r["trace"]["retrieved"]]


def _w(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def graph_root(tmp_path):
    root = tmp_path / "modules"
    nodes = [
        {"id": "screen:ESS_LR", "type": "Screen", "label": "Leave Request", "properties": {"screenId": "ESS_LR", "module": "ess", "status": "CONFIRMED"}},
        {"id": "screen:ESS_LB", "type": "Screen", "label": "Leave Balance", "properties": {"screenId": "ESS_LB", "module": "ess"}},
        {"id": "screen:OTHER_X", "type": "Screen", "label": "Other module screen", "properties": {"screenId": "OTHER_X", "module": "finance"}},
        {"id": "api:save", "type": "API", "label": "/TLeave/SaveTLeave", "properties": {"method": "POST", "route": "/TLeave/SaveTLeave"}},
        {"id": "api:bal", "type": "API", "label": "/TLeave/GetBalance", "properties": {"method": "GET", "route": "/TLeave/GetBalance"}},
        {"id": "ep:save", "type": "BackendEndpoint", "label": "SaveTLeave", "properties": {"className": "SaveTLeave", "file": "PayRollSL/SaveTLeave.cs"}},
        {"id": "ep:bal", "type": "BackendEndpoint", "label": "GetBalance", "properties": {"className": "GetBalance", "file": "PayRollSL/GetBalance.cs"}},
        {"id": "svc:bll", "type": "BackendService", "label": "TLeaveBLL", "properties": {"className": "TLeaveBLL", "file": "PayRollBLL/TLeaveBLL.cs"}},
        {"id": "db:tleave", "type": "DatabaseObject", "label": "DBO.TLEAVE", "properties": {"schema": "DBO", "name": "TLEAVE", "objectType": "TABLE"}},
        {"id": "db:memp", "type": "DatabaseObject", "label": "DBO.MEMPLOYEE", "properties": {"schema": "DBO", "name": "MEMPLOYEE", "objectType": "TABLE"}},
        {"id": "evidence:1", "type": "Evidence", "label": "leaverequest.json", "properties": {"source": "projects/ess/formjson/leaverequest.json", "fact": "form definition"}},
    ]
    edges = [
        {"source": "screen:ESS_LR", "relationship": "SUPPORTED_BY", "target": "evidence:1", "confidence": 1.0, "status": "OBSERVED", "evidence": []},
        {"source": "screen:ESS_LR", "relationship": "CALLS", "target": "api:save", "confidence": 0.92, "status": "OBSERVED",
         "evidence": [{"file": "formactionurls/saveurl.ts", "url": "/prs/TLeave.svc/"}]},
        {"source": "api:save", "relationship": "IMPLEMENTED_BY", "target": "ep:save", "confidence": 0.92, "status": "OBSERVED", "evidence": []},
        {"source": "ep:save", "relationship": "USES_SERVICE", "target": "svc:bll", "confidence": 0.98, "status": "OBSERVED", "evidence": []},
        {"source": "ep:save", "relationship": "READS_OR_WRITES", "target": "db:tleave", "confidence": 1.0, "status": "CANDIDATE",
         "evidence": [{"kind": "TABLE_OR_VIEW", "evidence": "INTO TLEAVE", "operations": ["WRITE"]}]},
        {"source": "svc:bll", "relationship": "READS_OR_WRITES", "target": "db:memp", "confidence": 1.0, "status": "CANDIDATE",
         "evidence": [{"kind": "TABLE_OR_VIEW", "evidence": "JOIN MEMPLOYEE", "operations": ["READ"]}]},
        {"source": "screen:ESS_LB", "relationship": "CALLS", "target": "api:bal", "confidence": 0.9, "status": "OBSERVED", "evidence": []},
        {"source": "api:bal", "relationship": "IMPLEMENTED_BY", "target": "ep:bal", "confidence": 0.9, "status": "OBSERVED", "evidence": []},
        {"source": "ep:bal", "relationship": "READS_OR_WRITES", "target": "db:tleave", "confidence": 1.0, "status": "CANDIDATE",
         "evidence": [{"kind": "TABLE_OR_VIEW", "evidence": "FROM TLEAVE", "operations": ["READ"]}]},
    ]
    _w(root / "ess" / "phase16-evidence-graph" / "evidence_graph_v16.json", {"generatedAt": "2026-09-27T00:00:00Z", "nodes": nodes, "edges": edges})
    _w(root / "ess" / "phase20-fields" / "leaverequest_fields_v20.json", {
        "screenId": "ESS_LR", "screen": "Leave Request", "form": "leaverequest", "entity": "TLeave", "table": "TLEAVE",
        "fields": [{"fieldName": "TLeaveFromDate", "label": "From Date", "database": {"table": "TLEAVE", "column": "FROMDATE", "dataType": "datetime"}},
                   {"fieldName": "Reason", "label": "Reason", "database": {}}]})
    relations._CACHE.clear()
    return root


def test_screen_relationships_follow_the_graph_with_evidence(graph_root):
    points = [{"screen_id": "ESS_LR", "knowledge_type": "FieldKnowledge", "title": "From Date", "publication_status": "PUBLISHED"},
              {"screen_id": "ESS_LR", "knowledge_type": "UsageFeature", "title": "Leave Request", "publication_status": "PUBLISHED"},
              {"screen_id": "ESS_LB", "knowledge_type": "UsageFeature", "title": "Leave Balance"}]
    r = relations.screen_relations("ESS_LR", points, graph_root)
    assert r["stats"] == {"apis": 1, "endpoints": 1, "services": 1, "tables": 2, "writes": 1, "evidence": 1, "fieldsMapped": 1}
    api = r["apis"][0]
    assert api["route"] == "/TLeave/SaveTLeave" and api["evidence"][0]["where"] == "formactionurls/saveurl.ts"
    assert api["endpoints"][0]["services"][0]["name"] == "TLeaveBLL"
    t = {x["name"]: x for x in r["tables"]}
    assert t["TLEAVE"]["operations"] == ["WRITE"] and t["TLEAVE"]["statuses"] == ["CANDIDATE"]
    assert t["TLEAVE"]["fields"] == [{"label": "From Date", "field": "TLeaveFromDate", "column": "FROMDATE", "dataType": "datetime"}]
    assert [o["label"] for o in t["TLEAVE"]["alsoUsedBy"]] == ["Leave Balance"]   # the other screen reading TLEAVE
    assert t["MEMPLOYEE"]["operations"] == ["READ"]                                # reached through the service
    assert r["tables"][0]["name"] == "TLEAVE"                                      # stored fields first
    assert r["knowledge"]["FieldKnowledge"]["count"] == 1 and "UsageFeature" in r["knowledge"]
    assert relations.screen_relations("NOPE", None, graph_root) is None


def test_screen_list_keeps_the_module_screens(graph_root):
    listing = relations.list_screens(graph_root)
    ids = [s["screenId"] for s in listing["screens"]]
    assert ids == ["ESS_LB", "ESS_LR"] and listing["modules"] == ["ess"]   # the finance screen node is not an ESS screen
    lr = [s for s in listing["screens"] if s["screenId"] == "ESS_LR"][0]
    assert (lr["apis"], lr["tables"], lr["hasFields"]) == (1, 2, True)


def test_relations_api(graph_root, kms, monkeypatch):
    from fastapi.testclient import TestClient
    import pie_agent.api as api
    monkeypatch.setattr(api, "_modules_root", lambda: graph_root)
    monkeypatch.setattr(api, "_CHAT", MarketingChat(kms))
    c = TestClient(api.app)
    assert [s["screenId"] for s in c.get("/usage/api/relations/screens").json()["screens"]] == ["ESS_LB", "ESS_LR"]
    r = c.get("/usage/api/relations/screen/ESS_LR").json()
    assert r["screen"]["label"] == "Leave Request" and r["knowledge"] == {}
    assert c.get("/usage/api/relations/screen/NOPE").status_code == 404
    assert c.get("/usage/api/relations/screen/bad%20id").status_code == 400
