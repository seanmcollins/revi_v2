"""Plain-language copy for the §12 error codes, at the API boundary.

The engine's error messages are written for the engine: they name probes,
entities, contracts and bases because that is what a §6.6 refusal is about
and what a trace must record. They were also the text a default-mode user
read, verbatim — a revenue-cycle director who asked "what was our denial
rate for 2025 versus 2024?" got

    DATE_BASIS_INVALID: date basis 'remit' is not bound for entity 'claim'

which names three internal concepts, offers no next step, and reads as a
crash. The precision is not the problem; the audience is.

So this module maps each code to one plain sentence that says what
happened and what to do about it, and the technical message rides
underneath rather than being thrown away:

- the ``code`` is unchanged — clients branch on it, and §12 fixes it;
- the envelope SHAPE is unchanged (``code``/``message``/``correlation_id``);
- the engine message is preserved in the logs and in the recorded trace,
  and is appended to the published message when the turn ran with
  ``debug`` on — the setting that already means "show me the working".

A code with no entry here falls back to the engine's own message: an
unmapped code publishing a cheerful generic sentence would be worse than
the jargon, because it would say less while sounding certain.
"""

from __future__ import annotations

from collections.abc import Mapping

from revi_kernel.errors import ErrorCode

#: One plain sentence per code: what happened, then what to do.
#:
#: Written for the analyst, not the operator. Each says which of the two
#: things is true — "the platform will not" (a governed refusal, and the
#: recovery is a different question) or "the platform cannot right now" (an
#: operational failure, and the recovery is to retry or to escalate) —
#: because those want opposite responses and the code alone does not say.
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

    ``technical`` is the engine's own sentence. It is appended when
    ``debug`` is on — the session setting that already means "show me the
    working" — and otherwise kept out of the user's way. It is never
    *dropped*: the caller logs it and the turn's trace records it either
    way, so nothing published here is the only copy of anything.

    ``details`` is the error's structured, client-safe mapping. It is read
    only to echo back a term the analyst themselves supplied (see
    :data:`_ECHO_CODES`) — being told "nothing here is called 'flurb_rate'"
    is the difference between a refusal you can act on and one you cannot.
    """
    plain = PLAIN_MESSAGES.get(code)
    if plain is None:
        # An unmapped code says the engine's own words rather than a
        # reassuring generic sentence that would carry less information
        # while sounding more certain.
        return technical
    if code in _ECHO_CODES:
        plain = f"{plain}{_named_terms(details)}"
    return f"{plain} [{code.value}: {technical}]" if debug else plain
