"""The generalized angle grammar — closed operation shapes over the catalog.

v1 deep research could ask six questions, all of them about denial recovery.
The families were closed for a good reason (a model cannot invent an
analysis) and hardcoded for a bad one (they were the only analysis anyone
had written). This module keeps the reason and drops the accident: the
closed set becomes five **shapes**, and the recovery families become the
recovery domain's *instances* of them.

    measure-profile     a governed measure over a population, optionally cut
    stratified-rates    outcome-like rates by population, with intervals
    contrast            two populations, with the test that separates them
    trend               a measure along an ordered axis — time, or a band
    composition         shares of a total, or expected value over priced data

A shape says what KIND of reading is being taken. What is read comes from
the catalog: any governed measure, any certified cut that measure declares,
any window, any statistics function the shape admits. The set of legal
angles is therefore the product of the semantic layer, which is exactly the
completeness bar's demand — *if the semantic layer and the data support a
path, the research finds it* — and it is still closed, because every factor
of that product is content this platform certifies.

**The recovery families did not move.** ``AngleFamily.PAYER_CONTRAST`` is
still executed by the code that executed it in M48, byte for byte; what
changed is that the planner can now say "this is a contrast" about it and
about a payer-behavior contrast over ``denial_rate`` in the same sentence.
A plan carries either kind of angle and the walk records both the same way.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from revi_investigation.application.deep_research.grammar import (
    AngleFamily,
    ResearchAngle,
    TargetPopulation,
)


class AngleShape(StrEnum):
    """The closed operation shapes a research angle may take."""

    #: What a measure is, over a population, optionally broken out.
    MEASURE_PROFILE = "measure_profile"
    #: A rate by population, with an interval and a size per cell. Only where
    #: outcome-like data supports one — a ratio whose denominator counts the
    #: population the numerator is drawn from.
    STRATIFIED_RATES = "stratified_rates"
    #: Two populations against each other, with the test that separates them.
    CONTRAST = "contrast"
    #: A measure along an ordered axis: months, or a band the content defines.
    TREND = "trend"
    #: Shares of a total, or expected value where priced data exists.
    COMPOSITION = "composition"


#: Which shape each v1 recovery family is an instance of. The recovery
#: domain is not a special case of the grammar — it is the first domain
#: written in it, and this table is the whole of the claim.
RECOVERY_SHAPES: dict[AngleFamily, AngleShape] = {
    AngleFamily.EXPECTED_RECOVERY: AngleShape.COMPOSITION,
    AngleFamily.OUTCOME_BY_STRATUM: AngleShape.STRATIFIED_RATES,
    AngleFamily.PAYER_CONTRAST: AngleShape.CONTRAST,
    AngleFamily.CLASS_CONTRAST: AngleShape.CONTRAST,
    AngleFamily.TIMELINESS_CURVE: AngleShape.TREND,
    AngleFamily.DEADLINE_INTERACTION: AngleShape.STRATIFIED_RATES,
}


class TimeStep(StrEnum):
    """How a trend buckets its axis. Mirrors the kernel's own closed set."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


#: How many angles one research run may hold across every round. Past this a
#: report stops being an argument and becomes a data dump, and the wall clock
#: stops being minutes.
MAX_ANGLES = 14

#: How many certified cuts one angle may take. A three-way cut of anything
#: interesting is thin almost everywhere, and an angle that refuses most of
#: its own cells has told the reader nothing they can act on.
MAX_CUTS = 2


@dataclass(frozen=True, slots=True, order=True)
class MeasureAngle:
    """One angle over the governed measure plane.

    Everything here names governed content: ``metric_id`` a metric
    contract, ``cut_by`` certified dimensions that contract declares,
    ``basis`` a date basis it allows. Validation resolves all three against
    the catalog and the pack before a probe is built — a plan naming
    something else does not become a weaker analysis, it does not exist.
    """

    metric_id: str
    cut_by: tuple[str, ...] = ()
    #: For TREND only. ``None`` on any other shape.
    step: TimeStep | None = None
    #: ``None`` means the contract's own primary date basis, which is the
    #: only basis a plan is entitled to assume.
    basis: str | None = None
    #: How many cells a broken-out reading publishes before it summarises.
    top_n: int = 12
    #: A second measure the shape needs: the total a composition is a share
    #: OF, or the comparison measure a contrast is taken on.
    against: str = ""
    #: Populations held FIXED for this angle — ``(dimension, value)`` pairs.
    #: This is how a later round CHASES a finding: the contrast that
    #: separated payers is deepened by re-reading the same measure inside
    #: the payer it separated on, which is a narrower population rather
    #: than a different analysis.
    within: tuple[tuple[str, str], ...] = ()

    @property
    def key(self) -> tuple[str, ...]:
        return (
            self.metric_id,
            *self.cut_by,
            str(self.step or ""),
            self.basis or "",
            self.against,
            *(f"{name}={value}" for name, value in self.within),
        )


