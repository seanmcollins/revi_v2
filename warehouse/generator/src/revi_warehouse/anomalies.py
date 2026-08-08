"""Declarative anomaly injection + per-snapshot detection.

Two halves, both driven by ``ANOMALY_SPECS`` (config.py):

1. ``inject_anomalies`` — ONE vectorized pass that appends spec-generated
   claims (with their lines, remits, transactions and denials) to the
   already-built base ``World``. Base arrays are never mutated, so the five
   original scenario aggregates cannot move; ``_enforce_guards`` raises if any
   appended row could intersect a scenario cell/window or the reference-week
   cash totals.

2. ``write_detected_anomalies`` — persists ``<snap>.detected_anomalies`` as if
   an external detection system ran at load time. Every impact and evidence
   figure is recomputed with SQL from that snapshot's visible data; a spec is
   emitted in a snapshot iff the snapshot shows >= ``min_events`` qualifying
   events and the recomputed impact clears ``min_impact_cents`` (self-resolved
   signals therefore simply disappear from later snapshots). Severity and
   confidence follow the documented rules in ``severity_for``/``confidence_for``.

Determinism: each spec draws from its own PCG64 stream seeded by
``(REVI_SEED, ANOMALY_STREAM, crc32(spec_id))`` — base-world draw sequences are
never consumed, and detection is pure SQL over deterministic data.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from typing import Any

import duckdb
import numpy as np

from revi_warehouse.config import (
    ANOMALY_SPECS,
    ANOMALY_STREAM,
    NEVER,
    REVI_SEED,
    SCENARIOS,
    AnomalySpec,
    GeneratorConfig,
    SnapshotSpec,
    day,
)
from revi_warehouse.dims import FACILITIES, PAYERS, PLANS, SERVICE_LINES, Dims
from revi_warehouse.world import PROC_GROUP_INDEX, World

_GROUP_CO = 0  # ("CO", "PR", "OA", "PI") — injected CARCs are all CO-group

_CLAIM_FIELDS = (
    "svc_day", "discharge_day", "patient_i", "payer_i", "plan_i", "provider_i",
    "facility_i", "svcline_i", "is_institutional", "pseq", "oins", "cobm",
    "sub_day", "remit1_day", "remit2_day", "denied", "den_carc", "den_group",
    "den_level_line", "den_line_pos", "den_amount", "den_rarc", "appealed",
    "appeal_file_day", "appeal_dec_day", "overturned", "writeoff_day",
    "billed_total", "allowed_total", "expected_total", "pr_amount",
    "pr_known_day", "first_pay_post", "resolved_day", "fpp", "n_lines_per_claim",
)
_LINE_FIELDS = (
    "line_claim", "line_num", "line_group_i", "line_code_i", "line_units",
    "line_svc_day", "line_charge_day", "line_billed", "line_allowed", "line_expected",
)

_REFERENCE_CASH_START = SCENARIOS.s3_week_prior_start  # 2026-07-20
_REFERENCE_CASH_END = SCENARIOS.s3_week_decline_end  # 2026-08-02
_PLAN_PAYER = {p[1]: p[0] for p in PLANS}


def _rint64(x: np.ndarray) -> np.ndarray:
    return np.rint(x).astype(np.int64)


def _spec_rng(spec: AnomalySpec) -> np.random.Generator:
    seq = np.random.SeedSequence((REVI_SEED, ANOMALY_STREAM, zlib.crc32(spec.spec_id.encode("ascii"))))
    return np.random.Generator(np.random.PCG64(seq))


def _spread(d0: int, d1: int, n: int) -> np.ndarray:
    """n event days evenly spread across [d0, d1] inclusive (deterministic)."""
    return _rint64(np.linspace(d0, d1, n))


def spec_payer_name(spec: AnomalySpec) -> str:
    if spec.payer is not None:
        return spec.payer
    assert spec.plan is not None
    return _PLAN_PAYER[spec.plan]


# ---------------------------------------------------------------------------
# injection: build one block of claims per spec, then append all blocks once


@dataclass
class _Block:
    """Everything one spec appends: claim arrays plus event tuples."""

    claims: dict[str, np.ndarray]
    lines: dict[str, np.ndarray]
    # (local claim, seq, day)
    remits: list[tuple[int, int, int]] = field(default_factory=list)
    # (local claim, seq, type_code, amount, post_day)
    txns: list[tuple[int, int, int, int, int]] = field(default_factory=list)
    # (local claim, day, carc, group_i, amount, appealed, file_day, dec_day, overturned)
    denials: list[tuple[int, int, int, int, int, bool, int, int, bool]] = field(default_factory=list)


def _claim_defaults(n: int) -> dict[str, np.ndarray]:
    never = np.full(n, NEVER, dtype=np.int64)
    zeros = np.zeros(n, dtype=np.int64)
    false = np.zeros(n, dtype=bool)
    return {
        "svc_day": zeros.copy(), "discharge_day": never.copy(),
        "patient_i": zeros.copy(), "payer_i": zeros.copy(), "plan_i": zeros.copy(),
        "provider_i": zeros.copy(), "facility_i": zeros.copy(), "svcline_i": zeros.copy(),
        "is_institutional": false.copy(), "pseq": zeros.copy(),
        "oins": false.copy(), "cobm": false.copy(),
        "sub_day": never.copy(), "remit1_day": never.copy(), "remit2_day": never.copy(),
        "denied": false.copy(), "den_carc": zeros.copy(),
        "den_group": zeros.copy(), "den_level_line": false.copy(),
        "den_line_pos": np.full(n, -1, dtype=np.int64), "den_amount": zeros.copy(),
        "den_rarc": np.full(n, -1, dtype=np.int64), "appealed": false.copy(),
        "appeal_file_day": never.copy(), "appeal_dec_day": never.copy(),
        "overturned": false.copy(), "writeoff_day": never.copy(),
        "billed_total": zeros.copy(), "allowed_total": zeros.copy(),
        "expected_total": zeros.copy(), "pr_amount": zeros.copy(),
        "pr_known_day": never.copy(), "first_pay_post": never.copy(),
        "resolved_day": never.copy(), "fpp": false.copy(),
        "n_lines_per_claim": np.ones(n, dtype=np.int64),
    }


def _fill_cell(
    c: dict[str, np.ndarray], spec: AnomalySpec, config: GeneratorConfig,
    dims: Dims, rng: np.random.Generator, n: int,
) -> None:
    if spec.plan is not None:
        plan_i = dims.plan_index[spec.plan]
        payer_i = int(dims.plan_payer_idx[plan_i])
        c["plan_i"][:] = plan_i
        c["payer_i"][:] = payer_i
    else:
        assert spec.payer is not None
        payer_i = dims.payer_index[spec.payer]
        idx, weights = dims.plans_by_payer[payer_i]
        c["payer_i"][:] = payer_i
        c["plan_i"][:] = rng.choice(idx, size=n, p=weights)
    sl_i = dims.service_line_index[spec.service_line]
    c["svcline_i"][:] = sl_i
    if spec.facility is not None:
        c["facility_i"][:] = dims.facility_index[spec.facility]
    else:
        fac_w = np.array([f[2] for f in FACILITIES])
        c["facility_i"][:] = rng.choice(len(FACILITIES), size=n, p=fac_w / fac_w.sum())
    c["provider_i"][:] = rng.choice(dims.providers_by_service_line[sl_i], size=n)
    c["patient_i"][:] = rng.integers(0, config.n_patients, size=n)


def _make_lines(
    spec: AnomalySpec, rng: np.random.Generator, n: int, payer_i: int,
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build lines for n claims. Returns (line dict, billed/expected/allowed totals, n_lines)."""
    base = spec.billed_total_cents / n
    billed = np.maximum(_rint64(base * np.exp(rng.normal(0.0, 0.25, n))), 2_000)
    rate_target = spec.rate_override if spec.rate_override is not None else PAYERS[payer_i].contract_rate
    rate = np.clip(rate_target + rng.normal(0.0, 0.01, n), 0.2, 0.8)
    k = np.where(billed < 8_000, 1, rng.integers(1, 4, size=n)).astype(np.int64)
    m = int(k.sum())
    starts = np.concatenate(([0], np.cumsum(k)[:-1]))
    line_claim = np.repeat(np.arange(n), k)
    line_num = (np.arange(m) - starts[line_claim] + 1).astype(np.int64)
    weights = rng.random(m) + 0.25
    wsum = np.add.reduceat(weights, starts)
    share = np.floor(billed[line_claim] * weights / wsum[line_claim]).astype(np.int64)
    # give each claim's rounding remainder to its first line
    per_claim = np.add.reduceat(share, starts)
    share[starts] += billed - per_claim
    line_billed = np.maximum(share, 1)
    if spec.proc_group is not None:
        group_i = np.full(m, PROC_GROUP_INDEX[spec.proc_group], dtype=np.int64)
    else:
        sl = SERVICE_LINES[[s[0] for s in SERVICE_LINES].index(spec.service_line)]
        groups = np.array([PROC_GROUP_INDEX[g] for g, _w in sl[4]])
        gw = np.array([w for _g, w in sl[4]])
        group_i = rng.choice(groups, size=m, p=gw / gw.sum())
    line_expected = _rint64(line_billed * rate[line_claim])
    factor = spec.allowed_factor if spec.allowed_factor is not None else 1.0
    line_allowed = _rint64(line_expected * factor)
    lines = {
        "line_claim": line_claim.astype(np.int64),
        "line_num": line_num,
        "line_group_i": group_i,
        "line_code_i": rng.integers(0, 6, size=m).astype(np.int64),
        "line_units": rng.integers(1, 4, size=m).astype(np.int64),
        "line_svc_day": np.zeros(m, dtype=np.int64),  # filled by the category builder
        "line_charge_day": np.zeros(m, dtype=np.int64),
        "line_billed": line_billed,
        "line_allowed": line_allowed,
        "line_expected": line_expected,
    }
    billed_total = np.bincount(line_claim, weights=line_billed, minlength=n).astype(np.int64)
    expected_total = np.bincount(line_claim, weights=line_expected, minlength=n).astype(np.int64)
    allowed_total = np.bincount(line_claim, weights=line_allowed, minlength=n).astype(np.int64)
    return lines, billed_total, expected_total, allowed_total, k


