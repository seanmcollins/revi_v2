"""Two-cohort comparison: is this difference real, and how big is it?

Two numbers are always reported together, because they answer different
questions and either alone misleads:

* the **p-value** — could a difference this large have arisen by chance?
* the **risk difference** with its interval — how large is it, in
  percentage points, and how precisely do we know that?

A significant tiny difference and an insignificant large one are both common,
and a comparison that publishes only significance invites reading the first as
important and the second as absent.

Which test runs is decided by the data, not the caller. The pooled
two-proportion z test is a normal approximation, and the approximation is
honest only when every expected cell of the 2x2 table is reasonably populated.
When the small-cell guard trips, the contrast routes to Fisher's exact test
(computed exactly — see :mod:`revi_statistics.exact`), and the published
:class:`ContrastTest` says which one ran, so nobody has to guess whether a
borderline p-value came from an approximation that did not apply.

Below the cohort floor nothing is tested at all. The floor is a disclosure
policy about publishing rates, and a p-value comparing two rates too thin to
publish is a rate comparison in disguise.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from revi_statistics.exact import fishers_exact_two_sided, needs_exact_test
from revi_statistics.intervals import (
    newcombe_risk_difference_interval,
    proportion,
    quantize,
    two_sided_p_from_z,
    wilson_interval,
)
from revi_statistics.rates import denominator_rows, is_success
from revi_statistics_contracts.contract import (
    Contrast,
    ContrastArm,
    ContrastTest,
    DenialRow,
    EstimationPolicy,
    RateBasis,
    RateCell,
)


def two_proportion_z(
    left_successes: int, left_n: int, right_successes: int, right_n: int
) -> float:
    """Pooled two-proportion z statistic.

    Pooled rather than unpooled because the null hypothesis being tested is
    that both arms share one proportion; estimating the standard error under
    that null is what makes this a test of it.
    """
    if left_n <= 0 or right_n <= 0:
        raise ValueError("two_proportion_z requires both arms to be non-empty")
    pooled = (left_successes + right_successes) / (left_n + right_n)
    variance = pooled * (1.0 - pooled) * (1.0 / left_n + 1.0 / right_n)
    if variance <= 0.0:
        # Both arms all-success or all-failure: no contrast to test.
        return 0.0
    return (left_successes / left_n - right_successes / right_n) / math.sqrt(variance)


def _arm(label: str, successes: int, n: int, policy: EstimationPolicy) -> ContrastArm:
    if n < policy.min_cohort:
        return ContrastArm(label=label, n=n, successes=successes, rate=None, interval=None)
    return ContrastArm(
        label=label,
        n=n,
        successes=successes,
        rate=proportion(successes, n),
        interval=wilson_interval(successes, n, policy.confidence),
    )


def contrast_counts(
    *,
    left_label: str,
    left_successes: int,
    left_n: int,
    right_label: str,
    right_successes: int,
    right_n: int,
    policy: EstimationPolicy,
) -> Contrast:
    """Compare two success-out-of-n counts. The primitive the rest wrap.

    Refuses when either arm is below ``policy.min_cohort``, publishing both
    arms' sizes and the reason. A refusal is a result, not an error: the
    caller learns the comparison is unsupportable and how far short it fell.
    """
    left = _arm(left_label, left_successes, left_n, policy)
    right = _arm(right_label, right_successes, right_n, policy)

    if left_n < policy.min_cohort or right_n < policy.min_cohort:
        thin = [
            f"{arm.label} n={arm.n}"
            for arm in (left, right)
            if arm.n < policy.min_cohort
        ]
        return Contrast(
            left=left,
            right=right,
            test=ContrastTest.REFUSED,
            min_cohort=policy.min_cohort,
            refusal_reason=(
                f"cohort below the floor of {policy.min_cohort}: " + "; ".join(thin)
            ),
        )

    difference = quantize(
        Decimal(left_successes) / Decimal(left_n) - Decimal(right_successes) / Decimal(right_n)
    )
    interval = newcombe_risk_difference_interval(
        left_successes, left_n, right_successes, right_n, policy.confidence
    )

    if needs_exact_test(left_successes, left_n, right_successes, right_n):
        return Contrast(
            left=left,
            right=right,
            test=ContrastTest.FISHERS_EXACT,
            min_cohort=policy.min_cohort,
            risk_difference=difference,
            risk_difference_interval=interval,
            p_value=fishers_exact_two_sided(left_successes, left_n, right_successes, right_n),
        )

    z = two_proportion_z(left_successes, left_n, right_successes, right_n)
    return Contrast(
        left=left,
        right=right,
        test=ContrastTest.TWO_PROPORTION_Z,
        min_cohort=policy.min_cohort,
        risk_difference=difference,
        risk_difference_interval=interval,
        z_statistic=quantize(z),
        p_value=quantize(two_sided_p_from_z(z)),
    )


def compare_rate_cells(left: RateCell, right: RateCell, *, policy: EstimationPolicy) -> Contrast:
    """Contrast two cells of the same estimate.

    Both cells must share a basis: a DECIDED rate and a PURSUIT rate are
    different conditionals over different populations, and differencing them
    produces a number with no interpretation.
    """
    if left.basis is not right.basis:
        raise ValueError(
            f"cannot contrast a {left.basis} rate with a {right.basis} rate — "
            "they are different conditionals over different denominators"
        )
    return contrast_counts(
        left_label=left.stratum.label,
        left_successes=left.successes,
        left_n=left.n,
        right_label=right.stratum.label,
        right_successes=right.successes,
        right_n=right.n,
        policy=policy,
    )


def compare_cohorts(
    left_rows: Sequence[DenialRow],
    right_rows: Sequence[DenialRow],
    *,
    left_label: str,
    right_label: str,
    basis: RateBasis,
    policy: EstimationPolicy,
    as_of: date,
) -> Contrast:
    """Contrast two populations of denial rows on the same basis.

    Each side is reduced to its denominator by exactly the machinery
    :func:`~revi_statistics.rates.estimate_rates` uses, so a contrast can
    never rest on a population the rate estimate would have treated
    differently.
    """
    left_kept, _ = denominator_rows(left_rows, basis=basis, policy=policy, as_of=as_of)
    right_kept, _ = denominator_rows(right_rows, basis=basis, policy=policy, as_of=as_of)
    return contrast_counts(
        left_label=left_label,
        left_successes=sum(1 for row in left_kept if is_success(row, basis)),
        left_n=len(left_kept),
        right_label=right_label,
        right_successes=sum(1 for row in right_kept if is_success(row, basis)),
        right_n=len(right_kept),
        policy=policy,
    )
