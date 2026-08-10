"""Throwaway-database plumbing for ``-m postgres`` suites.

Strategy (unchanged from where it was first written, only relocated so two
suites can share it rather than keep two copies that drift): use the
docker-compose Postgres if reachable; otherwise start it and wait for
healthy; if the docker daemon is unavailable, skip with a clear reason.
Each session creates a **throwaway database** through the admin connection,
migrates it to head with Alembic, and drops it at teardown — so reruns
never see stale state and the default ``revi`` database is left untouched.

Lives in ``revi_testing`` because more than one package now needs a real
Postgres: the store adapters are held to the shared contract suite, and the
Monitors service is exercised end-to-end across loads against a real
database, which is where a JSONB round-trip or an ordering assumption would
actually break.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from revi_store_postgres.engine import database_url

#: packages/testing/src/revi_testing/postgres_harness.py → repo root
REPO_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPO_ROOT / "packages" / "store-postgres" / "alembic.ini"

#: ``REVI_DATABASE_URL`` or the docker-compose default.
ADMIN_URL = database_url()


def reachable(url: str) -> bool:
    engine = sa.create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return True
    except sa.exc.OperationalError:
        return False
    finally:
        engine.dispose()


def ensure_postgres() -> str | None:
    """Return a skip reason, or ``None`` once Postgres is reachable."""
    if reachable(ADMIN_URL):
        return None
    docker = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, cwd=REPO_ROOT, check=False
    )
    if docker.returncode != 0:
        return "Postgres is not reachable and the docker daemon is unavailable"
    up = subprocess.run(
        ["docker", "compose", "up", "-d", "postgres"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if up.returncode != 0:
        return f"docker compose up -d postgres failed: {up.stderr.strip()[:200]}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if reachable(ADMIN_URL):
            return None
        time.sleep(1)
    return "compose Postgres did not become reachable within 90s"


def admin_engine() -> Engine:
    return sa.create_engine(ADMIN_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")


def test_db_url(dbname: str) -> str:
    return sa.engine.make_url(ADMIN_URL).set(database=dbname).render_as_string(
        hide_password=False
    )


def create_database(dbname: str) -> None:
    engine = admin_engine()
    with engine.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    engine.dispose()


def drop_database(dbname: str) -> None:
    engine = admin_engine()
    with engine.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
    engine.dispose()


def alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


@contextmanager
def throwaway_database() -> Iterator[str]:
    """A migrated, empty database for the duration of the block.

    Yields its URL; drops it on the way out, whatever happened inside.
    """
    dbname = f"revi_test_{uuid4().hex[:12]}"
    create_database(dbname)
    url = test_db_url(dbname)
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        drop_database(dbname)