def _default_line_dates(
    blk: _Block, rng: np.random.Generator, lag_min: int, lag_max: int,
) -> None:
    """Line service = claim service; charge entry = service + small lag, clamped to submission."""
    c = blk.claims
    line_claim = blk.lines["line_claim"]
    m = len(line_claim)
    blk.lines["line_svc_day"] = c["svc_day"][line_claim]
    lag = rng.integers(lag_min, lag_max + 1, size=m)
    charge = blk.lines["line_svc_day"] + lag
    sub = c["sub_day"][line_claim]
    blk.lines["line_charge_day"] = np.where(sub != NEVER, np.minimum(charge, sub), charge)


def _add_denials(
    blk: _Block, spec: AnomalySpec, rng: np.random.Generator, which: np.ndarray,
    den_day: np.ndarray, appealed_frac: float,
) -> None:
    c = blk.claims
    n_all = len(c["svc_day"])
    c["denied"][which] = True
    c["den_carc"][which] = spec.carc or 0
    c["den_group"][which] = _GROUP_CO
    c["den_amount"][which] = c["billed_total"][which]
    n_app = round(appealed_frac * len(which))
    appealed_pick = (
        rng.choice(which, size=n_app, replace=False) if n_app else np.empty(0, dtype=np.int64)
    )
    file_day = np.full(n_all, NEVER, dtype=np.int64)
    file_day[appealed_pick] = den_day[appealed_pick] + rng.integers(5, 21, size=n_all)[appealed_pick]
    c["appealed"][appealed_pick] = True
    c["appeal_file_day"][appealed_pick] = file_day[appealed_pick]
    for i in which:
        li = int(i)
        blk.denials.append(
            (li, int(den_day[li]), spec.carc or 0, _GROUP_CO, int(c["billed_total"][li]),
             bool(c["appealed"][li]), int(c["appeal_file_day"][li]),
             int(c["appeal_dec_day"][li]), bool(c["overturned"][li]))
        )


def _add_paid(
    blk: _Block, which: np.ndarray, seq: int, post_day: np.ndarray,
) -> None:
    """PAYMENT (allowed) + CONTRACTUAL_ADJ (billed - allowed) at post_day, remit-attached."""
    c = blk.claims
    for i in which:
        li = int(i)
        pay = int(c["allowed_total"][li])
        adj = int(c["billed_total"][li] - c["allowed_total"][li])
        if pay > 0:
            blk.txns.append((li, seq, 0, pay, int(post_day[li])))
        if adj > 0:
            blk.txns.append((li, seq, 1, adj, int(post_day[li])))


