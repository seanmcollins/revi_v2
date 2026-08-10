"""Uvicorn entry point: ``uvicorn revi_api.main:app``.

This module is the ONE place allowed to mutate process-global state before
the app is composed, and the only thing it does with that licence is load
the developer's repo-root ``.env``. Everything downstream —
:func:`revi_api.app.create_app`, :func:`revi_api.wiring.build_components` —
takes an explicit environment mapping, so tests stay hermetic: they never
import this module and therefore never inherit a developer's ``.env``.

``override=False`` is deliberate: a variable already exported in the shell
(or set by ``make api``, or injected by a container runtime) wins over the
file. The file is the default, not the authority.

Without this, ``make api`` on a fresh clone always ran the scripted model:
``REVI_MODEL_PIN`` lived in ``.env.example`` and nothing read it, so the live
Claude adapter was unreachable without exporting variables by hand. Loading
the file here makes the live adapter the default locally; ``REVI_LLM_MOCK=1``
still forces the scripted path, and the wiring logs which one it chose.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from revi_api.app import create_app

# apps/api/src/revi_api/main.py → repo root (same walk as revi_api.wiring).
# Resolved off __file__ rather than the cwd because ``--reload`` respawns
# workers whose working directory is not guaranteed to be the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_dotenv() -> None:
    """Load the repo-root ``.env`` if there is one; never fail without it."""
    root_env = _REPO_ROOT / ".env"
    path = str(root_env) if root_env.is_file() else find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)


_load_dotenv()

app = create_app()
