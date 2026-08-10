"""Reusable analytical-repository contract suite (design §16, §18.1).

Every ``AnalyticalRepository`` backend must pass the same behavioral suite
for the capabilities it declares — this is the swap-the-backend safety net
(DuckDB today, Snowflake later). Tests are capability-gated: a backend that
declares ``cohort_semijoin=False`` skips the cohort tests instead of failing.

Usage::

    class TestMyBackendContract(AnalyticalRepositoryContract):
        @pytest.fixture
        def repository(self, ...) -> AnalyticalRepository:
            return MyBackend(...)

The suite assumes the backend serves the revi warehouse semantic catalog
(``warehouse/catalog``: payer/service_line/carc/… dimensions, five entities)
and resolves the fixture metric contracts in :mod:`revi_testing.fixtures`
(cash_posted, claim_count, denial_count, denial_rate, ar_balance,
open_claim_count). Dates reference the generated warehouse's fixed timeline
(newest data 2026-08-02) which is scale-independent.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.cohort import CohortDefinition, CohortMaterialization, CohortRef
from revi_kernel.errors import DataLoadingError, WatermarkStaleError
from revi_kernel.filters import EMPTY_SCOPE, InCohort, Predicate, PredicateOp
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import (
    AggregationProbe,
    MeasurePredicate,
    Ordering,
    RowEvidenceProbe,
    SamplePolicy,
    SnapshotProbe,
)
from revi_kernel.refs import (
    POST,
    REMIT,
    SERVICE,
    DimensionRef,
    EntityGrain,
    FieldRef,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
)
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark

_MASKED_TOKEN_RE = re.compile(r"^MASKED-[0-9a-f]{10}$")

# The generated warehouse's fixed timeline (scale-independent).
_ALL_TIME = AbsoluteRange(date(2025, 1, 1), date(2026, 12, 31))
_H1_2026 = AbsoluteRange(date(2026, 1, 1), date(2026, 6, 30))
_POST_WINDOW = AbsoluteRange(date(2026, 2, 1), date(2026, 8, 2))

_COHORT_SCOPE = Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Halvern Health",))
_COHORT_DEFINITION = CohortDefinition(
    entity=EntityGrain.CLAIM,
    scope=_COHORT_SCOPE,
    window=TimeWindow(basis=SERVICE, range=_H1_2026),
)


def _claim_count_probe(dimensions: tuple[DimensionRef, ...] = ()) -> AggregationProbe:
    return AggregationProbe(
        measures=(MetricRef("claim_count"),),
        dimensions=dimensions,
        scope=EMPTY_SCOPE,
        window=TimeWindow(basis=SERVICE, range=_ALL_TIME),
        grain=Grain(EntityGrain.CLAIM),
    )


def _cash_by_payer_probe(
    *,
    having: tuple[MeasurePredicate, ...] = (),
    order_by: tuple[Ordering, ...] = (),
    limit: int | None = None,
) -> AggregationProbe:
    return AggregationProbe(
        measures=(MetricRef("cash_posted"),),
        dimensions=(DimensionRef("payer"),),
        scope=EMPTY_SCOPE,
        window=TimeWindow(basis=POST, range=_POST_WINDOW),
        grain=Grain(EntityGrain.TRANSACTION),
        having=having,
        order_by=order_by,
        limit=limit,
    )


class AnalyticalRepositoryContract:
    """Behavioral contract for ``AnalyticalRepository`` implementations.

    Subclass (with a ``Test``-prefixed name) and provide ``repository``.
    """

    @pytest.fixture
    def repository(self) -> AnalyticalRepository:
        raise NotImplementedError("contract subclasses must provide a repository fixture")

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _require(repository: AnalyticalRepository, capability: str) -> None:
        if not getattr(repository.capabilities(), capability):
            pytest.skip(f"backend does not declare {capability}")

    @staticmethod
    async def _watermarks(repository: AnalyticalRepository) -> tuple[DataWatermark, ...]:
        watermarks = await repository.list_watermarks()
        assert watermarks, "backend must expose at least one completed load"
        return watermarks

    @staticmethod
    def _rows_and_names(frame: EvidenceFrame) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
        return frame.schema.names, frame.rows

    @staticmethod
    def _cohort_ref(materialization: CohortMaterialization, definition: CohortDefinition) -> CohortRef:
        return CohortRef(
            id=materialization.cohort_id,
            definition=definition,
            origin=ReferentId("F1", ReferentKind.COHORT),
            size=materialization.size,
            pinned=materialization,
        )

    # ----------------------------------------------------------- 1. as-of

    async def test_watermark_ordering_and_as_of_reads(self, repository: AnalyticalRepository) -> None:
        self._require(repository, "as_of_reads")
        watermarks = await self._watermarks(repository)
        loaded = [w.loaded_at for w in watermarks]
        assert loaded == sorted(loaded), "list_watermarks must be oldest-first"
        assert len({w.id for w in watermarks}) == len(watermarks)

        probe = _claim_count_probe()
        counts: list[int] = []
        for watermark in watermarks:
            frame = await repository.execute(probe, watermark=watermark)
            assert frame.schema.names == ("claim_count",)
            value = frame.rows[0][0]
            assert isinstance(value, int)
            counts.append(value)
        # Consecutive nightly loads only ever add activity (answer-key
        # row_counts direction): counts are monotonically non-decreasing and
        # the newest load sees strictly more than the oldest.
        assert counts == sorted(counts)
        if len(counts) > 1:
            assert counts[0] < counts[-1]

    # --------------------------------------------------------- 2. stamping

    async def test_frames_stamp_watermark_and_reject_unknown(
        self, repository: AnalyticalRepository
    ) -> None:
        watermarks = await self._watermarks(repository)
        for watermark in watermarks:
            frame = await repository.execute(_claim_count_probe(), watermark=watermark)
            assert frame.watermark == watermark
            assert frame.suppressed_cells == 0  # suppression is not the adapter's job
        bogus = DataWatermark(
            id="wm_does_not_exist",
            loaded_at=watermarks[-1].loaded_at,
            newest_data_date=watermarks[-1].newest_data_date,
        )
        with pytest.raises(WatermarkStaleError):
            await repository.execute(_claim_count_probe(), watermark=bogus)

    # ----------------------------------------------- 3. cohort ≡ predicate

    async def test_cohort_semijoin_equals_inline_predicate(
        self, repository: AnalyticalRepository
    ) -> None:
        self._require(repository, "cohort_semijoin")
        watermark = (await self._watermarks(repository))[-1]
        materialization = await repository.materialize_cohort(_COHORT_DEFINITION, watermark=watermark)
        assert materialization.size > 0
        assert materialization.watermark == watermark
        cohort = self._cohort_ref(materialization, _COHORT_DEFINITION)

        def probe(scope: Predicate | InCohort) -> AggregationProbe:
            return AggregationProbe(
                measures=(MetricRef("claim_count"),),
                dimensions=(DimensionRef("service_line"),),
                scope=scope,
                window=TimeWindow(basis=SERVICE, range=_H1_2026),
                grain=Grain(EntityGrain.CLAIM),
            )

        via_cohort = await repository.execute(probe(InCohort(cohort)), watermark=watermark)
        via_predicate = await repository.execute(probe(_COHORT_SCOPE), watermark=watermark)
        assert via_cohort.schema.names == via_predicate.schema.names
        assert via_cohort.rows == via_predicate.rows
        assert via_cohort.evidence_grade == via_predicate.evidence_grade
        assert via_cohort.truncated == via_predicate.truncated
        total = sum(row[-1] for row in via_cohort.rows if isinstance(row[-1], int))
        assert total == materialization.size

    # -------------------------------------------------------- 4. top-N

    async def test_server_side_top_n(self, repository: AnalyticalRepository) -> None:
        self._require(repository, "server_side_top_n")
        watermark = (await self._watermarks(repository))[-1]
        order = (Ordering(MetricRef("cash_posted"), descending=True),)
        unlimited = await repository.execute(
            _cash_by_payer_probe(order_by=order), watermark=watermark
        )
        assert unlimited.row_count > 3, "top-N test needs more than 3 groups"
        assert not unlimited.truncated

        limited = await repository.execute(
            _cash_by_payer_probe(order_by=order, limit=3), watermark=watermark
        )
        assert limited.row_count == 3
        assert limited.truncated
        assert limited.rows == unlimited.rows[:3]

        roomy = await repository.execute(
            _cash_by_payer_probe(order_by=order, limit=unlimited.row_count + 50), watermark=watermark
        )
        assert not roomy.truncated
        assert roomy.rows == unlimited.rows

    # -------------------------------------------------------- 5. having

    async def test_having_pushdown_matches_client_side_filter(
        self, repository: AnalyticalRepository
    ) -> None:
        self._require(repository, "having_pushdown")
        watermark = (await self._watermarks(repository))[-1]
        unfiltered = await repository.execute(_cash_by_payer_probe(), watermark=watermark)
        values = sorted(v for v in unfiltered.column("cash_posted") if isinstance(v, int))
        assert values, "having test needs at least one group"
        threshold = values[len(values) // 2]  # median keeps both sides non-trivial
        ceiling = max(values) + 1

        having = (MeasurePredicate(MetricRef("cash_posted"), PredicateOp.RANGE, (threshold, ceiling)),)
        pushed = await repository.execute(_cash_by_payer_probe(having=having), watermark=watermark)
        idx = unfiltered.schema.index_of("cash_posted")

        def keep(row: tuple[object, ...]) -> bool:
            value = row[idx]
            return isinstance(value, int) and threshold <= value <= ceiling

        expected = tuple(row for row in unfiltered.rows if keep(row))
        assert pushed.rows == expected

    # -------------------------------------------- 6. row evidence sampling

    async def test_row_evidence_sampling_deterministic_and_masked(
        self, repository: AnalyticalRepository
    ) -> None:
        watermark = (await self._watermarks(repository))[-1]
        probe = RowEvidenceProbe(
            columns=(FieldRef("claim_id"), FieldRef("payer"), FieldRef("provider")),
            scope=EMPTY_SCOPE,
            sample=SamplePolicy(n=25, seed=417),
            purpose="contract-suite: sampling determinism + PHI masking",
            window=TimeWindow(basis=SERVICE, range=_H1_2026),
        )
        first = await repository.execute(probe, watermark=watermark)
        second = await repository.execute(probe, watermark=watermark)
        assert first.schema.names == ("claim_id", "payer", "provider")
        assert 0 < first.row_count <= 25
        assert first.rows == second.rows, "same seed must sample the same rows"
        for value in first.column("provider"):  # provider is PHI-classed (indirect)
            assert isinstance(value, str) and _MASKED_TOKEN_RE.match(value), value
        for value in first.column("payer"):  # payer is not PHI-classed
            assert isinstance(value, str) and not value.startswith("MASKED-")

    # ---------------------------------------------- 7. ratio components

    async def test_ratio_metrics_arrive_as_components(self, repository: AnalyticalRepository) -> None:
        watermark = (await self._watermarks(repository))[-1]
        probe = AggregationProbe(
            measures=(MetricRef("denial_rate"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            window=TimeWindow(basis=REMIT, range=_H1_2026),
            grain=Grain(EntityGrain.DENIAL),
        )
        frame = await repository.execute(probe, watermark=watermark)
        assert frame.schema.names == ("denial_rate__num", "denial_rate__den")
        for column in frame.schema.columns:
            assert column.ref == MetricRef("denial_rate")
            assert column.contract_version == 1
        (num, den) = frame.rows[0]
        assert isinstance(num, int) and isinstance(den, int), "no division in the adapter"
        assert 0 <= num <= den
        assert not any(isinstance(v, float) for row in frame.rows for v in row)

    # ------------------------------------------------------- 8. snapshot

    async def test_snapshot_as_of_and_age_buckets_partition(
        self, repository: AnalyticalRepository
    ) -> None:
        self._require(repository, "as_of_reads")
        watermark = (await self._watermarks(repository))[-1]
        beyond = SnapshotProbe(
            measures=(MetricRef("ar_balance"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            as_of=watermark.newest_data_date + timedelta(days=1),
            grain=Grain(EntityGrain.CLAIM),
        )
        with pytest.raises(DataLoadingError):
            await repository.execute(beyond, watermark=watermark)

        def snapshot(dimensions: tuple[DimensionRef, ...]) -> SnapshotProbe:
            return SnapshotProbe(
                measures=(MetricRef("open_claim_count"), MetricRef("ar_balance")),
                dimensions=dimensions,
                scope=EMPTY_SCOPE,
                as_of=watermark.newest_data_date,
                grain=Grain(EntityGrain.CLAIM),
            )

        total = await repository.execute(snapshot(()), watermark=watermark)
        by_bucket = await repository.execute(
            snapshot((DimensionRef("ar_age_bucket"),)), watermark=watermark
        )
        assert total.schema.names == ("open_claim_count", "ar_balance")
        assert by_bucket.schema.names == ("ar_age_bucket", "open_claim_count", "ar_balance")
        labels = tuple(by_bucket.column("ar_age_bucket"))
        assert set(labels) <= {"0-30", "31-60", "61-90", "91-120", "120+"}
        assert len(set(labels)) == len(labels)
        total_count = total.rows[0][0]
        total_balance = total.rows[0][1]
        assert sum(v for v in by_bucket.column("open_claim_count") if isinstance(v, int)) == total_count
        assert sum(v for v in by_bucket.column("ar_balance") if isinstance(v, int)) == total_balance
        assert isinstance(total_count, int) and total_count > 0

    # -------------------------------------- 9. cross-grain cohort mapping

    async def test_claim_cohort_filters_denial_probe(self, repository: AnalyticalRepository) -> None:
        self._require(repository, "cohort_semijoin")
        watermark = (await self._watermarks(repository))[-1]
        materialization = await repository.materialize_cohort(_COHORT_DEFINITION, watermark=watermark)
        cohort = self._cohort_ref(materialization, _COHORT_DEFINITION)

        def probe(scope: Predicate | InCohort) -> AggregationProbe:
            # The denial view carries its parent claim's claim_id and
            # service_date, so a CLAIM cohort maps through claim_id and the
            # SERVICE window keeps both probes over the same claim population.
            return AggregationProbe(
                measures=(MetricRef("denial_count"), MetricRef("denied_amount")),
                dimensions=(DimensionRef("denial_category"),),
                scope=scope,
                window=TimeWindow(basis=SERVICE, range=_H1_2026),
                grain=Grain(EntityGrain.DENIAL),
            )

        via_cohort = await repository.execute(probe(InCohort(cohort)), watermark=watermark)
        via_predicate = await repository.execute(probe(_COHORT_SCOPE), watermark=watermark)
        assert via_cohort.row_count > 0, "cross-grain semijoin returned no denials"
        assert via_cohort.schema.names == via_predicate.schema.names
        assert via_cohort.rows == via_predicate.rows
