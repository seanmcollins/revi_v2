"""Answer-key cross-check — the third provenance.

``data/answer_key.json`` is written by the warehouse generator from its own
independent queries over the data it planted. Where an answer-key quantity is
expressible as a governed metric contract, the audit path re-derives it and
the two are compared. Three separate implementations then have to agree on the
same number: the generator's key, this harness's SQL, and (through the corpus
replay) the product.

Only mappings whose population is *pinned by the answer key itself* are
declared here. Two deliberate omissions, both recorded as first-class gaps
rather than skipped:

* **The ``anomalies`` block.** The generator's detector scopes denial
  anomalies by CARC (``WHERE {scope} AND carc_code = {spec.carc}`` in
  ``warehouse/generator/src/revi_warehouse/anomalies.py``) but the published
  anomaly record carries only ``dimensions`` — payer, service line, plan,
  facility — and never the CARC. The published key therefore does not pin the
  population its own numbers were computed over, so re-deriving them would be
  guesswork. Reported as ``answer_key_scope_incomplete``.
* **``monthly_by_first_remit`` series** (scenarios 1 and 4). These bucket by
  the month of a claim's *first* remit at CLAIM grain. The catalog binds no
  REMIT basis at the claim entity (``warehouse/catalog/date_bases.yaml``), so
  the quantity is not expressible as any contract on any legal basis.
  Reported as ``no_contract_equivalent``.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from revi_warehouse_diff.deriver import (
    NO_MUTATION,
    AuditContext,
    DerivationRun,
    Mutation,
    Predicate,
    Underivable,
)
from revi_warehouse_diff.governed import DEFAULT_ANSWER_KEY

SILVERLINE = "Silverline Medicare Advantage"
ATLAS = "Atlas Commercial"


@dataclass(frozen=True)
class KeyCheck:
    """One answer-key quantity and the contract that should reproduce it."""

    key_path: str
    metric_id: str
    window: tuple[dt.date, dt.date]
    basis: str
    scope: tuple[Predicate, ...]
    expected: Decimal
    compare: str = "numerator"  # "numerator" | "value"


@dataclass(frozen=True)
class KeyResult:
    check: KeyCheck | None
    outcome: str  # matched | diverged | underivable
    detail: str = ""
    derived: Any = None
    sql: tuple[str, ...] = ()


def _date(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _payer(name: str) -> tuple[Predicate, ...]:
    return (Predicate("payer", "eq", (name,)),)


def build_checks(answer_key: dict[str, Any], snapshot: str) -> list[KeyCheck]:
    """Declare the contract-expressible answer-key quantities for a snapshot."""
    checks: list[KeyCheck] = []
    scenarios = answer_key["scenarios"]

    # --- scenario 3: the planted cash decline (post basis, weekly) ---------
    cash = scenarios["3_cash_decline"][snapshot]
    for label in ("week_decline", "week_prior"):
        block = cash[label]
        window = (_date(block["start"]), _date(block["end"]))
        checks.append(
            KeyCheck(
                f"scenarios.3_cash_decline.{snapshot}.{label}.payer_cash_cents",
                "cash_posted",
                window,
                "post",
                (),
                Decimal(block["payer_cash_cents"]),
            )
        )
        checks.append(
            KeyCheck(
                f"scenarios.3_cash_decline.{snapshot}.patient_cash_cents.{label}",
                "patient_cash_posted",
                window,
                "post",
                (),
                Decimal(cash["patient_cash_cents"][label]),
            )
        )
    decline_window = (_date(cash["week_decline"]["start"]), _date(cash["week_decline"]["end"]))
    prior_window = (_date(cash["week_prior"]["start"]), _date(cash["week_prior"]["end"]))
    for row in cash["by_payer"]:
        name = row["payer_name"]
        checks.append(
            KeyCheck(
                f"scenarios.3_cash_decline.{snapshot}.by_payer[{name}].week_decline_cents",
                "cash_posted",
                decline_window,
                "post",
                _payer(name),
                Decimal(row["week_decline_cents"]),
            )
        )
        checks.append(
            KeyCheck(
                f"scenarios.3_cash_decline.{snapshot}.by_payer[{name}].week_prior_cents",
                "cash_posted",
                prior_window,
                "post",
                _payer(name),
                Decimal(row["week_prior_cents"]),
            )
        )
    for row in cash["atlas_submissions_by_week"]:
        start = _date(row["week_start"])
        checks.append(
            KeyCheck(
                f"scenarios.3_cash_decline.{snapshot}.atlas_submissions_by_week[{row['week_start']}]",
                "claim_volume",
                (start, start + dt.timedelta(days=6)),
                "submission",
                _payer(ATLAS),
                Decimal(row["claims_submitted"]),
            )
        )

    # --- scenario 2: the planted COB mismatch (service basis) --------------
    cob = scenarios["2_cob_silverline"][snapshot]
    window = (_date(cob["service_window"]["start"]), _date(cob["service_window"]["end"]))
    checks.append(
        KeyCheck(
            f"scenarios.2_cob_silverline.{snapshot}.silverline_claims_in_window",
            "claim_volume",
            window,
            "service",
            _payer(SILVERLINE),
            Decimal(cob["silverline_claims_in_window"]),
        )
    )
    checks.append(
        KeyCheck(
            f"scenarios.2_cob_silverline.{snapshot}.cob_mismatch_claims",
            "cob_mismatch_claims",
            window,
            "service",
            _payer(SILVERLINE),
            Decimal(cob["cob_mismatch_claims"]),
        )
    )
    checks.append(
        KeyCheck(
            f"scenarios.2_cob_silverline.{snapshot}.cob_mismatch_share",
            "cob_mismatch_rate",
            window,
            "service",
            _payer(SILVERLINE),
            Decimal(str(cob["cob_mismatch_share"])),
            compare="value",
        )
    )
    carc22 = (*_payer(SILVERLINE), Predicate("carc", "eq", ("22",)))
    checks.append(
        KeyCheck(
            f"scenarios.2_cob_silverline.{snapshot}.carc22_denials",
            "denied_claims",
            window,
            "service",
            carc22,
            Decimal(cob["carc22_denials"]),
        )
    )
    checks.append(
        KeyCheck(
            f"scenarios.2_cob_silverline.{snapshot}.carc22_denied_cents",
            "denied_dollars",
            window,
            "service",
            carc22,
            Decimal(cob["carc22_denied_cents"]),
        )
    )
    return checks


#: Answer-key quantities that exist but are NOT contract-expressible in v1.
#: Counted so the cross-check's coverage is stated rather than implied.
def declared_gaps(answer_key: dict[str, Any], snapshot: str) -> list[KeyResult]:
    out: list[KeyResult] = []
    scenarios = answer_key["scenarios"]
    for scenario in ("1_denial_spike_meridian_imaging", "4_underpayment_northbridge_ortho"):
        series = scenarios[scenario][snapshot].get("monthly_by_first_remit", ())
        for row in series:
            out.append(
                KeyResult(
                    None,
                    "underivable",
                    f"no_contract_equivalent: scenarios.{scenario}.{snapshot}."
                    f"monthly_by_first_remit[{row['remit_month']}] buckets by first-remit month "
                    "at CLAIM grain; the catalog binds no REMIT basis at the claim entity",
                )
            )
    timely = scenarios["5_timely_filing_state_medicaid_hmo"][snapshot]
    out.append(
        KeyResult(
            None,
            "underivable",
            "snapshot_contract: scenarios.5_timely_filing_state_medicaid_hmo."
            f"{snapshot}.at_risk_billed_cents ({timely['at_risk_billed_cents']}) is "
            "timely_filing_at_risk_dollars, a snapshot contract v1 refuses",
        )
    )
    for anomaly in answer_key["anomalies"][snapshot]:
        out.append(
            KeyResult(
                None,
                "underivable",
                f"answer_key_scope_incomplete: anomalies.{snapshot}.{anomaly['anomaly_id']} "
                f"({anomaly['metric_id']}) publishes dimensions "
                f"{sorted(anomaly['dimensions'])} but the detector also scopes by CARC, "
                "which the key does not publish",
            )
        )
    return out


def cross_check(
    run: DerivationRun,
    schema: str,
    watermark_id: str,
    snapshot: str,
    answer_key_path: Path | None = None,
    mutation: Mutation = NO_MUTATION,
) -> list[KeyResult]:
    answer_key = json.loads((answer_key_path or DEFAULT_ANSWER_KEY).read_text())
    results: list[KeyResult] = []
    for check in build_checks(answer_key, snapshot):
        ctx = AuditContext(
            schema=schema,
            watermark_id=watermark_id,
            window_start=check.window[0],
            window_end=check.window[1],
            published_basis=check.basis,
            scope=check.scope,
        )
        try:
            derivation = run.derive(check.metric_id, ctx, mutation)
        except Underivable as exc:
            results.append(KeyResult(check, "underivable", f"{exc.reason}:{exc.detail}"))
            continue
        observed = (
            derivation.numerator.value if check.compare == "numerator" else derivation.value
        )
        if check.compare == "value":
            ok = abs(observed - check.expected) <= Decimal("1e-6")
        else:
            ok = observed == check.expected
        results.append(
            KeyResult(
                check,
                "matched" if ok else "diverged",
                "" if ok else f"derived {observed} != answer key {check.expected}",
                observed,
                derivation.sql_blocks,
            )
        )
    results.extend(declared_gaps(answer_key, snapshot))
    return results
