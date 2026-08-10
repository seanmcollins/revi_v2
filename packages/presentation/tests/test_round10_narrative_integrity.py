"""Round-10 R10-1: a substitution may replace a clause, never a noun slot.

Wave G closed R9-09 by putting a certifiable statement where the superlative
guard had left a hole. It put it in the wrong grammatical place. On the
demo's SECOND question — the analyst clicking the product's own option
"Break the increase down by payer", live ``sess_323f4681e887`` turn 3 — the
served narrative read:

    Of the 8 payers measurable this window, the largest movement is Premise
    cannot be verified: You asked about an increase in denial rate. Ask
    again once the thinner side matures. (F2).

The leading finding on that turn was not a row. It was the turn's VERDICT on
the question's premise, whose title is two sentences, and it went into the
object slot of "the largest movement is ___".

Two invariants, both asserted here over the live payload:

* a premise the engine could not verify yields a complete sentence of its
  own, never a name in a noun slot;
* nothing this module emits trips :func:`spliced_sentence`.
"""

from __future__ import annotations

from datetime import date

from revi_investigation_contracts.api import FindingPayload, FindingValue
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_presentation.narrative import (
    build_narrative_facts,
    spliced_sentence,
    validate_narrative,
)

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

BOUNDED = (
    "4 of 12 payers here are too small to measure exactly — fewer than 11 of the events "
    "being counted landed in each, over a population this answer publishes in full — so "
    "each shows a ceiling instead of a figure: Veritas Comp Fund (denial rate ≤ 76.9% "
    "over 13 entities); Harborline Health Plan (denial rate ≤ 20.8% over 48 entities). "
    "The true value is at or below the ceiling and is not a measurement."
)
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
        """The R9-09 fix is not withdrawn — only the verdict is kept out of
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
