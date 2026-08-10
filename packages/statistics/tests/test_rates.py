"""Censoring honesty, cohort refusal, and determinism.

These are the tests that decide whether the capability is trustworthy. The
arithmetic tests live next door; this file is about what goes in the
denominator and what is refused.
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revi_statistics import estimate_rates
from revi_statistics_contracts.contract import (
    Band,
    DenialRow,
    EstimationPolicy,
    EvidenceLabel,
    MaturityPolicy,
    MaturityWindow,
    RateBasis,
    RecoveryStatus,
    Stratifier,
    StratumKey,
)

from .conftest import AS_OF, RowFactory

DECIDED_STATUSES = [
    RecoveryStatus.RECOVERED_FULL,
    RecoveryStatus.RECOVERED_PARTIAL,
    RecoveryStatus.DENIED_AGAIN,
]


class TestOpenStoriesAreNeverFailures:
    """The single most consequential behaviour in the package."""

    def test_pending_chains_leave_the_decided_denominator_entirely(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [
            *[make_row(status=RecoveryStatus.RECOVERED_FULL) for _ in range(30)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(30)],
            # 500 chains awaiting an answer. If any of them reached the
            # denominator the rate would collapse from 50% toward 5%.
            *[make_row(status=RecoveryStatus.RESUBMITTED_PENDING) for _ in range(500)],
        ]
        estimate = estimate_rates(rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF)
        cell = estimate.cells[0]
        assert cell.n == 60
        assert cell.successes == 30
        assert cell.rate == Decimal("0.5")
        assert estimate.disclosure.excluded_open_undecided == 500

    def test_unresubmitted_denials_leave_the_decided_denominator(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [
            *[make_row(status=RecoveryStatus.RECOVERED_FULL) for _ in range(40)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(40)],
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED) for _ in range(900)],
        ]
        estimate = estimate_rates(rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF)
        assert estimate.cells[0].n == 80
        assert estimate.cells[0].rate == Decimal("0.5")
        assert estimate.disclosure.excluded_not_pursued == 900

    def test_adding_open_chains_cannot_move_a_decided_rate(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """The invariant behind the claim, stated directly."""
        decided = [
            *[make_row(status=RecoveryStatus.RECOVERED_FULL) for _ in range(37)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(53)],
        ]
        before = estimate_rates(decided, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF)
        for extra in (1, 10, 100, 1000):
            polluted = [
                *decided,
                *[make_row(status=RecoveryStatus.RESUBMITTED_PENDING) for _ in range(extra)],
                *[make_row(status=RecoveryStatus.NOT_RESUBMITTED) for _ in range(extra)],
            ]
            after = estimate_rates(polluted, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF)
            assert after.cells[0].rate == before.cells[0].rate
            assert after.cells[0].interval == before.cells[0].interval

    @given(
        recovered=st.integers(min_value=0, max_value=200),
        denied=st.integers(min_value=0, max_value=200),
        pending=st.integers(min_value=0, max_value=500),
        unworked=st.integers(min_value=0, max_value=500),
    )
    def test_decided_denominator_is_exactly_the_decided_rows(
        self, recovered: int, denied: int, pending: int, unworked: int
    ) -> None:
        loose = EstimationPolicy(min_cohort=1, maturity=MaturityPolicy(default_days=30))
        rows = []
        counter = 0
        for status, count in (
            (RecoveryStatus.RECOVERED_FULL, recovered),
            (RecoveryStatus.DENIED_AGAIN, denied),
            (RecoveryStatus.RESUBMITTED_PENDING, pending),
            (RecoveryStatus.NOT_RESUBMITTED, unworked),
        ):
            for _ in range(count):
                counter += 1
                rows.append(_row(status, counter))
        estimate = estimate_rates(rows, basis=RateBasis.DECIDED, policy=loose, as_of=AS_OF)
        assert estimate.disclosure.in_denominator == recovered + denied
        if recovered + denied:
            assert estimate.cells[0].n == recovered + denied
            assert estimate.cells[0].successes == recovered
        else:
            assert estimate.cells == ()


def _row(status: RecoveryStatus, index: int, payer: str = "Payer A") -> DenialRow:
    """Module-level row builder for hypothesis tests (fixtures cannot be drawn)."""
    denial_date = AS_OF - timedelta(days=400)
    pursued = status is not RecoveryStatus.NOT_RESUBMITTED
    return DenialRow(
        denial_id=f"H{index:06d}",
        denial_date=denial_date,
        service_date=denial_date - timedelta(days=30),
        payer_name=payer,
        plan_name="Plan A",
        recovery_class="CODING",
        recovery_status=status,
        denied_amount_cents=100_000,
        recovered_amount_cents=100_000 if status.is_recovered else 0,
        days_to_resubmission=10 if pursued else None,
        resubmission_date=denial_date + timedelta(days=10) if pursued else None,
        recovery_outcome_date=denial_date + timedelta(days=24) if status.is_decided else None,
        timely_filing_days=180,
        filing_rule_confirmed=True,
    )


class TestMaturityWindow:
    """The 383 invisible-pending chains are why this exists."""

    def test_young_unworked_denials_are_excluded_not_counted_as_failures(
        self, make_row: RowFactory
    ) -> None:
        policy = EstimationPolicy(
            min_cohort=10, maturity=MaturityPolicy(windows=(MaturityWindow("CODING", 30),))
        )
        rows = [
            # Mature cohort: 20 worked, 20 not. A real 50% pursuit rate.
            *[make_row(status=RecoveryStatus.DENIED_AGAIN, age_days=200) for _ in range(20)],
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED, age_days=200) for _ in range(20)],
            # 300 denials three days old. Nobody has had time to work them;
            # counting their silence would drag the rate to 6%.
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED, age_days=3) for _ in range(300)],
        ]
        estimate = estimate_rates(rows, basis=RateBasis.PURSUIT, policy=policy, as_of=AS_OF)
        assert estimate.cells[0].n == 40
        assert estimate.cells[0].rate == Decimal("0.5")
        assert estimate.disclosure.excluded_immature == 300

    def test_the_whole_immature_cohort_leaves_not_just_its_silent_half(
        self, make_row: RowFactory
    ) -> None:
        """Keeping young-but-worked rows would bias the rate upward instead.

        The mirror-image error: if maturity dropped only the unresubmitted
        young rows, every young row that *had* been worked would stay, and
        the pursuit rate would climb above the truth. Cohort maturity is a
        property of the cohort.
        """
        policy = EstimationPolicy(
            min_cohort=5, maturity=MaturityPolicy(windows=(MaturityWindow("CODING", 30),))
        )
        rows = [
            *[make_row(status=RecoveryStatus.DENIED_AGAIN, age_days=200) for _ in range(10)],
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED, age_days=200) for _ in range(10)],
            # Young and already worked — the fast movers.
            *[make_row(status=RecoveryStatus.DENIED_AGAIN, age_days=2) for _ in range(50)],
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED, age_days=2) for _ in range(50)],
        ]
        estimate = estimate_rates(rows, basis=RateBasis.PURSUIT, policy=policy, as_of=AS_OF)
        assert estimate.cells[0].n == 20
        assert estimate.cells[0].rate == Decimal("0.5")
        assert estimate.disclosure.excluded_immature == 100

    def test_a_row_exactly_at_the_window_is_mature(self, make_row: RowFactory) -> None:
        policy = EstimationPolicy(
            min_cohort=1, maturity=MaturityPolicy(windows=(MaturityWindow("CODING", 30),))
        )
        estimate = estimate_rates(
            [make_row(status=RecoveryStatus.NOT_RESUBMITTED, age_days=30)],
            basis=RateBasis.PURSUIT,
            policy=policy,
            as_of=AS_OF,
        )
        assert estimate.disclosure.in_denominator == 1
        assert estimate.disclosure.excluded_immature == 0

    def test_a_class_with_no_window_is_excluded_not_assumed_mature(
        self, make_row: RowFactory
    ) -> None:
        """Refusing to apply a rule nobody stated."""
        policy = EstimationPolicy(
            min_cohort=1, maturity=MaturityPolicy(windows=(MaturityWindow("CODING", 30),))
        )
        rows = [
            make_row(status=RecoveryStatus.NOT_RESUBMITTED, recovery_class="CLINICAL")
            for _ in range(10)
        ]
        estimate = estimate_rates(rows, basis=RateBasis.PURSUIT, policy=policy, as_of=AS_OF)
        assert estimate.disclosure.excluded_unclassifiable == 10
        assert estimate.disclosure.in_denominator == 0

    def test_pending_chains_count_as_pursued(self, make_row: RowFactory) -> None:
        """A resubmission awaiting an answer is a resubmission."""
        policy = EstimationPolicy(min_cohort=1, maturity=MaturityPolicy(default_days=30))
        rows = [make_row(status=RecoveryStatus.RESUBMITTED_PENDING, age_days=200) for _ in range(10)]
        estimate = estimate_rates(rows, basis=RateBasis.PURSUIT, policy=policy, as_of=AS_OF)
        assert estimate.cells[0].rate == Decimal(1)


class TestCohortFloor:
    def test_a_thin_cell_publishes_its_size_and_no_rate(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [make_row(status=RecoveryStatus.RECOVERED_FULL) for _ in range(5)]
        estimate = estimate_rates(rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF)
        cell = estimate.cells[0]
        assert cell.evidence is EvidenceLabel.REFUSED_THIN
        assert cell.rate is None
        assert cell.interval is None
        assert cell.n == 5
        assert cell.successes == 5
        assert cell.min_cohort == 30

    def test_a_cell_exactly_at_the_floor_is_measured(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [make_row(status=RecoveryStatus.RECOVERED_FULL) for _ in range(30)]
        cell = estimate_rates(rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF).cells[0]
        assert cell.evidence is EvidenceLabel.MEASURED
        assert cell.rate == Decimal(1)

    @given(
        floor=st.integers(min_value=1, max_value=80),
        counts=st.lists(st.integers(min_value=1, max_value=60), min_size=1, max_size=8),
    )
    def test_no_cell_below_the_floor_ever_carries_a_rate(
        self, floor: int, counts: list[int]
    ) -> None:
        """The headline invariant, over arbitrary stratum sizes."""
        policy = EstimationPolicy(min_cohort=floor, maturity=MaturityPolicy(default_days=30))
        rows = []
        index = 0
        for payer_index, count in enumerate(counts):
            for _ in range(count):
                index += 1
                rows.append(_row(RecoveryStatus.DENIED_AGAIN, index, payer=f"Payer {payer_index}"))
        estimate = estimate_rates(
            rows, basis=RateBasis.DECIDED, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        for cell in estimate.cells:
            if cell.n < floor:
                assert cell.evidence is EvidenceLabel.REFUSED_THIN
                assert cell.rate is None and cell.interval is None
            else:
                assert cell.evidence is EvidenceLabel.MEASURED
                assert cell.rate is not None and cell.interval is not None

    def test_thin_cells_still_appear_in_the_output(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """A refusal is published, not dropped — the reader must see the gap."""
        rows = [
            *[make_row(status=RecoveryStatus.DENIED_AGAIN, payer="Big") for _ in range(40)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN, payer="Tiny") for _ in range(2)],
        ]
        estimate = estimate_rates(
            rows, basis=RateBasis.DECIDED, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert len(estimate.cells) == 2
        assert len(estimate.measured) == 1
        assert len(estimate.refused) == 1
        assert estimate.refused[0].n == 2


class TestDisclosure:
    def test_every_row_is_accounted_for(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [
            *[make_row(status=RecoveryStatus.RECOVERED_FULL) for _ in range(11)],
            *[make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(13)],
            *[make_row(status=RecoveryStatus.RESUBMITTED_PENDING) for _ in range(7)],
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED) for _ in range(19)],
        ]
        disclosure = estimate_rates(
            rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF
        ).disclosure
        assert disclosure.rows_considered == 50
        assert disclosure.in_denominator == 24
        assert disclosure.excluded_open_undecided == 7
        assert disclosure.excluded_not_pursued == 19
        # The type enforces the sum; assert it explicitly anyway.
        assert (
            disclosure.in_denominator
            + disclosure.excluded_open_undecided
            + disclosure.excluded_not_pursued
            + disclosure.excluded_immature
            + disclosure.excluded_unclassifiable
            == disclosure.rows_considered
        )

    def test_open_counts_are_reported_on_both_bases(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [
            *[make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(30)],
            *[make_row(status=RecoveryStatus.RESUBMITTED_PENDING) for _ in range(9)],
            *[make_row(status=RecoveryStatus.NOT_RESUBMITTED) for _ in range(4)],
        ]
        for basis in (RateBasis.DECIDED, RateBasis.PURSUIT):
            disclosure = estimate_rates(rows, basis=basis, policy=policy, as_of=AS_OF).disclosure
            assert disclosure.open_undecided_in_input == 9
            assert disclosure.not_pursued_in_input == 4
            assert disclosure.data_edge_date == AS_OF


class TestDeterminism:
    def test_output_is_independent_of_input_order(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [
            make_row(
                status=random.Random(i).choice([*DECIDED_STATUSES, RecoveryStatus.NOT_RESUBMITTED]),
                payer=f"Payer {i % 5}",
                recovery_class=["CODING", "CLINICAL", "FINAL"][i % 3],
            )
            for i in range(600)
        ]
        stratify = (Stratifier.PAYER, Stratifier.RECOVERY_CLASS)
        reference = estimate_rates(
            rows, basis=RateBasis.DECIDED, stratify_by=stratify, policy=policy, as_of=AS_OF
        )
        for seed in range(6):
            shuffled = list(rows)
            random.Random(seed).shuffle(shuffled)
            other = estimate_rates(
                shuffled, basis=RateBasis.DECIDED, stratify_by=stratify, policy=policy, as_of=AS_OF
            )
            assert other == reference

    def test_repeated_runs_are_byte_identical(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [make_row(status=DECIDED_STATUSES[i % 3]) for i in range(150)]
        first = estimate_rates(rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF)
        for _ in range(20):
            assert estimate_rates(rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF) == first

    def test_cells_come_back_sorted(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [
            make_row(status=RecoveryStatus.DENIED_AGAIN, payer=payer)
            for payer in ("Zeta", "Alpha", "Mu")
            for _ in range(35)
        ]
        estimate = estimate_rates(
            rows, basis=RateBasis.DECIDED, stratify_by=(Stratifier.PAYER,), policy=policy, as_of=AS_OF
        )
        assert [cell.stratum.value_of(Stratifier.PAYER) for cell in estimate.cells] == [
            "Alpha",
            "Mu",
            "Zeta",
        ]


class TestStratification:
    def test_ungrouped_is_a_single_total_cell(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [make_row(status=RecoveryStatus.DENIED_AGAIN) for _ in range(40)]
        estimate = estimate_rates(rows, basis=RateBasis.DECIDED, policy=policy, as_of=AS_OF)
        assert len(estimate.cells) == 1
        assert estimate.cells[0].stratum == StratumKey(())
        assert estimate.cells[0].stratum.label == "(all)"

    def test_banded_stratifier_without_bands_is_rejected(
        self, make_row: RowFactory
    ) -> None:
        bare = EstimationPolicy(min_cohort=1, maturity=MaturityPolicy(default_days=30))
        with pytest.raises(ValueError, match="requires caller-supplied bands"):
            estimate_rates(
                [make_row()],
                basis=RateBasis.DECIDED,
                stratify_by=(Stratifier.DOLLAR_BAND,),
                policy=bare,
                as_of=AS_OF,
            )

    def test_a_value_outside_every_band_raises(self, make_row: RowFactory) -> None:
        policy = EstimationPolicy(
            min_cohort=1,
            maturity=MaturityPolicy(default_days=30),
            dollar_bands=(Band("tiny", 0, 100),),
        )
        with pytest.raises(ValueError, match="falls outside every band"):
            estimate_rates(
                [make_row(denied_cents=500_000)],
                basis=RateBasis.DECIDED,
                stratify_by=(Stratifier.DOLLAR_BAND,),
                policy=policy,
                as_of=AS_OF,
            )

    def test_duplicate_stratifiers_are_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        with pytest.raises(ValueError, match="requested twice"):
            estimate_rates(
                [make_row()],
                basis=RateBasis.DECIDED,
                stratify_by=(Stratifier.PAYER, Stratifier.PAYER),
                policy=policy,
                as_of=AS_OF,
            )

    def test_cells_partition_the_denominator(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = [
            make_row(status=DECIDED_STATUSES[i % 3], payer=f"P{i % 7}", plan=f"L{i % 3}")
            for i in range(500)
        ]
        estimate = estimate_rates(
            rows,
            basis=RateBasis.DECIDED,
            stratify_by=(Stratifier.PAYER, Stratifier.PLAN),
            policy=policy,
            as_of=AS_OF,
        )
        assert sum(cell.n for cell in estimate.cells) == estimate.disclosure.in_denominator