def _build_block(spec: AnomalySpec, config: GeneratorConfig, dims: Dims) -> _Block:
    rng = _spec_rng(spec)
    cat = spec.category
    n = spec.n_claims
    if cat == "duplicate":
        return _build_duplicate(spec, config, dims, rng)
    total = n + spec.prior_n
    c = _claim_defaults(total)
    _fill_cell(c, spec, config, dims, rng, total)
    payer_i = int(c["payer_i"][0])
    lines, billed, expected, allowed, k = _make_lines(spec, rng, total, payer_i)
    c["billed_total"], c["expected_total"], c["allowed_total"] = billed, expected, allowed
    c["n_lines_per_claim"] = k
    blk = _Block(claims=c, lines=lines)
    w0, w1 = day(spec.onset), day(spec.window_end)
    events = _spread(w0, w1, n)
    cur = np.arange(n)

    if cat in ("denial_spike", "eligibility_cluster", "unworked_denials"):
        c["remit1_day"][cur] = events
        c["sub_day"][cur] = events - rng.integers(10, 26, size=total)[cur]
        c["svc_day"][cur] = c["sub_day"][cur] - rng.integers(3, 16, size=total)[cur]
        _prior_episode(blk, spec, rng, total)
        _default_line_dates(blk, rng, 0, 4)
        _add_denials(blk, spec, rng, cur, c["remit1_day"], spec.appealed_frac)
        for i in range(n):  # prior-episode claims already added their remits
            blk.remits.append((i, 1, int(c["remit1_day"][i])))
    elif cat in ("underpayment", "contractual"):
        c["remit1_day"][cur] = events
        c["sub_day"][cur] = events - rng.integers(10, 21, size=n)
        c["svc_day"][cur] = c["sub_day"][cur] - rng.integers(2, 9, size=n)
        post = events + rng.integers(2, 7, size=n)
        c["first_pay_post"][cur] = post
        c["resolved_day"][cur] = post
        c["fpp"][cur] = True
        _default_line_dates(blk, rng, 0, 4)
        _add_paid(blk, cur, 1, c["first_pay_post"])
        for i in range(n):
            blk.remits.append((i, 1, int(c["remit1_day"][i])))
    elif cat == "posting_lag":
        assert spec.post_lag_days is not None
        c["remit1_day"][cur] = events
        c["sub_day"][cur] = events - rng.integers(10, 21, size=n)
        c["svc_day"][cur] = c["sub_day"][cur] - rng.integers(2, 9, size=n)
        post = events + spec.post_lag_days + rng.integers(0, 7, size=n)
        c["first_pay_post"][cur] = post
        c["resolved_day"][cur] = post
        c["fpp"][cur] = True
        _default_line_dates(blk, rng, 0, 4)
        _add_paid(blk, cur, 1, c["first_pay_post"])
        for i in range(n):
            blk.remits.append((i, 1, int(c["remit1_day"][i])))
    elif cat in ("submission_gap", "timely_filing"):
        c["svc_day"][cur] = events
        if spec.resolve_submit_on is not None:
            c["sub_day"][cur] = day(spec.resolve_submit_on)
        _default_line_dates(blk, rng, 0, 5)
    elif cat == "dnfb":
        c["is_institutional"][cur] = True
        c["discharge_day"][cur] = events
        los = rng.integers(1, 8, size=n)
        c["svc_day"][cur] = events - los
        if spec.resolve_submit_on is not None:
            c["sub_day"][cur] = day(spec.resolve_submit_on)
        _default_line_dates(blk, rng, 0, 5)
    elif cat == "credit_balance":
        assert spec.overpay_frac is not None
        dup_post = events
        post1 = dup_post - rng.integers(5, 13, size=n)
        c["remit1_day"][cur] = post1 - rng.integers(2, 6, size=n)
        c["sub_day"][cur] = c["remit1_day"][cur] - rng.integers(10, 21, size=n)
        c["svc_day"][cur] = c["sub_day"][cur] - rng.integers(2, 9, size=n)
        c["first_pay_post"][cur] = post1
        c["fpp"][cur] = True  # paid clean; the open credit keeps resolved_day = NEVER
        _default_line_dates(blk, rng, 0, 4)
        _add_paid(blk, cur, 1, c["first_pay_post"])
        dup_amt = np.maximum(_rint64(c["allowed_total"][cur] * spec.overpay_frac), 100)
        for i in range(n):
            blk.txns.append((i, 1, 0, int(dup_amt[i]), int(dup_post[i])))
        for i in range(n):
            blk.remits.append((i, 1, int(c["remit1_day"][i])))
    elif cat == "charge_entry_lag":
        charge = events
        lag = rng.integers(spec.charge_lag_min, spec.charge_lag_max + 1, size=n)
        c["svc_day"][cur] = charge - lag
        c["sub_day"][cur] = charge + rng.integers(1, 4, size=n)
        line_claim = lines["line_claim"]
        lines["line_svc_day"] = c["svc_day"][line_claim]
        lines["line_charge_day"] = charge[line_claim]
    elif cat == "charge_hold":
        assert spec.resolve_submit_on is not None
        hold = day(spec.resolve_submit_on)
        c["svc_day"][cur] = events
        c["sub_day"][cur] = hold
        line_claim = lines["line_claim"]
        lines["line_svc_day"] = c["svc_day"][line_claim]
        lines["line_charge_day"] = np.full(len(line_claim), hold, dtype=np.int64)
    else:  # pragma: no cover - spec table is validated by tests
        raise ValueError(f"unknown anomaly category {cat!r}")
    return blk


def _prior_episode(blk: _Block, spec: AnomalySpec, rng: np.random.Generator, total: int) -> None:
    """Resolved-then-recurred: a prior denial burst that was appealed, overturned and paid."""
    if spec.prior_n == 0:
        return
    c = blk.claims
    prior = np.arange(spec.n_claims, total)
    p0, p1 = day(spec.prior_onset or spec.onset), day(spec.prior_end or spec.window_end)
    events = _spread(p0, p1, spec.prior_n)
    c["remit1_day"][prior] = events
    c["sub_day"][prior] = events - rng.integers(10, 26, size=spec.prior_n)
    c["svc_day"][prior] = c["sub_day"][prior] - rng.integers(3, 16, size=spec.prior_n)
    c["appealed"][prior] = True
    c["appeal_file_day"][prior] = events + rng.integers(8, 16, size=spec.prior_n)
    c["appeal_dec_day"][prior] = c["appeal_file_day"][prior] + rng.integers(20, 31, size=spec.prior_n)
    c["overturned"][prior] = True
    c["remit2_day"][prior] = c["appeal_dec_day"][prior] + rng.integers(5, 11, size=spec.prior_n)
    post2 = c["remit2_day"][prior] + rng.integers(2, 7, size=spec.prior_n)
    c["first_pay_post"][prior] = post2
    c["resolved_day"][prior] = post2
    c["denied"][prior] = True
    c["den_carc"][prior] = spec.carc or 0
    c["den_group"][prior] = _GROUP_CO
    c["den_amount"][prior] = c["billed_total"][prior]
    for j, i in enumerate(prior):
        li = int(i)
        blk.denials.append(
            (li, int(events[j]), spec.carc or 0, _GROUP_CO, int(c["billed_total"][li]),
             True, int(c["appeal_file_day"][li]), int(c["appeal_dec_day"][li]), True)
        )
        blk.remits.append((li, 1, int(events[j])))
        blk.remits.append((li, 2, int(c["remit2_day"][li])))
    _add_paid(blk, prior, 2, c["first_pay_post"])


