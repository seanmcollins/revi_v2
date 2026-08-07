"""Time scope: windows, calendars, comparisons (design §6.1).

A window is a range *on a date basis* with a calendar policy. Relative ranges
("last 6 months", "last 3.25 months") resolve against an anchor date exactly
once, at planning time; the trace stores the concrete dates and replay uses
them. All resolution rules here are deterministic and documented.

Deterministic resolution rules
==============================

``AbsoluteRange`` is inclusive on both ends.

TRAILING (window ends at the anchor):
- DAY/WEEK: length = quantity (x7 for weeks) days, fractional days rounded
  HALF-UP to whole days; ``start = anchor - length + 1``.
- MONTH (QUARTER = 3 months, YEAR = 12 months): with quantity ``m + f``
  (integer ``m``, fraction ``f``): whole months step the start to
  ``shift_months(anchor, -m) + 1 day`` (calendar-aware, day clamped). The
  fraction extends the start earlier by ``round_half_up(f x D)`` days where
  ``D`` is the day-length of the *next earlier* whole-month step
  (``shift_months(anchor, -(m+1)) + 1 day .. shift_months(anchor, -m)``).
  Example: 3.25 months before 2026-08-03 → whole months give
  2026-05-04..2026-08-03; preceding month step 2026-04-04..2026-05-03 is
  30 days; 0.25 x 30 = 7.5 → 8 days → window 2026-04-26..2026-08-03.

FULL_PERIODS (last N completed calendar periods before the anchor's period):
- MONTH: integer part → last ``m`` full calendar months ending with the month
  before the anchor's month. A fraction extends the start earlier by
  ``round_half_up(f x D)`` days, ``D`` = day-length of the next earlier full
  month. Example: 6 full months before 2026-08-03 → 2026-02-01..2026-07-31.
- WEEK: ISO weeks (Mon..Sun) completed before the anchor's week.
- DAY: full days before the anchor day (``end = anchor - 1``).
- QUARTER/YEAR: calendar quarters / calendar years, fraction extends by days
  of the next earlier period.

TO_DATE (quantity must be exactly 1): start of the anchor's current period →
anchor (month-to-date, quarter-to-date, …).

Comparisons (design §6.1) derive deterministically from the primary window:
- PRIOR_PERIOD: calendar-unit windows (MONTH/QUARTER/YEAR, FULL_PERIODS or
  TO_DATE) shift by the window's period count calendar-aware; all other
  windows shift back by their exact day length.
- PRIOR_YEAR: both endpoints shift back one year (Feb 29 clamps to Feb 28);
  FULL_PERIODS month-aligned windows keep month boundaries.
- CUSTOM: an explicitly provided range.

The BUSINESS_DAY calendar policy is recorded on the window and honored where
business-day data exists (transform layer / adapter); it never silently
changes date arithmetic here.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from revi_kernel.refs import DateBasisRef


class CalendarPolicy(StrEnum):
    CALENDAR_DAY = "calendar_day"
    BUSINESS_DAY = "business_day"


@dataclass(frozen=True, slots=True)
class CalendarRef:
    id: str
    policy: CalendarPolicy = CalendarPolicy.CALENDAR_DAY


DEFAULT_CALENDAR = CalendarRef("default", CalendarPolicy.CALENDAR_DAY)
BUSINESS_CALENDAR = CalendarRef("business", CalendarPolicy.BUSINESS_DAY)


@dataclass(frozen=True, slots=True)
class AbsoluteRange:
    """Concrete dates, inclusive on both ends."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"AbsoluteRange start {self.start} after end {self.end}")

    @property
    def day_length(self) -> int:
        return (self.end - self.start).days + 1


