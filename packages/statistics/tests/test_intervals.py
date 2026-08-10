"""Interval arithmetic: the Wilson claim, and the coverage that backs it.

The package's docstrings assert that Wilson is chosen over Wald for concrete
reasons. These tests hold those assertions to account rather than taking them
on trust — in particular ``test_wald_degenerates_where_wilson_does_not``,
which reproduces the exact failure (0 of n) that occurs in the reference
warehouse's past-deadline cohort.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revi_statistics.intervals import (
    newcombe_risk_difference_interval,
    normal_cdf,
    normal_quantile,
    proportion,
    two_sided_p_from_z,
    wilson_interval,
    z_for_confidence,
)

NINETY_FIVE = Decimal("0.95")


class TestNormalQuantile:
    @pytest.mark.parametrize(
        ("p", "expected"),
        [
            (0.975, 1.959963984540054),
            (0.995, 2.5758293035489004),
            (0.95, 1.6448536269514722),
            (0.5, 0.0),
            (0.025, -1.959963984540054),
            (0.001, -3.090232306167813),
        ],
    )
    def test_known_quantiles(self, p: float, expected: float) -> None:
        assert normal_quantile(p) == pytest.approx(expected, abs=1e-12)

    def test_z_for_confidence(self) -> None:
        assert z_for_confidence(NINETY_FIVE) == pytest.approx(1.959963984540054, abs=1e-12)
        assert z_for_confidence(Decimal("0.99")) == pytest.approx(2.5758293035489004, abs=1e-12)

    @given(st.floats(min_value=1e-6, max_value=1 - 1e-6))
    def test_round_trips_through_the_cdf(self, p: float) -> None:
        assert normal_cdf(normal_quantile(p)) == pytest.approx(p, abs=1e-10)

    @pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.1])
    def test_rejects_out_of_range(self, p: float) -> None:
        with pytest.raises(ValueError, match="0 < p < 1"):
            normal_quantile(p)

    def test_two_sided_p_matches_the_tail(self) -> None:
        assert two_sided_p_from_z(1.959963984540054) == pytest.approx(0.05, abs=1e-12)
        assert two_sided_p_from_z(-1.959963984540054) == pytest.approx(0.05, abs=1e-12)
        assert two_sided_p_from_z(0.0) == pytest.approx(1.0, abs=1e-12)


class TestWilson:
    def test_known_interval(self) -> None:
        # 8 of 20 at 95%. The constants come from the independent
        # score-equation solution below, not from a remembered table.
        interval = wilson_interval(8, 20, NINETY_FIVE)
        assert float(interval.low) == pytest.approx(0.2188065324, abs=1e-9)
        assert float(interval.high) == pytest.approx(0.6134184992, abs=1e-9)

    @pytest.mark.parametrize(
        ("successes", "n"),
        [(8, 20), (0, 39), (84, 148), (34, 117), (316, 508), (2, 66), (1, 3), (99, 100)],
    )
    def test_closed_form_solves_the_score_equation(self, successes: int, n: int) -> None:
        """The definition, checked against the implementation.

        The Wilson interval *is* the set of ``p`` for which the score
        statistic ``(p̂ - p) / sqrt(p(1-p)/n)`` stays within ``±z``. Rather
        than trusting a transcribed algebraic rearrangement, solve that
        equation numerically by bisection and require the closed form to
        agree. This catches a mis-typed coefficient in a way no remembered
        constant can.
        """
        z = z_for_confidence(NINETY_FIVE)
        interval = wilson_interval(successes, n, NINETY_FIVE)
        p_hat = successes / n

        def score(p: float) -> float:
            return (p_hat - p) / math.sqrt(p * (1.0 - p) / n)

        def bisect(low: float, high: float, target: float) -> float:
            for _ in range(200):
                mid = (low + high) / 2.0
                if (score(mid) > target) == (score(low) > target):
                    low = mid
                else:
                    high = mid
            return (low + high) / 2.0

        # The score statistic is undefined at p = 0 and p = 1, so brackets
        # are opened just inside them.
        epsilon = 1e-12
        if successes > 0:
            root = bisect(epsilon, min(p_hat, 1 - epsilon), z)
            assert float(interval.low) == pytest.approx(root, abs=1e-9)
        else:
            assert interval.low == Decimal(0)
        if successes < n:
            root = bisect(max(p_hat, epsilon), 1 - epsilon, -z)
            assert float(interval.high) == pytest.approx(root, abs=1e-9)
        else:
            assert interval.high == Decimal(1)

    def test_wald_degenerates_where_wilson_does_not(self) -> None:
        """The reason Wald is not implemented, stated as a test.

        0 of 39 is the reference warehouse's past-deadline confirmed cohort.
        Wald's half-width is ``z * sqrt(p(1-p)/n)``, which is exactly zero at
        ``p = 0`` — an interval asserting certainty from 39 observations.
        """
        wald_half_width = 1.96 * math.sqrt(0.0 * 1.0 / 39)
        assert wald_half_width == 0.0

        interval = wilson_interval(0, 39, NINETY_FIVE)
        assert interval.low == Decimal(0)
        assert interval.high > Decimal("0.05")
        assert interval.high < Decimal("0.15")

    def test_boundaries_stay_inside_the_unit_interval(self) -> None:
        for n in (1, 5, 39, 400):
            low_end = wilson_interval(0, n, NINETY_FIVE)
            high_end = wilson_interval(n, n, NINETY_FIVE)
            assert low_end.low == Decimal(0)
            assert low_end.high > Decimal(0)
            assert high_end.high == Decimal(1)
            assert high_end.low < Decimal(1)

    @given(
        n=st.integers(min_value=1, max_value=5000),
        confidence=st.sampled_from([Decimal("0.8"), Decimal("0.9"), Decimal("0.95"), Decimal("0.99")]),
        data=st.data(),
    )
    def test_interval_always_brackets_the_point_estimate(
        self, n: int, confidence: Decimal, data: st.DataObject
    ) -> None:
        successes = data.draw(st.integers(min_value=0, max_value=n))
        interval = wilson_interval(successes, n, confidence)
        point = proportion(successes, n)
        assert interval.contains(point)
        assert Decimal(0) <= interval.low <= interval.high <= Decimal(1)

    @given(n=st.integers(min_value=2, max_value=2000), data=st.data())
    def test_higher_confidence_is_never_narrower(self, n: int, data: st.DataObject) -> None:
        successes = data.draw(st.integers(min_value=0, max_value=n))
        narrow = wilson_interval(successes, n, Decimal("0.90"))
        wide = wilson_interval(successes, n, Decimal("0.99"))
        assert wide.low <= narrow.low
        assert wide.high >= narrow.high

    def test_rejects_impossible_counts(self) -> None:
        with pytest.raises(ValueError, match="n > 0"):
            wilson_interval(0, 0, NINETY_FIVE)
        with pytest.raises(ValueError, match=r"\[0, n\]"):
            wilson_interval(5, 3, NINETY_FIVE)

    def test_is_deterministic(self) -> None:
        first = wilson_interval(84, 148, NINETY_FIVE)
        for _ in range(50):
            assert wilson_interval(84, 148, NINETY_FIVE) == first


class TestWilsonCoverage:
    """Fixed-seed simulation: does the nominal 95% interval cover ~95%?

    Not a proof, a sanity check with a pinned seed so it is reproducible and
    cannot flake. Wilson is known to under-cover slightly at some (n, p)
    combinations — the assertion band is set to catch an implementation that
    is *wrong*, not one that is merely imperfect, and the lower bound is
    still far above what Wald achieves at these settings.
    """

    @pytest.mark.parametrize(
        ("n", "p"),
        [(10, 0.5), (10, 0.1), (25, 0.3), (40, 0.05), (100, 0.5), (150, 0.62), (500, 0.03)],
    )
    def test_coverage_is_near_nominal(self, n: int, p: float) -> None:
        rng = random.Random(20260802)
        trials = 4000
        covered = 0
        target = Decimal(str(p))
        for _ in range(trials):
            successes = sum(1 for _ in range(n) if rng.random() < p)
            if wilson_interval(successes, n, NINETY_FIVE).contains(target):
                covered += 1
        coverage = covered / trials
        assert 0.90 <= coverage <= 1.0, f"n={n} p={p} coverage={coverage:.4f}"

    def test_wilson_beats_wald_at_a_small_sample(self) -> None:
        """The concrete comparison the module docstring claims."""
        rng = random.Random(4242)
        n, p, trials = 20, 0.1, 4000
        target = Decimal("0.1")
        wilson_hits = 0
        wald_hits = 0
        for _ in range(trials):
            successes = sum(1 for _ in range(n) if rng.random() < p)
            if wilson_interval(successes, n, NINETY_FIVE).contains(target):
                wilson_hits += 1
            p_hat = successes / n
            half = 1.959963984540054 * math.sqrt(p_hat * (1 - p_hat) / n)
            if p_hat - half <= p <= p_hat + half:
                wald_hits += 1
        assert wilson_hits > wald_hits
        assert wilson_hits / trials >= 0.90
        assert wald_hits / trials < 0.90


class TestNewcombe:
    def test_difference_interval_brackets_the_difference(self) -> None:
        interval = newcombe_risk_difference_interval(84, 148, 34, 117, NINETY_FIVE)
        difference = Decimal(84) / Decimal(148) - Decimal(34) / Decimal(117)
        assert interval.contains(difference)
        assert interval.excludes_zero

    def test_identical_arms_straddle_zero(self) -> None:
        interval = newcombe_risk_difference_interval(50, 100, 50, 100, NINETY_FIVE)
        assert interval.low < 0 < interval.high
        assert not interval.excludes_zero

    def test_survives_zero_counts(self) -> None:
        interval = newcombe_risk_difference_interval(0, 39, 0, 40, NINETY_FIVE)
        assert interval.low < 0 < interval.high
        assert interval.low > Decimal(-1)
        assert interval.high < Decimal(1)

    @given(
        left_n=st.integers(min_value=1, max_value=500),
        right_n=st.integers(min_value=1, max_value=500),
        data=st.data(),
    )
    def test_stays_within_minus_one_to_one(
        self, left_n: int, right_n: int, data: st.DataObject
    ) -> None:
        left = data.draw(st.integers(min_value=0, max_value=left_n))
        right = data.draw(st.integers(min_value=0, max_value=right_n))
        interval = newcombe_risk_difference_interval(left, left_n, right, right_n, NINETY_FIVE)
        assert Decimal(-1) <= interval.low <= interval.high <= Decimal(1)
