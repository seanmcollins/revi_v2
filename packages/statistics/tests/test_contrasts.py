"""Two-cohort comparison: routing, refusal, and effect size."""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_statistics import compare_cohorts, compare_rate_cells, contrast_counts, estimate_rates
from revi_statistics_contracts.contract import (
    ContrastTest,
    EstimationPolicy,
    MaturityPolicy,
    RateBasis,
    RecoveryStatus,
    Stratifier,
    StratumKey,
)

from .conftest import AS_OF, RowFactory


class TestRouting:
    def test_well_populated_tables_use_the_z_test(self, policy: EstimationPolicy) -> None:
        contrast = contrast_counts(
            left_label="strong",
            left_successes=84,
            left_n=148,
            right_label="weak",
            right_successes=34,
            right_n=117,
            policy=policy,
        )
        assert contrast.test is ContrastTest.TWO_PROPORTION_Z
        assert contrast.z_statistic is not None
        assert float(contrast.z_statistic) == pytest.approx(4.504832544, abs=1e-8)

    def test_a_thin_cell_routes_to_the_exact_test(self) -> None:
        """Small expected counts, so the normal approximation is not used."""
        policy = EstimationPolicy(min_cohort=10, maturity=MaturityPolicy(default_days=30))
        contrast = contrast_counts(
            left_label="a",
            left_successes=0,
            left_n=12,
            right_label="b",
            right_successes=9,
            right_n=11,
            policy=policy,
        )
        assert contrast.test is ContrastTest.FISHERS_EXACT
        assert contrast.z_statistic is None
        assert contrast.p_value is not None
        assert contrast.p_value < Decimal("0.001")

    def test_the_published_test_name_says_which_ran(self, policy: EstimationPolicy) -> None:
        big = contrast_counts(
            left_label="a",
            left_successes=200,
            left_n=400,
            right_label="b",
            right_successes=150,
            right_n=400,
            policy=policy,
        )
        assert big.test is ContrastTest.TWO_PROPORTION_Z


class TestRefusal:
    def test_refuses_when_either_arm_is_below_the_floor(self, policy: EstimationPolicy) -> None:
        contrast = contrast_counts(
            left_label="big",
            left_successes=40,
            left_n=80,
            right_label="tiny",
            right_successes=1,
            right_n=4,
            policy=policy,
        )
        assert contrast.is_refused
        assert contrast.p_value is None
        assert contrast.risk_difference is None
        assert contrast.risk_difference_interval is None
        assert contrast.refusal_reason is not None
        assert "tiny n=4" in contrast.refusal_reason
        # The sizes are still published — they are the evidence for refusing.
        assert contrast.left.n == 80
        assert contrast.right.n == 4

    def test_refuses_when_both_arms_are_thin(self, policy: EstimationPolicy) -> None:
        contrast = contrast_counts(
            left_label="a",
            left_successes=1,
            left_n=3,
            right_label="b",
            right_successes=2,
            right_n=5,
            policy=policy,
        )
        assert contrast.is_refused
        assert contrast.refusal_reason is not None
        assert "a n=3" in contrast.refusal_reason
        assert "b n=5" in contrast.refusal_reason

    def test_a_refused_arm_publishes_no_rate(self, policy: EstimationPolicy) -> None:
        contrast = contrast_counts(
            left_label="big",
            left_successes=40,
            left_n=80,
            right_label="tiny",
            right_successes=1,
            right_n=4,
            policy=policy,
        )
        assert contrast.left.rate is not None
        assert contrast.right.rate is None
        assert contrast.right.interval is None


