"""Narrative grounding: prompt composition, fact extraction, validation
with redaction of unverifiable sentences."""

from __future__ import annotations

from datetime import date

import pytest

from revi_investigation_contracts.api import FindingPayload, FindingValue
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_investigation_contracts.settings import NarrativeDepth
from revi_presentation import (
    REDACTION_NOTE,
    REDACTION_WARNING_PREFIX,
    build_narrative_facts,
    build_narrative_prompt,
    empty_narrative,
    mandatory_disclosures,
    reconciliation_disclosure,
    recovered_code,
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


#: The real overlay entry from packs/base-rcm/metric_display.yaml.
TF_METRIC = "timely_filing_at_risk_dollars"
TF_DISPLAY = "Unbilled open inventory (timely-filing watch proxy)"
TF_CAVEAT = (
    "Deadline proximity from filing_rules is NOT yet applied; this is total "
    "unbilled open inventory, an upper bound on filing exposure rather than a "
    "measure of it."
)
TF_FINDING = FindingPayload(
    referent="F1",
    title="State Medicaid HMO: $2,349,692.17 timely filing at risk dollars",
    statement="State Medicaid HMO ranks #1 by timely filing at risk dollars.",
    metric_ids=[TF_METRIC],
    values=[FindingValue(name=TF_METRIC, value=234969217)],
    grade="direct",
)


class TestCaveatsAndDisplayNames:
    """The composer reproduced the overclaim its own metric id makes —
    "the largest timely filing exposure" over a number that measures
    unbilled inventory — because the caveat correcting it was never in
    the prompt."""

    def test_caveats_are_rendered_as_governing_constraints(self) -> None:
        prompt = build_narrative_prompt(
            findings=[TF_FINDING], header=HEADER, reconciliation=None, caveats=[TF_CAVEAT]
        )
        assert TF_CAVEAT in prompt
        # Not background: the heading has to say they bind the writing.
        assert "govern how the figures may be characterized" in prompt
        assert "do not claim more than they allow" in prompt

    def test_no_caveats_still_renders_a_placeholder(self) -> None:
        prompt = build_narrative_prompt(
            findings=[TF_FINDING], header=HEADER, reconciliation=None
        )
        assert "(none on this turn)" in prompt

    @pytest.mark.parametrize("depth", list(NarrativeDepth))
    def test_both_depths_carry_the_caveat_and_its_instruction(
        self, depth: NarrativeDepth
    ) -> None:
        """Depth changes the writing asked for, never the honesty required."""
        prompt = build_narrative_prompt(
            findings=[TF_FINDING],
            header=HEADER,
            reconciliation=None,
            caveats=[TF_CAVEAT],
            depth=depth,
        )
        assert TF_CAVEAT in prompt
        assert "govern how the figures may be characterized" in prompt
        assert "Never use a raw metric id" in prompt

    def test_display_name_replaces_both_spellings_of_the_id(self) -> None:
        """Findings carry the id humanized in titles and raw in value
        names; the prompt must not show either."""
        prompt = build_narrative_prompt(
            findings=[TF_FINDING],
            header=HEADER,
            reconciliation=None,
            metric_display={TF_METRIC: TF_DISPLAY},
        )
        assert TF_DISPLAY in prompt
        assert TF_METRIC not in prompt  # raw id
        assert "timely filing at risk dollars" not in prompt  # humanized id

    def test_display_names_are_left_alone_when_not_supplied(self) -> None:
        prompt = build_narrative_prompt(
            findings=[TF_FINDING], header=HEADER, reconciliation=None
        )
        assert "timely filing at risk dollars" in prompt

    def test_quoting_a_caveat_is_never_redacted(self) -> None:
        """The prompt demands the caveat; the validator must accept it."""
        facts = build_narrative_facts(
            findings=[TF_FINDING],
            header=HEADER,
            caveats=[TF_CAVEAT],
            metric_display={TF_METRIC: TF_DISPLAY},
        )
        validation = validate_narrative(
            "State Medicaid HMO holds $2,349,692.17 of unbilled open inventory (F1). "
            "Deadline proximity from filing_rules is NOT yet applied; this is an "
            "upper bound on filing exposure rather than a measure of it.",
            facts,
        )
        assert validation.clean, validation.warnings

    def test_caveat_figures_are_admitted_but_only_when_supplied(self) -> None:
        """Admission is scoped to this turn's caveats, like benchmarks."""
        caveat = "Only 22.9% of July claims had adjudicated by the watermark."
        text = "Completeness was 22.9% (F1)."
        assert validate_narrative(
            text, build_narrative_facts(findings=[TF_FINDING], header=HEADER, caveats=[caveat])
        ).clean
        assert not validate_narrative(
            text, build_narrative_facts(findings=[TF_FINDING], header=HEADER)
        ).clean


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

    def test_invented_number_is_dropped_not_marked(self) -> None:
        text = "State Medicaid lost $777,777 last week (F1). The rest held steady."
        validation = validate_narrative(
            text, build_narrative_facts(findings=[F1, F2], header=HEADER)
        )
        assert not validation.clean
        # The offending sentence leaves the prose entirely — a customer
        # never reads a redaction marker (F6).
        assert REDACTION_NOTE not in validation.text
        assert "$777,777" not in validation.text
        assert validation.text == "The rest held steady."  # innocent prose kept
        assert any("$777,777" in r.reason for r in validation.redactions)

    def test_redactions_surface_as_one_aggregate_warning(self) -> None:
        text = (
            "State Medicaid lost $777,777 last week (F1). "
            "Atlas Commercial lost $888,888 (F2). "
            "Beacon Health drove it (F1). "
            "The rest held steady."
        )
        validation = validate_narrative(
            text, build_narrative_facts(findings=[F1, F2], header=HEADER)
        )
        assert len(validation.redactions) == 3
        assert len(validation.warnings) == 1  # one operator signal, not three
        warning = validation.warnings[0]
        assert warning.startswith(REDACTION_WARNING_PREFIX)
        assert "3 sentence(s) dropped" in warning
        assert "Beacon Health" in warning  # the reasons ride along
        assert validation.text == "The rest held steady."

    def test_wholly_ungrounded_narrative_yields_empty_text(self) -> None:
        validation = validate_narrative(
            "Beacon Health lost $777,777 (F9).",
            build_narrative_facts(findings=[F1], header=HEADER),
        )
        assert validation.text == ""  # nothing survived; nothing is invented
        assert validation.redactions and validation.warnings

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


class TestCertifiedContentIsAdmitted:
    """The other half of F6: the validator redacting its own evidence.

    Each case below was observed live, in prose whose every claim came out
    of the finding it cites.
    """

    def test_entity_sub_span_of_a_finding_title_is_admitted(self) -> None:
        """F2's title *is* "Summit Peak Medicare Advantage"; saying
        "Summit Peak" was redacted as outside the vocabulary."""
        finding = FindingPayload(
            referent="F2",
            title="Summit Peak Medicare Advantage: 12.4% denial rate",
            statement="Summit Peak Medicare Advantage denied 12.4% of claims.",
            values=[FindingValue(name="denial_rate", value=0.124)],
            grade="direct",
        )
        facts = build_narrative_facts(findings=[finding], header=HEADER)
        for text in (
            "Summit Peak leads at 12.4% (F2).",
            "Medicare Advantage leads at 12.4% (F2).",
            "The Summit Peak Medicare Advantage plan leads at 12.4% (F2).",
        ):
            validation = validate_narrative(text, facts)
            assert validation.clean, (text, validation.warnings)
            assert validation.text == text

    def test_ordinary_date_phrases_are_not_entities(self) -> None:
        """"For July" / "The July" are English, not payers."""
        facts = build_narrative_facts(findings=[F1], header=HEADER)
        for text in (
            "For July, cash posted fell $99,093 (F1).",
            "The July decline reached $99,093 (F1).",
            "Since March the trend held at $99,093 (F1).",
        ):
            validation = validate_narrative(text, facts)
            assert validation.clean, (text, validation.warnings)

    def test_benchmark_values_and_labels_are_admitted(self) -> None:
        """Benchmark lines go into the prompt, so the model quotes them;
        they must therefore be in the fact set.

        The values here are deliberately non-integral: an integral figure
        under 2100 already passed through the "reads as a count" escape
        hatch, so a benchmark like ``5.0-9.0`` would prove nothing.
        """
        line = (
            "denial_rate: 5.4-8.7 pct "
            "(Medicare Advantage, national; 2024; MGMA; machine_researched)"
        )
        facts = build_narrative_facts(findings=[F1], header=HEADER, benchmarks=[line])
        validation = validate_narrative(
            "That sits above the 5.4-8.7 pct Medicare Advantage range (F1).", facts
        )
        assert validation.clean, validation.warnings

    def test_a_benchmark_that_was_never_shown_is_still_blocked(self) -> None:
        """Admission is scoped to the lines this turn actually rendered."""
        facts = build_narrative_facts(findings=[F1], header=HEADER, benchmarks=[])
        validation = validate_narrative("That sits above the 5.4-8.7 pct range (F1).", facts)
        assert not validation.clean
        assert "5.4" in validation.redactions[0].reason

    def test_invented_entity_is_still_blocked_alongside_a_certified_one(self) -> None:
        """Admission is by containment, not by looseness: a name that is
        not inside any certified name still fails."""
        finding = FindingPayload(
            referent="F2",
            title="Summit Peak Medicare Advantage: 12.4% denial rate",
            statement="Summit Peak Medicare Advantage denied 12.4% of claims.",
            values=[FindingValue(name="denial_rate", value=0.124)],
            grade="direct",
        )
        facts = build_narrative_facts(findings=[finding], header=HEADER)
        validation = validate_narrative("Peak Summit leads at 12.4% (F2).", facts)
        assert not validation.clean  # reversed order is not a contiguous run
        validation = validate_narrative("Beacon Health leads at 12.4% (F2).", facts)
        assert "Beacon Health" in validation.redactions[0].reason

    def test_a_grammar_word_cannot_smuggle_an_invented_entity(self) -> None:
        validation = validate_narrative(
            "For Beacon Health the decline was $99,093 (F1).",
            build_narrative_facts(findings=[F1], header=HEADER),
        )
        assert not validation.clean
        assert "Beacon Health" in validation.redactions[0].reason


# ---------------------------------------------------------------------------
# round-2 FN-3: the narrative consumes the warnings the same answer carries


def test_the_refusal_leads_and_the_caveats_trail() -> None:
    """DIRECTION_UNMATCHED fired correctly and the prose opened "three
    payers show denial rates rising" and never said nothing improved. A
    refusal cannot sit beneath the movement that was found instead."""
    lead, trail = mandatory_disclosures(
        [
            ("SUPPRESSION_APPLIED", "suppression: cells counting fewer than 11 are suppressed"),
            (
                "DIRECTION_UNMATCHED",
                "direction_unmatched: nothing fell — no cell's denial rate moved the way "
                "'improved' asks about over this window",
            ),
            ("POPULATION_CAVEAT", "population_caveat: something that is not mandatory here"),
        ],
        suppressed_cells=4,
        total_cells=12,
    )
    assert len(lead) == 1
    assert lead[0].startswith("Nothing fell")
    assert any("4 of 12 cells" in sentence for sentence in trail)
    # Not every caution is a mandatory disclosure — only the ones that say
    # whether the answer answers the question or bound how it may be read.
    assert not any("not mandatory" in sentence for sentence in (*lead, *trail))


def test_a_reconciliation_strip_is_stated_in_words() -> None:
    """The strip read "diverged; card=$178,216.82; answer=$195,873.92" while
    the prose beneath it said reconciliation was not performed."""
    sentence = reconciliation_disclosure(
        status="diverged",
        card_cents=17_821_682,
        answer_cents=19_587_392,
        delta_cents=1_765_710,
        delta_fraction=0.0991,
    )
    assert "$178,216.82" in sentence and "$195,873.92" in sentence
    assert "$17,657.10" in sentence and "+9.9%" in sentence


def test_an_empty_turn_says_why_instead_of_publishing_null() -> None:
    text = empty_narrative(
        [("EMPTY_RESULT", "empty_result: every probe returned zero rows")]
    )
    assert text is not None
    assert "no finding" in text and "every probe returned zero rows" in text.lower()
    assert empty_narrative([("POPULATION_CAVEAT", "population_caveat: unrelated")]) is None


class TestBoundedSuppressionDisclosure:
    """Round-3 FN-1: what the §15 policy bounded rather than dropped.

    Once a numerator under the threshold is published as an upper bound, the
    old sentence — "every figure here describes only the cells that survived
    it" — is a story about censorship told over a fixed one, and it hides
    the fact the reader actually needs: which figures are ceilings.
    """

    def test_a_bounded_answer_says_the_figure_is_a_ceiling(self) -> None:
        lead, trail = mandatory_disclosures(
            [
                ("SUPPRESSION_APPLIED", "suppression: cells counting fewer than 11 entities"),
                (
                    "SUPPRESSION_BOUNDED",
                    "suppression_bounded: 1 cell had fewer than 11 entities in the numerator",
                ),
            ],
            suppressed_cells=1,
            total_cells=12,
        )
        assert lead == []
        joined = " ".join(trail)
        assert "upper bounds rather than dropped" in joined
        assert "only the cells that survived" not in joined
        # …and the engine's own sentence is published too, not summarized away
        assert any("fewer than 11 entities in the numerator" in s for s in trail)

    def test_a_fully_suppressed_answer_keeps_the_original_reading(self) -> None:
        _, trail = mandatory_disclosures(
            [("SUPPRESSION_APPLIED", "suppression: cells counting fewer than 11 entities")],
            suppressed_cells=4,
            total_cells=12,
        )
        assert any("only the cells that survived it" in s for s in trail)

    def test_a_mandatory_family_the_api_has_no_rule_for_still_leads(self) -> None:
        """New engine warning families ship before the API's table learns
        their names. A mandatory disclosure must not be demoted out of the
        lead by that gap — the engine's own prefix identifies it."""
        lead, _ = mandatory_disclosures(
            [("UNCLASSIFIED", "premise_false: denied dollars fell $48,068.30 vs prior period")]
        )
        assert lead and lead[0].startswith("Denied dollars fell")

    def test_the_recovery_never_invents_a_family(self) -> None:
        assert recovered_code("UNCLASSIFIED", "some_new_thing: happened") == "UNCLASSIFIED"
        assert recovered_code("POPULATION_CAVEAT", "premise_false: x") == "POPULATION_CAVEAT"
