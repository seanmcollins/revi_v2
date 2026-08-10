"""Gestures this platform prints and must therefore be able to read back.

Every finding ships ``suggested_refinements`` — "drill into F1" — and every
unknown-handle clarification offers the same string as an option. Sent back
verbatim and routed through the classifier, those strings come back
``clarification_required`` with ``referent_resolutions: []`` — a
clarification question at 0.72 confidence, while the deterministic F-handle
resolver that would have answered it in a dictionary lookup never runs. A
product that cannot parse its own suggestion has published a button that
does not work.

So the shapes the platform *emits* are parsed here, deterministically,
before any model call — the same rule §7.6 already applies to referent
handles, extended from "the handle inside the sentence" to "the sentence".
One module owns both halves: :func:`drill_suggestion` builds the string
findings publish, and :func:`parse_gesture` reads it. They cannot drift
without this module's own round-trip test failing.

The grammar is deliberately tiny and whole-utterance. "Drill into F1" is a
gesture; "drill into F1 broken out by payer and compare to last month" is a
*refinement*, which is language, and language goes to the model that
compiles language. Matching loosely here would silently drop the parts of
a request nobody parsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from revi_investigation.application.llm.schemas import (
    AnyRefinementOperator,
    DrillIntoModel,
    ExplainModel,
)
from revi_investigation.application.ports import RegisteredReferent

__all__ = [
    "GestureMatch",
    "drill_suggestion",
    "explain_suggestion",
    "parse_gesture",
    "suggested_refinements_for",
]

#: The one place the drill suggestion's wording lives.
_DRILL_VERB = "drill into"
_EXPLAIN_VERB = "explain"

_HANDLE = r"[FD]\d+"
_GESTURES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"^{_DRILL_VERB}\s+({_HANDLE})$", re.IGNORECASE), "drill_into"),
    (re.compile(rf"^{_EXPLAIN_VERB}\s+({_HANDLE})$", re.IGNORECASE), "explain"),
)


def drill_suggestion(referent_value: str) -> str:
    """The refinement a finding suggests, worded once."""
    return f"{_DRILL_VERB} {referent_value}"


def explain_suggestion(referent_value: str) -> str:
    return f"{_EXPLAIN_VERB} {referent_value}"


def suggested_refinements_for(referent_value: str) -> tuple[str, ...]:
    """Every suggestion published beside a finding — all of them parseable."""
    return (drill_suggestion(referent_value),)


@dataclass(frozen=True, slots=True)
class GestureMatch:
    """This utterance IS one of the platform's own gestures.

    ``operators`` is ``None`` when the shape matched but the handle names
    something this session never showed. That is still a gesture — it just
    has an honest answer ("I haven't shown F9 in this session; here is what
    I have") that the refinement turn already knows how to give, with no
    model call either way.
    """

    handle: str
    operators: tuple[AnyRefinementOperator, ...] | None


def parse_gesture(
    question: str, entries: tuple[RegisteredReferent, ...]
) -> GestureMatch | None:
    """Read a platform-emitted gesture back, or ``None`` if it is language.

    Whole-utterance and case-insensitive, tolerant only of surrounding
    whitespace and trailing punctuation — the things a person adds without
    meaning anything by them.
    """
    text = " ".join(question.split()).strip(" .!?")
    for pattern, operator in _GESTURES:
        match = pattern.match(text)
        if match is None:
            continue
        handle = match.group(1).upper()
        known = any(entry.referent.value == handle for entry in entries)
        if not known:
            return GestureMatch(handle=handle, operators=None)
        model: AnyRefinementOperator = (
            DrillIntoModel(op="drill_into", target=handle)
            if operator == "drill_into"
            else ExplainModel(op="explain", target=handle)
        )
        return GestureMatch(handle=handle, operators=(model,))
    return None
