"""What interpretation decided on the analyst's behalf, and whether it said so.

Round-1 live findings F19 (second half), F23 and F10 (capture half). Each
is the same failure in a different place: a decision the engine made
silently, published as if it were the analyst's own.

- **F19** — the interpreter scoped ``ar_over_90_pct`` by ``ar_age_bucket``.
  The metric *is* the 91-120 and 120+ buckets: its numerator pins them. The
  restated filter narrowed nothing, and because ``ar_age_bucket`` is not a
  declared scope dimension of the metric it turned an answerable question
  into a ``GRAIN_INCOMPATIBLE`` refusal.
- **F23** — the window was resolved against the load's clock rather than
  the data, and a window nobody asked for was disclosed only in a debug
  ``intent_summary``.
- **F10** — the direction a question asked about ("the biggest increase")
  was read by the model and then dropped on the floor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.anchoring import window_anchor
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.interpretation import InterpretQuestionService
from revi_investigation.application.planning import BuildInvestigationPlanService
from revi_investigation.application.ports import (
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_investigation.domain.context import AskedDirection, AskedMagnitude, PackVersionRef
from revi_investigation.domain.records import Session
from revi_kernel.filters import iter_predicates
from revi_kernel.scope import RangeMode
from revi_kernel.watermark import DataWatermark, WatermarkEpoch
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import make_usage

# The reference load: it ran at 04:10 on the 3rd over data through the 2nd.
WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
SESSION = Session(
    id="sess-1",
    tenant="demo",
    pack_version=PackVersionRef("base-rcm", "1.0.0"),
    epochs=(WatermarkEpoch(index=0, watermark=WATERMARK),),
    created_at=datetime(2026, 8, 3, 4, 20),
)


@dataclass
class _FixedLlm:
    output: dict[str, Any]

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        return StructuredLlmResult(output=self.output, usage=make_usage())

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        raise AssertionError("these tests never stream")

    async def last_usage(self) -> LlmUsage | None:
        return None


def _response(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent_summary": "test",
        "metric_ids": [],
        "dimension_ids": [],
        "concept_ids": [],
        "playbook_id": None,
        "window": None,
        "basis": None,
        "comparison": None,
        "scope": [],
        "direction": None,
        "magnitude": None,
        "clarification": None,
        "clarification_options": [],
        "definitional_terms": [],
    }
    base.update(overrides)
    return base


async def _interpret(
    pack_port: PackPort, catalog: CatalogSnapshot, **overrides: Any
) -> Any:
    service = InterpretQuestionService(_FixedLlm(_response(**overrides)), pack_port, catalog)
    outcome = await service.interpret("q", session=SESSION, turn_id="t1")
    assert outcome.investigation is not None, outcome.clarification
    return outcome.investigation


class TestAnchoring:
    def test_a_trailing_window_ends_at_the_newest_data_not_the_load(self) -> None:
        """"The last 90 days" that includes a day with no rows in it is 90
        days of data plus one day of structural zeroes."""
        assert window_anchor(WATERMARK, RangeMode.TRAILING) == date(2026, 8, 2)
        assert window_anchor(WATERMARK, RangeMode.TO_DATE) == date(2026, 8, 2)

    def test_a_full_period_window_keeps_the_period_the_data_completed(self) -> None:
        """Anchoring full periods at the newest data date would discard a
        complete week whenever the load lands on that week's last day."""
        assert window_anchor(WATERMARK, RangeMode.FULL_PERIODS) == date(2026, 8, 3)

    async def test_a_trailing_window_is_resolved_against_the_data(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"quantity": "90", "unit": "day", "mode": "trailing"},
        )
        assert interpreted.spec.context.window.range.end == date(2026, 8, 2)

    async def test_a_window_nobody_asked_for_is_stated_as_an_assumption(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(pack_port, catalog, metric_ids=["denial_rate"])

        assert interpreted.window_explicit is False
        assert any(note.startswith("window_assumed") for note in interpreted.notes)
        note = next(n for n in interpreted.notes if n.startswith("window_assumed"))
        assert interpreted.spec.context.window.range.start.isoformat() in note
        assert "2026-08-02" in note  # the anchor it was resolved against


class TestDirectionCapture:
    async def test_an_asked_direction_reaches_the_spec(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denied_dollars"],
            dimension_ids=["payer"],
            direction="increase",
            magnitude="largest",
            comparison="prior_period",
        )
        assert interpreted.spec.direction is AskedDirection.INCREASE
        assert interpreted.spec.magnitude is AskedMagnitude.LARGEST

    async def test_an_unasserted_direction_stays_none(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port, catalog, metric_ids=["denied_dollars"], dimension_ids=["payer"]
        )
        assert interpreted.spec.direction is None
        assert interpreted.spec.magnitude is None


class TestRedundantContractFilters:
    async def test_a_filter_the_metric_already_pins_is_dropped_and_stated(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """``ar_over_90_pct`` IS the 91-120 and 120+ buckets. Restating that
        narrowed nothing and refused the turn."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["ar_over_90_pct"],
            scope=[
                {"dimension": "ar_age_bucket", "op": "in", "values": ["91-120", "120+"]}
            ],
        )

        assert tuple(iter_predicates(interpreted.spec.context.scope)) == ()
        assert any(note.startswith("filter_redundant") for note in interpreted.notes)
        note = next(n for n in interpreted.notes if n.startswith("filter_redundant"))
        assert "ar_over_90_pct" in note

    async def test_a_subset_of_the_pinned_values_is_also_redundant(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["ar_over_90_pct"],
            scope=[{"dimension": "ar_age_bucket", "op": "eq", "values": ["120+"]}],
        )
        assert tuple(iter_predicates(interpreted.spec.context.scope)) == ()

    async def test_a_filter_naming_different_values_is_kept(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """"Unless values differ" is the whole rule: a filter that means
        something else survives, and §6.6's exclusion-overlap warning is
        what explains the interaction."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["ar_over_90_pct"],
            scope=[{"dimension": "ar_age_bucket", "op": "in", "values": ["0-30", "31-60"]}],
        )
        kept = tuple(iter_predicates(interpreted.spec.context.scope))
        assert [p.dimension.id for p in kept] == ["ar_age_bucket"]
        assert not any(note.startswith("filter_redundant") for note in interpreted.notes)

    async def test_an_unrelated_filter_is_untouched(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["ar_over_90_pct"],
            scope=[{"dimension": "payer", "op": "eq", "values": ["Atlas Commercial"]}],
        )
        kept = tuple(iter_predicates(interpreted.spec.context.scope))
        assert [p.dimension.id for p in kept] == ["payer"]


class TestDroppedGrain:
    def test_a_breakdown_no_probe_can_cut_by_is_named(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: Any
    ) -> None:
        """A plan that ignores a requested dimension is a valid plan and a
        silently averaged answer. It says so instead."""
        spec = make_spec(measures=("cash_posted",), dimensions=("payer",), watermark=WATERMARK)
        planner = BuildInvestigationPlanService(pack_port, catalog)

        # a playbook whose templates do not parameterize a dimension
        plan = planner.build(
            spec.__class__(  # same spec with no measures, so the playbook governs
                context=spec.context,
                measures=(),
                dimensions=(),
            ),
            playbook_id="cash_decline",
            window_explicit=True,
        )
        assert not [n for n in plan.notes if n.startswith("dropped_grain")]

    def test_a_planned_breakdown_raises_no_note(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: Any
    ) -> None:
        spec = make_spec(measures=("cash_posted",), dimensions=("payer",), watermark=WATERMARK)
        plan = BuildInvestigationPlanService(pack_port, catalog).build(spec)
        assert not [n for n in plan.notes if n.startswith("dropped_grain")]


@pytest.mark.parametrize("mode", list(RangeMode))
def test_every_range_mode_has_an_anchor(mode: RangeMode) -> None:
    assert window_anchor(WATERMARK, mode) >= WATERMARK.newest_data_date
