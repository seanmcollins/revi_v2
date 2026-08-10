"""DuckDB adapter tests.

Four layers:

1. ``TestDuckDbAnalyticalContract`` — the reusable, capability-gated
   analytical contract suite (``revi_testing.analytical_contract``) against a
   small generated warehouse (session fixture, fast).
2. ``TestDuckDbAdapterBehavior`` — adapter-specific error mapping and
   compilation rules not covered by the generic suite.
3. ``TestExclusionPolarity`` — executed proof that a contract's
   ``exclusions`` *removes* the population it names. Pinned because getting
   this backwards is invisible: an inverted exclusion still compiles, still
   returns numbers, and still looks like the metric it claims to be.
4. ``@pytest.mark.reference`` — answer-key regressions against the full-scale
   ``data/revi_warehouse.duckdb`` through the full probe path, including the
   shipped ``packs/base-rcm`` contracts themselves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from revi_calculation_contracts.contract import (
    CountDistinct,
    Filtered,
    MetricContract,
    MetricKind,
    MetricUnit,
    SignConvention,
    Sum,
)
from revi_catalog import load_catalog
from revi_catalog_contracts import CatalogSnapshot
from revi_connector_duckdb import (
    CohortInventory,
    CohortSweepResult,
    DuckDbAnalyticalRepository,
)
from revi_connector_duckdb.compile import _DERIVED_MEASURES, ProbeCompiler
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.errors import (
    DateBasisInvalidError,
    GrainIncompatibleError,
    QueryBudgetExceededError,
    SourceCapabilityUnsupportedError,
    SourceUnavailableError,
    UnsupportedConceptError,
    WatermarkStaleError,
)
from revi_kernel.filters import EMPTY_SCOPE, And, InCohort, Predicate, PredicateOp
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import (
    AggregationProbe,
    MeasurePredicate,
    Ordering,
    ProbeShape,
    SnapshotProbe,
)
from revi_kernel.refs import (
    POST,
    REMIT,
    SERVICE,
    SUBMISSION,
    DimensionRef,
    EntityGrain,
    FieldRef,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
    TimeBucket,
)
from revi_kernel.scope import AbsoluteRange, RangeMode, RelativeRange, TimeUnit, TimeWindow, resolve_relative
from revi_testing.analytical_contract import AnalyticalRepositoryContract
from revi_testing.fixtures import fixture_metrics

ROOT = Path(__file__).resolve().parents[3]
CATALOG_DIR = ROOT / "warehouse" / "catalog"
REFERENCE_DB = ROOT / "data" / "revi_warehouse.duckdb"
REFERENCE_KEY = ROOT / "data" / "answer_key.json"

_H1_2026 = AbsoluteRange(date(2026, 1, 1), date(2026, 6, 30))


@pytest.fixture(scope="session")
def catalog() -> CatalogSnapshot:
    return load_catalog(CATALOG_DIR)


@pytest.fixture(scope="session")
def small_warehouse_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from revi_warehouse.config import GeneratorConfig
    from revi_warehouse.generate import run_generation

    out = tmp_path_factory.mktemp("warehouse") / "revi_small.duckdb"
    return run_generation(GeneratorConfig.small(), out).db_path


@pytest.fixture
def repository(small_warehouse_path: Path, catalog: CatalogSnapshot) -> DuckDbAnalyticalRepository:
    return DuckDbAnalyticalRepository(small_warehouse_path, catalog, fixture_metrics)


async def _newest(repository: DuckDbAnalyticalRepository):
    return (await repository.list_watermarks())[-1]


# ---------------------------------------------------------------------------
# 1. the reusable analytical contract suite


class TestDuckDbAnalyticalContract(AnalyticalRepositoryContract):
    @pytest.fixture
    def repository(  # type: ignore[override]
        self, small_warehouse_path: Path, catalog: CatalogSnapshot
    ) -> DuckDbAnalyticalRepository:
        return DuckDbAnalyticalRepository(small_warehouse_path, catalog, fixture_metrics)


# ---------------------------------------------------------------------------
# 2. adapter-specific behavior


def _claim_probe(**overrides: object) -> AggregationProbe:
    defaults: dict[str, object] = {
        "measures": (MetricRef("claim_count"),),
        "dimensions": (),
        "scope": EMPTY_SCOPE,
        "window": TimeWindow(basis=SERVICE, range=_H1_2026),
        "grain": Grain(EntityGrain.CLAIM),
    }
    defaults.update(overrides)
    return AggregationProbe(**defaults)  # type: ignore[arg-type]


class TestDuckDbAdapterBehavior:
    def test_capabilities(self, repository: DuckDbAnalyticalRepository) -> None:
        caps = repository.capabilities()
        assert caps.as_of_reads and caps.cohort_semijoin
        assert caps.having_pushdown and caps.server_side_top_n
        assert caps.max_cohort_size == 100_000

    def test_the_advertisement_is_the_registry_itself(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        """§6.3: what this source tells the planner it computes is the same
        table the compiler enforces — not a copy of it.

        A copy is the whole failure mode being fixed here: §6.6 carried its
        own one-entry list of adapter-computed fields, it fell behind the
        adapter, and nine executable contracts were refused with a claim
        about the source the source disproved. So the advertisement is
        asserted against ``_DERIVED_MEASURES`` field by field, entity by
        entity, shape by shape.
        """
        advertised = repository.capabilities().derived_measures
        assert {m.field for m in advertised} == set(_DERIVED_MEASURES)
        for measure in advertised:
            spec = _DERIVED_MEASURES[measure.field]
            assert measure.entity == spec.entity
            assert measure.shapes == spec.shapes
            assert measure.shapes, "a derivation valid in no probe shape is not a capability"
        # ...and the construction the compiler documents is advertised too
        assert repository.capabilities().cross_entity_ratio_of_sums

    async def test_unknown_dimension_is_unsupported_concept(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        with pytest.raises(UnsupportedConceptError):
            await repository.execute(
                _claim_probe(dimensions=(DimensionRef("no_such_dim"),)), watermark=wm
            )

    async def test_dimension_unbound_at_grain_is_unsupported_concept(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        with pytest.raises(UnsupportedConceptError):  # carc binds only at denial grain
            await repository.execute(_claim_probe(dimensions=(DimensionRef("carc"),)), watermark=wm)

    async def test_unknown_metric_is_unsupported_concept(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        with pytest.raises(UnsupportedConceptError):
            await repository.execute(_claim_probe(measures=(MetricRef("no_such_metric"),)), watermark=wm)

    async def test_snapshot_metric_through_aggregation_probe(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        with pytest.raises(GrainIncompatibleError):
            await repository.execute(_claim_probe(measures=(MetricRef("ar_balance"),)), watermark=wm)

    async def test_flow_metric_through_snapshot_probe(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        probe = SnapshotProbe(
            measures=(MetricRef("claim_count"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        with pytest.raises(GrainIncompatibleError):
            await repository.execute(probe, watermark=wm)

    async def test_metric_grain_must_match_probe_grain(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        with pytest.raises(GrainIncompatibleError):  # cash_posted is transaction-grain
            await repository.execute(_claim_probe(measures=(MetricRef("cash_posted"),)), watermark=wm)

    async def test_unbound_date_basis_is_invalid(self, repository: DuckDbAnalyticalRepository) -> None:
        wm = await _newest(repository)
        with pytest.raises(DateBasisInvalidError):  # POST is not bound for claims
            await repository.execute(
                _claim_probe(window=TimeWindow(basis=POST, range=_H1_2026)), watermark=wm
            )

    async def test_snapshot_rejects_time_bucket(self, repository: DuckDbAnalyticalRepository) -> None:
        wm = await _newest(repository)
        probe = SnapshotProbe(
            measures=(MetricRef("ar_balance"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM, TimeBucket.MONTH),
        )
        with pytest.raises(GrainIncompatibleError):
            await repository.execute(probe, watermark=wm)

    async def test_ordering_by_ratio_metric_unsupported(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        probe = AggregationProbe(
            measures=(MetricRef("denial_rate"),),
            dimensions=(DimensionRef("payer"),),
            scope=EMPTY_SCOPE,
            window=TimeWindow(basis=REMIT, range=_H1_2026),
            grain=Grain(EntityGrain.DENIAL),
            order_by=(Ordering(MetricRef("denial_rate")),),
        )
        with pytest.raises(SourceCapabilityUnsupportedError):
            await repository.execute(probe, watermark=wm)

    async def test_uncertified_dimension_downgrades_to_discovery(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        probe = AggregationProbe(
            measures=(MetricRef("denial_count"),),
            dimensions=(DimensionRef("rarc_synthetic"),),
            scope=EMPTY_SCOPE,
            window=TimeWindow(basis=REMIT, range=_H1_2026),
            grain=Grain(EntityGrain.DENIAL),
        )
        frame = await repository.execute(probe, watermark=wm)
        assert frame.evidence_grade is EvidenceGrade.DISCOVERY
        certified = await repository.execute(
            _claim_probe(dimensions=(DimensionRef("payer"),)), watermark=wm
        )
        assert certified.evidence_grade is EvidenceGrade.DIRECT

    async def test_identical_probes_yield_identical_frames(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        probe = _claim_probe(dimensions=(DimensionRef("payer"), DimensionRef("service_line")))
        first = await repository.execute(probe, watermark=wm)
        second = await repository.execute(probe, watermark=wm)
        assert first.rows == second.rows  # deterministic default ordering
        assert first.provenance != second.provenance  # fresh repository_query_id

    # -- cohorts ----------------------------------------------------------

    async def test_cohort_budget_exceeded(
        self, small_warehouse_path: Path, catalog: CatalogSnapshot
    ) -> None:
        tight = DuckDbAnalyticalRepository(
            small_warehouse_path, catalog, fixture_metrics, max_cohort_size=10
        )
        wm = await _newest(tight)
        definition = CohortDefinition(entity=EntityGrain.CLAIM, scope=EMPTY_SCOPE)
        with pytest.raises(QueryBudgetExceededError):
            await tight.materialize_cohort(definition, watermark=wm)

    async def test_unpinned_cohort_rejected(self, repository: DuckDbAnalyticalRepository) -> None:
        wm = await _newest(repository)
        definition = CohortDefinition(entity=EntityGrain.CLAIM, scope=EMPTY_SCOPE)
        unpinned = CohortRef(
            id="cohort_ffffffffffff",
            definition=definition,
            origin=ReferentId("F9", ReferentKind.COHORT),
            size=1,
            pinned=None,
        )
        with pytest.raises(SourceCapabilityUnsupportedError):
            await repository.execute(_claim_probe(scope=InCohort(unpinned)), watermark=wm)

    async def test_cohort_pinned_at_other_watermark_is_stale(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        watermarks = await repository.list_watermarks()
        oldest, newest = watermarks[0], watermarks[-1]
        definition = CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Halvern Health",)),
            window=TimeWindow(basis=SERVICE, range=_H1_2026),
        )
        materialization = await repository.materialize_cohort(definition, watermark=newest)
        ref = CohortRef(
            id=materialization.cohort_id,
            definition=definition,
            origin=ReferentId("F2", ReferentKind.COHORT),
            size=materialization.size,
            pinned=materialization,
        )
        with pytest.raises(WatermarkStaleError):
            await repository.execute(_claim_probe(scope=InCohort(ref)), watermark=oldest)

    async def test_cross_grain_without_certified_path_unsupported(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        definition = CohortDefinition(
            entity=EntityGrain.DENIAL,
            scope=Predicate(DimensionRef("denial_category"), PredicateOp.EQ, ("COB",)),
            window=TimeWindow(basis=REMIT, range=_H1_2026),
        )
        materialization = await repository.materialize_cohort(definition, watermark=wm)
        ref = CohortRef(
            id=materialization.cohort_id,
            definition=definition,
            origin=ReferentId("F3", ReferentKind.COHORT),
            size=materialization.size,
            pinned=materialization,
        )
        # denial ids cannot filter a claim probe: no claim → denial join path.
        with pytest.raises(SourceCapabilityUnsupportedError):
            await repository.execute(_claim_probe(scope=InCohort(ref)), watermark=wm)

    async def test_drop_expired_cohorts(self, repository: DuckDbAnalyticalRepository) -> None:
        wm = await _newest(repository)
        definition = CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Halvern Health",)),
            window=TimeWindow(basis=SERVICE, range=_H1_2026),
        )
        materialization = await repository.materialize_cohort(definition, watermark=wm)
        before_expiry = materialization.created_at + timedelta(seconds=1)
        assert materialization.cohort_id not in await repository.drop_expired_cohorts(before_expiry)
        after_expiry = materialization.created_at + timedelta(seconds=materialization.ttl_seconds + 1)
        # The sweep is warehouse-wide, not call-scoped: it reclaims every
        # expired cohort in the file, so assert membership rather than a
        # tuple identity that other tests in this session would perturb.
        assert materialization.cohort_id in await repository.drop_expired_cohorts(after_expiry)
        assert await repository.drop_expired_cohorts(after_expiry) == ()  # idempotent
        # probing against the dropped table maps to a sanitized source error
        ref = CohortRef(
            id=materialization.cohort_id,
            definition=definition,
            origin=ReferentId("F4", ReferentKind.COHORT),
            size=materialization.size,
            pinned=materialization,
        )
        with pytest.raises(SourceUnavailableError):
            await repository.execute(_claim_probe(scope=InCohort(ref)), watermark=wm)

    async def test_materialize_at_unknown_watermark_is_stale(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        from revi_kernel.watermark import DataWatermark

        bogus = DataWatermark(id="wm_404", loaded_at=datetime(2026, 8, 3), newest_data_date=date(2026, 8, 2))
        with pytest.raises(WatermarkStaleError):
            await repository.materialize_cohort(
                CohortDefinition(entity=EntityGrain.CLAIM, scope=EMPTY_SCOPE), watermark=bogus
            )


# ---------------------------------------------------------------------------
# 2b. the cohort write path (review finding D6)
#
# The failure this pins was measured, not theorized: 214 cohort tables /
# 11.9M rows / 145MB accumulated in the development warehouse because ids
# were random (so every replay minted a new table) and the sweep's registry
# was a process-local dict (so a fresh sweep process could see none of it).
# Each test below closes one of those two holes. Every test gets its own
# warehouse file: the sweep is warehouse-wide by design, so a shared file
# would let these tests reclaim each other's fixtures.


def _cohort_definition(payer: str = "Halvern Health") -> CohortDefinition:
    return CohortDefinition(
        entity=EntityGrain.CLAIM,
        scope=Predicate(DimensionRef("payer"), PredicateOp.EQ, (payer,)),
        window=TimeWindow(basis=SERVICE, range=_H1_2026),
    )


def _cohort_table_names(path: Path) -> set[str]:
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'cohort_store'"
        ).fetchall()
    finally:
        con.close()
    return {str(row[0]) for row in rows}


class TestCohortWritePath:
    @pytest.fixture
    def warehouse(self, tmp_path: Path) -> Path:
        from revi_warehouse.config import GeneratorConfig
        from revi_warehouse.generate import run_generation

        return run_generation(GeneratorConfig.small(), tmp_path / "cohorts.duckdb").db_path

    @pytest.fixture
    def repo(self, warehouse: Path, catalog: CatalogSnapshot) -> DuckDbAnalyticalRepository:
        return DuckDbAnalyticalRepository(warehouse, catalog, fixture_metrics)

    async def test_identical_drills_reuse_one_table(
        self, repo: DuckDbAnalyticalRepository, warehouse: Path
    ) -> None:
        wm = await _newest(repo)
        first = await repo.materialize_cohort(_cohort_definition(), watermark=wm)
        second = await repo.materialize_cohort(_cohort_definition(), watermark=wm)

        assert first.cohort_id == second.cohort_id  # content-addressed
        assert first.created_at == second.created_at  # the reuse is the stored one
        assert first.size == second.size
        assert _cohort_table_names(warehouse) == {"registry", first.cohort_id}

    async def test_a_different_population_gets_a_different_table(
        self, repo: DuckDbAnalyticalRepository, warehouse: Path
    ) -> None:
        wm = await _newest(repo)
        a = await repo.materialize_cohort(_cohort_definition("Halvern Health"), watermark=wm)
        b = await repo.materialize_cohort(_cohort_definition("Atlas Commercial"), watermark=wm)

        assert a.cohort_id != b.cohort_id
        assert _cohort_table_names(warehouse) == {"registry", a.cohort_id, b.cohort_id}

    async def test_the_watermark_is_part_of_the_address(
        self, repo: DuckDbAnalyticalRepository
    ) -> None:
        """A cohort pinned at an older load is a different set of entity ids;
        reusing one across watermarks would silently re-date the population."""
        watermarks = await repo.list_watermarks()
        oldest, newest = watermarks[0], watermarks[-1]

        at_old = await repo.materialize_cohort(_cohort_definition(), watermark=oldest)
        at_new = await repo.materialize_cohort(_cohort_definition(), watermark=newest)

        assert at_old.cohort_id != at_new.cohort_id

    async def test_reuse_still_honours_the_max_cohort_size_guard(
        self, warehouse: Path, catalog: CatalogSnapshot
    ) -> None:
        """A budget lowered after a cohort was pinned must still bind — the
        reuse path is not a way around the guard."""
        generous = DuckDbAnalyticalRepository(warehouse, catalog, fixture_metrics)
        wm = await _newest(generous)
        pinned = await generous.materialize_cohort(_cohort_definition(), watermark=wm)
        assert pinned.size > 1

        tight = DuckDbAnalyticalRepository(warehouse, catalog, fixture_metrics, max_cohort_size=1)
        with pytest.raises(QueryBudgetExceededError):
            await tight.materialize_cohort(_cohort_definition(), watermark=wm)

    async def test_the_registry_survives_a_new_process(
        self, warehouse: Path, catalog: CatalogSnapshot
    ) -> None:
        """The whole of D6: a *fresh* repository object — the sweep CLI's
        situation, and the API's after a restart — must be able to reclaim
        cohorts it never materialized itself."""
        writer = DuckDbAnalyticalRepository(warehouse, catalog, fixture_metrics)
        wm = await _newest(writer)
        pinned = await writer.materialize_cohort(_cohort_definition(), watermark=wm)

        sweeper = DuckDbAnalyticalRepository(warehouse, catalog, fixture_metrics)
        after_expiry = pinned.created_at + timedelta(seconds=pinned.ttl_seconds + 1)
        result = await sweeper.sweep_cohorts(after_expiry)

        assert result.expired == (pinned.cohort_id,)
        assert result.orphaned == ()
        assert _cohort_table_names(warehouse) == {"registry"}

    async def test_unregistered_tables_are_reclaimed_as_orphans(
        self, repo: DuckDbAnalyticalRepository, warehouse: Path
    ) -> None:
        """The 214 tables the review found: written before the registry
        existed, so nothing could name them. Enumeration by naming convention
        is what makes the sweep authoritative rather than best-effort."""
        import duckdb

        con = duckdb.connect(str(warehouse))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS cohort_store")
            for legacy in ("cohort_0123456789ab", "cohort_fedcba987654"):
                con.execute(f"CREATE TABLE cohort_store.{legacy} AS SELECT 1 AS entity_id")
            con.execute("CREATE TABLE cohort_store.not_a_cohort AS SELECT 1 AS x")
        finally:
            con.close()

        result = await repo.sweep_cohorts(datetime(2026, 8, 7, tzinfo=UTC))

        assert result.expired == ()
        assert result.orphaned == ("cohort_0123456789ab", "cohort_fedcba987654")
        # A table that is not a cohort is never touched, whatever else is in
        # the schema — the sweep drops by naming convention, not by schema.
        assert "not_a_cohort" in _cohort_table_names(warehouse)

    async def test_a_registry_row_whose_table_vanished_is_re_materialized(
        self, repo: DuckDbAnalyticalRepository, warehouse: Path
    ) -> None:
        import duckdb

        wm = await _newest(repo)
        pinned = await repo.materialize_cohort(_cohort_definition(), watermark=wm)
        con = duckdb.connect(str(warehouse))
        try:
            con.execute(f"DROP TABLE cohort_store.{pinned.cohort_id}")
        finally:
            con.close()

        again = await repo.materialize_cohort(_cohort_definition(), watermark=wm)

        assert again.cohort_id == pinned.cohort_id
        assert again.size == pinned.size
        assert again.created_at > pinned.created_at  # genuinely re-made, not handed back
        assert pinned.cohort_id in _cohort_table_names(warehouse)

    async def test_inventory_reports_the_census_a_sweep_is_read_by(
        self, repo: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repo)
        assert await repo.cohort_inventory() == CohortInventory(tables=0, registered=0, rows=0)

        pinned = await repo.materialize_cohort(_cohort_definition(), watermark=wm)
        before = await repo.cohort_inventory()
        assert before == CohortInventory(tables=1, registered=1, rows=pinned.size)

        await repo.sweep_cohorts(pinned.created_at + timedelta(seconds=pinned.ttl_seconds + 1))
        assert await repo.cohort_inventory() == CohortInventory(tables=0, registered=0, rows=0)

    async def test_sweeping_a_warehouse_with_no_cohorts_writes_nothing(
        self, repo: DuckDbAnalyticalRepository, warehouse: Path
    ) -> None:
        result = await repo.sweep_cohorts(datetime(2026, 8, 7, tzinfo=UTC))

        assert result == CohortSweepResult()
        assert "cohort_store" not in {
            *_cohort_table_names(warehouse)
        }  # the schema was not conjured into existence

    async def test_dry_run_reports_without_writing(
        self, repo: DuckDbAnalyticalRepository, warehouse: Path
    ) -> None:
        """The registry is what makes a truthful dry run possible: the same
        answer as a real sweep, on a read-only connection."""
        wm = await _newest(repo)
        pinned = await repo.materialize_cohort(_cohort_definition(), watermark=wm)
        after_expiry = pinned.created_at + timedelta(seconds=pinned.ttl_seconds + 1)

        preview = await repo.sweep_cohorts(after_expiry, dry_run=True)

        assert preview.expired == (pinned.cohort_id,)
        assert _cohort_table_names(warehouse) == {"registry", pinned.cohort_id}  # untouched
        assert await repo.sweep_cohorts(after_expiry) == preview  # …and it was right

    async def test_dry_run_on_a_registry_less_warehouse_does_not_try_to_create_one(
        self, repo: DuckDbAnalyticalRepository, warehouse: Path
    ) -> None:
        """A read-only connection cannot run DDL, so a pre-registry warehouse
        must be reported, not repaired."""
        import duckdb

        con = duckdb.connect(str(warehouse))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS cohort_store")
            con.execute("CREATE TABLE cohort_store.cohort_0123456789ab AS SELECT 1 AS entity_id")
        finally:
            con.close()

        preview = await repo.sweep_cohorts(datetime(2026, 8, 7, tzinfo=UTC), dry_run=True)

        assert preview.orphaned == ("cohort_0123456789ab",)
        assert "registry" not in _cohort_table_names(warehouse)

    async def test_a_naive_now_is_read_as_utc(self, repo: DuckDbAnalyticalRepository) -> None:
        wm = await _newest(repo)
        pinned = await repo.materialize_cohort(_cohort_definition(), watermark=wm)
        naive = (pinned.created_at + timedelta(seconds=pinned.ttl_seconds + 1)).replace(tzinfo=None)

        assert (await repo.sweep_cohorts(naive)).expired == (pinned.cohort_id,)


# ---------------------------------------------------------------------------
# 3. exclusion polarity — `exclusions` REMOVES the population it names
#
# The compiler emits `FILTER (WHERE NOT <exclusions>)` on numerator and
# denominator alike (compile._measure_expr_sql). Nothing about a metric's
# shape reveals which way that goes: an inverted exclusion compiles cleanly,
# returns plausible numbers, and reports them under the metric's own name.
# packs/base-rcm shipped seven exclusions written as inclusion predicates and
# only one of them ever surfaced, so the law is pinned here in executed
# numbers rather than left to the reader of a YAML file.


_POLARITY_TARGET = "INSTITUTIONAL"  # a claim_type value, certified dimension


def _polarity_contract(metric_id: str, *, exclude: bool | None) -> MetricContract:
    """Distinct claims, optionally with the target population excluded
    (``exclude=True``) or kept alone (``exclude=False``)."""
    target = Predicate(DimensionRef("claim_type"), PredicateOp.EQ, (_POLARITY_TARGET,))
    numerator = CountDistinct(FieldRef("claim_id"))
    return MetricContract(
        id=metric_id,
        version=1,
        kind=MetricKind.FLOW,
        entity_grain=EntityGrain.CLAIM,
        numerator=numerator if exclude is not False else Filtered(numerator, target),
        denominator=None,
        primary_date_basis=SERVICE,
        allowed_date_bases=(SERVICE,),
        scope_dimensions=(DimensionRef("claim_type"), DimensionRef("payer")),
        sign=SignConvention.NEUTRAL,
        unit=MetricUnit.COUNT,
        exclusions=target if exclude else None,
    )


_POLARITY_METRICS = {
    "polarity_all": _polarity_contract("polarity_all", exclude=None),
    "polarity_excluded": _polarity_contract("polarity_excluded", exclude=True),
    "polarity_target_only": _polarity_contract("polarity_target_only", exclude=False),
}


def _polarity_metric(metric_id: str) -> MetricContract | None:
    return _POLARITY_METRICS.get(metric_id)


class TestExclusionPolarity:
    @pytest.fixture
    def repository(
        self, small_warehouse_path: Path, catalog: CatalogSnapshot
    ) -> DuckDbAnalyticalRepository:
        return DuckDbAnalyticalRepository(small_warehouse_path, catalog, _polarity_metric)

    def _probe(self, **overrides: object) -> AggregationProbe:
        defaults: dict[str, object] = {
            "measures": (
                MetricRef("polarity_all"),
                MetricRef("polarity_excluded"),
                MetricRef("polarity_target_only"),
            ),
            "dimensions": (),
            "scope": EMPTY_SCOPE,
            "window": TimeWindow(basis=SERVICE, range=_H1_2026),
            "grain": Grain(EntityGrain.CLAIM),
        }
        defaults.update(overrides)
        return AggregationProbe(**defaults)  # type: ignore[arg-type]

    async def test_the_excluded_population_is_subtracted_not_selected(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = await _newest(repository)
        frame = await repository.execute(self._probe(), watermark=wm)
        assert frame.schema.names == ("polarity_all", "polarity_excluded", "polarity_target_only")
        total, excluded, target_only = frame.rows[0]
        assert isinstance(total, int) and isinstance(excluded, int) and isinstance(target_only, int)

        # precondition: the target population is non-empty and not everything,
        # otherwise the assertions below would hold for either polarity.
        assert 0 < target_only < total

        # the law: `exclusions` removes. Were it read as a `where:` clause,
        # `polarity_excluded` would equal `polarity_target_only`.
        assert excluded == total - target_only
        assert excluded != target_only

    async def test_the_excluded_population_is_absent_from_a_cut(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        """Cut the same three measures by the very dimension the exclusion
        names: the excluded value must contribute zero on its own row while
        the unfiltered measure still counts it."""
        wm = await _newest(repository)
        frame = await repository.execute(
            self._probe(dimensions=(DimensionRef("claim_type"),)), watermark=wm
        )
        by_type = {row[0]: (row[1], row[2], row[3]) for row in frame.rows}
        assert _POLARITY_TARGET in by_type, "fixture warehouse has no institutional claims"

        total, excluded, target_only = by_type[_POLARITY_TARGET]
        assert total > 0  # the rows are there
        assert excluded == 0  # ...and the exclusion took every one of them
        assert target_only == total

        others = {k: v for k, v in by_type.items() if k != _POLARITY_TARGET}
        assert others, "fixture warehouse has only institutional claims"
        for claim_type, (row_total, row_excluded, row_target_only) in others.items():
            assert row_excluded == row_total, claim_type  # untouched
            assert row_target_only == 0, claim_type

    async def test_exclusions_apply_to_a_scoped_population_too(
        self, repository: DuckDbAnalyticalRepository
    ) -> None:
        """Scoping *to* the excluded population leaves nothing: the analyst's
        filter and the contract's exclusion conjoin, they do not cancel."""
        wm = await _newest(repository)
        frame = await repository.execute(
            self._probe(
                scope=Predicate(DimensionRef("claim_type"), PredicateOp.EQ, (_POLARITY_TARGET,))
            ),
            watermark=wm,
        )
        total, excluded, target_only = frame.rows[0]
        assert total > 0 and target_only == total
        assert excluded == 0


