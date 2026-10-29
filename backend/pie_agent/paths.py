"""Project layout: <root>/backend/pie_agent (this package), <root>/frontend, <root>/outputs, <root>/data, <root>/.env."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = Path(os.getenv("PIE_FRONTEND_DIR") or REPO_ROOT / "frontend")


def from_root(path: str | Path) -> Path:
    """Relative paths are taken from the project root, whatever the working directory."""
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p
