"""Confidence intervals on proportions, and the normal distribution behind them.

Everything here is stdlib ``math``. The workspace has no ``scipy`` and only
the warehouse generator has ``numpy``; adding either to a capability package
to obtain three scalar functions would be a poor trade, and import-linter now
forbids it for this package specifically.

**Why Wilson and not Wald.** The textbook interval
``p̂ ± z·sqrt(p̂(1-p̂)/n)`` is the Wald interval, and it is wrong in exactly
the places this capability spends its time:

* At ``p̂ = 0`` or ``p̂ = 1`` its width is **zero**. The FINAL recovery class
  decides 66 chains and wins 2; past a confirmed filing deadline the observed
  rate is 0 of 39. Wald reports the second as "0.0%, ± nothing" — an interval
  that asserts certainty from 39 observations. Wilson reports 0% with an upper
  bound near 9%, which is the true state of knowledge.
* Its actual coverage at 95% nominal is badly below 95% for small ``n`` and
  for ``p̂`` away from ½, and the shortfall is erratic rather than
  conservative — it does not fail safely.
* It can place an endpoint outside ``[0, 1]``, which then has to be clipped,
  which biases the interval it was clipped from.

Wilson inverts the score test instead of the Wald test: it solves for the
proportions ``p`` at which the observed ``p̂`` would sit ``z`` standard errors
away, using ``p``'s own standard error rather than ``p̂``'s. The result stays
inside ``[0, 1]`` by construction, never degenerates at the boundaries, and
holds close to nominal coverage down to single-digit ``n``. It costs one extra
line of arithmetic. There is no case in this package where Wald would be
preferable, so Wald is not implemented.

Determinism: results are quantized to :data:`PLACES` before publication, which
puts every platform's last-ULP disagreement well below the published digit.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal

from revi_statistics_contracts.contract import Interval

#: Published precision for every rate, probability and statistic. Ten
#: decimal places is far beyond any reportable precision and comfortably
#: above the noise floor of the double-precision arithmetic underneath, so
#: quantizing here makes results byte-identical across runs and platforms
#: without discarding anything a caller could use.
PLACES = Decimal("1E-10")

_SQRT_2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)

# Acklam's rational approximation to the standard normal quantile.
_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_B = (
    -5.447609879822406e01,
    1.615858368580409e02,
    -1.556989798598866e02,
    6.680131188771972e01,
    -1.328068155288572e01,
)
_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996e00,
    3.754408661907416e00,
)
_P_LOW = 0.02425
_P_HIGH = 1.0 - _P_LOW


def quantize(value: float | Decimal) -> Decimal:
    """Round to the published precision, half-to-even."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"cannot publish a non-finite statistic: {value}")
        value = Decimal(value)
    return value.quantize(PLACES, rounding=ROUND_HALF_EVEN)


def normal_cdf(z: float) -> float:
    """Standard normal CDF, via the stdlib complementary error function."""
    return 0.5 * math.erfc(-z / _SQRT_2)


def two_sided_p_from_z(z: float) -> float:
    """``P(|Z| >= |z|)`` — exact in one ``erfc`` call, no tail subtraction."""
    return math.erfc(abs(z) / _SQRT_2)


def normal_quantile(p: float) -> float:
    """The standard normal quantile ``Phi^-1(p)`` for ``0 < p < 1``.

    Acklam's rational approximation (relative error ~1e-9) refined by one
    Halley step against ``erfc``, which takes it to near machine precision.
    Implemented rather than imported because the alternative is a scipy
    dependency for one scalar function.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"normal_quantile requires 0 < p < 1; got {p}")
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
        )
    elif p <= _P_HIGH:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / (
            ((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
        )
    # One Halley refinement.
    err = normal_cdf(x) - p
    u = err * _SQRT_2PI * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


def z_for_confidence(confidence: Decimal) -> float:
    """The two-sided critical value for a confidence level (0.95 -> 1.9600)."""
    level = float(confidence)
    if not 0.0 < level < 1.0:
        raise ValueError(f"confidence must lie strictly in (0, 1); got {confidence}")
    return normal_quantile(1.0 - (1.0 - level) / 2.0)


def _wilson_bounds(successes: int, n: int, z: float) -> tuple[float, float]:
    p_hat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    return center - half, center + half


def wilson_interval(successes: int, n: int, confidence: Decimal) -> Interval:
    """Wilson score interval for ``successes`` of ``n`` at ``confidence``.

    Non-degenerate at ``successes == 0`` and ``successes == n``, and always
    inside ``[0, 1]`` before any clamping — see the module docstring for why
    that matters here rather than being a nicety.
    """
    if n <= 0:
        raise ValueError("wilson_interval requires n > 0")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must lie in [0, n]; got {successes}/{n}")
    low, high = _wilson_bounds(successes, n, z_for_confidence(confidence))
    # The score interval cannot leave [0, 1] mathematically; clamp only to
    # absorb floating-point dust at the exact boundaries.
    return Interval(
        low=quantize(max(0.0, low)),
        high=quantize(min(1.0, high)),
        confidence=confidence,
    )


def proportion(successes: int, n: int) -> Decimal:
    """The point estimate, quantized for publication."""
    if n <= 0:
        raise ValueError("proportion requires n > 0")
    return quantize(Decimal(successes) / Decimal(n))


def newcombe_risk_difference_interval(
    left_successes: int,
    left_n: int,
    right_successes: int,
    right_n: int,
    confidence: Decimal,
) -> Interval:
    """Newcombe's hybrid-score interval for ``p_left - p_right``.

    Built from the two arms' Wilson intervals rather than from a pooled
    standard error, so the effect-size interval and the per-arm intervals
    rest on the same method and cannot disagree about whether an arm's rate
    is near a boundary. Like Wilson it behaves at zero counts, where the
    Wald difference interval collapses to a point.
    """
    if left_n <= 0 or right_n <= 0:
        raise ValueError("newcombe interval requires both arms to be non-empty")
    z = z_for_confidence(confidence)
    p1 = left_successes / left_n
    p2 = right_successes / right_n
    l1, u1 = _wilson_bounds(left_successes, left_n, z)
    l2, u2 = _wilson_bounds(right_successes, right_n, z)
    delta = p1 - p2
    low = delta - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = delta + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return Interval(
        low=quantize(max(-1.0, low)),
        high=quantize(min(1.0, high)),
        confidence=confidence,
    )
