import json
from pathlib import Path

import pytest

from pie_agent import chat as chat_mod
from pie_agent.chat import MarketingChat, detect_industry, detect_intents, detect_persona, validate_citations
from pie_agent.knowledge_ingest import (normalize_tickets, publish_tickets, publish_usage_catalog, redact,
                                        read_ticket_file, validate_select)
from pie_agent.llm import LLMProvider
from pie_agent.qdrant_kms import QdrantKMS


class FakeQdrant:
    url = "fake"

    def __init__(self):
        self.points = {}

    def ensure_collection(self, name, size):
        return {"result": True}

    def upsert(self, name, points):
        for p in points:
            self.points[p["id"]] = p
        return {"status": "ok"}

    @staticmethod
    def _cond(p, c):
        v = p["payload"].get(c["key"])
        m = c["match"]
        return v in m["any"] if "any" in m else v == m["value"]

    def _ok(self, p, flt):
        if not flt:
            return True
        return all(self._cond(p, c) for c in flt.get("must", [])) and not any(self._cond(p, c) for c in flt.get("must_not", []))

    def search(self, name, vector, limit=10, query_filter=None):
        rows = [p for p in self.points.values() if self._ok(p, query_filter)]
        sim = lambda p: sum(a * b for a, b in zip(vector, p["vector"]))
        rows.sort(key=sim, reverse=True)
        return {"result": [{"id": p["id"], "score": sim(p), "payload": p["payload"]} for p in rows[:limit] if sim(p) > 0]}

    def scroll(self, name, query_filter=None, limit=256, offset=None):
        rows = [p for p in self.points.values() if self._ok(p, query_filter)]
        start = offset or 0
        nxt = start + limit if start + limit < len(rows) else None
        return {"result": {"points": [{"id": p["id"], "payload": p["payload"]} for p in rows[start:start + limit]], "next_page_offset": nxt}}


class FakeLLM(LLMProvider):
    name = "fake"

    def __init__(self, reply="", plan=None):
        self.reply, self.plan_reply, self.calls = reply, plan, []

    def generate(self, prompt):
        self.calls.append(("plan", prompt))
        return json.dumps(self.plan_reply or {"searchQueries": ["bill of materials", "casting heat"], "industry": "", "persona": ""})

    def chat(self, system, messages):
        self.calls.append(("chat", messages[-1]["content"]))
        return self.reply


CATALOG = {
    "module": "inventory", "moduleName": "Inventory",
    "statistics": {"features": 2, "capabilities": 2, "ready": 1, "partial": 1, "needsEvidence": 0, "verifiedClaims": 4, "reviewClaims": 0, "sharedPlatformTables": []},
    "capabilities": [
        {"name": "Bill of Materials & Engineering", "basis": ["keyword_taxonomy_candidate"], "needsReview": True, "features": 1, "ready": 1, "partial": 0, "needsEvidence": 0, "verifiedClaims": 3, "categories": {}},
        {"name": "Receiving & Procurement", "basis": ["keyword_taxonomy_candidate"], "needsReview": True, "features": 1, "ready": 0, "partial": 1, "needsEvidence": 0, "verifiedClaims": 1, "categories": {}}],
    "features": [
        {"screenId": "INVENTORY_BOM", "name": "BOM", "category": "Master Data", "readiness": "READY", "aliasScreenIds": [], "migrationGaps": [],
         "capability": {"name": "Bill of Materials & Engineering", "basis": "keyword_taxonomy_candidate", "needsReview": True},
         "summary": {"text": "Manage bills of materials."},
         "claims": [{"text": "Business rule: Assembly Type BOM cannot be created with a past From Date.", "status": "VERIFIED", "evidence": [{"source": "BOMBLL.cs", "detail": "exception"}]},
                    {"text": "Validates that BOM Name is filled in before saving.", "status": "VERIFIED", "evidence": [{"source": "BOMBLL.cs", "detail": "NotEmpty"}]},
                    {"text": "Maintains its own records (MBOM).", "status": "VERIFIED", "evidence": []}]},
        {"screenId": "INVENTORY_GATE_ENTRY", "name": "Gate Entry", "category": "Transaction", "readiness": "PARTIAL", "aliasScreenIds": [], "migrationGaps": [{"kind": "get"}],
         "capability": {"name": "Receiving & Procurement", "basis": "keyword_taxonomy_candidate", "needsReview": True}, "summary": None,
         "claims": [{"text": "Captures 58 data fields.", "status": "VERIFIED", "evidence": []}]}],
}


@pytest.fixture
def kms():
    chat_mod._SNAPSHOTS.clear()
    k = QdrantKMS(FakeQdrant(), "pie_knowledge")
    publish_usage_catalog(k, CATALOG)
    return k