class TestEffectSize:
    def test_risk_difference_and_interval(self, policy: EstimationPolicy) -> None:
        contrast = contrast_counts(
            left_label="strong",
            left_successes=84,
            left_n=148,
            right_label="weak",
            right_successes=34,
            right_n=117,
            policy=policy,
        )
        assert contrast.risk_difference is not None
        assert float(contrast.risk_difference) == pytest.approx(0.276969277, abs=1e-8)
        interval = contrast.risk_difference_interval
        assert interval is not None
        assert interval.excludes_zero
        assert interval.contains(contrast.risk_difference)

    def test_no_difference_gives_an_interval_straddling_zero(
        self, policy: EstimationPolicy
    ) -> None:
        contrast = contrast_counts(
            left_label="a",
            left_successes=50,
            left_n=100,
            right_label="b",
            right_successes=50,
            right_n=100,
            policy=policy,
        )
        assert contrast.risk_difference == Decimal(0)
        assert contrast.risk_difference_interval is not None
        assert not contrast.risk_difference_interval.excludes_zero
        assert contrast.p_value == Decimal(1).quantize(Decimal("1E-10"))

    def test_swapping_the_arms_flips_the_sign(self, policy: EstimationPolicy) -> None:
        forward = contrast_counts(
            left_label="a",
            left_successes=84,
            left_n=148,
            right_label="b",
            right_successes=34,
            right_n=117,
            policy=policy,
        )
        backward = contrast_counts(
            left_label="b",
            left_successes=34,
            left_n=117,
            right_label="a",
            right_successes=84,
            right_n=148,
            policy=policy,
        )
        assert forward.risk_difference is not None and backward.risk_difference is not None
        assert forward.risk_difference == -backward.risk_difference
        assert forward.p_value == backward.p_value


class TestFromRowsAndCells:
    def test_compare_cohorts_matches_compare_rate_cells(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        left_rows = [
            *[make_row(status=RecoveryStatus.RECOVERED_FULL, payer="Strong") for _ in range(60)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN, payer="Strong") for _ in range(40)],
            *[make_row(status=RecoveryStatus.RESUBMITTED_PENDING, payer="Strong") for _ in range(25)],
        ]
        right_rows = [
            *[make_row(status=RecoveryStatus.RECOVERED_FULL, payer="Weak") for _ in range(30)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN, payer="Weak") for _ in range(70)],
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Weak") for _ in range(90)],
        ]
        from_rows = compare_cohorts(
            left_rows,
            right_rows,
            left_label="payer=Strong",
            right_label="payer=Weak",
            basis=RateBasis.DECIDED,
            policy=policy,
            as_of=AS_OF,
        )
        estimate = estimate_rates(
            [*left_rows, *right_rows],
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        strong = estimate.cell_for(StratumKey((("payer", "Strong"),)))
        weak = estimate.cell_for(StratumKey((("payer", "Weak"),)))
        assert strong is not None and weak is not None
        from_cells = compare_rate_cells(strong, weak, policy=policy)
        assert from_rows.p_value == from_cells.p_value
        assert from_rows.risk_difference == from_cells.risk_difference
        assert from_rows.left.n == from_cells.left.n == 100

    def test_contrasting_different_bases_is_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(40)]
        decided = estimate_rates(rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF)
        pursuit = estimate_rates(rows, basis=RateBasis.PURSUIT, policy=policy, as_of=AS_OF)
        with pytest.raises(ValueError, match="different conditionals"):
            compare_rate_cells(decided.cells[0], pursuit.cells[0], policy=policy)

    def test_open_chains_do_not_enter_a_decided_contrast(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        base_left = [
            *[make_row(status=RecoveryStatus.RECOVERED_FULL) for _ in range(50)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(50)],
        ]
        base_right = [
            *[make_row(status=RecoveryStatus.RECOVERED_FULL) for _ in range(30)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(70)],
        ]

        def run(left: list[object], right: list[object]) -> object:
            return compare_cohorts(
                left,  # type: ignore[arg-type]
                right,  # type: ignore[arg-type]
                left_label="l",
                right_label="r",
                basis=RateBasis.DECIDED,
                policy=policy,
                as_of=AS_OF,
            )

        clean = run(base_left, base_right)
        noisy = run(
            [*base_left, *[make_row(status=RecoveryStatus.NOT_RESUBMITTED) for _ in range(400)]],
            [*base_right, *[make_row(status=RecoveryStatus.RESUBMITTED_PENDING) for _ in range(400)]],
        )
        assert clean == noisy
