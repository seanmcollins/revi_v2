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

**A violating sentence is DROPPED, never marked inline.** The validator
used to splice ``REDACTION_NOTE`` into the prose where a sentence failed,
which put ``[redacted: a sentence here failed evidence validation]`` in
front of paying customers — as often as five times in one answer, and in
the worst case as the answer's opening words. A redaction is an *internal*
quality event: the analyst should see the sentences that survived, and the
operator should see the count and the reasons. So the text keeps only what
validated, the ``redactions`` list keeps every dropped sentence for the
trace, and exactly one aggregate note (count + distinct reasons) goes to
the warnings channel. Nothing is silently kept; nothing is loudly defaced.

The vocabulary is admitted at the same granularity the findings publish it,
which is what stopped the *other* half of that defect — the validator
redacting its own certified content:

- **Entity sub-spans.** Finding F2's title is
  ``"Summit Peak Medicare Advantage: 12.4%"``, so the closed vocabulary
  held the whole four-word string and the narrative's perfectly correct
  ``"Summit Peak"`` matched nothing and was cut. A candidate name is
  admitted when its tokens are a contiguous run inside some certified
  name — the part of a certified entity is certified.
- **Ordinary date phrases.** ``"For July"`` and ``"The July"`` are English,
  not entities: the proper-name regex simply caught a sentence-initial
  function word next to a month. Leading/trailing grammar words are
  stripped before the check, and month and weekday names are date
  vocabulary rather than entities.
- **Benchmark material.** The benchmark lines are rendered *into the
  prompt*, so the model quotes them — and they were absent from the fact
  set, so quoting them got the sentence cut. ``build_narrative_facts``
  now takes the same lines and admits their values and labels.

The strictness that matters is unchanged: an uncited figure, a figure
matching no certified value, an unknown referent, or a genuinely invented
entity still fails.

**Caveats and display names bound what the prose may claim.** Grounding a
sentence in a certified number does not make the sentence honest: the
composer wrote *"the largest timely filing exposure sits with State
Medicaid HMO at $2,349,692.17"* on an answer whose own mandatory
population caveat said the metric applies no deadline predicate and is an
unbilled-inventory upper bound. Every figure in that sentence validated.
The overclaim was in the *characterization*, inherited from a metric id
that promises filing exposure over a number that measures inventory.

Two inputs close that gap, both rendered into the prompt as constraints
rather than as background:

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
from revi_investigation_contracts.header import ContextHeaderPayload
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
the certified findings below — and ONLY from them.

Rules:

- Cite the referent id (F1, F2, ...) in every sentence that makes a claim.
- Use only the numbers shown below, formatted naturally.
- Name only the entities that appear below; never introduce new ones.
- State the evidence grade plainly when it is weaker than direct.
- Call each metric by the name it is given below. Never use a raw metric id:
  those names say what the number actually measures, and an id may promise
  more than its formula delivers.
- A benchmark range is not a free-standing sentence: state it in the same
  sentence as the finding it bears on, and cite that finding. A range on
  its own cites nothing, so it cannot be published — and dropping it
  strands whatever sentence referred back to it.
- Two short paragraphs at most. No headings, no bullet lists.
- The mandatory disclosures below are ALREADY published, verbatim, ahead of
  whatever you write. Do not repeat them and do not contradict them: if a
  disclosure says nothing moved the way the question asked, the movements
  in the findings are context and must be named as context.

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

Benchmark context (governed):

{benchmarks}
"""

#: The analyst-depth twin of :data:`NARRATIVE_TEMPLATE`.
#:
#: Depth is a *composition parameter*, not a post-hoc trim: the two depths
#: render different templates, so the model is asked for different writing
#: and the trace records which template hash produced the text. A summary
#: is not a truncated analyst answer and an analyst answer is not a padded
#: summary — truncating one into the other is exactly how a narrative ends
#: up citing a finding whose caveat got cut.
#:
#: What the analyst depth adds is *coverage of what is already certified*:
#: every finding rather than the headline ones, the grade on each, the
#: reconciliation verdict, and the benchmark ranges with their cohorts.
#: It cannot add claims — the grounding validator below is identical for
#: both depths, and a sentence that outruns the evidence is redacted at
#: either depth.
NARRATIVE_TEMPLATE_ANALYST = """# Compose the answer narrative (full analyst detail)

