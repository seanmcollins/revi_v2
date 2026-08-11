"""Typed inputs and outputs for the statistics capability.

This package is DTOs only — no estimation logic, no warehouse access, no
third-party dependency beyond the shared kernel. It fixes the vocabulary
two independent parties must agree on: whoever fetches denial rows, and
whoever consumes an estimate.

Three shapes carry the honesty of the whole capability and are worth
reading before anything else:

* :class:`RateCell` — a cell is EITHER a measurement (``rate`` and
  ``interval`` present) OR a refusal (both ``None``, ``n`` still
  published). There is no third state and no cell that quietly carries a
  rate over a cohort too thin to support one. ``__post_init__`` enforces
  it, so a caller cannot construct the dishonest object.
* :class:`CensoringDisclosure` — rides on every estimate, never optional.
  It states what was left out of the denominator and why, plus the data
  edge date the whole estimate is relative to.
* :class:`EvidenceLabel` — ``MEASURED`` means "this stratum's own cohort
  supported a rate"; ``REFUSED_THIN`` means it did not. This capability
  never substitutes a prior, a pooled rate, or a neighbour's rate for a
  thin cell. A consumer that wants a prior applies it knowingly, above.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from revi_kernel.errors import ErrorCode

# ---------------------------------------------------------------------------
# Input shape
# ---------------------------------------------------------------------------


class RecoveryStatus(StrEnum):
    """Where a denial's follow-up story stands *at the data edge*.

    The three decided states are terminal for the chain as observed; the
    two others are open, and the distinction between "open" and "failed"
    is the single most consequential thing in this package.
    """

    #: No resubmission observed. NOT the same claim as "never worked" —
    #: at the data edge this also holds denials whose resubmission has
    #: not gone out yet, and nothing in the data separates the two.
    NOT_RESUBMITTED = "NOT_RESUBMITTED"
    #: Resubmitted, no answer from the payer yet. Open, not failed.
    RESUBMITTED_PENDING = "RESUBMITTED_PENDING"
    RECOVERED_FULL = "RECOVERED_FULL"
    RECOVERED_PARTIAL = "RECOVERED_PARTIAL"
    DENIED_AGAIN = "DENIED_AGAIN"

    @property
    def is_decided(self) -> bool:
        """Has the payer answered the most recent resubmission?"""
        return self in _DECIDED

    @property
    def is_recovered(self) -> bool:
        return self in _RECOVERED

    @property
    def is_pursued(self) -> bool:
        """Did a resubmission go out, whatever came back?"""
        return self is not RecoveryStatus.NOT_RESUBMITTED


_DECIDED = frozenset(
    {
        RecoveryStatus.RECOVERED_FULL,
        RecoveryStatus.RECOVERED_PARTIAL,
        RecoveryStatus.DENIED_AGAIN,
    }
)
_RECOVERED = frozenset({RecoveryStatus.RECOVERED_FULL, RecoveryStatus.RECOVERED_PARTIAL})


@dataclass(frozen=True, slots=True)
class DenialRow:
    """One denial, at the denial grain, with its recovery story rolled up.

    The capability owns no warehouse access: the caller reads rows (from
    ``v_denial`` or any equivalent) and hands them over already typed.
    That is deliberate and mirrors ``revi_calculation`` — an estimator
    that could reach the database could also quietly change the
    population underneath a published number.

    ``filing_rule_confirmed`` is the caller's assertion that this plan's
    ``timely_filing_days`` is a governed limit stated without a
    confirmation caveat, rather than a planning default. It changes what
    a deadline means, so it is an input, not something this package
    guesses: an analysis that treats every configured limit as governed
    over-predicts the deadline cliff.
    """

    denial_id: str
    denial_date: date
    service_date: date
    payer_name: str
    plan_name: str
    recovery_class: str
    recovery_status: RecoveryStatus
    denied_amount_cents: int
    recovered_amount_cents: int = 0
    days_to_resubmission: int | None = None
    resubmission_date: date | None = None
    recovery_outcome_date: date | None = None
    timely_filing_days: int | None = None
    filing_rule_confirmed: bool = False

    def __post_init__(self) -> None:
        if self.denied_amount_cents < 0:
            raise ValueError("DenialRow.denied_amount_cents must be >= 0")
        if self.recovered_amount_cents < 0:
            raise ValueError("DenialRow.recovered_amount_cents must be >= 0")
        if self.recovered_amount_cents > self.denied_amount_cents:
            raise ValueError(
                "DenialRow.recovered_amount_cents exceeds denied_amount_cents "
                f"({self.denied_amount_cents}) on denial {self.denial_id!r}"
            )
        if self.recovered_amount_cents and not self.recovery_status.is_recovered:
            raise ValueError(
                f"denial {self.denial_id!r} carries recovered dollars with status "
                f"{self.recovery_status}"
            )
        pursued = self.recovery_status.is_pursued
        if not pursued and (self.resubmission_date is not None or self.days_to_resubmission is not None):
            raise ValueError(
                f"denial {self.denial_id!r} reads NOT_RESUBMITTED but carries resubmission detail"
            )
        if pursued and self.resubmission_date is None:
            raise ValueError(f"denial {self.denial_id!r} is pursued but has no resubmission_date")
        if self.days_to_resubmission is not None and self.days_to_resubmission < 0:
            raise ValueError("DenialRow.days_to_resubmission must be >= 0")
        if self.timely_filing_days is not None and self.timely_filing_days <= 0:
            raise ValueError("DenialRow.timely_filing_days must be positive")

    def age_days(self, as_of: date) -> int:
        """Days between the denial and the data edge — the maturity clock."""
        return (as_of - self.denial_date).days

    @property
    def filing_deadline(self) -> date | None:
        """Service date plus the plan's configured limit, when there is one."""
        if self.timely_filing_days is None:
            return None
        return date.fromordinal(self.service_date.toordinal() + self.timely_filing_days)

    def deadline_passed(self, as_of: date) -> bool | None:
        """Is this denial past its filing deadline as of a date?

        ``None`` when the plan carries no configured limit — unknown is
        not the same as "still catchable", and the composition split
        reports it as its own bucket rather than folding it into either.
        """
        deadline = self.filing_deadline
        if deadline is None:
            return None
        return as_of > deadline


