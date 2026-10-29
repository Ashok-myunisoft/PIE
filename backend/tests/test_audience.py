"""Answer audience (Marketing, Client non-technical/technical, Developer, Stakeholder): same knowledge, different presentation."""
import pytest

from pie_agent import chat as chat_mod
from pie_agent.chat import AUDIENCES, MarketingChat, system_prompt
from test_phase19_chat import FakeLLM, kms  # noqa: F401  (kms is a fixture)


class SpyLLM(FakeLLM):
    def chat(self, system, messages):
        self.system = system
        return super().chat(system, messages)


QUESTION = "Tell me about the BOM business rules and gate entry"


def test_grounding_rules_are_the_same_for_every_audience():
    rules = lambda p: [l for l in p.splitlines() if l[:2] in {f"{i}." for i in (1, 2, 3, 4, 5, 6, 8, 9)}]
    base = rules(system_prompt("marketing"))
    assert len(base) == 8
    for key in AUDIENCES:
        assert rules(system_prompt(key)) == base                      # only intro, rule 7 and style change
    assert "Marketing Intelligence Assistant" in system_prompt("marketing") and "marketing-friendly" in system_prompt("marketing")
    assert "Keep technical names exactly" in system_prompt("developer")
    assert "Do not mention table, column, API" in system_prompt("client_nontechnical")
    assert "executive summary" in system_prompt("stakeholder")
    assert system_prompt("nonsense") == system_prompt("marketing") == chat_mod.SYSTEM_PROMPT


@pytest.mark.parametrize("audience", ["client_nontechnical", "stakeholder", "marketing"])
def test_audience_changes_presentation_not_knowledge(kms, audience):
    base = MarketingChat(kms, SpyLLM("BOM rules [K1].")).answer(QUESTION)
    llm = SpyLLM("BOM rules [K1].")
    r = MarketingChat(kms, llm).answer(QUESTION, audience=audience)
    label = AUDIENCES[audience]["label"]
    assert r["audience"] == {"key": audience, "label": label} and r["plan"]["audience"] == audience
    assert f"AUDIENCE: {label}." in llm.calls[-1][1] and llm.system == system_prompt(audience)
    assert r["trace"]["steps"][0]["audience"] == label
    # Non-technical audiences retrieve exactly the same knowledge items.
    assert [i["knowledgeId"] for i in r["trace"]["retrieved"]] == [i["knowledgeId"] for i in base["trace"]["retrieved"]]


def test_technical_audiences_add_relationships_and_evidence(kms, monkeypatch):
    import pie_agent.relations as relations
    fake = {"screen": {"label": "BOM", "module": "inventory"}, "tables": [
                {"name": "MBOM", "operations": ["READ", "WRITE"], "fields": [{"label": "BOM Name", "column": "BOMNAME"}]}],
            "apis": [{"method": "POST", "route": "/BOM/SaveBOM", "endpoints": [
                {"name": "SaveBOM", "file": "InventorySL/SaveBOM.cs", "services": [{"name": "BOMBLL"}]}]}]}
    monkeypatch.setattr(relations, "screen_relations", lambda sid, *a, **k: fake if sid == "INVENTORY_BOM" else None)
    base = MarketingChat(kms, SpyLLM("x [K1].")).answer(QUESTION)
    llm = SpyLLM("x [K1].")
    r = MarketingChat(kms, llm).answer(QUESTION, audience="developer")
    extra = [i for i in r["trace"]["retrieved"] if i["knowledgeType"] == "RelationshipGraph"]
    assert [i["knowledgeId"] for i in extra] == ["REL-INVENTORY_BOM"]
    assert "POST /BOM/SaveBOM -> SaveBOM (InventorySL/SaveBOM.cs) using BOMBLL" in llm.calls[-1][1]
    assert "Tables written: MBOM" in llm.calls[-1][1] and "BOM Name = BOMNAME" in llm.calls[-1][1]
    assert "Evidence: BOMBLL.cs (exception)" in llm.calls[-1][1]            # item evidence shown to technical readers
    # Everything else is the same knowledge as for Marketing.
    others = [i["knowledgeId"] for i in r["trace"]["retrieved"] if i["knowledgeType"] != "RelationshipGraph"]
    assert sorted(others) == sorted(i["knowledgeId"] for i in base["trace"]["retrieved"])
    assert "Evidence:" not in MarketingChat(kms, llm).answer(QUESTION, audience="stakeholder") and "Evidence:" not in llm.calls[-1][1]


def test_feature_questions_are_written_for_the_audience(kms):
    q = "What does the Inventory module provide?"
    assert MarketingChat(kms, SpyLLM("x [K1].")).answer(q)["plan"]["generator"] == "structured"      # Marketing: catalog list
    llm = SpyLLM("Inventory keeps your items and stock organised [K1].")
    r = MarketingChat(kms, llm).answer(q, audience="client_nontechnical")
    assert r["plan"]["generator"] == "fake" and "AUDIENCE: Client (non-technical)." in llm.calls[-1][1]
    r = MarketingChat(kms, SpyLLM("x")).answer("Give me the feature list of Inventory", audience="developer")
    assert r["plan"]["generator"] == "structured"                                                   # an explicit list request


def test_chat_api_audience(kms, monkeypatch):
    from fastapi.testclient import TestClient
    import pie_agent.api as api
    monkeypatch.setattr(api, "_CHAT", MarketingChat(kms))
    c = TestClient(api.app)
    r = c.post("/usage/api/chat", json={"message": "Tell me about BOM", "audience": "client_technical"}).json()
    assert r["audience"]["label"] == "Client (technical)"
    assert c.post("/usage/api/chat", json={"message": "Tell me about BOM"}).json()["audience"]["key"] == "marketing"
    assert c.post("/usage/api/chat", json={"message": "x", "audience": "ceo"}).status_code == 400
