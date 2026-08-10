"""Reading "watch X" as a watch declaration, before anything is classified.

An analyst who has just been shown a denial rate should be able to say
*"keep an eye on that for Silverline"* and have it become a watch. Making
them find a pin icon, or a settings page, is the difference between a
surface people use and one they configure once and forget.

**What this module does and does not do.** It recognises a closed LEAD-IN
vocabulary — "watch", "keep an eye on", "monitor", "track", "alert me
when", "let me know when" — strips it, and hands the REMAINDER to the
ordinary interpretation path. That remainder is a question like any other:
it is classified, interpreted against the pack and catalog, planned,
validated at §6.6 and answered. Nothing here parses the analyst's subject
matter, matches a metric, or guesses a scope. The lead-in is the only
language this module reads, and the rest of the sentence is handled by the
machinery that already handles sentences.

That distinction is the whole design. A watch is not a new kind of
question — it is an ordinary question plus the instruction "and tell me
when this changes" — so:

* a declaration that cannot be compiled CLARIFIES, exactly as the same
  words without the lead-in would. A watch registered against a spec
  nobody confirmed briefs the wrong number every morning, silently,
  forever;
* the declaration is answered ONCE, and that answer is the baseline. The
  analyst sees what they are now watching and what it currently reads
  before they walk away from it;
* scope specificity falls out for free. "Watch our denial rate" and "watch
  Silverline MA's denial rate for lab" differ only in the spec
  interpretation produced, and both are just specs.

**On matching language at all.** This platform's standing rule is that no
question text is matched anywhere — routing to governed artifacts goes
through the pack (``worklist.yaml``), not through phrasings in code. The
exceptions are deliberate and all of one kind: handles the platform itself
printed (``F2``, ``ANM-021``, "the top item") and closed INSTRUCTION
vocabularies that name an operation rather than a subject
(``display_scope_limit``'s "show me all twelve", ``parse_gesture``'s "drill
into F1"). A watch lead-in is the second kind: it says what to DO with the
question, and the question itself is still resolved entirely through
governed content. The vocabulary is small, closed, and listed here so it
can be reviewed as content rather than discovered as behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from revi_investigation.application.ports import RoundsWatch

#: The closed lead-in vocabulary. Each entry is an INSTRUCTION about the
#: sentence that follows it, never a subject. Ordered longest-first at
#: compile time so "keep an eye on" wins over "keep".
#:
#: "monitor" is accepted as an input word and never used as an output one:
#: the A/B round used "monitor" as the pejorative for the variant that
#: lost, and the product word is "watch". Reading somebody's "monitor" and
#: answering with "Watching:" is the right asymmetry — accept what people
#: type, publish one vocabulary.
_LEAD_INS: tuple[str, ...] = (
    "keep an eye on",
    "keep track of",
    "keep watching",
    "let me know when",
    "let me know if",
    "alert me when",
    "alert me if",
    "tell me when",
    "tell me if",
    "notify me when",
    "notify me if",
    "watch out for",
    "watch for",
    "watch",
    "monitor",
    "track",
)

#: Filler that may sit between the lead-in and the subject. Stripped so
#: "watch the denial rate for Silverline" and "watch denial rate for
#: Silverline" compile identically.
#: Punctuation that may separate a lead-in from its subject, including
#: the en and em dashes a chat box turns "--" into.
_SEPARATORS = ":-" + "\u2013\u2014"

_LEADING_FILLER = re.compile(r"^(?:the|our|my|on|for)\s+", re.IGNORECASE)

_LEAD_IN_RE = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|i'?d like (?:you )?to\s+|i want (?:you )?to\s+)?"
    r"(" + "|".join(re.escape(phrase) for phrase in sorted(_LEAD_INS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

#: The trailing sensitivity clause, when the analyst states one:
#: "…, tell me if it moves more than 2 points", "…any movement",
#: "…when it crosses 15%". A closed grammar over a number and a unit —
#: never an open parse of intent.
_ANY_MOVEMENT = re.compile(
    r"\b(?:on\s+)?any\s+(?:movement|change|move)\b|\bwhenever\s+it\s+(?:moves|changes)\b",
    re.IGNORECASE,
)
_DELTA = re.compile(
    r"\b(?:more than|at least|over|by|>=?)\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>points?|pts?|%|percent|percentage points?|dollars?|\$)",
    re.IGNORECASE,
)
_DELTA_DOLLARS = re.compile(
    r"\b(?:more than|at least|over|by|>=?)\s*\$\s*(?P<value>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CROSSES = re.compile(
    r"\b(?:cross(?:es)?|goes? (?:above|over)|rises? above|hits?|reaches?)\s*"
    r"\$?\s*(?P<value>[\d,]+(?:\.\d+)?)\s*(?P<unit>%|percent|points?|pts?)?",
    re.IGNORECASE,
)
_DIRECTION_UP = re.compile(r"\b(?:rises?|goes? up|increases?|worsens?|climbs?)\b", re.IGNORECASE)
_DIRECTION_DOWN = re.compile(r"\b(?:falls?|drops?|goes? down|decreases?|improves?)\b", re.IGNORECASE)

#: Where a sensitivity clause starts. Split there so the SUBJECT handed to
#: interpretation never carries the threshold words — "denial rate if it
#: moves more than 2 points" would be interpreted as a question about a
#: movement rather than a level.
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:,\s*)?\b(?:and\s+)?(?:tell me|let me know|alert me|notify me|"
    r"if it|when it|whenever it|on any|for any)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WatchDeclaration:
    """A recognised watch declaration, split into its two honest halves."""

    #: The lead-in the analyst used, verbatim — echoed back so the platform
    #: shows what it read rather than asserting it read intent.
    matched_phrase: str
    #: What is left after the lead-in and any sensitivity clause: an
    #: ordinary question, handed to the ordinary interpretation path.
    subject: str
    #: The sensitivity the analyst stated, or ``None`` for the governed
    #: default. Never inferred from the subject.
    watch: RoundsWatch | None
    #: The sensitivity clause verbatim, when there was one.
    threshold_phrase: str = ""


def parse_watch_declaration(utterance: str) -> WatchDeclaration | None:
    """A watch declaration, or ``None`` for ordinary language.

    ``None`` whenever the lead-in is absent, and whenever stripping it
    would leave nothing to investigate: "watch" alone is not a declaration,
    it is a word, and registering a watch over an empty subject would be
    the platform inventing what to watch.
    """
    text = (utterance or "").strip()
    if not text:
        return None
    match = _LEAD_IN_RE.match(text)
    if match is None:
        return None
    remainder = text[match.end() :].strip()
    # Punctuation an analyst may put between the lead-in and the subject.
    # The dashes are spelled by codepoint so the source carries no
    # ambiguous glyph a reviewer would have to squint at.
    remainder = remainder.lstrip(_SEPARATORS).strip()
    remainder = _LEADING_FILLER.sub("", remainder).strip()
    if not remainder:
        return None

    subject, threshold_phrase = _split_clause(remainder)
    subject = subject.strip().rstrip("," + _SEPARATORS + ".;").strip()
    if not subject:
        return None
    watch = _watch_from_clause(threshold_phrase) if threshold_phrase else None
    return WatchDeclaration(
        matched_phrase=match.group(1),
        subject=subject,
        watch=watch,
        threshold_phrase=threshold_phrase.strip(),
    )


def _split_clause(text: str) -> tuple[str, str]:
    """Separate the subject from any sensitivity clause.

    Split on a closed set of clause openers, so the subject handed to
    interpretation is a question about a LEVEL and never carries the
    threshold words with it.
    """
    match = _CLAUSE_SPLIT.search(text)
    if match is None:
        return text, ""
    return text[: match.start()], text[match.start() :]


def _watch_from_clause(clause: str) -> RoundsWatch | None:
    """The stated sensitivity, as a typed watch.

    Deliberately conservative: a clause this grammar does not recognise
    yields ``None``, which means "the governed default", not a guessed
    threshold. A watch that fires on a number nobody stated is worse than
    one that fires on the pack's.
    """
    direction = "any"
    if _DIRECTION_UP.search(clause) and not _DIRECTION_DOWN.search(clause):
        direction = "up"
    elif _DIRECTION_DOWN.search(clause) and not _DIRECTION_UP.search(clause):
        direction = "down"

    crosses = _CROSSES.search(clause)
    if crosses is not None:
        value = _number(crosses.group("value"))
        if value is not None:
            unit = _threshold_unit(crosses.group("unit"), clause)
            # A crossing is a LEVEL, and a level cannot be relative to
            # anything — "crosses 15%" names 15 percent, not 15 percent of
            # the reference value. ``points`` is the reading that converts
            # to the metric's own unit (15 → 0.15 on a rate); a
            # ``relative_pct`` crossing would be a threshold that moved
            # every time the thing it watches did.
            if unit == "relative_pct":
                unit = "points"
            return RoundsWatch(
                mode="crosses",
                value=value,
                unit=unit,
                direction=direction,
                note=f"declared in words: {clause.strip()}",
            )

    dollars = _DELTA_DOLLARS.search(clause)
    if dollars is not None:
        value = _number(dollars.group("value"))
        if value is not None:
            return RoundsWatch(
                mode="delta_gte",
                # Stated in dollars, carried in cents: the unit the money
                # contracts actually declare.
                value=value * 100,
                unit="cents",
                direction=direction,
                note=f"declared in words: {clause.strip()}",
            )

    delta = _DELTA.search(clause)
    if delta is not None:
        value = _number(delta.group("value"))
        if value is not None:
            unit = _threshold_unit(delta.group("unit"), clause)
            if unit == "cents":
                value = value * 100
            return RoundsWatch(
                mode="delta_gte",
                value=value,
                unit=unit,
                direction=direction,
                note=f"declared in words: {clause.strip()}",
            )

    if _ANY_MOVEMENT.search(clause):
        return RoundsWatch(
            mode="any_movement",
            direction=direction,
            note=f"declared in words: {clause.strip()}",
        )
    if direction != "any":
        # A direction with no magnitude narrows the governed default rather
        # than replacing it: "tell me if it rises" is a real instruction and
        # is not an instruction about size.
        return RoundsWatch(
            mode="governed_default",
            direction=direction,
            note=f"declared in words: {clause.strip()}",
        )
    return None


def _threshold_unit(raw: str | None, clause: str) -> str:
    """Which unit a stated number is in.

    ``%`` is the ambiguity this platform refuses everywhere else — "up 3%"
    is either a relative change or three percentage points — so it is read
    as ``relative_pct``, the reading that is legal against every contract
    and cannot silently become points. When the analyst says "points" they
    get points.
    """
    text = (raw or "").lower()
    if text.startswith(("point", "pt", "percentage point")):
        return "points"
    if text in ("%", "percent"):
        return "relative_pct"
    if text.startswith("dollar") or text == "$" or "$" in clause:
        return "cents"
    return "relative_pct"


def _number(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):  # pragma: no cover - regex guards this
        return None
