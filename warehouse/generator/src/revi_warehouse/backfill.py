"""Additive 2024 backfill: a closed prior year to compare 2025 against.

The organic world (world.py) plus the anomaly population (anomalies.py) cover
2025-01-01..2026-08-02. This stage appends a full calendar year of 2024 claims —
with their lines, remits, transactions, denials and appeals — after every other
stage has run, so period-over-period questions have real rows to read.

Two properties make the backfill free of side effects:

1. **Its own RNG streams.** Each sub-stream is seeded by
   ``(REVI_SEED, BACKFILL_STREAM, crc32(sub_stream))``, the pattern the anomaly
   engine uses. No draw belonging to dims/world/anomalies is consumed or
   shifted, and rows are appended after the existing ones, so every pre-existing
   row stays byte-identical.
2. **The year is closed by 2025-06-30.** Every backfill claim is submitted,
   adjudicated, and then paid or written off; every appeal is decided; every
   duplicate payment is refunded — all before ``BACKFILL.resolved_by``, more
   than a year before the earliest watermark. No backfill row can therefore
   reach a watermark-time point metric (A/R, DNFB, credit balances,
   timely-filing risk) or a trailing window anchored at the 2026-08 watermarks.

Shape: volume is ~86% of the 2025 claims-per-day rate with mild monthly
seasonality; cash follows volume, since contract rates and payment mechanics are
the organic world's unchanged; denial rate is each payer's 2025 propensity
scaled by ``BACKFILL.denial_factor``.

Lifecycle lag distributions are the organic world's, with clips tightened far
enough into the tail (>2.4 sigma) that mean cycle times are unaffected, so a
2024-vs-2025 days-to-pay comparison reflects the distributions rather than the
closing deadline.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from revi_warehouse.config import (
    BACKFILL,
    BACKFILL_STREAM,
    NEVER,
    ORGANIC_ERA_START,
    REVI_SEED,
    SCENARIOS,
    GeneratorConfig,
    day,
)
from revi_warehouse.dims import (
    CARC_GROUP,
    FACILITIES,
    GENERIC_CARC_MIX,
    PAYERS,
    PROC_GROUPS,
    SERVICE_LINES,
    Dims,
)
from revi_warehouse.world import (
    CLAIM_ARRAY_FIELDS,
    LINE_ARRAY_FIELDS,
    PROC_GROUP_INDEX,
    World,
)

_GROUP_NAMES = ("CO", "PR", "OA", "PI")


def _rint64(x: np.ndarray) -> np.ndarray:
    return np.rint(x).astype(np.int64)


def _stream(sub_stream: str) -> np.random.Generator:
    """One independent PCG64 per sub-stream, seeded like the anomaly engine's."""
    seq = np.random.SeedSequence((REVI_SEED, BACKFILL_STREAM, zlib.crc32(sub_stream.encode("ascii"))))
    return np.random.Generator(np.random.PCG64(seq))


def backfill_claim_count(world: World) -> int:
    """How many 2024 claims to plant: `volume_ratio` of the observed 2025 daily rate.

    Read off the built world rather than a constant so the ratio holds at every
    scale.
    """
    base = world.svc_day[: world.n_base_claims]
    n_2025 = int(np.count_nonzero((base >= ORGANIC_ERA_START) & (base <= day("2025-12-31"))))
    days_2024 = BACKFILL.service_end - BACKFILL.service_start + 1
    return round(n_2025 * BACKFILL.volume_ratio * days_2024 / 365)


# ---------------------------------------------------------------------------
# cohort construction


@dataclass
class _Rows:
    """Everything the backfill appends, in World's own array vocabulary.

    Claim/line/remit indices are LOCAL; `append_backfill` rebases them onto the
    existing arrays.
    """

    claims: dict[str, np.ndarray] = field(default_factory=dict)
    lines: dict[str, np.ndarray] = field(default_factory=dict)
    line_start: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    remit_claim: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    remit_seq: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    remit_day: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    remit_pos1: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    remit_pos2: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    txn: dict[str, np.ndarray] = field(default_factory=dict)
    dn: dict[str, np.ndarray] = field(default_factory=dict)


def _service_days(rng: np.random.Generator, n: int) -> np.ndarray:
    """2024 service dates: the organic weekday shape times monthly seasonality."""
    days = np.arange(BACKFILL.service_start, BACKFILL.service_end + 1)
    weekday = (days + 3) % 7  # 0=Mon .. 6=Sun, same convention as world.py
    weight = np.where(weekday <= 4, 1.0, np.where(weekday == 5, 0.35, 0.20))
    month = days.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64) % 12
    weight = weight * np.array(BACKFILL.seasonality, dtype=np.float64)[month]
    return np.sort(rng.choice(days, size=n, p=weight / weight.sum())).astype(np.int64)


