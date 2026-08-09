"""Self-check assertions run against the written warehouse (--verify).

Structural checks (both scales): watermark rows verbatim, FK integrity, line/claim
billed reconciliation, snapshot monotonicity, amount sanity. Scenario checks:
every planted signal must be detectable, with thresholds appropriate to scale.
A failing check makes the CLI exit nonzero.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from revi_warehouse.anomalies import (
    compute_detection,
    confidence_for,
    is_emitted,
    severity_for,
)
from revi_warehouse.config import (
    ANOMALY_SPECS,
    SELF_RESOLVING_IDS,
    SNAPSHOTS,
    GeneratorConfig,
    day,
)
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


def _open_state_checks(con: duckdb.DuckDBPyConnection, s: str) -> list[Check]:
    """What OPEN actually means, guarded.

    An audit found 593 OPEN claims at snap_003 carrying remit rows, which
    contradicted the catalog's "OPEN means no remittance has been received
    yet". The generator is right and the note was wrong: status is derived
    from *payment posting* and denial visibility (project.py), so a claim
    adjudicated clean sits OPEN for the length of the remit-to-post lag. That
    is a real partial-adjudication state; these four checks pin every half of
    it so neither the data nor the corrected note can drift again.
    """
    open_claim = f"SELECT count(*) FROM {s}.fact_claim c WHERE c.status = 'OPEN'"
    return [
        _expect_zero(
            con,
            f"{s}: no OPEN claim carries a denial record",
            f"{open_claim} AND EXISTS "
            f"(SELECT 1 FROM {s}.fact_denial d WHERE d.claim_id = c.claim_id)",
        ),
        _expect_zero(
            con,
            f"{s}: no OPEN claim carries a posted transaction",
            f"{open_claim} AND EXISTS "
            f"(SELECT 1 FROM {s}.fact_transaction t WHERE t.claim_id = c.claim_id)",
        ),
        _expect_zero(
            con,
            f"{s}: an OPEN claim has a remit iff it is first_pass_paid",
            f"{open_claim} AND c.first_pass_paid <> EXISTS "
            f"(SELECT 1 FROM {s}.fact_remit r WHERE r.claim_id = c.claim_id)",
        ),
        _expect_zero(
            con,
            f"{s}: no OPEN claim reads clean_claim (that flag needs posted cash)",
            f"{open_claim} AND c.clean_claim",
        ),
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
        checks.extend(_open_state_checks(con, s))
        checks.append(
            _expect_zero(
                con,
                f"{s}: v_claim flags restate their source dates",
                f"""
                SELECT count(*) FROM {s}.v_claim
                WHERE billed_flag <> (submission_date IS NOT NULL)
                   OR discharged_flag <> (discharge_date IS NOT NULL)
                """,
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


# ---------------------------------------------------------------------------
# detected-anomaly population checks


def _anomaly_checks(con: duckdb.DuckDBPyConnection, key: dict[str, Any]) -> list[Check]:
    """Re-derive every emitted anomaly per snapshot and compare with the table."""
    checks: list[Check] = []
    expected_final = {s.spec_id for s in ANOMALY_SPECS} - SELF_RESOLVING_IDS
    prev_ids: set[str] | None = None
    for snap in SNAPSHOTS:
        sch = snap.schema_name
        stored = {
            row[0]: (int(row[1]), row[2], float(row[3]), row[4], json.loads(row[5]))
            for row in con.execute(
                f"SELECT anomaly_id, impact_cents, severity, CAST(confidence AS DOUBLE), "
                f"status, CAST(evidence AS VARCHAR) FROM {sch}.detected_anomalies"
            ).fetchall()
        }
        recomputed: dict[str, tuple[int, int]] = {}
        for spec in ANOMALY_SPECS:
            if day(spec.onset) > snap.cutoff_day:
                continue
            n_events, impact, _evidence = compute_detection(con, sch, spec, snap.newest_data_date)
            if is_emitted(spec, n_events, impact):
                recomputed[spec.spec_id] = (n_events, impact)
        checks.append(
            Check(
                f"{sch}: emitted anomaly set matches recomputation",
                set(stored) == set(recomputed),
                f"stored-only={sorted(set(stored) - set(recomputed))} "
                f"recomputed-only={sorted(set(recomputed) - set(stored))}",
            )
        )
        mismatched = [
            aid
            for aid, (n_events, impact) in recomputed.items()
            if aid in stored
            and (
                stored[aid][0] != impact
                or stored[aid][1] != severity_for(impact)
                or stored[aid][2] != confidence_for(n_events)
                or stored[aid][3] != "open"
                or stored[aid][4].get("n_events") != n_events
            )
        ]
        checks.append(
            Check(
                f"{sch}: every anomaly impact/severity/confidence recomputes identically",
                not mismatched,
                f"mismatched={mismatched}",
            )
        )
        ids = set(stored)
        if prev_ids is not None:
            regressed = {a for a in prev_ids - ids if a not in SELF_RESOLVING_IDS}
            checks.append(
                Check(
                    f"{sch}: visibility monotonic except documented self-resolvers",
                    not regressed,
                    f"disappeared={sorted(regressed)}",
                )
            )
        prev_ids = ids
        key_ids = [row["anomaly_id"] for row in key.get("anomalies", {}).get(sch, [])]
        checks.append(
            Check(
                f"{sch}: answer-key anomalies mirror the table",
                sorted(ids) == key_ids,
                f"table={len(ids)} key={len(key_ids)}",
            )
        )
    final = SNAPSHOTS[-1].schema_name
    final_ids = {
        row[0]
        for row in con.execute(f"SELECT anomaly_id FROM {final}.detected_anomalies").fetchall()
    }
    checks.append(
        Check(
            f"{final}: population complete (all specs except self-resolvers)",
            final_ids == expected_final,
            f"missing={sorted(expected_final - final_ids)} "
            f"unexpected={sorted(final_ids - expected_final)}",
        )
    )
    checks.append(
        Check(
            "self-resolving anomalies absent at the final snapshot",
            not (final_ids & SELF_RESOLVING_IDS),
            f"lingering={sorted(final_ids & SELF_RESOLVING_IDS)}",
        )
    )
    return checks


# ---------------------------------------------------------------------------
# non-interference: injected rows must not move any scenario aggregate


_BASE_RELATIONS = (
    "v_claim",
    "v_claim_line",
    "v_transaction",
    "v_denial",
    "fact_claim",
    "fact_claim_line",
    "fact_remit",
    "fact_transaction",
    "fact_denial",
)


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is None and b is None
        return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_values_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_values_equal(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def _injection_guards(injected: str) -> tuple[tuple[str, str], ...]:
    """Direct guards that no anomaly-injected row can reach a scenario aggregate.

    `injected` is the SQL predicate selecting the injected claim-id range.
    """
    return (
        (
            "no injected claim on Silverline Medicare Advantage",
            f"SELECT count(*) FROM wh.snap_003.v_claim WHERE {injected} "
            "AND payer_name = 'Silverline Medicare Advantage'",
        ),
        (
            "no injected claim in Meridian Health x Imaging",
            f"SELECT count(*) FROM wh.snap_003.v_claim WHERE {injected} "
            "AND payer_name = 'Meridian Health' AND service_line_name = 'Imaging'",
        ),
        (
            "no injected claim on State Medicaid HMO x Eastside",
            f"SELECT count(*) FROM wh.snap_003.v_claim WHERE {injected} "
            "AND plan_name = 'State Medicaid HMO' "
            "AND facility_name = 'Eastside Medical Center'",
        ),
        (
            "no injected Atlas submission in the scenario-3a window",
            f"SELECT count(*) FROM wh.snap_003.v_claim WHERE {injected} "
            "AND payer_name = 'Atlas Commercial' "
            "AND submission_date BETWEEN DATE '2026-06-15' AND DATE '2026-08-02'",
        ),
        (
            "no injected Northbridge ORTHO-SURG line",
            f"SELECT count(*) FROM wh.snap_003.v_claim_line WHERE {injected} "
            "AND payer_name = 'Northbridge Commercial' AND proc_group = 'ORTHO-SURG'",
        ),
        (
            "no injected payer/patient cash inside the reference compare weeks",
            f"SELECT count(*) FROM wh.snap_003.v_transaction WHERE {injected} "
            "AND txn_type IN ('PAYMENT', 'PATIENT_PAYMENT') "
            "AND post_date BETWEEN DATE '2026-07-20' AND DATE '2026-08-02'",
        ),
        (
            "no injected State Medicaid payment with remit on/after 2026-07-06",
            f"SELECT count(*) FROM wh.snap_003.v_transaction WHERE {injected} "
            "AND txn_type = 'PAYMENT' AND payer_name = 'State Medicaid' "
            "AND remit_date >= DATE '2026-07-06'",
        ),
    )


def _backfill_guards(first_id: str, era_start: str, resolved_by: str) -> tuple[tuple[str, str], ...]:
    """The 2024 backfill's closure proof, per snapshot schema.

    Every scenario window, every anomaly observation window and every trailing
    window anchored at a watermark lives in 2025-2026, so "no backfill row
    carries a date on or after `resolved_by` + 1" covers all of them at once.
    The rest are the watermark-time point-metric invariants: nothing open,
    nothing unbilled, nothing over-collected.
    """
    backfill = f"claim_id >= '{first_id}'"
    guards: list[tuple[str, str]] = []
    for snap in SNAPSHOTS:
        s = snap.schema_name
        guards.extend(
            [
                (
                    f"{s}: every backfill claim has a 2024 service date",
                    f"SELECT count(*) FROM wh.{s}.fact_claim WHERE {backfill} "
                    f"AND (service_date < DATE '2024-01-01' OR service_date >= DATE '{era_start}')",
                ),
                (
                    f"{s}: every backfill claim is PAID or CLOSED (never in A/R)",
                    f"SELECT count(*) FROM wh.{s}.fact_claim WHERE {backfill} "
                    "AND status NOT IN ('PAID', 'CLOSED')",
                ),
                (
                    f"{s}: every backfill claim is billed (no DNFB / timely-filing risk)",
                    f"SELECT count(*) FROM wh.{s}.fact_claim WHERE {backfill} "
                    "AND submission_date IS NULL",
                ),
                (
                    f"{s}: every backfill claim is resolved by {resolved_by}",
                    f"SELECT count(*) FROM wh.{s}.fact_claim WHERE {backfill} "
                    f"AND (resolved_date IS NULL OR resolved_date > DATE '{resolved_by}')",
                ),
                (
                    f"{s}: no backfill activity after {resolved_by}",
                    f"SELECT (SELECT count(*) FROM wh.{s}.fact_transaction WHERE {backfill} "
                    f"          AND post_date > DATE '{resolved_by}')"
                    f"     + (SELECT count(*) FROM wh.{s}.fact_remit WHERE {backfill} "
                    f"          AND remit_date > DATE '{resolved_by}')"
                    f"     + (SELECT count(*) FROM wh.{s}.fact_denial WHERE {backfill} "
                    f"          AND (denial_date > DATE '{resolved_by}' "
                    f"               OR appeal_decision_date > DATE '{resolved_by}'))"
                    f"     + (SELECT count(*) FROM wh.{s}.fact_claim_line WHERE {backfill} "
                    f"          AND charge_entry_date > DATE '{resolved_by}')",
                ),
                (
                    f"{s}: every backfill denial carries a terminal appeal status",
                    f"SELECT count(*) FROM wh.{s}.fact_denial WHERE {backfill} "
                    "AND appeal_status = 'APPEALED'",
                ),
                (
                    f"{s}: no backfill claim carries an unrefunded credit balance",
                    f"""
                    SELECT count(*) FROM (
                        SELECT c.claim_id
                        FROM wh.{s}.fact_claim c
                        JOIN wh.{s}.fact_transaction t USING (claim_id)
                        WHERE c.claim_id >= '{first_id}'
                        GROUP BY c.claim_id, c.expected_amount_cents
                        HAVING COALESCE(SUM(t.amount_cents) FILTER (
                                   WHERE t.txn_type IN ('PAYMENT', 'PATIENT_PAYMENT')), 0)
                             - COALESCE(SUM(t.amount_cents) FILTER (
                                   WHERE t.txn_type = 'REFUND'), 0)
                             > c.expected_amount_cents
                    )
                    """,
                ),
            ]
        )
    return tuple(guards)


def _non_interference_checks(db_path: Path, key: dict[str, Any]) -> list[Check]:
    """Recompute all five scenarios over base claims only (every appended row
    excluded) and require exact agreement with the recorded all-rows values;
    plus direct guards on the anomaly and backfill blocks."""
    from revi_warehouse.answer_key import (
        _cash_decline,
        _cob,
        _denial_spike,
        _timely_filing,
        _underpayment,
    )

    checks: list[Check] = []
    threshold = key["anomalies_meta"]["first_injected_claim_id"]
    meta = key.get("backfill_meta", {})
    first_backfill = meta.get("first_backfill_claim_id")
    injected = f"claim_id >= '{threshold}'"
    if first_backfill is not None:
        injected += f" AND claim_id < '{first_backfill}'"
    con = duckdb.connect()  # in-memory primary; warehouse attached read-only
    try:
        con.execute(f"ATTACH '{db_path}' AS wh (READ_ONLY)")
        guards = _injection_guards(injected)
        if first_backfill is not None:
            guards += _backfill_guards(
                first_backfill, meta["organic_era_start"], meta["resolved_by"]
            )
        for name, sql in guards:
            n = con.execute(sql).fetchone()
            assert n is not None
            checks.append(Check(f"non-interference guard: {name}", int(n[0]) == 0, f"{n[0]} rows"))

        for snap in SNAPSHOTS:
            sch = snap.schema_name
            base = f"base_{sch}"
            con.execute(f"CREATE SCHEMA {base}")
            for rel in _BASE_RELATIONS:
                con.execute(
                    f"CREATE VIEW {base}.{rel} AS "
                    f"SELECT * FROM wh.{sch}.{rel} WHERE claim_id < '{threshold}'"
                )
            base_values: dict[str, Any] = {
                "1_denial_spike_meridian_imaging": _denial_spike(con, base),
                "2_cob_silverline": _cob(con, base),
                "3_cash_decline": _cash_decline(con, base),
                "4_underpayment_northbridge_ortho": _underpayment(con, base),
                "5_timely_filing_state_medicaid_hmo": _timely_filing(
                    con, base, snap.newest_data_date
                ),
            }
            for scenario, recomputed in base_values.items():
                recorded = key["scenarios"][scenario][sch]
                checks.append(
                    Check(
                        f"{sch}: scenario {scenario.split('_')[0]} unchanged by injection "
                        "(base-only recomputation identical)",
                        _values_equal(recorded, recomputed),
                        "base-only aggregates diverge from recorded scenario values",
                    )
                )
    finally:
        con.close()
    return checks


def run_verification(db_path: Path, key: dict[str, Any], config: GeneratorConfig) -> list[Check]:
    """All checks; caller decides what to do with failures."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        checks = _structural_checks(con)
        checks.extend(_anomaly_checks(con, key))
    finally:
        con.close()
    checks.extend(_scenario_checks(key, config))
    checks.extend(_non_interference_checks(db_path, key))
    return checks
