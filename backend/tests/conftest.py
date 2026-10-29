"""Tests run from any folder: imports come from backend/, relative data paths from the project root."""
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for p in (str(BACKEND), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)
os.chdir(ROOT)


@pytest.fixture(autouse=True)
def _isolated_activity_log(tmp_path_factory, monkeypatch):
    """Tests never write to the real activity log or send runs to the server."""
    monkeypatch.setenv("PIE_LOG_DIR", str(tmp_path_factory.mktemp("pie-logs")))
    monkeypatch.setenv("PIE_SESSION_DB", str(tmp_path_factory.mktemp("pie-sessions") / "sessions.db"))
    # Tests never call paid embeddings or the real code index, whatever .env says.
    monkeypatch.setenv("PIE_CODE_MANIFEST", str(tmp_path_factory.mktemp("pie-code") / "manifest.json"))
    for name in ("PIE_LOG_DB", "PIE_LOG_REMOTE", "PIE_LOG_TOKEN", "PIE_LOG_REQUIRE_TOKEN", "PIE_EMBEDDINGS", "PIE_CODE_COLLECTION"):
        monkeypatch.setenv(name, "")  # empty, not unset: load_dotenv() must not fill them from .env
