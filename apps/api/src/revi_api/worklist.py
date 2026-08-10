"""The conversation's read path onto the ranked anomaly worklist.

The platform computes a prioritised, reconciled worklist — detected cards
across two lanes, a published priority decomposition and a governed
recoverable estimate per card — and serves it at
``GET /v1/portfolio/latest``. This module is how a conversational turn
reaches that same list, so that asking which work to pick up first and
opening the rail cannot answer differently.

Two seams, no new pipeline:

* **Governed routing.** ``packs/base-rcm/worklist.yaml`` names the pack
  artifacts that mean "which work should I pick up" — a playbook id
  (``daily_portfolio``, whose triggers carry the prioritisation phrasings)
  and a concept id (``work_prioritization``, whose aliases carry the
  analyst vocabulary). Interpretation maps an utterance onto governed ids
  exactly as it always has; this module reads WHICH ids it chose. No
  question string is matched anywhere in the platform, and none is added
  here — that is the whole point of putting the mapping in the pack.
* **One computation.** The cards published are the same
  :class:`~revi_investigation_contracts.api.AnomalyCard` objects the rail
  renders, from the same :func:`~revi_api.portfolio.build_portfolio` call:
  same formula version, same decomposition, same ``ranked_on``, same
  reconciliation state, same recoverable estimates, same warnings. A chat
  answer and the rail cannot disagree about the order or the money.

The statement is composed here, deterministically, from the build — never
by a model. It names the formula, the lanes, the top card, the recoverable
total, how many cards were withheld, and how many are ranked on this
platform's re-derived figure rather than the detector's.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from revi_api.portfolio import COMPLIANCE_LANE, VALUE_LANE
from revi_investigation_contracts.api import (
    AnomalyCard,
    PortfolioResponse,
    WorklistPayload,
    WorklistQuery,
)

#: Where the routing lives, relative to the pack directory.
WORKLIST_FILENAME = "worklist.yaml"

_LANES = (COMPLIANCE_LANE, VALUE_LANE)


@dataclass(frozen=True, slots=True)
class WorklistRouting:
    """Which governed artifacts mean "show me the worklist".

    Empty (``enabled`` false) when the pack ships no ``worklist.yaml``: a
    deployment whose pack does not declare the routing simply never
    attaches a worklist, which is a stated absence rather than a silent
    behavior change.
    """

    playbook_ids: frozenset[str] = frozenset()
    concept_ids: frozenset[str] = frozenset()
    default_limit: int = 8
    max_limit: int = 25
    label: str = ""
    description: str = ""
    content_hash: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.playbook_ids or self.concept_ids)

    def match(
        self, *, playbook_id: str | None, concepts: tuple[str, ...]
    ) -> tuple[str, str] | None:
        """``(matched_on, matched_id)`` for a turn, or ``None``.

        The playbook is checked first: it is the stronger signal (the whole
        investigation was planned from it), and a concept can ride along on
        a turn that is mostly about something else.
        """
        if playbook_id is not None and playbook_id in self.playbook_ids:
            return "playbook", playbook_id
        for concept in concepts:
            if concept in self.concept_ids:
                return "concept", concept
        return None

    def bounded_limit(self, requested: int | None) -> int:
        if requested is None:
            return max(1, self.default_limit)
        return max(1, min(requested, self.max_limit))


def load_worklist_routing(path: str | Path) -> WorklistRouting:
    """Read the governed routing, or an empty one when the pack has none.

    A missing file is not an error — a pack that declares no worklist
    routing is a pack whose answers carry no worklist. A malformed one IS
    an error: routing that silently fails to load leaves the conversation
    unable to reach the list, which is the failure this module exists to
    prevent.
    """
    file = Path(path)
    if not file.is_file():
        return WorklistRouting()
    raw = file.read_text(encoding="utf-8")
    document: Any = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a mapping document")
    playbooks = document.get("playbook_ids", []) or []
    concepts = document.get("concept_ids", []) or []
    if not isinstance(playbooks, list) or not isinstance(concepts, list):
        raise ValueError(f"{path}: 'playbook_ids' and 'concept_ids' must be lists")
    default_limit = int(document.get("default_limit", 8))
    max_limit = int(document.get("max_limit", 25))
    if default_limit < 1 or max_limit < default_limit:
        raise ValueError(
            f"{path}: default_limit must be >= 1 and max_limit >= default_limit"
        )
    return WorklistRouting(
        playbook_ids=frozenset(str(p) for p in playbooks),
        concept_ids=frozenset(str(c) for c in concepts),
        default_limit=default_limit,
        max_limit=max_limit,
        label=" ".join(str(document.get("label", "")).split()),
        description=" ".join(str(document.get("description", "")).split()),
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _statement(
    portfolio: PortfolioResponse,
    *,
    published: Sequence[AnomalyCard],
    total: int,
    recoverable_cents: int,
    lane: str | None,
) -> str:
    """The answer, in sentences, from the build in hand.

    Every clause is a fact the payload also carries structurally, so a
    reader who checks finds the same numbers. Deliberately not composed by
    a model: this is a read of a computation, and a generated sentence
    could not be validated against it any more cheaply than writing it
    from the computation in the first place.
    """
    if total == 0:
        return (
            "There is no ranked work at this watermark: the detection feed reported no "
            "open anomalies, so this platform has no worklist to order."
        )
    parts: list[str] = []
    scope = f" in the {lane} lane" if lane is not None else ""
    parts.append(
        f"{len(published)} of {total} ranked cards{scope} at watermark "
        f"{portfolio.watermark_id}, highest governed priority first "
        f"({portfolio.formula_version}: normalised impact, recency, and the governed "
        f"recoverable estimate, with the cards this platform cannot yet investigate "
        f"listed last)."
    )
    lanes = {lane_payload.id: lane_payload for lane_payload in portfolio.lanes}
    lane_bits = [
        f"{lanes[lane_id].item_count} {lanes[lane_id].label.lower()}"
        for lane_id in _LANES
        if lane_id in lanes
    ]
    if lane_bits:
        parts.append("Lanes: " + "; ".join(lane_bits) + ".")
    # The first card OF THIS PAGE, not of the whole portfolio: a lane-scoped
    # read that led with the global top card would name a card the reader
    # was not shown.
    top = published[0] if published else None
    if top is not None:
        parts.append(
            f"First is {top.anomaly_id} — {top.title} — "
            f"{_dollars(abs(top.ranked_impact_cents))} ranked on the "
            f"{'platform' if top.ranked_on == 'platform' else 'detection system'}'s figure, "
            f"about {_dollars(top.recoverable_cents_estimate)} of it estimated recoverable "
            f"({top.actionability_label}), priority {top.priority_score:.6f}."
        )
    parts.append(
        f"Across the whole ranked population the governed recoverable estimate totals "
        f"{_dollars(recoverable_cents)}."
    )
    # Counted over the whole ranked population, and said to be: a count
    # taken over every ranked card, stated under a sentence about the
    # published page, is a different claim from the one the number supports.
    on_platform = sum(1 for card in portfolio.items if card.ranked_on == "platform")
    if on_platform:
        parts.append(
            f"Across the ranked population, {on_platform} card(s) are ranked on this "
            "platform's re-derived figure rather than the detector's, because the two "
            "diverge; each card publishes both numbers and which one ranked it."
        )
    blocked = sum(1 for card in portfolio.items if not card.drillable)
    if blocked:
        parts.append(
            f"{blocked} of the {len(portfolio.items)} cannot be opened at this catalog "
            "and pack version and carry the platform's own refusal; they sort last."
        )
    parts.append(
        "This is the detection feed's ranked work, not a measurement of the question "
        "asked above; the findings on this answer are that."
    )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# addressing the list


#: A card's own published id. The one unambiguous handle: the platform mints
#: it, prints it on every row, and ``GET /v1/portfolio/latest`` serves it.
_ANOMALY_ID = re.compile(r"\bANM[-_\s]?(\d{1,6})\b", re.IGNORECASE)

#: Positions, in the two forms an analyst writes them. Word ordinals cover
#: "the top item" / "open the first one"; the numeric form covers "number
#: 3", "#2", "item 4", "rank 2".
_ORDINAL_WORDS: dict[str, int] = {
    "top": 1,
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "sixth": 6,
    "6th": 6,
    "seventh": 7,
    "7th": 7,
    "eighth": 8,
    "8th": 8,
    "ninth": 9,
    "9th": 9,
    "tenth": 10,
    "10th": 10,
}
_ORDINAL_WORD = re.compile(
    r"\bthe\s+(" + "|".join(sorted(_ORDINAL_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_ORDINAL_NUMBER = re.compile(
    # ``#`` gets its own branch: a word boundary before a non-word character
    # requires a word character in front of it, so "#3" at the start of an
    # utterance never matched the alternation it was listed in.
    r"(?:\b(?:number|item|card|rank|row|priority)\s*#?\s*|#\s*)(\d{1,3})\b",
    re.IGNORECASE,
)
#: A finding handle. Present, the analyst is pointing at a finding and the
#: engine's own referent resolver owns the turn — this module stands down
#: rather than racing it for the same words.
_FINDING_HANDLE = re.compile(r"\b[FD]\d+\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class WorklistReference:
    """A worklist row the analyst named, and how they named it."""

    card: AnomalyCard
    mention: str
    basis: str  # anomaly_id | ordinal


def resolve_worklist_reference(
    utterance: str, cards: Sequence[AnomalyCard]
) -> WorklistReference | None:
    """The worklist row an utterance addresses, resolved deterministically.

    Without this the list is addressable by mouse and unaddressable by
    name: "open the top item" and "show me ANM-021" draw clarifications
    denying figures the same turn's own worklist payload had just printed,
    while ``/v1/portfolio/latest`` reports the card drillable and the rail's
    click dispatches its spec.

    So the ids and the positions this platform PRINTED are resolved the way
    every other handle it prints is resolved: by lookup, before any model
    call, against the rows the analyst was actually shown. The result routes
    to the card's STORED ``drill_spec`` with its ``anomaly_ref`` — the
    identical path the rail takes, so the reconciliation strip and the
    repoint disclosure fire exactly as they do from a click.

    ``None`` — meaning "this is ordinary language, hand it to the engine" —
    whenever nothing matches, whenever a position names a row that was not
    shown (a claim about a row the analyst cannot see is worse than a
    question), and whenever the utterance carries a finding handle, which
    belongs to the engine's referent registry and not to this list.
    """
    if not cards or not utterance.strip() or _FINDING_HANDLE.search(utterance):
        return None
    by_id = {card.anomaly_id.casefold(): card for card in cards}
    match = _ANOMALY_ID.search(utterance)
    if match is not None:
        # Matched on the digits so "ANM 21", "anm-021" and "ANM-021" are one
        # handle; the printed form is what gets echoed back.
        digits = match.group(1).lstrip("0") or "0"
        for key, card in by_id.items():
            trailing = key.rsplit("-", 1)[-1].lstrip("0") or "0"
            if trailing == digits:
                return WorklistReference(card=card, mention=match.group(0), basis="anomaly_id")
        return None
    position: int | None = None
    mention = ""
    word = _ORDINAL_WORD.search(utterance)
    if word is not None:
        position, mention = _ORDINAL_WORDS[word.group(1).lower()], word.group(0)
    else:
        number = _ORDINAL_NUMBER.search(utterance)
        if number is not None:
            position, mention = int(number.group(1)), number.group(0)
    if position is None or not 1 <= position <= len(cards):
        return None
    return WorklistReference(card=cards[position - 1], mention=mention, basis="ordinal")


#: Attached as an ordinary turn warning so the disclosure travels with the
#: answer's own warnings rather than only inside the payload.
WORKLIST_ATTACHED_PREFIX = "worklist_attached:"


def worklist_reference_warning(reference: WorklistReference) -> str:
    """What the platform resolved, said before the answer that used it."""
    card = reference.card
    return (
        f"named_cut_applied: read {reference.mention!r} as worklist row "
        f"{card.anomaly_id} — {card.title} — and opened the card's own stored drill "
        f"({', '.join(card.drill_spec.metric_ids)}), which is the same investigation the "
        "rail's click on that row runs. Nothing about the phrasing was interpreted: the id "
        "and the position are handles this platform published."
    )


def worklist_warning(payload: WorklistPayload) -> str:
    routed = (
        f"the governed {payload.matched_on} {payload.matched_id!r}"
        if payload.matched_on != "typed_query"
        else "an explicit worklist request on this turn"
    )
    return (
        f"{WORKLIST_ATTACHED_PREFIX} this answer also carries the ranked anomaly "
        f"worklist ({len(payload.items)} of {payload.total_items} cards), attached "
        f"because {routed} routed it. The cards are the detection feed's, ordered by "
        f"{payload.formula_version}; they are not findings this turn computed."
    )


#: Emitted only when the worklist ROUTED — i.e. the analyst's question
#: resolved the governed "what should I work first" playbook or concept —
#: and therefore only when the ranked cards ARE the answer.
WORKLIST_LEADS_PREFIX = "worklist_leads:"


def worklist_lead_warning(payload: WorklistPayload) -> str | None:
    """The sentence a work-prioritization answer must open with.

    When the worklist routed, it leads. Otherwise the ranked list renders
    below the findings, the charts and the prose, and the narrative's
    closing instruction can name a first action that is not the list's
    first row — two different answers to one question, in one response,
    under a worklist statement that labels itself "not a measurement of the
    question asked above".

    Composed from the payload the answer already carries (never from a
    model, and never a figure the payload does not hold), published verbatim
    ahead of the prose by the mandatory-disclosure machinery, and shown to
    the composer so the metric probes are written as what they are:
    also-measured context.

    ``None`` when the worklist was asked for explicitly (``typed_query``) or
    carries no cards — an attached list the analyst requested beside a
    different question is not that question's answer.
    """
    if payload.matched_on == "typed_query" or not payload.items:
        return None
    top = payload.items[0]
    lanes = "; ".join(
        f"{lane.item_count} {lane.label.lower()}" for lane in payload.lanes if lane.item_count
    )
    lane_clause = f" Lanes: {lanes}." if lanes else ""
    return (
        f"{WORKLIST_LEADS_PREFIX} this question routed to the governed "
        f"{payload.matched_on} {payload.matched_id!r}, so the ranked worklist below IS the "
        f"answer and the measurements on this answer are context beside it. Start with "
        f"{top.anomaly_id} — {top.title} — {_dollars(abs(top.ranked_impact_cents))}, about "
        f"{_dollars(top.recoverable_cents_estimate)} of it estimated recoverable "
        f"({top.actionability_label}). {payload.total_items} cards are ranked and the "
        f"governed recoverable estimate across them totals "
        f"{_dollars(payload.total_recoverable_cents_estimate)}.{lane_clause}"
    )


def worklist_first_action(payload: WorklistPayload | None) -> str | None:
    """The anomaly id no other first action may be named ahead of."""
    if payload is None or payload.matched_on == "typed_query" or not payload.items:
        return None
    return payload.items[0].anomaly_id


def build_worklist(
    portfolio: PortfolioResponse,
    routing: WorklistRouting,
    *,
    matched_on: str,
    matched_id: str,
    query: WorklistQuery | None = None,
) -> WorklistPayload:
    """Project a built portfolio into the conversational worklist payload.

    Selection only — no re-ranking, no re-scoring, no second formula. The
    array arrives ranked and lane-tagged; this takes the requested lane,
    the requested page, and carries the build's own warnings through.
    """
    lane = (query.lane if query is not None else None) or None
    if lane is not None and lane not in _LANES:
        lane = None
    ranked = [card for card in portfolio.items if lane is None or card.lane == lane]
    limit = routing.bounded_limit(query.limit if query is not None else None)
    published = ranked[:limit]
    recoverable = sum(card.recoverable_cents_estimate for card in ranked)
    return WorklistPayload(
        matched_on=matched_on,  # type: ignore[arg-type]
        matched_id=matched_id,
        statement=_statement(
            portfolio,
            published=published,
            total=len(ranked),
            recoverable_cents=recoverable,
            lane=lane,
        ),
        label=routing.label,
        description=routing.description,
        formula_version=portfolio.formula_version,
        watermark_id=portfolio.watermark_id,
        tenant=portfolio.tenant,
        items=published,
        lanes=list(portfolio.lanes),
        total_items=len(ranked),
        limit=limit,
        total_recoverable_cents_estimate=recoverable,
        # Verbatim: a worklist read into a conversation must not shed the
        # disclosures the rail shows beside the same cards.
        warnings=list(portfolio.warnings),
        warnings_v2=list(portfolio.warnings_v2),
    )
