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
from datetime import timedelta
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
    whole_month_span,
)
from revi_kernel.watermark import DataWatermark

_MIN_CLASSIFICATION_CONFIDENCE = 0.5
_DEFAULT_WINDOW = RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS)

#: The contract ``kind`` that reports a balance at a moment rather than a
#: quantity accumulated over a window.
_SNAPSHOT_KIND = "snapshot"
_DESCRIPTION_CLIP = 160

#: How many of a playbook's authored trigger phrasings reach the prompt.
#: Enough to carry the ways a question is actually asked, bounded so a
#: pack that authors twenty does not crowd out the other seventeen
#: playbooks.
_PLAYBOOK_TRIGGER_LIMIT = 6

#: Deterministic definitional lead-ins, as phrasing FAMILIES rather than a
#: list of sentences.
#:
#: "define denied dollars" resolved on the first try and produced a card;
#: "what counts as denied dollars here?" — the same question, asked the way
#: people ask it — matched no prefix, so the WHOLE utterance went to the
#: pack as the term, resolved to nothing, and came back as an amber "no
#: pack content matched the definitional lookup". A closed tuple of literal
#: openings will keep losing that race, because the number of ways to ask
#: what something means is not closed.
#:
#: Ordered: alternation is first-match, so specific openings precede the
#: generic "what is". Nothing here names a metric — the term is whatever
#: survives the strip, and it is the PACK that decides whether that is a
#: thing this deployment defines.
_DEFINITIONAL_LEAD_IN = re.compile(
    r"""^\s*
    (?:(?:can|could|would)\s+you\s+|please\s+)?
    (?:
        what(?:'s|s)?(?:\s+is)?\s+the\s+(?:meaning|definition)\s+of
      | (?:the\s+)?(?:meaning|definition)\s+of
      | what\s+(?:counts|qualifies)\s+(?:as|toward|towards)
      | what\s+(?:goes|falls)\s+(?:into|under)
      | what(?:'s|s)?(?:\s+is)?\s+included\s+in
      | what\s+do(?:es)?\s+(?:you|we|it|they)\s+mean\s+by
      | how\s+do(?:es)?\s+(?:you|we|they|this|it)\s+(?:define|calculate|compute|measure)
      | how\s+is(?=\s+.*\b(?:defined|calculated|computed|measured)\b)
      | tell\s+me\s+about
      | what\s+(?:is|are|does|do)\s+(?:a|an|the)
      | what\s+(?:is|are|does|do)
      | what(?:'s|s)
      | define
      | explain
      | describe
    )
    \s+""",
    re.IGNORECASE | re.VERBOSE,
)

#: Trailing filler that scopes a definitional question to this deployment
#: without changing which term it asks about — "…mean", "…here", "…in this
#: data". Stripped repeatedly: "what does denied dollars mean here?" ends
#: on two of them.
_DEFINITIONAL_TRAILERS = (
    "mean",
    "means",
    "stand for",
    "stands for",
    "defined",
    "calculated",
    "computed",
    "measured",
    "here",
    "exactly",
    "in this data",
    "in this dataset",
    "in this pack",
    "in our data",
    "in this warehouse",
    "for us",
    "on this platform",
)

