"""Verifying the premise a question asserts, before the question is answered."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from revi_calculation_contracts.contract import SignConvention
from revi_investigation.application.calculation_glue import (
    CalculationResult,
)
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.comparison import (
    ComparisonMaturity,
    ComparisonRendering,
    DeclaredNonComparability,
    comparison_maturity,
    declared_non_comparability,
    render_comparison,
)
from revi_investigation.application.execution import (
    BoundedCell,
)
from revi_investigation.application.findings.bounds import _is_additive
from revi_investigation.application.findings.shapes import (
    _compared_measures,
    _dimension_columns,
    _unit_of,
    as_number,
)
from revi_investigation.application.findings.windows import _PRIOR_SUFFIX
from revi_investigation.application.planning import InvestigationPlan, frame_window
from revi_investigation.application.rendering import (
    MONEY_UNIT as _MONEY_UNIT,
)
from revi_investigation.application.rendering import (
    RATIO_UNIT as _RATIO_UNIT,
)
from revi_investigation.application.rendering import (
    format_value,
    magnitude,
    metric_label,
    ratio_pct,
)
from revi_investigation.application.window_maturity import WindowMaturity
from revi_investigation.domain.context import (
    AnalysisSpec,
    wanted_delta_sign,
)
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame
from revi_kernel.scope import AbsoluteRange, TimeWindow

#: How far a movement may sit either side of the size a question ASSERTS
#: and still count as that size, as a fraction of the asserted change.
#:
#: The band is two-sided on purpose. A one-sided floor at half the asserted
#: change confirms "doubled" for anything from +50% upward — a denial rate
#: going 7.4% → 12.8% publishes "Premise confirmed … It happened" at high
#: confidence with ``asserted_multiple: 2.0`` and ``pct_change: 0.726`` in
#: the same values array, and a 10x move confirms a doubling too.
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
    #: The ceiling on each side, when the §15 policy withheld its numerator.
    #: A movement between two ceilings is the ratio of the two POPULATIONS
    #: and carries no information about the measure.
    current_bound: BoundedCell | None = None
    prior_bound: BoundedCell | None = None
    #: The panel asymmetry this plan reported, when it reported one.
    #: Borrowed from whichever frame carries a denominator: an additive
    #: money measure has no panel of its own and is distorted by an immature
    #: one exactly as much as a rate is.
    immature: ComparisonMaturity | None = None
    #: The question asserted a SIZE nothing could parse.
    size_asserted_unparsed: bool = False
    #: The metric CONTRACT declares these two windows non-comparable. The
    #: third leg of the integrity read: bounds and panel maturity are signals
    #: measured off the frame, and a pack author can also simply declare that
    #: a delta between two windows of this metric is not a result. See
    #: :class:`~...comparison.DeclaredNonComparability`.
    not_comparable: DeclaredNonComparability | None = None
    #: The two windows are materially different LENGTHS and the measure is
    #: additive (the same leg). ``comparison.py`` withholds the impact and
    #: qualifies every finding for this, and the premise verdict must not go
    #: on saying "confirmed" over it.
    length_mismatched: ComparisonRendering | None = None
    #: The window the premise probe actually read, when it declared one of
    #: its own. ``None`` is the investigation window. A premise probe is
    #: cloned from the playbook probe whose breakdown the findings layer
    #: publishes, so it inherits that probe's window — and the verdict
    #: sentence has to state the period it was checked over.
    window: TimeWindow | None = None
    #: That window's own settling verdict — the fourth integrity leg, and
    #: the one the playbook path needs. The panel rule above compares two
    #: sides and is blind to an ADDITIVE money measure (there is no
    #: denominator to count), so without this "we have a denial spike"
    #: measures denied dollars over the least settled window in the load and
    #: publishes "It did not happen: denied dollars fell 39.5%" as a flat
    #: refutation — while the DIRECT path refuses the equivalent comparison
    #: with "the two windows are not equally settled … 27.0%".
    window_immature: WindowMaturity | None = None

    @property
    def magnitude_short(self) -> bool:
        """The direction matched and the SIZE did not."""
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
            or self.window_immature is not None
            or self.not_comparable_windows
            or self.size_asserted_unparsed
        )

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


def _premise_measure(spec: AnalysisSpec, compared: tuple[str, ...]) -> str | None:
    """The metric the PREMISE names, out of the ones this frame compared.

    Preferring whichever compared column is money answers a different
    question: an analyst asking about denial RATE gets, as F1 at high
    confidence, "You asked about a doubling in denied dollars. It did not
    happen — denied dollars fell $829,506.94, -72.7%" — a true sentence
    about a metric nobody asked about, published as the verdict on a rate
    that had risen.

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

    Accepting **only** a compare step whose first input starts with
    ``premise`` misses the ordinary plan shape ``['main', 'main__prior']`` —
    an undimensioned comparison that measures exactly the aggregate the
    premise is about. A question that states a movement and plans no
    dimensions still has its premise sitting right there in the frame;
    refusing to look at it publishes a long narrative about cells while
    denials have FALLEN 4.2%, with no contradiction anywhere in it.

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