Write a thorough, plain-language analysis for a revenue-cycle analyst from
the certified findings below — and ONLY from them.

Rules:

- Cite the referent id (F1, F2, ...) in every sentence that makes a claim.
- Use only the numbers shown below, formatted naturally.
- Name only the entities that appear below; never introduce new ones.
- Cover EVERY certified finding, not only the largest.
- State each finding's evidence grade and confidence explicitly, including
  when the grade is direct.
- Report the reconciliation status in its own sentence, in plain words.
- Call each metric by the name it is given below. Never use a raw metric id:
  those names say what the number actually measures, and an id may promise
  more than its formula delivers.
- Every mandatory caveat below bounds what its figure may be said to mean.
  Where a caveat applies, state the limit in the same breath as the
  number — an upper bound is not an exposure, and an inventory is not a
  diagnosis.
- Where a benchmark range is given, say how the figure sits against it —
  as a range, with its cohort, never as a pass/fail target — in the same
  sentence as the finding it bears on, citing that finding. A range stated
  on its own cites nothing, so it cannot be published, and dropping it
  strands whatever sentence referred back to it.
- Four short paragraphs at most. No headings, no bullet lists.
- The mandatory disclosures below are ALREADY published, verbatim, ahead of
  whatever you write. Do not repeat them and do not contradict them: if a
  disclosure says nothing moved the way the question asked, the movements
  in the findings are context and must be named as context; if one states a
  suppressed-cell count, no count you give may exclude those cells.

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

Benchmark context (governed):

