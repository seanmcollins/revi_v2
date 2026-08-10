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

import hashlib
import importlib.util
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
from revi_store_postgres.monitors_stores import (
    PostgresMonitorsLeadStore,
    PostgresMonitorsLoadStore,
    PostgresMonitorsPinResultStore,
    PostgresMonitorsPinStore,
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
from revi_testing.monitors_store_contract import MonitorsStoreContract, MonitorsStores
from revi_testing.store_contract import ApplicationStateStoreContract, ApplicationStores

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_VERSIONS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "revi_store_postgres"
    / "migrations"
    / "versions"
)


def _migration(name: str) -> object:
    """One migration module, loaded by path.

    Version files are not importable as modules (``0008_…`` is not an
    identifier) and are deliberately not re-exported from the package: a
    migration is frozen history, and live code importing it would let a
    later refactor change what a past migration did. The test reads it the
    same way Alembic does, so the coverage list it checks IS the one the
    migration applied.
    """
    spec = importlib.util.spec_from_file_location(f"revi_migration_{name}", _VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SURFACES: tuple[tuple[str, str, str, bool], ...] = _migration(
    "0008_entity_label_rename"
)._SURFACES  # type: ignore[attr-defined]
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


class TestPostgresMonitorsStores(MonitorsStoreContract):
    """The Monitors adapters against the same suite the memory ones pass.

    Migration 0005's schema, exercised by behaviour rather than by reading
    the DDL back: ordering by the load's own clock, tenant scoping, soft
    archive, and exact round-tripping of the typed spec a monitor re-runs
    every load.
    """

    @pytest.fixture
    def monitors(self, engine: Engine) -> MonitorsStores:
        return MonitorsStores(
            pins=PostgresMonitorsPinStore(engine),
            results=PostgresMonitorsPinResultStore(engine),
            loads=PostgresMonitorsLoadStore(engine),
            leads=PostgresMonitorsLeadStore(engine),
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
        assert version == "0008"

    def test_monitors_tables_exist(self, engine: Engine) -> None:
        """Migration 0005. Monitors is a capability, so it gets a
        capability-named schema; the four tables are the four questions a
        load-over-load surface cannot answer without stored state — what is
        monitored, what each monitor read at each load, what the detection feed
        said at each load, and where each lead stands."""
        with engine.connect() as conn:
            tables = set(
                conn.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'revi_monitors'"
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
                        "WHERE schemaname = 'revi_monitors'"
                    )
                )
            }
        assert "ix_revi_monitors_pin_results_pin_loaded" in indexes
        assert "watermark_loaded_at" in indexes["ix_revi_monitors_pin_results_pin_loaded"]
        assert "ix_revi_monitors_loads_tenant_loaded" in indexes
        assert "watermark_loaded_at" in indexes["ix_revi_monitors_loads_tenant_loaded"]

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

    def test_investigations_carry_their_narrative_column(self, engine: Engine) -> None:
        """Migration 0006. "Copy link" shipped a page with the analysis
        removed because nothing stored the composed prose.
        Nullable and never backfilled: a turn written before the column
        existed did not keep its prose, and an empty string there would
        claim it published none."""
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT is_nullable, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'revi_trace' AND table_name = 'investigations' "
                    "AND column_name = 'narrative'"
                )
            ).one()
        assert row.is_nullable == "YES"
        assert row.data_type == "text"

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


# --- migration 0008: the M32 entity-label rename ----------------------------


