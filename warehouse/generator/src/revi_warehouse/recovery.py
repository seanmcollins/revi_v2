"""Denial recovery chains: resubmission events and their outcome remits.

Most denials in this warehouse used to end at the denial. This stage gives the
survivors a story — the tenant's own follow-up history, which deep research can
mine for *empirical* recoverability (observed denied -> resubmitted -> outcome
transition rates by payer, denial class, age and dollars) instead of a
hardcoded rule of thumb. The feed itself is mode-agnostic: it is remit history,
and nothing in the schema names a consumer.

WHAT IS MODELLED (all of it from config.py's tables, never per claim):

* **Who gets worked.** ``RECOVERY_CLASS_SPECS[class].resubmit_prob``, tilted by
  denial size (large denials are pursued harder). Payer identity does NOT enter
  here — it enters the outcome — so the payer contrast among resubmitted
  denials is unconfounded by selection.
* **How fast.** A lognormal delay whose median is the class's: coding fixes go
  back out in ~9 days, medical-necessity work in ~28.
* **What comes back.** ``base_overturn`` for the class, multiplied by
  ``RECOVERY_PAYER_OVERTURN_FACTOR[payer]`` (one strong payer, one weak, the
  rest between), by a timeliness decay in days-to-resubmission, by a filing
  deadline collapse, and by a mild dollar tilt. Paid outcomes are full or
  partial; the rest are denied again, occasionally worked a second time.
* **The filing deadline interaction.** The deadline is the claim's service date
  plus its plan's configured limit (dim_plan.timely_filing_days — the same
  arithmetic the certified ``filing_runway_bucket`` uses). Crossing it collapses
  recovery hard on the seven plans whose limit the pack ladder states without a
  confirmation caveat (``GOVERNED_CONFIRMED_PLANS``) and only partly on the
  other 23, whose limits are planning defaults. That difference is real signal:
  an analysis that treats every deadline as governed over-predicts the cliff.

WHAT IS NOT MODELLED, stated so nobody reads more into the feed than is there:

1. **Population.** Chains exist for organic-era denials the base world left as
   dead ends — no formal appeal on the remit. Denials that WERE appealed carry
   their outcome in ``fact_denial.appeal_status`` already, and duplicating them
   here would double-count the same recovery. The 2024 backfill is a closed
   comparison year and carries no follow-up feed at all (verify.py pins both).
2. **Cash.** ``recovered_amount_cents`` is the payer-ALLOWED amount on the
   denied unit — what the recovery is worth, not posted cash, and not net of
   patient responsibility. No transaction, remit, claim status or answer-key
   figure moves because of this feed; every published number is exactly what it
   was before it existed.

RIGHT-CENSORING is not a special case: event days are generated forward from
the denial and each snapshot keeps only the events that had happened by its
cutoff. Near the wm_003 edge that leaves chains resubmitted-but-undecided and
denials whose resubmission has not gone out yet — indistinguishable, in the
data, from denials nobody will ever work. A later load resolves them; the
scheme needs no change to admit one.

Determinism: every draw comes from ``(REVI_SEED, RECOVERY_STREAM,
crc32(sub_stream))``. No draw belonging to dims/world/anomalies/backfill is
consumed or shifted.
"""

from __future__ import annotations

import zlib
from typing import Any

import numpy as np

from revi_warehouse.config import (
    GOVERNED_CONFIRMED_PLANS,
    NEVER,
    ORGANIC_ERA_START,
    RECOVERY,
    RECOVERY_CLASS_BY_CARC,
    RECOVERY_CLASS_SPECS,
    RECOVERY_CLASSES,
    RECOVERY_OUTCOMES,
    RECOVERY_PAYER_OVERTURN_FACTOR,
    RECOVERY_STREAM,
    RESUBMISSION_TYPES,
    REVI_SEED,
    SNAPSHOTS,
    GeneratorConfig,
)
from revi_warehouse.dims import CARC_GROUP, PAYERS, PLANS
from revi_warehouse.world import World

_GROUP_NAMES = ("CO", "PR", "OA", "PI")

EVENT_RESUBMISSION, EVENT_OUTCOME = 0, 1
"""Event type codes; the writer's CASE expression must match this order."""

_OUTCOME_PAID_FULL = RECOVERY_OUTCOMES.index("PAID_FULL")
_OUTCOME_PAID_PARTIAL = RECOVERY_OUTCOMES.index("PAID_PARTIAL")
_OUTCOME_DENIED_AGAIN = RECOVERY_OUTCOMES.index("DENIED_AGAIN")

