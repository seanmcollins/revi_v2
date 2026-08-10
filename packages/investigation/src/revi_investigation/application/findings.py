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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from revi_calculation_contracts.contract import SignConvention
from revi_investigation.application.calculation_glue import (
    CalculationResult,
    EmptinessFact,
    EmptinessKind,
)
from revi_investigation.application.capability_ports import PackPort, PlaybookSpec
from revi_investigation.application.comparison import (
    ComparisonMaturity,
    ComparisonRendering,
    DeclaredNonComparability,
    comparison_maturity,
    comparison_range_for,
    declared_non_comparability,
    render_comparison,
)
from revi_investigation.application.execution import (
    TOO_SMALL_TO_MEASURE,
    BoundedCell,
    SuppressionCensus,
    bound_index,
    suppression_census,
)
from revi_investigation.application.gestures import suggested_refinements_for
from revi_investigation.application.planning import InvestigationPlan, frame_window
from revi_investigation.application.ports import ReferentRegistryStore, RegisteredReferent
from revi_investigation.application.rendering import (
    COUNT_UNIT as _COUNT_UNIT,
)
from revi_investigation.application.rendering import (
    MONEY_UNIT as _MONEY_UNIT,
)
from revi_investigation.application.rendering import (
    RATIO_UNIT as _RATIO_UNIT,
)
from revi_investigation.application.rendering import (
    format_value,
    magnitude,
    magnitude_money,
    measure_phrase,
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
from revi_kernel.maturity import (
    TERMINAL_BUCKET_MIN_SHARE as _TERMINAL_BUCKET_MIN_SHARE,
)
from revi_kernel.maturity import (
    CensoringKind,
    terminal_bucket_verdict,
)
from revi_kernel.refs import (
    DimensionRef,
    EntityGrain,
    MetricRef,
    ReferentId,
    ReferentKind,
)
from revi_kernel.scope import AbsoluteRange, TimeWindow

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


def _measured_range(spec: AnalysisSpec, window: TimeWindow | None) -> AbsoluteRange:
    """The range a published figure was actually computed over.

    ``window`` is the probe's own resolved window when it declared one (see
    :func:`~revi_investigation.application.planning.frame_window`); ``None``
    means the probe read the investigation window, which is the ordinary
    case and the only one before playbooks with their own probe windows.
    """
    return (window or spec.context.window).range


def probe_window_disclosure(spec: AnalysisSpec, window: TimeWindow | None) -> str | None:
    """Why this finding's period is not the one in the context header.

    ``None`` when the probe read the investigation window — the ordinary
    case, and one that must stay silent: a sentence explaining that a
    number was computed over the window the header names would be noise on
    every answer this engine gives.
    """
    if window is None or window.range == spec.context.window.range:
        return None
    header = spec.context.window.range
    own = window.range
    return (
        f"This check runs on its own period ({own.start.isoformat()}.."
        f"{own.end.isoformat()}), not the answer's "
        f"({header.start.isoformat()}..{header.end.isoformat()}): the playbook declares the "
        "period this measure is read over, and the figure above is stated over the period it "
        "was computed on."
    )


def _window_values(
    measure: str, spec: AnalysisSpec, window: TimeWindow | None
) -> list[tuple[str, Scalar]]:
    """The period this figure was computed over, as NAMED VALUES.

    The same move ``_bound_values`` makes, for the same reason: prose is
    not a contract. A card, a CSV, a restored header and an independent
    re-derivation all need to ask "which period is this number over?" and
    get one answer, and parsing it back out of a sentence is not asking.

    Empty when the probe read the investigation window — which the context
    header already publishes, and which is every finding on every answer
    that carries no playbook probe window of its own. Two names rather than
    one range object because every other value on a finding is a scalar,
    and a consumer that can read ``denial_rate__bound_population`` can read
    these without learning a second shape.
    """
    if window is None or window.range == spec.context.window.range:
        return []
    out: list[tuple[str, Scalar]] = [
        (f"{measure}{WINDOW_START_SUFFIX}", window.range.start),
        (f"{measure}{WINDOW_END_SUFFIX}", window.range.end),
    ]
    comparison = spec.context.comparison
    if comparison is not None:
        prior = comparison_range_for(comparison, window, spec.context.window)
        if prior != comparison.window.range:
            out.extend(
                [
                    (f"{measure}{PRIOR_WINDOW_START_SUFFIX}", prior.start),
                    (f"{measure}{PRIOR_WINDOW_END_SUFFIX}", prior.end),
                ]
            )
    return out


#: Suffixes of the named values above, so a consumer can read them back.
WINDOW_START_SUFFIX = "__window_start"
WINDOW_END_SUFFIX = "__window_end"
#: …and the range the comparison on this finding was taken against, when the
#: probe's own window moved it. The planner pairs a probe with a prior twin
#: derived from the PROBE's window, so a six-month probe under a one-month
#: question is differenced against the six months before it — which the
#: comparison phrase now names, and which this publishes as data.
PRIOR_WINDOW_START_SUFFIX = "__prior_window_start"
PRIOR_WINDOW_END_SUFFIX = "__prior_window_end"


def published_window_note(findings: Sequence[Finding]) -> str | None:
    """What the context header owes a reader, read off the FINDINGS.

    A playbook probe template may declare its own window, which the planner
    resolves and applies whenever the analyst named none of their own
    (``daily_portfolio``'s denial-rate probe reads ``{4, week,
    full_periods}``). Every figure it produced is correct over THAT period
    while the header names the investigation window, so the answer
    published one period over numbers computed across another — 104 of the
    corpus audit's 156 divergences, and the largest single class in it.

    Composed from the published findings rather than from the plan for two
    reasons. It names only periods a reader can actually see a number
    over — a probe that published nothing is not something to warn about —
    and it is the identical computation on a live turn and on a RESTORED
    one, which holds a ``plan_hash`` rather than a plan. Both read the same
    named values off the same findings and produce the same sentence.

    ``None`` when every finding was measured over the investigation window,
    which is every answer that runs no playbook probe window of its own.
    Nothing is re-scoped to make this go away: the window the probe read is
    the window the pack authored.
    """
    ranges: set[tuple[date, date]] = set()
    for finding in findings:
        starts: dict[str, date] = {}
        ends: dict[str, date] = {}
        for name, value in finding.values:
            if name.endswith(WINDOW_START_SUFFIX) and isinstance(value, date):
                starts[name[: -len(WINDOW_START_SUFFIX)]] = value
            elif name.endswith(WINDOW_END_SUFFIX) and isinstance(value, date):
                ends[name[: -len(WINDOW_END_SUFFIX)]] = value
        for measure, start in starts.items():
            end = ends.get(measure)
            if end is not None:
                ranges.add((start, end))
    if not ranges:
        return None
    text = "; ".join(f"{start.isoformat()}..{end.isoformat()}" for start, end in sorted(ranges))
    return (
        "some checks here use their own periods, declared by the playbook rather than by this "
        f"question ({text}) — each result states the period it was computed over"
    )


def _with_window_note(statement: str, spec: AnalysisSpec, window: TimeWindow | None) -> str:
    """Append the probe's own-window disclosure, when there is one to make."""
    note = probe_window_disclosure(spec, window)
    return f"{statement} {note}" if note else statement


def _period_phrase(
    spec: AnalysisSpec,
    pack: PackPort,
    measure: str,
    frame: EvidenceFrame,
    window: TimeWindow | None = None,
) -> str:
    """"over 2026-07-01..2026-07-31" — or "as of 2026-08-02" for a snapshot.

    Eight contracts here are ``kind: snapshot``: they read the balance at
    the watermark and apply no start..end predicate. Published titles and
    statements nonetheless carried the turn's window, so a finding read
    ``timely filing at risk dollars: $22,426,000.28 (2026-07-01..2026-07-31)``
    over a figure that is the ALL-TIME total — the July number is
    $5,565,290.35, and the two are not the same claim. The window is not
    removed from the answer (the cohort and charts are scoped by it); it is
    removed from the sentence that says what the number measures.

    ``window`` is the same rule applied to the other axis. A playbook probe
    may declare its own window, which the planner resolves and applies; the
    period said here is then the PROBE's, because a figure computed over
    2026-07-06..2026-08-02 and titled ``(2026-07-01..2026-07-31)`` is the
    identical defect wearing a different mask.
    """
    if _is_snapshot(pack, measure):
        return f"as of {frame.watermark.newest_data_date.isoformat()}"
    measured = _measured_range(spec, window)
    return f"over {measured.start.isoformat()}..{measured.end.isoformat()}"


def _period_paren(
    spec: AnalysisSpec,
    pack: PackPort,
    measure: str,
    frame: EvidenceFrame,
    window: TimeWindow | None = None,
) -> str:
    """The same period, parenthesized for a title."""
    if _is_snapshot(pack, measure):
        return f"(as of {frame.watermark.newest_data_date.isoformat()})"
    measured = _measured_range(spec, window)
    return f"({measured.start.isoformat()}..{measured.end.isoformat()})"
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

#: How far a movement may sit either side of the size a question ASSERTS
#: and still count as that size, as a fraction of the asserted change.
#:
#: Round-3 R3-03 made the magnitude test exist; round-4 R4-05 found it was
#: **one-sided**. ``PREMISE_MAGNITUDE_TOLERANCE`` was a floor at half the
#: asserted change, so anything from +50% upward confirmed "doubled" — and
#: live, a denial rate that went 7.4% → 12.8% was published as
#: "Premise confirmed … It happened", at high confidence, with
#: ``asserted_multiple: 2.0`` and ``pct_change: 0.726`` sitting in the same
#: values array. A 10x move would have confirmed a doubling too.
#:
#: A doubling is +100%. At a quarter-band, +75%..+125% is a doubling and
#: +72.6% is not — it is a sharp rise that fell short of the claim, which
#: is a third verdict and reads as one.
PREMISE_MAGNITUDE_BAND = Decimal("0.25")


class MagnitudeVerdict(StrEnum):
    """Where the movement landed against the size the question asserted."""

    #: Inside the band: the question's own word for it is accurate.
    WITHIN = "within"
    #: The right direction, short of the claimed size.
    SHORT = "short"
    #: The right direction, past the claimed size.
    BEYOND = "beyond"
    #: No base to measure a multiple against (zero or suppressed prior).
    UNVERIFIABLE = "unverifiable"


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


@dataclass(frozen=True, slots=True)
class SelectionCensus:
    """Every cell of a ranking frame, counted once and accounted for.

    Round-4 R4-10: one card carried two censuses of one frame and they
    contradicted — "52 of the 52 publishable denial rate cells … leaving 0
    measured, so no ranking is published" and, 1,900 characters later, "Of
    150 cell(s) on this answer, 52 carry an upper bound, 85 were withheld
    outright and 13 are measured". The refusal was manufactured by
    discarding those 13 measured cells as padding: they were providers with
    a **0% denial rate** — the perfect performers — dropped by a rule
    written for counts ("Payer X: 0 mismatched claims") and applied to
    rates. Zero is a measurement, and in a denial-rate ranking it is the
    most informative one there is.

    The four buckets partition the frame by construction
    (``total == bounded + measured + empty + withheld``), so no two
    sentences on one answer can state different arithmetic about it.
    """

    #: Rows in the ranking frame.
    total: int
    #: Publishable rows whose value is a ceiling, not a measurement.
    bounded: int
    #: Publishable rows carrying a measurement — zeros included.
    measured: int
    #: Rows dropped as empty. Only ever additive units: a 0 in a count or a
    #: dollar column is a ranking's tail, a 0 in a rate column is a result.
    empty: int

    @property
    def publishable(self) -> int:
        return self.bounded + self.measured

    @property
    def withheld(self) -> int:
        """Rows the small-cell policy nulled outright — the remainder."""
        return max(self.total - self.publishable - self.empty, 0)

    @property
    def bounded_share(self) -> float:
        return self.bounded / self.publishable if self.publishable else 0.0

    def as_payload(self) -> dict[str, int]:
        """The full census, for the trace.

        It used to be a SENTENCE, appended to the answer's own disclosure —
        so the most important paragraph on the page stated its arithmetic
        twice, once in words and once in the machine's ("Of 30 cell(s) on
        this answer, 24 carry an upper bound…"). The page now says the
        count once, in words; the complete partition is recorded here,
        where an auditor can check it and a reader is not asked to.
        """
        return {
            "total_rows": self.total,
            "bounded_rows": self.bounded,
            "measured_rows": self.measured,
            "empty_rows": self.empty,
            "withheld_rows": self.withheld,
        }


def _row_noun(dimension_columns: Sequence[str]) -> str:
    """What the rows of this answer ARE, in the analyst's vocabulary.

    "24 of 30 plans are too small to measure exactly" is a sentence a
    director acts on; "24 of the 30 publishable denial rate cells" is one
    they stop reading. The cut's own id is the closest thing the engine has
    to the reader's word for its rows, so it is the word used.
    """
    if not dimension_columns:
        return "rows"
    label = dimension_columns[0].replace("_", " ")
    return label if label.endswith("s") else f"{label}s"


def _unranked_bounds_warning(
    *,
    census: SelectionCensus,
    measure: str,
    unrankable: bool,
    order: object | None,
    noun: str = "rows",
) -> str:
    """What a ranking owes a reader once some of its rows are ceilings.

    Round-3 R3-02 and the population arithmetic R3-18 asked for in the same
    breath — rewritten in round 6 for the reader who has to act on it. A
    fresh-eyes review could not parse this paragraph: it opened with a
    count rather than with what the count MEANT, said "publishable … cells"
    and "cell(s)" in machine voice, carried three words for two ideas
    ("upper bound", "ceiling", "measurement"), and stated the same census
    twice — once in English and once as arithmetic.

    So: the meaning leads, the count is stated ONCE in words, and the full
    partition goes to the trace (:meth:`SelectionCensus.as_payload`), where
    an auditor can check it and a reader is not asked to.
    """
    label = metric_label(measure)
    if unrankable:
        return (
            f"ranking_refused: most of these {noun} are {TOO_SMALL_TO_MEASURE}, so no ranking "
            f"is published. {census.bounded} of {census.total} are {TOO_SMALL_TO_MEASURE} — for "
            "those only a ceiling is known, and putting ceilings in order beside measured "
            f"figures sorts by how big each group is rather than by {label}. The "
            f"{census.measured} that could be measured are published above; the rest are listed "
            "separately, each with the size of the group its ceiling was taken over."
        )
    asked = " (the order you asked for applies to the measured rows only)" if order else ""
    return (
        f"bounded_cells_unranked: {census.bounded} of {census.total} {noun} are "
        f"{TOO_SMALL_TO_MEASURE} — for those only a ceiling is known, so they are listed below "
        f"the ranking rather than inside it{asked}. The ranking covers the {census.measured} "
        "that could be measured: a ceiling has no place in an order it was never measured for."
    )


def _truncation_warning(served: int, computed: int, spec: AnalysisSpec) -> str | None:
    """What a truncated finding list owes its reader (round-3 R3-04).

    Live, "show me all twelve payers, not just three" and "every one of our
    12 payers" both returned three findings with no omission notice, and
    the same turn's evidence panel read ``rows: 12, limit: null, truncated:
    false``. The narrative then called a 4.4% to 15.0% spread "roughly three
    percentage points … a tight band" over the three it could see.

    ``computed`` is the frame the CHART draws, not the set selection kept
    (round-5 A-04): a direction filter that removed two of twelve cells
    made the census read "3 of 10" beside a twelve-row chart, and then
    disappeared entirely on the expand — suppressed because served (10) was
    no longer under a computed (10) that had already been narrowed.

    A count the analyst NAMED is also an obligation. When they asked for
    twelve and twelve is not what came back, this fires whether or not the
    frame had more rows to give, and says which of the two happened.
    """
    shortfall = spec.limit is not None and spec.limit > served
    if computed <= served and not shortfall:
        return None
    if computed <= served:
        return (
            f"findings_truncated: this answer publishes all {served} of the {computed} cell(s) "
            "this cut computed — the row count asked for is larger than the population, so "
            f"there are no further rows to show. Superlatives and spread statements describe "
            f"those {served}."
        )
    asked = (
        f" The {spec.limit} rows asked for could not be met: {served} are published."
        if shortfall
        else ""
    )
    return (
        f"findings_truncated: {served} of {computed} computed cells are published as findings; "
        f"the remaining {computed - served} are in the chart and the evidence frame but carry "
        "no finding. Superlatives and spread statements on this answer describe the published "
        f"slice, not the full population.{asked}"
    )


def _direction_omission_warning(
    counter: list[tuple[Scalar, ...]],
    published: int,
    shape: MovementShape,
    spec: AnalysisSpec,
    pack: PackPort,
) -> str | None:
    """Name the cells a direction filter removed (round-5 A-04).

    "Show me all twelve" returned ten, and the two that were missing were
    the only two that had IMPROVED — because the question asserted a rise,
    so the finding population silently narrowed to the cells that rose and
    every census on the card counted the narrowed set. The card read as
    "all twelve payers got worse" over a population in which two got
    better. A directional selection is legitimate; a silent one is not.
    """
    if not counter or spec.direction is None:
        return None
    names = ", ".join(
        render_row_label(
            pack,
            shape.dimension_columns,
            {
                dim: row[shape.frame.schema.index_of(dim)]
                for dim in shape.dimension_columns
            },
        )
        for row in counter[:8]
    )
    more = f" (and {len(counter) - 8} more)" if len(counter) > 8 else ""
    listed = (
        f"The first {published} of them are listed below the asked set, labelled."
        if published
        else "They carry no finding on this answer."
    )
    return (
        f"direction_omitted: {len(counter)} of {len(shape.frame.rows)} cell(s) moved the OTHER "
        f"way and are therefore not part of what {spec.direction.value!r} asked about: {names}"
        f"{more}. {listed} Any statement about how this whole population moved has to count "
        "them."
    )


@dataclass(frozen=True, slots=True)
class CompareShape:
    """A compare frame suitable for findings/reconciliation: at least one
    dimension column plus a money measure with its delta."""

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    money_measure: str
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None


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
                frame_id=step.id,
                frame=frame,
                dimension_columns=dims,
                money_measure=money,
                window=frame_window(plan, step.id),
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
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None

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
            window=frame_window(plan, step.id),
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
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None


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
            window=frame_window(plan, step.id),
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
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None

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
                    window=frame_window(plan, frame_id),
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
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


#: See :data:`revi_kernel.maturity.TERMINAL_BUCKET_MIN_SHARE` — re-exported
#: here because this module's callers have always read it from here.
TERMINAL_BUCKET_MIN_SHARE = _TERMINAL_BUCKET_MIN_SHARE


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
    #: The bucket key exactly as the frame holds it (``"2026-07-20"``,
    #: not ``"week of 2026-07-20"``). Round-4 R4-03: the prose named the
    #: provisional point and the chart drew a solid line straight through
    #: it, because the only form of the bucket that left this module was
    #: the human label. The raw key is what a chart row's ``x`` is built
    #: from, so the mark and the sentence can be made to agree.
    bucket_key: str = ""


def terminal_bucket_censoring(
    shape: TrendShape, spec: AnalysisSpec
) -> TerminalCensoring | None:
    """Is this series' last point a measurement, or an artifact of maturity?

    The verdict itself is :func:`revi_kernel.maturity.terminal_bucket_verdict`
    — one rule, in the kernel, because the chart builder must break its line
    at exactly the bucket this sentence calls provisional and the two
    capabilities may not import each other. What is composed here is the
    prose: the reason and the mandatory disclosure that carry it.
    """
    verdict = terminal_bucket_verdict(
        shape.frame, bucket_column=shape.bucket_column, measure=shape.measure
    )
    if verdict is None:
        return None
    bucket_label = _bucket_text(verdict.bucket, verdict.noun)
    noun = verdict.noun
    if verdict.kind is CensoringKind.CALENDAR_PARTIAL:
        assert verdict.covered_through is not None
        reason = (
            f"the {noun} runs to {verdict.covered_through.isoformat()} and this load ends "
            f"{verdict.newest_data_date.isoformat()}, so the bucket holds only part of its "
            "own period."
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
            bucket_key=str(verdict.bucket),
        )

    assert verdict.population is not None and verdict.median_population is not None
    share = Decimal(verdict.population) / Decimal(verdict.median_population)
    reason = (
        f"it was computed over {verdict.population:,} adjudicated records against a series "
        f"median of {verdict.median_population:,} ({ratio_pct(share)} of it), so the {noun} is "
        "still settling and the records that have settled are not a random sample of it."
    )
    return TerminalCensoring(
        bucket=bucket_label,
        population=verdict.population,
        median_population=verdict.median_population,
        reason=reason,
        warning=(
            f"adjudication_incomplete: the last point of this series ({bucket_label}) is "
            f"RIGHT-CENSORED — {reason} It is published as provisional and excluded from the "
            "first-to-last movement, the high and the low. A rise that terminates on an "
            "incompletely adjudicated bucket is a data-maturity artifact until that bucket "
            "matures."
        ),
        bucket_key=str(verdict.bucket),
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
                    window=frame_window(plan, node.id),
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


def as_number(value: Scalar) -> Decimal | None:
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
    #: Where the movement landed against that size.
    magnitude: MagnitudeVerdict = MagnitudeVerdict.UNVERIFIABLE
    #: The multiple that actually happened (``current / prior``), when
    #: there was a base to divide by. Published on the finding so a reader
    #: never has to take "it did not double" on trust.
    actual_multiple: Decimal | None = None
    #: Did the aggregate move the way the question says, before any of the
    #: integrity tests below? Kept separately from ``holds`` so an
    #: unverifiable verdict can still say which way the arithmetic pointed
    #: without claiming it means anything.
    directional: bool = False
    #: The ceiling on each side, when the §15 policy withheld its numerator
    #: (round-5 A-02a). A movement between two ceilings is the ratio of the
    #: two POPULATIONS and carries no information about the measure.
    current_bound: BoundedCell | None = None
    prior_bound: BoundedCell | None = None
    #: The panel asymmetry this plan reported, when it reported one
    #: (round-5 A-02b). Borrowed from whichever frame carries a
    #: denominator: an additive money measure has no panel of its own and
    #: is distorted by an immature one exactly as much as a rate is.
    immature: ComparisonMaturity | None = None
    #: The question asserted a SIZE nothing could parse (round-5 A-02c).
    size_asserted_unparsed: bool = False
    #: The metric CONTRACT declares these two windows non-comparable
    #: (round-7 FN-4). The third leg of the integrity read: bounds and panel
    #: maturity are signals measured off the frame, and a pack author can
    #: also simply declare that a delta between two windows of this metric
    #: is not a result. See
    #: :class:`~...comparison.DeclaredNonComparability`.
    not_comparable: DeclaredNonComparability | None = None
    #: The two windows are materially different LENGTHS and the measure is
    #: additive (round-7 FN-4, same leg). ``comparison.py`` already withholds
    #: the impact and qualifies every finding for this; the premise verdict
    #: was the one surface that still said "confirmed" over it.
    length_mismatched: ComparisonRendering | None = None
    #: The window the premise probe actually read, when it declared one of
    #: its own. ``None`` is the investigation window. A premise probe is
    #: cloned from the playbook probe whose breakdown the findings layer
    #: publishes, so it inherits that probe's window — and the verdict
    #: sentence has to state the period it was checked over.
    window: TimeWindow | None = None

    @property
    def magnitude_short(self) -> bool:
        """The direction matched and the SIZE did not (R3-03, R4-05)."""
        return self.magnitude is MagnitudeVerdict.SHORT

    @property
    def magnitude_beyond(self) -> bool:
        """It happened, and by more than the question claimed."""
        return self.magnitude is MagnitudeVerdict.BEYOND

    @property
    def bounded(self) -> bool:
        return self.current_bound is not None or self.prior_bound is not None

    @property
    def not_comparable_windows(self) -> bool:
        """Are the two windows declared, or measured, not a delta at all?"""
        return self.not_comparable is not None or self.length_mismatched is not None

    @property
    def unverifiable(self) -> bool:
        """Nothing here can confirm OR refute what the question asserted."""
        return (
            self.bounded
            or self.immature is not None
            or self.not_comparable_windows
            or self.size_asserted_unparsed
        )

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


def _premise_measure(spec: AnalysisSpec, compared: tuple[str, ...]) -> str | None:
    """The metric the PREMISE names, out of the ones this frame compared.

    Round-4 R4-05(b): the old rule preferred whichever compared column was
    money. An analyst asked about denial RATE and got, as bolded F1 at high
    confidence, "You asked about a doubling in denied dollars. It did not
    happen — denied dollars fell $829,506.94, -72.7%" — a true sentence
    about a metric nobody asked about, published as the verdict on a
    question about a rate that had risen. The same turn's next answer put
    denial rate at 9.1% → 12.8%.

    A premise is a claim about a named quantity. The metric the analyst's
    own spec names wins; when the spec names none of the compared columns,
    this frame cannot answer the question that was asked and the caller
    looks further rather than substituting a different metric.
    """
    if not compared:
        return None
    named: list[str] = []
    if spec.rank_by is not None:
        named.append(spec.rank_by.id)
    named.extend(ref.id for ref in spec.measures)
    for metric_id in named:
        if metric_id in compared:
            return metric_id
    # A spec that named nothing measurable here asserts nothing about which
    # column to read; the frame's first compared metric is all there is.
    return None if named else compared[0]


def _premise_frames(
    plan: InvestigationPlan,
    calculation: CalculationResult,
    premise_prefix: str,
) -> list[tuple[str, EvidenceFrame]]:
    """Every ungrouped single-row compare frame a premise could be read off,
    the dedicated premise probe first.

    Round-4 R4-05(a): the verdict fired on 0 of 5 live probes across two
    reviewers because this function's ancestor accepted **only** a compare
    step whose first input started with ``premise``, and the plans actually
    produced were ``['main', 'main__prior']`` — an undimensioned comparison
    that measures exactly the aggregate the premise is about. A question
    that states a movement and plans no dimensions still has its premise
    sitting right there in the frame; refusing to look at it published a
    300-word narrative about cells while denials had FALLEN 4.2%, with no
    contradiction anywhere in it.

    A scalar frame is a scalar frame whatever the step that made it is
    called. The dedicated probe still sorts first, so a plan that carries
    one is read exactly as before.
    """
    dedicated: list[tuple[str, EvidenceFrame]] = []
    scalar: list[tuple[str, EvidenceFrame]] = []
    for step in plan.transforms.steps:
        if step.operator != "compare" or not step.inputs:
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        if _dimension_columns(frame) or len(frame.rows) != 1:
            continue
        target = (
            dedicated
            if step.inputs[0].startswith(premise_prefix) or step.id.startswith(premise_prefix)
            else scalar
        )
        target.append((step.id, frame))
    return [*dedicated, *scalar]


def verify_premise(
    plan: InvestigationPlan,
    calculation: CalculationResult,
    spec: AnalysisSpec,
    pack: PackPort,
    *,
    premise_prefix: str,
    suppression_threshold: int | None = None,
) -> PremiseCheck | None:
    """Check the asserted aggregate movement, before anything explains it.

    Returns ``None`` when the question asserted nothing (the overwhelming
    majority of turns), or when no frame measured the metric the premise
    names — an unverifiable premise is not a refuted one, and claiming
    otherwise would be the same failure in the opposite direction.

    **The verdict reads what the integrity layer published** (round-5
    A-02). Three personas, three mechanisms, one architectural fact: this
    function used to read ``row[index_of(measure)]`` and ``__prior`` out of
    the frame and consult nothing else, so it published confident verdicts
    over quantities the rest of the engine had already marked unmeasurable.

    * **Bounds.** "Denial rate rose 157.1%, past the 100.0% a doubling
      assumes: 13.9% → 35.7%" — where the same answer's own
      ``SUPPRESSION_BOUNDED`` warning said both sides were ceilings over
      one clamped numerator of 10. 157.1% is exactly 72/28 - 1, the ratio
      of the two DENOMINATORS, carrying no denial information at all.
    * **Panel maturity.** "It did not happen — denied dollars fell
      $829,506.94, -72.7%" beside "this window holds 27.0% of the panel
      the comparison window does". Ground truth: denied dollars per
      adjudicated claim went $199.39 → $201.81, +1.2%. The guard could not
      see it because it fires on frames carrying a ``_den`` column and the
      premise probe measured an additive money measure — so the panel is
      BORROWED from the sibling frame that does carry one.
    * **Unparsed size.** ``premise_holds: true`` beside
      ``premise_magnitude: "unverifiable"``, rendered "Premise confirmed",
      over a question that said HALVE.
    * **Comparability** (round-7 FN-4, the third leg). The two above are
      *signals measured off the frame*; whether two windows may be
      differenced at all is a third, separate question, and the payload was
      already answering it — in the metric contract's own governed caveat
      and in the length-mismatch machinery — on a surface the verdict never
      read. Live: "Premise confirmed: net collection rate 72.5% → 18.5%,
      fell 53.9 points", ``direct``/``high``, three times on one payload,
      beside that payload's own caution that "two windows of unequal
      maturity are not comparable as levels". No panel guard fired and none
      should have: ``net_collection_rate``'s denominator is contract-
      expected DOLLARS, so there is no adjudicated-record asymmetry to see.
      The contract was the only thing on the turn that knew, and nothing
      asked it.
    """
    if not spec.direction_asserted or spec.direction is None:
        return None
    # Read once for the turn: any frame on this plan whose two sides rest
    # on differently-settled panels makes EVERY movement between the same
    # two windows a settlement artifact, whether or not the frame the
    # premise was measured on carries a denominator of its own.
    immature = next(iter(comparison_maturity(calculation.frames)), None)
    # …and the length of the two windows, which the rest of the engine
    # already refuses to net (see comparison.py) while this verdict went on
    # confirming premises over it.
    for frame_id, frame in _premise_frames(plan, calculation, premise_prefix):
        # Per FRAME, not per turn: a premise probe cloned from a playbook
        # probe carries that probe's window, and the pairing it was checked
        # against was derived from THAT window (round-E2, class C).
        window = frame_window(plan, frame_id)
        rendering = render_comparison(spec, window=window)
        compared = _compared_measures(frame)
        measure = _premise_measure(spec, compared)
        if measure is None:
            continue
        row = frame.rows[0]
        delta = as_number(row[frame.schema.index_of(f"{measure}__delta")])
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
        unit = _unit_of(frame, measure)
        directional = (delta > 0) if wanted > 0 else (delta < 0)
        current_bound, prior_bound = _premise_bounds(frame, measure, suppression_threshold)
        bounded = current_bound is not None or prior_bound is not None
        # The third leg: is the difference between these two windows a
        # result at all? Asked of the metric's own contract, and of the two
        # windows' lengths — the same per-unit rule the finding paths use,
        # because a ratio over a window does not scale with the window.
        not_comparable = declared_non_comparability(pack, measure)
        length_mismatched = (
            rendering
            if rendering is not None
            and rendering.material_length_mismatch
            and _is_additive(unit)
            else None
        )
        blocked = any(
            (bounded, immature is not None, not_comparable is not None, length_mismatched)
        )
        # Direction is necessary and not sufficient. "Doubled" asserts a
        # SIZE, and the movement has to land inside a band around what was
        # claimed — on either side of it — before the claim is confirmed.
        magnitude = MagnitudeVerdict.UNVERIFIABLE
        actual_multiple: Decimal | None = None
        if directional and spec.asserted_multiple is not None and not blocked:
            magnitude, actual_multiple = _magnitude_verdict(
                prior, current, spec.asserted_multiple
            )
        # A movement between two ceilings is not a movement, a movement
        # between two unequally-settled panels is not a business change, and
        # a movement the governing contract says may not be taken is not a
        # movement either: none of the three can confirm OR refute what was
        # asserted.
        unverifiable = blocked or spec.size_asserted_unparsed
        return PremiseCheck(
            frame_id=frame_id,
            frame=frame,
            measure=measure,
            unit=unit,
            current=current,
            prior=prior,
            delta=delta,
            pct=row[frame.schema.index_of(pct_col)] if pct_col in frame.schema.names else None,
            holds=(
                directional
                and magnitude is not MagnitudeVerdict.SHORT
                and not unverifiable
            ),
            asserted_multiple=spec.asserted_multiple,
            magnitude=magnitude,
            actual_multiple=actual_multiple,
            directional=directional,
            current_bound=current_bound,
            prior_bound=prior_bound,
            immature=immature,
            size_asserted_unparsed=spec.size_asserted_unparsed,
            not_comparable=not_comparable,
            length_mismatched=length_mismatched,
            window=window,
        )
    return None


def _premise_bounds(
    frame: EvidenceFrame, measure: str, threshold: int | None
) -> tuple[BoundedCell | None, BoundedCell | None]:
    """``(current, prior)`` ceilings on the two sides of a premise movement.

    ``bound_index`` recognises ``<m>__num``/``<m>__den`` and therefore sees
    only the CURRENT side of a compare frame — the prior side's columns are
    ``<m>__num__prior``/``<m>__den__prior`` and end in neither suffix. Both
    sides are read here, because a premise verdict over a bounded PRIOR is
    exactly as unmeasurable as one over a bounded current.
    """
    if threshold is None or not frame.rows:
        return None, None

    def side(suffix: str) -> BoundedCell | None:
        num_col, den_col = f"{measure}__num{suffix}", f"{measure}__den{suffix}"
        names = frame.schema.names
        if num_col not in names or den_col not in names:
            return None
        numerator = frame.rows[0][frame.schema.index_of(num_col)]
        population = frame.rows[0][frame.schema.index_of(den_col)]
        if isinstance(numerator, bool) or not isinstance(numerator, int):
            return None
        if isinstance(population, bool) or not isinstance(population, int):
            return None
        if not (0 < numerator < threshold) or population < threshold:
            return None
        return BoundedCell(
            label="",
            metric_id=measure,
            population=population,
            bound=Decimal(threshold - 1) / Decimal(population),
        )

    return side(""), side(_PRIOR_SUFFIX)


def _magnitude_verdict(
    prior: Scalar, current: Scalar, asserted: Decimal
) -> tuple[MagnitudeVerdict, Decimal | None]:
    """Where the movement landed against the size the question asserted.

    Measured as *changes* rather than as levels, so one rule reads
    "doubled" (asserted change +1.0) and "halved" (asserted change -0.5),
    and the fraction of the claim that was achieved is signed the same way
    for both. An unmeasurable base (a zero or suppressed prior) refutes
    nothing: an unverifiable premise is not a false one.

    Two-sided by construction (R4-05c). The predecessor asked only whether
    the movement was at least half the claim, which made "it doubled" true
    of +72.6% and of +900% alike.
    """
    prior_value = as_number(prior)
    current_value = as_number(current)
    if prior_value is None or current_value is None or prior_value == 0:
        return MagnitudeVerdict.UNVERIFIABLE, None
    actual = Decimal(current_value) / Decimal(prior_value)
    asserted_change = asserted - Decimal(1)
    if asserted_change == 0:
        return MagnitudeVerdict.UNVERIFIABLE, actual
    achieved = (actual - Decimal(1)) / asserted_change
    if achieved < Decimal(1) - PREMISE_MAGNITUDE_BAND:
        return MagnitudeVerdict.SHORT, actual
    if achieved > Decimal(1) + PREMISE_MAGNITUDE_BAND:
        return MagnitudeVerdict.BEYOND, actual
    return MagnitudeVerdict.WITHIN, actual


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
    moved = "fell" if premise.delta < 0 else ("rose" if premise.delta > 0 else "did not move")
    movement = _movement_text(premise)
    if premise.unverifiable:
        return _unverifiable_sentence(premise, noun, label, phrase, figures)
    if premise.magnitude_short:
        # Neither confirmation nor refutation-of-direction: the movement is
        # real and it is not the movement the question named. Both facts in
        # one sentence, with the shortfall stated as arithmetic (R4-05c) —
        # "Premise confirmed … It happened: 7.4% → 12.8%, 72.6%" was
        # published against ``asserted_multiple: 2.0`` on the same card.
        return (
            f"You asked about {noun} in {label}. It did not {verb} — {figures} {phrase}, "
            f"{moved} {movement}, short of the {_asserted_change_text(spec)} {noun} assumes"
        )
    if premise.magnitude_beyond:
        return (
            f"You asked about {noun} in {label}. It happened, and by more than that — "
            f"{figures} {phrase}, {moved} {movement}, past the "
            f"{_asserted_change_text(spec)} {noun} assumes"
        )
    if premise.holds:
        return (
            f"You asked about {noun} in {label}. It happened: {figures} {phrase}, "
            f"{moved} {movement}"
        )
    return (
        f"You asked about {noun} in {label}. It did not happen — {figures} {phrase}, "
        f"{label} {moved} {movement}"
    )


def _unverifiable_sentence(
    premise: PremiseCheck, noun: str, label: str, phrase: str, figures: str
) -> str:
    """The fourth verdict: the arithmetic is there and it means nothing.

    Round-5 A-02. Each arm does the arithmetic OUT LOUD, because the reason
    a reader must not act on the number is itself the finding — "157.1%" is
    a true division of two figures neither of which is a measurement, and
    saying so is more useful than withholding it.
    """
    if premise.bounded:
        sides = []
        for side, bound in (("prior", premise.prior_bound), ("current", premise.current_bound)):
            if bound is not None:
                sides.append(
                    f"the {side} side is at most {format_value(bound.bound, premise.unit)} over "
                    f"{bound.population:,}"
                )
        ceilings = " and ".join(sides)
        return (
            f"You asked about {noun} in {label}. It cannot be checked here — {ceilings}, each a "
            f"numerator the small-cell policy withheld, so {figures} is a movement between "
            "ceilings and the percentage between them is the ratio of the two POPULATIONS, not "
            f"a movement in {label}. Nothing on this answer confirms or refutes the claim."
        )
    if premise.immature is not None:
        maturity = premise.immature
        return (
            f"You asked about {noun} in {label}. It cannot be checked yet — the two windows are "
            f"not equally settled ({maturity.current_panel:,} adjudicated record(s) on this "
            f"window against {maturity.prior_panel:,} on the comparison window, "
            f"{maturity.share:.1%}), so the difference between {figures} {phrase} is dominated "
            "by how much of the newer window has come back rather than by anything that "
            "happened. Ask again once the thinner side matures."
        )
    if premise.not_comparable is not None:
        # The arithmetic OUT LOUD, like every other arm: withholding the two
        # figures would leave a reader believing the platform could not
        # measure them, when what it cannot do is DIFFERENCE them.
        return (
            f"You asked about {noun} in {label}. It cannot be checked here — the governed "
            f"contract for {label} declares these two windows non-comparable as levels: "
            f"{premise.not_comparable.caveat} Both figures are real ({figures} {phrase}) and the "
            "difference between them is a settlement artifact of the newer window, not a "
            f"movement in {label}. Nothing on this answer confirms or refutes the claim; ask "
            "over two settled windows and I will verify it."
        )
    if premise.length_mismatched is not None:
        mismatch = premise.length_mismatched
        return (
            f"You asked about {noun} in {label}. It cannot be checked here — the two windows are "
            f"not the same length ({mismatch.comparison_days}d against "
            f"{mismatch.current_days}d) and {label} is an additive measure, so the difference "
            f"between {figures} {phrase} is dominated by the length ratio rather than by "
            "anything that happened. Nothing is length-normalized on this answer. Ask over two "
            "windows of equal length and I will verify it."
        )
    return (
        f"You asked about {noun} in {label}. The SIZE that names is not one this platform can "
        f"read, so it was not checked: {label} did move {figures} {phrase}, and whether that is "
        f"{noun} is a question this answer does not settle. Restate the size as a percentage or "
        "a multiple and I will verify it."
    )


def movement_forms(delta: Scalar, pct: Scalar, unit: str | None) -> str:
    """A movement in BOTH of its readings, each named.

    Round-6 answer-surface review. "denial rate rose 11.5%" printed beside
    "7.1% → 7.9%" is read as *11.5 points* by every director who scans it —
    two different facts, one sentence, and nothing in the sentence to tell
    them apart. A rate moved 0.8 points AND 11.5% relative; both are true,
    neither implies the other, and a card that states one without saying
    which has stated nothing checkable.

    Money needs no such care — dollars and percentages do not look alike —
    so it keeps the shorter form with the relative change parenthesised.
    """
    absolute = magnitude(delta, unit)
    if not isinstance(pct, Decimal):
        return absolute
    relative = ratio_pct(abs(pct))
    if unit == _RATIO_UNIT:
        return f"{absolute}, a {relative} relative change"
    return f"{absolute} ({relative})"


def _movement_text(premise: PremiseCheck) -> str:
    """The movement a premise verdict states, in both of its readings."""
    return movement_forms(premise.delta, premise.pct, premise.unit)


def _asserted_change_text(spec: AnalysisSpec) -> str:
    """The movement the question assumes, as a percentage of the base.

    "a doubling" assumes +100%; "a halving" assumes -50%. Stating it beside
    what happened is what turns "it did not double" from an assertion into
    an arithmetic the reader can check.
    """
    multiple = spec.asserted_multiple
    if multiple is None:  # pragma: no cover - only reached with a multiple
        return "movement"
    return ratio_pct(abs(multiple - Decimal(1)))


def _unverifiable_reason(premise: PremiseCheck) -> str:
    """Which of the five things stopped the verdict, as a closed token.

    Read in the same order the sentence arms are, so the value and the prose
    can never name different reasons.
    """
    if premise.bounded:
        return "bounded_endpoint"
    if premise.immature is not None:
        return "immature_panel"
    if premise.not_comparable is not None:
        return "contract_not_comparable"
    if premise.length_mismatched is not None:
        return "window_length_mismatch"
    return "size_unparsed"


def _premise_warning(
    premise: PremiseCheck, spec: AnalysisSpec, *, comparison: ComparisonRendering | None
) -> str:
    """The correction a false premise owes the reader, said first.

    Generic by construction: the movement that was asserted comes from the
    interpretation's closed ``direction`` set, and what actually happened
    comes from the aggregate the premise probe measured. No phrasing of the
    original question appears here, because none of it was parsed.

    Two families, because they are two different corrections. A premise
    whose DIRECTION is wrong is refuted (``premise_false``). A premise
    whose direction is right and whose SIZE is not is *partly* supported
    (``premise_partial``) — telling an analyst "denials did not rise" when
    they rose 72.6% would be its own false statement.
    """
    assert spec.direction is not None
    sentence = premise_verdict_sentence(premise, spec, comparison=comparison)
    if premise.unverifiable:
        return (
            f"premise_unverifiable: {sentence}. The question's own assumption is neither "
            "confirmed nor refuted on this answer, so nothing below may be read as evidence "
            "for it or against it."
        )
    if premise.magnitude_short:
        return (
            f"premise_partial: {sentence}. The direction the question assumes is right and the "
            "size is not, so nothing below may be described in the question's own words for it. "
            "What follows is the composition of the movement that did happen."
        )
    return (
        f"premise_false: {sentence}. The question takes that movement as given, and over this "
        "window there was none. What follows describes the cells that did move that way; it is "
        "context for a movement that did not happen at the level asked about, not confirmation "
        "of it."
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
            plan,
            calculation,
            spec,
            pack,
            premise_prefix=_PREMISE_PREFIX,
            suppression_threshold=suppression_threshold,
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
        rows, selection_warnings, counter = self._select_directional(
            shape.frame.rows, idx_delta, spec, pack, shape.measure
        )

        qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
        comparison = render_comparison(spec, window=shape.window)

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
        eligible = [row for row in rows if as_number(row[idx_delta]) is not None]
        bounds = self._bounds(shape.frame)
        row_positions = {id(row): i for i, row in enumerate(shape.frame.rows)}
        # The cells the direction filter removed are not gone, they are
        # LAST (round-5 A-04). "Show me all twelve" returned ten, and the
        # two missing were the only two that had improved — a systematically
        # premise-flattering omission on a card whose own premise verdict
        # already said the premise was only partly supported. When the
        # analyst named a count, the asked-for set is published first and
        # the counter-direction cells fill the rest of it, labelled.
        counter_eligible = [row for row in counter if as_number(row[idx_delta]) is not None]
        # Only a count the ANALYST named opens the set: the default top-3 is
        # this platform's own choice of how much to show, and filling it
        # with cells that moved the other way would answer a question
        # nobody asked.
        room = max(limit - len(eligible), 0) if spec.limit is not None else 0
        publishable = [*eligible[:limit], *counter_eligible[:room]]
        counter_published = len(publishable) - len(eligible[:limit])
        for row in publishable:
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
                counter_direction=len(findings) >= len(eligible[:limit]),
            )
            findings.append(finding)
            referents.append(referent)
        # Counted over the frame the CHART draws, never over the
        # direction-filtered candidate set: the card that published "3 of 10
        # computed cells" pointed at a chart drawing twelve payer rows.
        omission = _direction_omission_warning(
            counter_eligible, counter_published, shape, spec, pack
        )
        if omission is not None:
            selection_warnings = (*selection_warnings, omission)
        truncation = _truncation_warning(len(findings), len(shape.frame.rows), spec)
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
    ) -> tuple[list[tuple[Scalar, ...]], tuple[str, ...], list[tuple[Scalar, ...]]]:
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

        The third return is the cells the direction filter REMOVED, in the
        frame's own order (round-5 A-04). They are not the answer to what
        was asked and they are not nothing: "which payers improved?" run as
        a control returned exactly the two an expand had silently dropped,
        so the caller publishes them, labelled, and counts them.
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
                    as_number(row[idx_delta]) is None,  # NULL deltas last
                    -(as_number(row[idx_delta]) or 0)
                    if descending
                    else (as_number(row[idx_delta]) or 0),
                ),
            )

        if wanted is None:
            # No direction was asked. An ORDER may still have been ("best to
            # worst"), and it wins: it is the analyst's own instruction about
            # which end to show first, resolved against the metric's sign.
            asked_order = descending_for_order(spec.order, sign)
            if asked_order is not None:
                return ordered(list(rows), descending=asked_order), (), []
            # Otherwise the default is not "ascending" — it is *worst
            # first*, read off the contract's own sign convention: a
            # higher-is-bad measure's worst movement is a rise. Ascending
            # was only ever right because the first metrics through here
            # were higher-is-good dollars, and it published the biggest
            # improvements of a higher-is-bad metric as its headline.
            adverse = adverse_delta_sign(sign)
            return ordered(list(rows), descending=adverse is not None and adverse > 0), (), []

        matched = [
            row
            for row in rows
            if (value := as_number(row[idx_delta])) is not None
            and (value > 0 if wanted > 0 else value < 0)
        ]
        assert spec.direction is not None
        movement = "rose" if wanted > 0 else "fell"
        if matched:
            removed = [
                row
                for row in rows
                if (value := as_number(row[idx_delta])) is not None
                and not (value > 0 if wanted > 0 else value < 0)
            ]
            return (
                ordered(matched, descending=(wanted > 0) == biggest_first),
                (),
                ordered(removed, descending=not (wanted > 0)),
            )
        warning = (
            f"direction_unmatched: nothing {movement} — no cell's "
            f"{metric_label(money_measure)} moved the way {spec.direction.value!r} asks about "
            "over this window. The movements below are the opposite direction, shown as "
            "context, not as an answer to what was asked."
        )
        return ordered(list(rows), descending=not (wanted > 0)), (warning,), []

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
        comparison = render_comparison(spec, window=premise.window)
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
        claim_noun, _ = _asserted_claim(spec)
        if premise.unverifiable:
            # A fourth title, because there are four outcomes. "Premise
            # confirmed" over two ceilings, an immature panel or a size
            # nobody parsed is the sentence round-5 A-02 is about — and
            # "Premise not supported" would be the opposite error.
            title = f"Premise cannot be verified: {sentence}"
            statement = (
                f"{sentence}. Nothing below may be called {claim_noun} or offered as evidence "
                "against it: the cells that follow are the composition of a movement this "
                "answer cannot certify."
            )
        elif premise.magnitude_short:
            # A third title, because there are three outcomes. "Premise
            # confirmed" over a movement 27% short of the claim is the
            # sentence R4-05 was raised about; "Premise not supported" over
            # a real 72.6% rise would be the opposite error.
            title = f"Premise partly supported: {sentence}"
            statement = (
                f"{sentence}. The movement is real and it is not the movement the question "
                f"names, so nothing here or below may be called {claim_noun}: the cells that "
                "follow compose a movement that fell short of the claim."
            )
        elif premise.holds:
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
        statement = _with_window_note(statement, spec, premise.window)
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
            # …and how it landed against the SIZE that was asserted, so
            # "confirmed" can never again sit beside an asserted_multiple
            # of 2.0 and a pct_change of 0.726 (R4-05c).
            ("premise_magnitude", premise.magnitude.value),
            # …and WHY nothing could be concluded, when nothing could
            # (round-5 A-02). ``premise_holds: false`` on its own reads as
            # a refutation, and an unverifiable premise is not a refuted
            # one — a client that renders the two the same way publishes
            # "it did not happen" over a movement between two ceilings.
            ("premise_unverifiable", premise.unverifiable),
        ]
        if premise.unverifiable:
            values.append(("premise_unverifiable_reason", _unverifiable_reason(premise)))
        if premise.not_comparable is not None:
            # The declaration as data, so a client (and the invariant test)
            # can branch on it without parsing prose, and so the metric that
            # carries it is named rather than inferred.
            values.append(("premise_not_comparable_metric", premise.not_comparable.measure))
        if premise.current_bound is not None:
            values.extend(_bound_values(premise.measure, premise.current_bound))
        if premise.prior_bound is not None:
            values.append((f"{premise.measure}__prior__is_bound", True))
            values.append(
                (f"{premise.measure}__prior__bound_population", premise.prior_bound.population)
            )
        if premise.asserted_multiple is not None:
            values.append(("asserted_multiple", premise.asserted_multiple))
            # The question's own word for the size it asserted, published as
            # data so the narrative validator can forbid it when the verdict
            # is short-of. "Roughly doubled" written under a finding that
            # says the movement fell 27 points short of doubling is the
            # contradiction R4-05 is about, and prose is where it surfaces.
            values.append(("premise_asserted_verb", _asserted_claim(spec)[1]))
        if premise.actual_multiple is not None:
            values.append(("actual_multiple", premise.actual_multiple))
        values.extend(_window_values(premise.measure, spec, premise.window))
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
            # A verdict that could not test the claim is not a high-
            # confidence verdict, whatever the arithmetic behind it looks
            # like (round-5 A-02).
            confidence="qualified" if premise.unverifiable else "high",
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
        counter_direction: bool = False,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        measure = shape.measure
        current = row[schema.index_of(measure)]
        prior = row[schema.index_of(f"{measure}__prior")]
        delta = as_number(row[schema.index_of(f"{measure}__delta")])
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
        # Both readings, each named. "up 0.8 points, a 11.5% relative
        # change" — never the bare "11.5%" beside "7.1% → 7.9%", which a
        # director reads as points (round-6 answer-surface review).
        movement = movement_forms(delta, pct, shape.unit)
        statement = (
            f"{label}: {measure_label} moved from {format_value(prior, shape.unit)} to "
            f"{current_text} ({direction} {movement} {period_phrase})."
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
        if counter_direction:
            # Published because the analyst named a count that the
            # direction-matched set could not fill, and labelled in both
            # fields because the title is what gets screenshotted: this
            # cell is IN the population and is not an answer to what was
            # asked (round-5 A-04).
            assert spec.direction is not None
            title = f"{title} — moved the other way"
            statement = (
                f"{statement[:-1]}. This cell moved the OPPOSITE way to the "
                f"{spec.direction.value!r} the question asks about; it is published because you "
                "asked for the whole set, not as an instance of what was asked."
            )

        statement = _with_window_note(statement, spec, shape.window)

        delta_value: Scalar = int(delta) if shape.is_money else delta
        values: list[tuple[str, Scalar]] = [
            ("current_cents" if shape.is_money else measure, current),
            ("prior_cents" if shape.is_money else f"{measure}__prior", prior),
            ("delta_cents" if shape.is_money else f"{measure}__delta", delta_value),
            ("pct_change", pct),
        ]
        values.extend(_bound_values(measure, bound))
        values.extend(_window_values(measure, spec, shape.window))
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
            # Per SHAPE: one playbook answer can carry a 4-week denial-rate
            # probe beside a 3-month underpayment probe, and a comparison
            # phrase rendered once for the turn would name the wrong prior
            # range on at least one of them.
            comparison = render_comparison(spec, window=shape.window)
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
        period_text = _period_phrase(spec, pack, shape.measure, shape.frame, shape.window)
        period_paren = _period_paren(spec, pack, shape.measure, shape.frame, shape.window)

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
            # Both readings, each named. "(39.6% change)" printed under
            # "12.8%, up from 9.1%" is read as 39.6 POINTS by a director
            # scanning the card — the ambiguity the title above already
            # refuses to publish, re-introduced one line down (round-6
            # answer-surface review).
            # The direction is already in ``movement`` ("up from"), so the
            # parenthesis carries only the SIZE — in both of its readings.
            moved = f" ({movement_forms(delta, pct, shape.unit)})" if delta is not None else ""
            statement = (
                f"{label} is {current_text} {period_text}, {movement} {prior_text} "
                f"{period_phrase}{moved}."
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
        statement = _with_window_note(statement, spec, shape.window)
        values.extend(_bound_values(shape.measure, bound))
        values.extend(_window_values(shape.measure, spec, shape.window))

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
        # Round-6 A-05: the trend was the one shape family that never asked
        # which of its points are ceilings. The chart under it drew them as
        # bounds while the title above it stated "7.5% → 9.0% (up 1.5
        # points)" — a measured-looking movement between two ceilings — and
        # the export, which prints this title verbatim, inherited the claim.
        row_bounds = self._bounds(shape.frame)
        points = [
            (row[idx_bucket], value, row_bounds.get(index, {}).get(shape.measure))
            for index, row in enumerate(shape.frame.rows)
            if (value := as_number(row[idx_value])) is not None
        ]
        if len(points) < 2:
            return None  # a series of one is not a trend, and nor is silence
        points.sort(key=lambda point: str(point[0]))
        provisional = points[-1] if censoring is not None else None
        settled = points[:-1] if (censoring is not None and len(points) > 2) else points
        (first_bucket, first_value, first_bound) = settled[0]
        (last_bucket, last_value, last_bound) = settled[-1]
        low = min(settled, key=lambda point: point[1])
        high = max(settled, key=lambda point: point[1])
        delta = last_value - first_value
        label = metric_label(shape.measure)
        window = _measured_range(spec, shape.window)
        noun = _bucket_noun(shape.frame, shape.bucket_column)
        direction = "down" if delta < 0 else ("up" if delta > 0 else "flat")
        bounded_ends = first_bound is not None or last_bound is not None
        if bounded_ends:
            # A movement between ceilings is not a movement. The direction
            # word survives (the ceilings really did move), the SIZE does
            # not: the true endpoints sit somewhere at or below the two
            # bounds, so their difference is unknown.
            movement = "movement between ceilings — size unknown"
        else:
            movement = (
                f"{direction} {magnitude(delta, shape.unit)}"
                if delta
                else "unchanged end to end"
            )
        first_text = bound_text(first_value, shape.unit, bounded=first_bound is not None)
        last_text = bound_text(last_value, shape.unit, bounded=last_bound is not None)
        title = (
            f"{label} by {noun}, "
            f"{window.start.isoformat()}..{window.end.isoformat()}: "
            f"{first_text} → {last_text} ({movement})"
        )
        statement = (
            f"{label} ran from {first_text} in "
            f"{_bucket_text(first_bucket, noun)} to {last_text} in "
            f"{_bucket_text(last_bucket, noun)} ({movement} over {len(settled)} {noun}s); highest "
            f"{bound_text(high[1], shape.unit, bounded=high[2] is not None)} in "
            f"{_bucket_text(high[0], noun)}, lowest "
            f"{bound_text(low[1], shape.unit, bounded=low[2] is not None)} in "
            f"{_bucket_text(low[0], noun)}."
        )
        statement = _with_window_note(statement, spec, shape.window)
        values: list[tuple[str, Scalar]] = [
            ("first", first_value),
            ("last", last_value),
            ("delta", int(delta) if shape.is_money else delta),
            ("high", high[1]),
            ("low", low[1]),
            ("periods", len(settled)),
        ]
        # The bound, as named values rather than as prose, so a card, a CSV
        # and an emailed export can all ask "is this a measurement?" of a
        # trend without re-deriving the suppression policy from the chart.
        #
        # ``__is_bound`` is the scalar contract's own name and means what it
        # means everywhere: this figure is not a measurement. There is
        # deliberately no series-level ``__bound`` — a series has no single
        # ceiling, and publishing one endpoint's as the trend's would be a
        # third answer to a question the two per-end names already answer.
        if bounded_ends:
            values.append((f"{shape.measure}__is_bound", True))
        for side, bound in (("first", first_bound), ("last", last_bound)):
            if bound is not None:
                values.extend(
                    [
                        (f"{shape.measure}__{side}__is_bound", True),
                        (f"{shape.measure}__{side}__bound", bound.bound),
                        (f"{shape.measure}__{side}__bound_population", bound.population),
                    ]
                )
        if provisional is not None and censoring is not None:
            # The point is published — dropping it would hide the newest
            # data the analyst asked for — and it is published as
            # provisional, outside the movement the sentence claims.
            statement = (
                f"{statement} The {_bucket_text(provisional[0], noun)} point "
                f"({bound_text(provisional[1], shape.unit, bounded=provisional[2] is not None)}) "
                f"is PROVISIONAL and is excluded from that movement: {censoring.reason}"
            )
            title = f"{title}; {_bucket_text(provisional[0], noun)} provisional"
            values.extend(
                [
                    ("provisional_bucket", str(provisional[0])),
                    ("provisional_value", provisional[1]),
                    ("terminal_provisional", True),
                ]
            )
        values.extend(_window_values(shape.measure, spec, shape.window))
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
                    # A movement measured between two ceilings is not a
                    # measurement, whatever the grade of the frame it came
                    # from (round-6 A-05).
                    or bounded_ends
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

        def is_empty(row: tuple[Scalar, ...]) -> bool:
            """A zero that is padding rather than a result.

            Only for additive units. "Payer X: 0 mismatched claims" is the
            tail every ranked list has, and it dilutes the one row that
            matters. "Dr. X: 0% denial rate" is the opposite — it is the
            best cell in the population, and dropping thirteen of them
            (round-4 R4-10) drove the measured count to zero and
            manufactured a refusal to rank over a frame that had thirteen
            perfect performers in it.
            """
            value = row[idx_measure]
            return (
                _is_additive(shape.unit)
                and isinstance(value, (int, Decimal))
                and not isinstance(value, bool)
                and value == 0
            )

        def publishable(row: tuple[Scalar, ...]) -> bool:
            # A suppressed (NULL) cell has no value to publish; an empty
            # one is padding only where zero means "nothing happened".
            return row[idx_measure] is not None and not is_empty(row)

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
        # A bound's ceiling is (threshold - 1) / population, so ordering
        # bounded cells by VALUE orders them by inverse panel size and the
        # three published are always the three smallest populations — the
        # loosest, least useful ceilings in the frame. Order them by the
        # population the bound was taken over instead: the tightest ceiling
        # is the one a reader can do something with (R4-10).
        bounded.sort(
            key=lambda row: (
                -(bound.population if (bound := bound_of(row)) is not None else 0),
                self._row_label(row, shape.frame, shape.dimension_columns, pack),
            )
        )
        census = SelectionCensus(
            total=len(shape.frame.rows),
            bounded=len(bounded),
            measured=len(measured),
            empty=sum(1 for row in ordered if is_empty(row)),
        )
        # Past a governed share of bounds there is no measured population
        # left to order, and an ordinal claim over it is arithmetic about
        # panel size. The answer is then the population arithmetic. Measured
        # ZEROS count toward the measured side of that share: they are the
        # cells the ranking is most about.
        unrankable = census.bounded_share > MAX_BOUNDED_SHARE_FOR_RANKING
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
                    census=census,
                    measure=shape.measure,
                    unrankable=unrankable,
                    order=spec.order,
                    # The reader's own noun for the rows in front of them —
                    # "plans", "payers", "providers" — rather than "cells",
                    # which is the engine's word for its own arithmetic.
                    noun=_row_noun(shape.dimension_columns),
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
        period_text = _period_phrase(spec, pack, shape.measure, shape.frame, shape.window)

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
        # The figure and the measure name, said with the unit ONCE. "179.5
        # days" beside a measure whose display name is "days in ar" is how
        # "Atlas Commercial: 179.5 days days in ar" became a permanent tile
        # headline; the collision is invisible to an f-string and visible
        # to :func:`measure_phrase` (FN-14).
        measured_text = measure_phrase(magnitude, measure_label, shape.unit)
        title = f"{label}: {measured_text}{share_text}"
        if bound is not None:
            # No ordinal, in either field. A bound cannot hold a position in
            # an order it was not measured for, and "ranks #1" over a
            # ceiling is the sentence this whole branch exists to delete.
            title = f"{label}: ≤ {measured_text} (upper bound){share_text}"
            statement = (
                f"{label}: {measure_label} is AT MOST {magnitude} {period_text} — the numerator "
                f"was suppressed over a population of {bound.population:,}, so this is a ceiling "
                "and not a measurement. It is published unranked: a bound cannot be ordered "
                "against measured cells."
            )
        elif urgency_position is not None:
            place, total = urgency_position
            statement = (
                f"{label}: {measured_text}{share_text} {period_text}. This is band "
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
                f"{label}: {measured_text}{share_text} {period_text}. No position is "
                "claimed for it — too much of this population carries suppressed numerators for "
                "an order to mean anything."
            )
        statement = _with_window_note(statement, spec, shape.window)

        values: list[tuple[str, Scalar]] = [(shape.measure, value)]
        if bound is None:
            values.append(("rank", display_rank if display_rank is not None else rank))
        if share is not None:
            values.append(("share_of_total", share))
        values.extend(_bound_values(shape.measure, bound))
        values.extend(_window_values(shape.measure, spec, shape.window))

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
