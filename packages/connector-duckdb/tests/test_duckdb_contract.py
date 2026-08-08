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
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from revi_calculation_contracts.contract import (
    CountDistinct,
    Filtered,
    MetricContract,
    MetricKind,
    MetricUnit,
    SignConvention,
)
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
                    Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",)),
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
        """Before the polarity correction this raised UNSUPPORTED_CONCEPT:
        its exclusion named `status`, which is not a catalog dimension, so
        every probe touching the metric was pruned as unanswerable."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(_pack_probe("clean_claim_rate"), watermark=wm)
        assert frame.schema.names == ("clean_claim_rate__num", "clean_claim_rate__den")
        numerator, denominator = frame.rows[0]
        assert isinstance(numerator, int) and isinstance(denominator, int)
        assert (numerator, denominator) == (13_725, 18_410)
        assert 0.7 < numerator / denominator < 0.8
        # every dimension it touches is certified, so nothing downgrades it
        assert frame.evidence_grade is EvidenceGrade.DIRECT

    async def test_clean_claim_rate_denominator_is_every_claim_in_the_window(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """The population is unrestricted, exactly as the contract now says:
        the denominator equals claim_volume over the same window."""
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
        assert denominator == claim_volume == 18_410

    async def test_clean_and_denied_partition_the_same_population(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """`clean_claim` is a non-null boolean, so with both contracts now
        reading the same unrestricted population the two numerators sum to
        the shared denominator — the "complementary to denial_rate" claim in
        both descriptions, asserted rather than asserted-in-prose."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        frame = await pack_reference_repository.execute(
            _pack_probe("clean_claim_rate", "denial_rate"), watermark=wm
        )
        clean_num, clean_den, denied_num, denied_den = frame.rows[0]
        assert clean_den == denied_den == 18_410
        assert clean_num + denied_num == clean_den
        assert (clean_num, denied_num) == (13_725, 4_685)

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
        assert sum(row[2] for row in cut.rows) == 18_410  # type: ignore[misc]

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

    async def test_first_pass_yield_remains_unanswerable_on_its_numerator(
        self, pack_reference_repository: DuckDbAnalyticalRepository
    ) -> None:
        """Known residue of the polarity correction: removing the exclusion
        does not make this metric answerable, because its numerator filters
        on `first_pass_paid`, a base-view column the catalog does not
        certify as a dimension. Pinned so it cannot quietly change in either
        direction (packs/base-rcm/NOTES.md)."""
        wm = (await pack_reference_repository.list_watermarks())[-1]
        with pytest.raises(UnsupportedConceptError) as excinfo:
            await pack_reference_repository.execute(_pack_probe("first_pass_yield"), watermark=wm)
        assert excinfo.value.details["dimension"] == "first_pass_paid"

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
        assert service.rows[0] == (7_484, 19_672)
