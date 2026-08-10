"""Round-9 narrative fixes: the write-up may not print itself twice, and a
redacted superlative may not leave a hole where the answer was.

* **R9-04** (uiux P0, live session ``sess_8f6e08789df4``) — a 1,436-character
  narrative whose tail repeated four caution sentences verbatim, glued
  mid-word at the seam ("…has matured.prior month — the true value…"),
  directly above the product's own note saying those sentences "are not
  printed twice". Intermittent: a second identical run came back clean at
  2,370 characters, which is worse in a room.
* **R9-09** (product-designer P1, his #3 of "the three I'd fix before the
  room") — "Who is my worst payer on denial rate right now…" produced 402
  words in which the words worst, highest and top never appear. The
  superlative guard was right to fire over a truncated list; deleting the
  answering sentence was the wrong remedy.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from revi_investigation_contracts.api import FindingPayload, FindingValue
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_investigation_contracts.narrative import NarrativeFacts
from revi_presentation.narrative import (
    DOUBLED_SPAN_CHARS,
    DUPLICATE_SENTENCE_REASON,
    build_narrative_facts,
    compose_narrative,
    dedupe_sentences,
    doubled_span,
    split_sentences,
    validate_narrative,
)

#: The four caution sentences the live composer printed twice, in the
#: product's own words.
CAUTIONS = (
    "This load only reaches 2026-08-02, so August is a partial month and the figures "
    "below cover 2026-07-01..2026-07-31 (F1). "
    "Small-cell suppression withheld the true value of 4 cells on this answer, some of "
    "them published as upper bounds rather than dropped (F1). "
    "The comparison window is the prior month and its denial data has matured further "
    "than this one has matured. "
    "Prior month figures are settled and this month's are not (F1)."
)


class TestAWriteUpNeverPrintsItselfTwice:
    def test_the_unspaced_seam_is_a_sentence_boundary(self) -> None:
        """"matured.prior" proves raw concatenation: without the repair the
        deduper cannot even see two sentences there."""
        parts = split_sentences("The comparison has matured.prior month is settled.")
        assert parts == ["The comparison has matured.", "prior month is settled."]

    def test_an_abbreviation_is_still_not_a_sentence_end(self) -> None:
        assert split_sentences("Dr. Casey Quarry leads. e.g. this one.") == [
            "Dr. Casey Quarry leads.",
            "e.g. this one.",
        ]

    def test_the_doubled_tail_is_removed_not_described(self) -> None:
        emitted, dropped = dedupe_sentences(CAUTIONS + CAUTIONS)
        assert emitted == " ".join(split_sentences(CAUTIONS))
        assert len(dropped) == 4
        assert doubled_span(emitted) is None

    def test_it_survives_the_glued_seam(self) -> None:
        """The live string: the repeat begins with no space at all —
        "…has matured.prior month — the true value…"."""
        glued = CAUTIONS.strip() + CAUTIONS.strip().lstrip()
        assert doubled_span(glued) is not None
        emitted, dropped = dedupe_sentences(glued)
        assert doubled_span(emitted) is None
        assert len(dropped) == 4

    def test_the_note_can_only_be_composed_from_the_emitted_text(self) -> None:
        """The invariant behind R9-04: whenever there is something to say
        about repetition, the emitted string differs from the original."""
        original = CAUTIONS + CAUTIONS
        emitted, dropped = dedupe_sentences(original)
        assert bool(dropped) is (emitted != original.strip())

    def test_prose_that_repeats_nothing_is_left_exactly_alone(self) -> None:
        text = "Denial rate was 29.5% (F1). Denied dollars were $31,174.49 (F2)."
        emitted, dropped = dedupe_sentences(text)
        assert emitted == text
        assert dropped == []

    def test_the_guard_reads_the_final_bytes(self) -> None:
        repeated = "x" * DOUBLED_SPAN_CHARS
        assert doubled_span(f"{repeated}|{repeated}") is not None
        assert doubled_span("short enough to share a clause. and again a clause.") is None

    def test_the_validator_publishes_no_sentence_twice(self) -> None:
        facts = NarrativeFacts(referent_ids=["F1"], allowed_names=[], numeric_values=[])
        one = "The comparison window has matured further than this one has."
        result = validate_narrative(f"{one} {one}", facts)
        assert result.text == one
        assert [r.reason for r in result.redactions] == [DUPLICATE_SENTENCE_REASON]
        assert doubled_span(result.text) is None

    def test_the_composed_answer_says_its_caveats_once(self) -> None:
        """The composer is SHOWN the mandatory disclosures, so it restates
        them — and they are published around it either way."""
        lead = ["I could not build the payer scorecard."]
        trail = ["Small-cell suppression withheld the true value of 4 cells on this answer."]
        body = (
            "I could not build the payer scorecard. Denial rate was 29.5% (F1). "
            "Small-cell suppression withheld the true value of 4 cells on this answer."
        )
        narrative, repeated = compose_narrative(lead, body, trail)
        assert narrative.count("I could not build the payer scorecard.") == 1
        assert narrative.count("Small-cell suppression withheld") == 1
        assert narrative.startswith("I could not build the payer scorecard.")
        assert len(repeated) == 2
        assert doubled_span(narrative) is None


#: The engine's own census sentence for the demo opener, verbatim.
BOUNDED = (
    "4 of 12 payers here are too small to measure exactly — fewer than 11 of the events "
    "being counted landed in each, over a population this answer publishes in full — so "
    "each shows a ceiling instead of a figure: Veritas Comp Fund (denial rate ≤ 76.9% "
    "over 13 entities); Harborline Health Plan (denial rate ≤ 20.8% over 48 entities). "
    "The true value is at or below the ceiling and is not a measurement."
)
#: The engine's own truncation disclosure, recognised by the marker
#: ``build_narrative_facts`` reads.
TRUNCATED = (
    "3 of 12 payers computed are published as findings — the rows below are not the "
    "whole population."
)


def _header() -> ContextHeaderPayload:
    return ContextHeaderPayload(
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 31),
        basis="service",
        watermark_id="wm_003",
        display="2026-07-01..2026-07-31 (service) · watermark wm_003",
    )


def _leader() -> FindingPayload:
    return FindingPayload(
        referent="F1",
        title="State Medicaid MCO: 29.5% denial rate",
        statement="State Medicaid MCO leads the measurable payers on denial rate.",
        metric_ids=["denial_rate"],
        values=[FindingValue(name="denial_rate", value=0.295082)],
    )


class TestARedactedSuperlativeIsAnsweredNotDeleted:
    def test_the_substitute_names_the_leader_and_its_figure(self) -> None:
        facts = build_narrative_facts(
            findings=[_leader()], header=_header(), disclosures=[TRUNCATED, BOUNDED]
        )
        assert facts.superlative_substitute is not None
        assert "State Medicaid MCO: 29.5% denial rate" in facts.superlative_substitute
        assert "(F1)" in facts.superlative_substitute

    def test_it_says_why_it_is_not_the_superlative(self) -> None:
        facts = build_narrative_facts(
            findings=[_leader()], header=_header(), disclosures=[TRUNCATED, BOUNDED]
        )
        assert facts.superlative_substitute is not None
        assert "can't call it your worst outright" in facts.superlative_substitute
        # The census is the engine's, read rather than re-derived: 12 payers,
        # 4 of them ceilings, so 8 were measurable.
        assert "Of the 8 payers measurable this window" in facts.superlative_substitute
        assert "4 publish only a ceiling" in facts.superlative_substitute

    def test_it_names_the_ceiling_that_could_overturn_the_ranking(self) -> None:
        facts = build_narrative_facts(
            findings=[_leader()], header=_header(), disclosures=[TRUNCATED, BOUNDED]
        )
        assert facts.superlative_substitute is not None
        assert "Veritas Comp Fund's (≤76.9%) sits above it" in facts.superlative_substitute

    def test_a_ceiling_below_the_leader_is_not_claimed_to_sit_above_it(self) -> None:
        below = BOUNDED.replace("≤ 76.9%", "≤ 6.9%").replace("≤ 20.8%", "≤ 2.8%")
        facts = build_narrative_facts(
            findings=[_leader()], header=_header(), disclosures=[TRUNCATED, below]
        )
        assert facts.superlative_substitute is not None
        assert "sits above it" not in facts.superlative_substitute
        assert "a ceiling can sit above a measured figure" in facts.superlative_substitute

    def test_the_answer_names_the_highest_measurable_payer(self) -> None:
        """The opener, end to end: the guard fires and the reader still gets
        an answer to the question they asked."""
        facts = build_narrative_facts(
            findings=[_leader()], header=_header(), disclosures=[TRUNCATED, BOUNDED]
        )
        result = validate_narrative(
            "State Medicaid MCO is your worst payer on denial rate.", facts
        )
        assert result.redactions
        assert "the largest figure is" in result.text
        assert "State Medicaid MCO" in result.text
        assert "29.5%" in result.text

    def test_one_substitute_however_many_superlatives(self) -> None:
        facts = build_narrative_facts(
            findings=[_leader()], header=_header(), disclosures=[TRUNCATED, BOUNDED]
        )
        result = validate_narrative(
            "State Medicaid MCO is worst. Atlas Commercial is the best. "
            "Summit Peak is the lowest.",
            facts,
        )
        assert result.text.count("the largest figure is") == 1

    def test_without_a_census_it_degrades_to_words_rather_than_inventing_any(self) -> None:
        facts = build_narrative_facts(
            findings=[_leader()], header=_header(), disclosures=[TRUNCATED]
        )
        assert facts.superlative_substitute is not None
        assert "The largest figure this answer measured is" in facts.superlative_substitute
        assert "Of the" not in facts.superlative_substitute

    def test_an_answer_with_no_leader_has_nothing_to_substitute(self) -> None:
        facts = build_narrative_facts(findings=[], header=_header(), disclosures=[TRUNCATED])
        assert facts.superlative_substitute is None
        assert Decimal("1") == Decimal(1)  # keeps the Decimal import honest

    def test_a_ceiling_is_never_named_as_the_highest_measured_figure(self) -> None:
        """Live, on the opener WITH a comparison, F1 is "Veritas Comp Fund
        denial rate at most ≤ 76.9%" — a ceiling over 13 entities, ranked
        first by its own delta. Replacing a redacted superlative with a
        false one is not a fix."""
        ceiling = FindingPayload(
            referent="F1",
            title="Veritas Comp Fund denial rate at most ≤ 76.9% vs prior month",
            statement="Veritas Comp Fund.",
            metric_ids=["denial_rate"],
            values=[
                FindingValue(name="denial_rate", value=0.769231),
                FindingValue(name="denial_rate__delta", value=0.679141),
                FindingValue(name="denial_rate__is_bound", value=True),
            ],
        )
        measured = FindingPayload(
            referent="F2",
            title="State Medicaid MCO denial rate up 22.0 points vs prior month",
            statement="State Medicaid MCO.",
            metric_ids=["denial_rate"],
            values=[
                FindingValue(name="denial_rate", value=0.295082),
                FindingValue(name="denial_rate__delta", value=0.219919),
            ],
        )
        facts = build_narrative_facts(
            findings=[ceiling, measured], header=_header(), disclosures=[TRUNCATED, BOUNDED]
        )
        assert facts.superlative_substitute is not None
        assert "(F2)" in facts.superlative_substitute
        assert "Veritas Comp Fund denial rate at most" not in facts.superlative_substitute
        # …and a compared turn ranks MOVEMENTS, so it does not claim a level.
        assert "the largest movement is" in facts.superlative_substitute

    def test_an_answer_of_nothing_but_ceilings_has_no_leader_to_name(self) -> None:
        ceiling = FindingPayload(
            referent="F1",
            title="Veritas Comp Fund denial rate at most ≤ 76.9%",
            statement="Veritas Comp Fund.",
            metric_ids=["denial_rate"],
            values=[FindingValue(name="denial_rate__is_bound", value=True)],
        )
        facts = build_narrative_facts(
            findings=[ceiling], header=_header(), disclosures=[TRUNCATED, BOUNDED]
        )
        assert facts.superlative_substitute is None
