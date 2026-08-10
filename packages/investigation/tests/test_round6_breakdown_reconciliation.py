"""Round-6 E-02: a breakdown reconciles to the whole it broke down.

"The first question any VP asks of a breakdown is whether the parts agree
with the whole. The arithmetic is right; the product makes the reader do it
by hand." — rcm-exec, round 6.

Three live symptoms, one predicate. ``containment_reconciliation`` opened
with ``targets = {op.target for op in operators if isinstance(op,
DrillInto)}`` and returned ``None`` the moment that set was empty, so:

* "Break that out by payer" off a $1,193,126.92 July total published twelve
  cells summing to $1,193,126.92 and reported ``not_applicable; this turn
  produced no compared money frame to reconcile against the parent``;
* a 13-cell CARC breakdown summing to $176,112.25 — exactly the Summit Peak
  figure the same session had published two turns earlier — reported
  ``not_applicable; this is a first turn``;
* a rate split reported ``this turn neither split nor drilled the parent's
  population`` on a turn that plainly split it, because the operator that
  got it there was not the one the predicate named.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.submit_turn import containment_reconciliation
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

#: The July total the exec's session published, in cents.
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
        assert summary.startswith("status=passed; scope=breakdown (level vs level);")
        assert "parent F1=$1,193,126.92" in summary
        assert "child=$1,193,126.92" in summary
        assert "delta=$0.00" in summary
        assert "3 row(s) this breakdown published, summed" in summary

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
        assert "parent F2=$176,112.25" in summary

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
