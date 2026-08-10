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
    _rule("PREMISE_FALSE", CAUTION, r"^premise_false:"),
    # Round-4 R4-05: the third verdict. A movement in the direction the
    # question asserts but short of the SIZE it asserts is neither
    # confirmed nor refuted — "denials did not rise" would be as false as
    # "denials doubled" over a real +72.6%.
    _rule("PREMISE_PARTIAL", CAUTION, r"^premise_partial:"),
    # Round-5 A-02: the fourth verdict. A movement between two suppressed
    # ceilings, a comparison whose two panels are not equally settled, and
    # a SIZE this platform could not parse are each unverifiable — neither
    # confirmed nor refuted, and rendering any of them as either is the
    # defect ("Premise confirmed" over 157.1% that was the ratio of two
    # denominators).
    _rule("PREMISE_UNVERIFIABLE", CAUTION, r"^premise_unverifiable:"),
    _rule("PREMISE_VERIFIED", INFO, r"^premise_verified:"),
    _rule("RANKING_REFUSED", CAUTION, r"^ranking_refused:"),
    _rule("BOUNDED_CELLS_UNRANKED", CAUTION, r"^bounded_cells_unranked:"),
    _rule("ADJUDICATION_INCOMPLETE", CAUTION, r"^adjudication_incomplete:"),
    _rule("FINDINGS_TRUNCATED", CAUTION, r"^findings_truncated:"),
    _rule("WINDOW_RELATIVE", INFO, r"^window_relative:"),
    _rule("WINDOW_HORIZON", CAUTION, r"^window_horizon:"),
    _rule("NAMED_CUT_APPLIED", INFO, r"^named_cut_applied:"),
    _rule("SUPPRESSION_BOUNDED", CAUTION, r"^suppression_bounded:"),
    _rule("WINDOW_OUT_OF_RANGE", CAUTION, r"^window_out_of_range:"),
    _rule("COMPARISON_ASSUMED", INFO, r"^comparison_assumed:"),
    # Round-7 FN-4: two windows the governed contract itself declares may
    # not be differenced as levels. The engine refuses the difference and
    # says why in the pack author's own words; without a rule the sentence
    # that carries the refusal landed UNCLASSIFIED, which is the code for
    # "we have no handle for this" on the one warning that changes whether a
    # number on the turn may be read as a comparison at all.
    _rule("NOT_COMPARABLE_WINDOWS", CAUTION, r"^not_comparable_windows:"),
    # Round-4 R4-08: a comparison cell whose prior side was never retrieved
    # (it fell outside a top-N). UNKNOWN is published; $0.00 never is.
    _rule("COMPARISON_PRIOR_UNKNOWN", CAUTION, r"^comparison_prior_unknown:"),
    # Round-4 R4-04: a refinement that re-served an existing plan, and the
    # governed caveats that plan carried, verbatim.
    _rule("REFINEMENT_REUSED_PLAN", INFO, r"^refinement_reused_plan:"),
    # Round-4 R4-04: the operators the analyst asked for that the served
    # answer does not reflect, named rather than silently dropped.
    _rule("REFINEMENT_NOT_APPLIED", CAUTION, r"^refinement_not_applied:"),
    # Round-4 R4-09: a chart whose rows were not uniquely keyed by the axes
    # it declared, and what the server did about it.
    _rule("CHART_ROWS_COLLAPSED", CAUTION, r"^chart_rows_collapsed:"),
    _rule("VALUE_CORRECTED", CAUTION, r"^value_corrected:"),
    _rule("DIRECTION_UNMATCHED", CAUTION, r"^direction_unmatched:"),
    # Round-5 A-04: the cells a directional selection removed, named. "Show
    # me all twelve" returned ten and the two missing were the only two
    # that had improved — a systematically premise-flattering omission that
    # no census on the card counted.
    _rule("DIRECTION_OMITTED", CAUTION, r"^direction_omitted:"),
    # Round-5 A-01: context carried onto a clarification resume from the
    # thread it interrupted, rather than defaulted.
    _rule("RESUMED_CONTEXT", INFO, r"^resumed_context:"),
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
    _rule(
        "CLARIFICATION_ANSWER_APPLIED",
        CAUTION,
        # Two sentences, one fact: the analyst's reply read as an answer, and
        # the engine applying a one-option clarification without asking.
        r"^(Read as an answer to the question above:|clarification_answer_applied:)",
    ),
    # This platform's own dimension swap, disclosed on the turn that made it
    # (round-3 R3-11). The card carried it and the drill answer did not, so
    # the strip blamed the detector for a population change the platform
    # chose. Emitted by :func:`revi_api.portfolio.dimension_repointed_warning`.
    _rule("DIMENSION_REPOINTED", CAUTION, r"^dimension_repointed:"),
    # -- what the platform did with the answer (does not change it) -------
    # Round-7 FN-10: a breakdown or drill naming the parent figure its cells
    # are parts of. Information rather than a caution — the cells are right
    # either way; this says what they add up to, so nobody reads one of them
    # as a measurement of the whole.
    _rule("PARENT_LEVEL", INFO, r"^parent_level:"),
    _rule("SUPPRESSION_APPLIED", INFO, r"^suppression: cells counting fewer than"),
    _rule("NARRATIVE_REDACTED", INFO, r"^narrative sentence redacted:"),
    _rule("NARRATIVE_NOT_COMPOSED", INFO, r"^narrative not composed:"),
    _rule("PROBE_TEMPLATE_SKIPPED", INFO, r"^probe template '.+' skipped:"),
    _rule("TRANSFORM_NOT_EXECUTABLE", INFO, r"^transform '?.+'? is not executable"),
    _rule("TRANSFORM_SKIPPED", INFO, r"^transform '?.+'? skipped:"),
    # -- the watch a turn declared, and what became of it ------------------
    # Round-7 FN-3. A refused watch declaration was appended to `warnings`
    # AFTER `warnings_v2` had been built, so the one sentence that mattered
    # — "nothing is being watched" — was classified nowhere, counted by no
    # integrity line, and rendered on no screen. It was also mis-coded as a
    # `population_caveat`, which is a statement about who is in a number and
    # not about whether a watch exists.
    _rule("WATCH_NOT_CREATED", CAUTION, r"^watch_not_created:"),
    # Round-7 FN-5. The declaration is held across the clarification it
    # triggered and registered from the resolved answer; while the question
    # is on screen, this says so. Silence here is the same defect wearing a
    # different mask.
    _rule("WATCH_PENDING_CLARIFICATION", CAUTION, r"^watch_pending_clarification:"),
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
    _rule(
        "PORTFOLIO_RANKED_ON_PLATFORM",
        CAUTION,
        r"ranked on this platform's re-derived figure",
    ),
    _rule(
        "PORTFOLIO_RANKED_ON_DETECTOR",
        INFO,
        r"ranked on the detection system's figure because",
    ),
    # -- the worklist, read into a conversation ---------------------------
    # The worklist is the ANSWER, not a companion to it: emitted only when
    # the question routed to the governed work-prioritization playbook or
    # concept, and led with (round-3 R3-10).
    _rule("WORKLIST_LEADS", CAUTION, r"^worklist_leads:"),
    _rule("WORKLIST_ATTACHED", INFO, r"^worklist_attached:"),
    _rule("WORKLIST_UNAVAILABLE", CAUTION, r"^the ranked anomaly worklist was requested"),
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


def unconserved(
    warnings: Sequence[str], structured: Sequence[WarningPayload]
) -> tuple[str, ...]:
    """Prose warnings with no classified twin — the drop, named.

    ``warnings_v2`` is what every client actually renders: the web reads the
    structured list whenever it is non-empty and never falls back to the
    prose. So a sentence that reaches ``warnings`` alone is a sentence
    nobody sees, and the API has appended to ``warnings`` alone at least
    once for every field it added after the assembler ran (round-7 FN-3: the
    refusal of a watch declaration, which is the one warning whose absence
    lets somebody walk away believing they are being watched).

    The check is by MESSAGE and not by count. ``structured_warnings``
    deduplicates identical sentences into one entry with a ``count``, so
    ``len(v2) >= len(warnings)`` is false for honest payloads and would
    train whoever hit it to delete the assertion. "Every sentence is
    represented" is the invariant that is actually true.
    """
    represented = {payload.message.strip() for payload in structured}
    return tuple(
        message
        for raw in warnings
        if (message := raw.strip()) and message not in represented
    )


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
