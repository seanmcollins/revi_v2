"""Self-check assertions run against the written warehouse (--verify).

Structural checks (both scales): watermark rows verbatim, FK integrity, line/claim
billed reconciliation, snapshot monotonicity, amount sanity. Scenario checks:
every planted signal must be detectable, with thresholds appropriate to scale.
A failing check makes the CLI exit nonzero.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from revi_warehouse.config import SNAPSHOTS, GeneratorConfig
from revi_warehouse.writer import FACT_TABLES


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


def _expect_zero(con: duckdb.DuckDBPyConnection, name: str, sql: str) -> Check:
    n = _count(con, sql)
    return Check(name, n == 0, f"{n} offending rows")


_FK_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "fact_claim.payer_id",
        "SELECT count(*) FROM {s}.fact_claim c LEFT JOIN {s}.dim_payer d USING (payer_id) "
        "WHERE d.payer_id IS NULL",
    ),
    (
        "fact_claim.plan_id",
        "SELECT count(*) FROM {s}.fact_claim c LEFT JOIN {s}.dim_plan d USING (plan_id) "
        "WHERE d.plan_id IS NULL",
    ),
    (
        "fact_claim.plan belongs to payer",
        "SELECT count(*) FROM {s}.fact_claim c JOIN {s}.dim_plan d USING (plan_id) "
        "WHERE d.payer_id <> c.payer_id",
    ),
    (
        "fact_claim.provider_id",
        "SELECT count(*) FROM {s}.fact_claim c LEFT JOIN {s}.dim_provider d USING (provider_id) "
        "WHERE d.provider_id IS NULL",
    ),
    (
        "fact_claim.facility_id",
        "SELECT count(*) FROM {s}.fact_claim c LEFT JOIN {s}.dim_facility d USING (facility_id) "
        "WHERE d.facility_id IS NULL",
    ),
    (
        "fact_claim.service_line_id",
        "SELECT count(*) FROM {s}.fact_claim c LEFT JOIN {s}.dim_service_line d "
        "USING (service_line_id) WHERE d.service_line_id IS NULL",
    ),
    (
        "fact_claim.patient_id",
        "SELECT count(*) FROM {s}.fact_claim c LEFT JOIN {s}.dim_patient d USING (patient_id) "
        "WHERE d.patient_id IS NULL",
    ),
    (
        "fact_claim_line.claim_id",
        "SELECT count(*) FROM {s}.fact_claim_line l LEFT JOIN {s}.fact_claim c USING (claim_id) "
        "WHERE c.claim_id IS NULL",
    ),
    (
        "fact_remit.claim_id",
        "SELECT count(*) FROM {s}.fact_remit r LEFT JOIN {s}.fact_claim c USING (claim_id) "
        "WHERE c.claim_id IS NULL",
    ),
    (
        "fact_transaction.claim_id",
        "SELECT count(*) FROM {s}.fact_transaction t LEFT JOIN {s}.fact_claim c USING (claim_id) "
        "WHERE c.claim_id IS NULL",
    ),
    (
        "fact_transaction.remit_id",
        "SELECT count(*) FROM {s}.fact_transaction t LEFT JOIN {s}.fact_remit r USING (remit_id) "
        "WHERE t.remit_id IS NOT NULL AND r.remit_id IS NULL",
    ),
    (
        "fact_transaction.claim_line_id",
        "SELECT count(*) FROM {s}.fact_transaction t LEFT JOIN {s}.fact_claim_line l "
        "USING (claim_line_id) WHERE t.claim_line_id IS NOT NULL AND l.claim_line_id IS NULL",
    ),
    (
        "fact_denial.claim_id",
        "SELECT count(*) FROM {s}.fact_denial d LEFT JOIN {s}.fact_claim c USING (claim_id) "
        "WHERE c.claim_id IS NULL",
    ),
    (
        "fact_denial.remit_id",
        "SELECT count(*) FROM {s}.fact_denial d LEFT JOIN {s}.fact_remit r USING (remit_id) "
        "WHERE r.remit_id IS NULL",
    ),
    (
        "fact_denial.claim_line_id",
        "SELECT count(*) FROM {s}.fact_denial d LEFT JOIN {s}.fact_claim_line l "
        "USING (claim_line_id) WHERE d.claim_line_id IS NOT NULL AND l.claim_line_id IS NULL",
    ),
    (
        "fact_denial.carc_code",
        "SELECT count(*) FROM {s}.fact_denial d LEFT JOIN {s}.dim_denial_code k USING (carc_code) "
        "WHERE k.carc_code IS NULL",
    ),
)

_EXPECTED_WATERMARKS = [
    (s.watermark_id, s.schema_name, s.loaded_at, s.newest_data_date) for s in SNAPSHOTS
]


def _structural_checks(con: duckdb.DuckDBPyConnection) -> list[Check]:
    checks: list[Check] = []
    rows = con.execute(
        "SELECT watermark_id, schema_name, CAST(loaded_at AS VARCHAR), CAST(newest_data_date AS VARCHAR) "
        "FROM main.watermarks ORDER BY watermark_id"
    ).fetchall()
    checks.append(
        Check(
            "watermarks match design doc 10.3 verbatim",
            [tuple(r) for r in rows] == _EXPECTED_WATERMARKS,
            f"got {rows}",
        )
    )
    for snap in SNAPSHOTS:
        s = snap.schema_name
        for name, sql in _FK_CHECKS:
            checks.append(_expect_zero(con, f"{s}: FK {name}", sql.format(s=s)))
        checks.append(
            _expect_zero(
                con,
                f"{s}: claim billed == sum(visible line billed)",
                f"""
                SELECT count(*) FROM {s}.fact_claim c
                LEFT JOIN (SELECT claim_id, SUM(billed_amount_cents) AS sb
                           FROM {s}.fact_claim_line GROUP BY claim_id) l USING (claim_id)
                WHERE c.billed_amount_cents <> COALESCE(l.sb, 0)
                """,
            )
        )
        checks.append(
            _expect_zero(
                con,
                f"{s}: allowed only when adjudicated",
                f"""
                SELECT count(*) FROM {s}.fact_claim_line l
                WHERE l.allowed_amount_cents IS NOT NULL
                  AND l.claim_id NOT IN (SELECT claim_id FROM {s}.fact_remit)
                """,
            )
        )
        checks.append(
            _expect_zero(
                con,
                f"{s}: transaction amounts strictly positive",
                f"SELECT count(*) FROM {s}.fact_transaction WHERE amount_cents <= 0",
            )
        )
        checks.append(
            _expect_zero(
                con,
                f"{s}: denied amounts strictly positive",
                f"SELECT count(*) FROM {s}.fact_denial WHERE denied_amount_cents <= 0",
            )
        )
        checks.append(
            _expect_zero(
                con,
                f"{s}: no activity after the snapshot cutoff",
                f"""
                SELECT (SELECT count(*) FROM {s}.fact_transaction
                        WHERE post_date > DATE '{snap.newest_data_date}')
                     + (SELECT count(*) FROM {s}.fact_remit
                        WHERE remit_date > DATE '{snap.newest_data_date}')
                     + (SELECT count(*) FROM {s}.fact_denial
                        WHERE denial_date > DATE '{snap.newest_data_date}')
                     + (SELECT count(*) FROM {s}.fact_claim
                        WHERE submission_date > DATE '{snap.newest_data_date}')
                     + (SELECT count(*) FROM {s}.fact_claim_line
                        WHERE charge_entry_date > DATE '{snap.newest_data_date}')
                """,
            )
        )
    # Monotonicity: snap_n activity is a subset of snap_{n+1}.
    id_cols = {
        "fact_claim": "claim_id",
        "fact_claim_line": "claim_line_id",
        "fact_remit": "remit_id",
        "fact_transaction": "txn_id",
        "fact_denial": "denial_id",
    }
    for prev, nxt in itertools.pairwise(SNAPSHOTS):
        for table in FACT_TABLES:
            col = id_cols[table]
            checks.append(
                _expect_zero(
                    con,
                    f"monotonic {prev.schema_name} subset of {nxt.schema_name}: {table}",
                    f"SELECT count(*) FROM (SELECT {col} FROM {prev.schema_name}.{table} "
                    f"EXCEPT SELECT {col} FROM {nxt.schema_name}.{table})",
                )
            )
    return checks


def _scenario_checks(key: dict[str, Any], config: GeneratorConfig) -> list[Check]:
    full = config.scale == "full"
    checks: list[Check] = []
    final = SNAPSHOTS[-1].schema_name

    s1 = key["scenarios"]["1_denial_spike_meridian_imaging"][final]
    ratio = s1["post_over_pre_rate_ratio"]
    min_ratio = 4.0 if full else 1.5
    checks.append(
        Check(
            "scenario 1: CARC 197 post/pre rate ratio",
            ratio is not None and ratio > min_ratio,
            f"ratio={ratio} (min {min_ratio}); pre={s1['pre_break']}, post={s1['post_break']}",
        )
    )
    checks.append(
        Check(
            "scenario 1: post-break CARC 197 denials exist",
            s1["post_break"]["carc197_denied"] >= (10 if full else 1),
            f"post denials={s1['post_break']['carc197_denied']}",
        )
    )

    s2 = key["scenarios"]["2_cob_silverline"][final]
    lo, hi = (0.05, 0.11) if full else (0.02, 0.20)
    checks.append(
        Check(
            "scenario 2: COB mismatch share of Silverline Apr-Jul claims",
            lo <= s2["cob_mismatch_share"] <= hi,
            f"share={s2['cob_mismatch_share']:.4f} claims={s2['cob_mismatch_claims']}",
        )
    )
    checks.append(
        Check(
            "scenario 2: CARC 22 (OA) denials present with dollars",
            s2["carc22_denials"] >= (50 if full else 2) and s2["carc22_denied_cents"] > 0,
            f"denials={s2['carc22_denials']} cents={s2['carc22_denied_cents']}",
        )
    )
    gap = s2["avg_rebill_gap_days"]
    checks.append(
        Check(
            "scenario 2: rebill remits ~30-45 days after first remit",
            gap is not None and 28.0 <= gap <= 47.0,
            f"avg gap={gap} rebilled={s2['claims_with_rebill_remit_visible']}",
        )
    )

    s3 = key["scenarios"]["3_cash_decline"][final]
    if full:
        # The headline number: total posted payer cash down ~12% week over week.
        delta = s3["delta_pct"]
        checks.append(
            Check(
                "scenario 3: weekly payer-cash delta in -10%..-14%",
                delta is not None and -0.14 <= delta <= -0.10,
                f"delta_pct={delta} prior={s3['week_prior']['payer_cash_cents']} "
                f"decline={s3['week_decline']['payer_cash_cents']}",
            )
        )
        top2 = {row["payer_name"] for row in s3["by_payer"][:2]}
        checks.append(
            Check(
                "scenario 3: Atlas Commercial + State Medicaid are the top decliners",
                top2 == {"Atlas Commercial", "State Medicaid"},
                f"top2={sorted(top2)}",
            )
        )
    # At small scale total weekly cash is noise-dominated (a single surgical claim
    # swings whole percents), so detect the two planted mechanisms directly.
    # Both checks also run at full scale.
    subs = {row["week_start"]: row["claims_submitted"] for row in s3["atlas_submissions_by_week"]}
    pre_weeks = [subs.get("2026-06-29", 0), subs.get("2026-07-06", 0)]
    post_weeks = [subs.get("2026-07-13", 0), subs.get("2026-07-20", 0)]
    pre_avg = sum(pre_weeks) / 2
    post_avg = sum(post_weeks) / 2
    sub_ratio = (post_avg / pre_avg) if pre_avg else None
    max_ratio = 0.90 if full else 1.00
    checks.append(
        Check(
            "scenario 3a: Atlas submission volume drops from 2026-07-13",
            sub_ratio is not None and sub_ratio < max_ratio,
            f"weekly submissions pre={pre_weeks} post={post_weeks} ratio={sub_ratio}",
        )
    )
    lags = s3["state_medicaid_observed_post_lag_days"]
    lag_pre = lags["remits_2026_07_06_to_07_23"]
    lag_late = lags["remits_2026_07_24_onward"]
    min_stretch = 2.5 if full else 1.5
    checks.append(
        Check(
            "scenario 3b: State Medicaid remit->post lag stretches in late July",
            lag_pre is not None and lag_late is not None and (lag_late - lag_pre) >= min_stretch,
            f"observed lag pre={lag_pre} late={lag_late}",
        )
    )

    s4 = key["scenarios"]["4_underpayment_northbridge_ortho"][final]
    pre_var = sum(
        m["underpayment_variance_cents"]
        for m in s4["monthly_by_first_remit"]
        if m["remit_month"] < "2026-05"
    )
    post_var = sum(
        m["underpayment_variance_cents"]
        for m in s4["monthly_by_first_remit"]
        if m["remit_month"] >= "2026-05"
    )
    checks.append(
        Check(
            "scenario 4: underpayment variance zero pre / positive post",
            pre_var == 0 and post_var > 0,
            f"pre={pre_var} post={post_var}",
        )
    )
    rr = s4["post_over_pre_ratio"]
    checks.append(
        Check(
            "scenario 4: ortho allowed/billed post-over-pre ratio ~0.92",
            rr is not None and 0.90 <= rr <= 0.94,
            f"post_over_pre={rr}",
        )
    )

    s5 = key["scenarios"]["5_timely_filing_state_medicaid_hmo"][final]
    slack = 20 if full else 6
    checks.append(
        Check(
            "scenario 5: unsubmitted July cluster present at snap_003",
            config.timely_cluster_size
            <= s5["unsubmitted_july_claims"]
            <= config.timely_cluster_size + slack,
            f"count={s5['unsubmitted_july_claims']} expected~{config.timely_cluster_size}",
        )
    )
    checks.append(
        Check(
            "scenario 5: CARC 29 denials already visible",
            s5["carc29_denials"] >= max(config.carc29_count - 2, 1),
            f"count={s5['carc29_denials']} expected~{config.carc29_count}",
        )
    )
    checks.append(
        Check(
            "scenario 5: cluster aging toward but not past the 90-day deadline",
            s5["age_days_at_watermark"]["max"] is not None
            and s5["age_days_at_watermark"]["max"] < 90
            and s5["at_risk_billed_cents"] > 0,
            f"ages={s5['age_days_at_watermark']} billed={s5['at_risk_billed_cents']}",
        )
    )
    return checks


def run_verification(db_path: Path, key: dict[str, Any], config: GeneratorConfig) -> list[Check]:
    """All checks; caller decides what to do with failures."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        checks = _structural_checks(con)
    finally:
        con.close()
    checks.extend(_scenario_checks(key, config))
    return checks
