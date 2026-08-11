"""Re-presentation requests: what may be reordered, and what must be refused."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from revi_investigation.application.findings import (
    as_number as _as_number,
)
from revi_investigation.application.interpretation import (
    PRESENTATION_CHANGE_REQUEST,
)
from revi_investigation.domain.records import (
    Finding,
)
from revi_investigation.domain.turns import (
    ClarificationRequest,
)
from revi_kernel.frame import EvidenceFrame, primary_measure
from revi_kernel.scope import AbsoluteRange

#: "Export this" and the ways people say it. A file is not something this
#: engine can hand over: the export is composed in the client, from the
#: answer already on screen, and nothing on the wire produces a document.
_EXPORT_REQUEST = re.compile(
    r"(?<!\w)(?:export|download|save\s+(?:this|that|it)|send\s+(?:me\s+)?(?:this|that|it)|"
    r"email\s+(?:me\s+)?(?:this|that|it)|(?:as|to)\s+(?:a\s+)?(?:csv|excel|xlsx|pdf|"
    r"spreadsheet)|give\s+me\s+(?:a\s+)?(?:csv|file|spreadsheet))(?!\w)",
    re.IGNORECASE,
)


def _export_refusal_sentence(question: str) -> str | None:
    """Refuse an export by name rather than re-answering.

    "Export this" used to come back ``turn_class: presentation_only`` with a
    freshly written paraphrase of the same finding — no file, no download,
    no sentence saying where export lives. A reader who says "export this"
    and gets a re-worded paragraph concludes the export failed or the
    assistant is stalling.

    The export itself is real and good: Copy answer and the CSV download
    sit on the answer card, run entirely in the browser, and carry the
    provenance line and the provisional marks. What does not exist is a
    way to ASK for it in words, so this says exactly that and points at the
    control — the shape this platform uses for every other thing it cannot
    do.
    """
    match = _EXPORT_REQUEST.search(question)
    if match is None:
        return None
    # "export" and "download" are bare verbs and want an object; "give me a
    # spreadsheet" already has one, and appending another produced "you
    # asked to give me a spreadsheet this".
    asked = match.group(0).lower()
    asked = f"{asked} this" if " " not in asked else asked
    return (
        f"you asked to {asked}, and I cannot hand you a file from here — nothing I return "
        "is a document. The export is on the answer itself: 'Copy answer' puts the "
        "findings, the analysis and every caveat on your clipboard, and the CSV download "
        "saves the rows with their provisional marks and the data load in the filename. "
        "There is no new answer here and nothing was exported by this turn."
    )


def _export_request_refusal(question: str) -> str | None:
    """The export refusal as a warning code, for the turn's own record."""
    sentence = _export_refusal_sentence(question)
    return None if sentence is None else f"refinement_not_applied: {sentence}"


def _unapplied_presentation_sentence(question: str) -> str | None:
    """Name what a re-presentation was asked to change and did not do.

    ``REFINEMENT_NOT_APPLIED`` was long registered with no caller on this
    path, so a turn could re-serve the parent's rows in the parent's order
    while reading like a fresh analysis that had honoured the request.
    """
    match = PRESENTATION_CHANGE_REQUEST.search(question)
    if match is None:
        return None
    return (
        f"you asked to {match.group(0).lower()} — and I could not resolve that against the "
        "rows on screen, so nothing was re-ordered and no new answer was produced. Name the "
        "column to order by (or ask for the cut you want) and I will re-run it."
    )


def _unapplied_presentation_request(question: str) -> str | None:
    """The unapplied-presentation note as a warning code."""
    sentence = _unapplied_presentation_sentence(question)
    return None if sentence is None else f"refinement_not_applied: {sentence}"


#: Marks the turn that asked this platform to re-present something and got
#: nothing new for it. The turn is a REFUSAL, not an answer: without this,
#: the payload carried ``REFINEMENT_NOT_APPLIED`` beside
#: ``outcome: answer`` — the engine recording that the instruction changed
#: nothing while shipping it as though it had.
PRESENTATION_PRODUCED_NOTHING_REASON = "PRESENTATION_PRODUCED_NOTHING"