# ---------------------------------------------------------------------------
# Stratification
# ---------------------------------------------------------------------------


class Stratifier(StrEnum):
    """A dimension an estimate may be cut by.

    The set is closed on purpose: a stratifier is a column this package
    knows how to read off :class:`DenialRow` deterministically. Anything
    else belongs to the caller's own grouping, above.
    """

    PAYER = "payer"
    PLAN = "plan"
    RECOVERY_CLASS = "recovery_class"
    AGE_BAND = "age_band"
    DOLLAR_BAND = "dollar_band"
    DELAY_BAND = "delay_band"
    FILING_POSITION = "filing_position"
    FILING_RULE = "filing_rule"


#: Stratifiers that need caller-supplied band edges before they can be used.
BANDED_STRATIFIERS: frozenset[Stratifier] = frozenset(
    {Stratifier.AGE_BAND, Stratifier.DOLLAR_BAND, Stratifier.DELAY_BAND}
)


@dataclass(frozen=True, slots=True, order=True)
class Band:
    """A half-open bucket ``[lower, upper)`` with a caller-chosen label.

    Bands are inputs rather than constants because a band edge is a
    judgement about the business, not a fact about arithmetic. Nothing in
    this package has a default set of them.
    """

    label: str
    lower: int
    upper: int | None = None

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("Band.label must be non-empty")
        if self.upper is not None and self.upper <= self.lower:
            raise ValueError(f"Band {self.label!r}: upper must exceed lower")

    def contains(self, value: int) -> bool:
        return value >= self.lower and (self.upper is None or value < self.upper)


#: The band label used when a row has no value for a banded stratifier —
#: an unresubmitted denial has no days-to-resubmission, and putting it in
#: a numeric band would invent one.
UNBANDED_LABEL = "unbanded"


@dataclass(frozen=True, slots=True, order=True)
class StratumKey:
    """The identity of one cell: ordered ``(stratifier, value)`` pairs.

    Empty ``parts`` is the ungrouped total, which is a legitimate stratum
    rather than a special case.
    """

    parts: tuple[tuple[str, str], ...] = ()

    @property
    def label(self) -> str:
        if not self.parts:
            return "(all)"
        return " / ".join(f"{name}={value}" for name, value in self.parts)

    def value_of(self, stratifier: Stratifier | str) -> str | None:
        wanted = str(stratifier)
        for name, value in self.parts:
            if name == wanted:
                return value
        return None


