"""What a published chart must guarantee: addressable rows, ordinal axes.

Two regressions are pinned here. A spec declared ``x`` and ``series`` over rows
indistinguishable under them, so any renderer keying on the declared axes kept
the last write and silently dropped the rest — under a CAVEATS block that said
nothing about it. And an ordinal dimension (``ar_age_bucket``) came off the API
alphabetically, because the catalog's declared order stopped at the findings
path.
"""

from __future__ import annotations

from datetime import date, datetime

from revi_api.chart_integrity import apply_axis_order, enforce_row_keys
from revi_investigation_contracts.api import ChartRow, ChartSpec
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _frame(dimensions: tuple[str, ...], rows: tuple[tuple[object, ...], ...],
           unit: str = "money_cents") -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                *(FrameColumn(d, DimensionRef(d)) for d in dimensions),
                FrameColumn("denied_dollars", MetricRef("denied_dollars"), 2, unit),
            )
        ),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _spec(x: str, series: str | None, rows: list[ChartRow], unit: str = "money_cents") -> ChartSpec:
    return ChartSpec(
        id="chart_main",
        chart_type="bar",
        title="t",
        frame_id="main",
        x=x,
        series=series,
        value="denied_dollars",
        unit=unit,
        rows=rows,
    )


class TestEveryRowIsAddressableByTheDeclaredAxes:
    def test_a_chart_that_already_keys_uniquely_is_untouched(self) -> None:
        frame = _frame(("payer",), (("Atlas", 100), ("State", 200)))
        spec = _spec("payer", None, [ChartRow(x="Atlas", value=100),
                                     ChartRow(x="State", value=200)])
        repaired, warning = enforce_row_keys(spec, frame)
        assert warning is None
        assert repaired is spec

    def test_an_undeclared_grouping_column_becomes_part_of_the_key(self) -> None:
        """A frame cut three ways, a spec declaring two. The third column is
        published as part of ``series`` rather than left to collapse a chart's
        rows by last-write-wins."""
        frame = _frame(
            ("payer", "service_line", "group_code"),
            (
                ("Atlas", "Imaging", "CO", 100),
                ("Atlas", "Imaging", "PI", 200),
                ("Atlas", "Surgery", "CO", 300),
            ),
        )
        spec = _spec(
            "payer",
            "service_line",
            [
                ChartRow(x="Atlas", series="Imaging", value=100),
                ChartRow(x="Atlas", series="Imaging", value=200),
                ChartRow(x="Atlas", series="Surgery", value=300),
            ],
        )
        repaired, warning = enforce_row_keys(spec, frame)
        assert repaired is not None
        assert warning is None
        assert repaired.series == "service_line / group_code"
        keys = {(r.x, r.series) for r in repaired.rows}
        assert len(keys) == len(repaired.rows) == 3
        # every cent still on the wire
        assert sum(r.value or 0 for r in repaired.rows) == 600

    def test_an_unkeyable_additive_chart_is_summed_and_says_so(self) -> None:
        """No column explains the repeats — the frame is finer than any of
        its dimensions. Summing is the honest cell; keeping the last one
        understates the total by orders of magnitude."""
        frame = _frame(("payer",), (("Atlas", 100), ("Atlas", 200)))
        spec = _spec("payer", None, [ChartRow(x="Atlas", value=100),
                                     ChartRow(x="Atlas", value=200)])
        repaired, warning = enforce_row_keys(spec, frame)
        assert repaired is not None
        assert len(repaired.rows) == 1
        assert repaired.rows[0].value == 300
        assert warning is not None and warning.startswith("chart_rows_collapsed:")
        assert any("folded" in a for a in repaired.annotations)

    def test_an_unkeyable_rate_chart_is_refused_with_its_reason(self) -> None:
        """Two rates under one key cannot be added, and a last-write-wins
        rate is a measurement nobody made."""
        frame = _frame(("payer",), (("Atlas", 100), ("Atlas", 200)), unit="ratio")
        spec = _spec("payer", None, [ChartRow(x="Atlas", value=0.1),
                                     ChartRow(x="Atlas", value=0.2)], unit="ratio")
        repaired, warning = enforce_row_keys(spec, frame)
        assert repaired is None
        assert warning is not None
        assert "was not published" in warning
        assert "ratio" in warning

    def test_a_colliding_row_never_inherits_the_last_drill_handle(self) -> None:
        frame = _frame(("payer",), (("Atlas", 100), ("Atlas", 200)))
        spec = _spec(
            "payer",
            None,
            [
                ChartRow(x="Atlas", value=100, referent_id="F1"),
                ChartRow(x="Atlas", value=200, referent_id="F2"),
            ],
        )
        repaired, _ = enforce_row_keys(spec, frame)
        assert repaired is not None
        assert repaired.rows[0].referent_id is None


class TestAnOrdinalAxisComesOffTheWireInOrder:
    ORDER = ("0-30", "31-60", "61-90", "91-120", "120+")

    def test_the_declared_order_is_published_and_applied(self) -> None:
        frame = _frame(("ar_age_bucket",), tuple((b, 1) for b in ("120+", "0-30", "61-90")))
        spec = _spec(
            "ar_age_bucket",
            None,
            [ChartRow(x="120+", value=1), ChartRow(x="0-30", value=1), ChartRow(x="61-90", value=1)],
        )
        ordered = apply_axis_order(spec, frame, {"ar_age_bucket": self.ORDER})
        assert ordered.axis_order == list(self.ORDER)
        assert [r.x for r in ordered.rows] == ["0-30", "61-90", "120+"]

    def test_a_value_sort_does_not_survive_an_ordinal_axis(self) -> None:
        """An aging chart ordered by dollars is not an aging chart. The plan's
        ordering ranks the findings; the axis keeps its own order."""
        frame = _frame(("ar_age_bucket",), (("120+", 1), ("0-30", 9)))
        spec = _spec("ar_age_bucket", None,
                     [ChartRow(x="120+", value=1), ChartRow(x="0-30", value=9)])
        spec = spec.model_copy(
            update={"sort": {"by": "denied_dollars", "direction": "desc"}}
        )
        ordered = apply_axis_order(spec, frame, {"ar_age_bucket": self.ORDER})
        assert ordered.sort is None
        assert [r.x for r in ordered.rows] == ["0-30", "120+"]

    def test_a_dimension_with_no_declared_order_is_left_alone(self) -> None:
        frame = _frame(("payer",), (("State", 1), ("Atlas", 2)))
        spec = _spec("payer", None, [ChartRow(x="State", value=1), ChartRow(x="Atlas", value=2)])
        ordered = apply_axis_order(spec, frame, {"ar_age_bucket": self.ORDER})
        assert ordered.axis_order is None
        assert [r.x for r in ordered.rows] == ["State", "Atlas"]

    def test_an_undeclared_bucket_value_keeps_its_place_at_the_end(self) -> None:
        frame = _frame(("ar_age_bucket",), (("unknown", 1), ("31-60", 2)))
        spec = _spec("ar_age_bucket", None,
                     [ChartRow(x="unknown", value=1), ChartRow(x="31-60", value=2)])
        ordered = apply_axis_order(spec, frame, {"ar_age_bucket": self.ORDER})
        assert [r.x for r in ordered.rows] == ["31-60", "unknown"]
