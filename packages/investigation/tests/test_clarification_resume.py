"""Clarifications that RESUME the question they interrupted (round-3 R3-07)
and options that are checked before they are offered (R3-17).

The round-3 review found the clarification loop failing at both ends.

*At the answering end* (R3-07, P0): a reply sent on the dedicated
``clarification_response`` channel — byte-for-byte an option the platform
had just offered — was flattened into an utterance at the API boundary,
re-classified by a model as ``refinement`` at confidence 0.45, and saved as
a ROOT investigation carrying only the reply text. The analyst's question
was gone, and the lineage was a forest of disconnected replies. On the
flagship filing-runway path the same turn was spent asking a question with
exactly ONE possible answer: *"'timely_filing_at_risk_dollars' cannot be
read on the 'submission' date basis here — the contract and this warehouse
between them leave 'service' at the claim grain. Which should I use?"*

*At the offering end* (R3-17): the value-existence guard that produces this
platform's best refusal was never applied to the options the platform
OFFERS. It checked a dimension's DECLARED ``value_domain`` and skipped the
open ones — ``payer``, ``plan``, ``facility`` — so "Summit Peak is a
facility" was offered over a warehouse holding six facilities, none of them
that.

Both are closed by the same structure: an option carries the governed ids
it stands for (:class:`ClarificationBinding`), which makes it *checkable*
before it is offered and *applicable* when it comes back.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from revi_investigation.application.interpretation import PendingClarification
from revi_investigation.application.submit_turn import (
    _bindings_from_trace,
    _with_chosen_values,
    drop_refuted_options,
)
from revi_investigation.domain.turns import ClarificationBinding, ClarificationRequest
from revi_kernel.filters import EMPTY_SCOPE, Predicate, PredicateOp, iter_predicates
from revi_kernel.refs import DimensionRef

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
    all carry their meaning. Round-3 R3-07's evidence names the first one;
    the other two are the same defect on the other two recovery paths."""

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
