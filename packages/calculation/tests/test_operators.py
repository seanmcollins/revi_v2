"""Kernel-law property tests for the transform operators.

Laws under test (design §5.3, §7.8, operator-algebra v0):
- slicing law: ratio computes ratio-of-sums per cell (the average-of-ratios
  trap fixture fails if anyone "simplifies");
- grade law: outputs carry the weakest input grade;
- shares sum to 1 modulo suppression;
- compare antisymmetry;
- decompose exact additivity + symmetry, cents-exact;
- reconcile passes/fails correctly with zero and suppressed tolerance.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revi_calculation.operators import (
    ReconciliationStatus,
    compare,
    decompose,
    pivot,
    project_lagged_realization,
    rank,
    ratio,
    reconcile,
    share_of_total,
    top_k,
)
from revi_kernel.errors import InsufficientEvidenceError
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark

WM = DataWatermark("wm_003", datetime(2026, 8, 3, 4, 10), date(2026, 8, 2))
PAYER = DimensionRef("payer")


def make_frame(
    columns: list[tuple[str, object, str | None]],
    rows: list[tuple[object, ...]],
    *,
    grade: EvidenceGrade = EvidenceGrade.DIRECT,
    truncated: bool = False,
    suppressed: int = 0,
) -> EvidenceFrame:
    schema = FrameSchema(
        tuple(FrameColumn(name, ref, None, unit) for name, ref, unit in columns)  # type: ignore[arg-type]
    )
    return EvidenceFrame(
        schema=schema,
        rows=tuple(rows),  # type: ignore[arg-type]
        watermark=WM,
        provenance=ProbeProvenance("p", "h"),
        evidence_grade=grade,
        truncated=truncated,
        suppressed_cells=suppressed,
    )


def denial_components(rows: list[tuple[str, int, int]]) -> EvidenceFrame:
    return make_frame(
        [
            ("payer", PAYER, None),
            ("denial_rate__num", MetricRef("denial_rate"), "count"),
            ("denial_rate__den", MetricRef("denial_rate"), "count"),
        ],
        list(rows),
    )


class TestRatioSlicingLaw:
    def test_ratio_of_sums_not_average_of_ratios(self) -> None:
        """The classic trap: two cells 1/10 and 90/90 → overall must be
        91/100 = 0.91, never (0.1 + 1.0)/2 = 0.55."""
        per_cell = denial_components([("A", 1, 10), ("B", 90, 90)])
        total = denial_components([("all", 91, 100)])
        cell_frame = ratio(
            per_cell, numerator="denial_rate__num", denominator="denial_rate__den",
            out="denial_rate", out_ref=MetricRef("denial_rate"),
        )
        total_frame = ratio(
            total, numerator="denial_rate__num", denominator="denial_rate__den",
            out="denial_rate", out_ref=MetricRef("denial_rate"),
        )
        assert total_frame.column("denial_rate")[0] == Decimal("0.910000")
        avg_of_ratios = sum(cell_frame.column("denial_rate")) / 2  # type: ignore[arg-type]
        assert avg_of_ratios != total_frame.column("denial_rate")[0]

    def test_zero_denominator_is_null(self) -> None:
        f = ratio(
            denial_components([("A", 5, 0)]),
            numerator="denial_rate__num", denominator="denial_rate__den",
            out="denial_rate", out_ref=MetricRef("denial_rate"),
        )
        assert f.column("denial_rate") == (None,)

    def test_grade_law(self) -> None:
        f = make_frame(
            [("payer", PAYER, None), ("m__num", MetricRef("m"), "count"),
             ("m__den", MetricRef("m"), "count")],
            [("A", 1, 2)],
            grade=EvidenceGrade.PROXY,
        )
        out = ratio(f, numerator="m__num", denominator="m__den", out="m", out_ref=MetricRef("m"))
        assert out.evidence_grade is EvidenceGrade.PROXY


def cash_frame(rows: list[tuple[str, int]], **kw: object) -> EvidenceFrame:
    return make_frame(
        [("payer", PAYER, None), ("cash", MetricRef("cash_posted"), "money_cents")],
        list(rows),
        **kw,  # type: ignore[arg-type]
    )


class TestCompare:
    def test_delta_and_pct(self) -> None:
        cur = cash_frame([("Atlas", 80_000_00), ("Meridian", 50_000_00)])
        pri = cash_frame([("Atlas", 100_000_00), ("Meridian", 40_000_00)])
        out = compare(cur, pri)
        assert out.column("cash__delta") == (-20_000_00, 10_000_00)
        assert out.column("cash__pct_change") == (Decimal("-0.200000"), Decimal("0.250000"))

    def test_missing_cell_zero_fill_for_money(self) -> None:
        cur = cash_frame([("Atlas", 5_000_00)])
        pri = cash_frame([("Atlas", 4_000_00), ("Gone", 3_000_00)])
        out = compare(cur, pri)
        by_payer = {r[0]: r for r in out.rows}
        assert by_payer["Gone"][out.schema.index_of("cash")] == 0
        assert by_payer["Gone"][out.schema.index_of("cash__delta")] == -3_000_00

    @given(
        st.lists(
            st.tuples(st.sampled_from(["A", "B", "C", "D"]), st.integers(-(10**9), 10**9)),
            min_size=1, max_size=4, unique_by=lambda t: t[0],
        ),
        st.lists(
            st.tuples(st.sampled_from(["A", "B", "C", "D"]), st.integers(-(10**9), 10**9)),
            min_size=1, max_size=4, unique_by=lambda t: t[0],
        ),
    )
    def test_antisymmetry(self, a: list[tuple[str, int]], b: list[tuple[str, int]]) -> None:
        ab = compare(cash_frame(a), cash_frame(b))
        ba = compare(cash_frame(b), cash_frame(a))
        deltas_ab = {r[0]: r[ab.schema.index_of("cash__delta")] for r in ab.rows}
        deltas_ba = {r[0]: r[ba.schema.index_of("cash__delta")] for r in ba.rows}
        assert set(deltas_ab) == set(deltas_ba)
        for k, v in deltas_ab.items():
            assert deltas_ba[k] == -v  # type: ignore[operator]

    def test_cross_watermark_rejected(self) -> None:
        other = EvidenceFrame(
            schema=cash_frame([("A", 1)]).schema,
            rows=(("A", 1),),
            watermark=DataWatermark("wm_002", datetime(2026, 8, 2, 4, 12), date(2026, 8, 1)),
            provenance=ProbeProvenance("p", "h"),
            evidence_grade=EvidenceGrade.DIRECT,
        )
        with pytest.raises(ValueError, match="watermark"):
            compare(cash_frame([("A", 2)]), other)


class TestShareOfTotal:
    @given(
        st.lists(
            st.tuples(st.text(alphabet="abcdef", min_size=1, max_size=3), st.integers(1, 10**9)),
            min_size=1, max_size=8, unique_by=lambda t: t[0],
        )
    )
    def test_shares_sum_to_one_without_suppression(self, rows: list[tuple[str, int]]) -> None:
        out = share_of_total(cash_frame(rows), measure="cash")
        total = sum(out.column("cash__share"))  # type: ignore[arg-type]
        assert abs(total - 1) <= Decimal("0.000001") * len(rows)

    def test_suppressed_shares_sum_below_one_is_honest(self) -> None:
        f = cash_frame([("A", 60), ("B", 40)], suppressed=2)
        out = share_of_total(f, measure="cash")
        assert out.suppressed_cells == 2  # travels with the frame


class TestTopKRank:
    def test_top_k_truncates_and_flags(self) -> None:
        f = cash_frame([("A", 10), ("B", 30), ("C", 20)])
        out = top_k(f, by="cash", k=2)
        assert [r[0] for r in out.rows] == ["B", "C"]
        assert out.truncated is True

    def test_top_k_within_size_not_truncated(self) -> None:
        out = top_k(cash_frame([("A", 10)]), by="cash", k=5)
        assert out.truncated is False

    def test_rank_deterministic_ties(self) -> None:
        f = cash_frame([("B", 10), ("A", 10), ("C", 30)])
        out = rank(f, by="cash")
        by_payer = {r[0]: r[out.schema.index_of("cash__rank")] for r in out.rows}
        assert by_payer["C"] == 1
        assert by_payer["A"] == 2  # tie broken by dimension value
        assert by_payer["B"] == 3


class TestPivot:
    def test_pivot_shape(self) -> None:
        f = make_frame(
            [("payer", PAYER, None), ("week", DimensionRef("week"), None),
             ("cash", MetricRef("cash_posted"), "money_cents")],
            [("Atlas", "W1", 10), ("Atlas", "W2", 20), ("Meridian", "W1", 5)],
        )
        out = pivot(f, index=("payer",), column="week", measure="cash")
        assert out.schema.names == ("payer", "cash[W1]", "cash[W2]")
        by_payer = {r[0]: r for r in out.rows}
        assert by_payer["Meridian"][2] is None


class TestReconcile:
    def test_exact_pass(self) -> None:
        parent = cash_frame([("all", 100)])
        children = cash_frame([("A", 60), ("B", 40)])
        result = reconcile(parent, children, measures=("cash",))
        assert result.status is ReconciliationStatus.PASSED

    def test_fail_flagged(self) -> None:
        result = reconcile(
            cash_frame([("all", 100)]), cash_frame([("A", 60), ("B", 39)]), measures=("cash",)
        )
        assert result.status is ReconciliationStatus.FAILED
        assert result.measures[0].difference == Decimal(-1)

    def test_suppression_tolerance(self) -> None:
        children = cash_frame([("A", 60), ("B", 30)], suppressed=1)
        result = reconcile(
            cash_frame([("all", 100)]), children,
            measures=("cash",), suppression_allowance=Decimal(15),
        )
        assert result.status is ReconciliationStatus.PASSED_WITH_SUPPRESSION

    def test_zero_tolerance_without_suppression(self) -> None:
        result = reconcile(
            cash_frame([("all", 100)]), cash_frame([("A", 60), ("B", 39)]),
            measures=("cash",), suppression_allowance=Decimal(15),
        )
        assert result.status is ReconciliationStatus.FAILED


def volume_value_frame(rows: list[tuple[str, int, int]], **kw: object) -> EvidenceFrame:
    return make_frame(
        [("payer", PAYER, None), ("claims", MetricRef("claim_volume"), "count"),
         ("cash", MetricRef("cash_posted"), "money_cents")],
        list(rows),
        **kw,  # type: ignore[arg-type]
    )


payer_cells = st.lists(
    st.tuples(
        st.sampled_from(["A", "B", "C", "D", "E"]),
        st.integers(0, 5000),
        st.integers(0, 10**9),
    ),
    min_size=1, max_size=5, unique_by=lambda t: t[0],
)


class TestDecompose:
    @given(payer_cells, payer_cells)
    def test_exact_additivity_in_cents(
        self, cur: list[tuple[str, int, int]], pri: list[tuple[str, int, int]]
    ) -> None:
        out = decompose(volume_value_frame(cur), volume_value_frame(pri), volume="claims", value="cash")
        contrib_idx = out.schema.index_of("contribution")
        total_contribution = sum(r[contrib_idx] for r in out.rows)  # type: ignore[misc]
        total_delta = sum(v for _, _, v in cur) - sum(v for _, _, v in pri)
        assert total_contribution == total_delta

    @given(payer_cells, payer_cells)
    def test_symmetry(self, cur: list[tuple[str, int, int]], pri: list[tuple[str, int, int]]) -> None:
        fwd = decompose(volume_value_frame(cur), volume_value_frame(pri), volume="claims", value="cash")
        rev = decompose(volume_value_frame(pri), volume_value_frame(cur), volume="claims", value="cash")

        def total_by_cell(frame: EvidenceFrame) -> dict[str, int]:
            d_idx = frame.schema.index_of("delta_total")
            return {str(r[0]): int(r[d_idx]) for r in frame.rows}  # type: ignore[arg-type]

        fwd_deltas, rev_deltas = total_by_cell(fwd), total_by_cell(rev)
        assert set(fwd_deltas) == set(rev_deltas)
        for k in fwd_deltas:
            assert fwd_deltas[k] == -rev_deltas[k]

    def test_pure_rate_change(self) -> None:
        """Same volume, higher rate → everything attributed to rate."""
        out = decompose(
            volume_value_frame([("A", 100, 120_000)]),
            volume_value_frame([("A", 100, 100_000)]),
            volume="claims", value="cash",
        )
        by_component = {r[1]: r[2] for r in out.rows}
        assert by_component["rate"] == 20_000
        assert by_component["volume_scale"] == 0
        assert by_component["volume_mix"] == 0

    def test_grade_law(self) -> None:
        out = decompose(
            volume_value_frame([("A", 1, 1)], grade=EvidenceGrade.PROXY),
            volume_value_frame([("A", 1, 1)]),
            volume="claims", value="cash",
        )
        assert out.evidence_grade is EvidenceGrade.PROXY


def _curves(rows: list[tuple[str, str, str, str, str]]) -> EvidenceFrame:
    return make_frame(
        [("payer", PAYER, None), ("age_bucket", DimensionRef("age_bucket"), None),
         ("realize_frac", MetricRef("realize_frac"), "ratio"),
         ("realize_frac_low", MetricRef("realize_frac_low"), "ratio"),
         ("realize_frac_high", MetricRef("realize_frac_high"), "ratio")],
        [(p, b, Decimal(f), Decimal(lo), Decimal(hi)) for p, b, f, lo, hi in rows],
    )


def _inventory(rows: list[tuple[str, str, int]]) -> EvidenceFrame:
    return make_frame(
        [("payer", PAYER, None), ("age_bucket", DimensionRef("age_bucket"), None),
         ("expected_open_cents", MetricRef("expected_open"), "money_cents")],
        list(rows),
    )


def _inflow(rows: list[tuple[str, int, str, str, str]]) -> EvidenceFrame:
    return make_frame(
        [("payer", PAYER, None), ("weekly_expected_cents", MetricRef("weekly_expected"), "money_cents"),
         ("new_realize_frac", MetricRef("nrf"), "ratio"),
         ("new_realize_frac_low", MetricRef("nrfl"), "ratio"),
         ("new_realize_frac_high", MetricRef("nrfh"), "ratio")],
        [(p, w, Decimal(f), Decimal(lo), Decimal(hi)) for p, w, f, lo, hi in rows],
    )


def _baseline(rows: list[tuple[str, int]]) -> EvidenceFrame:
    return make_frame(
        [("payer", PAYER, None), ("baseline_cash_cents", MetricRef("baseline_cash"), "money_cents")],
        list(rows),
    )


class TestProjection:
    def test_basic_projection_and_grade(self) -> None:
        out = project_lagged_realization(
            _inventory([("Atlas", "0-30", 100_000)]),
            _curves([("Atlas", "0-30", "0.6", "0.5", "0.7")]),
            _inflow([("Atlas", 10_000, "0.4", "0.3", "0.5")]),
            _baseline([("Atlas", 70_000)]),
            horizon_weeks=4,
        )
        by_payer = {r[0]: r for r in out.rows}
        atlas = by_payer["Atlas"]
        # inflight 100k*0.6=60k; inflow 10k*4*0.4=16k
        assert atlas[out.schema.index_of("driver_inflight_cents")] == 60_000
        assert atlas[out.schema.index_of("driver_assumed_inflow_cents")] == 16_000
        assert atlas[out.schema.index_of("projected_cash_cents")] == 76_000
        assert by_payer["__total__"][out.schema.index_of("projected_cash_cents")] == 76_000
        assert out.evidence_grade is EvidenceGrade.DERIVED  # never DIRECT

    def test_insufficient_coverage_refuses(self) -> None:
        with pytest.raises(InsufficientEvidenceError) as exc:
            project_lagged_realization(
                _inventory([("Atlas", "0-30", 50_000), ("Atlas", "31-60", 50_000)]),
                _curves([("Atlas", "0-30", "0.6", "0.5", "0.7")]),  # 50% coverage < 80%
                _inflow([("Atlas", 0, "0", "0", "0")]),
                _baseline([("Atlas", 0)]),
                horizon_weeks=4,
            )
        assert "Atlas" in str(exc.value.details["uncovered_share_by_payer"])
