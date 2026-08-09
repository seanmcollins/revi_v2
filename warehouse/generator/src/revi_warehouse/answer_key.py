"""Compute the answer key from the generated DuckDB file — never hand-entered.

For every planted scenario, at every watermark, this module runs SQL against the
written snapshots and records the exact aggregates a correct platform should
surface. The same SQL is documented in warehouse/ANSWER_KEY.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from revi_warehouse.config import (
    ANOMALY_SPECS,
    BACKFILL,
    ORGANIC_ERA_START,
    REVI_SEED,
    SCENARIOS,
    SELF_RESOLVING_IDS,
    SNAPSHOTS,
    GeneratorConfig,
)

_S = SCENARIOS


def _iso(day_int: int) -> str:
    return str(np.datetime64(day_int, "D"))


ERA_START = _iso(ORGANIC_ERA_START)
"""Every scenario cohort is scoped to claims serviced in the organic era.

Three of the five scenarios compare a window against "everything before the
break". The 2024 comparison backfill (backfill.py) is not part of that history —
it is a closed prior year planted for period-over-period questions — so the
open-ended cohorts are bounded below by the claim's service date. (Service date,
not remit date: a December-2024 claim adjudicates in January 2025.) Every
organic and injected claim is serviced on or after this day, so the bound leaves
each published figure exactly where it was.
"""


WEEK_PRIOR = (_iso(_S.s3_week_prior_start), _iso(_S.s3_week_prior_end))
WEEK_DECLINE = (_iso(_S.s3_week_decline_start), _iso(_S.s3_week_decline_end))
S1_BREAK = _iso(_S.s1_break_day)
S2_WINDOW = (_iso(_S.s2_service_start), _iso(_S.s2_service_end))
S4_START = _iso(_S.s4_start_day)
S5_JULY = (_iso(_S.s5_july_start), _iso(_S.s5_july_end))


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row


def _denial_spike(con: duckdb.DuckDBPyConnection, sch: str) -> dict[str, Any]:
    """Scenario 1: Meridian Health x Imaging CARC 197 (CO) rate by first-remit month."""
    monthly = con.execute(
        f"""
        WITH cell AS (
            SELECT claim_id FROM {sch}.v_claim
            WHERE payer_name = '{_S.s1_payer}' AND service_line_name = '{_S.s1_service_line}'
              AND service_date >= DATE '{ERA_START}'
        ),
        first_remit AS (
            SELECT claim_id, MIN(remit_date) AS fr FROM {sch}.fact_remit GROUP BY claim_id
        ),
        d197 AS (
            SELECT DISTINCT claim_id FROM {sch}.fact_denial
            WHERE carc_code = 197 AND group_code = 'CO'
        )
        SELECT strftime(fr.fr, '%Y-%m') AS remit_month,
               count(*) AS adjudicated_claims,
               count(d197.claim_id) AS carc197_denied_claims
        FROM cell
        JOIN first_remit fr USING (claim_id)
        LEFT JOIN d197 USING (claim_id)
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    pre_n, pre_d, post_n, post_d = _one(
        con,
        f"""
        WITH cell AS (
            SELECT claim_id FROM {sch}.v_claim
            WHERE payer_name = '{_S.s1_payer}' AND service_line_name = '{_S.s1_service_line}'
              AND service_date >= DATE '{ERA_START}'
        ),
        first_remit AS (
            SELECT claim_id, MIN(remit_date) AS fr FROM {sch}.fact_remit GROUP BY claim_id
        ),
        d197 AS (
            SELECT DISTINCT claim_id FROM {sch}.fact_denial
            WHERE carc_code = 197 AND group_code = 'CO'
        )
        SELECT count(*) FILTER (WHERE fr.fr < DATE '{S1_BREAK}'),
               count(d197.claim_id) FILTER (WHERE fr.fr < DATE '{S1_BREAK}'),
               count(*) FILTER (WHERE fr.fr >= DATE '{S1_BREAK}'),
               count(d197.claim_id) FILTER (WHERE fr.fr >= DATE '{S1_BREAK}')
        FROM cell JOIN first_remit fr USING (claim_id) LEFT JOIN d197 USING (claim_id)
        """,
    )
    pre_rate = pre_d / pre_n if pre_n else 0.0
    post_rate = post_d / post_n if post_n else 0.0
    return {
        "monthly_by_first_remit": [
            {
                "remit_month": m,
                "adjudicated_claims": n,
                "carc197_denied_claims": k,
                "rate": (k / n if n else 0.0),
            }
            for m, n, k in monthly
        ],
        "pre_break": {"claims": pre_n, "carc197_denied": pre_d, "rate": pre_rate},
        "post_break": {"claims": post_n, "carc197_denied": post_d, "rate": post_rate},
        "post_over_pre_rate_ratio": (post_rate / pre_rate) if pre_rate else None,
        "break_date": S1_BREAK,
    }


