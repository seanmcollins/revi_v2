"""A breakdown reconciles to the whole it broke down.

``containment_reconciliation`` opened on the drill operator alone and
returned ``None`` the moment no drill was present, so a twelve-cell payer
split of a July total, a CARC breakdown of a cell published two turns
earlier, and a rate split all reported "not applicable" over arithmetic that
tied out exactly. Money breakdowns sum; rate breakdowns recombine through
their own denominators, and a suppressed numerator yields an interval rather
than a disagreement. The child answer also carries the parent level it
descends from, so a reader who lands on the breakdown is told the whole.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.submit_turn import (
    containment_reconciliation,
    measure_mismatch_reason,
)
from revi_investigation.domain.context import AnalysisSpec
from revi_investigation.domain.records import Finding, Investigation, InvestigationStatus
from revi_investigation.domain.refinements import DrillInto, SetDimensions
from revi_investigation.domain.turns import TurnClass
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef, ReferentId, ReferentKind
from revi_kernel.watermark import DataWatermark

WATERMARK = DataWatermark(
    id="wm_003",
    loaded_at=datetime(2026, 8, 3, 4, 10, tzinfo=UTC).replace(tzinfo=None),
    newest_data_date=datetime(2026, 8, 2).date(),
)

#: The July total the live session published, in cents.
JULY_TOTAL = 119312692


def _whole(referent: str = "F1", cents: int = JULY_TOTAL) -> Finding:
    return Finding(
        referent=ReferentId(value=referent, kind=ReferentKind.FINDING),
        title=f"denied dollars: ${cents / 100:,.2f} (2026-07-01..2026-07-31)",
        statement="denied dollars over July 2026.",
        metric_refs=(MetricRef("denied_dollars"),),
        values=(("denied_dollars", cents),),
        grade=EvidenceGrade.DIRECT,
        impact_cents=cents,
        confidence="high",
    )


def _breakdown_frame(*cells: int) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            columns=(
                FrameColumn(name="payer", ref=DimensionRef("payer")),
                FrameColumn(
                    name="denied_dollars",
                    ref=MetricRef("denied_dollars"),
                    unit="money_cents",
                ),
            )
        ),
        rows=tuple((f"payer {i}", cents) for i, cents in enumerate(cells)),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h"),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _parent(make_spec, findings: tuple[Finding, ...], dimensions: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    return Investigation(
        id="inv_parent",
        session_id="sess",
        parent_id=None,
        turn_id="turn_parent",
        turn_class=TurnClass.NEW_INVESTIGATION,
        question="What were our denied dollars in July 2026?",
        spec=make_spec(measures=("denied_dollars",), dimensions=dimensions),
        plan_hash="hash",
        status=InvestigationStatus.COMPLETE,
        findings=findings,
        created_at=datetime.now(UTC),
    )


class TestABreakdownTiesOutToTheWhole:
    def test_the_parts_are_summed_and_the_agreement_is_published(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _parent(make_spec, (_whole(),))
        child = make_spec(measures=("denied_dollars",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _breakdown_frame(17611225, 16857544, 84843923)),),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None
        summary, passed = result.summary, result.passed
        assert passed is True
        assert summary.startswith("status=passed;")
        assert "breakdown was checked against the parent as a level vs level" in summary
        assert "parent F1 published $1,193,126.92" in summary
        assert "this answer comes to $1,193,126.92" in summary
        assert "a difference of $0.00" in summary
        assert "3 rows this breakdown published, summed" in summary

    def test_parts_that_do_not_sum_to_the_whole_fail_loudly(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _parent(make_spec, (_whole(),))
        child = make_spec(measures=("denied_dollars",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _breakdown_frame(17611225, 16857544)),), operations=()
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None
        summary, passed = result.summary, result.passed
        assert passed is False
        assert summary.startswith("status=failed;")

    def test_the_split_is_read_off_the_specs_not_off_the_operator_name(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """The third live symptom: the turn split the population, and the
        operator that got it there was not ``set_dimensions``."""
        parent = _parent(make_spec, (_whole(),))
        child = make_spec(measures=("denied_dollars",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _breakdown_frame(JULY_TOTAL)),), operations=()
        )

        result = containment_reconciliation(parent, calculation, (), child)

        assert result is not None and result.passed is True

    def test_an_already_cut_parent_has_no_whole_to_reconcile_against(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """A payer breakdown publishes twelve cells and no total; calling any
        one of them "the whole" would tie a breakdown out against a slice."""
        parent = _parent(make_spec, (_whole(),), dimensions=("payer",))
        child = make_spec(measures=("denied_dollars",), dimensions=("payer", "carc"))
        calculation = CalculationResult(
            frames=(("main", _breakdown_frame(JULY_TOTAL)),), operations=()
        )

        assert containment_reconciliation(parent, calculation, (), child) is None

    def test_a_turn_that_did_not_split_is_not_reconciled(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _parent(make_spec, (_whole(),))
        child = make_spec(measures=("denied_dollars",))
        calculation = CalculationResult(
            frames=(("main", _breakdown_frame(JULY_TOTAL)),), operations=()
        )

        assert containment_reconciliation(parent, calculation, (), child) is None


class TestADrillFindsItsCellWhereverTheSessionPublishedIt:
    def test_a_handle_from_an_earlier_turn_still_reconciles(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """The CARC case: the cell the drill decomposes was published two
        turns earlier, so it is not on the immediate parent."""
        ancestor = _whole("F2", 17611225)
        parent = _parent(make_spec, (), dimensions=("payer",))
        child = make_spec(measures=("denied_dollars",), dimensions=("carc",))
        calculation = CalculationResult(
            frames=(("main", _breakdown_frame(12375720, 1817529, 3418, 3414558)),),
            operations=(),
        )

        result = containment_reconciliation(
            parent,
            calculation,
            (DrillInto(ReferentId(value="F2", kind=ReferentKind.FINDING)),),
            child,
            session_findings=lambda: (ancestor,),
        )

        assert result is not None
        summary, passed = result.summary, result.passed
        assert passed is True
        assert "parent F2 published $176,112.25" in summary

    def test_an_older_handle_measuring_something_else_is_not_tied_out(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """Strictly the same metric across turns. A denied-dollar drill tied
        out against a cash finding is a disagreement this platform would
        then have to explain, and there is nothing to explain."""
        cash = replace(
            _whole("F2", 9909308),
            metric_refs=(MetricRef("cash_posted"),),
            values=(("cash_posted", 9909308),),
        )
        parent = _parent(make_spec, (), dimensions=("payer",))
        child = make_spec(measures=("denied_dollars",), dimensions=("carc",))
        calculation = CalculationResult(
            frames=(("main", _breakdown_frame(1196704)),), operations=()
        )

        assert (
            containment_reconciliation(
                parent,
                calculation,
                (DrillInto(ReferentId(value="F2", kind=ReferentKind.FINDING)),),
                child,
                session_findings=lambda: (cash,),
            )
            is None
        )


def test_the_tolerance_is_a_half_basis_point() -> None:
    """Pinned: a breakdown that misses by a rounding cent still passes, and
    one that misses by a percent does not."""
    from revi_investigation.application.submit_turn import _CONTAINMENT_TOLERANCE

    assert Decimal("0.005") == _CONTAINMENT_TOLERANCE

# ---------------------------------------------------------------------------
# Rate breakdowns: recomposition rather than addition
#
# "For July 2026 the denial rate came in at 12.8% (F1)" -> "Break that down
# by payer" reported that there was no compared money frame to reconcile
# against, then published 29.5% / 22.9% / 18.8% and four ceilings without
# ever restating the 12.8%. Rates recombine through their own denominators.


def _investigation(
    spec: AnalysisSpec, findings: tuple[Finding, ...], *, dimensions: tuple[str, ...] = ()
) -> Investigation:
    return Investigation(
        id="inv_parent",
        session_id="sess",
        parent_id=None,
        turn_id="turn_parent",
        turn_class=TurnClass.NEW_INVESTIGATION,
        question="What was our denial rate in July 2026?",
        spec=spec,
        plan_hash="hash",
        status=InvestigationStatus.COMPLETE,
        findings=findings,
        created_at=datetime.now(UTC),
    )


def _rate_whole(rate: str = "0.128", referent: str = "F1") -> Finding:
    return Finding(
        referent=ReferentId(value=referent, kind=ReferentKind.FINDING),
        title=f"denial rate: {float(rate):.1%} (2026-07-01..2026-07-31)",
        statement="denial rate over July 2026.",
        metric_refs=(MetricRef("denial_rate"),),
        values=(("denial_rate", Decimal(rate)),),
        grade=EvidenceGrade.DIRECT,
        impact_cents=None,
        confidence="high",
    )


def _rate_cells(*cells: tuple[str, int | None, int]) -> EvidenceFrame:
    """One row per payer: ``(label, numerator or None, denominator)``.

    ``None`` is a numerator the §15 policy withheld — the cell keeps its
    population and loses its contribution, which is the case the interval
    exists for.
    """
    columns = (
        FrameColumn("payer", DimensionRef("payer")),
        FrameColumn("denial_rate", MetricRef("denial_rate"), 2, "ratio"),
        FrameColumn("denial_rate__num", MetricRef("denial_rate"), 2, "count"),
        FrameColumn("denial_rate__den", MetricRef("denial_rate"), 2, "count"),
    )
    rows = tuple(
        (label, None if num is None else Decimal(num) / Decimal(den), num, den)
        for label, num, den in cells
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestARateBreakdownRecomposesToItsParent:
    def test_the_cells_recombine_through_their_denominators_and_agree(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """1,280 denied over 10,000 adjudicated across four payers IS the
        parent's 12.8% — weighted by each cell's own population, which is
        what a rate breakdown means and what summing three percentages
        never could."""
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(
                (
                    "main",
                    _rate_cells(
                        ("Atlas Commercial", 295, 1000),
                        ("Veritas Health", 229, 1000),
                        ("Pinnacle", 188, 1000),
                        ("State Medicaid MCO", 568, 7000),
                    ),
                ),
            ),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None
        assert result.passed is True
        assert result.summary.startswith("status=passed;")
        assert "breakdown was checked against the parent by recomposing the rate" in result.summary
        assert "parent F1 published 12.8%" in result.summary
        assert "the cells recompose to 12.8%" in result.summary
        assert "1,280 over 10,000" in result.summary

    def test_cells_that_do_not_recompose_to_the_parent_fail_loudly(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _rate_cells(("Atlas", 295, 1000), ("Veritas", 229, 1000))),),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None
        assert result.passed is False
        assert result.summary.startswith("status=failed;")
        # Signed: the direction of the gap survives the rewrite.
        assert "a difference of +" in result.summary

    def test_a_withheld_numerator_is_an_interval_not_a_disagreement(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """A cell the small-cell policy silenced keeps its population and
        loses its contribution. The parent still sits inside what those
        cells could be, so nothing disagrees — and calling that ``failed``
        would publish a conflict between two correct figures."""
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole("0.135"),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(
                (
                    "main",
                    _rate_cells(
                        ("Atlas Commercial", 1280, 10000),
                        ("Tiny Plan", None, 500),
                    ),
                ),
            ),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None
        assert result.passed is True
        # The grammar's own third state: not a point tie-out, not a
        # disagreement — the §15 policy standing between the two figures.
        assert result.summary.startswith("status=passed_with_suppression;")
        assert "A further 1 cell" in result.summary
        assert "between 12.2% and 17.0%" in result.summary
        assert "the gap is the suppression and not a disagreement" in result.summary

    def test_a_clamped_ceiling_is_not_summed_as_a_numerator(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """The bounded-endpoint rule, one layer up — and a live regression
        the first cut of this fix shipped.

        The §15 policy publishes a small numerator as ``threshold - 1``
        rather than dropping the cell, so a bounded cell arrives carrying
        the integer 10 and reads exactly like a measurement. Live, 12 payer
        cells summed to 208/1,544 = 13.5% against a parent of 12.8% and the
        seam reported ``RECONCILIATION_FAILED`` about a gap that was
        entirely the suppression policy's.
        """
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        cells = _rate_cells(
            ("Atlas Commercial", 29, 355),
            ("Bluestone Mutual", 21, 129),
            ("Meridian Health", 29, 223),
            ("Northbridge Commercial", 12, 140),
            ("Pinnacle Health Plan", 22, 96),
            ("Silverline Medicare Advantage", 22, 117),
            ("State Medicaid", 15, 95),
            ("State Medicaid MCO", 18, 61),
            # The four the live payload published as ceilings: a numerator
            # under the threshold, clamped to threshold - 1.
            ("Federal Medicare", 10, 214),
            ("Lakewood Medicaid MCO", 10, 48),
            ("Summit Peak Medicare Advantage", 10, 53),
            ("Veritas Comp Fund", 10, 13),
        )
        calculation = CalculationResult(frames=(("main", cells),), operations=())

        summed = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )
        guarded = containment_reconciliation(
            parent,
            calculation,
            (SetDimensions((DimensionRef("payer"),)),),
            child,
            suppression_threshold=11,
        )

        # Without the threshold the ceilings look like measurements…
        assert summed is not None and summed.passed is False
        assert "208 over 1,544" in summed.summary
        # …and with it, the eight measured cells recompose and the four
        # ceilings contribute their POPULATION and a cap the policy itself
        # supplies, which the parent's 12.8% sits inside.
        assert guarded is not None and guarded.passed is True
        assert guarded.summary.startswith("status=passed_with_suppression;")
        assert "168 over 1,216" in guarded.summary
        assert "A further 4 cells, covering 328 of the population" in guarded.summary
        assert "between 10.9% and 13.5%" in guarded.summary

    def test_a_parent_outside_that_interval_still_fails(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole("0.400"),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(
                (
                    "main",
                    _rate_cells(
                        ("Atlas Commercial", 1280, 10000),
                        ("Tiny Plan", None, 500),
                    ),
                ),
            ),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None and result.passed is False

    def test_a_rate_child_with_no_parent_rate_claims_nothing(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """Silence is the honest outcome, not a fabricated tie-out."""
        parent = _investigation(make_spec(measures=("denial_rate",)), ())
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _rate_cells(("Atlas", 295, 1000))),), operations=()
        )

        assert (
            containment_reconciliation(
                parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
            )
            is None
        )

    def test_a_drill_of_a_named_rate_finding_reconciles_too(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("carc",))
        calculation = CalculationResult(
            frames=(("main", _rate_cells(("CO / 16", 1280, 10000))),), operations=()
        )

        result = containment_reconciliation(
            parent,
            calculation,
            (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
            child,
        )

        assert result is not None and result.passed is True
        assert "this drill was checked against the parent by recomposing the rate" in result.summary


class TestTheChildAnswerCarriesTheParentLevel:
    def test_a_rate_breakdown_states_the_whole_it_descends_from(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """"A reader who lands on the breakdown comes away believing denial
        rates run 19-29%." The 12.8% is now on the child, as a mandatory
        disclosure rather than as a line in a verdict."""
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _rate_cells(("Atlas", 1280, 10000))),), operations=()
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None and result.anchor is not None
        assert result.anchor.startswith("parent_level:")
        assert "12.8% (F1)" in result.anchor
        assert "denial rate" in result.anchor
        # How they recombine, said explicitly: three percentages do not add.
        assert "through their own denominators, not by addition" in result.anchor

    def test_a_money_breakdown_carries_the_same_anchor(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        money_whole = Finding(
            referent=ReferentId(value="F1", kind=ReferentKind.FINDING),
            title="denied dollars: $1,193,126.92",
            statement="denied dollars over July 2026.",
            metric_refs=(MetricRef("denied_dollars"),),
            values=(("denied_dollars", 119312692),),
            grade=EvidenceGrade.DIRECT,
            impact_cents=119312692,
            confidence="high",
        )
        parent = _investigation(make_spec(measures=("denied_dollars",)), (money_whole,))
        child = make_spec(measures=("denied_dollars",), dimensions=("payer",))
        frame = EvidenceFrame(
            schema=FrameSchema(
                (
                    FrameColumn("payer", DimensionRef("payer")),
                    FrameColumn(
                        "denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"
                    ),
                )
            ),
            rows=(("Atlas", 119312692),),
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )

        result = containment_reconciliation(
            parent,
            CalculationResult(frames=(("main", frame),), operations=()),
            (SetDimensions((DimensionRef("payer"),)),),
            child,
        )

        assert result is not None and result.anchor is not None
        assert "$1,193,126.92 (F1)" in result.anchor
        assert "by addition" in result.anchor

    def test_the_disclosure_code_is_one_the_narrative_may_not_drop(self) -> None:
        from revi_presentation.narrative import MANDATORY_DISCLOSURE_CODES, recovered_code

        assert "PARENT_LEVEL" in MANDATORY_DISCLOSURE_CODES
        assert "NOT_COMPARABLE_WINDOWS" in MANDATORY_DISCLOSURE_CODES
        # The engine's prefix convention carries a brand-new family through
        # an API warning table that has not learned its name yet.
        assert recovered_code("UNCLASSIFIED", "parent_level: …") == "PARENT_LEVEL"
        assert (
            recovered_code("UNCLASSIFIED", "not_comparable_windows: …")
            == "NOT_COMPARABLE_WINDOWS"
        )


# ---------------------------------------------------------------------------
# Drilling a rate finding
#
# regression: the product's own "drill into F1" chip on a 29.5% denial-rate
# finding published RECONCILIATION_FAILED "parent F1=$0.00; child=$31,174.49;
# delta=$31,174.49 (+311744900.0%)" in a red alert. int(Decimal("0.295082"))
# is 0, and the immediate-parent branch matched on the referent alone, so a
# ratio was read back as a dollar figure of nothing and differenced against a
# pile of dollars.

#: The pinned pack's units, as the turn service reads them.
UNITS = {"denial_rate": "ratio", "denied_dollars": "money_cents"}


def _rate_finding() -> Finding:
    """A rate and a rank, and not one dollar anywhere on it."""
    return Finding(
        referent=ReferentId(value="F1", kind=ReferentKind.FINDING),
        title="State Medicaid MCO: 29.5% denial rate",
        statement="State Medicaid MCO leads on denial rate at 29.5%.",
        metric_refs=(MetricRef("denial_rate"),),
        values=(("denial_rate", Decimal("0.295082")), ("rank", 1)),
        grade=EvidenceGrade.DIRECT,
        confidence="high",
    )


def _money_child_frame(cents: int) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            columns=(
                FrameColumn(name="carc", ref=DimensionRef("carc")),
                FrameColumn(
                    name="denied_dollars", ref=MetricRef("denied_dollars"), unit="money_cents"
                ),
            )
        ),
        rows=(("16", cents),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h"),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _rate_parent(make_spec, findings: tuple[Finding, ...]):  # type: ignore[no-untyped-def]
    return Investigation(
        id="inv_parent",
        session_id="sess",
        parent_id=None,
        turn_id="turn_parent",
        turn_class=TurnClass.NEW_INVESTIGATION,
        question="Who is my worst payer on denial rate right now?",
        spec=make_spec(measures=("denial_rate",), dimensions=("payer",)),
        plan_hash="hash",
        status=InvestigationStatus.COMPLETE,
        findings=findings,
        created_at=datetime.now(UTC),
    )


class TestDrillingARateFindingNeverPublishesADisagreement:
    def test_a_rate_parent_is_never_read_back_as_zero_dollars(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _rate_parent(make_spec, (_rate_finding(),))
        child = make_spec(measures=("denied_dollars",), dimensions=("carc",))

        result = containment_reconciliation(
            parent,
            CalculationResult(frames=(("main", _money_child_frame(3117449)),), operations=()),
            (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
            child,
            metric_unit=UNITS.get,
        )

        # The one thing that may never happen: a red banner claiming two
        # figures for one cell disagree by 311,744,900%.
        assert result is None or "status=failed" not in result.summary
        assert result is None or "$0.00" not in result.summary

    def test_it_holds_without_a_pack_to_ask(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """Money is cents-as-int everywhere in this system, so a fraction of
        a cent is not money whether or not the contract is reachable."""
        parent = _rate_parent(make_spec, (_rate_finding(),))
        child = make_spec(measures=("denied_dollars",), dimensions=("carc",))

        result = containment_reconciliation(
            parent,
            CalculationResult(frames=(("main", _money_child_frame(3117449)),), operations=()),
            (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
            child,
        )

        assert result is None or "status=failed" not in result.summary

    def test_the_mismatch_is_said_rather_than_left_to_a_generic_reason(self) -> None:
        reason = measure_mismatch_reason(
            (_rate_finding(),),
            (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
            "denied_dollars",
        )
        assert reason is not None
        # The handle, and both measures — named the way a reader reads them.
        assert "F1" in reason and "denial rate" in reason and "denied dollars" in reason

    def test_a_handle_that_does_publish_the_measure_keeps_its_tie_out(self) -> None:
        money = Finding(
            referent=ReferentId(value="F1", kind=ReferentKind.FINDING),
            title="denied dollars: $31,174.49",
            statement="denied dollars.",
            metric_refs=(MetricRef("denied_dollars"),),
            values=(("denied_dollars", 3117449),),
            grade=EvidenceGrade.DIRECT,
            impact_cents=3117449,
        )
        assert (
            measure_mismatch_reason(
                (money,),
                (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
                "denied_dollars",
            )
            is None
        )
