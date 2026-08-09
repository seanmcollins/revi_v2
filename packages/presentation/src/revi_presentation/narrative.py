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


def _finding_line(
    finding: FindingPayload, substitutions: Sequence[tuple[re.Pattern[str], str]] = ()
) -> str:
    values = ", ".join(f"{v.name}={v.value}" for v in finding.values)
    line = (
        f"- {finding.referent}: {finding.title} (grade {finding.grade}, "
        f"confidence {finding.confidence}; {values})"
    )
    return _apply_display_names(line, substitutions)


def build_narrative_prompt(
    *,
    findings: Sequence[FindingPayload],
    header: ContextHeaderPayload,
    reconciliation: str | None,
    benchmarks: Sequence[str] = (),
    caveats: Sequence[str] = (),
    metric_display: Mapping[str, str] | None = None,
    depth: NarrativeDepth = NarrativeDepth.SUMMARY,
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
    """
    substitutions = _display_substitutions(metric_display)
    finding_lines = "\n".join(_finding_line(f, substitutions) for f in findings) or "- (none)"
    benchmark_lines = "\n".join(f"- {line}" for line in benchmarks) or "- (none provided)"
    caveat_lines = (
        "\n".join(f"- {_apply_display_names(line, substitutions)}" for line in caveats)
        or "- (none on this turn)"
    )
    return NARRATIVE_TEMPLATES[depth].format(
        header=header.display,
        findings=finding_lines,
        caveats=caveat_lines,
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
    for line in (*benchmarks, *caveats, *(metric_display or {}).values()):
        for token in _NUMBER_TOKEN.findall(line):
            value = _token_value(token)
            if value is not None:
                numbers.append(value)
        for match in _PROPER_NAME.finditer(line):
            names.add(match.group(1))
    if header.cohort_size is not None:
        numbers.append(Decimal(header.cohort_size))
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


def validate_narrative(text: str, facts: NarrativeFacts) -> NarrativeValidation:
    """Sentence-level grounding check.

    Sentences that fail are **dropped from the prose** and reported through
    ``redactions`` (full text + reason, for the trace) and one aggregate
    ``warnings`` entry (count + distinct reasons, for the operator). The
    returned ``text`` contains only sentences that validated, so a customer
    never reads a redaction marker — see the module docstring.
    """
    allowed: set[Decimal] = set()
    for value in facts.numeric_values:
        allowed.update(_expansions(Decimal(value)))
    referent_ids = set(facts.referent_ids)
    known_token_sequences = [name.split() for name in facts.allowed_names]
    date_tokens = set(facts.date_tokens)

    kept: list[str] = []
    redactions: list[NarrativeRedaction] = []

    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        if not sentence.strip():
            continue
        reason: str | None = None
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
        if reason is None:
            kept.append(sentence.strip())
        else:
            redactions.append(NarrativeRedaction(sentence=sentence.strip(), reason=reason))

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

    return NarrativeValidation(text=" ".join(kept), redactions=redactions, warnings=warnings)
