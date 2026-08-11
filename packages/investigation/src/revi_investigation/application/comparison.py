"""How a comparison is *described*, and what happens when the two windows
are not the same length (design §6.1, §7.2).

The context header prints the resolved comparison range —
``vs 2026-01-01..2026-03-31``. Finding text that re-derives a phrase from
the **current** window's requested unit does not: a ``CUSTOM`` comparison
falls through to ``"vs prior {unit}"``, so a 7-day window differenced
against calendar Q1 publishes *"cash posted down $4,199,421 vs prior week"*
at ``direct`` / ``high`` with an ``impact_cents`` of -419,942,121 and no
warning, contradicting the header on the same answer.

Two rules, both enforced here so there is one place to read them:

**Every rendered phrase names the resolved range.** A label ("prior week",
"prior year") is a convenience, never the whole truth, so the concrete
dates ride along with it and a ``CUSTOM`` comparison is rendered *as* its
range. Header and finding text can then be checked against each other
mechanically, which is what
``test_findings_rendering.py::TestComparisonPhrase`` does for every
``ComparisonKind``.

**Unequal window lengths are annotated, never netted.** Differencing 7 days
against 90 days is a legal thing to ask for and an illegal thing to call a
delta: the difference is dominated by the length ratio, not by anything
that happened. Three options: refuse the turn, normalize both sides to a
daily rate, or answer with a hard warning. This implementation takes the
third **and strips the false precision that makes the first two
attractive**:

- the phrase carries the mismatch inline (``90d vs 7d, not
  length-normalized``), so no reader sees the number without the caveat;
- ``impact_cents`` is left unset — an impact is a dollar figure the
  platform is willing to rank, sum, and put in a worklist, and a
  length-mismatched difference is none of those;
- the finding's confidence drops to ``qualified``, so it can never be
  published in certified language;
- a warning is emitted on the turn, in the ``warnings`` array that already
  exists on ``TurnAnswer`` and ``Investigation``.

Refusing was rejected because the comparison itself is well-formed and the
user asked for it on purpose: "how did last week compare with Q1" is a
real question, and this system's refusals are reserved for things it
*cannot* compute rather than things it must caveat. Silent normalization
was rejected because it answers a question nobody asked (a per-day rate)
under the label of the one they did. Annotating keeps the analyst's
question and removes the platform's false confidence — which is the same
trade the rest of the product makes when it grades evidence instead of
hiding it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.rendering import metric_label, plural
from revi_investigation.application.validation import population_caveat
from revi_investigation.domain.context import AnalysisSpec
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import MetricRef
from revi_kernel.scope import (
    AbsoluteRange,
    Comparison,
    ComparisonKind,
    TimeWindow,
    derive_comparison,
    whole_month_span,
)

#: How far two window lengths may differ before the difference is worth
#: caveating an additive measure for.
#:
#: An exact ``!=`` is too sharp by an order of magnitude. February against
#: March differ by 10%; a 90-day trailing window against its prior 90 days
#: can differ by a day when a month boundary falls badly, and 1 day in 90 is
#: 1.1% — a rounding error the calendar forced, not a distortion. Treating
#: either identically to a 7-day window differenced against a quarter
#: (impact withheld, every finding qualified, a warning on the turn) caveats
#: a 1.1% calendar artifact in the same words as a 1,186% one, which teaches
#: analysts to ignore the words.
LENGTH_TOLERANCE = 0.03


def same_calendar_kind(current: AbsoluteRange, prior: AbsoluteRange) -> int | None:
    """Whole calendar periods of equal span on both sides, as a month count.

    A calendar month against the calendar month before it is the standard
    revenue-cycle comparison unit and is *never* length-normalized in
    practice: nobody rebases June onto 31 days to compare it with July, and
    no month-end close would accept a system that refused to publish an
    impact figure because February is short. The length-mismatch machinery
    otherwise fires on every one of them — 30 vs 31 days is a 3.2% ratio,
    above :data:`LENGTH_TOLERANCE` — so every month-over-month turn comes
    back with ``COMPARISON_WINDOW_MISMATCH``, ``impact_cents`` null and every
    finding title carrying "(30d vs 31d, not length-normalized)".

    The test is structural rather than declared, exactly like
    :func:`~revi_kernel.scope.whole_month_span`: two ranges that each start
    on the 1st, each end on a month's last day, and each cover the same
    number of months ARE the same kind of period — month vs month, quarter
    vs quarter, year vs year — whoever built them and whether or not a
    relative spec survives on the window. One month against two is not, and
    keeps the guard.

    Returns the shared month span, or ``None`` when the two ranges are not
    like-for-like calendar periods.
    """
    left = whole_month_span(current)
    right = whole_month_span(prior)
    if left is None or right is None or left != right:
        return None
    return left


@dataclass(frozen=True, slots=True)
class ComparisonRendering:
    """Everything the presentation layer needs to talk about a comparison."""

    #: ``vs prior week (2026-07-20..2026-07-26)`` / ``vs 2026-01-01..2026-03-31``
    phrase: str
    #: ``2026-01-01..2026-03-31`` — the exact string the context header prints.
    range_text: str
    current_days: int
    comparison_days: int
    #: The shared whole-month span when both windows are the same kind of
    #: calendar period (1 = month vs month, 3 = quarter vs quarter, 12 =
    #: year vs year), else ``None``. See :func:`same_calendar_kind`.
    calendar_span: int | None = None

    @property
    def length_mismatch(self) -> bool:
        """Any difference at all — what the phrase mentions."""
        return self.current_days != self.comparison_days

    @property
    def length_ratio(self) -> float:
        """How far apart the two lengths are, as a fraction of the longer."""
        longer = max(self.current_days, self.comparison_days)
        if longer == 0:  # pragma: no cover - AbsoluteRange is inclusive
            return 0.0
        return abs(self.current_days - self.comparison_days) / longer

    @property
    def same_kind(self) -> bool:
        """Are both sides whole calendar periods of the same span?"""
        return self.calendar_span is not None

    @property
    def material_length_mismatch(self) -> bool:
        """A difference big enough to distort an additive measure.

        Only additive measures are distorted at all: a rate is a ratio of
        two quantities measured over the same window, so it is invariant to
        the window's length by construction, and qualifying a denial-rate
        comparison for a one-day calendar difference states a caution that
        is not true. The unit test lives at the call site (findings);
        the size test lives here.

        A SAME-KIND calendar comparison is exempt outright rather than by
        tolerance: February against January is a 10% day-count ratio and
        still the comparison every close performs. The day count
        is disclosed as an informational note; nothing is withheld for it.
        """
        if self.same_kind:
            return False
        return self.length_ratio > LENGTH_TOLERANCE


#: How a whole-month span is said out loud in the same-kind note.
_CALENDAR_NOUN = {1: "calendar month", 3: "calendar quarter", 12: "calendar year"}


def _base_label(comparison: Comparison, window: TimeWindow) -> str | None:
    """The human label for a comparison, or ``None`` when only dates will do."""
    if comparison.kind is ComparisonKind.PRIOR_YEAR:
        return "vs prior year"
    if comparison.kind is ComparisonKind.CUSTOM:
        # A custom range has no name. Naming it after the *current* window's
        # unit is precisely the defect this module exists to remove.
        return None
    requested = window.requested
    if requested is not None and requested.quantity == 1:
        return f"vs prior {requested.unit.value}"
    return "vs prior period"


def comparison_range_for(
    comparison: Comparison, window: TimeWindow, spec_window: TimeWindow
) -> AbsoluteRange:
    """The prior range a probe reading ``window`` was actually paired against.

    The planner derives a flow probe's prior twin from the probe's OWN
    window (``PlanBuilder._comparison_range``), so a playbook probe that
    declared ``{4, week, full_periods}`` is compared against the four weeks
    before *those* four weeks — not against the range the investigation
    window derives. Describing that pairing with the spec's comparison
    range is how a "vs prior period (2026-06-01..2026-06-30)" phrase comes
    to sit over a difference taken against 2026-06-08..2026-07-05.

    Stated here, once, in exactly the form the planner uses, so the two
    cannot drift: a CUSTOM comparison names dates the analyst chose and is
    never re-derived for anybody.
    """
    if window.range == spec_window.range or comparison.kind is ComparisonKind.CUSTOM:
        return comparison.window.range
    return derive_comparison(window, comparison.kind).window.range


def render_comparison(
    spec: AnalysisSpec, *, window: TimeWindow | None = None
) -> ComparisonRendering | None:
    """Describe the spec's comparison, or ``None`` when there is none.

    ``window`` is the window of the PROBE this rendering will be published
    beside, when that probe declared one of its own. It defaults to the
    investigation window, which is what every direct query and every
    playbook run under an explicit analyst window uses.
    """
    comparison = spec.context.comparison
    if comparison is None:
        return None
    spec_window = spec.context.window
    window = window if window is not None else spec_window
    cmp_range = comparison_range_for(comparison, window, spec_window)
    range_text = f"{cmp_range.start.isoformat()}..{cmp_range.end.isoformat()}"
    current_days = window.range.day_length
    comparison_days = cmp_range.day_length

    # Same-kind calendar periods carry no mismatch clause: a month against
    # the month before it IS like-for-like, and stamping "(30d vs 31d, not
    # length-normalized)" onto every finding title of every month-end close
    # teaches analysts to read past the words. The day count is
    # still disclosed, once, as an informational turn note below.
    calendar_span = same_calendar_kind(window.range, cmp_range)
    mismatch = (
        f"{comparison_days}d vs {current_days}d, not length-normalized"
        if current_days != comparison_days and calendar_span is None
        else ""
    )
    label = _base_label(comparison, window)
    if label is None:
        # No name exists for a custom range; the range *is* the label.
        phrase = f"vs {range_text}" + (f" ({mismatch})" if mismatch else "")
    else:
        phrase = f"{label} ({range_text}, {mismatch})" if mismatch else f"{label} ({range_text})"
    return ComparisonRendering(
        phrase=phrase,
        range_text=range_text,
        current_days=current_days,
        comparison_days=comparison_days,
        calendar_span=calendar_span,
    )


#: How far the two sides of a comparison's adjudicated panels may diverge
#: before the difference between them is a data-maturity artifact rather
#: than a business movement.
#:
#: The same 0.6 the trend guard uses for a terminal bucket
#: (``TERMINAL_BUCKET_MIN_SHARE``), applied to the axis that guard cannot
#: see: ``terminal_bucket_censoring`` needs a :class:`TrendShape` of at
#: least three rows and runs only inside the trend loop, so a two-window
#: comparison never reaches it. Without this, a window at 23% adjudicated
#: published against one at 91% reads "+73%" at direct/high, with
#: ``COMPARISON_WINDOW_MISMATCH`` firing loudly about a 30-vs-31-day
#: calendar difference — a ~3% effect — and nothing at all about the
#: three-quarters of the newer window that has not settled.
COMPARISON_MIN_PANEL_SHARE = Decimal("0.6")

_DENOMINATOR_SUFFIX = "__den"
_PRIOR_SUFFIX = "__prior"


@dataclass(frozen=True, slots=True)
class ComparisonMaturity:
    """One comparison whose two sides rest on differently-settled panels."""

    frame_id: str
    measure: str
    current_panel: int
    prior_panel: int
    warning: str

    @property
    def share(self) -> Decimal:
        """The smaller panel as a fraction of the larger."""
        larger = max(self.current_panel, self.prior_panel)
        if larger <= 0:  # pragma: no cover - guarded at construction
            return Decimal(1)
        return Decimal(min(self.current_panel, self.prior_panel)) / Decimal(larger)


def _panel_total(frame: EvidenceFrame, column: str) -> int | None:
    if column not in frame.schema.names:
        return None
    index = frame.schema.index_of(column)
    total = 0
    seen = False
    for row in frame.rows:
        value = row[index]
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        seen = True
        total += value
    return total if seen else None


def comparison_maturity(
    frames: tuple[tuple[str, EvidenceFrame], ...],
) -> tuple[ComparisonMaturity, ...]:
    """Data-maturity asymmetry between the two sides of every comparison.

    Read off the compare frames themselves: a ratio contract carries its
    adjudicated denominator as ``<metric>__den``, and the compare operator
    carries the baseline's as ``<metric>__den__prior``. Summed across the
    frame, those two integers are the panels the two halves of the
    published movement were measured over — and when one is a fraction of
    the other, the movement between them is a settlement curve, not a
    business change.

    Nothing is inferred where the numbers do not exist: a frame carrying no
    denominator yields no verdict, which is the honest outcome for an
    additive money measure that has no panel to speak of.
    """
    out: list[ComparisonMaturity] = []
    for frame_id, frame in frames:
        if "__compare" not in frame_id:
            continue
        names = frame.schema.names
        for name in names:
            if not name.endswith(_DENOMINATOR_SUFFIX):
                continue
            prior_name = f"{name}{_PRIOR_SUFFIX}"
            if prior_name not in names:
                continue
            current = _panel_total(frame, name)
            prior = _panel_total(frame, prior_name)
            if current is None or prior is None or current <= 0 or prior <= 0:
                continue
            larger, smaller = max(current, prior), min(current, prior)
            if Decimal(smaller) >= COMPARISON_MIN_PANEL_SHARE * Decimal(larger):
                continue
            measure = name[: -len(_DENOMINATOR_SUFFIX)]
            thin, thick = (
                ("this window", "the comparison window")
                if current < prior
                else ("the comparison window", "this window")
            )
            share = Decimal(smaller) / Decimal(larger)
            out.append(
                ComparisonMaturity(
                    frame_id=frame_id,
                    measure=measure,
                    current_panel=current,
                    prior_panel=prior,
                    warning=(
                        f"adjudication_incomplete: the two sides of this comparison are not "
                        f"equally settled. {metric_label(measure)} was measured over "
                        f"{current:,} adjudicated {plural(current, 'record')} on this period "
                        f"and {prior:,} on the comparison period — "
                        f"{thin} holds {share:.1%} of the panel {thick} does. Claims still "
                        "awaiting their first remittance are excluded from both sides, and they "
                        "are not excluded evenly, so the difference between the two figures is "
                        "a settlement artifact until the thinner side matures. The movement is "
                        "published as provisional, and this answer cannot be read as "
                        "settled."
                    ),
                )
            )
    return tuple(out)


#: How a metric CONTRACT declares that its own two windows may not be
#: differenced. The population caveat is governed prose the pack author
#: wrote and the engine already publishes verbatim on every answer that
#: reads the metric; this is the one assertion inside it the integrity layer
#: has to be able to act on rather than merely repeat.
#:
#: Matched on the assertion, not on the metric: ``net_collection_rate`` and
#: ``first_pass_yield`` carry it today and any pack may add it tomorrow, and
#: a hard-coded metric list would go stale silently.
_NOT_COMPARABLE_ASSERTION = re.compile(
    r"(?:are|is)\s+not\s+(?:directly\s+)?comparable|"
    r"(?:cannot|can\s*not|may\s+not)\s+be\s+compared",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DeclaredNonComparability:
    """A metric whose own contract says these two windows are not a delta.

    The third leg of the integrity read. ``verify_premise`` consults bounded
    endpoints and adjudicated PANEL share, and both are *signals measured
    off the frame*. A pack author can also simply **declare**
    non-comparability — "two windows of unequal maturity are not comparable
    as levels", published as a caution on the same payload — and a verdict
    that reads only the signals is blind to the declaration.

    That blindness publishes "Premise confirmed: net collection rate
    72.5% → 18.5%, fell 53.9 points" at ``grade: direct``,
    ``confidence: high``, beside the payload's own ``POPULATION_CAVEAT``
    saying those two windows cannot be compared — a 53.9-point collapse that
    did not happen.

    The signal guards do not fire and are not wrong to stay silent:
    ``net_collection_rate``'s denominator is contract-expected DOLLARS, not
    a count of adjudicated records, so there is no panel asymmetry to see.
    The contract is the only thing on the turn that knows.
    """

    #: The metric id whose contract carries the declaration.
    measure: str
    #: The pack author's own sentence, lifted verbatim — a paraphrase
    #: generated here would be a second, ungoverned statement of the
    #: population (see :func:`~...validation.population_caveat`).
    caveat: str

    @property
    def warning(self) -> str:
        return (
            f"not_comparable_windows: the standard definition of "
            f"{metric_label(self.measure)} says these two periods may not be subtracted "
            f"from one another as levels — {self.caveat} A movement between them is an "
            "artifact of the newer period still settling rather than a change in the "
            "business, so no figure on this answer is published as settled and the "
            "difference is not a result. Ask over two settled periods, or read each period "
            "on its own, to get a comparison this platform will stand behind."
        )


def declares_non_comparability(description: str) -> str | None:
    """The contract's own non-comparability sentence, or ``None``.

    Read out of the governed ``Population caveat:`` paragraph and nowhere
    else. The rest of a description is explanatory prose in which "not
    comparable" can appear about something other than the two windows of
    this comparison; the caveat is the field the pack promises is a
    statement about the population, and it is the field the answer already
    publishes as a caution.
    """
    caveat = population_caveat(description)
    if caveat is None or _NOT_COMPARABLE_ASSERTION.search(caveat) is None:
        return None
    return caveat


def declared_non_comparability(
    pack: PackPort, measure: str
) -> DeclaredNonComparability | None:
    """Does this metric's contract forbid differencing its two windows?"""
    contract = pack.metric(measure)
    if contract is None:
        return None
    caveat = declares_non_comparability(contract.description)
    return None if caveat is None else DeclaredNonComparability(measure=measure, caveat=caveat)