class TimeUnit(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class RangeMode(StrEnum):
    TRAILING = "trailing"
    FULL_PERIODS = "full_periods"
    TO_DATE = "to_date"


@dataclass(frozen=True, slots=True)
class RelativeRange:
    """The original relative spec ("last full week", "last 3.25 months").

    Kept on the window for re-anchoring; replay always uses the stored
    concrete dates.
    """

    quantity: Decimal
    unit: TimeUnit
    mode: RangeMode = RangeMode.TRAILING

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("RelativeRange.quantity must be positive")
        if self.mode is RangeMode.TO_DATE and self.quantity != 1:
            raise ValueError("TO_DATE ranges must have quantity exactly 1")


@dataclass(frozen=True, slots=True)
class TimeWindow:
    basis: DateBasisRef
    range: AbsoluteRange
    requested: RelativeRange | None = None
    calendar: CalendarRef = DEFAULT_CALENDAR


class ComparisonKind(StrEnum):
    PRIOR_PERIOD = "prior_period"
    PRIOR_YEAR = "prior_year"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class Comparison:
    kind: ComparisonKind
    window: TimeWindow


# ---------------------------------------------------------------------------
# calendar arithmetic


def shift_months(day: date, months: int) -> date:
    """Shift by calendar months, clamping the day-of-month (Jan 31 → Feb 28)."""
    month_index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    last = _calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def shift_years(day: date, years: int) -> date:
    """Shift by calendar years, clamping Feb 29 → Feb 28."""
    return shift_months(day, years * 12)


def _round_half_up_days(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _split_quantity(quantity: Decimal) -> tuple[int, Decimal]:
    whole = int(quantity)
    return whole, quantity - whole


def _months_per_unit(unit: TimeUnit) -> int:
    if unit is TimeUnit.MONTH:
        return 1
    if unit is TimeUnit.QUARTER:
        return 3
    if unit is TimeUnit.YEAR:
        return 12
    raise ValueError(f"{unit} is not month-based")


def _period_start(day: date, unit: TimeUnit) -> date:
    """First day of the calendar period containing ``day``."""
    if unit is TimeUnit.DAY:
        return day
    if unit is TimeUnit.WEEK:
        return day - timedelta(days=day.weekday())  # ISO week starts Monday
    if unit is TimeUnit.MONTH:
        return day.replace(day=1)
    if unit is TimeUnit.QUARTER:
        first_month = 3 * ((day.month - 1) // 3) + 1
        return date(day.year, first_month, 1)
    return date(day.year, 1, 1)


def resolve_relative(spec: RelativeRange, anchor: date) -> AbsoluteRange:
    """Resolve a relative range against an anchor date. Pure and deterministic."""
    whole, fraction = _split_quantity(spec.quantity)

    if spec.mode is RangeMode.TO_DATE:
        return AbsoluteRange(_period_start(anchor, spec.unit), anchor)

    if spec.mode is RangeMode.TRAILING:
        if spec.unit in (TimeUnit.DAY, TimeUnit.WEEK):
            per = 7 if spec.unit is TimeUnit.WEEK else 1
            days = _round_half_up_days(spec.quantity * per)
            if days < 1:
                raise ValueError(f"window resolves to zero days: {spec.quantity} {spec.unit}")
            return AbsoluteRange(anchor - timedelta(days=days - 1), anchor)
        months = _months_per_unit(spec.unit)
        whole_months = whole * months
        frac_months = fraction * months
        m_whole, m_frac = _split_quantity(frac_months) if frac_months else (0, Decimal(0))
        whole_months += m_whole
        start = shift_months(anchor, -whole_months) + timedelta(days=1)
        if m_frac:
            prev_step = AbsoluteRange(
                shift_months(anchor, -(whole_months + 1)) + timedelta(days=1),
                shift_months(anchor, -whole_months),
            )
            start -= timedelta(days=_round_half_up_days(m_frac * prev_step.day_length))
        return AbsoluteRange(start, anchor)

    # FULL_PERIODS
    if spec.unit is TimeUnit.DAY:
        days = _round_half_up_days(spec.quantity)
        end = anchor - timedelta(days=1)
        return AbsoluteRange(end - timedelta(days=days - 1), end)
    if spec.unit is TimeUnit.WEEK:
        this_week = _period_start(anchor, TimeUnit.WEEK)
        end = this_week - timedelta(days=1)
        days = _round_half_up_days(spec.quantity * 7)
        return AbsoluteRange(end - timedelta(days=days - 1), end)
    months = _months_per_unit(spec.unit)
    current_start = _period_start(anchor, spec.unit)
    end = current_start - timedelta(days=1)
    whole_months = whole * months
    frac_months = fraction * months
    m_whole, m_frac = _split_quantity(frac_months) if frac_months else (0, Decimal(0))
    whole_months += m_whole
    start = shift_months(current_start, -whole_months)
    if m_frac:
        prev_period = AbsoluteRange(
            shift_months(current_start, -(whole_months + 1)),
            start - timedelta(days=1),
        )
        start -= timedelta(days=_round_half_up_days(m_frac * prev_period.day_length))
    return AbsoluteRange(start, end)


def resolve_window(
    spec: RelativeRange,
    anchor: date,
    *,
    basis: DateBasisRef,
    calendar: CalendarRef = DEFAULT_CALENDAR,
) -> TimeWindow:
    """Build a concrete ``TimeWindow`` from a relative spec (plan-time, once)."""
    return TimeWindow(basis=basis, range=resolve_relative(spec, anchor), requested=spec, calendar=calendar)


def _is_calendar_aligned(window: TimeWindow) -> bool:
    req = window.requested
    return (
        req is not None
        and req.unit in (TimeUnit.MONTH, TimeUnit.QUARTER, TimeUnit.YEAR)
        and req.mode in (RangeMode.FULL_PERIODS, RangeMode.TO_DATE)
        and req.quantity == int(req.quantity)
    )


def derive_comparison(
    window: TimeWindow,
    kind: ComparisonKind,
    *,
    custom: AbsoluteRange | None = None,
) -> Comparison:
    """Derive a comparison window deterministically from the primary window."""
    if kind is ComparisonKind.CUSTOM:
        if custom is None:
            raise ValueError("CUSTOM comparison requires an explicit range")
        cmp_range = custom
    elif kind is ComparisonKind.PRIOR_YEAR:
        cmp_range = AbsoluteRange(shift_years(window.range.start, -1), shift_years(window.range.end, -1))
    else:  # PRIOR_PERIOD
        if _is_calendar_aligned(window):
            req = window.requested
            assert req is not None
            months = _months_per_unit(req.unit) * int(req.quantity)
            if req.mode is RangeMode.TO_DATE:
                # like-for-like: period-to-date compares against prior
                # period-to-same-date, not the whole prior period
                cmp_range = AbsoluteRange(
                    shift_months(window.range.start, -months),
                    shift_months(window.range.end, -months),
                )
            else:
                cmp_range = AbsoluteRange(
                    shift_months(window.range.start, -months),
                    window.range.start - timedelta(days=1),
                )
        else:
            length = window.range.day_length
            cmp_range = AbsoluteRange(
                window.range.start - timedelta(days=length),
                window.range.start - timedelta(days=1),
            )
    return Comparison(
        kind=kind,
        window=TimeWindow(basis=window.basis, range=cmp_range, requested=None, calendar=window.calendar),
    )