_MAX_REDENIAL_CARCS = max(len(spec.redenial_carcs) for spec in RECOVERY_CLASS_SPECS)

RECOVERY_ARRAY_FIELDS = (
    "rc_denial", "rc_claim", "rc_cycle", "rc_type", "rc_day", "rc_parent",
    "rc_action", "rc_outcome", "rc_days_from_denial", "rc_days_from_resub",
    "rc_denied_amount", "rc_recovered", "rc_carc", "rc_group", "rc_rarc",
)
"""Every per-event array the recovery stage owns (World fields, all int64)."""


def _rint64(x: np.ndarray) -> np.ndarray:
    return np.rint(x).astype(np.int64)


def _stream(sub_stream: str) -> np.random.Generator:
    """One independent PCG64 per sub-stream, seeded like the anomaly engine's."""
    seq = np.random.SeedSequence((REVI_SEED, RECOVERY_STREAM, zlib.crc32(sub_stream.encode("ascii"))))
    return np.random.Generator(np.random.PCG64(seq))


# --- config tables flattened into lookup arrays -------------------------------


def _class_index_by_carc() -> np.ndarray:
    """CARC -> class index; -1 for a CARC with no authored class."""
    table = np.full(300, -1, dtype=np.int64)
    for carc, name in RECOVERY_CLASS_BY_CARC.items():
        table[carc] = RECOVERY_CLASSES.index(name)
    return table


def _group_index_by_carc() -> np.ndarray:
    table = np.zeros(300, dtype=np.int64)  # default CO
    for carc, group in CARC_GROUP.items():
        table[carc] = _GROUP_NAMES.index(group)
    return table


def _plan_filing_limits() -> tuple[np.ndarray, np.ndarray]:
    """(limit days, governed-without-confirmation flag) per plan index."""
    limits = np.array([plan[3] for plan in PLANS], dtype=np.int64)
    confirmed = np.array([plan[1] in GOVERNED_CONFIRMED_PLANS for plan in PLANS], dtype=bool)
    return limits, confirmed


def _class_vector(attr: str) -> np.ndarray:
    return np.array([getattr(spec, attr) for spec in RECOVERY_CLASS_SPECS], dtype=np.float64)


def _redenial_carc_table() -> tuple[np.ndarray, np.ndarray]:
    """(padded CARC choices per class, choice count per class)."""
    table = np.zeros((len(RECOVERY_CLASS_SPECS), _MAX_REDENIAL_CARCS), dtype=np.int64)
    counts = np.zeros(len(RECOVERY_CLASS_SPECS), dtype=np.int64)
    for i, spec in enumerate(RECOVERY_CLASS_SPECS):
        counts[i] = len(spec.redenial_carcs)
        table[i, : counts[i]] = spec.redenial_carcs
    return table, counts