def _presentation_refusal(question: str) -> ClarificationRequest | None:
    """The refusal card for a re-presentation that produced no artifact.

    One shape for both dead ends this path can reach — an export asked for
    in words, and an ordering that resolves against no column the rows
    carry. Neither produces a file, a chart or a row that was not already
    on screen, and this platform's word for that is a refusal.

    No options on purpose: there is nothing to tap. The control is NAMED in
    the sentence, which is what the reader needs, and
    ``_no_options_card`` labels the card so a renderer draws a statement
    rather than a question above an empty row of buttons.
    """
    sentence = _export_refusal_sentence(question) or _unapplied_presentation_sentence(question)
    if sentence is None:
        return None
    return ClarificationRequest(
        question=sentence[0].upper() + sentence[1:],
        options=(),
        reason=(
            f"{PRESENTATION_PRODUCED_NOTHING_REASON}: this turn re-presented an existing "
            "answer and produced no new artifact, so it is refused rather than served as a "
            "second answer over the same rows"
        ),
    )


#: Words that say which END of an ordering comes first.
_ORDER_DESCENDING = re.compile(
    r"(?<!\w)(?:largest|biggest|highest|greatest|most|worst|descending|desc|"
    r"high\s+to\s+low)(?!\w)",
    re.IGNORECASE,
)


_ORDER_ASCENDING = re.compile(
    r"(?<!\w)(?:smallest|lowest|least|best|ascending|asc|low\s+to\s+high)(?!\w)",
    re.IGNORECASE,
)


_ORDER_BY_NAME = re.compile(r"(?<!\w)(?:alphabetical(?:ly)?|by\s+name|by\s+title)(?!\w)", re.IGNORECASE)


#: The analyst's words for the parts a published value name is built from.
#: A key is only chosen when EVERY one of its parts is named, so "percent
#: change" selects ``pct_change`` and never ``delta_cents``.
_VALUE_PART_WORDS: Mapping[str, tuple[str, ...]] = {
    "pct": ("pct", "percent", "percentage"),
    "change": ("change", "changes", "movement", "moved", "difference", "delta"),
    "delta": ("delta", "change", "changes", "movement", "moved", "difference"),
    "cents": ("dollars", "dollar", "amount", "amounts", "value", "values", "size", "magnitude"),
    "current": ("current", "value", "values", "level", "size"),
    "prior": ("prior", "previous", "baseline"),
    "rank": ("rank", "ranking", "position"),
    "share": ("share", "percent", "percentage"),
    "first": ("first",),
    "last": ("last",),
    "high": ("high", "highest"),
    "low": ("low", "lowest"),
    "periods": ("periods",),
}


def presentation_ordering(
    question: str, findings: Sequence[Finding]
) -> tuple[str, bool] | None:
    """``(value key, descending)`` the utterance asks the SHOWN rows to be in.

    "Sort them by percent change, largest first" names a column the served
    findings already carry (``pct_change``), so the honest
    answer is to serve them in that order — not to re-plan the question from
    scratch (which collapsed twelve rows back to three), and not to re-serve
    the parent's order under a note saying the request was ignored.

    Deliberately conservative. A key is chosen only when every part of its
    name is named by the analyst and exactly one key qualifies, and only
    when EVERY served finding carries a number for it — a partial order over
    a list where some rows have no value is a different list, not a sorted
    one. ``("", ascending)`` is the by-title case; ``None`` means the
    request could not be resolved, and the caller says so.
    """
    if not findings:
        return None
    # An ordering is applied only when one was ASKED for. "Show me that
    # ranking again" names a column and instructs nothing; re-ordering it
    # would be the engine rearranging an answer nobody asked it to touch.
    if PRESENTATION_CHANGE_REQUEST.search(question) is None:
        return None
    descending = _ORDER_ASCENDING.search(question) is None
    if _ORDER_BY_NAME.search(question) is not None:
        # "alphabetically" states its own direction; "reverse alphabetical"
        # is the one that needs the descending word to be read.
        return "", _ORDER_DESCENDING.search(question) is not None
    tokens = {token.casefold() for token in re.findall(r"[A-Za-z']+", question)}
    published = [dict(finding.values) for finding in findings]
    # The free test first — a name the analyst did not say cannot be the
    # column they asked to sort by, and the coverage test below reads every
    # row of every candidate.
    named = [
        key
        for key in published[0]
        if (parts := [part for part in key.split("_") if part])
        and all(
            any(word in tokens for word in _VALUE_PART_WORDS.get(part, (part,)))
            for part in parts
        )
    ]
    # …and only then: a partial order over a list where some rows have no
    # value is a different list, not a sorted one.
    qualifying = [
        key
        for key in named
        if all(_as_number(values.get(key)) is not None for values in published)
    ]
    if len(qualifying) != 1:
        return None
    return qualifying[0], descending


