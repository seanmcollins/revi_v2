"""Write the projected snapshots into one DuckDB file.

Layout: schemas snap_001..snap_003 (simulated consecutive nightly loads), each
holding the same dimension tables, the truncated fact tables, and curated
per-grain base views (v_claim, v_claim_line, v_transaction, v_remit, v_denial)
that pre-join the dimensions so downstream SQL never invents joins. main holds
the watermarks table (loaded_at values match design doc section 10.3 verbatim).

Performance note: only numeric/datetime numpy arrays are registered with DuckDB
(object-dtype registration is pathologically slow); ID strings and enum labels
are built in SQL via printf/CASE/tiny lookup joins. Dimension tables are created
once and copied into the other snapshot schemas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from revi_warehouse.config import SNAPSHOTS, GeneratorConfig
from revi_warehouse.dims import (
    DENIAL_CODES,
    FACILITIES,
    FIRST_NAMES,
    LAST_NAMES,
    PAYERS,
    PLANS,
    PROC_GROUPS,
    SERVICE_LINES,
    ZIP_CODES,
    Dims,
    calendar_rows,
)
from revi_warehouse.project import SnapshotTables
from revi_warehouse.world import World

FACT_TABLES = ("fact_claim", "fact_claim_line", "fact_remit", "fact_transaction", "fact_denial")
DIM_TABLES = (
    "dim_payer",
    "dim_plan",
    "dim_provider",
    "dim_facility",
    "dim_service_line",
    "dim_patient",
    "dim_denial_code",
    "dim_calendar",
)

_CLAIM_ID = "printf('CLM-%07d', claim_idx + 1)"

# Full CTAS statements per fact table; {sch} is the target schema. The source
# relation is always registered as _revi_src.
_FACT_SQL: dict[str, str] = {
    "fact_claim": """
        CREATE TABLE {sch}.fact_claim AS
        SELECT printf('CLM-%07d', idx + 1) AS claim_id,
               printf('PAT-%06d', patient_i + 1) AS patient_id,
               printf('PAY-%02d', payer_i + 1) AS payer_id,
               printf('PLN-%02d', plan_i + 1) AS plan_id,
               printf('PRV-%03d', provider_i + 1) AS provider_id,
               printf('FAC-%d', facility_i + 1) AS facility_id,
               printf('SVC-%02d', svcline_i + 1) AS service_line_id,
               CASE WHEN inst THEN 'INSTITUTIONAL' ELSE 'PROFESSIONAL' END AS claim_type,
               CASE pseq WHEN 0 THEN 'P' WHEN 1 THEN 'S' ELSE 'T' END AS payer_sequence,
               CAST(other_insurance_flag AS BOOLEAN) AS other_insurance_flag,
               CAST(cob_mismatch_flag AS BOOLEAN) AS cob_mismatch_flag,
               CAST(service_date AS DATE) AS service_date,
               CAST(discharge_date AS DATE) AS discharge_date,
               CAST(submission_date AS DATE) AS submission_date,
               CAST(billed_amount_cents AS BIGINT) AS billed_amount_cents,
               CAST(expected_amount_cents AS BIGINT) AS expected_amount_cents,
               CAST(patient_responsibility_cents AS BIGINT) AS patient_responsibility_cents,
               CASE status_code WHEN 0 THEN 'OPEN' WHEN 1 THEN 'PAID'
                    WHEN 2 THEN 'DENIED' ELSE 'CLOSED' END AS status,
               CAST(first_pass_paid AS BOOLEAN) AS first_pass_paid,
               CAST(clean_claim AS BOOLEAN) AS clean_claim,
               CAST(resolved_date AS DATE) AS resolved_date
        FROM _revi_src
        ORDER BY claim_id
    """,
    "fact_claim_line": f"""
        CREATE TABLE {{sch}}.fact_claim_line AS
        SELECT printf('CLM-%07d-L%d', claim_idx + 1, line_num) AS claim_line_id,
               {_CLAIM_ID} AS claim_id,
               CAST(line_num AS INTEGER) AS line_num,
               printf('%s%03d', pg.code_prefix, 100 + 7 * code_i) AS proc_code,
               pg.proc_group AS proc_group,
               pg.revenue_code AS revenue_code,
               CAST(units AS INTEGER) AS units,
               CAST(charge_entry_date AS DATE) AS charge_entry_date,
               CAST(billed_amount_cents AS BIGINT) AS billed_amount_cents,
               CAST(allowed_amount_cents AS BIGINT) AS allowed_amount_cents,
               CAST(service_date AS DATE) AS service_date
        FROM _revi_src
        JOIN _revi_proc_groups pg USING (group_i)
        ORDER BY claim_id, line_num
    """,
    "fact_remit": f"""
        CREATE TABLE {{sch}}.fact_remit AS
        SELECT printf('RMT-%07d', ridx + 1) AS remit_id,
               {_CLAIM_ID} AS claim_id,
               printf('PAY-%02d', payer_i + 1) AS payer_id,
               CAST(remit_date AS DATE) AS remit_date,
               CAST(remit_seq AS INTEGER) AS remit_seq
        FROM _revi_src
        ORDER BY remit_id
    """,
    "fact_transaction": f"""
        CREATE TABLE {{sch}}.fact_transaction AS
        SELECT printf('TXN-%07d', tidx + 1) AS txn_id,
               {_CLAIM_ID} AS claim_id,
               CASE WHEN line_claim_idx < 0 THEN NULL
                    ELSE printf('CLM-%07d-L%d', line_claim_idx + 1, line_num) END AS claim_line_id,
               CASE WHEN remit_idx < 0 THEN NULL
                    ELSE printf('RMT-%07d', remit_idx + 1) END AS remit_id,
               CASE type_code WHEN 0 THEN 'PAYMENT' WHEN 1 THEN 'CONTRACTUAL_ADJ'
                    WHEN 2 THEN 'OTHER_ADJ' WHEN 3 THEN 'PATIENT_PAYMENT' ELSE 'REFUND' END AS txn_type,
               CAST(amount_cents AS BIGINT) AS amount_cents,
               CAST(post_date AS DATE) AS post_date,
               CAST(remit_date AS DATE) AS remit_date
        FROM _revi_src
        ORDER BY txn_id
    """,
    "fact_denial": f"""
        CREATE TABLE {{sch}}.fact_denial AS
        SELECT printf('DEN-%06d', didx + 1) AS denial_id,
               {_CLAIM_ID} AS claim_id,
               CASE WHEN line_claim_idx < 0 THEN NULL
                    ELSE printf('CLM-%07d-L%d', line_claim_idx + 1, line_num) END AS claim_line_id,
               printf('RMT-%07d', remit_idx + 1) AS remit_id,
               CASE group_code_i WHEN 0 THEN 'CO' WHEN 1 THEN 'PR'
                    WHEN 2 THEN 'OA' ELSE 'PI' END AS group_code,
               CAST(carc_code AS INTEGER) AS carc_code,
               CASE WHEN rarc_i < 0 THEN NULL ELSE printf('RZ%02d', rarc_i) END AS rarc_synthetic,
               CASE WHEN level_line THEN 'LINE' ELSE 'CLAIM' END AS denial_level,
               CAST(denial_date AS DATE) AS denial_date,
               CAST(denied_amount_cents AS BIGINT) AS denied_amount_cents,
               CASE appeal_code WHEN 0 THEN 'NONE' WHEN 1 THEN 'APPEALED'
                    WHEN 2 THEN 'OVERTURNED' ELSE 'UPHELD' END AS appeal_status,
               CAST(appeal_decision_date AS DATE) AS appeal_decision_date
        FROM _revi_src
        ORDER BY denial_id
    """,
}

_VIEWS: dict[str, str] = {
    "v_claim": """
        SELECT c.*,
               p.payer_name, p.payer_type, p.financial_class,
               pl.plan_name, pl.product_type, pl.timely_filing_days, pl.timely_filing_basis,
               pr.provider_name, pr.specialty AS provider_specialty, pr.npi_synthetic,
               f.facility_name, f.region,
               s.service_line_name
        FROM {sch}.fact_claim c
        JOIN {sch}.dim_payer p USING (payer_id)
        JOIN {sch}.dim_plan pl USING (plan_id)
        JOIN {sch}.dim_provider pr USING (provider_id)
        JOIN {sch}.dim_facility f USING (facility_id)
        JOIN {sch}.dim_service_line s USING (service_line_id)
    """,
    "v_claim_line": """
        SELECT l.*,
               c.payer_id, c.plan_id, c.provider_id, c.facility_id, c.service_line_id,
               c.claim_type, c.payer_sequence, c.status AS claim_status,
               c.submission_date, c.service_date AS claim_service_date,
               p.payer_name, p.payer_type, p.financial_class,
               pl.plan_name, pl.product_type,
               f.facility_name, f.region,
               s.service_line_name
        FROM {sch}.fact_claim_line l
        JOIN {sch}.fact_claim c USING (claim_id)
        JOIN {sch}.dim_payer p USING (payer_id)
        JOIN {sch}.dim_plan pl USING (plan_id)
        JOIN {sch}.dim_facility f USING (facility_id)
        JOIN {sch}.dim_service_line s USING (service_line_id)
    """,
    "v_transaction": """
        SELECT t.txn_id, t.claim_id, t.claim_line_id, t.remit_id, t.txn_type,
               t.amount_cents, t.post_date, t.remit_date,
               c.payer_id, c.plan_id, c.facility_id, c.service_line_id,
               c.claim_type, c.service_date, c.submission_date,
               p.payer_name, p.payer_type, p.financial_class,
               pl.plan_name, pl.product_type,
               f.facility_name, f.region,
               s.service_line_name
        FROM {sch}.fact_transaction t
        JOIN {sch}.fact_claim c USING (claim_id)
        JOIN {sch}.dim_payer p USING (payer_id)
        JOIN {sch}.dim_plan pl USING (plan_id)
        JOIN {sch}.dim_facility f USING (facility_id)
        JOIN {sch}.dim_service_line s USING (service_line_id)
    """,
    "v_remit": """
        SELECT r.remit_id, r.claim_id, r.payer_id, r.remit_date, r.remit_seq,
               c.plan_id, c.facility_id, c.service_line_id, c.service_date, c.submission_date,
               p.payer_name, p.payer_type, p.financial_class,
               f.facility_name, f.region,
               s.service_line_name
        FROM {sch}.fact_remit r
        JOIN {sch}.fact_claim c USING (claim_id)
        JOIN {sch}.dim_payer p ON r.payer_id = p.payer_id
        JOIN {sch}.dim_facility f USING (facility_id)
        JOIN {sch}.dim_service_line s USING (service_line_id)
    """,
    "v_denial": """
        SELECT d.denial_id, d.claim_id, d.claim_line_id, d.remit_id, d.group_code, d.carc_code,
               d.rarc_synthetic, d.denial_level, d.denial_date, d.denied_amount_cents,
               d.appeal_status, d.appeal_decision_date,
               dc.description_paraphrase AS carc_description, dc.category AS denial_category,
               c.payer_id, c.plan_id, c.facility_id, c.service_line_id,
               c.claim_type, c.service_date, c.submission_date,
               p.payer_name, p.payer_type, p.financial_class,
               pl.plan_name, pl.product_type,
               f.facility_name, f.region,
               s.service_line_name
        FROM {sch}.fact_denial d
        JOIN {sch}.dim_denial_code dc USING (carc_code)
        JOIN {sch}.fact_claim c USING (claim_id)
        JOIN {sch}.dim_payer p USING (payer_id)
        JOIN {sch}.dim_plan pl USING (plan_id)
        JOIN {sch}.dim_facility f USING (facility_id)
        JOIN {sch}.dim_service_line s USING (service_line_id)
    """,
}


def _o(values: list[Any]) -> np.ndarray:
    return np.array(values, dtype=object)


def _register_lookups(con: duckdb.DuckDBPyConnection, dims: Dims) -> None:
    """Small lookup relations used by fact/dim CTAS statements."""
    con.register(
        "_revi_proc_groups",
        {
            "group_i": np.arange(len(PROC_GROUPS), dtype=np.int64),
            "proc_group": _o([g[0] for g in PROC_GROUPS]),
            "code_prefix": _o([g[1] for g in PROC_GROUPS]),
            "revenue_code": _o([g[2] for g in PROC_GROUPS]),
        },
    )
    con.register(
        "_revi_first_names",
        {"first_i": np.arange(len(FIRST_NAMES), dtype=np.int64), "first_name": _o(list(FIRST_NAMES))},
    )
    con.register(
        "_revi_last_names",
        {"last_i": np.arange(len(LAST_NAMES), dtype=np.int64), "last_name": _o(list(LAST_NAMES))},
    )
    con.register(
        "_revi_zips",
        {"zip_i": np.arange(len(ZIP_CODES), dtype=np.int64), "zip": _o(list(ZIP_CODES))},
    )


def _create_dims(con: duckdb.DuckDBPyConnection, sch: str, dims: Dims) -> None:
    """Create dimension tables in one schema (they are then copied to the others)."""
    small_dims: dict[str, tuple[dict[str, Any], str]] = {
        "dim_payer": (
            {
                "payer_id": _o(dims.payer_ids),
                "payer_name": _o([p.name for p in PAYERS]),
                "payer_type": _o([p.payer_type for p in PAYERS]),
                "financial_class": _o([p.financial_class for p in PAYERS]),
            },
            "payer_id",
        ),
        "dim_plan": (
            {
                "plan_id": _o(dims.plan_ids),
                "payer_id": _o([dims.payer_ids[i] for i in dims.plan_payer_idx]),
                "plan_name": _o([p[1] for p in PLANS]),
                "product_type": _o([p[2] for p in PLANS]),
                "timely_filing_days": np.array([p[3] for p in PLANS], dtype=np.int64),
                "timely_filing_basis": _o([p[4] for p in PLANS]),
            },
            "plan_id",
        ),
        "dim_provider": (
            {k: _o(v) for k, v in dims.provider_rows.items()},
            "provider_id",
        ),
        "dim_facility": (
            {
                "facility_id": _o(dims.facility_ids),
                "facility_name": _o([f[0] for f in FACILITIES]),
                "region": _o([f[1] for f in FACILITIES]),
            },
            "facility_id",
        ),
        "dim_service_line": (
            {
                "service_line_id": _o(dims.service_line_ids),
                "service_line_name": _o([s[0] for s in SERVICE_LINES]),
            },
            "service_line_id",
        ),
        "dim_denial_code": (
            {
                "carc_code": np.array([c[0] for c in DENIAL_CODES], dtype=np.int64),
                "description_paraphrase": _o([c[1] for c in DENIAL_CODES]),
                "category": _o([c[2] for c in DENIAL_CODES]),
            },
            "carc_code",
        ),
    }
    for table, (source, order_by) in small_dims.items():
        con.register("_revi_src", source)
        extra = (
            "CAST(timely_filing_days AS INTEGER) AS timely_filing_days" if table == "dim_plan" else None
        )
        if table == "dim_plan":
            con.execute(
                f"""CREATE TABLE {sch}.dim_plan AS
                SELECT CAST(plan_id AS VARCHAR) AS plan_id, CAST(payer_id AS VARCHAR) AS payer_id,
                       CAST(plan_name AS VARCHAR) AS plan_name,
                       CAST(product_type AS VARCHAR) AS product_type,
                       {extra},
                       CAST(timely_filing_basis AS VARCHAR) AS timely_filing_basis
                FROM _revi_src ORDER BY {order_by}"""
            )
        elif table == "dim_denial_code":
            con.execute(
                f"""CREATE TABLE {sch}.dim_denial_code AS
                SELECT CAST(carc_code AS INTEGER) AS carc_code,
                       CAST(description_paraphrase AS VARCHAR) AS description_paraphrase,
                       CAST(category AS VARCHAR) AS category
                FROM _revi_src ORDER BY {order_by}"""
            )
        else:
            cols = ", ".join(f"CAST({c} AS VARCHAR) AS {c}" for c in source)
            con.execute(f"CREATE TABLE {sch}.{table} AS SELECT {cols} FROM _revi_src ORDER BY {order_by}")
        con.unregister("_revi_src")

    con.register("_revi_src", dims.patient_cols)
    con.execute(
        f"""CREATE TABLE {sch}.dim_patient AS
        SELECT printf('PAT-%06d', idx + 1) AS patient_id,
               fn.first_name, ln.last_name,
               CAST(dob AS DATE) AS dob,
               printf('ZM%09d', 100000000 + member_i) AS member_id_synthetic,
               z.zip
        FROM _revi_src
        JOIN _revi_first_names fn USING (first_i)
        JOIN _revi_last_names ln USING (last_i)
        JOIN _revi_zips z USING (zip_i)
        ORDER BY patient_id"""
    )
    con.unregister("_revi_src")

    cal = calendar_rows()
    cal_source = {
        "cal_date": np.array(cal["cal_date"], dtype="datetime64[D]").astype("datetime64[us]"),
        "is_business_day": np.array(cal["is_business_day"], dtype=bool),
        "iso_week": np.array(cal["iso_week"], dtype=np.int64),
        "iso_year": np.array(cal["iso_year"], dtype=np.int64),
        "month": np.array(cal["month"], dtype=np.int64),
        "quarter": np.array(cal["quarter"], dtype=np.int64),
    }
    con.register("_revi_src", cal_source)
    con.execute(
        f"""CREATE TABLE {sch}.dim_calendar AS
        SELECT CAST(cal_date AS DATE) AS cal_date,
               CAST(is_business_day AS BOOLEAN) AS is_business_day,
               CAST(iso_week AS INTEGER) AS iso_week, CAST(iso_year AS INTEGER) AS iso_year,
               CAST(month AS INTEGER) AS month, CAST(quarter AS INTEGER) AS quarter
        FROM _revi_src ORDER BY cal_date"""
    )
    con.unregister("_revi_src")


def write_warehouse(path: Path, config: GeneratorConfig, world: World) -> dict[str, dict[str, int]]:
    """Write all snapshots + watermarks. Returns row counts per schema per table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    counts: dict[str, dict[str, int]] = {}
    try:
        con.execute(
            "CREATE TABLE main.watermarks("
            "watermark_id VARCHAR, schema_name VARCHAR, loaded_at TIMESTAMP, newest_data_date DATE)"
        )
        for snap in SNAPSHOTS:
            con.execute(
                "INSERT INTO main.watermarks VALUES (?, ?, ?, ?)",
                [snap.watermark_id, snap.schema_name, snap.loaded_at, snap.newest_data_date],
            )
        _register_lookups(con, world.dims)
        first_schema = SNAPSHOTS[0].schema_name
        for snap in SNAPSHOTS:
            sch = snap.schema_name
            con.execute(f"CREATE SCHEMA {sch}")
            if sch == first_schema:
                _create_dims(con, sch, world.dims)
            else:
                for dim_table in DIM_TABLES:
                    con.execute(
                        f"CREATE TABLE {sch}.{dim_table} AS "
                        f"SELECT * FROM {first_schema}.{dim_table} ORDER BY 1"
                    )
            tables = SnapshotTables(world, snap.cutoff_day)
            for fact_table in FACT_TABLES:
                con.register("_revi_src", getattr(tables, fact_table))
                con.execute(_FACT_SQL[fact_table].format(sch=sch))
                con.unregister("_revi_src")
            for view, sql in _VIEWS.items():
                con.execute(f"CREATE VIEW {sch}.{view} AS {sql.format(sch=sch)}")
            counts[sch] = {
                t: con.execute(f"SELECT count(*) FROM {sch}.{t}").fetchone()[0]  # type: ignore[index]
                for t in DIM_TABLES + FACT_TABLES
            }
    finally:
        con.close()
    return counts
