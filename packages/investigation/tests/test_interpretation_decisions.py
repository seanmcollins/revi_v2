"""What interpretation decided on the analyst's behalf, and whether it said so.

Three regressions, each the same failure in a different place — a decision the
engine made silently, published as if it were the analyst's own: a filter on
``ar_over_90_pct`` restating the 91-120 and 120+ buckets its numerator already
pins, which narrowed nothing and turned an answerable question into a
``GRAIN_INCOMPATIBLE`` refusal; a window resolved against the load's clock rather
than the data and disclosed only in a debug ``intent_summary``; and a direction
("the biggest increase") read by the model and then dropped on the floor.
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


# ---------------------------------------------------------------------------
# named periods, coverage, and the time axis


async def _interpret_outcome(
    pack_port: PackPort, catalog: CatalogSnapshot, **overrides: Any
) -> Any:
    service = InterpretQuestionService(_FixedLlm(_response(**overrides)), pack_port, catalog)
    return await service.interpret("q", session=SESSION, turn_id="t1")


class TestAnchoredWindows:
    """Regression: calendar vocabulary was unmappable.

    "June 2026", "Q2 2026", "2025" — the window schema was relative-only, so
    a named period either clarified or was silently answered over the last
    full month, while the typed path executed absolute windows perfectly.
    """

    async def test_a_named_month_resolves_to_that_month(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"unit": "month", "year": 2026, "index": 6},
        )
        window = interpreted.spec.context.window.range
        assert (window.start, window.end) == (date(2026, 6, 1), date(2026, 6, 30))
        assert interpreted.window_explicit is True
        # a period the analyst NAMED is never reported as one nobody named
        assert not [n for n in interpreted.notes if n.startswith("window_assumed")]

    async def test_a_named_quarter_compares_against_the_quarter_before_it(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """"Q2 2026 vs Q1" — the prior period of a calendar quarter is the
        calendar quarter before it, not the 91 days before it."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"unit": "quarter", "year": 2026, "index": 2},
            comparison="prior_period",
        )
        comparison = interpreted.spec.context.comparison
        assert comparison is not None
        assert (comparison.window.range.start, comparison.window.range.end) == (
            date(2026, 1, 1),
            date(2026, 3, 31),
        )

    async def test_a_named_year_resolves_to_the_calendar_year(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"unit": "year", "year": 2025},
        )
        window = interpreted.spec.context.window.range
        assert (window.start, window.end) == (date(2025, 1, 1), date(2025, 12, 31))

    async def test_explicit_dates_are_expressible_from_the_chat_box(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """A card publishes an absolute ``drill_spec.window`` the typed path
        runs; until now that window could not be restated in words, so a
        reviewer could not re-run a headline by hand."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"start": "2026-01-01", "end": "2026-06-30"},
        )
        window = interpreted.spec.context.window.range
        assert (window.start, window.end) == (date(2026, 1, 1), date(2026, 6, 30))

    async def test_relative_windows_are_unchanged(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"quantity": "6", "unit": "month", "mode": "full_periods"},
        )
        window = interpreted.spec.context.window.range
        assert (window.start, window.end) == (date(2026, 2, 1), date(2026, 7, 31))


class TestNamedPeriodsOutsideTheData:
    """Regression: WINDOW_ASSUMED claimed "the question named no period"
    under a bubble containing the words "in January 2019"."""

    async def test_a_period_after_the_data_clarifies_and_names_both(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        outcome = await _interpret_outcome(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"unit": "month", "year": 2027, "index": 3},
        )
        assert outcome.investigation is None
        clarification = outcome.clarification
        assert clarification is not None
        assert "March 2027" in clarification.question
        assert "2026-08-02" in clarification.question
        assert "WINDOW_OUT_OF_RANGE" in clarification.reason
        assert clarification.options, "an out-of-range period must offer one that exists"
        assert any("last full month" in option for option in clarification.options)

    async def test_a_period_before_the_data_is_named_when_the_load_knows_its_floor(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        from dataclasses import replace as _replace

        bounded = _replace(WATERMARK, oldest_data_date=date(2024, 1, 1))
        session = _replace(SESSION, epochs=(WatermarkEpoch(index=0, watermark=bounded),))
        service = InterpretQuestionService(
            _FixedLlm(
                _response(
                    metric_ids=["denial_rate"],
                    window={"unit": "month", "year": 2019, "index": 1},
                )
            ),
            pack_port,
            catalog,
        )
        outcome = await service.interpret("q", session=session, turn_id="t1")
        assert outcome.investigation is None
        assert outcome.clarification is not None
        assert "January 2019" in outcome.clarification.question
        assert "this data covers 2024-01-01..2026-08-02" in outcome.clarification.question

    async def test_a_period_that_only_partly_landed_answers_with_the_caveat(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """August 2026 exists in the data — for two days of it. That is an
        answer with a caveat, not a refusal."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"unit": "month", "year": 2026, "index": 8},
        )
        note = next(n for n in interpreted.notes if n.startswith("window_out_of_range"))
        assert "August 2026" in note and "2026-08-02" in note

    async def test_a_period_inside_the_data_says_nothing(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"unit": "month", "year": 2026, "index": 6},
        )
        assert not [n for n in interpreted.notes if n.startswith("window_out_of_range")]


class TestTimeGrain:
    """Regression: the "by month" guide chip returned one six-month scalar,
    no chart, and no warning that the grain had been dropped."""

    async def test_by_month_sets_a_monthly_time_bucket(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        from revi_kernel.refs import TimeBucket

        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denial_rate"],
            window={"quantity": "6", "unit": "month", "mode": "full_periods"},
            time_grain="month",
        )
        assert interpreted.spec.context.grain.time_bucket is TimeBucket.MONTH

    async def test_the_grain_reaches_the_probe(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        from revi_kernel.refs import TimeBucket

        interpreted = await _interpret(
            pack_port, catalog, metric_ids=["denial_rate"], time_grain="month"
        )
        plan = BuildInvestigationPlanService(pack_port, catalog).build(interpreted.spec)
        assert all(
            node.probe.grain.time_bucket is TimeBucket.MONTH for node in plan.nodes
        )

    async def test_no_time_grain_leaves_the_axis_alone(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(pack_port, catalog, metric_ids=["denial_rate"])
        assert interpreted.spec.context.grain.time_bucket is None


class TestAssertedMovementPlansAComparison:
    async def test_a_stated_movement_gets_a_baseline_and_says_so(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """"Why did denials double in July" names one window. Verifying the
        movement it asserts needs two, so the second is derived and
        disclosed rather than assumed silently."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            metric_ids=["denied_dollars"],
            dimension_ids=["payer"],
            window={"unit": "month", "year": 2026, "index": 7},
            direction="increase",
            direction_asserted=True,
        )
        assert interpreted.spec.direction_asserted is True
        comparison = interpreted.spec.context.comparison
        assert comparison is not None
        assert (comparison.window.range.start, comparison.window.range.end) == (
            date(2026, 6, 1),
            date(2026, 6, 30),
        )
        assert any(note.startswith("comparison_assumed") for note in interpreted.notes)
