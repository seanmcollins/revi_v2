"""The error text a default-mode user actually reads.

The live acceptance found the engine's own sentence on the wire verbatim:

    DATE_BASIS_INVALID: date basis 'remit' is not bound for entity 'claim'

Correct, recorded, and addressed to nobody who was asking. These tests pin
the three properties the fix has to keep together — plain language on top,
the code untouched, and the precision still reachable.
"""

from __future__ import annotations

import pytest

from revi_api.error_copy import (
    MODEL_SPEND_BUDGET,
    PLAIN_MESSAGES,
    WAREHOUSE_READ_BUDGET,
    budget_subcode,
    plain_message,
)
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
