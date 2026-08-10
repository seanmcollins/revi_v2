"""Stratified recovery-rate estimation that never counts an open story as a loss.

This module is the honesty core of the capability. Everything else here
computes a statistic; this decides *what goes in the denominator*, and that
decision is where a recoverability estimate is usually wrong.

The failure it exists to prevent
-------------------------------

A denial's follow-up story takes time. At the data edge some chains have gone
out and not been answered, and some denials have not been worked *yet*. Both
look, in a table, exactly like the outcome "we tried and lost" or "we never
tried". Divide recoveries by all denials and every one of those open stories
is silently scored as a failure, so the estimate is biased downward — and
biased *most* against the freshest cohort, which is precisely the cohort
anyone asks about. In this warehouse, 383 of the 2,653 denials that read
``NOT_RESUBMITTED`` at the 2026-08-02 edge will in fact be resubmitted after
it. Nothing in the data distinguishes them from the ones nobody will ever
work. A naive rate charges all 383 as losses.

The two honest denominators
---------------------------

There is no single "recovery rate", so this module refuses to publish one and
publishes two clearly-named conditionals instead:

**DECIDED** — denominator is decided chains only: recovered (full or partial)
versus denied again. It answers *"given we pursued this denial and the payer
answered, how often did we win?"* Open chains are absent from the numerator
**and** the denominator; a chain awaiting an answer is not a loss. This is the
clean conditional and it is the rate that belongs in a dollar projection.

**PURSUIT** — denominator is denials old enough that a resubmission would have
been observed by now; the numerator is those that were in fact pursued. It
answers *"do we actually work this kind of denial?"* A denial younger than its
class's **maturity window** is **excluded** from the denominator rather than
counted as never-pursued. The window is caller-supplied per class — it is a
claim about the tenant's operations (the tail of the authored resubmission
delay), not something derivable from censored data, and this package will not
invent one. A class with no window and no default makes PURSUIT unanswerable
for those rows, and they are excluded and counted as such rather than being
quietly assumed mature.

The whole immature cohort is excluded, not just its unresubmitted half.
Keeping young denials that *were* already worked while dropping young denials
that were not would select on the outcome and bias the rate upward — the
mirror image of the error being fixed. Cohort maturity is a property of the
cohort, so it is applied to the cohort.

What this is not
----------------

This is **cohort-maturity exclusion**, not survival analysis. No hazard is
fitted, no risk set is carried forward, nothing is extrapolated past the data
edge, and no Kaplan-Meier estimator is implied by any number here. It is
strictly weaker: it answers only about the mature, decided population and says
so. In exchange it is auditable by hand — every excluded row appears as a
count in :class:`CensoringDisclosure`, and a reader can add them back up. That
trade is deliberate. An estimator whose bias correction cannot be checked by
the person relying on it is not obviously better than one whose limits are
printed on the front.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from revi_statistics.intervals import proportion, wilson_interval
from revi_statistics.strata import group_rows, validate_stratifiers
from revi_statistics_contracts.contract import (
    CensoringDisclosure,
    DenialRow,
    EstimationPolicy,
    EvidenceLabel,
    RateBasis,
    RateCell,
    RateEstimate,
    RecoveryStatus,
    Stratifier,
    StratumKey,
)


def _decided_partition(rows: Sequence[DenialRow]) -> tuple[list[DenialRow], int, int]:
    """Split rows into the decided denominator and the two excluded reasons."""
    denominator: list[DenialRow] = []
    open_undecided = 0
    not_pursued = 0
    for row in rows:
        if row.recovery_status.is_decided:
            denominator.append(row)
        elif row.recovery_status is RecoveryStatus.RESUBMITTED_PENDING:
            open_undecided += 1
        else:
            not_pursued += 1
    return denominator, open_undecided, not_pursued


def _pursuit_partition(
    rows: Sequence[DenialRow], policy: EstimationPolicy, as_of: date
) -> tuple[list[DenialRow], int, int]:
    """Split rows into the mature denominator, the immature, the ruleless."""
    denominator: list[DenialRow] = []
    immature = 0
    unclassifiable = 0
    for row in rows:
        window = policy.maturity.days_for(row.recovery_class)
        if window is None:
            unclassifiable += 1
        elif row.age_days(as_of) < window:
            immature += 1
        else:
            denominator.append(row)
    return denominator, immature, unclassifiable


def is_success(row: DenialRow, basis: RateBasis) -> bool:
    """What counts as a win on this basis.

    Public because a contrast must score a row exactly the way the rate
    estimate scores it; two definitions of "success" is one too many.
    """
    if basis is RateBasis.DECIDED:
        return row.recovery_status.is_recovered
    return row.recovery_status.is_pursued


def _make_cell(
    stratum: StratumKey, rows: Sequence[DenialRow], basis: RateBasis, policy: EstimationPolicy
) -> RateCell:
    n = len(rows)
    successes = sum(1 for row in rows if is_success(row, basis))
    if n < policy.min_cohort:
        # Below the floor the cell publishes its size and nothing else. The
        # size is the evidence for the refusal, so it is never suppressed
        # along with the rate.
        return RateCell(
            stratum=stratum,
            basis=basis,
            n=n,
            successes=successes,
            min_cohort=policy.min_cohort,
            evidence=EvidenceLabel.REFUSED_THIN,
        )
    return RateCell(
        stratum=stratum,
        basis=basis,
        n=n,
        successes=successes,
        min_cohort=policy.min_cohort,
        evidence=EvidenceLabel.MEASURED,
        rate=proportion(successes, n),
        interval=wilson_interval(successes, n, policy.confidence),
    )


def denominator_rows(
    rows: Sequence[DenialRow],
    *,
    basis: RateBasis,
    policy: EstimationPolicy,
    as_of: date,
) -> tuple[list[DenialRow], CensoringDisclosure]:
    """The rows that reach the denominator, and what leaving cost.

    Exposed because contrasts and composition must reduce a cohort exactly
    the way :func:`estimate_rates` does. One implementation, so a contrast
    can never be computed over a population the rate estimate would have
    treated differently.
    """
    considered = len(rows)
    open_in_input = sum(
        1 for row in rows if row.recovery_status is RecoveryStatus.RESUBMITTED_PENDING
    )
    not_pursued_in_input = sum(1 for row in rows if not row.recovery_status.is_pursued)

    if basis is RateBasis.DECIDED:
        kept, open_undecided, not_pursued = _decided_partition(rows)
        disclosure = CensoringDisclosure(
            basis=basis,
            data_edge_date=as_of,
            rows_considered=considered,
            in_denominator=len(kept),
            excluded_open_undecided=open_undecided,
            excluded_not_pursued=not_pursued,
            open_undecided_in_input=open_in_input,
            not_pursued_in_input=not_pursued_in_input,
        )
    else:
        kept, immature, unclassifiable = _pursuit_partition(rows, policy, as_of)
        disclosure = CensoringDisclosure(
            basis=basis,
            data_edge_date=as_of,
            rows_considered=considered,
            in_denominator=len(kept),
            excluded_immature=immature,
            excluded_unclassifiable=unclassifiable,
            open_undecided_in_input=open_in_input,
            not_pursued_in_input=not_pursued_in_input,
        )
    return kept, disclosure


def estimate_rates(
    rows: Sequence[DenialRow],
    *,
    basis: RateBasis,
    stratify_by: Sequence[Stratifier] = (),
    policy: EstimationPolicy,
    as_of: date,
) -> RateEstimate:
    """Recovery rates by stratum, with Wilson intervals and full disclosure.

    ``basis`` selects the denominator and is not a formatting choice — read
    the module docstring before picking one. ``as_of`` is the data edge: the
    date the rows were read at, which every maturity and deadline judgement
    is relative to.

    Cells are returned in sorted stratum order so two runs over the same rows
    produce byte-identical output regardless of input order. Every cell whose
    cohort is below ``policy.min_cohort`` is ``REFUSED_THIN`` and carries no
    rate — the type enforces it, this function merely respects it.
    """
    validate_stratifiers(stratify_by, policy)
    kept, disclosure = denominator_rows(rows, basis=basis, policy=policy, as_of=as_of)
    grouped = group_rows(kept, stratify_by, policy, as_of)
    cells = tuple(
        _make_cell(stratum, grouped[stratum], basis, policy) for stratum in sorted(grouped)
    )
    return RateEstimate(
        basis=basis,
        stratifiers=tuple(stratify_by),
        cells=cells,
        disclosure=disclosure,
        policy_min_cohort=policy.min_cohort,
        confidence=policy.confidence,
    )
