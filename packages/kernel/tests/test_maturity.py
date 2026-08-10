"""The shared terminal-bucket rule.

One verdict, in the kernel, because the findings evaluator and the chart
builder must reach the same conclusion about the same frame and may not
import each other. Regression, from when each owned its own half of the
rule: the prose named the week of 2026-07-20 PROVISIONAL and the SVG drew
an unbroken solid line terminating on it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.maturity import (
    CensoringKind,
    bucket_end,
    bucket_noun,
    median,
    terminal_bucket_verdict,
    time_bucket_column,
)
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _series(
    rows: tuple[tuple[object, ...], ...], *, bucket: str = "month", with_denominator: bool = True
) -> EvidenceFrame:
    columns = [
        FrameColumn("bucket", DimensionRef(f"time_bucket:{bucket}")),
        FrameColumn("denial_rate", MetricRef("denial_rate"), 2, "ratio"),
    ]
    if with_denominator:
        columns.append(FrameColumn("denial_rate__den", MetricRef("denial_rate"), 2, "count"))
    return EvidenceFrame(
        schema=FrameSchema(tuple(columns)),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestRightCensoring:
    def test_a_quarter_size_terminal_panel_is_censored(self) -> None:
        """The live series: 6,049 / 6,133 / 5,723 / 1,544."""
        frame = _series(
            (
                ("2026-01-01", Decimal("0.055"), 6049),
                ("2026-05-01", Decimal("0.054"), 6133),
                ("2026-06-01", Decimal("0.103"), 5723),
                ("2026-07-01", Decimal("0.128"), 1544),
            )
        )

        verdict = terminal_bucket_verdict(frame, bucket_column="bucket", measure="denial_rate")

        assert verdict is not None
        assert verdict.kind is CensoringKind.RIGHT_CENSORED
        # The RAW bucket key, so a chart row's x can be matched against it.
        assert verdict.bucket == "2026-07-01"
        assert verdict.noun == "month"
        assert verdict.population == 1544
        assert verdict.median_population == 6049

    def test_a_settled_series_declares_nothing(self) -> None:
        frame = _series(
            (
                ("2026-01-01", Decimal("0.055"), 6049),
                ("2026-02-01", Decimal("0.054"), 6133),
                ("2026-03-01", Decimal("0.103"), 5723),
                ("2026-04-01", Decimal("0.101"), 5900),
            )
        )
        assert terminal_bucket_verdict(frame, bucket_column="bucket", measure="denial_rate") is None

    def test_no_denominator_declares_nothing(self) -> None:
        """An unverifiable maturity claim is not a maturity finding."""
        frame = _series(
            (
                ("2026-01-01", Decimal("0.055")),
                ("2026-02-01", Decimal("0.054")),
                ("2026-03-01", Decimal("0.103")),
            ),
            with_denominator=False,
        )
        assert terminal_bucket_verdict(frame, bucket_column="bucket", measure="denial_rate") is None

    def test_a_series_of_two_is_not_a_series(self) -> None:
        frame = _series(
            (("2026-01-01", Decimal("0.055"), 6049), ("2026-02-01", Decimal("0.128"), 100))
        )
        assert terminal_bucket_verdict(frame, bucket_column="bucket", measure="denial_rate") is None


class TestCalendarPartial:
    def test_a_month_running_past_the_load_is_partial(self) -> None:
        frame = _series(
            (
                ("2026-06-01", Decimal("0.055"), 6049),
                ("2026-07-01", Decimal("0.054"), 6133),
                ("2026-08-01", Decimal("0.128"), 5723),  # two days of August
            )
        )

        verdict = terminal_bucket_verdict(frame, bucket_column="bucket", measure="denial_rate")

        assert verdict is not None
        assert verdict.kind is CensoringKind.CALENDAR_PARTIAL
        assert verdict.covered_through == date(2026, 8, 31)
        assert verdict.newest_data_date == date(2026, 8, 2)

    def test_calendar_partial_outranks_a_healthy_panel(self) -> None:
        """A full panel does not make a two-day month a month."""
        frame = _series(
            (
                ("2026-06-01", Decimal("0.055"), 6000),
                ("2026-07-01", Decimal("0.054"), 6000),
                ("2026-08-01", Decimal("0.128"), 6000),
            )
        )
        verdict = terminal_bucket_verdict(frame, bucket_column="bucket", measure="denial_rate")
        assert verdict is not None and verdict.kind is CensoringKind.CALENDAR_PARTIAL


class TestBucketArithmetic:
    def test_bucket_ends(self) -> None:
        assert bucket_end("2026-08-01", "month") == date(2026, 8, 31)
        assert bucket_end("2026-12-01", "month") == date(2026, 12, 31)
        assert bucket_end("2026-07-20", "week") == date(2026, 7, 26)
        assert bucket_end("2026-07-20", "day") == date(2026, 7, 20)
        assert bucket_end("2026-04-01", "quarter") == date(2026, 6, 30)
        assert bucket_end("2026-01-01", "year") == date(2026, 12, 31)
        assert bucket_end("not-a-date", "month") is None

    def test_median_is_the_middle(self) -> None:
        assert median([1, 2, 3]) == 2
        assert median([1, 2, 3, 4]) == 2

    def test_the_time_axis_is_read_off_the_ref(self) -> None:
        frame = _series((("2026-01-01", Decimal("0.055"), 10),), bucket="week")
        assert time_bucket_column(frame) == "bucket"
        assert bucket_noun(frame, "bucket") == "week"
