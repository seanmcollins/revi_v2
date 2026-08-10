"""Postgres adapter tests against a REAL database (``-m postgres``).

Strategy: use the docker-compose Postgres if reachable; otherwise start it
via ``docker compose up -d postgres`` and wait for healthy; if the docker
daemon is unavailable, skip with a clear reason. Each run creates a
**throwaway database** (unique name) through the admin connection, migrates
it to head with Alembic, runs the shared store contract suite against it,
and drops it at teardown — reruns never see stale state, and the default
``revi`` database is left untouched.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from revi_store_postgres.engine import ENV_VAR, create_engine, database_url
from revi_store_postgres.rounds_stores import (
    PostgresRoundsLeadStore,
    PostgresRoundsLoadStore,
    PostgresRoundsPinResultStore,
    PostgresRoundsPinStore,
)
from revi_store_postgres.stores import (
    PostgresCohortStore,
    PostgresEvidenceCache,
    PostgresFrameStore,
    PostgresInvestigationStore,
    PostgresReferentRegistryStore,
    PostgresSessionStore,
    PostgresTraceStore,
)
from revi_store_postgres.tables import ALL_SCHEMAS
from revi_testing.rounds_store_contract import RoundsStoreContract, RoundsStores
from revi_testing.store_contract import ApplicationStateStoreContract, ApplicationStores

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
ADMIN_URL = database_url()  # REVI_DATABASE_URL or the docker-compose default


# --- reaching a real Postgres -----------------------------------------------


def _reachable(url: str) -> bool:
    engine = sa.create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return True
    except sa.exc.OperationalError:
        return False
    finally:
        engine.dispose()


def _ensure_postgres() -> str | None:
    """Return a skip reason, or None once Postgres is reachable."""
    if _reachable(ADMIN_URL):
        return None
    docker = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, cwd=ROOT, check=False
    )
    if docker.returncode != 0:
        return "Postgres is not reachable and the docker daemon is unavailable"
    up = subprocess.run(
        ["docker", "compose", "up", "-d", "postgres"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    if up.returncode != 0:
        return f"docker compose up -d postgres failed: {up.stderr.strip()[:200]}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if _reachable(ADMIN_URL):
            return None
        time.sleep(1)
    return "compose Postgres did not become reachable within 90s"


# --- throwaway-database plumbing --------------------------------------------


def _admin_engine() -> Engine:
    return sa.create_engine(ADMIN_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")


def _test_db_url(dbname: str) -> str:
    return sa.engine.make_url(ADMIN_URL).set(database=dbname).render_as_string(hide_password=False)


def _create_database(dbname: str) -> None:
    engine = _admin_engine()
    with engine.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    engine.dispose()


def _drop_database(dbname: str) -> None:
    engine = _admin_engine()
    with engine.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
    engine.dispose()


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    reason = _ensure_postgres()
    if reason is not None:
        pytest.skip(reason)
    dbname = f"revi_test_{uuid4().hex[:12]}"
    _create_database(dbname)
    url = _test_db_url(dbname)
    try:
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        _drop_database(dbname)


@pytest.fixture(scope="session")
def engine(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_size=2, max_overflow=2)
    yield engine
    engine.dispose()


# --- the shared contract against real Postgres ------------------------------


class TestPostgresApplicationStores(ApplicationStateStoreContract):
    @pytest.fixture
    def stores(self, engine: Engine) -> ApplicationStores:
        return ApplicationStores(
            sessions=PostgresSessionStore(engine),
            referents=PostgresReferentRegistryStore(engine),
            investigations=PostgresInvestigationStore(engine),
            traces=PostgresTraceStore(engine),
            frames=PostgresFrameStore(engine),
            cohorts=PostgresCohortStore(engine),
            evidence=PostgresEvidenceCache(engine),
        )


class TestPostgresRoundsStores(RoundsStoreContract):
    """The Rounds adapters against the same suite the memory ones pass.

    Migration 0005's schema, exercised by behaviour rather than by reading
    the DDL back: ordering by the load's own clock, tenant scoping, soft
    archive, and exact round-tripping of the typed spec a watch re-runs
    every load.
    """

    @pytest.fixture
    def rounds(self, engine: Engine) -> RoundsStores:
        return RoundsStores(
            pins=PostgresRoundsPinStore(engine),
            results=PostgresRoundsPinResultStore(engine),
            loads=PostgresRoundsLoadStore(engine),
            leads=PostgresRoundsLeadStore(engine),
        )


# --- migration + engine specifics -------------------------------------------


class TestMigrations:
    def test_capability_schemas_exist(self, engine: Engine) -> None:
        with engine.connect() as conn:
            names = set(
                conn.execute(sa.text("SELECT schema_name FROM information_schema.schemata")).scalars()
            )
        assert set(ALL_SCHEMAS) <= names

    def test_migrated_to_head(self, engine: Engine) -> None:
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0005"

    def test_rounds_tables_exist(self, engine: Engine) -> None:
        """Migration 0005. Rounds is a capability, so it gets a
        capability-named schema; the four tables are the four questions a
        load-over-load surface cannot answer without stored state — what is
        watched, what each watch read at each load, what the detection feed
        said at each load, and where each lead stands."""
        with engine.connect() as conn:
            tables = set(
                conn.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'revi_rounds'"
                    )
                ).scalars()
            )
        assert {"pins", "pin_results", "loads", "leads"} == tables

    def test_the_prior_load_lookup_is_indexed_on_the_loads_own_clock(
        self, engine: Engine
    ) -> None:
        """Migration 0005. Every brief and every tile delta asks "what did
        this read at the PREVIOUS load", ordered by the watermark's own
        ``loaded_at`` — never by its id, which is an opaque string whose
        lexical order is a coincidence of one warehouse's naming."""
        with engine.connect() as conn:
            indexes = {
                row[0]: row[1]
                for row in conn.execute(
                    sa.text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = 'revi_rounds'"
                    )
                )
            }
        assert "ix_revi_rounds_pin_results_pin_loaded" in indexes
        assert "watermark_loaded_at" in indexes["ix_revi_rounds_pin_results_pin_loaded"]
        assert "ix_revi_rounds_loads_tenant_loaded" in indexes
        assert "watermark_loaded_at" in indexes["ix_revi_rounds_loads_tenant_loaded"]

    def test_sessions_can_be_soft_archived(self, engine: Engine) -> None:
        """Migration 0004. The rail had no way to dismiss a session, and a
        hard delete would have orphaned its investigations, traces, frames
        and cohorts — so the column is a nullable timestamp and the list
        filters on it."""
        with engine.connect() as conn:
            columns = set(
                conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'revi_session' AND table_name = 'sessions'"
                    )
                ).scalars()
            )
        assert "archived_at" in columns

    def test_turn_receipts_outlive_the_process(self, engine: Engine) -> None:
        """Migration 0004. The idempotency key was honored from a
        process-local dict, so a restart between a client's POST and its
        retry executed the turn a second time."""
        with engine.connect() as conn:
            columns = set(
                conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'revi_session' AND table_name = 'turn_receipts'"
                    )
                ).scalars()
            )
        assert {"tenant", "session_id", "idempotency_key", "response"} <= columns

    def test_the_session_list_filter_is_indexed(self, engine: Engine) -> None:
        """Migration 0003. ``GET /v1/sessions`` filters on tenant and
        nothing else; unindexed, every list read scanned every session in
        the deployment rather than the asking tenant's own."""
        with engine.connect() as conn:
            names = set(
                conn.execute(
                    sa.text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'revi_session' AND tablename = 'sessions'"
                    )
                ).scalars()
            )
        assert "ix_revi_session_sessions_tenant" in names

    def test_sessions_carry_their_settings_column(self, engine: Engine) -> None:
        """Migration 0002. Nullable, and never backfilled: a session written
        before the column existed ran under the defaults, and NULL says
        exactly that."""
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT is_nullable, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'revi_session' AND table_name = 'sessions' "
                    "AND column_name = 'settings'"
                )
            ).one()
        assert row.is_nullable == "YES"
        assert row.data_type == "jsonb"

    def test_downgrade_upgrade_cycle(self, postgres_url: str) -> None:
        """Downgrade drops the capability schemas; upgrade restores them.
        Runs in its own throwaway database so the shared one keeps its data."""
        dbname = f"revi_test_{uuid4().hex[:12]}"
        _create_database(dbname)
        url = _test_db_url(dbname)
        try:
            config = _alembic_config(url)
            command.upgrade(config, "head")
            command.downgrade(config, "base")
            probe = sa.create_engine(url, poolclass=NullPool)
            with probe.connect() as conn:
                names = set(
                    conn.execute(
                        sa.text("SELECT schema_name FROM information_schema.schemata")
                    ).scalars()
                )
            probe.dispose()
            assert not (set(ALL_SCHEMAS) & names)
            command.upgrade(config, "head")
        finally:
            _drop_database(dbname)


class TestEngineConfig:
    def test_url_resolution_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "postgresql+psycopg://env:env@localhost:5433/from_env")
        assert database_url().endswith("/from_env")
        explicit = "postgresql+psycopg://x:x@localhost:5433/explicit"
        assert database_url(explicit) == explicit
        monkeypatch.delenv(ENV_VAR)
        assert database_url() == "postgresql+psycopg://revi:revi_dev_only@localhost:5433/revi"

    def test_timestamptz_reads_come_back_utc_aware(self, engine: Engine) -> None:
        with engine.connect() as conn:
            value = conn.execute(sa.text("SELECT now()")).scalar_one()
        assert value.tzinfo is not None
        assert value.utcoffset() is not None and value.utcoffset().total_seconds() == 0
