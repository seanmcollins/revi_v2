"""What the answer is *about*: the direction a question asked for, the
shapes an answer can take, and what "nothing" means.

Four round-1 live findings, all of them a confident answer to a question
nobody asked:

- **F10** — *"which payers had the biggest INCREASE in denials"* was
  answered with the three biggest **decreases**, narrated as improvements.
  Nothing in the pipeline carried the word "increase" past the model, so
  selection ranked by delta ascending, which is the default for "what
  moved" and the exact inverse of what was asked.
- **F5** — a grouped comparison of a **rate** published zero findings and a
  null narrative: the movement shape required a *money* measure, so a
  correct compare frame full of denial-rate movement was invisible.
- **F12b** — a one-day calendar difference between two 90-day windows
  qualified every finding and withheld every impact, in the same words a
  7-day-against-a-quarter comparison gets. A rate is length-invariant and
  should carry no such caveat at all.
- **F2** — an empty answer and a quiet one rendered identically (as
  silence), while their recoveries are opposite.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from revi_calculation_contracts.contract import SignConvention
from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.calculation_glue import (
    CalculationResult,
    EmptinessKind,
)
from revi_investigation.application.findings import (
    EvaluateFindingsService,
    find_primary_movement,
)
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    InvestigationPlan,
    TransformPlan,
    TransformPlanStep,
)
from revi_investigation.application.rendering import magnitude, points
from revi_investigation.domain.context import (
    AskedDirection,
    AskedMagnitude,
    adverse_delta_sign,
    wanted_delta_sign,
)
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import FakeReferentRegistryStore

if TYPE_CHECKING:
    from conftest import SpecFactory

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)

#: (payer, current, prior). Two payers rose, two fell — so a question that
#: names a direction has both a right and a wrong answer available.
MOVEMENTS = (
    ("Meridian Health", 1_400_000, 1_000_000),  # +400k, the biggest rise
    ("Atlas Commercial", 1_100_000, 1_000_000),  # +100k
    ("Bluestone Mutual", 700_000, 1_000_000),  # -300k
    ("State Medicaid", 200_000, 1_000_000),  # -800k, the biggest fall
)


def _compare_frame(
    rows: tuple[tuple[str, int, int], ...] = MOVEMENTS,
    *,
    measure: str = "denied_dollars",
    unit: str = "money_cents",
) -> EvidenceFrame:
    columns = (
        FrameColumn("payer", DimensionRef("payer")),
        FrameColumn(measure, MetricRef(measure), 1, unit),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 1, unit),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 1, unit),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 1, "ratio"),
    )
    built = tuple(
        (
            payer,
            current,
            prior,
            current - prior,
            (Decimal(current - prior) / Decimal(prior)).quantize(Decimal("0.0001")),
        )
        for payer, current, prior in rows
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=built,
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _ratio_compare_frame() -> EvidenceFrame:
    """The shape that used to publish nothing: a compared rate, no money."""
    measure = "denial_rate"
    columns = (
        FrameColumn("payer", DimensionRef("payer")),
        FrameColumn(measure, MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 2, "ratio"),
    )
    rows = (
        ("Meridian Health", Decimal("0.1400"), Decimal("0.0500"), Decimal("0.0900"), Decimal("1.8")),
        ("Atlas Commercial", Decimal("0.0400"), Decimal("0.0600"), Decimal("-0.0200"), Decimal("-0.33")),
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _compare_plan() -> InvestigationPlan:
    return InvestigationPlan(
        nodes=(),
        transforms=TransformPlan(
            steps=(TransformPlanStep(id="c", operator="compare", inputs=("main", "main__prior")),)
        ),
    )


async def _findings(
    frame: EvidenceFrame, spec: object, pack: PackSnapshotPort
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(finding titles, selection warnings) for one compare frame."""
    service = EvaluateFindingsService(FakeReferentRegistryStore())
    result = await service.evaluate(
        plan=_compare_plan(),
        calculation=CalculationResult(frames=(("c", frame),), operations=()),
        spec=spec,  # type: ignore[arg-type]
        pack=pack,
        playbook=None,
        session_id="sess",
        investigation_id="inv",
    )
    return tuple(f.title for f in result.findings), result.warnings