{benchmarks}
"""

#: Depth → the template rendered for it. Keyed by the contract enum so the
#: wire value, the prompt and the recorded template hash cannot drift.
NARRATIVE_TEMPLATES: dict[NarrativeDepth, str] = {
    NarrativeDepth.SUMMARY: NARRATIVE_TEMPLATE,
    NarrativeDepth.ANALYST: NARRATIVE_TEMPLATE_ANALYST,
}

_REFERENT_TOKEN = re.compile(r"\b[FD]\d+\b")
_NUMBER_TOKEN = re.compile(r"(?<![\w.])[$-]?\$?\d[\d,]*(?:\.\d+)?%?")
_PROPER_NAME = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Abbreviations whose full stop is not a sentence end. Every provider in
#: this warehouse is named "Dr. <name>", so round-4 R4-15 shipped shredded
#: prose on every provider answer that triggered a redaction: "…has a
#: denial rate of at most 90.9% over a population of 11 entities (F1), Dr."
#: was published as a sentence, and "Casey Quarry (143) …" as the next one,
#: which then failed grounding on its own and left the orphan behind.
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


#: A sentence terminator with no space after it, between two words. Round-9
#: R9-04: a live narrative's tail repeated four caution sentences verbatim
#: and the seam where the repeat began carried no space at all —
#: "…has matured.prior month — the true value…". Two consequences, both
#: bad: the prose is unreadable at exactly the point it starts repeating
#: itself, and the naive ``\\s+`` split cannot see a sentence boundary
#: there, so the dedupe below had nothing to compare.
#:
#: Three word characters (or a closing bracket) of left context keep it off
#: "e.g."/"i.e." — the letter before the stop there has a dot in front of it,
#: not two more letters — and the letter lookahead keeps it off decimals,
#: money and dates. Anything it over-splits ("Inc." opening a name) the
#: abbreviation rejoin below puts straight back.
_UNSPACED_SEAM = re.compile(r"(\w{3,}|[)\]\"'])([.!?])(?=[A-Za-z])")


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
    is repaired the same way, for the same reason (R9-04).
    """
    stripped = _UNSPACED_SEAM.sub(r"\1\2 ", text.strip())
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

    Exported so callers and tests can assert the thing R4-15 is about:
    no sentence this module emits may end on "Dr." — that is a fragment,
    and a fragment published as prose is how "…(F1), Dr." reached a buyer.
    """
    return bool(_ABBREVIATION_TAIL.search(sentence.strip()))


#: The two shapes a SPLICE leaves behind — a sentence dropped into a slot
#: that wanted a noun phrase (R10-1).
#:
#: * an interior full stop whose continuation is not a new sentence: a
#:   lower-case word or an opening bracket. "…once the thinner side
#:   matures. (F2)." is the live one;
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
    """The first sentence of ``text`` that reads as a splice, if any (R10-1).

    Wave G's redacted-superlative substitution replaced the OBJECT of a
    superlative clause with a two-sentence verdict, and the demo's second
    answer rendered *"Of the 8 payers measurable this window, the largest
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

    Round-2 FN-5. This rewrite existed only inside the narrative prompt and
    inside one TypeScript file at the wire seam
    (``apps/web/src/lib/contract.ts``), so the *title* — the most-scanned
    field on the card, the one that gets screenshotted — still read
    ``timely filing at risk dollars: $22,426,000.28`` about a number the
    platform's own caveat says is unbilled inventory. A correction applied
    by one client is not a correction: replay, export and any second client
    keep the mislabel. So the same substitution runs server-side, on the
    payload, before anything is published.
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

    Round-5 C-01. The slot is empty when nothing on the MANDATORY list is
    present; the answer may still be carrying a dozen amber banners the
    composer was never shown. Saying "(none)" and letting a model turn that
    into a claim about the answer is how "No mandatory caveats were
    attached to these findings on this turn" got published over seven of
    them.
    """
    if published_cautions <= 0:
        return "- (nothing on the mandatory list for this slot)"
    return (
        "- (nothing on the mandatory list for this slot — but this answer publishes "
        f"{published_cautions} caution-severity caveat(s) above your text. Do NOT write that "
        "the answer carries no caveats, no qualifications or no caution: it does.)"
    )


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
) -> str:
    """Render the composition prompt from certified material only.

    ``caveats`` are the turn's mandatory population caveats — the same
    sentences the §6.6 validation pass publishes as warnings. They are
    rendered as *constraints on characterization*, not as background: the
    composer wrote "the largest timely filing exposure sits with State
    Medicaid HMO at $2,349,692.17" on an answer whose own warning said the
    metric applies no deadline predicate and is an unbilled-inventory upper
    bound. The prose reproduced the overclaim the metric id makes because
    the caveat that corrects it was never in front of the model.

    ``metric_display`` maps metric ids to the governed display names that
    say what each number actually measures, so the prompt shows "unbilled
    open inventory" where the id says "timely filing at risk dollars".

    ``published_cautions`` is how many caution-severity warnings the ANSWER
    carries — which is not the same number as how many of them are handed
    to this composer, and confusing the two is round-5 C-01's second half.
    Both empty slots below used to read "(none)", and the model reported
    that as a fact about the answer: *"No mandatory caveats were attached
    to these findings on this turn"* was published on two turns rendering
    amber caution banners from the same ``warnings_v2`` array. An empty
    slot is a statement about this PROMPT, so it now says so.
    """
    substitutions = _display_substitutions(metric_display)
    finding_lines = "\n".join(_finding_line(f, substitutions) for f in findings) or "- (none)"
    benchmark_lines = "\n".join(f"- {line}" for line in benchmarks) or "- (none provided)"
    empty = _empty_slot(published_cautions)
    caveat_lines = (
        "\n".join(f"- {_apply_display_names(line, substitutions)}" for line in caveats) or empty
    )
    disclosure_lines = "\n".join(f"- {line}" for line in disclosures) or empty
    return NARRATIVE_TEMPLATES[depth].format(
        header=header.display,
        findings=finding_lines,
        caveats=caveat_lines,
        disclosures=disclosure_lines,
        reconciliation=reconciliation or "not applicable on this turn",
        benchmarks=benchmark_lines,
    )


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
) -> NarrativeFacts:
    """The closed fact set the validator trusts.

    ``benchmarks`` takes the same rendered lines that
    :func:`build_narrative_prompt` puts in front of the model. They were
    omitted here for as long as they have existed in the prompt, so a
    narrative that quoted a governed range — exactly what the analyst
    template asks for — had that sentence cut for citing a figure "matching
    no certified value". A range shown to the composer is certified
    material; it is admitted as such.

    ``caveats`` and ``metric_display`` follow the same rule, and for a
    sharper reason: the prompt now *instructs* the composer to state the
    caveat beside the number and to use the display name. Anything this
    module tells the model to write, it must also be willing to validate —
    a validator that redacts the correction it demanded would leave the
    overclaiming sentence standing and drop the sentence that qualified it.
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
    # The turn's own resolved predicate values are certified vocabulary
    # (round-3 R3-12). They were not, so a turn whose context header reads
    # ``filters: payer eq [Veritas Comp Fund]`` had two sentences deleted
    # for "naming 'Veritas Comp Fund', which is outside the certified
    # vocabulary" — a value the answer prints two rows above the redaction,
    # and one of the twelve the product's own clarification offers verbatim.
    # The resulting narrative opened "That bound rests on direct evidence…"
    # with no antecedent anywhere.
    for chip in header.filter_chips:
        names.update(chip.values)
        names.update(chip.requested_values)
    for finding in findings:
        for value in finding.values:
            if isinstance(value.value, str) and value.value:
                names.add(value.value)
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
        # checked against instead of a free pass (R3-18).
        population_counts=_population_counts(disclosures),
        # Read off the ANSWER's own census, not off what this composer was
        # handed (round-5 C-01): a turn carrying WINDOW_ASSUMED and nothing
        # on the mandatory list is a cautioned turn, and the face-value ban
        # below was silently off for every one of them.
        cautioned=bool(disclosures) or published_cautions > 0,
        published_cautions=published_cautions,
        truncated=any(_TRUNCATION_MARKER in line.lower() for line in disclosures),
        topic_sentence=_topic_sentence(findings, header),
        # What goes in the place of a superlative the guard removes, so the
        # demo's opening question is answered rather than left with a hole
        # where its answer was (R9-09).
        superlative_substitute=_superlative_substitute(findings, disclosures),
        # The ranked list's first item, when this question routed to the
        # worklist: no prose instruction may name a different one (R3-10).
        worklist_first_action=worklist_first_action,
        # Size words the premise verdict already ruled out (R4-05).
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
#: waved through as small numbers (R3-18).
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
#: publish a second one beside it (R3-18). The phrase is
#: ``revi_investigation.application.execution.TOO_SMALL_TO_MEASURE``, held
#: as a literal because a presentation package may not import an
#: investigation one — the pair is pinned by a test on both sides.
_CENSUS_CLAUSE = "too small to measure exactly"

