"""Prose warnings → structured, branchable warning objects (review F14).

Every warning this platform publishes was a sentence. The engine writes
them for a reader, which is right, but a *client* that wants to render
"3 caveats, 1 of them about suppression" had no option but to match on
substrings — and a client matching on substrings is a client that breaks
silently the day somebody improves the wording.

So each warning is classified here, at the API boundary, into

    {code, severity, message, count}

and published as ``warnings_v2`` **alongside** the untouched ``warnings``
strings. Additive on purpose: nothing that reads the legacy field has to
change, and nothing that reads the new one has to parse a sentence.

Three rules this module keeps:

* **The engine's sentence is never lost.** ``message`` is the warning
  verbatim. Classification adds a handle; it does not replace the text
  with a shorter, vaguer one (the mistake the plain-error-copy module was
  written to avoid making twice).
* **Nothing is dropped.** A sentence matching no known family is published
  under :data:`UNCLASSIFIED` rather than swallowed — an unrecognized
  warning is still a warning, and the code says "we don't have a handle
  for this one" instead of pretending it did not happen.
* **Identical codes collapse with a count.** A four-probe plan emitting
  the same population caveat four times is one caveat seen four times; the
  first message wins and ``count`` says how many there were, so a client
  renders one row instead of four identical ones. Warnings with the same
  code but *different* text stay separate entries — they are different
  facts.

Severity is a two-value ladder on purpose. ``caution`` means "this changes
how you should read the number"; ``info`` means "this is worth knowing and
does not change the reading". Anything that would need a third level is a
refusal, and refusals are errors with §12 codes, not warnings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from revi_investigation_contracts.api import WarningPayload

CAUTION = "caution"
INFO = "info"

#: The code for a warning no rule below recognizes. Published, never
#: dropped: see the module docstring.
UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True, slots=True)
class _Rule:
    code: str
    severity: str
    pattern: re.Pattern[str]


def _rule(code: str, severity: str, pattern: str) -> _Rule:
    return _Rule(code=code, severity=severity, pattern=re.compile(pattern, re.IGNORECASE))


#: Ordered: the first matching rule wins, so the specific families come
#: before the general ones. Every family the engine, the presentation
#: stage and the portfolio builder emit today is here; the list is the
#: contract the web codes against, and adding a warning without adding a
#: rule downgrades it to UNCLASSIFIED rather than breaking anything.
_RULES: tuple[_Rule, ...] = (
    # -- how the number was scoped or qualified (changes the reading) -----
    _rule("EMPTY_RESULT", CAUTION, r"^empty_result:"),
    _rule("VALUE_CORRECTED", CAUTION, r"^value_corrected:"),
    _rule("DIRECTION_UNMATCHED", CAUTION, r"^direction_unmatched:"),
    _rule("WINDOW_ASSUMED", CAUTION, r"^window_assumed:"),
    _rule("SNAPSHOT_AS_OF", CAUTION, r"^snapshot_as_of:"),
    _rule("DROPPED_GRAIN", CAUTION, r"^dropped_grain:"),
    _rule("FILTER_REDUNDANT", INFO, r"^filter_redundant:"),
    _rule("COMPARISON_WINDOW_LENGTH", INFO, r"^comparison_window_length:"),
    _rule("POPULATION_CAVEAT", CAUTION, r"^population_caveat:"),
    _rule("ALTERNATE_BASIS_USED", CAUTION, r"^alternate_basis_used:"),
    _rule("COMPARISON_WINDOW_MISMATCH", CAUTION, r"^COMPARISON_WINDOW_MISMATCH:"),
    _rule("RECONCILIATION_FAILED", CAUTION, r"^RECONCILIATION_FAILED:"),
    _rule("SCOPE_INTERACTS_WITH_CONTRACT", CAUTION, r"^scope on '.+' interacts with metric"),
    _rule("PROBE_OMITTED", CAUTION, r"^probe '.+' omitted:"),
    _rule("RESULT_TRUNCATED", CAUTION, r"^probe '.+' truncated to the top"),
    _rule("COHORT_WINDOW_DROPPED", CAUTION, r"^cohort pinned without its window:"),
    _rule("ASSUMPTION_COMMITTED", CAUTION, r"^Assumed:"),
    _rule("CLARIFICATION_ANSWER_APPLIED", CAUTION, r"^Read as an answer to the question above:"),
    # -- what the platform did with the answer (does not change it) -------
    _rule("SUPPRESSION_APPLIED", INFO, r"^suppression: cells counting fewer than"),
    _rule("NARRATIVE_REDACTED", INFO, r"^narrative sentence redacted:"),
    _rule("NARRATIVE_NOT_COMPOSED", INFO, r"^narrative not composed:"),
    _rule("PROBE_TEMPLATE_SKIPPED", INFO, r"^probe template '.+' skipped:"),
    _rule("TRANSFORM_NOT_EXECUTABLE", INFO, r"^transform '?.+'? is not executable"),
    _rule("TRANSFORM_SKIPPED", INFO, r"^transform '?.+'? skipped:"),
    # -- worklist-level facts about the portfolio -------------------------
    _rule("PORTFOLIO_CARDS_NOT_INVESTIGABLE", CAUTION, r"detected anomalies .* are not investigable"),
    _rule("PORTFOLIO_FEED_EMPTY", INFO, r"^no detected anomalies at this watermark"),
    _rule("PORTFOLIO_IMPACT_UNRECONCILED", CAUTION, r"^\d+ of \d+ ranked cards could not be re-derived"),
    _rule("PORTFOLIO_IMPACT_DIVERGED", CAUTION, r"^\d+ ranked cards? diverge"),
    _rule(
        "PORTFOLIO_IMPACT_NOT_COMPARABLE",
        CAUTION,
        r"^\d+ of \d+ ranked cards name a governed contract that is not comparable",
    ),
    # -- coverage the pipeline measured and did not publish ---------------
    _rule("PROBE_FAMILIES_EMPTY", CAUTION, r"^probe_families_empty:"),
)

#: Every code this module can emit, for the client that wants to enumerate
#: them (and for the report that tells the web lane what to expect).
WARNING_CODES: tuple[str, ...] = (*(rule.code for rule in _RULES), UNCLASSIFIED)


def classify(message: str) -> tuple[str, str]:
    """``(code, severity)`` for one warning sentence."""
    text = message.strip()
    for rule in _RULES:
        if rule.pattern.search(text):
            return rule.code, rule.severity
    return UNCLASSIFIED, INFO


def structured_warnings(warnings: Sequence[str] | Iterable[str]) -> list[WarningPayload]:
    """Classify and deduplicate a turn's warnings, order preserved.

    Deduplication is by ``(code, message)``, not by code alone: two
    ``alternate_basis_used`` warnings naming different probes are two
    facts, and collapsing them would hide one. Two *identical* sentences
    are one fact seen twice, and that is what ``count`` reports.
    """
    order: list[tuple[str, str]] = []
    seen: dict[tuple[str, str], WarningPayload] = {}
    for raw in warnings:
        message = raw.strip()
        if not message:
            continue
        code, severity = classify(message)
        key = (code, message)
        existing = seen.get(key)
        if existing is None:
            seen[key] = WarningPayload(
                code=code, severity=severity, message=message, count=1
            )
            order.append(key)
        else:
            seen[key] = existing.model_copy(update={"count": existing.count + 1})
    return [seen[key] for key in order]
