"""Same seed + same config => byte-identical logical content."""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from revi_warehouse.config import GeneratorConfig
from revi_warehouse.generate import GenerationResult, run_generation

SAMPLED_TABLES = (
    "snap_001.fact_claim",
    "snap_002.fact_transaction",
    "snap_003.fact_claim",
    "snap_003.fact_claim_line",
    "snap_003.fact_remit",
    "snap_003.fact_transaction",
    "snap_003.fact_denial",
    "snap_001.fact_recovery_event",
    "snap_003.fact_recovery_event",
    "snap_003.dim_recovery_class",
    "snap_003.dim_patient",
    "snap_002.detected_anomalies",
    "snap_003.detected_anomalies",
    "main.watermarks",
)

ORDER_COLUMNS = {
    "fact_claim": "claim_id",
    "fact_claim_line": "claim_line_id",
    "fact_remit": "remit_id",
    "fact_transaction": "txn_id",
    "fact_denial": "denial_id",
    "fact_recovery_event": "recovery_event_id",
    "dim_recovery_class": "carc_code",
    "dim_patient": "patient_id",
    "detected_anomalies": "anomaly_id",
    "watermarks": "watermark_id",
}


def _table_checksum(db_path: Path, qualified: str) -> str:
    table = qualified.split(".")[1]
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(f"SELECT * FROM {qualified} ORDER BY {ORDER_COLUMNS[table]}").fetchall()
    finally:
        con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


@pytest.fixture(scope="module")
def second_run(
    small_config: GeneratorConfig, tmp_path_factory: pytest.TempPathFactory
) -> GenerationResult:
    out = tmp_path_factory.mktemp("warehouse-rerun") / "revi_small.duckdb"
    return run_generation(small_config, out)


def test_row_counts_identical(small_result: GenerationResult, second_run: GenerationResult) -> None:
    assert small_result.row_counts == second_run.row_counts


def test_sampled_table_checksums_identical(
    small_result: GenerationResult, second_run: GenerationResult
) -> None:
    for qualified in SAMPLED_TABLES:
        first = _table_checksum(small_result.db_path, qualified)
        second = _table_checksum(second_run.db_path, qualified)
        assert first == second, f"checksum drift in {qualified}"


def test_answer_key_bytes_identical(
    small_result: GenerationResult, second_run: GenerationResult
) -> None:
    assert small_result.answer_key_path.read_bytes() == second_run.answer_key_path.read_bytes()


def test_seed_constant_recorded(small_result: GenerationResult) -> None:
    assert small_result.answer_key["seed"] == 20260807
