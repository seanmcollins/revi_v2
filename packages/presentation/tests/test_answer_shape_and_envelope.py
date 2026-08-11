"""The composer meets the question, and the disclosure envelope has a budget.

Two defects, one seam. The composer was never shown the utterance — it was
handed findings, a header, a caveat list and a reconciliation verdict — so
it wrote a summary of findings rather than an answer: six of six yes/no
questions in the live corpus came back without a yes or a no, and three of
three how-much questions came back without the total. And the envelope
around whatever it wrote averaged 150 words of engine prose per answer,
led on 12 of 26 by the same five-sentence settling paragraph.

Both fixes are bounded by one rule, asserted here as hard as the fixes
themselves: **nothing that bounds the EVIDENCE may be budgeted away.** A
suppression, a ceiling, an unranked block, a non-comparability, an
omission, a failed reconciliation and every premise verdict survive the
budget; only the sentences that bound the READING can be dropped, and they
stay on ``warnings_v2`` in full when they are.
"""

from __future__ import annotations

from datetime import date

from revi_investigation_contracts.api import FindingPayload, FindingValue
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_investigation_contracts.settings import NarrativeDepth
from revi_presentation import (
    OPERATOR_ONLY_DISCLOSURE_CODES,
    build_narrative_facts,
    build_narrative_prompt,
    mandatory_disclosures,
    operator_only_disclosures,
)

HEADER = ContextHeaderPayload(
    window_start=date(2026, 7, 1),
    window_end=date(2026, 7, 31),
    basis="service",
    watermark_id="wm_003",
    display="2026-07-01..2026-07-31 (service) · watermark wm_003",
)

F1 = FindingPayload(
    referent="F1",
    title="Total: $52,217.98 credit balance dollars (as of 2026-08-02)",
    statement="$52,217.98 credit balance dollars as of 2026-08-02, across the 9 payers.",
    values=[FindingValue(name="credit_balance_dollars", value=5221798)],
    grade="direct",
)

#: The live paragraph, in the shape the engine now composes it: everything
#: a reader must not miss is in sentence ONE.
SETTLING = (
    "adjudication_incomplete: only 26.3% of July 2026 has settled on the service date, so a "
    "total here is understated and a rate here is skewed. What HAS settled is not a random "
    "sample of what has not: the fastest cases reach a decision first. This whole load holds "
    "1,544 settled record(s) over a window of this length where it normally holds about 5,878."
)
TRUNCATED = (
    "findings_truncated: 3 of 144 computed cells are published as findings; the remaining 141 "
    "are on the chart and in the evidence but carry no finding."
)
PROBES = (
    "probe_families_empty: 8 metric famil(ies) on this plan were read and produced no "
    "published finding: denial_rate (portfolio_denial_trend, 1 row(s))."
)
SUPPRESSED = (
    "suppression_bounded: 24 of 30 plans are too small to measure exactly — for those only a "
    "ceiling is known."
)
VERDICT = (
    "verdict_lead: Yes — $52,217.98 of credit balance dollars as of 2026-08-02, across 9 "
    "payers."
)


class TestTheComposerIsToldWhatWasAsked:
    def test_the_question_and_its_shape_are_rendered_into_the_prompt(self) -> None:
        prompt = build_narrative_prompt(
            findings=[F1],
            header=HEADER,
            reconciliation=None,
            question="Do we owe any refunds right now?",
            answer_shape="verdict",
        )

        assert "Do we owe any refunds right now?" in prompt
        assert "YES/NO question" in prompt
        assert "ANSWER THE QUESTION IN YOUR FIRST SENTENCE" in prompt

    def test_every_shape_carries_an_instruction_and_so_does_none(self) -> None:
        """An unclassified turn is still answering something. A blank slot is
        what the composer had for as long as it was shown no question."""
        for shape in (
            "verdict", "entity", "scalar", "cause", "trend", "comparison",
            "definition", "worklist", None,
        ):
            prompt = build_narrative_prompt(
                findings=[F1],
                header=HEADER,
                reconciliation=None,
                depth=NarrativeDepth.ANALYST,
                question="How much did we write off last month?",
                answer_shape=shape,
            )
            assert "{shape_directive}" not in prompt
            assert "(the question was not recorded" not in prompt

    def test_a_turn_with_no_question_says_so_rather_than_leaving_a_hole(self) -> None:
        prompt = build_narrative_prompt(findings=[F1], header=HEADER, reconciliation=None)

        assert "(the question was not recorded for this turn)" in prompt

    def test_the_questions_own_names_are_certified_vocabulary(self) -> None:
        """The prompt DEMANDS a first sentence that answers the question, so
        the validator has to be willing to admit the analyst's own subject —
        a validator that redacted the sentence it just demanded would leave
        the answer without one."""
        facts = build_narrative_facts(
            findings=[F1],
            header=HEADER,
            question="Do we owe Atlas Commercial any refunds right now?",
        )

        assert "Atlas Commercial" in facts.allowed_names


