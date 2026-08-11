"""Every clarification leaves through one funnel.

Each defect pinned here lived in a path that skipped a step some other path
took: empty option arrays reached the page, an option the platform's own
policy had just refused was offered, a dry run that left exactly one option
asked about the survivor anyway, a reply spliced the question from one
pending clarification onto the parent pointer of another, a thread filter was
carried onto the very cut it asked about, and options were offered for cuts
and playbooks this engine cannot run. These are the pure decisions inside the
funnel; the dialogue-level behaviour is exercised in
``test_clarification_options_validated`` and ``test_clarification_convergence``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.interpretation import PendingClarification
from revi_investigation.application.submit_turn import (
    _ASKS_WHICH_MEASURE,
    ASKS_WHETHER_TO_PIN,
    CLARIFICATION_SOLE_SURVIVOR_REASON,
    ENTITY_SUPERLATIVE,
    NO_OPTIONS_REASON,
    SubmitTurnService,
    _answers_pending,
    _no_options_card,
    _state_the_survivor,
    _with_resumed_context,
    cuts_an_entity_axis,
)
from revi_investigation.application.validation import PlanValidationService
from revi_investigation.domain.turns import ClarificationBinding, ClarificationRequest
from revi_kernel.filters import Predicate, PredicateOp, iter_predicates
from revi_kernel.refs import DimensionRef
from revi_testing.engine_wiring import PackSnapshotPort


def _binding(option: str, kind: str = "grounded_option", **kw: object) -> ClarificationBinding:
    return ClarificationBinding(option=option, kind=kind, **kw)  # type: ignore[arg-type]


class TestAQuestionWithOneAnswerIsNotAQuestion:
    """``_lone_binding`` required a binding the PLATFORM derived, so it was
    reachable only from the validator's refusal path: a model clarification
    whose options a dry run had reduced to one still charged a turn to ask
    about the survivor."""

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
    """``ClarificationPrompt`` maps over the options array, so an empty one
    renders as a question above a blank row of buttons — reached live in two
    independent sessions."""

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
    """A genuinely new question was swallowed as a clarification answer,
    spliced onto the abandoned one, under a false
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


# ---------------------------------------------------------------------------
# What may be offered, and what a collapse to one option means


@pytest.fixture(name="validator")
def validator_fixture(
    catalog: CatalogSnapshot, pack_port: PackSnapshotPort
) -> PlanValidationService:
    """The plan validator, for the checks that read no warehouse.

    ``unexecutable_cut`` is pure catalog + pack — it answers "can this
    metric be cut that way" from two snapshots — so the repository is never
    reached and is not wired.
    """
    return PlanValidationService(catalog, pack_port, repository=cast(Any, None))


#: The option the live session collapsed to and then RAN, unasked.
SURVIVOR = "Show days in A/R for July 2026"


def _scorecard_refusal() -> ClarificationRequest:
    return ClarificationRequest(
        question=(
            "I can't build a payer scorecard: this pack has no playbook that composes "
            "denials, collections and A/R into one view."
        ),
        options=(SURVIVOR,),
        reason="PLAYBOOK_TRANSFORM_UNAVAILABLE: scorecard",
        bindings=(
            ClarificationBinding(
                option=SURVIVOR, kind="grounded_option", metric_ids=("days_in_ar",)
            ),
        ),
    )


class TestACollapseToOneOptionIsStatedNeverSelected:
    """regression: the same utterance that clarified correctly in a clean
    session answered outright in a session with prior context — sole finding
    "Atlas Commercial: 179.5 days in ar", a payer the turn never named, with
    the refusal demoted into a disclosure saying it "was applied rather than
    asked about"."""

    def test_the_refusal_keeps_the_lead(self) -> None:
        clarification = _scorecard_refusal()
        stated = _state_the_survivor(clarification, clarification.bindings[0])
        assert stated.question.startswith("I can't build a payer scorecard")

    def test_the_survivor_is_named_and_not_run(self) -> None:
        clarification = _scorecard_refusal()
        stated = _state_the_survivor(clarification, clarification.bindings[0])
        assert SURVIVOR in stated.question
        assert "I have not run it on your behalf" in stated.question
        assert stated.options == (SURVIVOR,)
        assert CLARIFICATION_SOLE_SURVIVOR_REASON in (stated.reason or "")

    def test_the_original_reason_survives_for_every_other_reader(self) -> None:
        clarification = _scorecard_refusal()
        stated = _state_the_survivor(clarification, clarification.bindings[0])
        assert (stated.reason or "").startswith("PLAYBOOK_TRANSFORM_UNAVAILABLE")


