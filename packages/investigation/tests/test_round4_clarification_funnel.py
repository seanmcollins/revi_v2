"""Round-4 R4-12: every clarification leaves through one funnel.

Five personas found six defects in this subsystem, and each one lived in a
path that skipped a step some other path took: empty option arrays reached
the page twice, an option the platform's own policy had just refused was
offered and then abandoned the subject entirely, the documented convergence
rule never fired, a dry-run that left exactly one option asked about it
anyway ($0.146 and a turn), and a reply spliced the question from one
pending clarification onto the parent pointer of another.

These are the pure decisions inside that funnel. The dialogue-level
behaviour is exercised in ``test_clarification_options_validated`` and
``test_clarification_convergence``.
"""

from __future__ import annotations

from revi_investigation.application.interpretation import PendingClarification
from revi_investigation.application.submit_turn import (
    NO_OPTIONS_REASON,
    SubmitTurnService,
    _answers_pending,
    _no_options_card,
)
from revi_investigation.domain.turns import ClarificationBinding, ClarificationRequest


def _binding(option: str, kind: str = "grounded_option", **kw: object) -> ClarificationBinding:
    return ClarificationBinding(option=option, kind=kind, **kw)  # type: ignore[arg-type]


class TestAQuestionWithOneAnswerIsNotAQuestion:
    """Defect 5. ``_lone_binding`` required a binding the PLATFORM derived,
    so it was reachable only from the validator's refusal path: a model
    clarification whose options a dry-run had reduced to one still charged
    a turn to ask about the survivor."""

    def test_a_derived_lone_option_still_applies(self) -> None:
        binding = _binding("Read it on the service basis", kind="date_basis", basis="service")
        clarification = ClarificationRequest(
            question="Which basis?", options=(binding.option,), bindings=(binding,)
        )
        assert SubmitTurnService._lone_binding(clarification) is binding

    def test_a_survivor_of_validation_applies_too(self) -> None:
        binding = _binding("Walk through the spike at Northgate Regional Hospital")
        clarification = ClarificationRequest(
            question="Which facility?", options=(binding.option,), bindings=(binding,)
        )
        # authored as one option by a model: still a real question
        assert SubmitTurnService._lone_binding(clarification) is None
        # reduced to one by this platform: not a question any more
        assert SubmitTurnService._lone_binding(clarification, reduced=True) is binding

    def test_two_options_are_always_asked(self) -> None:
        bindings = (_binding("A"), _binding("B"))
        clarification = ClarificationRequest(
            question="Which?", options=("A", "B"), bindings=bindings
        )
        assert SubmitTurnService._lone_binding(clarification, reduced=True) is None


class TestAnOptionlessClarificationSaysSo:
    """Defect 1. ``ClarificationPrompt`` maps over the options array, so an
    empty one renders as a question above a blank row of buttons — reached
    live in two independent sessions, one of them after $0.10 spent to deny
    a capability."""

    def test_the_card_is_marked_and_the_original_reason_still_leads(self) -> None:
        card = _no_options_card(
            ClarificationRequest(question="q", reason="CONTEXT_CONFLICT: x")
        )
        assert card.reason is not None
        assert card.reason.startswith("CONTEXT_CONFLICT")  # readers key off this
        assert NO_OPTIONS_REASON in card.reason

    def test_a_clarification_with_options_is_untouched(self) -> None:
        original = ClarificationRequest(question="q", options=("a", "b"), reason="r")
        assert _no_options_card(original) is original


class TestAReplyThatAnswersNothingIsNotAnAnswer:
    """Defect 6b. A genuinely new question was swallowed as a clarification
    answer, spliced onto the abandoned one, under a false
    ``CLARIFICATION_ANSWER_APPLIED`` disclosure."""

    PENDING = PendingClarification(
        question="Which facility did you mean?",
        options=("Northgate Regional Hospital", "Riverside Surgical Center"),
        original_question="Walk me through the denial spike",
        streak=1,
        investigation_id="inv_1",
        bindings=(_binding("Northgate Regional Hospital"),),
    )

    def test_an_offered_option_answers_it(self) -> None:
        assert _answers_pending("Northgate Regional Hospital", self.PENDING) is True

    def test_a_fragment_answers_it(self) -> None:
        assert _answers_pending("just the imaging service line", self.PENDING) is True
        assert _answers_pending("the last full month", self.PENDING) is True

    def test_a_self_contained_question_does_not(self) -> None:
        assert _answers_pending(
            "which payers are costing us the most money right now?", self.PENDING
        ) is False
        assert _answers_pending("show me days in A/R by payer", self.PENDING) is False

    def test_silence_is_not_treated_as_a_new_question(self) -> None:
        assert _answers_pending("   ", self.PENDING) is True
