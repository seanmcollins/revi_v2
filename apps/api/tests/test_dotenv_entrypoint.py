"""Where ``.env`` is allowed to be read, and where it is not.

``make api`` has to pick up ``REVI_MODEL_PIN`` from the repo-root ``.env``
or the live Claude adapter is unreachable without exporting variables by
hand. Loading it is process-global mutation, so exactly one module may do
it: the uvicorn entry point. If ``create_app`` or ``build_components`` ever
learn to load it, every test in this repo silently inherits whatever a
developer happens to have in their ``.env`` — and the suite stops meaning
what it says.

Asserted by reading the source rather than by importing: importing
``revi_api.main`` would load the developer's ``.env`` into this very test
process, which is the failure the rule exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "revi_api"

#: Everything a test can reach without meaning to load an environment file.
COMPOSITION_MODULES = ["app.py", "wiring.py", "service.py", "export_openapi.py", "auth.py"]


def test_the_entry_point_loads_the_repo_root_dotenv() -> None:
    source = (_SRC / "main.py").read_text(encoding="utf-8")
    assert "load_dotenv" in source
    # override=False: an exported shell variable, or one `make api` sets,
    # beats the file. The file is the default, never the authority.
    assert "override=False" in source


@pytest.mark.parametrize("module", COMPOSITION_MODULES)
def test_composition_modules_never_load_an_environment_file(module: str) -> None:
    path = _SRC / module
    if not path.is_file():  # module list drifts with the app; don't fail on that
        pytest.skip(f"{module} does not exist")
    assert "dotenv" not in path.read_text(encoding="utf-8")
