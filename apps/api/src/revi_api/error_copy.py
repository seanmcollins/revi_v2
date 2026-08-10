"""Plain-language copy for the §12 error codes, at the API boundary.

The engine's error messages are written for the engine: they name probes,
entities, contracts and bases because that is what a §6.6 refusal is about
and what a trace must record. Published verbatim to a default-mode user
they read as a crash — ``DATE_BASIS_INVALID: date basis 'remit' is not
bound for entity 'claim'`` names three internal concepts and offers no
next step. The precision is not the problem; the audience is.

So this module maps each code to one plain sentence that says what
happened and what to do about it, and the technical message rides
underneath rather than being thrown away:

- the ``code`` is unchanged — clients branch on it, and §12 fixes it;
- the envelope SHAPE is unchanged (``code``/``message``/``correlation_id``);
- the engine message is preserved in the logs and in the recorded trace,
  and is appended to the published message when the turn ran with
  ``debug`` on — the setting that already means "show me the working".

A code with no entry here falls back to the engine's own message: an
unmapped code publishing a generic sentence would say less while sounding
more certain.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from revi_kernel.errors import ErrorCode

#: One plain sentence per code: what happened, then what to do.
#:
#: Each says which of the two things is true — "the platform will not" (a
#: governed refusal, and the recovery is a different question) or "the
#: platform cannot right now" (an operational failure, and the recovery is
#: to retry or escalate) — because those want opposite responses and the
#: code alone does not distinguish them.
PLAIN_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.BINDING_AMBIGUOUS: (
        "That term matches more than one thing in this warehouse, so I can't tell which "
        "you meant. Naming the field or the breakdown you want will settle it."
    ),
    ErrorCode.INSUFFICIENT_EVIDENCE: (
        "There isn't enough data behind that question to answer it honestly. A wider "
        "window, or a broader population, may have enough."
    ),
    ErrorCode.UNSUPPORTED_CONCEPT: (
        "I couldn't map that question onto anything this warehouse measures. Try naming a "
        "specific metric or breakdown, or ask what's available."
    ),
    # POLICY_DENIED is deliberately ABSENT. Its messages are already
    # written for the person who hit them and are required to name the
    # bound that was crossed ("model_tier 'x' is not in this deployment's
    # allowlist (…)", "session 's' belongs to another tenant"). A plain
    # sentence here would replace an actionable refusal with a vaguer one,
    # which is the opposite of the fix.
    ErrorCode.SOURCE_UNAVAILABLE: (
        "The data source didn't respond, so nothing was read. Nothing about your question "
        "was wrong — please try again in a moment."
    ),
    # QUERY_BUDGET_EXCEEDED's entry is the WAREHOUSE-read case, which is
    # what the code is named for and what most of its raise sites mean.
    # The model-spend case gets its own sentence via `_SUBCODE_MESSAGES`
    # below — see :func:`budget_subcode` for why the two must not share one.
    ErrorCode.QUERY_BUDGET_EXCEEDED: (
        "That question would read more of the warehouse than one turn is allowed to. "
        "Narrowing it — fewer breakdowns, a shorter window, or a top-N — brings it in range."
    ),
    ErrorCode.AMBIGUOUS_REFINEMENT: (
        "That follow-up could change the answer in more than one way, and I won't guess "
        "between them. Say which change you meant."
    ),
    ErrorCode.REFERENT_NOT_FOUND: (
        "I couldn't find what you asked for here. Handles like F2 belong to the answer that "
        "introduced them, and session and investigation ids to the tenant that created them."
    ),
    ErrorCode.CONTEXT_CONFLICT: (
        "That change conflicts with something already pinned on this investigation. Clear "
        "the conflicting filter, or start a fresh question."
    ),
    ErrorCode.GRAIN_INCOMPATIBLE: (
        "That metric can't be cut that way — the breakdown is finer than the metric is "
        "defined at, so any number would be wrong rather than merely imprecise. A different "
        "breakdown, or a metric defined at that level, will answer it."
    ),
    ErrorCode.DATE_BASIS_INVALID: (
        "That metric can't be dated the way this question needs in this warehouse. Asking "
        "on a different date basis — service, submission or posting date — will answer it."
    ),
    ErrorCode.WATERMARK_STALE: (
        "Newer data has landed since this session was pinned. Re-anchor to the latest load "
        "to include it, or keep the current pin for a stable comparison."
    ),
    ErrorCode.DATA_LOADING: (
        "The warehouse has no completed load to read yet. This clears once the first load "
        "finishes."
    ),
    ErrorCode.RECONCILIATION_FAILED: (
        "The breakdown didn't add up to the total it came from, so I've withheld it rather "
        "than publish figures that disagree. This is a data issue worth reporting."
    ),
    ErrorCode.SOURCE_CAPABILITY_UNSUPPORTED: (
        "This data source can't perform one of the steps that question needs. A simpler "
        "form of the same question may be answerable here."
    ),
}


# ---------------------------------------------------------------------------
# QUERY_BUDGET_EXCEEDED is two different failures wearing one code.

#: The warehouse-read budget: the plan would group too many cells, hold too
#: many probes, or pin too large a cohort. The recovery is to ask a
#: narrower question.
WAREHOUSE_READ_BUDGET = "WAREHOUSE_READ_BUDGET"
#: The model-spend budget: the turn's per-call or per-turn cost ceiling was
#: reached. The question was fine; the wallet was the constraint. The
#: recovery is a higher ceiling, a cheaper tier, or waiting — and NOT
#: rewriting a question that was never too wide.
MODEL_SPEND_BUDGET = "MODEL_SPEND_BUDGET"

#: ``details`` keys that identify a model-spend stop. The language-model
#: adapter is the only thing that reports a dollar ceiling; every
#: warehouse-side budget is counted in cells, probes or rows.
_MODEL_SPEND_KEYS = ("max_budget_usd", "cost_usd", "max_turn_cost_usd")

#: Plain copy per subcode. The warehouse case keeps the code-level
#: sentence (it is the same failure the code is named for); the model case
#: needs its own, because telling somebody to narrow their question after a
#: spend stop sends them to rewrite something that was never the problem.
_SUBCODE_MESSAGES: dict[str, str] = {
    MODEL_SPEND_BUDGET: (
        "This turn reached its model-spend ceiling before it could finish. Nothing about "
        "your question was too large — raising the turn's cost ceiling, choosing a "
        "cheaper model tier, or simply asking again will get you an answer."
    ),
}


def budget_subcode(
    code: ErrorCode, details: Mapping[str, object] | None = None
) -> str | None:
    """Which budget stopped the turn, or ``None`` for any other code.

    Decided from the error's structured ``details`` rather than from its
    sentence: a dollar ceiling is recorded as a ceiling and a cost, a read
    budget as cells, probes or cohort size. Reading recorded numbers is
    stable; grepping prose is not.

    Defaults to the warehouse case when nothing identifies it — that is
    what the code has always meant and what most of its raise sites are,
    and it is the guess the analyst can act on and verify.
    """
    if code is not ErrorCode.QUERY_BUDGET_EXCEEDED:
        return None
    if details and any(details.get(key) is not None for key in _MODEL_SPEND_KEYS):
        return MODEL_SPEND_BUDGET
    return WAREHOUSE_READ_BUDGET


#: Codes whose failure IS "the thing you named does not resolve here".
#: For these the offending term is the single most useful fact in the
#: message — the analyst wrote it — so it is echoed back from the error's
#: structured ``details`` rather than being lost with the jargon around it.
_ECHO_CODES = frozenset(
    {ErrorCode.UNSUPPORTED_CONCEPT, ErrorCode.BINDING_AMBIGUOUS}
)

#: ``details`` keys that hold a term in the *analyst's* vocabulary. Keys
#: naming internals (``probe``, ``entity``, ``field``) are excluded: they
#: are what the plain message exists to keep out of the user's way.
_ECHO_KEYS = ("metric", "dimension", "playbook", "concept", "term")


def _named_terms(details: Mapping[str, object] | None) -> str:
    if not details:
        return ""
    named = [str(details[key]) for key in _ECHO_KEYS if details.get(key)]
    if not named:
        return ""
    return f" (nothing here is called {', '.join(repr(n) for n in named)})"


def plain_message(
    code: ErrorCode,
    technical: str,
    *,
    debug: bool = False,
    details: Mapping[str, object] | None = None,
) -> str:
    """The message to publish for one error.

    ``technical`` is the engine's own sentence: appended when ``debug`` is
    on and otherwise kept out of the user's way. It is never *dropped* —
    the caller logs it and the turn's trace records it either way, so
    nothing published here is the only copy of anything.

    ``details`` is the error's structured, client-safe mapping. It is read
    only to echo back a term the analyst themselves supplied (see
    :data:`_ECHO_CODES`), which is the difference between a refusal you
    can act on and one you cannot.
    """
    subcode = budget_subcode(code, details)
    plain = _SUBCODE_MESSAGES.get(subcode or "") or PLAIN_MESSAGES.get(code)
    if plain is None:
        # An unmapped code says the engine's own words rather than a
        # generic sentence carrying less information while sounding more
        # certain.
        return technical
    if code in _ECHO_CODES:
        plain = f"{plain}{_named_terms(details)}"
    return f"{plain} [{code.value}: {technical}]" if debug else plain


# ----------------------------------------------------------- clarifications
#
# A clarification is a first-class SUCCESSFUL outcome (§2.8, §12) and there
# are two kinds of it, which want opposite readings:
#
# * "Which AR view do you want — days in AR, aging distribution, or balance
#   trend?" needs ONE ANSWER, after which the question the analyst already
#   asked runs. Neutral. A dialogue move.
# * "I couldn't find a governed definition for that term" is a VERDICT:
#   nothing here answers it, and the way forward is a different question.
#
# Both shipped under the same amber refusal copy, so the helpful one read
# as a failure — the disambiguation above arrived under "There is no
# answerable option to offer here." The register is published as a coded
# warning rather than a new field: the severity ladder in
# :mod:`revi_api.warning_codes` already means exactly this (``info`` is
# "worth knowing", ``caution`` is "this changes how to read what you got"),
# so a client that renders warning codes gets the distinction for free and
# no parallel scheme is invented beside the one that exists.

CLARIFICATION_OPTIONS_OFFERED_WARNING = (
    "clarification_options_offered: this is a question with answers to choose from, not a "
    "refusal — pick one and the question you already asked runs with it applied."
)

CLARIFICATION_NO_OPTIONS_WARNING = (
    "clarification_no_options: there is no answerable option to offer for this one, so the "
    "way forward is to ask it a different way."
)


#: The engine's own marker for "I have nothing answerable to offer here"
#: (``submit_turn.clarification.NO_OPTIONS_REASON``), stamped by the last
#: step of the clarification funnel.
_NO_OPTIONS_MARKER = "CLARIFICATION_NO_OPTIONS"


def clarification_register(reason: str | None, options: Sequence[str]) -> str:
    """Which register this clarification is in, as a coded warning sentence.

    Loud only where the ENGINE declared it cannot offer anything. Empty
    ``options`` is not that declaration on its own: a clarification can
    legitimately invite a free-text answer, and treating "no buttons" as
    "no way forward" is how a question with twelve real payers behind it
    rendered as a dead end. The engine's marker is the signal; the option
    list is corroboration, not the test.
    """
    declared = _NO_OPTIONS_MARKER in (reason or "")
    if declared and not options:
        return CLARIFICATION_NO_OPTIONS_WARNING
    return CLARIFICATION_OPTIONS_OFFERED_WARNING


#: A reason fragment's leading code ("CLARIFICATION_SOLE_SURVIVOR: …").
_REASON_CODE = re.compile(r"^([A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)*):\s*(.*)$", re.DOTALL)

#: An internal enum token anywhere in a fragment.
_ENUM_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b")

#: A snake_case governed identifier — a metric id, a playbook id, a
#: dimension id. The vocabulary rule this repo already keeps says internal
#: identifiers never appear on default surfaces (display names do, and
#: Evidence and exports carry the ids); fine print is a default surface.
_INTERNAL_ID = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

#: A model's numeric confidence, in the phrasings the engine writes it —
#: "turn classification confidence 0.78", "referent resolution confidence
#: 0.62". A probability is a fact about the platform's internals, not about
#: the analyst's question; it belongs in the trace, where
#: ``DebugTracePayload.classification_confidence`` already publishes it
#: under the debug gate this string ignored.
_CONFIDENCE = re.compile(r"\bconfidence\s+\d*\.\d+")

#: Machine key/value pairs, e.g. ``options_dropped=2``.
_MACHINE_PAIR = re.compile(r"\b\w+=\S+")

#: The refinement operators by their wire ids, in the words a reader uses.
#: They are governed identifiers rather than English: "drill_into takes
#: exactly one referent id" is a sentence about this engine's schema, not
#: about the question that was asked.
_OPERATOR_PHRASES: dict[str, str] = {
    "set_dimensions": "changing the breakdown",
    "add_filter": "narrowing the scope",
    "remove_filter": "widening the scope",
    "set_comparison": "changing the comparison",
    "set_window": "changing the period",
    "set_grain": "changing the grain",
    "reset_context": "resetting the context",
    "drill_into": "drilling in",
    "rank_by": "re-ranking",
    "explain": "explaining",
    "expand": "showing more rows",
    "pivot": "pivoting",
}


def clarification_reason_copy(reason: str | None, *, debug: bool = False) -> str | None:
    """The customer-facing fine print for a clarification, or ``None``.

    ``ClarificationRequest.reason`` is written for the trace: it leads with
    a code, carries machine pairs, and names operators and metric ids
    because those are what a decision record is made of. It was published
    verbatim, so an analyst read *"turn classification confidence 0.78"*
    under a helpful question, and *"CLARIFICATION_SOLE_SURVIVOR"* under a
    refusal.

    Nothing is lost by cleaning it: the raw string is recorded on the trace
    (``recording.py``) and served at full fidelity by the trace endpoint and
    by ``debug=True`` here — the same seam
    :func:`plain_message` uses for the engine's own error sentences.

    Fragments are kept only when what remains is a sentence about the
    ANALYST'S question. A fragment that still carries internal vocabulary
    after the operator names are translated is omitted rather than
    paraphrased: guessing at what a code meant would publish a sentence
    nobody wrote. When every fragment goes, so does the fine print — a
    question with no explanation reads better than one explained in ids.
    """
    if reason is None or not reason.strip():
        return None
    if debug:
        return reason
    kept: list[str] = []
    for fragment in reason.split(";"):
        text = fragment.strip()
        code_match = _REASON_CODE.match(text)
        if code_match is not None:
            text = code_match.group(2).strip()
        if not text:
            continue
        for operator, phrase in _OPERATOR_PHRASES.items():
            text = re.sub(rf"\b{operator}\b", phrase, text)
        if (
            _ENUM_TOKEN.search(text)
            or _INTERNAL_ID.search(text)
            or _CONFIDENCE.search(text)
            or _MACHINE_PAIR.search(text)
        ):
            continue
        kept.append(text)
    if not kept:
        return None
    joined = "; ".join(kept)
    return joined[:1].upper() + joined[1:]


def clarification_frame_reason(reason: str | None) -> str | None:
    """The reason as the intermediate SSE frame may carry it.

    THE STREAM IS A DEFAULT SURFACE. ``clarification_reason_copy`` is
    applied where the terminal ``TurnResponse`` is assembled, and the
    ``clarification`` frame that precedes it published
    ``ClarificationRequest.reason`` byte for byte — so a card rendered from
    the stream (which is every live card: the client renders the frame the
    moment it lands) carried the trace's own vocabulary. Live, a follow-up
    that could not be pinned to a shown figure read *"referent resolution
    confidence 0.40"* under the question, and a model's probability is a
    fact about this platform's internals, never one an analyst is asked to
    weigh mid-investigation.

    One exception, and it is not copy: the engine's
    ``CLARIFICATION_NO_OPTIONS`` marker survives. It is a SHAPE instruction
    — the engine's own declaration that it has nothing answerable to offer,
    which decides whether the card is a question or a statement — and the
    renderer strips it before display precisely because it is not a
    sentence for a human. Dropping it here would silently retire the
    refusal register, so the one string this function preserves is the one
    string no reader ever sees.

    Full fidelity is unchanged: the raw reason is on the trace, on the
    trace endpoint, and on the terminal frame under ``debug``.
    """
    copy = clarification_reason_copy(reason)
    if _NO_OPTIONS_MARKER not in (reason or ""):
        return copy
    return f"{copy}; {_NO_OPTIONS_MARKER}" if copy else _NO_OPTIONS_MARKER