def compared_measures(frames: tuple[tuple[str, EvidenceFrame], ...]) -> tuple[str, ...]:
    """Every metric this turn actually DIFFERENCED, in frame order.

    A metric read on one window is not a comparison and carries no
    comparability question; the declaration only bites where a prior side
    exists. Read off the compare frames' own columns for the same reason
    :func:`comparison_maturity` is — the frame is what the findings stage
    saw, whatever the plan intended.
    """
    seen: dict[str, None] = {}
    for frame_id, frame in frames:
        if "__compare" not in frame_id:
            continue
        names = set(frame.schema.names)
        for column in frame.schema.columns:
            # A METRIC column, not one of the compare operator's derived
            # ones: ``<m>__num`` also has a ``__prior`` sibling and is not a
            # metric anybody declared a caveat about.
            if not isinstance(column.ref, MetricRef) or "__" in column.name:
                continue
            if f"{column.name}{_PRIOR_SUFFIX}" in names:
                seen.setdefault(column.name)
    return tuple(seen)


def declared_non_comparabilities(
    pack: PackPort, frames: tuple[tuple[str, EvidenceFrame], ...]
) -> tuple[DeclaredNonComparability, ...]:
    """Every compared metric on this turn whose contract forbids the delta."""
    out: list[DeclaredNonComparability] = []
    for measure in compared_measures(frames):
        declared = declared_non_comparability(pack, measure)
        if declared is not None:
            out.append(declared)
    return tuple(out)


