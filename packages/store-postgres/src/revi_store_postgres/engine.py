"""SQLAlchemy engine factory for the application-state Postgres.

Concurrency model — **sync SQLAlchemy Core + psycopg3, hopped off the event
loop with ``asyncio.to_thread``** (see the stores). Chosen over the async
driver deliberately: the stores are short, index-backed statements against a
small pool, so a worker-thread hop is honest about the blocking work, keeps
the Core code plain (no greenlet bridging, no async context-manager
plumbing in Alembic), and lets migrations, tests, and stores share one
engine construction path.

The URL comes from an explicit parameter when given, else the
``REVI_DATABASE_URL`` environment variable, else the docker-compose default.
The connection forces ``timezone=UTC`` so ``timestamptz`` columns round-trip
as UTC-aware datetimes: naive datetimes written to typed timestamp columns
are interpreted as UTC and come back UTC-aware (JSONB-serialized datetimes
round-trip exactly, tzinfo included, via the serde envelope).
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.engine import Engine

ENV_VAR = "REVI_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql+psycopg://revi:revi_dev_only@localhost:5433/revi"


def database_url(url: str | None = None) -> str:
    """Resolve the database URL: explicit > ``REVI_DATABASE_URL`` > compose default."""
    return url or os.environ.get(ENV_VAR) or DEFAULT_DATABASE_URL


def create_engine(
    url: str | None = None,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
) -> Engine:
    """Build a modestly-pooled sync engine for the application-state stores."""
    return sa.create_engine(
        database_url(url),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )
