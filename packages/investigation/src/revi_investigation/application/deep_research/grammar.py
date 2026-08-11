"""The closed plan grammar: what a deep-research run is allowed to look at.

The control plane chooses angles; this module decides whether the choice is
legal, and it is the only thing that decides. Three properties follow from
keeping the set closed here rather than in a prompt:

* **A model cannot invent an analysis.** Every angle is a family from
  :class:`AngleFamily` with parameters drawn from :class:`Stratum`, and
  both are enumerations. An angle naming something else does not become a
  weaker analysis — it does not exist, and it is dropped.
* **A model cannot compute.** An angle says *what to look at*; the number
  comes from ``revi_statistics``, which the control plane never touches.
* **The headline cannot go missing.** A report whose job is to say what the
  open inventory is worth needs the pricing angle. If the plan omits it,
  :func:`validate_plan` appends it and records that it did — added, on the
  record, rather than quietly assumed to have been asked for.

Angle families are deliberately few and deliberately overlapping in what
they read: the same rows serve all of them, so an eight-angle plan costs
one read, and the cost of a wide plan is arithmetic rather than I/O.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

# ---------------------------------------------------------------------------
# the closed vocabularies


class AngleFamily(StrEnum):
    """The kinds of question a run may ask of the recovery history."""

    #: Recovery rate by population, with the size of every cohort.
    OUTCOME_BY_STRATUM = "outcome_by_stratum"
    #: Strongest payer against weakest, with the test that separates them.
    PAYER_CONTRAST = "payer_contrast"
    #: Strongest denial type against weakest, tested the same way.
    CLASS_CONTRAST = "class_contrast"
    #: Recovery rate by how long the denial waited before going back out.
    TIMELINESS_CURVE = "timeliness_curve"
    #: What crossing a filing deadline costs, split by the limit's standing.
    DEADLINE_INTERACTION = "deadline_interaction"
    #: Expected recoverable dollars over the denials still open.
    EXPECTED_RECOVERY = "expected_recovery"


class Stratum(StrEnum):
    """Populations an angle may cut by — mirrors the estimator's own set."""

    PAYER = "payer"
    PLAN = "plan"
    RECOVERY_CLASS = "recovery_class"
    AGE_BAND = "age_band"
    DOLLAR_BAND = "dollar_band"
    DELAY_BAND = "delay_band"
    FILING_POSITION = "filing_position"
    FILING_RULE = "filing_rule"


class RateBasisChoice(StrEnum):
    """Which denominator an outcome angle is over.

    ``DECIDED`` answers "given we resubmitted and the payer answered, how
    often did we win". ``PURSUIT`` answers "is this being worked at all",
    over denials old enough that a resubmission would have been seen. They
    are different questions with the same units and are never mixed.
    """

    DECIDED = "decided"
    PURSUIT = "pursuit"


class PopulationKind(StrEnum):
    """How a run's target population is selected."""

    ALL_OPEN = "all_open"
    PAYER = "payer"
    RECOVERY_CLASS = "recovery_class"
    FACILITY = "facility"


#: The one stratum the deadline angle is always cut by, in this order.
_DEADLINE_STRATA: tuple[Stratum, ...] = (Stratum.FILING_POSITION, Stratum.FILING_RULE)

#: How many angles one run may hold. Past this a report stops being an
#: argument and becomes a data dump, and the wall clock stops being minutes.
MAX_ANGLES = 8

#: How many populations one angle may cut by. Three-way cuts of a denial
#: population are thin almost everywhere, and an angle that refuses most of
#: its own cells has told the reader nothing they can act on.
MAX_STRATIFIERS = 2


# ---------------------------------------------------------------------------
# the plan


@dataclass(frozen=True, slots=True, order=True)
class ResearchAngle:
    """One angle: a family plus the populations it is cut by."""

    family: AngleFamily
    stratify_by: tuple[Stratum, ...] = ()
    #: Populations held FIXED outside the angle's own axis — a timeliness
    #: curve ``within`` denial type is the within-type effect rather than
    #: the type-mix-confounded pooled one.
    within: tuple[Stratum, ...] = ()
    basis: RateBasisChoice = RateBasisChoice.DECIDED

    @property
    def key(self) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
        return (
            str(self.family),
            tuple(str(s) for s in self.stratify_by),
            tuple(str(s) for s in self.within),
            str(self.basis),
        )


@dataclass(frozen=True, slots=True)
class TargetPopulation:
    """Which denials a run is about.

    ``values`` are matched against the data exactly as given. A selector
    naming a value the data does not hold is a refusal at execution, not a
    silently empty population.
    """

    kind: PopulationKind = PopulationKind.ALL_OPEN
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is PopulationKind.ALL_OPEN and self.values:
            raise ValueError("the every-open-denial selector takes no values")
        if self.kind is not PopulationKind.ALL_OPEN and not self.values:
            raise ValueError(f"the {self.kind} selector needs at least one value")

    @property
    def dimension(self) -> str | None:
        """The column this selector narrows on, or ``None`` for everything."""
        if self.kind is PopulationKind.PAYER:
            return "payer"
        if self.kind is PopulationKind.RECOVERY_CLASS:
            return "denial_recovery_class"
        if self.kind is PopulationKind.FACILITY:
            return "facility"
        return None


