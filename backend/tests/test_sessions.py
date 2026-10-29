"""Chat sessions: new/open/continue/rename/archive/delete/search, per-user ownership, titles, and strict isolation."""
from fastapi.testclient import TestClient

from pie_agent import sessions as S
from pie_agent.chat import MarketingChat
from test_phase19_chat import FakeLLM, kms  # noqa: F401  (kms is a fixture)


class HistorySpy(FakeLLM):
    """Records the conversation each answer was given."""
    def chat(self, system, messages):
        self.seen = [m["content"][:60] for m in messages[:-1]]
        return super().chat(system, messages)


def _client(kms, monkeypatch, llm=None):
    import pie_agent.api as api
    monkeypatch.setattr(api, "_CHAT", MarketingChat(kms, llm))
    return TestClient(api.app)


def test_new_chat_is_saved_titled_and_continued(kms, monkeypatch):
    c = _client(kms, monkeypatch)
    r = c.post("/usage/api/chat", json={"message": "What are the top BOM features for the pump industry?", "modules": ["inventory"],
                                        "newSession": True, "userId": "anna"}).json()
    s = r["session"]
    assert s["messageCount"] == 2 and s["modules"] == ["inventory"] and s["audience"] == "marketing"
    assert s["title"].startswith("Inventory – ") and "Pump" in s["title"] and s["titleAuto"]
    r2 = c.post("/usage/api/chat", json={"message": "And the gate entry rules?", "sessionId": s["sessionId"],
                                         "modules": ["inventory"], "userId": "anna"}).json()
    assert r2["session"]["messageCount"] == 4 and r2["session"]["title"] == s["title"]   # the title is set once
    full = c.get(f"/usage/api/sessions/{s['sessionId']}", params={"userId": "anna"}).json()
    assert [m["role"] for m in full["messages"]] == ["user", "assistant", "user", "assistant"]
    assert full["messages"][1]["response"]["answer"] and full["messages"][1]["response"]["trace"]["steps"]


def test_sessions_never_mix_history(kms, monkeypatch):
    llm = HistorySpy("BOM rules [K1].")
    c = _client(kms, monkeypatch, llm)
    a = c.post("/usage/api/chat", json={"message": "Tell me about BOM business rules", "newSession": True, "userId": "anna"}).json()["session"]
    b = c.post("/usage/api/chat", json={"message": "Tell me about gate entry", "newSession": True, "userId": "anna",
                                        "audience": "developer"}).json()["session"]
    assert llm.seen == []                                                  # a new session starts with no history
    # The browser sends a stale history from session A; the server uses session B's own history instead.
    c.post("/usage/api/chat", json={"message": "and its fields?", "sessionId": b["sessionId"], "userId": "anna",
                                    "history": [{"role": "user", "content": "Tell me about BOM business rules"}]})
    assert llm.seen == ["Tell me about gate entry", "BOM rules [K1]."]
    c.post("/usage/api/chat", json={"message": "and more?", "sessionId": a["sessionId"], "userId": "anna"})
    assert llm.seen[0] == "Tell me about BOM business rules"
    assert S.get_session(b["sessionId"], "anna", with_messages=False)["audience"] == "developer"


def test_rename_archive_search_delete_and_ownership(kms, monkeypatch):
    c = _client(kms, monkeypatch)
    s = c.post("/usage/api/chat", json={"message": "Explain the Item Master screen", "newSession": True, "userId": "anna"}).json()["session"]
    sid = s["sessionId"]
    assert c.patch(f"/usage/api/sessions/{sid}", json={"userId": "anna", "title": "My item notes"}).json()["title"] == "My item notes"
    assert [x["sessionId"] for x in c.get("/usage/api/sessions", params={"userId": "anna", "q": "Item Master"}).json()["sessions"]] == [sid]
    assert c.get("/usage/api/sessions", params={"userId": "anna", "q": "payroll"}).json()["sessions"] == []
    # Another user can neither see nor open, rename, continue or delete it.
    assert c.get("/usage/api/sessions", params={"userId": "ben"}).json()["sessions"] == []
    assert c.get(f"/usage/api/sessions/{sid}", params={"userId": "ben"}).status_code == 404
    assert c.post("/usage/api/chat", json={"message": "x", "sessionId": sid, "userId": "ben"}).status_code == 404
    assert c.delete(f"/usage/api/sessions/{sid}", params={"userId": "ben"}).status_code == 404
    # The web server's signed-in user (X-PIE-User) takes precedence over the browser id.
    assert c.get("/usage/api/sessions", params={"userId": "ben"}, headers={"X-PIE-User": "anna"}).json()["sessions"]
    c.patch(f"/usage/api/sessions/{sid}", json={"userId": "anna", "archived": True})
    assert c.get("/usage/api/sessions", params={"userId": "anna"}).json()["sessions"] == []
    assert len(c.get("/usage/api/sessions", params={"userId": "anna", "archived": True}).json()["sessions"]) == 1
    assert c.delete(f"/usage/api/sessions/{sid}", params={"userId": "anna"}).status_code == 200
    assert c.get(f"/usage/api/sessions/{sid}", params={"userId": "anna"}).status_code == 404


def test_import_browser_conversation_and_legacy_chat(kms, monkeypatch):
    c = _client(kms, monkeypatch)
    s = c.post("/usage/api/sessions", json={"userId": "anna", "modules": ["ess"], "messages": [
        {"role": "user", "content": "How do I apply leave?"}, {"role": "assistant", "response": {"answer": "Use Leave Request [K1]."}}]}).json()
    assert s["messageCount"] == 2 and s["title"].startswith("ESS – ")
    assert S.history(s["sessionId"], "anna") == [{"role": "user", "content": "How do I apply leave?"},
                                                   {"role": "assistant", "content": "Use Leave Request [K1]."}]
    r = c.post("/usage/api/chat", json={"message": "Tell me about BOM"}).json()          # no session: nothing is stored
    assert "session" not in r and c.get("/usage/api/sessions").json()["sessions"] == []
    assert c.get("/usage/api/sessions", params={"userId": "bad id!"}).status_code == 400


def test_titles():
    assert S.rule_title("What are the top features for the pump industry?", "Inventory") == "Inventory – Features Pump Industry"
    assert S.scope_label([], "Marketing Team") == "Marketing" and S.scope_label(["ess"], None) == "ESS"

    class TitleLLM:
        def generate(self, prompt):
            return '"Leave Application Process."'
    assert S.make_title("how do I apply for leave", ["ess"], None, TitleLLM()) == "ESS – Leave Application Process"
    assert S.make_title("how do I apply for leave", ["ess"], None, None) == "ESS – Apply Leave"
