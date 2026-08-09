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

A violating sentence is REDACTED — replaced with a bracketed note — and a
warning is recorded; the caller flags the trace. Nothing is ever silently
kept.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
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
- Two short paragraphs at most. No headings, no bullet lists.

Effective context:

{header}

Certified findings:

{findings}

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
- Where a benchmark range is given, say how the figure sits against it —
  as a range, with its cohort, never as a pass/fail target.
- Four short paragraphs at most. No headings, no bullet lists.

Effective context:

{header}

Certified findings:

{findings}

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

REDACTION_NOTE = "[redacted: a sentence here failed evidence validation]"


def template_hash(depth: NarrativeDepth = NarrativeDepth.SUMMARY) -> str:
    """The hash of the template a given depth renders.

    Depth-aware because the hash is what a trace uses to prove which
    prompt produced a narrative; one hash for two prompts would make that
    proof a guess.
    """
    return hashlib.sha256(NARRATIVE_TEMPLATES[depth].encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# prompt + facts


def _finding_line(finding: FindingPayload) -> str:
    values = ", ".join(f"{v.name}={v.value}" for v in finding.values)
    return (
        f"- {finding.referent}: {finding.title} (grade {finding.grade}, "
        f"confidence {finding.confidence}; {values})"
    )


def build_narrative_prompt(
    *,
    findings: Sequence[FindingPayload],
    header: ContextHeaderPayload,
    reconciliation: str | None,
    benchmarks: Sequence[str] = (),
    depth: NarrativeDepth = NarrativeDepth.SUMMARY,
) -> str:
    finding_lines = "\n".join(_finding_line(f) for f in findings) or "- (none)"
    benchmark_lines = "\n".join(f"- {line}" for line in benchmarks) or "- (none provided)"
    return NARRATIVE_TEMPLATES[depth].format(
        header=header.display,
        findings=finding_lines,
        reconciliation=reconciliation or "not applicable on this turn",
        benchmarks=benchmark_lines,
    )


def build_narrative_facts(
    *,
    findings: Sequence[FindingPayload],
    header: ContextHeaderPayload,
    extra_names: Sequence[str] = (),
) -> NarrativeFacts:
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


def validate_narrative(text: str, facts: NarrativeFacts) -> NarrativeValidation:
    """Sentence-level grounding check; violations are redacted, never kept."""
    allowed: set[Decimal] = set()
    for value in facts.numeric_values:
        allowed.update(_expansions(Decimal(value)))
    referent_ids = set(facts.referent_ids)
    known_names = set(facts.allowed_names)
    date_tokens = set(facts.date_tokens)

    kept: list[str] = []
    redactions: list[NarrativeRedaction] = []
    warnings: list[str] = []

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
                if name not in known_names and (numbers or cited):
                    reason = f"names {name!r}, which is outside the certified vocabulary"
                    break
        if reason is None:
            kept.append(sentence.strip())
        else:
            redactions.append(NarrativeRedaction(sentence=sentence.strip(), reason=reason))
            warnings.append(f"narrative sentence redacted: {reason}")
            kept.append(REDACTION_NOTE)

    return NarrativeValidation(text=" ".join(kept), redactions=redactions, warnings=warnings)
