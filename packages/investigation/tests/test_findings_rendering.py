"""What a finding *says* — the surface a user actually reads.

Before this file existed, no test anywhere asserted on ``Finding.title`` or
``Finding.statement``. Everything was pinned in cents, and the review found
three separate defects living in the gap between a correct number and its
rendering:

- a custom comparison window labelled "vs prior week" while the header on
  the same answer said ``vs 2026-01-01..2026-03-31`` (D1);
- ``Decimal('1.000000')`` and floor-divided dollars printed beside raw
  cents (D7);
- bare CARC integers with the group code and title stripped, merging CO-16
  with PI-16 and publishing PR-2 as a "denial driver" (D7).

The comparison test is the property the review asked for: for **every**
``ComparisonKind``, the context header and the finding text must describe
the same comparison. It is written over the header builder that the API
actually serves, so it cannot pass by agreeing with itself.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.comparison import (
    comparison_phrase,
    render_comparison,
    window_mismatch_warning,
)
from revi_investigation.application.findings import EvaluateFindingsService
from revi_investigation.application.planning import (
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
    TransformPlanStep,
)
from revi_investigation.application.rendering import (
    format_value,
    money,
    ratio_pct,
    render_row_label,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import Finding
from revi_investigation_contracts.header import build_header_payload
from revi_kernel.filters import EMPTY_SCOPE, Scalar
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import POST, DimensionRef, EntityGrain, Grain, MetricRef
from revi_kernel.scope import (
    AbsoluteRange,
    Comparison,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    TimeWindow,
    derive_comparison,
)
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import FakeReferentRegistryStore

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
WEEK = TimeWindow(
    basis=POST,
    range=AbsoluteRange(date(2026, 7, 27), date(2026, 8, 2)),
    requested=RelativeRange(Decimal(1), TimeUnit.WEEK, RangeMode.FULL_PERIODS),
)
Q1 = AbsoluteRange(date(2026, 1, 1), date(2026, 3, 31))
PACK_VERSION = PackVersionRef("base-rcm", "1.0.0")


def _spec(comparison: Comparison | None, *, dimensions: tuple[str, ...] = ("payer",)) -> AnalysisSpec:
    return AnalysisSpec(
        context=InvestigationContext(
            window=WEEK,
            comparison=comparison,
            scope=EMPTY_SCOPE,
            cohort=None,
            grain=Grain(EntityGrain.CLAIM),
            watermark=WATERMARK,
            pack_version=PACK_VERSION,
        ),
        measures=(MetricRef("cash_posted"),),
        dimensions=tuple(DimensionRef(d) for d in dimensions),
    )


def _comparison_of(kind: ComparisonKind) -> Comparison:
    if kind is ComparisonKind.CUSTOM:
        return derive_comparison(WEEK, kind, custom=Q1)
    return derive_comparison(WEEK, kind)


def _compare_frame(
    *,
    dimensions: tuple[tuple[str, str], ...] = (("payer", "State Medicaid"),),
    measure: str = "cash_posted",
    current: int = 8812843,
    prior: int = 18722151,
) -> EvidenceFrame:
    columns = [FrameColumn(name, DimensionRef(name)) for name, _ in dimensions]
    columns += [
        FrameColumn(measure, MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 1, "ratio"),
    ]
    pct = Decimal(current - prior) / Decimal(prior)
    row = (
        *[value for _, value in dimensions],
        current,
        prior,
        current - prior,
        pct.quantize(Decimal("0.0001")),
    )
    return EvidenceFrame(
        schema=FrameSchema(tuple(columns)),
        rows=(row,),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _ranked_frame(
    *,
    dimensions: tuple[tuple[str, str | int], ...],
    measure: str,
    unit: str,
    value: Decimal | int,
) -> EvidenceFrame:
    columns = [FrameColumn(name, DimensionRef(name)) for name, _ in dimensions]
    columns += [
        FrameColumn(measure, MetricRef(measure), 1, unit),
        FrameColumn(f"{measure}__rank", MetricRef(measure), 1, "count"),
    ]
    return EvidenceFrame(
        schema=FrameSchema(tuple(columns)),
        rows=((*[v for _, v in dimensions], value, 1),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _compare_plan() -> InvestigationPlan:
    return InvestigationPlan(
        nodes=(),
        transforms=TransformPlan(
            steps=(TransformPlanStep(id="c", operator="compare", inputs=("main",)),)
        ),
    )


def _rank_plan(measure: str) -> InvestigationPlan:
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


async def _evaluate(
    plan: InvestigationPlan, frame_id: str, frame: EvidenceFrame, spec: AnalysisSpec, pack: PackSnapshotPort
) -> tuple[Finding, ...]:
    service = EvaluateFindingsService(FakeReferentRegistryStore())
    result = await service.evaluate(
        plan=plan,
        calculation=CalculationResult(frames=((frame_id, frame),), operations=()),
        spec=spec,
        pack=pack,
        playbook=None,
        session_id="sess_test",
        investigation_id="inv_test",
    )
    return result.findings


# ---------------------------------------------------------------------------
# D1 — the header and the finding must describe the same comparison


class TestComparisonPhrase:
    @pytest.mark.parametrize("kind", list(ComparisonKind))
    async def test_header_and_finding_agree_for_every_comparison_kind(
        self, kind: ComparisonKind, pack_port: PackSnapshotPort
    ) -> None:
        """The property the review asked for.

        Whatever range the context header prints, the published finding
        text names that same range. This is the check that would have
        caught a CUSTOM Q1 comparison being published as "vs prior week".
        """
        comparison = _comparison_of(kind)
        spec = _spec(comparison)
        header = build_header_payload(
            window=spec.context.window,
            comparison=comparison,
            predicates=(),
            watermark_id=WATERMARK.id,
        )
        assert header.comparison_start is not None and header.comparison_end is not None
        header_range = (
            f"{header.comparison_start.isoformat()}..{header.comparison_end.isoformat()}"
        )
        assert header_range in header.display

        [finding] = await _evaluate(_compare_plan(), "c", _compare_frame(), spec, pack_port)
        assert header_range in finding.title, (kind, finding.title)
        assert header_range in finding.statement, (kind, finding.statement)

    async def test_custom_comparison_is_never_labelled_a_prior_period(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The exact defect: a 7-day window against calendar Q1 published as
        "down $X vs prior week", graded direct/high with an impact."""
        spec = _spec(_comparison_of(ComparisonKind.CUSTOM))
        [finding] = await _evaluate(_compare_plan(), "c", _compare_frame(), spec, pack_port)

        assert "prior week" not in finding.title
        assert "prior period" not in finding.title
        assert "2026-01-01..2026-03-31" in finding.title

    async def test_length_mismatch_withholds_impact_and_qualifies(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """An impact is a dollar figure the platform will rank and sum. A
        90-day-against-7-day difference is not one, so it is not published
        as one — and the finding cannot speak in certified language."""
        spec = _spec(_comparison_of(ComparisonKind.CUSTOM))
        [finding] = await _evaluate(_compare_plan(), "c", _compare_frame(), spec, pack_port)

        assert finding.impact_cents is None
        assert finding.confidence == "qualified"
        assert "not length-normalized" in finding.title
        assert "90d vs 7d" in finding.title

    async def test_equal_length_comparison_keeps_its_impact(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The guard is about window *length*, not about custom ranges: a
        custom range of the same length is an ordinary, rankable delta."""
        same_length = Comparison(
            kind=ComparisonKind.CUSTOM,
            window=replace(WEEK, range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 7)), requested=None),
        )
        spec = _spec(same_length)
        [finding] = await _evaluate(_compare_plan(), "c", _compare_frame(), spec, pack_port)

        assert finding.impact_cents == 8812843 - 18722151
        assert finding.confidence == "high"
        assert "not length-normalized" not in finding.title

    def test_mismatch_warning_is_emitted_only_on_a_mismatch(self) -> None:
        mismatched = window_mismatch_warning(_spec(_comparison_of(ComparisonKind.CUSTOM)))
        assert mismatched is not None
        assert "COMPARISON_WINDOW_MISMATCH" in mismatched
        assert window_mismatch_warning(_spec(_comparison_of(ComparisonKind.PRIOR_PERIOD))) is None
        assert window_mismatch_warning(_spec(None)) is None

    def test_prior_year_and_prior_period_keep_their_labels(self) -> None:
        assert comparison_phrase(_spec(_comparison_of(ComparisonKind.PRIOR_YEAR))).startswith(
            "vs prior year ("
        )
        assert comparison_phrase(_spec(_comparison_of(ComparisonKind.PRIOR_PERIOD))).startswith(
            "vs prior week ("
        )
        assert comparison_phrase(_spec(None)) == "vs prior period"

    def test_rendering_reports_both_window_lengths(self) -> None:
        rendering = render_comparison(_spec(_comparison_of(ComparisonKind.CUSTOM)))
        assert rendering is not None
        assert (rendering.current_days, rendering.comparison_days) == (7, 90)
        assert rendering.length_mismatch is True


# ---------------------------------------------------------------------------
# D7 — every published value is rendered in its contract unit


class TestValueRendering:
    def test_money_keeps_its_cents_and_its_sign(self) -> None:
        assert money(18722151) == "$187,221.51"
        assert money(-9909308) == "-$99,093.08"
        assert money(0) == "$0.00"

    def test_ratios_render_as_percentages(self) -> None:
        assert ratio_pct(Decimal("0.888889")) == "88.9%"
        assert ratio_pct(Decimal("1.000000")) == "100.0%"

    def test_format_value_never_emits_a_python_repr(self) -> None:
        for unit in ("money_cents", "ratio", "days", "count", None):
            rendered = format_value(Decimal("1.000000"), unit)
            assert "Decimal(" not in rendered, unit
        assert format_value(None, "money_cents") == "suppressed"

    async def test_money_findings_carry_cents_not_floor_divided_dollars(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """Reproduces the verbatim defect string: "moved from 18722151 to
        8812843 cents (down $99,093, ...)" — two units, one truncated."""
        spec = _spec(_comparison_of(ComparisonKind.PRIOR_PERIOD))
        [finding] = await _evaluate(_compare_plan(), "c", _compare_frame(), spec, pack_port)

        assert "18722151" not in finding.statement
        assert "cents" not in finding.statement
        assert "$187,221.51" in finding.statement
        assert "$88,128.43" in finding.statement
        assert "$99,093.08" in finding.title

    async def test_ranked_ratio_finding_reads_as_a_percentage(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The repr fallback fired on the *default* plan for any dimensioned
        uncompared query — "Bluestone Mutual / Laboratory:
        Decimal('1.000000') denials unworked pct"."""
        frame = _ranked_frame(
            dimensions=(("payer", "Bluestone Mutual"), ("service_line", "Laboratory")),
            measure="denials_unworked_pct",
            unit="ratio",
            value=Decimal("0.888889"),
        )
        spec = _spec(None, dimensions=("payer", "service_line"))
        [finding] = await _evaluate(
            _rank_plan("denials_unworked_pct"), "r", frame, spec, pack_port
        )

        assert "Decimal(" not in finding.title and "Decimal(" not in finding.statement
        assert "88.9%" in finding.title
        assert finding.impact_cents is None  # a rate is not dollars

    async def test_ranked_count_finding_reads_as_a_count(
        self, pack_port: PackSnapshotPort
    ) -> None:
        frame = _ranked_frame(
            dimensions=(("payer", "Silverline Medicare Advantage"),),
            measure="cob_mismatch_claims",
            unit="count",
            value=153,
        )
        [finding] = await _evaluate(
            _rank_plan("cob_mismatch_claims"), "r", frame, _spec(None), pack_port
        )
        assert "153 cob mismatch claims" in finding.title


# ---------------------------------------------------------------------------
# D7 — denial codes are the group code + CARC pair, with the governed title


class TestDenialCodeRendering:
    def test_pair_renders_as_group_slash_carc_with_title(
        self, pack_port: PackSnapshotPort
    ) -> None:
        label = render_row_label(
            pack_port, ("group_code", "carc"), {"group_code": "CO", "carc": 16}
        )
        assert label.startswith("CO / 16 — ")

    def test_co_and_pi_of_the_same_carc_are_different_labels(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """14 of 20 CARCs in this warehouse span more than one group code;
        a row keyed on the CARC alone merges liabilities that differ."""
        co = render_row_label(pack_port, ("group_code", "carc"), {"group_code": "CO", "carc": 16})
        pi = render_row_label(pack_port, ("group_code", "carc"), {"group_code": "PI", "carc": 16})
        assert co != pi

    def test_a_carc_cut_without_its_group_says_so(self, pack_port: PackSnapshotPort) -> None:
        label = render_row_label(pack_port, ("carc",), {"carc": 16})
        assert label.startswith("CARC 16 — ")
        assert label.endswith("(all adjustment groups)")

    def test_other_dimensions_keep_their_order_and_their_values(
        self, pack_port: PackSnapshotPort
    ) -> None:
        label = render_row_label(
            pack_port,
            ("payer", "group_code", "carc"),
            {"payer": "Bluestone Mutual", "group_code": "PR", "carc": 2},
        )
        assert label.startswith("Bluestone Mutual / PR / 2 — ")

    async def test_a_denial_finding_names_the_pair(self, pack_port: PackSnapshotPort) -> None:
        frame = _compare_frame(
            dimensions=(("group_code", "CO"), ("carc", "16")),
            measure="denied_dollars",
            current=148451,
            prior=16597131,
        )
        spec = _spec(_comparison_of(ComparisonKind.PRIOR_PERIOD), dimensions=("group_code", "carc"))
        [finding] = await _evaluate(_compare_plan(), "c", frame, spec, pack_port)
        assert finding.title.startswith("CO / 16 — ")


# ---------------------------------------------------------------------------
# the scalar shape: the ungrouped answer
#
# A direct question with no breakdown — "what is our net collection rate
# over the last 90 days?" — plans one probe and produces one frame with one
# row and one cell. It has no dimension column, and both older shapes open
# by requiring one, so the probe executed, the number was computed, the
# grade was DIRECT, and `findings` came back EMPTY. The narrative stage
# short-circuits on no findings, so the answer was silent over correct
# evidence. These tests pin the third shape that closes it.


def _scalar_frame(
    *,
    measure: str = "net_collection_rate",
    unit: str = "ratio",
    value: Scalar = Decimal("0.558468"),
    prior: Scalar | None = None,
    grade: EvidenceGrade = EvidenceGrade.DIRECT,
) -> EvidenceFrame:
    """One ungrouped cell, with or without its comparison columns."""
    columns = [FrameColumn(measure, MetricRef(measure), 1, unit)]
    row: list[Scalar] = [value]
    if prior is not None:
        delta = value - prior  # type: ignore[operator]
        columns += [
            FrameColumn(f"{measure}__prior", MetricRef(measure), 1, unit),
            FrameColumn(f"{measure}__delta", MetricRef(measure), 1, unit),
            FrameColumn(f"{measure}__pct_change", MetricRef(measure), 1, "ratio"),
        ]
        row += [prior, delta, Decimal(delta) / Decimal(prior)]  # type: ignore[arg-type]
    return EvidenceFrame(
        schema=FrameSchema(tuple(columns)),
        rows=(tuple(row),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
        evidence_grade=grade,
    )


def _probe_node(node_id: str, measure: str) -> ProbeNode:
    return ProbeNode(
        id=node_id,
        probe=AggregationProbe(
            measures=(MetricRef(measure),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            window=WEEK,
            grain=Grain(EntityGrain.CLAIM),
        ),
        purpose="direct metric query",
    )


def _scalar_plan(*, measure: str = "net_collection_rate", compared: bool = False) -> InvestigationPlan:
    nodes = [_probe_node("main", measure)]
    steps: tuple[TransformPlanStep, ...] = ()
    if compared:
        nodes.append(_probe_node("main__prior", measure))
        steps = (
            TransformPlanStep(
                id="main__compare", operator="compare", inputs=("main", "main__prior")
            ),
        )
    return InvestigationPlan(nodes=tuple(nodes), transforms=TransformPlan(steps=steps))


class TestScalarShape:
    async def test_a_single_cell_frame_publishes_a_finding(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The P0: one probe, one row, one cell — and an answer."""
        findings = await _evaluate(
            _scalar_plan(), "main", _scalar_frame(), _spec(None, dimensions=()), pack_port
        )
        [finding] = findings
        assert finding.referent.value == "F1"
        assert finding.grade is EvidenceGrade.DIRECT
        assert finding.confidence == "high"

    async def test_the_level_is_rendered_in_the_contracts_unit(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """A ratio is a percentage, never ``Decimal('0.558468')``."""
        [finding] = await _evaluate(
            _scalar_plan(), "main", _scalar_frame(), _spec(None, dimensions=()), pack_port
        )
        assert "55.8%" in finding.title
        assert "0.558468" not in finding.title
        assert "0.558468" not in finding.statement

    async def test_money_scalars_read_as_dollars_and_carry_no_impact_without_a_comparison(
        self, pack_port: PackSnapshotPort
    ) -> None:
        frame = _scalar_frame(measure="cash_posted", unit="money_cents", value=8812843)
        [finding] = await _evaluate(
            _scalar_plan(measure="cash_posted"),
            "main",
            frame,
            _spec(None, dimensions=()),
            pack_port,
        )
        assert "$88,128.43" in finding.title
        # a level is not a movement, so there is nothing to call an impact
        assert finding.impact_cents is None

    async def test_the_window_is_named(self, pack_port: PackSnapshotPort) -> None:
        """A level without its window is a number without a claim."""
        [finding] = await _evaluate(
            _scalar_plan(), "main", _scalar_frame(), _spec(None, dimensions=()), pack_port
        )
        assert "2026-07-27..2026-08-02" in finding.statement

    async def test_a_suppressed_cell_publishes_no_finding(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """"suppressed" is not a level, and a headline may not assert one."""
        findings = await _evaluate(
            _scalar_plan(),
            "main",
            _scalar_frame(value=None),
            _spec(None, dimensions=()),
            pack_port,
        )
        assert findings == ()

    async def test_a_weak_grade_qualifies_the_scalar_finding(
        self, pack_port: PackSnapshotPort
    ) -> None:
        [finding] = await _evaluate(
            _scalar_plan(),
            "main",
            _scalar_frame(grade=EvidenceGrade.PROXY),
            _spec(None, dimensions=()),
            pack_port,
        )
        assert finding.confidence == "qualified"

    async def test_a_multi_row_ungrouped_frame_is_not_a_scalar(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """A time-bucketed series is a trend, not N headline levels."""
        frame = _scalar_frame()
        two_rows = replace(frame, rows=(frame.rows[0], frame.rows[0]))
        findings = await _evaluate(
            _scalar_plan(), "main", two_rows, _spec(None, dimensions=()), pack_port
        )
        assert findings == ()


class TestScalarComparison:
    """Two-cell shapes: the metric and its prior-period twin."""

    async def test_a_compared_scalar_states_both_sides_and_its_direction(
        self, pack_port: PackSnapshotPort
    ) -> None:
        frame = _scalar_frame(value=Decimal("0.070418"), prior=Decimal("0.060335"))
        spec = _spec(_comparison_of(ComparisonKind.PRIOR_YEAR), dimensions=())
        [finding] = await _evaluate(
            _scalar_plan(compared=True), "main__compare", frame, spec, pack_port
        )
        assert "7.0%" in finding.title
        assert "up from 6.0%" in finding.title
        assert comparison_phrase(spec) in finding.title

    async def test_a_ratio_delta_is_never_published_as_the_movement(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """A rate that moved 0.010083 rose by one percentage POINT, not by
        1.0%. Rendering that delta in the metric's own unit would publish
        an ambiguity, so both levels are stated instead and the relative
        change is labelled as a change."""
        frame = _scalar_frame(value=Decimal("0.070418"), prior=Decimal("0.060335"))
        spec = _spec(_comparison_of(ComparisonKind.PRIOR_YEAR), dimensions=())
        [finding] = await _evaluate(
            _scalar_plan(compared=True), "main__compare", frame, spec, pack_port
        )
        assert "1.0%" not in finding.title
        assert "16.7% change" in finding.statement

    async def test_a_rising_rate_is_never_reported_as_unchanged(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The direction is read off the delta in whatever numeric type the
        operator produced. An int-only coercion reported every ``Decimal``
        movement as "unchanged" — a headline stating the opposite of the
        evidence directly beneath it."""
        frame = _scalar_frame(value=Decimal("0.070418"), prior=Decimal("0.060335"))
        spec = _spec(_comparison_of(ComparisonKind.PRIOR_YEAR), dimensions=())
        [finding] = await _evaluate(
            _scalar_plan(compared=True), "main__compare", frame, spec, pack_port
        )
        assert "unchanged" not in finding.title

    async def test_a_money_movement_carries_its_impact(
        self, pack_port: PackSnapshotPort
    ) -> None:
        frame = _scalar_frame(
            measure="cash_posted", unit="money_cents", value=8812843, prior=18722151
        )
        spec = _spec(_comparison_of(ComparisonKind.PRIOR_PERIOD), dimensions=())
        [finding] = await _evaluate(
            _scalar_plan(measure="cash_posted", compared=True), "main__compare", frame, spec, pack_port
        )
        assert finding.impact_cents == 8812843 - 18722151
        assert "down from $187,221.51" in finding.title

    async def test_a_length_mismatched_comparison_withholds_impact(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The same rule the grouped shape applies (see comparison.py)."""
        frame = _scalar_frame(
            measure="cash_posted", unit="money_cents", value=8812843, prior=18722151
        )
        spec = _spec(_comparison_of(ComparisonKind.CUSTOM), dimensions=())
        [finding] = await _evaluate(
            _scalar_plan(measure="cash_posted", compared=True), "main__compare", frame, spec, pack_port
        )
        assert finding.impact_cents is None
        assert finding.confidence == "qualified"

    async def test_findings_values_ground_every_number_the_text_states(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The narrative validator trusts ``values`` and nothing else, so a
        figure in the title that is not in ``values`` is a sentence the
        composer can never legally write."""
        frame = _scalar_frame(value=Decimal("0.070418"), prior=Decimal("0.060335"))
        spec = _spec(_comparison_of(ComparisonKind.PRIOR_YEAR), dimensions=())
        [finding] = await _evaluate(
            _scalar_plan(compared=True), "main__compare", frame, spec, pack_port
        )
        names = {name for name, _ in finding.values}
        assert names == {
            "net_collection_rate",
            "net_collection_rate__prior",
            "net_collection_rate__delta",
            "pct_change",
        }