@dataclass(frozen=True, slots=True)
class DeepResearchPlan:
    """The angles a run will look at, and who chose them."""

    research_question: str
    angles: tuple[ResearchAngle, ...]
    rationale: str = ""
    #: ``"model"`` when the control plane chose; ``"revi"`` when the run
    #: fell back to the standing set. A fallback presented as a choice is a
    #: small lie about how the analysis was decided.
    authored_by: str = "revi"
    #: Families appended because the report cannot be written without them.
    added_by_revi: tuple[AngleFamily, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.angles:
            raise ValueError("a deep-research plan needs at least one angle")
        if self.authored_by not in ("model", "revi"):
            raise ValueError(f"unknown plan author {self.authored_by!r}")


#: The standing set — what a run looks at when nobody narrowed it, and the
#: fallback when the control plane has nothing to give. It is a complete
#: answer on its own: what the inventory is worth, what drives the rate,
#: who is strong and weak, what delay costs, and what the deadline costs.
STANDING_ANGLES: tuple[ResearchAngle, ...] = (
    ResearchAngle(
        family=AngleFamily.EXPECTED_RECOVERY,
        stratify_by=(Stratum.PAYER, Stratum.RECOVERY_CLASS),
    ),
    ResearchAngle(
        family=AngleFamily.OUTCOME_BY_STRATUM,
        stratify_by=(Stratum.RECOVERY_CLASS,),
    ),
    ResearchAngle(
        family=AngleFamily.OUTCOME_BY_STRATUM,
        stratify_by=(Stratum.PAYER,),
    ),
    ResearchAngle(family=AngleFamily.PAYER_CONTRAST),
    ResearchAngle(family=AngleFamily.CLASS_CONTRAST),
    ResearchAngle(family=AngleFamily.TIMELINESS_CURVE),
    ResearchAngle(family=AngleFamily.DEADLINE_INTERACTION),
    ResearchAngle(
        family=AngleFamily.OUTCOME_BY_STRATUM,
        stratify_by=(Stratum.RECOVERY_CLASS,),
        basis=RateBasisChoice.PURSUIT,
    ),
)

#: The question a run answers when the reader named none.
STANDING_QUESTION = (
    "Of the denials still open, how much is realistically coming back, and where?"
)


def standing_plan(question: str | None = None) -> DeepResearchPlan:
    """The plan a run uses when nothing narrowed it."""
    return DeepResearchPlan(
        research_question=(question or "").strip() or STANDING_QUESTION,
        angles=STANDING_ANGLES,
        rationale="Revi's standing set of angles for a recoverability review.",
        authored_by="revi",
    )


# ---------------------------------------------------------------------------
# disposal


def _stratifiers(raw: Iterable[object]) -> tuple[Stratum, ...]:
    """Keep the recognised populations, in order, without repeats."""
    kept: list[Stratum] = []
    for value in raw:
        try:
            stratum = Stratum(str(value))
        except ValueError:
            continue
        if stratum not in kept:
            kept.append(stratum)
    return tuple(kept)


def _normalized(angle: ResearchAngle) -> ResearchAngle | None:
    """One angle, reduced to a legal shape — or dropped.

    Each family has its own axis and its own free parameters. An angle that
    names parameters its family does not take is not refused outright: the
    surplus is dropped and the angle runs in the shape the family defines,
    because a plan that loses an angle over a stray field answers less than
    it could have. What is never done is running an angle over a population
    outside the closed set.
    """
    family = angle.family
    if family is AngleFamily.OUTCOME_BY_STRATUM:
        strata = angle.stratify_by[:MAX_STRATIFIERS]
        if not strata:
            return None
        return replace(angle, stratify_by=strata, within=())
    if family is AngleFamily.EXPECTED_RECOVERY:
        strata = angle.stratify_by[:MAX_STRATIFIERS]
        if not strata:
            strata = (Stratum.PAYER, Stratum.RECOVERY_CLASS)
        # Pricing is always over the answered-denial rate: a pursuit rate
        # answers "do we work these", not "what do we win", and applying it
        # to dollars would put the right units on the wrong question.
        return replace(
            angle, stratify_by=strata, within=(), basis=RateBasisChoice.DECIDED
        )
    within: tuple[Stratum, ...]
    if family is AngleFamily.PAYER_CONTRAST:
        within = tuple(s for s in angle.within if s is Stratum.RECOVERY_CLASS)[:1]
        return replace(
            angle, stratify_by=(), within=within, basis=RateBasisChoice.DECIDED
        )
    if family is AngleFamily.CLASS_CONTRAST:
        within = tuple(s for s in angle.within if s is Stratum.PAYER)[:1]
        return replace(
            angle, stratify_by=(), within=within, basis=RateBasisChoice.DECIDED
        )
    if family is AngleFamily.TIMELINESS_CURVE:
        within = tuple(
            s for s in angle.within if s in (Stratum.RECOVERY_CLASS, Stratum.PAYER)
        )[:1]
        return replace(
            angle, stratify_by=(), within=within, basis=RateBasisChoice.DECIDED
        )
    return replace(
        angle,
        stratify_by=_DEADLINE_STRATA,
        within=(),
        basis=RateBasisChoice.DECIDED,
    )


def build_angle(
    family: str,
    *,
    stratify_by: Sequence[object] = (),
    within: Sequence[object] = (),
    basis: str = "decided",
) -> ResearchAngle | None:
    """One angle from loose strings, or ``None`` when nothing legal remains."""
    try:
        parsed_family = AngleFamily(family)
    except ValueError:
        return None
    try:
        parsed_basis = RateBasisChoice(basis)
    except ValueError:
        parsed_basis = RateBasisChoice.DECIDED
    return _normalized(
        ResearchAngle(
            family=parsed_family,
            stratify_by=_stratifiers(stratify_by),
            within=_stratifiers(within),
            basis=parsed_basis,
        )
    )


def _one_headline(angles: list[ResearchAngle]) -> list[ResearchAngle]:
    """A run prices its open population once, at the finest cut it was asked for.

    Two pricing angles over the same denials produce two different totals,
    both arithmetically correct and neither wrong — a coarser cut prices
    more of the inventory because more of its populations clear the floor,
    and a finer cut prices less and confounds less. Publishing both would
    hand a reader two answers to one question with no basis for choosing,
    so the finest cut wins and it is the only one that runs.

    The finest cut is also the conservative one in the direction that
    matters. Pricing every payer's open denials at that payer's pooled
    rate blends its denial types together, and a payer whose open
    inventory is mostly clinical would be priced at a rate its coding
    denials earned. What that costs is stated instead: the populations too
    thin to price are listed at full value, outside the total.
    """
    pricing = [angle for angle in angles if angle.family is AngleFamily.EXPECTED_RECOVERY]
    if len(pricing) <= 1:
        return angles
    finest = max(
        pricing,
        key=lambda angle: (len(angle.stratify_by), tuple(str(s) for s in angle.stratify_by)),
    )
    return [
        angle
        for angle in angles
        if angle.family is not AngleFamily.EXPECTED_RECOVERY or angle is finest
    ]


def validate_plan(
    *,
    research_question: str,
    angles: Sequence[ResearchAngle],
    rationale: str = "",
    authored_by: str = "model",
) -> DeepResearchPlan:
    """Dispose of a proposed plan: normalize, de-duplicate, cap, complete.

    The pricing angle is appended when it is missing, and the family is
    recorded in ``added_by_revi`` so the report can say Revi added it. That
    is the one place this function changes what was asked for, and it says
    so rather than presenting the result as the plan it was handed.
    """
    kept: list[ResearchAngle] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...], str]] = set()
    for angle in angles:
        normalized = _normalized(angle)
        if normalized is None or normalized.key in seen:
            continue
        seen.add(normalized.key)
        kept.append(normalized)
        if len(kept) == MAX_ANGLES:
            break
    kept = _one_headline(kept)

    added: list[AngleFamily] = []
    if not any(a.family is AngleFamily.EXPECTED_RECOVERY for a in kept):
        headline = ResearchAngle(
            family=AngleFamily.EXPECTED_RECOVERY,
            stratify_by=(Stratum.PAYER, Stratum.RECOVERY_CLASS),
        )
        if len(kept) == MAX_ANGLES:
            kept.pop()
        kept.insert(0, headline)
        added.append(AngleFamily.EXPECTED_RECOVERY)

    if not kept:  # pragma: no cover - the headline insert above prevents it
        kept = list(STANDING_ANGLES)

    # The pricing angle leads: it is the headline, and running it first
    # means a run cancelled halfway still answered the question it was
    # asked before it answered the ones it chose.
    kept.sort(key=lambda a: (a.family is not AngleFamily.EXPECTED_RECOVERY,))
    return DeepResearchPlan(
        research_question=(research_question or "").strip() or STANDING_QUESTION,
        angles=tuple(kept),
        rationale=" ".join(rationale.split()),
        authored_by=authored_by,
        added_by_revi=tuple(added),
    )


def plan_fingerprint(plan: DeepResearchPlan, population: TargetPopulation) -> str:
    """A content address for "this plan over this population".

    Two runs sharing a fingerprint at the same data load must publish
    byte-identical numbers; the fingerprint is what makes that claim
    checkable rather than merely asserted.
    """
    digest = hashlib.sha256()
    parts: list[str] = [
        "deep_research_v1",
        str(population.kind),
        *population.values,
        plan.research_question,
    ]
    for angle in plan.angles:
        parts.append("|".join((angle.key[0], *angle.key[1], *angle.key[2], angle.key[3])))
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()
