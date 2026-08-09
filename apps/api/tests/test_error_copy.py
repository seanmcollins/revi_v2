"""The error text a default-mode user actually reads.

The live acceptance found the engine's own sentence on the wire verbatim:

    DATE_BASIS_INVALID: date basis 'remit' is not bound for entity 'claim'

Correct, recorded, and addressed to nobody who was asking. These tests pin
the three properties the fix has to keep together — plain language on top,
the code untouched, and the precision still reachable.
"""

from __future__ import annotations

import pytest

from revi_api.error_copy import PLAIN_MESSAGES, plain_message
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