#: How much of a premise's window one unsettled month must account for
#: before it decides the verdict. A month is a settlement artifact of the
#: window it dominates: July is 55% of 2026-06-08..2026-08-02 and 8% of a
#: year, and a caveat that fires on the second would be a caveat on every
#: annual question this warehouse can answer.
_UNSETTLED_MONTH_SHARE = Decimal("0.25")


def _unsettled_part(
    window: AbsoluteRange, verdicts: Mapping[AbsoluteRange, WindowMaturity] | None
) -> WindowMaturity | None:
    """This window's settling verdict — the whole window, or a month of it.

    The exact window first: for the ordinary shape (one named month) that
    is the whole answer. Then the months INSIDE it, because a window wider
    than a month blends settled and settling data and passes as a blend —
    "denied dollars fell 39.5%" over 2026-06-08..2026-08-02 is a fully
    settled June measured against a July that is a quarter adjudicated, and
    the blended share (68%) clears every threshold while the delta is an
    artifact.
    """
    if not verdicts:
        return None
    exact = verdicts.get(window)
    if exact is not None:
        return exact
    days = Decimal(window.day_length)
    for month, verdict in verdicts.items():
        if month.start < window.start or month.end > window.end:
            continue  # only a month this window holds WHOLE
        if Decimal(month.day_length) / days >= _UNSETTLED_MONTH_SHARE:
            return verdict
    return None


