"""Findings evaluation (design §8.1 steps 12-13): certified, referent-
addressable results built from the final frames, with drillable cohorts.

Three finding shapes, tried in order — all generic, none keyed to any
question or playbook id:

**Movement** (preferred). The primary findings frame is the first
``compare`` output that carries at least one dimension column and a money
measure with its delta. Rows are ranked by delta **ascending** (the biggest
declines of a higher-is-good measure first) and the top N become findings
F1, F2, ... Each carries current/prior/delta cents and pct change, and
``impact_cents`` equal to the delta. That ascending default holds only
while the question asserted no direction: when the spec carries one
(``AskedDirection``, resolved against the metric's sign convention), rows
moving the other way are not eligible to be the answer, and an empty
direction-matched set says so before the opposite is offered as context —
see ``_select_directional``.

**Concentration** (fallback). Plenty of real questions have no comparison
at all — "do I have a COB problem?", "score my facilities", "what's aging
out of timely filing?". Their playbooks rank a population instead of
comparing two windows, and before this path existed they executed
perfectly and then answered *nothing*: no compare frame, no findings, an
empty answer over correct evidence. So when no compare shape exists, the
first ``rank`` output carrying a dimension column and a measure supplies
the findings, in rank order, with ``impact_cents`` set only when the
ranked measure is money (a claim count is not dollars, and pretending
otherwise would invent an impact). Share-of-total columns ride along when
the playbook computed them.

**Scalar** (the ungrouped answer). The plainest question there is — "what
is our net collection rate over the last 90 days?" — plans one probe, no
dimensions, no comparison, and produces one frame with one row and one
cell. It has no dimension column, so neither shape above could see it:
``find_primary_compare`` and ``find_primary_concentration`` both begin by
requiring ``_dimension_columns(frame)`` to be non-empty and return ``None``
when it is. The probe executed, the number was computed, the grade was
DIRECT, the chart drew it — and ``findings`` came back empty, which also
meant the narrative stage short-circuited and the answer was silent. A
computed number the analyst never saw is the same failure the concentration
path was added to fix, one shape further down. So a frame with no
dimension columns and exactly one row publishes its metric cells as
findings: the level, the window, the grade, and — when the turn carried a
comparison, so the frame also holds ``__prior``/``__delta``/``__pct_change``
— the movement. Both sides are rendered in the metric contract's own unit,
so a ratio reads as a percentage and money as dollars; ``impact_cents`` is
set only for money, exactly as in the concentration path. A suppressed cell
publishes no finding: "suppressed" is not a level.

**Trend** (the series). An ungrouped frame with a *time bucket* column and
more than one row is neither a scalar nor a breakdown — it is a series, and
the scalar path refuses it by construction ("a frame with more than one row
is not a scalar"). Before this shape existed, "denial rate by month for the
last 6 months" either collapsed into one six-month number (grain dropped,
silently) or published nothing at all (grain honored, nothing to say). One
finding per measure states it as a series: where it started, where it
ended, and its extremes with the bucket each fell in. ``impact_cents``
stays unset — the end-to-end movement of a series is a description, not a
recoverable figure.

**Premise** (before all of them). A question that *states* a movement
("why did denials double") is answered honestly only once that movement has
been measured: the planner adds an ungrouped premise probe
(``BuildInvestigationPlanService.PREMISE_PREFIX``) and :func:`verify_premise`
checks the asserted direction against the aggregate. When the aggregate
moved the other way the correction leads — a ``premise_false`` warning
first, and F1 is the correction itself with the aggregate figures behind it
— and the direction-matched cells follow as context. Live, that path was
answering "why did denials at Federal Medicare double in July" with the
three CARC cells that rose, totalling $3,204, inside a fall from $58,983.54
to $10,915.24 that no sentence mentioned.

Whichever shape applies, each finding gets — via the referent registry — a drillable
:class:`CohortDefinition` at the CLAIM entity scoped to the finding's
dimension values plus the analysis window, so a later ``DrillInto``
refinement can pin the exact population shown.

Conclusion policies gate confidence: when a playbook's policies demand a
stronger grade than the frame provides (proxy or discovery evidence, or a
policy requiring DIRECT), the finding's confidence drops to "qualified" —
weak evidence can surface, but never in certified language. A comparison
whose two windows are different lengths qualifies a finding for the same
reason and additionally withholds ``impact_cents``
(:mod:`revi_investigation.application.comparison` documents why).

Every value that reaches a title or a statement is rendered through
:mod:`revi_investigation.application.rendering`, in the unit the metric
contract declares — never ``repr``, never floor-divided dollars beside raw
cents, and never a bare CARC integer without its group code and title.

Every compare row is also registered as a dimension-value referent
(D1, D2, ...) so table rows are addressable in follow-up turns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from revi_calculation_contracts.contract import SignConvention
from revi_investigation.application.calculation_glue import (
    CalculationResult,
    EmptinessFact,
    EmptinessKind,
)
from revi_investigation.application.capability_ports import PackPort, PlaybookSpec
from revi_investigation.application.comparison import ComparisonRendering, render_comparison
from revi_investigation.application.execution import (
    BoundedCell,
    SuppressionCensus,
    bound_index,
    suppression_census,
)
from revi_investigation.application.gestures import suggested_refinements_for
from revi_investigation.application.planning import InvestigationPlan
from revi_investigation.application.ports import ReferentRegistryStore, RegisteredReferent
from revi_investigation.application.rendering import (
    COUNT_UNIT as _COUNT_UNIT,
)
from revi_investigation.application.rendering import (
    MONEY_UNIT as _MONEY_UNIT,
)
from revi_investigation.application.rendering import (
    format_value,
    magnitude,
    magnitude_money,
    metric_label,
    ratio_pct,
    render_row_label,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    AskedMagnitude,
    adverse_delta_sign,
    descending_for_order,
    wanted_delta_sign,
)
from revi_investigation.domain.records import Finding
from revi_kernel.cohort import CohortDefinition
from revi_kernel.filters import Predicate, PredicateOp, Scalar, and_merge
from revi_kernel.frame import EvidenceFrame
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import (
    DimensionRef,
    EntityGrain,
    MetricRef,
    ReferentId,
    ReferentKind,
)

_TIME_BUCKET_PREFIX = "time_bucket:"

#: The adapter's ratio-denominator column suffix (``denial_rate__den``).
#: Read here to judge whether a series' terminal bucket has settled.
_DENOMINATOR_SUFFIX = "__den"

#: Node-id prefix of the premise-verification probe (see
#: ``BuildInvestigationPlanService.PREMISE_PREFIX``). Compared as a string
#: so this module keeps its existing import surface.
_PREMISE_PREFIX = "premise"

#: The contract ``kind`` that reports a balance standing at a moment rather
#: than a quantity accumulated over a window (round-2 FN-2). Compared as a
#: string so this module keeps its existing import surface.
_SNAPSHOT_KIND = "snapshot"


def _is_snapshot(pack: PackPort, measure: str) -> bool:
    contract = pack.metric(measure)
    return contract is not None and str(contract.kind) == _SNAPSHOT_KIND


def _period_phrase(spec: AnalysisSpec, pack: PackPort, measure: str, frame: EvidenceFrame) -> str:
    """"over 2026-07-01..2026-07-31" — or "as of 2026-08-02" for a snapshot.

    Eight contracts here are ``kind: snapshot``: they read the balance at
    the watermark and apply no start..end predicate. Published titles and
    statements nonetheless carried the turn's window, so a finding read
    ``timely filing at risk dollars: $22,426,000.28 (2026-07-01..2026-07-31)``
    over a figure that is the ALL-TIME total — the July number is
    $5,565,290.35, and the two are not the same claim. The window is not
    removed from the answer (the cohort and charts are scoped by it); it is
    removed from the sentence that says what the number measures.
    """
    if _is_snapshot(pack, measure):
        return f"as of {frame.watermark.newest_data_date.isoformat()}"
    window = spec.context.window.range
    return f"over {window.start.isoformat()}..{window.end.isoformat()}"


def _period_paren(spec: AnalysisSpec, pack: PackPort, measure: str, frame: EvidenceFrame) -> str:
    """The same period, parenthesized for a title."""
    if _is_snapshot(pack, measure):
        return f"(as of {frame.watermark.newest_data_date.isoformat()})"
    window = spec.context.window.range
    return f"({window.start.isoformat()}..{window.end.isoformat()})"
_PRIOR_SUFFIX = "__prior"
_QUALIFIED_GRADES = (EvidenceGrade.PROXY, EvidenceGrade.DISCOVERY, EvidenceGrade.UNAVAILABLE)

#: Share of a ranking frame's published cells that may carry an upper bound
#: before the ranking itself stops meaning anything (round-3 R3-02).
#:
#: "Rank our rendering providers by denial rate, worst first" published 150
#: values of which 147 were exactly ``(threshold - 1) / n``: the sort key
#: was the panel size, inverted, and the answer's first sentence was "Dr.
#: Casey Quarry ranks #1 by denial rate (worst first, as asked)". Past this
#: share there is no measured population left to rank, and the honest
#: answer is the population arithmetic rather than an order.
MAX_BOUNDED_SHARE_FOR_RANKING = 0.5

#: How far the movement a question ASSERTS may fall short of the movement
#: that happened before the premise is refuted (round-3 R3-03).
#:
#: ``holds`` tested direction alone, so "why did denials double?" over a
#: +4.2% move was scored TRUE, the verdict was discarded, and the narrative
#: opened on a 243% sub-cell. A doubling is a claim about size: an actual
#: change smaller than this fraction of the asserted one did not happen,
#: whatever its sign.
PREMISE_MAGNITUDE_TOLERANCE = Decimal("0.5")


def _bound_values(measure: str, bound: BoundedCell | None) -> list[tuple[str, Scalar]]:
    """The bound, as named values on the finding rather than as prose.

    Three additive names, so every consumer — card, chart, CSV, replay —
    can ask the same question the title now answers: is this a measurement
    (``__is_bound`` absent or false), what is the ceiling, and how big was
    the population it was taken over.
    """
    if bound is None:
        return []
    return [
        (f"{measure}__is_bound", True),
        (f"{measure}__bound", bound.bound),
        (f"{measure}__bound_population", bound.population),
    ]


def bound_text(value: Scalar, unit: str | None, *, bounded: bool) -> str:
    """A published figure, with the ``≤`` a suppressed numerator earns it.

    The single place a bound becomes visible prose. Round-3 R3-01: the
    engine computed the ceiling and then rendered it through the ordinary
    value formatter, so every title, statement, chart label and export cell
    said "45.5% denial rate" about 10/22 — the ``≤`` existed only inside a
    warning string nobody's screenshot contained.
    """
    text = format_value(value, unit)
    return f"≤ {text}" if bounded else text

#: Units whose totals scale with the length of the window they are measured
#: over. A length-mismatched comparison distorts these and leaves a rate
#: alone, which is why the mismatch caveat is applied per unit rather than
#: per turn.
_ADDITIVE_UNITS = (_MONEY_UNIT, _COUNT_UNIT)


def _is_additive(unit: str | None) -> bool:
    return unit in _ADDITIVE_UNITS


@dataclass(frozen=True, slots=True)
class FindingsResult:
    findings: tuple[Finding, ...]
    referents: tuple[RegisteredReferent, ...]
    #: What the analyst has to be told about the *selection* before they
    #: read the rows — chiefly that nothing moved the way they asked about.
    #: These lead the turn's warnings: a caveat published under the findings
    #: it contradicts is a caveat nobody reads.
    warnings: tuple[str, ...] = ()
    #: Set when frames had rows and no finding survived selection. The
    #: other half of :class:`EmptinessFact`: "there is nothing here" and
    #: "there is plenty here and none of it is notable" are different
    #: answers, and publishing both as silence made them the same one.
    emptiness: EmptinessFact | None = None


def _with_premise(
    result: FindingsResult, premise: tuple[Finding, RegisteredReferent, str] | None
) -> FindingsResult:
    """Put the premise verdict at the head of a non-movement result.

    The verdict is registered before the branch runs, so the branch's own
    findings are already numbered from F2 — this only splices the published
    objects back into the order the reader sees them in.
    """
    if premise is None:
        return result
    finding, referent, warning = premise
    return FindingsResult(
        findings=(finding, *result.findings),
        referents=(referent, *result.referents),
        warnings=(warning, *result.warnings),
        # A premise verdict IS a finding, so a turn carrying one is never
        # empty however little the rest of the plan found.
        emptiness=None,
    )


def _declared_bucket_order(
    plan: InvestigationPlan | None, shape: ConcentrationShape
) -> tuple[str, ...] | None:
    """The catalog's declared order for this cut, when it is an ordinal one."""
    if plan is None or len(shape.dimension_columns) != 1:
        return None
    return plan.bucket_order(shape.dimension_columns[0])