def _cohort(config: GeneratorConfig, dims: Dims, rng: np.random.Generator, n: int) -> dict[str, Any]:
    """Service dates, dimension assignment and stay length for the 2024 cohort."""
    svc = _service_days(rng, n)
    payer_p = np.array([p.weight for p in PAYERS]) * np.array(BACKFILL.payer_mix_tilt)
    payer_i = rng.choice(len(PAYERS), size=n, p=payer_p / payer_p.sum()).astype(np.int64)
    plan_i = np.zeros(n, dtype=np.int64)
    for pi in range(len(PAYERS)):
        mask = payer_i == pi
        idx, weights = dims.plans_by_payer[pi]
        plan_i[mask] = rng.choice(idx, size=int(mask.sum()), p=weights)
    line_p = np.array([sl[1] for sl in SERVICE_LINES])
    svcline_i = rng.choice(len(SERVICE_LINES), size=n, p=line_p / line_p.sum()).astype(np.int64)
    fac_p = np.array([f[2] for f in FACILITIES])
    facility_i = rng.choice(len(FACILITIES), size=n, p=fac_p / fac_p.sum()).astype(np.int64)
    provider_i = np.zeros(n, dtype=np.int64)
    for li in range(len(SERVICE_LINES)):
        mask = svcline_i == li
        provider_i[mask] = rng.choice(dims.providers_by_service_line[li], size=int(mask.sum()))
    patient_i = rng.integers(0, config.n_patients, size=n).astype(np.int64)
    inst_p = np.array([sl[3] for sl in SERVICE_LINES])[svcline_i]
    is_institutional = rng.random(n) < inst_p
    los = rng.integers(1, 8, size=n).astype(np.int64)
    pseq = rng.choice(3, size=n, p=np.array([0.86, 0.11, 0.03])).astype(np.int64)
    oins = (pseq > 0) | (rng.random(n) < 0.12)
    cobm = oins & (pseq == 0) & (rng.random(n) < 0.01)
    return {
        "svc_day": svc,
        "payer_i": payer_i,
        "plan_i": plan_i,
        "svcline_i": svcline_i,
        "facility_i": facility_i,
        "provider_i": provider_i,
        "patient_i": patient_i,
        "is_institutional": is_institutional,
        "los": los,
        "pseq": pseq,
        "oins": oins,
        "cobm": cobm,
        "discharge_day": np.where(is_institutional, svc + los, NEVER).astype(np.int64),
    }


def _lines(
    config: GeneratorConfig, rng: np.random.Generator, c: dict[str, Any], n: int
) -> dict[str, np.ndarray]:
    """Claim lines with the organic world's mix, charge lag and billed distributions."""
    n_extra = np.minimum(rng.poisson(config.extra_lines_lambda, size=n), 5)
    per_claim = (1 + n_extra).astype(np.int64)
    m = int(per_claim.sum())
    starts = np.concatenate(([0], np.cumsum(per_claim)[:-1])).astype(np.int64)
    line_claim = np.repeat(np.arange(n), per_claim).astype(np.int64)
    line_num = (np.arange(m) - starts[line_claim] + 1).astype(np.int64)
    group_i = np.zeros(m, dtype=np.int64)
    line_svcline = c["svcline_i"][line_claim]
    for li, sl in enumerate(SERVICE_LINES):
        mask = line_svcline == li
        groups = np.array([PROC_GROUP_INDEX[g] for g, _wt in sl[4]])
        gw = np.array([wt for _g, wt in sl[4]])
        group_i[mask] = rng.choice(groups, size=int(mask.sum()), p=gw / gw.sum())
    code_i = rng.integers(0, 6, size=m).astype(np.int64)
    units = rng.integers(1, 4, size=m).astype(np.int64)
    los_line = np.where(c["is_institutional"], c["los"], 0)[line_claim]
    offs = np.floor(rng.random(m) * (los_line + 1)).astype(np.int64)
    line_svc_day = c["svc_day"][line_claim] + offs
    charge_lag = np.clip(_rint64(rng.gamma(1.5, 1.5, size=m)), 0, 14)
    billed = np.zeros(m, dtype=np.int64)
    for gi, (_g, _pfx, _rev, median, sigma) in enumerate(PROC_GROUPS):
        mask = group_i == gi
        billed[mask] = _rint64(rng.lognormal(np.log(median), sigma, size=int(mask.sum())))
    return {
        "n_lines_per_claim": per_claim,
        "line_start": starts,
        "line_claim": line_claim,
        "line_num": line_num,
        "line_group_i": group_i,
        "line_code_i": code_i,
        "line_units": units,
        "line_svc_day": line_svc_day,
        "line_charge_day": line_svc_day + charge_lag,
        "line_billed": np.maximum(billed, 1),
    }


