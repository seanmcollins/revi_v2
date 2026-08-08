"""Window-resolution rules: exact documented cases + determinism properties."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revi_kernel.refs import POST, SERVICE
from revi_kernel.scope import (
    AbsoluteRange,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    TimeWindow,
    derive_comparison,
    resolve_relative,
    resolve_window,
    shift_months,
    shift_years,
)

ANCHOR = date(2026, 8, 3)  # matches the design §10.3 reference conversation


class TestShiftMonths:
    def test_plain(self) -> None:
        assert shift_months(date(2026, 8, 3), -3) == date(2026, 5, 3)

    def test_day_clamp(self) -> None:
        assert shift_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        assert shift_months(date(2024, 3, 31), -1) == date(2024, 2, 29)  # leap

    def test_year_boundary(self) -> None:
        assert shift_months(date(2026, 1, 15), -2) == date(2025, 11, 15)

    def test_leap_year_clamp(self) -> None:
        assert shift_years(date(2024, 2, 29), -1) == date(2023, 2, 28)


class TestTrailing:
    def test_days(self) -> None:
        r = resolve_relative(RelativeRange(Decimal(7), TimeUnit.DAY), ANCHOR)
        assert r == AbsoluteRange(date(2026, 7, 28), ANCHOR)
        assert r.day_length == 7

    def test_weeks(self) -> None:
        r = resolve_relative(RelativeRange(Decimal(2), TimeUnit.WEEK), ANCHOR)
        assert r.day_length == 14
        assert r.end == ANCHOR

    def test_whole_months(self) -> None:
        r = resolve_relative(RelativeRange(Decimal(3), TimeUnit.MONTH), ANCHOR)
        assert r == AbsoluteRange(date(2026, 5, 4), ANCHOR)

    def test_fractional_months_documented_example(self) -> None:
        """The docstring example: 3.25 months before 2026-08-03.

        Whole months: 2026-05-04..2026-08-03. Preceding month step
        2026-04-04..2026-05-03 = 30 days; 0.25 x 30 = 7.5 → 8 days →
        start 2026-04-26.
        """
        r = resolve_relative(RelativeRange(Decimal("3.25"), TimeUnit.MONTH), ANCHOR)
        assert r == AbsoluteRange(date(2026, 4, 26), ANCHOR)

    def test_fractional_quarter(self) -> None:
        # 1.5 quarters = 4.5 months: whole 4 → 2026-04-04..2026-08-03;
        # preceding step 2026-03-04..2026-04-03 = 31 days; .5x31=15.5 → 16
        r = resolve_relative(RelativeRange(Decimal("1.5"), TimeUnit.QUARTER), ANCHOR)
        assert r == AbsoluteRange(date(2026, 3, 19), ANCHOR)

    def test_fractional_days_round_half_up(self) -> None:
        r = resolve_relative(RelativeRange(Decimal("2.5"), TimeUnit.DAY), ANCHOR)
        assert r.day_length == 3

    def test_zero_length_rejected(self) -> None:
        with pytest.raises(ValueError, match="zero days"):
            resolve_relative(RelativeRange(Decimal("0.4"), TimeUnit.DAY), ANCHOR)


class TestFullPeriods:
    def test_six_full_months(self) -> None:
        """'last 6 months' as full periods from Aug 3 = Feb 1 .. Jul 31."""
        r = resolve_relative(RelativeRange(Decimal(6), TimeUnit.MONTH, RangeMode.FULL_PERIODS), ANCHOR)
        assert r == AbsoluteRange(date(2026, 2, 1), date(2026, 7, 31))

    def test_full_weeks_iso(self) -> None:
        # ANCHOR 2026-08-03 is a Monday; its week starts that day.
        r = resolve_relative(RelativeRange(Decimal(1), TimeUnit.WEEK, RangeMode.FULL_PERIODS), ANCHOR)
        assert r == AbsoluteRange(date(2026, 7, 27), date(2026, 8, 2))
        assert r.start.weekday() == 0 and r.end.weekday() == 6

    def test_full_quarter(self) -> None:
        r = resolve_relative(RelativeRange(Decimal(1), TimeUnit.QUARTER, RangeMode.FULL_PERIODS), ANCHOR)
        assert r == AbsoluteRange(date(2026, 4, 1), date(2026, 6, 30))

    def test_fractional_full_months(self) -> None:
        # 6.5 full months: Feb..Jul full, extend into January by .5x31=15.5→16 days
        r = resolve_relative(RelativeRange(Decimal("6.5"), TimeUnit.MONTH, RangeMode.FULL_PERIODS), ANCHOR)
        assert r == AbsoluteRange(date(2026, 1, 16), date(2026, 7, 31))

    def test_full_days(self) -> None:
        r = resolve_relative(RelativeRange(Decimal(3), TimeUnit.DAY, RangeMode.FULL_PERIODS), ANCHOR)
        assert r == AbsoluteRange(date(2026, 7, 31), date(2026, 8, 2))


class TestToDate:
    def test_month_to_date(self) -> None:
        r = resolve_relative(RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.TO_DATE), ANCHOR)
        assert r == AbsoluteRange(date(2026, 8, 1), ANCHOR)

    def test_quarter_to_date(self) -> None:
        r = resolve_relative(RelativeRange(Decimal(1), TimeUnit.QUARTER, RangeMode.TO_DATE), ANCHOR)
        assert r == AbsoluteRange(date(2026, 7, 1), ANCHOR)

    def test_quantity_must_be_one(self) -> None:
        with pytest.raises(ValueError, match="quantity exactly 1"):
            RelativeRange(Decimal(2), TimeUnit.MONTH, RangeMode.TO_DATE)


class TestComparisons:
    def test_prior_period_full_months_calendar_aware(self) -> None:
        w = resolve_window(
            RelativeRange(Decimal(6), TimeUnit.MONTH, RangeMode.FULL_PERIODS), ANCHOR, basis=POST
        )
        cmp = derive_comparison(w, ComparisonKind.PRIOR_PERIOD)
        assert cmp.window.range == AbsoluteRange(date(2025, 8, 1), date(2026, 1, 31))

    def test_prior_period_arbitrary_window_shifts_by_day_length(self) -> None:
        w = TimeWindow(basis=POST, range=AbsoluteRange(date(2026, 7, 27), date(2026, 8, 2)))
        cmp = derive_comparison(w, ComparisonKind.PRIOR_PERIOD)
        assert cmp.window.range == AbsoluteRange(date(2026, 7, 20), date(2026, 7, 26))

    def test_prior_period_month_to_date_is_like_for_like(self) -> None:
        w = resolve_window(RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.TO_DATE), ANCHOR, basis=POST)
        cmp = derive_comparison(w, ComparisonKind.PRIOR_PERIOD)
        assert cmp.window.range == AbsoluteRange(date(2026, 7, 1), date(2026, 7, 3))

    def test_prior_year_clamps_leap_day(self) -> None:
        w = TimeWindow(basis=SERVICE, range=AbsoluteRange(date(2024, 2, 29), date(2024, 3, 5)))
        cmp = derive_comparison(w, ComparisonKind.PRIOR_YEAR)
        assert cmp.window.range == AbsoluteRange(date(2023, 2, 28), date(2023, 3, 5))

    def test_custom_requires_range(self) -> None:
        w = TimeWindow(basis=POST, range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)))
        with pytest.raises(ValueError, match="CUSTOM"):
            derive_comparison(w, ComparisonKind.CUSTOM)


@given(
    anchor=st.dates(min_value=date(2000, 1, 15), max_value=date(2100, 1, 1)),
    quantity=st.decimals(min_value="0.5", max_value="48", places=2),
    unit=st.sampled_from([TimeUnit.DAY, TimeUnit.WEEK, TimeUnit.MONTH, TimeUnit.QUARTER, TimeUnit.YEAR]),
    mode=st.sampled_from([RangeMode.TRAILING, RangeMode.FULL_PERIODS]),
)
def test_resolution_properties(anchor: date, quantity: Decimal, unit: TimeUnit, mode: RangeMode) -> None:
    """Resolution is deterministic, ordered, and anchored correctly."""
    spec = RelativeRange(quantity, unit, mode)
    first = resolve_relative(spec, anchor)
    second = resolve_relative(spec, anchor)
    assert first == second
    assert first.start <= first.end
    if mode is RangeMode.TRAILING:
        assert first.end == anchor
    else:
        assert first.end < anchor


@given(anchor=st.dates(min_value=date(2000, 1, 15), max_value=date(2100, 1, 1)))
def test_prior_period_is_adjacent_and_same_length_for_day_windows(anchor: date) -> None:
    w = TimeWindow(basis=POST, range=AbsoluteRange(anchor - timedelta(days=6), anchor))
    cmp = derive_comparison(w, ComparisonKind.PRIOR_PERIOD)
    assert cmp.window.range.end == w.range.start - timedelta(days=1)
    assert cmp.window.range.day_length == w.range.day_length