def _unranked_bounds_warning(
    *,
    bounded_count: int,
    measured_count: int,
    total: int,
    measure: str,
    unrankable: bool,
    order: object | None,
) -> str:
    """What a ranking owes a reader once some of its cells are ceilings.

    Round-3 R3-02, and the population arithmetic R3-18 asked for in the
    same breath: the counts are computed once, here, from the frame the
    findings were selected from, so the narrative cannot invent a different
    denominator for them.
    """
    label = metric_label(measure)
    noun = "cell" if bounded_count == 1 else "cells"
    if unrankable:
        return (
            f"ranking_refused: {bounded_count} of the {total} publishable {label} {noun} on this "
            f"answer carry an upper bound rather than a measurement, leaving {measured_count} "
            "measured, so no ranking is published. Ordering ceilings against measurements sorts "
            "by population size, not by the measure that was asked about. The bounded cells are "
            "listed separately, each with the population its bound was taken over."
        )
    asked = " (the order you asked for applies to the measured cells only)" if order else ""
    return (
        f"bounded_cells_unranked: {bounded_count} of {total} {label} {noun} had a suppressed "
        f"numerator and are published as upper bounds in their own block, unranked{asked}. The "
        f"ranking above covers the {measured_count} measured {'cell' if measured_count == 1 else 'cells'} "
        "only — a ceiling has no position in an order it was never measured for."
    )


def _truncation_warning(served: int, computed: int, spec: AnalysisSpec) -> str | None:
    """What a truncated finding list owes its reader (round-3 R3-04).

    Live, "show me all twelve payers, not just three" and "every one of our
    12 payers" both returned three findings with no omission notice, and
    the same turn's evidence panel read ``rows: 12, limit: null, truncated:
    false``. The narrative then called a 4.4% to 15.0% spread "roughly three
    percentage points … a tight band" over the three it could see.
    """
    if computed <= served:
        return None
    asked = (
        " The limit you asked for could not be met by the rows this turn computed."
        if spec.limit is not None and spec.limit > served
        else ""
    )
    return (
        f"findings_truncated: {served} of {computed} computed cells are published as findings; "
        f"the remaining {computed - served} are in the chart and the evidence frame but carry "
        "no finding. Superlatives and spread statements on this answer describe the published "
        f"slice, not the full population.{asked}"
    )


@dataclass(frozen=True, slots=True)
class CompareShape:
    """A compare frame suitable for findings/reconciliation: at least one
    dimension column plus a money measure with its delta."""

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    money_measure: str


def find_primary_compare(
    plan: InvestigationPlan, calculation: CalculationResult
) -> CompareShape | None:
    """The first compare output carrying dimensions and a money measure —
    the findings frame, and the child side of the reconciliation invariant."""
    for step in plan.transforms.steps:
        if step.operator != "compare":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        money = _money_measure(frame)
        if dims and money is not None:
            return CompareShape(
                frame_id=step.id, frame=frame, dimension_columns=dims, money_measure=money
            )
    return None


@dataclass(frozen=True, slots=True)
class MovementShape:
    """A compare frame the movement path can publish findings from.

    The generalization of :class:`CompareShape`. That one required a
    **money** measure with a delta, because the first questions this engine
    answered were about dollars — and the requirement quietly became a
    filter on which questions could be answered at all. "Denial rate by
    payer for the last 90 days compared to the prior 90 days" plans two
    probes, compares them correctly, produces a frame with a payer column,
    a rate, a prior rate, a delta and a percentage change, and published
    **zero findings and a null narrative**: no column in it was money, so
    the shape came back ``None``, and ``evaluate`` only fell through to the
    concentration path when there was no compare shape — the compare step
    existed, so nothing looked further.

    A movement is a movement in whatever unit the contract declares.
    ``impact_cents`` is still money-only (a rate is not dollars), and
    reconciliation still requires money (children of a rate do not sum),
    which is why :func:`find_primary_compare` stays as it was.
    """

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    measure: str
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


def find_primary_movement(
    plan: InvestigationPlan, calculation: CalculationResult
) -> MovementShape | None:
    """The first compare output carrying dimensions and any measure delta.

    Money wins when a frame holds several compared measures — a dollar
    movement is what a worklist is built from, and preferring it keeps
    every answer this engine already gave byte-identical. Otherwise the
    first compared metric column is the answer, which is the case that used
    to publish nothing.
    """
    for step in plan.transforms.steps:
        if step.operator != "compare":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        if not dims:
            continue
        compared = _compared_measures(frame)
        if not compared:
            continue
        best = next((name for name in compared if _unit_of(frame, name) == _MONEY_UNIT), None)
        measure = best if best is not None else compared[0]
        return MovementShape(
            frame_id=step.id,
            frame=frame,
            dimension_columns=dims,
            measure=measure,
            unit=_unit_of(frame, measure),
        )
    return None


def _compared_measures(frame: EvidenceFrame) -> tuple[str, ...]:
    """Metric columns in this frame that carry a ``__delta`` sibling."""
    names = set(frame.schema.names)
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, MetricRef)
        and "__" not in col.name
        and f"{col.name}__delta" in names
    )


@dataclass(frozen=True, slots=True)
class ConcentrationShape:
    """A ranked frame suitable for findings when nothing was compared: at
    least one dimension column plus the measure the rank was taken on."""

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    measure: str
    rank_column: str
    share_column: str | None
    is_money: bool
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None


def find_primary_concentration(
    plan: InvestigationPlan, calculation: CalculationResult
) -> ConcentrationShape | None:
    """The first ``rank`` output carrying dimensions and a base measure —
    the findings frame for playbooks that rank rather than compare."""
    for step in plan.transforms.steps:
        if step.operator != "rank":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        if not dims:
            continue
        ranked_by = step.arg("by")
        if ranked_by is None:
            continue
        measure = _base_measure(frame, ranked_by)
        if measure is None:
            continue
        rank_column = f"{ranked_by}__rank"
        if rank_column not in frame.schema.names:
            continue
        share_column = f"{measure}__share"
        unit = _unit_of(frame, measure)
        return ConcentrationShape(
            frame_id=step.id,
            frame=frame,
            dimension_columns=dims,
            measure=measure,
            rank_column=rank_column,
            share_column=share_column if share_column in frame.schema.names else None,
            is_money=unit == _MONEY_UNIT,
            unit=unit,
        )
    return None


@dataclass(frozen=True, slots=True)
class ScalarShape:
    """One ungrouped metric cell: the whole answer to a direct question.

    ``prior_column``/``delta_column``/``pct_column`` are set only when the
    turn carried a comparison and the ``compare`` operator produced them,
    which is what separates "the rate is 5.2%" from "the rate is 5.2%, up
    from 4.9%".
    """

    frame_id: str
    frame: EvidenceFrame
    measure: str
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None
    prior_column: str | None
    delta_column: str | None
    pct_column: str | None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT

    @property
    def compared(self) -> bool:
        return self.prior_column is not None and self.delta_column is not None