def _carc_draws(
    dims: Dims, rng: np.random.Generator, c: dict[str, Any], n: int, n_lines: np.ndarray
) -> dict[str, np.ndarray]:
    """Generic denial CARCs, levels, groups and remark codes for denied claims.

    CARC 197 never appears in the Halvern Health x Imaging cell: prior-auth
    denials there belong to scenario 1 and stay a 2026 story.
    """
    carcs = np.array([code for code, _wt in GENERIC_CARC_MIX])
    carc_w = np.array([wt for _code, wt in GENERIC_CARC_MIX])
    carc = rng.choice(carcs, size=n, p=carc_w / carc_w.sum()).astype(np.int64)
    mer_img = (c["payer_i"] == dims.payer_index[SCENARIOS.s1_payer]) & (
        c["svcline_i"] == dims.service_line_index[SCENARIOS.s1_service_line]
    )
    carc = np.where(mer_img & (carc == 197), 16, carc)
    level_line = (rng.random(n) < 0.40) & (n_lines >= 2)
    pick_k = np.floor(rng.random(n) * n_lines).astype(np.int64)
    rarc = np.where(rng.random(n) < 0.40, rng.integers(1, 21, size=n), -1).astype(np.int64)
    group_map = np.zeros(300, dtype=np.int64)  # default CO = 0
    for code, grp in CARC_GROUP.items():
        group_map[code] = _GROUP_NAMES.index(grp)
    group = group_map[carc]
    group = np.where((group == 0) & (rng.random(n) < 0.08), _GROUP_NAMES.index("PI"), group)
    return {"carc": carc, "level_line": level_line, "pick_k": pick_k, "rarc": rarc, "group": group}


# ---------------------------------------------------------------------------
# lifecycle: submission -> adjudication -> denial/appeal -> cash -> closure


