"""Fisher's exact test, and the guard that routes to it."""

from __future__ import annotations

import math
from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revi_kernel.errors import QueryBudgetExceededError
from revi_statistics.exact import (
    MAX_EXACT_TOTAL,
    fishers_exact_two_sided,
    min_expected_cell_count,
    needs_exact_test,
)


class TestGuard:
    def test_expected_counts(self) -> None:
        # 2x2 with row totals 10/10 and column totals 10/10: every expected
        # cell is 5 exactly, which is the boundary and not below it.
        assert min_expected_cell_count(7, 10, 3, 10) == pytest.approx(5.0)
        assert not needs_exact_test(7, 10, 3, 10)

    def test_trips_on_a_thin_cell(self) -> None:
        assert needs_exact_test(0, 5, 3, 5)
        assert needs_exact_test(1, 200, 0, 4)

    def test_does_not_trip_on_the_reference_payer_contrast(self) -> None:
        """84/148 vs 34/117 is comfortably in normal-approximation territory."""
        assert not needs_exact_test(84, 148, 34, 117)


class TestFishersExact:
    def test_lady_tasting_tea(self) -> None:
        """Fisher's own experiment: 3/4 vs 1/4, the canonical p = 0.4857."""
        p = fishers_exact_two_sided(3, 4, 1, 4)
        assert float(p) == pytest.approx(0.4857142857, abs=1e-9)

    def test_perfect_separation(self) -> None:
        """4/4 vs 0/4 — the tea-taster who gets every cup right.

        Only two of the C(8,4) = 70 equally-likely tables are this extreme,
        one at each end, so p = 2/70 exactly.
        """
        p = fishers_exact_two_sided(4, 4, 0, 4)
        assert float(p) == pytest.approx(2.0 / 70.0, abs=1e-10)

    @pytest.mark.parametrize(
        ("a", "n1", "c", "n2", "expected"),
        [
            (1, 3, 3, 3, 0.4),
            # N=15, n1=5, k=10 over C(15,5)=3003: the tables no likelier
            # than the observed 450/3003 are 1 + 50 + 450 + 252 = 753.
            (2, 5, 8, 10, 753.0 / 3003.0),
            # N=20, n1=10, k=5 over C(20,10)=184756: symmetric extremes
            # 3003 + 3003 = 6006.
            (0, 10, 5, 10, 6006.0 / 184756.0),
            (10, 10, 0, 10, 2.0 / 184756.0),
        ],
    )
    def test_known_tables(self, a: int, n1: int, c: int, n2: int, expected: float) -> None:
        assert float(fishers_exact_two_sided(a, n1, c, n2)) == pytest.approx(expected, abs=1e-10)

    def test_matches_an_independent_brute_force_enumeration(self) -> None:
        """Re-derive the p-value from the definition, not the implementation.

        Enumerate every table with the observed margins, keep the ones no
        more probable than the observed one, and sum. Same answer, computed
        without reusing the module's tail loop.
        """
        tables = [
            (3, 10, 7, 12),
            (1, 8, 6, 9),
            (0, 15, 4, 11),
            (5, 20, 5, 6),
            (2, 5, 8, 10),
            (0, 10, 5, 10),
            (4, 4, 0, 4),
            (12, 40, 3, 9),
        ]
        for a, n1, c, n2 in tables:
            total = n1 + n2
            successes = a + c
            observed = Fraction(
                math.comb(successes, a) * math.comb(total - successes, n1 - a),
                math.comb(total, n1),
            )
            brute = Fraction(0)
            for x in range(max(0, n1 - (total - successes)), min(n1, successes) + 1):
                probability = Fraction(
                    math.comb(successes, x) * math.comb(total - successes, n1 - x),
                    math.comb(total, n1),
                )
                if probability <= observed:
                    brute += probability
            expected = Decimal(brute.numerator) / Decimal(brute.denominator)
            got = fishers_exact_two_sided(a, n1, c, n2)
            assert float(got) == pytest.approx(float(expected), abs=1e-10)

    def test_is_symmetric_under_swapping_the_arms(self) -> None:
        assert fishers_exact_two_sided(3, 10, 7, 12) == fishers_exact_two_sided(7, 12, 3, 10)

    def test_identical_arms_give_p_of_one(self) -> None:
        assert fishers_exact_two_sided(5, 10, 5, 10) == Decimal(1).quantize(Decimal("1E-10"))

    @given(
        n1=st.integers(min_value=1, max_value=60),
        n2=st.integers(min_value=1, max_value=60),
        data=st.data(),
    )
    def test_p_value_is_a_probability(self, n1: int, n2: int, data: st.DataObject) -> None:
        a = data.draw(st.integers(min_value=0, max_value=n1))
        c = data.draw(st.integers(min_value=0, max_value=n2))
        p = fishers_exact_two_sided(a, n1, c, n2)
        assert Decimal(0) <= p <= Decimal(1)

    def test_is_deterministic(self) -> None:
        first = fishers_exact_two_sided(2, 30, 11, 28)
        for _ in range(30):
            assert fishers_exact_two_sided(2, 30, 11, 28) == first

    def test_refuses_an_absurd_table_rather_than_approximating(self) -> None:
        big = MAX_EXACT_TOTAL
        with pytest.raises(QueryBudgetExceededError):
            fishers_exact_two_sided(1, big, 1, big)

    def test_rejects_impossible_inputs(self) -> None:
        with pytest.raises(ValueError, match="left_n > 0"):
            fishers_exact_two_sided(0, 0, 1, 5)
        with pytest.raises(ValueError, match="within their arm"):
            fishers_exact_two_sided(6, 5, 1, 5)


class TestAgreementWithTheNormalApproximation:
    """Where the guard says the approximation is fine, the two should agree.

    Not a tight equality — they are different tests — but a large table
    whose z-test p-value differs from its exact p-value by an order of
    magnitude would mean one of the two is wrong.
    """

    @pytest.mark.parametrize(
        ("a", "n1", "c", "n2"),
        [(84, 148, 34, 117), (316, 508, 374, 713), (50, 100, 40, 100)],
    )
    def test_close_on_well_populated_tables(self, a: int, n1: int, c: int, n2: int) -> None:
        from revi_statistics.contrasts import two_proportion_z
        from revi_statistics.intervals import two_sided_p_from_z

        assert not needs_exact_test(a, n1, c, n2)
        approx = two_sided_p_from_z(two_proportion_z(a, n1, c, n2))
        exact = float(fishers_exact_two_sided(a, n1, c, n2))
        if exact > 0:
            assert 0.2 < approx / exact < 5.0
