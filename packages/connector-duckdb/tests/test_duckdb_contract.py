"""DuckDB adapter tests.

Three layers:

1. ``TestDuckDbAnalyticalContract`` — the reusable, capability-gated
   analytical contract suite (``revi_testing.analytical_contract``) against a
   small generated warehouse (session fixture, fast).
2. ``TestDuckDbAdapterBehavior`` — adapter-specific error mapping and
   compilation rules not covered by the generic suite.
3. ``@pytest.mark.golden`` — answer-key regressions against the full-scale
   ``data/revi_warehouse.duckdb`` through the full probe path.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from revi_catalog import load_catalog
from revi_catalog_contracts import CatalogSnapshot
from revi_connector_duckdb import DuckDbAnalyticalRepository
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
from revi_kernel.probes import AggregationProbe, Ordering, SnapshotProbe
from revi_kernel.refs import (
    POST,
    REMIT,
    SERVICE,
    DimensionRef,
    EntityGrain,
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
GOLDEN_DB = ROOT / "data" / "revi_warehouse.duckdb"
GOLDEN_KEY = ROOT / "data" / "answer_key.json"

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
            scope=Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",)),
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
            scope=Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",)),
            window=TimeWindow(basis=SERVICE, range=_H1_2026),
        )
        materialization = await repository.materialize_cohort(definition, watermark=wm)
        before_expiry = materialization.created_at + timedelta(seconds=1)
        assert await repository.drop_expired_cohorts(before_expiry) == ()
        after_expiry = materialization.created_at + timedelta(seconds=materialization.ttl_seconds + 1)
        assert await repository.drop_expired_cohorts(after_expiry) == (materialization.cohort_id,)
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
# 3. golden answer-key regressions (full-scale warehouse in data/)


@pytest.fixture(scope="session")
def golden_repository(catalog: CatalogSnapshot) -> DuckDbAnalyticalRepository:
    if not GOLDEN_DB.exists():
        pytest.skip("data/revi_warehouse.duckdb not generated")
    return DuckDbAnalyticalRepository(GOLDEN_DB, catalog, fixture_metrics)


@pytest.fixture(scope="session")
def answer_key() -> dict[str, object]:
    if not GOLDEN_KEY.exists():
        pytest.skip("data/answer_key.json not generated")
    return json.loads(GOLDEN_KEY.read_text())  # type: ignore[no-any-return]


def _scenario3(answer_key: dict[str, object]) -> dict[str, object]:
    scenarios = answer_key["scenarios"]
    assert isinstance(scenarios, dict)
    return scenarios["3_cash_decline"]["snap_003"]  # type: ignore[index,no-any-return]


@pytest.mark.golden
class TestGoldenAnswerKey:
    async def test_watermarks_match_design(self, golden_repository: DuckDbAnalyticalRepository) -> None:
        watermarks = await golden_repository.list_watermarks()
        assert [w.id for w in watermarks] == ["wm_001", "wm_002", "wm_003"]
        assert [str(w.newest_data_date) for w in watermarks] == ["2026-07-31", "2026-08-01", "2026-08-02"]

    async def test_golden_week_cash_by_payer_matches_scenario3(
        self, golden_repository: DuckDbAnalyticalRepository, answer_key: dict[str, object]
    ) -> None:
        s3 = _scenario3(answer_key)
        wm = (await golden_repository.list_watermarks())[-1]
        probe = AggregationProbe(
            measures=(MetricRef("cash_posted"),),
            dimensions=(DimensionRef("payer"),),
            scope=EMPTY_SCOPE,
            window=TimeWindow(basis=POST, range=AbsoluteRange(date(2026, 7, 20), date(2026, 8, 2))),
            grain=Grain(EntityGrain.TRANSACTION, TimeBucket.WEEK),
        )
        frame = await golden_repository.execute(probe, watermark=wm)
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

    async def test_golden_denial_rate_monthly_shows_carc197_break(
        self, golden_repository: DuckDbAnalyticalRepository, answer_key: dict[str, object]
    ) -> None:
        scenarios = answer_key["scenarios"]
        assert isinstance(scenarios, dict)
        monthly = scenarios["1_denial_spike_meridian_imaging"]["snap_003"]["monthly_by_first_remit"]
        wm = (await golden_repository.list_watermarks())[-1]
        probe = AggregationProbe(
            measures=(MetricRef("denial_rate"),),
            dimensions=(),
            scope=And(
                (
                    Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",)),
                    Predicate(DimensionRef("service_line"), PredicateOp.EQ, ("Imaging",)),
                )
            ),
            window=TimeWindow(basis=REMIT, range=AbsoluteRange(date(2025, 1, 1), date(2026, 8, 2))),
            grain=Grain(EntityGrain.DENIAL, TimeBucket.MONTH),
        )
        frame = await golden_repository.execute(probe, watermark=wm)
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

    async def test_golden_weekly_cash_by_payer_type_last_13_weeks(
        self, golden_repository: DuckDbAnalyticalRepository, answer_key: dict[str, object]
    ) -> None:
        """Regression anchor for the '3.25 months of payer payments by payer
        type, weekly' guide question: trailing ~13 weeks resolve via the
        kernel and come back as 13 Monday-aligned weekly buckets."""
        s3 = _scenario3(answer_key)
        wm = (await golden_repository.list_watermarks())[-1]
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
        frame = await golden_repository.execute(probe, watermark=wm)
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
