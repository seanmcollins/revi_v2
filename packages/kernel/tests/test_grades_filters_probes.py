"""Grade law, filter algebra, probe hashing, frame invariants."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revi_kernel.cohort import CohortDefinition, CohortMaterialization, CohortRef
from revi_kernel.filters import (
    EMPTY_SCOPE,
    And,
    InCohort,
    Not,
    Or,
    Predicate,
    PredicateOp,
    and_merge,
    dimensions_used,
    is_empty,
    iter_cohorts,
    iter_predicates,
)
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
)
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.probes import (
    AggregationProbe,
    canonical_json,
    probe_hash,
)
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
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark

PAYER = DimensionRef("payer")
CASH = MetricRef("cash_posted")
WINDOW = TimeWindow(basis=POST, range=AbsoluteRange(date(2026, 7, 27), date(2026, 8, 2)))
WM = DataWatermark("wm_003", datetime(2026, 8, 3, 4, 10), date(2026, 8, 2))


# --- grades -----------------------------------------------------------------

GRADES = list(EvidenceGrade)


@given(st.lists(st.sampled_from(GRADES), min_size=1, max_size=6))
def test_min_grade_is_weakest(grades: list[EvidenceGrade]) -> None:
    result = min_grade(*grades)
    assert result in grades
    assert all(result.strength <= g.strength for g in grades)


@given(st.lists(st.sampled_from(GRADES), min_size=1, max_size=6))
def test_min_grade_order_invariant(grades: list[EvidenceGrade]) -> None:
    assert min_grade(*grades) == min_grade(*reversed(grades))


def test_proxy_cannot_launder() -> None:
    assert min_grade(EvidenceGrade.DIRECT, EvidenceGrade.PROXY, EvidenceGrade.DIRECT) is EvidenceGrade.PROXY


# --- filters ----------------------------------------------------------------


class TestPredicateArity:
    def test_eq_needs_exactly_one(self) -> None:
        with pytest.raises(ValueError):
            Predicate(PAYER, PredicateOp.EQ, ())
        with pytest.raises(ValueError):
            Predicate(PAYER, PredicateOp.EQ, ("a", "b"))

    def test_range_needs_two(self) -> None:
        with pytest.raises(ValueError):
            Predicate(PAYER, PredicateOp.RANGE, (Decimal(1),))

    def test_is_null_needs_none(self) -> None:
        Predicate(PAYER, PredicateOp.IS_NULL)
        with pytest.raises(ValueError):
            Predicate(PAYER, PredicateOp.IS_NULL, ("x",))

    def test_contains_needs_string(self) -> None:
        with pytest.raises(ValueError, match="string"):
            Predicate(PAYER, PredicateOp.CONTAINS, (1,))

    def test_empty_or_rejected(self) -> None:
        with pytest.raises(ValueError):
            Or(())


def _cohort() -> CohortRef:
    definition = CohortDefinition(entity=EntityGrain.CLAIM, scope=EMPTY_SCOPE)
    return CohortRef(
        id="coh_1",
        definition=definition,
        origin=ReferentId("F2", ReferentKind.FINDING),
        size=1200,
    )


class TestFilterHelpers:
    def test_empty_scope(self) -> None:
        assert is_empty(EMPTY_SCOPE)
        assert not is_empty(Predicate(PAYER, PredicateOp.EQ, ("Atlas",)))

    def test_and_merge_flattens(self) -> None:
        p1 = Predicate(PAYER, PredicateOp.EQ, ("Atlas",), origin_turn="t1")
        p2 = Predicate(DimensionRef("facility"), PredicateOp.NEQ, ("Eastside",), origin_turn="t2")
        merged = and_merge(And((p1,)), EMPTY_SCOPE, p2)
        assert isinstance(merged, And)
        assert merged.clauses == (p1, p2)

    def test_and_merge_single_collapses(self) -> None:
        p = Predicate(PAYER, PredicateOp.EQ, ("Atlas",))
        assert and_merge(EMPTY_SCOPE, p) == p

    def test_iterators(self) -> None:
        p1 = Predicate(PAYER, PredicateOp.EQ, ("Atlas",))
        p2 = Predicate(DimensionRef("carc"), PredicateOp.IN, ("197", "22"))
        expr = And((Or((p1, Not(p2))), InCohort(_cohort())))
        assert set(iter_predicates(expr)) == {p1, p2}
        assert len(list(iter_cohorts(expr))) == 1
        assert dimensions_used(expr) == frozenset({PAYER, DimensionRef("carc")})


# --- probe hashing ----------------------------------------------------------


def _probe(**overrides: object) -> AggregationProbe:
    base: dict[str, object] = {
        "measures": (CASH,),
        "dimensions": (PAYER,),
        "scope": And((Predicate(PAYER, PredicateOp.IN, ("Atlas", "Meridian"), origin_turn="t1"),)),
        "window": WINDOW,
        "grain": Grain(EntityGrain.TRANSACTION, TimeBucket.WEEK),
    }
    base.update(overrides)
    return AggregationProbe(**base)  # type: ignore[arg-type]


class TestProbeHash:
    def test_equal_probes_equal_hash(self) -> None:
        assert probe_hash(_probe()) == probe_hash(_probe())

    def test_any_field_change_changes_hash(self) -> None:
        h = probe_hash(_probe())
        assert probe_hash(_probe(limit=10)) != h
        assert probe_hash(_probe(dimensions=(DimensionRef("facility"),))) != h

    def test_known_stable_digest(self) -> None:
        """Cross-process stability: this digest must never change silently.

        If this fails, the canonical serialization changed — that invalidates
        every evidence cache entry and must be a conscious, versioned event.
        """
        assert probe_hash(_probe()) == (
            "088f0050828ffce0e88afefbccc94cd874bb97629560874828514e7fe0695d22"
        )

    def test_materialization_volatile_fields_excluded(self) -> None:
        definition = CohortDefinition(entity=EntityGrain.CLAIM, scope=EMPTY_SCOPE)
        origin = ReferentId("F2", ReferentKind.FINDING)

        def cohort_at(created: datetime) -> CohortRef:
            return CohortRef(
                id="coh_1",
                definition=definition,
                origin=origin,
                size=1200,
                pinned=CohortMaterialization(
                    cohort_id="coh_1",
                    watermark=WM,
                    entity_ids_ref="cohort_store.cohort_coh_1",
                    size=1200,
                    created_at=created,
                    ttl_seconds=86400,
                ),
            )

        a = _probe(scope=InCohort(cohort_at(datetime(2026, 8, 3, 10, 0))))
        b = _probe(scope=InCohort(cohort_at(datetime(2026, 8, 3, 11, 30))))
        assert probe_hash(a) == probe_hash(b)

    def test_canonical_json_sorted_and_typed(self) -> None:
        js = canonical_json(_probe())
        assert '"__type__":"AggregationProbe"' in js
        assert '"date:2026-07-27"' in js


# --- frames -----------------------------------------------------------------


class TestFrame:
    def test_row_width_validated(self) -> None:
        schema = FrameSchema((FrameColumn("payer", PAYER), FrameColumn("cash", CASH, contract_version=1)))
        with pytest.raises(ValueError, match="row 0"):
            EvidenceFrame(
                schema=schema,
                rows=(("Atlas",),),
                watermark=WM,
                provenance=ProbeProvenance("p1", "hash"),
                evidence_grade=EvidenceGrade.DIRECT,
            )

    def test_column_accessor(self) -> None:
        schema = FrameSchema((FrameColumn("payer", PAYER), FrameColumn("cash", CASH)))
        frame = EvidenceFrame(
            schema=schema,
            rows=(("Atlas", 412_000_00), ("Meridian", 291_500_00)),
            watermark=WM,
            provenance=ProbeProvenance("p1", "hash"),
            evidence_grade=EvidenceGrade.DIRECT,
        )
        assert frame.column("cash") == (412_000_00, 291_500_00)
        assert frame.row_count == 2

    def test_duplicate_columns_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            FrameSchema((FrameColumn("x", PAYER), FrameColumn("x", CASH)))

    def test_pinned_cohort_id_mismatch_rejected(self) -> None:
        definition = CohortDefinition(entity=EntityGrain.CLAIM, scope=EMPTY_SCOPE)
        with pytest.raises(ValueError, match="belongs to cohort"):
            CohortRef(
                id="coh_A",
                definition=definition,
                origin=ReferentId("F1", ReferentKind.FINDING),
                size=1,
                pinned=CohortMaterialization(
                    cohort_id="coh_B",
                    watermark=WM,
                    entity_ids_ref="ref",
                    size=1,
                    created_at=datetime(2026, 8, 3),
                    ttl_seconds=60,
                ),
            )
