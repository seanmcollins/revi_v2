"""Export the OpenAPI schema to ``contracts/openapi.json``.

Run via ``make openapi`` (``uv run python -m revi_api.export_openapi``).
The app is created without wiring components, so no warehouse, database,
or LLM is touched — route and DTO shapes only. The frontend types are
regenerated from this file (see ``scripts/generate_web_types.md``).
"""

from __future__ import annotations

import json
from pathlib import Path

from revi_api.app import create_app

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUT = _REPO_ROOT / "contracts" / "openapi.json"


def export(out: Path = DEFAULT_OUT) -> Path:
    app = create_app(service=None)
    schema = app.openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    path = export()
    print(f"wrote {path}")