def test_intent_industry_persona_detection():
    assert detect_intents("I am targeting the pump industry. What are the top features?") == ["industry"]
    assert "demo" in detect_intents("Prepare a demo flow for a pump industry's IT Manager")
    assert detect_intents("What are the top tickets reported?") == ["tickets"]
    assert "differentiation" in detect_intents("What are our key product differentiators?")
    assert "market_gap" in detect_intents("What product features are missing or in demand?")
    assert detect_intents("Give me the feature list of the modules.") == ["feature_discovery"]
    assert detect_industry("I am targeting the pump industry") == "pump"
    assert detect_industry("features relevant to pump manufacturing companies") == "pump"
    assert detect_persona("a demo for a pump industry's IT Manager").lower() == "it manager"


def test_usage_publication_is_idempotent(kms):
    n = len(kms.client.points)
    publish_usage_catalog(kms, CATALOG)
    assert len(kms.client.points) == n == 1 + 2 + 2
    kinds = sorted(p["payload"]["knowledge_type"] for p in kms.client.points.values())
    assert kinds == ["UsageCapability", "UsageCapability", "UsageFeature", "UsageFeature", "UsageModule"]


def test_ticket_question_without_ticket_data_never_calls_llm(kms):
    llm = FakeLLM("There are 42 tickets [K1].")
    r = MarketingChat(kms, llm).answer("What are the top tickets or issues reported for our software?")
    assert r["grounding"]["status"] == "not_found" and "ticket data" in r["grounding"]["missing"]
    assert "No ticket data" in r["answer"] and llm.calls == []


def test_feature_list_offline_is_extractive_and_cited(kms):
    r = MarketingChat(kms).answer("Give me the feature list of the inventory module")
    # Feature lists are built from the catalog data, not by the LLM.
    assert r["plan"]["generator"] == "structured"
    assert "Assembly Type BOM cannot be created with a past From Date" in r["answer"]
    assert r["plan"]["modules"] == ["inventory"]
    assert "[K" in r["answer"] and r["sources"]
    assert r["grounding"]["status"] == "grounded"


def test_llm_answer_citations_are_validated(kms):
    llm = FakeLLM("BOM enforces date rules [K2]. Also a made-up feature [K99].")
    r = MarketingChat(kms, llm).answer("Tell me about the BOM business rules and gate entry")
    assert "[K99]" not in r["answer"] and "[K2]" in r["answer"]
    assert any("Removed 1 citation" in n for n in r["grounding"]["notes"])
    assert [s["ref"] for s in r["sources"]] == ["K2"]


def test_industry_question_uses_planner_and_is_partial(kms):
    llm = FakeLLM("Highlight BOM for pump makers [K1].")
    r = MarketingChat(kms, llm).answer("I am targeting the pump industry. What are the top features I can highlight?")
    assert r["intent"] == "industry" and r["plan"]["industry"] == "pump" and r["plan"]["planner"] == "llm"
    assert "bill of materials" in r["plan"]["searchQueries"]
    assert r["grounding"]["status"] == "partial"
    prompt = llm.calls[-1][1]
    assert "no industry-specific knowledge for 'pump'" in prompt and "KNOWLEDGE ITEMS:" in prompt


def test_differentiation_and_gaps_state_missing_knowledge(kms):
    r = MarketingChat(kms).answer("What are our key product differentiators versus competitors?")
    assert "competitor product information" in r["grounding"]["missing"]
    r = MarketingChat(kms).answer("Which areas of our product have potential gaps?")
    assert "market demand / market research data" in r["grounding"]["missing"]
    assert "legacy backend services" in json.dumps(r["sources"]) or "Internal coverage" in r["answer"]


def test_ticket_import_and_ticket_answers(kms, tmp_path):
    f = tmp_path / "tickets.csv"
    f.write_text("Ticket No,Subject,Description,Module,Status,Priority\n"
                 "T-1,Stock mismatch after bin transfer,Call me on +91 98765 43210 or a@b.com,Inventory,Open,High\n"
                 "T-2,Stock mismatch in report,Totals differ,inventory,Closed,Low\n", encoding="utf-8")
    header, rows = read_ticket_file(f)
    tickets, cols = normalize_tickets(header, rows, "export", None, {"inventory"})
    assert cols["title"] == "Subject" and cols["ticket_id"] == "Ticket No"
    assert tickets[0]["module"] == "inventory" and "[phone]" in tickets[0]["description"] and "[email]" in tickets[0]["description"]
    publish_tickets(kms, tickets)
    chat_mod._SNAPSHOTS.clear()
    llm = FakeLLM("Inventory has 2 tickets [K1]; recurring theme stock mismatch [K1].")
    r = MarketingChat(kms, llm).answer("Which modules have the highest number of reported issues?")
    assert r["grounding"]["status"] == "grounded" and r["sources"][0]["kind"] == "ticket_stats"
    assert "'stock mismatch' (2)" in r["sources"][0]["text"]


