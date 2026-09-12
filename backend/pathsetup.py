"""Make Streamlit-era `agents.*` / `utils.*` imports work under FastAPI.

The research engines still do `from agents.recommender import ...`.
That requires the `backend/` directory on sys.path, not only the repo root.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent


def ensure_backend_on_path() -> str:
    backend = str(BACKEND_DIR)
    repo = str(REPO_ROOT)
    # backend first so `import agents` resolves; repo so `import backend` still works.
    for path in (backend, repo):
        if path not in sys.path:
            sys.path.insert(0, path)
    return backend
