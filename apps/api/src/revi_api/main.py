"""Uvicorn entry point: ``uvicorn revi_api.main:app``."""

from __future__ import annotations

from revi_api.app import create_app

app = create_app()
