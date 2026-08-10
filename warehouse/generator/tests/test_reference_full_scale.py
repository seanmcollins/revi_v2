"""Full-scale answer-key regression against the checked-out data/ warehouse.

Marked reference: needs `make warehouse` to have produced data/revi_warehouse.duckdb
and data/answer_key.json at full scale; skips otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from revi_warehouse.config import GeneratorConfig
from revi_warehouse.verify import run_verification

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "revi_warehouse.duckdb"
KEY_PATH = REPO_ROOT / "data" / "answer_key.json"

pytestmark = pytest.mark.reference


@pytest.fixture(scope="module")
def full_key() -> dict:
    if not DB_PATH.exists() or not KEY_PATH.exists():
        pytest.skip("full-scale warehouse not generated; run `make warehouse` first")
    key = json.loads(KEY_PATH.read_text())
    if key["config"]["scale"] != "full":
        pytest.skip("data/ warehouse was generated at small scale")
    return key


def test_full_scale_verification_passes(full_key: dict) -> None:
    checks = run_verification(DB_PATH, full_key, GeneratorConfig())
    failures = [c for c in checks if not c.ok]
    assert not failures, [f"{c.name}: {c.detail}" for c in failures]


def test_reference_conversation_numbers(full_key: dict) -> None:
    """The section-10.3 anchor numbers: cash decline week at the newest watermark."""
    s3 = full_key["scenarios"]["3_cash_decline"]["snap_003"]
    assert -0.14 <= s3["delta_pct"] <= -0.10
    by_payer = {row["payer_name"]: row for row in s3["by_payer"]}
    assert by_payer["Atlas Commercial"]["delta_cents"] < 0
    assert by_payer["State Medicaid"]["delta_cents"] < 0
    top2 = {row["payer_name"] for row in s3["by_payer"][:2]}
    assert top2 == {"Atlas Commercial", "State Medicaid"}


def test_full_scale_recovery_effects_are_detectable(full_key: dict) -> None:
    """The effect sizes are only worth anything if a cohort-sized study finds them.

    Small-scale tests can check direction; separation is a claim about *this*
    warehouse's cohort sizes, so it belongs here. The strong/weak payer contrast
    is the stated bar: a pooled two-proportion test over the decided chains has
    to clear it comfortably, not marginally.
    """
    recovery = full_key["recovery"]["snap_003"]
    detect = recovery["detectability"]
    assert detect["strong_decided"] >= 100 and detect["weak_decided"] >= 100
    assert detect["strong_rate"] - detect["weak_rate"] > 0.2
    assert detect["two_proportion_z"] > 4.0, detect

    by_class = {c["denial_recovery_class"]: c for c in recovery["by_class"]}
    fixable = by_class["CODING"]["recovery_rate_of_decided"]
    clinical = by_class["CLINICAL"]["recovery_rate_of_decided"]
    assert fixable > 3 * clinical, (fixable, clinical)

    buckets = {c["days_to_resubmission_bucket"]: c for c in recovery["by_days_to_resubmission"]}
    rates = [buckets[b]["recovery_rate_of_decided"] for b in ("0-14", "15-30", "31-60", "61+")]
    assert rates == sorted(rates, reverse=True), rates

    past = [c for c in recovery["by_filing_position"] if c["filing_position"] == "past_deadline"]
    assert past and sum(c["decided"] for c in past) >= 50
    assert all(c["recovery_rate_of_decided"] < 0.15 for c in past), past


def test_full_scale_recovery_is_censored_at_the_edge(full_key: dict) -> None:
    """The newest load must not tell a finished story about unfinished work."""
    recovery = full_key["recovery"]
    assert recovery["snap_003"]["censoring"]["resubmitted_no_outcome_yet"] > 0
    truth = recovery["world_truth"]
    assert truth["resubmission_after_newest_watermark"] > 0
    assert truth["settled_by_newest_watermark"] < truth["chains"]
    counts = [recovery[s]["events"] for s in ("snap_001", "snap_002", "snap_003")]
    assert counts == sorted(counts) and counts[0] < counts[-1]


def test_full_scale_planted_counts(full_key: dict) -> None:
    s5 = full_key["scenarios"]["5_timely_filing_state_medicaid_hmo"]["snap_003"]
    assert 400 <= s5["unsubmitted_july_claims"] <= 420
    assert s5["carc29_denials"] >= 13
    s1 = full_key["scenarios"]["1_denial_spike_meridian_imaging"]["snap_003"]
    assert s1["post_over_pre_rate_ratio"] > 4.0