class TestTheSettlingCaveatEarnsItsPlace:
    def test_it_leads_a_level_question_and_only_its_first_sentence_does(self) -> None:
        lead, trail = mandatory_disclosures(
            [("ADJUDICATION_INCOMPLETE", SETTLING)], answer_shape="scalar"
        )

        assert len(lead) == 1
        assert lead[0].startswith("Only 26.3% of July 2026 has settled")
        assert "fastest cases reach a decision first" not in " ".join(lead)
        assert trail == []

    def test_it_does_not_lead_a_standing_balance(self) -> None:
        """An unsettled window does not shrink a balance. This paragraph led
        "do we owe any refunds right now?" and the answer's own body then
        said the July framing does not bear on the amounts shown."""
        lead, trail = mandatory_disclosures(
            [("ADJUDICATION_INCOMPLETE", SETTLING)],
            settling_bears_on_headline=False,
            answer_shape="verdict",
        )

        assert lead == []
        assert any("has settled" in line for line in trail), "relocated, never removed"

    def test_it_does_not_lead_a_which_entity_question(self) -> None:
        lead, trail = mandatory_disclosures(
            [("ADJUDICATION_INCOMPLETE", SETTLING)], answer_shape="entity"
        )

        assert lead == []
        assert any("has settled" in line for line in trail)

    def test_a_verdict_outranks_it(self) -> None:
        """Only a refusal leads, and everything leading is the same as
        nothing leading."""
        lead, trail = mandatory_disclosures(
            [("VERDICT_LEAD", VERDICT), ("ADJUDICATION_INCOMPLETE", SETTLING)],
            answer_shape="scalar",
        )

        assert len(lead) == 1
        assert lead[0].startswith("Yes — $52,217.98")
        assert any("has settled" in line for line in trail)


class TestMachineryLeavesThePublishedProse:
    def test_the_probe_census_and_the_cell_census_are_not_in_the_trail(self) -> None:
        lead, trail = mandatory_disclosures(
            [("FINDINGS_TRUNCATED", TRUNCATED), ("PROBE_FAMILIES_EMPTY", PROBES)]
        )

        assert lead == []
        assert trail == []

    def test_they_are_still_published_for_the_fact_set_and_the_operator(self) -> None:
        stated = operator_only_disclosures(
            [("FINDINGS_TRUNCATED", TRUNCATED), ("PROBE_FAMILIES_EMPTY", PROBES)]
        )

        assert len(stated) == len(OPERATOR_ONLY_DISCLOSURE_CODES)
        assert any("published as findings" in line for line in stated)

    def test_the_truncation_fact_still_reaches_the_validator(self) -> None:
        """The prose cleanup must not loosen grounding: the superlative and
        spread rules fire off this flag."""
        facts = build_narrative_facts(
            findings=[F1],
            header=HEADER,
            disclosures=operator_only_disclosures([("FINDINGS_TRUNCATED", TRUNCATED)]),
        )

        assert facts.truncated is True


class TestTheBudgetNeverCostsAnHonestySentence:
    def test_a_suppression_note_survives_a_full_reading_budget(self) -> None:
        reading = [
            ("PARENT_LEVEL", "parent_level: the whole this is a part of runs at 12.8%."),
            ("WINDOW_RELATIVE", "window_relative: 'last month' resolved to July 2026."),
            ("WINDOW_HORIZON", "window_horizon: this load reaches 2026-08-02."),
            ("SNAPSHOT_AS_OF", "snapshot_as_of: this balance stands at 2026-08-02."),
            ("ADJUDICATION_INCOMPLETE", SETTLING),
        ]
        _, trail = mandatory_disclosures(
            [("SUPPRESSION_BOUNDED", SUPPRESSED), *reading], answer_shape="entity"
        )

        assert any("too small to measure exactly" in line for line in trail)
        budgeted = [line for line in trail if "too small to measure exactly" not in line]
        assert len(budgeted) == 3, "reading caveats are capped, evidence bounds are not"


class TestComposedDisclosuresGoThroughTheDisplayOverlay:
    def test_a_raw_metric_id_in_engine_prose_is_corrected(self) -> None:
        """The template forbids a raw id and the model obeys; the ids arrived
        in the ENGINE-composed sentences, which bypassed the overlay."""
        lead, _ = mandatory_disclosures(
            [
                (
                    "PREMISE_VERIFIED",
                    "premise_verified: timely_filing_at_risk_dollars rose 4.2%.",
                )
            ],
            metric_display={
                "timely_filing_at_risk_dollars": "unbilled open inventory on a filing clock"
            },
        )

        assert "timely_filing_at_risk_dollars" not in lead[0]
        assert "unbilled open inventory on a filing clock" in lead[0]
