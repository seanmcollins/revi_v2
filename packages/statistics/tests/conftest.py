"""Shared builders for the statistics suite.

Synthetic rows are built through one factory so a test that cares about
censoring does not accidentally also depend on a dollar amount, and so the
coherence rules :class:`DenialRow` enforces are satisfied in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import pytest

from revi_statistics_contracts.contract import (
    Band,
    DenialRow,
    EstimationPolicy,
    MaturityPolicy,
    MaturityWindow,
    RecoveryStatus,
)

AS_OF = date(2026, 8, 2)

RowFactory = Callable[..., DenialRow]


@pytest.fixture(scope="session")
def make_row() -> RowFactory:
    """Build a coherent :class:`DenialRow`; every field has a sane default.

    ``age_days`` positions the denial relative to :data:`AS_OF`, which is
    what maturity tests actually vary.
    """
    counter = {"n": 0}

    def _make(
        *,
        status: RecoveryStatus = RecoveryStatus.DENIED_AGAIN,
        age_days: int = 400,
        recovery_class: str = "CODING",
        payer: str = "Payer A",
        plan: str = "Plan A",
        denied_cents: int = 100_000,
        recovered_cents: int | None = None,
        days_to_resubmission: int | None = 10,
        timely_filing_days: int | None = 180,
        filing_rule_confirmed: bool = True,
        denial_id: str | None = None,
    ) -> DenialRow:
        counter["n"] += 1
        denial_date = AS_OF - timedelta(days=age_days)
        pursued = status is not RecoveryStatus.NOT_RESUBMITTED
        delay = days_to_resubmission if pursued else None
        resubmission_date = denial_date + timedelta(days=delay) if delay is not None else None
        outcome_date = (
            resubmission_date + timedelta(days=14)
            if resubmission_date is not None and status.is_decided
            else None
        )
        if recovered_cents is None:
            recovered_cents = denied_cents if status.is_recovered else 0
        return DenialRow(
            denial_id=denial_id or f"D{counter['n']:06d}",
            denial_date=denial_date,
            service_date=denial_date - timedelta(days=30),
            payer_name=payer,
            plan_name=plan,
            recovery_class=recovery_class,
            recovery_status=status,
            denied_amount_cents=denied_cents,
            recovered_amount_cents=recovered_cents,
            days_to_resubmission=delay,
            resubmission_date=resubmission_date,
            recovery_outcome_date=outcome_date,
            timely_filing_days=timely_filing_days,
            filing_rule_confirmed=filing_rule_confirmed,
        )

    return _make


@pytest.fixture
def policy() -> EstimationPolicy:
    """A workaday policy: floor of 30, 95%, one 30-day maturity window."""
    return EstimationPolicy(
        min_cohort=30,
        confidence=Decimal("0.95"),
        maturity=MaturityPolicy(
            windows=(MaturityWindow(recovery_class="CODING", days=30),), default_days=30
        ),
        dollar_bands=(Band("small", 0, 100_000), Band("large", 100_000, None)),
        delay_bands=(Band("0-14", 0, 15), Band("15-30", 15, 31), Band("31+", 31, None)),
        age_bands=(Band("fresh", 0, 60), Band("aged", 60, None)),
    )
