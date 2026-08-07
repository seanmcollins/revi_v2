"""Money regression traps. If a "simplification" breaks these, it loses cents."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revi_kernel.money import cents_to_dollars, dollars_to_cents, format_cents


class TestHalfCentTrap:
    """ROUND_HALF_UP at exactly half a cent — the classic silent-loss site."""

    def test_positive_half_cent_rounds_up(self) -> None:
        assert dollars_to_cents("0.005") == 1
        assert dollars_to_cents("1.005") == 101
        assert dollars_to_cents("2.675") == 268  # float would give 267

    def test_negative_half_cent_rounds_away_from_zero(self) -> None:
        assert dollars_to_cents("-0.005") == -1
        assert dollars_to_cents("-1.005") == -101

    def test_below_half_rounds_down(self) -> None:
        assert dollars_to_cents("0.004") == 0
        assert dollars_to_cents("1.0049") == 100


class TestTypeGuards:
    def test_float_rejected(self) -> None:
        with pytest.raises(TypeError, match="float"):
            dollars_to_cents(1.005)  # type: ignore[arg-type]

    def test_bool_rejected(self) -> None:
        with pytest.raises(TypeError):
            dollars_to_cents(True)  # type: ignore[arg-type]

    def test_garbage_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            dollars_to_cents("twelve dollars")

    def test_int_is_whole_dollars(self) -> None:
        assert dollars_to_cents(12) == 1200


class TestRoundTrip:
    @given(st.integers(min_value=-(10**13), max_value=10**13))
    def test_cents_dollars_cents(self, cents: int) -> None:
        assert dollars_to_cents(cents_to_dollars(cents)) == cents

    def test_exact_decimal(self) -> None:
        assert cents_to_dollars(1234) == Decimal("12.34")
        assert cents_to_dollars(-5) == Decimal("-0.05")


class TestFormat:
    def test_format(self) -> None:
        assert format_cents(1234550) == "$12,345.50"
        assert format_cents(-1234550) == "-$12,345.50"
        assert format_cents(0) == "$0.00"
