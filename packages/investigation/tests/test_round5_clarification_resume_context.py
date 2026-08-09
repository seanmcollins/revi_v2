"""Round-5 A-01: a clarification resume keeps the analyst's context.

Two adversaries, four independent sessions, one line. ``_apply_binding``'s
fall-through branch skipped interpretation outright and ran the spec
``_spec_for_binding`` builds for a DRY RUN — whose window is the widest one
the load admits, whose scope is the option's alone, and whose parent is
``None`` by construction. Live:

* *"How many dollars did we lose to denials in July 2026? I need the number
  for my board deck"* → the platform's own excellent clarification → the
  platform's own offered option → ``denied dollars: $25,929,558.84
  (2023-01-01..2026-08-02)``, confidence ``high``, under a
  ``CLARIFICATION_ANSWER_APPLIED`` disclosure asserting the July question
  had been resumed. June and July returned byte-identical numbers.
* *"Break that down by CARC code."* on a payer / service-line / July thread
  → ``class new_investigation``, ``parent None``, ``filters: []``,
  ``cohort: null``, and prose reading "Reconciliation does not apply here:
  this is a first turn" on turn two of a thread.

The control passed on both: the ``date_basis`` and ``predicate_value``
branches call ``_new_investigation_turn`` and preserve everything. So the
invariant is stated over EVERY binding kind rather than over the two that
happened to be right — a resume that names a period is measured over that
period, whichever kind of option carried it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.submit_turn import (
    SubmitTurnRequest,
    _with_binding,
    _with_resumed_context,
)
from revi_investigation.domain.turns import ClarificationBinding
from revi_kernel.filters import Predicate, PredicateOp, iter_predicates
from revi_kernel.refs import DimensionRef
from revi_kernel.scope import AbsoluteRange
from revi_testing.engine_wiring import WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not WAREHOUSE.is_file(),
        reason="generated warehouse missing — run: "
        "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
    ),
]

#: The exec's question, verbatim. It names its period out loud, which is
#: the whole point: nothing about the answer's window is a guess.
QUESTION = "How many dollars did we lose to denials in July 2026? I need the number for my board deck"
JULY = (date(2026, 7, 1), date(2026, 7, 31))

OPTION = "Billed dollars denied in July 2026"

#: One option per binding kind, all naming content this warehouse holds so
#: the funnel's dry run keeps every one of them.
KINDS: dict[str, dict[str, Any]] = {
    "grounded_option": {},
    "metric_cut": {"dimension_ids": ["payer"]},
    "predicate_value": {"scope": [{"dimension": "payer", "op": "eq", "values": ["Atlas Commercial"]}]},
    "date_basis": {"basis": "remit"},
}


def _july_interpretation(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "intent_summary": "denied dollars in July 2026",
        "metric_ids": ["denied_dollars"],
        "dimension_ids": [],
        "concept_ids": [],
        "playbook_id": None,
        "window": {"start": "2026-07-01", "end": "2026-07-31"},
        "basis": None,
        "comparison": None,
        "scope": [],
        "clarification": None,
        "clarification_options": [],
        "definitional_terms": [],
    }
    payload.update(over)
    return payload


def _clarification(kind: str) -> dict[str, Any]:
    """The interpreter's first reading: ambiguous, with one grounded option.

    The option carries the metric ids for every kind, because that is what
    makes it dry-runnable — the kind is what the engine keys its resume
    branch off.
    """
    option: dict[str, Any] = {
        "label": OPTION,
        "metric_ids": ["denied_dollars"],
        "dimension_ids": [],
        "playbook_id": None,
        "scope": [],
    }
    option.update({k: v for k, v in KINDS[kind].items() if k != "basis"})
    return _july_interpretation(
        metric_ids=[],
        window=None,
        clarification='"Lost to denials" can mean two different numbers — which one?',
        clarification_options=[option],
    )


def _engine(kind: str) -> WiredEngine:
    llm = MockLanguageModel()
    llm.respond("classify_turn", {"turn_class": "new_investigation", "confidence": 0.99})
    llm.respond(
        "classify_turn",
        {"turn_class": "clarification_response", "confidence": 0.99},
    )
    # The resume re-reads the analyst's sentence, which still names July —
    # matched on the joined utterance so the first reading stays ambiguous.
    llm.respond("interpret_question", _july_interpretation(), matcher=lambda p: OPTION in p)
    llm.respond("interpret_question", _clarification(kind))
    return build_duckdb_engine(warehouse_path=WAREHOUSE, llm=llm)


@pytest.mark.parametrize("kind", sorted(KINDS))
class TestEveryBindingKindResumesTheAnalystsPeriod:
    async def test_the_resumed_window_equals_the_period_the_question_named(
        self, kind: str
    ) -> None:
        engine = _engine(kind)
        asked = await engine.submit.submit(SubmitTurnRequest(tenant="demo", question=QUESTION))
        assert asked.clarification is not None, "fixture: turn one must clarify"
        assert OPTION in asked.clarification.options

        resumed = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question=OPTION,
                session_id=asked.session.id,
                clarification_response=True,
            )
        )
        assert resumed.clarification is None, resumed.clarification
        window = resumed.investigation.spec.context.window.range
        assert (window.start, window.end) == JULY, (
            f"{kind}: resumed over {window.start}..{window.end}, not the July the "
            "question named"
        )

    async def test_the_resume_is_a_child_of_the_turn_that_asked(self, kind: str) -> None:
        engine = _engine(kind)
        asked = await engine.submit.submit(SubmitTurnRequest(tenant="demo", question=QUESTION))
        resumed = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question=OPTION,
                session_id=asked.session.id,
                clarification_response=True,
            )
        )
        assert resumed.investigation.parent_id == asked.investigation.id


class TestTheOptionsIdsWinOverASecondReading:
    """A tapped option is not a suggestion a model may re-litigate."""

    def test_metrics_and_cuts_are_pinned(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=("denied_dollars",))
        binding = ClarificationBinding(
            option="x", kind="metric_cut", metric_ids=("denial_rate",), dimension_ids=("payer",)
        )
        pinned = _with_binding(spec, binding)
        assert [m.id for m in pinned.measures] == ["denial_rate"]
        assert [d.id for d in pinned.dimensions] == ["payer"]

    def test_what_the_option_is_silent_about_survives(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=("denied_dollars",), dimensions=("payer",))
        pinned = _with_binding(
            spec, ClarificationBinding(option="x", kind="grounded_option", metric_ids=("denial_rate",))
        )
        assert [d.id for d in pinned.dimensions] == ["payer"]

    def test_no_binding_is_a_no_op(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=("denied_dollars",))
        assert _with_binding(spec, None) is spec


class TestTheInterruptedThreadsContextIsCarried:
    """adonis's half: the resume must not land as a root investigation over
    the whole warehouse when a thread was already on screen."""

    def _thread(self, make_spec):  # type: ignore[no-untyped-def]
        spec = make_spec(
            measures=("denial_rate",),
            scope=Predicate(
                dimension=DimensionRef("payer"),
                op=PredicateOp.EQ,
                values=("Atlas Commercial",),
            ),
        )
        return spec.with_context(
            replace(
                spec.context,
                window=replace(
                    spec.context.window,
                    range=AbsoluteRange(start=JULY[0], end=JULY[1]),
                    requested=None,
                ),
            )
        )

    def test_an_unstated_window_comes_from_the_thread(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        fresh = make_spec(measures=("denial_rate",))
        carried, explicit, notes = _with_resumed_context(
            fresh, self._thread(make_spec), window_explicit=False
        )
        window = carried.context.window.range
        assert (window.start, window.end) == JULY
        assert explicit is True
        assert any(note.startswith("resumed_context:") for note in notes)

    def test_a_window_the_resume_stated_itself_wins(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        fresh = make_spec(measures=("denial_rate",))
        stated = fresh.context.window.range
        carried, _, _ = _with_resumed_context(
            fresh, self._thread(make_spec), window_explicit=True
        )
        assert carried.context.window.range == stated

    def test_the_threads_filters_ride_along_and_are_disclosed(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        fresh = make_spec(measures=("denial_rate",))
        carried, _, notes = _with_resumed_context(
            fresh, self._thread(make_spec), window_explicit=False
        )
        dimensions = {p.dimension.id for p in iter_predicates(carried.context.scope)}
        assert "payer" in dimensions
        assert any("filters the interrupted thread" in note for note in notes)

    def test_a_dimension_the_resume_rescoped_is_never_widened_back(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        fresh = make_spec(measures=("denial_rate",))
        rescoped = fresh.with_context(
            replace(
                fresh.context,
                scope=Predicate(
                    dimension=DimensionRef("payer"),
                    op=PredicateOp.EQ,
                    values=("Federal Medicare",),
                ),
            )
        )
        carried, _, _ = _with_resumed_context(
            rescoped, self._thread(make_spec), window_explicit=False
        )
        values = {
            value
            for p in iter_predicates(carried.context.scope)
            if p.dimension.id == "payer"
            for value in p.values
        }
        assert values == {"Federal Medicare"}

    def test_no_thread_is_a_no_op(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        fresh = make_spec(measures=("denial_rate",))
        carried, explicit, notes = _with_resumed_context(fresh, None, window_explicit=False)
        assert carried is fresh and explicit is False and notes == []
