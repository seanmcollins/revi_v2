"""Declared units survive arithmetic and are said exactly once.

Days in A/R was published as 15,941.2%: the pack declares ``unit: days`` and
``ratio()`` hardcoded ``unit="ratio"`` on its output column, so the
declaration never left the pack and all four days-shaped metrics were
rendered by the percentage path. The second half is the juxtaposition —
"179.5 days days in ar" — where a rendered value carries its unit and the
measure's own display name already ends in it. These tests walk the real base
pack, because only the pack knows which metrics declare a unit the shape of
their arithmetic does not imply.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_calculation_contracts.contract import MetricUnit
from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.findings import EvaluateFindingsService
from revi_investigation.application.planning import (
    InvestigationPlan,
    TransformPlan,
    TransformPlanStep,
)
from revi_investigation.application.rendering import (
    format_value,
    measure_phrase,
    metric_label,
    unit_word,
)
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
from revi_testing.fakes import FakeReferentRegistryStore

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
        # live finding title.
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

# ---------------------------------------------------------------------------
# The unit token is said once
#
# regression: "179.5 days days in ar" reached a monitor tile headline. The
# ungrouped scalar path renders correctly, so the defect is the
# juxtaposition in a GROUPED title, where the figure and the measure name
# sit side by side.


def _token_count(text: str, token: str) -> int:
    return len(re.findall(rf"(?<!\w){re.escape(token)}(?!\w)", text.casefold()))


class TestAUnitIsSaidOnce:
    @pytest.mark.parametrize(
        ("unit", "value", "label", "expected"),
        [
            # The defect, verbatim, in all four display-name shapes the base
            # pack actually contains.
            ("days", Decimal("179.5"), "days in ar", "179.5 days in ar"),
            ("days", Decimal("179.5"), "bill lag days", "179.5 bill lag days"),
            ("days", Decimal("179.5"), "avg days to pay", "179.5 avg days to pay"),
            ("days", Decimal("179.5"), "days", "179.5 days"),
            # A label that never mentions the unit keeps the suffix.
            ("days", Decimal("179.5"), "ar aging", "179.5 days ar aging"),
            # Money, ratio and count attach their unit to the digits, so
            # there is nothing to collide and nothing is touched.
            ("money_cents", 419942121, "denied dollars", "$4,199,421.21 denied dollars"),
            ("ratio", Decimal("0.128"), "denial rate", "12.8% denial rate"),
            ("count", 1204, "appeal volume", "1,204 appeal volume"),
            (None, Decimal("1.5"), "unknown thing", "1.5 unknown thing"),
        ],
    )
    def test_every_unit_kind_by_every_display_name_shape(
        self, unit: str | None, value: object, label: str, expected: str
    ) -> None:
        assert measure_phrase(format_value(value, unit), label, unit) == expected  # type: ignore[arg-type]

    def test_the_unit_word_is_derived_from_the_renderer_not_tabulated(self) -> None:
        """So the two can never drift: whatever ``format_value`` appends
        after a space IS the token a value of that unit carries."""
        assert unit_word("days") == "days"
        assert unit_word("money_cents") is None
        assert unit_word("ratio") is None
        assert unit_word("count") is None
        assert unit_word(None) is None

    def test_no_metric_in_the_base_pack_says_its_unit_twice(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The contract-level assertion, swept over every metric against
        its own declared unit."""
        for contract in pack_port.snapshot.metric_contracts:
            unit = contract.unit.value
            token = unit_word(unit)
            if token is None:
                continue
            label = metric_label(contract.id)
            phrase = measure_phrase(format_value(Decimal("179.5"), unit), label, unit)
            assert _token_count(phrase, token) == 1, f"{contract.id}: {phrase!r}"


class TestTheGroupedTitleIsTheSurfaceThatBroke:
    """The ungrouped scalar path renders correctly ("days in ar: 159.4
    days"), so the defect is specific to grouped titles — where the figure
    and the measure name are juxtaposed."""

    @staticmethod
    def _ranked(measure: str, unit: str, value: Decimal | int) -> EvidenceFrame:
        return EvidenceFrame(
            schema=FrameSchema(
                (
                    FrameColumn("payer", DimensionRef("payer")),
                    FrameColumn(measure, MetricRef(measure), 1, unit),
                    FrameColumn(f"{measure}__rank", MetricRef(measure), 1, "count"),
                )
            ),
            rows=(("Atlas Commercial", value, 1),),
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )

    @staticmethod
    def _plan(measure: str) -> InvestigationPlan:
        return InvestigationPlan(
            nodes=(),
            transforms=TransformPlan(
                steps=(
                    TransformPlanStep(
                        id="r", operator="rank", inputs=("main",), args=(("by", measure),)
                    ),
                )
            ),
        )

    @pytest.mark.parametrize(
        "measure", ["days_in_ar", "bill_lag_days", "charge_lag_days", "avg_days_to_pay"]
    )
    async def test_a_grouped_days_title_says_days_once(
        self, measure: str, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=(measure,), dimensions=("payer",), watermark=WATERMARK)
        service = EvaluateFindingsService(FakeReferentRegistryStore())

        result = await service.evaluate(
            plan=self._plan(measure),
            calculation=CalculationResult(
                frames=(("r", self._ranked(measure, "days", Decimal("179.5"))),), operations=()
            ),
            spec=spec,
            pack=pack_port,
            playbook=None,
            session_id="sess_fn14",
            investigation_id="inv_fn14",
        )

        [finding] = result.findings
        assert "days days" not in finding.title
        assert _token_count(finding.title, "days") == 1, finding.title
        assert "179.5" in finding.title
        # The registered referent's label is the title, and it is what a
        # Monitors tile headline is built from.
        assert result.referents[0].label == finding.title