def _action_codes() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(primary action code, primary share, alternate action code) per class."""
    primary = np.array([RESUBMISSION_TYPES.index(s.primary_action) for s in RECOVERY_CLASS_SPECS])
    share = np.array([s.primary_action_share for s in RECOVERY_CLASS_SPECS], dtype=np.float64)
    alt = np.array([RESUBMISSION_TYPES.index(s.alt_action) for s in RECOVERY_CLASS_SPECS])
    return primary.astype(np.int64), share, alt.astype(np.int64)


# --- the model ----------------------------------------------------------------


def _dollar_factor(denied: np.ndarray, tilt: float) -> np.ndarray:
    """Mild log-dollar tilt: 1.0 at the reference denial, +/- `tilt` per decade."""
    decades = np.log10(np.maximum(denied, 1) / RECOVERY.dollar_reference_cents)
    factor: np.ndarray = np.clip(
        1.0 + tilt * decades, RECOVERY.dollar_factor_min, RECOVERY.dollar_factor_max
    )
    return factor


def _timeliness_factor(days: np.ndarray) -> np.ndarray:
    floor = RECOVERY.timeliness_floor
    return floor + (1.0 - floor) * np.exp(-days / RECOVERY.timeliness_tau_days)


def _deadline_factor(past: np.ndarray, confirmed: np.ndarray) -> np.ndarray:
    penalty = np.where(
        confirmed, RECOVERY.past_deadline_factor_confirmed, RECOVERY.past_deadline_factor_default
    )
    return np.where(past, penalty, 1.0)


def _overturn_probability(
    cls: np.ndarray,
    payer_factor: np.ndarray,
    elapsed_days: np.ndarray,
    past_deadline: np.ndarray,
    confirmed: np.ndarray,
    dollar_factor: np.ndarray,
) -> np.ndarray:
    base = _class_vector("base_overturn")[cls]
    p = (
        base
        * payer_factor
        * _timeliness_factor(elapsed_days)
        * _deadline_factor(past_deadline, confirmed)
        * dollar_factor
    )
    bounded: np.ndarray = np.clip(
        p, RECOVERY.overturn_prob_floor, RECOVERY.overturn_prob_ceiling
    )
    return bounded


def _lognormal_days(
    rng: np.random.Generator, median: np.ndarray, sigma: np.ndarray, lo: int, hi: int
) -> np.ndarray:
    draw = rng.lognormal(np.log(median), sigma, size=len(median))
    days: np.ndarray = np.clip(_rint64(draw), lo, hi)
    return days


def apply_recovery(world: World, config: GeneratorConfig) -> World:
    """Append recovery-chain events to `world`. Existing arrays are never touched."""
    n_denials = 0 if world.dn_claim is None else len(world.dn_claim)
    if n_denials == 0:
        return _empty(world)

    claim = world.dn_claim
    class_by_carc = _class_index_by_carc()
    cls_all = class_by_carc[world.dn_carc]

    # Population: organic-era denials with no formal appeal on the remit.
    organic = world.svc_day[claim] >= ORGANIC_ERA_START
    eligible = organic & ~world.dn_appealed & (cls_all >= 0)
    idx = np.flatnonzero(eligible)
    n = len(idx)
    if n == 0:
        return _empty(world)

    den_claim = claim[idx]
    cls = cls_all[idx]
    den_day = world.dn_day[idx]
    denied_amount = world.dn_amount[idx]
    payer_i = world.payer_i[den_claim]
    plan_i = world.plan_i[den_claim]
    payer_factor = np.array(RECOVERY_PAYER_OVERTURN_FACTOR, dtype=np.float64)[payer_i]

    limits, confirmed_by_plan = _plan_filing_limits()
    deadline_day = world.svc_day[den_claim] + limits[plan_i]
    confirmed = confirmed_by_plan[plan_i]

    # What a recovery is worth: the payer-allowed amount on the denied unit,
    # capped by the denied amount so `recovered <= denied` holds by construction.
    line_pos = np.maximum(world.dn_line[idx], 0)
    recoverable = np.where(
        world.dn_level_line[idx], world.line_allowed[line_pos], world.allowed_total[den_claim]
    ).astype(np.int64)
    recoverable = np.minimum(recoverable, denied_amount)

    # The base world already recorded the outcome of some of these: a second
    # remit with no appeal is a rebill that landed (the planted COB cohort).
    # Those chains are back-dated onto the real remit instead of drawn.
    remit2_day = world.remit2_day[den_claim]
    rebilled = remit2_day != NEVER

    dollar_res = _dollar_factor(denied_amount, RECOVERY.dollar_tilt_resubmit)
    dollar_ot = _dollar_factor(denied_amount, RECOVERY.dollar_tilt_overturn)

    # --- cycle 1: does the denial get worked, and how fast? -------------------
    p_resubmit = np.clip(
        _class_vector("resubmit_prob")[cls] * dollar_res,
        RECOVERY.resubmit_prob_floor,
        RECOVERY.resubmit_prob_ceiling,
    )
    resubmits = _stream("resubmit_decision").random(n) < p_resubmit
    resubmits |= rebilled  # the rebill demonstrably happened

    delay = _lognormal_days(
        _stream("resubmit_delay"),
        _class_vector("delay_median_days")[cls],
        _class_vector("delay_sigma")[cls],
        RECOVERY.resubmit_min_days,
        RECOVERY.resubmit_max_days,
    )
    # Aged inventory: some denials sit in the work queue for months before
    # anyone touches them, and that is what carries a resubmission past a
    # 90-day filing window. The classes nobody wants to work age the most.
    backlogged = _stream("backlog_decision").random(n) < _class_vector("backlog_share")[cls]
    backlog_delay = _lognormal_days(
        _stream("backlog_delay"),
        np.full(n, RECOVERY.backlog_delay_median_days),
        np.full(n, RECOVERY.backlog_delay_sigma),
        RECOVERY.resubmit_min_days,
        RECOVERY.resubmit_max_days,
    )
    delay = np.where(backlogged, backlog_delay, delay)
    prep = _stream("rebill_prep").integers(
        RECOVERY.rebill_prep_min_days, RECOVERY.rebill_prep_max_days + 1, size=n
    )
    rebill_resub_day = np.clip(remit2_day - prep, den_day + 1, np.maximum(remit2_day - 1, den_day + 1))
    resub_day = np.where(rebilled, rebill_resub_day, den_day + delay)
    delay = resub_day - den_day

    primary, primary_share, alt = _action_codes()
    u_action = _stream("resubmit_action").random(n)
    action = np.where(u_action < primary_share[cls], primary[cls], alt[cls])
    action = np.where(rebilled, RESUBMISSION_TYPES.index("REBILL_REROUTE"), action)

    # --- cycle 1: the payer's answer -----------------------------------------
    past_deadline = resub_day > deadline_day
    p_overturn = _overturn_probability(
        cls, payer_factor, delay, past_deadline, confirmed, dollar_ot
    )
    paid = _stream("outcome_decision").random(n) < p_overturn
    paid |= rebilled

    outcome_lag = _lognormal_days(
        _stream("outcome_lag"),
        np.full(n, RECOVERY.outcome_lag_median_days),
        np.full(n, RECOVERY.outcome_lag_sigma),
        RECOVERY.outcome_lag_min_days,
        RECOVERY.outcome_lag_max_days,
    )
    outcome_day = np.where(rebilled, remit2_day, resub_day + outcome_lag)

    partial = paid & ~rebilled & (
        _stream("partial_decision").random(n) < _class_vector("partial_prob")[cls]
    )
    outcome = np.where(
        paid,
        np.where(partial, _OUTCOME_PAID_PARTIAL, _OUTCOME_PAID_FULL),
        _OUTCOME_DENIED_AGAIN,
    )
    share = _stream("partial_share").uniform(
        RECOVERY.partial_min_share, RECOVERY.partial_max_share, size=n
    )
    recovered = np.where(
        outcome == _OUTCOME_PAID_FULL,
        recoverable,
        np.where(outcome == _OUTCOME_PAID_PARTIAL, _rint64(recoverable * share), 0),
    ).astype(np.int64)
    redenial_carc, redenial_group, redenial_rarc = _redenial_codes(
        cls, world.dn_carc[idx], outcome == _OUTCOME_DENIED_AGAIN, "1"
    )

    # --- cycle 2: occasionally, we work it again -----------------------------
    denied_again = resubmits & (outcome == _OUTCOME_DENIED_AGAIN)
    second = denied_again & (
        _stream("cycle2_decision").random(n) < _class_vector("second_cycle_prob")[cls]
    )
    delay2 = _lognormal_days(
        _stream("cycle2_delay"),
        _class_vector("delay_median_days")[cls] * RECOVERY.second_cycle_delay_factor,
        _class_vector("delay_sigma")[cls],
        RECOVERY.resubmit_min_days,
        RECOVERY.resubmit_max_days,
    )
    resub2_day = outcome_day + delay2
    elapsed2 = resub2_day - den_day
    u_action2 = _stream("cycle2_action").random(n)
    action2 = np.where(u_action2 < primary_share[cls], primary[cls], alt[cls])
    p_overturn2 = np.clip(
        _overturn_probability(
            cls, payer_factor, elapsed2, resub2_day > deadline_day, confirmed, dollar_ot
        )
        * RECOVERY.second_cycle_overturn_factor,
        RECOVERY.overturn_prob_floor,
        RECOVERY.overturn_prob_ceiling,
    )
    paid2 = _stream("cycle2_outcome").random(n) < p_overturn2
    outcome_lag2 = _lognormal_days(
        _stream("cycle2_lag"),
        np.full(n, RECOVERY.outcome_lag_median_days),
        np.full(n, RECOVERY.outcome_lag_sigma),
        RECOVERY.outcome_lag_min_days,
        RECOVERY.outcome_lag_max_days,
    )
    outcome2_day = resub2_day + outcome_lag2
    partial2 = paid2 & (
        _stream("cycle2_partial").random(n) < _class_vector("partial_prob")[cls]
    )
    outcome2 = np.where(
        paid2,
        np.where(partial2, _OUTCOME_PAID_PARTIAL, _OUTCOME_PAID_FULL),
        _OUTCOME_DENIED_AGAIN,
    )
    share2 = _stream("cycle2_share").uniform(
        RECOVERY.partial_min_share, RECOVERY.partial_max_share, size=n
    )
    recovered2 = np.where(
        outcome2 == _OUTCOME_PAID_FULL,
        recoverable,
        np.where(outcome2 == _OUTCOME_PAID_PARTIAL, _rint64(recoverable * share2), 0),
    ).astype(np.int64)
    redenial2_carc, redenial2_group, redenial2_rarc = _redenial_codes(
        cls, world.dn_carc[idx], outcome2 == _OUTCOME_DENIED_AGAIN, "2"
    )

    # --- assemble the event rows ---------------------------------------------
    none_action = np.full(n, -1, dtype=np.int64)
    none_outcome = np.full(n, -1, dtype=np.int64)
    zero = np.zeros(n, dtype=np.int64)
    minus = np.full(n, -1, dtype=np.int64)
    groups = [
        # (mask, cycle, type, day, action, outcome, days_from_resub,
        #  recovered, carc, group, rarc)
        (resubmits, 1, EVENT_RESUBMISSION, resub_day, action, none_outcome, minus,
         zero, zero, minus, minus),
        (resubmits, 1, EVENT_OUTCOME, outcome_day, none_action, outcome, outcome_day - resub_day,
         recovered, redenial_carc, redenial_group, redenial_rarc),
        (second, 2, EVENT_RESUBMISSION, resub2_day, action2, none_outcome, minus,
         zero, zero, minus, minus),
        (second, 2, EVENT_OUTCOME, outcome2_day, none_action, outcome2, outcome2_day - resub2_day,
         recovered2, redenial2_carc, redenial2_group, redenial2_rarc),
    ]
    cols: dict[str, list[np.ndarray]] = {
        name: [] for name in RECOVERY_ARRAY_FIELDS if name != "rc_parent"
    }
    parent_pre: list[np.ndarray] = []
    offset = 0
    starts: list[int] = []
    for mask, cycle, kind, day, act, out, from_resub, rec, carc, group, rarc in groups:
        keep = np.flatnonzero(mask)
        starts.append(offset)
        size = len(keep)
        cols["rc_denial"].append(idx[keep])
        cols["rc_claim"].append(den_claim[keep])
        cols["rc_cycle"].append(np.full(size, cycle, dtype=np.int64))
        cols["rc_type"].append(np.full(size, kind, dtype=np.int64))
        cols["rc_day"].append(day[keep].astype(np.int64))
        cols["rc_action"].append(act[keep].astype(np.int64))
        cols["rc_outcome"].append(out[keep].astype(np.int64))
        cols["rc_days_from_denial"].append((day[keep] - den_day[keep]).astype(np.int64))
        cols["rc_days_from_resub"].append(from_resub[keep].astype(np.int64))
        cols["rc_denied_amount"].append(denied_amount[keep].astype(np.int64))
        cols["rc_recovered"].append(rec[keep].astype(np.int64))
        cols["rc_carc"].append(carc[keep].astype(np.int64))
        cols["rc_group"].append(group[keep].astype(np.int64))
        cols["rc_rarc"].append(rarc[keep].astype(np.int64))
        if kind == EVENT_OUTCOME:
            # The k-th outcome answers the k-th resubmission of the same cycle:
            # both groups carry the same mask, in the same order.
            parent_pre.append(starts[-2] + np.arange(size, dtype=np.int64))
        else:
            parent_pre.append(np.full(size, -1, dtype=np.int64))
        offset += size

    assembled = {name: np.concatenate(parts) for name, parts in cols.items()}
    parent = np.concatenate(parent_pre)
    order = np.lexsort(
        (
            assembled["rc_type"],
            assembled["rc_cycle"],
            assembled["rc_denial"],
            assembled["rc_day"],
        )
    )
    inverse = np.empty(len(order), dtype=np.int64)
    inverse[order] = np.arange(len(order), dtype=np.int64)
    for name, values in assembled.items():
        setattr(world, name, values[order])
    world.rc_parent = np.where(parent[order] >= 0, inverse[np.maximum(parent[order], 0)], -1)
    return world


def _redenial_codes(
    cls: np.ndarray, original_carc: np.ndarray, denied_again: np.ndarray, cycle: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """CARC / group / RARC carried by a second denial (0 / -1 when it was paid)."""
    n = len(cls)
    table, counts = _redenial_carc_table()
    same = _stream(f"redenial_same_c{cycle}").random(n) < RECOVERY.redenial_same_carc_prob
    pick = np.floor(_stream(f"redenial_pick_c{cycle}").random(n) * counts[cls]).astype(np.int64)
    alternative = table[cls, np.minimum(pick, counts[cls] - 1)]
    carc = np.where(denied_again, np.where(same, original_carc, alternative), 0).astype(np.int64)
    group = np.where(denied_again, _group_index_by_carc()[carc], -1).astype(np.int64)
    has_rarc = denied_again & (
        _stream(f"redenial_rarc_c{cycle}").random(n) < RECOVERY.redenial_rarc_prob
    )
    rarc = np.where(
        has_rarc, _stream(f"redenial_rarc_pick_c{cycle}").integers(1, 21, size=n), -1
    ).astype(np.int64)
    return carc, group, rarc


def _empty(world: World) -> World:
    for name in RECOVERY_ARRAY_FIELDS:
        setattr(world, name, np.empty(0, dtype=np.int64))
    return world


# --- world-side truth (the uncensored story, for the answer key) --------------


def recovery_truth(world: World) -> dict[str, Any]:
    """Authored parameters plus the counts only the generator can see.

    The realized aggregates in the answer key are read back out of the written
    snapshots; what cannot be read back is how much of the story the newest
    watermark cannot see yet. That is this function: the world knows the day
    every chain resubmits and settles, the snapshot only knows the ones that
    already happened.
    """
    edge = SNAPSHOTS[-1].cutoff_day
    day = world.rc_day
    kind = world.rc_type
    denial = world.rc_denial
    is_resub = kind == EVENT_RESUBMISSION
    first_cycle = world.rc_cycle == 1

    chains = np.unique(denial[is_resub & first_cycle])
    size = int(denial.max(initial=-1)) + 2
    resub_day = np.full(size, NEVER, dtype=np.int64)
    resub_day[denial[is_resub & first_cycle]] = day[is_resub & first_cycle]
    settled = np.full(size, -1, dtype=np.int64)
    outcome_rows = ~is_resub
    # the LAST outcome of a chain is the one that settles it
    np.maximum.at(settled, denial[outcome_rows], day[outcome_rows])
    beyond_edge = int(np.count_nonzero(resub_day[chains] > edge))
    pending_at_edge = int(
        np.count_nonzero((resub_day[chains] <= edge) & (settled[chains] > edge))
    )
    settled_at_edge = int(np.count_nonzero(settled[chains] <= edge))

    return {
        "model": {
            "population": (
                "organic-era denials with no formal appeal on the remit; the 2024 "
                "backfill and appealed denials are excluded"
            ),
            "recovered_amount_definition": (
                "payer-allowed dollars on the denied unit, capped at the denied "
                "amount — not posted cash, not net of patient responsibility"
            ),
            "timeliness": {
                "tau_days": RECOVERY.timeliness_tau_days,
                "floor": RECOVERY.timeliness_floor,
                "past_deadline_factor_confirmed": RECOVERY.past_deadline_factor_confirmed,
                "past_deadline_factor_default": RECOVERY.past_deadline_factor_default,
                "confirmed_plans": sorted(GOVERNED_CONFIRMED_PLANS),
            },
            "dollar_tilt": {
                "reference_cents": RECOVERY.dollar_reference_cents,
                "per_decade_resubmit": RECOVERY.dollar_tilt_resubmit,
                "per_decade_overturn": RECOVERY.dollar_tilt_overturn,
            },
            "second_cycle": {
                "delay_factor": RECOVERY.second_cycle_delay_factor,
                "overturn_factor": RECOVERY.second_cycle_overturn_factor,
            },
            "classes": [
                {
                    "class": spec.name,
                    "carcs": sorted(c for c, k in RECOVERY_CLASS_BY_CARC.items() if k == spec.name),
                    "resubmit_prob": spec.resubmit_prob,
                    "delay_median_days": spec.delay_median_days,
                    "delay_sigma": spec.delay_sigma,
                    "base_overturn": spec.base_overturn,
                    "partial_prob": spec.partial_prob,
                    "second_cycle_prob": spec.second_cycle_prob,
                }
                for spec in RECOVERY_CLASS_SPECS
            ],
            "payer_overturn_factor": {
                payer.name: factor
                for payer, factor in zip(PAYERS, RECOVERY_PAYER_OVERTURN_FACTOR, strict=True)
            },
        },
        "world_truth": {
            "chains": len(chains),
            "events": len(day),
            "newest_watermark_cutoff": str(np.datetime64(edge, "D")),
            "settled_by_newest_watermark": settled_at_edge,
            "resubmitted_undecided_at_newest_watermark": pending_at_edge,
            "resubmission_after_newest_watermark": beyond_edge,
        },
    }
