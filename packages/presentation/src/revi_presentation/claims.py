"""Directional and ranking claims, checked against the ranges under them.

The grounding validator in :mod:`revi_presentation.narrative` has always
been able to check that a NUMBER was measured. It has never been able to
check that a SENTENCE ABOUT those numbers was earned. That gap published
this, over a payer's appeal overturn rate:

    "only Atlas Commercial has a readable direction, and it is getting
    worse on appeals: 47.2% Aug, 40.7% Sep, 48.0% Oct, 36.7% Nov, 43.2%
    Dec, 45.2% Jan 2026, ending below where it started."

Every figure in it was measured. The intervals were in the same payload,
and every one of them overlapped every other — the point estimates span
11.3 points and the NARROWEST range is 28 points wide. The series was also
truncated at January without saying so: February through May exist, and
leaving them out is the only reason "ending below where it started" could
be written at all.

The same determination declared a facility "the best revenue quality" and
another "the weakest" on a 1.1-point spread with no ranges published —
then, two clauses later, correctly refused to separate denial rates inside
a 0.6-point band. One paragraph, two standards.

So this module is the standard, in code. Three deterministic verdicts over
a sentence and the reading it rests on:

**A direction whose endpoints overlap is not a direction.** The sentence
is replaced — not dropped — by the honest form the reading composed for
itself: the two figures, and the statement that the ranges around them
overlap.

**A best-or-worst inside its own uncertainty is not an ordering.** The
sentence is replaced by a refusal, the same discipline a ranking already
follows when too much of its field is a ceiling.

**A claim's window is its reading's window.** A direction quoted over part
of the series it rests on is dropped outright; there is no honest rewrite
of a sentence whose support was chosen to fit it.

Every replacement sentence is composed in :func:`build_reading_series`,
beside the figures, out of the figures' OWN formatted displays. The
validator decides whether to swap one in; it never composes a number.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from revi_investigation_contracts.deep_research import ResearchReadingPayload
from revi_investigation_contracts.narrative import ReadingSeries, SeriesPoint

#: Wording that turns a claim into a report of what did NOT happen. A
#: sentence quoting a verdict ("denial rate did not rise", "no facility is
#: separable here") is the sentence these rules want; only the affirmative
#: claim is checked. ``narrative._magnitude_claim`` uses the same escape
#: hatch and imports this same pattern, so the two rules cannot drift into
#: disagreeing about what a negation is.
NEGATION = re.compile(
    r"\b(?:did not|didn'?t|does not|doesn'?t|was not|wasn'?t|were not|weren'?t|never|not|"
    r"short of|fell short|falls short|far from|rather than|instead of|without|no)\b",
    re.IGNORECASE,
)

#: A claim that something moved one way. Comparatives belong here ("worse",
#: "better"); superlatives belong to :data:`RANKING`. "Moved" is
#: deliberately absent — it is the neutral verb the findings themselves use
#: ("cash posted moved from 18722151 to 8812843 cents") and the verb the
#: honest replacement below is written in.
DIRECTIONAL = re.compile(
    r"\b(?:"
    r"rose|rise|rises|rising|risen|"
    r"fell|fall|falls|falling|fallen|"
    r"climb|climbs|climbed|climbing|"
    r"drop|drops|dropped|dropping|"
    r"worsen|worsens|worsened|worsening|worse|"
    r"improve|improves|improved|improving|better|"
    r"deteriorate|deteriorates|deteriorated|deteriorating|deterioration|"
    r"increase|increases|increased|increasing|"
    r"decrease|decreases|decreased|decreasing|"
    r"decline|declines|declined|declining|"
    r"trend|trends|trending|trended|"
    r"upward|downward|uptrend|downtrend|"
    r"direction|directional|trajectory|"
    r"getting worse|getting better|"
    r"ending below|ending above|"
    r"below where it started|above where it started"
    r")\b",
    re.IGNORECASE,
)

#: A claim that one row leads or trails the others.
RANKING = re.compile(
    r"\b(?:best|worst|highest|lowest|largest|smallest|biggest|"
    r"strongest|weakest|top|bottom|leader|laggard|most|least|"
    r"outperform|outperforms|outperformed|outperforming|"
    r"underperform|underperforms|underperformed|underperforming)\b",
    re.IGNORECASE,
)

#: The shape whose rows are periods in order. Only over this shape does a
#: first and last row mean a start and an end.
_TREND_SHAPE = "trend"

#: Below this many characters a label's leading token is too generic to
#: match a sentence on ("Q1", "AR").
_MIN_LABEL_TOKEN = 3


@dataclass(frozen=True)
class ClaimVerdict:
    """Why a sentence cannot stand, and what stands in its place.

    ``substitute`` empty means the sentence is dropped with nothing put
    back — the verdict for a claim whose support was truncated, where
    there is no honest shorter statement to make.
    """

    reason: str
    substitute: str
    reading_id: str


# ---------------------------------------------------------------------------
# building the series — where the figures are, and where the words are made


def _decimal(text: str | None) -> Decimal | None:
    if text is None or not text.strip():
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _width(point: SeriesPoint) -> Decimal | None:
    """How wide the range around this figure is, when it carries one."""
    if point.interval_low is None or point.interval_high is None:
        return None
    return abs(point.interval_high - point.interval_low)


def _middle(widths: Sequence[Decimal]) -> Decimal | None:
    """The middle width of a set, taking the lower of two middles.

    The middle rather than the widest: one tiny-population reading in a
    study should not lend its enormous range to every sibling that
    published none. Lower-of-two so the answer is one of the widths that
    actually occurred rather than an average of two that did not.
    """
    if not widths:
        return None
    ordered = sorted(widths)
    return ordered[(len(ordered) - 1) // 2]


def _measured(points: Sequence[SeriesPoint]) -> list[SeriesPoint]:
    """The points that are measurements — not ceilings, not withheld."""
    return [p for p in points if p.value is not None and not p.withheld and not p.bounded]


def _direction_substitute(measure_label: str, points: Sequence[SeriesPoint]) -> str:
    """What a reading CAN say about its own two ends.

    Composed from the figures' own displays and nothing else: this module
    holds the numbers, so this module writes the sentence, and the
    validator downstream only decides whether to put it in. It says the
    movement and then says what the movement is worth, which is the whole
    of the correction — a reader who is told only "the ranges overlap"
    loses the two figures they came for.
    """
    measured = _measured(points)
    if len(measured) < 2:
        return ""
    first, last = measured[0], measured[-1]
    if not first.display or not last.display:
        return ""
    label = measure_label or "This reading"
    return (
        f"{label} reads {first.display} in {first.label} and {last.display} in "
        f"{last.label}, but the ranges around those two figures overlap — read it as "
        "noise-compatible rather than as a direction."
    )


def _ranking_refusal(measure_label: str, points: Sequence[SeriesPoint]) -> str:
    """The refusal a spread inside its own uncertainty earns.

    Written in the register the bounded-cell refusal already uses: the
    meaning leads, the two ends are named in their own units, and the
    reason is one clause a reader can act on.
    """
    measured = _measured(points)
    if len(measured) < 2:
        return ""
    lowest = min(measured, key=lambda p: p.value or Decimal(0))
    highest = max(measured, key=lambda p: p.value or Decimal(0))
    if not lowest.display or not highest.display:
        return ""
    label = measure_label or "This reading"
    return (
        f"{label} runs from {lowest.display} to {highest.display} across the "
        f"{len(measured)} groups in this reading — a gap narrower than the range each of "
        "these figures already carries, so no best or worst is named here."
    )


def build_reading_series(
    readings: Sequence[ResearchReadingPayload],
) -> list[ReadingSeries]:
    """The interval channel a determination is validated against.

    One entry per reading that published at least one figure. The ranges
    come off the wire exactly as the estimators produced them; nothing here
    computes an interval, and a study that published none gets a channel
    that refuses nothing.
    """
    widths_by_unit: dict[str, list[Decimal]] = {}
    series_points: list[tuple[ResearchReadingPayload, list[SeriesPoint]]] = []
    for reading in readings:
        points: list[SeriesPoint] = []
        for figure in reading.figures:
            interval = figure.interval
            point = SeriesPoint(
                label=figure.label,
                display=figure.display,
                value=_decimal(figure.value),
                interval_low=None if interval is None else _decimal(interval.low),
                interval_high=None if interval is None else _decimal(interval.high),
                bounded=figure.bounded,
                withheld=figure.withheld,
            )
            points.append(point)
            width = _width(point)
            if width is not None:
                widths_by_unit.setdefault(reading.unit, []).append(width)
        if points:
            series_points.append((reading, points))

    out: list[ReadingSeries] = []
    for reading, points in series_points:
        own = [w for w in (_width(p) for p in points) if w is not None]
        # This reading's own worst-case precision where it has one;
        # otherwise what the study demonstrated on measures of the same
        # unit. Never a number invented to make a guard fire.
        comparable = max(own) if own else _middle(widths_by_unit.get(reading.unit, []))
        out.append(
            ReadingSeries(
                reading_id=reading.id,
                measure_label=reading.measure_label,
                window_label=reading.window_label,
                over_time=reading.shape == _TREND_SHAPE,
                points=points,
                comparable_interval_width=comparable,
                direction_substitute=_direction_substitute(reading.measure_label, points),
                ranking_refusal=_ranking_refusal(reading.measure_label, points),
            )
        )
    return out


# ---------------------------------------------------------------------------
# matching a sentence to the reading it is about


def _mentions(sentence: str, point: SeriesPoint) -> bool:
    """Does this sentence name this figure — by its label or its value?

    Both, because a determination cites a series either way: "48.0% Oct"
    names the month by its short form and the figure by its display, and a
    facility sentence names the facility and quotes its rate. The label's
    leading token is enough ("Aug" for "Aug 2025"), which is how a reader
    writes a month and how the failing determination wrote all six of them.
    """
    if point.display and point.display.casefold() in sentence:
        return True
    if not point.label:
        return False
    if point.label.casefold() in sentence:
        return True
    lead = point.label.split()[0] if point.label.split() else ""
    if len(lead) < _MIN_LABEL_TOKEN or not lead[0].isalpha():
        return False
    return re.search(rf"\b{re.escape(lead.casefold())}\b", sentence) is not None


def _cited(sentence: str, series: ReadingSeries) -> list[int]:
    """Indices of the figures this sentence names, in the reading's order."""
    return [index for index, point in enumerate(series.points) if _mentions(sentence, point)]