def comparison_phrase(spec: AnalysisSpec) -> str:
    """The period phrase every finding on this spec must use."""
    rendering = render_comparison(spec)
    return "vs prior period" if rendering is None else rendering.phrase


def window_mismatch_warning(spec: AnalysisSpec) -> str | None:
    """The turn-level warning for a length-mismatched comparison, if any.

    Two strengths, because two different things happen. A *material*
    mismatch distorts every additive total on the turn and is stated in
    full. An immaterial one (a calendar artifact inside
    :data:`LENGTH_TOLERANCE`) is still disclosed — the windows really are
    different lengths and the difference really is not normalized — but it
    withholds no impact and qualifies no finding, so it does not claim to.
    """
    rendering = render_comparison(spec)
    if rendering is None or not rendering.length_mismatch:
        return None
    if rendering.calendar_span is not None:
        noun = _CALENDAR_NOUN.get(rendering.calendar_span, f"{rendering.calendar_span}-month period")
        share = abs(rendering.current_days - rendering.comparison_days) / max(
            rendering.current_days, rendering.comparison_days
        )
        return (
            f"comparison_window_length: this is a whole {noun} against the whole {noun} before "
            f"it ({rendering.range_text}) — the same kind of period on both sides, which is how "
            f"a close is read. The calendar makes them "
            f"{abs(rendering.current_days - rendering.comparison_days)} day(s) different in "
            f"length ({rendering.comparison_days}d vs {rendering.current_days}d), a mechanical "
            f"share of at most {share:.1%} of any additive total; nothing is normalized for it "
            "and nothing is withheld for it."
        )
    if not rendering.material_length_mismatch:
        return (
            "comparison_window_length: the comparison window "
            f"({rendering.range_text}, {rendering.comparison_days}d) is "
            f"{abs(rendering.current_days - rendering.comparison_days)}d shorter or longer "
            f"than the analysis window ({rendering.current_days}d) — a calendar artifact "
            f"within {LENGTH_TOLERANCE:.0%}. Differences are not length-normalized; nothing "
            "is withheld for it."
        )
    return (
        "COMPARISON_WINDOW_MISMATCH: the comparison window "
        f"({rendering.range_text}, {rendering.comparison_days}d) is not the same length as the "
        f"analysis window ({rendering.current_days}d). Differences and percentage changes "
        "between them are dominated by the length difference and are not normalized; no "
        "impact figure is published for additive measures on this turn and their findings "
        "are qualified. Rate metrics are unaffected: a ratio over a window does not scale "
        "with the window's length."
    )
