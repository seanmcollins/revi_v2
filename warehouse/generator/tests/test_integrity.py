"""Structural integrity: watermarks, FKs, reconciliation, snapshot truncation."""

from __future__ import annotations

import duckdb

from revi_warehouse.config import SNAPSHOTS, GeneratorConfig
from revi_warehouse.generate import GenerationResult
from revi_warehouse.verify import _structural_checks


def test_all_structural_checks_pass(small_result: GenerationResult) -> None:
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        checks = _structural_checks(con)
    finally:
        con.close()
    failures = [c for c in checks if not c.ok]
    assert not failures, [f"{c.name}: {c.detail}" for c in failures]


def test_watermarks_match_design_doc(small_result: GenerationResult) -> None:
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT watermark_id, schema_name, CAST(loaded_at AS VARCHAR), "
            "CAST(newest_data_date AS VARCHAR) FROM main.watermarks ORDER BY watermark_id"
        ).fetchall()
    finally:
        con.close()
    assert rows == [
        ("wm_001", "snap_001", "2026-08-01 04:05:00", "2026-07-31"),
        ("wm_002", "snap_002", "2026-08-02 04:12:00", "2026-08-01"),
        ("wm_003", "snap_003", "2026-08-03 04:10:00", "2026-08-02"),
    ]


def test_snapshots_grow_monotonically(small_result: GenerationResult) -> None:
    counts = small_result.row_counts
    for table in (
        "fact_claim",
        "fact_claim_line",
        "fact_remit",
        "fact_transaction",
        "fact_denial",
        "fact_recovery_event",
    ):
        sizes = [counts[s.schema_name][table] for s in SNAPSHOTS]
        assert sizes == sorted(sizes), f"{table}: {sizes}"
        assert sizes[0] < sizes[-1], f"{table} gained no rows across snapshots: {sizes}"


def test_activity_after_cutoff_absent_and_present(small_result: GenerationResult) -> None:
    """A transaction posted 2026-08-01..02 exists in snap_003 but not snap_001."""
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        late_in_3 = con.execute(
            "SELECT count(*) FROM snap_003.fact_transaction "
            "WHERE post_date > DATE '2026-07-31'"
        ).fetchone()
        late_in_1 = con.execute(
            "SELECT count(*) FROM snap_001.fact_transaction "
            "WHERE post_date > DATE '2026-07-31'"
        ).fetchone()
    finally:
        con.close()
    assert late_in_3 is not None and late_in_3[0] > 0
    assert late_in_1 is not None and late_in_1[0] == 0


def test_derived_status_reflects_snapshot_visibility(small_result: GenerationResult) -> None:
    """Claims paid between snap_001 and snap_003 cutoffs flip OPEN/DENIED -> PAID."""
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        flipped = con.execute(
            """
            SELECT count(*) FROM snap_001.fact_claim a
            JOIN snap_003.fact_claim b USING (claim_id)
            WHERE a.status <> 'PAID' AND b.status = 'PAID'
            """
        ).fetchone()
        regressed = con.execute(
            """
            SELECT count(*) FROM snap_001.fact_claim a
            JOIN snap_003.fact_claim b USING (claim_id)
            WHERE a.status = 'PAID' AND b.status <> 'PAID'
            """
        ).fetchone()
    finally:
        con.close()
    assert flipped is not None and flipped[0] > 0
    assert regressed is not None and regressed[0] == 0


def test_base_views_exist_and_join_cleanly(small_result: GenerationResult) -> None:
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        for snap in SNAPSHOTS:
            for view, base in (
                ("v_claim", "fact_claim"),
                ("v_claim_line", "fact_claim_line"),
                ("v_transaction", "fact_transaction"),
                ("v_remit", "fact_remit"),
                ("v_denial", "fact_denial"),
                ("v_recovery_event", "fact_recovery_event"),
            ):
                view_n = con.execute(f"SELECT count(*) FROM {snap.schema_name}.{view}").fetchone()
                base_n = con.execute(f"SELECT count(*) FROM {snap.schema_name}.{base}").fetchone()
                assert view_n is not None and base_n is not None
                assert view_n[0] == base_n[0], f"{snap.schema_name}.{view} loses rows on join"
    finally:
        con.close()


def test_money_is_bigint_cents_and_dates_are_dates(small_result: GenerationResult) -> None:
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        types = dict(
            con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'snap_003' AND table_name = 'fact_claim'"
            ).fetchall()
        )
    finally:
        con.close()
    for col in ("billed_amount_cents", "expected_amount_cents", "patient_responsibility_cents"):
        assert types[col] == "BIGINT", (col, types[col])
    for col in ("service_date", "discharge_date", "submission_date", "resolved_date"):
        assert types[col] == "DATE", (col, types[col])


def test_synthetic_shapes(small_result: GenerationResult, small_config: GeneratorConfig) -> None:
    """PHI-shaped but clearly synthetic: 99-prefixed NPIs, ZM member ids, XX### proc codes."""
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        bad_npi = con.execute(
            "SELECT count(*) FROM snap_001.dim_provider WHERE npi_synthetic NOT LIKE '99%'"
        ).fetchone()
        bad_member = con.execute(
            "SELECT count(*) FROM snap_001.dim_patient WHERE member_id_synthetic NOT LIKE 'ZM%'"
        ).fetchone()
        bad_proc = con.execute(
            "SELECT count(*) FROM snap_003.fact_claim_line "
            "WHERE NOT regexp_matches(proc_code, '^X[A-Z][0-9]{3}$')"
        ).fetchone()
        patients = con.execute("SELECT count(*) FROM snap_001.dim_patient").fetchone()
    finally:
        con.close()
    assert bad_npi is not None and bad_npi[0] == 0
    assert bad_member is not None and bad_member[0] == 0
    assert bad_proc is not None and bad_proc[0] == 0
    assert patients is not None and patients[0] == small_config.n_patients
