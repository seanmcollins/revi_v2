"""Everything a deep-research run must be told before it can publish.

These are decisions, not defaults: how many answered denials a rate needs
before it is published, where a band's edges fall, how long to wait before
a denial's silence means anything. The estimator refuses to pick any of
them, and it is right to — a suppression level chosen inside a library is
a disclosure policy nobody can inspect.

So they arrive as content. The composition root loads them from governed
content beside the definitions library and hands them in; this module is
the shape they arrive in and the one place they are turned into the
estimator's own policy object.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from revi_investigation.application.deep_research.grammar import Stratum
from revi_statistics_contracts.contract import (
    Band,
    EstimationPolicy,
    MaturityPolicy,
    MaturityWindow,
    Stratifier,
)

#: The estimator's stratifier for each of ours. The two enumerations mirror
#: each other by construction; this is the single crossing point, so a
#: divergence is one failing test rather than a silently mis-cut estimate.
_STRATIFIERS: Mapping[Stratum, Stratifier] = {
    Stratum.PAYER: Stratifier.PAYER,
    Stratum.PLAN: Stratifier.PLAN,
    Stratum.RECOVERY_CLASS: Stratifier.RECOVERY_CLASS,
    Stratum.AGE_BAND: Stratifier.AGE_BAND,
    Stratum.DOLLAR_BAND: Stratifier.DOLLAR_BAND,
    Stratum.DELAY_BAND: Stratifier.DELAY_BAND,
    Stratum.FILING_POSITION: Stratifier.FILING_POSITION,
    Stratum.FILING_RULE: Stratifier.FILING_RULE,
}


def stratifier_of(stratum: Stratum) -> Stratifier:
    return _STRATIFIERS[stratum]


@dataclass(frozen=True, slots=True)
class BandSpec:
    """A half-open bucket ``[lower, upper)`` with the words a reader sees."""

    label: str
    lower: int
    upper: int | None = None

    def to_band(self) -> Band:
        return Band(label=self.label, lower=self.lower, upper=self.upper)


@dataclass(frozen=True, slots=True)
class AngleCopy:
    """What a reader sees for one angle: while it runs, and in the report."""

    title: str
    progress: str
    purpose: str


@dataclass(frozen=True, slots=True)
class DeepResearchSettings:
    """The governed content one run is executed under.

    ``content_hash`` fingerprints the source so a report can be traced back
    to the exact rules it ran under — a floor moved between two runs is the
    kind of change that must be visible rather than inferred from a number
    that shifted.
    """

    #: Answered denials a population needs before any rate is published.
    min_cohort: int
    #: How the floor is stated to a reader: the rule, then who recommends it.
    min_cohort_label: str
    min_cohort_recommender: str
    confidence: Decimal
    delay_bands: tuple[BandSpec, ...] = ()
    dollar_bands: tuple[BandSpec, ...] = ()
    age_bands: tuple[BandSpec, ...] = ()
    #: Per denial type, the age by which a denial that was ever going to be
    #: worked has been worked. Below it, silence carries no information.
    maturity_days: Mapping[str, int] = field(default_factory=dict)
    #: The earliest service date recovery history exists for.
    earliest_service_date: date = date(2025, 1, 1)
    #: Denials with a formal appeal on the remit carry their outcome on a
    #: different feed and are excluded rather than double-counted.
    exclude_appealed: bool = True
    population_description: str = ""
    #: Denials a population needs before it may be NAMED. Distinct from the
    #: rate floor and always the smaller of the two: naming a population of
    #: four and printing its dollars discloses those four denials.
    disclosure_floor: int = 11
    #: The ceiling on one read. A population larger than this is refused
    #: rather than sampled: a sampled estimate published as a measurement
    #: would be the one dishonest number in the report.
    max_rows: int = 250_000
    stratifier_labels: Mapping[str, str] = field(default_factory=dict)
    value_labels: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    class_context: Mapping[str, str] = field(default_factory=dict)
    angle_copy: Mapping[str, AngleCopy] = field(default_factory=dict)
    copy: Mapping[str, str] = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.min_cohort < 1:
            raise ValueError("the rate floor must be at least 1")
        if self.disclosure_floor < 1:
            raise ValueError("the naming floor must be at least 1")
        if self.disclosure_floor > self.min_cohort:
            raise ValueError(
                "the naming floor must not exceed the rate floor: a population too "
                "small to name must never be large enough to publish a rate"
            )
        if not (Decimal(0) < self.confidence < Decimal(1)):
            raise ValueError("confidence must lie strictly between 0 and 1")
        if self.max_rows < 1:
            raise ValueError("the read ceiling must be at least 1")

    # -- the estimator's own policy -----------------------------------------

    def estimation_policy(self) -> EstimationPolicy:
        return EstimationPolicy(
            min_cohort=self.min_cohort,
            confidence=self.confidence,
            maturity=MaturityPolicy(
                windows=tuple(
                    MaturityWindow(recovery_class=name, days=days)
                    for name, days in sorted(self.maturity_days.items())
                )
            ),
            age_bands=tuple(b.to_band() for b in self.age_bands),
            dollar_bands=tuple(b.to_band() for b in self.dollar_bands),
            delay_bands=tuple(b.to_band() for b in self.delay_bands),
        )

    # -- words --------------------------------------------------------------

    def stratifier_label(self, stratum: Stratum | str) -> str:
        key = str(stratum)
        return self.stratifier_labels.get(key, key.replace("_", " "))

    def value_label(self, stratum: Stratum | str, value: str) -> str:
        return self.value_labels.get(str(stratum), {}).get(value, value)

    def angle(self, family: str) -> AngleCopy:
        found = self.angle_copy.get(family)
        if found is not None:
            return found
        readable = family.replace("_", " ")
        return AngleCopy(title=readable, progress=readable, purpose="")

    def line(self, key: str, fallback: str = "") -> str:
        return self.copy.get(key, fallback)

    def floor_sentence(self) -> str:
        """The rate floor as a client-facing rule: value, owner, and change.

        Stated in full every time it is shown. A reader asked to accept a
        withheld rate is owed the number that withheld it, who recommends
        that number, and the fact that it is theirs to move.
        """
        return (
            f"A rate is published only where {self.min_cohort_label} — "
            f"{self.min_cohort_recommender}. You can change this anytime."
        )

    def supported_stratifiers(self) -> tuple[Stratum, ...]:
        """Populations this content can actually cut by.

        A banded population with no band edges cannot be cut at all, and an
        angle asking for one is dropped at plan time rather than failing
        mid-run.
        """
        usable: list[Stratum] = []
        for stratum in Stratum:
            if stratum is Stratum.AGE_BAND and not self.age_bands:
                continue
            if stratum is Stratum.DOLLAR_BAND and not self.dollar_bands:
                continue
            if stratum is Stratum.DELAY_BAND and not self.delay_bands:
                continue
            usable.append(stratum)
        return tuple(usable)


def drop_unsupported(
    strata: Sequence[Stratum], settings: DeepResearchSettings
) -> tuple[Stratum, ...]:
    supported = set(settings.supported_stratifiers())
    return tuple(s for s in strata if s in supported)