def _cob(con: duckdb.DuckDBPyConnection, sch: str) -> dict[str, Any]:
    """Scenario 2: Silverline MA COB mismatch cohort + CARC 22 (OA) denials + rebill lag."""
    total, flagged = _one(
        con,
        f"""
        SELECT count(*),
               count(*) FILTER (
                   WHERE cob_mismatch_flag AND other_insurance_flag AND payer_sequence = 'P')
        FROM {sch}.v_claim
        WHERE payer_name = '{_S.s2_payer}'
          AND service_date BETWEEN DATE '{S2_WINDOW[0]}' AND DATE '{S2_WINDOW[1]}'
        """,
    )
    carc22_count, carc22_cents = _one(
        con,
        f"""
        SELECT count(*), COALESCE(SUM(denied_amount_cents), 0)
        FROM {sch}.v_denial
        WHERE payer_name = '{_S.s2_payer}' AND carc_code = 22 AND group_code = 'OA'
          AND service_date BETWEEN DATE '{S2_WINDOW[0]}' AND DATE '{S2_WINDOW[1]}'
        """,
    )
    rebilled, avg_gap = _one(
        con,
        f"""
        WITH cohort AS (
            SELECT claim_id FROM {sch}.v_claim
            WHERE payer_name = '{_S.s2_payer}' AND cob_mismatch_flag
              AND service_date BETWEEN DATE '{S2_WINDOW[0]}' AND DATE '{S2_WINDOW[1]}'
        ),
        gaps AS (
            SELECT r.claim_id,
                   MAX(r.remit_date) - MIN(r.remit_date) AS gap_days,
                   count(*) AS n_remits
            FROM {sch}.fact_remit r JOIN cohort USING (claim_id)
            GROUP BY r.claim_id
        )
        SELECT count(*) FILTER (WHERE n_remits >= 2),
               AVG(gap_days) FILTER (WHERE n_remits >= 2)
        FROM gaps
        """,
    )
    return {
        "silverline_claims_in_window": total,
        "cob_mismatch_claims": flagged,
        "cob_mismatch_share": (flagged / total) if total else 0.0,
        "carc22_denials": carc22_count,
        "carc22_denied_cents": carc22_cents,
        "claims_with_rebill_remit_visible": rebilled,
        "avg_rebill_gap_days": float(avg_gap) if avg_gap is not None else None,
        "service_window": {"start": S2_WINDOW[0], "end": S2_WINDOW[1]},
    }