#: One row per stored surface migration 0008 claims to cover, with a retired
#: label in it. Written the way the corpus actually holds them — a filter
#: value inside a spec, a recorded SQL string, a finding title, an analyst's
#: own rationale — because a migration that passes against tidy fixtures and
#: misses the real shapes has proved nothing.
_LEGACY_ROWS: tuple[tuple[str, str, dict[str, object]], ...] = (
    (
        "revi_monitors",
        "pins",
        {
            "id": "pin_legacy",
            "tenant": "demo",
            "label": "Pinnacle Health Plan — denial rate",
            "presentation": "finding",
            "window_mode": "absolute",
            "spec": '{"metric_ids": ["denial_rate"], "filters": [{"dimension": "payer", '
            '"predicate_op": "eq", "values": ["Pinnacle Health Plan"]}]}',
            "monitor": '{"rationale": "Pinnacle is our JOC account — brief me on anything '
            'over a point."}',
            "created_from_kind": "spec",
            "created_by": "",
            "created_at": "2026-08-01T00:00:00+00:00",
        },
    ),
    (
        "revi_monitors",
        "pin_results",
        {
            "pin_id": "pin_legacy",
            "watermark_id": "wm_003",
            "tenant": "demo",
            "watermark_loaded_at": "2026-08-03T04:10:00+00:00",
            "evaluated_at": "2026-08-03T05:00:00+00:00",
            "payload": '{"label": "Meridian HMO Care — denied dollars", '
            '"headline_subject": {"plan": "Meridian HMO Care"}, '
            '"headline_subject_label": "Meridian HMO Care"}',
        },
    ),
    (
        "revi_monitors",
        "loads",
        {
            "tenant": "demo",
            "watermark_id": "wm_003",
            "watermark_loaded_at": "2026-08-03T04:10:00+00:00",
            "evaluated_at": "2026-08-03T05:00:00+00:00",
            "payload": '{"leads": {"ANM-026": {"title": "Late charges: Eastside cardiology"}}}',
        },
    ),
    (
        "revi_monitors",
        "leads",
        {
            "tenant": "demo",
            "anomaly_id": "ANM-031",
            "status": "open",
            "note": "Pinnacle Oncology — posting stall, chasing the lockbox",
            "updated_at": "2026-08-03T05:00:00+00:00",
            "baseline_basis": "",
            "confirming_watermarks": "[]",
            "verification_note": "",
            "history": '[{"note": "Eastside Medical Center late charges"}]',
        },
    ),
    (
        "revi_trace",
        "investigations",
        {
            "id": "inv_legacy",
            "session_id": "sess_legacy",
            "turn_id": "turn_legacy",
            "turn_class": "typed",
            "question": "How is Meridian Health doing on denials?",
            "spec": '{"filters": [{"dimension": "payer", "values": ["Meridian Health"]}]}',
            "status": "answered",
            "findings": '[{"title": "Meridian Health: $28,614.94 denied dollars"}]',
            "frame_refs": "[]",
            "warnings": '["population_caveat: Bluestone PPO Blue excluded"]',
            "narrative": "Meridian Health leads the ranking.",
            "created_at": "2026-08-03T05:00:00+00:00",
        },
    ),
    (
        "revi_trace",
        "frames",
        {
            "key": "frame_legacy",
            "frame": '{"sql": "SELECT SUM(base.denied_amount_cents) FROM snap_003.v_denial '
            "AS base WHERE base.payer_name = 'Meridian Health'\"}",
            "created_at": "2026-08-03T05:00:00+00:00",
        },
    ),
    (
        "revi_session",
        "referents",
        {
            "session_id": "sess_legacy",
            "referent_id": "F1",
            "kind": "finding",
            "payload": '{"dimension_value": ["facility", "Eastside Medical Center"]}',
        },
    ),
    (
        "revi_session",
        "turn_receipts",
        {
            "tenant": "demo",
            "session_id": "sess_legacy",
            "idempotency_key": "key_legacy",
            "response": '{"findings": [{"title": "Bluestone HMO Blue: 18.8% denial rate"}]}',
            "created_at": "2026-08-03T05:00:00+00:00",
        },
    ),
    (
        "revi_cache",
        "evidence",
        {
            "probe_hash": "probe_legacy",
            "watermark_id": "wm_003",
            "pack_snapshot_id": "pack_legacy",
            "frame": '{"rows": [["Pinnacle PPO", 42]]}',
            "created_at": "2026-08-03T05:00:00+00:00",
        },
    ),
    (
        "revi_cohort",
        "cohorts",
        {
            "cohort_id": "coh_legacy",
            "tenant": "demo",
            "session_id": "sess_legacy",
            "definition": '{"scope": {"payer": "Pinnacle Health Plan"}}',
            "origin": '{"question": "Pinnacle POS claims"}',
            "size": 12,
            "pinned": '{"plan": "Meridian Exchange PPO"}',
        },
    ),
    # THE CONTROL. Every one of these marks SURVIVED the rename, and two of
    # them start with a stem that a retired name shares. A migration that
    # rewrites any of them has renamed a live entity.
    (
        "revi_trace",
        "investigations",
        {
            "id": "inv_survivor",
            "session_id": "sess_legacy",
            "turn_id": "turn_survivor",
            "turn_class": "typed",
            "question": "Bluestone Mutual and Bluestone Federal PPO denial rates?",
            "spec": '{"filters": [{"dimension": "payer", "values": ["Bluestone Mutual"]}]}',
            "status": "answered",
            "findings": '[{"title": "Non-covered denial burst: Bluestone PPO Imaging"}, '
            '{"title": "Small eligibility pocket: Bluestone HMO Primary Care"}]',
            "frame_refs": "[]",
            "warnings": "[]",
            "narrative": "Bluestone Mutual is unchanged by the rename.",
            "created_at": "2026-08-03T05:00:00+00:00",
        },
    ),
)


