"""Warnings become branchable without becoming less honest (review F14).

The properties that matter are not "every family has a code" — that is
easy and would be worth little. They are: the sentence survives untouched,
nothing is silently dropped, and identical warnings collapse while
*different* warnings never do.
"""

from __future__ import annotations

import pytest

from revi_api.warning_codes import UNCLASSIFIED, WARNING_CODES, classify, structured_warnings

# One live example per family the platform emits, copied from the emitting
# site rather than paraphrased — a rule that only matches a paraphrase is a
# rule that does not match production.
FAMILIES: list[tuple[str, str, str]] = [
    (
        "SUPPRESSION_APPLIED",
        "info",
        "suppression: cells counting fewer than 11 entities are suppressed before results "
        "leave the engine",
    ),
    (
        "POPULATION_CAVEAT",
        "caution",
        "population_caveat: timely_filing_at_risk_dollars — this figure applies NO deadline "
        "predicate.",
    ),
    (
        "ALTERNATE_BASIS_USED",
        "caution",
        "alternate_basis_used: probe 'main_1' reads 'denial_rate' on the 'service' date basis "
        "(primary is 'remit')",
    ),
    (
        "PROBE_OMITTED",
        "caution",
        "probe 'main_2' omitted: its measures are not answerable at the source for this "
        "catalog and this repository (credit_balance_cents unresolved)",
    ),
    (
        "RESULT_TRUNCATED",
        "caution",
        "probe 'main_1' truncated to the top 10 of an estimated 4200 cells",
    ),
    (
        "SCOPE_INTERACTS_WITH_CONTRACT",
        "caution",
        "scope on 'status' interacts with metric 'ar_balance' — the contract already "
        "constrains that dimension internally (exclusions or numerator filter); the result "
        "reflects both conditions",
    ),
    (
        "COHORT_WINDOW_DROPPED",
        "caution",
        "cohort pinned without its window: the 'post' basis is not answerable at the 'claim' "
        "grain, so the cohort covers the scoped population across all time",
    ),
    (
        "COMPARISON_WINDOW_MISMATCH",
        "caution",
        "COMPARISON_WINDOW_MISMATCH: the comparison window (2026-01-01..2026-03-31, 90d) is "
        "not the same length as the analysis window (30d).",
    ),
    (
        "RECONCILIATION_FAILED",
        "caution",
        "RECONCILIATION_FAILED: status=failed; failed measures: cash_posted",
    ),
    (
        "ASSUMPTION_COMMITTED",
        "caution",
        "Assumed: this is a fresh question, asked as written. I had asked 2 clarifying "
        "questions in a row without converging.",
    ),
    (
        "CLARIFICATION_ANSWER_APPLIED",
        "caution",
        "Read as an answer to the question above: 'which service line?' → 'imaging'. "
        "Answering the original question with that applied.",
    ),
    (
        "NARRATIVE_REDACTED",
        "info",
        "narrative sentence redacted: names 'Federal Medicare General Surgery', which is "
        "outside the certified vocabulary",
    ),
    (
        "NARRATIVE_NOT_COMPOSED",
        "info",
        "narrative not composed: this turn reached its cost ceiling after the evidence was "
        "computed; the findings and charts below are complete",
    ),
    (
        "PROBE_TEMPLATE_SKIPPED",
        "info",
        "probe template 'by_payer' skipped: it parameterizes a dimension and the question "
        "names none",
    ),
    (
        "TRANSFORM_SKIPPED",
        "info",
        "transform 'compare' skipped: the question carries no comparison window",
    ),
    (
        "TRANSFORM_NOT_EXECUTABLE",
        "info",
        "transform 'forecast' is not executable on this milestone's engine; recorded and "
        "skipped",
    ),
    (
        "PORTFOLIO_CARDS_NOT_INVESTIGABLE",
        "caution",
        "4 of 33 detected anomalies (36% of ranked impact) are not investigable at this "
        "catalog and pack version",
    ),
    (
        "PORTFOLIO_FEED_EMPTY",
        "info",
        "no detected anomalies at this watermark (the detection feed may not have landed yet)",
    ),
    (
        "PORTFOLIO_IMPACT_UNRECONCILED",
        "caution",
        "3 of 29 ranked cards could not be re-derived against this platform's governed "
        "contracts",
    ),
    (
        "PORTFOLIO_IMPACT_DIVERGED",
        "caution",
        "12 ranked cards diverge from this platform's re-derivation of the same cell "
        "(largest gap 9.9%)",
    ),
    (
        "EMPTY_RESULT",
        "caution",
        "empty_result: every probe returned zero rows — predicates in play: "
        "payer in [UnitedHealthcare, Aetna]",
    ),
    (
        "VALUE_CORRECTED",
        "caution",
        "value_corrected: read 'atlas commercial' as payer 'Atlas Commercial' — the "
        "closest match in this data differs only in case or punctuation",
    ),
    (
        "DIRECTION_UNMATCHED",
        "caution",
        "direction_unmatched: nothing worsened — no cell's denied dollars moved the way "
        "'increase' asks about over this window",
    ),
    (
        "WINDOW_ASSUMED",
        "caution",
        "window_assumed: the question named no period, so I used 2026-07-01..2026-07-31 "
        "on the service basis — the last full month this load can see "
        "(newest data date 2026-08-02)",
    ),
    (
        "DROPPED_GRAIN",
        "caution",
        "dropped_grain: the question asked for a breakdown by 'facility' and no probe in "
        "this plan is cut by it — the numbers below are aggregated over it",
    ),
    (
        "FILTER_REDUNDANT",
        "info",
        "filter_redundant: dropped the status filter status eq [OPEN] — metric "
        "'ar_balance' already pins that population in its own definition, so the "
        "filter restated it",
    ),
    (
        "COMPARISON_WINDOW_LENGTH",
        "info",
        "comparison_window_length: the comparison window (2026-02-05..2026-05-05, 90d) is "
        "one day shorter than the primary window — immaterial at this tolerance and "
        "disclosed rather than qualified",
    ),
]


