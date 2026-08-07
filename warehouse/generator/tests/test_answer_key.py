"""Answer-key structure: per scenario per watermark, computed from the data."""

from __future__ import annotations

import json

import duckdb

from revi_warehouse.generate import GenerationResult

SCENARIO_KEYS = {
    "1_denial_spike_meridian_imaging",
    "2_cob_silverline",
    "3_cash_decline",
    "4_underpayment_northbridge_ortho",
    "5_timely_filing_state_medicaid_hmo",
}


def test_file_written_and_loadable(small_result: GenerationResult) -> None:
    on_disk = json.loads(small_result.answer_key_path.read_text())
    assert on_disk == small_result.answer_key


def test_top_level_structure(small_result: GenerationResult) -> None:
    key = small_result.answer_key
    assert key["seed"] == 20260807
    assert key["config"]["scale"] == "small"
    assert set(key["scenarios"]) == SCENARIO_KEYS
    assert [w["watermark_id"] for w in key["watermarks"]] == ["wm_001", "wm_002", "wm_003"]
    assert key["watermarks"][2]["loaded_at"] == "2026-08-03 04:10:00"
    assert set(key["row_counts"]) == {"snap_001", "snap_002", "snap_003"}


def test_cash_decline_recorded_values_match_database(small_result: GenerationResult) -> None:
    """The recorded weekly totals must equal a direct recomputation from the data."""
    s3 = small_result.answer_key["scenarios"]["3_cash_decline"]["snap_003"]
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        row = con.execute(
            """
            SELECT COALESCE(SUM(amount_cents) FILTER (
                       WHERE post_date BETWEEN DATE '2026-07-20' AND DATE '2026-07-26'), 0),
                   COALESCE(SUM(amount_cents) FILTER (
                       WHERE post_date BETWEEN DATE '2026-07-27' AND DATE '2026-08-02'), 0)
            FROM snap_003.fact_transaction WHERE txn_type = 'PAYMENT'
            """
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    assert s3["week_prior"]["payer_cash_cents"] == row[0]
    assert s3["week_decline"]["payer_cash_cents"] == row[1]
    assert s3["delta_cents"] == row[1] - row[0]


def test_per_payer_deltas_cover_all_payers(small_result: GenerationResult) -> None:
    s3 = small_result.answer_key["scenarios"]["3_cash_decline"]["snap_003"]
    assert len(s3["by_payer"]) == 12
    for row in s3["by_payer"]:
        assert row["delta_cents"] == row["week_decline_cents"] - row["week_prior_cents"]


def test_scenario_values_evolve_across_watermarks(small_result: GenerationResult) -> None:
    """Later watermarks see strictly more activity for the timely-filing cluster ages."""
    s5 = small_result.answer_key["scenarios"]["5_timely_filing_state_medicaid_hmo"]
    ages = [s5[s]["age_days_at_watermark"]["max"] for s in ("snap_001", "snap_002", "snap_003")]
    assert ages == sorted(ages)
    cash = small_result.answer_key["scenarios"]["3_cash_decline"]
    decline_totals = [
        cash[s]["week_decline"]["payer_cash_cents"] for s in ("snap_001", "snap_002", "snap_003")
    ]
    assert decline_totals == sorted(decline_totals)  # the decline week fills in as loads arrive