def _build_duplicate(
    spec: AnomalySpec, config: GeneratorConfig, dims: Dims, rng: np.random.Generator,
) -> _Block:
    """n originals (paid weeks earlier) + n duplicates (denied CARC 18 in the window)."""
    n = spec.n_claims
    total = 2 * n
    c = _claim_defaults(total)
    _fill_cell(c, spec, config, dims, rng, total)
    payer_i = int(c["payer_i"][0])
    lines, billed, expected, allowed, k = _make_lines(spec, rng, n, payer_i)
    # duplicates mirror the original's dollars, lines and patient exactly
    dup_lines = {key: val.copy() for key, val in lines.items()}
    dup_lines["line_claim"] = dup_lines["line_claim"] + n
    merged = {key: np.concatenate([lines[key], dup_lines[key]]) for key in lines}
    c["billed_total"] = np.concatenate([billed, billed])
    c["expected_total"] = np.concatenate([expected, expected])
    c["allowed_total"] = np.concatenate([allowed, allowed])
    c["n_lines_per_claim"] = np.concatenate([k, k])
    c["patient_i"][n:] = c["patient_i"][:n]
    c["plan_i"][n:] = c["plan_i"][:n]
    c["facility_i"][n:] = c["facility_i"][:n]
    c["provider_i"][n:] = c["provider_i"][:n]
    blk = _Block(claims=c, lines=merged)
    orig, dup = np.arange(n), np.arange(n, total)
    dup_den = _spread(day(spec.onset), day(spec.window_end), n)
    orig_remit = dup_den - rng.integers(35, 46, size=n)
    orig_sub = orig_remit - rng.integers(10, 21, size=n)
    svc = orig_sub - rng.integers(2, 9, size=n)
    c["svc_day"][orig] = svc
    c["svc_day"][dup] = svc  # same service, rebilled
    c["sub_day"][orig] = orig_sub
    c["sub_day"][dup] = dup_den - rng.integers(8, 15, size=n)
    c["remit1_day"][orig] = orig_remit
    c["remit1_day"][dup] = dup_den
    post = orig_remit + rng.integers(2, 7, size=n)
    c["first_pay_post"][orig] = post
    c["resolved_day"][orig] = post
    c["fpp"][orig] = True
    _default_line_dates(blk, rng, 0, 4)
    _add_paid(blk, orig, 1, c["first_pay_post"])
    _add_denials(blk, spec, rng, dup, c["remit1_day"], appealed_frac=0.0)
    for i in range(total):
        blk.remits.append((i, 1, int(c["remit1_day"][i])))
    return blk


def inject_anomalies(world: World, config: GeneratorConfig) -> World:
    """Append every spec's block to the base world (base arrays untouched)."""
    w = world
    n0, m0, r0 = w.n_claims, len(w.line_claim), len(w.remit_claim)
    t0, d0 = len(w.txn_claim), len(w.dn_claim)
    blocks = [_build_block(spec, config, w.dims) for spec in ANOMALY_SPECS]

    offsets: list[int] = []
    off = n0
    for blk in blocks:
        offsets.append(off)
        off += len(blk.claims["svc_day"])
    total_new = off - n0

    for fld in _CLAIM_FIELDS:
        parts = [getattr(w, fld)] + [blk.claims[fld] for blk in blocks]
        setattr(w, fld, np.concatenate(parts))
    w.n_claims = off

    for fld in _LINE_FIELDS:
        parts = [getattr(w, fld)]
        for blk, boff in zip(blocks, offsets, strict=True):
            arr = blk.lines[fld]
            parts.append(arr + boff if fld == "line_claim" else arr)
        setattr(w, fld, np.concatenate(parts))
    new_counts = np.concatenate([blk.claims["n_lines_per_claim"] for blk in blocks])
    new_starts = m0 + np.concatenate(([0], np.cumsum(new_counts)[:-1])).astype(np.int64)
    w.line_start = np.concatenate([w.line_start, new_starts])

    # remit rows: block order is deterministic; positions continue after base rows
    r_claim, r_seq, r_day = [], [], []
    pos1 = np.full(total_new, -1, dtype=np.int64)
    pos2 = np.full(total_new, -1, dtype=np.int64)
    pos = r0
    for blk, boff in zip(blocks, offsets, strict=True):
        for local, seq, rday in blk.remits:
            gclaim = boff + local
            r_claim.append(gclaim)
            r_seq.append(seq)
            r_day.append(rday)
            (pos1 if seq == 1 else pos2)[gclaim - n0] = pos
            pos += 1
    w.remit_claim = np.concatenate([w.remit_claim, np.array(r_claim, dtype=np.int64)])
    w.remit_seq = np.concatenate([w.remit_seq, np.array(r_seq, dtype=np.int64)])
    w.remit_day = np.concatenate([w.remit_day, np.array(r_day, dtype=np.int64)])
    w.remit_pos1 = np.concatenate([w.remit_pos1, pos1])
    w.remit_pos2 = np.concatenate([w.remit_pos2, pos2])

    t_claim, t_line, t_remit, t_type, t_amount, t_post, t_rday = [], [], [], [], [], [], []
    for blk, boff in zip(blocks, offsets, strict=True):
        for local, seq, type_code, amount, post_day in blk.txns:
            gclaim = boff + local
            t_claim.append(gclaim)
            t_line.append(-1)
            if seq == 1:
                rpos = int(pos1[gclaim - n0])
            elif seq == 2:
                rpos = int(pos2[gclaim - n0])
            else:
                rpos = -1
            t_remit.append(rpos)
            t_rday.append(int(w.remit_day[rpos]) if rpos >= 0 else NEVER)
            t_type.append(type_code)
            t_amount.append(amount)
            t_post.append(post_day)
    w.txn_claim = np.concatenate([w.txn_claim, np.array(t_claim, dtype=np.int64)])
    w.txn_line = np.concatenate([w.txn_line, np.array(t_line, dtype=np.int64)])
    w.txn_remit = np.concatenate([w.txn_remit, np.array(t_remit, dtype=np.int64)])
    w.txn_type = np.concatenate([w.txn_type, np.array(t_type, dtype=np.int64)])
    w.txn_amount = np.concatenate([w.txn_amount, np.array(t_amount, dtype=np.int64)])
    w.txn_post_day = np.concatenate([w.txn_post_day, np.array(t_post, dtype=np.int64)])
    w.txn_remit_day = np.concatenate([w.txn_remit_day, np.array(t_rday, dtype=np.int64)])

    dn_claim, dn_day, dn_carc, dn_group, dn_amount = [], [], [], [], []
    dn_appealed, dn_file, dn_dec, dn_ot, dn_remit = [], [], [], [], []
    for blk, boff in zip(blocks, offsets, strict=True):
        for local, dday, carc, group_i, amount, appealed, file_day, dec_day, overturned in blk.denials:
            gclaim = boff + local
            dn_claim.append(gclaim)
            dn_day.append(dday)
            dn_carc.append(carc)
            dn_group.append(group_i)
            dn_amount.append(amount)
            dn_appealed.append(appealed)
            dn_file.append(file_day)
            dn_dec.append(dec_day)
            dn_ot.append(overturned)
            dn_remit.append(int(pos1[gclaim - n0]))
    w.dn_claim = np.concatenate([w.dn_claim, np.array(dn_claim, dtype=np.int64)])
    w.dn_line = np.concatenate([w.dn_line, np.full(len(dn_claim), -1, dtype=np.int64)])
    w.dn_remit = np.concatenate([w.dn_remit, np.array(dn_remit, dtype=np.int64)])
    w.dn_group = np.concatenate([w.dn_group, np.array(dn_group, dtype=np.int64)])
    w.dn_carc = np.concatenate([w.dn_carc, np.array(dn_carc, dtype=np.int64)])
    w.dn_rarc = np.concatenate([w.dn_rarc, np.full(len(dn_claim), -1, dtype=np.int64)])
    w.dn_level_line = np.concatenate([w.dn_level_line, np.zeros(len(dn_claim), dtype=bool)])
    w.dn_day = np.concatenate([w.dn_day, np.array(dn_day, dtype=np.int64)])
    w.dn_amount = np.concatenate([w.dn_amount, np.array(dn_amount, dtype=np.int64)])
    w.dn_appealed = np.concatenate([w.dn_appealed, np.array(dn_appealed, dtype=bool)])
    w.dn_file_day = np.concatenate([w.dn_file_day, np.array(dn_file, dtype=np.int64)])
    w.dn_dec_day = np.concatenate([w.dn_dec_day, np.array(dn_dec, dtype=np.int64)])
    w.dn_overturned = np.concatenate([w.dn_overturned, np.array(dn_ot, dtype=bool)])

    _enforce_guards(w, n0, m0, t0, d0)
    return w


