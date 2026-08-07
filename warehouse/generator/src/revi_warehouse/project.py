"""Project the single generated world onto one snapshot (a simulated nightly load).

A snapshot with cutoff day D contains exactly the activity that had happened by
end-of-day D: submissions, remits, transactions, denials, appeal decisions and
charge entry after D are absent, and every derived claim field (status,
first_pass_paid, clean_claim, resolved_date, patient responsibility, billed
totals) reflects only what that snapshot can see.

Sources are numeric-only numpy arrays (integer codes + datetime64[us]); ID
strings and enum labels are materialized in SQL by the writer — registering
large object-dtype arrays into DuckDB is pathologically slow.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from revi_warehouse.config import NEVER
from revi_warehouse.world import World

# Enum decode orders — the writer's CASE expressions must match these.
STATUS_CODES = ("OPEN", "PAID", "DENIED", "CLOSED")
PSEQ_CODES = ("P", "S", "T")
GROUP_CODES = ("CO", "PR", "OA", "PI")
APPEAL_CODES = ("NONE", "APPEALED", "OVERTURNED", "UPHELD")


def days_to_ts(days: np.ndarray) -> np.ndarray:
    """int day array -> datetime64[us] with NaT where the sentinel NEVER appears."""
    out = days.astype("datetime64[D]").astype("datetime64[us]")
    out[days >= NEVER] = np.datetime64("NaT", "us")
    return out


class SnapshotTables:
    """Numeric column dicts for every fact table of one snapshot."""

    def __init__(self, world: World, cutoff_day: int) -> None:
        w = world
        d = cutoff_day
        self.cutoff_day = d

        # --- claims ---------------------------------------------------------
        cm = w.svc_day <= d
        n = w.n_claims
        line_vis = w.line_charge_day <= d
        billed_vis = np.bincount(
            w.line_claim[line_vis], weights=w.line_billed[line_vis], minlength=n
        ).astype(np.int64)
        expected_vis = np.bincount(
            w.line_claim[line_vis], weights=w.line_expected[line_vis], minlength=n
        ).astype(np.int64)
        first_den_day = np.where(w.denied, w.remit1_day, NEVER)
        paid_vis = w.first_pay_post <= d
        den_vis = first_den_day <= d
        wo_vis = w.writeoff_day <= d
        status = np.zeros(n, dtype=np.int64)  # OPEN
        status[den_vis & ~wo_vis] = 2  # DENIED
        status[den_vis & wo_vis] = 3  # CLOSED
        status[paid_vis] = 1  # PAID (payment wins over denial history)
        self.fact_claim: dict[str, Any] = {
            "idx": np.arange(n, dtype=np.int64)[cm],
            "patient_i": w.patient_i[cm].astype(np.int64),
            "payer_i": w.payer_i[cm],
            "plan_i": w.plan_i[cm],
            "provider_i": w.provider_i[cm],
            "facility_i": w.facility_i[cm],
            "svcline_i": w.svcline_i[cm],
            "inst": w.is_institutional[cm],
            "pseq": w.pseq[cm],
            "other_insurance_flag": w.oins[cm],
            "cob_mismatch_flag": w.cobm[cm],
            "service_date": days_to_ts(w.svc_day[cm]),
            "discharge_date": days_to_ts(np.where(w.discharge_day <= d, w.discharge_day, NEVER)[cm]),
            "submission_date": days_to_ts(np.where(w.sub_day <= d, w.sub_day, NEVER)[cm]),
            "billed_amount_cents": billed_vis[cm],
            "expected_amount_cents": expected_vis[cm],
            "patient_responsibility_cents": np.where(w.pr_known_day <= d, w.pr_amount, 0)[cm],
            "status_code": status[cm],
            "first_pass_paid": (w.fpp & (w.remit1_day <= d))[cm],
            "clean_claim": (paid_vis & ~den_vis)[cm],
            "resolved_date": days_to_ts(np.where(w.resolved_day <= d, w.resolved_day, NEVER)[cm]),
        }

        # --- claim lines ----------------------------------------------------
        lm = line_vis
        adjudicated = w.remit1_day[w.line_claim] <= d
        allowed = np.where(adjudicated, w.line_allowed.astype(np.float64), np.nan)
        self.fact_claim_line: dict[str, Any] = {
            "claim_idx": w.line_claim[lm].astype(np.int64),
            "line_num": w.line_num[lm],
            "group_i": w.line_group_i[lm],
            "code_i": w.line_code_i[lm].astype(np.int64),
            "units": w.line_units[lm].astype(np.int64),
            "charge_entry_date": days_to_ts(w.line_charge_day[lm]),
            "billed_amount_cents": w.line_billed[lm],
            "allowed_amount_cents": allowed[lm],
            "service_date": days_to_ts(w.line_svc_day[lm]),
        }

        # --- remits ---------------------------------------------------------
        rm = w.remit_day <= d
        n_remits = len(w.remit_claim)
        self.fact_remit: dict[str, Any] = {
            "ridx": np.arange(n_remits, dtype=np.int64)[rm],
            "claim_idx": w.remit_claim[rm].astype(np.int64),
            "payer_i": w.payer_i[w.remit_claim[rm]],
            "remit_date": days_to_ts(w.remit_day[rm]),
            "remit_seq": w.remit_seq[rm],
        }

        # --- transactions ---------------------------------------------------
        tm = w.txn_post_day <= d
        has_line = w.txn_line >= 0
        txn_line_claim = np.where(has_line, w.line_claim[np.maximum(w.txn_line, 0)], -1)
        txn_line_num = np.where(has_line, w.line_num[np.maximum(w.txn_line, 0)], -1)
        n_txns = len(w.txn_claim)
        self.fact_transaction: dict[str, Any] = {
            "tidx": np.arange(n_txns, dtype=np.int64)[tm],
            "claim_idx": w.txn_claim[tm].astype(np.int64),
            "line_claim_idx": txn_line_claim[tm].astype(np.int64),
            "line_num": txn_line_num[tm].astype(np.int64),
            "remit_idx": w.txn_remit[tm].astype(np.int64),
            "type_code": w.txn_type[tm],
            "amount_cents": w.txn_amount[tm],
            "post_date": days_to_ts(w.txn_post_day[tm]),
            "remit_date": days_to_ts(w.txn_remit_day[tm]),
        }

        # --- denials --------------------------------------------------------
        dm = w.dn_day <= d
        has_dline = w.dn_line >= 0
        dn_line_claim = np.where(has_dline, w.line_claim[np.maximum(w.dn_line, 0)], -1)
        dn_line_num = np.where(has_dline, w.line_num[np.maximum(w.dn_line, 0)], -1)
        # Appeal state as visible at this cutoff.
        appealed_vis = w.dn_appealed & (w.dn_file_day <= d)
        decided_vis = appealed_vis & (w.dn_dec_day <= d)
        appeal_code = np.zeros(len(w.dn_claim), dtype=np.int64)  # NONE
        appeal_code[appealed_vis & ~decided_vis] = 1  # APPEALED
        appeal_code[decided_vis & w.dn_overturned] = 2  # OVERTURNED
        appeal_code[decided_vis & ~w.dn_overturned] = 3  # UPHELD
        dec_date = np.where(decided_vis, w.dn_dec_day, NEVER)
        n_denials = len(w.dn_claim)
        self.fact_denial: dict[str, Any] = {
            "didx": np.arange(n_denials, dtype=np.int64)[dm],
            "claim_idx": w.dn_claim[dm].astype(np.int64),
            "line_claim_idx": dn_line_claim[dm].astype(np.int64),
            "line_num": dn_line_num[dm].astype(np.int64),
            "remit_idx": w.dn_remit[dm].astype(np.int64),
            "group_code_i": w.dn_group[dm],
            "carc_code": w.dn_carc[dm],
            "rarc_i": w.dn_rarc[dm].astype(np.int64),
            "level_line": w.dn_level_line[dm],
            "denial_date": days_to_ts(w.dn_day[dm]),
            "denied_amount_cents": w.dn_amount[dm],
            "appeal_code": appeal_code[dm],
            "appeal_decision_date": days_to_ts(dec_date[dm]),
        }