def _subject(
    sentence: str, all_series: Sequence[ReadingSeries]
) -> tuple[ReadingSeries, list[int]] | None:
    """The reading a sentence is about, and which of its figures it names.

    The reading whose figures the sentence names MOST. A sentence naming
    none of any reading's figures is not attributed to one: guessing which
    table a claim meant would redact the wrong sentence for the wrong
    reason, which is worse than not firing.
    """
    best: tuple[ReadingSeries, list[int]] | None = None
    flat = sentence.casefold()
    for series in all_series:
        cited = _cited(flat, series)
        if not cited:
            continue
        if best is None or len(cited) > len(best[1]):
            best = (series, cited)
    return best


# ---------------------------------------------------------------------------
# the three verdicts


def _truncation_verdict(
    series: ReadingSeries, cited: Sequence[int]
) -> ClaimVerdict | None:
    """Does this claim quote less of the series than the reading published?

    The claim's window IS the reading's window. A direction over Aug
    through Jan of a series that runs Aug through May is a statement about
    a slice the sentence chose, and "ending below where it started" is true
    of that slice and false of the reading. There is no honest shorter
    version of such a sentence, so nothing is put back in its place.
    """
    measured = [i for i, p in enumerate(series.points) if p.value is not None and not p.withheld]
    if len(cited) < 2 or len(measured) < 2:
        return None
    first, last = measured[0], measured[-1]
    starts_late = min(cited) > first
    stops_early = max(cited) < last
    if not (starts_late or stops_early):
        return None
    edge = series.points[max(cited)].label if stops_early else series.points[min(cited)].label
    verb = "stops at" if stops_early else "starts at"
    label = series.measure_label or "this reading"
    return ClaimVerdict(
        reason=(
            f"reads a direction into {label} over part of the series it rests on — the "
            f"reading runs {series.points[first].label} to {series.points[last].label} and "
            f"this sentence {verb} {edge}"
        ),
        substitute="",
        reading_id=series.reading_id,
    )


