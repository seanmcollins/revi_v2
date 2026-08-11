"""The cohort chip stops being a hash.

The chip used to read ``cohort: coh_9f2a11… (312 claims)``. Everything these
tests assert already existed on the pinned ``CohortRef``; none of it was on the
wire, which made the one context chip a reader could not check the one they most
needed to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from revi_api.cohort_payload import (
    build_cohort_payload,
    cohort_id_from_trace,
    cohort_payload_for,
    render_filter,
)
from revi_kernel.cohort import CohortDefinition, CohortMaterialization, CohortRef
from revi_kernel.filters import And, Not, Or, Predicate, PredicateOp
from revi_kernel.refs import SERVICE, DimensionRef, EntityGrain, ReferentId, ReferentKind
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
PAYER = DimensionRef("payer")


def _predicate(op: PredicateOp = PredicateOp.IN, *values: str) -> Predicate:
    return Predicate(dimension=PAYER, op=op, values=values or ("State Medicaid",))


def _cohort(*, scope=None, window=None, pinned=True, size=312) -> CohortRef:
    definition = CohortDefinition(
        entity=EntityGrain.CLAIM,
        scope=scope if scope is not None else _predicate(
            PredicateOp.IN, "State Medicaid", "Summit Peak Medicare Advantage"
        ),
        window=window,
    )
    materialization = (
        CohortMaterialization(
            cohort_id="coh_9f2a11",
            watermark=WATERMARK,
            entity_ids_ref="cohort_coh_9f2a11",
            size=size,
            created_at=datetime(2026, 8, 3, 5, 0, tzinfo=UTC),
            ttl_seconds=86_400,
        )
        if pinned
        else None
    )
    return CohortRef(
        id="coh_9f2a11",
        definition=definition,
        origin=ReferentId("F2", ReferentKind.FINDING),
        size=size,
        pinned=materialization,
    )


class TestRendering:
    def test_a_membership_predicate_reads_as_the_selection_it_is(self) -> None:
        rendered = render_filter(
            _predicate(PredicateOp.IN, "State Medicaid", "Summit Peak Medicare Advantage")
        )
        assert rendered == "payer in [State Medicaid, Summit Peak Medicare Advantage]"

    def test_a_single_value_needs_no_brackets(self) -> None:
        assert render_filter(_predicate(PredicateOp.EQ, "State Medicaid")) == (
            "payer eq State Medicaid"
        )

    def test_conjunction_reads_as_prose(self) -> None:
        expr = And(
            (
                _predicate(PredicateOp.EQ, "State Medicaid"),
                Predicate(
                    dimension=DimensionRef("service_line"),
                    op=PredicateOp.EQ,
                    values=("Cardiology",),
                ),
            )
        )
        assert render_filter(expr) == "payer eq State Medicaid and service line eq Cardiology"

    def test_disjunction_and_negation_keep_their_shape(self) -> None:
        expr = Not(Or((_predicate(PredicateOp.EQ, "A"), _predicate(PredicateOp.EQ, "B"))))
        assert render_filter(expr) == "not ((payer eq A or payer eq B))"

    def test_an_empty_scope_says_so_rather_than_rendering_blank(self) -> None:
        """A definition that renders to "" reads as an empty population.
        This one selects everything, and says that."""
        assert "everything in scope" in render_filter(And(()))


class TestPayload:
    async def test_the_parts_a_reader_needs_are_all_published(self) -> None:
        payload = await build_cohort_payload(_cohort(), session_id="sess_1")
        assert payload.id == "coh_9f2a11"
        assert payload.entity_grain == "claim"
        assert payload.definition == (
            "payer in [State Medicaid, Summit Peak Medicare Advantage]"
        )
        assert payload.size == 312
        assert payload.origin_referent == "F2"
        assert payload.pinned is True
        assert payload.pinned_watermark_id == "wm_003"

    async def test_a_cohort_window_travels_when_the_definition_kept_one(self) -> None:
        window = TimeWindow(
            basis=SERVICE,
            range=AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 31)),
        )
        payload = await build_cohort_payload(_cohort(window=window), session_id="s")
        assert payload.window_start == date(2026, 7, 1)
        assert payload.window_end == date(2026, 7, 31)

    async def test_no_window_is_meaningful_absence(self) -> None:
        """A cohort pinned without its window covers the scoped population
        across all time, and a warning said so when it happened."""
        payload = await build_cohort_payload(_cohort(), session_id="s")
        assert payload.window_start is None and payload.window_end is None

    async def test_the_origin_turn_is_resolved_through_the_registry(self) -> None:
        payload = await build_cohort_payload(
            _cohort(),
            session_id="sess_1",
            referents=_Registry(investigation_id="inv_7"),
            investigations=_Investigations(turn_id="turn_3"),
        )
        assert payload.origin_investigation_id == "inv_7"
        assert payload.origin_turn_id == "turn_3"

    async def test_a_lost_registry_entry_costs_the_reader_nothing_else(self) -> None:
        payload = await build_cohort_payload(
            _cohort(), session_id="sess_1", referents=_Registry(investigation_id=None)
        )
        assert payload.origin_turn_id is None
        assert payload.definition  # the load-bearing parts still render
        assert payload.size == 312


class TestLookup:
    async def test_no_cohort_id_is_simply_no_payload(self) -> None:
        assert await cohort_payload_for(None, session_id="s", cohorts=_Cohorts()) is None

    async def test_a_named_cohort_the_store_lost_is_logged_not_fatal(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            payload = await cohort_payload_for(
                "coh_missing", session_id="s", cohorts=_Cohorts()
            )
        assert payload is None
        assert "no record of it" in caplog.text

    def test_the_stored_turn_names_its_cohort(self) -> None:
        assert cohort_id_from_trace({"refinement": {"cohort": {"id": "coh_1", "size": 3}}}) == (
            "coh_1"
        )
        assert cohort_id_from_trace({"refinement": {}}) is None
        assert cohort_id_from_trace(None) is None


# --- doubles ---------------------------------------------------------------


@dataclass
class _Registry:
    investigation_id: str | None

    async def resolve(self, session_id: str, referent):
        del session_id, referent
        if self.investigation_id is None:
            return None
        return _Entry(investigation_id=self.investigation_id)


@dataclass
class _Entry:
    investigation_id: str


@dataclass
class _Investigations:
    turn_id: str

    async def get(self, investigation_id: str):
        del investigation_id
        return _StoredInvestigation(turn_id=self.turn_id)


@dataclass
class _StoredInvestigation:
    turn_id: str


class _Cohorts:
    async def get(self, cohort_id: str) -> None:
        del cohort_id
        return None
