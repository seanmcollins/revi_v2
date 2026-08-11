"""One test class per angle executor, over rows written here.

The reference test proves the mode reproduces a real world. These prove the
executors behave at the edges that world does not reach: a population with
two denials in it, a contrast with only one side, a curve with no bands to
read along, an open population nothing can price.

Rows are built by hand so each case says exactly what it is about, and the
assertions are on the honesty rules rather than on the arithmetic — the
arithmetic has its own tests inside the estimators.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from revi_investigation.application.deep_research.angles import (
    AngleResult,
    run_angle,
    run_class_contrast,
    run_deadline_interaction,
    run_expected_recovery,
    run_outcome_by_stratum,
    run_payer_contrast,
    run_timeliness_curve,
)
from revi_investigation.application.deep_research.grammar import (
    AngleFamily,
    RateBasisChoice,
    ResearchAngle,
    Stratum,
)
from revi_investigation.application.deep_research.policy import (
    BandSpec,
    DeepResearchSettings,
)
from revi_investigation.application.deep_research.rows import DenialRows
from revi_statistics_contracts.contract import DenialRow, RecoveryStatus

AS_OF = date(2026, 8, 2)


def settings(**overrides: object) -> DeepResearchSettings:
    base: dict[str, object] = {
        "min_cohort": 10,
        "min_cohort_label": "at least 10 of these denials have a final answer from the payer",
        "min_cohort_recommender": "Revi's recommended level for recovery rates",
        "confidence": Decimal("0.95"),
        "delay_bands": (BandSpec("0-14", 0, 15), BandSpec("15+", 15)),
        "dollar_bands": (BandSpec("under $500", 0, 50_000), BandSpec("over $500", 50_000)),
        "disclosure_floor": 3,
        "maturity_days": {"CODING": 18, "CLINICAL": 73},
        "stratifier_labels": {"payer": "Payer", "recovery_class": "Denial type"},
        "value_labels": {"recovery_class": {"CODING": "coding", "CLINICAL": "clinical"}},
        "angle_copy": {},
    }
    base.update(overrides)
    return DeepResearchSettings(**base)  # type: ignore[arg-type]


def denial(
    index: int,
    *,
    payer: str = "Payer A",
    recovery_class: str = "CODING",
    status: RecoveryStatus = RecoveryStatus.RECOVERED_FULL,
    denied_cents: int = 100_000,
    delay: int | None = 7,
    filing_days: int | None = 365,
    confirmed: bool = False,
    denial_day: date = date(2026, 1, 5),
) -> DenialRow:
    pursued = status is not RecoveryStatus.NOT_RESUBMITTED
    resubmitted = denial_day + timedelta(days=delay or 0) if pursued else None
    decided = status in (
        RecoveryStatus.RECOVERED_FULL,
        RecoveryStatus.RECOVERED_PARTIAL,
        RecoveryStatus.DENIED_AGAIN,
    )
    return DenialRow(
        denial_id=f"D{index:05d}",
        denial_date=denial_day,
        service_date=denial_day - timedelta(days=20),
        payer_name=payer,
        plan_name=f"{payer} Plan",
        recovery_class=recovery_class,
        recovery_status=status,
        denied_amount_cents=denied_cents,
        recovered_amount_cents=(
            denied_cents if status is RecoveryStatus.RECOVERED_FULL else 0
        ),
        days_to_resubmission=delay if pursued else None,
        resubmission_date=resubmitted,
        recovery_outcome_date=(
            (resubmitted + timedelta(days=10)) if (decided and resubmitted) else None
        ),
        timely_filing_days=filing_days,
        filing_rule_confirmed=confirmed,
    )


def rows_of(*rows: DenialRow) -> DenialRows:
    return DenialRows(
        rows=tuple(rows),
        watermark=_Watermark(),  # type: ignore[arg-type]
        read_fingerprint="fingerprint",
        rows_read=len(rows),
        cache_hit=False,
        duration_ms=0,
        as_of=AS_OF,
    )


class _Watermark:
    id = "wm_test"
    newest_data_date = AS_OF


def wins_and_losses(
    wins: int, losses: int, *, payer: str = "Payer A", recovery_class: str = "CODING", **kwargs
) -> list[DenialRow]:
    out: list[DenialRow] = []
    for i in range(wins):
        out.append(
            denial(
                len(out) + i,
                payer=payer,
                recovery_class=recovery_class,
                status=RecoveryStatus.RECOVERED_FULL,
                **kwargs,
            )
        )
    for i in range(losses):
        out.append(
            denial(
                1000 + i,
                payer=payer,
                recovery_class=recovery_class,
                status=RecoveryStatus.DENIED_AGAIN,
                **kwargs,
            )
        )
    return out


class TestOutcomeByStratum:
    def test_a_population_above_the_floor_publishes_a_rate_and_a_range(self) -> None:
        rows = rows_of(*wins_and_losses(6, 6))
        angle = ResearchAngle(
            family=AngleFamily.OUTCOME_BY_STRATUM, stratify_by=(Stratum.PAYER,)
        )
        result = run_outcome_by_stratum(
            angle, rows, settings=settings(), policy=settings().estimation_policy()
        )
        assert result.rates is not None
        cell = result.rates.cells[0]
        assert cell.n == 12
        assert cell.successes == 6
        assert cell.rate == Decimal("0.5").quantize(Decimal("1E-10"))
        assert cell.interval is not None

    def test_a_population_below_the_floor_publishes_its_size_and_no_rate(self) -> None:
        rows = rows_of(*wins_and_losses(2, 2))
        angle = ResearchAngle(
            family=AngleFamily.OUTCOME_BY_STRATUM, stratify_by=(Stratum.PAYER,)
        )
        result = run_outcome_by_stratum(
            angle, rows, settings=settings(), policy=settings().estimation_policy()
        )
        assert result.rates is not None
        cell = result.rates.cells[0]
        assert cell.n == 4
        assert cell.rate is None
        assert cell.interval is None
        assert result.cells_published == 0
        assert result.cells_refused == 1

    def test_open_denials_are_in_neither_the_wins_nor_the_losses(self) -> None:
        rows = rows_of(
            *wins_and_losses(6, 6),
            *[
                denial(2000 + i, status=RecoveryStatus.RESUBMITTED_PENDING)
                for i in range(5)
            ],
        )
        angle = ResearchAngle(
            family=AngleFamily.OUTCOME_BY_STRATUM, stratify_by=(Stratum.PAYER,)
        )
        result = run_outcome_by_stratum(
            angle, rows, settings=settings(), policy=settings().estimation_policy()
        )
        assert result.rates is not None
        assert result.rates.cells[0].n == 12
        assert result.rates.disclosure.excluded_open_undecided == 5

    def test_the_worked_at_all_denominator_excludes_denials_too_recent_to_judge(
        self,
    ) -> None:
        fresh = [
            denial(
                3000 + i,
                status=RecoveryStatus.NOT_RESUBMITTED,
                denial_day=AS_OF - timedelta(days=2),
                delay=None,
            )
            for i in range(4)
        ]
        old = [
            denial(
                4000 + i,
                status=RecoveryStatus.NOT_RESUBMITTED,
                denial_day=date(2026, 1, 5),
                delay=None,
            )
            for i in range(10)
        ]
        rows = rows_of(*wins_and_losses(6, 6), *fresh, *old)
        angle = ResearchAngle(
            family=AngleFamily.OUTCOME_BY_STRATUM,
            stratify_by=(Stratum.PAYER,),
            basis=RateBasisChoice.PURSUIT,
        )
        result = run_outcome_by_stratum(
            angle, rows, settings=settings(), policy=settings().estimation_policy()
        )
        assert result.rates is not None
        assert result.rates.disclosure.excluded_immature == 4
        assert result.rates.cells[0].n == 22

    def test_a_cut_the_content_cannot_carry_is_refused_by_name(self) -> None:
        bare = settings(delay_bands=(), dollar_bands=(), age_bands=())
        angle = ResearchAngle(
            family=AngleFamily.OUTCOME_BY_STRATUM, stratify_by=(Stratum.DOLLAR_BAND,)
        )
        result = run_angle(
            angle, rows_of(*wins_and_losses(6, 6)), settings=bare, policy=bare.estimation_policy()
        )
        assert result.refusal is not None
        assert "band edges" in result.refusal
        assert result.rates is None


class TestContrasts:
    def test_the_strongest_and_weakest_are_the_two_ends_of_the_range(self) -> None:
        rows = rows_of(
            *wins_and_losses(10, 2, payer="Strong"),
            *wins_and_losses(6, 6, payer="Middle"),
            *wins_and_losses(2, 10, payer="Weak"),
        )
        result = run_payer_contrast(
            ResearchAngle(family=AngleFamily.PAYER_CONTRAST),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.contrast is not None
        assert result.contrast_cells is not None
        assert result.contrast_cells[0].stratum.value_of("payer") == "Strong"
        assert result.contrast_cells[1].stratum.value_of("payer") == "Weak"

    def test_an_extremum_contrast_always_says_it_was_chosen_for_its_extremes(
        self,
    ) -> None:
        rows = rows_of(
            *wins_and_losses(10, 2, payer="Strong"),
            *wins_and_losses(2, 10, payer="Weak"),
        )
        result = run_payer_contrast(
            ResearchAngle(family=AngleFamily.PAYER_CONTRAST),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert any("ends of the range" in note for note in result.notes)

    def test_one_measurable_population_refuses_the_comparison_and_states_the_rule(
        self,
    ) -> None:
        rows = rows_of(
            *wins_and_losses(6, 6, payer="Big"),
            *wins_and_losses(1, 1, payer="Tiny"),
        )
        result = run_payer_contrast(
            ResearchAngle(family=AngleFamily.PAYER_CONTRAST),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.contrast is not None
        assert result.contrast.is_refused
        assert result.contrast.p_value is None
        assert result.contrast.risk_difference is None
        assert "at least 10" in (result.contrast.refusal_reason or "")

    def test_holding_a_population_fixed_picks_the_largest_before_reading_a_rate(
        self,
    ) -> None:
        """The held population is chosen on size. Here the SMALLER denial
        type carries the wider payer gap, so a chooser that read the outcome
        would pick it — and this one must not."""
        rows = rows_of(
            *wins_and_losses(12, 12, payer="A", recovery_class="CODING"),
            *wins_and_losses(12, 12, payer="B", recovery_class="CODING"),
            *wins_and_losses(11, 1, payer="A", recovery_class="CLINICAL"),
            *wins_and_losses(1, 11, payer="B", recovery_class="CLINICAL"),
        )
        result = run_payer_contrast(
            ResearchAngle(
                family=AngleFamily.PAYER_CONTRAST, within=(Stratum.RECOVERY_CLASS,)
            ),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert "coding" in result.contrast_note
        assert result.contrast_cells is not None
        assert all(
            cell.stratum.value_of("recovery_class") == "CODING"
            for cell in result.contrast_cells
        )

    def test_a_denial_type_contrast_runs_over_denial_types(self) -> None:
        rows = rows_of(
            *wins_and_losses(10, 2, recovery_class="CODING"),
            *wins_and_losses(2, 10, recovery_class="CLINICAL"),
        )
        result = run_class_contrast(
            ResearchAngle(family=AngleFamily.CLASS_CONTRAST),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.contrast_cells is not None
        assert result.contrast_cells[0].stratum.value_of("recovery_class") == "CODING"


class TestTheTimelinessCurve:
    def test_bands_come_back_in_the_order_they_were_declared(self) -> None:
        rows = rows_of(
            *wins_and_losses(8, 4, delay=3),
            *wins_and_losses(2, 10, delay=40),
        )
        result = run_timeliness_curve(
            ResearchAngle(family=AngleFamily.TIMELINESS_CURVE),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.curve is not None
        labels = [cell.stratum.value_of("delay_band") for cell in result.curve.cells]
        assert labels == ["0-14", "15+"]

    def test_it_also_measures_where_the_delay_currently_is(self) -> None:
        rows = rows_of(*wins_and_losses(8, 4, delay=3))
        result = run_timeliness_curve(
            ResearchAngle(family=AngleFamily.TIMELINESS_CURVE),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.durations is not None
        assert result.durations.cells[0].median_days == Decimal(3).quantize(
            Decimal("1E-10")
        )

    def test_with_no_band_edges_the_curve_is_refused_rather_than_invented(self) -> None:
        bare = settings(delay_bands=())
        result = run_angle(
            ResearchAngle(family=AngleFamily.TIMELINESS_CURVE),
            rows_of(*wins_and_losses(8, 4)),
            settings=bare,
            policy=bare.estimation_policy(),
        )
        assert result.refusal is not None
        assert "delay bands" in result.refusal


class TestTheFilingDeadline:
    def test_the_two_authorities_are_reported_apart(self) -> None:
        rows = rows_of(
            *wins_and_losses(6, 6, filing_days=365, confirmed=True),
            *wins_and_losses(4, 8, filing_days=365, confirmed=False),
        )
        result = run_deadline_interaction(
            ResearchAngle(family=AngleFamily.DEADLINE_INTERACTION),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.rates is not None
        rules = {cell.stratum.value_of("filing_rule") for cell in result.rates.cells}
        assert rules == {"confirmed", "requires_confirmation"}

    def test_a_plan_with_no_limit_on_file_is_its_own_answer(self) -> None:
        rows = rows_of(*wins_and_losses(6, 6, filing_days=None))
        result = run_deadline_interaction(
            ResearchAngle(family=AngleFamily.DEADLINE_INTERACTION),
            rows,
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.rates is not None
        positions = {
            cell.stratum.value_of("filing_position") for cell in result.rates.cells
        }
        assert positions == {"unknown"}


class TestPricingTheOpenPopulation:
    def test_a_population_with_no_measurable_history_is_priced_at_nothing(self) -> None:
        history = wins_and_losses(2, 2, payer="Thin")
        open_rows = [
            denial(9000 + i, payer="Thin", status=RecoveryStatus.NOT_RESUBMITTED, delay=None)
            for i in range(5)
        ]
        result = run_expected_recovery(
            ResearchAngle(
                family=AngleFamily.EXPECTED_RECOVERY, stratify_by=(Stratum.PAYER,)
            ),
            rows_of(*history, *open_rows),
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.expected is not None
        assert result.expected.total_expected_cents == 0
        assert result.expected.refused_strata
        assert result.expected.unpriced_open_dollars_cents == 500_000

    def test_a_population_with_history_is_priced_at_its_own_rate(self) -> None:
        history = wins_and_losses(6, 6, payer="Measured")
        open_rows = [
            denial(
                9100 + i,
                payer="Measured",
                status=RecoveryStatus.NOT_RESUBMITTED,
                delay=None,
                denied_cents=100_000,
            )
            for i in range(4)
        ]
        result = run_expected_recovery(
            ResearchAngle(
                family=AngleFamily.EXPECTED_RECOVERY, stratify_by=(Stratum.PAYER,)
            ),
            rows_of(*history, *open_rows),
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.expected is not None
        # 50% of $4,000 of open denials.
        assert result.expected.total_expected_cents == 200_000
        assert result.expected.unpriced_open_dollars_cents == 0

    def test_a_decided_denial_is_never_priced_as_if_it_were_still_open(self) -> None:
        result = run_expected_recovery(
            ResearchAngle(
                family=AngleFamily.EXPECTED_RECOVERY, stratify_by=(Stratum.PAYER,)
            ),
            rows_of(*wins_and_losses(6, 6)),
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        assert result.expected is not None
        assert result.expected.total_open_dollars_cents == 0

    def test_the_deadline_split_partitions_the_open_dollars(self) -> None:
        history = wins_and_losses(6, 6, payer="Measured")
        catchable = [
            denial(
                9200 + i,
                payer="Measured",
                status=RecoveryStatus.NOT_RESUBMITTED,
                delay=None,
                filing_days=3650,
            )
            for i in range(2)
        ]
        expired = [
            denial(
                9300 + i,
                payer="Measured",
                status=RecoveryStatus.NOT_RESUBMITTED,
                delay=None,
                filing_days=30,
            )
            for i in range(2)
        ]
        unknown = [
            denial(
                9400 + i,
                payer="Measured",
                status=RecoveryStatus.NOT_RESUBMITTED,
                delay=None,
                filing_days=None,
            )
            for i in range(2)
        ]
        result = run_expected_recovery(
            ResearchAngle(
                family=AngleFamily.EXPECTED_RECOVERY, stratify_by=(Stratum.PAYER,)
            ),
            rows_of(*history, *catchable, *expired, *unknown),
            settings=settings(),
            policy=settings().estimation_policy(),
        )
        priced = result.expected
        assert priced is not None
        assert priced.catchable_dollars_cents == 200_000
        assert priced.deadline_passed_dollars_cents == 200_000
        assert priced.deadline_unknown_dollars_cents == 200_000
        assert (
            priced.catchable_dollars_cents
            + priced.deadline_passed_dollars_cents
            + priced.deadline_unknown_dollars_cents
            == priced.total_open_dollars_cents
        )


class TestRunAngle:
    @pytest.mark.parametrize("family", list(AngleFamily))
    def test_every_family_has_an_executor(self, family: AngleFamily) -> None:
        rows = rows_of(*wins_and_losses(6, 6))
        angle = ResearchAngle(
            family=family,
            stratify_by=(
                (Stratum.PAYER,)
                if family
                in (AngleFamily.OUTCOME_BY_STRATUM, AngleFamily.EXPECTED_RECOVERY)
                else ()
            ),
        )
        result = run_angle(
            angle, rows, settings=settings(), policy=settings().estimation_policy()
        )
        assert isinstance(result, AngleResult)
        assert result.angle.family is family