def _insert_legacy(engine: Engine) -> None:
    with engine.begin() as conn:
        for schema, table, row in _LEGACY_ROWS:
            columns = ", ".join(row)
            values = ", ".join(f":{name}" for name in row)
            conn.execute(
                sa.text(f'INSERT INTO "{schema}".{table} ({columns}) VALUES ({values})'), row
            )


def _label_snapshot(engine: Engine) -> str:
    """One md5 over every migrated surface, in a stable order."""
    digest = hashlib.md5()
    with engine.connect() as conn:
        for schema, table, column, _json in _SURFACES:
            rows = conn.execute(
                sa.text(f'SELECT {column}::text FROM "{schema}".{table} ORDER BY 1')
            ).scalars()
            digest.update(f"{schema}.{table}.{column}".encode())
            for value in rows:
                digest.update((value or "").encode())
    return digest.hexdigest()


def _all_text(engine: Engine) -> str:
    with engine.connect() as conn:
        return "\n".join(
            str(value)
            for schema, table, column, _json in _SURFACES
            for value in conn.execute(
                sa.text(f'SELECT {column}::text FROM "{schema}".{table}')
            ).scalars()
            if value is not None
        )


class TestEntityLabelRename:
    """Migration 0008. M32 renamed twelve entities in the warehouse; the
    stored corpus went on naming the retired ones, which made every audit
    filter derive 0 against a real published figure and put dead names on
    live tiles.
    """

    @pytest.fixture
    def at_prior_revision(self) -> Iterator[tuple[Engine, Config]]:
        """A throwaway database migrated to 0007 — the state this migration
        finds. Its own database, because it deliberately walks the revision
        back and forth."""
        reason = _ensure_postgres()
        if reason is not None:
            pytest.skip(reason)
        dbname = f"revi_labels_{uuid4().hex[:12]}"
        _create_database(dbname)
        url = _test_db_url(dbname)
        config = _alembic_config(url)
        engine = sa.create_engine(url, poolclass=NullPool)
        try:
            command.upgrade(config, "0007")
            yield engine, config
        finally:
            engine.dispose()
            _drop_database(dbname)

    def test_every_stored_surface_is_migrated(
        self, at_prior_revision: tuple[Engine, Config]
    ) -> None:
        engine, config = at_prior_revision
        _insert_legacy(engine)

        command.upgrade(config, "0008")

        text = _all_text(engine)
        for retired in (
            "Pinnacle Health Plan",
            "Meridian Health",
            "Meridian HMO Care",
            "Eastside Medical Center",
            "Bluestone PPO Blue",
            "Bluestone HMO Blue",
            "Pinnacle PPO",
            "Meridian Exchange PPO",
        ):
            assert retired not in text, retired
        # The current names are there instead — including inside a recorded
        # audit SQL string, which is what an auditor re-runs.
        assert "Ashvale Health Plan" in text
        assert "base.payer_name = 'Halvern Health'" in text
        assert "Halvern HMO Care" in text
        assert "Eastmere Medical Center" in text
        assert "Bluestone Preferred PPO" in text and "Bluestone Select HMO" in text
        # Prose the full labels cannot reach travels too: a card title, an
        # analyst's own rationale.
        assert "Ashvale Oncology" in text
        assert "Eastmere cardiology" in text
        assert "Ashvale is our JOC account" in text

    def test_a_surviving_mark_is_never_rewritten(
        self, at_prior_revision: tuple[Engine, Config]
    ) -> None:
        """The reason there is no ``Bluestone`` rule: three live entities and
        two live card titles carry that mark, and renaming them would invent
        names the warehouse has never held."""
        engine, config = at_prior_revision
        _insert_legacy(engine)

        command.upgrade(config, "0008")

        text = _all_text(engine)
        for survivor in (
            "Bluestone Mutual",
            "Bluestone Federal PPO",
            "Bluestone PPO Imaging",
            "Bluestone HMO Primary Care",
        ):
            assert survivor in text, survivor

    def test_the_reverse_map_round_trips_every_payload(
        self, at_prior_revision: tuple[Engine, Config]
    ) -> None:
        """Symmetric, byte for byte. A rename that cannot be undone is a
        rewrite of history rather than a migration."""
        engine, config = at_prior_revision
        _insert_legacy(engine)
        before = _label_snapshot(engine)

        command.upgrade(config, "0008")
        migrated = _label_snapshot(engine)
        command.downgrade(config, "0007")

        assert migrated != before
        assert _label_snapshot(engine) == before
