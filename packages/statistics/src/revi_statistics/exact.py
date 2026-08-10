"""Fisher's exact test, computed exactly.

``scipy`` is not a workspace dependency and this package does not add one —
see :mod:`revi_statistics.intervals`. The hypergeometric tail is implemented
here directly, and implementing it turns out to be *better* than importing it
rather than merely cheaper.

The usual implementation sums floating-point probabilities and decides which
tables are "at least as extreme as the observed one" with a relative
tolerance (scipy uses ``1 + 1e-7``) because floats make the comparison
``P(x) <= P(a)`` unreliable for tables of equal probability. Here the
probabilities are :class:`~fractions.Fraction` values built from exact integer
binomial coefficients, so that comparison is exact and the fudge factor is not
needed. The p-value is a rational number, quantized once at the end.

Cost is bounded by orienting the table so the smaller row total leads: the
support of the hypergeometric is then at most ``min(row, col) + 1`` terms.
Fisher is only ever reached through the small-cell guard, so tables arriving
here are small by construction; :data:`MAX_EXACT_TOTAL` is a defensive stop,
not a working limit.
"""

from __future__ import annotations

import math
from decimal import Decimal
from fractions import Fraction

from revi_kernel.errors import QueryBudgetExceededError
from revi_statistics.intervals import quantize

#: Above this grand total the exact computation is refused rather than
#: silently swapped for an approximation. Nothing routed by the small-cell
#: guard comes close.
MAX_EXACT_TOTAL = 200_000

#: Minimum expected count in every cell of the 2x2 table before the normal
#: approximation behind the two-proportion z test is trusted. The classical
#: rule; below it the z test's p-value is not the thing it claims to be, and
#: the exact test is used instead.
MIN_EXPECTED_CELL = 5.0


def min_expected_cell_count(
    left_successes: int, left_n: int, right_successes: int, right_n: int
) -> float:
    """The smallest expected cell count of the 2x2 table under independence."""
    total = left_n + right_n
    if total == 0:
        raise ValueError("cannot compute expected counts for an empty table")
    successes = left_successes + right_successes
    failures = total - successes
    return min(
        left_n * successes / total,
        left_n * failures / total,
        right_n * successes / total,
        right_n * failures / total,
    )


def needs_exact_test(
    left_successes: int,
    left_n: int,
    right_successes: int,
    right_n: int,
    *,
    min_expected: float = MIN_EXPECTED_CELL,
) -> bool:
    """Does the small-cell guard trip, routing this table to Fisher?"""
    return min_expected_cell_count(left_successes, left_n, right_successes, right_n) < min_expected


def _hypergeometric_pmf(x: int, *, n1: int, successes: int, total: int) -> Fraction:
    """``P(A = x)`` with both margins fixed — exact rational."""
    return Fraction(
        math.comb(successes, x) * math.comb(total - successes, n1 - x),
        math.comb(total, n1),
    )


def fishers_exact_two_sided(
    left_successes: int, left_n: int, right_successes: int, right_n: int
) -> Decimal:
    """Two-sided Fisher's exact p-value for a 2x2 table.

    The two-sided p-value is the total probability of every table with the
    observed margins whose probability is at most the observed table's — the
    standard convention, and here an exact comparison rather than a
    tolerance-guarded floating-point one.
    """
    for name, value in (
        ("left_n", left_n),
        ("right_n", right_n),
    ):
        if value <= 0:
            raise ValueError(f"fishers_exact_two_sided requires {name} > 0")
    if not (0 <= left_successes <= left_n and 0 <= right_successes <= right_n):
        raise ValueError("successes must lie within their arm's n")

    total = left_n + right_n
    if total > MAX_EXACT_TOTAL:
        raise QueryBudgetExceededError(
            f"exact test refused: table total {total} exceeds {MAX_EXACT_TOTAL}",
            details={"total": total, "limit": MAX_EXACT_TOTAL},
        )

    # Orient so the leading row is the smaller one; the p-value is invariant
    # and the support (hence the cost) shrinks to that row's size at most.
    if left_n <= right_n:
        observed, n1 = left_successes, left_n
    else:
        observed, n1 = right_successes, right_n
    successes = left_successes + right_successes

    observed_p = _hypergeometric_pmf(observed, n1=n1, successes=successes, total=total)
    low = max(0, n1 - (total - successes))
    high = min(n1, successes)
    tail = Fraction(0)
    for x in range(low, high + 1):
        probability = _hypergeometric_pmf(x, n1=n1, successes=successes, total=total)
        if probability <= observed_p:
            tail += probability
    # Rational arithmetic cannot exceed 1, but guard the published value
    # against a degenerate table rather than emitting p > 1.
    tail = min(tail, Fraction(1))
    return quantize(Decimal(tail.numerator) / Decimal(tail.denominator))
