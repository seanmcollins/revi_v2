"""Deep research over the generated warehouse, against the answer key.

The estimators have their own reference test; this one is about the MODE —
the read, the angles, the arithmetic that turns rates into dollars, and the
report a reader is handed. It asserts against ``data/answer_key.json``,
which the generator writes from the world it authored, and where the answer
key records a realized aggregate the report is required to REPRODUCE IT
EXACTLY rather than land near it. A population definition that differs from
the documented one by a single denial is a defect worth failing on, and a
tolerance would hide it.

The governed content under test is the real one: ``deep_research.yaml`` and
``filing_rules.yaml`` are loaded from the pack, not written here. A floor
moved in content moves this test, which is the point of putting it there.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from revi_api.deep_research_policy import (
    DEEP_RESEARCH_FILENAME,
    FILING_RULES_FILENAME,
    load_deep_research_settings,
    load_filing_rule_ladder,
)
from revi_catalog import load_catalog
from revi_connector_duckdb import DuckDbAnalyticalRepository
from revi_investigation.application.deep_research import (
    DeepResearchService,
    DenialRowSource,
    PopulationKind,
    TargetPopulation,
)
from revi_investigation.application.deep_research.grammar import AngleFamily
from revi_kernel.frame import EvidenceFrame

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"
ANSWER_KEY = REPO_ROOT / "data" / "answer_key.json"
CATALOG = REPO_ROOT / "warehouse" / "catalog"
PACK = REPO_ROOT / "packs" / "base-rcm"

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not (WAREHOUSE.is_file() and ANSWER_KEY.is_file()),
        reason="generated warehouse missing — run: make warehouse",
    ),
]


class _Cache:
    """The evidence cache, in process. One read serves every angle already;
    this makes a second RUN free as well, which is what the determinism
    assertions below are measuring the consequence of."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], EvidenceFrame] = {}
        self.hits = 0

    async def get(self, probe_hash: str, watermark_id: str, pack_snapshot_id: str):
        found = self.entries.get((probe_hash, watermark_id, pack_snapshot_id))
        if found is not None:
            self.hits += 1
        return found

    async def put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None:
        self.entries[(probe_hash, watermark_id, pack_snapshot_id)] = frame


@pytest.fixture(scope="module")
def answer_key() -> dict[str, Any]:
    key: dict[str, Any] = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))["recovery"]
    return key


@pytest.fixture(scope="module")
def settings():
    return load_deep_research_settings(PACK / DEEP_RESEARCH_FILENAME)


@pytest.fixture(scope="module")
def ladder():
    return load_filing_rule_ladder(PACK / FILING_RULES_FILENAME)


@pytest.fixture(scope="module")
def cache() -> _Cache:
    return _Cache()


@pytest.fixture(scope="module")
def repository():
    catalog = load_catalog(str(CATALOG))
    return DuckDbAnalyticalRepository(str(WAREHOUSE), catalog, lambda _id: None)


@pytest.fixture(scope="module")
async def run(repository, cache, settings, ladder):
    """One deep-research run over every open denial, at the newest load."""
    watermarks = await repository.list_watermarks()
    service = DeepResearchService(
        DenialRowSource(repository, cache, filing_rule_confirmed=ladder.confirmed)
    )
    return await service.run(
        run_id="dr_reference",
        question=None,
        population=TargetPopulation(),
        settings=settings,
        watermark=watermarks[-1],
        pack_snapshot_id="reference",
    )


@pytest.fixture(scope="module")
def report(run):
    return run.draft.report


