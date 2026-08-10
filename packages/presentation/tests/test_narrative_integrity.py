"""Narrative integrity: the write-up never prints itself twice, and a
redacted superlative leaves a sentence rather than a hole.

Three regressions pinned here. A composed narrative repeated four caution
sentences verbatim, glued mid-word at the seam ("…has matured.prior month —
the true value…"), directly above its own note saying those sentences are not
printed twice. The superlative guard was right to fire over a truncated list
but deleted the answering sentence, so an answer to "who is my worst payer"
contained none of the words worst, highest or top. And the substitute that
replaced it was spliced into a NOUN slot, so a two-sentence premise verdict
landed as the object of "the largest movement is ___".
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
    spliced_sentence,
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
        """The invariant: whenever there is something to say about
        repetition, the emitted string differs from the original."""
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
        """On the opener WITH a comparison the leading finding is a ceiling
        over 13 entities, ranked first by its own delta. Replacing a
        redacted superlative with a false one is not a fix."""
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

# ---------------------------------------------------------------------------
# A substitution may replace a clause, never a noun slot
#
# regression: the certifiable statement that closed the superlative hole was
# put in the wrong grammatical place. The leading finding on the turn was the
# VERDICT on the question's premise, whose title is two sentences, and it went
# into the object slot of "the largest movement is ___".


#: The exact splice, as it rendered on screen after foldComposedDisclosures.
LIVE_SPLICE = (
    "Of the 8 payers measurable this window, the largest movement is Premise cannot be "
    "verified: You asked about an increase in denial rate. Ask again once the thinner side "
    "matures. (F2)."
)

#: The premise verdict's title, verbatim from the payload that produced it —
#: ``findings._build_premise_finding`` composes it as
#: ``f"Premise cannot be verified: {sentence}"``.
UNVERIFIABLE_TITLE = (
    "Premise cannot be verified: You asked about an increase in denial rate. Ask again "
    "once the thinner side matures."
)

def _unverifiable_premise(referent: str = "F2") -> FindingPayload:
    """The live finding, with the verdict carried as data the way it is."""
    return FindingPayload(
        referent=referent,
        title=UNVERIFIABLE_TITLE,
        statement=(
            "You asked about an increase in denial rate. Ask again once the thinner side "
            "matures. Nothing below may be called an increase or offered as evidence "
            "against it: the cells that follow are the composition of a movement this "
            "answer cannot certify."
        ),
        metric_ids=["denial_rate"],
        values=[
            FindingValue(name="denial_rate", value=0.295082),
            FindingValue(name="denial_rate__delta", value=0.219919),
            FindingValue(name="premise_holds", value=False),
            FindingValue(name="premise_unverifiable", value=True),
        ],
    )


def _payer_row() -> FindingPayload:
    return FindingPayload(
        referent="F3",
        title="State Medicaid MCO denial rate up 22.0 points vs prior month",
        statement="State Medicaid MCO.",
        metric_ids=["denial_rate"],
        values=[
            FindingValue(name="denial_rate", value=0.295082),
            FindingValue(name="denial_rate__delta", value=0.219919),
        ],
    )


class TestTheSpliceIsRecognisedAtAll:
    def test_the_live_sentence_is_a_splice(self) -> None:
        assert spliced_sentence(LIVE_SPLICE) is not None

    def test_the_copula_collision_is_named(self) -> None:
        """"…the largest movement **is Premise cannot** be verified…"."""
        offender = spliced_sentence(
            "Of the 8 payers measurable this window, the largest movement is Premise "
            "cannot be verified today."
        )
        assert offender is not None
        assert "is Premise cannot" in offender

    def test_a_stranded_citation_is_named(self) -> None:
        """The nested full stop's other residue: "…matures. (F2)."."""
        assert spliced_sentence("Ask again once the thinner side matures. (F2).") is not None

    def test_good_prose_is_left_alone(self) -> None:
        clean = (
            "State Medicaid MCO leads the measurable payers on denial rate at 29.5% (F1). "
            "The comparison covers 2026-07-01..2026-07-31 and the load reaches 2026-08-02 "
            "(F1). Four payers publish a ceiling rather than a figure."
        )
        assert spliced_sentence(clean) is None

    def test_the_verdict_is_fine_as_PROSE_and_only_broken_as_a_NAME(self) -> None:
        """Nothing is wrong with the verdict's own words — it is a finding
        title, and it reads. The defect is entirely the slot it was put in,
        which is why the fix is grammatical rather than editorial."""
        assert spliced_sentence(UNVERIFIABLE_TITLE) is None
        # …and appending a citation to it is already the defect, because the
        # title ends on a full stop of its own: "…matures. (F2)."
        assert spliced_sentence(f"{UNVERIFIABLE_TITLE} (F2).") is not None


