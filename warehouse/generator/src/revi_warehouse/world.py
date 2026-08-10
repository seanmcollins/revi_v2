"""Build the full generated world once, as flat numpy arrays.

The world is generated a single time and later projected per snapshot (see
project.py) — snapshots never re-generate anything. Every random draw flows
through the one Generator passed in, in a fixed stage order; that order is part
of the determinism contract.

Dates are integer days since 1970-01-01. NEVER marks an event that does not
happen (or is unknown). Money is integer cents throughout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from revi_warehouse.config import (
    NEVER,
    SCENARIOS,
    SERVICE_END,
    SERVICE_START,
    GeneratorConfig,
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

TXN_TYPES = ("PAYMENT", "CONTRACTUAL_ADJ", "OTHER_ADJ", "PATIENT_PAYMENT", "REFUND")
APPEAL_NONE, APPEAL_APPEALED = 0, 1
PROC_GROUP_INDEX = {g[0]: i for i, g in enumerate(PROC_GROUPS)}

# The per-claim and per-line array names every append-only stage (anomalies.py,
# backfill.py) has to supply. Kept here so a new World field cannot be forgotten
# by one appender and remembered by the other.
CLAIM_ARRAY_FIELDS = (
    "svc_day", "discharge_day", "patient_i", "payer_i", "plan_i", "provider_i",
    "facility_i", "svcline_i", "is_institutional", "pseq", "oins", "cobm",
    "sub_day", "remit1_day", "remit2_day", "denied", "den_carc", "den_group",
    "den_level_line", "den_line_pos", "den_amount", "den_rarc", "appealed",
    "appeal_file_day", "appeal_dec_day", "overturned", "writeoff_day",
    "billed_total", "allowed_total", "expected_total", "pr_amount",
    "pr_known_day", "first_pay_post", "resolved_day", "fpp", "n_lines_per_claim",
)
LINE_ARRAY_FIELDS = (
    "line_claim", "line_num", "line_group_i", "line_code_i", "line_units",
    "line_svc_day", "line_charge_day", "line_billed", "line_allowed", "line_expected",
)


@dataclass
class World:
    """Full-world arrays. Facts carry event days so snapshots can truncate them."""

    config: GeneratorConfig
    dims: Dims
    # claims (N). n_base_claims counts the organic world; claims appended by the
    # anomaly injector (anomalies.py) occupy indices n_base_claims..n_claims-1.
    # The 2024 backfill (backfill.py) appends after those, from
    # n_pre_backfill_claims onward.
    n_claims: int = 0
    n_base_claims: int = 0
    n_pre_backfill_claims: int = 0
    svc_day: Any = None
    discharge_day: Any = None  # NEVER for professional claims
    patient_i: Any = None
    payer_i: Any = None
    plan_i: Any = None
    provider_i: Any = None
    facility_i: Any = None
    svcline_i: Any = None
    is_institutional: Any = None
    pseq: Any = None  # 0=P 1=S 2=T
    oins: Any = None
    cobm: Any = None
    sub_day: Any = None
    remit1_day: Any = None
    remit2_day: Any = None
    denied: Any = None
    den_carc: Any = None
    den_group: Any = None  # index into ("CO","PR","OA","PI")
    den_level_line: Any = None  # True = LINE-level denial
    den_line_pos: Any = None  # index into line arrays, -1 when claim-level
    den_amount: Any = None
    den_rarc: Any = None  # -1 = NULL
    appealed: Any = None
    appeal_file_day: Any = None
    appeal_dec_day: Any = None
    overturned: Any = None
    writeoff_day: Any = None
    billed_total: Any = None
    allowed_total: Any = None
    expected_total: Any = None
    pr_amount: Any = None
    pr_known_day: Any = None
    first_pay_post: Any = None
    resolved_day: Any = None
    fpp: Any = None  # first-pass paid (world outcome, known at remit1)
    # lines (M)
    n_lines_per_claim: Any = None
    line_start: Any = None
    line_claim: Any = None
    line_num: Any = None
    line_group_i: Any = None
    line_code_i: Any = None
    line_units: Any = None
    line_svc_day: Any = None
    line_charge_day: Any = None
    line_billed: Any = None
    line_allowed: Any = None
    line_expected: Any = None
    # remits (R)
    remit_claim: Any = None
    remit_seq: Any = None
    remit_day: Any = None
    remit_pos1: Any = None  # claim -> row index of its seq-1 remit (-1 if none)
    remit_pos2: Any = None
    # transactions (T)
    txn_claim: Any = None
    txn_line: Any = None  # -1 = NULL
    txn_remit: Any = None  # row index into remits, -1 = NULL
    txn_type: Any = None
    txn_amount: Any = None
    txn_post_day: Any = None
    txn_remit_day: Any = None  # NEVER = NULL
    # denials (D)
    dn_claim: Any = None
    dn_line: Any = None
    dn_remit: Any = None
    dn_group: Any = None
    dn_carc: Any = None
    dn_rarc: Any = None
    dn_level_line: Any = None
    dn_day: Any = None
    dn_amount: Any = None
    dn_appealed: Any = None
    dn_file_day: Any = None
    dn_dec_day: Any = None
    dn_overturned: Any = None
    # recovery chain events (E) — appended by recovery.py after every other
    # stage. One row per resubmission and per outcome remit; `rc_denial` indexes
    # the denial arrays above, `rc_parent` the outcome's own resubmission row.
    rc_denial: Any = None
    rc_claim: Any = None
    rc_cycle: Any = None
    rc_type: Any = None  # 0 = RESUBMISSION, 1 = OUTCOME
    rc_day: Any = None
    rc_parent: Any = None  # -1 on resubmission rows
    rc_action: Any = None  # index into RESUBMISSION_TYPES, -1 on outcome rows
    rc_outcome: Any = None  # index into RECOVERY_OUTCOMES, -1 on resubmission rows
    rc_days_from_denial: Any = None
    rc_days_from_resub: Any = None  # -1 on resubmission rows
    rc_denied_amount: Any = None
    rc_recovered: Any = None
    rc_carc: Any = None  # 0 = NULL (paid outcomes and resubmissions)
    rc_group: Any = None  # -1 = NULL
    rc_rarc: Any = None  # -1 = NULL


def _rint64(x: np.ndarray) -> np.ndarray:
    return np.rint(x).astype(np.int64)


def build_world(config: GeneratorConfig, rng: np.random.Generator, dims: Dims) -> World:
    w = World(config=config, dims=dims)
    s = SCENARIOS
    n = config.n_claims
    w.n_claims = n
    w.n_base_claims = n

    # --- Stage 1: service dates (weekday-weighted), sorted ascending ----------
    days = np.arange(SERVICE_START, SERVICE_END + 1)
    weekday = (days + 3) % 7  # 0=Mon .. 6=Sun
    day_w = np.where(weekday <= 4, 1.0, np.where(weekday == 5, 0.35, 0.20))
    day_p = day_w / day_w.sum()
    w.svc_day = np.sort(rng.choice(days, size=n, p=day_p)).astype(np.int64)

    # --- Stage 2: payer / plan / service line / facility / provider / patient -
    payer_p = np.array([p.weight for p in PAYERS])
    w.payer_i = rng.choice(len(PAYERS), size=n, p=payer_p / payer_p.sum()).astype(np.int64)
    w.plan_i = np.zeros(n, dtype=np.int64)
    for pi in range(len(PAYERS)):
        mask = w.payer_i == pi
        idx, weights = dims.plans_by_payer[pi]
        w.plan_i[mask] = rng.choice(idx, size=int(mask.sum()), p=weights)
    line_p = np.array([sl[1] for sl in SERVICE_LINES])
    w.svcline_i = rng.choice(len(SERVICE_LINES), size=n, p=line_p / line_p.sum()).astype(np.int64)
    fac_p = np.array([f[2] for f in FACILITIES])
    w.facility_i = rng.choice(len(FACILITIES), size=n, p=fac_p / fac_p.sum()).astype(np.int64)
    w.provider_i = np.zeros(n, dtype=np.int64)
    for li in range(len(SERVICE_LINES)):
        mask = w.svcline_i == li
        pool = dims.providers_by_service_line[li]
        w.provider_i[mask] = rng.choice(pool, size=int(mask.sum()))
    w.patient_i = rng.integers(0, config.n_patients, size=n)

    # --- Stage 3: claim type, discharge, payer sequence, COB base flags -------
    inst_p = np.array([sl[3] for sl in SERVICE_LINES])[w.svcline_i]
    w.is_institutional = rng.random(n) < inst_p
    los = rng.integers(1, 8, size=n)
    w.discharge_day = np.where(w.is_institutional, w.svc_day + los, NEVER).astype(np.int64)
    w.pseq = rng.choice(3, size=n, p=np.array([0.86, 0.11, 0.03])).astype(np.int64)
    u_oins = rng.random(n)
    w.oins = (w.pseq > 0) | (u_oins < 0.12)
    w.cobm = w.oins & (w.pseq == 0) & (rng.random(n) < 0.01)

    # --- Stage 4: scenario 5 selections (timely-filing cluster + CARC-29 set) -
    sm_payer = dims.payer_index[s.s3b_payer]  # State Medicaid
    smhmo_plan = dims.plan_index[s.s5_plan]
    eastside = dims.facility_index[s.s5_facility]
    july = (w.svc_day >= s.s5_july_start) & (w.svc_day <= s.s5_july_end) & (w.facility_i == eastside)
    cluster_pick = rng.choice(np.flatnonzero(july), size=config.timely_cluster_size, replace=False)
    cluster = np.zeros(n, dtype=bool)
    cluster[cluster_pick] = True
    febmar = (
        (w.svc_day >= s.s5_carc29_service_start)
        & (w.svc_day <= s.s5_carc29_service_end)
        & (w.facility_i == eastside)
    )
    carc29_pick = rng.choice(np.flatnonzero(febmar), size=config.carc29_count, replace=False)
    carc29 = np.zeros(n, dtype=bool)
    carc29[carc29_pick] = True
    for mask in (cluster, carc29):
        w.payer_i[mask] = sm_payer
        w.plan_i[mask] = smhmo_plan

    # --- Stage 5: scenario 2 COB cohort (Silverline MA, Apr-Jul 2026) ---------
    silverline = dims.payer_index[s.s2_payer]
    s2_window = (w.svc_day >= s.s2_service_start) & (w.svc_day <= s.s2_service_end)
    cob_set = (w.payer_i == silverline) & s2_window & (rng.random(n) < s.s2_frac)
    w.oins = w.oins | cob_set
    w.pseq[cob_set] = 0
    w.cobm = w.cobm | cob_set

    # --- Stage 6: claim lines --------------------------------------------------
    n_extra = np.minimum(rng.poisson(config.extra_lines_lambda, size=n), 5)
    w.n_lines_per_claim = (1 + n_extra).astype(np.int64)
    m = int(w.n_lines_per_claim.sum())
    w.line_start = np.concatenate(([0], np.cumsum(w.n_lines_per_claim)[:-1])).astype(np.int64)
    w.line_claim = np.repeat(np.arange(n), w.n_lines_per_claim)
    w.line_num = (np.arange(m) - w.line_start[w.line_claim] + 1).astype(np.int64)
    w.line_group_i = np.zeros(m, dtype=np.int64)
    line_svcline = w.svcline_i[w.line_claim]
    for li, sl in enumerate(SERVICE_LINES):
        mask = line_svcline == li
        groups = np.array([PROC_GROUP_INDEX[g] for g, _wt in sl[4]])
        gw = np.array([wt for _g, wt in sl[4]])
        w.line_group_i[mask] = rng.choice(groups, size=int(mask.sum()), p=gw / gw.sum())
    w.line_code_i = rng.integers(0, 6, size=m)
    w.line_units = rng.integers(1, 4, size=m)
    los_line = np.where(w.is_institutional, los, 0)[w.line_claim]
    offs = np.floor(rng.random(m) * (los_line + 1)).astype(np.int64)
    w.line_svc_day = w.svc_day[w.line_claim] + offs
    charge_lag = np.clip(_rint64(rng.gamma(1.5, 1.5, size=m)), 0, 14)
    w.line_charge_day = w.line_svc_day + charge_lag
    w.line_billed = np.zeros(m, dtype=np.int64)
    for gi, (_g, _pfx, _rev, median, sigma) in enumerate(PROC_GROUPS):
        mask = w.line_group_i == gi
        w.line_billed[mask] = _rint64(rng.lognormal(np.log(median), sigma, size=int(mask.sum())))
    # --- Stage 7: contract rates, expected / allowed ---------------------------
    payer_rate = np.array([p.contract_rate for p in PAYERS])
    rate = np.clip(payer_rate[w.payer_i] + rng.normal(0.0, 0.015, size=n), 0.2, 0.8)
    w.line_expected = _rint64(w.line_billed * rate[w.line_claim])
    w.line_allowed = w.line_expected.copy()
    w.billed_total = np.bincount(w.line_claim, weights=w.line_billed, minlength=n).astype(np.int64)
    w.expected_total = np.bincount(w.line_claim, weights=w.line_expected, minlength=n).astype(np.int64)

    # --- Stage 8: submission ---------------------------------------------------
    bill_ready = np.where(w.is_institutional, w.discharge_day, w.svc_day)
    sub_lag = _rint64(rng.lognormal(np.log(5.0), 0.6, size=n))
    sub_lag = np.clip(sub_lag + np.where(w.is_institutional, 3, 0), 1, 45)
    w.sub_day = bill_ready + sub_lag
    never_sub = rng.random(n) < config.never_submitted_frac
    late_lag = rng.integers(s.s5_carc29_late_min_days, s.s5_carc29_late_max_days + 1, size=n)
    atlas = dims.payer_index[s.s3a_payer]
    u_defer = rng.random(n)
    w.sub_day[never_sub] = NEVER
    w.sub_day[cluster] = NEVER  # scenario 5: still unsubmitted at snap_003
    w.sub_day[carc29] = w.svc_day[carc29] + late_lag[carc29]  # scenario 5: filed late
    defer = (w.payer_i == atlas) & (w.sub_day != NEVER) & (w.sub_day >= s.s3a_start_day)
    defer &= u_defer < s.s3a_drop_frac
    w.sub_day = np.where(defer, w.sub_day + s.s3a_defer_days, w.sub_day)  # scenario 3a

    # Charges must be entered before a claim can go out the door.
    submitted_line = w.sub_day[w.line_claim] != NEVER
    w.line_charge_day = np.where(
        submitted_line, np.minimum(w.line_charge_day, w.sub_day[w.line_claim]), w.line_charge_day
    )

    # --- Stage 9: first remit (adjudication lag) -------------------------------
    adj_mu = np.array([p.adj_lag_mean for p in PAYERS])[w.payer_i]
    adj_sd = np.array([p.adj_lag_sd for p in PAYERS])[w.payer_i]
    adj_lag = np.clip(_rint64(rng.normal(adj_mu, adj_sd)), 3, 60)
    w.remit1_day = np.where(w.sub_day == NEVER, NEVER, w.sub_day + adj_lag).astype(np.int64)

    # --- Stage 10: scenario 4 (Northbridge ORTHO-SURG allowed at 92%) ----------
    northbridge = dims.payer_index[s.s4_payer]
    ortho = PROC_GROUP_INDEX[s.s4_proc_group]
    s4_mask = (
        (w.payer_i[w.line_claim] == northbridge)
        & (w.line_group_i == ortho)
        & (w.remit1_day[w.line_claim] != NEVER)
        & (w.remit1_day[w.line_claim] >= s.s4_start_day)
    )
    w.line_allowed = np.where(s4_mask, _rint64(w.line_expected * s.s4_factor), w.line_allowed)
    w.allowed_total = np.bincount(w.line_claim, weights=w.line_allowed, minlength=n).astype(np.int64)

    # --- Stage 11: denial draws ------------------------------------------------
    has_remit = w.remit1_day != NEVER
    meridian = dims.payer_index[s.s1_payer]
    imaging = dims.service_line_index[s.s1_service_line]
    mer_img = (w.payer_i == meridian) & (w.svcline_i == imaging)
    p197 = np.where(
        mer_img & has_remit, np.where(w.remit1_day >= s.s1_break_day, s.s1_post_prob, s.s1_pre_prob), 0.0
    )
    base_p = np.where(has_remit, np.array([p.denial_prob for p in PAYERS])[w.payer_i], 0.0)
    u_den = rng.random(n)
    denied_197 = u_den < p197
    denied_gen = ~denied_197 & (u_den < p197 + base_p)
    carcs = np.array([c for c, _wt in GENERIC_CARC_MIX])
    carc_w = np.array([wt for _c, wt in GENERIC_CARC_MIX])
    gen_carc = rng.choice(carcs, size=n, p=carc_w / carc_w.sum())
    gen_carc = np.where(mer_img & (gen_carc == 197), 16, gen_carc)  # 197 in that cell is scenario-owned
    w.denied = denied_197 | denied_gen
    w.den_carc = np.where(denied_197, 197, gen_carc).astype(np.int64)
    # Forced scenario denials override the generic draw.
    cob_denied = cob_set & has_remit
    w.denied |= cob_denied
    w.den_carc[cob_denied] = 22
    carc29_denied = carc29 & has_remit
    w.denied |= carc29_denied
    w.den_carc[carc29_denied] = 29
    w.den_carc[~w.denied] = 0
    forced = denied_197 | cob_denied | carc29_denied
    u_level = rng.random(n)
    # Line-level denials only make sense with at least two lines: a single-line
    # "partial" denial would leave a paid-nothing claim with a contractual posting.
    w.den_level_line = w.denied & ~forced & (u_level < 0.40) & (w.n_lines_per_claim >= 2)
    u_pick = rng.random(n)
    pick_k = np.floor(u_pick * w.n_lines_per_claim).astype(np.int64)
    w.den_line_pos = np.where(w.den_level_line, w.line_start + pick_k, -1)
    denied_line_allowed = np.where(w.den_level_line, w.line_allowed[np.maximum(w.den_line_pos, 0)], 0)
    denied_line_billed = np.where(w.den_level_line, w.line_billed[np.maximum(w.den_line_pos, 0)], 0)
    w.den_amount = np.where(w.den_level_line, denied_line_billed, w.billed_total)
    w.den_amount = np.where(w.denied, w.den_amount, 0)
    u_rarc = rng.random(n)
    rarc_pick = rng.integers(1, 21, size=n)
    w.den_rarc = np.where(w.denied & (u_rarc < 0.40), rarc_pick, -1)
    group_map = np.zeros(300, dtype=np.int64)  # default CO=0
    group_names = ("CO", "PR", "OA", "PI")
    for carc, grp in CARC_GROUP.items():
        group_map[carc] = group_names.index(grp)
    u_pi = rng.random(n)
    w.den_group = group_map[w.den_carc]
    make_pi = w.denied & (w.den_group == 0) & ~forced & (u_pi < 0.08)
    w.den_group = np.where(make_pi, group_names.index("PI"), w.den_group)

    # --- Stage 12: appeals, second remits, write-offs --------------------------
    appealable = w.denied & ~cob_denied & ~carc29_denied
    w.appealed = appealable & (rng.random(n) < config.appeal_frac)
    w.appeal_file_day = np.where(w.appealed, w.remit1_day + rng.integers(5, 31, size=n), NEVER)
    w.appeal_dec_day = np.where(w.appealed, w.appeal_file_day + rng.integers(20, 61, size=n), NEVER)
    w.overturned = w.appealed & (rng.random(n) < config.overturn_frac)
    rebill_gap = rng.integers(s.s2_rebill_min_days, s.s2_rebill_max_days + 1, size=n)
    ot_gap = rng.integers(5, 16, size=n)
    w.remit2_day = np.full(n, NEVER, dtype=np.int64)
    w.remit2_day[cob_denied] = w.remit1_day[cob_denied] + rebill_gap[cob_denied]  # scenario 2 rebill
    w.remit2_day[w.overturned] = w.appeal_dec_day[w.overturned] + ot_gap[w.overturned]
    upheld = w.appealed & ~w.overturned
    wo_upheld = rng.integers(5, 21, size=n)
    wo_unappealed = rng.integers(30, 91, size=n)
    w.writeoff_day = np.full(n, NEVER, dtype=np.int64)
    w.writeoff_day[upheld] = w.appeal_dec_day[upheld] + wo_upheld[upheld]
    never_appealed = w.denied & ~w.appealed & ~cob_denied
    w.writeoff_day[never_appealed] = w.remit1_day[never_appealed] + wo_unappealed[never_appealed]

    # --- Stage 13: patient responsibility --------------------------------------
    pr_frac = np.array([p.patient_resp_frac for p in PAYERS])[w.payer_i]
    pr_jitter = 0.7 + 0.6 * rng.random(n)
    claim_denied = w.denied & ~w.den_level_line
    pays_at_remit1 = has_remit & ~claim_denied
    pays_at_remit2 = w.remit2_day != NEVER
    paid_allowed = np.where(pays_at_remit1, w.allowed_total - denied_line_allowed, w.allowed_total)
    ever_paid = pays_at_remit1 | pays_at_remit2
    w.pr_amount = np.where(ever_paid, _rint64(paid_allowed * pr_frac * pr_jitter), 0)
    w.pr_known_day = np.where(
        pays_at_remit1, w.remit1_day, np.where(pays_at_remit2, w.remit2_day, NEVER)
    ).astype(np.int64)

    # --- Stage 14: posting lags (scenario 3b stretches State Medicaid) ---------
    post_mu = np.array([p.post_lag_mean for p in PAYERS])[w.payer_i]
    post_sd = np.array([p.post_lag_sd for p in PAYERS])[w.payer_i]
    lag1 = np.clip(_rint64(rng.normal(post_mu, post_sd)), 1, 15)
    lag2 = np.clip(_rint64(rng.normal(post_mu, post_sd)), 1, 15)
    is_sm = w.payer_i == sm_payer
    lag1 = lag1 + np.where(is_sm & (w.remit1_day >= s.s3b_remit_start_day), s.s3b_extra_lag_days, 0)
    stretch2 = is_sm & (w.remit2_day >= s.s3b_remit_start_day) & pays_at_remit2
    lag2 = lag2 + np.where(stretch2, s.s3b_extra_lag_days, 0)
    post1 = np.where(pays_at_remit1, w.remit1_day + lag1, NEVER)
    post2 = np.where(pays_at_remit2, w.remit2_day + lag2, NEVER)

    # --- Stage 15: transaction assembly ----------------------------------------
    payment1 = np.where(pays_at_remit1, np.maximum(paid_allowed - w.pr_amount, 0), 0)
    contractual1 = np.where(pays_at_remit1, w.billed_total - w.allowed_total, 0)
    pay2_amt = np.zeros(n, dtype=np.int64)
    pay2_amt[cob_denied] = np.maximum(w.allowed_total - w.pr_amount, 0)[cob_denied]
    ot_claim = w.overturned & ~w.den_level_line
    ot_line = w.overturned & w.den_level_line
    pay2_amt[ot_claim] = np.maximum(w.allowed_total - w.pr_amount, 0)[ot_claim]
    pay2_amt[ot_line] = denied_line_allowed[ot_line]
    contractual2 = np.zeros(n, dtype=np.int64)
    pays_full_at_2 = cob_denied | ot_claim
    contractual2[pays_full_at_2] = (w.billed_total - w.allowed_total)[pays_full_at_2]
    wo_amount = np.zeros(n, dtype=np.int64)
    has_wo = w.writeoff_day != NEVER
    wo_amount[has_wo & w.den_level_line] = denied_line_allowed[has_wo & w.den_level_line]
    wo_amount[has_wo & ~w.den_level_line] = w.billed_total[has_wo & ~w.den_level_line]

    u_collect = rng.random(n)
    collect_delay = rng.integers(10, 46, size=n)
    first_pay_post = np.where(payment1 > 0, post1, np.where(pay2_amt > 0, post2, NEVER))
    pay_post_for_patient = np.where(payment1 > 0, post1, post2)
    collects = (w.pr_amount > 0) & (first_pay_post != NEVER) & (u_collect < config.patient_collect_frac)
    patient_day = np.where(collects, pay_post_for_patient + collect_delay, NEVER)

    u_refund = rng.random(n)
    dup_delay = rng.integers(7, 22, size=n)
    refund_delay = rng.integers(20, 61, size=n)
    dup_mask = pays_at_remit1 & ~w.denied & (u_refund < config.refund_frac) & (payment1 > 0)
    dup_amt = np.where(dup_mask, _rint64(payment1 * 0.10), 0)
    dup_day = np.where(dup_mask, post1 + dup_delay, NEVER)
    refund_day = np.where(dup_mask, dup_day + refund_delay, NEVER)

    events: list[tuple[np.ndarray, ...]] = []

    def add_events(
        mask: np.ndarray,
        line_pos: np.ndarray | None,
        seq: int,
        type_code: int,
        amount: np.ndarray,
        post_day: np.ndarray,
        remit_attached: bool,
    ) -> None:
        keep = mask & (amount > 0) & (post_day != NEVER)
        idx = np.flatnonzero(keep)
        rd = (w.remit1_day if seq == 1 else w.remit2_day)[idx] if remit_attached else np.full(len(idx), NEVER)
        lp = line_pos[idx] if line_pos is not None else np.full(len(idx), -1)
        events.append(
            (
                idx,
                lp.astype(np.int64),
                np.full(len(idx), seq if remit_attached else 0, dtype=np.int64),
                np.full(len(idx), type_code, dtype=np.int64),
                amount[idx].astype(np.int64),
                post_day[idx].astype(np.int64),
                rd.astype(np.int64),
            )
        )

    add_events(pays_at_remit1, None, 1, 0, payment1, post1, True)
    add_events(pays_at_remit1, None, 1, 1, contractual1, post1, True)
    add_events(pays_at_remit2, None, 2, 0, pay2_amt, post2, True)
    add_events(pays_full_at_2, None, 2, 1, contractual2, post2, True)
    wo_line_pos = np.where(w.den_level_line, w.den_line_pos, -1)
    add_events(has_wo, wo_line_pos, 0, 2, wo_amount, w.writeoff_day, False)
    add_events(collects, None, 0, 3, w.pr_amount, patient_day, False)
    add_events(dup_mask, None, 1, 0, dup_amt, dup_day, True)
    add_events(dup_mask, None, 0, 4, dup_amt, refund_day, False)

    t_claim = np.concatenate([e[0] for e in events])
    t_line = np.concatenate([e[1] for e in events])
    t_seq = np.concatenate([e[2] for e in events])
    t_type = np.concatenate([e[3] for e in events])
    t_amount = np.concatenate([e[4] for e in events])
    t_post = np.concatenate([e[5] for e in events])
    t_remit_day = np.concatenate([e[6] for e in events])
    order = np.lexsort((t_amount, t_type, t_claim, t_post))
    w.txn_claim = t_claim[order]
    w.txn_line = t_line[order]
    t_seq = t_seq[order]
    w.txn_type = t_type[order]
    w.txn_amount = t_amount[order]
    w.txn_post_day = t_post[order]
    w.txn_remit_day = t_remit_day[order]

    # --- Stage 16: remit rows ---------------------------------------------------
    r1 = np.flatnonzero(w.remit1_day != NEVER)
    r2 = np.flatnonzero(w.remit2_day != NEVER)
    r_claim = np.concatenate([r1, r2])
    r_seq = np.concatenate([np.full(len(r1), 1), np.full(len(r2), 2)]).astype(np.int64)
    r_day = np.concatenate([w.remit1_day[r1], w.remit2_day[r2]])
    r_order = np.lexsort((r_seq, r_claim, r_day))
    w.remit_claim = r_claim[r_order]
    w.remit_seq = r_seq[r_order]
    w.remit_day = r_day[r_order]
    w.remit_pos1 = np.full(n, -1, dtype=np.int64)
    w.remit_pos2 = np.full(n, -1, dtype=np.int64)
    pos = np.arange(len(r_order))
    seq1 = w.remit_seq == 1
    w.remit_pos1[w.remit_claim[seq1]] = pos[seq1]
    w.remit_pos2[w.remit_claim[~seq1]] = pos[~seq1]
    w.txn_remit = np.where(
        t_seq == 1, w.remit_pos1[w.txn_claim], np.where(t_seq == 2, w.remit_pos2[w.txn_claim], -1)
    )

    # --- Stage 17: denial rows --------------------------------------------------
    d_idx = np.flatnonzero(w.denied)
    d_order = d_idx[np.lexsort((d_idx, w.remit1_day[d_idx]))]
    w.dn_claim = d_order
    w.dn_line = w.den_line_pos[d_order]
    w.dn_remit = w.remit_pos1[d_order]
    w.dn_group = w.den_group[d_order]
    w.dn_carc = w.den_carc[d_order]
    w.dn_rarc = w.den_rarc[d_order]
    w.dn_level_line = w.den_level_line[d_order]
    w.dn_day = w.remit1_day[d_order]
    w.dn_amount = w.den_amount[d_order]
    w.dn_appealed = w.appealed[d_order]
    w.dn_file_day = w.appeal_file_day[d_order]
    w.dn_dec_day = w.appeal_dec_day[d_order]
    w.dn_overturned = w.overturned[d_order]

    # --- Stage 18: claim outcome fields ----------------------------------------
    w.first_pay_post = first_pay_post.astype(np.int64)
    w.fpp = pays_at_remit1 & ~w.denied
    line_resolution = np.where(ot_line, post2, np.where(w.den_level_line, w.writeoff_day, 0))
    resolved = np.full(n, NEVER, dtype=np.int64)
    path_a = pays_at_remit1 & ~w.denied
    resolved[path_a] = post1[path_a]
    resolved[dup_mask] = refund_day[dup_mask]
    path_b = w.den_level_line
    resolved[path_b] = np.maximum(post1[path_b], line_resolution[path_b])
    path_c_paid = claim_denied & (pay2_amt > 0)
    resolved[path_c_paid] = post2[path_c_paid]
    path_c_wo = claim_denied & has_wo
    resolved[path_c_wo] = w.writeoff_day[path_c_wo]
    resolved[~has_remit] = NEVER
    still_open = claim_denied & (pay2_amt == 0) & ~has_wo
    resolved[still_open] = NEVER
    w.resolved_day = resolved

    return w
