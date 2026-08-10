"""How long recovery takes, and what waiting costs.

Two related things, deliberately separate:

* :func:`estimate_durations` — the distribution of elapsed days, by stratum.
  Only over chains that reached the event being timed. An open chain has no
  outcome date, and substituting the data edge for it would manufacture a
  duration that gets shorter the fresher the cohort, which is backwards.
* :func:`delay_effect_curve` — the DECIDED recovery rate per
  days-to-resubmission band, with intervals. Not a duration: a rate, cut by a
  duration. It is the empirical answer to "does moving faster actually win
  more", and it is reported with intervals because the tail bands are thin.

Both are descriptive. Neither controls for anything, and the effect curve in
particular is confounded by construction wherever slow classes are also weak
ones — which is the case in this warehouse. Stratify the curve by recovery
class to see the within-class effect; the unstratified curve mixes the timing
effect with the class mix and overstates the slope.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from revi_statistics.intervals import quantize
from revi_statistics.rates import estimate_rates
from revi_statistics.strata import group_rows, validate_stratifiers
from revi_statistics_contracts.contract import (
    CensoringDisclosure,
    DenialRow,
    DurationCell,
    DurationEstimate,
    DurationMeasure,
    EstimationPolicy,
    EvidenceLabel,
    RateBasis,
    RateCell,
    RateEstimate,
    RecoveryStatus,
    Stratifier,
    StratumKey,
)


def quantile(values: Sequence[int], q: Decimal) -> Decimal:
    """Linear interpolation between order statistics ("type 7").

    Named in the docstring and in :class:`DurationCell` because a median is
    not method-free at small n: the six common quantile conventions disagree,
    and a number whose method is unstated cannot be reproduced.
    """
    if not values:
        raise ValueError("quantile of an empty sample")
    if not (Decimal(0) <= q <= Decimal(1)):
        raise ValueError(f"quantile q must lie in [0, 1]; got {q}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return quantize(Decimal(ordered[0]))
    position = (Decimal(len(ordered)) - 1) * q
    lower_index = int(position)
    fraction = position - lower_index
    if lower_index >= len(ordered) - 1:
        return quantize(Decimal(ordered[-1]))
    lower = Decimal(ordered[lower_index])
    upper = Decimal(ordered[lower_index + 1])
    return quantize(lower + fraction * (upper - lower))


def _duration_days(row: DenialRow, measure: DurationMeasure) -> int | None:
    if measure is DurationMeasure.DAYS_TO_RESUBMISSION:
        return row.days_to_resubmission
    if not row.recovery_status.is_decided or row.recovery_outcome_date is None:
        return None
    return (row.recovery_outcome_date - row.denial_date).days


def estimate_durations(
    rows: Sequence[DenialRow],
    *,
    measure: DurationMeasure,
    stratify_by: Sequence[Stratifier] = (),
    policy: EstimationPolicy,
    as_of: date,
) -> DurationEstimate:
    """Median and quartiles of elapsed days, by stratum.

    Rows with no value for the measure are excluded and counted in the
    disclosure — never imputed, never censored-at-the-edge. Strata below the
    cohort floor publish their ``n`` and no quantiles, on the same rule as
    rate cells: a median over four observations is not a distribution.
    """
    validate_stratifiers(stratify_by, policy)
    kept: list[DenialRow] = []
    open_undecided = 0
    not_pursued = 0
    for row in rows:
        if _duration_days(row, measure) is not None:
            kept.append(row)
        elif row.recovery_status is RecoveryStatus.RESUBMITTED_PENDING:
            open_undecided += 1
        else:
            not_pursued += 1

    disclosure = CensoringDisclosure(
        basis=RateBasis.DECIDED,
        data_edge_date=as_of,
        rows_considered=len(rows),
        in_denominator=len(kept),
        excluded_open_undecided=open_undecided,
        excluded_not_pursued=not_pursued,
        open_undecided_in_input=sum(
            1 for row in rows if row.recovery_status is RecoveryStatus.RESUBMITTED_PENDING
        ),
        not_pursued_in_input=sum(1 for row in rows if not row.recovery_status.is_pursued),
    )

    grouped = group_rows(kept, stratify_by, policy, as_of)
    cells: list[DurationCell] = []
    for stratum in sorted(grouped):
        days = [
            value
            for row in grouped[stratum]
            if (value := _duration_days(row, measure)) is not None
        ]
        n = len(days)
        if n < policy.min_cohort:
            cells.append(
                DurationCell(
                    stratum=stratum,
                    measure=measure,
                    n=n,
                    min_cohort=policy.min_cohort,
                    evidence=EvidenceLabel.REFUSED_THIN,
                )
            )
            continue
        cells.append(
            DurationCell(
                stratum=stratum,
                measure=measure,
                n=n,
                min_cohort=policy.min_cohort,
                evidence=EvidenceLabel.MEASURED,
                p25_days=quantile(days, Decimal("0.25")),
                median_days=quantile(days, Decimal("0.5")),
                p75_days=quantile(days, Decimal("0.75")),
                min_days=min(days),
                max_days=max(days),
            )
        )
    return DurationEstimate(
        measure=measure,
        stratifiers=tuple(stratify_by),
        cells=tuple(cells),
        disclosure=disclosure,
        policy_min_cohort=policy.min_cohort,
    )


def delay_effect_curve(
    rows: Sequence[DenialRow],
    *,
    policy: EstimationPolicy,
    as_of: date,
    within: Sequence[Stratifier] = (),
) -> RateEstimate:
    """DECIDED recovery rate per days-to-resubmission band, in band order.

    ``within`` adds stratifiers *outside* the band, so
    ``within=(Stratifier.RECOVERY_CLASS,)`` gives the within-class effect
    rather than the class-mix-confounded pooled one.

    Cells come back in the order the caller's ``delay_bands`` were declared,
    not in alphabetical label order. A curve is read along its axis, and
    "10-14" sorting before "5-9" would make a monotone decay look ragged.
    """
    if not policy.delay_bands:
        raise ValueError("delay_effect_curve requires EstimationPolicy.delay_bands")
    stratifiers = (*within, Stratifier.DELAY_BAND)
    estimate = estimate_rates(
        rows, basis=RateBasis.DECIDED, stratify_by=stratifiers, policy=policy, as_of=as_of
    )
    band_order = {band.label: index for index, band in enumerate(policy.delay_bands)}

    def sort_key(cell: RateCell) -> tuple[tuple[str, str], ...] | tuple[object, ...]:
        band = cell.stratum.value_of(Stratifier.DELAY_BAND) or ""
        outer = tuple(part for part in cell.stratum.parts if part[0] != str(Stratifier.DELAY_BAND))
        # Unknown labels (e.g. UNBANDED) sort after every declared band.
        return (outer, band_order.get(band, len(band_order)), band)

    ordered = tuple(sorted(estimate.cells, key=sort_key))
    return RateEstimate(
        basis=estimate.basis,
        stratifiers=estimate.stratifiers,
        cells=ordered,
        disclosure=estimate.disclosure,
        policy_min_cohort=estimate.policy_min_cohort,
        confidence=estimate.confidence,
    )


def band_sequence(estimate: RateEstimate) -> tuple[StratumKey, ...]:
    """The strata of a curve, in the order the estimate holds them."""
    return tuple(cell.stratum for cell in estimate.cells)
