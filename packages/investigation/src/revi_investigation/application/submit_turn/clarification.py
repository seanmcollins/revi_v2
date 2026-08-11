"""Clarification options: proposing them, validating them, and resuming from one."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date
from typing import Any

from revi_investigation.application.interpretation import (
    PendingClarification,
)
from revi_investigation.application.ports import (
    RegisteredReferent,
)
from revi_investigation.application.refinement_llm import (
    REFERENT_HANDLE,
)
from revi_investigation.application.submit_turn.types import _predicate_label, _TurnState
from revi_investigation.application.validation import (
    map_predicates,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import (
    Investigation,
    Session,
)
from revi_investigation.domain.turns import (
    ClarificationBinding,
    ClarificationRequest,
)
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
)
from revi_kernel.filters import (
    EMPTY_SCOPE,
    Predicate,
    PredicateOp,
    and_merge,
    iter_predicates,
)
from revi_kernel.refs import DimensionRef, MetricRef

#: How a refuted value is recorded on the turn that refuted it. Parsed back
#: so a LATER clarification cannot re-offer the value the engine has already
#: proved does not exist.
_REFUTED_VALUES = re.compile(r"^PREDICATE_VALUE_UNMATCHED: \S+ \[(?P<values>.*?)\]")


_QUOTED = re.compile(r"'([^']+)'|\"([^\"]+)\"")


def refuted_values(reasons: Iterable[str]) -> frozenset[str]:
    """Every dimension value this session has already proved does not exist."""
    out: set[str] = set()
    for reason in reasons:
        match = _REFUTED_VALUES.match(reason.strip())
        if match is None:
            continue
        for single, double in _QUOTED.findall(match.group("values")):
            value = (single or double).strip()
            if value:
                out.add(value.casefold())
    return frozenset(out)


def drop_refuted_options(
    clarification: ClarificationRequest, refuted: frozenset[str]
) -> ClarificationRequest:
    """The same question, minus every option naming a refuted value.

    A reply of ``"Federal Medicare"`` — typed verbatim from the options the
    platform had just offered — came back with three new options, two of
    which re-proposed the hallucinated payer the platform had CORRECTLY
    refused one turn earlier ("UnitedHealthcare, limited to the Federal
    Medicare financial class"; "The Federal Medicare payer instead of
    UnitedHealthcare"). Three turns, zero answers, and the loop was closed
    by the platform's own suggestions.

    The existence check that produced the refusal is the check every option
    must pass. Nothing is rewritten: an option naming a value this session
    has proved absent is dropped, and if that empties the list the question
    says so rather than shipping a choice with nothing in it.
    """
    if not refuted or not clarification.options:
        return clarification
    kept = tuple(
        option
        for option in clarification.options
        if not any(value in option.casefold() for value in refuted)
    )
    if len(kept) == len(clarification.options):
        return clarification
    if kept:
        return replace(clarification, options=kept, bindings=_bindings_for(clarification, kept))
    return replace(
        clarification,
        question=(
            f"{clarification.question} (I had suggestions here and dropped them: each one "
            "named a value this data does not hold, which is the thing I already refused. "
            "Name a different one, or ask me what exists.)"
        ),
        options=(),
        bindings=(),
        reason=f"{clarification.reason}; all generated options named a refuted value",
    )


#: An option that is itself a question — e.g. "Which metric are you asking
#: about? — I mean the last figure you charted." An option is a sentence the
#: analyst SENDS BACK; a question sent back resolves nothing, and the reader
#: is left choosing between answering and being asked again.
#:
#: Matched on the shape rather than on a keyword list: an interrogative
#: opener plus a question mark. "What if we exclude Medicare?" would be a
#: legitimate thing to say and is not one of these — it has no
#: interrogative opener asking the ANALYST to supply the thing this
#: platform is missing.
_INTERROGATIVE_OPTION = re.compile(
    r"^\s*(?:what|which|who|why|how|when|where|do|does|did|are|is|should|could|would)\b"
    r"[^?]*\?",
    re.IGNORECASE,
)


def _drop_interrogative_options(
    clarification: ClarificationRequest,
) -> ClarificationRequest:
    """The same question, minus every "option" that is another question."""
    if not clarification.options:
        return clarification
    kept = tuple(
        option
        for option in clarification.options
        if _INTERROGATIVE_OPTION.match(option) is None
    )
    if len(kept) == len(clarification.options):
        return clarification
    if not kept:
        # Better an honest optionless card than a row of buttons that ask
        # the reader the same thing the heading does; ``_no_options_card``
        # already renders that state as a statement of what is needed.
        return replace(
            clarification,
            options=(),
            bindings=(),
            reason=(
                f"{clarification.reason}; every generated option was itself a question, "
                "so none of them could be sent back as an answer"
            ),
        )
    return replace(clarification, options=kept, bindings=_bindings_for(clarification, kept))


def _bindings_for(
    clarification: ClarificationRequest, kept: tuple[str, ...]
) -> tuple[ClarificationBinding, ...]:
    """The bindings of the options that survived a drop.

    Dropping an option must drop its meaning with it: a binding left behind
    for an option nobody can see is a resolution the analyst never chose.
    """
    surviving = {" ".join(option.split()).casefold().rstrip(".") for option in kept}
    return tuple(
        binding
        for binding in clarification.bindings
        if " ".join(binding.option.split()).casefold().rstrip(".") in surviving
    )


#: How far back a clarification option's DRY RUN looks. Deliberately the
#: widest window the load will admit — a value check asks "does this exist
#: in the data", and asking it over one narrow month would refuse a payer
#: that is simply quiet in that month. The observed-value read is cached per
#: (watermark, entity, dimension, window), so every option in a session
#: shares one read.
_OPTION_CHECK_YEARS = 3


def _option_window(session: Session) -> AbsoluteWindowModel:
    """The window a clarification option is dry-run over (see above)."""
    end = session.watermark.newest_data_date
    floor = session.watermark.oldest_data_date
    start = date(end.year - _OPTION_CHECK_YEARS, 1, 1)
    return AbsoluteWindowModel(start=max(start, floor) if floor is not None else start, end=end)


def _with_chosen_values(
    spec: AnalysisSpec, chosen: tuple[tuple[str, tuple[str, ...]], ...]
) -> AnalysisSpec:
    """Substitute the values an analyst picked from a value clarification.

    Every predicate on a chosen dimension is REPLACED, not added to: the
    clarification exists because the value in the question does not exist
    in the data, so carrying it alongside the real one re-raises the
    refusal that started the dialogue. The dimension the analyst never
    mentioned is untouched,
    and a dimension the re-interpretation dropped is re-added, because the
    choice is the analyst's and it must survive the model's second reading.
    """
    if not chosen:
        return spec
    values_by_dimension = {dimension: values for dimension, values in chosen if values}
    if not values_by_dimension:
        return spec

    def substitute(predicate: Predicate) -> Predicate:
        values = values_by_dimension.get(predicate.dimension.id)
        if values is None:
            return predicate
        op = PredicateOp.IN if len(values) > 1 else PredicateOp.EQ
        return replace(predicate, op=op, values=tuple(values))

    scope = map_predicates(spec.context.scope, substitute)
    present = {p.dimension.id for p in iter_predicates(scope)}
    missing = [
        Predicate(
            dimension=DimensionRef(dimension),
            op=PredicateOp.IN if len(values) > 1 else PredicateOp.EQ,
            values=tuple(values),
        )
        for dimension, values in values_by_dimension.items()
        if dimension not in present
    ]
    if missing:
        scope = and_merge(scope, *missing)
    return spec.with_context(replace(spec.context, scope=scope))


def _with_binding(spec: AnalysisSpec, binding: ClarificationBinding | None) -> AnalysisSpec:
    """Pin the ids a clarification option stands for onto an interpretation.

    The option is not a suggestion the model may re-litigate: the analyst
    tapped a thing this platform named, in this platform's own ids, and
    those ids win over whatever a second reading of the sentence proposes.
    Everything the option is silent about — window, comparison, cuts it
    does not name — is left exactly as the sentence was read.
    """
    if binding is None:
        return spec
    if binding.metric_ids:
        spec = replace(spec, measures=tuple(MetricRef(m) for m in binding.metric_ids))
    if binding.dimension_ids:
        spec = replace(spec, dimensions=tuple(DimensionRef(d) for d in binding.dimension_ids))
    return _with_chosen_values(spec, binding.scope)


def _with_resumed_context(
    spec: AnalysisSpec,
    resume: AnalysisSpec | None,
    window_explicit: bool,
    *,
    continuation: bool = False,
) -> tuple[AnalysisSpec, bool, list[str]]:
    """Carry the standing thread's context onto the answer that continues it.

    A clarification interrupts a THREAD, and the thread's window, filters,
    comparison and cohort belong to the analyst: "break that down by CARC
    code." on a Meridian / imaging / July thread came back with
    ``filters: []``, ``cohort: null`` and a three-year window, narrated as
    a first turn.

    THE SAME IS TRUE OF A FOLLOW-UP THAT NEVER STUMBLED. This machinery was
    wired only to the clarification-resume path, which inverted the
    product: a question the model read cleanly — "denial rate for June
    2026" then "show me denial rate by facility" — was silently
    re-defaulted to the last full month, while a question that fumbled into
    a clarification got June back. ``continuation`` is that second caller:
    the same carries, the same refusals, and a disclosure sentence that is
    TRUE of it (nothing interrupted anything — the conversation simply
    stated a period earlier and has not changed it).

    Applied only where the continuing sentence states nothing itself, so a
    follow-up that names its own period keeps it, and a dimension the
    analyst re-scoped is never widened back. Every carry is disclosed: an
    inherited window the analyst did not say out loud is an assumption, and
    §2.8 assumptions are published, not buried.
    """
    if resume is None:
        return spec, window_explicit, []
    notes: list[str] = []
    context = spec.context
    if not window_explicit and resume.context.window.range != context.window.range:
        carried = resume.context.window.range
        context = replace(context, window=resume.context.window)
        window_explicit = True
        notes.append(
            "resumed_context: your question named no period, so it is measured over the one "
            f"this conversation has been reading ({carried.start.isoformat()}.."
            f"{carried.end.isoformat()}) rather than a default one. Say a period if you want a "
            "different one."
            if continuation
            else "resumed_context: this answers a question that interrupted an existing thread, "
            f"so it is measured over that thread's window ({carried.start.isoformat()}.."
            f"{carried.end.isoformat()}) rather than a default one. Say a period if you want a "
            "different one."
        )
    if context.comparison is None and resume.context.comparison is not None:
        context = replace(context, comparison=resume.context.comparison)
        notes.append(
            "resumed_context: the comparison this conversation has been reading against "
            f"({resume.context.comparison.window.range.start.isoformat()}.."
            f"{resume.context.comparison.window.range.end.isoformat()}) is carried onto this "
            "answer."
            if continuation
            else "resumed_context: the comparison the interrupted thread was reading against "
            f"({resume.context.comparison.window.range.start.isoformat()}.."
            f"{resume.context.comparison.window.range.end.isoformat()}) is carried onto this "
            "answer."
        )
    constrained = {p.dimension.id for p in iter_predicates(context.scope)}
    # A dimension this turn CUTS BY is a dimension it is asking about across
    # its whole population, and pinning it to the thread's one value answers
    # a different question under the asked question's heading: "Give me a
    # payer scorecard for July 2026" once inherited ``payer eq [Atlas
    # Commercial]`` from the thread it interrupted and published one payer's
    # A/R as the scorecard.
    cut_by = {ref.id for ref in spec.dimensions}
    inherited = [
        predicate
        for predicate in iter_predicates(resume.context.scope)
        if predicate.dimension.id not in constrained and predicate.dimension.id not in cut_by
    ]
    declined = [
        predicate
        for predicate in iter_predicates(resume.context.scope)
        if predicate.dimension.id not in constrained and predicate.dimension.id in cut_by
    ]
    if inherited:
        context = replace(context, scope=and_merge(context.scope, *inherited))
        thread = "this conversation is scoped by" if continuation else (
            "the interrupted thread was scoped by"
        )
        notes.append(
            f"resumed_context: the filters {thread} are carried onto "
            "this answer — " + "; ".join(_predicate_label(p) for p in inherited) + "."
        )
    if declined:
        # Said, not silently dropped: the analyst can see which scope the
        # thread had and that this answer deliberately widened past it.
        lead = "this conversation is scoped by" if continuation else (
            "the interrupted thread was scoped by"
        )
        notes.append(
            f"resumed_context: {lead} "
            + "; ".join(_predicate_label(p) for p in declined)
            + ", and this question breaks out BY that same cut — so the filter is NOT carried "
            "and the figures below cover the whole population. Name it again if you wanted "
            "just that one."
        )
    if context.cohort is None and resume.context.cohort is not None:
        context = replace(context, cohort=resume.context.cohort)
        scoped = "this conversation is" if continuation else "the interrupted thread was"
        notes.append(
            f"resumed_context: the pinned population ({resume.context.cohort.id}) {scoped} "
            "scoped to is carried onto this answer."
        )
    return spec.with_context(context), window_explicit, notes


def claim_referent_predicates(
    spec: AnalysisSpec, entries: Sequence[RegisteredReferent]
) -> tuple[AnalysisSpec, list[str]]:
    """Take every referent handle back out of the scope before it is judged.

    Defence in depth behind ``_referent_resume``. A predicate whose value
    is ``F1`` is not a claim about the data; it is a claim about
    something this platform published, and the value-existence guard has no
    business refusing it as a missing facility. Where the registry knows
    what the handle stood for — a row IS a ``(dimension, value)`` pair — the
    predicate is rewritten to that pair; where it does not, the predicate is
    dropped, and either way the substitution is disclosed.
    """
    predicates = list(iter_predicates(spec.context.scope))
    # Rewritten only where the rewrite is provably meaning-preserving: a
    # flat conjunction of POSITIVE membership tests. Taking a value out of
    # one side of an OR changes what the other side means, and dropping a
    # NOT turns an exclusion into an inclusion — a scope this engine did
    # not build is one it must not silently edit. Anything else keeps its
    # handle and gets the value-existence guard's honest refusal, which is
    # worse copy but a true statement.
    if not predicates or and_merge(*predicates) != spec.context.scope:
        return spec, []
    if any(p.op not in (PredicateOp.EQ, PredicateOp.IN) for p in predicates):
        return spec, []
    known = {entry.referent.value: entry.dimension_value for entry in entries}
    notes: list[str] = []
    kept_predicates: list[Predicate] = []
    additions: list[Predicate] = []
    for predicate in predicates:
        claimed = {
            handle: known[handle]
            for value in predicate.values
            if REFERENT_HANDLE.fullmatch(str(value).strip())
            and (handle := str(value).strip().upper()) in known
        }
        if not claimed:
            kept_predicates.append(predicate)
            continue
        for handle, pair in claimed.items():
            if pair is None:
                notes.append(
                    f"referent_claimed: {handle} is a handle this session published, not a "
                    f"{predicate.dimension.id} value, so it was not applied as a filter."
                )
                continue
            dimension, value = pair
            notes.append(
                f"referent_claimed: {handle} is the row this session published for "
                f"{dimension} {value!r}, so it was read as that rather than as a "
                f"{predicate.dimension.id} value this warehouse does not hold."
            )
            additions.append(
                Predicate(dimension=DimensionRef(dimension), op=PredicateOp.EQ, values=(value,))
            )
        remaining = tuple(
            value for value in predicate.values if str(value).strip().upper() not in claimed
        )
        if remaining:
            kept_predicates.append(
                replace(
                    predicate,
                    op=PredicateOp.IN if len(remaining) > 1 else PredicateOp.EQ,
                    values=remaining,
                )
            )
    if not notes:
        return spec, []
    constrained = {p.dimension.id for p in kept_predicates}
    kept_predicates.extend(p for p in additions if p.dimension.id not in constrained)
    scope = and_merge(*kept_predicates) if kept_predicates else EMPTY_SCOPE
    return spec.with_context(replace(spec.context, scope=scope)), notes


def scope_names_a_handle(spec: AnalysisSpec) -> bool:
    """Does this scope filter on something SHAPED like a referent handle?

    The pure precondition for :func:`claim_referent_predicates` doing
    anything at all, so the registry read behind it is not paid on every
    new-investigation turn — a first turn has no referents to claim and a
    handle-shaped filter value is rare on any turn.
    """
    return any(
        REFERENT_HANDLE.fullmatch(str(value).strip())
        for predicate in iter_predicates(spec.context.scope)
        for value in predicate.values
    )


def _option_window_assumed(spec: AnalysisSpec) -> str:
    """Say out loud that a resumed answer's window is the platform's default.

    The fallback path builds its spec from the option's ids alone, and the
    only window available there is the widest one the load admits. That is
    a decision the analyst did not make, so it is disclosed as one — the
    silence is what let a whole-warehouse total be published under a
    disclosure claiming a July question had been resumed.
    """
    window = spec.context.window.range
    return (
        "window_assumed: your question did not resolve to a period on this reading, so the "
        f"answer covers {window.start.isoformat()}..{window.end.isoformat()} — everything this "
        "load holds — rather than a period you named. Say the period you want and I will re-run "
        "it."
    )


def _bindings_from_trace(payload: Mapping[str, Any]) -> tuple[ClarificationBinding, ...]:
    """Rebuild the option bindings a clarification turn recorded.

    Read back off the trace for the same reason ``_pending_clarification``
    reads the lineage: a turn is a stateless request and the session may
    resume in another process. A record written before this field existed
    simply yields nothing, and the reply falls back to being read as text.
    """
    raw = payload.get("clarification_bindings") or ()
    out: list[ClarificationBinding] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        option, kind = entry.get("option"), entry.get("kind")
        if not isinstance(option, str) or not isinstance(kind, str):
            continue
        scope: list[tuple[str, tuple[str, ...]]] = []
        for item in entry.get("scope") or ():
            if not isinstance(item, Mapping):
                continue
            dimension = item.get("dimension")
            if isinstance(dimension, str):
                scope.append(
                    (dimension, tuple(str(v) for v in (item.get("values") or ())))
                )
        basis, playbook_id = entry.get("basis"), entry.get("playbook_id")
        out.append(
            ClarificationBinding(
                option=option,
                kind=kind,
                metric_ids=tuple(str(m) for m in (entry.get("metric_ids") or ())),
                dimension_ids=tuple(str(d) for d in (entry.get("dimension_ids") or ())),
                playbook_id=playbook_id if isinstance(playbook_id, str) else None,
                scope=tuple(scope),
                basis=basis if isinstance(basis, str) else None,
            )
        )
    return tuple(out)


#: A reply that opens like this and matches no offered option is a new
#: question, not an answer to the one on screen. Deliberately narrow —
#: fragments ("just imaging", "the last full month", "denied dollars") do
#: not match, and those are what a real clarification answer looks like.
_FRESH_QUESTION = re.compile(
    r"^\s*(?:what|which|who|why|how|when|where|show me|give me|list|compare|break\s+down|"
    r"tell me)\b",
    re.IGNORECASE,
)


def _answers_pending(reply: str, pending: PendingClarification) -> bool:
    """Does this utterance answer the question that is on screen?

    True whenever it matches an option (verbatim or by binding), or when it
    reads as a fragment — the shape of every genuine clarification answer.
    False only for a self-contained question that matches nothing offered,
    which is the case that used to be swallowed under a false disclosure.
    """
    text = reply.strip()
    if not text:
        return True
    if pending.binding_for(text) is not None:
        return True
    folded = " ".join(text.split()).casefold().rstrip(".")
    for option in pending.options:
        candidate = " ".join(option.split()).casefold().rstrip(".")
        if folded == candidate or folded in candidate or candidate in folded:
            return True
    return _FRESH_QUESTION.match(text) is None


#: Marks a clarification the analyst cannot tap their way out of, so the
#: client renders it as a statement of what the platform needs rather than
#: as a question above an empty row of buttons.
NO_OPTIONS_REASON = "CLARIFICATION_NO_OPTIONS"


#: Words that carry no identity in a reply to "which of these did you mean?"
#: — function words, and the counting/superlative words that describe a
#: choice without naming one ("the two biggest commercial ones").
_UNSELECTIVE_WORDS = frozenset(
    {
        "all", "and", "any", "are", "both", "but", "each", "for", "from", "how",
        "its", "just", "largest", "least", "biggest", "bigger", "smallest",
        "smaller", "most", "much", "many", "one", "ones", "only", "other", "our",
        "out", "please", "rest", "same", "several", "some", "that", "the", "their",
        "them", "then", "these", "they", "this", "those", "three", "top", "two",
        "want", "was", "were", "what", "which", "with", "you", "your", "four",
        "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    }
)


def _identifying_words(text: str) -> frozenset[str]:
    return frozenset(
        word
        for word in re.findall(r"[A-Za-z]{3,}", text.casefold())
        if word not in _UNSELECTIVE_WORDS
    )


def options_named(reply: str, options: Sequence[str]) -> tuple[str, ...]:
    """Which of the offered options the analyst's reply actually names.

    The value-existence refusal — "there is no payer named
    'UnitedHealthcare' in this data", all twelve real values enumerated —
    used to be replayed BYTE-IDENTICALLY for any reply that was not a
    verbatim value: same
    question, same twelve options, same reason, for a reply ("the two
    biggest commercial ones") that had narrowed the twelve to two.

    Word overlap only, and only on words that identify: a reply naming a
    count or a superlative names no value, and this must never invent one.
    The BEST overlap wins outright — "federal medicare" names 'Federal
    Medicare' and not also 'Summit Peak Medicare Advantage', which shares
    one word with it and none of the ones that pick it out.
    """
    words = _identifying_words(reply)
    if not words:
        return ()
    scored = [(len(words & _identifying_words(option)), option) for option in options]
    best = max((score for score, _ in scored), default=0)
    if best == 0:
        return ()
    return tuple(option for score, option in scored if score == best)


def _subject_option(parent: Investigation | None) -> ClarificationBinding | None:
    """The thread's own subject, as an option that can be applied.

    A ``metric_cut`` over ids this platform published a moment ago:
    deterministic, dry-runnable, and the one recovery that is guaranteed
    to be on-subject. ``None`` when the session has no analytical answer to
    point at — there is nothing to commit to, and §2.8 forbids guessing one.
    """
    if parent is None or not parent.spec.measures:
        return None
    metrics = tuple(ref.id for ref in parent.spec.measures)
    cuts = tuple(ref.id for ref in parent.spec.dimensions)
    label = (
        "Answer it for "
        + ", ".join(metrics)
        + (" by " + ", ".join(cuts) if cuts else "")
        + " — the answer already on screen"
    )
    return ClarificationBinding(
        option=label, kind="metric_cut", metric_ids=metrics, dimension_ids=cuts
    )


def _no_options_card(clarification: ClarificationRequest) -> ClarificationRequest:
    """Label a clarification that offers nothing to choose from.

    A one-option clarification never reaches here — it is applied, not
    asked (see ``_lone_binding``) — so "fewer than two" means zero, and
    zero is an error card: the question keeps its text, and the reason
    carries the marker a renderer keys the card shape off. Without it,
    sessions reach the page with ``options: []`` and no buttons at all.
    """
    if clarification.options:
        return clarification
    reason = clarification.reason or ""
    if NO_OPTIONS_REASON in reason:
        return clarification
    # Appended, never prefixed: the reason's own opening code is what every
    # other reader keys off, and moving it would break them to label this.
    return replace(
        clarification,
        reason=f"{reason}; {NO_OPTIONS_REASON}" if reason else NO_OPTIONS_REASON,
    )


#: Marks a thread that has asked its allowance of questions and is stating
#: the impasse instead of asking another. Applied ONCE: the funnel reaches
#: the guard from two sites, and a second application nests the reason
#: inside itself.
CLARIFICATION_NOT_CONVERGING_REASON = "CLARIFICATION_NOT_CONVERGING"


#: Marks a question that was ASKED AGAIN because the reply could not be
#: matched — narrowed where the reply narrowed it, and named as a repeat
#: either way, so the second ask is never mistaken for the first.
CLARIFICATION_REPEATED_REASON = "CLARIFICATION_REPEATED"


#: Marks a clarification whose option set the DATA reduced to one. The
#: option is stated and offered; it is never selected on the analyst's
#: behalf.
CLARIFICATION_SOLE_SURVIVOR_REASON = "CLARIFICATION_SOLE_SURVIVOR"


#: Markers for the two places this ENGINE authors the single option rather
#: than being left with it. A commitment to the subject already on screen is
#: not a survivor of a cull — nothing was dropped to reach it — so the
#: "state, never select" rule above does not apply to it, and applying it is
#: exactly what stops "why did it go up" going round again.
CLARIFICATION_CONVERGED_REASON = "CLARIFICATION_CONVERGED"


CLARIFICATION_MEASURE_SETTLED_REASON = "CLARIFICATION_MEASURE_SETTLED"


#: Clarifications the subject commitment must never replace.
#:
#: Converging on the thread's subject is right when the platform could not
#: work out WHICH of several things was meant. It is wrong when the
#: platform is saying a thing does not exist: answering "I couldn't find a
#: definition for that term" with the denial rate already on screen does
#: not answer it, it changes the subject. §2.8's objection to committing —
#: that it would mean inventing coverage — applies exactly here.
NOT_CONVERGIBLE_REASONS = (
    "no pack content matched",
    "UNSUPPORTED_CONCEPT",
    "TURN_BUDGET_EXHAUSTED",
    "WINDOW_OUT_OF_RANGE",
    "PREDICATE_VALUE_UNMATCHED",
)


_COMMITTED_REASONS = (CLARIFICATION_CONVERGED_REASON, CLARIFICATION_MEASURE_SETTLED_REASON)


#: A clarification asking the analyst to name the measure. Narrow on
#: purpose: it fires on the question this engine composes for that, never
#: on a question that merely contains the word "metric".
_ASKS_WHICH_MEASURE = re.compile(
    r"\bwhich\s+(?:\w+\s+){0,2}?(?:metric|measure|figure|number)\b"
    r"|\bwhat\s+(?:metric|measure)\s+(?:are|do|did|would)\b",
    re.IGNORECASE,
)


#: A clarification asking WHICH result a bare "that" or "it" points at.
#: The measure question's twin, and the one the live corpus tripped over:
#: a parent that measured denial rate by month, a follow-up reading "which
#: payer is driving that?", and three near-identical options each of which
#: was "break the one thing on screen out by payer". Narrow in the same way
#: — it fires on the questions this engine composes for a referent-free
#: anaphora, never on a question that merely contains the word "that".
_ASKS_WHICH_REFERENT = re.compile(
    r"\brefers?\s+to\s+something\s+in\s+the\s+(?:previous|last)\s+answer\b"
    r"|\bwhich\s+(?:result|finding|row|one)\s+(?:should|do|did|are|would)\b"
    r"|\bwhich\s+(?:result|finding|row)\b",
    re.IGNORECASE,
)

#: A clarification whose two readings produce the SAME population for this
#: answer and differ only in whether the narrowing survives to the next one:
#: "Do you want the previous result re-run filtered to Atlas Commercial, or
#: should Atlas Commercial be pinned as a filter for the rest of the
#: session?"
#:
#: THE POPULATION TEST. A wrong guess here changes no row that gets counted,
#: so it is not a question — it is a follow-up affordance, and it belongs
#: after the number rather than in front of it. Narrow on purpose: it wants
#: the word for the persistent thing AND the span it would persist over, so
#: an ordinary question about a filter does not trip it.
ASKS_WHETHER_TO_PIN = re.compile(
    r"\bpin(?:ned|ning)?\b[^?]{0,120}?\b(?:for the (?:rest of the )?session"
    r"|for the rest of (?:the|this) (?:session|conversation)"
    r"|across (?:the|this) (?:session|conversation)"
    r"|until (?:you|I) (?:clear|remove|change) it)",
    re.IGNORECASE,
)


#: A referent-free anaphora in the ANALYST's own words. Checked alongside
#: the clarification above so the guard fires on the case it is named for —
#: a follow-up that points at the answer on screen — and not on a question
#: that named its own subject and was asked to disambiguate something else.
ANAPHORIC_SUBJECT = re.compile(
    r"(?<!\w)(?:that|it|this|those|these|them)(?!\w)",
    re.IGNORECASE,
)


#: A superlative asking WHICH ROW, in the analyst's own words.
#:
#: The worst answer in the live corpus: "denial rate last quarter excluding
#: Medicare" → "and excluding Medicaid too?" → **"which one is worst now"**,
#: answered `denial rate: 8.1%` — a single org-level scalar — with the
#: narrative explaining that *"with only one measure in hand there is
#: nothing to rank it against, so 'worst' here names it by default rather
#: than by comparison"*. Two turns of carving payer types out of the
#: population, and "which one" was read as *which measure*.
#:
#: A superlative resolves on the axis the conversation is already cutting.
#: This is the half of that rule the measure guard needs: the words that
#: mean a ROW rather than a metric.
ENTITY_SUPERLATIVE = re.compile(
    r"\bwhich\s+(?:one|ones|of\s+(?:them|these|those))\b"
    r"|\b(?:the\s+)?(?:worst|best|biggest|largest|highest|lowest|smallest)\s+one\b"
    r"|\bwhich\s+\w+\s+(?:is|was|are|were)\s+(?:the\s+)?"
    r"(?:worst|best|biggest|largest|highest|lowest|smallest)\b",
    re.IGNORECASE,
)


def cuts_an_entity_axis(spec: AnalysisSpec) -> bool:
    """Is this conversation operating on a dimension rather than a measure?

    True when the answer on screen is broken out by one, and true when it is
    merely SCOPED by one: two turns spent excluding payer types are two
    turns spent on the payer axis, whether or not the figure was cut by it.
    """
    return bool(spec.dimensions) or any(iter_predicates(spec.context.effective_scope()))


def _state_the_survivor(
    clarification: ClarificationRequest, lone: ClarificationBinding
) -> ClarificationRequest:
    """Say that one option survived, without answering as if it were chosen.

    The refusal keeps the lead — it is the first thing in the question text
    and the first thing the analyst reads — and the surviving
    option is named as what this warehouse could answer INSTEAD, with the
    difference said out loud. What the previous behaviour published was the
    survivor's answer under the original question's heading, with the
    refusal moved into a warning; a reader who trusted the heading was
    reading the wrong number.
    """
    reason = clarification.reason or ""
    return replace(
        clarification,
        question=(
            f"{clarification.question} Only one of the options I could offer survives what "
            f"this data load holds: “{lone.option}”. That answers less than you "
            "asked for, so I have not run it on your behalf — say it and I will, or say what "
            "you want in your own words."
        ),
        reason=(
            f"{reason}; {CLARIFICATION_SOLE_SURVIVOR_REASON}: one option left after value and "
            "plan validation; stated rather than applied"
            if reason
            else f"{CLARIFICATION_SOLE_SURVIVOR_REASON}: one option left after validation"
        ),
    )


def _no_replay(
    state: _TurnState, clarification: ClarificationRequest, narrowed: Sequence[str]
) -> ClarificationRequest:
    """The same impasse, said differently — never the same words twice.

    Two outcomes, both honest and neither a replay: when the
    reply narrowed the offered set, the question narrows with it and says
    what it read; when it narrowed nothing, the question stops being a
    question about values and becomes a plain statement that the reply
    could not be matched to any of them.
    """
    pending = state.pending
    asked = pending.streak + 1 if pending is not None else 2
    reply = state.utterance or state.question
    if narrowed and len(narrowed) < len(clarification.options):
        return replace(
            clarification,
            question=(
                f"I read {reply!r} as pointing at "
                f"{', '.join(repr(option) for option in narrowed)} — that is as far as I can "
                "narrow it without guessing which you meant. Name the one you want."
            ),
            options=tuple(narrowed),
            bindings=_bindings_for(clarification, tuple(narrowed)),
            reason=(
                f"{clarification.reason}; {CLARIFICATION_REPEATED_REASON}: ask {asked} "
                f"narrowed {len(clarification.options)} option(s) to {len(narrowed)} from the "
                "reply"
            ),
        )
    return replace(
        clarification,
        question=(
            f"I could not match {reply!r} to any of the "
            f"{len(clarification.options)} value(s) I offered, and asking you the same "
            "question a second time would not change that. Name one of them exactly — or "
            "ask me which of them is largest and I will measure it, which is a question I "
            "can answer."
        ),
        reason=(
            f"{clarification.reason}; {CLARIFICATION_REPEATED_REASON}: ask {asked} would have "
            "repeated ask 1 verbatim; the reply matched none of the offered values"
        ),
    )
