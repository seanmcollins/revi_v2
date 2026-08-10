"""Clarification options: checkable before they are offered, and resumable
when they come back.

Both ends of the loop were broken. At the offering end the value-existence
guard skipped dimensions with an open value domain, so options named
facilities the warehouse does not hold. At the answering end a reply sent on
the ``clarification_response`` channel was flattened into an utterance,
re-classified as a refinement and saved as a ROOT investigation — losing the
analyst's question, window and filters. Both close through
:class:`ClarificationBinding`: an option carries the governed ids it stands
for, which makes it checkable before it is offered and applicable when it
comes back.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.interpretation import PendingClarification
from revi_investigation.application.submit_turn import (
    SubmitTurnRequest,
    _bindings_from_trace,
    _with_binding,
    _with_chosen_values,
    _with_resumed_context,
    drop_refuted_options,
)
from revi_investigation.domain.turns import ClarificationBinding, ClarificationRequest
from revi_kernel.filters import EMPTY_SCOPE, Predicate, PredicateOp, iter_predicates
from revi_kernel.refs import DimensionRef
from revi_kernel.scope import AbsoluteRange
from revi_testing.engine_wiring import WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

BASIS_OPTION = "Use the service date basis"


def _basis_clarification() -> ClarificationRequest:
    """The live DATE_BASIS_INVALID_RECOVERABLE clarification, verbatim."""
    return ClarificationRequest(
        question=(
            "'timely_filing_at_risk_dollars' cannot be read on the 'submission' date "
            "basis here — the contract and this warehouse between them leave 'service' "
            "at the claim grain. Which should I use?"
        ),
        options=(BASIS_OPTION,),
        reason=(
            "DATE_BASIS_INVALID_RECOVERABLE: timely_filing_at_risk_dollars cannot be "
            "read on the 'submission' date basis; 1 bound alternative(s) offered"
        ),
        bindings=(
            ClarificationBinding(
                option=BASIS_OPTION,
                kind="date_basis",
                metric_ids=("timely_filing_at_risk_dollars",),
                basis="service",
            ),
        ),
    )


class TestOptionsCarryTheirMeaning:
    def test_a_reply_matching_an_option_resolves_by_lookup(self) -> None:
        binding = _basis_clarification().binding_for(BASIS_OPTION)
        assert binding is not None
        assert binding.kind == "date_basis"
        assert binding.basis == "service"

    @pytest.mark.parametrize(
        "reply",
        [
            BASIS_OPTION,
            BASIS_OPTION.upper(),
            f"  {BASIS_OPTION}  ",
            f"{BASIS_OPTION}.",
            "Use  the   service  date  basis",
        ],
    )
    def test_matching_forgives_only_what_is_not_a_different_choice(
        self, reply: str
    ) -> None:
        """Case, surrounding space, inner runs of space and a trailing full
        stop are how a reply gets retyped or pasted; none of them names a
        different basis. Anything beyond that is a different sentence and
        falls through to being read as language."""
        assert _basis_clarification().binding_for(reply) is not None

    def test_a_different_sentence_does_not_match(self) -> None:
        assert _basis_clarification().binding_for("use the posting date") is None

    def test_only_platform_derived_bindings_may_be_applied_unasked(self) -> None:
        """A model's proposal is a suggestion and stays one: committing to
        it without the analyst is a bigger step than committing to the one
        date basis a contract and a warehouse leave between them."""
        assert ClarificationBinding(option="x", kind="date_basis").deterministic
        assert ClarificationBinding(option="x", kind="metric_cut").deterministic
        assert not ClarificationBinding(option="x", kind="grounded_option").deterministic
        assert not ClarificationBinding(option="x", kind="predicate_value").deterministic


class TestBindingsSurviveTheRoundTrip:
    """A turn is a stateless request and the session may resume in another
    process, so the bindings are read back off the trace — the same way the
    pending clarification itself is."""

    def test_a_recorded_binding_rebuilds_whole(self) -> None:
        payload = {
            "clarification_bindings": [
                {
                    "option": BASIS_OPTION,
                    "kind": "date_basis",
                    "metric_ids": ["timely_filing_at_risk_dollars"],
                    "dimension_ids": [],
                    "playbook_id": None,
                    "scope": [{"dimension": "payer", "values": ["Federal Medicare"]}],
                    "basis": "service",
                }
            ]
        }
        (binding,) = _bindings_from_trace(payload)
        assert binding.option == BASIS_OPTION
        assert binding.metric_ids == ("timely_filing_at_risk_dollars",)
        assert binding.scope == (("payer", ("Federal Medicare",)),)
        assert binding.basis == "service"

    def test_a_trace_written_before_bindings_existed_yields_nothing(self) -> None:
        """Not an error: the reply falls back to being read as text, which
        is exactly the behaviour that predates this field."""
        assert _bindings_from_trace({"clarification": "which one?"}) == ()

    def test_the_pending_clarification_carries_the_asking_investigation(self) -> None:
        pending = PendingClarification(
            question="which payer?",
            options=("Federal Medicare",),
            original_question="What is the denial rate for UnitedHealthcare?",
            streak=1,
            investigation_id="inv_asked",
            bindings=(
                ClarificationBinding(
                    option="Federal Medicare",
                    kind="predicate_value",
                    scope=(("payer", ("Federal Medicare",)),),
                ),
            ),
        )
        assert pending.investigation_id == "inv_asked"
        assert pending.binding_for("federal medicare") is not None


class TestDroppingAnOptionDropsItsMeaning:
    def test_a_refuted_option_takes_its_binding_with_it(self) -> None:
        """A binding left behind for an option nobody can see is a
        resolution the analyst never chose."""
        clarification = ClarificationRequest(
            question="which payer?",
            options=("UnitedHealthcare", "Federal Medicare"),
            reason="PREDICATE_VALUE_UNMATCHED: payer ['x'] not in the 12 values",
            bindings=(
                ClarificationBinding(option="UnitedHealthcare", kind="predicate_value"),
                ClarificationBinding(option="Federal Medicare", kind="predicate_value"),
            ),
        )
        kept = drop_refuted_options(clarification, frozenset({"unitedhealthcare"}))
        assert kept.options == ("Federal Medicare",)
        assert [b.option for b in kept.bindings] == ["Federal Medicare"]

    def test_dropping_every_option_leaves_no_bindings(self) -> None:
        clarification = ClarificationRequest(
            question="which payer?",
            options=("UnitedHealthcare",),
            reason="PREDICATE_VALUE_UNMATCHED",
            bindings=(
                ClarificationBinding(option="UnitedHealthcare", kind="predicate_value"),
            ),
        )
        kept = drop_refuted_options(clarification, frozenset({"unitedhealthcare"}))
        assert kept.options == ()
        assert kept.bindings == ()


class TestChosenValuesAreSubstitutedNotAppended:
    """The clarification exists because the value in the question does not
    exist in the data. Joining the reply onto the sentence and
    re-interpreting it leaves the refuted value in the question — live, that
    came straight back as the SAME refusal, on the same session, for a
    second turn's cost.
    """

    def test_the_refuted_value_is_replaced(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(
            measures=("denial_rate",),
            scope=Predicate(
                dimension=DimensionRef("payer"),
                op=PredicateOp.EQ,
                values=("UnitedHealthcare",),
            ),
        )
        resumed = _with_chosen_values(spec, (("payer", ("Federal Medicare",)),))
        values = {
            p.dimension.id: p.values for p in iter_predicates(resumed.context.scope)
        }
        assert values["payer"] == ("Federal Medicare",)

    def test_a_dimension_the_model_dropped_is_re_added(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """The choice is the analyst's and must survive a second reading of
        their sentence, which may not mention the value at all."""
        spec = make_spec(measures=("denial_rate",), scope=EMPTY_SCOPE)
        resumed = _with_chosen_values(spec, (("payer", ("Federal Medicare",)),))
        values = {
            p.dimension.id: p.values for p in iter_predicates(resumed.context.scope)
        }
        assert values == {"payer": ("Federal Medicare",)}

    def test_other_dimensions_are_untouched(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(
            measures=("denial_rate",),
            scope=Predicate(
                dimension=DimensionRef("service_line"),
                op=PredicateOp.EQ,
                values=("Imaging",),
            ),
        )
        resumed = _with_chosen_values(spec, (("payer", ("Federal Medicare",)),))
        values = {
            p.dimension.id: p.values for p in iter_predicates(resumed.context.scope)
        }
        assert values["service_line"] == ("Imaging",)
        assert values["payer"] == ("Federal Medicare",)

    def test_no_choice_leaves_the_spec_identical(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=("denial_rate",))
        assert _with_chosen_values(spec, ()) is spec


class TestTheValidatorsClarificationsAreBound:
    """The three deterministic clarifications this platform derives itself
    all carry their meaning. The date-basis one is where the defect was
    found; the other two are the same defect on the other recovery paths."""

    def test_a_basis_alternative_binds_its_basis(self) -> None:
        clarification = _basis_clarification()
        assert len(clarification.bindings) == len(clarification.options)
        assert clarification.bindings[0].basis == "service"

    def test_an_option_and_its_binding_are_matched_by_text_not_position(self) -> None:
        """Positional pairing would silently rebind a surviving option to a
        dropped one's meaning the first time anything filtered the list."""
        clarification = replace(
            _basis_clarification(),
            options=("Use the posting date basis", BASIS_OPTION),
        )
        binding = clarification.binding_for("Use the posting date basis")
        assert binding is None
        assert clarification.binding_for(BASIS_OPTION) is not None


# ---------------------------------------------------------------------------
# The resume keeps the analyst's context
#
# ``_apply_binding``'s fall-through branch skipped interpretation outright and
# ran the spec ``_spec_for_binding`` builds for a DRY RUN — whose window is the
# widest one the load admits, whose scope is the option's alone, and whose
# parent is ``None`` by construction. A July question answered with the
# platform's own offered option came back measured over the whole warehouse,
# under a disclosure asserting the July question had been resumed; a drill on a
# payer / service-line thread came back as a root investigation with no
# filters. The two branches that called ``_new_investigation_turn`` were
# already correct, so the invariant is stated over EVERY binding kind rather
# than over the two that happened to be right.

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

_needs_warehouse = pytest.mark.skipif(
    not WAREHOUSE.is_file(),
    reason="generated warehouse missing — run: "
    "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
)

#: A question that names its period out loud, which is the whole point:
#: nothing about the answer's window is a guess.
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


@pytest.mark.reference
@_needs_warehouse
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


@pytest.mark.reference
@_needs_warehouse
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


@pytest.mark.reference
@_needs_warehouse
class TestTheInterruptedThreadsContextIsCarried:
    """The resume must not land as a root investigation over the whole
    warehouse when a thread was already on screen."""

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
