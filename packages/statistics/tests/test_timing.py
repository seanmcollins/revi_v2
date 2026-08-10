"""Duration distributions and the delay effect curve."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

import pytest

from revi_statistics import delay_effect_curve, estimate_durations, quantile
from revi_statistics_contracts.contract import (
    Band,
    DurationMeasure,
    EstimationPolicy,
    EvidenceLabel,
    MaturityPolicy,
    RecoveryStatus,
    Stratifier,
)

from .conftest import AS_OF, RowFactory


class TestQuantile:
    def test_matches_the_type_seven_convention(self) -> None:
        values = [1, 2, 3, 4]
        # h = (n-1)*q = 3*0.5 = 1.5 -> midway between 2 and 3.
        assert quantile(values, Decimal("0.5")) == Decimal("2.5000000000")
        # 3*0.25 = 0.75 -> 1 + 0.75*(2-1) = 1.75
        assert quantile(values, Decimal("0.25")) == Decimal("1.7500000000")
        assert quantile(values, Decimal("0.75")) == Decimal("3.2500000000")

    def test_endpoints(self) -> None:
        values = [5, 1, 9, 3]
        assert quantile(values, Decimal(0)) == Decimal("1.0000000000")
        assert quantile(values, Decimal(1)) == Decimal("9.0000000000")

    def test_single_observation(self) -> None:
        assert quantile([7], Decimal("0.5")) == Decimal("7.0000000000")

    def test_odd_length_median_is_the_middle_value(self) -> None:
        assert quantile([1, 2, 3, 4, 5], Decimal("0.5")) == Decimal("3.0000000000")

    def test_is_order_independent(self) -> None:
        assert quantile([9, 1, 5, 3], Decimal("0.5")) == quantile([1, 3, 5, 9], Decimal("0.5"))

    def test_rejects_an_empty_sample(self) -> None:
        with pytest.raises(ValueError, match="empty sample"):
            quantile([], Decimal("0.5"))


class TestDurations:
    def test_unpursued_rows_are_excluded_not_timed(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [
            *[make_row(status=RecoveryStatus.DENIED_AGAIN, days_to_resubmission=10) for _ in range(40)],
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED) for _ in range(500)],
        ]
        estimate = estimate_durations(
            rows, measure=DurationMeasure.DAYS_TO_RESUBMISSION, policy=policy, as_of=AS_OF
        )
        assert estimate.cells[0].n == 40
        assert estimate.cells[0].median_days == Decimal("10.0000000000")
        assert estimate.disclosure.excluded_not_pursued == 500

    def test_open_chains_have_no_outcome_duration(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """The data edge is never used as a stand-in outcome date."""
        rows = [
            *[make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(35)],
            *[make_row(status=RecoveryStatus.RESUBMITTED_PENDING) for _ in range(200)],
        ]
        estimate = estimate_durations(
            rows, measure=DurationMeasure.DAYS_TO_OUTCOME, policy=policy, as_of=AS_OF
        )
        assert estimate.cells[0].n == 35
        assert estimate.disclosure.excluded_open_undecided == 200

    def test_outcome_duration_is_measured_from_the_denial(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        # The builder puts the outcome 14 days after a resubmission that is
        # itself `days_to_resubmission` after the denial.
        rows = [
            make_row(status=RecoveryStatus.RECOVERED_FULL, days_to_resubmission=20) for _ in range(30)
        ]
        estimate = estimate_durations(
            rows, measure=DurationMeasure.DAYS_TO_OUTCOME, policy=policy, as_of=AS_OF
        )
        assert estimate.cells[0].median_days == Decimal("34.0000000000")

    def test_a_thin_stratum_publishes_no_quantiles(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(4)]
        cell = estimate_durations(
            rows, measure=DurationMeasure.DAYS_TO_RESUBMISSION, policy=policy, as_of=AS_OF
        ).cells[0]
        assert cell.evidence is EvidenceLabel.REFUSED_THIN
        assert cell.median_days is None
        assert cell.p25_days is None
        assert cell.p75_days is None
        assert cell.n == 4

    def test_quartiles_are_ordered(self, make_row: RowFactory, policy: EstimationPolicy) -> None:
        rows = [
            make_row(status=RecoveryStatus.DENIED_AGAIN, days_to_resubmission=day)
            for day in range(1, 61)
        ]
        cell = estimate_durations(
            rows, measure=DurationMeasure.DAYS_TO_RESUBMISSION, policy=policy, as_of=AS_OF
        ).cells[0]
        assert cell.p25_days is not None and cell.median_days is not None and cell.p75_days is not None
        assert cell.p25_days < cell.median_days < cell.p75_days
        assert cell.min_days == 1
        assert cell.max_days == 60


class TestDelayEffectCurve:
    def test_bands_come_back_in_declared_order_not_alphabetical(
        self, make_row: RowFactory
    ) -> None:
        """A curve is read along its axis; alphabetical order would ruin it."""
        policy = EstimationPolicy(
            min_cohort=1,
            maturity=MaturityPolicy(default_days=30),
            # Labels chosen so alphabetical order differs from band order.
            delay_bands=(Band("5-9", 5, 10), Band("10-14", 10, 15), Band("15+", 15, None)),
        )
        rows = [
            make_row(status=RecoveryStatus.DENIED_AGAIN, days_to_resubmission=day)
            for day in (6, 12, 20)
        ]
        curve = delay_effect_curve(rows, policy=policy, as_of=AS_OF)
        labels = [cell.stratum.value_of(Stratifier.DELAY_BAND) for cell in curve.cells]
        assert labels == ["5-9", "10-14", "15+"]
        assert sorted(labels) != labels  # the ordering is doing real work

    def test_recovers_a_planted_decay(self, make_row: RowFactory) -> None:
        policy = EstimationPolicy(
            min_cohort=10,
            maturity=MaturityPolicy(default_days=30),
            delay_bands=(Band("0-14", 0, 15), Band("15-30", 15, 31), Band("31+", 31, None)),
        )
        rows: list[object] = []
        for day, wins, losses in ((7, 80, 20), (20, 50, 50), (40, 10, 90)):
            rows += [
                make_row(status=RecoveryStatus.RECOVERED_FULL, days_to_resubmission=day)
                for _ in range(wins)
            ]
            rows += [
                make_row(status=RecoveryStatus.DENIED_AGAIN, days_to_resubmission=day)
                for _ in range(losses)
            ]
        curve = delay_effect_curve(rows, policy=policy, as_of=AS_OF)  # type: ignore[arg-type]
        rates = [cell.rate for cell in curve.cells]
        assert rates == [Decimal("0.8"), Decimal("0.5"), Decimal("0.1")]
        assert all(a > b for a, b in pairwise(rates))  # type: ignore[operator]

    def test_within_class_stratification_is_supported(self, make_row: RowFactory) -> None:
        policy = EstimationPolicy(
            min_cohort=1,
            maturity=MaturityPolicy(default_days=30),
            delay_bands=(Band("0-14", 0, 15), Band("15+", 15, None)),
        )
        rows = [
            make_row(
                status=RecoveryStatus.DENIED_AGAIN,
                days_to_resubmission=day,
                recovery_class=recovery_class,
            )
            for recovery_class in ("CODING", "CLINICAL")
            for day in (5, 40)
        ]
        curve = delay_effect_curve(
            rows, policy=policy, as_of=AS_OF, within=(Stratifier.RECOVERY_CLASS,)
        )
        assert len(curve.cells) == 4
        # Grouped by class first, band order within each class.
        keys = [
            (
                cell.stratum.value_of(Stratifier.RECOVERY_CLASS),
                cell.stratum.value_of(Stratifier.DELAY_BAND),
            )
            for cell in curve.cells
        ]
        assert keys == [
            ("CLINICAL", "0-14"),
            ("CLINICAL", "15+"),
            ("CODING", "0-14"),
            ("CODING", "15+"),
        ]

    def test_requires_delay_bands(self, make_row: RowFactory) -> None:
        bare = EstimationPolicy(min_cohort=1, maturity=MaturityPolicy(default_days=30))
        with pytest.raises(ValueError, match=r"requires EstimationPolicy\.delay_bands"):
            delay_effect_curve([make_row()], policy=bare, as_of=AS_OF)
