"""Round-4 R4-15 (shredded prose), R4-03 (provisional marks), R4-02
(charts of a refused ranking), R4-05 (no "roughly doubled" after a
short-of verdict).

All four are the same class of defect: a fact the engine already computed
that never reached the surface a reader looks at.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_investigation_contracts.api import FindingPayload, FindingValue
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_investigation_contracts.narrative import NarrativeFacts
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_presentation import (
    build_chart_spec,
    build_narrative_facts,
    ends_on_abbreviation,
    provisional_bucket,
    split_sentences,
    validate_narrative,
)

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)

HEADER = ContextHeaderPayload(
    window_start=date(2026, 7, 1),
    window_end=date(2026, 7, 31),
    basis="remit",
    comparison_kind="prior_period",
    comparison_start=date(2026, 6, 1),
    comparison_end=date(2026, 6, 30),
    watermark_id="wm_003",
    display="2026-07-01..2026-07-31 (remit) · watermark wm_003",
)


# ------------------------------------------------------------------ R4-15


class TestSentenceSplittingKeepsAbbreviations:
    """Every provider in this warehouse is named "Dr. X", so every
    provider-dimension answer that triggered a redaction shipped shredded
    prose: "…over a population of 11 entities (F1), Dr." was published as a
    sentence, and the orphan "Indigo Mossberg (019) describe only
    adjudicated claims (F5, F6, F7)." as another."""

    def test_the_live_shredded_sentence_stays_whole(self) -> None:
        text = (
            "Three providers carry ceilings rather than measurements, the loosest of which "
            "has a denial rate of at most 90.9% over a population of 11 entities (F1), "
            "Dr. Casey Quarry. Each of these three carries a direct evidence grade."
        )

        sentences = split_sentences(text)

        assert len(sentences) == 2
        assert sentences[0].endswith("Dr. Casey Quarry.")
        assert sentences[1].startswith("Each of these three")

    @pytest.mark.parametrize(
        "text",
        [
            "The comparison is undetermined for Dr. Indigo Mossberg (019).",
            "Figures come from Acme Health Inc. and are direct.",
            "The record is No. 14 in the register.",
            "Compare Atlas vs. Meridian on the same basis.",
            "The initial is Casey Q. Quarry, a rendering provider.",
        ],
    )
    def test_one_sentence_stays_one_sentence(self, text: str) -> None:
        assert split_sentences(text) == [text]

    def test_real_sentence_boundaries_still_split(self) -> None:
        text = "Denials rose 4.2% (F1). Cash fell $99,093 (F2). Both are direct."
        assert len(split_sentences(text)) == 3

    def test_no_emitted_sentence_terminates_on_an_abbreviation(self) -> None:
        text = (
            "Dr. Casey Quarry is at most 90.9% (F1). The population is 11 entities, "
            "per Dr. Indigo Mossberg (F2)."
        )
        for sentence in split_sentences(text):
            assert not ends_on_abbreviation(sentence), sentence

    def test_the_validator_no_longer_shreds_provider_prose(self) -> None:
        """End to end: the fragment "…(F1), Dr." used to validate on its own
        and the rest of the name became a separate, ungrounded sentence."""
        facts = NarrativeFacts(
            referent_ids=["F1"],
            numeric_values=[Decimal("0.909"), Decimal(11)],
            allowed_names=["Casey Quarry"],
        )
        text = (
            "The loosest ceiling is 90.9% over a population of 11 entities (F1), "
            "Dr. Casey Quarry."
        )

        validation = validate_narrative(text, facts)

        assert validation.clean, validation.redactions
        assert "Dr. Casey Quarry" in validation.text
        assert not validation.text.endswith("Dr.")


# ------------------------------------------------------------------ R4-05


def _premise_finding(magnitude: str) -> FindingPayload:
    return FindingPayload(
        referent="F1",
        title="Premise partly supported: it did not double",
        statement="denial rate rose 72.6%, short of the 100.0% a doubling assumes.",
        values=[
            FindingValue(name="premise_holds", value=False),
            FindingValue(name="premise_magnitude", value=magnitude),
            FindingValue(name="premise_asserted_verb", value="double"),
        ],
        grade="direct",
        confidence="high",
    )


class TestShortOfVerdictForbidsTheClaim:
    """R4-05. The verdict says "it did not double"; nothing stopped the
    composer writing "denials roughly doubled" in the next paragraph."""

    def test_the_forbidden_word_is_read_off_the_finding(self) -> None:
        facts = build_narrative_facts(findings=[_premise_finding("short")], header=HEADER)
        assert facts.forbidden_magnitude_claims == ["double"]

    def test_a_confirmed_premise_forbids_nothing(self) -> None:
        facts = build_narrative_facts(findings=[_premise_finding("within")], header=HEADER)
        assert facts.forbidden_magnitude_claims == []

    def test_an_affirmative_doubling_claim_is_dropped(self) -> None:
        facts = NarrativeFacts(
            referent_ids=["F1"], forbidden_magnitude_claims=["double"]
        )

        validation = validate_narrative("Denials roughly doubled this period (F1).", facts)

        assert validation.text == ""
        assert "premise verdict" in validation.redactions[0].reason

    def test_reporting_the_verdict_is_still_allowed(self) -> None:
        facts = NarrativeFacts(
            referent_ids=["F1"], forbidden_magnitude_claims=["double"]
        )

        validation = validate_narrative("Denials did not double this period (F1).", facts)

        assert validation.clean
        assert "did not double" in validation.text