@dataclass(frozen=True, slots=True)
class PlannedAngle:
    """One angle in a generalized plan, with the reason it is there.

    Exactly one of ``recovery`` and ``measure`` is set. The reason is not
    decoration: the walk is what a permalink restores and what the harness
    audits, and an angle whose presence has no stated cause makes the walk
    a list of what happened rather than a record of what was decided.
    """

    shape: AngleShape
    reason: str
    #: Which round chose this angle. Round 0 is the opening plan; anything
    #: above it was chosen after reading certified results.
    round: int = 0
    recovery: ResearchAngle | None = None
    measure: MeasureAngle | None = None
    #: Set when a later round chose this angle to go INSIDE an earlier
    #: finding — the chase relationship, recorded rather than inferred from
    #: round numbers.
    chases: str = ""

    def __post_init__(self) -> None:
        if (self.recovery is None) == (self.measure is None):
            raise ValueError("a planned angle is exactly one of recovery or measure")

    @property
    def key(self) -> tuple[str, ...]:
        """What makes this angle THIS angle.

        The shape is part of it. A stratified-rate reading of denial rate by
        payer and a contrast between the extremes of that same reading share
        every parameter and are different analyses — one publishes twelve
        cells, the other publishes the test that says whether the widest gap
        between them is real. Keying without the shape silently dropped the
        contrast out of every plan that also asked for the rates, which is
        the half of the question a reader most wanted answered.
        """
        if self.recovery is not None:
            k = self.recovery.key
            return (str(self.shape), "recovery", k[0], *k[1], *k[2], k[3])
        assert self.measure is not None
        return (str(self.shape), "measure", *self.measure.key)

    @property
    def subject(self) -> str:
        """What this angle is about, for a trace line and a progress note."""
        if self.recovery is not None:
            return str(self.recovery.family)
        assert self.measure is not None
        return self.measure.metric_id


@dataclass(frozen=True, slots=True)
class WalkStep:
    """One decision the loop made, with its stated reason.

    The recorded walk *is* the plan (addendum, "the recorded path is the
    plan"). It is what a permalink restores, what replay re-executes, what
    the warehouse-diff harness audits and what the trace explains — so every
    step carries who acted, on what, and why, in that order.
    """

    round: int
    #: ``orient`` | ``consult`` | ``plan`` | ``execute`` | ``chase`` |
    #: ``broaden`` | ``drop`` | ``refuse`` | ``synthesize``
    action: str
    subject: str
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ResearchWalk:
    """The whole recorded path of one research run."""

    question: str
    population: TargetPopulation
    angles: tuple[PlannedAngle, ...]
    steps: tuple[WalkStep, ...] = field(default=())
    #: ``"model"`` when the control plane chose; ``"revi"`` when the run fell
    #: back to a standing set. A fallback presented as a choice is a small
    #: lie about how the analysis was decided.
    authored_by: str = "revi"
    rationale: str = ""
    rounds: int = 1
    budget: int = 1
    #: ``True`` when the opening round is the plan a reader saw and
    #: confirmed. ``False`` when the run planned its own opening — in which
    #: case the same question asked twice can legitimately open on
    #: different readings, and the report must say so rather than let
    #: "chosen for this question" read as one deliberation per run.
    plan_confirmed: bool = False

    @property
    def by_round(self) -> tuple[tuple[int, tuple[PlannedAngle, ...]], ...]:
        grouped: dict[int, list[PlannedAngle]] = {}
        for angle in self.angles:
            grouped.setdefault(angle.round, []).append(angle)
        return tuple((index, tuple(grouped[index])) for index in sorted(grouped))

    def with_steps(self, *steps: WalkStep) -> ResearchWalk:
        return replace(self, steps=(*self.steps, *steps))

    def with_angles(self, *angles: PlannedAngle) -> ResearchWalk:
        return replace(self, angles=(*self.angles, *angles))


def walk_fingerprint(walk: ResearchWalk) -> str:
    """A content address for "this walk over this population".

    Two runs sharing a fingerprint at the same data load must publish
    byte-identical numbers. The reasons are excluded deliberately: they are
    what the model SAID, and a report whose numbers changed because a
    sentence was worded differently would have a reproducibility claim it
    could not keep. What is hashed is what was executed.
    """
    digest = hashlib.sha256()
    parts: list[str] = [
        "deep_research_v2",
        str(walk.population.kind),
        *walk.population.values,
        walk.question,
    ]
    for angle in walk.angles:
        parts.append("|".join((str(angle.shape), *angle.key)))
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# disposal


