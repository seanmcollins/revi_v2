"""Reading "monitor X" as a monitor declaration, before anything is classified.

An analyst who has just been shown a denial rate should be able to say
*"keep an eye on that for Silverline"* and have it become a monitor. Making
them find a pin icon, or a settings page, is the difference between a
surface people use and one they configure once and forget.

**What this module does and does not do.** It recognises a closed LEAD-IN
vocabulary — "monitor", "watch", "keep an eye on", "track", "alert me
when", "let me know when" — strips it, and hands the REMAINDER to the
ordinary interpretation path. That remainder is a question like any other:
it is classified, interpreted against the pack and catalog, planned,
validated at §6.6 and answered. Nothing here parses the analyst's subject
matter, matches a metric, or guesses a scope. The lead-in is the only
language this module reads, and the rest of the sentence is handled by the
machinery that already handles sentences.

That distinction is the whole design. A monitor is not a new kind of
question — it is an ordinary question plus the instruction "and tell me
when this changes" — so:

* a declaration that cannot be compiled CLARIFIES, exactly as the same
  words without the lead-in would. A monitor registered against a spec
  nobody confirmed briefs the wrong number every morning, silently,
  forever;
* the declaration is answered ONCE, and that answer is the baseline. The
  analyst sees what they are now monitoring and what it currently reads
  before they walk away from it;
* scope specificity falls out for free. "Monitor our denial rate" and "monitor
  Silverline MA's denial rate for lab" differ only in the spec
  interpretation produced, and both are just specs.

**On matching language at all.** This platform's standing rule is that no
question text is matched anywhere — routing to governed artifacts goes
through the pack (``worklist.yaml``), not through phrasings in code. The
exceptions are deliberate and all of one kind: handles the platform itself
printed (``F2``, ``ANM-021``, "the top item") and closed INSTRUCTION
vocabularies that name an operation rather than a subject
(``display_scope_limit``'s "show me all twelve", ``parse_gesture``'s "drill
into F1"). A monitor lead-in is the second kind: it says what to DO with the
question, and the question itself is still resolved entirely through
governed content. The vocabulary is small, closed, and listed here so it
can be reviewed as content rather than discovered as behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from revi_investigation.application.ports import Monitor

#: The closed lead-in vocabulary. Each entry is an INSTRUCTION about the
#: sentence that follows it, never a subject. Ordered longest-first at
#: compile time so "keep an eye on" wins over "keep".
#:
#: "watch" is accepted as an input word and never used as an output one:
#: the product word is "monitor", and an analyst who has used any other
#: tool will type "watch" for years yet. Reading somebody's "watch" and
#: answering with "Monitoring:" is the right asymmetry — accept what people
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
#: "monitor the denial rate for Silverline" and "monitor denial rate for
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
#: Number words this grammar reads, so a stated threshold is not lost to
#: the analyst having typed it the way people speak. "half a point" was a
#: real utterance from a real buyer and it registered the governed default
#: silently — the confirmation sentence never mentioned the instruction
#: (round-7 FN-6). Small and closed, like every other vocabulary here: this
#: is not a number parser, it is the dozen words an RCM analyst types.
_NUMBER_WORDS: dict[str, str] = {
    "half": "0.5",
    "a half": "0.5",
    "one": "1",
    "a": "1",
    "an": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "fifteen": "15",
    "twenty": "20",
    "twenty five": "25",
    "thirty": "30",
    "fifty": "50",
    "a hundred": "100",
    "one hundred": "100",
}

#: ``half a point`` / ``three points`` / ``a quarter of a point``: the
#: number word, then optionally the article the phrase carries ("half A
#: point"), then the unit.
_WORD_VALUE = "|".join(
    re.escape(word) for word in sorted(_NUMBER_WORDS, key=len, reverse=True)
)
#: Every unit a threshold may be stated in. ``days`` was absent entirely,
#: so "tell me when it moves more than 2 days" on a metric the pack governs
#: with `min_absolute_days` fell to the governed default with no warning. It
#: is now read here AND legal in :data:`MONITOR_THRESHOLD_UNITS` — over a
#: ``days`` contract only, refused by name over any other.
_UNIT_ALTERNATIVES = r"percentage points?|points?|pts?|%|percent|dollars?|\$|days?"

_DELTA = re.compile(
    r"\b(?:more than|at least|over|by|>=?)\s*"
    r"(?:(?P<value>\d+(?:\.\d+)?)|(?P<word>" + _WORD_VALUE + r")(?:\s+an?)?)\s*"
    r"(?P<unit>" + _UNIT_ALTERNATIVES + r")",
    re.IGNORECASE,
)
_DELTA_DOLLARS = re.compile(
    r"\b(?:more than|at least|over|by|>=?)\s*\$\s*(?P<value>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CROSSES = re.compile(
    r"\b(?:cross(?:es)?|goes? (?:above|over)|rises? above|hits?|reaches?)\s*"
    r"\$?\s*(?:(?P<value>[\d,]+(?:\.\d+)?)|(?P<word>" + _WORD_VALUE + r")(?:\s+an?)?)\s*"
    r"(?P<unit>%|percent|percentage points?|points?|pts?|days?)?",
    re.IGNORECASE,
)

#: Does this clause state a SIZE or a LEVEL?
#:
#: The difference between two honest outcomes and one dishonest one. "tell
#: me if it rises" states a direction and nothing about size, so completing
#: it with the pack's magnitude is honest. "tell me if it moves more than
#: half a smidgen" states a size this grammar cannot read, and resolving
#: THAT to the governed default is a silent substitution of the platform's
#: number for the analyst's.
#:
#: Deliberately excludes bare movement verbs — "if it moves", "when it
#: changes" — which are instructions about WHETHER, not about how much.
_STATES_A_THRESHOLD = re.compile(
    r"\b(?:more than|at least|greater than|no less than|under|below|above|>=?|<=?|"
    r"cross(?:es)?|goes? (?:above|over|below|under)|rises? above|falls? below|"
    r"hits?|reaches?)\b"
    r"|\d"
    r"|\b(?:" + _WORD_VALUE + r")\s+(?:an?\s+)?(?:" + _UNIT_ALTERNATIVES + r")\b",
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
class MonitorDeclaration:
    """A recognised monitor declaration, split into its two honest halves."""

    #: The lead-in the analyst used, verbatim — echoed back so the platform
    #: shows what it read rather than asserting it read intent.
    matched_phrase: str
    #: What is left after the lead-in and any sensitivity clause: an
    #: ordinary question, handed to the ordinary interpretation path.
    subject: str
    #: The sensitivity the analyst stated, or ``None`` for the governed
    #: default. Never inferred from the subject.
    monitor: Monitor | None
    #: The sensitivity clause verbatim, when there was one.
    threshold_phrase: str = ""
    #: True when the analyst STATED a sensitivity and this grammar could
    #: not read it. The caller must refuse or clarify — never register the
    #: governed default, because a monitor that silently gates at 0.5 points
    #: when somebody typed "three points" briefs the wrong number every
    #: morning and says nothing about it (round-7 FN-6).
    #:
    #: Distinct from ``monitor is None`` on its own, which is the ordinary
    #: and honest "no sensitivity was stated, so the pack's applies".
    threshold_unreadable: bool = False


def parse_monitor_declaration(utterance: str) -> MonitorDeclaration | None:
    """A monitor declaration, or ``None`` for ordinary language.

    ``None`` whenever the lead-in is absent, and whenever stripping it
    would leave nothing to investigate: "monitor" alone is not a declaration,
    it is a word, and registering a monitor over an empty subject would be
    the platform inventing what to monitor.
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
    monitor = _monitor_from_clause(threshold_phrase) if threshold_phrase else None
    # A clause that says something about SIZE and that this grammar could
    # not read is reported as unreadable, never resolved to the governed
    # default. The direction-only branch of `_monitor_from_clause` is not
    # this case: "tell me if it rises" is a complete instruction about
    # direction and says nothing about size, so the governed magnitude is
    # the honest completion of it rather than a substitution for something
    # the analyst asked for.
    unreadable = bool(
        threshold_phrase
        and (monitor is None or monitor.mode == "governed_default")
        and _STATES_A_THRESHOLD.search(threshold_phrase) is not None
    )
    return MonitorDeclaration(
        matched_phrase=match.group(1),
        subject=subject,
        monitor=monitor,
        threshold_phrase=threshold_phrase.strip(),
        threshold_unreadable=unreadable,
    )