class TestAThreadFilterIsNeverCarriedOntoTheCutItAsksAbout:
    """regression: the resumed context carried ``payer eq [Atlas Commercial]``
    onto a turn that asked for a scorecard ACROSS payers."""

    def test_a_filter_on_the_dimension_being_cut_by_is_not_inherited(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        thread = make_spec(
            measures=("days_in_ar",),
            scope=Predicate(
                dimension=DimensionRef("payer"),
                op=PredicateOp.EQ,
                values=("Atlas Commercial",),
            ),
        )
        asked = make_spec(measures=("days_in_ar",), dimensions=("payer",))

        resumed, _, notes = _with_resumed_context(asked, thread, True)

        assert not [
            p for p in iter_predicates(resumed.context.scope) if p.dimension.id == "payer"
        ]
        assert any("is NOT carried" in note for note in notes)

    def test_a_filter_on_another_dimension_is_still_inherited(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        thread = make_spec(
            measures=("denial_rate",),
            scope=Predicate(
                dimension=DimensionRef("service_line"),
                op=PredicateOp.EQ,
                values=("Imaging",),
            ),
        )
        asked = make_spec(measures=("denial_rate",), dimensions=("payer",))

        resumed, _, notes = _with_resumed_context(asked, thread, True)

        assert [p.dimension.id for p in iter_predicates(resumed.context.scope)] == [
            "service_line"
        ]
        assert any("are carried onto" in note for note in notes)


class TestEveryOfferedOptionIsOneTheEngineCanRun:
    """regression: "Why did it go up?" burned three turns and fired the
    circuit breaker on the product's own suggestion —
    ``GRAIN_INCOMPATIBLE_RECOVERABLE: denial_category is not a scope dimension
    of denial_rate``, a breakdown the engine knew it cannot run."""

    def test_a_cut_the_metric_does_not_declare_is_refused_before_offer(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        assert (
            validator.unexecutable_cut(
                "Yes — re-group the figure F1 result by denial reason", ("denial_rate",)
            )
            is not None
        )

    def test_a_legal_cut_survives(self, validator) -> None:  # type: ignore[no-untyped-def]
        assert (
            validator.unexecutable_cut("Break denial rate down by payer", ("denial_rate",))
            is None
        )

    def test_a_platform_recovery_chip_is_not_a_query(self, validator) -> None:  # type: ignore[no-untyped-def]
        assert (
            validator.unexecutable_cut(
                "Raise the cost ceiling for one question", ("denial_rate",)
            )
            is None
        )

    def test_an_option_naming_both_a_legal_and_an_illegal_cut_survives(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        """One-sided on purpose: the option is answerable, the composer was
        imprecise, and dropping it would cost the analyst a real route."""
        assert (
            validator.unexecutable_cut(
                "Break denial rate down by payer and denial category", ("denial_rate",)
            )
            is None
        )

    def test_the_scorecard_option_is_offered_because_the_scorecard_runs(
        self, validator
    ) -> None:
        """The live option verbatim, and it is now a GOOD one.

        "Who is my worst payer?" offered "Run a full payer scorecard across
        all measures", and asking for that used to return
        ``PLAYBOOK_TRANSFORM_UNAVAILABLE: payer_scorecard answers by
        'pivot'`` — a button the engine had already decided it could not
        press. The scorecard's answering transform is implemented, so the
        button reaches an answer and this guard has nothing to withhold.

        The guard itself is unchanged and still fires for the forecast
        (below); what changed is the pack's capability, which is exactly the
        shape this list was documented to have — "the day either is
        implemented, it comes off this list and the playbooks answer".
        """
        assert (
            validator.unanswerable_playbook("Run a full payer scorecard across all measures")
            is None
        )

    def test_the_hero_chip_advertising_an_unimplemented_forecast_is_caught(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        """``cash_outlook`` answers by ``project_lagged_realization``, which
        this engine does not implement, and the chip is on the hero."""
        assert validator.unanswerable_playbook("Will my cash increase next month?") == (
            "cash_outlook",
            "project_lagged_realization",
        )

    def test_a_playbook_this_engine_CAN_answer_survives(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        assert validator.unanswerable_playbook("Show me AR aging") is None
        assert validator.unanswerable_playbook("Break denial rate down by payer") is None

    def test_an_option_naming_a_measure_is_a_direct_query_however_it_is_phrased(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        """One-sided, exactly like ``unexecutable_cut``. ``payer_scorecard``
        declares the trigger "rank payers", and "Rank payers by denial rate"
        is a question this engine answers in one probe — dropping it would
        cost the analyst a real route to keep a rule tidy."""
        for option in (
            "Rank payers by denial rate",
            "Score each payer on days_in_ar",
            "Payer scorecard: just the denial rate column",
        ):
            assert validator.unanswerable_playbook(option) is None, option

    def test_an_option_naming_no_playbook_at_all_survives(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        assert (
            validator.unanswerable_playbook("Raise the cost ceiling for one question") is None
        )
        assert validator.unanswerable_playbook("") is None

    def test_which_measure_is_recognised_however_it_is_phrased(self) -> None:
        for question in (
            "Which metric are you asking about?",
            "Which measure did you mean — the last figure you charted?",
            "What metric are you asking about?",
        ):
            assert _ASKS_WHICH_MEASURE.search(question), question

    def test_an_ordinary_question_is_not_mistaken_for_it(self) -> None:
        for question in (
            "Which payer did you mean?",
            "This pack defines no metric called 'foo'. Did you mean one of these?",
        ):
            assert not _ASKS_WHICH_MEASURE.search(question), question


class TestASuperlativeResolvesOnTheAxisTheConversationIsCutting:
    """§5a, and the worst answer in the live corpus.

    "denial rate last quarter excluding Medicare" → "and excluding Medicaid
    too?" → **"which one is worst now"** came back as
    ``denial rate: 8.1%`` — a single org-level scalar — narrated as *"with
    only one measure in hand there is nothing to rank it against, so 'worst'
    here names it by default rather than by comparison"*. Two turns of
    carving payer types out of the population, and "which one" was read as
    *which measure*. A clarification would have been strictly better than
    the answer that was given.
    """

    def test_the_words_that_name_a_row_are_recognised(self) -> None:
        for question in (
            "which one is worst now",
            "which of them is biggest",
            "the worst one",
            "which payer is worst",
        ):
            assert ENTITY_SUPERLATIVE.search(question), question

    def test_a_question_about_the_measure_is_not_one_of_them(self) -> None:
        for question in (
            "what was our denial rate",
            "show me the math",
            "and the dollars?",
        ):
            assert not ENTITY_SUPERLATIVE.search(question), question

    def test_a_thread_scoped_by_a_dimension_is_on_the_entity_axis(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        scoped = make_spec(
            measures=("denial_rate",),
            scope=Predicate(
                dimension=DimensionRef("payer_type"),
                op=PredicateOp.NOT_IN,
                values=("Medicare", "Medicaid"),
            ),
        )
        assert cuts_an_entity_axis(scoped)

    def test_a_thread_cut_by_a_dimension_is_too(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        assert cuts_an_entity_axis(make_spec(measures=("denial_rate",), dimensions=("payer",)))

    def test_a_bare_org_level_measure_is_not(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        assert not cuts_an_entity_axis(make_spec(measures=("denial_rate",)))


class TestAPersistenceQuestionIsAskedAfterTheAnswer:
    """The population test, corollary 3.

    *"Do you want the previous result re-run filtered to Atlas Commercial,
    or should Atlas Commercial be pinned as a filter for the rest of the
    session?"* — both readings count the same records on THIS answer, and
    the one that differs is a decision the analyst can only usefully make
    once they have seen the number.
    """

    def test_the_filter_or_pin_question_is_recognised(self) -> None:
        for question in (
            "Do you want the previous result re-run filtered to Atlas Commercial, or should "
            "Atlas Commercial be pinned as a filter for the rest of the session?",
            "Should I pin that for the rest of this conversation?",
            "Pin this until you clear it?",
        ):
            assert ASKS_WHETHER_TO_PIN.search(question), question

    def test_a_real_scope_question_is_not_one(self) -> None:
        for question in (
            "There is no payer named 'Silverline' in this data. Which did you mean?",
            "Which measure should I compare against the benchmark?",
        ):
            assert not ASKS_WHETHER_TO_PIN.search(question), question