def _cash_decline(con: duckdb.DuckDBPyConnection, sch: str) -> dict[str, Any]:
    """Scenario 3: posted payer cash, week 2026-07-27..08-02 vs 2026-07-20..26."""

    def week_total(start: str, end: str, txn_type: str) -> int:
        (v,) = _one(
            con,
            f"""
            SELECT COALESCE(SUM(amount_cents), 0) FROM {sch}.fact_transaction
            WHERE txn_type = '{txn_type}' AND post_date BETWEEN DATE '{start}' AND DATE '{end}'
            """,
        )
        return int(v)

    prior = week_total(*WEEK_PRIOR, "PAYMENT")
    decline = week_total(*WEEK_DECLINE, "PAYMENT")
    prior_pat = week_total(*WEEK_PRIOR, "PATIENT_PAYMENT")
    decline_pat = week_total(*WEEK_DECLINE, "PATIENT_PAYMENT")
    by_payer = con.execute(
        f"""
        SELECT payer_name,
               COALESCE(SUM(amount_cents) FILTER (WHERE post_date
                   BETWEEN DATE '{WEEK_PRIOR[0]}' AND DATE '{WEEK_PRIOR[1]}'), 0) AS w_prior,
               COALESCE(SUM(amount_cents) FILTER (WHERE post_date
                   BETWEEN DATE '{WEEK_DECLINE[0]}' AND DATE '{WEEK_DECLINE[1]}'), 0) AS w_decline
        FROM {sch}.v_transaction
        WHERE txn_type = 'PAYMENT'
        GROUP BY payer_name ORDER BY (w_decline - w_prior) ASC, payer_name
        """
    ).fetchall()
    atlas_subs = con.execute(
        f"""
        SELECT strftime(DATE_TRUNC('week', submission_date), '%Y-%m-%d') AS week_start, count(*)
        FROM {sch}.v_claim
        WHERE payer_name = '{_S.s3a_payer}'
          AND submission_date BETWEEN DATE '2026-06-15' AND DATE '{WEEK_DECLINE[1]}'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    lag_pre, lag_late = _one(
        con,
        f"""
        SELECT AVG(post_date - remit_date) FILTER (
                   WHERE remit_date BETWEEN DATE '2026-07-06' AND DATE '2026-07-23'),
               AVG(post_date - remit_date) FILTER (
                   WHERE remit_date BETWEEN DATE '2026-07-24' AND DATE '{WEEK_DECLINE[1]}')
        FROM {sch}.v_transaction
        WHERE txn_type = 'PAYMENT' AND payer_name = '{_S.s3b_payer}' AND remit_date IS NOT NULL
        """,
    )
    return {
        "week_prior": {"start": WEEK_PRIOR[0], "end": WEEK_PRIOR[1], "payer_cash_cents": prior},
        "week_decline": {"start": WEEK_DECLINE[0], "end": WEEK_DECLINE[1], "payer_cash_cents": decline},
        "delta_cents": decline - prior,
        "delta_pct": ((decline - prior) / prior) if prior else None,
        "patient_cash_cents": {"week_prior": prior_pat, "week_decline": decline_pat},
        "total_cash_cents": {
            "week_prior": prior + prior_pat,
            "week_decline": decline + decline_pat,
        },
        "by_payer": [
            {
                "payer_name": name,
                "week_prior_cents": int(a),
                "week_decline_cents": int(b),
                "delta_cents": int(b) - int(a),
                "delta_pct": ((int(b) - int(a)) / int(a)) if a else None,
            }
            for name, a, b in by_payer
        ],
        "atlas_submissions_by_week": [{"week_start": ws, "claims_submitted": c} for ws, c in atlas_subs],
        "state_medicaid_observed_post_lag_days": {
            "remits_2026_07_06_to_07_23": float(lag_pre) if lag_pre is not None else None,
            "remits_2026_07_24_onward": float(lag_late) if lag_late is not None else None,
        },
    }


def _underpayment(con: duckdb.DuckDBPyConnection, sch: str) -> dict[str, Any]:
    """Scenario 4: Northbridge ORTHO-SURG allowed at ~92% of expected from 2026-05-01 remits."""
    monthly = con.execute(
        f"""
        WITH first_remit AS (
            SELECT claim_id, MIN(remit_date) AS fr FROM {sch}.fact_remit GROUP BY claim_id
        ),
        ortho_claims AS (
            SELECT DISTINCT l.claim_id FROM {sch}.v_claim_line l
            WHERE l.payer_name = '{_S.s4_payer}' AND l.proc_group = '{_S.s4_proc_group}'
              AND l.claim_service_date >= DATE '{ERA_START}'
        ),
        adjudicated AS (
            SELECT c.claim_id, fr.fr, c.expected_amount_cents,
                   (SELECT SUM(allowed_amount_cents) FROM {sch}.fact_claim_line ll
                    WHERE ll.claim_id = c.claim_id) AS allowed_cents
            FROM {sch}.fact_claim c
            JOIN ortho_claims USING (claim_id)
            JOIN first_remit fr USING (claim_id)
        )
        SELECT strftime(fr, '%Y-%m') AS remit_month,
               count(*) AS claims,
               SUM(GREATEST(expected_amount_cents - allowed_cents, 0)) AS underpayment_variance_cents
        FROM adjudicated
        WHERE allowed_cents IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    pre_billed, pre_allowed, post_billed, post_allowed = _one(
        con,
        f"""
        WITH first_remit AS (
            SELECT claim_id, MIN(remit_date) AS fr FROM {sch}.fact_remit GROUP BY claim_id
        )
        SELECT COALESCE(SUM(l.billed_amount_cents) FILTER (WHERE fr.fr < DATE '{S4_START}'), 0),
               COALESCE(SUM(l.allowed_amount_cents) FILTER (WHERE fr.fr < DATE '{S4_START}'), 0),
               COALESCE(SUM(l.billed_amount_cents) FILTER (WHERE fr.fr >= DATE '{S4_START}'), 0),
               COALESCE(SUM(l.allowed_amount_cents) FILTER (WHERE fr.fr >= DATE '{S4_START}'), 0)
        FROM {sch}.v_claim_line l
        JOIN first_remit fr USING (claim_id)
        WHERE l.payer_name = '{_S.s4_payer}' AND l.proc_group = '{_S.s4_proc_group}'
          AND l.claim_service_date >= DATE '{ERA_START}'
          AND l.allowed_amount_cents IS NOT NULL
        """,
    )
    pre_ratio = (pre_allowed / pre_billed) if pre_billed else None
    post_ratio = (post_allowed / post_billed) if post_billed else None
    return {
        "monthly_by_first_remit": [
            {"remit_month": m, "claims": n, "underpayment_variance_cents": int(v or 0)} for m, n, v in monthly
        ],
        "ortho_line_allowed_to_billed_ratio": {"pre": pre_ratio, "post": post_ratio},
        "post_over_pre_ratio": (post_ratio / pre_ratio) if (pre_ratio and post_ratio) else None,
        "start_date": S4_START,
    }


def _timely_filing(con: duckdb.DuckDBPyConnection, sch: str, newest_data_date: str) -> dict[str, Any]:
    """Scenario 5: State Medicaid HMO x Eastside — unsubmitted July claims aging to 90 days."""
    count, billed, min_age, med_age, max_age = _one(
        con,
        f"""
        SELECT count(*),
               COALESCE(SUM(billed_amount_cents), 0),
               MIN(DATE '{newest_data_date}' - service_date),
               MEDIAN(DATE '{newest_data_date}' - service_date),
               MAX(DATE '{newest_data_date}' - service_date)
        FROM {sch}.v_claim
        WHERE plan_name = '{_S.s5_plan}' AND facility_name = '{_S.s5_facility}'
          AND service_date BETWEEN DATE '{S5_JULY[0]}' AND DATE '{S5_JULY[1]}'
          AND submission_date IS NULL
        """,
    )
    carc29_count, carc29_cents = _one(
        con,
        f"""
        SELECT count(*), COALESCE(SUM(denied_amount_cents), 0)
        FROM {sch}.v_denial
        WHERE plan_name = '{_S.s5_plan}' AND facility_name = '{_S.s5_facility}' AND carc_code = 29
          AND service_date >= DATE '{ERA_START}'
        """,
    )
    return {
        "unsubmitted_july_claims": count,
        "at_risk_billed_cents": billed,
        "age_days_at_watermark": {
            "min": int(min_age) if min_age is not None else None,
            "median": float(med_age) if med_age is not None else None,
            "max": int(max_age) if max_age is not None else None,
        },
        "timely_filing_limit_days": 90,
        "days_remaining_min": (90 - int(max_age)) if max_age is not None else None,
        "days_remaining_max": (90 - int(min_age)) if min_age is not None else None,
        "carc29_denials": carc29_count,
        "carc29_denied_cents": carc29_cents,
    }


_ANOMALY_COLUMNS = (
    "anomaly_id, CAST(detected_at AS VARCHAR), category, title, description, metric_id, "
    "CAST(dimensions AS VARCHAR), CAST(window_start AS VARCHAR), CAST(window_end AS VARCHAR), "
    "impact_cents, severity, CAST(confidence AS VARCHAR), status, CAST(evidence AS VARCHAR)"
)


def _anomaly_rows(con: duckdb.DuckDBPyConnection, sch: str) -> list[dict[str, Any]]:
    """detected_anomalies rows exactly as persisted (JSON fields parsed)."""
    rows = con.execute(
        f"SELECT {_ANOMALY_COLUMNS} FROM {sch}.detected_anomalies ORDER BY anomaly_id"
    ).fetchall()
    return [
        {
            "anomaly_id": r[0],
            "detected_at": r[1],
            "category": r[2],
            "title": r[3],
            "description": r[4],
            "metric_id": r[5],
            "dimensions": json.loads(r[6]),
            "window_start": r[7],
            "window_end": r[8],
            "impact_cents": int(r[9]),
            "severity": r[10],
            "confidence": float(r[11]),
            "status": r[12],
            "evidence": json.loads(r[13]),
        }
        for r in rows
    ]


def _backfill_meta(con: duckdb.DuckDBPyConnection, config: GeneratorConfig) -> dict[str, Any]:
    """The 2024 backfill's footprint, read back out of the newest snapshot."""
    final = SNAPSHOTS[-1].schema_name
    first_id, claims, min_svc, max_svc, max_resolved = _one(
        con,
        f"""
        SELECT MIN(claim_id), count(*), CAST(MIN(service_date) AS VARCHAR),
               CAST(MAX(service_date) AS VARCHAR), CAST(MAX(resolved_date) AS VARCHAR)
        FROM {final}.fact_claim WHERE service_date < DATE '{ERA_START}'
        """,
    )
    return {
        "enabled": config.include_backfill,
        "organic_era_start": ERA_START,
        "first_backfill_claim_id": first_id,
        "claims": int(claims),
        "service_window": {"start": min_svc, "end": max_svc},
        "resolved_by": _iso(BACKFILL.resolved_by),
        "observed_last_resolved_date": max_resolved,
        "volume_ratio_target": BACKFILL.volume_ratio,
        "denial_factor": BACKFILL.denial_factor,
    }


def compute_answer_key(db_path: Path, config: GeneratorConfig) -> dict[str, Any]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        key: dict[str, Any] = {
            "seed": REVI_SEED,
            "config": {
                "scale": config.scale,
                "n_claims": config.n_claims,
                "n_patients": config.n_patients,
                "timely_cluster_size": config.timely_cluster_size,
                "carc29_count": config.carc29_count,
                "include_backfill": config.include_backfill,
            },
            "watermarks": [
                {
                    "watermark_id": s.watermark_id,
                    "schema_name": s.schema_name,
                    "loaded_at": s.loaded_at,
                    "newest_data_date": s.newest_data_date,
                }
                for s in SNAPSHOTS
            ],
            "row_counts": {},
            "scenarios": {
                "1_denial_spike_meridian_imaging": {},
                "2_cob_silverline": {},
                "3_cash_decline": {},
                "4_underpayment_northbridge_ortho": {},
                "5_timely_filing_state_medicaid_hmo": {},
            },
            "anomalies": {},
            "anomalies_meta": {
                "base_n_claims": config.n_claims,
                "first_injected_claim_id": f"CLM-{config.n_claims + 1:07d}",
                "spec_count": len(ANOMALY_SPECS),
                "self_resolving_ids": sorted(SELF_RESOLVING_IDS),
                "per_snapshot_counts": {},
            },
            "backfill_meta": _backfill_meta(con, config),
        }
        for snap in SNAPSHOTS:
            sch = snap.schema_name
            tables = con.execute(
                f"SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = '{sch}' AND table_type = 'BASE TABLE' ORDER BY table_name"
            ).fetchall()
            key["row_counts"][sch] = {
                t: _one(con, f"SELECT count(*) FROM {sch}.{t}")[0] for (t,) in tables
            }
            key["scenarios"]["1_denial_spike_meridian_imaging"][sch] = _denial_spike(con, sch)
            key["scenarios"]["2_cob_silverline"][sch] = _cob(con, sch)
            key["scenarios"]["3_cash_decline"][sch] = _cash_decline(con, sch)
            key["scenarios"]["4_underpayment_northbridge_ortho"][sch] = _underpayment(con, sch)
            key["scenarios"]["5_timely_filing_state_medicaid_hmo"][sch] = _timely_filing(
                con, sch, snap.newest_data_date
            )
            key["anomalies"][sch] = _anomaly_rows(con, sch)
            key["anomalies_meta"]["per_snapshot_counts"][sch] = len(key["anomalies"][sch])
        return key
    finally:
        con.close()


def write_answer_key(key: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(key, indent=2, sort_keys=True) + "\n")