def _build_rows(config: GeneratorConfig, dims: Dims, n: int) -> _Rows:
    cohort_rng = _stream("cohort")
    lines_rng = _stream("lines")
    life = _stream("lifecycle")
    deadline = BACKFILL.resolved_by

    c = _cohort(config, dims, cohort_rng, n)
    ln = _lines(config, lines_rng, c, n)
    line_claim = ln["line_claim"]

    # --- contract rates, expected and allowed --------------------------------
    payer_rate = np.array([p.contract_rate for p in PAYERS])
    rate = np.clip(payer_rate[c["payer_i"]] + life.normal(0.0, 0.015, size=n), 0.2, 0.8)
    line_expected = _rint64(ln["line_billed"] * rate[line_claim])
    line_allowed = line_expected.copy()  # allowed == expected outside the planted cells
    billed_total = np.bincount(line_claim, weights=ln["line_billed"], minlength=n).astype(np.int64)
    expected_total = np.bincount(line_claim, weights=line_expected, minlength=n).astype(np.int64)
    allowed_total = np.bincount(line_claim, weights=line_allowed, minlength=n).astype(np.int64)

    # --- submission (every 2024 claim went out the door) ----------------------
    bill_ready = np.where(c["is_institutional"], c["discharge_day"], c["svc_day"])
    sub_lag = _rint64(life.lognormal(np.log(5.0), 0.6, size=n))
    sub_lag = np.clip(sub_lag + np.where(c["is_institutional"], 3, 0), 1, BACKFILL.max_submission_lag)
    sub_day = (bill_ready + sub_lag).astype(np.int64)
    line_charge_day = np.minimum(ln["line_charge_day"], sub_day[line_claim])

    # --- adjudication ---------------------------------------------------------
    adj_mu = np.array([p.adj_lag_mean for p in PAYERS])[c["payer_i"]]
    adj_sd = np.array([p.adj_lag_sd for p in PAYERS])[c["payer_i"]]
    adj_lag = np.clip(_rint64(life.normal(adj_mu, adj_sd)), 3, BACKFILL.max_adjudication_lag)
    remit1_day = (sub_day + adj_lag).astype(np.int64)

    # --- denials --------------------------------------------------------------
    base_p = np.array([p.denial_prob for p in PAYERS])[c["payer_i"]] * BACKFILL.denial_factor
    denied = life.random(n) < base_p
    dr = _carc_draws(dims, life, c, n, ln["n_lines_per_claim"])
    den_carc = np.where(denied, dr["carc"], 0).astype(np.int64)
    den_group = np.where(denied, dr["group"], 0).astype(np.int64)
    den_level_line = denied & dr["level_line"]
    den_line_pos = np.where(den_level_line, ln["line_start"] + dr["pick_k"], -1).astype(np.int64)
    safe_pos = np.maximum(den_line_pos, 0)
    denied_line_allowed = np.where(den_level_line, line_allowed[safe_pos], 0)
    denied_line_billed = np.where(den_level_line, ln["line_billed"][safe_pos], 0)
    den_amount = np.where(denied, np.where(den_level_line, denied_line_billed, billed_total), 0)
    den_rarc = np.where(denied, dr["rarc"], -1).astype(np.int64)

    # --- appeals: filed, decided, and closed inside the deadline --------------
    # Appeals are drawn exactly as the organic world draws them (same filing,
    # decision and rebill lags); a chain whose closure would land after
    # `resolved_by` is stood down to the never-appealed path. That backstop only
    # reaches the very end of the year and at full scale never fires — the latest
    # possible chain closes in April 2025 — so the 2024 appeal rate is the
    # organic draw and stays comparable with 2025's.
    appeal_draw = denied & (life.random(n) < config.appeal_frac)
    file_day = remit1_day + life.integers(5, 31, size=n)
    dec_day = file_day + life.integers(20, 61, size=n)
    overturn_draw = life.random(n) < config.overturn_frac
    ot_gap = life.integers(5, 16, size=n)
    remit2_candidate = dec_day + ot_gap
    post_mu = np.array([p.post_lag_mean for p in PAYERS])[c["payer_i"]]
    post_sd = np.array([p.post_lag_sd for p in PAYERS])[c["payer_i"]]
    lag1 = np.clip(_rint64(life.normal(post_mu, post_sd)), 1, BACKFILL.max_post_lag)
    lag2 = np.clip(_rint64(life.normal(post_mu, post_sd)), 1, BACKFILL.max_post_lag)
    wo_upheld = life.integers(5, 21, size=n)
    wo_unappealed = life.integers(30, 91, size=n)
    chain_close = np.where(overturn_draw, remit2_candidate + lag2, dec_day + wo_upheld)
    appealed = appeal_draw & (chain_close <= deadline)
    overturned = appealed & overturn_draw
    appeal_file_day = np.where(appealed, file_day, NEVER).astype(np.int64)
    appeal_dec_day = np.where(appealed, dec_day, NEVER).astype(np.int64)
    remit2_day = np.where(overturned, remit2_candidate, NEVER).astype(np.int64)

    upheld = appealed & ~overturned
    never_appealed = denied & ~appealed
    writeoff_day = np.full(n, NEVER, dtype=np.int64)
    writeoff_day[upheld] = (dec_day + wo_upheld)[upheld]
    writeoff_day[never_appealed] = (remit1_day + wo_unappealed)[never_appealed]

    # --- patient responsibility and posting ----------------------------------
    claim_denied = denied & ~den_level_line
    pays_at_remit1 = ~claim_denied
    pays_at_remit2 = remit2_day != NEVER
    paid_allowed = np.where(pays_at_remit1, allowed_total - denied_line_allowed, allowed_total)
    pr_frac = np.array([p.patient_resp_frac for p in PAYERS])[c["payer_i"]]
    pr_jitter = 0.7 + 0.6 * life.random(n)
    ever_paid = pays_at_remit1 | pays_at_remit2
    pr_amount = np.where(ever_paid, _rint64(paid_allowed * pr_frac * pr_jitter), 0).astype(np.int64)
    pr_known_day = np.where(
        pays_at_remit1, remit1_day, np.where(pays_at_remit2, remit2_day, NEVER)
    ).astype(np.int64)
    post1 = np.where(pays_at_remit1, remit1_day + lag1, NEVER).astype(np.int64)
    post2 = np.where(pays_at_remit2, remit2_day + lag2, NEVER).astype(np.int64)

    payment1 = np.where(pays_at_remit1, np.maximum(paid_allowed - pr_amount, 0), 0)
    contractual1 = np.where(pays_at_remit1, billed_total - allowed_total, 0)
    ot_claim = overturned & ~den_level_line
    ot_line = overturned & den_level_line
    pay2_amt = np.zeros(n, dtype=np.int64)
    pay2_amt[ot_claim] = np.maximum(allowed_total - pr_amount, 0)[ot_claim]
    pay2_amt[ot_line] = denied_line_allowed[ot_line]
    contractual2 = np.zeros(n, dtype=np.int64)
    contractual2[ot_claim] = (billed_total - allowed_total)[ot_claim]
    has_wo = writeoff_day != NEVER
    wo_amount = np.zeros(n, dtype=np.int64)
    wo_amount[has_wo & den_level_line] = denied_line_allowed[has_wo & den_level_line]
    wo_amount[has_wo & ~den_level_line] = billed_total[has_wo & ~den_level_line]

    first_pay_post = np.where(payment1 > 0, post1, np.where(pay2_amt > 0, post2, NEVER)).astype(np.int64)
    pay_post_for_patient = np.where(payment1 > 0, post1, post2)
    collect_delay = life.integers(10, 46, size=n)
    patient_day = np.where(
        (pr_amount > 0) & (first_pay_post != NEVER), pay_post_for_patient + collect_delay, NEVER
    )
    collects = (
        (pr_amount > 0)
        & (first_pay_post != NEVER)
        & (life.random(n) < config.patient_collect_frac)
        & (patient_day <= deadline)  # a 2024 balance still owed today is not a closed year
    )

    dup_delay = life.integers(7, 22, size=n)
    refund_delay = life.integers(20, 61, size=n)
    dup_day = np.where(pays_at_remit1, post1 + dup_delay, NEVER)
    refund_day = np.where(pays_at_remit1, dup_day + refund_delay, NEVER)
    dup_mask = (
        pays_at_remit1
        & ~denied
        & (life.random(n) < config.refund_frac)
        & (payment1 > 0)
        & (refund_day <= deadline)  # the credit must be returned inside the closed year
    )
    dup_amt = np.where(dup_mask, _rint64(payment1 * 0.10), 0)

    # --- resolution -----------------------------------------------------------
    line_resolution = np.where(ot_line, post2, np.where(den_level_line, writeoff_day, 0))
    resolved = np.full(n, NEVER, dtype=np.int64)
    path_a = pays_at_remit1 & ~denied
    resolved[path_a] = post1[path_a]
    resolved[dup_mask] = refund_day[dup_mask]
    resolved[den_level_line] = np.maximum(post1, line_resolution)[den_level_line]
    path_c_paid = claim_denied & (pay2_amt > 0)
    resolved[path_c_paid] = post2[path_c_paid]
    path_c_wo = claim_denied & has_wo
    resolved[path_c_wo] = writeoff_day[path_c_wo]

    claims: dict[str, np.ndarray] = {
        "svc_day": c["svc_day"],
        "discharge_day": c["discharge_day"],
        "patient_i": c["patient_i"],
        "payer_i": c["payer_i"],
        "plan_i": c["plan_i"],
        "provider_i": c["provider_i"],
        "facility_i": c["facility_i"],
        "svcline_i": c["svcline_i"],
        "is_institutional": c["is_institutional"],
        "pseq": c["pseq"],
        "oins": c["oins"],
        "cobm": c["cobm"],
        "sub_day": sub_day,
        "remit1_day": remit1_day,
        "remit2_day": remit2_day,
        "denied": denied,
        "den_carc": den_carc,
        "den_group": den_group,
        "den_level_line": den_level_line,
        "den_line_pos": den_line_pos,
        "den_amount": den_amount.astype(np.int64),
        "den_rarc": den_rarc,
        "appealed": appealed,
        "appeal_file_day": appeal_file_day,
        "appeal_dec_day": appeal_dec_day,
        "overturned": overturned,
        "writeoff_day": writeoff_day,
        "billed_total": billed_total,
        "allowed_total": allowed_total,
        "expected_total": expected_total,
        "pr_amount": pr_amount,
        "pr_known_day": pr_known_day,
        "first_pay_post": first_pay_post,
        "resolved_day": resolved,
        "fpp": pays_at_remit1 & ~denied,
        "n_lines_per_claim": ln["n_lines_per_claim"],
    }
    lines: dict[str, np.ndarray] = {
        "line_claim": line_claim,
        "line_num": ln["line_num"],
        "line_group_i": ln["line_group_i"],
        "line_code_i": ln["line_code_i"],
        "line_units": ln["line_units"],
        "line_svc_day": ln["line_svc_day"],
        "line_charge_day": line_charge_day,
        "line_billed": ln["line_billed"],
        "line_allowed": line_allowed,
        "line_expected": line_expected,
    }

    rows = _Rows(claims=claims, lines=lines, line_start=ln["line_start"])
    _remit_rows(rows, remit1_day, remit2_day, n)
    _txn_rows(
        rows,
        n,
        events=(
            # (mask, line_pos, remit_seq, type_code, amount, post_day)
            (pays_at_remit1, None, 1, 0, payment1, post1),
            (pays_at_remit1, None, 1, 1, contractual1, post1),
            (pays_at_remit2, None, 2, 0, pay2_amt, post2),
            (ot_claim, None, 2, 1, contractual2, post2),
            (has_wo, np.where(den_level_line, den_line_pos, -1), 0, 2, wo_amount, writeoff_day),
            (collects, None, 0, 3, pr_amount, patient_day),
            (dup_mask, None, 1, 0, dup_amt, dup_day),
            (dup_mask, None, 0, 4, dup_amt, refund_day),
        ),
    )
    _denial_rows(rows, claims)
    return rows


