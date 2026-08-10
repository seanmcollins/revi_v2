"""One census of a frame, and disclosures that agree with the rows they print.

Two server strings stated different censuses of one eight-cell trend — six
bounded and none withheld in the prose, one withheld in the chart caption —
because the numerator survives on a nulled row. The SHAPE finding never asked
which of its points were ceilings, so a title read "7.5% -> 9.0% (up 1.5
points)" over two bounds. And the bounded-cell disclosure said "4 of 12
groups", listed five rows, named one payer twice (the second being the
comparison window's cell) and claimed "fewer than 11 things sit behind each"
over a row of 214 entities.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_investigation.application.execution import (
    BoundedCell,
    SuppressionCensus,
    bounded_cells_warning,
    suppression_census,
)
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
    primary_measure,
    published_measures,
    withheld_row_indices,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_presentation.charts import build_chart_spec

THRESHOLD = 11
WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _trend() -> EvidenceFrame:
    """The live Veritas shape: eight monthly cells, six bounded, one nulled
    outright, one measured. The numerator survives on the withheld row —
    which is exactly what made the two counters disagree."""
    schema = FrameSchema(
        columns=(
            FrameColumn(name="month", ref=DimensionRef("time_bucket:month")),
            FrameColumn(name="denial_rate", ref=MetricRef("denial_rate"), unit="ratio"),
            FrameColumn(name="denial_rate__num", ref=MetricRef("denial_rate__num"), unit="count"),
            FrameColumn(name="denial_rate__den", ref=MetricRef("denial_rate__den"), unit="count"),
        )
    )
    rows = [
        (date(2026, m, 1), 0.5, 3, 20) for m in range(1, 7)
    ]  # six bounded cells: 0 < num < 11 <= den
    rows.insert(4, (date(2026, 5, 1), 0.25, 40, 160))  # the one measured cell
    rows.append((date(2026, 8, 1), None, None, None))  # withheld outright
    return EvidenceFrame(
        schema=schema,
        rows=tuple(rows),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h"),
        evidence_grade=EvidenceGrade.DIRECT,
        # The frame does NOT admit a suppressed cell: the row is empty at
        # source. The old census required this to be non-zero before it
        # would call anything withheld, which is how the disagreement began.
        suppressed_cells=0,
    )


class TestOneCensus:
    def test_the_published_measure_is_the_measure_a_reader_counts(self) -> None:
        """Anatomy columns are not a second metric: counting a row as
        measured because its numerator survived is the bug itself."""
        frame = _trend()
        assert published_measures(frame) == ("denial_rate",)
        assert primary_measure(frame) == "denial_rate"

    def test_a_drawn_row_with_no_value_is_withheld_whatever_nulled_it(self) -> None:
        frame = _trend()
        assert withheld_row_indices(frame) == frozenset({len(frame.rows) - 1})

    def test_the_engines_census_and_the_charts_annotation_agree(self) -> None:
        frame = _trend()
        census = suppression_census(frame, THRESHOLD)
        spec = build_chart_spec("chart_main", frame, suppression_threshold=THRESHOLD)

        assert spec is not None
        assert census.total == 8
        assert census.bounded == 6
        assert census.withheld == 1
        assert census.measured == 1
        withheld_annotations = [a for a in spec.annotations if a.startswith("withheld:")]
        assert withheld_annotations == [
            f"withheld: {census.withheld} of {census.total} groups are too small to publish "
            "at all and are drawn with no value"
        ]

    def test_the_census_sentence_states_the_same_arithmetic(self) -> None:
        """The sentence a reader compares against the chart caption."""
        frame = _trend()
        census = suppression_census(frame, THRESHOLD)
        from revi_investigation.application.execution import bounded_cells_of

        warning = bounded_cells_warning(
            bounded_cells_of(frame, THRESHOLD), THRESHOLD, census=census
        )

        assert warning is not None
        # One count, in words, and the withheld row named as a second fact
        # rather than as a second census.
        assert "6 of 8 groups here are too small to measure exactly" in warning
        assert "A further 1 could not be published at all." in warning
        assert "cell(s)" not in warning


class TestATrendAdmitsItsCeilings:
    """A trend point that is a ceiling is marked as one, over the
    reference conversation's own shapes."""

    @pytest.mark.reference
    def test_the_helper_that_marks_a_ceiling_is_the_one_the_trend_uses(self) -> None:
        from decimal import Decimal

        from revi_investigation.application.execution import BoundedCell
        from revi_investigation.application.findings import _bound_values, bound_text

        bound = BoundedCell(
            label="2026-07", metric_id="denial_rate", population=13, bound=Decimal("0.769")
        )
        values = dict(_bound_values("denial_rate", bound))

        assert values["denial_rate__is_bound"] is True
        assert values["denial_rate__bound_population"] == 13
        assert bound_text(Decimal("0.769"), "ratio", bounded=True).startswith("≤")