# Coming up empty has two honest shapes and they want opposite advice. A
# model that read the utterance and had no mapping for it wants a different
# wording; an answer that never arrived in a readable shape wants the same
# wording again. Telling an analyst to rephrase a question that was never
# the problem is how a platform teaches people it cannot be trusted.
_CLASSIFY_REPHRASE = "I couldn't confidently read that request — could you rephrase it?"
_CLASSIFY_RETRY = "I hit a problem reading that just now — please try again."
_INTERPRET_REPHRASE = (
    "I couldn't match that question to anything in your definitions library — could you "
    "rephrase it?"
)
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
    """Does this load hold the period that was asked about?

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
#: the closed kernel shape it resolves to.
#:
#: Without it, "what should my denial team work first THIS WEEK…" and "what
#: is at risk in the NEXT 30 DAYS" both come back with ``window_assumed: the
#: question named no period`` — a false statement about the analyst's own
#: sentence, printed directly under it, over a silent widening of a 7-day
#: horizon to a 31-day month. Everything here is anchored on the newest data
#: date, exactly like "June 2026".
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
#: measurement window. There is no data after the newest data date, so
#: substituting a month of history answers a different question.
_FORWARD_PHRASE = re.compile(
    r"\b(?:next|coming|upcoming|following)\s+(\d{1,3})\s+(day|week|month|quarter|year)s?\b",
    re.IGNORECASE,
)

#: "Now" said about a quantity that only exists over a period. Read
#: literally, "who is my worst payer on denial rate RIGHT NOW, and is that a
#: change from last month?" resolves to the two days since the month
#: boundary, compares them against a same-length slice of the prior month,
#: and returns a blank page — while the identical question with the months
#: named returns three findings.
#:
#: A denial RATE has no value at an instant: it is a numerator over a
#: denominator accumulated across a period, and the last two days of it are
#: the least adjudicated data in the load. So "right now" on a periodic
#: metric means "the latest period this load can speak for" — the last FULL
#: one — and the assumption is stated, exactly as an assumed window is.
#: (A snapshot contract IS an instant, and the ``snapshot_as_of`` note
#: already says so; this rule never touches those.)
_NOW_PHRASE = re.compile(
    r"(?<!\w)(right now|just now|as of now|as we speak|as things stand|at the moment|"
    r"at present|presently|currently|today)(?!\w)",
    re.IGNORECASE,
)

#: The now-phrases that are ALSO in the relative vocabulary above. "Today"
#: resolves to a one-day window there; on a monthly metric that is the same
#: two-day defect with a shorter window, so it is anchored like the rest of
#: the family rather than excluded as a period the analyst named.
_NOW_PHRASE_LABELS: frozenset[str] = frozenset({"today"})

#: A period named as the thing being COMPARED AGAINST rather than as the
#: window to measure — "is that a change FROM LAST MONTH", "vs last month",
#: "compared to a year ago". Two jobs:
#:
#: 1. it is not the window. The vocabulary scan above matches "last month"
#:    wherever it appears, so an utterance that names its window with a now
#:    phrase and its baseline with "from last month" would otherwise be
#:    measured over the baseline;
#: 2. it IS a comparison. A question that asks for a change and gets a level
#:    is a dead end even when the level is right.
#:
#: Deterministic and closed, like every other phrase table here: the model
#: is asked for a comparison first and this catches the ones it drops.
_COMPARISON_PHRASE = re.compile(
    r"(?<!\w)(?:than|versus|vs\.?|compared\s+(?:to|with)|against|from|since|over)\s+"
    r"(?:the\s+)?(?:same\s+(?:period|month|quarter)\s+)?"
    r"(?P<period>last\s+month|the\s+(?:prior|previous)\s+month|a\s+month\s+ago|"
    r"last\s+quarter|the\s+(?:prior|previous)\s+quarter|"
    r"last\s+year|the\s+(?:prior|previous)\s+year|a\s+year\s+ago|"
    r"the\s+(?:prior|previous)\s+period)",
    re.IGNORECASE,
)

#: Which kind of comparison each of those periods names. A month-ago
#: baseline is the period before a MONTHLY window; it is not "the prior
#: period" of a quarter, so the caller checks the window's own shape before
#: honoring it (see ``_comparison_from_phrase``).
_PRIOR_YEAR_PERIODS = ("last year", "prior year", "previous year", "a year ago")


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


def recognize_now_phrase(question: str) -> str | None:
    """The "now" this utterance says, if it says one."""
    match = _NOW_PHRASE.search(question)
    return match.group(0).lower() if match is not None else None


def recognize_comparison_phrase(question: str) -> tuple[str, ComparisonKind] | None:
    """The baseline this utterance names, and which comparison it is.

    ``("last month", PRIOR_PERIOD)`` for "is that a change from last month".
    The kind is the FAMILY the phrase belongs to; whether the period it
    names really is the window's prior period is the caller's check, because
    only the caller knows how long the window is.
    """
    match = _COMPARISON_PHRASE.search(question)
    if match is None:
        return None
    period = " ".join(match.group("period").lower().split())
    kind = (
        ComparisonKind.PRIOR_YEAR
        if any(needle in period for needle in _PRIOR_YEAR_PERIODS)
        else ComparisonKind.PRIOR_PERIOD
    )
    return period, kind


def without_comparison_clause(question: str) -> str:
    """The utterance with its "vs last month" clause removed.

    Only ever used to decide what the WINDOW is. "Last month" names a
    period wherever it appears and the vocabulary scan cannot tell the two
    roles apart, so without this an utterance that names its window one way
    and its baseline another is measured over the baseline.
    """
    return _COMPARISON_PHRASE.sub(" ", question)


def last_full_period(
    watermark: DataWatermark, unit: TimeUnit, basis: DateBasisRef
) -> TimeWindow:
    """The most recent COMPLETE period of this unit that the load can see.

    One helper so the "now" rule, the default window and a playbook default
    cannot sit on three different ideas of the latest period; the anchor is
    the one :mod:`revi_investigation.application.anchoring` states.
    """
    requested = RelativeRange(Decimal(1), unit, RangeMode.FULL_PERIODS)
    return resolve_window(
        requested, window_anchor(watermark, RangeMode.FULL_PERIODS), basis=basis
    )


#: The units a "now" can be rounded back to. A metric read at day grain has
#: no full period worth anchoring to (yesterday is not a reporting period),
#: so day and week "nows" land on the month the rest of the product assumes.
_PERIOD_UNITS: tuple[TimeUnit, ...] = (TimeUnit.MONTH, TimeUnit.QUARTER, TimeUnit.YEAR)


def _open_period_clause(
    literal: AbsoluteRange, full_period: AbsoluteRange, watermark: DataWatermark
) -> str:
    """How much of the literal window sits in the period still open.

    The operand is the INTERSECTION of the literal window with the open
    partial period — everything after the last full period this load can
    see, up to the newest data date. Using the literal window's own LENGTH
    instead makes the clause meaningless: a trailing-31-day "right now"
    straddling a month boundary reads *"31 day(s) of the period that is
    still open"* over 2026-07-03..2026-08-02, of which exactly two days are
    in the open month, and by its own arithmetic every day of July was
    unsettled too. The sentence exists to say how little settled data the
    literal reading would have rested on.
    """
    open_start = full_period.end + timedelta(days=1)
    open_end = min(literal.end, watermark.newest_data_date)
    start = max(literal.start, open_start)
    days = (open_end - start).days + 1 if open_end >= start else 0
    if days <= 0:
        # The literal reading runs past the newest data date without
        # touching a partial period — there is no open period to count.
        return f"a window this load has no settled data for beyond {open_start.isoformat()}"
    if days == literal.day_length:
        return f"all {days} day(s) of it inside the period that is still open"
    return (
        f"{days} of its {literal.day_length} day(s) inside the period that is still open"
    )


def _period_names_prior_window(
    phrase: str, kind: ComparisonKind, window: TimeWindow
) -> bool:
    """Is the baseline this phrase names the window's own prior period?

    "Vs last month" against a month IS the prior period. Against a quarter
    it is not — the period before this quarter is three months back — and
    silently answering the second when the analyst said the first is how a
    comparison ends up describing dates nobody asked about. When the shapes
    do not line up this returns False and the utterance's baseline goes
    unanswered rather than wrongly answered.
    """
    if kind is ComparisonKind.PRIOR_YEAR or "period" in phrase:
        return True
    requested = window.requested
    span = whole_month_span(window.range)
    if "month" in phrase:
        return span == 1 or (
            requested is not None
            and requested.unit is TimeUnit.MONTH
            and requested.quantity == 1
        )
    if "quarter" in phrase:
        return span == 3 or (
            requested is not None
            and requested.unit is TimeUnit.QUARTER
            and requested.quantity == 1
        )
    return False  # pragma: no cover - the pattern names no other period


#: The sizes an assertion can name, as a multiple of the prior level. A
#: closed vocabulary of magnitude words, read off the utterance the same way
#: the period vocabulary above is: "doubled" is not a direction, it is a
#: quantity, and a premise check that tests only the sign never sees it.
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
#: 40%", "5x". A gap in the closed table above ("halved" but not "halve", no
#: numeric form at all) turns an unparsed size into a confirmed DIRECTION:
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

    The closed table is a deterministic OVERRIDE, not the sole source: a
    word this platform recognises is resolved here and never asked of a
    model, and ``proposed`` — the interpretation's own typed reading — fills
    the long tail of phrasings a table cannot hold.
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
#: names TWELVE; resolving it to the 100-row ``FULL_RANKING_LIMIT`` makes
#: the shortfall notice quote a ceiling this platform invented back at an
#: analyst who had named a number.
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
    """How many rows the analyst asked to see, when they asked.

    ``top_n = 3`` is a constructor default; nothing else can lift it, so
    without this "show me all twelve payers, not just three" returns three
    findings and "every one of our 12 payers" returns three with no omission
    notice. A count the question names is an instruction.
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

    "Show me all twelve" reaching the classifier comes back at confidence
    0.45-0.50 and is answered with a clarification asking whether the twelve
    have already been computed — a question the platform can answer itself
    from the frame it is holding — leaving the limit lift on a path a
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


