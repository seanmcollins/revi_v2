"""What a clarification says when the model gives us nothing back.

Two things look identical at the port — a model that read the question and
had no mapping for it, and an answer that never arrived in a readable shape
— and they want opposite advice. These tests pin that the four LLM services
tell them apart, that the failure kind reaches the trace reason, and that
model-proposed recovery options survive the trim into
``ClarificationRequest.options`` (which the API renders as chips).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.interpretation import (
    ClassifyTurnService,
    InterpretQuestionService,
)
from revi_investigation.application.llm.schemas import (
    MAX_CLARIFICATION_OPTIONS,
    clarification_options,
)
from revi_investigation.application.ports import (
    LlmFailureKind,
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
    retry_may_help,
)
from revi_investigation.application.refinement_llm import (
    EmitRefinementsService,
    ResolveReferentsService,
)
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import Session
from revi_kernel.errors import UnsupportedConceptError
from revi_kernel.watermark import DataWatermark, WatermarkEpoch
from revi_testing.fakes import make_usage

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

#: Every empty-handed shape a structured call can arrive in, and whether the
#: honest ask is "again" (the answer was mangled) or "differently" (the model
#: had nothing). ``None`` is an adapter that did not say: assume the model.
EMPTY_HANDED = [
    pytest.param(LlmFailureKind.SCHEMA, True, "schema", id="schema"),
    pytest.param(LlmFailureKind.DECLINED, False, "declined", id="declined"),
    pytest.param(LlmFailureKind.OFF_SCRIPT, False, "off_script", id="off_script"),
    pytest.param(None, False, "unspecified", id="unspecified"),
]


@dataclass
class FixedLlm:
    """A one-answer ``LanguageModelPort``: exactly the result under test."""

    result: StructuredLlmResult

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        return self.result

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        raise AssertionError("these tests never stream")

    async def last_usage(self) -> LlmUsage | None:
        return None


def _empty(failure: LlmFailureKind | None) -> FixedLlm:
    return FixedLlm(StructuredLlmResult(output=None, usage=make_usage(), failure=failure))


def _returning(output: dict[str, Any]) -> FixedLlm:
    return FixedLlm(StructuredLlmResult(output=output, usage=make_usage()))


def _interpreter(pack_port: PackPort, catalog: CatalogSnapshot, llm: FixedLlm) -> Any:
    return InterpretQuestionService(llm, pack_port, catalog)


# ---------------------------------------------------------------------------
# the port itself


class TestFailureKind:
    def test_only_a_schema_failure_is_worth_repeating(self) -> None:
        assert retry_may_help(LlmFailureKind.SCHEMA)
        assert not retry_may_help(LlmFailureKind.DECLINED)
        assert not retry_may_help(LlmFailureKind.OFF_SCRIPT)
        # an adapter that did not say gets the conservative reading
        assert not retry_may_help(None)

    def test_a_result_cannot_both_answer_and_fail(self) -> None:
        with pytest.raises(ValueError, match="both an output and a failure"):
            StructuredLlmResult(
                output={"turn_class": "meta"},
                usage=make_usage(),
                failure=LlmFailureKind.SCHEMA,
            )

    def test_a_plain_result_reports_no_failure(self) -> None:
        assert StructuredLlmResult(output={"x": 1}, usage=make_usage()).failure is None


# ---------------------------------------------------------------------------
# the four call sites


class TestClassificationHonesty:
    @pytest.mark.parametrize(("failure", "retry", "token"), EMPTY_HANDED)
    async def test_empty_handed_asks_for_the_right_thing(
        self, failure: LlmFailureKind | None, retry: bool, token: str
    ) -> None:
        outcome = await ClassifyTurnService(_empty(failure)).classify("mumble mumble")

        assert outcome.classification is None
        assert outcome.clarification is not None
        assert ("try again" in outcome.clarification.question) is retry
        assert ("rephrase" in outcome.clarification.question) is not retry
        assert outcome.clarification.reason is not None
        assert f"llm_failure={token}" in outcome.clarification.reason

    async def test_an_unparseable_answer_is_a_schema_failure(self) -> None:
        """Something came back; it just was not a classification. The analyst's
        wording was never in question, so the ask is "again"."""
        outcome = await ClassifyTurnService(_returning({"turn_class": "nonsense"})).classify("hi")

        assert outcome.clarification is not None
        assert "try again" in outcome.clarification.question
        assert outcome.clarification.reason is not None
        assert "llm_failure=schema" in outcome.clarification.reason

    async def test_the_two_asks_are_actually_different(self) -> None:
        broken = await ClassifyTurnService(_empty(LlmFailureKind.SCHEMA)).classify("q")
        declined = await ClassifyTurnService(_empty(LlmFailureKind.DECLINED)).classify("q")

        assert broken.clarification is not None and declined.clarification is not None
        assert broken.clarification.question != declined.clarification.question

    async def test_model_options_ride_along_with_the_question(self) -> None:
        llm = _returning(
            {
                "turn_class": "new_investigation",
                "confidence": 0.3,
                "clarification_question": "Which cash question do you mean?",
                "clarification_options": ["Why did cash decline last week?", "Cash by payer"],
            }
        )
        outcome = await ClassifyTurnService(llm).classify("cash?")

        assert outcome.clarification is not None
        assert outcome.clarification.options == (
            "Why did cash decline last week?",
            "Cash by payer",
        )

    async def test_a_failed_call_carries_no_options(self) -> None:
        outcome = await ClassifyTurnService(_empty(LlmFailureKind.SCHEMA)).classify("q")
        assert outcome.clarification is not None
        assert outcome.clarification.options == ()


class TestInterpretationHonesty:
    @pytest.mark.parametrize(("failure", "retry", "token"), EMPTY_HANDED)
    async def test_empty_handed_asks_for_the_right_thing(
        self,
        pack_port: PackPort,
        catalog: CatalogSnapshot,
        failure: LlmFailureKind | None,
        retry: bool,
        token: str,
    ) -> None:
        service = _interpreter(pack_port, catalog, _empty(failure))
        outcome = await service.interpret("mumble", session=SESSION, turn_id="t1")

        assert outcome.investigation is None
        assert outcome.clarification is not None
        assert ("try again" in outcome.clarification.question) is retry
        assert ("rephrase" in outcome.clarification.question) is not retry
        assert outcome.clarification.reason is not None
        assert f"llm_failure={token}" in outcome.clarification.reason

    async def test_an_unparseable_answer_is_a_schema_failure(
        self, pack_port: PackPort, catalog: CatalogSnapshot
    ) -> None:
        service = _interpreter(pack_port, catalog, _returning({"nope": True}))
        outcome = await service.interpret("q", session=SESSION, turn_id="t1")

        assert outcome.clarification is not None
        assert "try again" in outcome.clarification.question
        assert outcome.clarification.reason is not None
        assert "llm_failure=schema" in outcome.clarification.reason

    async def test_model_options_ride_along_with_the_question(
        self, pack_port: PackPort, catalog: CatalogSnapshot
    ) -> None:
        """Options survive when the ids they name actually resolve."""
        service = _interpreter(
            pack_port,
            catalog,
            _returning(
                {
                    "intent_summary": "ambiguous",
                    "clarification": "Which denial view do you want?",
                    "clarification_options": [
                        {
                            "label": "Denial rate by payer",
                            "metric_ids": ["denial_rate"],
                            "dimension_ids": ["payer"],
                        },
                        {
                            "label": "Denied dollars by CARC",
                            "metric_ids": ["denied_dollars"],
                            "dimension_ids": ["carc"],
                        },
                    ],
                }
            ),
        )
        outcome = await service.interpret("denials?", session=SESSION, turn_id="t1")

        assert outcome.clarification is not None
        assert outcome.clarification.options == (
            "Denial rate by payer",
            "Denied dollars by CARC",
        )
        assert outcome.clarification.reason == "model requested clarification"

    async def test_an_option_the_pack_cannot_honor_is_dropped(
        self, pack_port: PackPort, catalog: CatalogSnapshot
    ) -> None:
        """F13: an option is a promise. "Denial rate by CARC" is a
        claim-grain rate cut by a line-level code — §6.6 refuses it — so it
        must never reach a chip the analyst can tap."""
        service = _interpreter(
            pack_port,
            catalog,
            _returning(
                {
                    "intent_summary": "ambiguous",
                    "clarification": "Which denial view do you want?",
                    "clarification_options": [
                        {
                            "label": "Denial rate by CARC",
                            "metric_ids": ["denial_rate"],
                            "dimension_ids": ["carc"],
                        },
                        {
                            "label": "Denial rate by payer",
                            "metric_ids": ["denial_rate"],
                            "dimension_ids": ["payer"],
                        },
                    ],
                }
            ),
        )
        outcome = await service.interpret("denials?", session=SESSION, turn_id="t1")

        assert outcome.clarification is not None
        assert outcome.clarification.options == ("Denial rate by payer",)

    async def test_all_options_ungrounded_refuses_rather_than_clarifies(
        self, pack_port: PackPort, catalog: CatalogSnapshot
    ) -> None:
        """Zero survivors is a capability refusal, not a hollow question:
        offering only unanswerable ways forward costs the analyst a turn to
        reach the same no."""
        service = _interpreter(
            pack_port,
            catalog,
            _returning(
                {
                    "intent_summary": "ambiguous",
                    "clarification": "Which view do you want?",
                    "clarification_options": [
                        {"label": "Patient satisfaction by region", "metric_ids": ["nps_score"]},
                    ],
                }
            ),
        )
        with pytest.raises(UnsupportedConceptError):
            await service.interpret("how happy are patients?", session=SESSION, turn_id="t1")


class TestReferentResolutionHonesty:
    @pytest.mark.parametrize(("failure", "retry", "token"), EMPTY_HANDED)
    async def test_empty_handed_asks_for_the_right_thing(
        self, failure: LlmFailureKind | None, retry: bool, token: str
    ) -> None:
        outcome = await ResolveReferentsService(_empty(failure)).resolve("those two", ())

        assert outcome.resolutions == ()
        assert outcome.clarification is not None
        assert ("try again" in outcome.clarification.question) is retry
        assert outcome.clarification.reason is not None
        assert f"llm_failure={token}" in outcome.clarification.reason

    async def test_an_unparseable_answer_says_so_in_the_trace(self) -> None:
        """The old code reported "returned no structured output" for a reply
        that very much arrived — it just did not validate."""
        outcome = await ResolveReferentsService(_returning({"resolutions": "nope"})).resolve(
            "those two", ()
        )

        assert outcome.clarification is not None
        assert outcome.clarification.reason is not None
        assert "failed schema validation" in outcome.clarification.reason
        assert "llm_failure=schema" in outcome.clarification.reason


class TestRefinementEmissionHonesty:
    @staticmethod
    async def _emit(llm: FixedLlm) -> Any:
        return await EmitRefinementsService(llm).emit(
            "make it monthly",
            context_summary="cash by payer, last week",
            entries=(),
            resolutions=(),
            dimension_lines="- payer: Payer",
            metric_lines="- cash_posted",
        )

    @pytest.mark.parametrize(("failure", "retry", "token"), EMPTY_HANDED)
    async def test_empty_handed_asks_for_the_right_thing(
        self, failure: LlmFailureKind | None, retry: bool, token: str
    ) -> None:
        outcome = await self._emit(_empty(failure))

        assert outcome.operators is None
        assert outcome.clarification is not None
        assert ("try again" in outcome.clarification.question) is retry
        assert outcome.clarification.reason is not None
        assert "AMBIGUOUS_REFINEMENT" in outcome.clarification.reason
        assert f"llm_failure={token}" in outcome.clarification.reason

    async def test_model_options_ride_along_when_nothing_compiles(self) -> None:
        outcome = await self._emit(
            _returning(
                {
                    "operators": [],
                    "rationale": "no closed-set operator expresses that",
                    "clarification_options": ["Break it down by payer", "Compare to last month"],
                }
            )
        )

        assert outcome.operators is None
        assert outcome.clarification is not None
        assert outcome.clarification.options == (
            "Break it down by payer",
            "Compare to last month",
        )

    async def test_a_compiled_refinement_needs_no_clarification(self) -> None:
        outcome = await self._emit(
            _returning(
                {
                    "operators": [{"op": "set_dimensions", "dimensions": ["payer"]}],
                    "rationale": "split by payer",
                }
            )
        )

        assert outcome.clarification is None
        assert outcome.operators is not None and len(outcome.operators) == 1


# ---------------------------------------------------------------------------
# the option trim


class TestClarificationOptionTrim:
    def test_absent_options_are_absent(self) -> None:
        assert clarification_options([]) == ()

    def test_at_most_four_survive(self) -> None:
        trimmed = clarification_options([f"option {n}" for n in range(10)])
        assert len(trimmed) == MAX_CLARIFICATION_OPTIONS
        assert trimmed[0] == "option 0"  # first-seen order, not sorted

    def test_blanks_and_case_duplicates_are_dropped(self) -> None:
        assert clarification_options(["  ", "By payer", "by  payer", "", "By CARC"]) == (
            "By payer",
            "By CARC",
        )

    def test_whitespace_is_flattened_and_long_options_are_clipped(self) -> None:
        (flattened,) = clarification_options(["by\n  payer\tand carc"])
        assert flattened == "by payer and carc"
        (clipped,) = clarification_options(["x" * 400])
        assert len(clipped) == 120

    def test_dedupe_survives_the_cut(self) -> None:
        """Four DISTINCT options, not four entries: a model that repeats
        itself must not fill the card with one chip."""
        raw = ["a", "a", "b", "b", "c", "d", "e"]
        assert clarification_options(raw) == ("a", "b", "c", "d")
