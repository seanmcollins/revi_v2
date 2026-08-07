"""Planted scenario signals must be detectable at small scale (loosened thresholds)."""

from __future__ import annotations

from typing import Any

from revi_warehouse.config import GeneratorConfig
from revi_warehouse.generate import GenerationResult
from revi_warehouse.verify import run_verification


def _snap3(result: GenerationResult, scenario: str) -> dict[str, Any]:
    return result.answer_key["scenarios"][scenario]["snap_003"]


def test_full_verification_suite_passes(
    small_result: GenerationResult, small_config: GeneratorConfig
) -> None:
    checks = run_verification(small_result.db_path, small_result.answer_key, small_config)
    failures = [c for c in checks if not c.ok]
    assert not failures, [f"{c.name}: {c.detail}" for c in failures]


def test_denial_spike_signal(small_result: GenerationResult) -> None:
    s1 = _snap3(small_result, "1_denial_spike_meridian_imaging")
    assert s1["post_break"]["carc197_denied"] >= 1
    assert s1["post_break"]["rate"] > s1["pre_break"]["rate"]
    assert s1["post_over_pre_rate_ratio"] > 1.5


def test_cob_cohort_flags_and_denials(small_result: GenerationResult) -> None:
    s2 = _snap3(small_result, "2_cob_silverline")
    assert 0.02 <= s2["cob_mismatch_share"] <= 0.20
    assert s2["carc22_denials"] >= 2
    assert s2["carc22_denied_cents"] > 0
    assert 28.0 <= s2["avg_rebill_gap_days"] <= 47.0


def test_cash_decline_mechanisms(small_result: GenerationResult) -> None:
    s3 = _snap3(small_result, "3_cash_decline")
    # Mechanism (a): Atlas submissions drop from 2026-07-13.
    subs = {row["week_start"]: row["claims_submitted"] for row in s3["atlas_submissions_by_week"]}
    pre = subs["2026-06-29"] + subs["2026-07-06"]
    post = subs["2026-07-13"] + subs["2026-07-20"]
    assert post < pre
    # Mechanism (b): State Medicaid observed remit->post lag stretches in late July.
    lags = s3["state_medicaid_observed_post_lag_days"]
    assert lags["remits_2026_07_24_onward"] - lags["remits_2026_07_06_to_07_23"] >= 1.5
    # The decline week is lower than the prior week even at noisy small scale.
    assert s3["week_decline"]["payer_cash_cents"] < s3["week_prior"]["payer_cash_cents"]


def test_underpayment_signal(small_result: GenerationResult) -> None:
    s4 = _snap3(small_result, "4_underpayment_northbridge_ortho")
    pre = sum(
        m["underpayment_variance_cents"]
        for m in s4["monthly_by_first_remit"]
        if m["remit_month"] < "2026-05"
    )
    post = sum(
        m["underpayment_variance_cents"]
        for m in s4["monthly_by_first_remit"]
        if m["remit_month"] >= "2026-05"
    )
    assert pre == 0  # allowed == expected exactly before the break
    assert post > 0
    assert 0.90 <= s4["post_over_pre_ratio"] <= 0.94


def test_timely_filing_cluster(
    small_result: GenerationResult, small_config: GeneratorConfig
) -> None:
    s5 = _snap3(small_result, "5_timely_filing_state_medicaid_hmo")
    assert (
        small_config.timely_cluster_size
        <= s5["unsubmitted_july_claims"]
        <= small_config.timely_cluster_size + 6
    )
    assert s5["at_risk_billed_cents"] > 0
    assert s5["age_days_at_watermark"]["max"] < 90  # aging toward, not past, the deadline
    assert s5["carc29_denials"] >= small_config.carc29_count - 2


def test_scenarios_computed_per_watermark(small_result: GenerationResult) -> None:
    for scenario, per_snap in small_result.answer_key["scenarios"].items():
        assert set(per_snap) == {"snap_001", "snap_002", "snap_003"}, scenario