def _remit_rows(rows: _Rows, remit1_day: np.ndarray, remit2_day: np.ndarray, n: int) -> None:
    """Remit rows ordered by date, with each claim's row positions recorded."""
    r1 = np.flatnonzero(remit1_day != NEVER)
    r2 = np.flatnonzero(remit2_day != NEVER)
    r_claim = np.concatenate([r1, r2])
    r_seq = np.concatenate([np.full(len(r1), 1), np.full(len(r2), 2)]).astype(np.int64)
    r_day = np.concatenate([remit1_day[r1], remit2_day[r2]])
    order = np.lexsort((r_seq, r_claim, r_day))
    rows.remit_claim = r_claim[order].astype(np.int64)
    rows.remit_seq = r_seq[order]
    rows.remit_day = r_day[order].astype(np.int64)
    rows.remit_pos1 = np.full(n, -1, dtype=np.int64)
    rows.remit_pos2 = np.full(n, -1, dtype=np.int64)
    pos = np.arange(len(order))
    seq1 = rows.remit_seq == 1
    rows.remit_pos1[rows.remit_claim[seq1]] = pos[seq1]
    rows.remit_pos2[rows.remit_claim[~seq1]] = pos[~seq1]


def _txn_rows(rows: _Rows, n: int, events: tuple[tuple[Any, ...], ...]) -> None:
    """Transaction rows, assembled and ordered exactly as world.py orders them."""
    parts: list[tuple[np.ndarray, ...]] = []
    for mask, line_pos, seq, type_code, amount, post_day in events:
        keep = mask & (amount > 0) & (post_day != NEVER)
        idx = np.flatnonzero(keep)
        lp = line_pos[idx] if line_pos is not None else np.full(len(idx), -1)
        parts.append(
            (
                idx.astype(np.int64),
                lp.astype(np.int64),
                np.full(len(idx), seq, dtype=np.int64),
                np.full(len(idx), type_code, dtype=np.int64),
                amount[idx].astype(np.int64),
                post_day[idx].astype(np.int64),
            )
        )
    t_claim = np.concatenate([p[0] for p in parts])
    t_line = np.concatenate([p[1] for p in parts])
    t_seq = np.concatenate([p[2] for p in parts])
    t_type = np.concatenate([p[3] for p in parts])
    t_amount = np.concatenate([p[4] for p in parts])
    t_post = np.concatenate([p[5] for p in parts])
    order = np.lexsort((t_amount, t_type, t_claim, t_post))
    t_claim, t_line, t_seq = t_claim[order], t_line[order], t_seq[order]
    t_type, t_amount, t_post = t_type[order], t_amount[order], t_post[order]
    t_remit = np.where(
        t_seq == 1,
        rows.remit_pos1[t_claim],
        np.where(t_seq == 2, rows.remit_pos2[t_claim], -1),
    ).astype(np.int64)
    rows.txn = {
        "txn_claim": t_claim,
        "txn_line": t_line,
        "txn_remit": t_remit,
        "txn_type": t_type,
        "txn_amount": t_amount,
        "txn_post_day": t_post,
        "txn_remit_day": np.where(t_remit >= 0, rows.remit_day[np.maximum(t_remit, 0)], NEVER).astype(
            np.int64
        ),
    }