def _reordered(
    findings: Sequence[Finding], key: str, descending: bool
) -> tuple[Finding, ...]:
    """The same findings, in the order the analyst asked for.

    ``key`` is one :func:`presentation_ordering` has already proved every
    finding carries a number for, so the sort key cannot be missing.
    """
    if not key:
        return tuple(sorted(findings, key=lambda f: f.title.casefold(), reverse=descending))
    return tuple(
        sorted(
            findings,
            key=lambda f: _as_number(dict(f.values).get(key)) or Decimal(0),
            reverse=descending,
        )
    )


def _chart_sorts_for(
    frames: Sequence[tuple[str, EvidenceFrame]], key: str, descending: bool
) -> tuple[tuple[str, str, bool], ...]:
    """The chart ordering that matches a re-served finding order.

    A ranked answer and the chart under it must not disagree about which
    cell is first. The finding value name and the frame column name
    are not always the same string — a compared rate publishes ``pct_change``
    on the finding and ``<measure>__pct_change`` on the frame — so the
    PUBLISHED measure's own spelling is looked for first. That precedence is
    load-bearing: a ratio frame also carries ``<measure>__num__pct_change``,
    the numerator's movement, which sorts by a different number entirely.
    """
    if not key:
        return ()
    out: list[tuple[str, str, bool]] = []
    for frame_id, frame in frames:
        names = frame.schema.names
        measure = primary_measure(frame)
        candidates = [
            f"{measure}__{key}" if measure else None,
            key,
            *sorted(
                (name for name in names if name.endswith(f"__{key}")),
                key=lambda name: name.count("__"),
            ),
        ]
        column = next((c for c in candidates if c is not None and c in names), None)
        if column is not None:
            out.append((frame_id, column, descending))
    return tuple(out)


def _frame_windows_from_trace(
    raw: Any,
) -> tuple[tuple[str, AbsoluteRange, AbsoluteRange | None], ...]:
    """The per-frame windows a recorded plan resolved, off its trace.

    A frame whose dates will not parse is skipped rather than defaulted:
    the caller then falls back to the turn's own window, which is what the
    chart did before per-frame windows existed — a worse label, never a
    fabricated one.
    """
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    out: list[tuple[str, AbsoluteRange, AbsoluteRange | None]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        frame_id = entry.get("frame_id")
        window = _range_from_trace(entry.get("start"), entry.get("end"))
        if not isinstance(frame_id, str) or window is None:
            continue
        out.append(
            (frame_id, window, _range_from_trace(entry.get("prior_start"), entry.get("prior_end")))
        )
    return tuple(out)


def _range_from_trace(start: Any, end: Any) -> AbsoluteRange | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        return AbsoluteRange(start=date.fromisoformat(start), end=date.fromisoformat(end))
    except ValueError:
        return None


def _chart_sorts_from_trace(raw: Any) -> tuple[tuple[str, str, bool], ...]:
    """The orderings a recorded plan resolved, read back off its trace."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    out: list[tuple[str, str, bool]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        frame_id, by = entry.get("frame_id"), entry.get("by")
        if isinstance(frame_id, str) and isinstance(by, str):
            out.append((frame_id, by, bool(entry.get("descending", True))))
    return tuple(out)


def _frame_windows_payload(
    windows: Sequence[tuple[str, AbsoluteRange, AbsoluteRange | None]],
) -> list[dict[str, str | None]]:
    """Per-frame windows in the shape a trace records and replays.

    One writer for the three call sites that record it, so a re-served turn
    and the turn it re-serves cannot spell the same fact two ways.
    """
    return [
        {
            "frame_id": frame_id,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "prior_start": None if prior is None else prior.start.isoformat(),
            "prior_end": None if prior is None else prior.end.isoformat(),
        }
        for frame_id, window, prior in windows
    ]