@pytest.mark.parametrize(
    ("code", "severity", "message"), FAMILIES, ids=[f[0] for f in FAMILIES]
)
def test_every_emitted_family_classifies(code: str, severity: str, message: str) -> None:
    assert classify(message) == (code, severity)


def test_every_rule_is_reachable_from_a_real_sentence() -> None:
    """A code nothing produces is a code the web would code against and
    never see. Every rule in the table must be covered by the fixtures
    above, so the published list and the emitted list are the same list."""
    covered = {code for code, _, _ in FAMILIES} | {UNCLASSIFIED}
    assert set(WARNING_CODES) == covered


def test_an_unknown_sentence_is_published_not_swallowed() -> None:
    payload = structured_warnings(["something nobody has written a rule for yet"])
    assert [(w.code, w.message) for w in payload] == [
        (UNCLASSIFIED, "something nobody has written a rule for yet")
    ]


def test_the_sentence_survives_verbatim() -> None:
    """The code is a handle added beside the text, never a replacement."""
    original = FAMILIES[0][2]
    [payload] = structured_warnings([original])
    assert payload.message == original


def test_identical_warnings_collapse_with_a_count() -> None:
    caveat = FAMILIES[1][2]
    payload = structured_warnings([caveat, caveat, caveat])
    assert len(payload) == 1 and payload[0].count == 3


def test_same_code_different_text_stays_two_entries() -> None:
    """Two probes truncated at different limits are two facts; merging
    them would hide one."""
    payload = structured_warnings(
        [
            "probe 'main_1' truncated to the top 10 of an estimated 4200 cells",
            "probe 'main_2' truncated to the top 5 of an estimated 900 cells",
        ]
    )
    assert len(payload) == 2
    assert all(w.code == "RESULT_TRUNCATED" for w in payload)


def test_order_is_preserved_and_blanks_dropped() -> None:
    payload = structured_warnings([FAMILIES[0][2], "   ", FAMILIES[1][2]])
    assert [w.code for w in payload] == ["SUPPRESSION_APPLIED", "POPULATION_CAVEAT"]
