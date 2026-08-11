"""Knowledge consultation — the pack's RCM judgement, as quotable context.

The constitution is exact about what this step is and is not. The pack's
RCM knowledge is "consulted, quotable as context, **never a source of
numbers**". A planner that has oriented on the data still has to decide
*what deserves checking*, and that decision is domain judgement: that a
climbing over-90 is usually one of denials, underpayments, or a slowdown in
posting; that Medicare Advantage denies differently from commercial; that a
filing deadline crossed is a different loss from a filing deadline missed.
None of that is in the warehouse and none of it should be invented by a
model that happens to have read the internet.

**Retrieval is governed and boring.** Alias, domain and title matching over
a few dozen authored cards. No embeddings, no similarity model, nothing that
routes a question somewhere nobody can audit — at this scale a vector index
would add a dependency, a training corpus and an unexplainable ranking to
solve a problem `str.find` solves. Every match records *what matched*, so
the trace can answer "why was this card in the planner's context".

**The wall between context and computation is enforced here, not asked
for.** :func:`as_prompt_context` renders summaries, key points and cautions
and refuses to render anything else; the planner's response schema cannot
carry a figure; and every number in the report comes from the deterministic
plane. A card saying "initial denial rates run near 12% nationally" can
therefore change *which angle runs* and can never change *what a number
says* — which is precisely the trade the addendum names.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from revi_investigation.application.capability_ports import KnowledgeEntry, PackPort

#: How many cards one consultation may put in front of a planner. Past this
#: the context stops being judgement and becomes a reading list, and the
#: thing being crowded out is the certified catalog.
MAX_CONSULTED = 6

#: How many key points travel per card. A card's own author put the most
#: load-bearing point first; the tail is elaboration a planner does not need
#: to choose an angle.
MAX_KEY_POINTS = 4

_WORD = re.compile(r"[a-z0-9]+")

#: Words that match everything and therefore select nothing. Kept short and
#: domain-neutral on purpose: an RCM stop-list that grew to include "denial"
#: would silently stop the denial cards from ever matching.
_STOPWORDS = frozenset(
    [
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by", "can", "could", "did",
    "do", "does", "for", "from", "get", "got", "had", "has", "have", "how", "i", "if", "in",
    "into", "is", "it", "its", "me", "my", "of", "on", "or", "our", "over", "should", "so", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "to",
    "under", "up", "us", "was", "we", "were", "what", "when", "where", "which", "who", "why",
    "will", "with", "would", "you", "your"
    ]
)


def _fold(word: str) -> str:
    """A word reduced to the form two spellings of it share.

    Plurals only, and only past three letters. "denials" and "denial" are
    one term to a reader and must be one term here; "aging" and "age" are
    not, and a stemmer aggressive enough to join them would also join
    "billing" to "bill" and start matching cards about charge capture to
    questions about bills.
    """
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(
        _fold(word) for word in _WORD.findall(text.lower()) if word not in _STOPWORDS
    )


def _normalize(text: str) -> str:
    return " ".join(_WORD.findall(text.lower().replace("_", " ")))


def _folded(text: str) -> str:
    return " ".join(_fold(word) for word in _WORD.findall(text.lower().replace("_", " ")))


@dataclass(frozen=True, slots=True)
class ConsultedEntry:
    """One knowledge card a run consulted, and why it was consulted."""

    id: str
    title: str
    summary: str
    key_points: tuple[str, ...]
    cautions: tuple[str, ...]
    review_status: str
    #: What matched — an alias, a domain, or the question's own words. On
    #: the record so "why was this in context" is answerable from the trace
    #: rather than by re-running the matcher.
    matched_on: tuple[str, ...] = field(default=())
    score: int = 0


@dataclass(frozen=True, slots=True)
class KnowledgeConsultation:
    """What the pack had to say about this question, before any angle ran."""

    question: str
    terms: tuple[str, ...]
    entries: tuple[ConsultedEntry, ...]
    #: The corpus this was drawn from — so a consultation that found nothing
    #: is distinguishable from a deployment with no knowledge at all.
    corpus_size: int
    statement: str

    @property
    def consulted(self) -> bool:
        return bool(self.entries)


def consult(
    pack: PackPort,
    *,
    question: str,
    concepts: Sequence[str] = (),
    metric_ids: Sequence[str] = (),
    limit: int = MAX_CONSULTED,
) -> KnowledgeConsultation:
    """Retrieve the pack knowledge this research question deserves.

    Scoring, in the order it is applied — highest wins, card id breaks ties
    so two runs of the same question consult the same cards in the same
    order:

    * an alias appearing verbatim in the question (10 per alias, weighted by
      how many words it is: "denial rate benchmark" matching is worth more
      than "denial" matching);
    * a named concept or metric matching an alias or domain (6);
    * a distinctive word shared with the card's title (2).

    A card that scores nothing is not consulted. Padding the context with
    the highest-scoring zeros would hand the planner six cards about
    anything and call it judgement.
    """
    corpus = pack.knowledge_entries()
    haystack = _folded(question)
    named = frozenset(
        term for term in (_folded(t) for t in (*concepts, *metric_ids)) if term
    )
    #: The named terms broken into words, so `ar_over_90_pct` reaches a card
    #: about aged A/R without anyone authoring an alias for a metric id.
    named_words = frozenset(word for term in named for word in _tokens(term))
    question_words = frozenset(_tokens(question)) | named_words

    scored: list[ConsultedEntry] = []
    for card in corpus:
        score = 0
        matched: list[str] = []
        alias_words: set[str] = set()
        for alias in _alias_terms(card):
            if not alias:
                continue
            alias_words.update(_tokens(alias))
            if _contains_phrase(haystack, alias):
                score += 10 * len(alias.split())
                matched.append(alias)
            elif alias in named:
                # Exact only for a NAMED term. Counting a concept as a match
                # because it is one word of an alias makes "denial" match
                # every card whose title mentions denials, which is most of
                # them — and a consultation returning the same six cards for
                # every question has consulted nothing.
                score += 6
                matched.append(alias)
        for domain in card.domains:
            flat = _folded(domain)
            if flat and (flat in named or _contains_phrase(haystack, flat)):
                score += 6
                matched.append(domain)
        title_overlap = question_words & frozenset(_tokens(card.title))
        if title_overlap:
            score += 3 * len(title_overlap)
            matched.extend(sorted(title_overlap))
        # Distinctive words the card's own aliases share with the question.
        # Capped, because a card with forty aliases would otherwise outrank
        # a card that is simply about the subject.
        alias_overlap = (question_words & alias_words) - title_overlap
        if alias_overlap:
            score += min(len(alias_overlap), 4)
            matched.extend(sorted(alias_overlap)[:4])
        if score <= 0:
            continue
        scored.append(
            ConsultedEntry(
                id=card.id,
                title=card.title,
                summary=card.summary,
                key_points=card.key_points[:MAX_KEY_POINTS],
                cautions=card.cautions,
                review_status=card.review_status,
                matched_on=tuple(dict.fromkeys(matched))[:6],
                score=score,
            )
        )
    scored.sort(key=lambda entry: (-entry.score, entry.id))
    kept = tuple(scored[:limit])
    return KnowledgeConsultation(
        question=question,
        terms=tuple(sorted(named)),
        entries=kept,
        corpus_size=len(corpus),
        statement=_statement(kept, len(corpus)),
    )


def _alias_terms(card: KnowledgeEntry) -> Iterable[str]:
    yield _normalize(card.title)
    for alias in card.aliases:
        yield _normalize(alias)


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Whole-word containment. ``cob`` must not match ``cobra``."""
    if not needle:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def _statement(entries: Sequence[ConsultedEntry], corpus_size: int) -> str:
    """One sentence naming what was consulted, for the preview and the trace.

    Never quotes a figure from a card. The whole point of the step is that
    what it contributes is *judgement about what to check*, and a sentence
    that led with an industry number would be the first place that line
    blurred.
    """
    if not entries:
        if corpus_size == 0:
            return "Your definitions library carries no background notes to consult here."
        return (
            f"None of the {corpus_size} background notes in your definitions library "
            "speak to this question, so this plan is built from your data alone."
        )
    titles = ", ".join(entry.title for entry in entries[:3])
    more = len(entries) - 3
    tail = f", and {more} more" if more > 0 else ""
    return (
        f"I read {len(entries)} background notes from your definitions library before "
        f"choosing what to check — {titles}{tail}. They shaped the questions, never "
        "the figures."
    )


def as_prompt_context(consultation: KnowledgeConsultation) -> str:
    """The consultation, rendered for a planner prompt. Prose only.

    Renders exactly three things per card — summary, key points, cautions —
    and nothing else. A renderer that could reach any further into the card
    would eventually reach something a model could mistake for a
    measurement, and the one rule this seam exists to keep is that it
    cannot.
    """
    if not consultation.entries:
        return (
            "No background notes in your definitions library speak to this question. "
            "Plan from the catalogue and the orientation findings alone."
        )
    blocks: list[str] = []
    for entry in consultation.entries:
        lines = [f"### {entry.title}", entry.summary]
        if entry.key_points:
            lines.append("What is known:")
            lines.extend(f"- {point}" for point in entry.key_points)
        if entry.cautions:
            lines.append("Cautions:")
            lines.extend(f"- {caution}" for caution in entry.cautions)
        lines.append(f"(provenance: {entry.review_status.replace('_', ' ')})")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
