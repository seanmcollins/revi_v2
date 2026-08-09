"""Days-unit integrity end to end (round-4 R4-06).

"Days in A/R published as 15,941.2%." The pack declares ``unit: days``;
``ratio()`` hardcoded ``unit="ratio"`` on its output column, so the
declaration never left the pack and the single most-quoted KPI in revenue
cycle was rendered by the percentage path. All four days-unit metrics in
the base pack are numerator/denominator shaped, so all four were affected.

These tests walk the real base pack rather than a fixture contract: the
regression surface is exactly "which metrics declare a unit the shape of
their arithmetic does not imply", and only the pack knows that.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_calculation_contracts.contract import MetricUnit
from revi_investigation.application.rendering import format_value
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import CalculationTransforms, PackSnapshotPort

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _components(metric_id: str, num: int, den: int) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("payer", DimensionRef("payer")),
                FrameColumn(f"{metric_id}__num", MetricRef(metric_id), 1, "money_cents"),
                FrameColumn(f"{metric_id}__den", MetricRef(metric_id), 1, "money_cents"),
            )
        ),
        rows=(("Atlas Commercial", num, den),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _ratio_metric_ids(pack_port: PackSnapshotPort) -> list[str]:
    return sorted(
        metric.id for metric in pack_port.snapshot.metric_contracts if metric.denominator is not None
    )


class TestDeclaredUnitReachesTheFrame:
    def test_days_in_ar_publishes_days_not_a_percentage(
        self, pack_port: PackSnapshotPort
    ) -> None:
        transforms = CalculationTransforms(pack_port.snapshot.metric)
        out = transforms.ratio(
            _components("days_in_ar", 1_594_118_470, 10_000_000),
            numerator="days_in_ar__num",
            denominator="days_in_ar__den",
            out="days_in_ar",
            out_ref=MetricRef("days_in_ar"),
            contract_version=1,
        )
        column = out.schema.columns[out.schema.index_of("days_in_ar")]
        assert column.unit == "days"
        # The published sentence, not just the column: "15,941.2%" was the
        # live title on the exec's #1 pilot gate.
        rendered = format_value(out.column("days_in_ar")[0], column.unit)
        assert rendered == "159.4 days"
        assert "%" not in rendered

    def test_every_ratio_shaped_contract_keeps_its_declaration(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The whole regression surface, from the pack itself."""
        transforms = CalculationTransforms(pack_port.snapshot.metric)
        checked = 0
        for metric_id in _ratio_metric_ids(pack_port):
            contract = pack_port.metric(metric_id)
            assert contract is not None
            out = transforms.ratio(
                _components(metric_id, 3, 4),
                numerator=f"{metric_id}__num",
                denominator=f"{metric_id}__den",
                out=metric_id,
                out_ref=MetricRef(metric_id),
                contract_version=contract.version,
            )
            column = out.schema.columns[out.schema.index_of(metric_id)]
            assert column.unit == contract.unit.value, metric_id
            checked += 1
        assert checked > 0

    def test_the_days_metrics_are_all_ratio_shaped(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """Why this defect hit every days metric at once: each one is a
        quotient, and the quotient was the only thing the frame carried."""
        days_metrics = [
            metric.id
            for metric in pack_port.snapshot.metric_contracts
            if metric.unit is MetricUnit.DAYS
        ]
        assert days_metrics
        for metric_id in days_metrics:
            contract = pack_port.metric(metric_id)
            assert contract is not None and contract.denominator is not None, metric_id

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(Decimal("159.411847"), "159.4 days"), (Decimal("0.0"), "0.0 days")],
    )
    def test_days_rendering(self, value: Decimal, expected: str) -> None:
        assert format_value(value, "days") == expected


class TestNoPackNoClaim:
    def test_unresolvable_contract_falls_back_to_ratio(self) -> None:
        """An adapter with no pack asserts nothing about units — it does
        exactly what it always did rather than guessing."""
        transforms = CalculationTransforms()
        out = transforms.ratio(
            _components("denial_rate", 1, 2),
            numerator="denial_rate__num",
            denominator="denial_rate__den",
            out="denial_rate",
            out_ref=MetricRef("denial_rate"),
        )
        assert out.schema.columns[out.schema.index_of("denial_rate")].unit == "ratio"
