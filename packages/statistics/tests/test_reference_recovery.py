"""Validation against the generated warehouse and its answer key.

The estimators are pure functions over rows, so this is the only place the
capability meets real data. The connector lives *here*, in the test — the
package itself never learns a table name, and this file is the proof that a
connector-free estimator can still be validated end to end.

Every assertion below is against ``data/answer_key.json``, which the
generator writes from the world it authored. Where the answer key records the
realized aggregate (decided counts, recovered counts, the two-proportion z),
the estimators are required to *reproduce it exactly* rather than merely land
near it: a population definition that differs from the documented one by even
one row is a bug worth failing on, and a tolerance would hide it.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import pytest

from revi_statistics import (
    compare_rate_cells,
    deadline_rates,
    delay_effect_curve,
    estimate_durations,
    estimate_rates,
    expected_recovery,
    severity_ratios,
)
from revi_statistics_contracts.contract import (
    Band,
    DenialRow,
    DurationMeasure,
    EstimationPolicy,
    EvidenceLabel,
    MaturityPolicy,
    MaturityWindow,
    RateBasis,
    RateEstimate,
    RecoveryStatus,
    Stratifier,
    StratumKey,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"
ANSWER_KEY = REPO_ROOT / "data" / "answer_key.json"

#: The wm_003 data edge. Every maturity and deadline judgement is relative
#: to it, and the answer key's censoring counts are stated at it.
DATA_EDGE = date(2026, 8, 2)

#: The recovery feed's own population, verbatim from ANSWER_KEY.md.
COHORT_PREDICATE = "service_date >= DATE '2025-01-01' AND appeal_status = 'NONE'"

#: A cohort floor loose enough to leave the documented class and payer cells
#: measurable, so the validation exercises the estimators rather than the
#: suppression rule (which has its own tests).
MIN_COHORT = 30

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not (WAREHOUSE.is_file() and ANSWER_KEY.is_file()),
        reason="generated warehouse missing — run: make warehouse",
    ),
]


@pytest.fixture(scope="module")
def answer_key() -> dict[str, Any]:
    key: dict[str, Any] = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))["recovery"]
    return key


@pytest.fixture(scope="module")
def rows(answer_key: dict[str, Any]) -> list[DenialRow]:
    """``snap_003.v_denial`` as typed rows — the capability's actual input.

    Read through a function-scoped read-only handle that is closed
    immediately: DuckDB refuses a read-write connection while any read-only
    one is open in the process, and a lingering handle here would break
    cohort materialization in later test modules.
    """
    confirmed = set(answer_key["generating_model"]["timeliness"]["confirmed_plans"])
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        raw = con.execute(
            f"""
            SELECT denial_id, denial_date, service_date, payer_name, plan_name,
                   denial_recovery_class, recovery_status, denied_amount_cents,
                   recovered_amount_cents, days_to_resubmission, resubmission_date,
                   recovery_outcome_date, timely_filing_days
            FROM snap_003.v_denial
            WHERE {COHORT_PREDICATE}
            """
        ).fetchall()
    finally:
        con.close()
    return [
        DenialRow(
            denial_id=record[0],
            denial_date=record[1],
            service_date=record[2],
            payer_name=record[3],
            plan_name=record[4],
            recovery_class=record[5],
            recovery_status=RecoveryStatus(record[6]),
            denied_amount_cents=int(record[7]),
            recovered_amount_cents=int(record[8]),
            days_to_resubmission=record[9],
            resubmission_date=record[10],
            recovery_outcome_date=record[11],
            timely_filing_days=record[12],
            # The pack's filing ladder states these seven plans' limits
            # without a confirmation caveat; the other 23 are planning
            # defaults. The distinction is an input, not a guess.
            filing_rule_confirmed=record[4] in confirmed,
        )
        for record in raw
    ]


@pytest.fixture(scope="module")
def maturity(answer_key: dict[str, Any]) -> MaturityPolicy:
    """Per-class maturity windows derived from the authored delay model.

    The generator draws the resubmission delay from a lognormal with the
    class's median and sigma. The 90th percentile of that distribution is
    the age by which a denial that was ever going to be worked would have
    been worked — a defensible tail, and derived from the authored
    parameters rather than tuned against the outcome.
    """
    z90 = 1.2815515655446004
    return MaturityPolicy(
        windows=tuple(
            MaturityWindow(
                recovery_class=spec["class"],
                days=round(spec["delay_median_days"] * math.exp(spec["delay_sigma"] * z90)),
            )
            for spec in answer_key["generating_model"]["classes"]
        )
    )


@pytest.fixture(scope="module")
def policy(maturity: MaturityPolicy) -> EstimationPolicy:
    return EstimationPolicy(
        min_cohort=MIN_COHORT,
        confidence=Decimal("0.95"),
        maturity=maturity,
        delay_bands=(
            Band("0-14", 0, 15),
            Band("15-30", 15, 31),
            Band("31-60", 31, 61),
            Band("61+", 61, None),
        ),
        dollar_bands=(
            Band("<$500", 0, 50_000),
            Band("$500-$5k", 50_000, 500_000),
            Band(">$5k", 500_000, None),
        ),
    )


@pytest.fixture(scope="module")
def by_class(rows: list[DenialRow], policy: EstimationPolicy) -> RateEstimate:
    return estimate_rates(
        rows,
        basis=RateBasis.DECIDED,
        stratify_by=(Stratifier.RECOVERY_CLASS,),
        policy=policy,
        as_of=DATA_EDGE,
    )


class TestPopulation:
    def test_the_cohort_matches_the_answer_key(
        self, rows: list[DenialRow], answer_key: dict[str, Any]
    ) -> None:
        assert len(rows) == answer_key["snap_003"]["overall"]["denials"] == 5398


class TestClassRates:
    """Realized recovery rate over decided chains, by denial class."""

    def test_counts_and_points_reproduce_the_answer_key_exactly(
        self, by_class: RateEstimate, answer_key: dict[str, Any]
    ) -> None:
        expected = {
            entry["denial_recovery_class"]: entry for entry in answer_key["snap_003"]["by_class"]
        }
        assert len(by_class.cells) == len(expected) == 5
        for cell in by_class.cells:
            name = cell.stratum.value_of(Stratifier.RECOVERY_CLASS)
            assert name is not None
            truth = expected[name]
            assert cell.n == truth["decided"], name
            assert cell.successes == truth["recovered"], name
            assert cell.rate is not None
            assert float(cell.rate) == pytest.approx(
                truth["recovery_rate_of_decided"], abs=1e-9
            ), name

    def test_the_documented_headline_rates(self, by_class: RateEstimate) -> None:
        """62.2 / 52.5 / 44.7 / 14.0 / 3.0 percent, as ANSWER_KEY.md states."""
        rates = {
            cell.stratum.value_of(Stratifier.RECOVERY_CLASS): cell.rate
            for cell in by_class.cells
        }
        for name, expected in (
            ("CODING", 0.622),
            ("REGISTRATION", 0.525),
            ("ROUTING", 0.447),
            ("CLINICAL", 0.140),
            ("FINAL", 0.030),
        ):
            rate = rates[name]
            assert rate is not None
            assert float(rate) == pytest.approx(expected, abs=5e-4), name

    def test_every_realized_rate_lies_inside_its_own_interval(
        self, by_class: RateEstimate, answer_key: dict[str, Any]
    ) -> None:
        expected = {
            entry["denial_recovery_class"]: entry for entry in answer_key["snap_003"]["by_class"]
        }
        for cell in by_class.cells:
            name = cell.stratum.value_of(Stratifier.RECOVERY_CLASS)
            assert name is not None
            assert cell.interval is not None
            realized = Decimal(str(expected[name]["recovery_rate_of_decided"]))
            assert cell.interval.contains(realized), name

    def test_the_class_ordering_is_recovered(self, by_class: RateEstimate) -> None:
        """CODING > REGISTRATION > ROUTING > CLINICAL > FINAL, as authored."""
        ordered = ["CODING", "REGISTRATION", "ROUTING", "CLINICAL", "FINAL"]
        rates = {
            cell.stratum.value_of(Stratifier.RECOVERY_CLASS): cell.rate
            for cell in by_class.cells
        }
        series = [rates[name] for name in ordered]
        assert all(a is not None and b is not None and a > b for a, b in pairwise(series))

    def test_the_weakest_class_interval_is_wide_not_degenerate(
        self, by_class: RateEstimate
    ) -> None:
        """FINAL is 2 of 66 — Wald would understate this badly."""
        final = next(
            cell
            for cell in by_class.cells
            if cell.stratum.value_of(Stratifier.RECOVERY_CLASS) == "FINAL"
        )
        assert final.n == 66
        assert final.successes == 2
        assert final.interval is not None
        assert final.interval.low > Decimal(0)
        assert final.interval.high > Decimal("0.09")


class TestCensoringDisclosure:
    def test_counts_match_the_answer_keys_censoring_block(
        self, by_class: RateEstimate, answer_key: dict[str, Any]
    ) -> None:
        censoring = answer_key["snap_003"]["censoring"]
        overall = answer_key["snap_003"]["overall"]
        disclosure = by_class.disclosure

        assert disclosure.data_edge_date == DATA_EDGE
        assert disclosure.rows_considered == overall["denials"] == 5398
        assert disclosure.in_denominator == overall["decided"] == 2533
        assert disclosure.excluded_open_undecided == censoring["resubmitted_no_outcome_yet"] == 212
        assert (
            disclosure.excluded_not_pursued
            == censoring["not_resubmitted_at_this_watermark"]
            == 2653
        )
        assert disclosure.open_undecided_in_input == 212
        assert disclosure.not_pursued_in_input == 2653

    def test_the_naive_rate_would_have_been_materially_lower(
        self, by_class: RateEstimate, answer_key: dict[str, Any]
    ) -> None:
        """What the censoring-honest denominator is worth, in points.

        Dividing recoveries by *all* denials charges every open story as a
        loss: 20.1% instead of 42.9%. Both figures are in the answer key,
        and the gap is the bias this capability exists to avoid.
        """
        overall = answer_key["snap_003"]["overall"]
        decided_rate = overall["recovery_rate_of_decided"]
        naive_rate = overall["recovery_rate_of_denials"]
        assert decided_rate == pytest.approx(0.4291354126, abs=1e-9)
        assert naive_rate == pytest.approx(0.2013708781, abs=1e-9)

        recovered = sum(cell.successes for cell in by_class.cells)
        decided = sum(cell.n for cell in by_class.cells)
        assert recovered / decided == pytest.approx(decided_rate, abs=1e-9)
        assert recovered / 5398 == pytest.approx(naive_rate, abs=1e-9)
        # 22.8 points of downward bias, avoided by the choice of denominator.
        assert decided_rate - naive_rate == pytest.approx(0.2277645345, abs=1e-9)


class TestMaturityWindow:
    """The 383 chains that are pending in world-truth and invisible in data."""

    def test_immature_denials_are_excluded_from_the_pursuit_denominator(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        estimate = estimate_rates(rows, basis=RateBasis.PURSUIT, policy=policy, as_of=DATA_EDGE)
        disclosure = estimate.disclosure
        assert disclosure.rows_considered == 5398
        assert disclosure.excluded_immature > 0
        assert disclosure.excluded_unclassifiable == 0
        assert disclosure.in_denominator == 5398 - disclosure.excluded_immature

        # The correction moves the rate up: the excluded cohort is
        # disproportionately silent-because-young, not silent-because-ignored.
        naive = answer_key["snap_003"]["overall"]["resubmission_rate"]
        cell = estimate.cells[0]
        assert cell.rate is not None
        assert float(cell.rate) > naive
        assert float(cell.rate) == pytest.approx(0.5556032632, abs=1e-9)
        assert naive == pytest.approx(0.5085216747, abs=1e-9)

    def test_the_window_covers_the_invisible_pending_population(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        """The counterfactual check the world truth makes possible.

        ``resubmission_after_newest_watermark`` is 383: denials reading
        NOT_RESUBMITTED at the edge whose resubmission the generator knows
        is still coming. Only the generator can see them. A maturity window
        that excludes fewer silent denials than that would still be
        charging some of the 383 as never-pursued, which is exactly the
        error being corrected — so the count of excluded-and-silent rows
        must be at least 383.
        """
        still_coming = answer_key["world_truth"]["resubmission_after_newest_watermark"]
        assert still_coming == 383

        excluded_and_silent = sum(
            1
            for row in rows
            if not row.recovery_status.is_pursued
            and (window := policy.maturity.days_for(row.recovery_class)) is not None
            and row.age_days(DATA_EDGE) < window
        )
        assert excluded_and_silent >= still_coming
        assert excluded_and_silent == 583

    def test_a_decided_rate_is_untouched_by_the_maturity_window(
        self, rows: list[DenialRow], policy: EstimationPolicy, maturity: MaturityPolicy
    ) -> None:
        """Maturity is a PURSUIT concern; DECIDED never needed it."""
        without = EstimationPolicy(
            min_cohort=MIN_COHORT,
            confidence=policy.confidence,
            maturity=MaturityPolicy(),
            delay_bands=policy.delay_bands,
            dollar_bands=policy.dollar_bands,
        )
        stratify = (Stratifier.RECOVERY_CLASS,)
        with_window = estimate_rates(
            rows, basis=RateBasis.DECIDED, stratify_by=stratify, policy=policy, as_of=DATA_EDGE
        )
        no_window = estimate_rates(
            rows, basis=RateBasis.DECIDED, stratify_by=stratify, policy=without, as_of=DATA_EDGE
        )
        assert with_window.cells == no_window.cells


class TestPayerContrast:
    """The detectability check the generator's effect sizes were set to pass."""

    def test_strong_versus_weak_payer(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        truth = answer_key["snap_003"]["detectability"]
        estimate = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=DATA_EDGE,
        )
        strong = estimate.cell_for(StratumKey((("payer", truth["strong_payer"]),)))
        weak = estimate.cell_for(StratumKey((("payer", truth["weak_payer"]),)))
        assert strong is not None and weak is not None

        assert strong.n == truth["strong_decided"] == 148
        assert strong.successes == truth["strong_recovered"] == 84
        assert weak.n == truth["weak_decided"] == 117
        assert weak.successes == truth["weak_recovered"] == 34

        contrast = compare_rate_cells(strong, weak, policy=policy)
        assert contrast.z_statistic is not None
        assert float(contrast.z_statistic) == pytest.approx(truth["two_proportion_z"], abs=1e-6)
        assert 4.4 < float(contrast.z_statistic) < 4.6
        assert contrast.p_value is not None
        assert contrast.p_value < Decimal("0.0001")

    def test_the_effect_interval_excludes_zero(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        truth = answer_key["snap_003"]["detectability"]
        estimate = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=DATA_EDGE,
        )
        strong = estimate.cell_for(StratumKey((("payer", truth["strong_payer"]),)))
        weak = estimate.cell_for(StratumKey((("payer", truth["weak_payer"]),)))
        assert strong is not None and weak is not None
        contrast = compare_rate_cells(strong, weak, policy=policy)

        assert contrast.risk_difference is not None
        realized = truth["strong_rate"] - truth["weak_rate"]
        assert float(contrast.risk_difference) == pytest.approx(realized, abs=1e-9)
        interval = contrast.risk_difference_interval
        assert interval is not None
        assert interval.excludes_zero
        assert interval.low > Decimal("0.15")