def _denial_rows(rows: _Rows, claims: dict[str, np.ndarray]) -> None:
    """One denial row per denied claim, ordered by denial date then claim."""
    d_idx = np.flatnonzero(claims["denied"])
    order = d_idx[np.lexsort((d_idx, claims["remit1_day"][d_idx]))]
    rows.dn = {
        "dn_claim": order.astype(np.int64),
        "dn_line": claims["den_line_pos"][order],
        "dn_remit": rows.remit_pos1[order],
        "dn_group": claims["den_group"][order],
        "dn_carc": claims["den_carc"][order],
        "dn_rarc": claims["den_rarc"][order],
        "dn_level_line": claims["den_level_line"][order],
        "dn_day": claims["remit1_day"][order],
        "dn_amount": claims["den_amount"][order],
        "dn_appealed": claims["appealed"][order],
        "dn_file_day": claims["appeal_file_day"][order],
        "dn_dec_day": claims["appeal_dec_day"][order],
        "dn_overturned": claims["overturned"][order],
    }


# ---------------------------------------------------------------------------
# append + guards


def _rebase(arr: np.ndarray, offset: int) -> np.ndarray:
    """Shift non-negative indices by `offset`, leaving -1 sentinels alone."""
    return np.where(arr >= 0, arr + offset, arr)


