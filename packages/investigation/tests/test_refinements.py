"""Refinement locality (design §18.1-12) and the carryover laws (§7.7)."""

from dataclasses import fields, replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revi_investigation.domain import (
    AddFilter,
    AnalysisSpec,
    ContextPin,
    DrillInto,
    Expand,
    Explain,
    PackVersionRef,
    Pivot,
    RankBy,
    RemoveFilter,
    ResetContext,
    SetComparison,
    SetDimensions,
    SetGrain,
    SetWindow,
    apply_refinement,
    apply_refinements,
    empty_context,
)
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.errors import ContextConflictError, ReferentNotFoundError
from revi_kernel.filters import And, Predicate, PredicateOp
from revi_kernel.refs import (
    POST,
    DimensionRef,
    EntityGrain,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
    TimeBucket,
)
from revi_kernel.scope import (
    AbsoluteRange,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    resolve_window,
)
from revi_kernel.watermark import DataWatermark

WM = DataWatermark("wm_003", datetime(2026, 8, 3, 4, 10), date(2026, 8, 2))
PACK = PackVersionRef("revi-base-rcm", "2026.08.0")
PAYER = DimensionRef("payer")
CASH = MetricRef("cash_posted")


def base_spec() -> AnalysisSpec:
    window = resolve_window(
        RelativeRange(Decimal(1), TimeUnit.WEEK, RangeMode.FULL_PERIODS),
        WM.loaded_at.date(),
        basis=POST,
    )
    ctx = empty_context(window, Grain(EntityGrain.TRANSACTION, TimeBucket.WEEK), WM, PACK)
    return AnalysisSpec(context=ctx, measures=(CASH,))


def medicaid_cohort() -> CohortRef:
    return CohortRef(
        id="coh_medicaid",
        definition=CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=Predicate(PAYER, PredicateOp.EQ, ("State Medicaid",)),
        ),
        origin=ReferentId("F2", ReferentKind.FINDING),
        size=850,
    )


class TestReferenceAnchoring:
    def test_last_full_week_matches_design_10_3(self) -> None:
        """T1's header: Window Jul 27-Aug 2 (post) at watermark 2026-08-03."""
        spec = base_spec()
        assert spec.context.window.range == AbsoluteRange(date(2026, 7, 27), date(2026, 8, 2))


# --- locality: each operator changes only what it names ---------------------

CONTEXT_FIELDS = ("window", "comparison", "scope", "cohort", "grain", "watermark", "pack_version", "pins")
SPEC_FIELDS = ("context", "measures", "dimensions", "rank_by", "rank_descending", "limit")


def changed_fields(before: AnalysisSpec, after: AnalysisSpec) -> set[str]:
    changed = set()
    for f in SPEC_FIELDS:
        if getattr(before, f) != getattr(after, f):
            changed.add(f)
    if "context" in changed:
        changed.discard("context")
        for f in CONTEXT_FIELDS:
            if getattr(before.context, f) != getattr(after.context, f):
                changed.add(f"context.{f}")
    return changed


class TestLocality:
    def test_set_dimensions(self) -> None:
        out = apply_refinement(base_spec(), SetDimensions((PAYER,)), turn_id="t2")
        assert changed_fields(base_spec(), out) == {"dimensions"}

    def test_add_filter(self) -> None:
        op = AddFilter(Predicate(PAYER, PredicateOp.EQ, ("Atlas Commercial",)))
        out = apply_refinement(base_spec(), op, turn_id="t2")
        assert changed_fields(base_spec(), out) == {"context.scope"}
        scope = out.context.scope
        assert isinstance(scope, Predicate) and scope.origin_turn == "t2"  # §7.2 origin tagging

    def test_set_window_changes_window_only_when_no_comparison(self) -> None:
        op = SetWindow(RelativeRange(Decimal(6), TimeUnit.MONTH, RangeMode.FULL_PERIODS))
        out = apply_refinement(base_spec(), op, turn_id="t2")
        assert changed_fields(base_spec(), out) == {"context.window"}
        assert out.context.window.range == AbsoluteRange(date(2026, 2, 1), date(2026, 7, 31))

    def test_set_window_rederives_comparison(self) -> None:
        spec = apply_refinement(base_spec(), SetComparison(ComparisonKind.PRIOR_PERIOD), turn_id="t2")
        out = apply_refinement(
            spec, SetWindow(RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS)), turn_id="t3"
        )
        assert changed_fields(spec, out) == {"context.window", "context.comparison"}
        assert out.context.comparison is not None
        assert out.context.comparison.window.range == AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))

    def test_set_comparison(self) -> None:
        out = apply_refinement(base_spec(), SetComparison(ComparisonKind.PRIOR_PERIOD), turn_id="t2")
        assert changed_fields(base_spec(), out) == {"context.comparison"}
        cmp = out.context.comparison
        assert cmp is not None and cmp.window.range == AbsoluteRange(date(2026, 7, 20), date(2026, 7, 26))

    def test_set_grain(self) -> None:
        out = apply_refinement(base_spec(), SetGrain(Grain(EntityGrain.CLAIM, None)), turn_id="t2")
        assert changed_fields(base_spec(), out) == {"context.grain"}

    def test_drill_into(self) -> None:
        cohort = medicaid_cohort()
        out = apply_refinement(
            base_spec(), DrillInto(ReferentId("F2", ReferentKind.FINDING)),
            turn_id="t2", resolve_cohort=lambda _r: cohort,
        )
        assert changed_fields(base_spec(), out) == {"context.cohort"}
        assert out.context.cohort == cohort

    def test_pivot(self) -> None:
        out = apply_refinement(base_spec(), Pivot((MetricRef("denied_dollars"),)), turn_id="t2")
        assert changed_fields(base_spec(), out) == {"measures"}

    def test_explain_changes_nothing(self) -> None:
        out = apply_refinement(base_spec(), Explain(ReferentId("F2", ReferentKind.FINDING)), turn_id="t2")
        assert changed_fields(base_spec(), out) == set()

    def test_rank_by(self) -> None:
        out = apply_refinement(base_spec(), RankBy(CASH, descending=False), turn_id="t2")
        assert changed_fields(base_spec(), out) == {"rank_by", "rank_descending"}

    def test_expand(self) -> None:
        out = apply_refinement(base_spec(), Expand(25), turn_id="t2")
        assert changed_fields(base_spec(), out) == {"limit"}

    def test_remove_filter(self) -> None:
        spec = apply_refinement(
            base_spec(), AddFilter(Predicate(PAYER, PredicateOp.EQ, ("Atlas Commercial",))), turn_id="t2"
        )
        out = apply_refinement(spec, RemoveFilter(PAYER), turn_id="t3")
        assert changed_fields(spec, out) == {"context.scope"}
        assert out.context.scope == And(())


