"""Expected recoverable dollars over a population of open denials.

The arithmetic is unremarkable — per stratum, per side of the filing
deadline, a rate times the dollars sitting there, times the share of a
denied dollar a win actually returns, summed. Everything that matters is
what the function refuses to do, and what it refuses to leave out.

**It prices a win at what a win is worth.** A recovery is almost never the
full denied amount; the payer allows part of the denied unit. Multiplying a
*count* rate — "how often do we win" — by the *full* denied dollars prices
every win at a hundred cents on the dollar, and the resulting headline is
wrong by the size of the shortfall, in the direction that flatters. So the
count rate is multiplied by an observed :class:`SeverityRatio`: recovered
dollars over denied dollars, across decided wins only. Measured per stratum
where the win cohort clears the floor, and over the whole read where it does
not — and which of the two applied is published per stratum, never assumed.

**It prices a denial past its filing deadline like a denial past its filing
deadline.** Resubmit inside the limit and the payer answers one way; resubmit
past it and the payer answers very differently. Pricing an open population at
its blended rate charges the past-deadline dollars the within-deadline rate,
which is the second half of the same overstatement. So the open dollars are
split three ways by filing position (the split the type already conserved)
and each side is priced at *that side's* decided rate. The filing-deadline
effect is a property of the deadline rather than of the payer, so it is the
one quantity carried across strata when a stratum's own cohort for a position
is thin — and every bucket says, in :class:`RateScope`, whose evidence priced
it.

**It never substitutes a prior for a stratum.** A stratum whose own decided
cohort is below the floor is labelled ``REFUSED_THIN``, contributes
**nothing** to the total, and is listed separately with its dollars intact.
It is not priced at the pooled rate, not at a neighbouring payer's rate, not
at an industry rule of thumb. This is the whole reason the capability exists:
a thin cell silently borrowing a rate produces a total that looks complete
and is unfalsifiable, because nothing in the output says which parts were
measured. Here the split is explicit and ``unpriced_open_dollars_cents`` is
published beside the total.

**It never treats an unknown deadline as a good one.** Open dollars split
three ways — still catchable, past the filing deadline, and *unknown*
because the plan carries no configured limit. Folding the third into
"catchable" would be the optimistic guess; folding it into "passed" would be
the pessimistic one. It gets its own line, and it is priced at nothing: there
is no filing-position rate that answers for it, and inventing one is the
error this module exists to refuse.

**It says what its interval is.** The total's interval adds the per-stratum
endpoints, which is the *perfectly-correlated* combination and therefore the
widest of the family — wider than the quadrature sum independence would
justify. That is stated on the result type
(``interval_is_summed_endpoints``) rather than in a comment. And only the
rate carries variance: the denied amounts and the severity ratio enter as
constants, which ``amounts_treated_as_known`` says out loud, because a band
drawn around money reads as a band on the money.

The rate applied is always the **DECIDED** rate: "given we pursue this and
the payer answers, how often do we win", which is the right conditional for
"what is this open inventory worth if we work it". Passing a PURSUIT estimate
here is rejected rather than silently accepted — it would answer a different
question with the same units. What that conditional does *not* cover is
stated by the caller: the rate is measured on denials somebody chose to work,
and open inventory is not a random sample of those.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from revi_statistics.intervals import quantize
from revi_statistics.rates import denominator_rows, estimate_rates
from revi_statistics.strata import (
    FILING_POSITION_PAST,
    FILING_POSITION_UNKNOWN,
    FILING_POSITION_WITHIN,
    group_rows,
    validate_stratifiers,
)
from revi_statistics_contracts.contract import (
    CentsInterval,
    DeadlineRates,
    DenialRow,
    EstimationPolicy,
    EvidenceLabel,
    ExpectedRecovery,
    ExpectedRecoveryStratum,
    PricedPosition,
    RateBasis,
    RateCell,
    RateEstimate,
    RateScope,
    SeverityEstimate,
    SeverityRatio,
    Stratifier,
    StratumKey,
)

#: The order filing positions are published in: what can still be worked,
#: what cannot, and what nobody can say.
POSITION_ORDER: tuple[str, ...] = (
    FILING_POSITION_WITHIN,
    FILING_POSITION_PAST,
    FILING_POSITION_UNKNOWN,
)


def _cents(rate: Decimal, dollars_cents: int) -> int:
    """Apply a rate to integer cents, rounding half up — never via float."""
    return int((rate * Decimal(dollars_cents)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _empty_cell(stratum: StratumKey, policy: EstimationPolicy) -> RateCell:
    """The stand-in cell for a stratum the rate estimate never saw.

    A target stratum with no evidence at all is a refusal with ``n = 0``,
    which is exactly what it is: no cohort, no rate.
    """
    return RateCell(
        stratum=stratum,
        basis=RateBasis.DECIDED,
        n=0,
        successes=0,
        min_cohort=policy.min_cohort,
        evidence=EvidenceLabel.REFUSED_THIN,
    )


# ---------------------------------------------------------------------------
# what a win is worth
# ---------------------------------------------------------------------------


def _severity_cell(
    stratum: StratumKey, wins: Sequence[DenialRow], policy: EstimationPolicy
) -> SeverityRatio:
    denied = sum(row.denied_amount_cents for row in wins)
    recovered = sum(row.recovered_amount_cents for row in wins)
    if len(wins) < policy.min_cohort or denied == 0:
        # Below the floor the cell publishes its cohort and its sums and
        # nothing else. The cohort is the reason for the refusal, so it is
        # never suppressed along with the ratio.
        return SeverityRatio(
            stratum=stratum,
            wins=len(wins),
            min_cohort=policy.min_cohort,
            evidence=EvidenceLabel.REFUSED_THIN,
            denied_cents=denied,
            recovered_cents=recovered,
        )
    return SeverityRatio(
        stratum=stratum,
        wins=len(wins),
        min_cohort=policy.min_cohort,
        evidence=EvidenceLabel.MEASURED,
        denied_cents=denied,
        recovered_cents=recovered,
        ratio=quantize(Decimal(recovered) / Decimal(denied)),
    )


def severity_ratios(
    rows: Sequence[DenialRow],
    *,
    stratify_by: Sequence[Stratifier] = (),
    policy: EstimationPolicy,
    as_of: date,
) -> SeverityEstimate:
    """What a win returns on the denied dollar, per stratum and overall.

    Measured over **decided wins only** — the rows that carry a recovered
    amount. A denial that was denied again returns nothing and is not
    evidence about the size of a recovery; including it would fold the win
    rate into the severity and double-count it against the rate the
    composition already applies.

    The floor is the same one every other cell in this package respects,
    read here as a count of wins.
    """
    validate_stratifiers(stratify_by, policy)
    decided, _ = denominator_rows(rows, basis=RateBasis.DECIDED, policy=policy, as_of=as_of)
    wins = [row for row in decided if row.recovery_status.is_recovered]
    grouped = group_rows(wins, stratify_by, policy, as_of)
    return SeverityEstimate(
        stratifiers=tuple(stratify_by),
        cells=tuple(_severity_cell(key, grouped[key], policy) for key in sorted(grouped)),
        population=_severity_cell(StratumKey(), wins, policy),
        policy_min_cohort=policy.min_cohort,
    )


# ---------------------------------------------------------------------------
# what the filing deadline costs
# ---------------------------------------------------------------------------


def deadline_rates(
    rows: Sequence[DenialRow],
    *,
    stratify_by: Sequence[Stratifier] = (),
    policy: EstimationPolicy,
    as_of: date,
) -> DeadlineRates:
    """The decided rate on each side of the filing deadline.

    Two ordinary rate estimates over the same rows: one cut by the pricing
    stratification *and* filing position, one cut by filing position alone.
    Both carry Wilson intervals, both refuse below the floor, and neither
    invents anything — which is the point of computing them here rather
    than pooling counts by hand at the report layer, where a refused cell
    can be dropped from a sum without anybody noticing.
    """
    validate_stratifiers(stratify_by, policy)
    strata = tuple(stratify_by)
    if Stratifier.FILING_POSITION in strata:
        raise ValueError(
            "deadline_rates cuts by filing position itself; "
            "passing it in stratify_by would request it twice"
        )
    return DeadlineRates(
        stratifiers=strata,
        stratified=estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(*strata, Stratifier.FILING_POSITION),
            policy=policy,
            as_of=as_of,
        ),
        pooled=estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.FILING_POSITION,),
            policy=policy,
            as_of=as_of,
        ),
    )


# ---------------------------------------------------------------------------
# the composition
# ---------------------------------------------------------------------------


def _position_rate(
    deadlines: DeadlineRates, stratum: StratumKey, position: str
) -> tuple[RateCell | None, RateScope]:
    """This position's rate: the stratum's own, else the population's, else none."""
    if position == FILING_POSITION_UNKNOWN:
        # There is no "rate for denials whose deadline nobody recorded".
        # Reaching for one would be exactly the substitution this module
        # refuses everywhere else.
        return None, RateScope.NONE
    own = deadlines.stratum_cell(stratum, position)
    if own is not None and own.is_measured:
        return own, RateScope.OWN
    pooled = deadlines.population_cell(position)
    if pooled is not None and pooled.is_measured:
        return pooled, RateScope.POPULATION
    return own or pooled, RateScope.NONE


