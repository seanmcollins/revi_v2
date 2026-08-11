"""The error text a default-mode user actually reads.

The live acceptance found the engine's own sentence on the wire verbatim:

    DATE_BASIS_INVALID: date basis 'remit' is not bound for entity 'claim'

Correct, recorded, and addressed to nobody who was asking. These tests pin
the three properties the fix has to keep together — plain language on top,
the code untouched, and the precision still reachable.
"""

from __future__ import annotations

import re

import pytest

from revi_api.error_copy import (
    CLARIFICATION_NO_OPTIONS_WARNING,
    CLARIFICATION_OPTIONS_OFFERED_WARNING,
    MODEL_SPEND_BUDGET,
    PLAIN_MESSAGES,
    WAREHOUSE_READ_BUDGET,
    budget_subcode,
    clarification_frame_reason,
    clarification_reason_copy,
    clarification_register,
    plain_message,
)
from revi_api.warning_codes import structured_warnings
from revi_kernel.errors import ErrorCode

TECHNICAL = "date basis 'remit' is not bound for entity 'claim'"


def test_the_defect_verbatim_is_replaced_with_a_sentence_and_a_next_step() -> None:
    message = plain_message(ErrorCode.DATE_BASIS_INVALID, TECHNICAL)

    assert "entity" not in message
    assert "not bound" not in message
    assert "date basis" in message  # the concept survives, the jargon does not


def test_debug_mode_keeps_the_engines_own_words() -> None:
    """"Show me the working" is an existing setting, and this is working."""
    message = plain_message(ErrorCode.DATE_BASIS_INVALID, TECHNICAL, debug=True)

    assert TECHNICAL in message
    assert "DATE_BASIS_INVALID" in message
    assert message.startswith(PLAIN_MESSAGES[ErrorCode.DATE_BASIS_INVALID])


@pytest.mark.parametrize("code", sorted(PLAIN_MESSAGES, key=lambda c: c.value))
def test_every_mapped_message_reads_as_a_sentence_to_a_person(code: ErrorCode) -> None:
    """A sweep over the whole table, not one hand-checked entry.

    The failure mode this guards is a well-meant edit that pastes an
    engine string in as "plain": the code name, an underscored identifier,
    or a bare probe id is the tell.
    """
    message = PLAIN_MESSAGES[code]

    assert message[0].isupper() and message.rstrip().endswith(".")
    assert code.value not in message  # never the stable code as prose
    assert "probe '" not in message
    assert not any(token in message for token in ("entity_grain", "scope_dimensions", "__"))


def test_an_unmapped_code_says_the_engines_words_rather_than_something_vaguer() -> None:
    """Silence is not permission here either: a code nobody wrote copy for
    keeps the sentence that at least describes what happened."""

    class _Unmapped:
        value = "NOT_A_REAL_CODE"

    assert plain_message(_Unmapped(), TECHNICAL) == TECHNICAL  # type: ignore[arg-type]


def test_a_policy_refusal_keeps_naming_the_bound_it_crossed() -> None:
    """§7.1: out-of-bounds settings are refused *naming the bound*. Plain
    copy that dropped it would be a downgrade, not an improvement."""
    technical = "model_tier 'x' is not in this deployment's allowlist (claude-opus-5)"

    assert plain_message(ErrorCode.POLICY_DENIED, technical) == technical


def test_an_unresolvable_term_is_echoed_back_from_the_structured_details() -> None:
    """The one fact worth keeping from an UNSUPPORTED_CONCEPT is the term
    the analyst typed — being told *which* word failed is what makes the
    refusal actionable."""
    message = plain_message(
        ErrorCode.UNSUPPORTED_CONCEPT,
        "interpreted metric 'flurb_rate' is not in the pack",
        details={"metric": "flurb_rate"},
    )

    assert "flurb_rate" in message
    assert "pack" not in message  # the internal noun does not come with it


def test_internal_detail_keys_are_never_echoed() -> None:
    """``details`` also carries probe ids and entity names. Those are the
    jargon this module exists to keep out of the message."""
    message = plain_message(
        ErrorCode.UNSUPPORTED_CONCEPT,
        "no probe in the plan is answerable at the source",
        details={"probe": "main_2", "entity": "claim"},
    )

    assert "main_2" not in message
    assert "claim" not in message