# ------------------------------------------------------------------ R4-03


def _series(rows: tuple[tuple[object, ...], ...]) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("week", DimensionRef("time_bucket:week")),
                FrameColumn("denial_rate", MetricRef("denial_rate"), 2, "ratio"),
                FrameColumn("denial_rate__num", MetricRef("denial_rate"), 2, "count"),
                FrameColumn("denial_rate__den", MetricRef("denial_rate"), 2, "count"),
            )
        ),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


CENSORED_SERIES = (
    ("2026-06-29", Decimal("0.101"), 610, 6049),
    ("2026-07-06", Decimal("0.104"), 638, 6133),
    ("2026-07-13", Decimal("0.108"), 618, 5723),
    ("2026-07-20", Decimal("0.667"), 10, 15),  # 0.2% of the median panel
)


class TestProvisionalReachesTheWire:
    """R4-03. Across 15 turns and 1,046 chart rows, ``provisional`` was
    true zero times — while a finding title said "the week of 2026-07-20
    point (66.7%) is PROVISIONAL and is excluded from that movement" and
    the SVG drew a solid line straight up to it."""

    def test_the_censored_bucket_is_derived_from_the_frame(self) -> None:
        assert provisional_bucket(_series(CENSORED_SERIES), "denial_rate") == "2026-07-20"

    def test_a_settled_series_marks_nothing(self) -> None:
        settled = (
            ("2026-06-29", Decimal("0.101"), 610, 6049),
            ("2026-07-06", Decimal("0.104"), 638, 6133),
            ("2026-07-13", Decimal("0.108"), 618, 5723),
            ("2026-07-20", Decimal("0.110"), 640, 5900),
        )
        assert provisional_bucket(_series(settled), "denial_rate") is None

    def test_the_chart_row_carries_it(self) -> None:
        spec = build_chart_spec("main", _series(CENSORED_SERIES), suppression_threshold=11)

        assert spec is not None
        provisional = [row for row in spec.rows if row.provisional]
        assert [row.x for row in provisional] == ["2026-07-20"]
        assert sum(1 for row in spec.rows if not row.provisional) == 3

    def test_an_explicit_bucket_still_wins(self) -> None:
        spec = build_chart_spec(
            "main", _series(CENSORED_SERIES), provisional_x="2026-07-13"
        )
        assert spec is not None
        assert [row.x for row in spec.rows if row.provisional] == ["2026-07-13"]


# ------------------------------------------------------------------ R4-02


class TestRefusedRankingsAreAnnotated:
    """R4-02. The answer refused to rank and the chart 400px below it was
    captioned "ordered by denial_rate, high to low" over 52 ceilings."""

    def _providers(self, rows: tuple[tuple[object, ...], ...]) -> EvidenceFrame:
        return EvidenceFrame(
            schema=FrameSchema(
                (
                    FrameColumn("provider", DimensionRef("provider")),
                    FrameColumn("denial_rate", MetricRef("denial_rate"), 2, "ratio"),
                    FrameColumn("denial_rate__num", MetricRef("denial_rate"), 2, "count"),
                    FrameColumn("denial_rate__den", MetricRef("denial_rate"), 2, "count"),
                )
            ),
            rows=rows,  # type: ignore[arg-type]
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )

    def test_a_mostly_bounded_chart_says_it_is_not_ranked(self) -> None:
        frame = self._providers(
            (
                ("Dr. Alder", Decimal("0.909"), 10, 11),
                ("Dr. Birch", Decimal("0.909"), 10, 11),
                ("Dr. Cedar", Decimal("0.555"), 10, 18),
                ("Dr. Dogwood", Decimal("0.100"), 20, 200),
                ("Dr. Elm", None, None, None),
            )
        )

        spec = build_chart_spec(
            "main", frame, suppression_threshold=11, sort=("denial_rate", True)
        )

        assert spec is not None
        refusal = [a for a in spec.annotations if a.startswith("ranking_refused:")]
        assert refusal, spec.annotations
        assert "3 of the 4 publishable marks" in refusal[0]
        assert "leaving 1 measured" in refusal[0]

    def test_a_mostly_measured_chart_is_left_alone(self) -> None:
        frame = self._providers(
            (
                ("Dr. Alder", Decimal("0.909"), 10, 11),
                ("Dr. Birch", Decimal("0.150"), 30, 200),
                ("Dr. Cedar", Decimal("0.100"), 20, 200),
            )
        )

        spec = build_chart_spec("main", frame, suppression_threshold=11)

        assert spec is not None
        assert not [a for a in spec.annotations if a.startswith("ranking_refused:")]
