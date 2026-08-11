"""Narrative composition prompt + the grounding validator (design §2.2:
the LLM may compose a narrative; it may not state conclusions unsupported
by recorded evidence).

``build_narrative_prompt`` renders the versioned template with ONLY
certified material: findings (referent ids, values, grades), the canonical
context header, reconciliation status, and pack benchmark context when
provided. ``build_narrative_facts`` derives the closed fact set the
validator trusts.

``validate_narrative`` checks the *final* text (the stream is provisional;
the validated text is authoritative):

- every number token must match a certified value — formatted variants are
  allowed ($99,093 · 99,093.08 · -12.7% · 12.7% · 152,196,731), matched by
  numeric equality against the fact set's value expansions (cents, whole
  dollars, dollars+cents, percents at 0-2 decimals);
- every claim-bearing sentence (one containing a number) must cite a
  referent id (F1, D3, ...);
- multi-word proper names must come from the closed vocabulary (payer and
  metric names the facts carry) — invented entities are rejected.

**A violating sentence is DROPPED, never marked inline.** A redaction is an
*internal* quality event: the reader should see the sentences that survived,
and the operator should see the count and the reasons. The text keeps only
what validated, the ``redactions`` list keeps every dropped sentence for the
trace, and exactly one aggregate note (count + distinct reasons) goes to the
warnings channel. Nothing is silently kept; nothing is loudly defaced.

The vocabulary is admitted at the same granularity the findings publish it,
so the validator does not redact its own certified content:

- **Entity sub-spans.** A candidate name is admitted when its tokens are a
  contiguous run inside some certified name — the part of a certified
  entity is certified. Otherwise ``"Summit Peak"`` matches nothing against
  a finding titled ``"Summit Peak Medicare Advantage: 12.4%"``.
- **Ordinary date phrases.** ``"For July"`` and ``"The July"`` are English,
  not entities. Leading/trailing grammar words are stripped before the
  check, and month and weekday names are date vocabulary.
- **Benchmark material.** Benchmark lines are rendered *into the prompt*,
  so the model quotes them; ``build_narrative_facts`` takes the same lines
  and admits their values and labels, or quoting them cuts the sentence.

The strictness that matters is unchanged: an uncited figure, a figure
matching no certified value, an unknown referent, or a genuinely invented
entity still fails.

**Caveats and display names bound what the prose may claim.** Grounding a
sentence in a certified number does not make the sentence honest — the
overclaim can live in the *characterization*, inherited from a metric id
that promises more than its formula delivers. Two inputs close that gap,
both rendered into the prompt as constraints rather than as background:

- ``caveats`` — the turn's mandatory population caveats, the same
  sentences the §6.6 validation pass publishes as warnings, shown under a
  heading that says they govern how the figures may be characterized;
- ``metric_display`` — governed display names for ids that overclaim, so
  the prompt reads "unbilled open inventory" where the id says "timely
  filing at risk dollars". Ids are reference anchors the pack cannot
  rename, so they are corrected where they are *read* rather than where
  they are referenced.

Both are admitted to the fact set as well as the prompt. Anything this
module instructs the model to write, it must be willing to validate — a
validator that redacted the correction it demanded would leave the
overclaiming sentence standing and drop the sentence that qualified it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from revi_investigation_contracts.api import FindingPayload
from revi_investigation_contracts.header import ContextHeaderPayload, basis_phrase
from revi_investigation_contracts.narrative import (
    NarrativeFacts,
    NarrativeRedaction,
    NarrativeValidation,
)
from revi_investigation_contracts.settings import NarrativeDepth

NARRATIVE_TEMPLATE_ID = "compose_narrative"
NARRATIVE_TEMPLATE_VERSION = "v1"

NARRATIVE_TEMPLATE = """# Compose the answer narrative

Write a short, plain-language narrative for a revenue-cycle analyst from
the measured findings below — and ONLY from them.

The question asked:

{question}

{shape_directive}

Rules:

- ANSWER THE QUESTION IN YOUR FIRST SENTENCE. Everything else follows it.
  A reader who stops after one sentence must have their answer.
- Cite the referent id (F1, F2, ...) in every sentence that makes a claim.
- Use only the numbers shown below, formatted naturally.
- Name only the entities that appear below; never introduce new ones.
- Say how a figure was arrived at whenever it was arrived at by anything
  other than measuring it directly, and say it in exactly one of these
  phrases: measured directly, calculated from measured values, estimated,
  exploratory, not measured.
- Never state a confidence for a finding — not as a number and not as a
  word. The reader is never asked to weigh a probability.
- Call each metric by the name it is given below. Never use a raw metric id:
  those names say what the number actually measures, and an id may promise
  more than its formula delivers.
- A benchmark range is not a free-standing sentence: state it in the same
  sentence as the finding it bears on, and cite that finding. A range on
  its own cites nothing, so it cannot be published — and dropping it
  strands whatever sentence referred back to it.
- Two short paragraphs at most. No headings, no bullet lists.
- The mandatory disclosures below are ALREADY published, verbatim, ahead of
  whatever you write. Do not repeat them, do not paraphrase them, and do
  not contradict them: if a disclosure says nothing moved the way the
  question asked, the movements in the findings are context and must be
  named as context. A disclosure restated in your own words is the same
  sentence printed twice, and the reader pays for it either way.

Mandatory disclosures (already published above your text):

{disclosures}

Effective context:

{header}

Certified findings:

{findings}

Mandatory caveats. These govern how the figures may be characterized —
do not claim more than they allow:

{caveats}

Reconciliation: {reconciliation}

Benchmark context:

{benchmarks}
"""

#: The analyst-depth twin of :data:`NARRATIVE_TEMPLATE`.
#:
#: Depth is a *composition parameter*, not a post-hoc trim: the two depths
#: render different templates, so the model is asked for different writing
#: and the trace records which template hash produced the text. Truncating
#: an analyst answer into a summary is how a narrative ends up citing a
#: finding whose caveat got cut.
#:
#: What the analyst depth adds is *coverage of what is already certified*:
#: every finding rather than the headline ones, the grade on each, the
#: reconciliation verdict, and the benchmark ranges with their cohorts.
#: It cannot add claims — the grounding validator below is identical for
#: both depths.
NARRATIVE_TEMPLATE_ANALYST = """# Compose the answer narrative (full analyst detail)

Write a thorough, plain-language analysis for a revenue-cycle analyst from
the measured findings below — and ONLY from them.

The question asked:

{question}

{shape_directive}

Rules:

- ANSWER THE QUESTION IN YOUR FIRST SENTENCE. Everything else follows it.
  A reader who stops after one sentence must have their answer.
- Cite the referent id (F1, F2, ...) in every sentence that makes a claim.
- Use only the numbers shown below, formatted naturally.
- Name only the entities that appear below; never introduce new ones.
- Cover EVERY measured finding, not only the largest.
- Say how EVERY finding was arrived at, including the ones measured
  directly, and say it in exactly one of these phrases: measured directly,
  calculated from measured values, estimated, exploratory, not measured.
- Never state a confidence for a finding — not as a number and not as a
  word. The reader is never asked to weigh a probability.
- Report the reconciliation status in its own sentence, in plain words.
- Call each metric by the name it is given below. Never use a raw metric id:
  those names say what the number actually measures, and an id may promise
  more than its formula delivers.
- Every mandatory caveat below bounds what its figure may be said to mean.
  Where a caveat applies, state the limit in the same breath as the
  number — an upper bound is not an exposure, and an inventory is not a
  diagnosis.
- Where a benchmark range is given, say how the figure sits against it —
  as a range, with its population, never as a pass/fail target — in the same
  sentence as the finding it bears on, citing that finding. A range stated
  on its own cites nothing, so it cannot be published, and dropping it
  strands whatever sentence referred back to it.
- Four short paragraphs at most. No headings, no bullet lists.
- The mandatory disclosures below are ALREADY published, verbatim, ahead of
  whatever you write. Do not repeat them, do not paraphrase them, and do
  not contradict them: if a disclosure says nothing moved the way the
  question asked, the movements in the findings are context and must be
  named as context; if one states a suppressed-cell count, no count you
  give may exclude those cells. A disclosure restated in your own words is
  the same sentence printed twice.

Mandatory disclosures (already published above your text):

{disclosures}

Effective context:

{header}

Certified findings:

{findings}

Mandatory caveats. These govern how the figures may be characterized —
do not claim more than they allow:

{caveats}

Reconciliation: {reconciliation}

Benchmark context:

{benchmarks}
"""

#: Depth → the template rendered for it. Keyed by the contract enum so the
#: wire value, the prompt and the recorded template hash cannot drift.
NARRATIVE_TEMPLATES: dict[NarrativeDepth, str] = {
    NarrativeDepth.SUMMARY: NARRATIVE_TEMPLATE,
    NarrativeDepth.ANALYST: NARRATIVE_TEMPLATE_ANALYST,
}

DETERMINATION_TEMPLATE_ID = "compose_research_determination"
DETERMINATION_TEMPLATE_VERSION = "v1"

#: The research determination — a THIRD template beside the two depths, and
#: the reason it is a template rather than a longer shape directive is
#: worth stating, because the other choice was available and was rejected.
#:
#: The M45 path takes findings, a header, caveats, disclosures and a
#: reconciliation verdict, and asks for prose in the shape the question's
#: classified answer-shape demands. A research determination needs three
#: things that path has no slot for and cannot be given one without
#: changing what every other answer is composed from:
#:
#:  * **the walk's reasons.** A study's argument is partly the order it was
#:    made in — "the payer spread was decisive, so I cut inside it" is why
#:    the third reading exists, and a determination written without it
#:    describes a pile of tables.
#:  * **the consulted background notes, as QUOTABLE CONTEXT.** They may
#:    inform the so-what framing and may never be a number. That is the
#:    whole trade ``docs/agentic-resolution.md`` names, and it is enforced
#:    rather than requested: none of these lines reaches the fact set's
#:    numeric values (:func:`build_determination_facts`), so a sentence
#:    lifting an industry figure out of one fails grounding and is dropped.
#:  * **a composite question.** "Why has it been climbing and what will it
#:    take to bring it down" is two questions in one sentence, and every
#:    directive in :data:`_SHAPE_DIRECTIVES` answers exactly one thing.
#:
#: What is NOT different is the validation. The same
#: :func:`validate_narrative` runs over the result, against a fact set
#: built by the same :func:`build_narrative_facts` — a second grounding
#: path would be a second place the one rule that matters could weaken.
DETERMINATION_TEMPLATE = """# Write the determination

You are a revenue-cycle consultant reporting back on a study you just ran.
Write the determination — the answer to the question below — from the
measured readings, and ONLY from them.

The question asked:

{question}

Rules:

- ANSWER THE QUESTION IN YOUR FIRST SENTENCE. If it asks two things — why
  something happened AND what to do about it — your first sentence answers
  the first, and a later sentence answers the second in as many words. A
  reader who stops after one sentence must have their answer.
- OPEN ON THE SUBJECT, never on a pronoun. Do not begin with This, That,
  These, Those, It or They: name the thing you are talking about. A
  sentence that opens on a demonstrative reads as a continuation of
  something, and there is nothing above it to continue from.
- EVERY PART OF THE QUESTION GETS AN ANSWER. A part you cannot answer from
  the readings below is said to be unanswered, in one sentence, naming what
  was missing. Silence on half a question reads as an answer to it.
- Cite the referent id (F1, F2, ...) in every sentence that makes a claim.
- Use only the numbers shown below, formatted naturally. Every figure you
  write is checked against a value an estimator produced.