class TestDirectionSemantics:
    @pytest.mark.parametrize(
        ("direction", "sign", "expected"),
        [
            (AskedDirection.INCREASE, SignConvention.HIGHER_IS_GOOD, 1),
            (AskedDirection.DECREASE, SignConvention.HIGHER_IS_BAD, -1),
            (AskedDirection.WORSENED, SignConvention.HIGHER_IS_BAD, 1),
            (AskedDirection.WORSENED, SignConvention.HIGHER_IS_GOOD, -1),
            (AskedDirection.IMPROVED, SignConvention.HIGHER_IS_BAD, -1),
            (AskedDirection.IMPROVED, SignConvention.HIGHER_IS_GOOD, 1),
            (None, SignConvention.HIGHER_IS_BAD, None),
        ],
    )
    def test_worse_and_better_are_read_off_the_contract_not_guessed(
        self, direction: AskedDirection | None, sign: SignConvention, expected: int | None
    ) -> None:
        assert wanted_delta_sign(direction, sign) == expected

    def test_a_neutral_metric_has_no_worse_direction_to_lead_with(self) -> None:
        assert adverse_delta_sign(SignConvention.NEUTRAL) is None
        assert wanted_delta_sign(AskedDirection.WORSENED, SignConvention.NEUTRAL) is None


