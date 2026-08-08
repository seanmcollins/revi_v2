"""Narrative grounding: prompt composition, fact extraction, validation
with redaction of unverifiable sentences."""

from __future__ import annotations

from datetime import date

from revi_investigation_contracts.api import FindingPayload, FindingValue
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_presentation import (
    REDACTION_NOTE,
    build_narrative_facts,
    build_narrative_prompt,
    validate_narrative,
)

HEADER = ContextHeaderPayload(
    window_start=date(2026, 7, 27),
    window_end=date(2026, 8, 2),
    basis="post",
    comparison_kind="prior_period",
    comparison_start=date(2026, 7, 20),
    comparison_end=date(2026, 7, 26),
    watermark_id="wm_003",
    display="2026-07-27..2026-08-02 (post) · vs 2026-07-20..2026-07-26 · watermark wm_003",
)

F1 = FindingPayload(
    referent="F1",
    title="State Medicaid cash posted down $99,093 vs prior week",
    statement="State Medicaid: cash posted moved from 18722151 to 8812843 cents.",
    values=[
        FindingValue(name="current_cents", value=8812843),
        FindingValue(name="prior_cents", value=18722151),
        FindingValue(name="delta_cents", value=-9909308),
        FindingValue(name="pct_change", value=-0.529283),
    ],
    grade="direct",
)
F2 = FindingPayload(
    referent="F2",
    title="Atlas Commercial cash posted down $48,940 vs prior week",
    statement="Atlas Commercial: cash posted moved from 31224527 to 26330486 cents.",
    values=[
        FindingValue(name="current_cents", value=26330486),
        FindingValue(name="prior_cents", value=31224527),
        FindingValue(name="delta_cents", value=-4894041),
        FindingValue(name="pct_change", value=-0.156737),
    ],
    grade="direct",
)


class TestPromptAndFacts:
    def test_prompt_carries_only_certified_material(self) -> None:
        prompt = build_narrative_prompt(
            findings=[F1, F2], header=HEADER, reconciliation="status=passed"
        )
        assert "F1: State Medicaid cash posted down" in prompt
        assert HEADER.display in prompt
        assert "status=passed" in prompt
        assert "(none provided)" in prompt  # benchmarks placeholder

    def test_facts_extract_numbers_names_referents(self) -> None:
        facts = build_narrative_facts(findings=[F1, F2], header=HEADER)
        assert facts.referent_ids == ["F1", "F2"]
        assert "State Medicaid" in facts.allowed_names
        assert "Atlas Commercial" in facts.allowed_names
        assert any(abs(v) == 9909308 for v in facts.numeric_values)


class TestValidation:
    def test_grounded_narrative_passes_with_formatted_variants(self) -> None:
        text = (
            "Cash posted fell $99,093 at State Medicaid (F1). "
            "Atlas Commercial (F2) declined -15.7% against the prior week. "
            "Together they drive most of the decline."
        )
        validation = validate_narrative(
            text, build_narrative_facts(findings=[F1, F2], header=HEADER)
        )
        assert validation.clean, validation.warnings
        assert validation.text == text

    def test_invented_number_is_redacted(self) -> None:
        text = "State Medicaid lost $777,777 last week (F1). The rest held steady."
        validation = validate_narrative(
            text, build_narrative_facts(findings=[F1, F2], header=HEADER)
        )
        assert not validation.clean
        assert REDACTION_NOTE in validation.text
        assert "The rest held steady." in validation.text  # innocent prose kept
        assert any("$777,777" in r.reason for r in validation.redactions)

    def test_uncited_figures_are_redacted(self) -> None:
        text = "Cash fell $99,093 in the decline week."  # true number, no referent
        validation = validate_narrative(
            text, build_narrative_facts(findings=[F1], header=HEADER)
        )
        assert not validation.clean
        assert "without citing a referent" in validation.redactions[0].reason

    def test_unknown_referent_is_redacted(self) -> None:
        text = "The spike traces to F9."
        validation = validate_narrative(
            text, build_narrative_facts(findings=[F1], header=HEADER)
        )
        assert not validation.clean
        assert "unknown referent" in validation.redactions[0].reason

    def test_invented_payer_name_is_redacted(self) -> None:
        text = "Beacon Health drove the decline (F1)."
        validation = validate_narrative(
            text, build_narrative_facts(findings=[F1], header=HEADER)
        )
        assert not validation.clean
        assert "Beacon Health" in validation.redactions[0].reason

    def test_dates_and_small_counts_are_not_claims(self) -> None:
        text = "Between 2026-07-27 and 2026-08-02, 3 payers stand out (F1)."
        validation = validate_narrative(
            text, build_narrative_facts(findings=[F1], header=HEADER)
        )
        assert validation.clean, validation.warnings