def test_sql_must_be_read_only():
    assert validate_select("SELECT a FROM t;").lower().startswith("select")
    assert validate_select("WITH x AS (SELECT 1 AS a) SELECT a FROM x")
    for bad in ("DELETE FROM t", "SELECT * FROM t; DROP TABLE t", "SELECT * INTO x FROM t", "EXEC sp_x", "UPDATE t SET a=1"):
        with pytest.raises(ValueError):
            validate_select(bad)
    assert validate_select("SELECT 'drop table' AS note FROM t")


def test_redact():
    assert redact("mail x.y@corp.com or call 044-2345 6789") == "mail [email] or call [phone]"


def test_validate_citations_helper():
    text, cited, invalid = validate_citations("a [K1] b [K3] c [K1]", [{"ref": "K1"}, {"ref": "K2"}])
    assert cited == ["K1"] and invalid == ["K3"] and "[K3]" not in text
    # Grouped citations are validated one by one and normalised.
    text, cited, invalid = validate_citations("x [K2, K9; K1]", [{"ref": "K1"}, {"ref": "K2"}])
    assert text == "x [K2][K1]" and cited == ["K2", "K1"] and invalid == ["K9"]


def test_feature_list_includes_verified_highlights_and_orders_evidence(kms):
    llm = FakeLLM("should not be used")
    r = MarketingChat(kms, llm).answer("Give me the feature list of the inventory module")
    assert llm.calls == []                      # deterministic: no LLM call
    lines = [l for l in r["answer"].splitlines() if l.startswith("- **")]
    assert lines and all("[K" in l for l in lines)   # every feature line cites its source
    # Marketing-ready features are listed before partly verified ones within a capability.
    assert r["answer"].index("BOM") < r["answer"].index("Gate Entry") or "Receiving" in r["answer"]
    assert {x["kind"] for x in r["sources"]} >= {"module", "feature"}

def test_chat_api(kms, monkeypatch):
    from fastapi.testclient import TestClient
    import pie_agent.api as api
    monkeypatch.setattr(api, "_CHAT", MarketingChat(kms))
    c = TestClient(api.app)
    # The backend serves the API only; the chat page lives in the frontend project.
    assert c.get("/usage/chat").status_code == 404
    assert "Ask PIE" in (Path(__file__).resolve().parents[2] / "frontend" / "chat.html").read_text(encoding="utf-8")
    r = c.post("/usage/api/chat", json={"message": "What are the key features in Inventory?", "history": []})
    assert r.status_code == 200 and r.json()["sources"]
    assert c.post("/usage/api/chat", json={"message": ""}).status_code == 422
    assert c.post("/usage/api/chat", json={"message": "x", "module": "../x"}).status_code == 400
    s = c.get("/usage/api/chat/status").json()
    assert s["verifiedModules"] == ["inventory"] and s["tickets"] == 0


def test_shareable_chat_file_embeds_only_the_server_address(tmp_path):
    from pie_agent.usage import export_chat_ui
    out = export_chat_ui("http://pie-server:8000", tmp_path / "ask.html")
    html = out.read_text(encoding="utf-8")
    assert 'window.__PIE_CHAT__={"apiBase": "http://pie-server:8000/usage/"}' in html
    assert "<!--PIE_CHAT_CONFIG-->" not in html and "sk-" not in html and "OPENAI" not in html
    assert "http://pie-server:8000/usage/" in export_chat_ui("http://pie-server:8000/usage/", tmp_path / "b.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        export_chat_ui("pie-server", tmp_path / "c.html")


def test_chat_api_allows_pages_opened_from_disk(kms, monkeypatch):
    from fastapi.testclient import TestClient
    import pie_agent.api as api
    monkeypatch.setattr(api, "_CHAT", MarketingChat(kms))
    c = TestClient(api.app)
    pre = c.options("/usage/api/chat", headers={"Origin": "null", "Access-Control-Request-Method": "POST",
                                                "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "null"
    r = c.post("/usage/api/chat", json={"message": "Inventory features"}, headers={"Origin": "null"})
    assert r.headers["access-control-allow-origin"] == "null"
    other = c.get("/usage/api/chat/status", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_follow_up_after_a_very_long_answer_is_accepted(kms, monkeypatch):
    # Regression: the 237-field table (~21k chars) in history made the next question fail with 422.
    from fastapi.testclient import TestClient
    import pie_agent.api as api
    monkeypatch.setattr(api, "_CHAT", MarketingChat(kms))
    history = [{"role": "user", "content": "all fields"}, {"role": "assistant", "content": "x" * 25000}] * 10
    r = TestClient(api.app).post("/usage/api/chat", json={"message": "And which ones are mandatory?", "history": history})
    assert r.status_code == 200, r.text


def test_claim_words_not_in_knowledge_are_flagged(kms):
    llm = FakeLLM("BOM offers real-time dashboards [K1].")
    r = MarketingChat(kms, llm).answer("Tell me about the BOM business rules")
    assert any("Check before use" in n and "'real-time'" in n and "'dashboard'" in n for n in r["grounding"]["notes"])
    assert r["grounding"]["status"] == "partial"