#: An utterance that asks for the answer already on screen to be RE-ARRANGED
#: rather than merely re-shown. **One vocabulary, two readers**: this is the
#: gate ``presentation_order_request`` admits a turn on AND the gate
#: :func:`revi_investigation.application.submit_turn.presentation_ordering`
#: resolves it with. Two regexes for one intent lets the wider one be the
#: gate: "reverse the order" and "flip it" pass admission, match nothing on
#: resolution, and re-serve the parent's rows in the parent's order under
#: "nothing changed but the presentation".
PRESENTATION_CHANGE_REQUEST = re.compile(
    r"(?<!\w)(?:sort|sorts|sorted|sorting|re-?sort|order|orders|ordered|ordering|"
    r"reorder|re-?order|rank|ranks|ranked|ranking|re-?rank|reverse|reversed|flip|"
    r"group\s+by|grouped\s+by|filter|filtered|exclude|excluding|"
    r"alphabetical(?:ly)?|ascending|descending|largest\s+first|smallest\s+first|"
    r"highest\s+first|lowest\s+first)(?!\w)",
    re.IGNORECASE,
)

#: Every word an utterance may contain and still be *only* an instruction
#: about the order of rows already on screen. The closed-list discipline is
#: :func:`display_scope_limit`'s, and for the same reason: the moment a
#: sentence names a metric, a cut, a payer or a period it is asking for
#: something new, and "rank our providers by denial rate" must not be
#: mistaken for "sort them by percent change, largest first".
#:
#: The column words in here are the ones a frame on screen already has a
#: column for — percent, change, delta, dollars, value, rank, name. Nothing
#: in this list can select data; the worst a wrong match can do is order a
#: set of rows the reader is already looking at, and say that it did.
_PRESENTATION_ORDER_WORDS = frozenset(
    [
        "a", "absolute", "again", "all", "already", "alphabetical", "alphabetically",
        "also", "amount", "amounts", "and", "another", "asc", "ascending", "at",
        "back", "best", "big", "bigger", "biggest", "but", "by", "can", "change",
        "changes", "column", "columns", "could", "delta", "desc", "descending",
        "difference", "display", "do", "dollar", "dollars", "down", "exclude",
        "excluding", "existing", "filter", "filtered",
        "first", "flip", "for", "from", "get", "give", "greatest", "group",
        "grouped", "high", "higher",
        "highest", "i", "in", "instead", "is", "it", "its", "just", "keep", "largest",
        "last", "least", "leave", "level", "list", "low", "lower", "lowest",
        "magnitude", "me", "most", "movement", "moved", "my", "name", "names", "now",
        "of", "on", "one", "only", "order", "ordered", "ordering", "orders", "our",
        "percent", "percentage", "pct", "please", "point", "points", "position",
        "put", "rank", "ranked", "ranking", "ranks", "re", "rearrange", "reorder",
        "resort", "rest", "reverse", "reversed", "row", "rows", "same", "see",
        "share", "show", "showing", "shown", "size", "small", "smaller", "smallest",
        "sort", "sorted", "sorting", "sorts",
        "that", "the", "their", "them", "then", "there", "these", "this",
        "those", "to", "top", "up", "value", "values", "want", "way", "we", "with",
        "worst", "would", "you", "your",
    ]
)