def find_scalar_shapes(
    plan: InvestigationPlan, calculation: CalculationResult
) -> tuple[ScalarShape, ...]:
    """Every ungrouped single-row metric cell this plan produced.

    Reads the *final* logical frame for each probe node — the node's
    ``compare`` output when it has one, else the probe frame itself — so a
    scalar with a comparison is described by its movement rather than twice
    by its level. Prior-window twins are skipped: they are an input to the
    comparison, never an answer.

    A frame with more than one row is not a scalar. That is deliberate:
    an ungrouped frame with several rows is a time-bucketed series, which
    is a trend and wants a trend's treatment, not N headline levels.
    """
    compare_of: dict[str, str] = {}
    for step in plan.transforms.steps:
        if step.operator == "compare" and step.inputs:
            compare_of[step.inputs[0]] = step.id

    shapes: list[ScalarShape] = []
    for node in plan.nodes:
        if node.id.endswith(_PRIOR_SUFFIX):
            continue
        frame_id = compare_of.get(node.id, node.id)
        try:
            frame = calculation.frame(frame_id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        if _dimension_columns(frame) or len(frame.rows) != 1:
            continue
        names = set(frame.schema.names)
        for column in frame.schema.columns:
            if not isinstance(column.ref, MetricRef) or "__" in column.name:
                continue
            measure = column.name
            prior = f"{measure}__prior"
            delta = f"{measure}__delta"
            pct = f"{measure}__pct_change"
            compared = prior in names and delta in names
            shapes.append(
                ScalarShape(
                    frame_id=frame_id,
                    frame=frame,
                    measure=measure,
                    unit=column.unit,
                    prior_column=prior if compared else None,
                    delta_column=delta if compared else None,
                    pct_column=pct if pct in names else None,
                )
            )
    return tuple(shapes)


def _dimension_columns(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, DimensionRef) and not col.ref.id.startswith(_TIME_BUCKET_PREFIX)
    )


def _time_bucket_column(frame: EvidenceFrame) -> str | None:
    """The frame's time axis, when it has one (``time_bucket:month``)."""
    for col in frame.schema.columns:
        if isinstance(col.ref, DimensionRef) and col.ref.id.startswith(_TIME_BUCKET_PREFIX):
            return col.name
    return None


@dataclass(frozen=True, slots=True)
class TrendShape:
    """An ungrouped series over time: the answer to "…by month".

    The shape that had nowhere to go. ``find_scalar_shapes`` deliberately
    refuses a frame with more than one row — "an ungrouped frame with
    several rows is a time-bucketed series, which is a trend and wants a
    trend's treatment" — and until now nothing gave it one, so a monthly
    breakdown either collapsed into a single scalar (when the grain was
    dropped) or published nothing at all (when it was honored). One
    finding per measure states the series as a series: where it started,
    where it ended, and its extremes, each with the bucket it fell in.
    """

    frame_id: str
    frame: EvidenceFrame
    bucket_column: str
    measure: str
    unit: str | None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


#: How small a terminal bucket's own denominator may be, as a fraction of
#: the series median, before the point it produces is a data-maturity
#: artifact rather than a measurement (round-3 R3-06).
#:
#: Live: adjudicated denominators 6,049 / 6,133 / 5,723 / 1,544 across
#: 2026-01..2026-07. The July point was computed on 25% of the median panel
#: — the fastest-adjudicating quarter of the month, which skews heavily to
#: denials — and published as "up 5.5 points", ``direct``, ``high``, with an
#: ACA benchmark attached. "Are denials getting worse" answered confidently
#: backwards.
TERMINAL_BUCKET_MIN_SHARE = Decimal("0.6")


