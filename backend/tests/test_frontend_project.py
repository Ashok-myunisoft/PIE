"""The frontend is a separate static project that reaches the backend only through config.js."""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PAGES = ["index.html", "chat.html", "catalog.html", "fields.html"]


def test_pages_and_config_exist():
    for p in PAGES + ["config.js"]:
        assert (FRONTEND / p).exists(), p
    assert re.search(r'apiBase:\s*"http://localhost:8000/usage/"', (FRONTEND / "config.js").read_text(encoding="utf-8"))


def test_pages_use_config_and_link_to_each_other():
    for p in PAGES:
        html = (FRONTEND / p).read_text(encoding="utf-8")
        assert '<script src="config.js"></script>' in html, p
        # No relative API calls left: every request goes to the configured backend.
        assert not re.search(r'fetch\(\s*["\']api/', html), p
        for other in PAGES:
            assert f'href="{other}"' in html, (p, other)


def test_exports_still_work(tmp_path):
    from pie_agent.usage import export_chat_ui
    html = export_chat_ui("http://pie-server:8000", tmp_path / "ask.html").read_text(encoding="utf-8")
    assert '"apiBase": "http://pie-server:8000/usage/"' in html
