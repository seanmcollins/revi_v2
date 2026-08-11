"""Reconciling a breakdown against the whole it broke down (§7.8)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from revi_investigation.application.calculation_glue import (
    CalculationResult,
)
from revi_investigation.application.execution import (
    bound_index,
)
from revi_investigation.application.findings import (
    find_primary_compare,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
)
from revi_investigation.application.ports import (
    TurnEvent,
)
from revi_investigation.application.rendering import MONEY_UNIT as _MONEY_UNIT_NAME
from revi_investigation.application.rendering import RATIO_UNIT as _RATIO_UNIT_NAME
from revi_investigation.application.rendering import (
    metric_label,
    money,
    plural,
    points,
    ratio_pct,
)
from revi_investigation.application.submit_turn.recording import _TurnRecording
from revi_investigation.application.submit_turn.types import _not_applicable, _TurnState
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import (
    Finding,
    Investigation,
)
from revi_investigation.domain.refinements import (
    DrillInto,
    Refinement,
    SetDimensions,
)
from revi_kernel.filters import (
    Scalar,
)
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import DimensionRef, MetricRef

#: How close a child's total must land to the parent finding it drilled to
#: be called agreed. The same half-percent the card/answer reconciliation
#: uses, for the same reason: below it the difference is rounding, above it
#: two published figures for one cell disagree and a reader must be told.
_CONTAINMENT_TOLERANCE = Decimal("0.005")


def _frame_money_total(
    frames: tuple[tuple[str, EvidenceFrame], ...],
) -> tuple[int | None, str | None]:
    """Sum the money column of the last frame that has one.

    "Last" because frames are listed in creation order, so the final
    money-bearing frame is the one after every transform — the frame the
    findings stage read.
    """
    level, _, measure = _frame_money_totals(frames)
    return level, measure


def _frame_money_totals(
    frames: tuple[tuple[str, EvidenceFrame], ...],
) -> tuple[int | None, int | None, str | None]:
    """``(level, movement, measure)`` for the last money-bearing frame.

    Both quantities, because a child answer can be either kind and the
    reconciliation has to know which it is holding. The level is the sum
    of the measure column — the figure the child card
    publishes. The movement is the sum of the compare operator's
    ``<measure>__delta`` column when this turn compared, and ``None`` when
    it did not: a turn with no prior side has no movement to tie out, and
    inventing one from a level is precisely the mismatch that made every
    drill of a comparison finding fail.
    """
    for _, frame in reversed(frames):
        for index, column in enumerate(frame.schema.columns):
            if column.unit != _MONEY_UNIT_NAME or column.name.endswith(
                (_PRIOR_COLUMN_SUFFIX, _DELTA_COLUMN_SUFFIX)
            ):
                continue
            measure = column.ref.id if isinstance(column.ref, MetricRef) else column.name
            return (
                _sum_money_column(frame, index),
                _sum_named_money_column(frame, f"{column.name}{_DELTA_COLUMN_SUFFIX}"),
                measure,
            )
    return None, None, None


def _sum_money_column(frame: EvidenceFrame, index: int) -> int:
    """Sum one money column in cents, skipping cells that are not amounts.

    Same guard as :func:`_sum_named_money_column` below, and for the same
    reason: a column carries the whole ``Scalar`` vocabulary, and a date or
    a label in a money-united column is not a smaller amount — it is not an
    amount. ``bool`` is refused ahead of ``int`` because Python counts
    ``True`` as 1.
    """
    total = 0
    for row in frame.rows:
        value = row[index]
        if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            continue
        total += int(value)
    return total


def _sum_named_money_column(frame: EvidenceFrame, name: str) -> int | None:
    """Sum one named integer column, or ``None`` when the frame has none.

    ``None`` and ``0`` are different answers here: a frame with no delta
    column produced no movement, while a frame whose deltas sum to zero
    produced a movement of nothing.
    """
    if name not in frame.schema.names:
        return None
    index = frame.schema.index_of(name)
    total = 0
    seen = False
    for row in frame.rows:
        value = row[index]
        if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            continue
        seen = True
        total += int(value)
    return total if seen else None


#: Column suffixes the compare operator adds beside a measure.
_PRIOR_COLUMN_SUFFIX = "__prior"
_DELTA_COLUMN_SUFFIX = "__delta"


#: Resolves a metric id to its contract unit, or ``None`` when this
#: reconciliation was handed no pack to ask. Supplied by the turn service,
#: which holds the pinned snapshot; defaulted so the pure function stays
#: callable from a test with nothing but two findings.
MetricUnitLookup = Callable[[str], str | None]


def _is_money_metric(metric_id: str, metric_unit: MetricUnitLookup | None) -> bool:
    """Does ``metric_id`` publish CENTS?

    ``int(Decimal("0.295082"))`` is ``0``, so a denial-rate finding read
    through the money path published "a figure of zero" rather than "no
    figure", and a drill off the product's own "drill into F1" chip
    reported ``RECONCILIATION_FAILED … parent F1=$0.00; child=$31,174.49
    (+311744900.0%)`` — two numbers that were never in the same unit.
    :func:`_parent_whole` had the mitigation (match on the child's own
    measure) and this readback did not.

    So the unit is ASKED FOR rather than inferred from the value's Python
    type. Where the pack can be reached that is the contract's own
    ``unit``; where it cannot, the structural rule stands in for it — money
    is cents-as-int everywhere in this system, so a Decimal carrying a
    fraction of a cent is, whatever else it is, not money.
    """
    if metric_unit is None:
        return True
    unit = metric_unit(metric_id)
    return unit is None or unit == _MONEY_UNIT_NAME


def _finding_money(
    finding: Finding, metric_unit: MetricUnitLookup | None = None
) -> tuple[int | None, int | None]:
    """``(level, movement)`` a parent finding published, in cents.

    ``impact_cents`` alone cannot answer this: on a movement finding it is
    the DELTA and on a concentration or ranking finding it is the LEVEL,
    and the reconciliation compared it against a child level either way.
    The named values disambiguate — a movement finding carries
    ``current_cents``/``delta_cents``, a compared scalar carries
    ``<metric>``/``<metric>__delta``, and a level-only finding carries
    neither delta.

    ``metric_unit`` makes the read UNIT-AWARE: a metric whose contract does
    not publish cents has no money figure here, and reading one out of it
    by truncation is how a rate parent became ``$0.00``.
    """
    named = dict(finding.values)

    def cents(key: str) -> int | None:
        value = named.get(key)
        if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            return None
        if isinstance(value, Decimal) and value != value.to_integral_value():
            # Cents are whole. A fraction of one is another unit wearing
            # the same Python type, and int() would silently truncate it.
            return None
        return int(value)

    level = cents("current_cents")
    delta = cents("delta_cents")
    for ref in finding.metric_refs:
        if not _is_money_metric(ref.id, metric_unit):
            continue
        if level is None:
            level = cents(ref.id)
        if delta is None:
            delta = cents(f"{ref.id}{_DELTA_COLUMN_SUFFIX}")
    if level is None and delta is None:
        # A finding that published only an impact figure: a level unless a
        # delta value named it otherwise, which is the pre-comparison case
        # this check was originally written for.
        return finding.impact_cents, None
    return level, delta


def _parent_whole(
    parent: Investigation,
    measure: str | None,
    has_figure: Callable[[Finding], bool],
) -> Finding | None:
    """The parent finding that speaks for the WHOLE population, if any.

    Only an undimensioned parent has one: an answer already cut by payer
    published twelve cells and no total, and calling any one of them "the
    whole" would reconcile a breakdown against a slice. One finding over a
    spec with no cuts is the figure a breakdown of it must recompose to.

    Matched on the child's OWN measure, which is load-bearing rather than
    tidy: money is cents-as-int on a finding and ``_finding_money``
    truncates, so a RATE finding carrying ``Decimal("0.294")`` reads back as
    ``0`` — "a figure of zero" rather than "no figure" — and a breakdown
    reconciled against it would publish ``RECONCILIATION_FAILED … parent
    F1=$0.00`` about two numbers that were never comparable. ``has_figure``
    is therefore supplied by the caller that knows which KIND of quantity
    this child holds, and money and rate never answer for each other.
    """
    if parent.spec.dimensions or measure is None:
        return None
    speaking = [
        finding
        for finding in parent.findings
        if measure in {ref.id for ref in finding.metric_refs} and has_figure(finding)
    ]
    return speaking[0] if len(speaking) == 1 else None


def _parent_finding(
    parent: Investigation,
    operators: tuple[Refinement, ...],
    measure: str | None,
    spec: AnalysisSpec | None,
    session_findings: Callable[[], Sequence[Finding]],
    has_figure: Callable[[Finding], bool],
) -> tuple[Finding, bool] | None:
    """The published figure this child descends from, and how it got there.

    Returns ``(finding, breakdown)`` — ``breakdown`` true when this turn cut
    an undimensioned parent rather than drilling a named handle. ``None``
    when nothing on screen contains this child's population.

    Shared by the money and rate paths: which finding a child descends from
    is a question about the THREAD, and it has the same answer whether the
    quantity being tied out is dollars or a ratio.

    **Matched on the child's own measure, on every branch.** Matching the
    immediate parent on the referent alone meant drilling F1 "State
    Medicaid MCO: 29.5% denial rate" from a money child located F1, read
    its ratio back through the money path as ``$0.00`` and published a red
    ``+311,744,900.0%`` disagreement between a rate and a pile of dollars.
    :func:`_parent_whole` documents exactly this trap. A parent that
    published no figure of the child's kind does not contain the child — it
    is a different measurement, and the caller says so as
    ``not_applicable``.
    """
    targets = {op.target.value for op in operators if isinstance(op, DrillInto)}
    finding = next(
        (
            f
            for f in parent.findings
            if f.referent.value in targets
            and measure is not None
            and measure in {ref.id for ref in f.metric_refs}
            and has_figure(f)
        ),
        None,
    )
    if finding is None and targets:
        # A thread drills what is ON SCREEN, and what is on screen is every
        # handle the session has published — not only the last turn's. A
        # CARC breakdown of a payer cell once reconciled against nothing
        # because the cell it decomposed had been published two turns
        # earlier: 13 cells summing to $176,112.25 beside a figure of
        # $176,112.25, and the product made the reader do the arithmetic.
        #
        # Strictly the SAME METRIC across turns, which the immediate-parent
        # case can take for granted and this one cannot: an older handle in
        # a long thread is as likely to measure something else, and tying a
        # denied-dollar drill out against a cash finding is a disagreement
        # this platform would then have to explain.
        finding = next(
            (
                f
                for f in session_findings()
                if f.referent.value in targets
                and measure is not None
                and measure in {ref.id for ref in f.metric_refs}
                and has_figure(f)
            ),
            None,
        )
    if finding is not None:
        return finding, False
    # A BREAKDOWN of a whole is the same containment question a drill asks,
    # and it used never to be asked: "Break that out by payer" off a
    # $1,193,126.92 July total published twelve cells that sum to
    # $1,193,126.92 and said ``not_applicable; this turn produced no
    # compared money frame`` — the arithmetic every reader of a breakdown
    # does by hand, available and withheld.
    if targets or not _splits_parent(spec, parent):
        return None
    whole = _parent_whole(parent, measure, has_figure)
    return None if whole is None else (whole, True)


def _splits_parent(spec: AnalysisSpec | None, parent: Investigation) -> bool:
    """Did this turn cut the parent's population along a new dimension?

    Read off the SPECS, not off the operator names. "Break that out by
    payer" reached the engine as a ``set_dimensions`` on one
    session and as something else on another, and the second reported
    ``this turn neither split nor drilled the parent's population`` about a
    turn that plainly had. A turn that gained a cut split the population,
    whichever operator got it there.
    """
    if spec is None:
        return False
    gained = {d.id for d in spec.dimensions} - {d.id for d in parent.spec.dimensions}
    return bool(gained)


#: Component-column suffixes a ratio contract carries beside its value.
_NUMERATOR_COLUMN_SUFFIX = "__num"
_DENOMINATOR_COLUMN_SUFFIX = "__den"


@dataclass(frozen=True, slots=True)
class _RateTotals:
    """A rate child's cells, ready to be recombined into their parent.

    A rate does not sum. ``29.5% + 22.9% + 18.8%`` is not a number, which is
    why the money reconciliation cannot simply be pointed at a ratio. What
    DOES recompose is the pair of components every ratio cell carries: the
    parent rate is
    ``Σ numerator / Σ denominator`` across the cells, weighted by each
    cell's own population without anybody having to say so.
    """

    measure: str
    numerator: Decimal
    denominator: Decimal
    cells: int
    #: Cells the §15 policy left without a usable numerator — nulled, or
    #: CLAMPED to a ceiling, which is the same fact wearing a number. Their
    #: population is known and their numerator is not, so they cannot enter
    #: the recomposition, and their denominator is carried here rather than
    #: dropped because it is exactly the slack that makes a gap honest.
    withheld_cells: int
    withheld_denominator: Decimal
    #: The tightest cap the policy itself puts on those numerators' SUM.
    #: A cell is bounded because its numerator is under the §15 threshold,
    #: so ``cells * (threshold - 1)`` is knowledge, not a guess — and it is
    #: the difference between an interval a reader can act on and one so
    #: wide a real disagreement could hide inside it.
    withheld_ceiling: Decimal

    @property
    def rate(self) -> Decimal | None:
        return self.numerator / self.denominator if self.denominator > 0 else None

    @property
    def bounds(self) -> tuple[Decimal, Decimal] | None:
        """What the parent rate COULD be, given the cells that were withheld.

        Every withheld numerator lies in ``[0, min(its denominator, the §15
        threshold - 1)]``, so the whole population's true rate lies between
        the two extremes of putting all of that slack, or none of it, in the
        numerator. With nothing withheld the interval collapses to the
        recomposed point and the ordinary tolerance decides.
        """
        total_den = self.denominator + self.withheld_denominator
        if total_den <= 0:
            return None
        return (
            self.numerator / total_den,
            (self.numerator + self.withheld_ceiling) / total_den,
        )


def _frame_rate_totals(
    frames: tuple[tuple[str, EvidenceFrame], ...],
    threshold: int | None,
) -> _RateTotals | None:
    """Recompose the last ratio-bearing frame's cells into one rate.

    "Last" for the same reason the money reader uses it: frames are listed
    in creation order, so the final one is the frame the findings stage
    read. Nothing is inferred where the components do not exist — a ratio
    published without its numerator and denominator columns cannot be
    recomposed, and saying so is the honest outcome.

    **A ceiling is not a numerator.**
    The §15 policy publishes a small numerator as ``threshold - 1`` rather
    than dropping the cell, so a bounded cell arrives carrying the integer
    ``10`` and reads exactly like a measurement. Summing those tens gave
    12 live payer cells recomposing to 13.5% against a parent of 12.8% and
    a ``RECONCILIATION_FAILED`` about a gap that was entirely the policy's.
    So bounded cells are recognised by :func:`bound_index` — the same
    governed definition the findings and charts use — and contribute their
    population, never their ceiling.
    """
    for _, frame in reversed(frames):
        bounded = bound_index(frame, threshold) if threshold is not None else {}
        for column in frame.schema.columns:
            if column.unit != _RATIO_UNIT_NAME or "__" in column.name:
                continue
            num_col = f"{column.name}{_NUMERATOR_COLUMN_SUFFIX}"
            den_col = f"{column.name}{_DENOMINATOR_COLUMN_SUFFIX}"
            names = frame.schema.names
            if num_col not in names or den_col not in names:
                continue
            idx_num, idx_den = frame.schema.index_of(num_col), frame.schema.index_of(den_col)
            numerator = denominator = withheld_den = Decimal(0)
            cells = withheld = 0
            for index, row in enumerate(frame.rows):
                den = _as_decimal(row[idx_den])
                if den is None or den <= 0:
                    continue
                num = _as_decimal(row[idx_num])
                if num is None or column.name in bounded.get(index, {}):
                    withheld += 1
                    withheld_den += den
                    continue
                numerator += num
                denominator += den
                cells += 1
            if cells == 0 and withheld == 0:
                continue
            cap = withheld_den
            if threshold is not None:
                cap = min(cap, Decimal(withheld * (threshold - 1)))
            measure = column.ref.id if isinstance(column.ref, MetricRef) else column.name
            return _RateTotals(
                measure=measure,
                numerator=numerator,
                denominator=denominator,
                cells=cells,
                withheld_cells=withheld,
                withheld_denominator=withheld_den,
                withheld_ceiling=cap,
            )
    return None


def _as_decimal(value: Scalar) -> Decimal | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return Decimal(value)


def _finding_rate(finding: Finding, measure: str) -> Decimal | None:
    """The ratio a parent finding published for ``measure``, if any."""
    for name, value in finding.values:
        if name != measure:
            continue
        return _as_decimal(value)
    return None


@dataclass(frozen=True, slots=True)
class Containment:
    """A child's tie-out against the whole it descends from.

    ``anchor`` is the half the summary cannot carry: the parent's own
    level, restated ON THE CHILD as a mandatory disclosure. A breakdown
    that states 29.5%, 22.9% and 18.8% by payer and never once says the
    12.8% it descends from leaves a reader who lands on it — from a
    Monitors tile, say — believing denial rates run 19-29%. ``None`` when
    there is no parent LEVEL to anchor to (a movement tied out against a
    movement anchors nothing).
    """

    summary: str
    passed: bool
    anchor: str | None = None


def containment_reconciliation(
    parent: Investigation,
    calculation: CalculationResult,
    operators: tuple[Refinement, ...],
    spec: AnalysisSpec | None = None,
    #: The session's published findings, as a THUNK: only a drill whose
    #: handle is not on the immediate parent reads it, and on a breakdown —
    #: the shape this function was extended for — the store round trip it
    #: costs would buy nothing.
    session_findings: Callable[[], Sequence[Finding]] = tuple,
    #: The §15 threshold this turn ran under. A ceiling is only recognisable
    #: against the threshold that produced it, and a ceiling summed as a
    #: numerator turns the suppression policy into a false disagreement.
    suppression_threshold: int | None = None,
    #: The pinned pack's unit for a metric id. Without it a rate finding's
    #: ``Decimal("0.295082")`` truncates to ``0`` and reconciles as a dollar
    #: figure of nothing.
    metric_unit: MetricUnitLookup | None = None,
) -> Containment | None:
    """Reconcile a drill against the PARENT FINDING it was launched from.

    Clicking "drill into DNFB accumulation: Northgate general-surgery
    discharges" published ``dnfb_dollars = $195,873.92``; clicking the
    platform's own "drill into F1" chip on that same answer published
    ``$178,216.82`` — same metric id, same contract version, same pack,
    same window, same scope, both graded direct, neither warning — and the
    child's reconciliation read ``not_applicable; this turn produced no
    compared money frame to reconcile against the parent``. The predicate
    was true and beside the point: it asked whether there was a *compare*
    frame and an *undimensioned parent total*, when what the reader had in
    front of them was one finding's figure and one drill's.

    The mirror-image symptom, same predicate: a drill decomposing a
    parent's $410,166.15 into nine categories that sum to $410,166.15
    exactly reported ``not_applicable; the parent investigation holds no
    undimensioned 'denied_dollars' total`` — a perfect tie-out available
    and withheld.

    So a child scoped to a parent finding's cell reconciles against that
    finding's own published figure, and publishes both numbers, the delta
    and the percentage **even when they agree**: "we checked and it agreed"
    is the verdict this whole grammar exists to be able to say.

    Returns a :class:`Containment`, or ``None`` when this turn drilled no
    parent finding that published a figure of the child's own kind — in
    which case the caller's existing verdicts stand.

    **Rates recompose; they do not sum.** The money seam above holds; the
    rate seam did not exist. "For July 2026 on a service basis, the denial
    rate came in at 12.8% (F1)" followed by "Break that down by payer"
    reported *"this turn produced no compared money frame to reconcile
    against the parent, so reconciliation is not applicable"*, published
    29.5% / 22.9% / 18.8% plus four ceilings, and never restated the 12.8%
    it descends from — and a rate drill is the one an RCM director performs
    daily.

    A rate breakdown therefore ties out through its COMPONENTS: each cell
    carries its own numerator and denominator, and ``Σ num / Σ den`` is the
    parent rate weighted by each cell's population. Where the §15 policy
    withheld a numerator the cell cannot enter that sum, so the verdict
    states the interval its population could move the parent inside, and
    calls a gap inside that interval what it is — the suppression, not a
    disagreement.

    **The two sides must be the same KIND of quantity.** ``impact_cents``
    on a comparison finding is a MOVEMENT and the child frame's total is a
    LEVEL, so every drill of a top mover — the single most common action in
    a close — reported ``RECONCILIATION_FAILED`` against numbers that
    agreed perfectly: parent F5 "$82,623.40 vs prior
    period" against a child of $102,409.87, whose residual $19,786.47 was
    exactly the prior side the parent had differenced away. So the quantity
    is chosen before it is compared: movement against movement when both
    sides compare, level against level otherwise, and ``not_applicable``
    naming the mismatch when they cannot be matched at all — never
    ``failed``, which is a claim that two figures for one cell disagree.
    """
    child_level, child_delta, measure = _frame_money_totals(calculation.frames)
    if child_level is None:
        return _rate_containment(
            parent, calculation, operators, spec, session_findings, suppression_threshold
        )
    located = _parent_finding(
        parent,
        operators,
        measure,
        spec,
        session_findings,
        lambda f: any(value is not None for value in _finding_money(f, metric_unit)),
    )
    if located is None:
        return _rate_containment(
            parent, calculation, operators, spec, session_findings, suppression_threshold
        )
    finding, breakdown = located
    parent_level, parent_delta = _finding_money(finding, metric_unit)
    if parent_level is None and parent_delta is None:
        # The handle on screen published no dollar figure. A turn carrying
        # both a money column and a rate one still has a rate to tie out,
        # so the rate path gets its turn rather than the seam closing here.
        return _rate_containment(
            parent, calculation, operators, spec, session_findings, suppression_threshold
        )
    same_measure = measure is not None and measure in {ref.id for ref in finding.metric_refs}
    if not same_measure:  # pragma: no cover - _parent_finding now matches on measure
        scope = (
            f"{metric_label(measure) if measure else 'this answer'} against "
            f"{finding.referent.value}"
        )
    elif breakdown:
        cells = max((len(frame.rows) for _, frame in calculation.frames), default=0)
        scope = (
            f"the {cells} {plural(cells, 'row')} this breakdown published, summed, against "
            f"the whole {finding.referent.value} measured"
        )
    else:
        scope = (
            f"the same measure ({metric_label(measure or '')}) over the cell "
            f"{finding.referent.value} names"
        )
    if parent_delta is not None and child_delta is not None:
        kind, parent_cents, child_cents = "movement vs movement", parent_delta, child_delta
    elif parent_level is not None:
        kind, parent_cents, child_cents = "level vs level", parent_level, child_level
    else:
        # The parent published a movement and this turn published only a
        # level. Nothing disagrees; there is simply nothing to tie out.
        return Containment(
            summary=_not_applicable(
                f"the parent finding {finding.referent.value} published a movement "
                f"({money(parent_delta or 0)} vs its prior period) and this answer published a "
                f"level ({money(child_level)}) — two different kinds of quantity, so neither "
                "contains the other. Ask this same question against that prior period to tie "
                "the two out."
            ),
            passed=True,
        )
    delta = child_cents - parent_cents
    fraction = Decimal(delta) / Decimal(abs(parent_cents) or 1)
    passed = abs(fraction) <= _CONTAINMENT_TOLERANCE
    summary = (
        f"status={'passed' if passed else 'failed'}; "
        f"this {'breakdown' if breakdown else 'drill'} was checked against the parent as a "
        f"{kind}. The parent {finding.referent.value} published {money(parent_cents)} and this "
        f"answer comes to {money(child_cents)}, a difference of {money(delta)} "
        f"({float(fraction):+.1%}). What was compared: {scope}."
    )
    return Containment(
        summary=summary,
        passed=passed,
        anchor=(
            _parent_anchor(
                finding,
                measure,
                money(parent_level),
                breakdown=breakdown,
                recombines="by addition",
            )
            if same_measure and parent_level is not None
            else None
        ),
    )


def measure_mismatch_reason(
    findings: Sequence[Finding],
    operators: tuple[Refinement, ...],
    measure: str | None,
) -> str | None:
    """Why a drill had nothing to tie out: the handle measures something else.

    The thread is real — the analyst clicked a chip this product put on
    that finding — and there is still nothing to reconcile: F1 published a
    denial RATE and the drill published dollars, so neither contains the
    other. Before the measure predicate on :func:`_parent_finding` this
    case produced a red ``failed`` banner asserting that two figures for
    one cell disagreed (``parent F1=$0.00; child=$31,174.49;
    +311,744,900.0%``) over two quantities that were never the same kind.
    It now produces nothing at all, and the caller's generic "this turn
    produced no compared money frame" is false on a turn that plainly holds
    one — so this is the sentence that goes in its place.

    ``None`` when no drilled handle is on screen, or when the handle does
    publish the child's measure: those are the caller's other verdicts.
    """
    targets = {op.target.value for op in operators if isinstance(op, DrillInto)}
    if not targets or measure is None:
        return None
    finding = next((f for f in findings if f.referent.value in targets), None)
    if finding is None:
        return None
    published = sorted({ref.id for ref in finding.metric_refs})
    if measure in published:
        return None
    named = ", ".join(metric_label(mid) for mid in published) or "no measure of its own"
    return (
        f"the finding this answer drilled into ({finding.referent.value}) published {named}, "
        f"and this answer publishes {metric_label(measure)} — two different measurements of "
        "that cell rather than a part and its whole, so neither contains the other. Ask for "
        f"{metric_label(measure)} over the parent population to tie the two out."
    )


def _parent_anchor(
    finding: Finding,
    measure: str | None,
    figure: str,
    *,
    breakdown: bool,
    recombines: str,
) -> str:
    """The parent's own level, restated on the child.

    The reconciliation summary states it too, and that is not enough: a
    reader who lands on a breakdown from a Monitors tile reads the cells, and
    the seam verdict is a line they have to go looking for. This sentence is
    a MANDATORY disclosure — composed here from a figure the parent already
    certified, published verbatim ahead of whatever the composer writes, and
    exempt from the grounding pass for the same reason every other mandatory
    disclosure is (it carries no number that is not already certified).
    """
    opening = (
        "decomposes a population this session already measured"
        if breakdown
        else "drills into a cell of a population this session already measured"
    )
    label = metric_label(measure) if measure else "that measure"
    return (
        f"parent_level: this answer {opening} — {label} over the parent population is "
        f"{figure} ({finding.referent.value}). The cells below are parts of that {figure}: they "
        f"recombine to it {recombines}, and none of them is a second measurement of the whole."
    )


def _rate_containment(
    parent: Investigation,
    calculation: CalculationResult,
    operators: tuple[Refinement, ...],
    spec: AnalysisSpec | None,
    session_findings: Callable[[], Sequence[Finding]],
    suppression_threshold: int | None,
) -> Containment | None:
    """Recompose a RATE child into the rate it was cut out of.

    The money path answers "do the parts sum to the whole". A rate has no
    such question — ``29.5% + 22.9% + 18.8%`` is not a number — so the
    question asked here is the one an analyst actually asks: *given these
    cells, what is the population rate, and is it the one the parent
    published?* Answered from each cell's own numerator and denominator,
    which is a weighted recomposition without anybody having to weight
    anything.

    Suppression is stated rather than absorbed. A cell whose numerator the
    §15 policy withheld keeps its population and loses its contribution, so
    the recomposed figure is one point in an interval — and a parent inside
    that interval AGREES with these cells, while one outside it does not.
    Calling the first case ``failed`` would publish a disagreement between
    two figures that are both correct.
    """
    totals = _frame_rate_totals(calculation.frames, suppression_threshold)
    if totals is None:
        return None
    located = _parent_finding(
        parent,
        operators,
        totals.measure,
        spec,
        session_findings,
        lambda f: _finding_rate(f, totals.measure) is not None,
    )
    if located is None:
        return None
    finding, breakdown = located
    parent_rate = _finding_rate(finding, totals.measure)
    recomposed = totals.rate
    if parent_rate is None:
        return None
    if recomposed is None:
        # Every cell's numerator was withheld: the components exist and
        # none of them may be read, so there is no recomposition to state.
        return Containment(
            summary=_not_applicable(
                f"every one of the {totals.withheld_cells} "
                f"{plural(totals.withheld_cells, 'cell')} this answer published for "
                f"{metric_label(totals.measure)} had its numerator withheld by the small-cell "
                "policy, so the parent rate cannot be recomposed from them. The parent figure "
                f"{finding.referent.value} stands as published, at {ratio_pct(parent_rate)}."
            ),
            passed=True,
            anchor=_parent_anchor(
                finding,
                totals.measure,
                ratio_pct(parent_rate),
                breakdown=breakdown,
                recombines="through their own denominators, not by addition",
            ),
        )
    interval = totals.bounds
    delta = recomposed - parent_rate
    fraction = delta / (abs(parent_rate) or Decimal(1))
    within_tolerance = abs(fraction) <= _CONTAINMENT_TOLERANCE
    explained = (
        interval is not None
        and totals.withheld_cells > 0
        and interval[0] <= parent_rate <= interval[1]
    )
    passed = within_tolerance or explained
    cells_text = (
        f"the {totals.cells} measurable {plural(totals.cells, 'cell')} this "
        f"{'breakdown' if breakdown else 'drill'} published, recombined through their own "
        f"denominators, against the whole {finding.referent.value} measured"
    )
    withheld_text = ""
    if totals.withheld_cells:
        assert interval is not None
        withheld_text = (
            f" A further {totals.withheld_cells} "
            f"{plural(totals.withheld_cells, 'cell')}, covering "
            f"{totals.withheld_denominator:,f} of the population, had the numerator suppressed "
            "by the small-cell policy, so the rate over the whole population lies somewhere "
            f"between {ratio_pct(interval[0])} and {ratio_pct(interval[1])}."
        )
        if explained and not within_tolerance:
            # Said, not left to be inferred: a reader looking at a passing
            # verdict beside a 1.1-point delta is owed the reason it is not
            # a disagreement, in the same line.
            withheld_text += (
                " The parent sits inside that range, so the gap is the suppression and not "
                "a disagreement."
            )
    # ``passed_with_suppression`` is the grammar's own third state
    # (:class:`revi_calculation.operators.reconcile.ReconciliationStatus`),
    # and it is exactly this case: the two figures do not meet at a point
    # and they do not disagree, because the §15 policy is standing between
    # them. Calling it plain ``passed`` would overstate the tie-out;
    # ``failed`` would invent a conflict.
    status = "failed"
    if within_tolerance:
        status = "passed"
    elif explained:
        status = "passed_with_suppression"
    summary = (
        f"status={status}; "
        f"this {'breakdown' if breakdown else 'drill'} was checked against the parent by "
        f"recomposing the rate. The parent {finding.referent.value} published "
        f"{ratio_pct(parent_rate)} and the cells recompose to {ratio_pct(recomposed)} "
        f"({totals.numerator:,f} over {totals.denominator:,f}), a difference of "
        # Signed: ``points`` is unsigned by design (a rate's movement is
        # said with a direction word beside it), and a reconciliation delta
        # has no such word — an unsigned "1.5 points" beside "-11.6%" reads
        # as two different answers to one question.
        f"{'-' if delta < 0 else '+'}{points(delta)} ({float(fraction):+.1%})."
        f"{withheld_text} What was compared: {cells_text}."
    )
    return Containment(
        summary=summary,
        passed=passed,
        anchor=_parent_anchor(
            finding,
            totals.measure,
            ratio_pct(parent_rate),
            breakdown=breakdown,
            recombines="through their own denominators, not by addition",
        ),
    )


class _Reconciliation(_TurnRecording):
    """Checking a child answer's totals against its parent's, and saying so
    when they disagree."""

    async def _reconcile_with_parent(
        self,
        parent: Investigation,
        plan: InvestigationPlan,
        calculation: CalculationResult,
        operators: tuple[Refinement, ...],
        warnings: list[str],
        state: _TurnState,
        spec: AnalysisSpec | None = None,
    ) -> str:
        """On splits (SetDimensions) and drills (DrillInto), check that the
        child's cells sum to the parent totals the analyst was shown.

        Every exit from this method says *something*. It used to return
        ``None`` from four different paths, and the caller returned ``None``
        for a fifth — with the wire type ``string | null`` and no third
        state, "we checked and it agreed" and "we never checked" were the
        same value. That produced ``None`` on a turn that drilled three
        payers and pivoted the measure, and ``"status=passed"`` on a turn
        that was a no-op: the one that looked reassuring was the one that
        had done nothing.

        The grammar is the existing one: ``status=<verdict>`` with
        semicolon-separated detail, so ``not_applicable`` carries a
        machine-readable ``reason=`` naming which path was taken.
        """
        # Split-or-drill is read off the SPECS as well as the operators: a
        # turn that gained a cut split the parent's population whichever
        # operator got it there, and reading the operators alone published
        # "this turn neither split nor drilled" over a turn that plainly had.
        if not any(
            isinstance(op, (SetDimensions, DrillInto)) for op in operators
        ) and not _splits_parent(spec, parent):
            return _not_applicable(
                "this question neither split nor drilled into the earlier answer's population"
            )
        # A drill of a named parent finding reconciles against THAT
        # finding's published figure, whether or not this turn produced a
        # compared money frame and whether or not the parent kept an
        # undimensioned total. Tried first because it is the
        # comparison the reader actually made — two figures on two
        # consecutive screens — and the sum-of-cells check below is the
        # comparison the lineage makes.
        # Only a drill whose handle the immediate parent did not publish
        # needs the session's older findings, and that is the rarer half of
        # the rarer branch — a breakdown never reads them. The registry is
        # therefore fetched under exactly that condition rather than as an
        # argument, which is a store round trip on every split turn.
        drilled = {op.target.value for op in operators if isinstance(op, DrillInto)}
        elsewhere: tuple[Finding, ...] = ()
        if drilled and not any(f.referent.value in drilled for f in parent.findings):
            elsewhere = tuple(
                entry.finding
                for entry in await self._referents.list_for_session(parent.session_id)
                if entry.finding is not None
            )
        containment = containment_reconciliation(
            parent,
            calculation,
            operators,
            spec,
            session_findings=lambda: elsewhere,
            suppression_threshold=self._executor.suppression_threshold,
            # The pinned snapshot's own unit for each metric, so a ratio
            # finding is never read back through the money path.
            metric_unit=self._metric_unit,
        )
        if containment is not None:
            if not containment.passed:
                await self._fail_reconciliation(containment.summary, warnings, state)
            # The parent's own level, restated on the child as a mandatory
            # disclosure. The seam verdict states it too and that is a line
            # a reader has to go looking for; this one is published ahead of
            # the prose, so a breakdown can never publish 29.5% / 22.9% /
            # 18.8% without saying what they are parts of.
            if containment.anchor is not None:
                warnings.append(containment.anchor)
            return containment.summary
        shape = find_primary_compare(plan, calculation)
        if shape is None:
            # A drill whose handle measures something else says so: "no
            # compared money frame" is a true sentence about the wrong
            # question on a turn that published dollars off a rate finding.
            mismatch = measure_mismatch_reason(
                (*parent.findings, *elsewhere),
                operators,
                _frame_money_totals(calculation.frames)[2],
            )
            return _not_applicable(
                mismatch
                or "this answer produced no compared dollar figure to reconcile against the "
                "earlier one"
            )
        measure = shape.money_measure
        parent_totals: EvidenceFrame | None = None
        for key in parent.frame_refs:
            frame = await self._frames.get(key)
            if frame is None or measure not in frame.schema.names:
                continue
            if any(isinstance(col.ref, DimensionRef) for col in frame.schema.columns):
                continue
            parent_totals = frame
            if f"{measure}__prior" in frame.schema.names:
                break  # prefer the compare totals (they carry the prior side)
        if parent_totals is None:
            return _not_applicable(
                f"the earlier answer holds no overall {metric_label(measure)} total, "
                "unbroken by any cut, to reconcile against"
            )
        if parent_totals.watermark != shape.frame.watermark:
            return _not_applicable(
                "the earlier answer's totals were read from a different data load than this "
                "one, so the two are not the same measurement to compare"
            )
        measures: tuple[str, ...] = (measure,)
        if (
            f"{measure}__prior" in parent_totals.schema.names
            and f"{measure}__prior" in shape.frame.schema.names
        ):
            measures = (measure, f"{measure}__prior")
        verdict = self._transforms.reconcile(parent_totals, shape.frame, measures=measures)
        if not verdict.passed:
            await self._fail_reconciliation(verdict.summary, warnings, state)
        return verdict.summary

    def _metric_unit(self, metric_id: str) -> str | None:
        """The pinned pack's declared unit for a metric id, or ``None``.

        One lookup, one authority. The reconciliation asks it before it
        reads a figure out of a finding, because "is this cents?" is a
        contract question and answering it from the Python type of the
        value is what published ``parent F1=$0.00`` over a 29.5% rate.
        """
        contract = self._pack.metric(metric_id)
        return None if contract is None else str(contract.unit)

    async def _fail_reconciliation(
        self, summary: str, warnings: list[str], state: _TurnState
    ) -> None:
        """One coded warning + one event for every failed reconciliation.

        Shared so the containment check and the sum-of-cells check cannot
        report the same class of disagreement two different ways.
        """
        warnings.append(f"RECONCILIATION_FAILED: {summary}")
        await self._events.publish(
            TurnEvent(
                kind="warning",
                turn_id=state.turn_id,
                payload={"code": "RECONCILIATION_FAILED", "detail": summary},
            )
        )
