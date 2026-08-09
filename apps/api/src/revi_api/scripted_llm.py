"""A table-driven ``LanguageModelPort`` for demo mode (no API key, no SDK).

Serves the reference five-turn conversation, the definitional anchor
("what is PR3"), and the COB playbook, plus honest fallbacks: an unmatched
structured call returns ``structured_output=None``, which the engine
converts into a clarification — the demo never guesses. The same script
backs the reference-over-HTTP tests, so demo mode and CI exercise one
table.

What is scripted here is *only* the probabilistic layer: turn class,
interpreted ids, refinement operators, and the *shape* of the narrative
sentence. Every number, grade, chart and header downstream is computed by
the real engine against the real warehouse, which is what makes demo mode
a genuine test of the pipeline rather than a puppet show.

The narrative is composed per turn from the certified findings the engine
put in the prompt (:func:`compose_demo_narrative`) — never from a fixed
sentence. Referent handles are session-monotonic (turn 2 of the reference
conversation certifies F4/F5/F6, not F1/F2/F3), so a hard-coded fixture
cites handles that do not exist on later turns and the grounding validator
correctly redacts it. Reading the handles back out of the prompt is what
makes demo mode survive :func:`revi_presentation.narrative.validate_narrative`
on every turn, for the same reason a real model would: it says only what
the certified findings support.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from revi_investigation.application.ports import (
    LlmFailureKind,
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


NarrativeComposer = Callable[[TextLlmRequest], Sequence[str]]


@dataclass
class ScriptedLanguageModel:
    """``LanguageModelPort`` over a fixed script (first match wins).

    ``narrator`` composes the streamed narrative from the rendered prompt;
    with no narrator the model streams nothing, which the assembly layer
    treats as "no narrative on this turn"."""

    #: The script is not a model: it cannot run on a different tier and
    #: cannot spend a budget, so a deployment wired to it refuses a
    #: ``model_tier`` outright instead of accepting a control that would
    #: change nothing (``/v1/capabilities`` publishes the same fact).
    applies_call_policy = False

    entries: list[ScriptEntry] = field(default_factory=list)
    narrator: NarrativeComposer | None = None
    structured_calls: list[StructuredLlmRequest] = field(default_factory=list)
    text_calls: list[TextLlmRequest] = field(default_factory=list)

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        self.structured_calls.append(request)
        for entry in self.entries:
            if entry.template_id != request.template_id:
                continue
            if entry.contains is not None and entry.contains not in request.rendered_prompt:
                continue
            if entry.response is None:
                # a scripted non-answer: the script matched and says nothing
                return StructuredLlmResult(
                    output=None, usage=_usage(), failure=LlmFailureKind.DECLINED
                )
            return StructuredLlmResult(output=dict(entry.response), usage=_usage())
        # honest fallback: no scripted answer means no answer, never a guess.
        # OFF_SCRIPT says so plainly, so the clarification the engine builds
        # blames the script running out rather than the analyst's wording.
        return StructuredLlmResult(output=None, usage=_usage(), failure=LlmFailureKind.OFF_SCRIPT)

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        self.text_calls.append(request)
        chunks = tuple(self.narrator(request)) if self.narrator is not None else ()

        async def iterate() -> AsyncIterator[str]:
            for chunk in chunks:
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
                    # denials are keyed on the group code + CARC PAIR
                    # (denied_dollars.yaml, presentation.yaml, codes.yaml):
                    # CO-16 and PI-16 are different denials, and 14 of the
                    # 20 CARCs in this warehouse span more than one group.
                    {"op": "set_dimensions", "dimensions": ["group_code", "carc"]},
                ],
                "rationale": "pin the top-three payer cohort; denied dollars by group code + CARC",
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


# ---------------------------------------------------------------------------
# narrative
#
# The composer reads the "Certified findings:" block of the rendered
# ``compose_narrative`` prompt and reuses ONLY what the grounding validator
# can already vouch for: the referent handles and their evidence grades, in
# the order the engine ranked them. It states no free numbers at all and no
# proper names, so every sentence is trivially grounded on every turn —
# which is the honest shape of a narrative anyway: the numbers live on the
# finding cards and the chart, where they carry their own provenance.

_FINDINGS_HEADER = "Certified findings:"
_FINDINGS_END = "\nReconciliation:"
_FINDING_LINE = re.compile(r"^-\s+(?P<referent>[FD]\d+):\s+(?P<body>.+?)\s*$")
_GRADE = re.compile(r"\(grade (?P<grade>[a-z_]+), confidence [^;]+;")


def parse_certified_findings(prompt: str) -> tuple[tuple[str, str], ...]:
    """``(referent, grade)`` per certified finding, in prompt (rank) order."""
    _, _, tail = prompt.partition(_FINDINGS_HEADER)
    block = tail.partition(_FINDINGS_END)[0]
    parsed: list[tuple[str, str]] = []
    for line in block.splitlines():
        match = _FINDING_LINE.match(line.strip())
        if match is None:
            continue
        grades = _GRADE.findall(match.group("body"))
        parsed.append((match.group("referent"), grades[-1] if grades else "unstated"))
    return tuple(parsed)


def _join(items: Sequence[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def compose_demo_narrative(request: TextLlmRequest) -> tuple[str, ...]:
    """A grounded narrative for *this* turn's findings, as stream chunks.

    Empty when the prompt certifies nothing — the demo never narrates a
    turn it has no findings for (the assembly layer short-circuits before
    that anyway, but the model must not depend on it)."""
    findings = parse_certified_findings(request.rendered_prompt)
    if not findings:
        return ()
    handles = [referent for referent, _ in findings]
    weak = [f"{referent} ({grade})" for referent, grade in findings if grade != "direct"]

    if len(handles) == 1:
        lead = f"{handles[0]} is the only certified finding on this turn."
    elif len(handles) == 2:
        lead = (
            f"{handles[0]} leads the certified findings on this turn, "
            f"with {handles[1]} behind it."
        )
    else:
        lead = (
            f"{handles[0]} leads the certified findings on this turn, "
            f"followed by {handles[1]} and {handles[2]}."
        )
    chunks = [lead + " "]
    if len(handles) > 3:
        chunks.append("The remaining findings are ranked below them. ")
    if weak:
        verb = "rests" if len(weak) == 1 else "rest"
        pronoun = "it" if len(weak) == 1 else "them"
        chunks.append(
            f"{_join(weak)} {verb} on evidence weaker than direct, "
            f"so read {pronoun} as indicative rather than settled. "
        )
    else:
        chunks.append("Every finding here is graded direct against certified semantics. ")
    chunks.append("Open a finding to see the probes, grades and figures behind it.")
    return tuple(chunks)


def demo_language_model() -> ScriptedLanguageModel:
    return ScriptedLanguageModel(
        entries=demo_script_entries(), narrator=compose_demo_narrative
    )
