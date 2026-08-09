"""Turn classification and question interpretation (design §8.1 steps 3-7).

The LLM proposes; deterministic code disposes. Every id the model returns
is validated against the pinned pack snapshot and semantic catalog — an
unknown metric/dimension/playbook/concept is ``UNSUPPORTED_CONCEPT``, and
model ambiguity (missing structured output, an explicit clarification, or
low classification confidence) becomes a :class:`ClarificationRequest`,
which is a successful outcome, never a guess.

A clarification says which of two things happened, because the recoveries
differ: ``LlmFailureKind.SCHEMA`` means the answer never arrived in a
readable shape and asking again may simply work, while a model that
declined (or a demo script with no entry) will decline identically until
the question changes. When the model proposes ways forward they ride along
as ``ClarificationRequest.options`` — deterministically trimmed, never
invented here.

Window resolution happens exactly once, here: the anchor is the session
watermark's ``newest_data_date`` — the newest activity the load can see,
never the load's own clock and never wall-clock today — and the concrete
dates are stored on the spec (replay uses the stored dates). A window
nobody asked for is stated as an assumption rather than left in the debug
payload. The date basis defaults to the
primary governing metric's primary basis; an explicit basis is validated
against the contract's ``allowed_date_bases`` (``DATE_BASIS_INVALID``) and
then against what this warehouse actually binds at the metric's grain —
see :mod:`revi_investigation.application.date_basis`, which is why the
window's basis (and therefore the context header) can never name a basis
no probe was able to read.

The DEFINITIONAL path answers from governed pack content with provenance
and ZERO probes: lead-in phrases are stripped deterministically and the
remainder resolves through ``PackSnapshot.resolve_term`` semantics via the
:class:`PackPort` seam ("what is PR3" → the PR group code and CARC 3).

``from_typed_spec`` is the typed twin of ``interpret``: a caller that
already knows what it wants states it in the typed vocabulary and the same
deterministic disposal runs — pack/catalog id validation, basis legality,
one-shot window resolution — with zero model calls. It exists so that
surfaces with an already-typed intent (a portfolio card, a chart click in
a fresh session) can open an investigation instead of being told there is
nothing to refine.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import ValidationError

from revi_calculation_contracts.contract import MetricContract
from revi_catalog_contracts.model import CatalogSnapshot, normalize_synonym
from revi_investigation.application.anchoring import window_anchor
from revi_investigation.application.capability_ports import PackPort, TermDefinition
from revi_investigation.application.date_basis import resolve_answerable_basis
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.llm.render import (
    LoadedTemplate,
    load_template,
    render_template,
)
from revi_investigation.application.llm.schemas import (
    AnchoredWindowModel,
    GroundedOptionModel,
    InterpretationResponse,
    TurnClassificationResponse,
    clarification_options,
    sanitize_json_schema,
)
from revi_investigation.application.ports import (
    DEFAULT_LLM_CALL_POLICY,
    LanguageModelPort,
    LlmCallPolicy,
    LlmFailureKind,
    LlmUsage,
    StructuredLlmRequest,
    failure_note,
    retry_may_help,
)
from revi_investigation.application.validation import contract_pinned_values
from revi_investigation.domain.context import (
    AnalysisSpec,
    AskedDirection,
    AskedMagnitude,
    AskedOrder,
    InvestigationContext,
)
from revi_investigation.domain.records import Session
from revi_investigation.domain.turns import (
    ClarificationBinding,
    ClarificationRequest,
    TurnClass,
    TurnClassification,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    WindowSpecModel,
)
from revi_kernel.errors import UnsupportedConceptError
from revi_kernel.filters import (
    EMPTY_SCOPE,
    FilterExpr,
    Predicate,
    PredicateOp,
    Scalar,
    and_merge,
)
from revi_kernel.refs import DateBasisRef, DimensionRef, Grain, MetricRef, TimeBucket
from revi_kernel.scope import (
    AbsoluteRange,
    AnchoredRange,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    TimeWindow,
    derive_comparison,
    resolve_anchored,
    resolve_window,
)
from revi_kernel.watermark import DataWatermark

_MIN_CLASSIFICATION_CONFIDENCE = 0.5
_DEFAULT_WINDOW = RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS)

#: The contract ``kind`` that reports a balance at a moment rather than a
#: quantity accumulated over a window (round-2 FN-2).
_SNAPSHOT_KIND = "snapshot"
_DESCRIPTION_CLIP = 160

# Deterministic definitional lead-ins, longest first.
_DEFINITIONAL_LEAD_INS = (
    "tell me about",
    "what is the meaning of",
    "what is a",
    "what is an",
    "what does",
    "what are",
    "meaning of",
    "what is",
    "what's",
    "whats",
    "define",
    "explain",
)

# Coming up empty has two honest shapes and they want opposite advice. A
# model that read the utterance and had no mapping for it wants a different
# wording; an answer that never arrived in a readable shape wants the same
# wording again. Telling an analyst to rephrase a question that was never
# the problem is how a platform teaches people it cannot be trusted.
_CLASSIFY_REPHRASE = "I couldn't confidently read that request — could you rephrase it?"
_CLASSIFY_RETRY = "I hit a problem reading that just now — please try again."
_INTERPRET_REPHRASE = "I couldn't map that question onto governed content — could you rephrase it?"
_INTERPRET_RETRY = "I hit a problem working that out just now — please try again."


class Coverage(StrEnum):
    """How much of a named period this load actually holds."""

    #: Every day of it is inside the data range.
    FULL = "full"
    #: Some of it is; the rest has not landed (or predates the warehouse).
    PARTIAL = "partial"
    #: None of it is.
    OUTSIDE = "outside"


def window_coverage(window: AbsoluteRange, watermark: DataWatermark) -> Coverage:
    """Does this load hold the period that was asked about? (round-3 FN-11)

    The upper bound is always known — a load knows the newest activity it
    can see — so a period that starts after it is unanswerable, full stop.
    The lower bound is known only when the adapter publishes
    ``oldest_data_date``; ``None`` means "unknown", so a period before the
    data is treated as covered rather than refused on a guess.
    """
    newest = watermark.newest_data_date
    oldest = watermark.oldest_data_date
    if window.start > newest or (oldest is not None and window.end < oldest):
        return Coverage.OUTSIDE
    if window.end > newest or (oldest is not None and window.start < oldest):
        return Coverage.PARTIAL
    return Coverage.FULL


def data_range_phrase(watermark: DataWatermark) -> str:
    """What this load actually covers, as a clause the analyst reads.

    Two shapes, because the load knows two different things. Both name the
    end date, which is the fact the analyst needs; only a load that
    publishes ``oldest_data_date`` can name where the data starts.
    """
    newest = watermark.newest_data_date.isoformat()
    if watermark.oldest_data_date is None:
        return f"this data ends {newest}"
    return f"this data covers {watermark.oldest_data_date.isoformat()}..{newest}"


def absolute_label(window: AbsoluteRange) -> str:
    return f"{window.start.isoformat()}..{window.end.isoformat()}"


#: Relative period vocabulary the resolver understands directly, mapped to
#: the closed kernel shape it resolves to (round-3 R3-16).
#:
#: "What should my denial team work first THIS WEEK to recover the most
#: cash?" and "what is at risk in the NEXT 30 DAYS" both came back with
#: ``window_assumed: the question named no period`` — a false statement
#: about the analyst's own sentence, printed directly under it, over a
#: silent widening of a 7-day horizon to a 31-day month. Everything here is
#: anchored on the newest data date, exactly like "June 2026".
_RELATIVE_WINDOW_VOCABULARY: tuple[tuple[str, str, RelativeRange | None], ...] = (
    ("week to date", "week to date", RelativeRange(Decimal(1), TimeUnit.WEEK, RangeMode.TO_DATE)),
    ("wtd", "WTD", RelativeRange(Decimal(1), TimeUnit.WEEK, RangeMode.TO_DATE)),
    ("this week", "this week", RelativeRange(Decimal(1), TimeUnit.WEEK, RangeMode.TO_DATE)),
    ("last week", "last week", RelativeRange(Decimal(1), TimeUnit.WEEK, RangeMode.FULL_PERIODS)),
    ("month to date", "month to date", RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.TO_DATE)),
    ("mtd", "MTD", RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.TO_DATE)),
    ("this month", "this month", RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.TO_DATE)),
    ("last month", "last month", RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS)),
    ("quarter to date", "quarter to date", RelativeRange(Decimal(1), TimeUnit.QUARTER, RangeMode.TO_DATE)),
    ("qtd", "QTD", RelativeRange(Decimal(1), TimeUnit.QUARTER, RangeMode.TO_DATE)),
    ("this quarter", "this quarter", RelativeRange(Decimal(1), TimeUnit.QUARTER, RangeMode.TO_DATE)),
    ("year to date", "year to date", RelativeRange(Decimal(1), TimeUnit.YEAR, RangeMode.TO_DATE)),
    ("ytd", "YTD", RelativeRange(Decimal(1), TimeUnit.YEAR, RangeMode.TO_DATE)),
    ("today", "today", RelativeRange(Decimal(1), TimeUnit.DAY, RangeMode.TO_DATE)),
    ("yesterday", "yesterday", RelativeRange(Decimal(1), TimeUnit.DAY, RangeMode.FULL_PERIODS)),
)

#: ``last 30 days`` / ``trailing 6 months`` / ``past 2 weeks``.
_TRAILING_PHRASE = re.compile(
    r"\b(?:last|past|trailing|previous|prior)\s+(\d{1,3})\s+(day|week|month|quarter|year)s?\b",
    re.IGNORECASE,
)

#: ``next 30 days`` / ``coming 2 weeks`` — a horizon for WORK, not a
#: measurement window. There is no data after the newest data date and
#: pretending a month of history answers it is the substitution R3-16 names.
_FORWARD_PHRASE = re.compile(
    r"\b(?:next|coming|upcoming|following)\s+(\d{1,3})\s+(day|week|month|quarter|year)s?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RelativePeriod:
    """A relative period the analyst named, as quoted and as resolved."""

    #: Exactly what the analyst wrote, for quoting back to them.
    quoted: str
    #: ``None`` for a forward horizon: no window can measure it.
    relative: RelativeRange | None
    forward: bool = False


def recognize_relative_period(question: str) -> RelativePeriod | None:
    """The relative period this utterance names, if it names one.

    Deterministic and closed-vocabulary — the model is asked for a window
    first and this is what catches the phrases it drops. Nothing here reads
    the question's *subject*; it reads time words, which is the same job
    ``resolve_window`` already does for "June 2026".
    """
    text = question.lower()
    forward = _FORWARD_PHRASE.search(question)
    if forward is not None:
        return RelativePeriod(quoted=forward.group(0), relative=None, forward=True)
    trailing = _TRAILING_PHRASE.search(question)
    if trailing is not None:
        return RelativePeriod(
            quoted=trailing.group(0),
            relative=RelativeRange(
                quantity=Decimal(trailing.group(1)),
                unit=TimeUnit(trailing.group(2).lower()),
                mode=RangeMode.TRAILING,
            ),
        )
    for needle, label, relative in _RELATIVE_WINDOW_VOCABULARY:
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text):
            return RelativePeriod(quoted=label, relative=relative)
    return None


#: The sizes an assertion can name, as a multiple of the prior level. A
#: closed vocabulary of magnitude words, read off the utterance the same way
#: the period vocabulary above is: "doubled" is not a direction, it is a
#: quantity, and the premise check tested only the sign (round-3 R3-03).
_MAGNITUDE_WORDS: tuple[tuple[str, Decimal], ...] = (
    ("quadrupled", Decimal(4)),
    ("quadruple", Decimal(4)),
    ("fourfold", Decimal(4)),
    ("four-fold", Decimal(4)),
    ("4x", Decimal(4)),
    ("tripled", Decimal(3)),
    ("triple", Decimal(3)),
    ("threefold", Decimal(3)),
    ("three-fold", Decimal(3)),
    ("3x", Decimal(3)),
    ("doubled", Decimal(2)),
    ("double", Decimal(2)),
    ("twice", Decimal(2)),
    ("twofold", Decimal(2)),
    ("two-fold", Decimal(2)),
    ("2x", Decimal(2)),
    ("halved", Decimal("0.5")),
    ("halve", Decimal("0.5")),
    ("by half", Decimal("0.5")),
    ("in half", Decimal("0.5")),
    ("cut in half", Decimal("0.5")),
)

#: Sizes stated as arithmetic rather than as a word — "jumped 300%", "up by
#: 40%", "5x". Round-5 A-02(3): the closed table held "halved" and not
#: "halve", "quadrupled" and not "quadruple", "2x"/"3x" and no numeric form
#: at all, and an unparsed size silently became a confirmed DIRECTION —
#: "Premise confirmed … It happened: -8.0%" over a question that said HALVE.
_PERCENT_MOVE = re.compile(
    r"(?<!\w)(?:by\s+)?(\d{1,4}(?:\.\d+)?)\s*(?:%|percent)(?!\w)", re.IGNORECASE
)
_MULTIPLE_MOVE = re.compile(r"(?<!\w)(\d{1,3}(?:\.\d+)?)\s*(?:x|-fold|fold)(?!\w)", re.IGNORECASE)

#: Language that ASSERTS a size without naming one this platform can read.
#: Its only job is to stop a size the engine could not parse from being
#: published as a confirmed direction.
_SIZE_ASSERTED = re.compile(
    r"(?<!\w)(?:\d{1,4}(?:\.\d+)?\s*(?:%|percent|x|-?fold)|half|halve[ds]?|double[ds]?|"
    r"triple[ds]?|quadruple[ds]?|twice|[a-z]+fold|order of magnitude)(?!\w)",
    re.IGNORECASE,
)


#: Words that make a bare percentage a DECREASE. Used only when the
#: interpretation did not hand over a direction to read it against.
_FALLING = re.compile(
    r"(?<!\w)(?:fall|falls|fell|fallen|drop|drops|dropped|declin\w*|down|decreas\w*|"
    r"shrink\w*|shrank|shrunk|lower\w*|reduc\w*|lost|losing|slid|slipped)(?!\w)",
    re.IGNORECASE,
)


def asserted_multiple(
    question: str,
    direction_asserted: bool,
    *,
    proposed: float | None = None,
    direction: str | None = None,
) -> Decimal | None:
    """The size a question ASSERTS, when it asserts one.

    Only meaningful alongside an asserted direction: "which payer doubled?"
    is a query over cells, while "why did denials double?" is a premise
    about the aggregate, and only the second is checked here.

    The closed table is a deterministic OVERRIDE, not the sole source
    (round-5 A-02d): a word this platform recognises is resolved here and
    never asked of a model, and ``proposed`` — the interpretation's own
    typed reading — fills the long tail of phrasings a table cannot hold.
    """
    if not direction_asserted:
        return None
    text = question.lower()
    for word, multiple in _MAGNITUDE_WORDS:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
            return multiple
    match = _MULTIPLE_MOVE.search(text)
    if match is not None:
        return Decimal(match.group(1))
    match = _PERCENT_MOVE.search(text)
    if match is not None:
        # A percentage names a CHANGE; the multiple is the level it lands
        # on. Which way it points comes from the interpretation's own
        # closed direction set where there is one, and from the sentence
        # only as a fallback — a table of falling verbs is a worse source
        # than the reading the model already produced.
        change = Decimal(match.group(1)) / Decimal(100)
        # "improved"/"worsened" are polarity-relative and resolve to a sign
        # only against a metric contract, which is not in scope here; only
        # the two absolute directions decide it outright.
        falling = (
            direction == "decrease"
            if direction in ("increase", "decrease")
            else _FALLING.search(text) is not None
        )
        return Decimal(1) + (-change if falling else change)
    if proposed is not None and proposed > 0:
        return Decimal(str(proposed))
    return None


def size_asserted_unparsed(question: str, direction_asserted: bool) -> bool:
    """Did the question assert a SIZE this platform could not read?

    The signal that keeps ``premise_holds`` from being rendered as "Premise
    confirmed" over a magnitude nobody verified. Direction-only is a legal
    premise ("why are denials up?"); direction-only *after* a size was
    asserted is a verdict on a claim that was never checked.
    """
    if not direction_asserted:
        return False
    if asserted_multiple(question, direction_asserted) is not None:
        return False
    return _SIZE_ASSERTED.search(question) is not None


#: What "all of them" means when the analyst does not name a number. Large
#: enough to stop being a truncation on any real breakdown in this catalog,
#: and still a bound: an unbounded answer is a different failure.
FULL_RANKING_LIMIT = 100

#: Counts an analyst says out loud rather than types. "Show me all twelve"
#: names TWELVE, and resolving it to the 100-row ``FULL_RANKING_LIMIT``
#: made the shortfall notice quote a ceiling this platform invented back at
#: an analyst who had named a number (round-5 A-04).
_NUMBER_WORDS: dict[str, int] = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

_EXPLICIT_LIMIT = re.compile(r"\b(?:top|first|bottom|worst|best)\s+(\d{1,3})\b", re.IGNORECASE)
_COUNTED_ALL = re.compile(
    r"\b(?:all|every|each)\b[^.?!]{0,40}?\b(\d{1,3})\b", re.IGNORECASE
)
_COUNTED_ALL_WORD = re.compile(
    r"\b(?:all|every|each)\b[^.?!]{0,40}?\b(" + "|".join(_NUMBER_WORDS) + r")\b",
    re.IGNORECASE,
)
_UNCOUNTED_ALL = re.compile(
    r"\b(?:all of them|all twelve|every one|every single|full (?:ranking|list|breakdown)"
    r"|complete (?:ranking|list|breakdown)|not just (?:the )?(?:top )?\w+)\b",
    re.IGNORECASE,
)


def requested_finding_limit(question: str) -> int | None:
    """How many rows the analyst asked to see, when they asked (R3-04).

    ``top_n = 3`` was a constructor default nothing could lift, so "show me
    all twelve payers, not just three" returned three findings, and "every
    one of our 12 payers" returned three with no omission notice. A count
    the question names is an instruction.
    """
    explicit = _EXPLICIT_LIMIT.search(question)
    if explicit is not None:
        return int(explicit.group(1))
    counted = _COUNTED_ALL.search(question)
    if counted is not None:
        return int(counted.group(1))
    spelled = _COUNTED_ALL_WORD.search(question)
    if spelled is not None:
        return _NUMBER_WORDS[spelled.group(1).lower()]
    if _UNCOUNTED_ALL.search(question):
        return FULL_RANKING_LIMIT
    return None


#: Reason fragment stating how many proposed options this platform removed
#: before the analyst ever saw them. Read by the turn engine's clarification
#: funnel, which treats a lone SURVIVOR differently from a lone proposal.
OPTIONS_DROPPED_MARKER = "options_dropped="


def _dropped_marker(offered: int, kept: int) -> str:
    return f"; {OPTIONS_DROPPED_MARKER}{offered - kept}" if offered > kept else ""


#: Every word an utterance may contain and still be *only* a statement
#: about how many rows to show. Deliberately a closed list: the moment a
#: sentence names a metric, a dimension, a payer or a period it is asking
#: for something new, and "show me the top 5 payers by denial rate" must
#: not be mistaken for "show me all twelve".
_DISPLAY_SCOPE_WORDS = frozenset(
    [
        "a", "all", "also", "and", "another", "any", "as", "be", "but", "can",
        "could", "display", "do", "entire", "entirely", "entries", "every",
        "everything", "expand", "full", "get", "give", "i", "just", "let",
        "list", "listed", "lot", "me", "more", "much", "next", "not", "now",
        "of", "ok", "okay", "only", "open", "other", "others", "out", "please",
        "pull", "rank", "ranked", "remaining", "rest", "results", "return",
        "rows", "same", "see", "set", "show", "showing", "shown", "some",
        "the", "them", "then", "there", "these", "they", "this", "those",
        "to", "top", "up", "us", "view", "want", "was", "way", "we", "were",
        "what", "whole", "would", "you", "your",
        "zero", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
        "fifteen", "twenty", "thirty", "forty", "fifty", "hundred",
    ]
)


def display_scope_limit(question: str) -> int | None:
    """A request that changes ONLY how many rows are shown, and how many.

    Round-4 R4-11, 6 of 6 personas, zero successes: "show me all twelve"
    reached the classifier, came back at confidence 0.45-0.50, and was
    answered with a clarification asking whether the twelve had already
    been computed — a question the platform could answer itself from the
    frame it was holding. The limit lift then sat on the one path a
    follow-up can never reach.

    A display-scope request is decidable without a model, so it is decided
    without one: the utterance must name a count, and every word in it must
    be a word that says nothing about WHAT to measure. Anything naming a
    metric, a cut, a value or a period falls straight through to normal
    interpretation, where it belongs.
    """
    limit = requested_finding_limit(question)
    if limit is None:
        return None
    for token in re.findall(r"[A-Za-z']+", question):
        if token.casefold() not in _DISPLAY_SCOPE_WORDS:
            return None
    return limit


def out_of_range_question(period_label: str, watermark: DataWatermark) -> str:
    """Say which period was asked for and which one exists.

    Live, "…in January 2019" came back with a July 2026 window and the
    warning "the question named no period", rendered directly under the
    analyst's own words. The period was named; it was simply not there.
    """
    return (
        f"You asked about {period_label} — {data_range_phrase(watermark)}, so there is nothing "
        "in that period to answer over. Which of these would you like instead?"
    )


def in_range_options(watermark: DataWatermark) -> tuple[str, ...]:
    """Periods that DO have data, named with the dates they resolve to.

    Each is a relative period this vocabulary already resolves, so an
    analyst who sends one back verbatim gets an answer rather than a second
    clarification (the round-trip rule: never offer what cannot be parsed).
    """
    last_month = resolve_window(
        _DEFAULT_WINDOW,
        window_anchor(watermark, _DEFAULT_WINDOW.mode),
        basis=DateBasisRef("service"),
    ).range
    return (
        f"the last full month ({absolute_label(last_month)})",
        "the last 90 days",
        "the last 12 months",
        "year to date",
    )


@dataclass(frozen=True, slots=True)
class DefinitionalAnswer:
    """A zero-probe answer from governed pack content, with provenance."""

    question: str
    terms: tuple[TermDefinition, ...]
    pack_id: str
    pack_version: str
    pack_snapshot_id: str


@dataclass(frozen=True, slots=True)
class ClassificationOutcome:
    classification: TurnClassification | None
    clarification: ClarificationRequest | None
    usage: LlmUsage
    template_hash: str
    #: Why the call came back empty-handed, when it did. The clarification
    #: reason already spells it for a reader; this carries it as data so a
    #: trace consumer does not have to parse English to chart it.
    failure: LlmFailureKind | None = None


@dataclass(frozen=True, slots=True)
class InterpretedInvestigation:
    spec: AnalysisSpec
    playbook_id: str | None
    window_explicit: bool
    intent_summary: str
    metric_ids: tuple[str, ...]
    dimension_ids: tuple[str, ...]
    concept_ids: tuple[str, ...]
    #: Interpretation decisions the analyst has to be told about, in their
    #: terms — a filter dropped as redundant, a period nobody asked for.
    #: Surfaced as turn warnings; never left in the debug payload alone.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InterpretationOutcome:
    investigation: InterpretedInvestigation | None
    clarification: ClarificationRequest | None
    definitional: DefinitionalAnswer | None
    usage: LlmUsage
    template_hash: str
    #: See :attr:`ClassificationOutcome.failure`.
    failure: LlmFailureKind | None = None


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:_DESCRIPTION_CLIP]


def strip_definitional_lead_in(question: str) -> str:
    """Deterministically strip definitional lead-in phrases and trailing
    filler ("what does PR3 mean?" → "PR3")."""
    text = question.strip().strip("?!.").strip().lower()
    for lead in _DEFINITIONAL_LEAD_INS:
        if text.startswith(lead + " "):
            text = text[len(lead) :].strip()
            break
    for trailer in (" mean", " stand for"):
        if text.endswith(trailer):
            text = text[: -len(trailer)].strip()
    return text


@dataclass(frozen=True, slots=True)
class PendingClarification:
    """A clarification this session asked and has not had answered yet.

    Classification without it is classification without the one fact that
    decides the answer: an utterance is only "an answer to a question I
    haven't asked" if no question is outstanding. Live, a session that
    replied to a clarification with a VERBATIM option string was read fresh
    each time and clarified four turns running — the model was never told
    it had asked anything.
    """

    #: The question the platform put to the analyst.
    question: str
    #: The options it offered, if any — a verbatim reply is the strongest
    #: possible signal that this turn is an answer.
    options: tuple[str, ...] = ()
    #: The analyst's own question that the clarification interrupted, so a
    #: resolved turn can resume it rather than dropping it.
    original_question: str | None = None
    #: How many clarifications this thread has issued back-to-back.
    streak: int = 0
    #: The investigation that ASKED, so an answer can be parented to it.
    #: Round-3 R3-07: the resolved turn was saved as a root node, leaving
    #: the clarification and its own answer in two disconnected trees.
    investigation_id: str | None = None
    #: What each offered option means in governed ids. A reply that matches
    #: one is resolved by lookup, not by re-reading it as language.
    bindings: tuple[ClarificationBinding, ...] = ()

    def binding_for(self, reply: str) -> ClarificationBinding | None:
        wanted = " ".join(reply.split()).casefold().rstrip(".")
        for binding in self.bindings:
            if " ".join(binding.option.split()).casefold().rstrip(".") == wanted:
                return binding
        return None


def render_pending_clarification(pending: PendingClarification | None) -> str:
    """The pending-clarification block for the classification prompt."""
    if pending is None:
        return "No clarification is pending; this utterance stands on its own."
    lines = [
        "A clarification IS pending. The platform asked the analyst:",
        f"  {pending.question}",
    ]
    if pending.options:
        lines.append("and offered these options:")
        lines.extend(f"  - {option}" for option in pending.options)
    if pending.original_question:
        lines.append(f"The question it interrupted was: {pending.original_question}")
    return "\n".join(lines)


class ClassifyTurnService:
    """LLM turn classification against the closed §7.3 taxonomy."""

    def __init__(self, llm: LanguageModelPort) -> None:
        self._llm = llm
        self._template: LoadedTemplate = load_template("classify_turn", "v1")
        self._schema = sanitize_json_schema(TurnClassificationResponse.model_json_schema())

    async def classify(
        self,
        question: str,
        *,
        pending: PendingClarification | None = None,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> ClassificationOutcome:
        prompt = render_template(
            self._template.text,
            {"question": question, "pending": render_pending_clarification(pending)},
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
                "turn classification returned no structured output", result.failure, result.usage
            )
        try:
            parsed = TurnClassificationResponse.model_validate(dict(result.output))
        except ValidationError:
            return self._unusable(
                "turn classification failed schema validation",
                LlmFailureKind.SCHEMA,
                result.usage,
            )
        classification = TurnClassification(
            turn_class=TurnClass(parsed.turn_class),
            confidence=parsed.confidence,
            clarification_question=parsed.clarification_question,
        )
        clarification: ClarificationRequest | None = None
        if (
            classification.clarification_question is not None
            or classification.confidence < _MIN_CLASSIFICATION_CONFIDENCE
        ):
            clarification = ClarificationRequest(
                question=classification.clarification_question
                or "Could you say more about what you'd like to investigate?",
                options=clarification_options(parsed.clarification_options),
                reason=f"turn classification confidence {classification.confidence:.2f}",
            )
        return ClassificationOutcome(
            classification=classification,
            clarification=clarification,
            usage=result.usage,
            template_hash=self._template.sha256,
        )

    def _unusable(
        self, reason: str, failure: LlmFailureKind | None, usage: LlmUsage
    ) -> ClassificationOutcome:
        """No classification came back — ask for the right thing.

        A schema failure is the platform's problem and the analyst's
        question may have been fine, so the ask is "again", not "differently".
        The failure kind rides into the trace either way. No options on this
        path by construction: there is no parsed response for the model to
        have proposed any on.
        """
        retry = retry_may_help(failure)
        return ClassificationOutcome(
            classification=None,
            clarification=ClarificationRequest(
                question=_CLASSIFY_RETRY if retry else _CLASSIFY_REPHRASE,
                reason=reason + failure_note(failure),
            ),
            usage=usage,
            template_hash=self._template.sha256,
            failure=failure,
        )


class InterpretQuestionService:
    """Question → validated AnalysisSpec (or clarification / definitional)."""

    def __init__(self, llm: LanguageModelPort, pack: PackPort, catalog: CatalogSnapshot) -> None:
        self._llm = llm
        self._pack = pack
        self._catalog = catalog
        self._template: LoadedTemplate = load_template("interpret_question", "v1")
        self._schema = sanitize_json_schema(InterpretationResponse.model_json_schema())

    # ---------------------------------------------------------- vocabulary

    def _vocabulary(self) -> dict[str, str]:
        metrics = "\n".join(
            f"- {mid}: {_clip(desc)}" for mid, desc in self._pack.metric_summaries()
        )
        dimensions = "\n".join(
            f"- {dim.id}: {dim.label}" for dim in self._catalog.dimensions if dim.certified
        )
        playbooks = "\n".join(
            f"- {pid}: {_clip(desc)}" for pid, desc in self._pack.playbook_summaries()
        )
        concepts = "\n".join(f"- {cid}: {name}" for cid, name in self._pack.concept_summaries())
        date_bases = ", ".join(basis.id for basis in self._catalog.date_bases)
        return {
            "metrics": metrics,
            "dimensions": dimensions,
            "playbooks": playbooks,
            "concepts": concepts,
            "date_bases": date_bases,
        }

    # ----------------------------------------------------------------- api

    def definitional_match(self, question: str) -> bool:
        """Is this utterance a definitional question, decidably?

        Strictly: the question must *open* with one of the governed
        lead-ins and what remains must resolve in the pack **whole**. Both
        halves matter. Without the lead-in, "denial rate by payer" would
        qualify; with the last-word fallback :meth:`definitional_answer`
        uses for recovery, "what is our net collection rate over the last
        90 days" would resolve on ``days`` and be answered with a
        dictionary entry instead of a number.

        Used only where the alternative is a model call that cannot do
        better: deciding the first utterance of a session, where nothing
        else in the taxonomy is available (see
        ``SubmitTurnService._classification_by_construction``). A lookup
        against governed content is not a guess, so it does not need one.
        """
        stripped = strip_definitional_lead_in(question)
        if not stripped or stripped == question.strip().strip("?!.").strip().lower():
            return False  # no lead-in was present: not phrased as a definition
        return bool(self._pack.resolve_term(stripped))

    def definitional_answer(self, question: str) -> DefinitionalAnswer:
        """Deterministic pack lookup for the DEFINITIONAL path (zero probes)."""
        stripped = strip_definitional_lead_in(question)
        terms = self._pack.resolve_term(stripped) if stripped else ()
        if not terms and " " in stripped:
            terms = self._pack.resolve_term(stripped.split()[-1])
        return DefinitionalAnswer(
            question=question,
            terms=terms,
            pack_id=self._pack.pack_id,
            pack_version=self._pack.pack_version,
            pack_snapshot_id=self._pack.snapshot_id,
        )

    def from_typed_spec(
        self, typed: TypedInvestigationSpec, *, session: Session, turn_id: str
    ) -> InterpretedInvestigation:
        """The typed twin of :meth:`interpret`: same governance, no model.

        A caller that already knows what it wants (a portfolio card, a
        chart click in a fresh session, a saved view) states it in the
        typed vocabulary instead of a sentence. Everything the LLM would
        have *proposed* is supplied; everything deterministic code
        *disposes* is unchanged — every metric id is checked against the
        pinned pack, every dimension (breakdown and scope alike) against
        the semantic catalog, the date basis against the governing
        contract's ``allowed_date_bases``, and the window resolves exactly
        once into stored concrete dates (§6.1) just as it does here.

        Zero LLM calls by construction: nothing on this path touches
        :class:`LanguageModelPort`.
        """
        contracts: list[MetricContract] = []
        for metric_id in typed.metric_ids:
            contract = self._pack.metric(metric_id)
            if contract is None:
                raise UnsupportedConceptError(
                    f"typed metric {metric_id!r} is not in the pack",
                    details={"metric": metric_id},
                )
            contracts.append(contract)
        for dimension_id in typed.dimensions:
            if self._catalog.dimension(dimension_id) is None:
                raise UnsupportedConceptError(
                    f"typed dimension {dimension_id!r} is not in the catalog",
                    details={"dimension": dimension_id},
                )
        primary = contracts[0]
        basis = self._resolve_basis(typed.basis, primary)
        window = self._typed_window(typed.window, basis, session)
        context = InvestigationContext(
            window=window,
            comparison=None,
            scope=self._typed_scope(typed.filters, turn_id),
            cohort=None,
            grain=Grain(primary.entity_grain),
            watermark=session.watermark,
            pack_version=session.pack_version,
        )
        if typed.comparison is not None:
            context = replace(
                context, comparison=derive_comparison(window, ComparisonKind(typed.comparison))
            )
        return InterpretedInvestigation(
            spec=AnalysisSpec(
                context=context,
                measures=tuple(MetricRef(mid) for mid in typed.metric_ids),
                dimensions=tuple(DimensionRef(did) for did in typed.dimensions),
            ),
            playbook_id=None,
            window_explicit=True,
            intent_summary="typed investigation spec (no interpretation)",
            metric_ids=tuple(typed.metric_ids),
            dimension_ids=tuple(typed.dimensions),
            concept_ids=(),
        )

    async def interpret(
        self,
        question: str,
        *,
        session: Session,
        turn_id: str,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
        basis_override: str | None = None,
    ) -> InterpretationOutcome:
        """Interpret one question against the pack, catalog and session.

        ``basis_override`` forces the date basis this turn reads on, in
        place of whatever the model proposes. It exists for exactly one
        caller: a clarification the platform itself asked ("this metric
        cannot be read on the submission basis here — which should I
        use?"), being ANSWERED. The answer is a governed basis id the
        platform offered, so re-running the analyst's original question
        with it applied is a substitution, not a second interpretation
        (round-3 R3-07).
        """
        prompt = render_template(self._template.text, {**self._vocabulary(), "question": question})
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
        template_hash = self._template.sha256
        if result.output is None:
            return self._unusable(
                "interpretation returned no structured output", result.failure, result.usage
            )
        try:
            parsed = InterpretationResponse.model_validate(dict(result.output))
        except ValidationError:
            return self._unusable(
                "interpretation failed schema validation", LlmFailureKind.SCHEMA, result.usage
            )
        options = self._grounded_options(parsed.clarification_options)
        if parsed.clarification:
            if parsed.clarification_options and not options:
                # Every way forward the model proposed named something this
                # pack cannot do. A clarification whose options are all
                # unanswerable is worse than a refusal: it costs the analyst
                # a turn to discover the same "no". Refuse honestly instead
                # — the API's capability copy is written for exactly this.
                raise UnsupportedConceptError(
                    "the question maps onto no governed content, and every alternative "
                    "proposed for it names content this pack does not define",
                    details={
                        "clarification": parsed.clarification,
                        "rejected_options": [o.label for o in parsed.clarification_options],
                    },
                )
            return self._clarify(
                parsed.clarification,
                "model requested clarification"
                # How many ways forward this platform removed before the
                # analyst saw the question. The turn engine reads it to
                # decide whether a lone survivor is a real choice or the
                # last thing left standing (round-4 R4-12 defect 5).
                + _dropped_marker(len(parsed.clarification_options), len(options)),
                result.usage,
                template_hash,
                options=options,
                bindings=self._grounded_bindings(parsed.clarification_options),
            )

        analytical = bool(parsed.metric_ids or parsed.playbook_id or parsed.dimension_ids)
        if parsed.definitional_terms and not analytical:
            answer = self._definitional_from_terms(question, tuple(parsed.definitional_terms))
            return InterpretationOutcome(
                investigation=None,
                clarification=None,
                definitional=answer,
                usage=result.usage,
                template_hash=template_hash,
            )

        # -- validate EVERY returned id against pack/catalog ---------------
        for metric_id in parsed.metric_ids:
            if self._pack.metric(metric_id) is None:
                raise UnsupportedConceptError(
                    f"interpreted metric {metric_id!r} is not in the pack",
                    details={"metric": metric_id},
                )
        if parsed.playbook_id is not None and self._pack.playbook(parsed.playbook_id) is None:
            raise UnsupportedConceptError(
                f"interpreted playbook {parsed.playbook_id!r} is not in the pack",
                details={"playbook": parsed.playbook_id},
            )
        for dimension_id in parsed.dimension_ids:
            if self._catalog.dimension(dimension_id) is None:
                raise UnsupportedConceptError(
                    f"interpreted dimension {dimension_id!r} is not in the catalog",
                    details={"dimension": dimension_id},
                )
        for concept_id in parsed.concept_ids:
            if not self._pack.has_concept(concept_id):
                raise UnsupportedConceptError(
                    f"interpreted concept {concept_id!r} is not in the pack",
                    details={"concept": concept_id},
                )

        governing = self._governing_contracts(parsed)
        if not governing:
            return self._clarify(
                "Which metric or investigation should I use for that?",
                "no governing metric or playbook resolved"
                + _dropped_marker(len(parsed.clarification_options), len(options)),
                result.usage,
                template_hash,
                options=options,
                bindings=self._grounded_bindings(parsed.clarification_options),
            )
        primary = governing[0]

        basis = self._resolve_basis(basis_override or parsed.basis, primary)
        window_explicit = parsed.window is not None
        window, period_label = self._interpreted_window(parsed.window, basis, session)

        notes: list[str] = []
        # The model is asked for a window first; this catches the phrases it
        # drops (round-3 R3-16). "This week" and "the next 30 days" were
        # both answered with ``the question named no period``, printed under
        # the analyst's own sentence, over a silent widening to a 31-day
        # month.
        relative_named: RelativePeriod | None = None
        if not window_explicit:
            relative_named = recognize_relative_period(question)
        if relative_named is not None and relative_named.relative is not None:
            anchor = window_anchor(session.watermark, relative_named.relative.mode)
            window = resolve_window(relative_named.relative, anchor, basis=basis)
            window_explicit = True
            period_label = relative_named.quoted
            notes.append(
                f'window_relative: you said "{relative_named.quoted}", which resolves to '
                f"{window.range.start.isoformat()}..{window.range.end.isoformat()} on the "
                f"{basis.id} basis, anchored on the newest data date "
                f"({session.watermark.newest_data_date.isoformat()})."
            )
        elif relative_named is not None and relative_named.forward:
            # A horizon for WORK, not a measurement window. No data exists
            # after the newest data date, and substituting a month of
            # history for it silently answers a different question.
            notes.append(
                f'window_horizon: you said "{relative_named.quoted}", which names when the work '
                f"happens rather than a period to measure — and this load ends "
                f"{session.watermark.newest_data_date.isoformat()}, so there is no data after "
                f"it. The figures below are read over "
                f"{window.range.start.isoformat()}..{window.range.end.isoformat()}; the horizon "
                "you named is applied to the runway of the population, not to the window."
            )
            window_explicit = True  # a period WAS named; do not claim otherwise
            period_label = relative_named.quoted
        # A period the analyst NAMED is checked against the data this load
        # holds before anything is computed over it. Saying "the question
        # named no period" under a bubble containing the words "in January
        # 2019" is not a caveat, it is a misattribution.
        coverage = window_coverage(window.range, session.watermark)
        if period_label is not None and coverage is Coverage.OUTSIDE:
            return self._clarify(
                out_of_range_question(period_label, session.watermark),
                f"WINDOW_OUT_OF_RANGE: {period_label} lies outside "
                f"{data_range_phrase(session.watermark)}",
                result.usage,
                template_hash,
                options=in_range_options(session.watermark),
            )
        if coverage is Coverage.PARTIAL and window.range.end > session.watermark.newest_data_date:
            # Round-3 R3-05: the warning was right and nothing acted on it.
            # "Compare denied dollars in Q3 2026 to Q3 2025" published the
            # REQUESTED window in the header, the finding title and the
            # narrative — 92 days against 33 of data — and reported denials
            # "down 56.9% year over year" when per-day they were UP ~20%.
            # The comparison is derived from the window below, and the
            # length gate reads it, so truncating HERE is what makes every
            # downstream surface state the window that was actually read.
            requested = window.range
            effective = AbsoluteRange(
                start=requested.start, end=session.watermark.newest_data_date
            )
            window = replace(window, range=effective)
            named = f"you asked about {period_label}" if period_label else "the window requested"
            notes.append(
                f"window_out_of_range: {named}, and this load only reaches "
                f"{session.watermark.newest_data_date.isoformat()} — so the EFFECTIVE window is "
                f"{effective.start.isoformat()}..{effective.end.isoformat()} "
                f"({effective.day_length} of the {requested.day_length} days named). Every "
                "figure, the context header and any comparison below are computed over the "
                "effective window; nothing here describes the part of the period that has not "
                "landed."
            )
        scope = self._resolve_scope(parsed, turn_id, governing, notes)
        context = InvestigationContext(
            window=window,
            comparison=None,
            scope=scope,
            cohort=None,
            # Two orthogonal axes (§6.1): what a row IS, and how time is
            # bucketed. "by month" sets the second one; without it the
            # question resolved to a single scalar over the whole span.
            grain=Grain(
                primary.entity_grain,
                TimeBucket(parsed.time_grain) if parsed.time_grain else None,
            ),
            watermark=session.watermark,
            pack_version=session.pack_version,
        )
        if parsed.comparison is not None:
            context = replace(
                context, comparison=derive_comparison(window, ComparisonKind(parsed.comparison))
            )
        spec = AnalysisSpec(
            context=context,
            measures=tuple(MetricRef(mid) for mid in parsed.metric_ids),
            dimensions=tuple(DimensionRef(did) for did in parsed.dimension_ids),
            # already validated against the pack above — closed set only
            concepts=tuple(parsed.concept_ids),
            # …and the movement the question asked about, if it asked about
            # one. Closed sets by schema; carried so selection can honor them.
            direction=AskedDirection(parsed.direction) if parsed.direction else None,
            magnitude=AskedMagnitude(parsed.magnitude) if parsed.magnitude else None,
            order=AskedOrder(parsed.order) if parsed.order else None,
            # A movement stated as fact is a premise, not a filter (see
            # AnalysisSpec.direction_asserted). Only meaningful with a
            # direction to assert.
            direction_asserted=bool(parsed.direction_asserted and parsed.direction),
            # The SIZE the question asserted, so the premise check can test
            # it — "doubled" is a claim about magnitude (R3-03).
            asserted_multiple=asserted_multiple(
                question,
                parsed.direction_asserted,
                proposed=parsed.asserted_multiple,
                direction=parsed.direction,
            ),
            # …and whether a size was asserted that nothing could read, so
            # an unverified magnitude can never be published as a confirmed
            # direction (round-5 A-02c).
            size_asserted_unparsed=(
                parsed.asserted_multiple is None
                and size_asserted_unparsed(question, parsed.direction_asserted)
            ),
            # What the analyst called the period, so no later sentence has
            # to assert that they named none (R3-16).
            period_label=period_label,
            # A count the question names is an instruction, not a
            # suggestion (R3-04).
            limit=requested_finding_limit(question),
        )
        if spec.direction_asserted and context.comparison is None:
            # A question that asserts a movement is asking about two
            # windows whether or not it says so; without a comparison there
            # is no aggregate movement to verify the premise against, and
            # the turn would answer "why did X double" with a level.
            assumed = derive_comparison(window, ComparisonKind.PRIOR_PERIOD)
            spec = spec.with_context(replace(context, comparison=assumed))
            notes.append(
                "comparison_assumed: the question states that something moved, so I compared "
                f"{window.range.start.isoformat()}..{window.range.end.isoformat()} against the "
                f"period before it ({assumed.window.range.start.isoformat()}.."
                f"{assumed.window.range.end.isoformat()}) to check that movement before "
                "explaining it."
            )
        # An as-of contract applies no start..end predicate at all, so
        # announcing an assumed window over one is a confident statement
        # about a scoping that did not happen (round-2 FN-2). The turn
        # still carries a window — the cohort and charts are scoped by it
        # — and what the analyst is owed is the fact that the number is not.
        as_of_only = bool(governing) and all(
            str(contract.kind) == _SNAPSHOT_KIND for contract in governing
        )
        if as_of_only:
            names = ", ".join(repr(contract.id) for contract in governing)
            notes.append(
                f"snapshot_as_of: {names} reports the balance standing at the watermark "
                f"(as of {session.watermark.newest_data_date.isoformat()}) and applies no "
                "start..end window, so naming a period does not narrow this number."
            )
        elif not window_explicit:
            # An assumed period is a decision the analyst did not make. It
            # used to live in the debug intent_summary; it belongs beside
            # the number it scoped.
            notes.append(
                f"window_assumed: the question named no period, so I used "
                f"{window.range.start.isoformat()}..{window.range.end.isoformat()} on the "
                f"{basis.id} basis — the last full month this load can see (newest data "
                f"date {session.watermark.newest_data_date.isoformat()})."
            )
        return InterpretationOutcome(
            investigation=InterpretedInvestigation(
                spec=spec,
                playbook_id=parsed.playbook_id,
                window_explicit=window_explicit,
                intent_summary=parsed.intent_summary,
                metric_ids=tuple(parsed.metric_ids),
                dimension_ids=tuple(parsed.dimension_ids),
                concept_ids=tuple(parsed.concept_ids),
                notes=tuple(notes),
            ),
            clarification=None,
            definitional=None,
            usage=result.usage,
            template_hash=template_hash,
        )

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _clarify(
        question: str,
        reason: str,
        usage: LlmUsage,
        template_hash: str,
        *,
        options: tuple[str, ...] = (),
        bindings: tuple[ClarificationBinding, ...] = (),
        failure: LlmFailureKind | None = None,
    ) -> InterpretationOutcome:
        return InterpretationOutcome(
            investigation=None,
            clarification=ClarificationRequest(
                question=question, options=options, reason=reason, bindings=bindings
            ),
            definitional=None,
            usage=usage,
            template_hash=template_hash,
            failure=failure,
        )

    def _unusable(
        self, reason: str, failure: LlmFailureKind | None, usage: LlmUsage
    ) -> InterpretationOutcome:
        """Nothing interpretable came back — ask for the right thing.

        Same split as classification: a shape that never arrived is worth
        asking again for, a model that had no mapping is not. No options
        here either — there is no parsed response to have carried any.
        """
        retry = retry_may_help(failure)
        return self._clarify(
            _INTERPRET_RETRY if retry else _INTERPRET_REPHRASE,
            reason + failure_note(failure),
            usage,
            self._template.sha256,
            failure=failure,
        )

    def _definitional_from_terms(
        self, question: str, raw_terms: tuple[str, ...]
    ) -> DefinitionalAnswer:
        matches: list[TermDefinition] = []
        for term in raw_terms:
            for match in self._pack.resolve_term(term):
                if match not in matches:
                    matches.append(match)
        return DefinitionalAnswer(
            question=question,
            terms=tuple(matches),
            pack_id=self._pack.pack_id,
            pack_version=self._pack.pack_version,
            pack_snapshot_id=self._pack.snapshot_id,
        )

    def _governing_contracts(self, parsed: InterpretationResponse) -> tuple[MetricContract, ...]:
        contracts: list[MetricContract] = []
        for metric_id in parsed.metric_ids:
            contract = self._pack.metric(metric_id)
            assert contract is not None  # validated above
            contracts.append(contract)
        if not contracts and parsed.playbook_id is not None:
            playbook = self._pack.playbook(parsed.playbook_id)
            assert playbook is not None  # validated above
            seen: set[str] = set()
            for template in playbook.probes:
                for metric_id in template.metric_ids:
                    if metric_id in seen:
                        continue
                    seen.add(metric_id)
                    contract = self._pack.metric(metric_id)
                    if contract is not None:
                        contracts.append(contract)
        return tuple(contracts)

    def _resolve_basis(self, raw: str | None, primary: MetricContract) -> DateBasisRef:
        """The basis this window will be read on (§5.3, §6.6 step 3).

        A basis the contract forbids is still ``DATE_BASIS_INVALID``. A
        basis the contract allows but this warehouse does not bind at the
        metric's grain falls back to an allowed basis it does bind — here
        rather than in the planner alone, because the window's basis is
        what the context header publishes, and a header naming a basis
        nothing read is a header that misstates the answer.
        """
        requested = DateBasisRef(raw.strip().lower()) if raw is not None else None
        return resolve_answerable_basis(primary, requested, self._catalog).basis

    @staticmethod
    def _relative_range(window: WindowSpecModel) -> RelativeRange:
        try:
            quantity = Decimal(window.quantity)
        except InvalidOperation:
            raise UnsupportedConceptError(
                f"window quantity {window.quantity!r} is not a decimal",
                details={"quantity": window.quantity},
            ) from None
        return RelativeRange(
            quantity=quantity,
            unit=TimeUnit(window.unit),
            mode=RangeMode(window.mode),
        )

    @staticmethod
    def _anchored_range(window: AnchoredWindowModel) -> AnchoredRange:
        """A named calendar period DTO → the kernel's closed shape.

        The DTO's ``index`` is validated 1..12 by the schema because a
        month needs that range; a quarter's 1..4 and a year's "no index at
        all" are the kernel's rules, and stating them twice is how the two
        drift apart. So the kernel raises, and an out-of-range index is an
        ``UNSUPPORTED_CONCEPT`` naming what was wrong rather than a 500.
        """
        try:
            return AnchoredRange(
                unit=TimeUnit(window.unit), year=window.year, index=window.index
            )
        except ValueError as exc:
            raise UnsupportedConceptError(
                f"named period {window.unit} {window.index} {window.year} is not a calendar "
                f"period: {exc}",
                details={"unit": window.unit, "year": window.year, "index": window.index},
            ) from None

    def _interpreted_window(
        self,
        window: WindowSpecModel | AnchoredWindowModel | AbsoluteWindowModel | None,
        basis: DateBasisRef,
        session: Session,
    ) -> tuple[TimeWindow, str | None]:
        """The turn's window, resolved exactly once, plus what to call it.

        The second element is the period as the analyst *named* it ("June
        2026", "2026-01-01..2026-06-30") or ``None`` when they named none —
        which is the difference between a window this platform assumed and
        one it was given, and therefore the difference between an honest
        ``window_assumed`` note and the misattribution one.

        Relative specs anchor to the data, never to the load's clock or to
        wall-clock now (:mod:`revi_investigation.application.anchoring`).
        Named and absolute periods anchor to nothing: they are where they
        are.
        """
        if window is None:
            anchor = window_anchor(session.watermark, _DEFAULT_WINDOW.mode)
            return resolve_window(_DEFAULT_WINDOW, anchor, basis=basis), None
        if isinstance(window, AnchoredWindowModel):
            anchored = self._anchored_range(window)
            return (
                TimeWindow(basis=basis, range=resolve_anchored(anchored), requested=None),
                anchored.label,
            )
        if isinstance(window, AbsoluteWindowModel):
            resolved = self._absolute_window(window, basis)
            return resolved, absolute_label(resolved.range)
        requested = self._relative_range(window)
        anchor = window_anchor(session.watermark, requested.mode)
        return resolve_window(requested, anchor, basis=basis), None

    @staticmethod
    def _absolute_window(window: AbsoluteWindowModel, basis: DateBasisRef) -> TimeWindow:
        if window.end < window.start:
            raise UnsupportedConceptError(
                f"window {window.start.isoformat()}..{window.end.isoformat()} "
                "ends before it starts",
                details={"start": window.start.isoformat(), "end": window.end.isoformat()},
            )
        return TimeWindow(
            basis=basis,
            range=AbsoluteRange(start=window.start, end=window.end),
            requested=None,
        )

    def _typed_window(
        self,
        window: WindowSpecModel | AnchoredWindowModel | AbsoluteWindowModel,
        basis: DateBasisRef,
        session: Session,
    ) -> TimeWindow:
        """Resolve a typed window once, into stored concrete dates (§6.1)."""
        if isinstance(window, AnchoredWindowModel):
            return TimeWindow(
                basis=basis, range=resolve_anchored(self._anchored_range(window)), requested=None
            )
        if isinstance(window, AbsoluteWindowModel):
            if window.end < window.start:
                raise UnsupportedConceptError(
                    f"typed window {window.start.isoformat()}..{window.end.isoformat()} "
                    "ends before it starts",
                    details={"start": window.start.isoformat(), "end": window.end.isoformat()},
                )
            return TimeWindow(
                basis=basis,
                range=AbsoluteRange(start=window.start, end=window.end),
                requested=None,
            )
        try:
            quantity = Decimal(window.quantity)
        except InvalidOperation:
            raise UnsupportedConceptError(
                f"window quantity {window.quantity!r} is not a decimal",
                details={"quantity": window.quantity},
            ) from None
        requested = RelativeRange(
            quantity=quantity, unit=TimeUnit(window.unit), mode=RangeMode(window.mode)
        )
        # Same anchor rule as the interpreted path.
        return resolve_window(
            requested, window_anchor(session.watermark, requested.mode), basis=basis
        )

    def _typed_scope(self, filters: Sequence[AddFilterModel], turn_id: str) -> FilterExpr:
        """Typed filter clauses → kernel scope, catalog-validated like any
        interpreted one (an unknown dimension is UNSUPPORTED_CONCEPT)."""
        predicates: list[Predicate] = []
        for clause in filters:
            if self._catalog.dimension(clause.dimension) is None:
                raise UnsupportedConceptError(
                    f"typed filter dimension {clause.dimension!r} is not in the catalog",
                    details={"dimension": clause.dimension},
                )
            predicates.append(
                Predicate(
                    dimension=DimensionRef(clause.dimension),
                    op=PredicateOp(clause.predicate_op),
                    values=tuple(self._scalar(value) for value in clause.values),
                    origin_turn=turn_id,
                )
            )
        if not predicates:
            return EMPTY_SCOPE
        return and_merge(*predicates)

    def _resolve_scope(
        self,
        parsed: InterpretationResponse,
        turn_id: str,
        governing: tuple[MetricContract, ...] = (),
        notes: list[str] | None = None,
    ) -> FilterExpr:
        predicates: list[Predicate] = []
        pinned = self._pinned_by_contracts(governing)
        for entry in parsed.scope:
            if self._catalog.dimension(entry.dimension) is None:
                raise UnsupportedConceptError(
                    f"scope dimension {entry.dimension!r} is not in the catalog",
                    details={"dimension": entry.dimension},
                )
            values = tuple(self._scalar(value) for value in entry.values)
            redundant = self._redundant_note(entry.dimension, entry.op, values, pinned)
            if redundant is not None:
                if notes is not None:
                    notes.append(redundant)
                continue
            predicates.append(
                Predicate(
                    dimension=DimensionRef(entry.dimension),
                    op=PredicateOp(entry.op),
                    values=values,
                    origin_turn=turn_id,
                )
            )
        if not predicates:
            return EMPTY_SCOPE
        return and_merge(*predicates)

    def _pinned_by_contracts(
        self, governing: tuple[MetricContract, ...]
    ) -> dict[str, tuple[frozenset[str], str]]:
        """Dimension values the governing contracts already pin, by dimension."""
        pinned: dict[str, tuple[frozenset[str], str]] = {}
        for contract in governing:
            for dimension_id, values in contract_pinned_values(contract).items():
                if values and dimension_id not in pinned:
                    pinned[dimension_id] = (values, contract.id)
        return pinned

    @staticmethod
    def _redundant_note(
        dimension_id: str,
        op: str,
        values: tuple[Scalar, ...],
        pinned: dict[str, tuple[frozenset[str], str]],
    ) -> str | None:
        """Is this filter a restatement of what the metric already is?

        ``ar_over_90_pct`` *is* the 91-120 and 120+ buckets: its numerator
        pins them. An analyst filter repeating that pin narrows nothing —
        and, because ``ar_age_bucket`` is not a declared scope dimension of
        the metric, it turns an answerable question into a
        ``GRAIN_INCOMPATIBLE`` refusal. Dropping the restatement (and
        saying so) answers the question that was asked.

        The rule is exactly "unless values differ": a filter naming a
        *subset* of the pinned values is dropped as redundant, a filter
        naming anything outside them is kept — it means something else, and
        the §6.6 exclusion-overlap warning is what explains the interaction.
        """
        entry = pinned.get(dimension_id)
        if entry is None or op not in (PredicateOp.EQ.value, PredicateOp.IN.value) or not values:
            return None
        pinned_values, metric_id = entry
        asked = {normalize_synonym(str(value)) for value in values}
        if not asked <= pinned_values:
            return None
        stated = ", ".join(repr(str(value)) for value in values)
        return (
            f"filter_redundant: dropped the {dimension_id} filter {stated} — metric "
            f"{metric_id!r} already pins that population in its own definition, so the "
            "filter narrowed nothing and is not a cut this metric supports."
        )

    def _grounded_bindings(
        self, options: Sequence[GroundedOptionModel]
    ) -> tuple[ClarificationBinding, ...]:
        """The surviving options' ids, carried onto the clarification.

        Round-3 R3-17: the disposal below is deterministic and good, and it
        was the LAST thing that looked at an option. Nothing downstream
        could re-check one against the warehouse (which is where the phantom
        facility lived — an OPEN dimension has no declared ``value_domain``,
        so ``_option_resolves`` skipped its values entirely), and nothing
        could dry-run one against the planner. Both of those happen in the
        turn engine, which has the watermark and the planner; they need the
        ids, and this is where the ids still exist.
        """
        return tuple(
            ClarificationBinding(
                option=" ".join(option.label.split()),
                kind="grounded_option",
                metric_ids=tuple(option.metric_ids),
                dimension_ids=tuple(option.dimension_ids),
                playbook_id=option.playbook_id,
                scope=tuple(
                    (entry.dimension, tuple(str(value) for value in entry.values))
                    for entry in option.scope
                ),
            )
            for option in options
            if self._option_resolves(option)
        )

    def _grounded_options(
        self, options: Sequence[GroundedOptionModel]
    ) -> tuple[str, ...]:
        """Keep only the recovery options this pack and catalog can honor.

        A clarification option is a promise: tap it and you get an answer.
        The platform offered "Compare denial rates across all Medicare
        Advantage payers" and refused that request on the very next turn —
        the option was a sentence, and a sentence resolves against nothing.
        So every option now carries the ids it would use and they go
        through the same disposal an interpretation does: metrics and
        playbooks against the pinned pack, dimensions and scope dimensions
        against the catalog, scope values against a declared
        ``value_domain`` where the catalog states one, and a breakdown
        dimension against the governing contract's own
        ``scope_dimensions`` — the ratio-grain rule §6.6 would refuse it by
        one turn later.

        Failures are dropped silently *as options*; the caller decides what
        an empty survivor list means (see
        :meth:`InterpretQuestionService.interpret`).
        """
        return clarification_options(
            [option.label for option in options if self._option_resolves(option)]
        )

    def _option_resolves(self, option: GroundedOptionModel) -> bool:
        if not option.label.strip():
            return False
        contracts: list[MetricContract] = []
        for metric_id in option.metric_ids:
            contract = self._pack.metric(metric_id)
            if contract is None:
                return False
            contracts.append(contract)
        if option.playbook_id is not None and self._pack.playbook(option.playbook_id) is None:
            return False
        if not contracts and option.playbook_id is None:
            # An option naming no governed content is exactly the hollow
            # kind: a restatement whose answerability nobody can check.
            return False
        for dimension_id in option.dimension_ids:
            dim = self._catalog.dimension(dimension_id)
            if dim is None:
                return False
            ref = DimensionRef(dimension_id)
            if any(c.is_ratio and not c.allows_dimension(ref) for c in contracts):
                return False  # §6.6 step 2 would refuse this one turn later
        for entry in option.scope:
            dim = self._catalog.dimension(entry.dimension)
            if dim is None:
                return False
            if dim.value_domain is None:
                continue
            allowed = {normalize_synonym(value) for value in dim.value_domain}
            if any(normalize_synonym(str(value)) not in allowed for value in entry.values):
                return False
        return True

    @staticmethod
    def _scalar(value: str | int | float | bool | None) -> Scalar:
        if isinstance(value, float):
            return Decimal(str(value))
        return value
