"""The additive 2024 backfill: shape, closure, and non-perturbation.

The load-bearing test is `test_*_identical_without_the_backfill`: a build with
`include_backfill=False` must reproduce the pre-backfill warehouse exactly on
the pre-existing id space. Everything else here asserts the two properties the
backfill is allowed to have — a real comparison year, and no reach into any
2026 answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from revi_warehouse.config import BACKFILL, ORGANIC_ERA_START, SNAPSHOTS, GeneratorConfig, day
from revi_warehouse.generate import GenerationResult, run_generation

SNAP_NAMES = ("snap_001", "snap_002", "snap_003")
FACT_TABLES = ("fact_claim", "fact_claim_line", "fact_remit", "fact_transaction", "fact_denial")
ORDER_COLUMNS = {
    "fact_claim": "claim_id",
    "fact_claim_line": "claim_line_id",
    "fact_remit": "remit_id",
    "fact_transaction": "txn_id",
    "fact_denial": "denial_id",
}
ERA_START = "2025-01-01"
RESOLVED_BY = "2025-06-30"


@pytest.fixture(scope="module")
def no_backfill(
    small_config: GeneratorConfig, tmp_path_factory: pytest.TempPathFactory
) -> GenerationResult:
    """The same world with the 2024 backfill switched off."""
    out = tmp_path_factory.mktemp("warehouse-no-backfill") / "revi_small.duckdb"
    return run_generation(
        GeneratorConfig(**{**small_config.__dict__, "include_backfill": False}), out
    )


def _rows(path: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _one(path: Path, sql: str) -> tuple:
    row = _rows(path, sql)
    assert len(row) == 1
    return row[0]


# ---------------------------------------------------------------------------
# non-perturbation: the backfill is additive and nothing else


def test_pre_existing_rows_identical_without_the_backfill(
    small_result: GenerationResult, no_backfill: GenerationResult
) -> None:
    """Every fact row below the first backfill claim id is byte-identical."""
    first = small_result.answer_key["backfill_meta"]["first_backfill_claim_id"]
    assert first is not None
    for schema in SNAP_NAMES:
        for table in FACT_TABLES:
            order = ORDER_COLUMNS[table]
            sql = (
                f"SELECT * FROM {schema}.{table} WHERE claim_id < '{first}' ORDER BY {order}"
            )
            with_bf = _rows(small_result.db_path, sql)
            without = _rows(no_backfill.db_path, f"SELECT * FROM {schema}.{table} ORDER BY {order}")
            assert with_bf == without, f"{schema}.{table} perturbed by the backfill"


def test_dimensions_other_than_the_calendar_are_untouched(
    small_result: GenerationResult, no_backfill: GenerationResult
) -> None:
    """The backfill reuses existing members; it never invents a dimension row."""
    dims = {
        "dim_payer": "payer_id",
        "dim_plan": "plan_id",
        "dim_provider": "provider_id",
        "dim_facility": "facility_id",
        "dim_service_line": "service_line_id",
        "dim_patient": "patient_id",
        "dim_denial_code": "carc_code",
    }
    for table, order in dims.items():
        sql = f"SELECT * FROM snap_003.{table} ORDER BY {order}"
        assert _rows(small_result.db_path, sql) == _rows(no_backfill.db_path, sql), table


def test_detected_anomalies_identical_without_the_backfill(
    small_result: GenerationResult, no_backfill: GenerationResult
) -> None:
    for schema in SNAP_NAMES:
        sql = f"SELECT * FROM {schema}.detected_anomalies ORDER BY anomaly_id"
        assert _rows(small_result.db_path, sql) == _rows(no_backfill.db_path, sql), schema


def test_answer_key_scenarios_identical_without_the_backfill(
    small_result: GenerationResult, no_backfill: GenerationResult
) -> None:
    """All five scenarios, all three watermarks, value-identical."""
    assert json.dumps(small_result.answer_key["scenarios"], sort_keys=True) == json.dumps(
        no_backfill.answer_key["scenarios"], sort_keys=True
    )
    assert json.dumps(small_result.answer_key["anomalies"], sort_keys=True) == json.dumps(
        no_backfill.answer_key["anomalies"], sort_keys=True
    )


def test_backfill_meta_records_the_switch(no_backfill: GenerationResult) -> None:
    meta = no_backfill.answer_key["backfill_meta"]
    assert meta["enabled"] is False
    assert meta["claims"] == 0
    assert meta["first_backfill_claim_id"] is None
    assert no_backfill.answer_key["config"]["include_backfill"] is False


# ---------------------------------------------------------------------------
# closure: nothing the backfill plants can reach a 2026 answer


def test_every_backfill_claim_is_a_closed_2024_claim(small_result: GenerationResult) -> None:
    first = small_result.answer_key["backfill_meta"]["first_backfill_claim_id"]
    for snap in SNAPSHOTS:
        bad = _one(
            small_result.db_path,
            f"""
            SELECT count(*) FILTER (WHERE service_date < DATE '2024-01-01'
                                       OR service_date >= DATE '{ERA_START}'),
                   count(*) FILTER (WHERE status NOT IN ('PAID', 'CLOSED')),
                   count(*) FILTER (WHERE submission_date IS NULL),
                   count(*) FILTER (WHERE resolved_date IS NULL
                                       OR resolved_date > DATE '{RESOLVED_BY}')
            FROM {snap.schema_name}.fact_claim WHERE claim_id >= '{first}'
            """,
        )
        assert bad == (0, 0, 0, 0), f"{snap.schema_name}: {bad}"


def test_no_backfill_activity_after_the_closing_deadline(small_result: GenerationResult) -> None:
    """One closure proof covers every scenario, anomaly and trailing window."""
    first = small_result.answer_key["backfill_meta"]["first_backfill_claim_id"]
    for snap in SNAPSHOTS:
        s = snap.schema_name
        late = _one(
            small_result.db_path,
            f"""
            SELECT (SELECT count(*) FROM {s}.fact_transaction
                    WHERE claim_id >= '{first}' AND post_date > DATE '{RESOLVED_BY}')
                 + (SELECT count(*) FROM {s}.fact_remit
                    WHERE claim_id >= '{first}' AND remit_date > DATE '{RESOLVED_BY}')
                 + (SELECT count(*) FROM {s}.fact_denial
                    WHERE claim_id >= '{first}' AND (denial_date > DATE '{RESOLVED_BY}'
                          OR appeal_decision_date > DATE '{RESOLVED_BY}'))
                 + (SELECT count(*) FROM {s}.fact_claim_line
                    WHERE claim_id >= '{first}' AND charge_entry_date > DATE '{RESOLVED_BY}')
            """,
        )
        assert late == (0,), f"{s}: {late[0]} backfill rows after {RESOLVED_BY}"


def test_backfill_denials_all_carry_a_terminal_appeal_status(
    small_result: GenerationResult,
) -> None:
    """No 2024 denial is left mid-appeal: it was worked to a decision or written off."""
    first = small_result.answer_key["backfill_meta"]["first_backfill_claim_id"]
    counts = dict(
        _rows(
            small_result.db_path,
            f"SELECT appeal_status, count(*) FROM snap_003.fact_denial "
            f"WHERE claim_id >= '{first}' GROUP BY 1",
        )
    )
    assert counts.get("APPEALED", 0) == 0, counts
    assert counts.get("OVERTURNED", 0) > 0 and counts.get("UPHELD", 0) > 0, counts
    assert counts.get("NONE", 0) > 0, counts  # never-appealed denials exist and were written off


def test_the_backfill_appears_identically_in_every_snapshot(
    small_result: GenerationResult,
) -> None:
    """2024 predates every cutoff, so the block is the same at all three watermarks."""
    first = small_result.answer_key["backfill_meta"]["first_backfill_claim_id"]
    counts = [
        _one(small_result.db_path, f"SELECT count(*) FROM {s}.fact_claim WHERE claim_id >= '{first}'")
        for s in SNAP_NAMES
    ]
    assert len(set(counts)) == 1, counts
    rows = [
        _rows(small_result.db_path, f"SELECT * FROM {s}.fact_claim WHERE claim_id >= '{first}' "
              "ORDER BY claim_id")
        for s in SNAP_NAMES
    ]
    assert rows[0] == rows[1] == rows[2]


def test_backfill_contributes_nothing_to_a_trailing_window(
    small_result: GenerationResult,
) -> None:
    """Trailing windows anchored at the newest watermark never reach the backfill."""
    first = small_result.answer_key["backfill_meta"]["first_backfill_claim_id"]
    n = _one(
        small_result.db_path,
        f"""
        SELECT count(*) FROM snap_003.fact_transaction
        WHERE claim_id >= '{first}' AND post_date >= DATE '2025-08-03'
        """,
    )
    assert n == (0,)


# ---------------------------------------------------------------------------
# shape: the comparison year says something honest on all three axes


def test_volume_growth_story(small_result: GenerationResult) -> None:
    """2024 runs at ~`volume_ratio` of the 2025 claims-per-day rate."""
    per_day = dict(
        _rows(
            small_result.db_path,
            """
            SELECT year(service_date),
                   count(*) / (CASE WHEN year(service_date) = 2024 THEN 366.0 ELSE 365.0 END)
            FROM snap_003.fact_claim WHERE service_date < DATE '2026-01-01'
            GROUP BY 1
            """,
        )
    )
    ratio = per_day[2024] / per_day[2025]
    assert abs(ratio - BACKFILL.volume_ratio) < 0.03, ratio


def test_denial_rate_sits_below_the_2025_level(small_result: GenerationResult) -> None:
    rates = dict(
        _rows(
            small_result.db_path,
            """
            WITH c AS (SELECT claim_id, year(service_date) AS yr FROM snap_003.fact_claim
                       WHERE service_date < DATE '2026-01-01'),
            d AS (SELECT DISTINCT claim_id FROM snap_003.fact_denial)
            SELECT yr, count(d.claim_id)::DOUBLE / count(*)
            FROM c LEFT JOIN d USING (claim_id) GROUP BY 1
            """,
        )
    )
    assert rates[2024] < rates[2025], rates
    assert rates[2024] > 0.5 * rates[2025], rates  # slightly below, not a different world


def test_payer_mix_drifts_mildly_without_dropping_a_payer(
    small_result: GenerationResult,
) -> None:
    shares = _rows(
        small_result.db_path,
        """
        WITH x AS (SELECT payer_name, year(service_date) AS yr FROM snap_003.v_claim
                   WHERE service_date < DATE '2026-01-01')
        SELECT payer_name,
               count(*) FILTER (WHERE yr = 2024)::DOUBLE
                   / SUM(count(*) FILTER (WHERE yr = 2024)) OVER (),
               count(*) FILTER (WHERE yr = 2025)::DOUBLE
                   / SUM(count(*) FILTER (WHERE yr = 2025)) OVER ()
        FROM x GROUP BY payer_name
        """,
    )
    assert len(shares) == 12
    for name, s24, s25 in shares:
        assert s24 > 0 and s25 > 0, f"{name} missing from a year"
        assert abs(s24 - s25) < 0.05, f"{name} mix shift is not mild: {s24:.3f} -> {s25:.3f}"
    assert any(abs(s24 - s25) > 0.002 for _n, s24, s25 in shares), "no mix drift at all"


def test_cycle_times_are_comparable_across_the_year_boundary(
    small_result: GenerationResult,
) -> None:
    """The tightened backfill lag clips must not fake a cycle-time improvement."""
    lags = dict(
        _rows(
            small_result.db_path,
            """
            SELECT year(c.service_date),
                   AVG(DATE_DIFF('day', c.submission_date, t.post_date))
            FROM snap_003.fact_claim c
            JOIN (SELECT claim_id, MIN(post_date) AS post_date FROM snap_003.fact_transaction
                  WHERE txn_type = 'PAYMENT' GROUP BY 1) t USING (claim_id)
            WHERE c.service_date < DATE '2026-01-01' GROUP BY 1
            """,
        )
    )
    assert abs(float(lags[2024]) - float(lags[2025])) < 1.0, lags


def test_the_calendar_covers_the_backfill_year(small_result: GenerationResult) -> None:
    span = _one(
        small_result.db_path,
        "SELECT CAST(MIN(cal_date) AS VARCHAR), CAST(MAX(cal_date) AS VARCHAR), count(*) "
        "FROM snap_003.dim_calendar",
    )
    assert span == ("2024-01-01", "2026-12-31", 1096)  # 366 + 365 + 365
    holidays = _one(
        small_result.db_path,
        "SELECT count(*) FROM snap_003.dim_calendar WHERE cal_date < DATE '2025-01-01' "
        "AND NOT is_business_day AND dayofweek(cal_date) BETWEEN 1 AND 5",
    )
    assert holidays == (6,)


def test_backfill_constants_agree_with_the_data(small_result: GenerationResult) -> None:
    meta = small_result.answer_key["backfill_meta"]
    assert meta["enabled"] is True
    assert meta["organic_era_start"] == ERA_START
    assert day(ERA_START) == ORGANIC_ERA_START
    assert meta["service_window"] == {"start": "2024-01-01", "end": "2024-12-31"}
    assert meta["resolved_by"] == RESOLVED_BY
    assert meta["observed_last_resolved_date"] <= RESOLVED_BY
    assert meta["claims"] > 0
    assert day(meta["service_window"]["start"]) == BACKFILL.service_start
    assert day(meta["service_window"]["end"]) == BACKFILL.service_end