# ---------------------------------------------------------------------------
# 4. reference answer-key regressions (full-scale warehouse in data/)


@pytest.fixture(scope="session")
def reference_repository(catalog: CatalogSnapshot) -> DuckDbAnalyticalRepository:
    if not REFERENCE_DB.exists():
        pytest.skip("data/revi_warehouse.duckdb not generated")
    return DuckDbAnalyticalRepository(REFERENCE_DB, catalog, fixture_metrics)


@pytest.fixture(scope="session")
def answer_key() -> dict[str, object]:
    if not REFERENCE_KEY.exists():
        pytest.skip("data/answer_key.json not generated")
    return json.loads(REFERENCE_KEY.read_text())  # type: ignore[no-any-return]


def _scenario3(answer_key: dict[str, object]) -> dict[str, object]:
    scenarios = answer_key["scenarios"]
    assert isinstance(scenarios, dict)
    return scenarios["3_cash_decline"]["snap_003"]  # type: ignore[index,no-any-return]


@pytest.mark.reference
class TestReferenceAnswerKey:
    async def test_watermarks_match_design(self, reference_repository: DuckDbAnalyticalRepository) -> None:
        watermarks = await reference_repository.list_watermarks()
        assert [w.id for w in watermarks] == ["wm_001", "wm_002", "wm_003"]
        assert [str(w.newest_data_date) for w in watermarks] == ["2026-07-31", "2026-08-01", "2026-08-02"]

    async def test_reference_week_cash_by_payer_matches_scenario3(
        self, reference_repository: DuckDbAnalyticalRepository, answer_key: dict[str, object]
    ) -> None:
        s3 = _scenario3(answer_key)
        wm = (await reference_repository.list_watermarks())[-1]
        probe = AggregationProbe(
            measures=(MetricRef("cash_posted"),),
            dimensions=(DimensionRef("payer"),),
            scope=EMPTY_SCOPE,
            window=TimeWindow(basis=POST, range=AbsoluteRange(date(2026, 7, 20), date(2026, 8, 2))),
            grain=Grain(EntityGrain.TRANSACTION, TimeBucket.WEEK),
        )
        frame = await reference_repository.execute(probe, watermark=wm)
        assert frame.schema.names == ("payer", "week", "cash_posted")
        wk_prior, wk_decline = date(2026, 7, 20), date(2026, 7, 27)
        cash: dict[tuple[str, date], int] = {(r[0], r[1]): r[2] for r in frame.rows}  # type: ignore[misc]
        assert {week for _, week in cash} == {wk_prior, wk_decline}

        # exact weekly totals (payer cash = txn_type PAYMENT only)
        prior_total = sum(v for (_, week), v in cash.items() if week == wk_prior)
        decline_total = sum(v for (_, week), v in cash.items() if week == wk_decline)
        assert prior_total == s3["week_prior"]["payer_cash_cents"]  # type: ignore[index]
        assert decline_total == s3["week_decline"]["payer_cash_cents"]  # type: ignore[index]

        # exact per-payer attribution for all twelve payers
        by_payer = s3["by_payer"]
        assert isinstance(by_payer, list) and len(by_payer) == 12
        for entry in by_payer:
            payer = entry["payer_name"]
            assert cash.get((payer, wk_prior), 0) == entry["week_prior_cents"], payer
            assert cash.get((payer, wk_decline), 0) == entry["week_decline_cents"], payer
        # the two planted decliners lead the drop
        drops = sorted(by_payer, key=lambda e: e["delta_cents"])  # type: ignore[arg-type,return-value]
        assert {drops[0]["payer_name"], drops[1]["payer_name"]} == {"State Medicaid", "Atlas Commercial"}

    async def test_reference_denial_rate_monthly_shows_carc197_break(
        self, reference_repository: DuckDbAnalyticalRepository, answer_key: dict[str, object]
    ) -> None:
        scenarios = answer_key["scenarios"]
        assert isinstance(scenarios, dict)
        monthly = scenarios["1_denial_spike_meridian_imaging"]["snap_003"]["monthly_by_first_remit"]
        wm = (await reference_repository.list_watermarks())[-1]
        probe = AggregationProbe(
            measures=(MetricRef("denial_rate"),),
            dimensions=(),
            scope=And(
                (
                    Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Halvern Health",)),
                    Predicate(DimensionRef("service_line"), PredicateOp.EQ, ("Imaging",)),
                )
            ),
            window=TimeWindow(basis=REMIT, range=AbsoluteRange(date(2025, 1, 1), date(2026, 8, 2))),
            grain=Grain(EntityGrain.DENIAL, TimeBucket.MONTH),
        )
        frame = await reference_repository.execute(probe, watermark=wm)
        assert frame.schema.names == ("month", "denial_rate__num", "denial_rate__den")
        got = {row[0].strftime("%Y-%m"): (row[1], row[2]) for row in frame.rows}  # type: ignore[union-attr]
        key_197 = {m["remit_month"]: m["carc197_denied_claims"] for m in monthly}

        # Planted CARC-197 denials ride the claim's first remit, so denial
        # month == first-remit month: the probe's numerator must equal the
        # answer key's carc197_denied_claims month by month, exactly.
        for month, (num, _) in got.items():
            assert num == key_197.get(month, 0), month
        for month, expected in key_197.items():
            if expected:
                assert month in got and got[month][0] == expected, month

        # The break: CARC-197 share of denied claims jumps from 2026-06 on.
        pre = [n / d for m, (n, d) in got.items() if m < "2026-06" and d]
        post = [n / d for m, (n, d) in got.items() if "2026-06" <= m <= "2026-07" and d]
        assert pre and post
        assert sum(post) / len(post) >= 2 * (sum(pre) / len(pre))

    async def test_reference_weekly_cash_by_payer_type_last_13_weeks(
        self, reference_repository: DuckDbAnalyticalRepository, answer_key: dict[str, object]
    ) -> None:
        """Regression anchor for the '3.25 months of payer payments by payer
        type, weekly' guide question: trailing ~13 weeks resolve via the
        kernel and come back as 13 Monday-aligned weekly buckets."""
        s3 = _scenario3(answer_key)
        wm = (await reference_repository.list_watermarks())[-1]
        anchor = wm.newest_data_date  # 2026-08-02
        window = resolve_relative(RelativeRange(Decimal(13), TimeUnit.WEEK, RangeMode.TRAILING), anchor)
        assert window == AbsoluteRange(date(2026, 5, 4), date(2026, 8, 2))
        probe = AggregationProbe(
            measures=(MetricRef("cash_posted"),),
            dimensions=(DimensionRef("payer_type"),),
            scope=EMPTY_SCOPE,
            window=TimeWindow(basis=POST, range=window),
            grain=Grain(EntityGrain.TRANSACTION, TimeBucket.WEEK),
        )
        frame = await reference_repository.execute(probe, watermark=wm)
        assert frame.schema.names == ("payer_type", "week", "cash_posted")
        weeks = sorted({row[1] for row in frame.rows})  # type: ignore[type-var]
        assert len(weeks) == 13
        assert all(isinstance(w, date) and w.weekday() == 0 for w in weeks)  # ISO Mondays
        assert weeks[0] == date(2026, 5, 4) and weeks[-1] == date(2026, 7, 27)
        types = {row[0] for row in frame.rows}
        domain = {"COMMERCIAL", "MEDICARE", "MEDICARE_ADVANTAGE", "MEDICAID", "MEDICAID_MCO", "BCBS", "OTHER"}
        assert types <= domain
        assert len(types) >= 4
        assert all(isinstance(row[2], int) and row[2] > 0 for row in frame.rows)

        def week_total(week: date) -> int:
            return sum(row[2] for row in frame.rows if row[1] == week)  # type: ignore[misc]

        assert week_total(date(2026, 7, 20)) == s3["week_prior"]["payer_cash_cents"]  # type: ignore[index]
        assert week_total(date(2026, 7, 27)) == s3["week_decline"]["payer_cash_cents"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# 5. the shipped pack's own contracts, through the same probe path
#
# Everything above resolves metrics through `fixture_metrics`. These run the
# real `packs/base-rcm` contracts, so they answer the question the fixtures
# structurally cannot: does the governed content this repository actually
# ships compile and execute? Added with the 2026-08-08 exclusion-polarity
# correction (packs/base-rcm/NOTES.md).

PACK_DIR = ROOT / "packs" / "base-rcm"
OVERLAY_DIR = ROOT / "packs" / "overlays" / "demo-tenant"

# A window comfortably inside the generated data, ending at wm_003's newest
# data date so nothing depends on how the trailing edge is resolved.
_PACK_WINDOW = AbsoluteRange(date(2026, 5, 1), date(2026, 8, 2))


@pytest.fixture(scope="session")
def pack_metrics():  # type: ignore[no-untyped-def]
    """The base pack + demo-tenant overlay, composed as `revi_api.wiring` does."""
    from revi_pack.loader import load_layer
    from revi_pack.snapshot import build_snapshot

    return build_snapshot([load_layer(PACK_DIR), load_layer(OVERLAY_DIR)]).metric


@pytest.fixture(scope="session")
def pack_reference_repository(  # type: ignore[no-untyped-def]
    catalog: CatalogSnapshot, pack_metrics
) -> DuckDbAnalyticalRepository:
    if not REFERENCE_DB.exists():
        pytest.skip("data/revi_warehouse.duckdb not generated")
    return DuckDbAnalyticalRepository(REFERENCE_DB, catalog, pack_metrics)


@pytest.fixture
def reference_con() -> Iterator[Any]:
    """A raw read-only connection to the reference warehouse.

    Every number a probe returns below is also computed here by a hand-written
    query that shares no code with the compiler — the two paths agreeing is
    the actual claim, not the constant.

    Deliberately function-scoped: DuckDB refuses a read-write connection to a
    file that any read-only connection in the process still holds, so a
    session-scoped handle here would break cohort materialization in every
    later test module.
    """
    if not REFERENCE_DB.exists():
        pytest.skip("data/revi_warehouse.duckdb not generated")
    con = duckdb.connect(str(REFERENCE_DB), read_only=True)
    try:
        yield con
    finally:
        con.close()


def _one(con: Any, sql: str) -> tuple[Any, ...]:
    row = con.execute(sql).fetchone()
    assert row is not None
    return tuple(row)


#: The reference window as SQL, so hand-written cross-checks and the probes
#: they check cannot drift apart.
_W = "DATE '2026-05-01' AND DATE '2026-08-02'"
_AS_OF = "DATE '2026-08-02'"

#: Open inventory as-of, the snapshot compiler's own definition restated.
_OPEN_INVENTORY = f"""
    FROM snap_003.v_claim c
    WHERE c.service_date <= {_AS_OF}
      AND (c.submission_date IS NULL OR c.submission_date <= {_AS_OF})
      AND (c.resolved_date IS NULL OR c.resolved_date > {_AS_OF})
"""


def _pack_probe(*metric_ids: str, **overrides: object) -> AggregationProbe:
    defaults: dict[str, object] = {
        "measures": tuple(MetricRef(m) for m in metric_ids),
        "dimensions": (),
        "scope": EMPTY_SCOPE,
        "window": TimeWindow(basis=SUBMISSION, range=_PACK_WINDOW),
        "grain": Grain(EntityGrain.CLAIM),
    }
    defaults.update(overrides)
    return AggregationProbe(**defaults)  # type: ignore[arg-type]


@pytest.mark.reference
class TestReferencePackContracts:
    async def test_clean_claim_rate_executes_and_reads_the_whole_window(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """v2's exclusion, executed. `status` is certified, so
        `exclusions: {status eq OPEN}` compiles to
        `FILTER (WHERE NOT (status = 'OPEN'))` on BOTH sides and the
        population is adjudicated claims only."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(_pack_probe("clean_claim_rate"), watermark=wm)
        assert frame.schema.names == ("clean_claim_rate__num", "clean_claim_rate__den")
        numerator, denominator = frame.rows[0]
        assert isinstance(numerator, int) and isinstance(denominator, int)
        assert (numerator, denominator) == (13_725, 15_068)
        assert 0.9 < numerator / denominator < 0.92
        # every dimension it touches is certified, so nothing downgrades it
        assert frame.evidence_grade is EvidenceGrade.DIRECT

    async def test_clean_claim_rate_denominator_is_every_claim_in_the_window(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """The exclusion is symmetric and it bites: the denominator is
        claim_volume MINUS the un-adjudicated claims in the window, which is
        exactly the population the contract's caveat describes."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe("clean_claim_rate", "claim_volume"), watermark=wm
        )
        assert frame.schema.names == (
            "clean_claim_rate__num",
            "clean_claim_rate__den",
            "claim_volume",
        )
        _, denominator, claim_volume = frame.rows[0]
        assert claim_volume == 18_410  # every claim in the window
        assert denominator == 15_068  # ...minus the 3,342 still awaiting a remit

    async def test_clean_and_denied_partition_the_same_population(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """`clean_claim` is a non-null boolean and both contracts now carry
        the SAME adjudicated-only exclusion, so their numerators sum to the
        shared denominator — the "over an identical population the two sum
        to one" claim in both descriptions, asserted rather than
        asserted-in-prose. It is also the invariant that forced
        clean_claim_rate to v2 alongside denial_rate: restoring one
        population and not the other would have broken it silently."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe("clean_claim_rate", "denial_rate"), watermark=wm
        )
        clean_num, clean_den, denied_num, denied_den = frame.rows[0]
        assert clean_den == denied_den == 15_068
        assert clean_num + denied_num == clean_den
        # v1 read (13_725, 4_685) over 18_410: 3,342 of those 4,685 "denials"
        # were claims with no adjudication outcome at all.
        assert (clean_num, denied_num) == (13_725, 1_343)

    async def test_clean_claim_rate_cut_by_payer_reconciles_to_the_total(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        wm = (await pack_reference_repository.list_watermarks())[-1]
        cut = await pack_reference_repository.execute(
            _pack_probe("clean_claim_rate", dimensions=(DimensionRef("payer"),)), watermark=wm
        )
        assert cut.schema.names == ("payer", "clean_claim_rate__num", "clean_claim_rate__den")
        assert len(cut.rows) == 12
        assert sum(row[1] for row in cut.rows) == 13_725  # type: ignore[misc]
        assert sum(row[2] for row in cut.rows) == 15_068  # type: ignore[misc]

    async def test_denials_unworked_pct_v2_removes_patient_responsibility(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """The repaired exclusion, executed. Cut by the dimension it names:
        PATIENT_RESP contributes zero on both sides while its rows plainly
        exist, and every other category is untouched."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = AggregationProbe(
            measures=(MetricRef("denials_unworked_pct"),),
            dimensions=(DimensionRef("denial_category"),),
            scope=EMPTY_SCOPE,
            window=TimeWindow(basis=REMIT, range=_PACK_WINDOW),
            grain=Grain(EntityGrain.DENIAL),
        )
        frame = await pack_reference_repository.execute(probe, watermark=wm)
        by_category = {row[0]: (row[1], row[2]) for row in frame.rows}
        assert "PATIENT_RESP" in by_category, "no patient-responsibility denials to exclude"
        assert by_category["PATIENT_RESP"] == (0, 0)
        assert all(den > 0 for cat, (_, den) in by_category.items() if cat != "PATIENT_RESP")

        total = await pack_reference_repository.execute(
            AggregationProbe(
                measures=(MetricRef("denials_unworked_pct"),),
                dimensions=(),
                scope=EMPTY_SCOPE,
                window=TimeWindow(basis=REMIT, range=_PACK_WINDOW),
                grain=Grain(EntityGrain.DENIAL),
            ),
            watermark=wm,
        )
        # v1's inverted exclusion computed 41/62 — the patient-responsibility
        # records alone. v2 computes the workable-denial population.
        assert total.rows[0] == (1_182, 1_520)

    async def test_first_pass_yield_executes_once_the_flag_is_certified(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """Was unanswerable: the numerator filters on `first_pass_paid`, which
        the catalog did not certify as a dimension. Certifying the flag (a
        predicate needs a dimension; a date basis carries a window) is the
        whole fix — the contract is untouched."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(_pack_probe("first_pass_yield"), watermark=wm)
        assert frame.schema.names == ("first_pass_yield__num", "first_pass_yield__den")
        assert frame.rows[0] == (14_318, 18_410)
        assert frame.evidence_grade is EvidenceGrade.DIRECT

    async def test_first_pass_yield_numerator_includes_adjudicated_open_claims(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The OPEN trap, executed in the direction that surprises people.
        `first_pass_paid` reads the remit, not the cash, so claims still
        standing OPEN because payment has not posted DO belong in the
        numerator — unlike `clean_claim`, which reads false on every one of
        them. Both halves asserted so a "fix" to either flag is loud."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe("first_pass_yield", "clean_claim_rate"), watermark=wm
        )
        yield_num, _, clean_num, _ = frame.rows[0]
        assert isinstance(yield_num, int) and isinstance(clean_num, int)
        assert (yield_num, clean_num) == (14_318, 13_725)
        # The gap is not approximately the OPEN-but-adjudicated claims — it is
        # exactly them, and nothing is clean without being first-pass-paid.
        open_and_paid, clean_not_paid = _one(
            reference_con,
            "SELECT count(*) FILTER (WHERE status = 'OPEN' AND first_pass_paid), "
            "       count(*) FILTER (WHERE clean_claim AND NOT first_pass_paid) "
            f"FROM snap_003.v_claim WHERE submission_date BETWEEN {_W}",
        )
        assert yield_num - clean_num == open_and_paid == 593
        assert clean_not_paid == 0
        (open_and_clean,) = _one(
            reference_con,
            "SELECT count(*) FROM snap_003.v_claim WHERE status = 'OPEN' AND clean_claim",
        )
        assert open_and_clean == 0

    async def test_denial_rate_cannot_be_probed_on_its_own_primary_basis(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """Adjacent, separately reported gap: denial_rate is claim-grain with
        `primary_date_basis: remit`, but REMIT is bound only on the remit,
        transaction and denial entities. The contract's alternates work."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        with pytest.raises(DateBasisInvalidError):
            await pack_reference_repository.execute(
                _pack_probe("denial_rate", window=TimeWindow(basis=REMIT, range=_PACK_WINDOW)),
                watermark=wm,
            )
        service = await pack_reference_repository.execute(
            _pack_probe("denial_rate", window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW)),
            watermark=wm,
        )
        # v1 read (7_484, 19_672) = 38.0%: every un-adjudicated claim in
        # the window counted as denied. v2 excludes them on both sides.
        assert service.rows[0] == (1_212, 13_400)


# ---------------------------------------------------------------------------
# 6. the previously-dead contracts, now executing
#
# Thirteen shipped contracts had never executed through the probe path. Twelve
# of them light up here; each test executes the real pack contract and compares
# the result with a hand-written query over the same base views. `dnfb_dollars`
# and `timely_filing_at_risk_dollars` joined the list once their `filtered:`
# predicates were renamed from the raw date columns to the certified
# `billed_flag` / `discharged_flag` dimensions (the population is unchanged —
# both tests below assert the flag form and the raw-date form agree). The one
# contract still dark is `denial_rate` on its remit basis, pinned above.


@pytest.mark.reference
class TestPreviouslyDeadContracts:
    """Every assertion is a pair: the compiled probe, and the same number
    derived independently in SQL. A constant on its own would only pin
    whatever the compiler happens to emit."""

    async def test_net_and_gross_collection_rate_compile_across_entity_grains(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The headline fix: `payment_cents` lives at the transaction grain and
        the denominators at the claim grain, which used to raise
        GrainIncompatibleError inside the compiler. Both sides now aggregate
        over the same service-date cohort and the kernel still divides."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe(
                "net_collection_rate",
                "gross_collection_rate",
                window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW),
            ),
            watermark=wm,
        )
        assert frame.schema.names == (
            "net_collection_rate__num",
            "net_collection_rate__den",
            "gross_collection_rate__num",
            "gross_collection_rate__den",
        )
        net_num, net_den, gross_num, gross_den = frame.rows[0]
        (sql_payments,) = _one(
            reference_con,
            "SELECT SUM(amount_cents) FILTER (WHERE txn_type = 'PAYMENT') "
            f"FROM snap_003.v_transaction WHERE service_date BETWEEN {_W}",
        )
        sql_expected, sql_billed = _one(
            reference_con,
            "SELECT SUM(expected_amount_cents), SUM(billed_amount_cents) "
            f"FROM snap_003.v_claim WHERE service_date BETWEEN {_W}",
        )
        assert (net_num, net_den) == (sql_payments, sql_expected) == (1_494_532_901, 2_623_183_106)
        assert (gross_num, gross_den) == (sql_payments, sql_billed) == (1_494_532_901, 5_642_309_382)
        # 56.97% of contract-expected realized so far; 26.49% of gross charges.
        assert 0.56 < net_num / net_den < 0.58
        assert frame.evidence_grade is EvidenceGrade.DIRECT

    async def test_cross_entity_ratio_reconciles_cut_by_payer(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The slicing law across the join: cut by payer, both sides must sum
        back to the ungrouped totals, and no cell may go missing on either
        side of the FULL OUTER JOIN."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        cut = await pack_reference_repository.execute(
            _pack_probe(
                "net_collection_rate",
                dimensions=(DimensionRef("payer"),),
                window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW),
            ),
            watermark=wm,
        )
        assert cut.schema.names == ("payer", "net_collection_rate__num", "net_collection_rate__den")
        assert len(cut.rows) == 12
        assert all(row[0] is not None for row in cut.rows)
        assert sum(row[1] for row in cut.rows) == 1_494_532_901  # type: ignore[misc]
        assert sum(row[2] for row in cut.rows) == 2_623_183_106  # type: ignore[misc]
        by_payer = {row[0]: (row[1], row[2]) for row in cut.rows}
        for payer, num, den in reference_con.execute(
            "SELECT c.payer_name, "
            "  (SELECT COALESCE(SUM(t.amount_cents) FILTER (WHERE t.txn_type = 'PAYMENT'), 0) "
            "   FROM snap_003.v_transaction t "
            f"   WHERE t.payer_name = c.payer_name AND t.service_date BETWEEN {_W}), "
            "  SUM(c.expected_amount_cents) "
            f"FROM snap_003.v_claim c WHERE c.service_date BETWEEN {_W} "
            "GROUP BY c.payer_name ORDER BY c.payer_name"
        ).fetchall():
            assert by_payer[payer] == (num, den), payer

    async def test_cross_entity_ratio_keeps_cells_only_one_side_has(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The reason the blocks are FULL OUTER joined rather than left joined.

        August service cohorts have expected dollars but no posted cash yet, so
        those cells exist in the claim block and not the transaction one. They
        survive with a NULL numerator — which `revi_calculation.ratio` renders
        as no reading rather than as 0% — and both column totals still
        reconcile to the ungrouped run.
        """
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe(
                "net_collection_rate",
                dimensions=(DimensionRef("payer"),),
                window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW),
                grain=Grain(EntityGrain.CLAIM, TimeBucket.MONTH),
            ),
            watermark=wm,
        )
        assert frame.schema.names == (
            "payer",
            "month",
            "net_collection_rate__num",
            "net_collection_rate__den",
        )
        num_missing = [row for row in frame.rows if row[2] is None]
        assert num_missing, "no one-sided cell in this window — the join shape is untested"
        assert all(row[1] == date(2026, 8, 1) and row[3] for row in num_missing)
        assert all(row[0] is not None and row[1] is not None for row in frame.rows)
        assert sum(row[2] for row in frame.rows if row[2] is not None) == 1_494_532_901  # type: ignore[misc]
        assert sum(row[3] for row in frame.rows) == 2_623_183_106  # type: ignore[misc]
        (cells,) = _one(
            reference_con,
            "SELECT count(*) FROM (SELECT payer_name, date_trunc('month', service_date) "
            f"FROM snap_003.v_claim WHERE service_date BETWEEN {_W} GROUP BY 1, 2)",
        )
        assert len(frame.rows) == cells

    async def test_cross_entity_ratio_rejects_having(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """A HAVING predicate on a cross-entity probe would filter one block
        and not the other — a denominator-law violation. Refused, not applied."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = _pack_probe(
            "net_collection_rate",
            window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW),
            having=(
                MeasurePredicate(
                    measure=MetricRef("claim_volume"), op=PredicateOp.RANGE, values=(0, 10)
                ),
            ),
        )
        with pytest.raises(SourceCapabilityUnsupportedError):
            await pack_reference_repository.execute(probe, watermark=wm)

    async def test_avg_days_to_pay_delivers_the_payment_lag_derived_measure(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe(
                "avg_days_to_pay",
                window=TimeWindow(basis=POST, range=_PACK_WINDOW),
                grain=Grain(EntityGrain.TRANSACTION),
            ),
            watermark=wm,
        )
        expected = _one(
            reference_con,
            "SELECT SUM(datediff('day', submission_date, post_date)) "
            "         FILTER (WHERE txn_type = 'PAYMENT'), COUNT(*) "
            f"FROM snap_003.v_transaction WHERE post_date BETWEEN {_W}",
        )
        assert frame.rows[0] == expected == (328_459, 48_984)
        # 6.7 days, diluted by the non-payment transactions the contract's
        # denominator counts and its description warns about.
        assert 6.0 < 328_459 / 48_984 < 7.0

    async def test_bill_lag_days_delivers_the_submission_lag_derived_measure(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(_pack_probe("bill_lag_days"), watermark=wm)
        expected = _one(
            reference_con,
            "SELECT SUM(datediff('day', service_date, submission_date)), COUNT(DISTINCT claim_id) "
            f"FROM snap_003.v_claim WHERE submission_date BETWEEN {_W}",
        )
        assert frame.rows[0] == expected == (149_973, 18_410)
        assert 8.0 < 149_973 / 18_410 < 8.3  # days, service to submission

    async def test_charge_lag_and_late_charge_share_the_charge_capture_pair(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe(
                "charge_lag_days",
                "late_charge_pct",
                window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW),
                grain=Grain(EntityGrain.LINE),
            ),
            watermark=wm,
        )
        lag_num, lag_den, late_num, late_den = frame.rows[0]
        expected = _one(
            reference_con,
            "SELECT SUM(datediff('day', service_date, charge_entry_date)), "
            "       COUNT(DISTINCT claim_line_id), "
            "       SUM(CASE WHEN datediff('day', service_date, charge_entry_date) > 3 "
            "                THEN billed_amount_cents ELSE 0 END), "
            "       SUM(billed_amount_cents) "
            f"FROM snap_003.v_claim_line WHERE service_date BETWEEN {_W}",
        )
        assert (lag_num, lag_den, late_num, late_den) == expected
        assert (lag_num, lag_den) == (100_670, 48_294)  # 2.08 days mean
        assert (late_num, late_den) == (953_661_749, 5_697_582_337)  # 16.7% of charges
        assert 2.0 < lag_num / lag_den < 2.2

    async def test_underpayment_variance_floors_per_claim_and_never_nets(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The derived measure's whole point: floor at zero per claim, then sum
        over adjudicated claims only.

        On this warehouse the floor is a *guard*, not an effect: no claim is
        allowed more than its expected amount, so the floored and netted forms
        agree exactly. Both facts are asserted rather than one of them assumed
        — if the generator ever plants an overpayment, the equality breaks and
        whoever changed it has to decide deliberately."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe("underpayment_variance", window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW)),
            watermark=wm,
        )
        floored, netted = _one(
            reference_con,
            "WITH a AS (SELECT claim_id, SUM(allowed_amount_cents) AS allowed "
            "           FROM snap_003.v_claim_line GROUP BY claim_id) "
            "SELECT SUM(GREATEST(c.expected_amount_cents - a.allowed, 0)), "
            "       SUM(c.expected_amount_cents - a.allowed) "
            "FROM snap_003.v_claim c JOIN a USING (claim_id) "
            f"WHERE c.service_date BETWEEN {_W} AND a.allowed IS NOT NULL",
        )
        assert frame.rows[0] == (floored,) == (14_306_720,)
        assert netted == floored
        (overpaid_claims,) = _one(
            reference_con,
            "WITH a AS (SELECT claim_id, SUM(allowed_amount_cents) AS allowed "
            "           FROM snap_003.v_claim_line GROUP BY claim_id) "
            "SELECT count(*) FROM snap_003.v_claim c JOIN a USING (claim_id) "
            f"WHERE c.service_date BETWEEN {_W} AND a.allowed > c.expected_amount_cents",
        )
        assert overpaid_claims == 0, "the floor now bites: netting is no longer a no-op"
        # Un-adjudicated claims are excluded, not counted as fully underpaid:
        # including them would inflate the variance by their whole expected value.
        (naive_including_unadjudicated,) = _one(
            reference_con,
            "WITH a AS (SELECT claim_id, SUM(allowed_amount_cents) AS allowed "
            "           FROM snap_003.v_claim_line GROUP BY claim_id) "
            "SELECT SUM(GREATEST(c.expected_amount_cents - COALESCE(a.allowed, 0), 0)) "
            "FROM snap_003.v_claim c LEFT JOIN a USING (claim_id) "
            f"WHERE c.service_date BETWEEN {_W}",
        )
        assert naive_including_unadjudicated > 50 * floored

    async def test_the_per_claim_line_rollup_does_not_fan_out_the_claim_grain(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """`underpayment_cents` needs summed line allowed amounts, which means a
        join from the claim grain down to lines — the classic fan-out bug. The
        rollup is pre-aggregated to one row per claim, so a claim count probed
        alongside it must be unchanged. Cut by payer too: fan-out shows up per
        cell before it shows up in a total."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        window = TimeWindow(basis=SERVICE, range=_PACK_WINDOW)
        alone = await pack_reference_repository.execute(
            _pack_probe("claim_volume", window=window, dimensions=(DimensionRef("payer"),)),
            watermark=wm,
        )
        together = await pack_reference_repository.execute(
            _pack_probe(
                "claim_volume",
                "underpayment_variance",
                window=window,
                dimensions=(DimensionRef("payer"),),
            ),
            watermark=wm,
        )
        assert [(row[0], row[1]) for row in alone.rows] == [
            (row[0], row[1]) for row in together.rows
        ]
        assert sum(row[1] for row in alone.rows) == 19_672  # type: ignore[misc]

    async def test_days_in_ar_is_the_billed_weighted_age_of_open_receivables(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = SnapshotProbe(
            measures=(MetricRef("days_in_ar"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        frame = await pack_reference_repository.execute(probe, watermark=wm)
        expected = _one(
            reference_con,
            "SELECT SUM(c.billed_amount_cents * datediff('day', c.service_date, "
            f"           {_AS_OF})) FILTER (WHERE c.status IN ('OPEN', 'DENIED')), "
            "       SUM(c.billed_amount_cents) FILTER (WHERE c.status IN ('OPEN', 'DENIED')) "
            + _OPEN_INVENTORY,
        )
        assert frame.rows[0] == expected == (548_063_722_723, 3_438_036_345)
        # The denominator is exactly ar_over_120_pct's open population
        # (Appendix A), which is the reconciliation that matters: two
        # snapshot contracts must value the same A/R identically.
        assert expected[1] == 3_438_036_345
        # 159 days: the aging form, and this warehouse's A/R really is old —
        # 41% of open dollars sit past 120 days with a 578-day tail. NOT the
        # MAP FM-1 net-days figure, which the contract says it is not.
        assert 155 < expected[0] / expected[1] < 165

    async def test_credit_balance_dollars_floors_at_zero_after_refunds(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = SnapshotProbe(
            measures=(MetricRef("credit_balance_dollars"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        frame = await pack_reference_repository.execute(probe, watermark=wm)
        (expected,) = _one(
            reference_con,
            "SELECT SUM(GREATEST(GREATEST(COALESCE(t.cash_in, 0) - c.expected_amount_cents, 0) "
            "                    - COALESCE(t.refunds, 0), 0)) "
            "FROM snap_003.v_claim c LEFT JOIN ("
            "  SELECT claim_id, "
            "    COALESCE(SUM(amount_cents) FILTER ("
            "      WHERE txn_type IN ('PAYMENT', 'PATIENT_PAYMENT')), 0) AS cash_in, "
            "    COALESCE(SUM(amount_cents) FILTER (WHERE txn_type = 'REFUND'), 0) AS refunds "
            f"  FROM snap_003.v_transaction WHERE post_date <= {_AS_OF} GROUP BY claim_id"
            ") t USING (claim_id) "
            f"WHERE c.service_date <= {_AS_OF} "
            f"  AND (c.submission_date IS NULL OR c.submission_date <= {_AS_OF}) "
            f"  AND (c.resolved_date IS NULL OR c.resolved_date > {_AS_OF})",
        )
        assert frame.rows[0] == (expected,) == (5_221_798,)

    async def test_dnfb_dollars_reads_the_certified_flags_not_the_raw_dates(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The rename, proved to be a rename.

        The contract's `filtered:` predicates now say `discharged_flag eq true`
        AND `billed_flag eq false` where they used to say `NOT discharge_date
        IS NULL` AND `submission_date IS NULL`. The flags are materialized from
        exactly those two columns (`warehouse/generator/src/revi_warehouse/writer.py`,
        guarded by `verify.py`), so the SECOND query below is the pre-rename
        definition spelled out in raw columns: probe, flag-form SQL and
        date-form SQL must all agree, or the rename changed the population.
        """
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = SnapshotProbe(
            measures=(MetricRef("dnfb_dollars"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        frame = await pack_reference_repository.execute(probe, watermark=wm)
        (by_flags,) = _one(
            reference_con,
            "SELECT SUM(c.billed_amount_cents) FILTER "
            "  (WHERE c.discharged_flag AND NOT c.billed_flag) " + _OPEN_INVENTORY,
        )
        (by_dates,) = _one(
            reference_con,
            "SELECT SUM(billed_amount_cents) FROM snap_003.v_claim "
            "WHERE discharge_date IS NOT NULL AND submission_date IS NULL "
            f"  AND service_date <= {_AS_OF} "
            f"  AND (resolved_date IS NULL OR resolved_date > {_AS_OF})",
        )
        assert frame.rows[0] == (by_flags,) == (by_dates,) == (963_165_147,)
        assert frame.evidence_grade is EvidenceGrade.DIRECT

    async def test_timely_filing_at_risk_dollars_values_open_unbilled_inventory(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """Same rename on the other contract: `billed_flag eq false` replaces
        `submission_date IS NULL`, with the `status eq OPEN` half untouched.

        Note the relationship to `dnfb_dollars`: DNFB is the discharged slice of
        this inventory, so the DNFB total must sit strictly inside it."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = SnapshotProbe(
            measures=(MetricRef("timely_filing_at_risk_dollars"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        frame = await pack_reference_repository.execute(probe, watermark=wm)
        (by_flags,) = _one(
            reference_con,
            "SELECT SUM(c.billed_amount_cents) FILTER "
            "  (WHERE NOT c.billed_flag AND c.status = 'OPEN') " + _OPEN_INVENTORY,
        )
        (by_dates,) = _one(
            reference_con,
            "SELECT SUM(billed_amount_cents) FROM snap_003.v_claim "
            "WHERE submission_date IS NULL AND status = 'OPEN' "
            f"  AND service_date <= {_AS_OF} "
            f"  AND (resolved_date IS NULL OR resolved_date > {_AS_OF})",
        )
        assert frame.rows[0] == (by_flags,) == (by_dates,) == (2_242_600_028,)
        assert frame.evidence_grade is EvidenceGrade.DIRECT
        assert by_dates > 963_165_147

    async def test_open_balance_still_reconciles_after_the_rollup_refactor(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """Regression guard for the shared per-claim money rollup: `ar_balance`
        and the A/R band contracts read `open_balance_cents` through the same
        join that now also serves credit balances. Appendix A's published
        numbers must not move."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = SnapshotProbe(
            measures=(MetricRef("ar_over_120_pct"), MetricRef("denied_ar_dollars")),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        frame = await pack_reference_repository.execute(probe, watermark=wm)
        num, den, denied = frame.rows[0]
        assert (num, den) == (1_410_505_150, 3_438_036_345)
        assert denied == 209_606_158

    async def test_derived_measures_refuse_the_wrong_probe_shape(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """`ar_age_days_billed_cents` needs a snapshot's as-of to have an age at
        all. Asked for inside a flow aggregation the compiler refuses rather
        than inventing a reference date."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        contract = pack_reference_repository._compiler._metrics("days_in_ar")
        assert contract is not None and contract.kind is MetricKind.SNAPSHOT
        with pytest.raises(GrainIncompatibleError):  # kind gate fires first
            await pack_reference_repository.execute(_pack_probe("days_in_ar"), watermark=wm)

    async def test_an_advertised_shape_is_the_shape_the_compiler_accepts(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """The two verdicts, taken from opposite ends of one declaration.

        ``credit_balance_cents`` is advertised for snapshots only. Named by
        a contract mis-authored at the flow kind — so the metric-kind gate
        cannot answer first — the compiler refuses on the *shape*, which is
        precisely the refusal §6.6 now issues at plan time from the same
        advertisement.
        """
        wm = (await pack_reference_repository.list_watermarks())[-1]
        compiler = pack_reference_repository._compiler
        credit = next(
            m
            for m in pack_reference_repository.capabilities().derived_measures
            if m.field == "credit_balance_cents"
        )
        assert credit.shapes == frozenset({ProbeShape.SNAPSHOT})
        authored = compiler._metrics("credit_balance_dollars")
        assert authored is not None
        mis_authored = replace(authored, kind=MetricKind.FLOW)
        rebuilt = ProbeCompiler(
            compiler._catalog, {"credit_balance_dollars": mis_authored}.get
        )
        with pytest.raises(SourceCapabilityUnsupportedError) as raised:
            rebuilt.compile_aggregation(
                _pack_probe(
                    "credit_balance_dollars",
                    window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW),
                ),
                schema="snap_003",
                watermark=wm,
            )
        assert "snapshot" in str(raised.value)


# ---------------------------------------------------------------------------
# 7. the claim -> plan -> filing rule join, and claim-grain procedure attribution
#
# Two capability gaps the base pack's NOTES.md carried as named milestones.
# Both land as catalog surface the compiler reads (a declared column and two
# certified dimensions), so both are checked the same way as everything above:
# execute the probe, and derive the same number independently in SQL.


@pytest.mark.reference
class TestFilingRunwayAndClaimProcedureAttribution:
    async def test_days_to_filing_deadline_is_the_plans_own_limit_from_service(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The derived measure: deadline = service date + the plan's configured
        filing limit; runway = deadline - as-of. Restricted to unsubmitted
        claims, because a submitted claim's initial filing clock is closed.

        Summed over the open-inventory population it is a claim-day total, so
        the cross-check computes it twice — once through the probe, once from
        dim_plan's own column — and also pins the population count, which is
        what makes the sum interpretable.
        """
        wm = (await pack_reference_repository.list_watermarks())[-1]
        contract = MetricContract(
            id="filing_runway_days",
            version=1,
            kind=MetricKind.SNAPSHOT,
            entity_grain=EntityGrain.CLAIM,
            numerator=Sum(FieldRef("days_to_filing_deadline")),
            denominator=None,
            primary_date_basis=SERVICE,
            allowed_date_bases=(SERVICE,),
            scope_dimensions=(),
            sign=SignConvention.NEUTRAL,
            unit=MetricUnit.COUNT,
            description="Summed filing runway over unsubmitted open inventory.",
        )
        repo = DuckDbAnalyticalRepository(
            REFERENCE_DB,
            pack_reference_repository._compiler._catalog,
            {"filing_runway_days": contract}.get,
        )
        probe = SnapshotProbe(
            measures=(MetricRef("filing_runway_days"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        frame = await repo.execute(probe, watermark=wm)
        (runway_total,) = frame.rows[0]
        by_hand, unsubmitted = _one(
            reference_con,
            "SELECT SUM(datediff('day', "
            f"          {_AS_OF}, c.service_date + c.timely_filing_days)) "
            "         FILTER (WHERE c.submission_date IS NULL), "
            "       count(*) FILTER (WHERE c.submission_date IS NULL) " + _OPEN_INVENTORY,
        )
        assert runway_total == by_hand == 31_980
        assert unsubmitted == 7_977
        assert frame.evidence_grade is EvidenceGrade.DIRECT

    async def test_filing_runway_bucket_decomposes_the_timely_filing_metric(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The dimension that retires the proxy reading.

        `timely_filing_at_risk_dollars` still values the whole unbilled open
        population — narrowing it would hide the expired dollars, which are the
        worst ones — but the population is now decomposable by deadline
        proximity. The buckets must sum back to the published total exactly,
        and `filed` must be empty inside this metric (its population is
        unbilled by construction).
        """
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = SnapshotProbe(
            measures=(MetricRef("timely_filing_at_risk_dollars"),),
            dimensions=(DimensionRef("filing_runway_bucket"),),
            scope=EMPTY_SCOPE,
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        frame = await pack_reference_repository.execute(probe, watermark=wm)
        assert frame.schema.names == (
            "filing_runway_bucket",
            "timely_filing_at_risk_dollars",
        )
        by_bucket = {row[0]: row[1] for row in frame.rows}
        # The dimension partitions the whole open inventory, so `filed` is a
        # real cell of the cut; the metric's population excludes it by
        # construction, so its numerator is empty rather than zero.
        assert by_bucket.pop("filed") is None
        assert by_bucket == {
            "0-30": 103_821_804,
            "31-60": 112_830_845,
            "61-90": 242_754_866,
            "90+": 730_136_225,
            "expired": 1_053_056_288,
        }
        assert sum(by_bucket.values()) == 2_242_600_028  # Appendix A, to the cent
        hand = dict(
            reference_con.execute(
                "SELECT CASE WHEN c.billed_flag THEN 'filed' "
                "            WHEN r < 0 THEN 'expired' WHEN r <= 30 THEN '0-30' "
                "            WHEN r <= 60 THEN '31-60' WHEN r <= 90 THEN '61-90' "
                "            ELSE '90+' END, SUM(c.billed_amount_cents) "
                "FROM (SELECT c.*, datediff('day', "
                f"            {_AS_OF}, c.service_date + c.timely_filing_days) AS r "
                + _OPEN_INVENTORY
                + ") c WHERE NOT c.billed_flag AND c.status = 'OPEN' GROUP BY 1"
            ).fetchall()
        )
        hand.pop("filed", None)
        assert hand == by_bucket
        assert frame.evidence_grade is EvidenceGrade.DIRECT

    async def test_filing_runway_bucket_is_also_a_legal_scope_filter(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """Scope, not only breakdown — the capability an analyst asking "how
        much is inside 30 days" actually needs. It is compiled before the
        projection is assembled, which is the reason the derived column exists
        by the time the WHERE clause references it."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        probe = SnapshotProbe(
            measures=(MetricRef("timely_filing_at_risk_dollars"),),
            dimensions=(),
            scope=And(
                (
                    Predicate(
                        dimension=DimensionRef("filing_runway_bucket"),
                        op=PredicateOp.IN,
                        values=("expired", "0-30"),
                    ),
                )
            ),
            as_of=wm.newest_data_date,
            grain=Grain(EntityGrain.CLAIM),
        )
        frame = await pack_reference_repository.execute(probe, watermark=wm)
        assert frame.rows[0] == (1_053_056_288 + 103_821_804,)

    async def test_claim_grain_metrics_cut_by_primary_proc_group_reconcile(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The four blocked portfolio cards' missing axis.

        `gross_collection_rate` (a cross-entity ratio) and
        `underpayment_variance` (a claim-grain derived sum) both cut by
        `primary_proc_group`, and both reconcile to their ungrouped totals —
        which is the property that makes a dominance rule safe to certify: each
        claim lands in exactly one bucket, so nothing is double counted and
        nothing is dropped.
        """
        wm = (await pack_reference_repository.list_watermarks())[-1]
        ungrouped = await pack_reference_repository.execute(
            _pack_probe(
                "gross_collection_rate",
                "underpayment_variance",
                window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW),
            ),
            watermark=wm,
        )
        cut = await pack_reference_repository.execute(
            _pack_probe(
                "gross_collection_rate",
                "underpayment_variance",
                window=TimeWindow(basis=SERVICE, range=_PACK_WINDOW),
                dimensions=(DimensionRef("primary_proc_group"),),
            ),
            watermark=wm,
        )
        assert cut.schema.names == (
            "primary_proc_group",
            "gross_collection_rate__num",
            "gross_collection_rate__den",
            "underpayment_variance",
        )
        groups = {row[0] for row in cut.rows}
        assert len(groups & {None}) == 1  # the line-less claims, zero-billed
        assert len(groups - {None}) == 13  # every procedure group in the catalog
        for column, total in enumerate(ungrouped.rows[0], start=1):
            assert sum(row[column] or 0 for row in cut.rows) == total, column
        assert ungrouped.rows[0] == (1_494_532_901, 5_642_309_382, 14_306_720)
        assert cut.evidence_grade is EvidenceGrade.DIRECT
        # ...and the attribution is the dominant group, not a line-grain split.
        (billed_by_line_ortho,) = _one(
            reference_con,
            "SELECT SUM(billed_amount_cents) FROM snap_003.v_claim_line "
            f"WHERE proc_group = 'ORTHO-SURG' AND service_date BETWEEN {_W}",
        )
        (billed_by_claim_ortho,) = _one(
            reference_con,
            "SELECT SUM(billed_amount_cents) FROM snap_003.v_claim "
            f"WHERE primary_proc_group = 'ORTHO-SURG' AND service_date BETWEEN {_W}",
        )
        assert billed_by_claim_ortho > billed_by_line_ortho

    async def test_discharged_flag_is_projected_at_the_probes_as_of(
        self, pack_reference_repository: DuckDbAnalyticalRepository, reference_con: Any
    ) -> None:
        """The documented DNFB as-of limitation, closed.

        The stored flag restates the CURRENT discharge date, so a back-dated
        snapshot used to count claims discharged after the as-of. The snapshot
        builder now re-projects it, the same way `resolved_date` is already
        honoured. At the watermark nothing moves (no claim carries a discharge
        date beyond a snapshot's cutoff), which is why every published number
        is unchanged.
        """
        wm = (await pack_reference_repository.list_watermarks())[-1]

        async def dnfb(as_of: date) -> int:
            frame = await pack_reference_repository.execute(
                SnapshotProbe(
                    measures=(MetricRef("dnfb_dollars"),),
                    dimensions=(),
                    scope=EMPTY_SCOPE,
                    as_of=as_of,
                    grain=Grain(EntityGrain.CLAIM),
                ),
                watermark=wm,
            )
            value = frame.rows[0][0]
            assert isinstance(value, int)
            return value

        assert await dnfb(wm.newest_data_date) == 963_165_147  # Appendix A, unmoved
        back_dated = date(2026, 6, 1)
        stored_flag, as_of_dates = _one(
            reference_con,
            "SELECT SUM(billed_amount_cents) FILTER (WHERE discharged_flag), "
            "       SUM(billed_amount_cents) FILTER (WHERE discharge_date <= DATE '2026-06-01') "
            "FROM snap_003.v_claim "
            "WHERE submission_date IS NULL AND service_date <= DATE '2026-06-01' "
            "  AND (resolved_date IS NULL OR resolved_date > DATE '2026-06-01')",
        )
        assert (stored_flag, as_of_dates) == (654_723_734, 651_695_123)
        assert await dnfb(back_dated) == as_of_dates == 651_695_123