#: What a threshold may legally be stated as, per unit kind — the sentence
#: a refusal owes the analyst. Governed by the metric's own contract unit,
#: which is the thing that makes "$5,000" illegal on a rate and legal on a
#: money measure.
LEGAL_THRESHOLD_PHRASES: dict[str, tuple[str, ...]] = {
    "ratio": (
        "more than 2 points",
        "more than half a point",
        "when it crosses 15%",
        "on any movement",
    ),
    "money_cents": (
        "more than $5,000",
        "more than 10%",
        "on any movement",
    ),
    # A lag metric is the one contract whose own unit is also the natural
    # way a human states a threshold for it, so "more than 2 days" is read,
    # carried as a `days` threshold and applied in the metric's own unit.
    # It stays illegal over every other contract, refused by name — "2 days"
    # on a denial rate has no meaning.
    "days": (
        "more than 2 days",
        "more than 10% (a fraction of the current value)",
        "on any movement",
        "nothing at all, and this monitor uses the pack's governed gate for days",
    ),
    "count": (
        "more than 10% (a fraction of the current value)",
        "on any movement",
        "nothing at all, and this monitor uses the pack's governed gate for counts",
    ),
}

#: The fallback list, for a metric whose unit this module has no phrasing
#: table for. Still concrete: a refusal with no way forward is a wall.
GENERIC_THRESHOLD_PHRASES: tuple[str, ...] = (
    "more than 10%",
    "on any movement",
)