@dataclass(frozen=True, slots=True)
class MaturityWindow:
    """How long to wait before a denial's silence means anything.

    ``days`` is the tail of the authored resubmission-delay distribution
    for this class — the age by which a denial that was ever going to be
    worked would have been worked. Caller-supplied, because it is a claim
    about the tenant's operations that this package cannot derive from
    the censored data it is handed.
    """

    recovery_class: str
    days: int

    def __post_init__(self) -> None:
        if self.days < 0:
            raise ValueError("MaturityWindow.days must be >= 0")


@dataclass(frozen=True, slots=True)
class MaturityPolicy:
    """Per-class maturity windows for the PURSUIT basis.

    A class with no window and no ``default_days`` has no maturity rule,
    and :func:`estimate_rates` refuses the PURSUIT basis rather than
    silently treating every young denial as never-pursued. Refusing to
    apply a rule nobody stated is the point.
    """

    windows: tuple[MaturityWindow, ...] = ()
    default_days: int | None = None

    def __post_init__(self) -> None:
        seen = [w.recovery_class for w in self.windows]
        if len(seen) != len(set(seen)):
            raise ValueError("MaturityPolicy.windows has duplicate recovery_class entries")
        if self.default_days is not None and self.default_days < 0:
            raise ValueError("MaturityPolicy.default_days must be >= 0")

    def days_for(self, recovery_class: str) -> int | None:
        for window in self.windows:
            if window.recovery_class == recovery_class:
                return window.days
        return self.default_days


@dataclass(frozen=True, slots=True)
class EstimationPolicy:
    """Everything the caller must decide before a number can be published.

    ``min_cohort`` has no default. A suppression floor is a disclosure
    policy, and a package that picked one for you would be making that
    policy invisible at exactly the moment it matters.
    """

    min_cohort: int
    confidence: Decimal = Decimal("0.95")
    maturity: MaturityPolicy = MaturityPolicy()
    age_bands: tuple[Band, ...] = ()
    dollar_bands: tuple[Band, ...] = ()
    delay_bands: tuple[Band, ...] = ()

    def __post_init__(self) -> None:
        if self.min_cohort < 1:
            raise ValueError("EstimationPolicy.min_cohort must be >= 1")
        if not (Decimal(0) < self.confidence < Decimal(1)):
            raise ValueError("EstimationPolicy.confidence must lie strictly in (0, 1)")

    def bands_for(self, stratifier: Stratifier) -> tuple[Band, ...]:
        if stratifier is Stratifier.AGE_BAND:
            return self.age_bands
        if stratifier is Stratifier.DOLLAR_BAND:
            return self.dollar_bands
        if stratifier is Stratifier.DELAY_BAND:
            return self.delay_bands
        return ()


# ---------------------------------------------------------------------------
# Output shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Interval:
    """A closed confidence interval on a proportion or a difference."""

    low: Decimal
    high: Decimal
    confidence: Decimal

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("Interval.high must be >= Interval.low")
        if not (Decimal(0) < self.confidence < Decimal(1)):
            raise ValueError("Interval.confidence must lie strictly in (0, 1)")

    def contains(self, value: Decimal) -> bool:
        return self.low <= value <= self.high

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0


@dataclass(frozen=True, slots=True)
class CentsInterval:
    """A confidence interval in integer cents (money never becomes float)."""

    low_cents: int
    high_cents: int
    confidence: Decimal

    def __post_init__(self) -> None:
        if self.high_cents < self.low_cents:
            raise ValueError("CentsInterval.high_cents must be >= low_cents")


class EvidenceLabel(StrEnum):
    """Did this stratum's own cohort support a rate?"""

    #: Own-cohort rate, cohort at or above the floor.
    MEASURED = "measured"
    #: Cohort below the floor. No rate is published and no prior is
    #: substituted; the stratum is reported separately, never folded into
    #: a total as if it were zero.
    REFUSED_THIN = "refused_thin"


class RateBasis(StrEnum):
    """Which denominator a rate is over. The names are not interchangeable."""

    #: Decided chains only (recovered vs denied again). The clean
    #: conditional: "given this denial was pursued and the payer
    #: answered, how often did we win?" Open chains are absent from both
    #: numerator and denominator — never counted as failures.
    DECIDED = "decided"
    #: Was the denial worked at all, over a cohort old enough that a
    #: resubmission would have been observed by now. Immature denials are
    #: excluded rather than counted as never-pursued.
    PURSUIT = "pursuit"