def _overlap_verdict(series: ReadingSeries) -> ClaimVerdict | None:
    """Do the two ends of this reading's range overlap each other?

    Overlapping means neither end excludes the other: ``low_a <= high_b``
    and ``low_b <= high_a``. When they do, the difference between the two
    point estimates is compatible with no movement at all, and a sentence
    calling it a direction is telling the reader something the data does
    not know.
    """
    measured = _measured(series.points)
    if len(measured) < 2 or not series.direction_substitute:
        return None
    first, last = measured[0], measured[-1]
    if _width(first) is None or _width(last) is None:
        return None
    assert first.interval_low is not None and first.interval_high is not None
    assert last.interval_low is not None and last.interval_high is not None
    overlaps = (
        first.interval_low <= last.interval_high and last.interval_low <= first.interval_high
    )
    if not overlaps:
        return None
    label = series.measure_label or "this reading"
    return ClaimVerdict(
        reason=(
            f"reads a direction into {label} figures whose ranges overlap, so it was "
            "replaced with what those ranges support"
        ),
        substitute=series.direction_substitute,
        reading_id=series.reading_id,
    )


def _ranking_verdict(series: ReadingSeries, cited: Sequence[int]) -> ClaimVerdict | None:
    """Is the gap this sentence orders smaller than its own uncertainty?

    The threshold is the wider of the two named figures' own ranges where
    both carry one, and otherwise the width this study demonstrated for
    figures of the same kind. A study measuring rates of this sort to
    within thirty points does not get to name a best and a worst 1.1 points
    apart, and the fact that the facility reading published no ranges of
    its own is the reason to refuse rather than an exemption from refusing.
    """
    if not series.ranking_refusal:
        return None
    named = [
        series.points[i]
        for i in cited
        if series.points[i].value is not None and not series.points[i].withheld
    ]
    if len(named) < 2:
        return None
    lowest = min(named, key=lambda p: p.value or Decimal(0))
    highest = max(named, key=lambda p: p.value or Decimal(0))
    assert lowest.value is not None and highest.value is not None
    spread = highest.value - lowest.value
    widths = [w for w in (_width(lowest), _width(highest)) if w is not None]
    threshold = max(widths) if len(widths) == 2 else series.comparable_interval_width
    if threshold is None or spread >= threshold:
        return None
    label = series.measure_label or "this reading"
    return ClaimVerdict(
        reason=(
            f"names a best or worst across {label} figures whose difference is smaller "
            "than the range each of them carries, so it was replaced with the refusal "
            "that spread earns"
        ),
        substitute=series.ranking_refusal,
        reading_id=series.reading_id,
    )


def claim_verdict(
    sentence: str, all_series: Sequence[ReadingSeries]
) -> ClaimVerdict | None:
    """Whether this sentence's direction or ordering survives its ranges.

    ``None`` — the answer for almost every sentence — when the sentence
    makes no such claim, names no reading's figures, reports what did NOT
    happen, or makes a claim the ranges support.
    """
    if not all_series:
        return None
    directional = DIRECTIONAL.search(sentence) is not None
    ranking = RANKING.search(sentence) is not None
    if not (directional or ranking):
        return None
    if NEGATION.search(sentence):
        return None
    subject = _subject(sentence, all_series)
    if subject is None:
        return None
    series, cited = subject
    if directional and series.over_time:
        truncated = _truncation_verdict(series, cited)
        if truncated is not None:
            return truncated
        overlapping = _overlap_verdict(series)
        if overlapping is not None:
            return overlapping
    if ranking:
        return _ranking_verdict(series, cited)
    return None