def _priced_position(
    *,
    position: str,
    dollars: int,
    deadlines: DeadlineRates,
    stratum: StratumKey,
    severity: SeverityRatio,
    confidence: Decimal,
) -> PricedPosition:
    cell, scope = _position_rate(deadlines, stratum, position)
    if scope is RateScope.NONE or cell is None or cell.rate is None or cell.interval is None:
        return PricedPosition(
            position=position,
            dollars_cents=dollars,
            scope=RateScope.NONE,
            rate_cell=None,
        )
    share = severity.ratio
    assert share is not None  # a MEASURED severity always carries its ratio
    return PricedPosition(
        position=position,
        dollars_cents=dollars,
        scope=scope,
        rate_cell=cell,
        severity=share,
        expected_cents=_cents(share * cell.rate, dollars),
        expected_interval=CentsInterval(
            low_cents=_cents(share * cell.interval.low, dollars),
            high_cents=_cents(share * cell.interval.high, dollars),
            confidence=confidence,
        ),
    )


def expected_recovery(
    open_rows: Sequence[DenialRow],
    *,
    rates: RateEstimate,
    deadlines: DeadlineRates,
    severity: SeverityEstimate,
    stratify_by: Sequence[Stratifier],
    policy: EstimationPolicy,
    as_of: date,
) -> ExpectedRecovery:
    """Price a population of open denials against measured own-cohort rates.

    ``open_rows`` is the target population — denials whose story has not
    ended. A decided denial passed in here would be priced as if its dollars
    were still available, double-counting money already won or already lost,
    so it is rejected rather than filtered: silently dropping rows would make
    the published population differ from the one the caller believes it
    supplied.

    ``rates`` must be a DECIDED estimate stratified the same way; it is the
    gate, so every stratum that is priced at all has its own answered-denial
    evidence. ``deadlines`` and ``severity`` are what turn a count rate into
    dollars, and both are required — a default for either would be the
    silent overstatement this signature exists to prevent.
    """
    if rates.basis is not RateBasis.DECIDED:
        raise ValueError(
            f"expected_recovery requires a DECIDED rate estimate; got {rates.basis}. "
            "A pursuit rate answers 'do we work these', not 'what do we win'."
        )
    if tuple(rates.stratifiers) != tuple(stratify_by):
        raise ValueError(
            "rate estimate and target population must share a stratification; "
            f"rates are cut by {rates.stratifiers}, target by {tuple(stratify_by)}"
        )
    if tuple(deadlines.stratifiers) != tuple(stratify_by):
        raise ValueError(
            "filing-position rates must share the pricing stratification; "
            f"they are cut by {deadlines.stratifiers}, target by {tuple(stratify_by)}"
        )
    if tuple(severity.stratifiers) != tuple(stratify_by):
        raise ValueError(
            "severity ratios must share the pricing stratification; "
            f"they are cut by {severity.stratifiers}, target by {tuple(stratify_by)}"
        )
    if not severity.population.is_measured and not any(
        cell.is_measured for cell in severity.cells
    ):
        raise ValueError(
            "no decided win in this read carries a recovered amount, so what a win "
            "returns on the dollar is unmeasured and nothing here can be priced"
        )
    validate_stratifiers(stratify_by, policy)

    decided = [row for row in open_rows if row.recovery_status.is_decided]
    if decided:
        raise ValueError(
            f"{len(decided)} of {len(open_rows)} target rows are already decided; "
            "the target population must contain only open denials"
        )

    grouped = group_rows(open_rows, stratify_by, policy, as_of)
    measured: list[ExpectedRecoveryStratum] = []
    refused: list[ExpectedRecoveryStratum] = []

    for stratum in sorted(grouped):
        rows = grouped[stratum]
        open_dollars = sum(row.denied_amount_cents for row in rows)
        buckets = dict.fromkeys(POSITION_ORDER, 0)
        for row in rows:
            verdict = row.deadline_passed(as_of)
            if verdict is None:
                buckets[FILING_POSITION_UNKNOWN] += row.denied_amount_cents
            elif verdict:
                buckets[FILING_POSITION_PAST] += row.denied_amount_cents
            else:
                buckets[FILING_POSITION_WITHIN] += row.denied_amount_cents
        catchable = buckets[FILING_POSITION_WITHIN]
        passed = buckets[FILING_POSITION_PAST]
        unknown = buckets[FILING_POSITION_UNKNOWN]

        cell = rates.cell_for(stratum) or _empty_cell(stratum, policy)
        if not cell.is_measured or cell.rate is None or cell.interval is None:
            refused.append(
                ExpectedRecoveryStratum(
                    stratum=stratum,
                    evidence=EvidenceLabel.REFUSED_THIN,
                    open_denials=len(rows),
                    open_dollars_cents=open_dollars,
                    catchable_dollars_cents=catchable,
                    deadline_passed_dollars_cents=passed,
                    deadline_unknown_dollars_cents=unknown,
                    rate_cell=cell,
                )
            )
            continue

        own_severity = severity.cell_for(stratum)
        if own_severity is not None and own_severity.is_measured:
            applied, severity_scope = own_severity, RateScope.OWN
        elif severity.population.is_measured:
            applied, severity_scope = severity.population, RateScope.POPULATION
        else:
            applied, severity_scope = severity.population, RateScope.NONE

        positions: list[PricedPosition] = []
        for position in POSITION_ORDER:
            dollars = buckets[position]
            if severity_scope is RateScope.NONE:
                positions.append(
                    PricedPosition(
                        position=position, dollars_cents=dollars, scope=RateScope.NONE
                    )
                )
                continue
            positions.append(
                _priced_position(
                    position=position,
                    dollars=dollars,
                    deadlines=deadlines,
                    stratum=stratum,
                    severity=applied,
                    confidence=policy.confidence,
                )
            )

        measured.append(
            ExpectedRecoveryStratum(
                stratum=stratum,
                evidence=EvidenceLabel.MEASURED,
                open_denials=len(rows),
                open_dollars_cents=open_dollars,
                catchable_dollars_cents=catchable,
                deadline_passed_dollars_cents=passed,
                deadline_unknown_dollars_cents=unknown,
                rate_cell=cell,
                positions=tuple(positions),
                severity=applied,
                severity_scope=severity_scope,
                expected_cents=sum(p.expected_cents or 0 for p in positions),
                expected_interval=CentsInterval(
                    low_cents=sum(
                        p.expected_interval.low_cents for p in positions if p.expected_interval
                    ),
                    high_cents=sum(
                        p.expected_interval.high_cents for p in positions if p.expected_interval
                    ),
                    confidence=policy.confidence,
                ),
            )
        )

    total_expected = sum(s.expected_cents or 0 for s in measured)
    low = sum(s.expected_interval.low_cents for s in measured if s.expected_interval)
    high = sum(s.expected_interval.high_cents for s in measured if s.expected_interval)
    priced = sum(s.priced_dollars_cents for s in measured)
    unpriced_position = sum(s.unpriced_position_dollars_cents for s in measured)
    unpriced = sum(s.open_dollars_cents for s in refused)
    every = (*measured, *refused)

    return ExpectedRecovery(
        as_of=as_of,
        strata=tuple(measured),
        refused_strata=tuple(refused),
        total_open_dollars_cents=priced + unpriced_position + unpriced,
        total_expected_cents=total_expected,
        total_expected_interval=CentsInterval(
            low_cents=low, high_cents=high, confidence=policy.confidence
        ),
        priced_open_dollars_cents=priced,
        unpriced_open_dollars_cents=unpriced,
        unpriced_position_dollars_cents=unpriced_position,
        catchable_dollars_cents=sum(s.catchable_dollars_cents for s in every),
        deadline_passed_dollars_cents=sum(s.deadline_passed_dollars_cents for s in every),
        deadline_unknown_dollars_cents=sum(s.deadline_unknown_dollars_cents for s in every),
        severity=severity,
        within_deadline_rate=deadlines.population_cell(FILING_POSITION_WITHIN),
        past_deadline_rate=deadlines.population_cell(FILING_POSITION_PAST),
        # The pricing rests on the rate evidence, so the disclosure that
        # travels with the money is the one describing how those rates were
        # formed — including what the data edge removed from them.
        disclosure=rates.disclosure,
        confidence=policy.confidence,
    )
