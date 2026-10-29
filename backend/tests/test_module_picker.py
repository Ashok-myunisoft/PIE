"""Module picker: answers limited to one, several or all modules; the shared picker script in exported pages."""
from pie_agent import chat as chat_mod
from pie_agent.chat import MarketingChat, selected_modules
from pie_agent.knowledge_ingest import publish_usage_catalog
from test_phase19_chat import CATALOG, FakeLLM
from test_phase20_module_fields import ess, ess_kms  # noqa: F401  (fixtures)


def test_selected_modules_parsing():
    assert selected_modules(None) == [] and selected_modules("") == []
    assert selected_modules("ESS, inventory,ess") == ["ess", "inventory"]
    assert selected_modules(["inventory", "", "ess"]) == ["inventory", "ess"]


def _two_modules(kms):
    publish_usage_catalog(kms, CATALOG)      # adds the inventory module next to ESS
    chat_mod._SNAPSHOTS.clear()
    return kms


def test_selection_is_the_scope_and_a_named_module_narrows_it(ess_kms):
    kms = _two_modules(ess_kms)
    bot = MarketingChat(kms, FakeLLM("From Date is a date [K1]."))
    r = bot.answer("What is the From Date field?", module=["ess", "inventory"])
    assert r["plan"]["modules"] == ["ess", "inventory"]
    step = r["trace"]["steps"][0]
    assert step["selectedModules"] == ["ess", "inventory"] and step["moduleSource"] == "module selector (2 modules)"
    r = bot.answer("Tell me about the inventory features and BOM rules", module=["ess", "inventory"])
    assert r["plan"]["modules"] == ["inventory"]      # named in the question, within the selection
    r = bot.answer("Tell me about BOM rules", module="inventory,ess")
    assert r["plan"]["modules"] == ["inventory", "ess"]


def test_screens_outside_the_selection_are_not_used(ess_kms):
    kms = _two_modules(ess_kms)
    llm = FakeLLM("Nothing about it [K1].")
    r = MarketingChat(kms, llm).answer("What is the From Date field in Leave Request?", module=["inventory"])
    retrieve = [s for s in r["trace"]["steps"] if s["step"] == "retrieve"][0]
    assert retrieve["namedScreens"] == []                           # Leave Request is an ESS screen
    assert not any(it["module"] == "ess" for it in r["trace"]["retrieved"])
    r = MarketingChat(kms, llm).answer("What is the From Date field in Leave Request?", module=["ess"])
    assert [s for s in r["trace"]["steps"] if s["step"] == "retrieve"][0]["namedScreens"] == ["Leave Request"]


def test_chat_api_accepts_several_modules(ess_kms, monkeypatch):
    from fastapi.testclient import TestClient
    import pie_agent.api as api
    monkeypatch.setattr(api, "_CHAT", MarketingChat(_two_modules(ess_kms)))
    c = TestClient(api.app)
    r = c.post("/usage/api/chat", json={"message": "What is the From Date field?", "modules": ["ess", "inventory"]}).json()
    assert r["plan"]["modules"] == ["ess", "inventory"]
    r = c.post("/usage/api/chat", json={"message": "What is the From Date field?", "module": "ess"}).json()   # older pages
    assert r["plan"]["modules"] == ["ess"]
    assert c.post("/usage/api/chat", json={"message": "x", "modules": ["ess", "../x"]}).status_code == 400


def test_exported_pages_carry_the_picker(tmp_path):
    from pie_agent.usage import export_chat_ui, export_fields_ui, export_static_ui
    for out in (export_chat_ui("http://pie:8000", tmp_path / "chat.html"),
                export_static_ui([CATALOG | {"product": "GoodBooks", "generatedAt": "2026-09-01"}], tmp_path / "catalog.html"),
                export_fields_ui([], tmp_path / "fields.html")):
        html = out.read_text(encoding="utf-8")
        assert "window.PieModules" in html and '<script src="modules.js">' not in html