#: The stable §12 code a refusal carries, so a consumer can surface the
#: refusal through the platform's ordinary honest-non-answer machinery
#: instead of inventing a second vocabulary for it.
REFUSAL_CODE: ErrorCode = ErrorCode.INSUFFICIENT_EVIDENCE


@dataclass(frozen=True, slots=True)
class RateCell:
    """One stratum's rate, or one stratum's refusal to publish a rate.

    The invariant, enforced here so it cannot be broken anywhere: a cell
    with ``evidence == REFUSED_THIN`` carries ``rate is None`` and
    ``interval is None``, and a cell whose ``n`` is below ``min_cohort``
    is always ``REFUSED_THIN``. ``n`` and ``successes`` are published
    either way — the cohort size is the reason for the refusal and hiding
    it would make the refusal unauditable.
    """

    stratum: StratumKey
    basis: RateBasis
    n: int
    successes: int
    min_cohort: int
    evidence: EvidenceLabel
    rate: Decimal | None = None
    interval: Interval | None = None

    def __post_init__(self) -> None:
        if self.n < 0:
            raise ValueError("RateCell.n must be >= 0")
        if not (0 <= self.successes <= self.n):
            raise ValueError(f"RateCell.successes must lie in [0, n]; got {self.successes}/{self.n}")
        if self.n < self.min_cohort and self.evidence is not EvidenceLabel.REFUSED_THIN:
            raise ValueError(
                f"RateCell n={self.n} is below the floor {self.min_cohort} but is not REFUSED_THIN"
            )
        if self.evidence is EvidenceLabel.REFUSED_THIN:
            if self.rate is not None or self.interval is not None:
                raise ValueError("a REFUSED_THIN RateCell must publish neither rate nor interval")
        elif self.rate is None or self.interval is None:
            raise ValueError("a MEASURED RateCell must publish both rate and interval")

    @property
    def is_measured(self) -> bool:
        return self.evidence is EvidenceLabel.MEASURED


@dataclass(frozen=True, slots=True)
class CensoringDisclosure:
    """What the data edge cost this estimate, stated in counts.

    Attached to every estimate, never optional, because the numbers above
    it are only interpretable against it. This is **cohort-maturity
    exclusion**, not survival analysis: no hazard is modelled, nothing is
    extrapolated past the edge, and every excluded row is counted here
    rather than imputed. It is weaker than Kaplan-Meier and auditable by
    hand, which is the trade being made.
    """

    basis: RateBasis
    #: The newest data date the rows were read at — every "not yet" in
    #: this disclosure is relative to it.
    data_edge_date: date
    #: Rows handed in.
    rows_considered: int
    #: Rows that reached the denominator.
    in_denominator: int
    #: PURSUIT only: rows younger than their class's maturity window, so
    #: their silence carries no information yet.
    excluded_immature: int = 0
    #: DECIDED only: pursued rows the payer has not answered yet.
    excluded_open_undecided: int = 0
    #: DECIDED only: rows with no resubmission observed.
    excluded_not_pursued: int = 0
    #: Rows dropped because the estimate could not be formed for them at
    #: all (e.g. PURSUIT with no maturity window for the class).
    excluded_unclassifiable: int = 0
    #: Open chains present in the input, whatever basis is in force —
    #: always disclosed, because they are the population a later load
    #: will resolve.
    open_undecided_in_input: int = 0
    #: Rows with no resubmission observed, whatever basis is in force.
    not_pursued_in_input: int = 0

    def __post_init__(self) -> None:
        counts = (
            self.rows_considered,
            self.in_denominator,
            self.excluded_immature,
            self.excluded_open_undecided,
            self.excluded_not_pursued,
            self.excluded_unclassifiable,
            self.open_undecided_in_input,
            self.not_pursued_in_input,
        )
        if any(value < 0 for value in counts):
            raise ValueError("CensoringDisclosure counts must be >= 0")
        accounted = (
            self.in_denominator
            + self.excluded_immature
            + self.excluded_open_undecided
            + self.excluded_not_pursued
            + self.excluded_unclassifiable
        )
        if accounted != self.rows_considered:
            raise ValueError(
                "CensoringDisclosure does not account for every row: "
                f"{accounted} accounted vs {self.rows_considered} considered"
            )