def verify_premise(
    plan: InvestigationPlan,
    calculation: CalculationResult,
    spec: AnalysisSpec,
    pack: PackPort,
    *,
    premise_prefix: str,
    suppression_threshold: int | None = None,
    window_maturity: Mapping[AbsoluteRange, WindowMaturity] | None = None,
) -> PremiseCheck | None:
    """Check the asserted aggregate movement, before anything explains it.

    Returns ``None`` when the question asserted nothing (the overwhelming
    majority of turns), or when no frame measured the metric the premise
    names — an unverifiable premise is not a refuted one, and claiming
    otherwise would be the same failure in the opposite direction.

    **The verdict reads what the integrity layer published.** Reading
    ``row[index_of(measure)]`` and ``__prior`` off the frame and consulting
    nothing else publishes confident verdicts over quantities the rest of
    the engine has already marked unmeasurable. Four separate ways:

    * **Bounds.** "Denial rate rose 157.1%, past the 100.0% a doubling
      assumes: 13.9% → 35.7%" — beside the same answer's own
      ``SUPPRESSION_BOUNDED`` warning that both sides are ceilings over one
      clamped numerator of 10. 157.1% is exactly 72/28 - 1, the ratio of the
      two DENOMINATORS, carrying no denial information at all.
    * **Panel maturity.** "It did not happen — denied dollars fell
      $829,506.94, -72.7%" beside "this window holds 27.0% of the panel the
      comparison window does", when denied dollars per adjudicated claim
      actually went $199.39 → $201.81, +1.2%. The guard fires on frames
      carrying a ``_den`` column and a premise probe may measure an additive
      money measure, so the panel is BORROWED from the sibling frame that
      does carry one.
    * **Unparsed size.** ``premise_holds: true`` beside
      ``premise_magnitude: "unverifiable"``, rendered "Premise confirmed",
      over a question that said HALVE.
    * **Comparability** (the third leg). The two above are *signals measured
      off the frame*; whether two windows may be differenced at all is a
      separate question the payload already answers — in the metric
      contract's own governed caveat and in the length-mismatch machinery.
      Skipping it publishes "Premise confirmed: net collection rate 72.5% →
      18.5%, fell 53.9 points" at ``direct``/``high`` beside that payload's
      own caution that "two windows of unequal maturity are not comparable
      as levels". No panel guard fires and none should:
      ``net_collection_rate``'s denominator is contract-expected DOLLARS, so
      there is no adjudicated-record asymmetry to see, and the contract is
      the only thing on the turn that knows.
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
        # against was derived from THAT window.
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
        # …and the fourth leg: has the window this was checked over
        # finished settling at all? Keyed on the range the
        # premise probe actually read — a playbook probe's own window, not
        # the one the header announced — because judging the announced
        # window is how this guard came to be silent on the playbook path.
        window_immature = _unsettled_part(
            (window or spec.context.window).range, window_maturity
        )
        blocked = any(
            (
                bounded,
                immature is not None,
                window_immature is not None,
                not_comparable is not None,
                length_mismatched,
            )
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
            window_immature=window_immature,
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

    Two-sided by construction. Asking only whether the movement is at least
    half the claim makes "it doubled" true of +72.6% and of +900% alike.
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


def then(sentence: str, tail: str) -> str:
    """``sentence`` followed by ``tail``, with exactly one stop between them.

    :func:`premise_verdict_sentence` returns a finished sentence — some
    branches end on "…Ask again once the thinner side matures." — and every
    caveat that quotes it appended a full stop of its own. The CSV export
    and the warning register both published *"…matures.. The question's own
    assumption…"*, a doubled period in the middle of the most carefully
    written prose this engine produces.

    Fixed where the composition happens rather than where it is rendered:
    a client that repairs its own copy leaves the wire, the export and the
    trace saying something the product would not write.
    """
    lead = sentence.rstrip()
    if not lead.endswith((".", "!", "?", ":", ";")):
        lead = f"{lead}."
    return f"{lead} {tail}"


def premise_verdict_sentence(
    premise: PremiseCheck, spec: AnalysisSpec, *, comparison: ComparisonRendering | None
) -> str:
    """What the question assumed, and what the aggregate did — deterministic.

    Composed here, from the premise probe's own figures and the
    interpretation's closed ``direction`` set, and never
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
        # one sentence, with the shortfall stated as arithmetic — otherwise
        # "Premise confirmed … It happened: 7.4% → 12.8%, 72.6%" is
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

    Each arm does the arithmetic OUT LOUD, because the reason a reader must
    not act on the number is itself the finding — "157.1%" is a true
    division of two figures neither of which is a measurement, and saying so
    is more useful than withholding it.
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
    if premise.window_immature is not None:
        # The same refusal the direct path makes, made on the playbook path
        # and in the same words: nobody may be told their denial spike did
        # not happen because three quarters of the window has not come back
        # yet.
        immature_window = premise.window_immature
        checked = premise.window.range if premise.window is not None else None
        part = (
            f"the window it was checked over ({immature_window.window.start.isoformat()}.."
            f"{immature_window.window.end.isoformat()})"
            if checked is None or checked == immature_window.window
            else (
                f"the window it was checked over ({checked.start.isoformat()}.."
                f"{checked.end.isoformat()}) still runs into a period "
                f"({immature_window.window.start.isoformat()}.."
                f"{immature_window.window.end.isoformat()}) that"
            )
        )
        return (
            f"You asked about {noun} in {label}. It cannot be checked yet — {part} "
            "has not finished settling: it holds "
            f"{immature_window.population:,} settled record(s) where a window of that length "
            f"normally holds about {immature_window.expected:,}, "
            f"{immature_window.share:.1%} of it. What HAS settled "
            f"is not a random sample of what has not, so {figures} {phrase} is dominated by how "
            "much of the window has come back rather than by anything that happened. Ask again "
            "over a settled period and I will verify it."
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


def movement_forms(delta: Scalar, pct: Scalar, unit: str | None, *, bounded: bool = False) -> str:
    """A movement in BOTH of its readings, each named.

    "denial rate rose 11.5%" printed beside "7.1% → 7.9%" reads as *11.5
    points* to anybody scanning it — two different facts, one sentence, and
    nothing in the sentence to tell them apart. A rate moved 0.8 points AND
    11.5% relative; both are true, neither implies the other, and a card
    that states one without saying which has stated nothing checkable.

    Money needs no such care — dollars and percentages do not look alike —
    so it keeps the shorter form with the relative change parenthesised.

    ``bounded`` suppresses the relative reading entirely. A percentage
    change is a division by a number, and when either side of the comparison
    is a CEILING there is no such number — a 13-claim upper bound otherwise
    publishes "a 753.8% relative change". The absolute form survives because
    the title already says "at most"; the ratio does not, because a ratio of
    a bound is not a bound on the ratio.
    """
    absolute = magnitude(delta, unit)
    if bounded or not isinstance(pct, Decimal):
        return absolute
    relative = ratio_pct(abs(pct))
    if unit == _RATIO_UNIT:
        return f"{absolute}, a {relative} relative change"
    return f"{absolute} ({relative})"


def _movement_text(premise: PremiseCheck) -> str:
    """The movement a premise verdict states, in both of its readings."""
    return movement_forms(
        premise.delta,
        premise.pct,
        premise.unit,
        bounded=premise.current_bound is not None or premise.prior_bound is not None,
    )


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
        return "premise_unverifiable: " + then(
            sentence,
            "The question's own assumption is neither confirmed nor refuted on this "
            "answer, so nothing below may be read as evidence for it or against it.",
        )
    if premise.magnitude_short:
        return "premise_partial: " + then(
            sentence,
            "The direction the question assumes is right and the size is not, so nothing "
            "below may be described in the question's own words for it. What follows is "
            "the composition of the movement that did happen.",
        )
    return "premise_false: " + then(
        sentence,
        "The question takes that movement as given, and over this window there was none. "
        "What follows describes the cells that did move that way; it is context for a "
        "movement that did not happen at the level asked about, not confirmation of it.",
    )


def _premise_verified_warning(
    premise: PremiseCheck, spec: AnalysisSpec, *, comparison: ComparisonRendering | None
) -> str:
    """The verdict a *confirmed* premise owes the reader, said first.

    A premise probe runs on every turn that states a movement, and
    publishing its verdict only when it fails discards a measured aggregate:
    "why did denials double?" over a real +4.2% then opens on a 243%
    sub-cell. A verdict is a verdict either way.
    """
    sentence = premise_verdict_sentence(premise, spec, comparison=comparison)
    return "premise_verified: " + then(
        sentence,
        "The movement below is read against that aggregate, which is the level the "
        "question asked about.",
    )