- Name only the entities that appear below; never introduce new ones.
- Say how a figure was arrived at whenever it was arrived at by anything
  other than measuring it directly, and say it in exactly one of these
  phrases: measured directly, calculated from measured values, estimated,
  exploratory, not measured.
- Never state a confidence for a finding — not as a number and not as a
  word. The reader is never asked to weigh a probability.
- Call each measure by the name it is given below. Never use a raw metric
  id: those names say what the number actually measures.
- A figure marked as a ceiling is the most it could be, not what it is.
  Never call one the highest, the largest or the worst of anything.
- Four short paragraphs at most. No headings, no bullet lists.
- The mandatory disclosures below are ALREADY published, verbatim, ahead of
  whatever you write. Do not repeat them, do not paraphrase them, and do
  not contradict them.

How the study reached these readings. This is the run's own record of what
it decided and why. Use it to explain the shape of the answer — which
reading followed from which — and never as a source of a figure:

{walk}

Background notes this study read before choosing what to check. They are
CONTEXT: they may inform what the answer MEANS and what it implies for the
work, and they are never a measurement. Any figure in them belongs to
somebody else's population and may not be written down here:

{knowledge}

Mandatory disclosures (already published above your text):

{disclosures}

Effective context:

{header}

Certified readings:

{findings}

Mandatory caveats. These govern how the figures may be characterized —
do not claim more than they allow:

{caveats}
"""

_REFERENT_TOKEN = re.compile(r"\b[FD]\d+\b")
_NUMBER_TOKEN = re.compile(r"(?<![\w.])[$-]?\$?\d[\d,]*(?:\.\d+)?%?")
_PROPER_NAME = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Abbreviations whose full stop is not a sentence end. Every provider in
#: this warehouse is named "Dr. <name>", so without this the naive split
#: shredded provider prose: "…over a population of 11 entities (F1), Dr."
#: became one sentence and "Casey Quarry (143) …" the next, which then
#: failed grounding on its own and left the orphan behind.
#:
#: Kept deliberately short and closed: an over-eager list would glue two
#: real sentences together, which is the same defect pointed the other way.
_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "Dr", "Drs", "Mr", "Mrs", "Ms", "Mx", "Prof", "Rev", "Hon",
        "Sr", "Jr", "St", "Mt", "Ft",
        "Inc", "Ltd", "Co", "Corp", "Dept", "Univ", "Assn",
        "No", "Nos", "vs", "approx", "est", "cf", "al",
        "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept",
        "Oct", "Nov", "Dec",
    }
)

#: The tail of a fragment that is not a sentence end: a known abbreviation,
#: a single capital initial ("Casey Q. Quarry"), or a lettered enumeration
#: ("e.g.", "i.e.").
_ABBREVIATION_TAIL = re.compile(
    r"(?:^|[\s(\[\"'])(?:"
    + "|".join(sorted(_ABBREVIATIONS, key=len, reverse=True))
    + r"|[A-Z]|[a-z]\.[a-z])\.$"
)


#: A sentence terminator with no space after it, between two words —
#: "…has matured.prior month — the true value…". Such a seam is unreadable,
#: and the naive ``\\s+`` split cannot see a sentence boundary there, so the
#: dedupe below would have nothing to compare.
#:
#: Three word characters (or a closing bracket) of left context keep it off
#: "e.g."/"i.e." — the letter before the stop there has a dot in front of it,
#: not two more letters — and the letter lookahead keeps it off decimals,
#: money and dates. Anything it over-splits ("Inc." opening a name) the
#: abbreviation rejoin below puts straight back.
#:
#: This finds CANDIDATES. :data:`_NAME_INTERNAL_SUFFIXES` vetoes the ones
#: whose stop belongs to a name rather than to a sentence.
_UNSPACED_SEAM = re.compile(r"(\w{3,}|[)\]\"'])([.!?])(?=[A-Za-z])")

#: Tokens that make a full stop part of a NAME rather than a sentence end.
#: The mirror image of :data:`_ABBREVIATIONS`: that set keeps the split off
#: a stop whose LEFT side is not a sentence end, this one keeps it off a
#: stop whose RIGHT side is not a sentence start.
#:
#: "HealthCare.gov" is the live case. Benchmark cohort labels and sources
#: are certified vocabulary the composer is instructed to quote
#: (``ACA marketplace (HealthCare.gov issuers)``), and the unguarded seam
#: split one into "…sites." + "gov) issuer data…". The deduper then dropped
#: the head — a split head is by construction a PREFIX of the sentence it
#: came from, so the 120-character prefix rule below matched it against an
#: already-published disclosure and reported it as a word-for-word repeat —
#: and published the orphaned tail alone. One missing space cost a whole
#: sentence and left a fragment in its place.
#:
#: Kept closed and short for the reason the abbreviation set is: a wide
#: list would leave two real sentences welded together, which is this
#: defect pointed the other way. Every entry is a token that is *never* an
#: English word this corpus would open a sentence with — which is why
#: ``net``, ``co`` and ``us`` are deliberately absent ("…rose 4%.net of
#: contractual adjustments…" is a seam worth repairing, and "net" opening
#: a clause is ordinary revenue-cycle prose).
_NAME_INTERNAL_SUFFIXES: frozenset[str] = frozenset(
    {
        # Domain suffixes present in governed pack content (benchmark
        # sources and cohort labels): hfma.org, cms.gov, HealthCare.gov,
        # techtarget.com, kff.org, mgma.com, optum.com …
        "gov", "com", "org", "edu", "io",
        # File extensions the product names when it talks about exports.
        "csv", "json", "pdf", "xlsx",
    }
)

#: The word immediately following a candidate seam's stop.
_SEAM_FOLLOWER = re.compile(r"[A-Za-z]+")


def _repair_seam(match: re.Match[str]) -> str:
    """Insert the missing space, unless the stop is inside a name."""
    follower = _SEAM_FOLLOWER.match(match.string, match.end(2))
    if follower is not None and follower.group(0).casefold() in _NAME_INTERNAL_SUFFIXES:
        return match.group(0)
    return f"{match.group(1)}{match.group(2)} "


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences without shredding it at an abbreviation.

    The naive ``(?<=[.!?])\\s+`` split is applied first and its fragments
    are then re-joined wherever the left-hand side ends on something that
    is not a sentence terminator. Written as a rejoin rather than as one
    regex because Python's ``re`` requires fixed-width lookbehind, and the
    abbreviation set is not fixed width.

    Whitespace between rejoined fragments is normalised to a single space —
    the validator emits ``" ".join(kept)`` anyway, so no published text
    changes shape because of it. A terminator with NO whitespace after it
    is repaired the same way, for the same reason — except where the stop
    belongs to a name (:data:`_NAME_INTERNAL_SUFFIXES`), which is left
    exactly as the composer wrote it.
    """
    stripped = _UNSPACED_SEAM.sub(_repair_seam, text.strip())
    if not stripped:
        return []
    parts: list[str] = []
    for part in _SENTENCE_SPLIT.split(stripped):
        if parts and _ABBREVIATION_TAIL.search(parts[-1]):
            parts[-1] = f"{parts[-1]} {part}"
        else:
            parts.append(part)
    return parts


def ends_on_abbreviation(sentence: str) -> bool:
    """Does this sentence terminate on a known abbreviation?

    Exported so callers and tests can assert the invariant: no sentence
    this module emits may end on "Dr." — that is a fragment, not prose.
    """
    return bool(_ABBREVIATION_TAIL.search(sentence.strip()))


#: The two shapes a SPLICE leaves behind — a sentence dropped into a slot
#: that wanted a noun phrase.
#:
#: * an interior full stop whose continuation is not a new sentence: a
#:   lower-case word or an opening bracket ("…once the thinner side
#:   matures. (F2).");
#: * a copula whose complement opens a clause: "the largest movement **is
#:   Premise cannot** be verified".
#:
#: Both are checked against ISO-safe text: ``2026-07-01..2026-07-31`` has no
#: ". " in it, so the ranges the product prints are never candidates.
_INTERIOR_SENTENCE_BREAK = re.compile(r"[.!?]\s+(?=[a-z(\[])")
_SPLICED_CLAUSE = re.compile(
    r"\b(?:is|was|are|were)\s+[A-Z][a-z]+\s+(?:cannot|could not|did not)\b"
)
#: A "sentence" that is only the citation left behind when the text before
#: it ended in a full stop of its own: "(F2)." after "…matures."
_ORPHANED_CITATION = re.compile(r"^[(\[]")


def spliced_sentence(text: str) -> str | None:
    """The first sentence of ``text`` that reads as a splice, if any.

    A splice is what substituting for the OBJECT of a superlative clause
    produces when the replacement is itself a sentence: *"…the largest
    movement is Premise cannot be verified: You asked about an increase in
    denial rate. Ask again once the thinner side matures. (F2)."*

    Three shapes, all of them that sentence's:

    * a copula whose complement opens a clause ("**is Premise cannot**");
    * an interior full stop continued in lower case — a second sentence
      hiding inside one, after :func:`split_sentences` has had its say;
    * a fragment that is nothing but a citation, which is what a nested
      full stop strands ("(F2).").

    Exported so the composer (which refuses to publish a substitute that
    trips it) and the narrative-integrity suite (which asserts it over
    every emitted string) apply exactly one predicate.
    """
    for index, sentence in enumerate(split_sentences(text)):
        body = sentence.strip()
        if not body:
            continue
        if _SPLICED_CLAUSE.search(body) or _INTERIOR_SENTENCE_BREAK.search(body):
            return body
        if index and _ORPHANED_CITATION.match(body):
            return body
    return None


#: Month and weekday names. ``_PROPER_NAME`` cannot tell "July" from a payer,
#: so a run made only of these is a date phrase, not an entity claim.
_DATE_WORDS = frozenset(
    {
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December",
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday",
        "Q1", "Q2", "Q3", "Q4",
    }
)

#: Ordinary English words that only *look* like part of a proper name when
#: they open a sentence ("For July, ...", "The July spike ..."). Stripped
#: from either end of a candidate before it is checked against the closed
#: vocabulary — a real entity name neither begins nor ends with one.
_GRAMMAR_WORDS = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those",
        "and", "but", "or", "nor", "so", "yet", "as", "than", "then",
        "for", "in", "on", "at", "of", "to", "by", "with", "from", "into",
        "over", "under", "per", "since", "during", "through", "across",
        "within", "between", "before", "after", "against", "about",
        "versus", "vs", "both", "each", "all", "most", "some", "no", "not",
        "only", "also", "because", "although", "though", "while", "when",
        "if", "however", "meanwhile", "overall", "together", "here",
        "there", "its", "their", "our", "we", "it", "they", "he", "she",
        "was", "were", "is", "are", "be", "been", "has", "have", "had",
        "driven", "drove", "led", "remains", "remained", "stands",
    }
)

#: The legacy inline marker. It is no longer written into any narrative —
#: it is kept, exported, and named so that callers and tests can assert its
#: **absence** from customer-visible prose.
REDACTION_NOTE = "[redacted: a sentence here failed evidence validation]"

#: Prefix of the single aggregate warning emitted when sentences are
#: dropped. Existing guards match on this substring, so it stays stable.
REDACTION_WARNING_PREFIX = "narrative sentence redacted"


def template_hash(depth: NarrativeDepth = NarrativeDepth.SUMMARY) -> str:
    """The hash of the template a given depth renders.

    Depth-aware because the hash is what a trace uses to prove which
    prompt produced a narrative; one hash for two prompts would make that
    proof a guess.
    """
    return hashlib.sha256(NARRATIVE_TEMPLATES[depth].encode("utf-8")).hexdigest()