@dataclass(frozen=True, slots=True)
class RateEstimate:
    """A stratified set of rate cells plus the disclosure they rest on."""

    basis: RateBasis
    stratifiers: tuple[Stratifier, ...]
    cells: tuple[RateCell, ...]
    disclosure: CensoringDisclosure
    policy_min_cohort: int
    confidence: Decimal

    @property
    def measured(self) -> tuple[RateCell, ...]:
        return tuple(cell for cell in self.cells if cell.is_measured)

    @property
    def refused(self) -> tuple[RateCell, ...]:
        return tuple(cell for cell in self.cells if not cell.is_measured)

    def cell_for(self, stratum: StratumKey) -> RateCell | None:
        for cell in self.cells:
            if cell.stratum == stratum:
                return cell
        return None


# ---------------------------------------------------------------------------
# Time-to-recovery
# ---------------------------------------------------------------------------


class DurationMeasure(StrEnum):
    """Which elapsed time a distribution is over."""

    #: Denial date to the resubmission going out. Observed for every
    #: pursued chain, decided or not.
    DAYS_TO_RESUBMISSION = "days_to_resubmission"
    #: Denial date to the payer's answer. Decided chains only — an open
    #: chain has no outcome date, and using the data edge as a stand-in
    #: would manufacture a duration.
    DAYS_TO_OUTCOME = "days_to_outcome"


@dataclass(frozen=True, slots=True)
class DurationCell:
    """A stratum's duration distribution, or its refusal to publish one.

    Quartiles come from the caller's rows by linear interpolation between
    order statistics (the "type 7" convention); the method is named here
    because a median is not method-free at small n.
    """

    stratum: StratumKey
    measure: DurationMeasure
    n: int
    min_cohort: int
    evidence: EvidenceLabel
    p25_days: Decimal | None = None
    median_days: Decimal | None = None
    p75_days: Decimal | None = None
    min_days: int | None = None
    max_days: int | None = None

    def __post_init__(self) -> None:
        if self.n < 0:
            raise ValueError("DurationCell.n must be >= 0")
        if self.n < self.min_cohort and self.evidence is not EvidenceLabel.REFUSED_THIN:
            raise ValueError("a DurationCell below the floor must be REFUSED_THIN")
        quantiles = (self.p25_days, self.median_days, self.p75_days)
        if self.evidence is EvidenceLabel.REFUSED_THIN:
            if any(value is not None for value in quantiles):
                raise ValueError("a REFUSED_THIN DurationCell must publish no quantiles")
        elif any(value is None for value in quantiles):
            raise ValueError("a MEASURED DurationCell must publish all three quantiles")


@dataclass(frozen=True, slots=True)
class DurationEstimate:
    measure: DurationMeasure
    stratifiers: tuple[Stratifier, ...]
    cells: tuple[DurationCell, ...]
    disclosure: CensoringDisclosure
    policy_min_cohort: int


# ---------------------------------------------------------------------------
# Contrasts
# ---------------------------------------------------------------------------


class ContrastTest(StrEnum):
    #: Pooled two-proportion z. Used when every expected cell count is
    #: large enough for the normal approximation to be honest.
    TWO_PROPORTION_Z = "two_proportion_z"
    #: Fisher's exact test, computed exactly over the hypergeometric
    #: distribution. Used when the small-n guard trips.
    FISHERS_EXACT = "fishers_exact"
    #: No test run — at least one arm is below the cohort floor.
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ContrastArm:
    label: str
    n: int
    successes: int
    rate: Decimal | None
    interval: Interval | None

    def __post_init__(self) -> None:
        if not (0 <= self.successes <= self.n):
            raise ValueError("ContrastArm.successes must lie in [0, n]")


