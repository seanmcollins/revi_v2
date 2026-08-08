"""Calculation glue: ratio evaluation, the operator registry, typed args."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_investigation.application.calculation_glue import CalculateMetricsService
from revi_investigation.application.execution import ExecutedProbe
from revi_investigation.application.planning import (
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
    TransformPlanStep,
)
from revi_kernel.errors import UnsupportedConceptError
from revi_kernel.filters import EMPTY_SCOPE
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import REMIT, DimensionRef, EntityGrain, Grain, MetricRef
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import CalculationTransforms, PackSnapshotPort

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
WINDOW = TimeWindow(basis=REMIT, range=AbsoluteRange(date(2026, 7, 27), date(2026, 8, 2)))


def _ratio_frame(num: int, den: int, payer: str = "Payer A") -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("payer", DimensionRef("payer")),
                FrameColumn("denial_rate__num", MetricRef("denial_rate"), 1, "count"),
                FrameColumn("denial_rate__den", MetricRef("denial_rate"), 1, "count"),
            )
        ),
        rows=((payer, num, den),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _denial_rate_plan(steps: tuple[TransformPlanStep, ...]) -> InvestigationPlan:
    probe = AggregationProbe(
        measures=(MetricRef("denial_rate"),),
        dimensions=(DimensionRef("payer"),),
        scope=EMPTY_SCOPE,
        window=WINDOW,
        grain=Grain(EntityGrain.CLAIM),
    )
    prior = AggregationProbe(
        measures=(MetricRef("denial_rate"),),
        dimensions=(DimensionRef("payer"),),
        scope=EMPTY_SCOPE,
        window=TimeWindow(basis=REMIT, range=AbsoluteRange(date(2026, 7, 20), date(2026, 7, 26))),
        grain=Grain(EntityGrain.CLAIM),
    )
    return InvestigationPlan(
        nodes=(
            ProbeNode(id="main", probe=probe, purpose="test"),
            ProbeNode(id="main__prior", probe=prior, purpose="baseline"),
        ),
        transforms=TransformPlan(steps=steps),
    )


@pytest.fixture
def service(pack_port: PackSnapshotPort) -> CalculateMetricsService:
    return CalculateMetricsService(CalculationTransforms(), pack_port)


class TestCalculationGlue:
    def test_ratio_components_fold_into_metric_then_compare(
        self, service: CalculateMetricsService
    ) -> None:
        plan = _denial_rate_plan(
            (
                TransformPlanStep(
                    id="main__compare",
                    operator="compare",
                    inputs=("main", "main__prior"),
                    args=(("kind", "prior_period"),),
                ),
            )
        )
        executed = (
            ExecutedProbe(node_id="main", frame=_ratio_frame(12, 100), cache_hit=False),
            ExecutedProbe(node_id="main__prior", frame=_ratio_frame(8, 100), cache_hit=False),
        )
        result = service.calculate(plan, executed)
        main = result.frame("main")
        assert main.column("denial_rate") == (Decimal("0.12"),)
        compared = result.frame("main__compare")
        idx = compared.schema.index_of("denial_rate__delta")
        assert compared.rows[0][idx] == Decimal("0.04")
        ops = [(op.operator, op.output) for op in result.operations]
        assert ("ratio", "main") in ops and ("ratio", "main__prior") in ops
        assert ("compare", "main__compare") in ops
        versions = {op.operator: op.version for op in result.operations}
        assert versions["ratio"] == "1.0.0" and versions["compare"] == "1.0.0"

    def test_rank_step_with_typed_args(self, service: CalculateMetricsService) -> None:
        plan = _denial_rate_plan(
            (
                TransformPlanStep(
                    id="main__rank",
                    operator="rank",
                    inputs=("main",),
                    args=(("by", "denial_rate"), ("descending", "false")),
                ),
            )
        )
        executed = (
            ExecutedProbe(node_id="main", frame=_ratio_frame(12, 100), cache_hit=False),
            ExecutedProbe(node_id="main__prior", frame=_ratio_frame(8, 100), cache_hit=False),
        )
        result = service.calculate(plan, executed)
        ranked = result.frame("main__rank")
        assert "denial_rate__rank" in ranked.schema.names

    def test_unknown_operator_is_typed_error(self, service: CalculateMetricsService) -> None:
        plan = _denial_rate_plan(
            (TransformPlanStep(id="bad", operator="teleport", inputs=("main",)),)
        )
        executed = (
            ExecutedProbe(node_id="main", frame=_ratio_frame(1, 2), cache_hit=False),
            ExecutedProbe(node_id="main__prior", frame=_ratio_frame(1, 2), cache_hit=False),
        )
        with pytest.raises(UnsupportedConceptError):
            service.calculate(plan, executed)

    def test_missing_required_arg_is_typed_error(self, service: CalculateMetricsService) -> None:
        plan = _denial_rate_plan(
            (TransformPlanStep(id="main__rank", operator="rank", inputs=("main",)),)
        )
        executed = (
            ExecutedProbe(node_id="main", frame=_ratio_frame(1, 2), cache_hit=False),
            ExecutedProbe(node_id="main__prior", frame=_ratio_frame(1, 2), cache_hit=False),
        )
        with pytest.raises(UnsupportedConceptError):
            service.calculate(plan, executed)
