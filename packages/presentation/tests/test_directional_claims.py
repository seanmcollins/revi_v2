"""A direction is a claim, and a claim gets checked against its ranges.

The determination this file pins was published with every figure in it
certified. Six monthly appeal overturn rates for one payer, quoted
correctly to one decimal, narrated as "getting worse … ending below where
it started" — over intervals of which every one overlapped every other,
point estimates spanning 11.3 points inside a narrowest range of 28. And
the series was cut at January without saying so; February through May are
what make "ending below where it started" false.

In the same paragraph a facility was called "the best revenue quality" and
another "the weakest" on a 1.1-point gap with no ranges published — two
clauses after the same paragraph correctly refused to separate denial
rates inside a 0.6-point band.

The numbers below are that determination's own. Nothing here tests a
composer; it tests that the platform cannot publish those sentences again.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from revi_investigation_contracts.api import FindingPayload, FindingValue
from revi_investigation_contracts.deep_research import (
    IntervalPayload,
    ResearchFigurePayload,
    ResearchReadingPayload,
)
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_presentation.claims import build_reading_series, claim_verdict
from revi_presentation.narrative import (
    REDACTION_WARNING_PREFIX,
    build_determination_facts,
    build_narrative_facts,
    validate_narrative,
)

HEADER = ContextHeaderPayload(
    window_start=date(2025, 8, 1),
    window_end=date(2026, 5, 31),
    basis="post",
    watermark_id="wm_014",
    display="2025-08-01..2026-05-31 (post) · watermark wm_014",
)

#: The published series, exactly as the review found it: rate, population,
#: and the interval the estimator produced around it.
ATLAS = (
    ("Aug 2025", "0.472", "47.2%", 36, "0.320", "0.630"),
    ("Sep 2025", "0.407", "40.7%", 27, "0.245", "0.593"),
    ("Oct 2025", "0.480", "48.0%", 25, "0.300", "0.665"),
    ("Nov 2025", "0.367", "36.7%", 30, "0.219", "0.545"),
    ("Dec 2025", "0.432", "43.2%", 44, "0.297", "0.578"),
    ("Jan 2026", "0.452", "45.2%", 31, "0.292", "0.622"),
)

#: The four months that existed and were left out. The truncation is what
#: made the claim writable, so the honest series carries them.
ATLAS_TAIL = (
    ("Feb 2026", "0.412", "41.2%", 34, "0.257", "0.581"),
    ("Mar 2026", "0.370", "37.0%", 27, "0.211", "0.560"),
    ("Apr 2026", "0.440", "44.0%", 25, "0.262", "0.632"),
    ("May 2026", "0.412", "41.2%", 34, "0.257", "0.581"),
)

#: Six facilities inside 1.1 points, published with no ranges at all.
FACILITIES = (
    ("Southfield", "0.741", "74.1%", 1180),
    ("Rivergate", "0.738", "73.8%", 1042),
    ("Northline", "0.735", "73.5%", 998),
    ("Eastbrook", "0.734", "73.4%", 1310),
    ("Harbor Point", "0.732", "73.2%", 1121),
    ("Westpark", "0.730", "73.0%", 1247),
)


def _rate_figure(
    label: str,
    value: str,
    display: str,
    population: int,
    low: str | None = None,
    high: str | None = None,
) -> ResearchFigurePayload:
    return ResearchFigurePayload(
        label=label,
        evidence="measured",
        value=value,
        display=display,
        population=population,
        interval=(
            None
            if low is None or high is None
            else IntervalPayload(low=low, high=high, confidence="0.95")
        ),
    )


def atlas_reading(
    points: tuple[tuple[str, str, str, int, str, str], ...] = ATLAS,
) -> ResearchReadingPayload:
    return ResearchReadingPayload(
        id="R1",
        shape="trend",
        title="Appeal overturn rate by month within Atlas Commercial",
        measure_label="Appeal overturn rate",
        metric_id="appeal_overturn_rate",
        unit="ratio",
        reason="Atlas Commercial carried the largest appealed balance.",
        window_label="Aug 2025..May 2026",
        figures=[_rate_figure(*point) for point in points],
    )


def facility_reading() -> ResearchReadingPayload:
    return ResearchReadingPayload(
        id="R2",
        shape="stratified_rates",
        title="Net collection rate by facility",
        measure_label="Net collection rate",
        metric_id="net_collection_rate",
        unit="ratio",
        reason="Revenue quality was asked about by site.",
        window_label="2025-08-01..2026-05-31",
        ranked=True,
        figures=[_rate_figure(*point) for point in FACILITIES],
    )


def _finding(referent: str, title: str, points: tuple[tuple[str, ...], ...]) -> FindingPayload:
    """A finding certifying the same figures the reading published.

    The grounding validator checks digits against findings and directions
    against ranges. Both have to be satisfied for these tests to be about
    the second one.
    """
    return FindingPayload(
        referent=referent,
        title=title,
        statement=title,
        values=[FindingValue(name=point[0], value=float(point[1])) for point in points],
        grade="direct",
    )


ATLAS_FINDING = _finding("F1", "Atlas Commercial appeal overturn rate by month", ATLAS)
FACILITY_FINDING = _finding("F2", "Net collection rate by facility", FACILITIES)


def determination_facts(*readings: ResearchReadingPayload, findings: list[FindingPayload]):
    return build_determination_facts(
        findings=findings,
        header=HEADER,
        extra_names=("Atlas Commercial", "Appeal overturn rate", "Net collection rate"),
        readings=list(readings),
    )


#: The sentence the review found, with a referent citation added — without
#: one it is dropped for stating figures uncited, which is a different rule
#: and would prove nothing about this one.
GETTING_WORSE = (
    "Only Atlas Commercial has a readable direction, and it is getting worse on appeals "
    "(F1): 47.2% Aug, 40.7% Sep, 48.0% Oct, 36.7% Nov, 43.2% Dec, 45.2% Jan 2026, ending "
    "below where it started."
)


class TestADirectionInsideItsOwnRanges:
    def test_the_published_claim_is_replaced_not_published(self) -> None:
        facts = determination_facts(atlas_reading(), findings=[ATLAS_FINDING])
        result = validate_narrative(GETTING_WORSE, facts)

        assert "getting worse" not in result.text
        assert "ending below where it started" not in result.text
        # Replaced, not deleted: the reader keeps both figures and gains
        # the reason the movement between them is not a direction.
        assert "47.2%" in result.text
        assert "45.2%" in result.text
        assert "the ranges around those two figures overlap" in result.text
        assert "noise-compatible" in result.text

    def test_the_warning_says_the_sentence_was_replaced(self) -> None:
        facts = determination_facts(atlas_reading(), findings=[ATLAS_FINDING])
        result = validate_narrative(GETTING_WORSE, facts)

        assert len(result.redactions) == 1
        reason = result.redactions[0].reason
        assert "ranges overlap" in reason
        assert "replaced" in reason
        assert len(result.warnings) == 1
        assert result.warnings[0].startswith(REDACTION_WARNING_PREFIX)
        assert "replaced" in result.warnings[0]

    def test_every_interval_in_the_published_series_overlaps_every_other(self) -> None:
        """The premise of the finding, checked rather than asserted."""
        series = build_reading_series([atlas_reading()])[0]
        for left in series.points:
            for right in series.points:
                assert left.interval_low is not None and left.interval_high is not None
                assert right.interval_low is not None and right.interval_high is not None
                assert left.interval_low <= right.interval_high
                assert right.interval_low <= left.interval_high

    def test_the_narrowest_range_is_wider_than_the_whole_spread(self) -> None:
        series = build_reading_series([atlas_reading()])[0]
        widths = [
            (p.interval_high or Decimal(0)) - (p.interval_low or Decimal(0))
            for p in series.points
        ]
        values = [p.value or Decimal(0) for p in series.points]
        assert max(values) - min(values) == Decimal("0.113")
        assert min(widths) == Decimal("0.281")
        assert min(widths) > max(values) - min(values)

    def test_a_replaced_direction_goes_in_once_however_often_it_is_claimed(self) -> None:
        facts = determination_facts(atlas_reading(), findings=[ATLAS_FINDING])
        twice = (
            f"{GETTING_WORSE} The appeal overturn rate is trending down through the period "
            "(F1), from 47.2% to 45.2%."
        )
        result = validate_narrative(twice, facts)

        assert len(result.redactions) == 2
        assert result.text.count("noise-compatible") == 1


class TestTheGuardIsNotABlanketBan:
    #: The same six months and the same point estimates, measured over a
    #: population large enough that the two ends do not touch. A real
    #: direction survives untouched — the guard checks the intervals, it
    #: does not ban the vocabulary.
    SEPARATED = (
        ("Aug 2025", "0.472", "47.2%", 40000, "0.467", "0.477"),
        ("Sep 2025", "0.407", "40.7%", 40000, "0.402", "0.412"),
        ("Oct 2025", "0.480", "48.0%", 40000, "0.475", "0.485"),
        ("Nov 2025", "0.367", "36.7%", 40000, "0.362", "0.372"),
        ("Dec 2025", "0.432", "43.2%", 40000, "0.427", "0.437"),
        ("Jan 2026", "0.452", "45.2%", 40000, "0.447", "0.457"),
    )

    def test_a_direction_whose_endpoints_are_separated_survives_unchanged(self) -> None:
        reading = atlas_reading(self.SEPARATED)
        finding = _finding("F1", "Atlas Commercial appeal overturn rate by month", self.SEPARATED)
        facts = determination_facts(reading, findings=[finding])

        sentence = (
            "Atlas Commercial appeal overturn fell over the period (F1): 47.2% Aug, 40.7% "
            "Sep, 48.0% Oct, 36.7% Nov, 43.2% Dec, 45.2% Jan 2026."
        )
        result = validate_narrative(sentence, facts)

        assert result.clean
        assert result.text == sentence
        assert result.warnings == []

    def test_the_endpoints_of_that_series_really_do_not_overlap(self) -> None:
        series = build_reading_series([atlas_reading(self.SEPARATED)])[0]
        first, last = series.points[0], series.points[-1]
        assert first.interval_high is not None and last.interval_low is not None
        assert first.interval_high < last.interval_low or (
            last.interval_high is not None
            and first.interval_low is not None
            and last.interval_high < first.interval_low
        )

    def test_a_negated_direction_survives(self) -> None:
        """The escape hatch the magnitude rule already relies on."""
        facts = determination_facts(atlas_reading(), findings=[ATLAS_FINDING])
        sentence = (
            "Atlas Commercial appeal overturn did not rise over the period (F1) — it reads "
            "47.2% in Aug 2025 and 45.2% in Jan 2026."
        )
        result = validate_narrative(sentence, facts)

        assert result.clean
        assert result.text == sentence

    def test_the_quick_path_carries_no_series_and_is_unaffected(self) -> None:
        facts = build_narrative_facts(findings=[ATLAS_FINDING], header=HEADER)
        assert facts.interval_series == []

        sentence = "Appeal overturn rose sharply and is the highest it has been (F1)."
        assert validate_narrative(sentence, facts).clean

    def test_a_direction_over_a_league_table_is_not_a_direction_claim(self) -> None:
        """First and last rows of an ordering are not a start and an end."""
        facts = determination_facts(facility_reading(), findings=[FACILITY_FINDING])
        verdict = claim_verdict(
            "Net collection rate improved at Southfield, which reads 74.1% (F2).",
            facts.interval_series,
        )
        assert verdict is None


class TestABestOrWorstInsideItsOwnUncertainty:
    #: The review's own pairing: the payer study and the facility study in
    #: one determination, one paragraph, one standard.
    def _facts(self):
        return determination_facts(
            atlas_reading(),
            facility_reading(),
            findings=[ATLAS_FINDING, FACILITY_FINDING],
        )

    def test_the_facility_verdict_is_refused(self) -> None:
        sentence = (
            "Southfield shows the best revenue quality and Westpark the weakest, with "
            "Southfield collecting 74.1% against Westpark's 73.0% (F2)."
        )
        result = validate_narrative(sentence, self._facts())

        assert "best revenue quality" not in result.text
        assert "weakest" not in result.text
        assert "no best or worst is named here" in result.text
        assert "74.1%" in result.text and "73.0%" in result.text
        assert len(result.redactions) == 1
        assert "smaller than the range" in result.redactions[0].reason

    def test_the_spread_it_refuses_is_the_one_the_review_measured(self) -> None:
        series = next(
            s for s in build_reading_series([facility_reading()]) if s.reading_id == "R2"
        )
        values = [p.value or Decimal(0) for p in series.points]
        assert max(values) - min(values) == Decimal("0.011")

    def test_a_facility_reading_with_no_ranges_borrows_the_study_s_own_precision(
        self,
    ) -> None:
        """No ranges is a reason to refuse, not an exemption from refusing.

        The facility reading published none; the study's other readings of
        the same unit published ranges around thirty points wide, and that
        is the precision this study demonstrated for rates of this kind.
        """
        series = {s.reading_id: s for s in build_reading_series([atlas_reading(), facility_reading()])}
        assert series["R2"].comparable_interval_width == Decimal("0.326")
        assert all(p.interval_low is None for p in series["R2"].points)

    def test_no_ranges_anywhere_in_the_study_refuses_nothing(self) -> None:
        """Precision is never invented to make a guard fire."""
        series = build_reading_series([facility_reading()])
        assert series[0].comparable_interval_width is None

        facts = determination_facts(facility_reading(), findings=[FACILITY_FINDING])
        sentence = (
            "Southfield shows the best revenue quality at 74.1%, against Westpark's "
            "73.0% (F2)."
        )
        assert validate_narrative(sentence, facts).clean

    def test_a_separated_ranking_survives(self) -> None:
        wide = (
            ("Southfield", "0.741", "74.1%", 1180),
            ("Westpark", "0.310", "31.0%", 1247),
        )
        reading = facility_reading().model_copy(
            update={"figures": [_rate_figure(*point) for point in wide]}
        )
        facts = determination_facts(
            atlas_reading(),
            reading,
            findings=[ATLAS_FINDING, _finding("F2", "Net collection rate by facility", wide)],
        )
        sentence = (
            "Southfield shows the best net collection rate at 74.1%, against Westpark's "
            "31.0% (F2)."
        )
        assert validate_narrative(sentence, facts).clean


class TestAClaimsWindowIsItsReadingsWindow:
    def test_a_series_cut_at_january_is_dropped_with_the_truncation_reason(self) -> None:
        full = (*ATLAS, *ATLAS_TAIL)
        reading = atlas_reading(full)
        facts = determination_facts(
            reading,
            findings=[_finding("F1", "Atlas Commercial appeal overturn rate by month", full)],
        )
        result = validate_narrative(GETTING_WORSE, facts)

        assert result.text == ""
        assert len(result.redactions) == 1
        reason = result.redactions[0].reason
        assert "part of the series it rests on" in reason
        assert "Aug 2025 to May 2026" in reason
        assert "stops at Jan 2026" in reason
        # A truncated claim has no honest shorter form, so nothing is put
        # back in its place.
        assert "noise-compatible" not in result.text

    def test_a_claim_spanning_the_whole_series_is_not_truncated(self) -> None:
        facts = determination_facts(atlas_reading(), findings=[ATLAS_FINDING])
        result = validate_narrative(GETTING_WORSE, facts)
        assert "part of the series it rests on" not in result.redactions[0].reason

    def test_naming_only_the_two_ends_is_not_truncation(self) -> None:
        """The claim's window equals the reading's — that is the whole rule."""
        full = (*ATLAS, *ATLAS_TAIL)
        facts = determination_facts(
            atlas_reading(full),
            findings=[_finding("F1", "Atlas appeal overturn by month", full)],
        )
        verdict = claim_verdict(
            "Appeal overturn declined from 47.2% in Aug 2025 to 41.2% in May 2026 (F1).",
            facts.interval_series,
        )
        assert verdict is not None
        assert "part of the series" not in verdict.reason
        assert verdict.substitute  # the overlap rewrite, not the drop


class TestTheGuardsAroundItAreUnchanged:
    """The four behaviours this change was forbidden to weaken."""

    def test_the_benchmark_wall_still_admits_names_and_not_figures(self) -> None:
        facts = build_determination_facts(
            findings=[ATLAS_FINDING],
            header=HEADER,
            knowledge=["Industry appeal overturn for Medicare Advantage runs near 62.4%."],
            readings=[atlas_reading()],
        )
        assert "Medicare Advantage" in facts.allowed_names
        assert Decimal("0.624") not in facts.numeric_values
        assert Decimal("62.4") not in facts.numeric_values

        dropped = validate_narrative(
            "Atlas Commercial sits at 62.4% against the industry mark (F1).", facts
        )
        assert dropped.text == ""
        assert "matches no measured value" in dropped.redactions[0].reason

    def test_figure_matching_still_drops_an_uncertified_number(self) -> None:
        facts = determination_facts(atlas_reading(), findings=[ATLAS_FINDING])
        result = validate_narrative(
            "Atlas Commercial appeal overturn reads 51.9% in Aug 2025 (F1).", facts
        )
        assert result.text == ""
        assert "51.9%" in result.redactions[0].reason

    def test_the_superlative_substitution_still_fires_on_a_truncated_list(self) -> None:
        facts = build_narrative_facts(
            findings=[ATLAS_FINDING, FACILITY_FINDING],
            header=HEADER,
            disclosures=[
                "3 of 12 payers computed are published as findings — the rows below are "
                "not the whole population."
            ],
        )
        assert facts.truncated
        assert facts.superlative_substitute
        result = validate_narrative("Southfield is the highest measured of these (F2).", facts)
        assert facts.superlative_substitute in result.text

    def test_the_negation_escape_hatch_still_covers_magnitude_claims(self) -> None:
        facts = build_narrative_facts(
            findings=[
                FindingPayload(
                    referent="F1",
                    title="Denial rate premise",
                    statement="It did not double.",
                    values=[
                        FindingValue(name="premise_magnitude", value="short"),
                        FindingValue(name="premise_asserted_verb", value="double"),
                    ],
                    grade="direct",
                )
            ],
            header=HEADER,
        )
        assert facts.forbidden_magnitude_claims == ["double"]
        assert validate_narrative("Denials did not double (F1).", facts).clean
        assert not validate_narrative("Denials roughly doubled (F1).", facts).clean