@dataclass(frozen=True, slots=True)
class Contrast:
    """Two cohorts compared, with the effect size and how it was tested.

    A refused contrast publishes both arms' ``n`` and nothing else: the
    reader needs to know how thin the comparison was, and must not be
    handed a p-value that the cohort floor says is not publishable.
    """

    left: ContrastArm
    right: ContrastArm
    test: ContrastTest
    min_cohort: int
    #: Risk difference, ``left.rate - right.rate``. The effect size —
    #: reported alongside the test because significance is not size.
    risk_difference: Decimal | None = None
    #: Newcombe hybrid-score interval on the risk difference, built from
    #: the two arms' Wilson intervals so the interval and the point
    #: estimates rest on the same method.
    risk_difference_interval: Interval | None = None
    #: The z statistic, when the z test was the one that ran.
    z_statistic: Decimal | None = None
    p_value: Decimal | None = None
    refusal_reason: str | None = None

    def __post_init__(self) -> None:
        if self.test is ContrastTest.REFUSED:
            if self.p_value is not None or self.risk_difference is not None:
                raise ValueError("a REFUSED contrast must publish no p-value and no effect size")
            if not self.refusal_reason:
                raise ValueError("a REFUSED contrast must state why")
        elif self.p_value is None or self.risk_difference is None:
            raise ValueError("a tested contrast must publish a p-value and an effect size")
        if self.test is ContrastTest.FISHERS_EXACT and self.z_statistic is not None:
            raise ValueError("Fisher's exact test produces no z statistic")

    @property
    def is_refused(self) -> bool:
        return self.test is ContrastTest.REFUSED


# ---------------------------------------------------------------------------
# Expected-recoverable composition
# ---------------------------------------------------------------------------


class RateScope(StrEnum):
    """Whose evidence produced the rate that priced a bucket of dollars.

    Published per bucket rather than assumed, because "this payer's own
    past-deadline denials" and "every payer's past-deadline denials" are
    different claims and a reader deciding where to put people is entitled
    to know which one they are reading.
    """

    #: This stratum's own cohort, at or above the floor.
    OWN = "own"
    #: The whole population's rate for this filing position, used because
    #: the stratum's own cohort for that position was below the floor. The
    #: filing-deadline effect is a property of the deadline rather than of
    #: the payer, so it is the one quantity this capability will carry
    #: across strata — and it says so, every time, per bucket.
    POPULATION = "population"
    #: No cohort at or above the floor on either footing. The dollars are
    #: reported at full value and priced at nothing.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class SeverityRatio:
    """How much of a denied dollar a win actually returns.

    A recovery is almost never the full denied amount: the payer allows
    part of the denied unit. A projection that multiplies a *count* rate by
    the *full* denied dollars therefore prices every win at 100 cents on
    the dollar and overstates the total by whatever the shortfall is. This
    is the observed shortfall, measured over decided wins only — the rows
    where a recovered amount exists to be divided.

    The ratio carries no interval. It is a ratio of two observed dollar
    sums, not a proportion of trials, and this capability will not invent a
    variance for amounts it was handed as facts. Consumers publishing a
    band around a priced total must say that the amounts and this ratio are
    treated as known and the band carries rate variance only.
    """

    stratum: StratumKey
    #: Decided wins the ratio was measured over — the cohort, and the
    #: reason for a refusal when there is one.
    wins: int
    min_cohort: int
    evidence: EvidenceLabel
    #: Denied and recovered dollars over those wins.
    denied_cents: int = 0
    recovered_cents: int = 0
    ratio: Decimal | None = None

    def __post_init__(self) -> None:
        if self.wins < 0:
            raise ValueError("SeverityRatio.wins must be >= 0")
        if self.denied_cents < 0 or self.recovered_cents < 0:
            raise ValueError("SeverityRatio dollar sums must be >= 0")
        if self.recovered_cents > self.denied_cents:
            raise ValueError(
                "SeverityRatio recovered dollars exceed denied dollars: "
                f"{self.recovered_cents} vs {self.denied_cents}"
            )
        if self.wins < self.min_cohort and self.evidence is not EvidenceLabel.REFUSED_THIN:
            raise ValueError(
                f"SeverityRatio over {self.wins} wins is below the floor "
                f"{self.min_cohort} but is not REFUSED_THIN"
            )
        if self.evidence is EvidenceLabel.REFUSED_THIN:
            if self.ratio is not None:
                raise ValueError("a REFUSED_THIN SeverityRatio must publish no ratio")
        elif self.ratio is None:
            raise ValueError("a MEASURED SeverityRatio must publish a ratio")

    @property
    def is_measured(self) -> bool:
        return self.evidence is EvidenceLabel.MEASURED