@dataclass(frozen=True, slots=True)
class AngleVocabulary:
    """What a plan is allowed to name, resolved from catalog and pack.

    Built by the orient phase out of discovery results, so "legal" means
    *this* warehouse rather than *a* warehouse: a measure whose grain this
    deployment does not carry is not in the vocabulary, and an angle naming
    it is dropped with the reason recorded rather than failing mid-run.
    """

    #: metric id → the certified cuts that metric declares.
    measures: dict[str, frozenset[str]]
    #: metric id → the date bases it allows.
    bases: dict[str, frozenset[str]]
    #: metric id → ``"flow"`` or ``"snapshot"``.
    kinds: dict[str, str]
    #: metric id → declared unit.
    units: dict[str, str]
    #: Ratio metrics whose denominator counts the population their numerator
    #: is drawn from — the ones a stratified-rate reading is honest over.
    rate_like: frozenset[str] = frozenset()
    #: metric id → the definitions library's own description of it. Carried
    #: so a planner prompt can say what a measure IS rather than handing a
    #: model an id and hoping it guesses: ``ar_over_90_pct`` means nothing
    #: to anything that has not read the contract, and a planner choosing
    #: between measures it cannot tell apart is choosing at random.
    descriptions: dict[str, str] = field(default_factory=dict)

    def knows(self, metric_id: str) -> bool:
        return metric_id in self.measures

    def cuts_for(self, metric_id: str) -> frozenset[str]:
        return self.measures.get(metric_id, frozenset())


def normalize_measure_angle(
    angle: MeasureAngle, shape: AngleShape, vocabulary: AngleVocabulary
) -> tuple[MeasureAngle | None, str]:
    """One angle, reduced to a legal shape — or dropped, with the reason.

    Surplus is trimmed rather than refused: a plan that loses an angle over
    a stray cut answers less than it could have. What is never done is
    running an angle over a measure or a cut outside the certified set —
    there the angle does not exist, and saying so is the honest outcome.
    """
    from revi_investigation.application.rendering import metric_label

    measure = metric_label(angle.metric_id)
    if not vocabulary.knows(angle.metric_id):
        return None, f"{measure} is not a measure in your definitions library"
    declared = vocabulary.cuts_for(angle.metric_id)
    kept = tuple(dict.fromkeys(cut for cut in angle.cut_by if cut in declared))[:MAX_CUTS]
    dropped = [cut for cut in angle.cut_by if cut not in declared]
    kind = vocabulary.kinds.get(angle.metric_id, "flow")

    # A TREND KEEPS ITS BREAKDOWN, deliberately. Forced to a bucket, but
    # not stripped of ``cut_by``: a trend that also carries a breakdown is
    # a GRID — one cell per (group, period) — and the choice was between
    # clearing the breakdown here so a trend is always one series, and
    # keeping the grid and teaching everything downstream what it is.
    #
    # The grid stays. Clearing it would delete measured data the plan asked
    # for in order to protect one sentence, and ``_title`` already renders
    # "by month, by payer", so the reader was never told there was one
    # axis. What WAS wrong is fixed where it was wrong: the study's settled
    # sentence speaks for one named group at a time and its chart draws one
    # line per group (``general_report._is_grid``), instead of reading the
    # first and last cells of a month-sorted grid as a series' ends and
    # publishing "rose from 47.2% in Atlas Commercial / Aug 2025 to 54.2%
    # in State Medicaid / May 2026".
    step = angle.step if shape is AngleShape.TREND else None
    if shape is AngleShape.TREND and step is None:
        step = TimeStep.MONTH
    if step is not None and kind == "snapshot":
        # A snapshot is a point in time. Bucketing one is not a coarser
        # trend, it is a category error the adapter refuses; catching it
        # here turns a mid-run failure into a plan-time drop with a reason.
        return None, f"{measure} is an as-of figure and has no trend over one read"

    basis = angle.basis if angle.basis in vocabulary.bases.get(angle.metric_id, frozenset()) else None

    if shape is AngleShape.CONTRAST and not kept:
        return None, "a comparison needs a breakdown to compare across"
    if shape is AngleShape.STRATIFIED_RATES:
        if angle.metric_id not in vocabulary.rate_like:
            return None, f"{measure} is not a rate, so it has no population to stratify"
        if not kept:
            return None, "a rate by population needs a breakdown"
    if shape is AngleShape.COMPOSITION and not kept:
        return None, "a share of a total needs a breakdown to divide it by"

    reason = (
        f"dropped the {', '.join(cut.replace('_', ' ') for cut in dropped)} breakdown — "
        f"{measure} is not broken out by it"
        if dropped
        else ""
    )
    return (
        replace(angle, cut_by=kept, step=step, basis=basis, top_n=max(1, min(angle.top_n, 40))),
        reason,
    )


def dedupe(angles: Sequence[PlannedAngle]) -> tuple[PlannedAngle, ...]:
    """Keep the first of each distinct angle, in order, up to the cap."""
    seen: set[tuple[str, ...]] = set()
    kept: list[PlannedAngle] = []
    for angle in angles:
        if angle.key in seen:
            continue
        seen.add(angle.key)
        kept.append(angle)
        if len(kept) == MAX_ANGLES:
            break
    return tuple(kept)