#: The engine's own census sentence, as it composes it: "4 of 12 groups
#: here are too small to measure exactly". Matched — rather than recomputed
#: — for the same reason ``_CENSUS_CLAUSE`` is: this package may not import
#: the engine, and two derivations of one census is the defect (R3-18).
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
    """The statement this answer CAN make about its leader (R9-09).

    The superlative guard is right: a "worst" over a truncated or partly
    bounded list is a claim about rows the answer did not publish. Deleting
    the sentence is the wrong remedy — live, on the demo's opening question
    ("Who is my worst payer on denial rate right now…"), it removed the only
    sentence that answered it and left 402 words of movement findings in
    which the words worst, highest and top never appear.

    So the guard substitutes instead of deleting, and what it substitutes is
    the relation the evidence certifies: the leading finding is the highest
    figure this answer MEASURED, which is a different claim from "your
    worst" precisely because of the ceilings — and saying so out loud is the
    same honesty, delivered as an answer.

    Every figure comes from material already certified on this turn: the
    leading finding's own title, and the engine's own census sentence (see
    :data:`_BOUNDED_CENSUS`). Where the census cannot be read the sentence
    degrades to words rather than inventing arithmetic.

    Round-10 R10-1 — a substitution that replaces the OBJECT of a
    superlative clause has to replace the whole clause. This one used to
    drop the leading finding's title into a noun slot, and on the demo's
    second question that title was the turn's premise VERDICT: *"Of the 8
    payers measurable this window, the largest movement is Premise cannot
    be verified: You asked about an increase in denial rate. Ask again once
    the thinner side matures. (F2)."* — a subject-verb collision with a
    nested full stop, read out loud in a room. A verdict is never a row, so
    it never enters the noun slot; where the verdict is that nothing can be
    certified, the certifiable statement is emitted as its own complete
    sentence instead.
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
    # Last line of defence for R10-1: whatever went into the noun slot, the
    # sentence that comes out has to read as one. A substitute that splices
    # is not published at all — the deletion the guard would otherwise have
    # made is a worse answer but never a broken one.
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


def _is_premise_verdict(finding: FindingPayload) -> bool:
    """Is this finding a VERDICT about the question rather than a row?

    Its title is a sentence — *"Premise cannot be verified: You asked about
    an increase in denial rate. Ask again once the thinner side matures."* —
    and a sentence cannot stand where a row label stands (R10-1).
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

    Live on the demo opener with a comparison, F1 is *"Veritas Comp Fund
    denial rate at most ≤ 76.9%"* — a ceiling over 13 entities, ranked
    first by its own delta. Naming it as the highest measured figure would
    replace a redacted superlative with a false one.

    A premise verdict is skipped for a second reason (R10-1): it is not a
    row at all, and its title is a two-sentence judgement that cannot be
    dropped into "the largest movement is ___".
    """
    for finding in findings:
        if _is_premise_verdict(finding):
            continue
        if not any(
            value.name.endswith(_BOUND_SUFFIX) and bool(value.value)
            for value in finding.values
        ):
            return finding
    return None


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
    the survivor starts with a pronoun (round-3 R3-12): "That bound rests
    on direct evidence and is carried at high confidence (F10)" shipped as
    an answer's first words, and the reader never learned what "that bound"
    was. Composed from the header and the leading finding, both certified.
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
    return f"This answer covers {period} ({header.basis} basis){where}. {lead.title} ({lead.referent})."



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
    # The worklist, when the worklist IS the answer (round-3 R3-10). "What
    # should my denial team work first" routed to the governed
    # work_prioritization concept, returned three denied-dollars-by-payer
    # findings and ~500 words about them, and rendered the ranked list below
    # the findings, the charts and the prose — an answer whose own worklist
    # statement said it was "not a measurement of the question asked above".
    # It leads, because the question asked for it.
    "WORKLIST_LEADS",
    "PREMISE_FALSE",
    # The third verdict (round-4 R4-05). A movement in the asserted
    # direction that fell short of the asserted SIZE leads for the same
    # reason a refutation does: everything below it is the composition of a
    # movement the question named wrongly.
    "PREMISE_PARTIAL",
    # The fourth verdict (round-5 A-02). A premise over two suppressed
    # ceilings, an immature comparison panel, or a size nothing could parse
    # is neither confirmed nor refuted — and it leads for the same reason
    # the other three do: it governs whether anything below may be read as
    # evidence for the question's own assumption.
    "PREMISE_UNVERIFIABLE",
    # The other half of the premise family (round-3 R3-03). A verdict that
    # CONFIRMS the question is still the answer's first claim: publishing it
    # only on failure is what let "why did denials double?" — a real +4.2%
    # — open on a 243% sub-cell while the measured aggregate was discarded.
    "PREMISE_VERIFIED",
    # A ranking the platform declined to publish, because too much of its
    # population carries ceilings rather than measurements (R3-02). A
    # refusal cannot sit under the rows it refused to order.
    "RANKING_REFUSED",
    "DIRECTION_UNMATCHED",
    "EMPTY_RESULT",
    # A window that has not finished adjudicating (round-8 FIX-12(a)). It
    # trailed for six rounds, and trailing is what made the first question
    # of every demo — "what is my denial rate?" — answer "12.8%" in its
    # opening clause: the July figure this product's own trend answer
    # excludes as provisional, with the caveat that governs it two
    # paragraphs down and a favourable benchmark verdict in between. The
    # caveat is not a footnote on the number; it decides whether the number
    # may be read as the level at all, which is the same job the premise
    # verdicts above do. Where the engine could measure the last SETTLED
    # period, this sentence carries that figure — so the answer leads with
    # what has settled and names the provisional one as provisional.
    "ADJUDICATION_INCOMPLETE",
    # A stored clarification answer that was APPLIED rather than asked
    # about (round-9 R9-02). Where the engine legitimately applies one — a
    # binding it derived itself from governed content, with genuinely one
    # answer available — the analyst is being handed the answer to a
    # slightly different question, and that sentence cannot live only in
    # ``warnings_v2`` where the client's caution fold can hide it. Live,
    # "Give me a payer scorecard for July 2026" came back as one payer's
    # A/R with the refusal demoted into a warning and 8 of 10 narrative
    # sentences folded away, so the prose never said the scorecard could
    # not be built.
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
    # …and which of them could not be ordered at all (round-3 R3-02).
    "BOUNDED_CELLS_UNRANKED",
    # …and the same fact where the CONTRACT declares it rather than a panel
    # count revealing it (round-7 FN-4). "Net collection rate 72.5% →
    # 18.5%, fell 53.9 points" was published at direct/high beside the same
    # payload's caution that two windows of unequal maturity are not
    # comparable as levels. A delta the governing contract forbids is not a
    # caveat on a result; it is the reason there is no result.
    "NOT_COMPARABLE_WINDOWS",
    # The whole this answer is a part of, restated on the part (FN-10). A
    # breakdown that never says what it broke down leaves a reader who
    # landed on it believing denial rates run 19-29% when the population
    # they descend from is at 12.8%.
    "PARENT_LEVEL",
    # What the answer did NOT publish (R3-04). An omission the reader
    # cannot see is the one that makes a superlative false.
    "FINDINGS_TRUNCATED",
    # …and specifically the cells a DIRECTION removed (round-5 A-04), which
    # is the omission that flatters the question that asked for it.
    "DIRECTION_OMITTED",
    # The window that was actually read, when it is not the window that was
    # asked for (R3-05), and the period vocabulary that was resolved or
    # could not be (R3-16).
    "WINDOW_OUT_OF_RANGE",
    "WINDOW_HORIZON",
    "WINDOW_RELATIVE",
    "RECONCILIATION_FAILED",
    "SNAPSHOT_AS_OF",
    "PROBE_FAMILIES_EMPTY",
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

    The strip on the wire read ``diverged; card=$178,216.82;
    answer=$195,873.92; +9.9%`` while the prose directly beneath it read
    "Reconciliation was not performed on this turn because this is a first
    turn" — the exact sentence the strip exists to eliminate. The lineage
    verdict was true and was about something else; the two figures the
    reader had just compared went unmentioned.
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
            "the two are not comparable — the governed contract is an as-of balance and the "
            "card's figure was computed over a window, so the gap is a difference of "
            "measurement kind rather than a disagreement"
        )
    return _sentence(
        f"The detection card this drill was opened from published {card} and this answer "
        f"publishes {answer}{gap}: the detector's window, population or valuation basis is "
        "not the contract's, and both figures stand as what each system measured"
    )


def mandatory_disclosures(
    classified_warnings: Sequence[tuple[str, str]],
    *,
    reconciliation_sentence: str | None = None,
    suppressed_cells: int = 0,
    total_cells: int = 0,
) -> tuple[list[str], list[str]]:
    """The sentences this answer may not be published without.

    Round-2 FN-3, which five of six review personas hit as four separate
    symptoms of one defect. ``DIRECTION_UNMATCHED`` fired correctly —
    "nothing fell; the movements below are the opposite direction, shown as
    context, not as an answer to what was asked" — and the narrative opened
    "three payers show denial rates rising" and never said nothing
    improved. ``SUPPRESSION_APPLIED`` shipped while the narrative wrote
    "three payers were measured" with nine computable and four censored.
    ``anomaly_reconciliation`` shipped a divergence strip while the prose
    beneath it said reconciliation was not performed. In every case the
    structured fact was ON THE SAME RESPONSE the prose ignored.

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

    lead = stated(LEAD_DISCLOSURE_CODES)
    trail: list[str] = []
    # The engine now counts its own cells and publishes the arithmetic on
    # the SUPPRESSION_BOUNDED disclosure (round-3 R3-18). When it has, the
    # count derived here from ``suppressed_cells`` — which counts nulled
    # VALUES, several per row — is a second, different population for one
    # control, and two arithmetics in one paragraph is the defect.
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
        trail.append(
            _sentence(
                "This answer does not reconcile against the answer it was drilled from, and "
                f"both figures stand published until it does — {detail or failed}"
            )
        )
    trail.extend(stated(TRAILING_DISCLOSURE_CODES))
    if reconciliation_sentence:
        trail.append(_sentence(reconciliation_sentence))
    return lead, trail