@dataclass(frozen=True, slots=True)
class SeverityEstimate:
    """Per-stratum severity ratios plus the population's own."""

    stratifiers: tuple[Stratifier, ...]
    cells: tuple[SeverityRatio, ...]
    #: The ungrouped ratio over every decided win in the read. Used where a
    #: stratum's own win cohort is below the floor, and named as such.
    population: SeverityRatio
    policy_min_cohort: int

    def cell_for(self, stratum: StratumKey) -> SeverityRatio | None:
        for cell in self.cells:
            if cell.stratum == stratum:
                return cell
        return None


@dataclass(frozen=True, slots=True)
class DeadlineRates:
    """The decided rate on each side of the filing deadline.

    Two estimates over the same decided rows: one cut by the pricing
    stratification *and* filing position, one cut by filing position alone.
    Both are ordinary :class:`RateEstimate` objects with Wilson intervals
    and the same floor as everything else, so a thin cell here refuses in
    exactly the way a thin cell anywhere else does.
    """

    stratifiers: tuple[Stratifier, ...]
    #: Cut by ``(*stratifiers, filing_position)``.
    stratified: RateEstimate
    #: Cut by ``(filing_position,)`` — the whole read's answer for each side
    #: of the deadline.
    pooled: RateEstimate

    def population_cell(self, position: str) -> RateCell | None:
        return self.pooled.cell_for(StratumKey((("filing_position", position),)))

    def stratum_cell(self, stratum: StratumKey, position: str) -> RateCell | None:
        return self.stratified.cell_for(
            StratumKey((*stratum.parts, ("filing_position", position)))
        )


@dataclass(frozen=True, slots=True)
class PricedPosition:
    """One filing position's open dollars inside a stratum, and its price.

    The unit of the whole composition. Every dollar in the target
    population lands in exactly one of these, and each one carries the rate
    that priced it, whose cohort that rate came from, and what it produced
    — so a reader can re-derive any line of the total by hand.
    """

    #: ``within_deadline``, ``past_deadline`` or ``unknown``.
    position: str
    dollars_cents: int
    scope: RateScope
    #: The cell whose rate was applied, or ``None`` when nothing was.
    rate_cell: RateCell | None = None
    #: The severity ratio applied on top of the rate.
    severity: Decimal | None = None
    expected_cents: int | None = None
    expected_interval: CentsInterval | None = None

    def __post_init__(self) -> None:
        if self.dollars_cents < 0:
            raise ValueError("PricedPosition.dollars_cents must be >= 0")
        if self.scope is RateScope.NONE:
            if self.expected_cents is not None or self.expected_interval is not None:
                raise ValueError("an unpriced position must carry no expected dollars")
            if self.rate_cell is not None and self.rate_cell.is_measured:
                raise ValueError("an unpriced position must not carry a measured rate")
        else:
            if self.rate_cell is None or not self.rate_cell.is_measured:
                raise ValueError("a priced position must carry the measured cell it used")
            if self.expected_cents is None or self.expected_interval is None:
                raise ValueError("a priced position must carry expected dollars and an interval")
            if self.severity is None:
                raise ValueError("a priced position must carry the severity ratio it applied")