class TestDirectionAwareSelection:
    async def test_biggest_increase_returns_increases(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The live repro. Ranked by delta ascending, the top three rows are
        the two falls and the smaller rise — three answers, none of them to
        the question that was asked."""
        spec = make_spec(measures=("denied_dollars",), dimensions=("payer",), watermark=WATERMARK)
        spec = replace(
            spec, direction=AskedDirection.INCREASE, magnitude=AskedMagnitude.LARGEST
        )

        titles, warnings = await _findings(_compare_frame(), spec, pack_port)

        assert titles[0].startswith("Meridian Health")  # +400k, the biggest rise
        assert all("down" not in title for title in titles), titles
        assert not warnings

    async def test_smallest_flips_the_end_of_the_same_set(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("denied_dollars",), dimensions=("payer",), watermark=WATERMARK)
        spec = replace(
            spec, direction=AskedDirection.INCREASE, magnitude=AskedMagnitude.SMALLEST
        )

        titles, _ = await _findings(_compare_frame(), spec, pack_port)

        assert titles[0].startswith("Atlas Commercial")  # +100k, the smallest rise

    async def test_an_empty_direction_matched_set_says_so_before_the_opposite(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The honest shape of "nothing rose": say it, then offer the falls
        as context — never as an answer to what was asked."""
        falls = tuple(row for row in MOVEMENTS if row[1] < row[2])
        spec = make_spec(measures=("denied_dollars",), dimensions=("payer",), watermark=WATERMARK)
        spec = replace(spec, direction=AskedDirection.INCREASE)

        titles, warnings = await _findings(_compare_frame(falls), spec, pack_port)

        assert warnings and warnings[0].startswith("direction_unmatched")
        assert "increase" in warnings[0]
        assert titles, "the opposite direction is still shown, as context"

    async def test_no_direction_leads_with_the_worst_movement(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """Unprompted, worst-first — read off the contract's sign, not off a
        hardcoded "ascending" that was only ever right for cash."""
        spec = make_spec(measures=("denied_dollars",), dimensions=("payer",), watermark=WATERMARK)

        titles, warnings = await _findings(_compare_frame(), spec, pack_port)

        # denied_dollars is higher_is_bad: the worst thing that happened is
        # the biggest RISE in denied dollars.
        assert titles[0].startswith("Meridian Health")
        assert not warnings


class TestPlanCarriesDirection:
    def test_the_spec_direction_reaches_the_plan_and_its_rank_step(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: SpecFactory
    ) -> None:
        """The seam the calculation layer reads: the plan says which way,
        and every rank/top_k step carries it as an argument so an operator
        never has to reach back for it."""
        spec = make_spec(measures=("denied_dollars",), dimensions=("payer",), watermark=WATERMARK)
        spec = replace(spec, direction=AskedDirection.INCREASE)
        planner = BuildInvestigationPlanService(pack_port, catalog)

        plan = planner.build(spec)

        assert plan.direction is AskedDirection.INCREASE
        rank_steps = [s for s in plan.transforms.steps if s.operator in ("rank", "top_k")]
        assert rank_steps
        assert all(step.arg("direction") == "increase" for step in rank_steps)

    def test_a_question_with_no_direction_carries_none(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("denied_dollars",), dimensions=("payer",), watermark=WATERMARK)
        plan = BuildInvestigationPlanService(pack_port, catalog).build(spec)
        assert plan.direction is None
        assert all(s.arg("direction") is None for s in plan.transforms.steps)


class TestRateMovementIsAnAnswer:
    def test_a_compared_rate_is_a_movement_shape(self) -> None:
        shape = find_primary_movement(
            _compare_plan(),
            CalculationResult(frames=(("c", _ratio_compare_frame()),), operations=()),
        )
        assert shape is not None
        assert shape.measure == "denial_rate"
        assert shape.is_money is False

    async def test_a_grouped_rate_comparison_publishes_findings(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The live repro: "denial rate by payer, last 90 days vs the prior
        90" produced zero findings and a null narrative."""
        spec = make_spec(measures=("denial_rate",), dimensions=("payer",), watermark=WATERMARK)

        titles, _ = await _findings(_ratio_compare_frame(), spec, pack_port)

        assert titles, "a compared rate is a movement, not silence"
        # denial_rate is higher_is_bad, so the rise leads
        assert titles[0].startswith("Meridian Health")

    async def test_a_rate_movement_is_stated_in_percentage_points(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """"up 3.2%" on a rate is ambiguous between relative and absolute
        change. Points are not."""
        spec = make_spec(measures=("denial_rate",), dimensions=("payer",), watermark=WATERMARK)

        titles, _ = await _findings(_ratio_compare_frame(), spec, pack_port)

        assert "9.0 points" in titles[0], titles[0]
        assert "$" not in titles[0]

    async def test_a_rate_movement_publishes_no_impact_cents(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        service = EvaluateFindingsService(FakeReferentRegistryStore())
        spec = make_spec(measures=("denial_rate",), dimensions=("payer",), watermark=WATERMARK)
        result = await service.evaluate(
            plan=_compare_plan(),
            calculation=CalculationResult(frames=(("c", _ratio_compare_frame()),), operations=()),
            spec=spec,
            pack=pack_port,
            playbook=None,
            session_id="sess",
            investigation_id="inv",
        )
        assert result.findings
        assert all(f.impact_cents is None for f in result.findings), "a rate is not dollars"

    def test_money_still_wins_when_a_frame_holds_both(self) -> None:
        """Preferring money keeps every dollar answer this engine already
        gave byte-identical."""
        shape = find_primary_movement(
            _compare_plan(), CalculationResult(frames=(("c", _compare_frame()),), operations=())
        )
        assert shape is not None and shape.measure == "denied_dollars"


class TestUnitRendering:
    def test_points_render_a_rate_difference_unsigned(self) -> None:
        assert points(Decimal("0.032")) == "3.2 points"
        assert points(Decimal("-0.032")) == "3.2 points"

    def test_magnitude_picks_the_unit_the_contract_declared(self) -> None:
        assert magnitude(-12345, "money_cents") == "$123.45"
        assert magnitude(Decimal("-0.013"), "ratio") == "1.3 points"
        assert magnitude(-7, "count") == "7"


class TestLengthMismatchTolerance:
    def test_a_calendar_artifact_is_disclosed_but_costs_nothing(
        self, make_spec: SpecFactory
    ) -> None:
        from revi_investigation.application.comparison import (
            render_comparison,
        )
        from revi_kernel.scope import ComparisonKind, RangeMode, RelativeRange, TimeUnit

        spec = make_spec(
            measures=("denial_rate",),
            window=RelativeRange(Decimal(90), TimeUnit.DAY, RangeMode.TRAILING),
            comparison=ComparisonKind.PRIOR_PERIOD,
            watermark=WATERMARK,
        )
        rendering = render_comparison(spec)
        assert rendering is not None
        # trailing day windows are exactly equal; force a one-day artifact
        rendering = type(rendering)(
            phrase=rendering.phrase,
            range_text=rendering.range_text,
            current_days=90,
            comparison_days=89,
        )
        assert rendering.length_mismatch is True
        assert rendering.material_length_mismatch is False

    def test_a_real_mismatch_is_still_material(self) -> None:
        from revi_investigation.application.comparison import ComparisonRendering

        rendering = ComparisonRendering(
            phrase="x", range_text="y", current_days=7, comparison_days=90
        )
        assert rendering.material_length_mismatch is True

    async def test_a_rate_is_never_qualified_for_a_window_length(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """A ratio over a window does not scale with the window's length, so
        the caveat that exists for totals is simply not true of it."""
        from revi_kernel.scope import ComparisonKind

        spec = make_spec(
            measures=("denial_rate",),
            dimensions=("payer",),
            comparison=ComparisonKind.PRIOR_YEAR,
            watermark=WATERMARK,
        )
        service = EvaluateFindingsService(FakeReferentRegistryStore())
        result = await service.evaluate(
            plan=_compare_plan(),
            calculation=CalculationResult(frames=(("c", _ratio_compare_frame()),), operations=()),
            spec=spec,
            pack=pack_port,
            playbook=None,
            session_id="sess",
            investigation_id="inv",
        )
        assert result.findings
        assert all(f.confidence == "high" for f in result.findings)


class TestEmptinessIsAFact:
    async def test_data_with_no_publishable_finding_records_why(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """Rows exist, nothing survived selection — a different answer from
        "nothing matched", and a different recovery."""
        spec = make_spec(measures=("denied_dollars",), dimensions=("payer",), watermark=WATERMARK)
        spec = replace(spec, direction=AskedDirection.INCREASE)
        # every delta suppressed: rows retrieved, no movement publishable
        frame = _compare_frame()
        blanked = EvidenceFrame(
            schema=frame.schema,
            rows=tuple((row[0], row[1], row[2], None, None) for row in frame.rows),
            watermark=frame.watermark,
            provenance=frame.provenance,
            evidence_grade=frame.evidence_grade,
        )
        service = EvaluateFindingsService(FakeReferentRegistryStore())

        result = await service.evaluate(
            plan=_compare_plan(),
            calculation=CalculationResult(frames=(("c", blanked),), operations=()),
            spec=spec,
            pack=pack_port,
            playbook=None,
            session_id="sess",
            investigation_id="inv",
        )

        assert result.findings == ()
        assert result.emptiness is not None
        assert result.emptiness.kind is EmptinessKind.NO_FINDINGS
        assert result.emptiness.frame_id == "c"
        assert "compared row" in result.emptiness.detail


class TestRankByImpactWithoutAComparison:
    """F4: the daily portfolio answered two scalars from 97 ranked rows.

    Its ``rank`` transform asks for ``impact_cents``, which resolves to a
    money measure's ``__delta`` on a compare output. The playbook's own
    ``compare`` step is skipped when the question carries no comparison
    window — so the rank step resolved to nothing, was dropped, and with it
    every ranked finding the nine planned probes could have produced.
    """

    def test_the_rank_step_falls_back_to_the_level(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(watermark=WATERMARK)
        planner = BuildInvestigationPlanService(pack_port, catalog)

        plan = planner.build(spec, playbook_id="daily_portfolio", window_explicit=False)

        ranks = [s for s in plan.transforms.steps if s.operator == "rank"]
        assert ranks, "a rank the frame can satisfy beats no rank at all"
        assert all(s.arg("by") is not None and "__delta" not in (s.arg("by") or "") for s in ranks)
        assert all(s.arg("descending") == "true" for s in ranks)
        assert not [n for n in plan.notes if "'rank' skipped" in n]

    def test_a_comparison_still_ranks_the_movement(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: SpecFactory
    ) -> None:
        """The fallback is for the case with nothing to compare. Where a
        comparison exists, impact is still the movement."""
        from revi_kernel.scope import ComparisonKind

        spec = make_spec(comparison=ComparisonKind.PRIOR_PERIOD, watermark=WATERMARK)
        plan = BuildInvestigationPlanService(pack_port, catalog).build(
            spec, playbook_id="daily_portfolio", window_explicit=False
        )

        ranks = [s for s in plan.transforms.steps if s.operator == "rank"]
        assert ranks
        assert any("__delta" in (s.arg("by") or "") for s in ranks)
