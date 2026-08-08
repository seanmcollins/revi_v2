"""A table-driven ``LanguageModelPort`` for demo mode (no API key, no SDK).

Serves the reference five-turn conversation, the definitional anchor
("what is PR3"), and the COB playbook, plus honest fallbacks: an unmatched
structured call returns ``structured_output=None``, which the engine
converts into a clarification — the demo never guesses. The same script
backs the reference-over-HTTP tests, so demo mode and CI exercise one
table.

What is scripted here is *only* the probabilistic layer: turn class,
interpreted ids, refinement operators. Every number, grade, chart and
header downstream is computed by the real engine against the real
warehouse, which is what makes demo mode a genuine test of the pipeline
rather than a puppet show.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from revi_investigation.application.ports import (
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)


def _usage() -> LlmUsage:
    return LlmUsage(
        model="scripted-demo",
        cost_usd=Decimal("0"),
        input_tokens=1,
        output_tokens=1,
        schema_retries=0,
        duration_ms=1,
    )


@dataclass(frozen=True, slots=True)
class ScriptEntry:
    template_id: str
    contains: str | None
    response: Mapping[str, Any] | None


@dataclass
class ScriptedLanguageModel:
    """``LanguageModelPort`` over a fixed script (first match wins)."""

    entries: list[ScriptEntry] = field(default_factory=list)
    narrative_chunks: tuple[str, ...] = ()
    structured_calls: list[StructuredLlmRequest] = field(default_factory=list)
    text_calls: list[TextLlmRequest] = field(default_factory=list)

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        self.structured_calls.append(request)
        for entry in self.entries:
            if entry.template_id != request.template_id:
                continue
            if entry.contains is not None and entry.contains not in request.rendered_prompt:
                continue
            output = dict(entry.response) if entry.response is not None else None
            return StructuredLlmResult(output=output, usage=_usage())
        # honest fallback: no scripted answer means no answer, never a guess
        return StructuredLlmResult(output=None, usage=_usage())

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        self.text_calls.append(request)

        async def iterate() -> AsyncIterator[str]:
            for chunk in self.narrative_chunks:
                yield chunk

        return iterate()

    async def last_usage(self) -> LlmUsage | None:
        return _usage() if self.text_calls else None


REFERENCE_QUESTIONS = (
    "Why did cash decline last week?",
    "Break that down by payer",
    "Just the top three payers - what's the CARC mix on their denials?",
    "Compare that to Q1",
    "Why do you say F2?",
)

_T1_INTERPRETATION: dict[str, Any] = {
    "intent_summary": "Investigate last week's posted-cash decline by payer",
    "metric_ids": [],
    "dimension_ids": ["payer"],
    "concept_ids": [],
    "playbook_id": "cash_decline",
    "window": {"quantity": "1", "unit": "week", "mode": "full_periods"},
    "basis": "post",
    "comparison": "prior_period",
    "scope": [],
    "clarification": None,
    "definitional_terms": [],
}


DEFINITIONAL_QUESTION = "what is pr3"
COB_QUESTION = "Do I have a COB problem?"

_COB_INTERPRETATION: dict[str, Any] = {
    "intent_summary": "Assess whether a coordination-of-benefits problem exists",
    "metric_ids": [],
    "dimension_ids": ["payer"],
    # the concept id is load-bearing: it is what grades the CARC branch
    # PROXY and the mismatch-flag branch DIRECT (design §5.5)
    "concept_ids": ["cob"],
    "playbook_id": "cob_investigation",
    "window": {"quantity": "4", "unit": "month", "mode": "full_periods"},
    "basis": None,
    "comparison": None,
    "scope": [],
    "clarification": None,
    "definitional_terms": [],
}


def demo_script_entries() -> list[ScriptEntry]:
    """The reference five-turn conversation as a scripted-demo table."""
    def classify(contains: str, turn_class: str, confidence: float) -> ScriptEntry:
        return ScriptEntry(
            "classify_turn",
            contains,
            {"turn_class": turn_class, "confidence": confidence, "clarification_question": None},
        )

    return [
        classify(REFERENCE_QUESTIONS[0], "new_investigation", 0.94),
        ScriptEntry("interpret_question", REFERENCE_QUESTIONS[0], _T1_INTERPRETATION),
        classify(REFERENCE_QUESTIONS[1], "refinement", 0.93),
        ScriptEntry("resolve_referents", REFERENCE_QUESTIONS[1], {"resolutions": []}),
        ScriptEntry(
            "emit_refinements",
            REFERENCE_QUESTIONS[1],
            {
                "operators": [{"op": "set_dimensions", "dimensions": ["payer"]}],
                "rationale": "split the decline by payer",
            },
        ),
        classify("top three payers", "refinement", 0.92),
        ScriptEntry(
            "resolve_referents",
            "top three payers",
            {
                "resolutions": [
                    {"mention": "the top three payers", "referent_id": "F1", "confidence": 0.95},
                    {"mention": "the top three payers", "referent_id": "F2", "confidence": 0.95},
                    {"mention": "the top three payers", "referent_id": "F3", "confidence": 0.95},
                ]
            },
        ),
        ScriptEntry(
            "emit_refinements",
            "top three payers",
            {
                "operators": [
                    {"op": "drill_into", "target": "F1"},
                    {"op": "drill_into", "target": "F2"},
                    {"op": "drill_into", "target": "F3"},
                    {"op": "pivot", "measures": ["denied_dollars"]},
                    {"op": "set_dimensions", "dimensions": ["carc"]},
                ],
                "rationale": "pin the top-three payer cohort; denied dollars by CARC",
            },
        ),
        classify(REFERENCE_QUESTIONS[3], "refinement", 0.90),
        ScriptEntry("resolve_referents", REFERENCE_QUESTIONS[3], {"resolutions": []}),
        ScriptEntry(
            "emit_refinements",
            REFERENCE_QUESTIONS[3],
            {
                "operators": [
                    {
                        "op": "set_comparison",
                        "kind": None,
                        "custom": {"start": "2026-01-01", "end": "2026-03-31"},
                    }
                ],
                "rationale": "compare the decline week against calendar Q1",
            },
        ),
        classify(REFERENCE_QUESTIONS[4], "meta", 0.95),
        # --- guide-question anchors, answered by the same generic paths ---
        # DEFINITIONAL needs no interpretation call: the term resolves
        # against pack content deterministically, zero probes.
        classify("pr3", "definitional", 0.96),
        classify("PR3", "definitional", 0.96),
        classify(COB_QUESTION, "new_investigation", 0.91),
        ScriptEntry("interpret_question", "COB", _COB_INTERPRETATION),
    ]


# Claim sentences cite referents and carry no free numbers, so the demo
# narrative always survives grounding validation.
DEMO_NARRATIVE_CHUNKS = (
    "Posted cash fell versus the prior week, and the decline concentrates in a ",
    "small set of payers (F1, F2, F3). ",
    "The largest single driver is F1; see the payer chart for the full split.",
)


def demo_language_model() -> ScriptedLanguageModel:
    return ScriptedLanguageModel(
        entries=demo_script_entries(), narrative_chunks=DEMO_NARRATIVE_CHUNKS
    )