@dataclass(frozen=True, slots=True)
class TerminalCensoring:
    """A trend's last bucket, and why it cannot close the series."""

    bucket: str
    #: The bucket's own population, and the series median, when the frame
    #: carries a denominator to read them off.
    population: int | None
    median_population: int | None
    reason: str
    warning: str


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _bucket_end(bucket: Scalar, noun: str) -> date | None:
    """The last calendar day the bucket covers, when it can be derived."""
    text = str(bucket)
    try:
        start = date.fromisoformat(text[:10]) if len(text) >= 10 else None
        if start is None and noun == "month" and len(text) >= 7:
            start = date(int(text[:4]), int(text[5:7]), 1)
    except ValueError:
        return None
    if start is None:
        return None
    if noun == "day":
        return start
    if noun == "week":
        return start + timedelta(days=6)
    if noun == "month":
        return date(start.year + start.month // 12, start.month % 12 + 1, 1) - timedelta(days=1)
    if noun == "quarter":
        end_month = ((start.month - 1) // 3 + 1) * 3
        return date(
            start.year + end_month // 12, end_month % 12 + 1, 1
        ) - timedelta(days=1)
    if noun == "year":
        return date(start.year, 12, 31)
    return None


def terminal_bucket_censoring(
    shape: TrendShape, spec: AnalysisSpec
) -> TerminalCensoring | None:
    """Is this series' last point a measurement, or an artifact of maturity?

    Two independent tests, both read off material the turn already holds:

    * **Calendar-partial** — the bucket extends past the newest data date,
      so it covers fewer days than every bucket before it. ``2026-08`` on a
      load ending ``2026-08-02`` is two days of a month, and it was plotted
      unannotated beside eleven full ones.
    * **Right-censored** — the bucket's own adjudicated denominator is a
      fraction of the series median. The claims exist; they have not
      settled. The engine holds both counts and said neither.
    """
    frame = shape.frame
    schema = frame.schema
    if shape.bucket_column not in schema.names or len(frame.rows) < 3:
        return None
    idx_bucket = schema.index_of(shape.bucket_column)
    idx_value = schema.index_of(shape.measure)
    rows = sorted(
        (row for row in frame.rows if _as_number(row[idx_value]) is not None),
        key=lambda row: str(row[idx_bucket]),
    )
    if len(rows) < 3:
        return None
    noun = _bucket_noun(frame, shape.bucket_column)
    terminal = rows[-1]
    bucket_label = _bucket_text(terminal[idx_bucket], noun)
    newest = frame.watermark.newest_data_date

    covered = _bucket_end(terminal[idx_bucket], noun)
    if covered is not None and covered > newest:
        reason = (
            f"the {noun} runs to {covered.isoformat()} and this load ends "
            f"{newest.isoformat()}, so the bucket holds only part of its own period."
        )
        return TerminalCensoring(
            bucket=bucket_label,
            population=None,
            median_population=None,
            reason=reason,
            warning=(
                f"adjudication_incomplete: the last point of this series ({bucket_label}) is a "
                f"PARTIAL {noun} — {reason} It is published as provisional and excluded from the "
                "first-to-last movement, the high and the low; a series that terminates on a "
                "partial bucket reports the calendar, not the business."
            ),
        )

    denominator = f"{shape.measure}{_DENOMINATOR_SUFFIX}"
    if denominator not in schema.names:
        return None
    idx_den = schema.index_of(denominator)
    populations = [
        value for row in rows if (value := _as_int(row[idx_den])) is not None and value > 0
    ]
    if len(populations) < 3:
        return None
    terminal_population = _as_int(terminal[idx_den])
    if terminal_population is None or terminal_population <= 0:
        return None
    median = _median(populations[:-1])
    if median <= 0 or Decimal(terminal_population) >= TERMINAL_BUCKET_MIN_SHARE * Decimal(median):
        return None
    share = Decimal(terminal_population) / Decimal(median)
    reason = (
        f"it was computed over {terminal_population:,} adjudicated records against a series "
        f"median of {median:,} ({ratio_pct(share)} of it), so the {noun} is still settling and "
        "the records that have settled are not a random sample of it."
    )
    return TerminalCensoring(
        bucket=bucket_label,
        population=terminal_population,
        median_population=median,
        reason=reason,
        warning=(
            f"adjudication_incomplete: the last point of this series ({bucket_label}) is "
            f"RIGHT-CENSORED — {reason} It is published as provisional and excluded from the "
            "first-to-last movement, the high and the low. A rise that terminates on an "
            "incompletely adjudicated bucket is a data-maturity artifact until that bucket "
            "matures."
        ),
    )


def find_trend_shapes(
    plan: InvestigationPlan, calculation: CalculationResult
) -> tuple[TrendShape, ...]:
    """Every ungrouped multi-row series this plan produced, in plan order."""
    shapes: list[TrendShape] = []
    for node in plan.nodes:
        if node.id.endswith(_PRIOR_SUFFIX):
            continue
        try:
            frame = calculation.frame(node.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        bucket = _time_bucket_column(frame)
        if bucket is None or _dimension_columns(frame) or len(frame.rows) < 2:
            continue
        for column in frame.schema.columns:
            if not isinstance(column.ref, MetricRef) or "__" in column.name:
                continue
            shapes.append(
                TrendShape(
                    frame_id=node.id,
                    frame=frame,
                    bucket_column=bucket,
                    measure=column.name,
                    unit=column.unit,
                )
            )
    return tuple(shapes)


def _unit_of(frame: EvidenceFrame, name: str) -> str | None:
    for col in frame.schema.columns:
        if col.name == name:
            return col.unit
    return None


def _base_measure(frame: EvidenceFrame, ranked_by: str) -> str | None:
    """The undecorated metric column behind a rank arg (``x__delta`` → ``x``)."""
    names = set(frame.schema.names)
    candidate = ranked_by.split("__", 1)[0]
    if candidate not in names:
        return None
    for col in frame.schema.columns:
        if col.name == candidate and isinstance(col.ref, MetricRef):
            return candidate
    return None


def _money_measure(frame: EvidenceFrame) -> str | None:
    names = set(frame.schema.names)
    for col in frame.schema.columns:
        if (
            isinstance(col.ref, MetricRef)
            and col.unit == _MONEY_UNIT
            and "__" not in col.name
            and f"{col.name}__delta" in names
        ):
            return col.name
    return None


def _as_int(value: Scalar) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_number(value: Scalar) -> Decimal | None:
    """Any numeric cell as a Decimal, or ``None`` when there is no number.

    Movement selection used to read deltas through :func:`_as_int`, which
    is correct for money (integer cents) and silently wrong for everything
    else: a ratio delta is a ``Decimal``, so every row of a compared *rate*
    read as "no movement" and sorted into the NULL bucket. Ordering a
    frame by a value the ordering cannot see is how a rate comparison came
    back with nothing to say.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return Decimal(value)


def _direction(delta: Scalar) -> str | None:
    """"up from" / "down from" / "unchanged from", or ``None`` when the
    delta is not a number and there is therefore no movement to name.

    Read off the delta in whatever numeric type the operator produced —
    money deltas are integer cents, ratio deltas are ``Decimal``. Deciding
    direction from an int-only coercion published a *rate* that rose 1.0
    point as "unchanged", which is the one thing a headline must never be.
    """
    if delta is None or isinstance(delta, bool) or not isinstance(delta, (int, Decimal)):
        return None
    if delta == 0:
        return "unchanged from"
    return "down from" if delta < 0 else "up from"


@dataclass(frozen=True, slots=True)
class PremiseCheck:
    """Whether the movement a question STATED actually happened.

    The aggregate the premise probe measured, and the verdict. ``holds`` is
    false when the aggregate moved the other way (or not at all) — the case
    where every cell-level number can be correct and the answer still
    false, because the question's premise was never checked.
    """

    frame_id: str
    frame: EvidenceFrame
    measure: str
    unit: str | None
    current: Scalar
    prior: Scalar
    delta: Decimal
    pct: Scalar
    holds: bool
    #: The size the question asserted, when it asserted one (2 for
    #: "doubled"). Carried so the verdict sentence can say what was claimed
    #: as well as what happened.
    asserted_multiple: Decimal | None = None
    #: Set when the direction matched and the SIZE did not — the case that
    #: used to score as holding and publish nothing (R3-03).
    magnitude_short: bool = False

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


def verify_premise(
    plan: InvestigationPlan,
    calculation: CalculationResult,
    spec: AnalysisSpec,
    pack: PackPort,
    *,
    premise_prefix: str,
) -> PremiseCheck | None:
    """Check the asserted aggregate movement, before anything explains it.

    Returns ``None`` when the question asserted nothing (the overwhelming
    majority of turns), or when the premise probe produced no comparable
    aggregate — an unverifiable premise is not a refuted one, and claiming
    otherwise would be the same failure in the opposite direction.
    """
    if not spec.direction_asserted or spec.direction is None:
        return None
    for step in plan.transforms.steps:
        if step.operator != "compare" or not step.inputs:
            continue
        if not step.inputs[0].startswith(premise_prefix):
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        if _dimension_columns(frame) or len(frame.rows) != 1:
            continue
        compared = _compared_measures(frame)
        if not compared:
            continue
        measure = next(
            (name for name in compared if _unit_of(frame, name) == _MONEY_UNIT), compared[0]
        )
        row = frame.rows[0]
        delta = _as_number(row[frame.schema.index_of(f"{measure}__delta")])
        if delta is None:
            continue
        contract = pack.metric(measure)
        sign = contract.sign if contract is not None else SignConvention.NEUTRAL
        wanted = wanted_delta_sign(spec.direction, sign)
        if wanted is None:
            continue
        pct_col = f"{measure}__pct_change"
        current = row[frame.schema.index_of(measure)]
        prior = row[frame.schema.index_of(f"{measure}__prior")]
        directional = (delta > 0) if wanted > 0 else (delta < 0)
        # Direction is necessary and not sufficient. "Doubled" asserts a
        # SIZE, and +4.2% is not it: the movement has to reach a governed
        # fraction of what was claimed before the claim is confirmed.
        short = False
        if directional and spec.asserted_multiple is not None:
            short = _magnitude_short(prior, current, spec.asserted_multiple)
        return PremiseCheck(
            frame_id=step.id,
            frame=frame,
            measure=measure,
            unit=_unit_of(frame, measure),
            current=current,
            prior=prior,
            delta=delta,
            pct=row[frame.schema.index_of(pct_col)] if pct_col in frame.schema.names else None,
            holds=directional and not short,
            asserted_multiple=spec.asserted_multiple,
            magnitude_short=short,
        )
    return None


def _magnitude_short(prior: Scalar, current: Scalar, asserted: Decimal) -> bool:
    """Did the movement fall short of the size the question asserted?

    Compared as *changes* rather than as levels, so the same rule reads
    "doubled" (asserted change +1.0) and "halved" (asserted change -0.5).
    An unmeasurable base (a zero or suppressed prior) refutes nothing: an
    unverifiable premise is not a false one.
    """
    prior_value = _as_number(prior)
    current_value = _as_number(current)
    if prior_value is None or current_value is None or prior_value == 0:
        return False
    actual = Decimal(current_value) / Decimal(prior_value)
    asserted_change = abs(asserted - Decimal(1))
    if asserted_change == 0:
        return False
    return abs(actual - Decimal(1)) < PREMISE_MAGNITUDE_TOLERANCE * asserted_change


def _bucket_noun(frame: EvidenceFrame, column: str) -> str:
    """"month" / "week" / "day", read off the frame's own time-axis ref."""
    for col in frame.schema.columns:
        if col.name == column and isinstance(col.ref, DimensionRef):
            return col.ref.id.removeprefix(_TIME_BUCKET_PREFIX)
    return "period"


def _bucket_text(value: Scalar, noun: str) -> str:
    """One bucket, named the way the bucket is ("2026-02", "week of …").

    A monthly series whose points read "2026-02-01" is stating a day where
    it means a month, which is the same class of imprecision the rest of
    this module exists to avoid — and the first day of a month is exactly
    the value a reader would misread as the measurement date.
    """
    text = str(value)
    if noun == "month" and len(text) >= 7 and text[4] == "-":
        return text[:7]
    if noun == "week":
        return f"week of {text}"
    return text


def _premise_sentence(premise: PremiseCheck, phrase: str) -> str:
    """What actually happened to the aggregate, in the contract's own unit."""
    label = metric_label(premise.measure)
    moved = "fell" if premise.delta < 0 else ("rose" if premise.delta > 0 else "did not move")
    amount = magnitude(premise.delta, premise.unit)
    pct = f" ({ratio_pct(premise.pct)})" if isinstance(premise.pct, Decimal) else ""
    return (
        f"{label} {moved} {amount}{pct} {phrase} — from "
        f"{format_value(premise.prior, premise.unit)} to "
        f"{format_value(premise.current, premise.unit)}"
    )


#: How a multiple reads in English. A closed table, because the sentence
#: that refutes a question has to use the question's own word for the size
#: it asserted ("they did not DOUBLE"), and inventing one is how a
#: correction stops being recognisable as an answer.
_MULTIPLE_WORDS: tuple[tuple[Decimal, str, str], ...] = (
    (Decimal(2), "a doubling", "double"),
    (Decimal(3), "a tripling", "triple"),
    (Decimal(4), "a quadrupling", "quadruple"),
    (Decimal("0.5"), "a halving", "halve"),
)


def _asserted_claim(spec: AnalysisSpec) -> tuple[str, str]:
    """``(noun phrase, verb)`` for the movement the question asserted."""
    assert spec.direction is not None
    multiple = spec.asserted_multiple
    if multiple is not None:
        for value, noun, verb in _MULTIPLE_WORDS:
            if value == multiple:
                return noun, verb
        return f"a {multiple.normalize()}x movement", f"move {multiple.normalize()}x"
    noun = f"a{'n' if spec.direction.value[0] in 'aeiou' else ''} {spec.direction.value}"
    return noun, spec.direction.value


def premise_verdict_sentence(
    premise: PremiseCheck, spec: AnalysisSpec, *, comparison: ComparisonRendering | None
) -> str:
    """What the question assumed, and what the aggregate did — deterministic.

    Round-3 R3-03. This sentence is composed here, from the premise probe's
    own figures and the interpretation's closed ``direction`` set, and never
    by a model: it is the answer's first claim on every turn that states a
    movement, and a first claim a composer may decline to write is not a
    first claim. No phrasing of the original question appears in it,
    because none of it was parsed.
    """
    noun, verb = _asserted_claim(spec)
    label = metric_label(premise.measure)
    phrase = comparison.phrase if comparison is not None else "vs the prior period"
    figures = (
        f"{format_value(premise.prior, premise.unit)} → "
        f"{format_value(premise.current, premise.unit)}"
    )
    pct = f", {ratio_pct(premise.pct)}" if isinstance(premise.pct, Decimal) else ""
    if premise.holds:
        return (
            f"You asked about {noun} in {label}. It happened: {figures}{pct} {phrase}"
        )
    if premise.magnitude_short:
        return (
            f"You asked about {noun} in {label}. It did not {verb}: {figures}{pct} {phrase}"
        )
    moved = "fell" if premise.delta < 0 else ("rose" if premise.delta > 0 else "did not move")
    return (
        f"You asked about {noun} in {label}. It did not happen — {label} {moved} "
        f"{magnitude(premise.delta, premise.unit)}{pct} {phrase}: {figures}"
    )


def _premise_warning(
    premise: PremiseCheck, spec: AnalysisSpec, *, comparison: ComparisonRendering | None
) -> str:
    """The correction a false premise owes the reader, said first.

    Generic by construction: the movement that was asserted comes from the
    interpretation's closed ``direction`` set, and what actually happened
    comes from the aggregate the premise probe measured. No phrasing of the
    original question appears here, because none of it was parsed.
    """
    assert spec.direction is not None
    sentence = premise_verdict_sentence(premise, spec, comparison=comparison)
    tail = (
        "The question takes that size as given and the aggregate did not reach it."
        if premise.magnitude_short
        else "The question takes that movement as given, and over this window there was none."
    )
    return (
        f"premise_false: {sentence}. {tail} What follows describes the cells that did move that "
        "way; it is context for a movement that did not happen at the level asked about, not "
        "confirmation of it."
    )


def _premise_verified_warning(
    premise: PremiseCheck, spec: AnalysisSpec, *, comparison: ComparisonRendering | None
) -> str:
    """The verdict a *confirmed* premise owes the reader, said first.

    The other half of R3-03. A premise probe ran on every turn that states
    a movement and its verdict was published only when it failed, so
    "why did denials double?" over a real +4.2% opened on a 243% sub-cell
    and the aggregate the platform had already measured was discarded. A
    verdict is a verdict either way.
    """
    sentence = premise_verdict_sentence(premise, spec, comparison=comparison)
    return (
        f"premise_verified: {sentence}. The movement below is read against that aggregate, "
        "which is the level the question asked about."
    )


class EvaluateFindingsService:
    def __init__(self, registry: ReferentRegistryStore, *, top_n: int = 3) -> None:
        self._registry = registry
        self._top_n = top_n
        #: The §15 threshold of the turn being evaluated, set per call. A
        #: bound is only recognisable against the threshold that produced
        #: it, and the evaluator has no catalog of its own.
        self._threshold: int | None = None

    def _limit(self, spec: AnalysisSpec) -> int:
        """How many findings this turn may publish.

        Round-3 R3-04: ``top_n`` was a constructor default applied at four
        call sites, and ``Expand(limit=12)`` — parsed perfectly from "show
        me all twelve payers, not just three" — set ``spec.limit`` that
        nothing read. The same three findings came back for $0.0919 and
        33.8 seconds. The analyst's own limit is not a suggestion.
        """
        return spec.limit if spec.limit is not None and spec.limit > 0 else self._top_n

    def _bounds(self, frame: EvidenceFrame) -> dict[int, dict[str, BoundedCell]]:
        """Which of this frame's cells carry a ceiling instead of a value."""
        if self._threshold is None:
            return {}
        return bound_index(frame, self._threshold)

    def _census(self, frame: EvidenceFrame) -> SuppressionCensus | None:
        if self._threshold is None:
            return None
        return suppression_census(frame, self._threshold)

    async def evaluate(
        self,
        *,
        plan: InvestigationPlan,
        calculation: CalculationResult,
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
        suppression_threshold: int | None = None,
    ) -> FindingsResult:
        self._threshold = suppression_threshold
        # The premise first, always: a question that STATES a movement is
        # answered honestly only once that movement has been measured.
        premise = verify_premise(
            plan, calculation, spec, pack, premise_prefix=_PREMISE_PREFIX
        )
        # The verdict is published on EVERY premise turn, holds or not, and
        # it is published FIRST — registered before any other shape reads
        # the registry, so the aggregate the question assumed is F1 and the
        # cells that explain it are F2 onward (R3-03).
        premise_lead: tuple[Finding, RegisteredReferent, str] | None = None
        if premise is not None:
            premise_lead = await self._publish_premise(
                premise, spec, pack, session_id, investigation_id
            )

        shape = find_primary_movement(plan, calculation)
        if shape is None:
            concentration = find_primary_concentration(plan, calculation)
            if concentration is not None:
                return _with_premise(
                    await self._evaluate_concentration(
                        shape=concentration,
                        spec=spec,
                        plan=plan,
                        pack=pack,
                        playbook=playbook,
                        session_id=session_id,
                        investigation_id=investigation_id,
                    ),
                    premise_lead,
                )
            trends = find_trend_shapes(plan, calculation)
            if trends:
                return _with_premise(
                    await self._evaluate_trends(
                        shapes=trends,
                        spec=spec,
                        pack=pack,
                        playbook=playbook,
                        session_id=session_id,
                        investigation_id=investigation_id,
                    ),
                    premise_lead,
                )
            scalars = find_scalar_shapes(plan, calculation)
            if not scalars:
                # Frames exist and no shape could publish from them: the
                # turn read data and has nothing to say about it. Which of
                # the two nothings this is, said as data.
                return _with_premise(
                    FindingsResult(
                        findings=(),
                        referents=(),
                        emptiness=self._no_findings(plan, calculation, "no publishable shape"),
                    ),
                    premise_lead,
                )
            return _with_premise(
                await self._evaluate_scalars(
                    shapes=scalars,
                    spec=spec,
                    pack=pack,
                    playbook=playbook,
                    session_id=session_id,
                    investigation_id=investigation_id,
                ),
                premise_lead,
            )

        delta_col = f"{shape.measure}__delta"
        idx_delta = shape.frame.schema.index_of(delta_col)
        rows, selection_warnings = self._select_directional(
            shape.frame.rows, idx_delta, spec, pack, shape.measure
        )

        qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
        comparison = render_comparison(spec)

        # Referent handles are session-monotonic (design §7.6): F2 keeps
        # meaning the finding it named when it was shown — later turns mint
        # new handles instead of overwriting old ones.
        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(
            1 for entry in existing if entry.referent.kind is ReferentKind.FINDING
        )
        row_offset = sum(
            1 for entry in existing if entry.referent.kind is ReferentKind.DIMENSION_VALUE
        )

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        limit = self._limit(spec)
        eligible = [row for row in rows if _as_number(row[idx_delta]) is not None]
        bounds = self._bounds(shape.frame)
        row_positions = {id(row): i for i, row in enumerate(shape.frame.rows)}
        for row in eligible[:limit]:
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_finding(
                f"F{n}",
                row,
                shape,
                spec,
                comparison,
                qualified,
                pack,
                session_id,
                investigation_id,
                bound=bounds.get(row_positions.get(id(row), -1), {}).get(shape.measure),
            )
            findings.append(finding)
            referents.append(referent)
        truncation = _truncation_warning(len(findings), len(eligible), spec)
        if truncation is not None:
            selection_warnings = (*selection_warnings, truncation)

        for i, row in enumerate(shape.frame.rows):
            referents.append(
                self._dimension_value_referent(
                    f"D{row_offset + i + 1}",
                    row,
                    shape.frame,
                    shape.dimension_columns,
                    spec,
                    pack,
                    session_id,
                    investigation_id,
                )
            )

        await self._registry.register(tuple(referents))
        # The cells below moved the way the question assumed; whether the
        # population they sit in did is the verdict, and it leads either
        # way — as a correction when it refutes the question, as the
        # measured aggregate when it confirms it.
        return _with_premise(
            FindingsResult(
                findings=tuple(findings),
                referents=tuple(referents),
                warnings=selection_warnings,
                emptiness=(
                    None
                    if findings
                    else EmptinessFact(
                        kind=EmptinessKind.NO_FINDINGS,
                        frame_id=shape.frame_id,
                        detail=(
                            f"{len(shape.frame.rows)} compared row(s) on {shape.measure!r}, and "
                            "none carried a movement that could be published (every delta was "
                            "suppressed or filtered out by the asked direction)"
                        ),
                    )
                ),
            ),
            premise_lead,
        )

    @staticmethod
    def _no_findings(
        plan: InvestigationPlan, calculation: CalculationResult, why: str
    ) -> EmptinessFact:
        """The emptiness fact for a turn whose frames could publish nothing.

        Names the frame that was looked at, so a reader can go and see the
        rows the answer declined to conclude from.
        """
        candidate = next(
            (
                frame_id
                for frame_id, frame in calculation.frames
                if frame.rows and _dimension_columns(frame)
            ),
            None,
        )
        rows = sum(len(frame.rows) for _, frame in calculation.frames)
        return EmptinessFact(
            kind=EmptinessKind.NO_FINDINGS,
            frame_id=candidate,
            detail=(
                f"{rows} row(s) were retrieved across {len(calculation.frames)} frame(s) and "
                f"no finding could be published from them ({why})"
            ),
        )

    # ---------------------------------------------------------- direction

    def _select_directional(
        self,
        rows: tuple[tuple[Scalar, ...], ...],
        idx_delta: int,
        spec: AnalysisSpec,
        pack: PackPort,
        money_measure: str,
    ) -> tuple[list[tuple[Scalar, ...]], tuple[str, ...]]:
        """Order (and, when a direction was asked, restrict) the compare rows.

        Without a direction this is the old rule: rank by delta ascending,
        biggest declines of a higher-is-good measure first — the right
        default for "what moved?".

        With one it is not a default any more, it is the question. Live,
        "which payers had the biggest INCREASE in denials" ran the default
        and published the three biggest *decreases*, narrated as
        improvements: a confident, well-evidenced, exactly-backwards answer.
        So rows whose delta has the wrong sign are not eligible to be the
        answer at all, and the remaining ones are ordered by the extremity
        the analyst phrased.

        When nothing moved the asked-for way the answer says that FIRST and
        then shows the opposite as context — the honest shape of an empty
        direction-matched set. Rows with a NULL delta are never eligible
        either way: a suppressed movement is not a movement.
        """
        contract = pack.metric(money_measure)
        sign = contract.sign if contract is not None else SignConvention.NEUTRAL
        wanted = wanted_delta_sign(spec.direction, sign)
        biggest_first = spec.magnitude is not AskedMagnitude.SMALLEST

        def ordered(
            candidates: list[tuple[Scalar, ...]], descending: bool
        ) -> list[tuple[Scalar, ...]]:
            return sorted(
                candidates,
                key=lambda row: (
                    _as_number(row[idx_delta]) is None,  # NULL deltas last
                    -(_as_number(row[idx_delta]) or 0)
                    if descending
                    else (_as_number(row[idx_delta]) or 0),
                ),
            )

        if wanted is None:
            # No direction was asked. An ORDER may still have been ("best to
            # worst"), and it wins: it is the analyst's own instruction about
            # which end to show first, resolved against the metric's sign.
            asked_order = descending_for_order(spec.order, sign)
            if asked_order is not None:
                return ordered(list(rows), descending=asked_order), ()
            # Otherwise the default is not "ascending" — it is *worst
            # first*, read off the contract's own sign convention: a
            # higher-is-bad measure's worst movement is a rise. Ascending
            # was only ever right because the first metrics through here
            # were higher-is-good dollars, and it published the biggest
            # improvements of a higher-is-bad metric as its headline.
            adverse = adverse_delta_sign(sign)
            return ordered(list(rows), descending=adverse is not None and adverse > 0), ()

        matched = [
            row
            for row in rows
            if (value := _as_number(row[idx_delta])) is not None
            and (value > 0 if wanted > 0 else value < 0)
        ]
        assert spec.direction is not None
        movement = "rose" if wanted > 0 else "fell"
        if matched:
            return ordered(matched, descending=(wanted > 0) == biggest_first), ()
        warning = (
            f"direction_unmatched: nothing {movement} — no cell's "
            f"{metric_label(money_measure)} moved the way {spec.direction.value!r} asks about "
            "over this window. The movements below are the opposite direction, shown as "
            "context, not as an answer to what was asked."
        )
        return ordered(list(rows), descending=not (wanted > 0)), (warning,)

    # -------------------------------------------------------------- premise

    async def _publish_premise(
        self,
        premise: PremiseCheck,
        spec: AnalysisSpec,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
    ) -> tuple[Finding, RegisteredReferent, str]:
        """Register the premise verdict as this turn's first finding.

        Registered here, before any shape reads the registry, so the
        aggregate the question assumed always takes F1 and every other
        shape numbers itself after it — the alternative was threading a
        "reserve one handle" flag through four independent branches.
        """
        comparison = render_comparison(spec)
        existing = await self._registry.list_for_session(session_id)
        offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)
        finding, referent = self._build_premise_finding(
            f"F{offset + 1}", premise, spec, comparison, pack, session_id, investigation_id
        )
        await self._registry.register((referent,))
        warning = (
            _premise_verified_warning(premise, spec, comparison=comparison)
            if premise.holds
            else _premise_warning(premise, spec, comparison=comparison)
        )
        return finding, referent, warning

    def _build_premise_finding(
        self,
        referent_value: str,
        premise: PremiseCheck,
        spec: AnalysisSpec,
        comparison: ComparisonRendering | None,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
    ) -> tuple[Finding, RegisteredReferent]:
        """The refutation as a first-class finding, not a footnote.

        It carries the same certified values every other finding does —
        level, prior, delta, pct — because the reader's next question is
        "by how much, then?", and a correction that cannot answer that is
        just a contradiction.
        """
        assert spec.direction is not None
        sentence = premise_verdict_sentence(premise, spec, comparison=comparison)
        if premise.holds:
            title = f"Premise confirmed: {sentence}"
            statement = (
                f"{sentence}. That is the movement the question takes as given, measured on the "
                "population it names, so the cells below are its composition rather than a "
                "separate claim."
            )
        else:
            title = f"Premise not supported: {sentence}"
            statement = (
                f"{sentence}. The population the question names does not show the movement it "
                "assumes, so the movements below are the exceptions inside it rather than the "
                "story."
            )
        values: list[tuple[str, Scalar]] = [
            (premise.measure, premise.current),
            (f"{premise.measure}__prior", premise.prior),
            (
                f"{premise.measure}__delta",
                int(premise.delta) if premise.is_money else premise.delta,
            ),
            ("pct_change", premise.pct),
            # The verdict as data, so a client never has to read the title
            # to know whether the question's own assumption survived.
            ("premise_holds", premise.holds),
        ]
        if premise.asserted_multiple is not None:
            values.append(("asserted_multiple", premise.asserted_multiple))
        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(premise.measure),),
            values=tuple(values),
            grade=premise.frame.evidence_grade,
            # A refutation is not a recoverable opportunity: ranking it in a
            # worklist would put "this did not happen" on somebody's queue.
            impact_cents=None,
            confidence="high",
            suggested_refinements=suggested_refinements_for(referent_value),
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            cohort_definition=self._cohort_definition(
                premise.frame.rows[0], premise.frame, (), spec
            ),
            finding=finding,
        )
        return finding, registered

    # ------------------------------------------------------------- building

    def _requires_qualification(
        self, grade: EvidenceGrade, pack: PackPort, playbook: PlaybookSpec | None
    ) -> bool:
        if grade in _QUALIFIED_GRADES:
            return True
        if playbook is not None:
            for policy_id in playbook.conclusion_policies:
                policy = pack.conclusion_policy(policy_id)
                if policy is not None and grade.strength < policy.required_grade.strength:
                    return True
        return False

    def _cohort_definition(
        self,
        row: tuple[Scalar, ...],
        frame: EvidenceFrame,
        dimension_columns: tuple[str, ...],
        spec: AnalysisSpec,
    ) -> CohortDefinition:
        """Drillable cohort: CLAIM entity, current scope narrowed to this
        row's dimension values, over the analysis window."""
        predicates = tuple(
            Predicate(DimensionRef(dim), PredicateOp.EQ, (row[frame.schema.index_of(dim)],))
            for dim in dimension_columns
        )
        return CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=and_merge(spec.context.effective_scope(), *predicates),
            window=spec.context.window,
        )

    @staticmethod
    def _row_label(
        row: tuple[Scalar, ...],
        frame: EvidenceFrame,
        dimension_columns: tuple[str, ...],
        pack: PackPort,
    ) -> str:
        """Dimension values as a label, with remittance codes rendered as
        ``GROUP / CARC — Title`` rather than as bare integers."""
        values = {dim: row[frame.schema.index_of(dim)] for dim in dimension_columns}
        return render_row_label(pack, dimension_columns, values)

    @staticmethod
    def _single_dim(
        row: tuple[Scalar, ...], frame: EvidenceFrame, dimension_columns: tuple[str, ...]
    ) -> tuple[str, str] | None:
        if len(dimension_columns) != 1:
            return None
        name = dimension_columns[0]
        return (name, str(row[frame.schema.index_of(name)]))

    def _build_finding(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        shape: MovementShape,
        spec: AnalysisSpec,
        comparison: ComparisonRendering | None,
        qualified: bool,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
        bound: BoundedCell | None = None,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        measure = shape.measure
        current = row[schema.index_of(measure)]
        prior = row[schema.index_of(f"{measure}__prior")]
        delta = _as_number(row[schema.index_of(f"{measure}__delta")])
        pct = row[schema.index_of(f"{measure}__pct_change")]
        assert delta is not None  # caller filtered NULL deltas

        label = self._row_label(row, shape.frame, shape.dimension_columns, pack)
        measure_label = metric_label(measure)
        direction = "down" if delta < 0 else "up"
        # In the contract's own unit: dollars for money, percentage POINTS
        # for a rate. Rendering a rate's movement through the money path is
        # how "denial rate up $0.01" gets published.
        amount = magnitude(delta, shape.unit)
        period_phrase = comparison.phrase if comparison is not None else "vs prior period"
        # A comparison over a *materially* different-length window is not a
        # delta the platform will stand behind for an additive measure: the
        # phrase says so, the impact is withheld, and the confidence is
        # qualified. A rate is length-invariant and carries no such caveat
        # (see comparison.py).
        mismatched = (
            comparison is not None
            and comparison.material_length_mismatch
            and _is_additive(shape.unit)
        )
        current_text = bound_text(current, shape.unit, bounded=bound is not None)
        title = f"{label} {measure_label} {direction} {amount} {period_phrase}"
        pct_text = ratio_pct(pct) if isinstance(pct, Decimal) else "n/a"
        statement = (
            f"{label}: {measure_label} moved from {format_value(prior, shape.unit)} to "
            f"{current_text} "
            f"({direction} {amount}, {pct_text} {period_phrase})."
        )
        if bound is not None:
            # A movement computed off a bounded endpoint is a movement
            # toward a ceiling, not a measured one. Said in the title as
            # well as the statement: the title is what gets screenshotted.
            title = f"{label} {measure_label} at most {current_text} {period_phrase}"
            statement = (
                f"{statement[:-1]}. The current side is an UPPER BOUND: its numerator was "
                f"suppressed over a population of {bound.population:,}, so the movement is at "
                "most this large and may be smaller."
            )

        delta_value: Scalar = int(delta) if shape.is_money else delta
        values: list[tuple[str, Scalar]] = [
            ("current_cents" if shape.is_money else measure, current),
            ("prior_cents" if shape.is_money else f"{measure}__prior", prior),
            ("delta_cents" if shape.is_money else f"{measure}__delta", delta_value),
            ("pct_change", pct),
        ]
        values.extend(_bound_values(measure, bound))
        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # A rate is not dollars: an impact is a figure this platform is
            # willing to rank, sum and put in a worklist, and a percentage
            # point is none of those. Nor is a bounded movement: a ceiling
            # is not a recoverable dollar figure.
            impact_cents=(
                int(delta) if (shape.is_money and not mismatched and bound is None) else None
            ),
            confidence=(
                "qualified" if (qualified or mismatched or bound is not None) else "high"
            ),
            suggested_refinements=suggested_refinements_for(referent_value),
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            cohort_definition=self._cohort_definition(
                row, shape.frame, shape.dimension_columns, spec
            ),
            finding=finding,
            dimension_value=self._single_dim(row, shape.frame, shape.dimension_columns),
        )
        return finding, registered

    def _dimension_value_referent(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        frame: EvidenceFrame,
        dimension_columns: tuple[str, ...],
        spec: AnalysisSpec,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
    ) -> RegisteredReferent:
        return RegisteredReferent(
            referent=ReferentId(value=referent_value, kind=ReferentKind.DIMENSION_VALUE),
            session_id=session_id,
            investigation_id=investigation_id,
            label=self._row_label(row, frame, dimension_columns, pack),
            cohort_definition=self._cohort_definition(row, frame, dimension_columns, spec),
            dimension_value=self._single_dim(row, frame, dimension_columns),
        )

    # --------------------------------------------------------- scalar shape

    async def _evaluate_scalars(
        self,
        *,
        shapes: tuple[ScalarShape, ...],
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
    ) -> FindingsResult:
        """Findings from ungrouped metric cells — the direct answer."""
        comparison = render_comparison(spec)
        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        for shape in shapes[: self._limit(spec)]:
            row = shape.frame.rows[0]
            value = row[shape.frame.schema.index_of(shape.measure)]
            # A suppressed cell has no level to publish. Saying so is the
            # frame's job (suppressed_cells) and the warning's; a finding
            # titled "net collection rate: suppressed" would be a headline
            # asserting a measurement that was withheld.
            if value is None:
                continue
            qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_scalar_finding(
                f"F{n}",
                shape,
                value,
                spec,
                comparison,
                qualified,
                pack,
                session_id,
                investigation_id,
                bound=self._bounds(shape.frame).get(0, {}).get(shape.measure),
            )
            findings.append(finding)
            referents.append(referent)

        await self._registry.register(tuple(referents))
        return FindingsResult(
            findings=tuple(findings),
            referents=tuple(referents),
            emptiness=(
                None
                if findings
                else EmptinessFact(
                    kind=EmptinessKind.NO_FINDINGS,
                    frame_id=shapes[0].frame_id,
                    detail="every scalar cell this turn produced was suppressed",
                )
            ),
        )

    def _build_scalar_finding(
        self,
        referent_value: str,
        shape: ScalarShape,
        value: Scalar,
        spec: AnalysisSpec,
        comparison: ComparisonRendering | None,
        qualified: bool,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
        bound: BoundedCell | None = None,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        row = shape.frame.rows[0]
        label = metric_label(shape.measure)
        current_text = bound_text(value, shape.unit, bounded=bound is not None)
        period_text = _period_phrase(spec, pack, shape.measure, shape.frame)
        period_paren = _period_paren(spec, pack, shape.measure, shape.frame)

        values: list[tuple[str, Scalar]] = [(shape.measure, value)]
        prior: Scalar = None
        delta: Scalar = None
        pct: Scalar = None
        if shape.compared:
            assert shape.prior_column is not None and shape.delta_column is not None
            prior = row[schema.index_of(shape.prior_column)]
            delta = row[schema.index_of(shape.delta_column)]
            pct = row[schema.index_of(shape.pct_column)] if shape.pct_column else None
            values.extend(
                [(f"{shape.measure}__prior", prior), (f"{shape.measure}__delta", delta)]
            )
            if pct is not None:
                values.append(("pct_change", pct))

        # Same per-unit rule as the movement path: only an additive measure
        # is distorted by a length mismatch, and only a material one.
        mismatched = (
            comparison is not None
            and comparison.material_length_mismatch
            and _is_additive(shape.unit)
        )
        movement = _direction(delta)
        # Both sides are stated in the contract's unit rather than the delta
        # being rendered in it: "up 3.2%" on a *rate* is ambiguous between
        # relative change and percentage points, and there is no reason to
        # publish an ambiguity when "5.2%, up from 4.9%" is available. A
        # delta that is not a number (a suppressed prior cell) publishes the
        # level alone — there is no movement to name.
        if prior is not None and movement is not None:
            period_phrase = comparison.phrase if comparison is not None else "vs prior period"
            prior_text = format_value(prior, shape.unit)
            title = f"{label}: {current_text}, {movement} {prior_text} {period_phrase}"
            pct_text = f" ({ratio_pct(pct)} change)" if isinstance(pct, Decimal) else ""
            statement = (
                f"{label} is {current_text} {period_text}, {movement} {prior_text} "
                f"{period_phrase}{pct_text}."
            )
        else:
            title = f"{label}: {current_text} {period_paren}"
            statement = f"{label} is {current_text} {period_text}."
        if bound is not None:
            statement = (
                f"{statement[:-1]} — an UPPER BOUND, not a measurement: the numerator was "
                f"suppressed over a population of {bound.population:,}, so the true figure is "
                "at or below this one."
            )
        values.extend(_bound_values(shape.measure, bound))

        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(shape.measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # A rate is not dollars, and a length-mismatched difference is
            # not an impact — the same two rules the other shapes apply.
            impact_cents=(
                _as_int(delta)
                if (shape.is_money and not mismatched and bound is None)
                else None
            ),
            confidence=(
                "qualified" if (qualified or mismatched or bound is not None) else "high"
            ),
            suggested_refinements=suggested_refinements_for(referent_value),
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            # No dimension values to pin: the drillable cohort is the
            # answer's own population over the analysis window.
            cohort_definition=self._cohort_definition(row, shape.frame, (), spec),
            finding=finding,
        )
        return finding, registered

    # ---------------------------------------------------------- trend shape

    async def _evaluate_trends(
        self,
        *,
        shapes: tuple[TrendShape, ...],
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
    ) -> FindingsResult:
        """Findings from an ungrouped series — the "by month" answer."""
        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        warnings: list[str] = []
        for shape in shapes[: self._limit(spec)]:
            censoring = terminal_bucket_censoring(shape, spec)
            finding = self._build_trend_finding(
                f"F{finding_offset + len(findings) + 1}",
                shape,
                spec,
                pack,
                playbook,
                censoring=censoring,
            )
            if finding is None:
                continue
            if censoring is not None:
                warnings.append(censoring.warning)
            findings.append(finding)
            referents.append(
                RegisteredReferent(
                    referent=finding.referent,
                    session_id=session_id,
                    investigation_id=investigation_id,
                    label=finding.title,
                    cohort_definition=self._cohort_definition(
                        shape.frame.rows[0], shape.frame, (), spec
                    ),
                    finding=finding,
                )
            )

        await self._registry.register(tuple(referents))
        return FindingsResult(
            findings=tuple(findings),
            referents=tuple(referents),
            warnings=tuple(dict.fromkeys(warnings)),
            emptiness=(
                None
                if findings
                else EmptinessFact(
                    kind=EmptinessKind.NO_FINDINGS,
                    frame_id=shapes[0].frame_id,
                    detail="every bucket in this series was suppressed or empty",
                )
            ),
        )

    def _build_trend_finding(
        self,
        referent_value: str,
        shape: TrendShape,
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        censoring: TerminalCensoring | None = None,
    ) -> Finding | None:
        """One series, stated as a series: ends first, then its extremes.

        A right-censored terminal bucket never becomes the "end" (round-3
        R3-06). "Denial rate by month for 2026 so far" published
        ``7.3% → 12.8% (up 5.5 points)`` at grade ``direct``, confidence
        ``high``, with a benchmark attached, over a July point computed on
        22.9% of July's claims — the fastest-adjudicating subset, which
        skews heavily to denials. The series is stated to its last SETTLED
        bucket and the provisional point is named as provisional.
        """
        schema = shape.frame.schema
        idx_bucket = schema.index_of(shape.bucket_column)
        idx_value = schema.index_of(shape.measure)
        points = [
            (row[idx_bucket], value)
            for row in shape.frame.rows
            if (value := _as_number(row[idx_value])) is not None
        ]
        if len(points) < 2:
            return None  # a series of one is not a trend, and nor is silence
        points.sort(key=lambda point: str(point[0]))
        provisional = points[-1] if censoring is not None else None
        settled = points[:-1] if (censoring is not None and len(points) > 2) else points
        (first_bucket, first_value) = settled[0]
        (last_bucket, last_value) = settled[-1]
        low = min(settled, key=lambda point: point[1])
        high = max(settled, key=lambda point: point[1])
        delta = last_value - first_value
        label = metric_label(shape.measure)
        window = spec.context.window.range
        noun = _bucket_noun(shape.frame, shape.bucket_column)
        direction = "down" if delta < 0 else ("up" if delta > 0 else "flat")
        movement = (
            f"{direction} {magnitude(delta, shape.unit)}"
            if delta
            else "unchanged end to end"
        )
        title = (
            f"{label} by {noun}, "
            f"{window.start.isoformat()}..{window.end.isoformat()}: "
            f"{format_value(first_value, shape.unit)} → "
            f"{format_value(last_value, shape.unit)} ({movement})"
        )
        statement = (
            f"{label} ran from {format_value(first_value, shape.unit)} in "
            f"{_bucket_text(first_bucket, noun)} to {format_value(last_value, shape.unit)} in "
            f"{_bucket_text(last_bucket, noun)} ({movement} over {len(settled)} {noun}s); highest "
            f"{format_value(high[1], shape.unit)} in {_bucket_text(high[0], noun)}, lowest "
            f"{format_value(low[1], shape.unit)} in {_bucket_text(low[0], noun)}."
        )
        values: list[tuple[str, Scalar]] = [
            ("first", first_value),
            ("last", last_value),
            ("delta", int(delta) if shape.is_money else delta),
            ("high", high[1]),
            ("low", low[1]),
            ("periods", len(settled)),
        ]
        if provisional is not None and censoring is not None:
            # The point is published — dropping it would hide the newest
            # data the analyst asked for — and it is published as
            # provisional, outside the movement the sentence claims.
            statement = (
                f"{statement} The {_bucket_text(provisional[0], noun)} point "
                f"({format_value(provisional[1], shape.unit)}) is PROVISIONAL and is excluded "
                f"from that movement: {censoring.reason}"
            )
            title = f"{title}; {_bucket_text(provisional[0], noun)} provisional"
            values.extend(
                [
                    ("provisional_bucket", str(provisional[0])),
                    ("provisional_value", provisional[1]),
                    ("terminal_provisional", True),
                ]
            )
        return Finding(
            referent=ReferentId(value=referent_value, kind=ReferentKind.FINDING),
            title=title,
            statement=statement,
            metric_refs=(MetricRef(shape.measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # End-to-end movement of a series is not a recoverable dollar
            # figure, whatever the unit: it is a description, not a target.
            impact_cents=None,
            confidence=(
                "qualified"
                if (
                    censoring is not None
                    or self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
                )
                else "high"
            ),
            suggested_refinements=suggested_refinements_for(referent_value),
        )

    # -------------------------------------------------- concentration shape

    async def _evaluate_concentration(
        self,
        *,
        shape: ConcentrationShape,
        spec: AnalysisSpec,
        plan: InvestigationPlan | None = None,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
    ) -> FindingsResult:
        """Findings from a ranked population — the no-comparison answer.

        Measured cells are ranked; bounded cells are not (round-3 R3-02).
        Ordering a ceiling against a measurement sorts by *panel size*, and
        "rank our rendering providers by denial rate, worst first" published
        147 bounds of 150 values, sorted descending, so the answer's ranking
        was exactly ascending population with "ranks #1 … (worst first, as
        asked)" written over it. The bounded cells still publish — dropping
        them is the censorship the bound exists to avoid — in their own
        block, unranked, and saying so.
        """
        schema = shape.frame.schema
        idx_rank = schema.index_of(shape.rank_column)
        idx_measure = schema.index_of(shape.measure)
        bounds = self._bounds(shape.frame)
        positions = {id(row): i for i, row in enumerate(shape.frame.rows)}

        def publishable(row: tuple[Scalar, ...]) -> bool:
            # Suppressed (NULL) and empty (zero) rows are not findings. A
            # ranked list always has a tail; "Payer X: 0 mismatched claims"
            # is padding that dilutes the one row that matters.
            value = row[idx_measure]
            return value is not None and not (
                isinstance(value, int | Decimal) and value == 0
            )

        def bound_of(row: tuple[Scalar, ...]) -> BoundedCell | None:
            return bounds.get(positions.get(id(row), -1), {}).get(shape.measure)

        ordered = sorted(
            shape.frame.rows,
            key=lambda row: (
                _as_int(row[idx_rank]) is None,  # unranked rows last
                _as_int(row[idx_rank]) or 0,
            ),
        )
        candidates = [row for row in ordered if publishable(row)]
        # An ordinal bucket dimension carries its own direction, and it is
        # urgency, not size (round-3 R3-08). Sequencing "90+" ahead of
        # "61-90" tells a team to work the least urgent band first and let
        # the 61-90 band age into expired; the catalog declares the order
        # and the plan carries it here.
        urgency = _declared_bucket_order(plan, shape)
        if urgency is not None:
            idx_dim = schema.index_of(shape.dimension_columns[0])
            position = {value: i for i, value in enumerate(urgency)}
            candidates.sort(
                key=lambda row: position.get(str(row[idx_dim]), len(position))
            )
        measured = [row for row in candidates if bound_of(row) is None]
        bounded = [row for row in candidates if bound_of(row) is not None]
        # Past a governed share of bounds there is no measured population
        # left to order, and an ordinal claim over it is arithmetic about
        # panel size. The answer is then the population arithmetic.
        unrankable = bool(candidates) and (
            len(bounded) / len(candidates) > MAX_BOUNDED_SHARE_FOR_RANKING
        )
        qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)

        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)
        row_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.DIMENSION_VALUE)

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        warnings: list[str] = []
        limit = self._limit(spec)
        for position, row in enumerate(measured[:limit], start=1):
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_concentration_finding(
                f"F{n}",
                row,
                shape,
                spec,
                qualified,
                pack,
                session_id,
                investigation_id,
                display_rank=None if (unrankable or urgency is not None) else position,
                measured_total=len(measured),
                bound=None,
                urgency_position=(
                    None if urgency is None else (position, len(measured))
                ),
            )
            findings.append(finding)
            referents.append(referent)
        for row in bounded[:limit]:
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_concentration_finding(
                f"F{n}",
                row,
                shape,
                spec,
                qualified,
                pack,
                session_id,
                investigation_id,
                display_rank=None,
                measured_total=len(measured),
                bound=bound_of(row),
            )
            findings.append(finding)
            referents.append(referent)
        if bounded:
            warnings.append(
                _unranked_bounds_warning(
                    bounded_count=len(bounded),
                    measured_count=len(measured),
                    total=len(candidates),
                    measure=shape.measure,
                    unrankable=unrankable,
                    order=spec.order,
                )
            )
        truncation = _truncation_warning(
            min(len(measured), limit) + min(len(bounded), limit), len(candidates), spec
        )
        if truncation is not None:
            warnings.append(truncation)

        for i, row in enumerate(shape.frame.rows):
            referents.append(
                self._dimension_value_referent(
                    f"D{row_offset + i + 1}",
                    row,
                    shape.frame,
                    shape.dimension_columns,
                    spec,
                    pack,
                    session_id,
                    investigation_id,
                )
            )

        await self._registry.register(tuple(referents))
        return FindingsResult(
            findings=tuple(findings),
            referents=tuple(referents),
            warnings=tuple(warnings),
            emptiness=(
                None
                if findings
                else EmptinessFact(
                    kind=EmptinessKind.NO_FINDINGS,
                    frame_id=shape.frame_id,
                    detail=(
                        f"{len(shape.frame.rows)} ranked row(s) on {shape.measure!r}, and "
                        "every one was zero or suppressed"
                    ),
                )
            ),
        )

    def _build_concentration_finding(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        shape: ConcentrationShape,
        spec: AnalysisSpec,
        qualified: bool,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
        display_rank: int | None = None,
        measured_total: int = 0,
        bound: BoundedCell | None = None,
        urgency_position: tuple[int, int] | None = None,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        value = row[schema.index_of(shape.measure)]
        rank = _as_int(row[schema.index_of(shape.rank_column)])
        share = row[schema.index_of(shape.share_column)] if shape.share_column else None
        label = self._row_label(row, shape.frame, shape.dimension_columns, pack)
        measure_label = metric_label(shape.measure)
        period_text = _period_phrase(spec, pack, shape.measure, shape.frame)

        amount = _as_int(value)
        # The unit is the metric contract's, carried on the frame column —
        # a ratio renders as a percentage and a count as a count, instead of
        # falling through to a Python repr.
        magnitude = (
            magnitude_money(amount)
            if shape.is_money and amount is not None
            else format_value(value, shape.unit)
        )
        # share_of_total divides by the VISIBLE total, so with suppressed
        # cells "% of total" would overstate the concentration. Say which
        # total it is rather than quietly meaning a different one.
        share_basis = "visible total" if shape.frame.suppressed_cells else "total"
        share_text = (
            f" ({ratio_pct(share)} of {share_basis})" if isinstance(share, Decimal) else ""
        )
        # "Ranks #1" is meaningless until the sentence says which end. Live,
        # "rank payers best to worst" returned the worst payer first and
        # narrated it "ranks first at a 29.5% denial rate" — the ordering
        # the analyst asked for was neither honored nor stated. When an
        # order WAS asked for, the rows now arrive in it (planning resolves
        # "best" against the contract's sign) and the sentence names it; when
        # none was, nothing is claimed about what first means.
        order_text = f" ({spec.order.phrase}, as asked)" if spec.order is not None else ""
        title = f"{label}: {magnitude} {measure_label}{share_text}"
        if bound is not None:
            # No ordinal, in either field. A bound cannot hold a position in
            # an order it was not measured for, and "ranks #1" over a
            # ceiling is the sentence this whole branch exists to delete.
            title = f"{label}: ≤ {magnitude} {measure_label} (upper bound){share_text}"
            statement = (
                f"{label}: {measure_label} is AT MOST {magnitude} {period_text} — the numerator "
                f"was suppressed over a population of {bound.population:,}, so this is a ceiling "
                "and not a measurement. It is published unranked: a bound cannot be ordered "
                "against measured cells."
            )
        elif urgency_position is not None:
            place, total = urgency_position
            statement = (
                f"{label}: {magnitude} {measure_label}{share_text} {period_text}. This is band "
                f"{place} of {total} in the catalog's declared order for "
                f"{shape.dimension_columns[0]}, which runs most urgent first — it is sequenced "
                "by urgency, not by size."
            )
        elif display_rank is not None:
            of_text = f" of {measured_total} measured" if measured_total else ""
            statement = (
                f"{label} ranks #{display_rank}{of_text} by {measure_label}{order_text} "
                f"{period_text}: {magnitude}{share_text}."
            )
        else:
            statement = (
                f"{label}: {magnitude} {measure_label}{share_text} {period_text}. No position is "
                "claimed for it — too much of this population carries suppressed numerators for "
                "an order to mean anything."
            )

        values: list[tuple[str, Scalar]] = [(shape.measure, value)]
        if bound is None:
            values.append(("rank", display_rank if display_rank is not None else rank))
        if share is not None:
            values.append(("share_of_total", share))
        values.extend(_bound_values(shape.measure, bound))

        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(shape.measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # A count is not dollars: impact stays unset unless the ranked
            # measure is money, rather than inventing a figure. Nor is a
            # ceiling: nobody can work a bound.
            impact_cents=amount if (shape.is_money and bound is None) else None,
            confidence="qualified" if (qualified or bound is not None) else "high",
            suggested_refinements=suggested_refinements_for(referent_value),
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            cohort_definition=self._cohort_definition(
                row, shape.frame, shape.dimension_columns, spec
            ),
            finding=finding,
            dimension_value=self._single_dim(row, shape.frame, shape.dimension_columns),
        )
        return finding, registered