def _cents(rate: Decimal, dollars: int) -> int:
    return int((rate * Decimal(dollars)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


class TestTheRead:
    def test_the_population_is_the_documented_one(self, run, answer_key) -> None:
        assert run.rows.rows_read == answer_key["snap_003"]["overall"]["denials"] == 5398

    def test_the_read_is_whole_never_a_sample(self, run) -> None:
        """A truncated read is refused upstream; this pins that it was not
        merely tolerated but never happened."""
        assert run.rows.rows_read == len(run.rows.rows)
        assert run.rows.read_fingerprint

    def test_it_is_pinned_to_the_newest_load(self, run) -> None:
        assert run.rows.watermark.id == "wm_003"
        assert run.rows.as_of.isoformat() == "2026-08-02"

    def test_the_open_population_is_every_unfinished_denial(self, run, answer_key) -> None:
        overall = answer_key["snap_003"]["overall"]
        assert len(run.rows.open_rows) == overall["not_resubmitted"] + overall["resubmitted_pending"]
        assert len(run.rows.open_rows) == 2865


class TestTheStandingPlan:
    def test_every_angle_ran_and_the_headline_led(self, report) -> None:
        families = [angle.family for angle in report.plan.angles]
        assert families[0] == "expected_recovery"
        assert set(families) == {
            "expected_recovery",
            "outcome_by_stratum",
            "payer_contrast",
            "class_contrast",
            "timeliness_curve",
            "deadline_interaction",
        }
        assert report.plan.authored_by == "revi"

    def test_no_angle_refused(self, run) -> None:
        assert [result.refusal for result in run.results if result.refusal] == []


class TestCensoring:
    def test_the_counts_are_exactly_the_answer_keys(self, report, answer_key) -> None:
        censoring = answer_key["snap_003"]["censoring"]
        overall = answer_key["snap_003"]["overall"]
        published = report.censoring
        assert published.rows_considered == overall["denials"] == 5398
        assert published.in_denominator == overall["decided"] == 2533
        assert published.excluded_open_undecided == censoring["resubmitted_no_outcome_yet"] == 212
        assert (
            published.excluded_not_pursued
            == censoring["not_resubmitted_at_this_watermark"]
            == 2653
        )
        assert published.data_edge_date.isoformat() == "2026-08-02"

    def test_every_denial_is_accounted_for(self, report) -> None:
        published = report.censoring
        assert (
            published.in_denominator
            + published.excluded_open_undecided
            + published.excluded_not_pursued
            + published.excluded_immature
            + published.excluded_unclassifiable
            == published.rows_considered
        )

    def test_the_disclosure_travels_onto_the_report_in_words(self, report) -> None:
        joined = " ".join(report.censoring.statements)
        assert "2,533" in joined
        assert "5,398" in joined
        assert "212" in joined
        assert "2,653" in joined
        assert "Aug 2, 2026" in joined


class TestRatesAgainstTruth:
    """Every published rate lies inside its own interval around truth."""

    def test_denial_type_rates_reproduce_the_answer_key(self, report, answer_key) -> None:
        truth = {
            entry["denial_recovery_class"]: entry
            for entry in answer_key["snap_003"]["by_class"]
        }
        cells = [
            cell
            for cell in report.rates
            if cell.basis == "decided"
            and len(cell.parts) == 1
            and cell.parts[0].stratifier == "recovery_class"
        ]
        assert len(cells) == 5
        for cell in cells:
            entry = truth[cell.parts[0].value]
            assert cell.n == entry["decided"], cell.label
            assert cell.successes == entry["recovered"], cell.label
            assert cell.rate is not None
            realized = Decimal(str(entry["recovery_rate_of_decided"]))
            assert Decimal(cell.rate) == realized.quantize(Decimal("1E-10")), cell.label
            assert cell.interval is not None
            assert Decimal(cell.interval.low) <= realized <= Decimal(cell.interval.high)

    def test_payer_rates_reproduce_the_answer_key(self, report, answer_key) -> None:
        truth = {entry["payer_name"]: entry for entry in answer_key["snap_003"]["by_payer"]}
        cells = [
            cell
            for cell in report.rates
            if cell.basis == "decided"
            and len(cell.parts) == 1
            and cell.parts[0].stratifier == "payer"
        ]
        assert len(cells) == 12
        for cell in cells:
            entry = truth[cell.parts[0].value]
            assert cell.n == entry["decided"], cell.label
            assert cell.successes == entry["recovered"], cell.label
            if cell.rate is None:
                assert cell.evidence == "not_estimable"
                continue
            realized = Decimal(str(entry["recovery_rate_of_decided"]))
            assert cell.interval is not None
            assert Decimal(cell.interval.low) <= realized <= Decimal(cell.interval.high)

    def test_no_published_rate_sits_under_the_floor(self, report, settings) -> None:
        for cell in report.rates:
            if cell.rate is None:
                assert cell.evidence == "not_estimable"
            else:
                assert cell.evidence == "measured"
                assert cell.n >= settings.min_cohort, cell.label
            assert cell.floor == settings.min_cohort


class TestThePayerContrast:
    def test_the_strong_and_weak_payers_are_the_documented_pair(
        self, report, answer_key
    ) -> None:
        truth = answer_key["snap_003"]["detectability"]
        contrast = next(c for c in report.contrasts if "payer" in c.title)
        assert contrast.left.label == truth["strong_payer"] == "Northbridge Commercial"
        assert contrast.right.label == truth["weak_payer"] == "Lakewood Medicaid MCO"
        assert contrast.left.n == truth["strong_decided"] == 148
        assert contrast.left.successes == truth["strong_recovered"] == 84
        assert contrast.right.n == truth["weak_decided"] == 117
        assert contrast.right.successes == truth["weak_recovered"] == 34

    def test_the_test_that_separates_them_is_the_documented_one(
        self, report, answer_key
    ) -> None:
        truth = answer_key["snap_003"]["detectability"]
        contrast = next(c for c in report.contrasts if "payer" in c.title)
        assert contrast.test == "two_proportion_z"
        assert contrast.z_statistic is not None
        assert float(Decimal(contrast.z_statistic)) == pytest.approx(
            truth["two_proportion_z"], abs=1e-6
        )
        assert contrast.p_value is not None
        assert Decimal(contrast.p_value) < Decimal("0.0001")

    def test_the_effect_and_its_range_exclude_no_difference(self, report) -> None:
        contrast = next(c for c in report.contrasts if "payer" in c.title)
        assert contrast.risk_difference is not None
        assert contrast.risk_difference_interval is not None
        assert Decimal(contrast.risk_difference_interval.low) > 0

    def test_the_extremes_are_named_as_extremes(self, run, report) -> None:
        """A max-versus-min comparison is a screen, and the report says so."""
        assert any("ends of the range" in warning for warning in run.draft.warnings)
        contrast = next(c for c in report.contrasts if "payer" in c.title)
        assert "under 1 in 1,000" in contrast.implication

    def test_the_denial_type_contrast_is_present_and_separated(self, report) -> None:
        contrast = next(c for c in report.contrasts if "denial type" in c.title)
        assert contrast.left.label == "coding"
        assert contrast.right.label.startswith("final")
        assert contrast.p_value is not None
        assert Decimal(contrast.p_value) < Decimal("0.0001")


class TestTheTimelinessCurve:
    def test_the_bands_reproduce_the_answer_key_and_decay(self, report, answer_key) -> None:
        truth = {
            entry["days_to_resubmission_bucket"]: entry
            for entry in answer_key["snap_003"]["by_days_to_resubmission"]
        }
        assert report.timeliness is not None
        bands = report.timeliness.bands
        assert [band.band for band in bands] == ["0-14", "15-30", "31-60", "61+"]
        rates: list[Decimal] = []
        for band in bands:
            entry = truth[band.band]
            assert band.cell.n == entry["decided"], band.band
            assert band.cell.successes == entry["recovered"], band.band
            assert band.cell.rate is not None
            realized = Decimal(str(entry["recovery_rate_of_decided"]))
            assert Decimal(band.cell.rate) == realized.quantize(Decimal("1E-10")), band.band
            rates.append(Decimal(band.cell.rate))
        assert all(a > b for a, b in pairwise(rates))

    def test_the_implication_states_both_ends(self, report) -> None:
        assert report.timeliness is not None
        implication = report.timeliness.implication
        assert "0-14" in implication and "61+" in implication
        assert "54.4%" in implication and "14.2%" in implication

    def test_the_fastest_and_slowest_intervals_do_not_overlap(self, report) -> None:
        assert report.timeliness is not None
        bands = report.timeliness.bands
        fastest, slowest = bands[0].cell, bands[-1].cell
        assert fastest.interval is not None and slowest.interval is not None
        assert Decimal(slowest.interval.high) < Decimal(fastest.interval.low)


class TestTheFilingDeadline:
    def test_the_four_cells_reproduce_the_answer_key(self, report, answer_key) -> None:
        truth = {
            (entry["filing_position"], entry["filing_rule_authority"]): entry
            for entry in answer_key["snap_003"]["by_filing_position"]
        }
        assert report.deadline is not None
        seen = set()
        for row in report.deadline.rows:
            entry = truth[(row.position, row.rule)]
            assert row.cell.n == entry["decided"], (row.position, row.rule)
            assert row.cell.successes == entry["recovered"], (row.position, row.rule)
            seen.add((row.position, row.rule))
        assert seen == {
            ("within_deadline", "confirmed"),
            ("within_deadline", "requires_confirmation"),
            ("past_deadline", "confirmed"),
            ("past_deadline", "requires_confirmation"),
        }

    def test_past_a_confirmed_deadline_nothing_came_back_and_zero_is_bounded(
        self, report
    ) -> None:
        assert report.deadline is not None
        row = next(
            r
            for r in report.deadline.rows
            if r.position == "past_deadline" and r.rule == "confirmed"
        )
        assert row.cell.n == 39
        assert row.cell.successes == 0
        assert row.cell.rate == "0E-10"
        assert row.cell.interval is not None
        assert Decimal(row.cell.interval.low) == 0
        assert Decimal(row.cell.interval.high) > Decimal("0.05")

    def test_the_cliff_is_shallower_where_the_limit_is_only_a_default(self, report) -> None:
        assert report.deadline is not None
        unconfirmed = next(
            r
            for r in report.deadline.rows
            if r.position == "past_deadline" and r.rule == "requires_confirmation"
        )
        assert unconfirmed.cell.n == 40
        assert unconfirmed.cell.successes == 3
        assert Decimal(unconfirmed.cell.rate or "0") == Decimal("0.075")

    def test_the_authority_split_is_stated_not_pooled(self, report) -> None:
        notes = " ".join(report.context_notes)
        assert "planning default" in notes
        assert "overstates the cliff" in notes


class TestTheHeadlineArithmetic:
    def test_the_total_is_exactly_the_sum_of_its_priced_populations(self, report) -> None:
        recomputed = sum(row.expected_cents or 0 for row in report.strata)
        assert recomputed == report.headline.total_expected_cents

    def test_every_population_is_priced_at_its_own_rate_to_the_cent(self, report) -> None:
        for row in report.strata:
            assert row.rate_cell.rate is not None
            assert row.expected_cents == _cents(
                Decimal(row.rate_cell.rate), row.open_dollars_cents
            ), row.label
            assert row.expected_interval is not None
            assert row.rate_cell.interval is not None
            assert row.expected_interval.low_cents == _cents(
                Decimal(row.rate_cell.interval.low), row.open_dollars_cents
            )
            assert row.expected_interval.high_cents == _cents(
                Decimal(row.rate_cell.interval.high), row.open_dollars_cents
            )

    def test_the_range_is_the_sum_of_the_ranges_and_says_so(self, report) -> None:
        headline = report.headline
        assert headline.total_expected_interval.low_cents == sum(
            row.expected_interval.low_cents for row in report.strata if row.expected_interval
        )
        assert headline.total_expected_interval.high_cents == sum(
            row.expected_interval.high_cents for row in report.strata if row.expected_interval
        )
        assert headline.range_assumes_independence is True

    def test_open_dollars_partition_by_filing_position(self, report) -> None:
        headline = report.headline
        assert (
            headline.catchable_dollars_cents
            + headline.deadline_passed_dollars_cents
            + headline.deadline_unknown_dollars_cents
            == headline.total_open_dollars_cents
        )

    def test_priced_and_unpriced_partition_the_open_dollars(self, report) -> None:
        headline = report.headline
        assert (
            headline.priced_open_dollars_cents + headline.unpriced_open_dollars_cents
            == headline.total_open_dollars_cents
        )

    def test_the_total_never_exceeds_what_it_priced(self, report) -> None:
        headline = report.headline
        assert 0 < headline.total_expected_cents < headline.priced_open_dollars_cents
        assert (
            headline.total_expected_interval.high_cents <= headline.priced_open_dollars_cents
        )

    def test_the_open_dollars_are_the_open_denials_own(self, run, report) -> None:
        assert report.headline.total_open_dollars_cents == sum(
            row.denied_amount_cents for row in run.rows.open_rows
        )
        assert report.headline.total_open_denials == len(run.rows.open_rows)


class TestWhatCouldNotBePriced:
    def test_the_refused_populations_are_exactly_the_thin_ones(
        self, run, report, answer_key, settings
    ) -> None:
        """The thin list is a consequence of the floor, checked against truth.

        A population of open denials is priced when its own answered-denial
        count reaches the floor, and refused otherwise. The answer key holds
        that count for every payer-and-type pair, so the expected refusal set
        is derivable from content plus truth rather than from the code under
        test.
        """
        decided = {
            (entry["payer_name"], entry["denial_recovery_class"]): entry["decided"]
            for entry in answer_key["snap_003"]["by_payer_class"]
        }
        targeted = {
            (row.payer_name, row.recovery_class) for row in run.rows.open_rows
        }
        expected_refused = {
            key for key in targeted if decided.get(key, 0) < settings.min_cohort
        }
        published_refused = {
            (row.parts[0].value, row.parts[1].value) for row in report.not_estimable
        }
        rolled = report.thin_populations
        assert len(published_refused) + (rolled.populations if rolled else 0) == len(
            expected_refused
        )
        assert published_refused <= expected_refused

        published_priced = {
            (row.parts[0].value, row.parts[1].value) for row in report.strata
        }
        assert published_priced == targeted - expected_refused

    def test_a_refused_population_publishes_its_size_and_dollars_and_no_rate(
        self, report
    ) -> None:
        assert report.not_estimable
        for row in report.not_estimable:
            assert row.evidence == "not_estimable"
            assert row.expected_cents is None
            assert row.expected_interval is None
            assert row.rate_cell.rate is None
            assert row.rate_cell.interval is None
            assert row.open_denials > 0
            assert row.open_dollars_cents >= 0

    def test_populations_too_small_to_name_are_counted_together_not_named(
        self, report, settings
    ) -> None:
        rolled = report.thin_populations
        assert rolled is not None
        assert rolled.floor == settings.disclosure_floor
        assert rolled.populations > 0
        assert all(
            row.open_denials >= settings.disclosure_floor for row in report.not_estimable
        )

    def test_the_unpriced_dollars_are_the_refused_populations_own(self, report) -> None:
        rolled = report.thin_populations
        named = sum(row.open_dollars_cents for row in report.not_estimable)
        hidden = rolled.open_dollars_cents if rolled else 0
        assert named + hidden == report.headline.unpriced_open_dollars_cents

    def test_no_prior_is_substituted_anywhere_and_the_report_says_so(
        self, run, report
    ) -> None:
        assert any("industry average" in warning for warning in run.draft.warnings)
        assert all(
            row.expected_cents is None for row in report.not_estimable
        )


class TestDeterminism:
    async def test_two_runs_at_the_same_load_agree_on_every_number(
        self, repository, cache, settings, ladder, report
    ) -> None:
        watermarks = await repository.list_watermarks()
        service = DeepResearchService(
            DenialRowSource(repository, cache, filing_rule_confirmed=ladder.confirmed)
        )
        second = await service.run(
            run_id="dr_reference_again",
            question=None,
            population=TargetPopulation(),
            settings=settings,
            watermark=watermarks[-1],
            pack_snapshot_id="reference",
        )
        first_payload = report.model_dump(mode="json")
        second_payload = second.draft.report.model_dump(mode="json")
        for payload in (first_payload, second_payload):
            for key in ("id", "created_at", "completed_at", "duration_ms", "evidence"):
                payload.pop(key, None)
        assert first_payload == second_payload

    async def test_the_second_run_reuses_the_first_reads_evidence(
        self, repository, cache, settings, ladder, report
    ) -> None:
        watermarks = await repository.list_watermarks()
        service = DeepResearchService(
            DenialRowSource(repository, cache, filing_rule_confirmed=ladder.confirmed)
        )
        before = cache.hits
        result = await service.run(
            run_id="dr_reference_cached",
            question=None,
            population=TargetPopulation(),
            settings=settings,
            watermark=watermarks[-1],
            pack_snapshot_id="reference",
        )
        assert cache.hits == before + 1
        assert result.rows.cache_hit is True

    async def test_a_narrower_population_reads_and_prices_only_it(
        self, repository, cache, settings, ladder
    ) -> None:
        watermarks = await repository.list_watermarks()
        service = DeepResearchService(
            DenialRowSource(repository, cache, filing_rule_confirmed=ladder.confirmed)
        )
        result = await service.run(
            run_id="dr_reference_payer",
            question="What can we recover from Northbridge?",
            population=TargetPopulation(
                kind=PopulationKind.PAYER, values=("Northbridge Commercial",)
            ),
            settings=settings,
            watermark=watermarks[-1],
            pack_snapshot_id="reference",
        )
        assert {row.payer_name for row in result.rows.rows} == {"Northbridge Commercial"}
        assert result.rows.rows_read == 329
        report = result.draft.report
        assert report.population.kind == "payer"
        assert report.headline.total_open_dollars_cents > 0


class TestProvenance:
    def test_every_angle_names_the_read_and_the_estimator_behind_it(
        self, run, report
    ) -> None:
        assert len(report.evidence) == len(run.plan.angles)
        for entry in report.evidence:
            assert entry.read_fingerprint == run.rows.read_fingerprint
            assert entry.rows_read == run.rows.rows_read == 5398
            assert entry.estimator
            assert entry.title

    def test_the_pricing_angle_publishes_and_refuses_cells(self, report) -> None:
        pricing = next(
            entry for entry in report.evidence if entry.family == AngleFamily.EXPECTED_RECOVERY
        )
        assert pricing.cells_published > 0
        assert pricing.cells_refused > 0

    def test_the_plan_is_content_addressed(self, run) -> None:
        assert len(run.fingerprint) == 64
