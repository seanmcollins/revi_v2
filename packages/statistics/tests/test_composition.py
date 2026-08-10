"""Expected-recoverable composition: what it prices, and what it refuses to."""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_statistics import estimate_rates, expected_recovery
from revi_statistics_contracts.contract import (
    EstimationPolicy,
    EvidenceLabel,
    MaturityPolicy,
    RateBasis,
    RecoveryStatus,
    Stratifier,
)

from .conftest import AS_OF, RowFactory


def _evidence_rows(make_row: RowFactory, payer: str, wins: int, losses: int) -> list[object]:
    return [
        *[make_row(status=RecoveryStatus.RECOVERED_FULL, payer=payer) for _ in range(wins)],
        *[make_row(status=RecoveryStatus.DENIED_AGAIN, payer=payer) for _ in range(losses)],
    ]


class TestPricing:
    def test_expected_dollars_are_rate_times_open_dollars(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = _evidence_rows(make_row, "Acme", wins=25, losses=25)  # 50%
        rates = estimate_rates(
            evidence,  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Acme", denied_cents=1_000_00)
            for _ in range(10)
        ]
        result = expected_recovery(
            target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert result.total_open_dollars_cents == 1_000_000
        assert result.total_expected_cents == 500_000
        assert result.strata[0].evidence is EvidenceLabel.MEASURED

    def test_interval_brackets_the_point_and_flags_its_assumption(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = _evidence_rows(make_row, "Acme", wins=25, losses=25)
        rates = estimate_rates(
            evidence,  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Acme", denied_cents=500_00)
            for _ in range(20)
        ]
        result = expected_recovery(
            target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert (
            result.total_expected_interval.low_cents
            <= result.total_expected_cents
            <= result.total_expected_interval.high_cents
        )
        assert result.total_expected_interval.low_cents < result.total_expected_interval.high_cents
        assert result.interval_assumes_independence is True


class TestNoPriorIsEverSubstituted:
    """The behaviour the whole capability exists to guarantee."""

    def test_a_thin_stratum_is_excluded_from_the_total_and_listed(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = [
            *_evidence_rows(make_row, "Big", wins=30, losses=30),
            # Only 4 decided rows for Small — below the floor of 30.
            *_evidence_rows(make_row, "Small", wins=2, losses=2),
        ]
        rates = estimate_rates(
            evidence,  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            *[
                make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Big", denied_cents=100_00)
                for _ in range(10)
            ],
            *[
                make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Small", denied_cents=100_00)
                for _ in range(10)
            ],
        ]
        result = expected_recovery(
            target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert len(result.strata) == 1
        assert len(result.refused_strata) == 1
        refused = result.refused_strata[0]
        assert refused.stratum.value_of(Stratifier.PAYER) == "Small"
        assert refused.expected_cents is None
        assert refused.open_dollars_cents == 100_000
        # The thin stratum's dollars are visible but not in the total.
        assert result.unpriced_open_dollars_cents == 100_000
        assert result.priced_open_dollars_cents == 100_000
        assert result.total_expected_cents == 50_000  # Big's 50% only
        assert result.unpriced_share == Decimal("0.5")

    def test_a_stratum_with_no_evidence_at_all_is_refused_with_n_zero(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rates = estimate_rates(
            _evidence_rows(make_row, "Known", wins=30, losses=30),  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="NeverSeen", denied_cents=999_00)
            for _ in range(5)
        ]
        result = expected_recovery(
            target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert result.total_expected_cents == 0
        assert len(result.refused_strata) == 1
        assert result.refused_strata[0].rate_cell.n == 0
        assert result.unpriced_open_dollars_cents == 499_500

    def test_the_pooled_rate_is_not_borrowed_for_a_thin_cell(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """If a prior were substituted the total would be larger. It is not."""
        evidence = [
            *_evidence_rows(make_row, "Big", wins=60, losses=0),  # 100%
            *_evidence_rows(make_row, "Small", wins=1, losses=0),
        ]
        rates = estimate_rates(
            evidence,  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Small", denied_cents=1_000_00)
        ]
        result = expected_recovery(
            target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert result.total_expected_cents == 0
        assert result.strata == ()


class TestDeadlineSplit:
    def test_open_dollars_split_three_ways(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rates = estimate_rates(
            _evidence_rows(make_row, "Acme", wins=25, losses=25),  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            # Service date 30 days before a denial 10 days old: 40 days of
            # runway used against a 180-day limit — still catchable.
            make_row(
                status=RecoveryStatus.NOT_RESUBMITTED,
                payer="Acme",
                age_days=10,
                timely_filing_days=180,
                denied_cents=100_00,
            ),
            # A 90-day limit against a denial 400 days old — long past.
            make_row(
                status=RecoveryStatus.NOT_RESUBMITTED,
                payer="Acme",
                age_days=400,
                timely_filing_days=90,
                denied_cents=200_00,
            ),
            # No configured limit at all: unknown, its own bucket.
            make_row(
                status=RecoveryStatus.NOT_RESUBMITTED,
                payer="Acme",
                timely_filing_days=None,
                denied_cents=400_00,
            ),
        ]
        result = expected_recovery(
            target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert result.catchable_dollars_cents == 10_000
        assert result.deadline_passed_dollars_cents == 20_000
        assert result.deadline_unknown_dollars_cents == 40_000
        assert (
            result.catchable_dollars_cents
            + result.deadline_passed_dollars_cents
            + result.deadline_unknown_dollars_cents
            == result.total_open_dollars_cents
        )

    def test_unknown_is_never_folded_into_catchable(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rates = estimate_rates(
            _evidence_rows(make_row, "Acme", wins=25, losses=25),  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            make_row(
                status=RecoveryStatus.NOT_RESUBMITTED, payer="Acme", timely_filing_days=None
            )
            for _ in range(5)
        ]
        result = expected_recovery(
            target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert result.catchable_dollars_cents == 0
        assert result.deadline_unknown_dollars_cents == 500_000


class TestGuards:
    def test_a_pursuit_estimate_is_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [make_row(status=RecoveryStatus.DENIED_AGAIN, payer="Acme") for _ in range(40)]
        pursuit = estimate_rates(
            rows,
            basis=RateBasis.PURSUIT,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Acme")]
        with pytest.raises(ValueError, match="requires a DECIDED rate estimate"):
            expected_recovery(
                target, rates=pursuit, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
            )

    def test_a_mismatched_stratification_is_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [make_row(status=RecoveryStatus.DENIED_AGAIN, payer="Acme") for _ in range(40)]
        rates = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        with pytest.raises(ValueError, match="must share a stratification"):
            expected_recovery(
                [make_row(status=RecoveryStatus.NOT_RESUBMITTED)],
                rates=rates,
                stratify_by=(Stratifier.PLAN,),
                policy=policy,
                as_of=AS_OF,
            )

    def test_a_decided_row_in_the_target_population_is_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """Pricing a settled denial would double-count money already resolved."""
        rows = [make_row(status=RecoveryStatus.DENIED_AGAIN, payer="Acme") for _ in range(40)]
        rates = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Acme"),
            make_row(status=RecoveryStatus.RECOVERED_FULL, payer="Acme"),
        ]
        with pytest.raises(ValueError, match="already decided"):
            expected_recovery(
                target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
            )

    def test_pending_rows_are_legitimate_targets(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """A resubmitted-but-unanswered denial is still open money."""
        rates = estimate_rates(
            _evidence_rows(make_row, "Acme", wins=25, losses=25),  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        target = [
            make_row(status=RecoveryStatus.RESUBMITTED_PENDING, payer="Acme", denied_cents=100_00)
            for _ in range(4)
        ]
        result = expected_recovery(
            target, rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert result.total_expected_cents == 20_000

    def test_the_disclosure_travels_with_the_money(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = [
            *_evidence_rows(make_row, "Acme", wins=25, losses=25),
            *[make_row(status=RecoveryStatus.RESUBMITTED_PENDING, payer="Acme") for _ in range(11)],
        ]
        rates = estimate_rates(
            evidence,  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        result = expected_recovery(
            [make_row(status=RecoveryStatus.NOT_RESUBMITTED, payer="Acme")],
            rates=rates,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert result.disclosure == rates.disclosure
        assert result.disclosure.excluded_open_undecided == 11
        assert result.disclosure.data_edge_date == AS_OF


class TestEmptyPolicy:
    def test_an_empty_target_population_prices_to_zero(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rates = estimate_rates(
            _evidence_rows(make_row, "Acme", wins=25, losses=25),  # type: ignore[arg-type]
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        result = expected_recovery(
            [], rates=rates, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert result.total_expected_cents == 0
        assert result.total_open_dollars_cents == 0
        assert result.unpriced_share == Decimal(0)


def test_policy_requires_an_explicit_floor() -> None:
    """``min_cohort`` has no default — the policy must be stated."""
    with pytest.raises(TypeError):
        EstimationPolicy()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="min_cohort must be >= 1"):
        EstimationPolicy(min_cohort=0, maturity=MaturityPolicy(default_days=1))