def append_backfill(world: World, rows: _Rows) -> World:
    """Concatenate the backfill block onto the world (existing rows untouched)."""
    w = world
    n0, m0, r0 = w.n_claims, len(w.line_claim), len(w.remit_claim)
    n_new = len(rows.claims["svc_day"])

    for fld in CLAIM_ARRAY_FIELDS:
        arr = rows.claims[fld]
        if fld == "den_line_pos":
            arr = _rebase(arr, m0)
        setattr(w, fld, np.concatenate([getattr(w, fld), arr]))
    w.line_start = np.concatenate([w.line_start, rows.line_start + m0])
    w.n_pre_backfill_claims = n0
    w.n_claims = n0 + n_new

    for fld in LINE_ARRAY_FIELDS:
        arr = rows.lines[fld]
        if fld == "line_claim":
            arr = arr + n0
        setattr(w, fld, np.concatenate([getattr(w, fld), arr]))

    w.remit_claim = np.concatenate([w.remit_claim, rows.remit_claim + n0])
    w.remit_seq = np.concatenate([w.remit_seq, rows.remit_seq])
    w.remit_day = np.concatenate([w.remit_day, rows.remit_day])
    w.remit_pos1 = np.concatenate([w.remit_pos1, _rebase(rows.remit_pos1, r0)])
    w.remit_pos2 = np.concatenate([w.remit_pos2, _rebase(rows.remit_pos2, r0)])

    t = rows.txn
    w.txn_claim = np.concatenate([w.txn_claim, t["txn_claim"] + n0])
    w.txn_line = np.concatenate([w.txn_line, _rebase(t["txn_line"], m0)])
    w.txn_remit = np.concatenate([w.txn_remit, _rebase(t["txn_remit"], r0)])
    w.txn_type = np.concatenate([w.txn_type, t["txn_type"]])
    w.txn_amount = np.concatenate([w.txn_amount, t["txn_amount"]])
    w.txn_post_day = np.concatenate([w.txn_post_day, t["txn_post_day"]])
    w.txn_remit_day = np.concatenate([w.txn_remit_day, t["txn_remit_day"]])

    d = rows.dn
    w.dn_claim = np.concatenate([w.dn_claim, d["dn_claim"] + n0])
    w.dn_line = np.concatenate([w.dn_line, _rebase(d["dn_line"], m0)])
    w.dn_remit = np.concatenate([w.dn_remit, _rebase(d["dn_remit"], r0)])
    w.dn_group = np.concatenate([w.dn_group, d["dn_group"]])
    w.dn_carc = np.concatenate([w.dn_carc, d["dn_carc"]])
    w.dn_rarc = np.concatenate([w.dn_rarc, d["dn_rarc"]])
    w.dn_level_line = np.concatenate([w.dn_level_line, d["dn_level_line"]])
    w.dn_day = np.concatenate([w.dn_day, d["dn_day"]])
    w.dn_amount = np.concatenate([w.dn_amount, d["dn_amount"]])
    w.dn_appealed = np.concatenate([w.dn_appealed, d["dn_appealed"]])
    w.dn_file_day = np.concatenate([w.dn_file_day, d["dn_file_day"]])
    w.dn_dec_day = np.concatenate([w.dn_dec_day, d["dn_dec_day"]])
    w.dn_overturned = np.concatenate([w.dn_overturned, d["dn_overturned"]])
    return w