# ---------------------------------------------------------------------------
# The bounded-cell disclosure agrees with itself
#
# regression: the sentence said "4 of 12 groups", printed five rows, named one
# payer twice (the second row being the comparison window's cell), and stated
# "fewer than 11 things sit behind each" over a row of 214 entities — beside a
# separate caution stating the real rule correctly.


class TestTheSuppressionDisclosureAgreesWithItself:
    CURRENT = (
        BoundedCell("Veritas Comp Fund", "denial_rate", 214, Decimal("0.0467"), 0),
        BoundedCell("Harborline Health Plan", "denial_rate", 48, Decimal("0.2083"), 1),
        BoundedCell("Cascade Select", "denial_rate", 53, Decimal("0.1887"), 2),
        BoundedCell("Northgate Choice", "denial_rate", 13, Decimal("0.7692"), 3),
    )
    #: The same payer, one window earlier: ≤9.0% over 111.
    PRIOR = (BoundedCell("Veritas Comp Fund", "denial_rate", 111, Decimal("0.0901"), 0),)

    def test_the_stated_count_is_the_length_of_the_list_it_prints(self) -> None:
        sentence = bounded_cells_warning(
            self.CURRENT,
            11,
            census=SuppressionCensus(total=12, bounded=4, withheld=0),
            comparison_cells=self.PRIOR,
        )
        assert sentence is not None
        assert "4 of 12 groups" in sentence
        # One row per named cell, and the count says four because four are
        # named. The live sentence said four and printed five.
        answer, _, _ = sentence.partition("In the comparison window")
        assert answer.count(" entities)") == 4

    def test_the_count_never_disagrees_with_the_list_even_when_the_census_does(self) -> None:
        """The census counted the widest frame; the list came from every
        probe the plan ran. Whichever is stale, the sentence stays true to
        the rows it prints."""
        sentence = bounded_cells_warning(
            self.CURRENT, 11, census=SuppressionCensus(total=2, bounded=9, withheld=0)
        )
        assert sentence is not None
        assert "4 of 4 groups" in sentence
        assert sentence.count(" entities)") == 4

    def test_no_cell_is_named_twice(self) -> None:
        sentence = bounded_cells_warning(
            (*self.CURRENT, *self.CURRENT),
            11,
            census=SuppressionCensus(total=12, bounded=4, withheld=0),
        )
        assert sentence is not None
        assert sentence.count("Veritas Comp Fund") == 1

    def test_the_comparison_window_gets_its_own_labelled_clause(self) -> None:
        sentence = bounded_cells_warning(
            self.CURRENT,
            11,
            census=SuppressionCensus(total=12, bounded=4, withheld=0),
            comparison_cells=self.PRIOR,
        )
        assert sentence is not None
        before, marker, after = sentence.partition("comparison window")
        assert marker, "the prior window's ceilings must be labelled as such"
        # …and the current window's list does not carry the prior cell.
        assert "111 entities" not in before
        assert "111 entities" in after

    def test_the_rule_stated_is_the_rule_applied(self) -> None:
        """A ceiling is a suppressed NUMERATOR over a published population.
        'Fewer than 11 things sit behind each of those numbers' printed over
        a row of 214 entities is refuted by the row beside it."""
        sentence = bounded_cells_warning(
            self.CURRENT, 11, census=SuppressionCensus(total=12, bounded=4, withheld=0)
        )
        assert sentence is not None
        assert "fewer than 11 things sit behind each" not in sentence
        assert "fewer than 11" in sentence
        assert "214 entities" in sentence

    def test_a_comparison_only_bound_still_gets_said(self) -> None:
        sentence = bounded_cells_warning(
            (), 11, census=SuppressionCensus(total=12, bounded=0, withheld=0),
            comparison_cells=self.PRIOR,
        )
        assert sentence is not None
        assert "comparison window" in sentence
        assert "0 of 12" not in sentence


class TestOneSetOfRowsGetsOneNoun:
    """The census sentence said "4 of 12 groups" and the ranking's own
    sentence said "4 of 12 payers" about the same four rows, on one card."""

    def test_the_disclosure_uses_the_analyst_s_word_for_its_rows(self) -> None:
        sentence = bounded_cells_warning(
            TestTheSuppressionDisclosureAgreesWithItself.CURRENT,
            11,
            census=SuppressionCensus(total=12, bounded=4, withheld=0),
            noun="payers",
        )
        assert sentence is not None
        assert "4 of 12 payers here are" in sentence

    def test_a_single_row_is_singular(self) -> None:
        sentence = bounded_cells_warning(
            TestTheSuppressionDisclosureAgreesWithItself.CURRENT[:1],
            11,
            census=SuppressionCensus(total=1, bounded=1, withheld=0),
            noun="payers",
        )
        assert sentence is not None
        assert "1 of 1 payer here is" in sentence or "1 of 1 payer here are" in sentence
