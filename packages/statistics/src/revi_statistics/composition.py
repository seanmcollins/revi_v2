"""Expected recoverable dollars over a population of open denials.

The arithmetic is unremarkable — per stratum, the decided rate times the open
dollars, summed. Everything that matters is what the function refuses to do.

**It never substitutes a prior.** A stratum whose own cohort is below the
floor is labelled ``REFUSED_THIN``, contributes **nothing** to the total, and
is listed separately with its dollars intact. It is not priced at the pooled
rate, not at a neighbouring payer's rate, not at an industry rule of thumb.
This is the whole reason the capability exists: a thin cell silently borrowing
a rate produces a total that looks complete and is unfalsifiable, because
nothing in the output says which parts were measured. Here the split is
explicit, ``unpriced_open_dollars_cents`` is published beside the total, and
whoever wants a prior applies it above this layer, on the record.

**It never treats an unknown deadline as a good one.** Open dollars split
three ways — still catchable, past the filing deadline, and *unknown* because
the plan carries no configured limit. Folding the third into "catchable" would
be the optimistic guess; folding it into "passed" would be the pessimistic
one. It gets its own line.

**It says what its interval assumes.** The total's interval is the sum of the
per-stratum interval endpoints, which is exact only if the strata are
independent. They are not — they share payers, seasons, staffing and policy —
so the summed interval is **narrower than the truth**. That is stated on the
result type (``interval_assumes_independence``) rather than in a comment,
because a caller rendering a band around a headline number needs to know it is
a spread indication and not a calibrated bound. Doing better would require a
covariance structure this capability is not given and would have to invent.

The rate applied is always the **DECIDED** rate: "given we pursue this and the
payer answers, how often do we win", which is the right conditional for "what
is this open inventory worth if we work it". Passing a PURSUIT estimate here
is rejected rather than silently accepted — it would answer a different
question with the same units.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from revi_statistics.strata import group_rows, validate_stratifiers
from revi_statistics_contracts.contract import (
    CentsInterval,
    DenialRow,
    EstimationPolicy,
    EvidenceLabel,
    ExpectedRecovery,
    ExpectedRecoveryStratum,
    RateBasis,
    RateCell,
    RateEstimate,
    Stratifier,
    StratumKey,
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


def expected_recovery(
    open_rows: Sequence[DenialRow],
    *,
    rates: RateEstimate,
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

    ``rates`` must be a DECIDED estimate stratified the same way, so every
    lookup is a stratum's *own* rate.
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
        catchable = 0
        passed = 0
        unknown = 0
        for row in rows:
            verdict = row.deadline_passed(as_of)
            if verdict is None:
                unknown += row.denied_amount_cents
            elif verdict:
                passed += row.denied_amount_cents
            else:
                catchable += row.denied_amount_cents

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
                expected_cents=_cents(cell.rate, open_dollars),
                expected_interval=CentsInterval(
                    low_cents=_cents(cell.interval.low, open_dollars),
                    high_cents=_cents(cell.interval.high, open_dollars),
                    confidence=cell.interval.confidence,
                ),
            )
        )

    total_expected = sum(s.expected_cents or 0 for s in measured)
    low = sum(s.expected_interval.low_cents for s in measured if s.expected_interval)
    high = sum(s.expected_interval.high_cents for s in measured if s.expected_interval)
    priced = sum(s.open_dollars_cents for s in measured)
    unpriced = sum(s.open_dollars_cents for s in refused)
    every = (*measured, *refused)

    return ExpectedRecovery(
        as_of=as_of,
        strata=tuple(measured),
        refused_strata=tuple(refused),
        total_open_dollars_cents=priced + unpriced,
        total_expected_cents=total_expected,
        total_expected_interval=CentsInterval(
            low_cents=low, high_cents=high, confidence=policy.confidence
        ),
        priced_open_dollars_cents=priced,
        unpriced_open_dollars_cents=unpriced,
        catchable_dollars_cents=sum(s.catchable_dollars_cents for s in every),
        deadline_passed_dollars_cents=sum(s.deadline_passed_dollars_cents for s in every),
        deadline_unknown_dollars_cents=sum(s.deadline_unknown_dollars_cents for s in every),
        # The pricing rests on the rate evidence, so the disclosure that
        # travels with the money is the one describing how those rates were
        # formed — including what the data edge removed from them.
        disclosure=rates.disclosure,
        confidence=policy.confidence,
    )