def empty_narrative(classified_warnings: Sequence[tuple[str, str]]) -> str | None:
    """Prose for a turn that published no finding.

    ``EMPTY_RESULT`` returned ``outcome: "answer"``, ``findings: []`` and
    ``narrative: null`` on the wire — four personas, three separate
    questions — and the client then rendered "No findings for this
    question" over a population where a value exists (9/214 = 4.21% for
    Federal Medicare). A null narrative is not an absence of prose; it is
    an absence of the explanation the analyst most needs, on exactly the
    turn that most needs it. The cause is already structured on the
    response, so it is stated.
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
        ["This turn published no finding, and here is why.", *dict.fromkeys(sentences)]
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


#: Sentences that assert the answer needs no allowance. Round-3 R3-05: the
#: engine warned "this load only reaches 2026-08-02 — the figures below
#: cover 2026-07-01..2026-08-02" and the prose on the same turn said the
#: magnitude and direction "can be taken at face value for the period and
#: basis stated, without an allowance for derivation error", over a
#: sign-inverted year-over-year comparison. A caution and a face-value
#: claim cannot both be published.
_FACE_VALUE = re.compile(
    r"\b(?:at face value|taken at face value|without (?:an? )?(?:allowance|caveat|qualification)"
    r"|no allowance for)\b",
    re.IGNORECASE,
)

#: Sentences asserting the ANSWER carries no caveats. Round-5 C-01: the
#: composer was testing whether ``mandatory_disclosures`` had handed it a
#: lead or a trail and reporting that as a fact about the answer — "No
#: mandatory caveats were attached to these findings on this turn", written
#: on two independent turns that render amber caution banners from the same
#: ``warnings_v2`` array. The affirmation is derived from the warning
#: census now (see ``published_cautions``), and a sentence that makes it
#: anyway is redacted rather than argued with.
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
#: was narrated as "roughly three percentage points … a tight band"
#: (round-3 R3-04). Never certified over a slice, whatever it cites.
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
#: claim about rows the answer did not publish: "State Medicaid MCO is
#: highest of the measured group at 7.5%" was written over a served slice
#: whose true maximum was 15.0%.
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
                return (
                    f"counts {value} cell(s)/entities, which no certified suppression figure "
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

    Round-4 R4-05. The premise finding said "It did not double — denial
    rate rose 72.6%, short of the 100.0% a doubling assumes", and nothing
    stopped the composer writing "denials roughly doubled" two sentences
    later over the same figure. A narrative may report the verdict; it may
    not restate the claim the verdict declined.
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

    Round-3 R3-10. The worklist and the narrative are two orderings on one
    card, and when the question was "what should we work first" only one of
    them was asked for. A prose instruction that names a different first
    thing is not a second opinion — the analyst has no way to tell which
    the platform means, and the one they read first pointed at a fifth of
    the money.

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

    The render-time half of R9-04. The deduper below works on sentences,
    which is the right unit and not the guarantee that matters: what a
    reader sees is a string, and the invariant the answer owes them is that
    no paragraph of it appears twice. This is that invariant, checkable
    from outside, on the final bytes.

    ``None`` — the only acceptable answer for published prose — when
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

    Round-9 R9-04, live on the demo's opening question: a 1,436-character
    narrative whose tail repeated four caution sentences verbatim, glued
    mid-word at the seam, directly beneath the product's own note saying
    those sentences "are not printed twice". The note was composed from the
    match set and the string that shipped was the undeduplicated one, so
    the answer contradicted itself in the largest body copy on the page.
    Intermittent, which is worse in a room: a second identical run came
    back clean.

    So the note may only ever be composed from the EMITTED text — which is
    what returning the dropped sentences alongside the prose is for — and
    the emitted text is deduplicated here rather than described as if it
    were. A sentence is dropped when its normalized form has already been
    kept, or when :data:`DOUBLED_SPAN_CHARS` characters of it already
    appear in what has been kept: the second rule catches a caution
    restated with a comma moved, which byte equality does not.
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
    composes ABOUT the emitted text (R9-04: a note derived from anything
    else can, and did, describe a string that never shipped).

    The join itself is the other half of that defect. The mandatory
    disclosures are put in front of the model as constraints, so a
    conscientious composer restates them — and then they are published
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
    recorded with its own reason (R9-04). It is not a grounding failure —
    the repeated sentence is usually a mandatory caution the composer was
    shown and dutifully copied — but publishing it twice is still publishing
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
    # derived, and deriving the censorship arithmetic is what produced
    # "3 of 15 cells" over a 12-cell answer with nothing withheld (R3-18).
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
    #: certifiable statement in its place, not three (R9-09).
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
            reason = f"cites unknown referent(s) {sorted(unknown_citations)}"
        if reason is None and numbers:
            if not cited:
                reason = "states figures without citing a referent"
            else:
                for token in numbers:
                    if not _number_allowed(token, allowed, date_tokens):
                        reason = f"figure {token!r} matches no certified value"
                        break
        if reason is None:
            for match in _PROPER_NAME.finditer(sentence):
                name = match.group(1)
                if (numbers or cited) and not _name_admitted(name, known_token_sequences):
                    reason = f"names {name!r}, which is outside the certified vocabulary"
                    break
        if reason is None and facts.cautioned and _FACE_VALUE.search(sentence):
            reason = (
                "claims the figures can be taken at face value on a turn carrying a "
                "caution-severity disclosure"
            )
        if reason is None and facts.published_cautions and _NO_CAVEATS.search(sentence):
            reason = (
                f"states the answer carries no caveats while {facts.published_cautions} "
                "caution-severity caveat(s) are published on it"
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
                    f"leading finding ({leading_referent or 'none'}) — the relation is certified "
                    "only over the full computed population"
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
                # Never a silent hole where the answer was (R9-09): the
                # certifiable statement goes in, in the redacted sentence's
                # own place, and says why it is not the superlative.
                substitute_owed = True
                kept.append(facts.superlative_substitute)

    # An analysis whose opening pronoun lost its antecedent is not an
    # analysis (round-3 R3-12). When redaction took the first sentence and
    # the survivor opens with a demonstrative, the deterministic topic
    # sentence goes in front of it rather than the reader being left to
    # guess what "that bound" was.
    if (
        redactions
        and kept
        and facts.topic_sentence
        and _STRANDED_OPENING.match(kept[0])
        and text.strip()
        and split_sentences(text)[0].strip() != kept[0]
    ):
        kept.insert(0, facts.topic_sentence)

    # …and nothing survives here twice (R9-04). Last, so that a sentence
    # dropped for grounding is reported as a grounding failure and only a
    # genuine repetition is reported as one.
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
        warnings.append(
            f"{REDACTION_WARNING_PREFIX}: {len(redactions)} sentence(s) dropped "
            f"from the narrative ({'; '.join(reasons)})"
        )

    return NarrativeValidation(text=emitted, redactions=redactions, warnings=warnings)