class TestBudgetSubcodes:
    """QUERY_BUDGET_EXCEEDED was two failures wearing one code.

    A plan that would group too many cells and a turn that ran out of model
    spend both arrived as "narrow your question" — sending whoever read it
    after a spend stop off to rewrite a question that was never too wide.
    """

    def test_a_warehouse_read_stop_keeps_the_narrow_your_question_copy(self) -> None:
        details = {"probe": "main_1", "cells": 9000, "budget": 5000}
        assert (
            budget_subcode(ErrorCode.QUERY_BUDGET_EXCEEDED, details)
            == WAREHOUSE_READ_BUDGET
        )
        message = plain_message(
            ErrorCode.QUERY_BUDGET_EXCEEDED,
            "probe 'main_1' groups an estimated 9000 cells",
            details=details,
        )
        assert message == PLAIN_MESSAGES[ErrorCode.QUERY_BUDGET_EXCEEDED]

    def test_a_model_spend_stop_gets_its_own_sentence(self) -> None:
        details = {"provider": "claude_agent_sdk", "max_budget_usd": 0.5, "cost_usd": 0.51}
        assert (
            budget_subcode(ErrorCode.QUERY_BUDGET_EXCEEDED, details) == MODEL_SPEND_BUDGET
        )
        message = plain_message(
            ErrorCode.QUERY_BUDGET_EXCEEDED,
            "Claude Agent SDK call exceeded its per-call budget cap",
            details=details,
        )
        # It must NOT send the analyst to narrow a question that was fine.
        assert "warehouse" not in message
        assert "cost ceiling" in message or "model-spend" in message
        assert message != PLAIN_MESSAGES[ErrorCode.QUERY_BUDGET_EXCEEDED]

    def test_the_stable_code_is_untouched_by_the_split(self) -> None:
        """Clients branch on the §12 code; the subcode is additive."""
        assert ErrorCode.QUERY_BUDGET_EXCEEDED.value == "QUERY_BUDGET_EXCEEDED"
        assert budget_subcode(ErrorCode.DATE_BASIS_INVALID, {"cost_usd": 1}) is None

    def test_an_unattributable_budget_stop_defaults_to_the_readable_recovery(
        self,
    ) -> None:
        """With nothing to tell them apart, the guess is the one the
        analyst can act on and verify for themselves."""
        assert budget_subcode(ErrorCode.QUERY_BUDGET_EXCEEDED, None) == WAREHOUSE_READ_BUDGET

    def test_debug_mode_still_carries_the_engines_words_for_a_subcoded_stop(
        self,
    ) -> None:
        technical = "Claude Agent SDK call exceeded its per-call budget cap"
        message = plain_message(
            ErrorCode.QUERY_BUDGET_EXCEEDED,
            technical,
            debug=True,
            details={"max_budget_usd": 0.5},
        )
        assert technical in message and "QUERY_BUDGET_EXCEEDED" in message


class TestClarificationRegister:
    """A clarification is a successful outcome and there are two kinds.
    "Which AR view do you want — days in AR, aging distribution, or balance
    trend?" needs one answer; "I couldn't find a governed definition for
    that term" is a verdict. Both shipped in the same register, so the
    helpful one arrived under "There is no answerable option to offer
    here." """

    def test_a_question_with_options_is_neutral(self) -> None:
        sentence = clarification_register(
            "PREDICATE_VALUE_UNMATCHED: payer ['X'] not in the 12 values",
            ("Atlas Commercial", "State Medicaid"),
        )
        assert sentence == CLARIFICATION_OPTIONS_OFFERED_WARNING
        [payload] = structured_warnings([sentence])
        assert payload.code == "CLARIFICATION_OPTIONS_OFFERED"
        assert payload.severity == "info"

    def test_only_an_engine_declared_dead_end_is_loud(self) -> None:
        sentence = clarification_register(
            "no pack content matched the definitional lookup; CLARIFICATION_NO_OPTIONS", ()
        )
        assert sentence == CLARIFICATION_NO_OPTIONS_WARNING
        [payload] = structured_warnings([sentence])
        assert payload.code == "CLARIFICATION_NO_OPTIONS"
        assert payload.severity == "caution"

    def test_no_buttons_is_not_a_declaration_on_its_own(self) -> None:
        """A clarification may legitimately invite a free-text answer."""
        assert (
            clarification_register("model requested clarification", ())
            == CLARIFICATION_OPTIONS_OFFERED_WARNING
        )