def determination_template_hash() -> str:
    """The hash of the determination template, for the same reason."""
    return hashlib.sha256(DETERMINATION_TEMPLATE.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# prompt + facts


def _display_substitutions(metric_display: Mapping[str, str] | None) -> list[tuple[re.Pattern[str], str]]:
    """Patterns rewriting a metric id — raw or humanized — to its display name.

    A metric id is a reference anchor the pack cannot rename, so an id that
    promises more than its formula delivers keeps its name everywhere it is
    *referenced* and is corrected everywhere it is *read*. Findings arrive
    carrying both spellings: ``timely_filing_at_risk_dollars`` in
    ``metric_ids`` and value names, and the humanized "timely filing at risk
    dollars" in titles and statements (the rendering layer swaps underscores
    for spaces). Both are rewritten, longest id first so a shorter id that is
    a prefix of a longer one cannot capture it.
    """
    if not metric_display:
        return []
    out: list[tuple[re.Pattern[str], str]] = []
    for metric_id in sorted(metric_display, key=len, reverse=True):
        display = metric_display[metric_id]
        for spelling in (metric_id, metric_id.replace("_", " ")):
            out.append((re.compile(rf"(?<!\w){re.escape(spelling)}(?!\w)", re.IGNORECASE), display))
    return out


def _apply_display_names(
    text: str, substitutions: Sequence[tuple[re.Pattern[str], str]]
) -> str:
    for pattern, display in substitutions:
        # A function replacement, so nothing in a display name is ever read
        # as a backreference (``\1``, ``\g<0>``) the way a string one would.
        text = pattern.sub(lambda _m, d=display: d, text)  # type: ignore[misc]
    return text


def apply_metric_display(text: str, metric_display: Mapping[str, str] | None) -> str:
    """Rewrite every metric id in ``text`` to its governed display name.

    Runs server-side, on the payload, before anything is published. A
    correction applied only by one client is not a correction: replay,
    export and any second client keep the mislabel — a finding title would
    still read ``timely filing at risk dollars`` over a number the
    platform's own caveat says is unbilled inventory.
    """
    return _apply_display_names(text, _display_substitutions(metric_display))


def _finding_line(
    finding: FindingPayload, substitutions: Sequence[tuple[re.Pattern[str], str]] = ()
) -> str:
    values = ", ".join(f"{v.name}={v.value}" for v in finding.values)
    line = (
        f"- {finding.referent}: {finding.title} (grade {finding.grade}, "
        f"confidence {finding.confidence}; {values})"
    )
    return _apply_display_names(line, substitutions)


def _empty_slot(published_cautions: int) -> str:
    """What an empty prompt slot says, so nothing reads it as "no caveats".

    The slot is empty when nothing on the MANDATORY list is present; the
    answer may still carry a dozen caution banners the composer was never
    shown. Saying "(none)" lets a model turn an absence in the PROMPT into
    a claim about the ANSWER — "No mandatory caveats were attached to these
    findings on this turn", published over seven of them.
    """
    if published_cautions <= 0:
        return "- (nothing on the mandatory list for this slot)"
    noun = "caveat" if published_cautions == 1 else "caveats"
    return (
        "- (nothing on the mandatory list for this slot — but this answer publishes "
        f"{published_cautions} {noun} above your text. Do NOT write that "
        "the answer carries no caveats, no qualifications or no caution: it does.)"
    )


#: What a prompt says when the caller passed no question. Never empty and
#: never invented: a composer shown a blank slot writes about the findings,
#: which is the behaviour this parameter exists to end.
_QUESTION_UNKNOWN = "(the question was not recorded for this turn)"

#: What a prompt says when no answer shape was determined. Deliberately
#: still an instruction — a turn whose interpretation named no shape is
#: still answering something, and "write a summary of the findings" is what
#: the composer did for as long as it was shown no question at all.
_SHAPE_UNKNOWN = (
    "Its shape was not classified, so answer it in whatever shape it asks for: a yes or "
    "no for a yes/no question, the entity's name for a which-X, the number for a "
    "how-much, the candidate causes for a why."
)


def question_directive(question: str | None, answer_shape: str | None) -> tuple[str, str]:
    """``(question line, shape directive)`` for the prompt's two new slots.

    The composer was never shown the utterance. It was handed findings, a
    header, a caveat list and a reconciliation verdict, so it wrote a
    *summary of findings* — fluent, grounded, and blind to whether the
    reader had asked a yes/no, a which-X, a how-much or a why. Six of six
    yes/no questions came back without a yes or a no; three of three
    how-much questions came back without the total.

    The directive is composed here rather than passed in as prose so that
    one closed set of shapes has one set of words, and an unclassified turn
    still gets an instruction rather than a blank.
    """
    asked = (question or "").strip() or _QUESTION_UNKNOWN
    directive = _SHAPE_DIRECTIVES.get(answer_shape or "", _SHAPE_UNKNOWN)
    return asked, directive


#: One directive per closed shape. Mirrors
#: ``revi_investigation.domain.context.AnswerShape`` — held as literal keys
#: because a presentation package may not import an investigation one; the
#: pair is pinned by a test on both sides.
_SHAPE_DIRECTIVES: dict[str, str] = {
    "verdict": (
        "It is a YES/NO question. If a sentence beginning Yes or No is already among the "
        "mandatory disclosures above, that IS the answer and it is already published — do "
        "not restate it in your own words; start from what it means and what to do about "
        "it. Otherwise your first sentence must open with Yes or No, then give the figure "
        "it rests on and the one entity that carries most of it. Either way, do not open "
        "with a caveat, a period, or a ranking: the caveats are published above your text "
        "and the reader has already read them."
    ),
    "entity": (
        "It asks WHICH ONE. Your first sentence must name the entity, then its figure. "
        "Do not open with the period, the population, or how the ranking was computed."
    ),
    "scalar": (
        "It asks HOW MUCH. Your first sentence must state the total — the whole number "
        "the question asked for, not a share of it. Concentration comes after."
    ),
    "cause": (
        "It asks WHY. Your first sentence must name what moved and by how much; the "
        "candidate causes follow, largest contributor first."
    ),
    "trend": (
        "It asks for a MOVEMENT OVER TIME. Your first sentence must give the direction "
        "and both endpoints. If the series does not exist in this data, say that first."
    ),
    "comparison": (
        "It asks for TWO SIDES. Your first sentence must give both figures and the "
        "difference between them."
    ),
    "definition": "It asks what a term MEANS. Say it in one sentence, first.",
    "worklist": (
        "It asks WHAT TO WORK FIRST. Your first sentence must name the first item, by "
        "name. Do not describe how the list was built."
    ),
}


def build_narrative_prompt(
    *,
    findings: Sequence[FindingPayload],
    header: ContextHeaderPayload,
    reconciliation: str | None,
    benchmarks: Sequence[str] = (),
    caveats: Sequence[str] = (),
    metric_display: Mapping[str, str] | None = None,
    depth: NarrativeDepth = NarrativeDepth.SUMMARY,
    disclosures: Sequence[str] = (),
    published_cautions: int = 0,
    question: str | None = None,
    answer_shape: str | None = None,
) -> str:
    """Render the composition prompt from certified material only.

    ``question`` is the utterance this answer answers, and ``answer_shape``
    is the closed shape its first sentence owes it. Both are instructions,
    not context: the composer is told to answer in that shape before it is
    shown anything to answer with.

    ``caveats`` are the turn's mandatory population caveats — the same
    sentences the §6.6 validation pass publishes as warnings. They are
    rendered as *constraints on characterization*, not as background: prose
    reproduces the overclaim a metric id makes whenever the caveat that
    corrects it is not in front of the model.

    ``metric_display`` maps metric ids to the governed display names that
    say what each number actually measures, so the prompt shows "unbilled
    open inventory" where the id says "timely filing at risk dollars".

    ``published_cautions`` is how many caution-severity warnings the ANSWER
    carries, which is not the same as how many are handed to this composer.
    An empty slot is a statement about this PROMPT and says so, because a
    model reads "(none)" as a fact about the answer.
    """
    substitutions = _display_substitutions(metric_display)
    finding_lines = "\n".join(_finding_line(f, substitutions) for f in findings) or "- (none)"
    benchmark_lines = "\n".join(f"- {line}" for line in benchmarks) or "- (none provided)"
    empty = _empty_slot(published_cautions)
    caveat_lines = (
        "\n".join(f"- {_apply_display_names(line, substitutions)}" for line in caveats) or empty
    )
    disclosure_lines = "\n".join(f"- {line}" for line in disclosures) or empty
    asked, directive = question_directive(question, answer_shape)
    return NARRATIVE_TEMPLATES[depth].format(
        question=asked,
        shape_directive=directive,
        header=header.display,
        findings=finding_lines,
        caveats=caveat_lines,
        disclosures=disclosure_lines,
        reconciliation=reconciliation or "not applicable on this answer",
        benchmarks=benchmark_lines,
    )


def build_determination_prompt(
    *,
    findings: Sequence[FindingPayload],
    header: ContextHeaderPayload,
    question: str,
    walk: Sequence[str] = (),
    knowledge: Sequence[str] = (),
    caveats: Sequence[str] = (),
    disclosures: Sequence[str] = (),
    metric_display: Mapping[str, str] | None = None,
    published_cautions: int = 0,
) -> str:
    """Render the determination prompt from certified material and context.

    Two slots are new and only one of them is certified. ``walk`` is the
    run's own record of what it decided and why — the platform's sentences,
    quoted back so the determination can explain the shape of its own
    answer. ``knowledge`` is the pack's RCM judgement, and it is context in
    the strict sense the addendum means: it may inform what the answer
    MEANS and it may never be a number. Neither slot's numbers reach the
    fact set, so the rule is kept by the validator rather than asked for.
    """
    substitutions = _display_substitutions(metric_display)
    finding_lines = "\n".join(_finding_line(f, substitutions) for f in findings) or "- (none)"
    empty = _empty_slot(published_cautions)
    caveat_lines = (
        "\n".join(f"- {_apply_display_names(line, substitutions)}" for line in caveats) or empty
    )
    disclosure_lines = "\n".join(f"- {line}" for line in disclosures) or empty
    walk_lines = (
        "\n".join(f"- {_apply_display_names(line, substitutions)}" for line in walk)
        or "- (this run recorded no decisions beyond its opening read)"
    )
    knowledge_lines = (
        "\n".join(f"- {line}" for line in knowledge)
        or "- (no background notes in your definitions library speak to this question)"
    )
    return DETERMINATION_TEMPLATE.format(
        question=(question or "").strip() or _QUESTION_UNKNOWN,
        walk=walk_lines,
        knowledge=knowledge_lines,
        disclosures=disclosure_lines,
        header=header.display,
        findings=finding_lines,
        caveats=caveat_lines,
    )


def build_determination_facts(
    *,
    findings: Sequence[FindingPayload],
    header: ContextHeaderPayload,
    extra_names: Sequence[str] = (),
    caveats: Sequence[str] = (),
    disclosures: Sequence[str] = (),
    knowledge: Sequence[str] = (),
    metric_display: Mapping[str, str] | None = None,
    published_cautions: int = 0,
    question: str | None = None,
) -> NarrativeFacts:
    """The fact set a determination is validated against.

    :func:`build_narrative_facts`, plus the ONE widening the background
    notes earn and no more: the proper names they contain are admitted, so
    a determination may say "Medicare Advantage plans behave differently
    here" without the sentence being cut for naming an entity no finding
    happened to mention. Their FIGURES are deliberately not admitted —
    that is the wall between context and computation, and this is the line
    of code that holds it. A determination quoting an industry benchmark
    fails grounding and the sentence is dropped, which is the intended
    behaviour: the notes shape what is checked, never what a number says.
    """
    facts = build_narrative_facts(
        findings=findings,
        header=header,
        extra_names=extra_names,
        caveats=caveats,
        metric_display=metric_display,
        disclosures=disclosures,
        published_cautions=published_cautions,
        question=question,
    )
    names = set(facts.allowed_names)
    for line in knowledge:
        for match in _PROPER_NAME.finditer(line):
            names.add(match.group(1))
    return facts.model_copy(update={"allowed_names": sorted(names)})


def build_narrative_facts(
    *,
    findings: Sequence[FindingPayload],
    header: ContextHeaderPayload,
    extra_names: Sequence[str] = (),
    benchmarks: Sequence[str] = (),
    caveats: Sequence[str] = (),
    metric_display: Mapping[str, str] | None = None,
    disclosures: Sequence[str] = (),
    worklist_first_action: str | None = None,
    published_cautions: int = 0,
    question: str | None = None,
) -> NarrativeFacts:
    """The closed fact set the validator trusts.

    ``benchmarks`` takes the same rendered lines that
    :func:`build_narrative_prompt` puts in front of the model. A range shown
    to the composer is certified material and is admitted as such; omitting
    it here cuts the very sentence the analyst template asks for, for
    citing a figure "matching no certified value".

    ``caveats`` and ``metric_display`` follow the same rule, and for a
    sharper reason: the prompt *instructs* the composer to state the caveat
    beside the number and to use the display name. Anything this module
    tells the model to write, it must also be willing to validate — a
    validator that redacts the correction it demanded would leave the
    overclaiming sentence standing and drop the sentence that qualified it.

    ``question`` closes the same loop for the sentence the prompt now
    DEMANDS. The composer is told to answer the question in its first
    sentence, and that sentence restates the analyst's own subject — so the
    capitalized runs of the utterance are admitted, exactly as a finding's
    are. It is a narrow widening and deliberately so: only the reader's own
    words about their own population, and nothing else about the sentence
    relaxes. Every figure in it must still match a certified value, every
    claim-bearing sentence must still cite a referent, and a population the
    question names but the data does not is stopped upstream — this turn
    never composes prose at all.
    """
    numbers: list[Decimal] = []
    names: set[str] = set()
    referents: list[str] = []
    for finding in findings:
        referents.append(finding.referent)
        for value in finding.values:
            if isinstance(value.value, bool) or value.value is None:
                continue
            if isinstance(value.value, (int, float)):
                numbers.append(Decimal(str(value.value)))
        for match in _PROPER_NAME.finditer(finding.title):
            names.add(match.group(1))
        for match in _PROPER_NAME.finditer(finding.statement):
            names.add(match.group(1))
    for line in (*benchmarks, *caveats, *disclosures, *(metric_display or {}).values()):
        for token in _NUMBER_TOKEN.findall(line):
            value = _token_value(token)
            if value is not None:
                numbers.append(value)
        for match in _PROPER_NAME.finditer(line):
            names.add(match.group(1))
    if header.cohort_size is not None:
        numbers.append(Decimal(header.cohort_size))
    # The turn's own resolved predicate values are certified vocabulary.
    # Without them a header reading ``filters: payer eq [Veritas Comp Fund]``
    # gets its narrative's sentences deleted for naming a value the answer
    # prints two rows above the redaction.
    for chip in header.filter_chips:
        names.update(chip.values)
        names.update(chip.requested_values)
    for finding in findings:
        for value in finding.values:
            if isinstance(value.value, str) and value.value:
                names.add(value.value)
    for match in _PROPER_NAME.finditer(question or ""):
        names.add(match.group(1))
    names.update(extra_names)
    dates = [
        header.window_start.isoformat(),
        header.window_end.isoformat(),
        *( [header.comparison_start.isoformat()] if header.comparison_start else [] ),
        *( [header.comparison_end.isoformat()] if header.comparison_end else [] ),
    ]
    return NarrativeFacts(
        referent_ids=referents,
        numeric_values=numbers,
        allowed_names=sorted(names),
        date_tokens=dates,
        # Every integer the mandatory disclosures state about the cell
        # population, so a sentence that counts cells has something to be
        # checked against instead of a free pass.
        population_counts=_population_counts(disclosures),
        # Read off the ANSWER's own census, not off what this composer was
        # handed: a turn carrying WINDOW_ASSUMED and nothing on the mandatory
        # list is still a cautioned turn, and the face-value ban below must
        # not be silently off for it.
        cautioned=bool(disclosures) or published_cautions > 0,
        published_cautions=published_cautions,
        truncated=any(_TRUNCATION_MARKER in line.lower() for line in disclosures),
        topic_sentence=_topic_sentence(findings, header),
        # What goes in the place of a superlative the guard removes, so the
        # question is answered rather than left with a hole where its answer
        # was.
        superlative_substitute=_superlative_substitute(findings, disclosures),
        # The ranked list's first item, when this question routed to the
        # worklist: no prose instruction may name a different one.
        worklist_first_action=worklist_first_action,
        # Size words the premise verdict already ruled out.
        forbidden_magnitude_claims=_forbidden_magnitude_claims(findings),
    )


#: The premise verdict as the findings publish it: the arm the movement
#: landed in, and the question's own verb for the size it asserted.
_PREMISE_MAGNITUDE_VALUE = "premise_magnitude"
_PREMISE_VERB_VALUE = "premise_asserted_verb"
_PREMISE_SHORT = "short"


def _forbidden_magnitude_claims(findings: Sequence[FindingPayload]) -> list[str]:
    """Size words this answer's own premise verdict has ruled out.

    Read off the certified finding rather than the prose around it: the
    verdict is data (``premise_magnitude: "short"``) precisely so that no
    downstream stage has to parse the sentence that states it.
    """
    out: list[str] = []
    for finding in findings:
        values = {value.name: value.value for value in finding.values}
        if str(values.get(_PREMISE_MAGNITUDE_VALUE, "")) != _PREMISE_SHORT:
            continue
        verb = values.get(_PREMISE_VERB_VALUE)
        if isinstance(verb, str) and verb.strip():
            out.append(verb.strip())
    return list(dict.fromkeys(out))


#: Recognises the FINDINGS_TRUNCATED disclosure without re-classifying it.
_TRUNCATION_MARKER = "published as findings"

#: A number immediately qualified by a population noun — "3 of 15 cells",
#: "12 payers", "296 entities". These are claims about how much was
#: measured, and they are checked against certified integers rather than
#: waved through as small numbers.
_POPULATION_CLAIM = re.compile(
    r"(\d[\d,]*)\s*(?:of\s+(\d[\d,]*)\s*)?"
    r"(?:small\s+)?(?:cells?|payers?|entities|rows?|providers?|facilities|plans?)",
    re.IGNORECASE,
)


def _population_counts(lines: Sequence[str]) -> list[int]:
    """Every population integer the certified disclosures state."""
    out: list[int] = []
    for line in lines:
        for token in re.findall(r"\d[\d,]*", line):
            try:
                out.append(int(token.replace(",", "")))
            except ValueError:  # pragma: no cover - the regex only matches digits
                continue
    return sorted(set(out))


#: The engine's own frame-level count, recognised so this module does not
#: publish a second one beside it. The phrase is
#: ``revi_investigation.application.execution.TOO_SMALL_TO_MEASURE``, held
#: as a literal because a presentation package may not import an
#: investigation one — the pair is pinned by a test on both sides.
_CENSUS_CLAUSE = "too small to measure exactly"

#: The engine's own census sentence, as it composes it: "4 of 12 groups
#: here are too small to measure exactly". Matched rather than recomputed,
#: for the same reason ``_CENSUS_CLAUSE`` is a literal: this package may not
#: import the engine, and two derivations of one census is the defect.
#: Both sides are pinned by tests.
_BOUNDED_CENSUS = re.compile(
    r"(\d[\d,]*)\s+of\s+(\d[\d,]*)\s+([A-Za-z ]+?)\s+(?:here\s+)?are\s+"
    + re.escape(_CENSUS_CLAUSE),
    re.IGNORECASE,
)
_FURTHER_WITHHELD = re.compile(r"A further (\d[\d,]*) could not be published", re.IGNORECASE)
#: One named ceiling out of the disclosure's list: "Northgate Choice
#: (denial rate ≤ 76.9% over 13 entities)".
_NAMED_CEILING = re.compile(r"([^;:()]+?)\s*\([^)]*?≤\s*([\d.]+)%[^)]*\)")
_LEADING_PERCENT = re.compile(r"([\d.]+)%")


def _int(token: str) -> int:
    return int(token.replace(",", ""))


def _superlative_substitute(
    findings: Sequence[FindingPayload], disclosures: Sequence[str]
) -> str | None:
    """The statement this answer CAN make about its leader.

    The superlative guard is right: a "worst" over a truncated or partly
    bounded list is a claim about rows the answer did not publish. Deleting
    the sentence is the wrong remedy — on a question that ASKED for the
    worst payer, it removes the only sentence that answers it. So the guard
    substitutes instead of deleting, and what it substitutes is the relation
    the evidence certifies: the leading finding is the highest figure this
    answer MEASURED, which is a different claim from "your worst" precisely
    because of the ceilings.

    Every figure comes from material already certified on this turn: the
    leading finding's own title, and the engine's own census sentence (see
    :data:`_BOUNDED_CENSUS`). Where the census cannot be read the sentence
    degrades to words rather than inventing arithmetic.

    A substitution that replaces the OBJECT of a superlative clause has to
    replace the whole clause. Dropping the leading finding's title into the
    noun slot splices when that title is the turn's premise VERDICT: *"…the
    largest movement is Premise cannot be verified: You asked about an
    increase in denial rate. Ask again once the thinner side matures.
    (F2)."* A verdict is never a row, so it never enters the noun slot;
    where the verdict is that nothing can be certified, the certifiable
    statement is emitted as its own complete sentence instead.
    """
    uncertified = _uncertified_premise(findings)
    if uncertified is not None:
        # The engine has said, in data, that it cannot certify the movement
        # this question assumes ("Nothing below may be called an increase
        # or offered as evidence against it"). Naming a largest anything
        # over those cells would be exactly the overclaim the guard fired
        # on — so the whole clause is replaced by what IS true.
        ranked = "movement" if _has_delta(uncertified) else "figure"
        return (
            f"The largest {ranked} cannot be named: the premise itself is unverified "
            f"({uncertified.referent})."
        )
    lead = _measured_leader(findings)
    if lead is None:
        # Every published row is a ceiling. There is no measured leader to
        # name, so there is no substitute to make and the deletion stands:
        # inventing one would be the overclaim the guard exists to stop.
        return None
    bounded = total = withheld = None
    noun = "groups"
    for line in disclosures:
        match = _BOUNDED_CENSUS.search(line)
        if match is not None:
            bounded, total = _int(match.group(1)), _int(match.group(2))
            # The engine says "groups" in one disclosure and "payers" in
            # the next; the reader's own word wins wherever it is offered.
            candidate = match.group(3).strip().lower()
            if candidate and (noun == "groups" or candidate != "groups"):
                noun = candidate
        further = _FURTHER_WITHHELD.search(line)
        if further is not None:
            withheld = _int(further.group(1))
    # What the ranking is OVER, read off the leader's own values: a turn
    # that compared ranks movements, and calling a movement "the highest
    # figure" would be a second superlative in place of the one just
    # redacted.
    ranked = "movement" if _has_delta(lead) else "figure"
    if bounded is not None and total is not None:
        measurable = max(total - bounded - (withheld or 0), 0)
        opening = (
            f"Of the {measurable} {noun} measurable this window, the largest {ranked} is "
            f"{lead.title} ({lead.referent})."
        )
        qualifier = f"I can't call it your worst outright: {bounded} publish only a ceiling"
    else:
        opening = f"The largest {ranked} this answer measured is {lead.title} ({lead.referent})."
        qualifier = (
            "I can't call it your worst outright: some of these groups publish only a ceiling"
        )
    above = _ceiling_above(lead, disclosures)
    tail = (
        f", and {above} sits above it"
        if above
        else ", and a ceiling can sit above a measured figure without being a larger number"
    )
    substitute = f"{opening} {qualifier}{tail}."
    # Last line of defence: whatever went into the noun slot, the sentence
    # that comes out has to read as one. A substitute that splices is not
    # published at all — the deletion the guard would otherwise have made is
    # a worse answer but never a broken one.
    if spliced_sentence(substitute) is not None:  # pragma: no cover - guarded above
        return None
    return substitute


#: The value name a finding carries when its figure is a CEILING rather than
#: a measurement (``revi_investigation.application.findings._bound_values``).
#: A bounded row cannot be called the highest anything: its true value is
#: unknown, which is the whole reason the guard fired.
_BOUND_SUFFIX = "__is_bound"
_DELTA_SUFFIX = "__delta"

#: The premise verdict as DATA, published by
#: ``revi_investigation.application.findings._build_premise_finding``. A
#: finding carrying it is the turn's verdict on the question's own
#: assumption, not a row of the population — held as literals here for the
#: same reason :data:`_CENSUS_CLAUSE` is, and pinned on both sides by a test.
_PREMISE_HOLDS_VALUE = "premise_holds"
_PREMISE_UNVERIFIABLE_VALUE = "premise_unverifiable"

#: The value name marking a finding that is the WHOLE the other findings
#: are parts of (``revi_investigation.application.findings.bounds.
#: AGGREGATE_VALUE``). A total is not a row, so it never enters the noun
#: slot of "the largest X is ___": a substitute reading "the largest figure
#: this answer measured is Total: $22,426,000.28" names the sum of the very
#: cells it was asked to rank. Held as a literal for the same reason
#: :data:`_CENSUS_CLAUSE` is, and pinned on both sides by a test.
_AGGREGATE_VALUE = "aggregate_total"


def _is_premise_verdict(finding: FindingPayload) -> bool:
    """Is this finding a VERDICT about the question rather than a row?

    Its title is a sentence — *"Premise cannot be verified: You asked about
    an increase in denial rate. Ask again once the thinner side matures."* —
    and a sentence cannot stand where a row label stands.
    """
    return any(value.name == _PREMISE_HOLDS_VALUE for value in finding.values)


def _uncertified_premise(findings: Sequence[FindingPayload]) -> FindingPayload | None:
    """The premise verdict that says this turn could certify nothing.

    ``premise_unverifiable: true`` is the engine's own statement that the
    movement under the question was not measurable — two ceilings, an
    immature panel, a size nobody parsed. Every cell published beneath such
    a verdict is a composition of a movement the answer cannot certify, so
    there is no largest anything to name.
    """
    for finding in findings:
        for value in finding.values:
            if value.name == _PREMISE_UNVERIFIABLE_VALUE and bool(value.value):
                return finding
    return None


def _measured_leader(findings: Sequence[FindingPayload]) -> FindingPayload | None:
    """The first published finding whose figure is a MEASUREMENT.

    A ceiling can rank first by its own delta — *"Veritas Comp Fund denial
    rate at most ≤ 76.9%"* over 13 entities — and naming it as the highest
    measured figure would replace a redacted superlative with a false one.

    A premise verdict is skipped for a second reason: it is not a row at
    all, and its title is a two-sentence judgement that cannot be dropped
    into "the largest movement is ___".
    """
    for finding in findings:
        if _is_premise_verdict(finding) or _is_aggregate(finding):
            continue
        if not any(
            value.name.endswith(_BOUND_SUFFIX) and bool(value.value)
            for value in finding.values
        ):
            return finding
    return None


def _is_aggregate(finding: FindingPayload) -> bool:
    """Is this finding the WHOLE rather than one of its parts?"""
    return any(
        value.name == _AGGREGATE_VALUE and bool(value.value) for value in finding.values
    )


def _has_delta(finding: FindingPayload) -> bool:
    return any(value.name.endswith(_DELTA_SUFFIX) for value in finding.values)


def _ceiling_above(lead: FindingPayload, disclosures: Sequence[str]) -> str | None:
    """The named ceiling that could exceed the leader, if the census has one.

    "Veritas Comp Fund's (≤76.9%) sits above it" is the half of the honesty
    a reader can act on: it names the row that would overturn the ranking if
    its population were big enough to measure.
    """
    top = _LEADING_PERCENT.search(lead.title)
    if top is None:
        return None
    try:
        level = Decimal(top.group(1))
    except InvalidOperation:  # pragma: no cover - the regex yields digits only
        return None
    best: tuple[Decimal, str] | None = None
    for line in disclosures:
        if _CENSUS_CLAUSE not in line:
            continue
        for match in _NAMED_CEILING.finditer(line):
            try:
                bound = Decimal(match.group(2))
            except InvalidOperation:  # pragma: no cover - digits only
                continue
            name = match.group(1).strip(" —-,;")
            if bound > level and (best is None or bound > best[0]):
                best = (bound, name)
    if best is None:
        return None
    return f"{best[1]}'s (≤{best[0]}%)"


def _topic_sentence(
    findings: Sequence[FindingPayload], header: ContextHeaderPayload
) -> str | None:
    """What this answer is about, said deterministically.

    Prepended when grounding validation removes the narrative's opening and
    the survivor starts with a pronoun — "That bound rests on direct
    evidence and is carried at high confidence (F10)" as an answer's first
    words never tells the reader what "that bound" was. Composed from the
    header and the leading finding, both certified.
    """
    if not findings:
        return None
    scope = "; ".join(header.filters) if header.filters else None
    period = (
        f"as of {header.as_of.isoformat()}"
        if header.as_of is not None
        else f"{header.window_start.isoformat()}..{header.window_end.isoformat()}"
    )
    lead = findings[0]
    where = f" for {scope}" if scope else ""
    # The bare token ("remit") is a modeling word; ``basis_phrase`` is the
    # one client rendering of it, shared with the context header, and falls
    # back to the raw id for a basis it has no phrase for.
    basis = basis_phrase(header.basis)
    return (
        f"This answer covers {period}, measured {basis}{where}. "
        f"{lead.title} ({lead.referent})."
    )



# ---------------------------------------------------------------------------
# mandatory disclosures


#: Codes that must be said BEFORE anything else. A refusal — "nothing moved
#: the way the question asked about; what follows is context, not an
#: answer" — cannot sit beneath two paragraphs about the movement that was
#: found instead. Kept to one family: leading with everything is the same as
#: leading with nothing.
#:
#: ``PREMISE_FALSE`` joins it because it is the same family one level up.
#: "Why did denials double?" answered with the three cells that rose, inside
#: an 81% fall nobody mentioned, is not a caveated answer — it is a
#: confirmation of something that did not happen. The correction leads or it
#: does not work.
LEAD_DISCLOSURE_CODES: tuple[str, ...] = (
    # The worklist, when the worklist IS the answer: "what should my denial
    # team work first" routes to a ranked list whose own statement says it
    # is not a measurement of the question above it. It leads, because the
    # question asked for it.
    "WORKLIST_LEADS",
    "PREMISE_FALSE",
    # A movement in the asserted direction that fell short of the asserted
    # SIZE leads for the same reason a refutation does: everything below it
    # is the composition of a movement the question named wrongly.
    "PREMISE_PARTIAL",
    # A premise over two suppressed ceilings, an immature comparison panel,
    # or a size nothing could parse is neither confirmed nor refuted, and it
    # governs whether anything below may be read as evidence for the
    # question's own assumption.
    "PREMISE_UNVERIFIABLE",
    # The other half of the premise family. A verdict that CONFIRMS the
    # question is still the answer's first claim: publishing it only on
    # failure lets a real +4.2% open on a 243% sub-cell while the measured
    # aggregate is discarded.
    "PREMISE_VERIFIED",
    # The yes or the no. A premise verdict answers a movement the question
    # ASSERTED; this answers a question that asserted nothing and asked for
    # a judgement — "do we owe refunds?", "do I have a COB problem?", "are
    # any payers paying below contract?". Six of six such questions were
    # answered without a yes or a no, and four of them opened on the
    # settling caveat instead.
    "VERDICT_LEAD",
    # …and the gap where the subject should have been. A frame the question
    # is ABOUT that published nothing is a lead-class fact: "A/R over 90 is
    # a standing balance in this data — there is no monthly series for it"
    # is the answer to "show me A/R over 90 by month", and it arrived as
    # that answer's last sentence.
    "SUBJECT_UNPUBLISHED",
    # A ranking the platform declined to publish, because too much of its
    # population carries ceilings rather than measurements. A refusal cannot
    # sit under the rows it refused to order.
    "RANKING_REFUSED",
    "DIRECTION_UNMATCHED",
    "EMPTY_RESULT",
    # A window that has not finished adjudicating. Trailing this makes
    # "what is my denial rate?" answer with a provisional figure in its
    # opening clause and the caveat that governs it two paragraphs down.
    # The caveat is not a footnote on the number; it decides whether the
    # number may be read as the level at all, which is the same job the
    # premise verdicts above do. Where the engine could measure the last
    # SETTLED period, this sentence carries that figure — so the answer
    # leads with what has settled and names the provisional one as
    # provisional.
    "ADJUDICATION_INCOMPLETE",
    # A stored clarification answer that was APPLIED rather than asked
    # about. Where the engine legitimately applies one — a binding it
    # derived itself from governed content, with genuinely one answer
    # available — the reader is being handed the answer to a slightly
    # different question, and that sentence cannot live only in
    # ``warnings_v2`` where a client's caution fold can hide it.
    "CLARIFICATION_ANSWER_APPLIED",
)

#: Codes that must be said, after the prose, in this order. These bound how
#: the figures may be read rather than whether they answer the question.
TRAILING_DISCLOSURE_CODES: tuple[str, ...] = (
    "SUPPRESSION_APPLIED",
    # Which cells carry an upper bound instead of a measurement. A ranking
    # that silently mixes the two is as misleading as one that drops the
    # bounded rows, so the bound is said, not merely available.
    "SUPPRESSION_BOUNDED",
    # …and which of them could not be ordered at all.
    "BOUNDED_CELLS_UNRANKED",
    # …and the same fact where the CONTRACT declares it rather than a panel
    # count revealing it. A delta the governing contract forbids — two
    # windows of unequal maturity are not comparable as levels — is not a
    # caveat on a result; it is the reason there is no result.
    "NOT_COMPARABLE_WINDOWS",
    # The whole this answer is a part of, restated on the part. A breakdown
    # that never says what it broke down leaves a reader who landed on it
    # believing denial rates run 19-29% when the population they descend
    # from is at 12.8%.
    "PARENT_LEVEL",
    # The cells a DIRECTION removed — the omission that flatters the
    # question that asked for it.
    "DIRECTION_OMITTED",
    # The window that was actually read, when it is not the window that was
    # asked for, and the period vocabulary that was resolved or could not be.
    "WINDOW_OUT_OF_RANGE",
    "WINDOW_HORIZON",
    "WINDOW_RELATIVE",
    "RECONCILIATION_FAILED",
    "SNAPSHOT_AS_OF",
)

#: Codes whose sentence is an OPERATOR fact, not a reader fact. They stay on
#: ``warnings_v2`` — where a client folds them and an operator can read them
#: in full — and are no longer appended to published prose.
#:
#: ``PROBE_FAMILIES_EMPTY`` spelled internal probe node ids and row counts
#: into the answer ("denial_rate (portfolio_denial_trend, 1 row(s))"); it
#: appeared on 18 of 26 prose answers in the live corpus.
#: ``FINDINGS_TRUNCATED`` published the cell census as arithmetic ("3 of 144
#: computed cells are published as findings"), on 16 of 26.
#:
#: The honesty they carry is not lost and is not weakened: both codes still
#: ride on ``warnings_v2`` with their full message, the client renders them
#: as caution banners, and the *behaviour* they gate is untouched — a
#: truncated answer still sets :attr:`NarrativeFacts.truncated`, so the
#: superlative and spread rules in :func:`validate_narrative` fire exactly
#: as before and the substitute sentence still replaces a redacted
#: superlative.
OPERATOR_ONLY_DISCLOSURE_CODES: tuple[str, ...] = (
    "PROBE_FAMILIES_EMPTY",
    "FINDINGS_TRUNCATED",
)

#: Every code the narrative may not leave unsaid.
MANDATORY_DISCLOSURE_CODES: tuple[str, ...] = (
    *LEAD_DISCLOSURE_CODES,
    *TRAILING_DISCLOSURE_CODES,
)

#: Codes whose sentence is composed here rather than lifted from the
#: engine's prose, because the engine's sentence omits a number the reader
#: needs (the suppressed-cell count) or does not exist as prose at all (the
#: card/answer reconciliation).
_COMPOSED_CODES = frozenset({"SUPPRESSION_APPLIED", "RECONCILIATION_FAILED"})

#: Every code whose message carries that count. It used to be checked on
#: SUPPRESSION_BOUNDED alone, so a ranking that refused itself — and stated
#: the count in doing so — still got a second, differently-derived sentence
#: composed beneath it.
_CENSUS_CODES = ("SUPPRESSION_BOUNDED", "RANKING_REFUSED", "BOUNDED_CELLS_UNRANKED")


def _sentence(text: str) -> str:
    body = text.strip()
    if not body:
        return ""
    body = body[0].upper() + body[1:]
    return body if body.endswith((".", "!", "?")) else body + "."


def _strip_code_prefix(message: str) -> str:
    head, sep, tail = message.partition(": ")
    if sep and " " not in head and head.lower() == head:
        return tail
    return message


#: The classification the API's warning table publishes for a sentence no
#: rule of its recognizes.
_UNCLASSIFIED = "UNCLASSIFIED"


def recovered_code(code: str, message: str) -> str:
    """The code to disclose under, when the classifier had no rule for it.

    Classification lives in one place — the API's warning table — and this
    does not move it. It covers exactly one gap, in one direction: a
    *mandatory* disclosure that arrives as ``UNCLASSIFIED`` because a new
    engine warning family shipped before the table learned its name would
    otherwise be silently demoted out of the lead, which is the one failure
    mandatory disclosures exist to prevent. The engine's own prefix
    convention (``lowercase_token: sentence``) is what identifies it, the
    recovery only ever fires for codes already listed as mandatory, and the
    table wins wherever it has an opinion.
    """
    if code != _UNCLASSIFIED:
        return code
    head, sep, _ = message.partition(": ")
    if not sep or " " in head or head.lower() != head:
        return code
    candidate = head.upper()
    return candidate if candidate in MANDATORY_DISCLOSURE_CODES else code


def reconciliation_disclosure(
    *,
    status: str,
    card_cents: int | None,
    answer_cents: int | None,
    delta_cents: int | None,
    delta_fraction: float | None,
) -> str:
    """The sentence a card-to-drill reconciliation owes the reader.

    Composed from the two figures the strip published, because the lineage
    verdict is a different statement: a strip reading ``diverged;
    card=$178,216.82; answer=$195,873.92; +9.9%`` above prose saying
    "reconciliation was not performed on this turn" leaves the two figures
    the reader just compared unmentioned.
    """
    card = f"${card_cents / 100:,.2f}" if card_cents is not None else "no figure"
    answer = f"${answer_cents / 100:,.2f}" if answer_cents is not None else "no figure"
    if answer_cents is None:
        return _sentence(
            f"The detection card this drill was opened from published {card}; this answer "
            "produces no comparable dollar figure, so the two are not reconciled here"
        )
    gap = ""
    if delta_cents is not None:
        pct = f" ({delta_fraction:+.1%})" if delta_fraction is not None else ""
        gap = f", a difference of ${delta_cents / 100:,.2f}{pct}"
    if status == "agreed":
        return _sentence(
            f"This answer reconciles against the detection card it was opened from: the card "
            f"published {card} and this answer publishes {answer}{gap}"
        )
    if status == "not_comparable":
        return _sentence(
            f"The detection card published {card} and this answer publishes {answer}{gap}, but "
            "the two are not comparable — this answer measures a balance as it stood on one "
            "date and the card's figure was accumulated over a window, so the gap is a "
            "difference in what each figure measures rather than a disagreement"
        )
    return _sentence(
        f"The detection card this drill was opened from published {card} and this answer "
        f"publishes {answer}{gap}: the card's window, population or valuation is not this "
        "answer's, and both figures stand as what each system measured"
    )


#: The settling caveat. It is the one lead disclosure whose message is five
#: sentences long, and it led 12 of 26 live answers — including snapshot
#: questions ("do we owe refunds right now?") where the answer's own body
#: then said the framing does not bear on a standing balance.
SETTLING_CODE = "ADJUDICATION_INCOMPLETE"

#: Answer shapes the settling caveat may still LEAD for. It bears on a level
#: and on a movement — both are read off the window, and an unsettled window
#: understates a total and skews a rate. It does not decide which entity is
#: largest, what a term means, or what to work first.
_SETTLING_LEADS_FOR: frozenset[str] = frozenset(
    {"scalar", "trend", "comparison", "cause"}
)

#: Trail codes the envelope budget may drop from PROSE. They bound how the
#: figures are READ — which window, which as-of framing, which parent level.
#: Everything not listed here bounds the EVIDENCE — a suppression, a
#: ceiling, an unranked block, a non-comparability, an omission, a failed
#: reconciliation — and is never budgeted out: an answer may be long, and it
#: may not be quietly less honest.
#:
#: Dropped sentences are not lost. Every one of them rides on
#: ``warnings_v2`` with its full message, where the client renders it as a
#: caution banner.
_BUDGETABLE_TRAIL_CODES: frozenset[str] = frozenset(
    {"PARENT_LEVEL", "WINDOW_OUT_OF_RANGE", "WINDOW_HORIZON", "WINDOW_RELATIVE",
     "SNAPSHOT_AS_OF", SETTLING_CODE}
)

#: How many reading-caveat sentences may follow the prose. Three, because a
#: reader who has to scroll past the bounds to reach the caveats reads
#: neither.
MAX_BUDGETED_TRAIL_SENTENCES = 3


def _first_sentence(text: str) -> str:
    """The opening sentence of a composed disclosure.

    Used for exactly one code (:data:`SETTLING_CODE`), whose message is
    written so that its first sentence carries the period, the share and
    what an unsettled window does to a total and to a rate. Applying it
    anywhere else would cut a premise verdict in half.
    """
    parts = split_sentences(text)
    return parts[0].strip() if parts else text.strip()


def operator_only_disclosures(
    classified_warnings: Sequence[tuple[str, str]],
) -> list[str]:
    """Sentences that are TRUE, published, and not for the reader's prose.

    :data:`OPERATOR_ONLY_DISCLOSURE_CODES`, in their own order. They are
    still handed to :func:`build_narrative_facts` — the truncation fact
    gates the superlative and spread rules, and dropping it from the fact
    set would turn a prose cleanup into a silent loosening of grounding —
    and they are still on ``warnings_v2`` in full. What changes is only that
    they are no longer appended to the answer a person reads.
    """
    by_code: dict[str, str] = {}
    for code, message in classified_warnings:
        by_code.setdefault(recovered_code(code, message), message)
    return [
        _sentence(_strip_code_prefix(by_code[code]))
        for code in OPERATOR_ONLY_DISCLOSURE_CODES
        if code in by_code
    ]


def mandatory_disclosures(
    classified_warnings: Sequence[tuple[str, str]],
    *,
    reconciliation_sentence: str | None = None,
    suppressed_cells: int = 0,
    total_cells: int = 0,
    metric_display: Mapping[str, str] | None = None,
    settling_bears_on_headline: bool = True,
    answer_shape: str | None = None,
) -> tuple[list[str], list[str]]:
    """The sentences this answer may not be published without.

    A composer that is merely *shown* a structured fact will contradict it:
    prose has opened "three payers show denial rates rising" under a
    correctly-fired ``DIRECTION_UNMATCHED``, and written "three payers were
    measured" under a ``SUPPRESSION_APPLIED`` covering four censored cells.
    In each case the structured fact was ON THE SAME RESPONSE the prose
    ignored.

    These sentences are therefore composed HERE, from the payload, and
    prepended to the validated prose rather than requested from the model:
    a mandatory disclosure that a composer may decline to write, or that
    grounding validation may drop, is not mandatory. They carry no figure
    that is not already certified on the answer — the suppressed-cell count
    off the frame, the two impact figures off the reconciliation strip —
    which is why they are exempt from the grounding pass that guards
    *generated* prose.

    ``classified_warnings`` is ``(code, message)`` in the order the engine
    emitted them; classification stays in one place (the API's warning
    table) rather than being re-derived from substrings here.

    ``settling_bears_on_headline`` is ``False`` when this answer's headline
    figure is a standing balance read at the data date. An unsettled window
    does not shrink a balance, and the settling caveat led twelve live
    answers whose own body then said so — so on those turns it trails
    instead of leading. It is never removed: honesty relocates.

    ``answer_shape`` is the closed shape the question's first sentence owes
    (``verdict``, ``scalar``, …). The settling caveat leads only for a level
    or a movement; on a which-entity or a worklist question it bounds the
    reading and belongs under it.

    ``metric_display`` rewrites every raw metric id these sentences carry.
    They are composed from ENGINE prose, which bypassed the display overlay
    the model's own text goes through — which is how ``'ar_over_90_pct'``
    and ``cob mismatch claims`` reached 21 of 26 published answers under a
    template that forbids a raw id.

    Returns ``(lead, trail)``: sentences that must precede the composed
    prose and sentences that must follow it. Only a refusal leads —
    everything leading is the same as nothing leading.
    """
    by_code: dict[str, str] = {}
    for code, message in classified_warnings:
        by_code.setdefault(recovered_code(code, message), message)

    def stated(codes: Sequence[str]) -> list[str]:
        out: list[str] = []
        for code in codes:
            message = by_code.get(code)
            if message is None or code in _COMPOSED_CODES:
                continue
            out.append(_sentence(_strip_code_prefix(message)))
        return out

    lead: list[str] = []
    demoted: list[str] = []
    for code in LEAD_DISCLOSURE_CODES:
        message = by_code.get(code)
        if message is None or code in _COMPOSED_CODES:
            continue
        sentence = _sentence(_strip_code_prefix(message))
        if code != SETTLING_CODE:
            lead.append(sentence)
            continue
        # One sentence, and only where it decides how the headline figure
        # may be read. Everything after its first sentence is on
        # ``warnings_v2`` in full, which is where the client's caution fold
        # renders it — the reader loses nothing and stops paying five
        # sentences to reach their own answer.
        short = _first_sentence(sentence)
        leads = (
            settling_bears_on_headline
            and not lead
            and (answer_shape is None or answer_shape in _SETTLING_LEADS_FOR)
        )
        (lead if leads else demoted).append(short)
    trail: list[str] = []
    # The engine counts its own cells and publishes the arithmetic on the
    # SUPPRESSION_BOUNDED disclosure. When it has, the count derived here
    # from ``suppressed_cells`` — which counts nulled VALUES, several per
    # row — is a second, different population for one control, and two
    # arithmetics in one paragraph is the defect.
    engine_counted = any(
        _CENSUS_CLAUSE in by_code.get(code, "") for code in _CENSUS_CODES
    )
    if "SUPPRESSION_APPLIED" in by_code and suppressed_cells > 0 and not engine_counted:
        scope = f" of {total_cells}" if total_cells else ""
        noun = "cell" if suppressed_cells == 1 else "cells"
        # Two policies, two readings. Where every withheld cell was dropped,
        # the surviving figures describe only what survived. Where some were
        # BOUNDED instead, the cell is still here and its figure is a
        # ceiling — saying "only the cells that survived" over a published
        # bound would be the old censorship story told about a fixed one.
        rest = (
            "some of them are published as upper bounds rather than dropped, so a figure "
            "marked as a bound is a ceiling and not a measurement"
            if "SUPPRESSION_BOUNDED" in by_code
            else "so every figure and count here describes only the cells that survived it "
            "— never the whole population"
        )
        trail.append(
            _sentence(
                f"Small-cell suppression withheld the true value of {suppressed_cells}{scope} "
                f"{noun} on this answer, {rest}"
            )
        )
    failed = by_code.get("RECONCILIATION_FAILED")
    if failed is not None:
        _, _, detail = failed.partition("RECONCILIATION_FAILED: ")
        # The branch handle never reaches published prose. When the prefix
        # is spelled any other way the partition yields nothing, and the
        # old fallback spliced the whole message — code and all — in front
        # of the reader. The code still rides on ``warnings_v2[].code``.
        detail = detail or failed.replace("RECONCILIATION_FAILED", "").lstrip(" :;—-")
        tail = f" — {detail}" if detail else ""
        trail.append(
            _sentence(
                "This answer does not reconcile against the answer it was drilled from, and "
                f"both figures stand published until it does{tail}"
            )
        )
    trail.extend(stated(TRAILING_DISCLOSURE_CODES))
    trail.extend(demoted)
    if reconciliation_sentence:
        trail.append(_sentence(reconciliation_sentence))
    trail = _budgeted(trail, by_code)
    substitutions = _display_substitutions(metric_display)
    return (
        [_apply_display_names(line, substitutions) for line in lead],
        [_apply_display_names(line, substitutions) for line in trail],
    )


def _budgeted(trail: Sequence[str], by_code: Mapping[str, str]) -> list[str]:
    """Cap the READING caveats in a trail; never the evidence-bounding ones.

    The live corpus averaged 308 words an answer with the model writing
    ~150 of them: the rest was this envelope. A budget is the fix, and a
    budget that can delete a bound is not — so it is applied only to the
    codes in :data:`_BUDGETABLE_TRAIL_CODES`, which say which window and
    which framing, and never to the ones that say what was withheld,
    bounded, unranked, omitted or unreconciled.

    Membership is decided by the sentence the code composed, matched
    against the messages this turn actually emitted — the same map the
    trail was built from, so no sentence can be mistaken for another
    code's.
    """
    budgetable: set[str] = set()
    for code in _BUDGETABLE_TRAIL_CODES:
        message = by_code.get(code)
        if message is not None:
            budgetable.add(_first_sentence(_sentence(_strip_code_prefix(message))))
            budgetable.add(_sentence(_strip_code_prefix(message)))
    kept: list[str] = []
    spent = 0
    for line in trail:
        if line in budgetable:
            if spent >= MAX_BUDGETED_TRAIL_SENTENCES:
                continue
            spent += 1
        kept.append(line)
    return kept


def empty_narrative(classified_warnings: Sequence[tuple[str, str]]) -> str | None:
    """Prose for a turn that published no finding.

    ``EMPTY_RESULT`` with ``narrative: null`` on the wire renders as "No
    findings for this question" over a population where a value exists. A
    null narrative is not an absence of prose; it is an absence of the
    explanation the reader most needs, on exactly the turn that most needs
    it. The cause is already structured on the response, so it is stated.
    """
    sentences = [
        _sentence(_strip_code_prefix(message))
        for code, message in classified_warnings
        if recovered_code(code, message)
        in ("PREMISE_FALSE", "EMPTY_RESULT", "DIRECTION_UNMATCHED", "PROBE_FAMILIES_EMPTY")
    ]
    if not sentences:
        return None
    return " ".join(
        ["This answer published no finding, and here is why.", *dict.fromkeys(sentences)]
    )


# ---------------------------------------------------------------------------
# validation


def _expansions(value: Decimal) -> set[Decimal]:
    """All formatted magnitudes a certified value may legitimately take:
    the raw value; cents → dollars (rounded and exact); ratios → percents
    at 0-2 decimals. Sign-insensitive (the token match handles sign)."""
    v = abs(value)
    out = {v}
    # cents → dollars
    dollars = v / 100
    out.add(dollars)
    out.add(dollars.quantize(Decimal("1")))
    out.add(dollars.quantize(Decimal("0.01")))
    # ratio → percent
    pct = v * 100
    for places in ("1", "0.1", "0.01"):
        try:
            out.add(pct.quantize(Decimal(places)))
            out.add(v.quantize(Decimal(places)))
        except InvalidOperation:  # pragma: no cover - enormous values
            pass
    return out


def _token_value(token: str) -> Decimal | None:
    cleaned = token.strip().lstrip("$-").rstrip("%").replace(",", "").lstrip("$")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _number_allowed(token: str, allowed: set[Decimal], date_tokens: set[str]) -> bool:
    raw = token.strip().lstrip("$-").lstrip("$")
    if _DATE_LIKE.match(raw):
        return True
    value = _token_value(token)
    if value is None:
        return False
    if abs(value) <= Decimal(2100) and value == value.to_integral_value():
        # small integers read as counts/years/ordinals, not financial claims
        return True
    return abs(value) in allowed


def _strip_grammar(tokens: list[str]) -> list[str]:
    """Drop ordinary English words from either end of a capitalized run.

    ``_PROPER_NAME`` matches any run of capitalized words, so a sentence
    that opens "For July, denials rose" hands it ``"For July"``. The
    grammar is not part of anybody's name.
    """
    start, end = 0, len(tokens)
    while start < end and tokens[start].lower() in _GRAMMAR_WORDS:
        start += 1
    while end > start and tokens[end - 1].lower() in _GRAMMAR_WORDS:
        end -= 1
    return tokens[start:end]


def _is_contiguous_run(candidate: list[str], within: list[str]) -> bool:
    n = len(candidate)
    if n == 0 or n > len(within):
        return False
    return any(within[i : i + n] == candidate for i in range(len(within) - n + 1))


def _name_admitted(name: str, known_token_sequences: list[list[str]]) -> bool:
    """Is this capitalized run certified material?

    Admitted when, after grammar is stripped, it is empty, is pure date
    vocabulary, or is a contiguous run of tokens inside a certified name —
    the last of which is what lets a narrative say "Summit Peak" about a
    finding titled "Summit Peak Medicare Advantage: 12.4%".
    """
    core = _strip_grammar(name.split())
    if not core:
        return True
    if all(token in _DATE_WORDS for token in core):
        return True
    return any(_is_contiguous_run(core, known) for known in known_token_sequences)


#: Sentences that assert the answer needs no allowance. A caution and a
#: face-value claim cannot both be published: prose has said a magnitude
#: "can be taken at face value … without an allowance for derivation error"
#: on a turn whose own warning said the load reached only part of the
#: requested window.
_FACE_VALUE = re.compile(
    r"\b(?:at face value|taken at face value|without (?:an? )?(?:allowance|caveat|qualification)"
    r"|no allowance for)\b",
    re.IGNORECASE,
)

#: Sentences asserting the ANSWER carries no caveats. A composer reads the
#: emptiness of its own prompt slots as a fact about the answer — "No
#: mandatory caveats were attached to these findings on this turn", written
#: on turns that render caution banners from the same ``warnings_v2``
#: array. The affirmation is derived from the warning census instead (see
#: ``published_cautions``), and a sentence that makes it anyway is redacted
#: rather than argued with.
_NO_CAVEATS = re.compile(
    r"\b(?:no|not any|zero|none of the)\b[^.?!]{0,60}?"
    r"\b(?:caveat|caveats|qualification|qualifications|disclosure|disclosures|"
    r"caution|cautions|warning|warnings|reservation|reservations)\b"
    r"|\b(?:caveat|caveats|qualification|qualifications|disclosure|disclosures|"
    r"caution|cautions|warning|warnings)\b[^.?!]{0,40}?"
    r"\b(?:were|was|are|is)\s+(?:not|none)\b",
    re.IGNORECASE,
)

#: Claims about the SHAPE of a population — its spread, its band, "the
#: measured group", "all of them". On a truncated answer these describe the
#: served slice and nothing else, which is how a 4.4% to 15.0% spread (3.4x)
#: gets narrated as "roughly three percentage points … a tight band". Never
#: certified over a slice, whatever it cites.
_SPREAD_CLAIM = re.compile(
    r"\b(?:tight|narrow|wide|broad)\s+(?:band|range|spread)"
    r"|\bthe\s+(?:measured|published|shown)\s+group\b"
    r"|\bspread\s+(?:of|is|was|runs)\b"
    r"|\ball\s+(?:of\s+them|payers|providers|facilities|plans)\b"
    r"|\b(?:every|each)\s+(?:payer|provider|facility|plan)\b",
    re.IGNORECASE,
)

#: Superlatives. The ordering IS computed over the full population, so the
#: leading finding may be called the largest — that relation is certified.
#: A superlative in a sentence that does NOT cite the leading finding is a
#: claim about rows the answer did not publish: "highest of the measured
#: group at 7.5%" over a served slice whose true maximum was 15.0%.
_SUPERLATIVE = re.compile(
    r"\b(?:largest|biggest|highest|lowest|worst|best|smallest|most|least)\b",
    re.IGNORECASE,
)

#: Opening words that need an antecedent the previous sentence supplied.
_STRANDED_OPENING = re.compile(
    r"^(?:that|those|this|these|it|they|them|its|their|he|she|both|such)\b",
    re.IGNORECASE,
)


def _population_claim_allowed(sentence: str, certified: set[int]) -> str | None:
    """Reason a population count in this sentence is not certified, if any."""
    for match in _POPULATION_CLAIM.finditer(sentence):
        for group in match.groups():
            if group is None:
                continue
            try:
                value = int(group.replace(",", ""))
            except ValueError:  # pragma: no cover - regex yields digits only
                continue
            if value not in certified:
                noun = "cell or entity" if value == 1 else "cells or entities"
                return (
                    f"counts {value} {noun}, which no measured suppression figure "
                    "on this answer states"
                )
    return None


#: How a sentence proposes what to do first. Deliberately narrow: it fires
#: on an INSTRUCTION about ordering ("start with", "the first action is",
#: "prioritise", "work X first"), never on a sentence that merely describes
#: which figure is largest — the findings are allowed to say that, and the
#: worklist is allowed to rank differently, because they rank different
#: things over different populations.
_FIRST_ACTION = re.compile(
    r"\b("
    r"start(?:ing)? with|begin(?:ning)? with|first (?:action|step|priority|move)|"
    r"prioriti[sz]e|work(?:ing)? (?:on )?[^.;]{0,40}?first|focus (?:first )?on|"
    r"next step|immediate (?:action|priority)|should (?:be )?(?:tackle|address|work)"
    r")\b",
    re.IGNORECASE,
)


#: Wording that turns a size word into a report of what did NOT happen.
#: A sentence quoting the verdict ("denials did not double") is the
#: sentence this rule wants; only the affirmative claim is dropped.
_MAGNITUDE_NEGATION = re.compile(
    r"\b(?:did not|didn'?t|does not|doesn'?t|was not|wasn'?t|were not|weren'?t|never|not|"
    r"short of|fell short|falls short|far from|rather than|instead of|without|no)\b",
    re.IGNORECASE,
)


def _magnitude_claim(sentence: str, verbs: Sequence[str]) -> str | None:
    """Reason this sentence asserts a size the verdict already refused.

    A narrative may report the verdict; it may not restate the claim the
    verdict declined. Otherwise a premise finding reading "It did not
    double — denial rate rose 72.6%, short of the 100.0% a doubling
    assumes" is followed two sentences later by "denials roughly doubled"
    over the same figure.
    """
    for verb in verbs:
        stem = re.escape(verb)
        pattern = re.compile(rf"\b{stem}(?:d|s|ed|ing)?\b", re.IGNORECASE)
        if not pattern.search(sentence):
            continue
        if _MAGNITUDE_NEGATION.search(sentence):
            continue
        return (
            f"asserts a {verb!r} on an answer whose premise verdict states the movement fell "
            "short of it"
        )
    return None


def _first_action_conflict(sentence: str, first_action: str) -> str | None:
    """Does this sentence recommend a first action other than rank 1?

    The worklist and the narrative are two orderings on one card, and when
    the question was "what should we work first" only one of them was asked
    for. A prose instruction naming a different first thing is not a second
    opinion — the reader has no way to tell which the platform means.

    The rule is one-directional: a sentence may recommend rank 1 by name,
    or recommend nothing, but it may not recommend instead of it.
    """
    if not _FIRST_ACTION.search(sentence):
        return None
    if first_action.casefold() in sentence.casefold():
        return None
    return (
        "recommends a first action without naming the ranked worklist's first item "
        f"({first_action}), which is the answer this question routed to"
    )


#: How much repeated prose is a repetition rather than a coincidence. Two
#: sentences may legitimately share a clause ("over 2026-07-01..2026-07-31
#: on the service basis"); 120 consecutive characters is a paragraph saying
#: itself twice.
DOUBLED_SPAN_CHARS = 120

#: The reason a sentence is dropped for repeating one already published.
DUPLICATE_SENTENCE_REASON = (
    "repeats a sentence this narrative has already published, word for word"
)


def _normalized(text: str) -> str:
    """Casefolded, whitespace-collapsed text, for comparing prose to prose."""
    return " ".join(text.split()).casefold()


def doubled_span(text: str, minimum: int = DOUBLED_SPAN_CHARS) -> str | None:
    """The longest run of ``minimum`` characters this text contains twice.

    The deduper below works on sentences, which is the right unit but not
    the guarantee that matters: what a reader sees is a string, and the
    invariant the answer owes them is that no paragraph of it appears
    twice. This is that invariant, checkable from outside, on the final
    bytes. ``None`` — the only acceptable answer for published prose — when
    nothing that long repeats.
    """
    flat = _normalized(text)
    for start in range(0, len(flat) - minimum + 1):
        window = flat[start : start + minimum]
        if flat.find(window, start + 1) != -1:
            return window
    return None


def dedupe_sentences(text: str) -> tuple[str, list[str]]:
    """``(prose, dropped)`` with every repeated sentence removed.

    Any note ABOUT repetition may only be composed from the EMITTED text —
    which is what returning the dropped sentences alongside the prose is
    for. Composing it from the match set instead lets an answer print a
    note saying those sentences "are not printed twice" directly above the
    undeduplicated string; the failure is intermittent, so it survives a
    clean second run.

    A sentence is dropped when its normalized form has already been kept,
    or when :data:`DOUBLED_SPAN_CHARS` characters of it already appear in
    what has been kept: the second rule catches a caution restated with a
    comma moved, which byte equality does not.
    """
    kept: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    running = ""
    for sentence in split_sentences(text):
        stripped = sentence.strip()
        if not stripped:
            continue
        flat = _normalized(stripped)
        repeated = flat in seen or (
            len(flat) >= DOUBLED_SPAN_CHARS and flat[:DOUBLED_SPAN_CHARS] in running
        )
        if repeated:
            dropped.append(stripped)
            continue
        seen.add(flat)
        kept.append(stripped)
        running = f"{running} {flat}".strip()
    return " ".join(kept), dropped


def compose_narrative(
    lead: Sequence[str], body: str, trail: Sequence[str]
) -> tuple[str, list[str]]:
    """Join a refusal, the composer's prose and the bounding caveats.

    ``(narrative, repeated)`` — the second half being every sentence the
    join would otherwise have printed twice, for whatever note the caller
    composes ABOUT the emitted text. A note derived from anything else
    describes a string that never shipped.

    The mandatory disclosures are put in front of the model as constraints,
    so a conscientious composer restates them — and then they are published
    again, around it. Deduplicating the assembled string is what makes the
    refusal lead and the caveats bound without either being said twice.
    """
    joined, repeated = dedupe_sentences(" ".join([*lead, body.strip(), *trail]).strip())
    return joined, repeated


def validate_narrative(text: str, facts: NarrativeFacts) -> NarrativeValidation:
    """Sentence-level grounding check.

    Sentences that fail are **dropped from the prose** and reported through
    ``redactions`` (full text + reason, for the trace) and one aggregate
    ``warnings`` entry (count + distinct reasons, for the operator). The
    returned ``text`` contains only sentences that validated, so a customer
    never reads a redaction marker — see the module docstring.

    A sentence the composer printed twice is dropped the same way and
    recorded with its own reason. It is not a grounding failure — the
    repeated sentence is usually a mandatory caution the composer was shown
    and dutifully copied — but publishing it twice is still publishing
    something the answer does not mean to say.
    """
    allowed: set[Decimal] = set()
    for value in facts.numeric_values:
        allowed.update(_expansions(Decimal(value)))
    referent_ids = set(facts.referent_ids)
    known_token_sequences = [name.split() for name in facts.allowed_names]
    date_tokens = set(facts.date_tokens)
    # A population count may be quoted from a mandatory disclosure or from
    # a certified finding value; anything else is a count the composer
    # derived, and deriving the censorship arithmetic is what produces
    # "3 of 15 cells" over a 12-cell answer with nothing withheld.
    leading_referent = facts.referent_ids[0] if facts.referent_ids else ""
    certified_counts: set[int] = set(facts.population_counts)
    for value in facts.numeric_values:
        decimal_value = Decimal(value)
        if decimal_value == decimal_value.to_integral_value():
            certified_counts.add(int(decimal_value))

    kept: list[str] = []
    redactions: list[NarrativeRedaction] = []
    #: The substitute goes in ONCE, where the first redacted superlative
    #: stood: a composer that reaches for "worst" three times gets one
    #: certifiable statement in its place, not three.
    substitute_owed = False

    for sentence in split_sentences(text):
        if not sentence.strip():
            continue
        reason: str | None = None
        substituted = False
        numbers = [
            tok for tok in _NUMBER_TOKEN.findall(sentence) if _token_value(tok) is not None
        ]
        cited = set(_REFERENT_TOKEN.findall(sentence))
        unknown_citations = cited - referent_ids
        if unknown_citations:
            unknown = sorted(unknown_citations)
            label = "referent" if len(unknown) == 1 else "referents"
            reason = f"cites unknown {label} {', '.join(unknown)}"
        if reason is None and numbers:
            if not cited:
                reason = "states figures without citing a referent"
            else:
                for token in numbers:
                    if not _number_allowed(token, allowed, date_tokens):
                        reason = f"figure {token!r} matches no measured value"
                        break
        if reason is None:
            for match in _PROPER_NAME.finditer(sentence):
                name = match.group(1)
                if (numbers or cited) and not _name_admitted(name, known_token_sequences):
                    reason = f"names {name!r}, which is not among the names this answer measured"
                    break
        if reason is None and facts.cautioned and _FACE_VALUE.search(sentence):
            reason = (
                "claims the figures can be taken at face value on an answer that publishes "
                "a caution"
            )
        if reason is None and facts.published_cautions and _NO_CAVEATS.search(sentence):
            noun = "caveat" if facts.published_cautions == 1 else "caveats"
            reason = (
                "states the answer carries no caveats while this answer publishes "
                f"{facts.published_cautions} {noun}"
            )
        if reason is None and facts.truncated:
            if _SPREAD_CLAIM.search(sentence):
                reason = (
                    "describes the spread or extent of a population over a truncated finding "
                    "list — the shape of the full population was not published"
                )
            elif _SUPERLATIVE.search(sentence) and leading_referent not in cited:
                reason = (
                    "states a superlative over a truncated finding list without citing the "
                    f"leading finding ({leading_referent or 'none'}) — the relation is measured "
                    "only over the full population"
                )
                substituted = True
        if reason is None:
            claim = _population_claim_allowed(sentence, certified_counts)
            if claim is not None:
                reason = claim
        if reason is None and facts.forbidden_magnitude_claims:
            claimed = _magnitude_claim(sentence, facts.forbidden_magnitude_claims)
            if claimed is not None:
                reason = claimed
        if reason is None and facts.worklist_first_action:
            first = _first_action_conflict(sentence, facts.worklist_first_action)
            if first is not None:
                reason = first
        if reason is None:
            kept.append(sentence.strip())
        else:
            redactions.append(NarrativeRedaction(sentence=sentence.strip(), reason=reason))
            if substituted and facts.superlative_substitute and not substitute_owed:
                # Never a silent hole where the answer was: the certifiable
                # statement goes in, in the redacted sentence's own place,
                # and says why it is not the superlative.
                substitute_owed = True
                kept.append(facts.superlative_substitute)

    # An analysis whose opening pronoun lost its antecedent is not an
    # analysis. When redaction took the first sentence and the survivor
    # opens with a demonstrative, the deterministic topic sentence goes in
    # front of it rather than the reader being left to guess what "that
    # bound" was.
    if (
        redactions
        and kept
        and facts.topic_sentence
        and _STRANDED_OPENING.match(kept[0])
        and text.strip()
        and split_sentences(text)[0].strip() != kept[0]
    ):
        kept.insert(0, facts.topic_sentence)

    # …and nothing survives here twice. Last, so that a sentence dropped
    # for grounding is reported as a grounding failure and only a genuine
    # repetition is reported as one.
    emitted, repeats = dedupe_sentences(" ".join(kept))
    redactions.extend(
        NarrativeRedaction(sentence=sentence, reason=DUPLICATE_SENTENCE_REASON)
        for sentence in repeats
    )

    warnings: list[str] = []
    if redactions:
        # One note, not one per sentence: this is an operator signal about
        # the turn, and N copies of it in the answer's warnings array read
        # as N separate problems.
        reasons = list(dict.fromkeys(r.reason for r in redactions))
        count = len(redactions)
        subject = "sentence was" if count == 1 else "sentences were"
        warnings.append(
            f"{REDACTION_WARNING_PREFIX}: {count} {subject} removed "
            f"from the summary ({'; '.join(reasons)})"
        )

    return NarrativeValidation(text=emitted, redactions=redactions, warnings=warnings)
