"""The scorecard assembly: N per-entity measurements onto one row each.

``panel`` is the operator the payer scorecard and the generic
dimension scorecard answer WITH. Before it existed, "what is my top
performing payer?" — the single most natural question an executive asks —
routed correctly to ``payer_scorecard``, ran six probe families, got
direct-grade rows out of every one, and fell into the refusal machinery
because the step that turns those rows into a card had never been built.

What this file pins is the arithmetic of that step, and one rule above it:
an ordering exists only where the metric contract says which end is good.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_calculation.operators import panel
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _frame(
    measures: dict[str, str],
    rows: tuple[tuple[object, ...], ...],
    *,
    dimension: str = "payer",
    grade: EvidenceGrade = EvidenceGrade.DIRECT,
) -> EvidenceFrame:
    columns = (
        FrameColumn(dimension, DimensionRef(dimension), None, None),
        *(FrameColumn(name, MetricRef(name), 1, unit) for name, unit in measures.items()),
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="p" * 64),
        evidence_grade=grade,
    )


class TestTheColumnsComeFromEveryCheckThatMeasuredTheEntity:
    def test_measures_from_separate_frames_land_on_one_row(self) -> None:
        cash = _frame({"cash_posted": "money_cents"}, (("Atlas", 500), ("Northbridge", 900)))
        rate = _frame(
            {"denial_rate": "ratio"},
            (("Atlas", Decimal("0.10")), ("Northbridge", Decimal("0.04"))),
        )

        out = panel(cash, rate, entity="payer")

        assert out.schema.names[:3] == ("payer", "cash_posted", "denial_rate")
        assert out.rows == (("Atlas", 500, Decimal("0.10")), ("Northbridge", 900, Decimal("0.04")))

    def test_an_entity_only_one_check_saw_keeps_a_row_and_gets_NULL(self) -> None:
        """NULL is a different fact from zero and is published as one: the
        payer simply was not in that measurement's population."""
        cash = _frame({"cash_posted": "money_cents"}, (("Atlas", 500), ("Veritas", 40)))
        rate = _frame({"denial_rate": "ratio"}, (("Atlas", Decimal("0.10")),))

        out = panel(cash, rate, entity="payer")

        assert out.rows == (("Atlas", 500, Decimal("0.10")), ("Veritas", 40, None))

    def test_the_ratio_anatomy_travels_so_a_bound_stays_recognisable(self) -> None:
        """``__num``/``__den`` are what let a downstream reader ask whether
        a cell is a measurement or a ceiling. Dropping them here makes every
        bound on a scorecard indistinguishable from a measured rate."""
        rate = _frame(
            {"denial_rate__num": "count", "denial_rate__den": "count", "denial_rate": "ratio"},
            (("Atlas", 3, 200, Decimal("0.015")),),
        )

        out = panel(rate, entity="payer")

        assert "denial_rate__num" in out.schema.names
        assert "denial_rate__den" in out.schema.names

    def test_a_finer_cut_is_refused_rather_than_folded_onto_the_row(self) -> None:
        """A payer-by-service-line cell is a slice of a payer. Joining one
        onto a payer row publishes the slice as the whole."""
        mix = EvidenceFrame(
            schema=FrameSchema(
                (
                    FrameColumn("payer", DimensionRef("payer"), None, None),
                    FrameColumn("service_line", DimensionRef("service_line"), None, None),
                    FrameColumn("charges", MetricRef("charges"), 1, "money_cents"),
                )
            ),
            rows=(("Atlas", "Imaging", 100),),
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="p", probe_hash="p" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )

        with pytest.raises(ValueError, match="a panel row is one 'payer'"):
            panel(mix, entity="payer")

    def test_two_checks_measuring_one_thing_is_an_error_not_a_silent_pick(self) -> None:
        first = _frame({"charges": "money_cents"}, (("Atlas", 100),))
        second = _frame({"charges": "money_cents"}, (("Atlas", 200),))

        with pytest.raises(ValueError, match="produced by two"):
            panel(first, second, entity="payer")


class TestOneOrderingPerMeasureInTheDirectionItsContractDeclares:
    def test_higher_is_good_ranks_the_highest_first(self) -> None:
        cash = _frame(
            {"cash_posted": "money_cents"},
            (("Atlas", 500), ("Northbridge", 900), ("Veritas", 40)),
        )

        out = panel(cash, entity="payer", better_high=("cash_posted",))

        ranks = dict(zip(out.column("payer"), out.column("cash_posted__rank"), strict=True))
        assert ranks == {"Northbridge": 1, "Atlas": 2, "Veritas": 3}

    def test_higher_is_bad_ranks_the_lowest_first(self) -> None:
        """First on the denial rate is the LOWEST. A single ordering with no
        direction would put the worst payer at the head of the list and call
        it the best."""
        rate = _frame(
            {"denial_rate": "ratio"},
            (
                ("Atlas", Decimal("0.10")),
                ("Northbridge", Decimal("0.04")),
                ("Veritas", Decimal("0.22")),
            ),
        )

        out = panel(rate, entity="payer", better_low=("denial_rate",))

        ranks = dict(zip(out.column("payer"), out.column("denial_rate__rank"), strict=True))
        assert ranks == {"Northbridge": 1, "Atlas": 2, "Veritas": 3}

    def test_a_neutral_measure_gets_no_ordering_at_all(self) -> None:
        """Nobody leads on charges. A rank over a neutral measure asserts
        that billing more is better, which is not a fact this pack holds."""
        charges = _frame({"charges": "money_cents"}, (("Atlas", 500), ("Northbridge", 900)))

        out = panel(charges, entity="payer")

        assert "charges__rank" not in out.schema.names

    def test_a_null_cell_takes_no_position(self) -> None:
        cash = _frame({"cash_posted": "money_cents"}, (("Atlas", 500), ("Veritas", None)))

        out = panel(cash, entity="payer", better_high=("cash_posted",))

        assert out.column("cash_posted__rank") == (1, None)

    def test_ties_break_on_the_label_the_same_way_in_both_directions(self) -> None:
        """Otherwise the alphabetical tie-break flips with the measure's
        polarity and two runs of one dataset disagree about who is second."""
        rows = (("Atlas", 500), ("Northbridge", 500))
        high = panel(
            _frame({"m": "count"}, rows), entity="payer", better_high=("m",)
        ).column("m__rank")
        low = panel(_frame({"m": "count"}, rows), entity="payer", better_low=("m",)).column(
            "m__rank"
        )

        assert high == (1, 2)
        assert low == (1, 2)

    def test_a_measure_cannot_improve_in_both_directions(self) -> None:
        with pytest.raises(ValueError, match="one improvement direction or none"):
            panel(
                _frame({"m": "count"}, (("Atlas", 1),)),
                entity="payer",
                better_high=("m",),
                better_low=("m",),
            )


class TestTheGradeLawSurvivesTheJoin:
    def test_the_panel_grades_at_its_weakest_input(self) -> None:
        """Quality is the worst input, never an average — a scorecard whose
        A/R column is estimated is an estimated scorecard."""
        direct = _frame({"cash_posted": "money_cents"}, (("Atlas", 500),))
        proxy = _frame(
            {"denial_rate": "ratio"},
            (("Atlas", Decimal("0.1")),),
            grade=EvidenceGrade.PROXY,
        )

        out = panel(direct, proxy, entity="payer")

        assert out.evidence_grade is EvidenceGrade.PROXY

    def test_the_operator_is_recorded_on_the_output_provenance(self) -> None:
        out = panel(_frame({"m": "count"}, (("Atlas", 1),)), entity="payer")

        assert out.provenance.operator == "panel"