@dataclass(frozen=True, slots=True)
class ExpectedRecoveryStratum:
    """One stratum of a target population, priced or explicitly not priced."""

    stratum: StratumKey
    evidence: EvidenceLabel
    #: Open denials in the target population for this stratum.
    open_denials: int
    open_dollars_cents: int
    #: Split of ``open_dollars_cents`` by filing position at the as-of
    #: date. Unknown is its own bucket: a plan with no configured limit
    #: is not evidence that the claim is still catchable.
    catchable_dollars_cents: int
    deadline_passed_dollars_cents: int
    deadline_unknown_dollars_cents: int
    #: This stratum's own overall DECIDED rate. It is the gate — a stratum
    #: whose own answered-denial cohort cannot support a rate is refused
    #: whole — and it is published so a reader sees the evidence the
    #: stratum rests on.
    rate_cell: RateCell
    #: The filing-position buckets, each with the rate that priced it.
    positions: tuple[PricedPosition, ...] = ()
    #: The severity ratio applied, and whose cohort it came from.
    severity: SeverityRatio | None = None
    severity_scope: RateScope = RateScope.NONE
    expected_cents: int | None = None
    expected_interval: CentsInterval | None = None

    def __post_init__(self) -> None:
        parts = (
            self.catchable_dollars_cents
            + self.deadline_passed_dollars_cents
            + self.deadline_unknown_dollars_cents
        )
        if parts != self.open_dollars_cents:
            raise ValueError(
                "filing-position split does not sum to open dollars: "
                f"{parts} vs {self.open_dollars_cents}"
            )
        if self.evidence is EvidenceLabel.REFUSED_THIN:
            if self.expected_cents is not None or self.expected_interval is not None:
                raise ValueError("a REFUSED_THIN stratum must carry no expected dollars")
        elif self.expected_cents is None or self.expected_interval is None:
            raise ValueError("a MEASURED stratum must carry expected dollars and an interval")
        if self.positions:
            bucketed = sum(position.dollars_cents for position in self.positions)
            if bucketed != self.open_dollars_cents:
                raise ValueError(
                    "priced positions do not sum to open dollars: "
                    f"{bucketed} vs {self.open_dollars_cents}"
                )

    @property
    def priced_dollars_cents(self) -> int:
        """Open dollars that actually received a rate."""
        return sum(
            position.dollars_cents
            for position in self.positions
            if position.scope is not RateScope.NONE
        )

    @property
    def unpriced_position_dollars_cents(self) -> int:
        """Dollars inside a measured stratum that no position rate could price."""
        return sum(
            position.dollars_cents
            for position in self.positions
            if position.scope is RateScope.NONE
        )


@dataclass(frozen=True, slots=True)
class ExpectedRecovery:
    """Expected recoverable dollars over a target population of open denials.

    The total covers MEASURED strata only. Thin strata are listed in
    ``refused_strata`` with their dollars intact, so the consumer can see
    exactly how much of the population went unpriced and decide — above
    this capability — whether any prior should be applied to it. Nothing
    here substitutes one.

    ``interval_is_summed_endpoints`` is ``True`` and says so in the type
    because the total's interval is formed by adding the per-stratum
    interval endpoints. Summing endpoints is the **perfectly-correlated**
    combination: it is the widest of the family, and it is wider than the
    quadrature sum that independence would justify. So it is conservative
    on width and not a calibrated 95% band on the total — a caller
    rendering it must read it as a spread indication. (An earlier version
    of this field was named ``interval_assumes_independence`` and its
    documentation had the direction backwards. Both are fixed here: the
    name says what the arithmetic is, and the arithmetic is unchanged.)

    ``amounts_treated_as_known`` is the second thing a band around money
    hides. Only the rate carries variance; the denied amounts and the
    observed severity ratio enter as constants. A consumer publishing the
    interval must say so.
    """

    as_of: date
    strata: tuple[ExpectedRecoveryStratum, ...]
    refused_strata: tuple[ExpectedRecoveryStratum, ...]
    total_open_dollars_cents: int
    total_expected_cents: int
    total_expected_interval: CentsInterval
    priced_open_dollars_cents: int
    unpriced_open_dollars_cents: int
    catchable_dollars_cents: int
    deadline_passed_dollars_cents: int
    deadline_unknown_dollars_cents: int
    disclosure: CensoringDisclosure
    confidence: Decimal
    #: The population's severity ratio and the two population-level
    #: filing-position rates, published so the reader can re-derive the
    #: construction from the numbers on the page.
    severity: SeverityEstimate | None = None
    within_deadline_rate: RateCell | None = None
    past_deadline_rate: RateCell | None = None
    #: Dollars inside a MEASURED stratum that no filing-position rate could
    #: price — an unknown deadline, or a position whose cohort was thin on
    #: both its own and the population's footing.
    unpriced_position_dollars_cents: int = 0
    interval_is_summed_endpoints: bool = True
    amounts_treated_as_known: bool = True

    @property
    def unpriced_dollars_cents(self) -> int:
        """Every open dollar that received no rate, for whatever reason."""
        return self.unpriced_open_dollars_cents + self.unpriced_position_dollars_cents

    @property
    def unpriced_share(self) -> Decimal:
        """Fraction of open dollars no rate could price."""
        if self.total_open_dollars_cents == 0:
            return Decimal(0)
        return Decimal(self.unpriced_dollars_cents) / Decimal(self.total_open_dollars_cents)