def presentation_order_request(question: str) -> bool:
    """Is this utterance *only* an instruction about the order on screen?

    Routing this through the classifier loses it: "sort them by percent
    change, largest first" comes back ``presentation_only`` at 0.76 and
    0.68, below the confidence threshold, so the turn ends as a
    clarification ("is percent change already a column?") over a question
    the engine can answer itself from the rows it is holding — two model
    calls, no answer, and a re-sort request diverted into a dialogue.

    Decided here, before any model call, on the same closed-vocabulary rule
    :func:`display_scope_limit` uses: the utterance must ask for a
    re-arrangement (:data:`PRESENTATION_CHANGE_REQUEST` — the same gate the
    resolver uses, so nothing can be admitted here that cannot be answered
    there), and every word in it must be one that says nothing about WHAT to
    measure.
    """
    if PRESENTATION_CHANGE_REQUEST.search(question) is None:
        return False
    return all(
        token.casefold() in _PRESENTATION_ORDER_WORDS
        for token in re.findall(r"[A-Za-z']+", question)
    )


def out_of_range_question(period_label: str, watermark: DataWatermark) -> str:
    """Say which period was asked for and which one exists.

    Without it, "…in January 2019" comes back with a current-month window
    and the warning "the question named no period", rendered directly under
    the analyst's own words. The period was named; it was simply not there.
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


def _strip_definitional_trailers(text: str) -> str:
    """Drop trailing filler until none is left ("PR3 mean here" → "PR3")."""
    current = text
    while True:
        for trailer in _DEFINITIONAL_TRAILERS:
            if current.endswith(" " + trailer):
                current = current[: -len(trailer) - 1].strip().strip(",").strip()
                break
        else:
            return current


def definitional_lead_in(question: str) -> str | None:
    """The term a definitional question asks about, or ``None``.

    ``None`` means the utterance is not PHRASED as a definition — no
    governed lead-in opens it — which is a different fact from "the term it
    named is not one we define". Callers that need the distinction (see
    :meth:`InterpretQuestionService.definitional_match`) must not infer it
    from the stripped text, because trailing filler is stripped either way.
    """
    text = question.strip().strip("?!.").strip().lower()
    match = _DEFINITIONAL_LEAD_IN.match(text)
    if match is None:
        return None
    term = text[match.end() :].strip()
    for article in ("a ", "an ", "the ", "our ", "my "):
        if term.startswith(article):
            term = term[len(article) :].strip()
            break
    return _strip_definitional_trailers(term) or None


def strip_definitional_lead_in(question: str) -> str:
    """Deterministically strip definitional lead-in phrases and trailing
    filler ("what does PR3 mean?" → "PR3")."""
    text = question.strip().strip("?!.").strip().lower()
    return definitional_lead_in(question) or _strip_definitional_trailers(text)


@dataclass(frozen=True, slots=True)
class PendingClarification:
    """A clarification this session asked and has not had answered yet.

    Classification without it is classification without the one fact that
    decides the answer: an utterance is only "an answer to a question I
    haven't asked" if no question is outstanding. A session whose reply
    repeats an offered option VERBATIM is otherwise read fresh each time and
    clarifies turn after turn, because the model is never told it asked
    anything.
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
    #: Without it the resolved turn is saved as a root node, leaving the
    #: clarification and its own answer in two disconnected trees.
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

    def _playbook_line(self, pid: str, description: str) -> str:
        """One playbook's prompt entry: what it is, and how it gets asked.

        Playbook selection read the id and 160 clipped characters of
        description and nothing else, so every ``triggers:`` block in the
        pack was authored content with no runtime consumer — eighteen
        playbooks' worth of the pack author's own words for how analysts
        phrase each question, sitting inert. The cost was routing:
        "Is anything about to miss a filing deadline?" matched
        ``timely_filing_watch``'s trigger *"claims about to miss filing"*
        exactly, and the model never saw it, so the question came back as a
        bare ``timely_filing_at_risk_dollars`` total whose own mandatory
        caveat told the analyst to go cut it by ``filing_runway_bucket``
        themselves.

        Triggers are phrasings, not patterns: nothing here matches an
        utterance: the model still chooses, and every id it returns is
        still validated against the pinned pack. This is vocabulary.
        """
        line = f"- {pid}: {_clip(description)}"
        spec = self._pack.playbook(pid)
        triggers = spec.triggers if spec is not None else ()
        phrasings = "; ".join(
            _clip(trigger) for trigger in triggers[:_PLAYBOOK_TRIGGER_LIMIT] if trigger.strip()
        )
        return f"{line}\n    asked as: {phrasings}" if phrasings else line

    def _vocabulary(self) -> dict[str, str]:
        metrics = "\n".join(
            f"- {mid}: {_clip(desc)}" for mid, desc in self._pack.metric_summaries()
        )
        dimensions = "\n".join(
            f"- {dim.id}: {dim.label}" for dim in self._catalog.dimensions if dim.certified
        )
        playbooks = "\n".join(
            self._playbook_line(pid, desc) for pid, desc in self._pack.playbook_summaries()
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
        stripped = definitional_lead_in(question)
        if not stripped:
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
        with it applied is a substitution, not a second interpretation.
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
                # last thing left standing.
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
        # An as-of contract reports a balance standing at the watermark, so
        # "right now" is literally what it measures and no period rounding
        # applies to it. Read here rather than below because the "now" rule
        # and the ``snapshot_as_of`` note are the same decision said twice.
        as_of_only = bool(governing) and all(
            str(contract.kind) == _SNAPSHOT_KIND for contract in governing
        )
        now_phrase = recognize_now_phrase(question)
        comparison_named = recognize_comparison_phrase(question)
        # The model is asked for a window first; this catches the phrases it
        # drops. Otherwise "this week" and "the next 30 days" are both
        # answered with ``the question named no period``, printed under the
        # analyst's own sentence, over a silent widening to a 31-day month.
        relative_named: RelativePeriod | None = None
        if not window_explicit:
            # …read off the utterance MINUS its baseline clause when the
            # window is named by a "now": "worst payer right now, is that a
            # change from last month" names two different periods, not the
            # same one twice.
            relative_named = recognize_relative_period(
                without_comparison_clause(question)
                if now_phrase is not None and comparison_named is not None
                else question
            )
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
        # "Right now" on a quantity that only exists over a period.
        # Everything above has run, so this sees the window the model or the
        # vocabulary actually resolved — and re-anchors it to the last FULL
        # period when that window is a slice of the period still open. The
        # analyst is told, in the same shape an assumed window is told.
        if now_phrase is not None and not as_of_only:
            named_a_period = (
                relative_named is not None
                and relative_named.quoted.lower() not in _NOW_PHRASE_LABELS
            )
            requested = window.requested
            unit = (
                requested.unit
                if requested is not None and requested.unit in _PERIOD_UNITS
                else TimeUnit.MONTH
            )
            target = last_full_period(session.watermark, unit, basis)
            # Only a window that reaches into the open period and is no
            # longer than one whole period is a "now" to round back: a
            # trailing 90 days ends there too and is a span the analyst
            # asked for, not a period boundary they fell over.
            partial = (
                window.range.end > target.range.end
                and window.range.day_length <= target.range.day_length
            )
            if partial and not named_a_period:
                stale = window.range
                window = target
                window_explicit = True  # a period WAS named — "right now"
                period_label = now_phrase
                notes.append(
                    f'window_assumed: you said "{now_phrase}", and this metric is measured '
                    "over a period rather than at an instant — so I read it over the last "
                    f"FULL period this load can see, {window.range.start.isoformat()}.."
                    f"{window.range.end.isoformat()} on the {basis.id} basis (newest data "
                    f"date {session.watermark.newest_data_date.isoformat()}). Taken "
                    f"literally it would have been {stale.start.isoformat()}.."
                    f"{stale.end.isoformat()} — "
                    + _open_period_clause(stale, target.range, session.watermark)
                    + ", which is the least settled data in this load and is not comparable "
                    "to a whole one. Name a period to read a different one."
                )
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
            # Warning about the shortfall is not enough on its own:
            # "compare denied dollars in Q3 2026 to Q3 2025" otherwise
            # publishes the REQUESTED window in the header, the finding title
            # and the narrative — 92 days against 33 of data — and reports
            # denials "down 56.9% year over year" when per-day they are UP
            # ~20%. The comparison is derived from the window below, and the
            # length gate reads it, so truncating HERE is what makes every
            # downstream surface state the window that was actually read.
            requested_range = window.range
            effective = AbsoluteRange(
                start=requested_range.start, end=session.watermark.newest_data_date
            )
            window = replace(window, range=effective)
            named = f"you asked about {period_label}" if period_label else "the window requested"
            notes.append(
                f"window_out_of_range: {named}, and this load only reaches "
                f"{session.watermark.newest_data_date.isoformat()} — so the EFFECTIVE window is "
                f"{effective.start.isoformat()}..{effective.end.isoformat()} "
                f"({effective.day_length} of the {requested_range.day_length} days named). Every "
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
        elif comparison_named is not None:
            # The model can drop a baseline the utterance names: "…and is
            # that a change from last month?" comes back as a level with no
            # comparison at all, which answers half the question and reads
            # as a non-answer to the half that matters.
            # Honored only where the named period IS the window's own prior
            # one — "last month" against a monthly window — because
            # answering "vs last month" with the quarter before this quarter
            # would be a different wrong answer.
            phrase, kind = comparison_named
            if _period_names_prior_window(phrase, kind, window):
                assumed = derive_comparison(window, kind)
                context = replace(context, comparison=assumed)
                notes.append(
                    f'comparison_assumed: you asked for a change "{phrase}", so I compared '
                    f"{window.range.start.isoformat()}..{window.range.end.isoformat()} "
                    f"against {assumed.window.range.start.isoformat()}.."
                    f"{assumed.window.range.end.isoformat()} — the whole period that name "
                    "points at, not a slice of it."
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
            # it — "doubled" is a claim about magnitude.
            asserted_multiple=asserted_multiple(
                question,
                parsed.direction_asserted,
                proposed=parsed.asserted_multiple,
                direction=parsed.direction,
            ),
            # …and whether a size was asserted that nothing could read, so
            # an unverified magnitude can never be published as a confirmed
            # direction.
            size_asserted_unparsed=(
                parsed.asserted_multiple is None
                and size_asserted_unparsed(question, parsed.direction_asserted)
            ),
            # What the analyst called the period, so no later sentence has
            # to assert that they named none.
            period_label=period_label,
            # A count the question names is an instruction, not a
            # suggestion.
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
        # about a scoping that did not happen. The turn
        # still carries a window — the cohort and charts are scoped by it
        # — and what the analyst is owed is the fact that the number is not.
        # (Read above, where the "now" rule needs the same fact.)
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

        The disposal below is deterministic, and it is the last thing in
        this module that looks at an option. Nothing downstream can re-check
        one against the warehouse — an OPEN dimension has no declared
        ``value_domain``, so ``_option_resolves`` skips its values entirely
        and a nonexistent facility survives — and nothing can dry-run one
        against the planner. Both of those happen in the turn engine, which
        has the watermark and the planner; they need the ids, and this is
        where the ids still exist.
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
        An option that is only a sentence resolves against nothing, so
        "compare denial rates across all Medicare Advantage payers" can be
        offered and then refused on the very next turn. Every option
        therefore carries the ids it would use, and they go
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