class TestClarificationReasonCopy:
    """The reason is written for the trace: it leads with a code, carries
    machine pairs, and names operators and metric ids. Published verbatim,
    an analyst read "turn classification confidence 0.78" under a helpful
    question and "CLARIFICATION_SOLE_SURVIVOR" under a refusal."""

    @pytest.mark.parametrize(
        "reason",
        [
            "turn classification confidence 0.78",
            "referent resolution confidence 0.62",
            "model requested clarification; options_dropped=2",
        ],
    )
    def test_a_numeric_confidence_never_reaches_fine_print(self, reason: str) -> None:
        copy = clarification_reason_copy(reason)
        assert copy is None or "confidence 0." not in copy
        assert copy is None or "=" not in copy

    @pytest.mark.parametrize(
        "reason",
        [
            "CLARIFICATION_SOLE_SURVIVOR: one option left after value and plan validation",
            "CLARIFICATION_NOT_CONVERGING: 2 consecutive clarifications",
            "PLAYBOOK_TRANSFORM_UNAVAILABLE: dimension_scorecard answers by 'pivot'",
            "no pack content matched the definitional lookup; CLARIFICATION_NO_OPTIONS",
        ],
    )
    def test_no_internal_enum_or_id_reaches_fine_print(self, reason: str) -> None:
        copy = clarification_reason_copy(reason)
        if copy is None:
            return
        assert not re.search(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b", copy), copy
        assert not re.search(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", copy), copy

    def test_the_analysts_own_sentence_survives(self) -> None:
        copy = clarification_reason_copy(
            "PREDICATE_VALUE_UNMATCHED: payer ['UnitedHealthcare'] not in the 12 values "
            "this load holds"
        )
        assert copy is not None
        assert "not in the 12 values this load holds" in copy
        assert "PREDICATE_VALUE_UNMATCHED" not in copy

    def test_a_fragment_naming_our_machinery_is_dropped_not_published(self) -> None:
        """The fifth filter, and why it had to exist.

        "only one option survived the pack's filters" carries no enum token,
        no snake_case id, no confidence and no machine pair — it passed
        every guard this function had and published the word ``pack`` under
        a clarification, which is the vocabulary complaint that started
        ``docs/client-language.md``. Dropped rather than paraphrased: the
        fine print is optional, and guessing at what a fragment meant would
        publish a sentence nobody wrote.
        """
        for reason in (
            "CLARIFICATION_SOLE_SURVIVOR: only one option survived the pack's filters",
            "the governed contract does not bind here",
            "this probe was not run at this watermark",
        ):
            assert clarification_reason_copy(reason) is None, reason

    def test_operator_ids_are_translated_not_printed(self) -> None:
        copy = clarification_reason_copy(
            "AMBIGUOUS_REFINEMENT: drill_into takes exactly one referent, so choosing "
            "would be a guess"
        )
        assert copy is not None
        assert "drill_into" not in copy
        assert "rilling in" in copy  # capitalized when it leads the sentence

    def test_debug_keeps_every_byte(self) -> None:
        raw = "CLARIFICATION_SOLE_SURVIVOR: one left; options_dropped=2"
        assert clarification_reason_copy(raw, debug=True) == raw

    def test_an_all_internal_reason_publishes_nothing(self) -> None:
        assert clarification_reason_copy("CLARIFICATION_NO_OPTIONS") is None
        assert clarification_reason_copy(None) is None
        assert clarification_reason_copy("   ") is None


class TestClarificationFrameReason:
    """THE STREAM IS A DEFAULT SURFACE TOO.

    The copy discipline was applied where the terminal ``TurnResponse`` is
    assembled, and the intermediate ``clarification`` SSE frame — which is
    what a client renders the card from the instant a turn resolves —
    published ``ClarificationRequest.reason`` byte for byte. Live, a
    follow-up that could not be pinned to a shown figure printed *"referent
    resolution confidence 0.40"* under the question.
    """

    def test_a_model_confidence_never_reaches_the_frame(self) -> None:
        assert clarification_frame_reason("referent resolution confidence 0.40") is None

    def test_the_frame_and_the_terminal_payload_agree_on_copy(self) -> None:
        reason = (
            "PREDICATE_VALUE_UNMATCHED: payer ['UnitedHealthcare'] not in the 12 values "
            "this watermark holds"
        )
        assert clarification_frame_reason(reason) == clarification_reason_copy(reason)

    def test_the_shape_marker_survives_because_nobody_reads_it(self) -> None:
        """The one string this keeps, and the reason it is not copy.

        ``CLARIFICATION_NO_OPTIONS`` is the engine's declaration that it has
        nothing answerable to offer — the fact that decides whether the card
        is a question or a statement. The renderer strips it before display.
        Dropping it here would retire the refusal register silently.
        """
        assert (
            clarification_frame_reason("referent resolution confidence 0.40; CLARIFICATION_NO_OPTIONS")
            == "CLARIFICATION_NO_OPTIONS"
        )
        kept = clarification_frame_reason(
            "nothing in your definitions library matched that term; CLARIFICATION_NO_OPTIONS"
        )
        assert kept is not None
        assert kept.startswith("Nothing in your definitions library matched that term")
        assert kept.endswith("; CLARIFICATION_NO_OPTIONS")
        # And when the fragment names our machinery instead, the sentence
        # goes and the shape marker still does not: the card must stay in
        # the refusal register even with no fine print to show.
        assert (
            clarification_frame_reason(
                "no pack content matched the definitional lookup; CLARIFICATION_NO_OPTIONS"
            )
            == "CLARIFICATION_NO_OPTIONS"
        )

    def test_nothing_else_internal_rides_along_with_it(self) -> None:
        kept = clarification_frame_reason(
            "CLARIFICATION_SOLE_SURVIVOR: one left; options_dropped=2; CLARIFICATION_NO_OPTIONS"
        )
        # The leading code and the machine pair are gone; the sentence the
        # engine wrote for a reader survives, and so does the shape marker.
        assert kept == "One left; CLARIFICATION_NO_OPTIONS"
