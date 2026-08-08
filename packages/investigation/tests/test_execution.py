"""Execution service: the small-cell suppression rule, evidence-cache
behavior, and stage events."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.execution import (
    ExecuteInvestigationService,
    apply_small_cell_suppression,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
)
from revi_kernel.filters import EMPTY_SCOPE
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import AggregationProbe, probe_hash
from revi_kernel.refs import POST, DimensionRef, EntityGrain, Grain, MetricRef
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_testing.fakes import (
    FakeEvidenceCache,
    FakeTurnEventBus,
    SpyAnalyticalRepository,
    StubAnalyticalRepository,
)

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
THRESHOLD = 11


def _frame(rows: tuple[tuple[object, ...], ...], *, with_count: bool = True) -> EvidenceFrame:
    columns = [FrameColumn("payer", DimensionRef("payer"))]
    if with_count:
        columns.append(FrameColumn("claim_volume", MetricRef("claim_volume"), 1, "count"))
    columns.append(FrameColumn("cash_posted", MetricRef("cash_posted"), 1, "money_cents"))
    return EvidenceFrame(
        schema=FrameSchema(tuple(columns)),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestSuppressionRule:
    def test_small_counts_null_all_measures_and_count_cells(self) -> None:
        frame = _frame(
            (
                ("Payer A", 5, 12345),  # 0 < 5 < 11 → suppressed
                ("Payer B", 50, 99999),  # kept
                ("Payer C", 0, 0),  # zero count: "nothing happened" stays
            )
        )
        out = apply_small_cell_suppression(frame, THRESHOLD)
        assert out.rows[0] == ("Payer A", None, None)
        assert out.rows[1] == ("Payer B", 50, 99999)
        assert out.rows[2] == ("Payer C", 0, 0)
        # two cells changed value → suppressed_cells counts both
        assert out.suppressed_cells == 2
        # dimension column untouched
        assert out.rows[0][0] == "Payer A"

    def test_frame_without_count_measure_untouched(self) -> None:
        frame = _frame((("Payer A", 1),), with_count=False)
        assert apply_small_cell_suppression(frame, THRESHOLD) is frame

    def test_no_small_cells_returns_same_frame(self) -> None:
        frame = _frame((("Payer A", 100, 5),))
        assert apply_small_cell_suppression(frame, THRESHOLD) is frame


class TestCacheAndEvents:
    @pytest.fixture
    def probe(self) -> AggregationProbe:
        return AggregationProbe(
            measures=(MetricRef("cash_posted"),),
            dimensions=(DimensionRef("payer"),),
            scope=EMPTY_SCOPE,
            window=TimeWindow(basis=POST, range=AbsoluteRange(date(2026, 7, 27), date(2026, 8, 2))),
            grain=Grain(EntityGrain.TRANSACTION),
        )

    async def test_cache_miss_then_hit(
        self, catalog: CatalogSnapshot, probe: AggregationProbe
    ) -> None:
        frame = _frame((("Payer A", 100, 500),))
        stub = StubAnalyticalRepository(watermarks=(WATERMARK,))
        stub.frames[probe_hash(probe)] = frame
        spy = SpyAnalyticalRepository(stub)
        cache = FakeEvidenceCache()
        events = FakeTurnEventBus()
        executor = ExecuteInvestigationService(spy, cache, events, catalog)
        plan = InvestigationPlan(
            nodes=(ProbeNode(id="main", probe=probe, purpose="test"),),
            transforms=TransformPlan(),
        )

        first = await executor.execute(
            plan, watermark=WATERMARK, pack_snapshot_id="pack1", turn_id="t1"
        )
        assert [(e.node_id, e.cache_hit) for e in first] == [("main", False)]
        assert spy.execute_count == 1

        second = await executor.execute(
            plan, watermark=WATERMARK, pack_snapshot_id="pack1", turn_id="t2"
        )
        assert [(e.node_id, e.cache_hit) for e in second] == [("main", True)]
        assert spy.execute_count == 1  # served from cache, no new probe
        provenance = second[0].frame.provenance
        assert isinstance(provenance, ProbeProvenance) and provenance.cache_hit

        # a different pack snapshot id is a different cache key
        await executor.execute(plan, watermark=WATERMARK, pack_snapshot_id="pack2", turn_id="t3")
        assert spy.execute_count == 2

        stages = [e.payload for e in events.events if e.kind == "stage"]
        assert all(p["stage"] == "executing" and p["n"] == 1 for p in stages)
        assert [p["cache_hit"] for p in stages] == [False, True, False]

    async def test_execution_applies_suppression_before_caching(
        self, catalog: CatalogSnapshot, probe: AggregationProbe
    ) -> None:
        frame = _frame((("Payer A", 5, 12345),))
        stub = StubAnalyticalRepository(watermarks=(WATERMARK,))
        stub.frames[probe_hash(probe)] = frame
        cache = FakeEvidenceCache()
        executor = ExecuteInvestigationService(
            SpyAnalyticalRepository(stub), cache, FakeTurnEventBus(), catalog
        )
        plan = InvestigationPlan(
            nodes=(ProbeNode(id="main", probe=probe, purpose="test"),),
            transforms=TransformPlan(),
        )
        [executed] = await executor.execute(
            plan, watermark=WATERMARK, pack_snapshot_id="pack1", turn_id="t1"
        )
        assert executed.frame.rows[0] == ("Payer A", None, None)
        assert executed.frame.suppressed_cells == 2
        cached = next(iter(cache.entries.values()))
        assert cached.suppressed_cells == 2  # the cached frame is post-policy