class TestTimelinessCurve:
    def test_bands_reproduce_the_answer_key_and_decay_monotonically(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        curve = delay_effect_curve(rows, policy=policy, as_of=DATA_EDGE)
        truth = {
            entry["days_to_resubmission_bucket"]: entry
            for entry in answer_key["snap_003"]["by_days_to_resubmission"]
        }
        labels = [cell.stratum.value_of(Stratifier.DELAY_BAND) for cell in curve.cells]
        assert labels == ["0-14", "15-30", "31-60", "61+"]

        rates: list[Decimal] = []
        for cell in curve.cells:
            band = cell.stratum.value_of(Stratifier.DELAY_BAND)
            assert band is not None
            entry = truth[band]
            assert cell.n == entry["decided"], band
            assert cell.successes == entry["recovered"], band
            assert cell.rate is not None
            assert float(cell.rate) == pytest.approx(
                entry["recovery_rate_of_decided"], abs=1e-9
            ), band
            rates.append(cell.rate)

        assert all(a > b for a, b in pairwise(rates)), rates
        assert float(rates[0]) == pytest.approx(0.5435356201, abs=1e-9)
        assert float(rates[-1]) == pytest.approx(0.1424148607, abs=1e-9)

    def test_every_band_carries_an_interval(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        curve = delay_effect_curve(rows, policy=policy, as_of=DATA_EDGE)
        for cell in curve.cells:
            assert cell.evidence is EvidenceLabel.MEASURED
            assert cell.interval is not None
            assert cell.interval.contains(cell.rate)  # type: ignore[arg-type]

    def test_the_first_and_last_band_intervals_do_not_overlap(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        """The decay is not an artefact of sampling noise."""
        curve = delay_effect_curve(rows, policy=policy, as_of=DATA_EDGE)
        fastest, slowest = curve.cells[0], curve.cells[-1]
        assert fastest.interval is not None and slowest.interval is not None
        assert slowest.interval.high < fastest.interval.low


class TestFilingDeadline:
    def test_past_a_confirmed_deadline_the_decided_rate_is_zero(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        estimate = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.FILING_POSITION, Stratifier.FILING_RULE),
            policy=policy,
            as_of=DATA_EDGE,
        )
        cell = estimate.cell_for(
            StratumKey((("filing_position", "past_deadline"), ("filing_rule", "confirmed")))
        )
        assert cell is not None
        truth = next(
            entry
            for entry in answer_key["snap_003"]["by_filing_position"]
            if entry["filing_position"] == "past_deadline"
            and entry["filing_rule_authority"] == "confirmed"
        )
        assert cell.n == truth["decided"] == 39
        assert cell.successes == truth["recovered"] == 0
        assert cell.rate == Decimal(0)

    def test_the_zero_rate_still_publishes_an_honest_upper_bound(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        """0 of 39 is not certainty, and the interval must not claim it is.

        This is the concrete case the Wilson-over-Wald choice was made for:
        Wald's interval here is exactly ``[0, 0]``.
        """
        estimate = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.FILING_POSITION, Stratifier.FILING_RULE),
            policy=policy,
            as_of=DATA_EDGE,
        )
        cell = estimate.cell_for(
            StratumKey((("filing_position", "past_deadline"), ("filing_rule", "confirmed")))
        )
        assert cell is not None and cell.interval is not None
        assert cell.interval.low == Decimal(0)
        assert cell.interval.high > Decimal("0.05")
        assert float(cell.interval.high) == pytest.approx(0.0896668537, abs=1e-9)

    def test_the_cliff_is_shallower_where_the_limit_is_only_a_default(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        """Treating every configured limit as governed over-predicts the cliff."""
        estimate = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.FILING_POSITION, Stratifier.FILING_RULE),
            policy=policy,
            as_of=DATA_EDGE,
        )
        unconfirmed = estimate.cell_for(
            StratumKey(
                (("filing_position", "past_deadline"), ("filing_rule", "requires_confirmation"))
            )
        )
        assert unconfirmed is not None
        truth = next(
            entry
            for entry in answer_key["snap_003"]["by_filing_position"]
            if entry["filing_position"] == "past_deadline"
            and entry["filing_rule_authority"] == "requires_confirmation"
        )
        assert unconfirmed.n == truth["decided"] == 40
        assert unconfirmed.successes == truth["recovered"] == 3
        assert unconfirmed.rate == Decimal("0.075")

    def test_within_deadline_cells_match_the_answer_key(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        estimate = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.FILING_POSITION, Stratifier.FILING_RULE),
            policy=policy,
            as_of=DATA_EDGE,
        )
        for rule, expected_n, expected_recovered in (
            ("confirmed", 595, 255),
            ("requires_confirmation", 1859, 829),
        ):
            cell = estimate.cell_for(
                StratumKey((("filing_position", "within_deadline"), ("filing_rule", rule)))
            )
            assert cell is not None, rule
            assert cell.n == expected_n, rule
            assert cell.successes == expected_recovered, rule