def _enforce_guards(w: World, n0: int, m0: int, r0: int, t0: int, d0: int) -> None:
    """Prove at build time that no backfill row can reach a 2026 answer.

    The scenario windows, the anomaly observation windows and every trailing
    window anchored at a watermark all live in 2025-2026, so a single closure
    proof — no backfill date on or after `resolved_by` + 1 — covers all of them.
    The remaining checks are the point-metric invariants stated per metric.
    """
    bf = slice(n0, w.n_claims)
    deadline = BACKFILL.resolved_by
    problems: list[str] = []

    svc = w.svc_day[bf]
    if np.any((svc < BACKFILL.service_start) | (svc > BACKFILL.service_end)):
        problems.append("backfill service date outside 2024")
    if np.any(w.sub_day[bf] == NEVER):
        problems.append("backfill claim left unsubmitted (would create DNFB / timely-filing risk)")
    if np.any(w.remit1_day[bf] == NEVER):
        problems.append("backfill claim left unadjudicated (would sit in A/R at the watermark)")
    if np.any(w.resolved_day[bf] == NEVER) or np.any(w.resolved_day[bf] > deadline):
        problems.append("backfill claim not resolved by 2025-06-30")
    if np.any(w.appealed[bf] & (w.appeal_dec_day[bf] > deadline)):
        problems.append("backfill appeal still undecided at 2025-06-30")
    # project.py reads PAID from a visible payment and CLOSED from a visible
    # denial plus write-off; anything else would land in OPEN or DENIED, i.e. in
    # A/R at every watermark.
    closed = (w.first_pay_post[bf] != NEVER) | (w.denied[bf] & (w.writeoff_day[bf] != NEVER))
    if not np.all(closed):
        problems.append("backfill claim would read OPEN or DENIED at the watermark")

    # every appended date, of every kind, must land inside the closed year
    dated = {
        "service": svc,
        "discharge": w.discharge_day[bf],
        "submission": w.sub_day[bf],
        "first remit": w.remit1_day[bf],
        "second remit": w.remit2_day[bf],
        "appeal filing": w.appeal_file_day[bf],
        "appeal decision": w.appeal_dec_day[bf],
        "write-off": w.writeoff_day[bf],
        "first payment posting": w.first_pay_post[bf],
        "charge entry": w.line_charge_day[m0:],
        "line service": w.line_svc_day[m0:],
        "remit row": w.remit_day[r0:],
        "transaction posting": w.txn_post_day[t0:],
        "denial": w.dn_day[d0:],
    }
    for label, arr in dated.items():
        real = arr[arr != NEVER]
        if real.size and int(real.max()) > deadline:
            problems.append(f"backfill {label} date after 2025-06-30")

    # point-metric invariants: nothing open, nothing owed, nothing over-collected
    n_new = w.n_claims - n0
    cash = np.isin(w.txn_type[t0:], (0, 3))  # PAYMENT / PATIENT_PAYMENT
    refunds = w.txn_type[t0:] == 4
    local_claim = w.txn_claim[t0:] - n0
    collected = np.bincount(local_claim[cash], weights=w.txn_amount[t0:][cash], minlength=n_new)
    returned = np.bincount(local_claim[refunds], weights=w.txn_amount[t0:][refunds], minlength=n_new)
    if np.any(collected - returned - w.expected_total[bf] > 0):
        problems.append("backfill claim carries an unrefunded credit balance")
    if np.any(w.txn_amount[t0:] <= 0) or np.any(w.dn_amount[d0:] <= 0):
        problems.append("backfill non-positive transaction/denied amount")
    if np.any(w.line_billed[m0:] <= 0):
        problems.append("backfill non-positive line billed amount")
    if np.any(w.line_charge_day[m0:] > w.sub_day[w.line_claim[m0:]]):
        problems.append("backfill charge entered after the claim was submitted")

    if problems:
        raise ValueError("2024 backfill violates its closure guards: " + "; ".join(problems))


def apply_backfill(world: World, config: GeneratorConfig) -> World:
    """Append the closed 2024 year, or record that it was switched off."""
    if not config.include_backfill:
        world.n_pre_backfill_claims = world.n_claims
        return world
    n0, m0 = world.n_claims, len(world.line_claim)
    r0, t0, d0 = len(world.remit_claim), len(world.txn_claim), len(world.dn_claim)
    rows = _build_rows(config, world.dims, backfill_claim_count(world))
    world = append_backfill(world, rows)
    _enforce_guards(world, n0, m0, r0, t0, d0)
    return world