class TestAnUnverifiablePremiseYieldsItsOwnSentence:
    def test_the_substitute_is_a_complete_sentence_not_a_name(self) -> None:
        facts = build_narrative_facts(
            findings=[_unverifiable_premise(), _payer_row()],
            header=_header(),
            disclosures=[TRUNCATED, BOUNDED],
        )
        assert facts.superlative_substitute is not None
        assert facts.superlative_substitute == (
            "The largest movement cannot be named: the premise itself is unverified (F2)."
        )

    def test_the_verdict_title_never_enters_the_noun_slot(self) -> None:
        facts = build_narrative_facts(
            findings=[_unverifiable_premise(), _payer_row()],
            header=_header(),
            disclosures=[TRUNCATED, BOUNDED],
        )
        assert facts.superlative_substitute is not None
        assert "the largest movement is" not in facts.superlative_substitute
        assert "Premise cannot be verified:" not in facts.superlative_substitute
        assert spliced_sentence(facts.superlative_substitute) is None

    def test_the_live_turn_reproduces_clean_end_to_end(self) -> None:
        """The turn as it happened: the composer reaches for a superlative
        over a truncated list, the guard fires, and what is published is a
        sentence a room can read out loud."""
        facts = build_narrative_facts(
            findings=[_unverifiable_premise(), _payer_row()],
            header=_header(),
            disclosures=[TRUNCATED, BOUNDED],
        )
        result = validate_narrative(
            "State Medicaid MCO is the worst payer on denial rate this window.", facts
        )
        assert result.redactions
        assert spliced_sentence(result.text) is None
        assert "cannot be named" in result.text
        assert "Premise cannot be verified" not in result.text

    def test_a_premise_that_WAS_verifiable_still_names_its_leader(self) -> None:
        """The substitute is not withdrawn — only the verdict is kept out of
        the noun slot. A confirmed premise ranks the rows underneath it."""
        confirmed = FindingPayload(
            referent="F1",
            title="Premise confirmed: denial rate rose 7.3 points vs prior month",
            statement="Premise confirmed.",
            metric_ids=["denial_rate"],
            values=[
                FindingValue(name="denial_rate__delta", value=0.073),
                FindingValue(name="premise_holds", value=True),
                FindingValue(name="premise_unverifiable", value=False),
            ],
        )
        facts = build_narrative_facts(
            findings=[confirmed, _payer_row()],
            header=_header(),
            disclosures=[TRUNCATED, BOUNDED],
        )
        assert facts.superlative_substitute is not None
        assert "State Medicaid MCO denial rate up 22.0 points" in facts.superlative_substitute
        assert "(F3)" in facts.superlative_substitute
        assert "Premise confirmed" not in facts.superlative_substitute
        assert spliced_sentence(facts.superlative_substitute) is None


class TestNoSubstituteThisModuleBuildsIsASplice:
    def test_every_shape_the_builder_can_reach_reads_as_one_sentence(self) -> None:
        leader = FindingPayload(
            referent="F1",
            title="State Medicaid MCO: 29.5% denial rate",
            statement="State Medicaid MCO leads.",
            metric_ids=["denial_rate"],
            values=[FindingValue(name="denial_rate", value=0.295082)],
        )
        for disclosures in ([TRUNCATED], [TRUNCATED, BOUNDED]):
            for findings in ([leader], [leader, _payer_row()]):
                facts = build_narrative_facts(
                    findings=findings, header=_header(), disclosures=disclosures
                )
                if facts.superlative_substitute is not None:
                    assert spliced_sentence(facts.superlative_substitute) is None