class TestDurations:
    def test_median_days_to_resubmission_by_class(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        """ANSWER_KEY.md: 9 / 13 / 20 / 31 / 49, fixable classes first."""
        estimate = estimate_durations(
            rows,
            measure=DurationMeasure.DAYS_TO_RESUBMISSION,
            stratify_by=(Stratifier.RECOVERY_CLASS,),
            policy=policy,
            as_of=DATA_EDGE,
        )
        medians = {
            cell.stratum.value_of(Stratifier.RECOVERY_CLASS): cell.median_days
            for cell in estimate.cells
        }
        for name, expected in (
            ("CODING", 9),
            ("REGISTRATION", 13),
            ("ROUTING", 20),
            ("CLINICAL", 31),
            ("FINAL", 49),
        ):
            assert medians[name] == Decimal(expected).quantize(Decimal("1E-10")), name

    def test_quartiles_are_ordered_in_every_class(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        estimate = estimate_durations(
            rows,
            measure=DurationMeasure.DAYS_TO_RESUBMISSION,
            stratify_by=(Stratifier.RECOVERY_CLASS,),
            policy=policy,
            as_of=DATA_EDGE,
        )
        for cell in estimate.cells:
            assert cell.p25_days is not None
            assert cell.median_days is not None
            assert cell.p75_days is not None
            assert cell.p25_days <= cell.median_days <= cell.p75_days

    def test_unresubmitted_denials_are_disclosed_not_timed(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        estimate = estimate_durations(
            rows, measure=DurationMeasure.DAYS_TO_RESUBMISSION, policy=policy, as_of=DATA_EDGE
        )
        assert estimate.disclosure.excluded_not_pursued == 2653
        assert estimate.cells[0].n == 2745  # every chain, decided or not


class TestExpectedRecovery:
    """The headline figure, and the arithmetic that must produce it.

    The construction under test has three parts, and the two that were
    missing are the two that made the old figure four times too big:

    1. the open dollars are split by filing position;
    2. each side is priced at *that side's* decided rate, this population's
       own where its cohort clears the floor and the read's otherwise;
    3. the result is multiplied by what a win has actually returned on the
       denied dollar.

    :meth:`test_the_published_total_is_reproducible_to_the_cent` rebuilds
    all three from the warehouse rows without calling the estimator once,
    and requires equality to the cent. A tolerance here would hide exactly
    the class of error this test exists for.
    """

    @staticmethod
    def _priced(
        rows: list[DenialRow], policy: EstimationPolicy, stratify: tuple[Stratifier, ...]
    ) -> Any:
        target = [row for row in rows if not row.recovery_status.is_decided]
        return expected_recovery(
            target,
            rates=estimate_rates(
                rows,
                basis=RateBasis.DECIDED,
                stratify_by=stratify,
                policy=policy,
                as_of=DATA_EDGE,
            ),
            deadlines=deadline_rates(
                rows, stratify_by=stratify, policy=policy, as_of=DATA_EDGE
            ),
            severity=severity_ratios(
                rows, stratify_by=stratify, policy=policy, as_of=DATA_EDGE
            ),
            stratify_by=stratify,
            policy=policy,
            as_of=DATA_EDGE,
        )

    def test_open_inventory_is_priced_only_where_a_cohort_supports_it(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        result = self._priced(rows, policy, (Stratifier.PAYER, Stratifier.RECOVERY_CLASS))
        target = [row for row in rows if not row.recovery_status.is_decided]

        assert len(target) == 2653 + 212
        assert result.total_open_dollars_cents == sum(row.denied_amount_cents for row in target)
        assert (
            result.priced_open_dollars_cents
            + result.unpriced_open_dollars_cents
            + result.unpriced_position_dollars_cents
            == result.total_open_dollars_cents
        )
        # Payer x class over 12 payers and 5 classes leaves real thin cells,
        # and they must be refused rather than filled in.
        assert result.refused_strata
        assert result.unpriced_open_dollars_cents > 0
        assert all(s.expected_cents is None for s in result.refused_strata)
        assert all(s.expected_cents is not None for s in result.strata)

    def test_the_total_never_includes_a_refused_stratum(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        result = self._priced(rows, policy, (Stratifier.PAYER, Stratifier.RECOVERY_CLASS))
        recomputed = sum(s.expected_cents or 0 for s in result.strata)
        assert recomputed == result.total_expected_cents
        assert (
            result.total_expected_interval.low_cents
            <= result.total_expected_cents
            <= result.total_expected_interval.high_cents
        )

    def test_expected_dollars_never_exceed_the_priced_dollars(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        result = self._priced(rows, policy, (Stratifier.RECOVERY_CLASS,))
        assert 0 < result.total_expected_cents < result.priced_open_dollars_cents
        assert result.total_expected_interval.high_cents <= result.priced_open_dollars_cents

    def test_the_deadline_split_partitions_open_dollars(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        result = self._priced(rows, policy, (Stratifier.RECOVERY_CLASS,))
        assert (
            result.catchable_dollars_cents
            + result.deadline_passed_dollars_cents
            + result.deadline_unknown_dollars_cents
            == result.total_open_dollars_cents
        )
        # Every plan in this warehouse carries a configured limit, so
        # nothing lands in the unknown bucket — but both other buckets are
        # populated, which is what makes the split worth publishing.
        assert result.deadline_unknown_dollars_cents == 0
        assert result.catchable_dollars_cents > 0
        assert result.deadline_passed_dollars_cents > 0

    def test_the_two_deadline_rates_match_the_answer_key(
        self, rows: list[DenialRow], policy: EstimationPolicy, answer_key: dict[str, Any]
    ) -> None:
        """The rates the pricing quotes are the realized ones, not new ones."""
        result = self._priced(rows, policy, (Stratifier.PAYER,))
        truth = {
            (entry["filing_position"], entry["filing_rule_authority"]): entry
            for entry in answer_key["snap_003"]["by_filing_position"]
        }
        for position, cell in (
            ("within_deadline", result.within_deadline_rate),
            ("past_deadline", result.past_deadline_rate),
        ):
            decided = sum(
                truth[(position, rule)]["decided"]
                for rule in ("confirmed", "requires_confirmation")
            )
            recovered = sum(
                truth[(position, rule)]["recovered"]
                for rule in ("confirmed", "requires_confirmation")
            )
            assert cell is not None, position
            assert cell.n == decided, position
            assert cell.successes == recovered, position
        assert result.within_deadline_rate.n == 2454  # type: ignore[union-attr]
        assert result.past_deadline_rate.n == 79  # type: ignore[union-attr]

    def test_a_win_is_priced_at_what_a_win_has_returned(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        """No win in this warehouse returns the full denied amount."""
        wins = [
            row
            for row in rows
            if row.recovery_status.is_decided and row.recovery_status.is_recovered
        ]
        assert wins
        assert not any(row.recovered_amount_cents == row.denied_amount_cents for row in wins)
        result = self._priced(rows, policy, (Stratifier.PAYER,))
        assert result.severity is not None
        population = result.severity.population
        assert population.is_measured and population.ratio is not None
        assert population.recovered_cents == sum(row.recovered_amount_cents for row in wins)
        assert population.denied_cents == sum(row.denied_amount_cents for row in wins)
        assert Decimal("0.30") < population.ratio < Decimal("0.60")

    def test_the_published_total_is_reproducible_to_the_cent(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        """Rebuild the headline from the rows, without the estimator.

        This is the whole point of the fix. The old composition was
        ``count rate x full denied dollars``, which came to 42% of the open
        book. The construction below is the defensible one, it is written
        here from the published rules rather than read off the result, and
        the two must agree exactly.
        """
        stratify = (Stratifier.PAYER, Stratifier.RECOVERY_CLASS)
        result = self._priced(rows, policy, stratify)

        def key(row: DenialRow) -> tuple[str, str]:
            return (row.payer_name, row.recovery_class)

        def position_of(row: DenialRow, *, decided: bool) -> str:
            if row.timely_filing_days is None:
                return "unknown"
            deadline = row.service_date + timedelta(days=row.timely_filing_days)
            reference = (
                (row.resubmission_date or DATA_EDGE) if decided else DATA_EDGE
            )
            return "past_deadline" if reference > deadline else "within_deadline"

        def rate(successes: int, n: int) -> Decimal:
            return (Decimal(successes) / Decimal(n)).quantize(
                Decimal("1E-10"), rounding=ROUND_HALF_EVEN
            )

        decided = [row for row in rows if row.recovery_status.is_decided]
        open_rows = [row for row in rows if not row.recovery_status.is_decided]

        stratum_n: Counter[tuple[str, str]] = Counter()
        position_n: Counter[tuple[tuple[str, str], str]] = Counter()
        position_wins: Counter[tuple[tuple[str, str], str]] = Counter()
        pooled_n: Counter[str] = Counter()
        pooled_wins: Counter[str] = Counter()
        wins_n: Counter[tuple[str, str]] = Counter()
        wins_denied: Counter[tuple[str, str]] = Counter()
        wins_recovered: Counter[tuple[str, str]] = Counter()
        for row in decided:
            won = row.recovery_status.is_recovered
            place = position_of(row, decided=True)
            stratum_n[key(row)] += 1
            position_n[(key(row), place)] += 1
            pooled_n[place] += 1
            if won:
                position_wins[(key(row), place)] += 1
                pooled_wins[place] += 1
                wins_n[key(row)] += 1
                wins_denied[key(row)] += row.denied_amount_cents
                wins_recovered[key(row)] += row.recovered_amount_cents

        population_severity = rate(
            sum(wins_recovered.values()), sum(wins_denied.values())
        ) if sum(wins_denied.values()) else None
        assert population_severity is not None
        assert sum(wins_n.values()) >= MIN_COHORT

        dollars: dict[tuple[tuple[str, str], str], int] = {}
        for row in open_rows:
            dollars[(key(row), position_of(row, decided=False))] = (
                dollars.get((key(row), position_of(row, decided=False)), 0)
                + row.denied_amount_cents
            )

        expected = 0
        unpriced_thin = 0
        unpriced_position = 0
        for (stratum, place), cents in sorted(dollars.items()):
            if stratum_n[stratum] < MIN_COHORT:
                unpriced_thin += cents
                continue
            severity = (
                rate(wins_recovered[stratum], wins_denied[stratum])
                if wins_n[stratum] >= MIN_COHORT and wins_denied[stratum]
                else population_severity
            )
            if place == "unknown":
                unpriced_position += cents
                continue
            if position_n[(stratum, place)] >= MIN_COHORT:
                applied = rate(position_wins[(stratum, place)], position_n[(stratum, place)])
            elif pooled_n[place] >= MIN_COHORT:
                applied = rate(pooled_wins[place], pooled_n[place])
            else:
                unpriced_position += cents
                continue
            expected += int(
                (severity * applied * Decimal(cents)).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            )

        assert expected == result.total_expected_cents
        assert unpriced_thin == result.unpriced_open_dollars_cents
        assert unpriced_position == result.unpriced_position_dollars_cents

    def test_the_headline_is_no_longer_forty_two_percent_of_the_open_book(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        """The review's repro, as a regression.

        ``headline / total_open`` read 0.4244 when the composition priced a
        win at the full denied amount and ignored the filing deadline. The
        two corrections are multiplicative and both are well below one, so
        the ratio cannot come back near 0.42 without one of them being
        dropped again.
        """
        result = self._priced(rows, policy, (Stratifier.PAYER,))
        ratio = Decimal(result.total_expected_cents) / Decimal(
            result.total_open_dollars_cents
        )
        assert ratio < Decimal("0.15")
        # The published band must contain the honest figure, which the old
        # one did not: its lower bound sat 44% above the truth.
        assert (
            result.total_expected_interval.low_cents
            <= result.total_expected_cents
            <= result.total_expected_interval.high_cents
        )


class TestDeterminism:
    def test_the_whole_pipeline_is_reproducible(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        stratify = (Stratifier.PAYER, Stratifier.RECOVERY_CLASS)
        first = estimate_rates(
            rows, basis=RateBasis.DECIDED, stratify_by=stratify, policy=policy, as_of=DATA_EDGE
        )
        for _ in range(3):
            assert (
                estimate_rates(
                    rows,
                    basis=RateBasis.DECIDED,
                    stratify_by=stratify,
                    policy=policy,
                    as_of=DATA_EDGE,
                )
                == first
            )

    def test_reversing_the_row_order_changes_nothing(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        stratify = (Stratifier.PAYER,)
        forward = estimate_rates(
            rows, basis=RateBasis.DECIDED, stratify_by=stratify, policy=policy, as_of=DATA_EDGE
        )
        backward = estimate_rates(
            list(reversed(rows)),
            basis=RateBasis.DECIDED,
            stratify_by=stratify,
            policy=policy,
            as_of=DATA_EDGE,
        )
        assert forward == backward


class TestCohortFloorOnRealData:
    def test_no_published_cell_is_below_the_floor(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        for stratify in (
            (Stratifier.PAYER,),
            (Stratifier.PLAN,),
            (Stratifier.PAYER, Stratifier.RECOVERY_CLASS),
            (Stratifier.PLAN, Stratifier.RECOVERY_CLASS),
        ):
            estimate = estimate_rates(
                rows,
                basis=RateBasis.DECIDED,
                stratify_by=stratify,
                policy=policy,
                as_of=DATA_EDGE,
            )
            for cell in estimate.cells:
                if cell.rate is not None:
                    assert cell.n >= MIN_COHORT, (stratify, cell.stratum.label, cell.n)
                else:
                    assert cell.evidence is EvidenceLabel.REFUSED_THIN

    def test_a_fine_stratification_produces_real_refusals(
        self, rows: list[DenialRow], policy: EstimationPolicy
    ) -> None:
        """Plan x class is thin enough that refusal must actually happen."""
        estimate = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PLAN, Stratifier.RECOVERY_CLASS),
            policy=policy,
            as_of=DATA_EDGE,
        )
        assert estimate.refused
        assert estimate.measured
        assert all(cell.rate is None for cell in estimate.refused)