def _enforce_guards(w: World, n0: int, m0: int, t0: int, d0: int) -> None:
    """Prove non-interference at build time: raise if any appended row could
    move a scenario aggregate or the reference-week cash totals."""
    dims = w.dims
    s = SCENARIOS
    inj = slice(n0, w.n_claims)
    payer = w.payer_i[inj]
    plan = w.plan_i[inj]
    facility = w.facility_i[inj]
    svcline = w.svcline_i[inj]
    sub = w.sub_day[inj]
    problems: list[str] = []
    if np.any(payer == dims.payer_index[s.s2_payer]):
        problems.append("injected claim on Silverline Medicare Advantage (scenario 2 cell)")
    mer_img = (payer == dims.payer_index[s.s1_payer]) & (
        svcline == dims.service_line_index[s.s1_service_line]
    )
    if np.any(mer_img):
        problems.append("injected claim in Meridian Health x Imaging (scenario 1 cell)")
    smhmo_east = (plan == dims.plan_index[s.s5_plan]) & (facility == dims.facility_index[s.s5_facility])
    if np.any(smhmo_east):
        problems.append("injected claim on State Medicaid HMO x Eastside (scenario 5 cell)")
    atlas = payer == dims.payer_index[s.s3a_payer]
    atlas_sub_window = atlas & (sub >= day("2026-06-15")) & (sub <= day("2026-08-02"))
    if np.any(atlas_sub_window):
        problems.append("injected Atlas Commercial submission inside the scenario-3a window")
    line_inj = w.line_claim >= n0
    nb_ortho = (
        line_inj
        & (w.payer_i[w.line_claim] == dims.payer_index[s.s4_payer])
        & (w.line_group_i == PROC_GROUP_INDEX[s.s4_proc_group])
    )
    if np.any(nb_ortho):
        problems.append("injected Northbridge ORTHO-SURG line (scenario 4 cell)")
    tt = w.txn_type[t0:]
    tp = w.txn_post_day[t0:]
    tr = w.txn_remit_day[t0:]
    tclaim = w.txn_claim[t0:]
    cash = (tt == 0) | (tt == 3)  # PAYMENT / PATIENT_PAYMENT
    in_ref_weeks = cash & (tp >= _REFERENCE_CASH_START) & (tp <= _REFERENCE_CASH_END)
    if np.any(in_ref_weeks):
        problems.append("injected payer/patient cash posted inside the reference compare weeks")
    sm_pay = cash & (w.payer_i[tclaim] == dims.payer_index[s.s3b_payer]) & (tr >= day("2026-07-06"))
    if np.any(sm_pay):
        problems.append("injected State Medicaid payment with remit on/after 2026-07-06 (scenario 3b)")
    if np.any((w.svc_day[inj] < day("2025-01-01")) | (w.svc_day[inj] > day("2026-08-02"))):
        problems.append("injected claim service date outside 2025-01-01..2026-08-02")
    if np.any(w.dn_amount[d0:] <= 0) or np.any(w.txn_amount[t0:] <= 0):
        problems.append("injected non-positive denied/transaction amount")
    if np.any(w.line_billed[m0:] <= 0):
        problems.append("injected non-positive line billed amount")
    if problems:
        raise ValueError("anomaly injection violates non-interference guards: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# detection: recompute impact + evidence per spec per snapshot, emit rows


def severity_for(impact_cents: int) -> str:
    """Documented thresholds: critical >= $100k, high >= $25k, medium >= $5k, low below."""
    if impact_cents >= 10_000_000:
        return "critical"
    if impact_cents >= 2_500_000:
        return "high"
    if impact_cents >= 500_000:
        return "medium"
    return "low"


def confidence_for(n_events: int) -> float:
    """Documented rule: confidence follows the qualifying-event count."""
    if n_events >= 40:
        return 0.95
    if n_events >= 20:
        return 0.90
    if n_events >= 10:
        return 0.80
    if n_events >= 5:
        return 0.70
    return 0.60


def _scope_where(spec: AnomalySpec, *, include_proc_group: bool = False) -> str:
    conds = [f"payer_name = '{spec_payer_name(spec)}'"]
    if spec.plan is not None:
        conds.append(f"plan_name = '{spec.plan}'")
    if spec.facility is not None:
        conds.append(f"facility_name = '{spec.facility}'")
    conds.append(f"service_line_name = '{spec.service_line}'")
    if include_proc_group and spec.proc_group is not None:
        conds.append(f"proc_group = '{spec.proc_group}'")
    return " AND ".join(conds)


def spec_dimensions(spec: AnomalySpec) -> dict[str, str]:
    dims: dict[str, str] = {"payer": spec_payer_name(spec)}
    if spec.plan is not None:
        dims["plan"] = spec.plan
    if spec.facility is not None:
        dims["facility"] = spec.facility
    dims["service_line"] = spec.service_line
    if spec.proc_group is not None:
        dims["proc_group"] = spec.proc_group
    return dims


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row


def _stat(value: Any) -> float | int | None:
    if value is None:
        return None
    f = float(value)
    return int(f) if f.is_integer() else f


def _cell_rates(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec,
) -> dict[str, Any]:
    """Cell denial rate for the spec CARC, before the onset vs inside the window."""
    scope = _scope_where(spec)
    pre_n, pre_d, win_n, win_d = _one(
        con,
        f"""
        WITH cell AS (SELECT claim_id FROM {sch}.v_claim WHERE {scope}),
        fr AS (SELECT claim_id, MIN(remit_date) AS fr FROM {sch}.fact_remit GROUP BY claim_id),
        dx AS (SELECT DISTINCT claim_id FROM {sch}.v_denial
               WHERE {scope} AND carc_code = {spec.carc})
        SELECT count(*) FILTER (WHERE fr.fr < DATE '{spec.onset}'),
               count(dx.claim_id) FILTER (WHERE fr.fr < DATE '{spec.onset}'),
               count(*) FILTER (WHERE fr.fr BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'),
               count(dx.claim_id) FILTER (WHERE fr.fr BETWEEN DATE '{spec.onset}'
                   AND DATE '{spec.window_end}')
        FROM cell JOIN fr USING (claim_id) LEFT JOIN dx USING (claim_id)
        """,
    )
    return {
        "baseline_adjudicated_claims": int(pre_n),
        "baseline_carc_denial_rate": (int(pre_d) / int(pre_n)) if pre_n else None,
        "window_adjudicated_claims": int(win_n),
        "window_carc_denial_rate": (int(win_d) / int(win_n)) if win_n else None,
    }


def _detect_denial_based(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec, cutoff: str,
) -> tuple[int, int, dict[str, Any]]:
    scope = _scope_where(spec)
    apw = spec.appeal_window_days
    base = f"""
        FROM {sch}.v_denial
        WHERE {scope} AND carc_code = {spec.carc}
          AND denial_date BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
    """
    row = _one(
        con,
        f"""
        SELECT count(*), COALESCE(SUM(denied_amount_cents), 0),
               count(*) FILTER (WHERE appeal_status = 'NONE'),
               count(*) FILTER (WHERE appeal_status = 'APPEALED'),
               count(*) FILTER (WHERE appeal_status IN ('OVERTURNED', 'UPHELD')),
               MIN(DATE '{cutoff}' - denial_date), MEDIAN(DATE '{cutoff}' - denial_date),
               MAX(DATE '{cutoff}' - denial_date),
               count(*) FILTER (WHERE appeal_status = 'NONE'
                   AND denial_date + {apw} >= DATE '{cutoff}'),
               count(*) FILTER (WHERE denial_date + {apw} < DATE '{cutoff}'),
               MIN(denial_date + {apw} - DATE '{cutoff}'),
               MAX(denial_date + {apw} - DATE '{cutoff}')
        {base}
        """,
    )
    n = int(row[0])
    impact = int(row[1])
    # scope columns live only on v_denial and status only on fact_claim, so
    # unqualified names are unambiguous after the USING join
    status_rows = con.execute(
        f"""
        SELECT c.status, count(*)
        FROM {sch}.v_denial d JOIN {sch}.fact_claim c USING (claim_id)
        WHERE {scope} AND carc_code = {spec.carc}
          AND denial_date BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    evidence: dict[str, Any] = {
        "denied_claims": n,
        "denied_cents": impact,
        "appeal_status_counts": {"NONE": int(row[2]), "APPEALED": int(row[3]), "DECIDED": int(row[4])},
        "days_since_denial": {"min": _stat(row[5]), "median": _stat(row[6]), "max": _stat(row[7])},
        "appeal_window_days": apw,
        "appealable_claims": int(row[8]),
        "appeal_window_expired_claims": int(row[9]),
        "days_to_appeal_deadline": {"min": _stat(row[10]), "max": _stat(row[11])},
        "claim_status_counts": {str(st): int(cnt) for st, cnt in status_rows},
    }
    if spec.category in ("denial_spike", "eligibility_cluster"):
        evidence["cell_rates"] = _cell_rates(con, sch, spec)
    if spec.category == "eligibility_cluster":
        (patients,) = _one(
            con,
            f"""
            SELECT count(DISTINCT c.patient_id)
            FROM {sch}.v_denial d JOIN {sch}.fact_claim c USING (claim_id)
            WHERE {scope} AND carc_code = {spec.carc}
              AND denial_date BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
            """,
        )
        evidence["distinct_patients"] = int(patients)
    if spec.category == "duplicate":
        (originals_paid,) = _one(
            con,
            f"""
            SELECT count(DISTINCT o.claim_id)
            FROM {sch}.v_denial d
            JOIN {sch}.fact_claim dup USING (claim_id)
            JOIN {sch}.fact_claim o
              ON o.patient_id = dup.patient_id AND o.service_date = dup.service_date
             AND o.claim_id <> dup.claim_id AND o.status = 'PAID'
            WHERE {" AND ".join("d." + cond for cond in scope.split(" AND "))}
              AND d.carc_code = {spec.carc}
              AND d.denial_date BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
            """,
        )
        evidence["matching_paid_originals"] = int(originals_paid)
    if spec.prior_onset is not None:
        p_n, p_cents, p_ot = _one(
            con,
            f"""
            SELECT count(*), COALESCE(SUM(denied_amount_cents), 0),
                   count(*) FILTER (WHERE appeal_status = 'OVERTURNED')
            FROM {sch}.v_denial
            WHERE {scope} AND carc_code = {spec.carc}
              AND denial_date BETWEEN DATE '{spec.prior_onset}' AND DATE '{spec.prior_end}'
            """,
        )
        evidence["prior_episode"] = {
            "window_start": spec.prior_onset,
            "window_end": spec.prior_end,
            "denied_claims": int(p_n),
            "denied_cents": int(p_cents),
            "overturned_claims": int(p_ot),
        }
    return n, impact, evidence


def _detect_allowed_shift(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec, cutoff: str,
) -> tuple[int, int, dict[str, Any]]:
    scope = _scope_where(spec)
    pg_filter = ""
    if spec.proc_group is not None:
        pg_filter = (
            f"AND EXISTS (SELECT 1 FROM {sch}.fact_claim_line pl "
            f"WHERE pl.claim_id = c.claim_id AND pl.proc_group = '{spec.proc_group}')"
        )
    sql = f"""
        WITH fr AS (SELECT claim_id, MIN(remit_date) AS fr FROM {sch}.fact_remit GROUP BY claim_id),
        scoped AS (
            SELECT c.claim_id, c.expected_amount_cents, c.billed_amount_cents,
                   (SELECT SUM(l.allowed_amount_cents) FROM {sch}.fact_claim_line l
                    WHERE l.claim_id = c.claim_id) AS allowed
            FROM {sch}.v_claim c JOIN fr USING (claim_id)
            WHERE {scope} {pg_filter}
              AND fr.fr BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
        )
        SELECT count(*), COALESCE(SUM(GREATEST(expected_amount_cents - allowed, 0)), 0),
               COALESCE(SUM(expected_amount_cents), 0), COALESCE(SUM(allowed), 0),
               COALESCE(SUM(billed_amount_cents), 0)
        FROM scoped WHERE allowed IS NOT NULL
    """
    n, variance, expected, allowed, billed = (int(v) for v in _one(con, sql))
    evidence: dict[str, Any] = {
        "adjudicated_claims": n,
        "expected_cents": expected,
        "allowed_cents": allowed,
        "billed_cents": billed,
        "expected_minus_allowed_cents": expected - allowed,
        "allowed_to_expected_ratio": (allowed / expected) if expected else None,
    }
    if spec.category == "contractual":
        impact = billed - allowed  # the contractual write-down; variance stays ~0
        evidence["contractual_adj_cents"] = impact
        evidence["allowed_to_billed_ratio"] = (allowed / billed) if billed else None
        (b_billed, b_allowed) = _one(
            con,
            f"""
            WITH fr AS (SELECT claim_id, MIN(remit_date) AS fr FROM {sch}.fact_remit GROUP BY claim_id),
            scoped AS (
                SELECT c.claim_id, c.billed_amount_cents,
                       (SELECT SUM(l.allowed_amount_cents) FROM {sch}.fact_claim_line l
                        WHERE l.claim_id = c.claim_id) AS allowed
                FROM {sch}.v_claim c JOIN fr USING (claim_id)
                WHERE {scope} {pg_filter} AND fr.fr < DATE '{spec.onset}'
            )
            SELECT COALESCE(SUM(billed_amount_cents), 0), COALESCE(SUM(allowed), 0)
            FROM scoped WHERE allowed IS NOT NULL
            """,
        )
        evidence["baseline_allowed_to_billed_ratio"] = (
            int(b_allowed) / int(b_billed) if int(b_billed) else None
        )
    else:
        impact = variance
    return n, impact, evidence


def _detect_posting_lag(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec, cutoff: str,
) -> tuple[int, int, dict[str, Any]]:
    scope = _scope_where(spec)
    n, cents, mn, md, mx = _one(
        con,
        f"""
        WITH fr AS (SELECT claim_id, MIN(remit_date) AS fr FROM {sch}.fact_remit GROUP BY claim_id),
        paid AS (SELECT DISTINCT claim_id FROM {sch}.fact_transaction WHERE txn_type = 'PAYMENT')
        SELECT count(*), COALESCE(SUM(c.expected_amount_cents), 0),
               MIN(DATE '{cutoff}' - fr.fr), MEDIAN(DATE '{cutoff}' - fr.fr),
               MAX(DATE '{cutoff}' - fr.fr)
        FROM {sch}.v_claim c JOIN fr USING (claim_id)
        WHERE {scope} AND fr.fr BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
          AND c.claim_id NOT IN (SELECT claim_id FROM paid)
        """,
    )
    (baseline_lag,) = _one(
        con,
        f"""
        SELECT AVG(post_date - remit_date) FROM {sch}.v_transaction
        WHERE txn_type = 'PAYMENT' AND {scope} AND remit_date IS NOT NULL
          AND remit_date BETWEEN DATE '2026-05-01' AND DATE '{spec.onset}' - 1
        """,
    )
    evidence = {
        "remitted_unpaid_claims": int(n),
        "remitted_unpaid_expected_cents": int(cents),
        "days_since_remit": {"min": _stat(mn), "median": _stat(md), "max": _stat(mx)},
        "baseline_avg_post_lag_days": _stat(baseline_lag),
    }
    return int(n), int(cents), evidence


def _detect_unsubmitted(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec, cutoff: str,
) -> tuple[int, int, dict[str, Any]]:
    scope = _scope_where(spec)
    if spec.category == "dnfb":
        window_col, extra = "discharge_date", "AND claim_type = 'INSTITUTIONAL'"
        age_expr = f"DATE '{cutoff}' - discharge_date"
    else:
        window_col, extra = "service_date", ""
        age_expr = f"DATE '{cutoff}' - service_date"
    n, cents, mn, md, mx = _one(
        con,
        f"""
        SELECT count(*), COALESCE(SUM(billed_amount_cents), 0),
               MIN({age_expr}), MEDIAN({age_expr}), MAX({age_expr})
        FROM {sch}.v_claim
        WHERE {scope} {extra} AND submission_date IS NULL
          AND {window_col} BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
        """,
    )
    key = "days_since_discharge" if spec.category == "dnfb" else "days_since_service"
    evidence: dict[str, Any] = {
        "unsubmitted_claims": int(n),
        "billed_cents": int(cents),
        key: {"min": _stat(mn), "median": _stat(md), "max": _stat(mx)},
    }
    if spec.category == "timely_filing":
        row = _one(
            con,
            f"""
            SELECT ANY_VALUE(timely_filing_days),
                   count(*) FILTER (WHERE service_date + timely_filing_days >= DATE '{cutoff}'),
                   count(*) FILTER (WHERE service_date + timely_filing_days < DATE '{cutoff}'),
                   COALESCE(SUM(billed_amount_cents) FILTER (
                       WHERE service_date + timely_filing_days >= DATE '{cutoff}'), 0),
                   COALESCE(SUM(billed_amount_cents) FILTER (
                       WHERE service_date + timely_filing_days < DATE '{cutoff}'), 0),
                   MIN(service_date + timely_filing_days - DATE '{cutoff}'),
                   MEDIAN(service_date + timely_filing_days - DATE '{cutoff}'),
                   MAX(service_date + timely_filing_days - DATE '{cutoff}')
            FROM {sch}.v_claim
            WHERE {scope} AND submission_date IS NULL
              AND service_date BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
            """,
        )
        evidence.update(
            {
                "timely_filing_days": _stat(row[0]),
                "open_claims": int(row[1]),
                "expired_claims": int(row[2]),
                "open_billed_cents": int(row[3]),
                "expired_billed_cents": int(row[4]),
                "days_to_deadline": {"min": _stat(row[5]), "median": _stat(row[6]), "max": _stat(row[7])},
            }
        )
    return int(n), int(cents), evidence


def _detect_credit_balance(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec, cutoff: str,
) -> tuple[int, int, dict[str, Any]]:
    scope = _scope_where(spec)
    n, cents, refunds, mn, md, mx = _one(
        con,
        f"""
        WITH pays AS (
            SELECT claim_id,
                   COALESCE(SUM(amount_cents) FILTER (
                       WHERE txn_type IN ('PAYMENT', 'PATIENT_PAYMENT')), 0) AS paid,
                   COALESCE(SUM(amount_cents) FILTER (WHERE txn_type = 'REFUND'), 0) AS refunded,
                   MAX(post_date) FILTER (WHERE txn_type = 'PAYMENT') AS last_pay
            FROM {sch}.v_transaction WHERE {scope} GROUP BY claim_id
        ),
        credits AS (
            SELECT p.claim_id, GREATEST(p.paid - p.refunded - c.expected_amount_cents, 0) AS credit,
                   p.refunded, p.last_pay
            FROM pays p JOIN {sch}.fact_claim c USING (claim_id)
            WHERE p.last_pay BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
        )
        SELECT count(*) FILTER (WHERE credit > 0), COALESCE(SUM(credit), 0),
               COALESCE(SUM(refunded), 0),
               MIN(DATE '{cutoff}' - last_pay) FILTER (WHERE credit > 0),
               MEDIAN(DATE '{cutoff}' - last_pay) FILTER (WHERE credit > 0),
               MAX(DATE '{cutoff}' - last_pay) FILTER (WHERE credit > 0)
        FROM credits
        """,
    )
    evidence = {
        "claims_with_credit": int(n),
        "credit_cents": int(cents),
        "refunds_posted_cents": int(refunds),
        "days_since_last_payment": {"min": _stat(mn), "median": _stat(md), "max": _stat(mx)},
    }
    return int(n), int(cents), evidence


def _detect_charge_lag(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec, cutoff: str,
) -> tuple[int, int, dict[str, Any]]:
    scope = _scope_where(spec, include_proc_group=True)
    n, cents, mn, md, mx = _one(
        con,
        f"""
        SELECT count(*), COALESCE(SUM(billed_amount_cents), 0),
               MIN(charge_entry_date - service_date), MEDIAN(charge_entry_date - service_date),
               MAX(charge_entry_date - service_date)
        FROM {sch}.v_claim_line
        WHERE {scope} AND charge_entry_date - service_date > 14
          AND charge_entry_date BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
        """,
    )
    evidence = {
        "late_lines": int(n),
        "late_billed_cents": int(cents),
        "lag_threshold_days": 14,
        "charge_lag_days": {"min": _stat(mn), "median": _stat(md), "max": _stat(mx)},
    }
    return int(n), int(cents), evidence


def _detect_charge_hold(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec, cutoff: str,
) -> tuple[int, int, dict[str, Any]]:
    scope = _scope_where(spec)
    n, mn, md, mx = _one(
        con,
        f"""
        SELECT count(*), MIN(DATE '{cutoff}' - service_date),
               MEDIAN(DATE '{cutoff}' - service_date), MAX(DATE '{cutoff}' - service_date)
        FROM {sch}.v_claim
        WHERE {scope} AND billed_amount_cents = 0 AND submission_date IS NULL
          AND service_date BETWEEN DATE '{spec.onset}' AND DATE '{spec.window_end}'
        """,
    )
    evidence = {
        "claims_without_charges": int(n),
        "billed_cents_known": 0,
        "days_since_service": {"min": _stat(mn), "median": _stat(md), "max": _stat(mx)},
    }
    return int(n), 0, evidence


_DETECTORS = {
    "denial_spike": _detect_denial_based,
    "eligibility_cluster": _detect_denial_based,
    "duplicate": _detect_denial_based,
    "unworked_denials": _detect_denial_based,
    "underpayment": _detect_allowed_shift,
    "contractual": _detect_allowed_shift,
    "posting_lag": _detect_posting_lag,
    "submission_gap": _detect_unsubmitted,
    "timely_filing": _detect_unsubmitted,
    "dnfb": _detect_unsubmitted,
    "credit_balance": _detect_credit_balance,
    "charge_entry_lag": _detect_charge_lag,
    "charge_hold": _detect_charge_hold,
}

_EVENT_NOUN = {
    "denial_spike": "denials",
    "eligibility_cluster": "eligibility denials",
    "duplicate": "duplicate-claim denials",
    "unworked_denials": "unworked denials",
    "underpayment": "underpaid claims",
    "contractual": "reduced-rate claims",
    "posting_lag": "remitted-unpaid claims",
    "submission_gap": "unsubmitted claims",
    "timely_filing": "unsubmitted claims",
    "dnfb": "unbilled discharges",
    "credit_balance": "overpaid claims",
    "charge_entry_lag": "late-entered lines",
    "charge_hold": "claims without charges",
}


def compute_detection(
    con: duckdb.DuckDBPyConnection, sch: str, spec: AnomalySpec, cutoff: str,
) -> tuple[int, int, dict[str, Any]]:
    """(n_events, impact_cents, evidence) for one spec against one snapshot."""
    return _DETECTORS[spec.category](con, sch, spec, cutoff)


def is_emitted(spec: AnomalySpec, n_events: int, impact_cents: int) -> bool:
    return n_events >= spec.min_events and impact_cents >= spec.min_impact_cents


def _describe(spec: AnomalySpec, n_events: int, impact_cents: int) -> str:
    cell = " / ".join(spec_dimensions(spec).values())
    noun = _EVENT_NOUN[spec.category]
    dollars = f"${impact_cents / 100:,.0f}"
    if spec.category == "charge_hold":
        return (
            f"{cell}: {n_events} {noun} for services {spec.onset}..{spec.window_end}; "
            "billed dollars unknown until charges post."
        )
    return (
        f"{cell}: {n_events} {noun} totaling {dollars} "
        f"in window {spec.onset}..{spec.window_end}."
    )


def detect_snapshot_anomalies(
    con: duckdb.DuckDBPyConnection, sch: str, snap: SnapshotSpec,
) -> list[tuple[Any, ...]]:
    """All emitted anomaly rows for one snapshot, ordered by anomaly_id."""
    rows: list[tuple[Any, ...]] = []
    for spec in ANOMALY_SPECS:
        if day(spec.onset) > snap.cutoff_day:
            continue
        n_events, impact, evidence = compute_detection(con, sch, spec, snap.newest_data_date)
        if not is_emitted(spec, n_events, impact):
            continue
        evidence["n_events"] = n_events
        evidence["cutoff"] = snap.newest_data_date
        rows.append(
            (
                spec.spec_id,
                snap.loaded_at,
                spec.category,
                spec.title,
                _describe(spec, n_events, impact),
                spec.metric_id,
                json.dumps(spec_dimensions(spec), sort_keys=True),
                spec.onset,
                spec.window_end,
                impact,
                severity_for(impact),
                f"{confidence_for(n_events):.2f}",
                "open",
                json.dumps(evidence, sort_keys=True),
            )
        )
    return rows


def write_detected_anomalies(
    con: duckdb.DuckDBPyConnection, sch: str, snap: SnapshotSpec,
) -> int:
    """Create and fill <sch>.detected_anomalies; returns the row count."""
    con.execute(
        f"""
        CREATE TABLE {sch}.detected_anomalies (
            anomaly_id VARCHAR NOT NULL,
            detected_at TIMESTAMP NOT NULL,
            category VARCHAR NOT NULL,
            title VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            metric_id VARCHAR NOT NULL,
            dimensions JSON NOT NULL,
            window_start DATE NOT NULL,
            window_end DATE NOT NULL,
            impact_cents BIGINT NOT NULL,
            severity VARCHAR NOT NULL,
            confidence DECIMAL(3, 2) NOT NULL,
            status VARCHAR NOT NULL,
            evidence JSON NOT NULL
        )
        """
    )
    rows = detect_snapshot_anomalies(con, sch, snap)
    for row in rows:
        con.execute(
            f"INSERT INTO {sch}.detected_anomalies VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(row),
        )
    return len(rows)
