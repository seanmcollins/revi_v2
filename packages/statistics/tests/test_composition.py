"""Expected-recoverable composition: what it prices, and what it refuses to.

The three things the composition must never do again, each with a test that
fails loudly if it starts:

* price a win at the **full denied amount** when a win has never returned it;
* price a denial **past its filing deadline** at the rate denials inside the
  deadline come back at;
* substitute a rate for a stratum whose own cohort could not support one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_statistics import (
    deadline_rates,
    estimate_rates,
    expected_recovery,
    severity_ratios,
)
from revi_statistics_contracts.contract import (
    DeadlineRates,
    EstimationPolicy,
    EvidenceLabel,
    MaturityPolicy,
    RateBasis,
    RateScope,
    RecoveryStatus,
    SeverityEstimate,
    Stratifier,
)

from .conftest import AS_OF, RowFactory

#: A denial resubmitted 10 days after it landed, against a 180-day limit
#: measured from a service date 30 days earlier, is comfortably inside the
#: deadline. Changing either number moves the row to the other side.
WITHIN = {"timely_filing_days": 180, "days_to_resubmission": 10}
#: A 40-day limit with a 30-day wait puts the resubmission past it.
PAST = {"timely_filing_days": 40, "days_to_resubmission": 30}


def _evidence_rows(
    make_row: RowFactory,
    payer: str,
    *,
    wins: int,
    losses: int,
    recovered_cents: int | None = None,
    **position: object,
) -> list[object]:
    shape = position or WITHIN
    return [
        *[
            make_row(
                status=RecoveryStatus.RECOVERED_FULL,
                payer=payer,
                recovered_cents=recovered_cents,
                **shape,  # type: ignore[arg-type]
            )
            for _ in range(wins)
        ],
        *[
            make_row(
                status=RecoveryStatus.DENIED_AGAIN,
                payer=payer,
                **shape,  # type: ignore[arg-type]
            )
            for _ in range(losses)
        ],
    ]


def _inputs(
    evidence: list[object],
    policy: EstimationPolicy,
    stratify: tuple[Stratifier, ...] = (Stratifier.PAYER,),
    *,
    basis: RateBasis = RateBasis.DECIDED,
) -> tuple[object, DeadlineRates, SeverityEstimate]:
    """The three estimates a pricing needs, from one set of rows."""
    return (
        estimate_rates(
            evidence,  # type: ignore[arg-type]
            basis=basis,
            stratify_by=stratify,
            policy=policy,
            as_of=AS_OF,
        ),
        deadline_rates(
            evidence,  # type: ignore[arg-type]
            stratify_by=stratify,
            policy=policy,
            as_of=AS_OF,
        ),
        severity_ratios(
            evidence,  # type: ignore[arg-type]
            stratify_by=stratify,
            policy=policy,
            as_of=AS_OF,
        ),
    )


def _open_rows(
    make_row: RowFactory,
    payer: str,
    *,
    n: int,
    cents: int,
    catchable: bool,
    status: RecoveryStatus = RecoveryStatus.NOT_RESUBMITTED,
) -> list[object]:
    """Open denials on the chosen side of the filing deadline at the as-of date."""
    shape = (
        {"age_days": 10, "timely_filing_days": 180}
        if catchable
        else {"age_days": 400, "timely_filing_days": 180}
    )
    return [
        make_row(status=status, payer=payer, denied_cents=cents, **shape)  # type: ignore[arg-type]
        for _ in range(n)
    ]


class TestSeverity:
    """A win is worth what a win has returned, not what was denied."""

    def test_a_win_that_returns_half_prices_at_half(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        # 50 answered denials inside the deadline, 25 of them won, and every
        # win returned half of the $1,000 denied.
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, recovered_cents=50_000, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = _open_rows(make_row, "Acme", n=10, cents=1_000_00, catchable=True)
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert result.total_open_dollars_cents == 1_000_000
        # 50% win rate x 50 cents on the dollar = 25% of the open dollars.
        assert result.total_expected_cents == 250_000
        assert severity.population.ratio == Decimal("0.5")
        assert result.strata[0].severity_scope is RateScope.OWN

    def test_the_full_denied_amount_is_never_the_price_of_a_win(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """The published-figure defect, pinned.

        Rate times full denied dollars would be $500,000 here. It is not,
        and the gap is exactly the severity ratio.
        """
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, recovered_cents=44_000, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = _open_rows(make_row, "Acme", n=10, cents=1_000_00, catchable=True)
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert result.total_expected_cents == 220_000
        assert result.total_expected_cents < 500_000

    def test_a_thin_win_cohort_falls_back_to_the_population_and_says_so(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = [
            *_evidence_rows(
                make_row, "Big", wins=40, losses=10, recovered_cents=50_000, **WITHIN
            ),
            # Enough answered denials for a rate, too few wins for a ratio.
            *_evidence_rows(
                make_row, "Few", wins=5, losses=45, recovered_cents=90_000, **WITHIN
            ),
        ]
        rates, deadlines, severity = _inputs(evidence, policy)
        target = _open_rows(make_row, "Few", n=10, cents=100_00, catchable=True)
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        stratum = result.strata[0]
        assert stratum.severity_scope is RateScope.POPULATION
        assert severity.cell_for(stratum.stratum) is not None
        assert not severity.cell_for(stratum.stratum).is_measured  # type: ignore[union-attr]
        assert stratum.severity == severity.population


class TestFilingDeadline:
    """Dollars past the deadline are priced like dollars past the deadline."""

    def test_each_side_is_priced_at_its_own_rate(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = [
            # Inside the deadline: 40 of 80 come back.
            *_evidence_rows(
                make_row, "Acme", wins=40, losses=40, **WITHIN
            ),
            # Past it: 4 of 40.
            *_evidence_rows(
                make_row, "Acme", wins=4, losses=36, **PAST
            ),
        ]
        rates, deadlines, severity = _inputs(evidence, policy)
        target = [
            *_open_rows(make_row, "Acme", n=10, cents=100_00, catchable=True),
            *_open_rows(make_row, "Acme", n=10, cents=100_00, catchable=False),
        ]
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        # Severity is 1.0 here, so the arithmetic is visible: 0.5 x $1,000
        # inside the deadline plus 0.1 x $1,000 past it.
        assert result.catchable_dollars_cents == 100_000
        assert result.deadline_passed_dollars_cents == 100_000
        assert result.total_expected_cents == 60_000
        # The blended rate would have been 44/120 = 36.7%, which over the
        # whole $2,000 is $73,333 — the overstatement this split removes.
        assert result.total_expected_cents < 73_000

    def test_the_populations_rate_covers_a_thin_side_and_the_line_says_so(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = [
            *_evidence_rows(
                make_row, "Big", wins=40, losses=40, **WITHIN
            ),
            *_evidence_rows(
                make_row, "Big", wins=4, losses=36, **PAST
            ),
            # Small has enough answered denials overall, but only two past
            # the deadline — far below the floor.
            *_evidence_rows(
                make_row, "Small", wins=30, losses=30, **WITHIN
            ),
            *_evidence_rows(
                make_row, "Small", wins=1, losses=1, **PAST
            ),
        ]
        rates, deadlines, severity = _inputs(evidence, policy)
        target = _open_rows(make_row, "Small", n=10, cents=100_00, catchable=False)
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        past = next(
            position
            for position in result.strata[0].positions
            if position.position == "past_deadline"
        )
        assert past.scope is RateScope.POPULATION
        assert past.rate_cell is not None and past.rate_cell.n == 42
        assert result.unpriced_position_dollars_cents == 0

    def test_dollars_with_no_deadline_on_file_are_priced_at_nothing(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = [
            make_row(
                status=RecoveryStatus.NOT_RESUBMITTED,
                payer="Acme",
                timely_filing_days=None,
                denied_cents=100_00,
            )
            for _ in range(5)
        ]
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert result.deadline_unknown_dollars_cents == 500_00
        assert result.total_expected_cents == 0
        assert result.unpriced_position_dollars_cents == 500_00
        assert result.priced_open_dollars_cents == 0
        unknown = next(
            position
            for position in result.strata[0].positions
            if position.position == "unknown"
        )
        assert unknown.scope is RateScope.NONE
        assert unknown.expected_cents is None

    def test_a_side_no_cohort_can_read_leaves_its_dollars_unpriced(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """Nothing past the deadline has been answered anywhere in the read."""
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = [
            *_open_rows(make_row, "Acme", n=10, cents=100_00, catchable=True),
            *_open_rows(make_row, "Acme", n=10, cents=100_00, catchable=False),
        ]
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert result.unpriced_position_dollars_cents == 100_000
        assert result.priced_open_dollars_cents == 100_000
        assert result.total_expected_cents == 50_000
        assert result.unpriced_share == Decimal("0.5")


class TestConservation:
    def test_open_dollars_split_three_ways(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = [
            *_open_rows(make_row, "Acme", n=1, cents=100_00, catchable=True),
            *_open_rows(make_row, "Acme", n=1, cents=200_00, catchable=False),
            make_row(
                status=RecoveryStatus.NOT_RESUBMITTED,
                payer="Acme",
                timely_filing_days=None,
                denied_cents=400_00,
            ),
        ]
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
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

    def test_every_dollar_is_priced_or_named_unpriced(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = [
            *_evidence_rows(
                make_row, "Big", wins=30, losses=30, **WITHIN
            ),
            *_evidence_rows(make_row, "Small", wins=2, losses=2, **WITHIN),
        ]
        rates, deadlines, severity = _inputs(evidence, policy)
        target = [
            *_open_rows(make_row, "Big", n=10, cents=100_00, catchable=True),
            *_open_rows(make_row, "Small", n=10, cents=100_00, catchable=True),
            *_open_rows(make_row, "Big", n=10, cents=100_00, catchable=False),
        ]
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert (
            result.priced_open_dollars_cents
            + result.unpriced_open_dollars_cents
            + result.unpriced_position_dollars_cents
            == result.total_open_dollars_cents
        )
        assert result.unpriced_dollars_cents == (
            result.unpriced_open_dollars_cents + result.unpriced_position_dollars_cents
        )

    def test_positions_sum_to_the_strata_open_dollars(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = [
            *_open_rows(make_row, "Acme", n=3, cents=100_00, catchable=True),
            *_open_rows(make_row, "Acme", n=2, cents=250_00, catchable=False),
        ]
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        stratum = result.strata[0]
        assert (
            sum(position.dollars_cents for position in stratum.positions)
            == stratum.open_dollars_cents
        )
        assert stratum.expected_cents == sum(
            position.expected_cents or 0 for position in stratum.positions
        )


class TestNoPriorIsEverSubstituted:
    """The behaviour the whole capability exists to guarantee."""

    def test_a_thin_stratum_is_excluded_from_the_total_and_listed(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = [
            *_evidence_rows(
                make_row, "Big", wins=30, losses=30, **WITHIN
            ),
            # Only 4 decided rows for Small — below the floor of 30.
            *_evidence_rows(make_row, "Small", wins=2, losses=2, **WITHIN),
        ]
        rates, deadlines, severity = _inputs(evidence, policy)
        target = [
            *_open_rows(make_row, "Big", n=10, cents=100_00, catchable=True),
            *_open_rows(make_row, "Small", n=10, cents=100_00, catchable=True),
        ]
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
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
        evidence = _evidence_rows(
            make_row, "Known", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = _open_rows(make_row, "NeverSeen", n=5, cents=999_00, catchable=True)
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
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
            *_evidence_rows(
                make_row, "Big", wins=60, losses=0, **WITHIN
            ),
            *_evidence_rows(make_row, "Small", wins=1, losses=0, **WITHIN),
        ]
        rates, deadlines, severity = _inputs(evidence, policy)
        target = _open_rows(make_row, "Small", n=1, cents=1_000_00, catchable=True)
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert result.total_expected_cents == 0
        assert result.strata == ()


class TestTheIntervalSaysWhatItIs:
    def test_it_brackets_the_point_and_names_its_arithmetic(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = _open_rows(make_row, "Acme", n=20, cents=500_00, catchable=True)
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert (
            result.total_expected_interval.low_cents
            <= result.total_expected_cents
            <= result.total_expected_interval.high_cents
        )
        assert result.total_expected_interval.low_cents < result.total_expected_interval.high_cents
        # Summing endpoints is the perfectly-correlated combination, and the
        # field name now says that rather than claiming independence.
        assert result.interval_is_summed_endpoints is True
        assert result.amounts_treated_as_known is True

    def test_the_severity_ratio_scales_the_interval_as_a_constant(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """Only the rate varies. Halving what a win returns halves the band."""
        full = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        half = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, recovered_cents=50_000, **WITHIN
        )
        target = _open_rows(make_row, "Acme", n=20, cents=500_00, catchable=True)
        bands = []
        for evidence in (full, half):
            rates, deadlines, severity = _inputs(evidence, policy)
            result = expected_recovery(
                target,
                rates=rates,  # type: ignore[arg-type]
                deadlines=deadlines,
                severity=severity,
                stratify_by=(Stratifier.PAYER,),
                policy=policy,
                as_of=AS_OF,
            )
            bands.append(result.total_expected_interval)
        assert bands[1].high_cents * 2 == pytest.approx(bands[0].high_cents, abs=1)
        assert bands[1].low_cents * 2 == pytest.approx(bands[0].low_cents, abs=1)


class TestGuards:
    def test_a_pursuit_estimate_is_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        pursuit, deadlines, severity = _inputs(rows, policy, basis=RateBasis.PURSUIT)
        target = _open_rows(make_row, "Acme", n=1, cents=100_00, catchable=True)
        with pytest.raises(ValueError, match="requires a DECIDED rate estimate"):
            expected_recovery(
                target,
                rates=pursuit,  # type: ignore[arg-type]
                deadlines=deadlines,
                severity=severity,
                stratify_by=(Stratifier.PAYER,),
                policy=policy,
                as_of=AS_OF,
            )

    def test_a_mismatched_stratification_is_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(rows, policy)
        with pytest.raises(ValueError, match="must share a stratification"):
            expected_recovery(
                _open_rows(make_row, "Acme", n=1, cents=100_00, catchable=True),
                rates=rates,  # type: ignore[arg-type]
                deadlines=deadlines,
                severity=severity,
                stratify_by=(Stratifier.PLAN,),
                policy=policy,
                as_of=AS_OF,
            )

    def test_deadline_rates_cut_another_way_are_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """The three estimates must describe the same populations."""
        rows = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, _, severity = _inputs(rows, policy)
        elsewhere = deadline_rates(
            rows,  # type: ignore[arg-type]
            stratify_by=(Stratifier.PLAN,),
            policy=policy,
            as_of=AS_OF,
        )
        with pytest.raises(ValueError, match="filing-position rates must share"):
            expected_recovery(
                _open_rows(make_row, "Acme", n=1, cents=100_00, catchable=True),
                rates=rates,  # type: ignore[arg-type]
                deadlines=elsewhere,
                severity=severity,
                stratify_by=(Stratifier.PAYER,),
                policy=policy,
                as_of=AS_OF,
            )

    def test_deadline_rates_refuse_to_cut_by_filing_position_twice(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        rows = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        with pytest.raises(ValueError, match="would request it twice"):
            deadline_rates(
                rows,  # type: ignore[arg-type]
                stratify_by=(Stratifier.FILING_POSITION,),
                policy=policy,
                as_of=AS_OF,
            )

    def test_a_decided_row_in_the_target_population_is_rejected(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """Pricing a settled denial would double-count money already resolved."""
        rows = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(rows, policy)
        target = [
            *_open_rows(make_row, "Acme", n=1, cents=100_00, catchable=True),
            make_row(status=RecoveryStatus.RECOVERED_FULL, payer="Acme"),
        ]
        with pytest.raises(ValueError, match="already decided"):
            expected_recovery(
                target,
                rates=rates,  # type: ignore[arg-type]
                deadlines=deadlines,
                severity=severity,
                stratify_by=(Stratifier.PAYER,),
                policy=policy,
                as_of=AS_OF,
            )

    def test_pending_rows_are_legitimate_targets(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """A resubmitted-but-unanswered denial is still open money."""
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        target = _open_rows(
            make_row,
            "Acme",
            n=4,
            cents=100_00,
            catchable=True,
            status=RecoveryStatus.RESUBMITTED_PENDING,
        )
        result = expected_recovery(
            target,
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert result.total_expected_cents == 20_000

    def test_the_disclosure_travels_with_the_money(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = [
            *_evidence_rows(
                make_row, "Acme", wins=30, losses=30, **WITHIN
            ),
            *[
                make_row(status=RecoveryStatus.RESUBMITTED_PENDING, payer="Acme")
                for _ in range(11)
            ],
        ]
        rates, deadlines, severity = _inputs(evidence, policy)
        result = expected_recovery(
            _open_rows(make_row, "Acme", n=1, cents=100_00, catchable=True),
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
        )
        assert result.disclosure == rates.disclosure  # type: ignore[union-attr]
        assert result.disclosure.excluded_open_undecided == 11
        assert result.disclosure.data_edge_date == AS_OF

    def test_a_read_with_no_measurable_win_refuses_to_price_anything(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        """No win, no severity, no price — rather than a price of one dollar per dollar."""
        evidence = _evidence_rows(make_row, "Acme", wins=0, losses=60, **WITHIN)
        rates, deadlines, severity = _inputs(evidence, policy)
        assert not severity.population.is_measured
        with pytest.raises(ValueError, match="what a win returns on the dollar"):
            expected_recovery(
                _open_rows(make_row, "Acme", n=1, cents=100_00, catchable=True),
                rates=rates,  # type: ignore[arg-type]
                deadlines=deadlines,
                severity=severity,
                stratify_by=(Stratifier.PAYER,),
                policy=policy,
                as_of=AS_OF,
            )


class TestEmptyPolicy:
    def test_an_empty_target_population_prices_to_zero(
        self, make_row: RowFactory, policy: EstimationPolicy
    ) -> None:
        evidence = _evidence_rows(
            make_row, "Acme", wins=30, losses=30, **WITHIN
        )
        rates, deadlines, severity = _inputs(evidence, policy)
        result = expected_recovery(
            [],
            rates=rates,  # type: ignore[arg-type]
            deadlines=deadlines,
            severity=severity,
            stratify_by=(Stratifier.PAYER,),
            policy=policy,
            as_of=AS_OF,
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


def test_a_severity_cell_below_the_floor_publishes_its_cohort_not_its_ratio(
    make_row: RowFactory, policy: EstimationPolicy
) -> None:
    evidence = _evidence_rows(
        make_row, "Acme", wins=5, losses=55, recovered_cents=50_00, **WITHIN
    )
    severity = severity_ratios(
        evidence,  # type: ignore[arg-type]
        stratify_by=(Stratifier.PAYER,),
        policy=policy,
        as_of=AS_OF,
    )
    cell = severity.cells[0]
    assert cell.evidence is EvidenceLabel.REFUSED_THIN
    assert cell.ratio is None
    assert cell.wins == 5
    assert cell.recovered_cents == 5 * 50_00
