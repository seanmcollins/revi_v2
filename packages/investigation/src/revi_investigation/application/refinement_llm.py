"""Follow-up-turn LLM services (design §8.2 steps 2-3) and the DTO → domain
operator converter.

The LLM's job here is exactly two closed-set choices: which shown referents
an utterance points at (validated against the live registry —
``REFERENT_NOT_FOUND`` on drift) and which of the twelve refinement
operators express the request (``AMBIGUOUS_REFINEMENT`` → clarification
when the model can't say). Everything downstream of the validated
operators is deterministic; the typed-gesture path enters at the converter
with no LLM involvement at all.

Both services split their empty-handed cases the way interpretation does
(see its module docstring): a readable answer that never arrived is worth
asking again for, a model that had no mapping is not. When the model
proposes ways forward on the "no operators" path they ride along as
``ClarificationRequest.options``, deterministically trimmed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from revi_investigation.application.findings import claimed_rank
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.llm.render import (
    LoadedTemplate,
    load_template,
    render_template,
)
from revi_investigation.application.llm.schemas import (
    AbsoluteWindowModel,
    AddFilterModel,
    AnchoredWindowModel,
    AnyRefinementOperator,
    DrillIntoModel,
    ExpandModel,
    ExplainModel,
    PivotModel,
    RankByModel,
    ReferentResolutionResponse,
    RefinementEmissionResponse,
    RemoveFilterModel,
    ResetContextModel,
    SetComparisonModel,
    SetDimensionsModel,
    SetGrainModel,
    SetWindowModel,
    WindowSpecModel,
    clarification_options,
    sanitize_json_schema,
)
from revi_investigation.application.ports import (
    DEFAULT_LLM_CALL_POLICY,
    LanguageModelPort,
    LlmCallPolicy,
    LlmFailureKind,
    LlmUsage,
    RegisteredReferent,
    StructuredLlmRequest,
    failure_note,
    retry_may_help,
)
from revi_investigation.domain.records import Finding
from revi_investigation.domain.refinements import (
    AddFilter,
    DrillInto,
    Expand,
    Explain,
    Pivot,
    RankBy,
    Refinement,
    RemoveFilter,
    ResetContext,
    SetComparison,
    SetDimensions,
    SetGrain,
    SetWindow,
)
from revi_investigation.domain.turns import ClarificationRequest
from revi_kernel.errors import ReferentNotFoundError
from revi_kernel.filters import Predicate, PredicateOp, Scalar
from revi_kernel.refs import (
    DateBasisRef,
    DimensionRef,
    EntityGrain,
    Grain,
    MetricRef,
    ReferentId,
    TimeBucket,
)
from revi_kernel.scope import (
    AbsoluteRange,
    AnchoredRange,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    resolve_anchored,
)

_MIN_RESOLUTION_CONFIDENCE = 0.5
_MAX_REFERENT_LINES = 60

# The same split interpretation makes (see its module docstring): a model
# that read the follow-up and could not compile it wants different words; an
# answer that never arrived in a readable shape wants the same words again.
_RESOLVE_REPHRASE = "Which of the shown items do you mean?"
_RESOLVE_RETRY = "I hit a problem matching that to what I showed you — please try again."
_EMIT_REPHRASE = (
    "I couldn't turn that into a concrete refinement of the current answer — "
    "could you say it another way?"
)
_EMIT_RETRY = "I hit a problem applying that to the current answer — please try again."


def referent_lines(entries: tuple[RegisteredReferent, ...]) -> str:
    """Serialize the live registry for prompts: ids + labels, never data."""
    lines = [f"- {entry.referent.value}: {entry.label}" for entry in entries]
    if not lines:
        return "- (nothing has been shown yet)"
    return "\n".join(lines[-_MAX_REFERENT_LINES:])


#: A referent handle as the platform prints it and the analyst types it
#: back. ``F`` for findings, ``D`` for dimension-value rows (design §7.6).
REFERENT_HANDLE = re.compile(r"\b([FD]\d+)\b", re.IGNORECASE)


def referent_tokens(question: str) -> tuple[str, ...]:
    """Every handle the analyst typed, upper-cased, in first-seen order."""
    return tuple(dict.fromkeys(match.group(1).upper() for match in REFERENT_HANDLE.finditer(question)))


def resolve_referent_tokens(
    question: str, entries: tuple[RegisteredReferent, ...]
) -> tuple[tuple[ReferentResolution, ...], tuple[str, ...]]:
    """Resolve typed handles against the registry — deterministically.

    "Drill into F2" contains no anaphora. F2 is an identifier this platform
    minted, printed, and stored; matching it is a dictionary lookup. It was
    nonetheless sent to a language model on every follow-up turn, which
    bought a call, a latency, and a probability: the model can return a
    different handle, or a confidence below the threshold, and the turn
    that asked to drill into F2 comes back asking which F2 was meant.

    So handles resolve here, before any model call, at confidence 1.0 —
    ``resolutions`` for the ones the registry knows, ``unknown`` for the
    ones it does not (a handle that never existed, or one from a session
    whose registry was rebuilt: the caller says so rather than guessing).
    Named entities the platform itself printed resolve here too — see
    :func:`resolve_named_referents`. Everything else ("the second row", "the
    one above") is anaphora over presentation and still needs the model.
    """
    by_value = {entry.referent.value: entry for entry in entries}
    resolutions: list[ReferentResolution] = []
    unknown: list[str] = []
    for token in referent_tokens(question):
        entry = by_value.get(token)
        if entry is None:
            unknown.append(token)
            continue
        resolutions.append(
            ReferentResolution(mention=token, referent=entry.referent, confidence=1.0)
        )
    if not resolutions and not unknown:
        resolutions.extend(resolve_named_referents(question, entries))
    return tuple(resolutions), tuple(unknown)


def _mentions(question: str, needle: str) -> bool:
    """Whole-token, case-insensitive containment ("Summit Peak" in …)."""
    if not needle.strip():
        return False
    return re.search(rf"(?<!\w){re.escape(needle.strip())}(?!\w)", question, re.IGNORECASE) is not None


def resolve_named_referents(
    question: str, entries: tuple[RegisteredReferent, ...]
) -> tuple[ReferentResolution, ...]:
    """Bind a follow-up to an entity THIS SESSION named, deterministically.

    One turn after publishing "Summit Peak Medicare Advantage initial denial
    rate up 2.1 points" as F1, the same session asked the analyst whether
    "Summit Peak" was a facility, a payer or a provider. It had just said so
    itself: the row is in the registry with its ``(dimension, value)`` pair
    attached. Asking a model — or asking the analyst — to recover a fact the
    platform published one turn earlier is not caution, it is amnesia.

    Two deterministic shapes, both requiring the answer to be *unambiguous*:

    * the value itself ("Summit Peak Medicare Advantage", or the exact text
      of the row label), and
    * a demonstrative over the dimension ("that payer", "this payer"), which
      binds only when the session has shown exactly one distinct value for
      that dimension.

    Ambiguity is left to the model on purpose. Two payers on screen and
    "that payer" means the model (or the analyst) has to say which — and a
    deterministic guess there would be the confident-wrong answer this rule
    exists to prevent.
    """
    named: dict[str, list[RegisteredReferent]] = {}
    by_dimension: dict[str, set[str]] = {}
    for entry in entries:
        if entry.dimension_value is None:
            continue
        dimension, value = entry.dimension_value
        named.setdefault(value, []).append(entry)
        by_dimension.setdefault(dimension, set()).add(value)

    for value, matches in named.items():
        if not _mentions(question, value):
            continue
        distinct = {entry.dimension_value for entry in matches}
        if len(distinct) != 1:
            continue  # the same text under two dimensions: say which
        # The most recently published row wins: handles are monotonic, so
        # the last one is the one the analyst just read.
        entry = matches[-1]
        return (
            ReferentResolution(mention=value, referent=entry.referent, confidence=1.0),
        )

    for dimension, values in by_dimension.items():
        if len(values) != 1:
            continue
        if not any(
            _mentions(question, f"{word} {dimension}") for word in ("that", "this", "the")
        ):
            continue
        value = next(iter(values))
        entry = next(
            e for e in reversed(entries)
            if e.dimension_value is not None and e.dimension_value[1] == value
        )
        return (
            ReferentResolution(
                mention=f"that {dimension}", referent=entry.referent, confidence=1.0
            ),
        )
    return ()


#: Words that name a row's PLACE in an order that was published.
#:
#: Polarity words — "worst", "best", "biggest", "smallest" — are
#: deliberately absent. Which end of a ranking they point at depends on the
#: metric contract's sign convention AND on the order the analyst asked for
#: (``AskedOrder``), so "the worst one" over a list ranked best-first is
#: the LAST row, not the first. Reading them here would silently drill into
#: the opposite row; they stay with the model, which sees the sentence.
_ORDINAL_NAMES: dict[str, int] = {
    "first": 1, "1st": 1, "top": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
    "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7,
    "eighth": 8, "8th": 8,
    "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
}

#: Sentinel for "whichever position the answer put last".
_LAST_POSITION = -1

_ORDINAL_LAST = ("last", "bottom")

#: What an ordinal may be attached to. "The top one", "the first row" — but
#: never "the top 3", which is a LIMIT the analyst named, not a row they
#: pointed at (``requested_finding_limit`` owns that).
_ORDINAL_NOUN = r"(?:one|row|item|result|entry|finding|record)"

_ORDINAL_WORD_GROUP = "|".join(
    re.escape(word) for word in (*_ORDINAL_NAMES, *_ORDINAL_LAST)
)

_ORDINAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "the top one", "that first row", "the last finding"
    re.compile(
        rf"\b(?:the|that|this)\s+(?P<word>{_ORDINAL_WORD_GROUP})\s+{_ORDINAL_NOUN}\b",
        re.IGNORECASE,
    ),
    # "the top", "the bottom" with no noun — only at a clause end, and
    # only for the two words that cannot also name a calendar period.
    # "first", "second" and "last" are excluded here on purpose:
    # "compare the first quarter to the second" ends on an ordinal that
    # points at a PERIOD, and binding it to a row would answer a different
    # question confidently.
    re.compile(
        r"\b(?:the|that|this)\s+(?P<word>top|bottom)\s*(?=[.,;:!?]|$)",
        re.IGNORECASE,
    ),
    # "#1", "number 2", "rank 3", "no. 1"
    re.compile(
        r"(?:#\s*|\bnumber\s+|\brank\s+|\bno\.?\s+)(?P<digits>\d{1,2})\b",
        re.IGNORECASE,
    ),
)


def _requested_ordinal(question: str) -> tuple[str, int] | None:
    """``(the words the analyst used, the position they name)``, or ``None``."""
    for pattern in _ORDINAL_PATTERNS:
        match = pattern.search(question)
        if match is None:
            continue
        digits = match.groupdict().get("digits")
        if digits is not None:
            position = int(digits)
            return (match.group(0).strip(), position) if position >= 1 else None
        word = (match.group("word") or "").lower()
        if word in _ORDINAL_LAST:
            return match.group(0).strip(), _LAST_POSITION
        return match.group(0).strip(), _ORDINAL_NAMES[word]
    return None


def resolve_ordinal_referent(
    question: str,
    findings: Sequence[Finding],
    entries: tuple[RegisteredReferent, ...],
) -> tuple[ReferentResolution, ...]:
    """Bind "the top one" to the row the previous answer PUT first.

    An answer that published an ordering established that ordering as
    context. "Denial rate by facility last quarter" replied *"Eastmere
    Medical Center ranks #1 of 6"*, captioned its chart *"Ordered by denial
    rate, high to low"*, and wrote *"the ranking puts Eastmere first"* — and
    the very next turn, "drill into the top one" came back refusing to
    choose, on the grounds that picking a row would invent an order the
    context had not established. The context had established it. The engine
    was reading the registry (ids and labels) and the spec's ``rank_by``,
    neither of which carries what the ANSWER said.

    So the ordering is read where it was published: off the findings' own
    claimed positions (:func:`claimed_rank`). Three consequences follow, all
    of them the honest ones:

    * an answer that claimed no positions resolves nothing — a population
      too bounded to order does not acquire an order because someone asked
      for its top row;
    * a position no finding claims resolves nothing ("the fourth one" over
      three published rows is a question, not a referent);
    * ties do not block it. Three facilities within a rounding hair of each
      other still have a published #1, and the caveat about the tie is in
      the prose the analyst just read. Refusing here would mean the
      platform's own ranking is not something it will stand behind.
    """
    requested = _requested_ordinal(question)
    if requested is None:
        return ()
    mention, wanted = requested
    ranked = [
        (rank, finding)
        for rank, finding in ((claimed_rank(f), f) for f in findings)
        if rank is not None
    ]
    if not ranked:
        return ()
    if wanted == _LAST_POSITION:
        wanted = max(rank for rank, _ in ranked)
    matches = [finding for rank, finding in ranked if rank == wanted]
    if len(matches) != 1:
        return ()
    by_value = {entry.referent.value: entry for entry in entries}
    entry = by_value.get(matches[0].referent.value)
    if entry is None:
        return ()  # the registry was rebuilt: let the model see the drift
    return (ReferentResolution(mention=mention, referent=entry.referent, confidence=1.0),)


@dataclass(frozen=True, slots=True)
class ReferentResolution:
    mention: str
    referent: ReferentId
    confidence: float


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    resolutions: tuple[ReferentResolution, ...]
    clarification: ClarificationRequest | None
    usage: LlmUsage
    template_hash: str
    #: Why the call came back empty-handed, when it did — as data, so a
    #: trace consumer never has to parse the clarification's English.
    failure: LlmFailureKind | None = None


class ResolveReferentsService:
    """LLM anaphora resolution against the live registry (design §7.6)."""

    def __init__(self, llm: LanguageModelPort) -> None:
        self._llm = llm
        self._template: LoadedTemplate = load_template("resolve_referents", "v1")
        self._schema = sanitize_json_schema(ReferentResolutionResponse.model_json_schema())

    async def resolve(
        self,
        question: str,
        entries: tuple[RegisteredReferent, ...],
        *,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> ResolutionOutcome:
        prompt = render_template(
            self._template.text,
            {"question": question, "referents": referent_lines(entries)},
        )
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
                policy=policy,
            )
        )
        if result.output is None:
            clarify = self._unusable(
                "referent resolution returned no structured output", result.failure
            )
            return ResolutionOutcome(
                (), clarify, result.usage, self._template.sha256, result.failure
            )
        try:
            parsed = ReferentResolutionResponse.model_validate(dict(result.output))
        except ValidationError:
            clarify = self._unusable(
                "referent resolution failed schema validation", LlmFailureKind.SCHEMA
            )
            return ResolutionOutcome(
                (), clarify, result.usage, self._template.sha256, LlmFailureKind.SCHEMA
            )
        by_value = {entry.referent.value: entry.referent for entry in entries}
        resolutions: list[ReferentResolution] = []
        for item in parsed.resolutions:
            referent = by_value.get(item.referent_id)
            if referent is None:
                raise ReferentNotFoundError(
                    f"resolved referent {item.referent_id!r} is not in the live registry",
                    details={"referent": item.referent_id, "mention": item.mention},
                )
            if item.confidence < _MIN_RESOLUTION_CONFIDENCE:
                return ResolutionOutcome(
                    (),
                    ClarificationRequest(
                        question=(
                            f"By {item.mention!r}, do you mean {item.referent_id} "
                            f"({by_value[item.referent_id].kind.value})?"
                        ),
                        reason=f"referent resolution confidence {item.confidence:.2f}",
                    ),
                    result.usage,
                    self._template.sha256,
                )
            resolutions.append(
                ReferentResolution(
                    mention=item.mention, referent=referent, confidence=item.confidence
                )
            )
        return ResolutionOutcome(tuple(resolutions), None, result.usage, self._template.sha256)

    @staticmethod
    def _unusable(reason: str, failure: LlmFailureKind | None) -> ClarificationRequest:
        """Nothing resolvable came back: ask again, or ask differently."""
        retry = retry_may_help(failure)
        return ClarificationRequest(
            question=_RESOLVE_RETRY if retry else _RESOLVE_REPHRASE,
            reason=reason + failure_note(failure),
        )


@dataclass(frozen=True, slots=True)
class EmissionOutcome:
    operators: tuple[AnyRefinementOperator, ...] | None
    rationale: str
    clarification: ClarificationRequest | None
    usage: LlmUsage
    template_hash: str
    #: See :attr:`ResolutionOutcome.failure`.
    failure: LlmFailureKind | None = None


class EmitRefinementsService:
    """LLM compilation of a follow-up into the closed operator set (§7.4)."""

    def __init__(self, llm: LanguageModelPort) -> None:
        self._llm = llm
        self._template: LoadedTemplate = load_template("emit_refinements", "v1")
        self._schema = sanitize_json_schema(RefinementEmissionResponse.model_json_schema())

    async def emit(
        self,
        question: str,
        *,
        context_summary: str,
        entries: tuple[RegisteredReferent, ...],
        resolutions: tuple[ReferentResolution, ...],
        dimension_lines: str,
        metric_lines: str,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> EmissionOutcome:
        # Resolved referents ride in as STRUCTURE, not as a bare id: the
        # label the analyst saw and, when the row was a single dimension
        # value, that (dimension, value) pair. A model asked to compile
        # "drill into F2" into an operator needs to know F2 is a payer row
        # before it can choose between DrillInto and AddFilter; without it
        # the compilation was a second guess layered on the first.
        by_value = {entry.referent.value: entry for entry in entries}
        resolved_lines: list[str] = []
        for r in resolutions:
            entry = by_value.get(r.referent.value)
            detail = f" — {entry.label}" if entry is not None else ""
            if entry is not None and entry.dimension_value is not None:
                dimension, value = entry.dimension_value
                detail += f" [{dimension} = {value}]"
            resolved_lines.append(
                f"- {r.mention!r} -> {r.referent.value} "
                f"(confidence {r.confidence:.2f}){detail}"
            )
        resolution_lines = "\n".join(resolved_lines) or "- (none)"
        prompt = render_template(
            self._template.text,
            {
                "question": question,
                "context": context_summary,
                "referents": referent_lines(entries),
                "resolutions": resolution_lines,
                "dimensions": dimension_lines,
                "metrics": metric_lines,
            },
        )
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
                policy=policy,
            )
        )
        if result.output is None:
            return self._unusable(
                "AMBIGUOUS_REFINEMENT: no structured operator emission",
                result.failure,
                result.usage,
            )
        try:
            parsed = RefinementEmissionResponse.model_validate(dict(result.output))
        except ValidationError:
            return self._unusable(
                "AMBIGUOUS_REFINEMENT: operator emission failed schema validation",
                LlmFailureKind.SCHEMA,
                result.usage,
            )
        if not parsed.operators:
            return EmissionOutcome(
                operators=None,
                rationale=parsed.rationale,
                clarification=ClarificationRequest(
                    question="What would you like to change about the current answer?",
                    options=clarification_options(parsed.clarification_options),
                    reason=f"AMBIGUOUS_REFINEMENT: {parsed.rationale or 'no operators emitted'}",
                ),
                usage=result.usage,
                template_hash=self._template.sha256,
            )
        return EmissionOutcome(
            operators=tuple(parsed.operators),
            rationale=parsed.rationale,
            clarification=None,
            usage=result.usage,
            template_hash=self._template.sha256,
        )

    def _unusable(
        self, reason: str, failure: LlmFailureKind | None, usage: LlmUsage
    ) -> EmissionOutcome:
        """No operators came back at all — ask again, or ask differently.

        No options on this path by construction: there is no parsed response
        for the model to have proposed any on.
        """
        retry = retry_may_help(failure)
        return EmissionOutcome(
            operators=None,
            rationale="",
            clarification=ClarificationRequest(
                question=_EMIT_RETRY if retry else _EMIT_REPHRASE,
                reason=reason + failure_note(failure),
            ),
            usage=usage,
            template_hash=self._template.sha256,
            failure=failure,
        )


# ---------------------------------------------------------------------------
# DTO → domain conversion (shared by the LLM path and the typed-gesture path)


def _scalar(value: str | int | float | bool | None) -> Scalar:
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _window(
    model: WindowSpecModel | AnchoredWindowModel | AbsoluteWindowModel,
) -> RelativeRange | AbsoluteRange:
    """A window DTO → the kernel shape a ``SetWindow`` operator carries.

    A NAMED period resolves to concrete dates here, exactly as
    interpretation resolves one: the calendar arithmetic lives in
    :func:`revi_kernel.scope.resolve_anchored` and nowhere else, so
    "set the window to Q2 2026" means the same dates whether it arrived as
    a sentence, a typed gesture or a replayed trace.
    """
    if isinstance(model, AbsoluteWindowModel):
        return AbsoluteRange(start=model.start, end=model.end)
    if isinstance(model, AnchoredWindowModel):
        return resolve_anchored(
            AnchoredRange(unit=TimeUnit(model.unit), year=model.year, index=model.index)
        )
    return RelativeRange(
        quantity=Decimal(model.quantity), unit=TimeUnit(model.unit), mode=RangeMode(model.mode)
    )


def _referent(value: str, registry_index: dict[str, ReferentId]) -> ReferentId:
    referent = registry_index.get(value)
    if referent is None:
        raise ReferentNotFoundError(
            f"operator targets referent {value!r}, which is not in the live registry",
            details={"referent": value},
        )
    return referent


def to_domain_operators(
    operators: tuple[AnyRefinementOperator, ...],
    registry_index: dict[str, ReferentId],
) -> tuple[Refinement, ...]:
    """Convert validated DTO operators into domain operators.

    ``drill_into``/``explain`` targets resolve through the live registry
    (``REFERENT_NOT_FOUND`` on unknown handles). A ``set_comparison`` with
    a ``custom`` range means a CUSTOM comparison regardless of ``kind``.
    """
    out: list[Refinement] = []
    for op in operators:
        if isinstance(op, SetDimensionsModel):
            out.append(SetDimensions(tuple(DimensionRef(d) for d in op.dimensions)))
        elif isinstance(op, AddFilterModel):
            out.append(
                AddFilter(
                    Predicate(
                        dimension=DimensionRef(op.dimension),
                        op=PredicateOp(op.predicate_op),
                        values=tuple(_scalar(v) for v in op.values),
                    )
                )
            )
        elif isinstance(op, RemoveFilterModel):
            out.append(RemoveFilter(DimensionRef(op.dimension)))
        elif isinstance(op, SetWindowModel):
            out.append(
                SetWindow(
                    window=_window(op.window),
                    basis=DateBasisRef(op.basis.lower()) if op.basis is not None else None,
                )
            )
        elif isinstance(op, SetComparisonModel):
            if op.custom is not None:
                out.append(
                    SetComparison(
                        kind=ComparisonKind.CUSTOM,
                        custom=AbsoluteRange(start=op.custom.start, end=op.custom.end),
                    )
                )
            elif op.kind is not None:
                out.append(SetComparison(kind=ComparisonKind(op.kind)))
            else:
                out.append(SetComparison(kind=None))
        elif isinstance(op, SetGrainModel):
            bucket = TimeBucket(op.time_bucket) if op.time_bucket is not None else None
            out.append(SetGrain(Grain(EntityGrain(op.entity), bucket)))
        elif isinstance(op, DrillIntoModel):
            out.append(DrillInto(_referent(op.target, registry_index)))
        elif isinstance(op, PivotModel):
            out.append(Pivot(tuple(MetricRef(m) for m in op.measures)))
        elif isinstance(op, ExplainModel):
            out.append(Explain(_referent(op.target, registry_index)))
        elif isinstance(op, RankByModel):
            out.append(RankBy(by=MetricRef(op.by), descending=op.descending))
        elif isinstance(op, ExpandModel):
            out.append(Expand(limit=op.limit))
        else:  # ResetContextModel — the union is closed
            assert isinstance(op, ResetContextModel)
            out.append(ResetContext(keep_pins=op.keep_pins))
    return tuple(out)
