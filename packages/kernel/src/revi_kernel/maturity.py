"""Has the last point of a series settled? (design §6.4, round-3 R3-06.)

A time-bucketed frame's terminal bucket is the newest thing the analyst
asked for and the least trustworthy thing in the answer. Two independent
ways it can fail to be a measurement:

* **Calendar-partial** — the bucket extends past the newest data date, so
  it covers fewer days than every bucket before it. ``2026-08`` on a load
  ending ``2026-08-02`` is two days of a month.
* **Right-censored** — the bucket's own denominator is a fraction of the
  series median. The claims exist; they have not settled, and the ones that
  have are not a random sample of them.

The rule lives in the kernel because two capabilities must reach the same
verdict about the same frame and may not import each other (capability
independence, import-linter enforced): the findings evaluator names the
point PROVISIONAL in prose, and the chart builder must break the line at
the same bucket. Round-4 R4-03 is precisely what happens when they don't —
the finding title read "the week of 2026-07-20 point (66.7%) is
PROVISIONAL and is excluded from that movement" while the chart drew an
unbroken solid line terminating on that very point, because ``provisional``
was never true on the wire. One rule, one bucket, both surfaces.

This module states the *verdict*; the sentences that describe it belong to
whoever publishes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import DimensionRef

#: Prefix of a frame column whose dimension is a time axis
#: (``time_bucket:month``). The bucketing grain travels on the ref, so a
#: reader of the frame never has to guess whether a date column is a day.
TIME_BUCKET_PREFIX = "time_bucket:"

#: The adapter's ratio-denominator suffix (``denial_rate__den``) — the
#: per-bucket population a rate was computed over.
DENOMINATOR_SUFFIX = "__den"

#: How small a terminal bucket's own denominator may be, as a fraction of
#: the series median, before the point it produces is a data-maturity
#: artifact rather than a measurement.
#:
#: Live: adjudicated denominators 6,049 / 6,133 / 5,723 / 1,544 across
#: 2026-01..2026-07. The July point was computed on 25% of the median panel
#: — the fastest-adjudicating quarter of the month, which skews heavily to
#: denials — and published as "up 5.5 points", ``direct``, ``high``.
TERMINAL_BUCKET_MIN_SHARE = Decimal("0.6")

#: A series shorter than this has no shape to judge a terminal point
#: against, so nothing is claimed about it either way.
MIN_SERIES_POINTS = 3


class CensoringKind(StrEnum):
    """Why a terminal bucket is not yet a measurement."""

    #: The bucket's period runs past the newest data date.
    CALENDAR_PARTIAL = "calendar_partial"
    #: The bucket's population is a fraction of the series median.
    RIGHT_CENSORED = "right_censored"


@dataclass(frozen=True, slots=True)
class TerminalBucketVerdict:
    """The terminal bucket of a series, and why it cannot close it."""

    kind: CensoringKind
    #: The bucket key exactly as the frame holds it — what a chart row's
    #: ``x`` is built from, so the mark and the sentence agree.
    bucket: Scalar
    #: "month" / "week" / "day", read off the frame's own time-axis ref.
    noun: str
    #: The newest data date the frame was computed as of.
    newest_data_date: date
    #: Calendar-partial only: the last calendar day the bucket covers.
    covered_through: date | None = None
    #: Right-censored only: the bucket's own population and the series
    #: median it is measured against.
    population: int | None = None
    median_population: int | None = None


def time_bucket_column(frame: EvidenceFrame) -> str | None:
    """The frame's time axis, when it has one."""
    for col in frame.schema.columns:
        if isinstance(col.ref, DimensionRef) and col.ref.id.startswith(TIME_BUCKET_PREFIX):
            return col.name
    return None


def bucket_noun(frame: EvidenceFrame, column: str) -> str:
    """"month" / "week" / "day", read off the frame's own time-axis ref."""
    for col in frame.schema.columns:
        if col.name == column and isinstance(col.ref, DimensionRef):
            return col.ref.id.removeprefix(TIME_BUCKET_PREFIX)
    return "period"


def bucket_end(bucket: Scalar, noun: str) -> date | None:
    """The last calendar day the bucket covers, when it can be derived."""
    text = str(bucket)
    try:
        start = date.fromisoformat(text[:10]) if len(text) >= 10 else None
        if start is None and noun == "month" and len(text) >= 7:
            start = date(int(text[:4]), int(text[5:7]), 1)
    except ValueError:
        return None
    if start is None:
        return None
    if noun == "day":
        return start
    if noun == "week":
        return start + timedelta(days=6)
    if noun == "month":
        return date(start.year + start.month // 12, start.month % 12 + 1, 1) - timedelta(days=1)
    if noun == "quarter":
        end_month = ((start.month - 1) // 3 + 1) * 3
        return date(start.year + end_month // 12, end_month % 12 + 1, 1) - timedelta(days=1)
    if noun == "year":
        return date(start.year, 12, 31)
    return None


def median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _as_int(value: Scalar) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _is_number(value: Scalar) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, Decimal))


def terminal_bucket_verdict(
    frame: EvidenceFrame,
    *,
    bucket_column: str,
    measure: str,
    min_share: Decimal = TERMINAL_BUCKET_MIN_SHARE,
) -> TerminalBucketVerdict | None:
    """Is this series' last point a measurement, or an artifact of maturity?

    ``None`` means "nothing to declare": too short a series, no derivable
    calendar period, or no denominator to read a population off. An
    unverifiable maturity claim is not a maturity finding — the same rule
    the premise check follows.
    """
    schema = frame.schema
    if bucket_column not in schema.names or measure not in schema.names:
        return None
    if len(frame.rows) < MIN_SERIES_POINTS:
        return None
    idx_bucket = schema.index_of(bucket_column)
    idx_value = schema.index_of(measure)
    rows = sorted(
        (row for row in frame.rows if _is_number(row[idx_value])),
        key=lambda row: str(row[idx_bucket]),
    )
    if len(rows) < MIN_SERIES_POINTS:
        return None
    noun = bucket_noun(frame, bucket_column)
    terminal = rows[-1]
    newest = frame.watermark.newest_data_date

    covered = bucket_end(terminal[idx_bucket], noun)
    if covered is not None and covered > newest:
        return TerminalBucketVerdict(
            kind=CensoringKind.CALENDAR_PARTIAL,
            bucket=terminal[idx_bucket],
            noun=noun,
            newest_data_date=newest,
            covered_through=covered,
        )

    denominator = f"{measure}{DENOMINATOR_SUFFIX}"
    if denominator not in schema.names:
        return None
    idx_den = schema.index_of(denominator)
    populations = [
        value for row in rows if (value := _as_int(row[idx_den])) is not None and value > 0
    ]
    if len(populations) < MIN_SERIES_POINTS:
        return None
    terminal_population = _as_int(terminal[idx_den])
    if terminal_population is None or terminal_population <= 0:
        return None
    series_median = median(populations[:-1])
    if series_median <= 0 or Decimal(terminal_population) >= min_share * Decimal(series_median):
        return None
    return TerminalBucketVerdict(
        kind=CensoringKind.RIGHT_CENSORED,
        bucket=terminal[idx_bucket],
        noun=noun,
        newest_data_date=newest,
        population=terminal_population,
        median_population=series_median,
    )