def legal_threshold_phrases(unit: str | None) -> list[str]:
    """The phrasings this platform accepts for a metric in ``unit``."""
    return list(LEGAL_THRESHOLD_PHRASES.get(unit or "", GENERIC_THRESHOLD_PHRASES))


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


def _monitor_from_clause(clause: str) -> Monitor | None:
    """The stated sensitivity, as a typed monitor.

    Deliberately conservative: a clause this grammar does not recognise
    yields ``None``, which means "the governed default", not a guessed
    threshold. A monitor that fires on a number nobody stated is worse than
    one that fires on the pack's.
    """
    direction = "any"
    if _DIRECTION_UP.search(clause) and not _DIRECTION_DOWN.search(clause):
        direction = "up"
    elif _DIRECTION_DOWN.search(clause) and not _DIRECTION_UP.search(clause):
        direction = "down"

    crosses = _CROSSES.search(clause)
    if crosses is not None:
        value = _magnitude(crosses)
        if value is not None:
            unit = _threshold_unit(crosses.group("unit"), clause)
            # A crossing is a LEVEL, and a level cannot be relative to
            # anything — "crosses 15%" names 15 percent, not 15 percent of
            # the reference value. ``points`` is the reading that converts
            # to the metric's own unit (15 → 0.15 on a rate); a
            # ``relative_pct`` crossing would be a threshold that moved
            # every time the thing it monitors did.
            if unit == "relative_pct":
                unit = "points"
            return Monitor(
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
            return Monitor(
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
        value = _magnitude(delta)
        if value is not None:
            unit = _threshold_unit(delta.group("unit"), clause)
            if unit == "cents":
                value = value * 100
            return Monitor(
                mode="delta_gte",
                value=value,
                unit=unit,
                direction=direction,
                note=f"declared in words: {clause.strip()}",
            )

    if _ANY_MOVEMENT.search(clause):
        return Monitor(
            mode="any_movement",
            direction=direction,
            note=f"declared in words: {clause.strip()}",
        )
    if direction != "any":
        # A direction with no magnitude narrows the governed default rather
        # than replacing it: "tell me if it rises" is a real instruction and
        # is not an instruction about size.
        return Monitor(
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

    The reading is COMMITTED to here and disclosed at the confirmation:
    against a rate, ``relative_pct`` gates on a fraction of the current
    value, which on a 25.9% base makes "2%" about half a point — four times
    tighter than the pack's own gate. That is a legal reading of the words
    and it is not the only one, so :func:`revi_api.monitors._monitor_confirmation`
    names the alternative rather than leaving the analyst to discover the
    difference from the brief (round-7 FN-6).
    """
    text = (raw or "").lower()
    if text.startswith(("point", "pt", "percentage point")):
        return "points"
    if text.startswith("day"):
        return "days"
    if text in ("%", "percent"):
        return "relative_pct"
    if text.startswith("dollar") or text == "$" or "$" in clause:
        return "cents"
    return "relative_pct"


def _magnitude(match: re.Match[str]) -> Decimal | None:
    """The stated size, whether it was typed as digits or as words."""
    digits = match.group("value")
    if digits:
        return _number(digits)
    word = (match.group("word") or "").lower().strip()
    spelled = _NUMBER_WORDS.get(" ".join(word.split()))
    return None if spelled is None else _number(spelled)


def _number(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):  # pragma: no cover - regex guards this
        return None
