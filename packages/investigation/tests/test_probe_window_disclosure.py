"""A finding says the period its own number was computed over.

A playbook probe template may declare its own window, which the planner applies
when the analyst named none. That resolution was correct and wholly undisclosed:
findings were titled with ``spec.context.window``, so "denial rate: 14.3%
(2026-07-01..2026-07-31)" sat over a figure computed across 2026-07-06..2026-08-02
— both numbers right, the sentence false. Nothing is re-scoped; the title, the
published values, the comparison phrase and the context header now name the
period actually read.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.findings import (
    PRIOR_WINDOW_END_SUFFIX,
    PRIOR_WINDOW_START_SUFFIX,
    WINDOW_END_SUFFIX,
    WINDOW_START_SUFFIX,
    EvaluateFindingsService,
    published_window_note,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
    TransformPlanStep,
    frame_window,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import Finding
from revi_investigation_contracts.header import build_header_payload
from revi_kernel.filters import EMPTY_SCOPE
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import POST, EntityGrain, Grain, MetricRef, ReferentId, ReferentKind
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
#: The question's window: July, the last full month this load can see.
JULY = TimeWindow(
    basis=POST,
    range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
    requested=RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS),
)
#: The probe's own window: four full weeks to the newest data date. This is
#: the real ``daily_portfolio`` resolution against ``wm_003``.
FOUR_WEEKS = TimeWindow(
    basis=POST,
    range=AbsoluteRange(date(2026, 7, 6), date(2026, 8, 2)),
    requested=RelativeRange(Decimal(4), TimeUnit.WEEK, RangeMode.FULL_PERIODS),
)
PACK_VERSION = PackVersionRef("base-rcm", "1.0.0")

JULY_TEXT = "2026-07-01..2026-07-31"
PROBE_TEXT = "2026-07-06..2026-08-02"
#: The same two periods as a READER sees them. Finding titles and values
#: keep the ISO range (they are handles a client parses); the disclosure
#: PROSE spells the dates out — docs/client-language.md §4, "dates are
#: readable". Both forms are asserted so a regression in either is caught.
JULY_PROSE = "Jul 1, 2026 to Jul 31, 2026"
PROBE_PROSE = "Jul 6, 2026 to Aug 2, 2026"


def _spec(comparison: Comparison | None = None) -> AnalysisSpec:
    return AnalysisSpec(
        context=InvestigationContext(
            window=JULY,
            comparison=comparison,
            scope=EMPTY_SCOPE,
            cohort=None,
            grain=Grain(EntityGrain.CLAIM),
            watermark=WATERMARK,
            pack_version=PACK_VERSION,
        ),
        measures=(),
        dimensions=(),
    )


def _probe(window: TimeWindow) -> AggregationProbe:
    return AggregationProbe(
        measures=(MetricRef("denial_rate"),),
        dimensions=(),
        scope=EMPTY_SCOPE,
        window=window,
        grain=Grain(EntityGrain.CLAIM),
    )


def _scalar_frame(
    *, measure: str = "denial_rate", value: Decimal | int = Decimal("0.143")
) -> EvidenceFrame:
    unit = "ratio" if isinstance(value, Decimal) else "money_cents"
    return EvidenceFrame(
        schema=FrameSchema((FrameColumn(measure, MetricRef(measure), 1, unit),)),
        rows=((value,),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="portfolio_denial_trend", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _compare_frame(
    *, measure: str = "cash_posted", current: int = 87997568, prior: int = 91000000
) -> EvidenceFrame:
    columns = (
        FrameColumn(measure, MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 1, "ratio"),
    )
    pct = Decimal(current - prior) / Decimal(prior)
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=((current, prior, current - prior, pct.quantize(Decimal("0.000001"))),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="portfolio_cash_trend", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _plan(window: TimeWindow, node_id: str = "portfolio_denial_trend") -> InvestigationPlan:
    return InvestigationPlan(
        nodes=(ProbeNode(id=node_id, probe=_probe(window), purpose="playbook probe"),),
        transforms=TransformPlan(steps=()),
    )


def _compare_plan(window: TimeWindow) -> InvestigationPlan:
    return InvestigationPlan(
        nodes=(
            ProbeNode(id="portfolio_cash_trend", probe=_probe(window), purpose="playbook probe"),
        ),
        transforms=TransformPlan(
            steps=(
                TransformPlanStep(
                    id="portfolio_cash_trend__cmp",
                    operator="compare",
                    inputs=("portfolio_cash_trend",),
                ),
            )
        ),
    )


async def _evaluate(
    plan: InvestigationPlan,
    frames: tuple[tuple[str, EvidenceFrame], ...],
    spec: AnalysisSpec,
    pack: PackSnapshotPort,
) -> tuple[Finding, ...]:
    service = EvaluateFindingsService(FakeReferentRegistryStore())
    result = await service.evaluate(
        plan=plan,
        calculation=CalculationResult(frames=frames, operations=()),
        spec=spec,
        pack=pack,
        playbook=None,
        session_id="sess_test",
        investigation_id="inv_test",
    )
    return result.findings


# ---------------------------------------------------------------------------
# the plan knows; the finding asks


class TestFrameWindow:
    def test_a_probe_frame_resolves_to_its_probes_window(self) -> None:
        plan = _plan(FOUR_WEEKS)
        assert frame_window(plan, "portfolio_denial_trend") == FOUR_WEEKS

    def test_a_transform_output_resolves_to_the_probe_that_fed_it(self) -> None:
        """Every operator preserves the window of the frame it was given, and
        a ``compare`` output is keyed to its CURRENT side (its first input)."""
        assert frame_window(_compare_plan(FOUR_WEEKS), "portfolio_cash_trend__cmp") == FOUR_WEEKS

    def test_an_unknown_frame_claims_no_window(self) -> None:
        assert frame_window(_plan(FOUR_WEEKS), "nothing_by_this_name") is None


# ---------------------------------------------------------------------------
# the finding says it


class TestTheFindingStatesItsOwnPeriod:
    async def test_a_scalar_titles_the_probes_window_not_the_questions(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The defect: 'denial rate: 14.3% (2026-07-01..2026-07-31)' over a
        figure derived across 2026-07-06..2026-08-02."""
        [finding] = await _evaluate(
            _plan(FOUR_WEEKS),
            (("portfolio_denial_trend", _scalar_frame()),),
            _spec(),
            pack_port,
        )
        assert PROBE_TEXT in finding.title, finding.title
        assert JULY_TEXT not in finding.title, finding.title
        assert PROBE_PROSE in finding.statement

    async def test_the_statement_says_why_two_periods_appear_on_one_answer(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """A title naming a period the header does not is a puzzle unless the
        answer says whose period it is."""
        [finding] = await _evaluate(
            _plan(FOUR_WEEKS),
            (("portfolio_denial_trend", _scalar_frame()),),
            _spec(),
            pack_port,
        )
        assert "its own period" in finding.statement
        assert JULY_PROSE in finding.statement, "the answer's own window is named as the contrast"

    async def test_the_period_is_published_as_data_not_only_as_prose(
        self, pack_port: PackSnapshotPort
    ) -> None:
        [finding] = await _evaluate(
            _plan(FOUR_WEEKS),
            (("portfolio_denial_trend", _scalar_frame()),),
            _spec(),
            pack_port,
        )
        values = dict(finding.values)
        assert values[f"denial_rate{WINDOW_START_SUFFIX}"] == date(2026, 7, 6)
        assert values[f"denial_rate{WINDOW_END_SUFFIX}"] == date(2026, 8, 2)

    async def test_a_probe_on_the_questions_own_window_says_nothing_extra(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The silence half of the property. A note on every answer explaining
        that a number was computed over the window the header names is noise,
        and noise is how a real disclosure gets read past."""
        [finding] = await _evaluate(
            _plan(JULY),
            (("portfolio_denial_trend", _scalar_frame()),),
            _spec(),
            pack_port,
        )
        assert JULY_TEXT in finding.title
        assert "its own period" not in finding.statement
        assert not [name for name, _ in finding.values if name.endswith(WINDOW_START_SUFFIX)]


class TestTheComparisonMovesWithIt:
    """The prior twin is derived from the PROBE's window (planning's
    ``_comparison_range``), so the phrase beside the movement has to name
    THAT range."""

    async def test_the_phrase_names_the_range_the_probe_was_paired_against(
        self, pack_port: PackSnapshotPort
    ) -> None:
        comparison = derive_comparison(JULY, ComparisonKind.PRIOR_PERIOD)
        probe_prior = derive_comparison(FOUR_WEEKS, ComparisonKind.PRIOR_PERIOD).window.range
        assert probe_prior != comparison.window.range, "the fixture must exercise the difference"

        [finding] = await _evaluate(
            _compare_plan(FOUR_WEEKS),
            (("portfolio_cash_trend__cmp", _compare_frame()),),
            _spec(comparison),
            pack_port,
        )
        expected = f"{probe_prior.start.isoformat()}..{probe_prior.end.isoformat()}"
        assert expected in finding.title, finding.title
        published = comparison.window.range
        assert (
            f"{published.start.isoformat()}..{published.end.isoformat()}" not in finding.title
        ), "the answer's comparison range is not the range this probe was differenced against"

    async def test_the_prior_range_is_published_as_data(
        self, pack_port: PackSnapshotPort
    ) -> None:
        comparison = derive_comparison(JULY, ComparisonKind.PRIOR_PERIOD)
        probe_prior = derive_comparison(FOUR_WEEKS, ComparisonKind.PRIOR_PERIOD).window.range
        [finding] = await _evaluate(
            _compare_plan(FOUR_WEEKS),
            (("portfolio_cash_trend__cmp", _compare_frame()),),
            _spec(comparison),
            pack_port,
        )
        values = dict(finding.values)
        assert values[f"cash_posted{PRIOR_WINDOW_START_SUFFIX}"] == probe_prior.start
        assert values[f"cash_posted{PRIOR_WINDOW_END_SUFFIX}"] == probe_prior.end

    async def test_a_custom_comparison_is_never_re_derived(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """A CUSTOM range is dates the analyst chose. Moving it to fit a
        probe's window would be answering a question nobody asked."""
        custom = derive_comparison(
            JULY, ComparisonKind.CUSTOM, custom=AbsoluteRange(date(2026, 1, 1), date(2026, 3, 31))
        )
        [finding] = await _evaluate(
            _compare_plan(FOUR_WEEKS),
            (("portfolio_cash_trend__cmp", _compare_frame()),),
            _spec(custom),
            pack_port,
        )
        assert "2026-01-01..2026-03-31" in finding.title, finding.title


# ---------------------------------------------------------------------------
# the header says it too


class TestTheContextHeaderNote:
    def test_no_note_when_every_figure_used_the_answers_window(self) -> None:
        assert published_window_note(()) is None

    def test_the_note_names_the_periods_a_reader_can_see_a_number_over(self) -> None:
        finding = Finding(
            referent=_referent(),
            title="denial rate: 14.3% (2026-07-06..2026-08-02)",
            statement="…",
            metric_refs=(MetricRef("denial_rate"),),
            values=(
                ("denial_rate", Decimal("0.143")),
                (f"denial_rate{WINDOW_START_SUFFIX}", date(2026, 7, 6)),
                (f"denial_rate{WINDOW_END_SUFFIX}", date(2026, 8, 2)),
            ),
            grade=EvidenceGrade.DIRECT,
        )
        note = published_window_note((finding,))
        assert note is not None
        assert PROBE_PROSE in note
        assert "own periods" in note

    def test_the_note_rides_on_the_header_display(self) -> None:
        header = build_header_payload(
            window=JULY,
            comparison=None,
            predicates=(),
            watermark_id=WATERMARK.id,
            window_note="some checks here use their own periods (2026-07-06..2026-08-02)",
        )
        assert header.window_note is not None
        assert header.window_note in header.display
        # The header's own window is unchanged: the cohort, the charts and
        # every drill are still scoped by it.
        assert header.window_start == date(2026, 7, 1)
        assert header.window_end == date(2026, 7, 31)

    def test_a_header_with_nothing_to_disclose_is_byte_identical(self) -> None:
        plain = build_header_payload(
            window=JULY, comparison=None, predicates=(), watermark_id=WATERMARK.id
        )
        assert plain.window_note is None
        # The load is named, never numbered: the id stays on
        # `watermark_id` for anyone reconciling against it, and the display
        # string is the sentence a reader sees (docs/client-language.md).
        assert plain.display.endswith("this data load")
        assert WATERMARK.id not in plain.display
        assert plain.watermark_id == WATERMARK.id


def _referent() -> ReferentId:
    return ReferentId(value="F1", kind=ReferentKind.FINDING)