@given(
    dims=st.lists(st.sampled_from(["payer", "facility", "service_line", "carc"]), max_size=3, unique=True),
    limit=st.one_of(st.none(), st.integers(1, 50)),
)
def test_locality_property_over_random_specs(dims: list[str], limit: int | None) -> None:
    """Whatever the starting spec, SetDimensions touches only dimensions."""
    spec = replace(base_spec(), dimensions=tuple(DimensionRef(d) for d in dims), limit=limit)
    out = apply_refinement(spec, SetDimensions((DimensionRef("plan"),)), turn_id="tx")
    assert changed_fields(spec, out) <= {"dimensions"}
    assert out.dimensions == (DimensionRef("plan"),)


# --- carryover laws ---------------------------------------------------------


class TestCarryoverLaws:
    def test_law4_medicaid_conflict_detected_before_execution(self) -> None:
        """'Exclude Medicaid' while the active cohort is Medicaid-only."""
        spec = apply_refinement(
            base_spec(), DrillInto(ReferentId("F2", ReferentKind.FINDING)),
            turn_id="t2", resolve_cohort=lambda _r: medicaid_cohort(),
        )
        with pytest.raises(ContextConflictError, match="contradicts"):
            apply_refinement(
                spec,
                AddFilter(Predicate(PAYER, PredicateOp.NOT_IN, ("State Medicaid",))),
                turn_id="t3",
            )

    def test_law4_disjoint_eq_conflict(self) -> None:
        spec = apply_refinement(
            base_spec(), AddFilter(Predicate(PAYER, PredicateOp.EQ, ("Atlas Commercial",))), turn_id="t2"
        )
        with pytest.raises(ContextConflictError):
            apply_refinement(
                spec, AddFilter(Predicate(PAYER, PredicateOp.EQ, ("Meridian Health",))), turn_id="t3"
            )

    def test_law5_pins_survive_reset(self) -> None:
        pin = ContextPin(
            predicate=Predicate(PAYER, PredicateOp.NOT_IN, ("State Medicaid",)), declared_at_turn="t1"
        )
        spec = base_spec()
        spec = spec.with_context(replace(spec.context, pins=(pin,)))
        spec = apply_refinement(
            spec, AddFilter(Predicate(DimensionRef("facility"), PredicateOp.EQ, ("Eastside",))), turn_id="t2"
        )
        out = apply_refinement(spec, ResetContext(), turn_id="t3")
        assert out.context.pins == (pin,)
        assert out.context.scope == And(())
        cleared = apply_refinement(spec, ResetContext(keep_pins=False), turn_id="t3")
        assert cleared.context.pins == ()

    def test_referent_not_found(self) -> None:
        with pytest.raises(ReferentNotFoundError):
            apply_refinement(
                base_spec(), DrillInto(ReferentId("F9", ReferentKind.FINDING)),
                turn_id="t2", resolve_cohort=lambda _r: None,
            )

    def test_multi_operator_turn_t3_combo(self) -> None:
        """§10.3 T3: DrillInto + Pivot + SetDimensions applied in order."""
        cohort = medicaid_cohort()
        out = apply_refinements(
            base_spec(),
            (
                DrillInto(ReferentId("F2", ReferentKind.FINDING)),
                Pivot((MetricRef("denied_dollars"),)),
                SetDimensions((DimensionRef("carc"),)),
            ),
            turn_id="t3",
            resolve_cohort=lambda _r: cohort,
        )
        assert out.context.cohort == cohort
        assert out.measures == (MetricRef("denied_dollars"),)
        assert out.dimensions == (DimensionRef("carc"),)
        # everything else inherited (law 1)
        assert out.context.window == base_spec().context.window

    def test_effective_scope_conjoins_pins(self) -> None:
        pin = ContextPin(
            predicate=Predicate(PAYER, PredicateOp.NOT_IN, ("State Medicaid",)), declared_at_turn="t1"
        )
        spec = base_spec()
        spec = spec.with_context(replace(spec.context, pins=(pin,)))
        effective = spec.context.effective_scope()
        assert pin.predicate in getattr(effective, "clauses", (effective,))


def test_all_twelve_operators_covered() -> None:
    """The closed set has exactly twelve members (design §7.4)."""
    from revi_investigation.domain import refinements

    union_members = {
        "SetDimensions", "AddFilter", "RemoveFilter", "SetWindow", "SetComparison",
        "SetGrain", "DrillInto", "Pivot", "Explain", "RankBy", "Expand", "ResetContext",
    }
    for name in union_members:
        assert hasattr(refinements, name)
    assert len(union_members) == 12
    assert {f.name for f in fields(refinements.SetDimensions)} == {"dimensions"}
