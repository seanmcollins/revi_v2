"""Report rendering. Every divergence carries the audit SQL beside it."""

from __future__ import annotations

from collections import Counter
from typing import Any

from revi_warehouse_diff.answer_key import KeyResult
from revi_warehouse_diff.archaeology import DISCLOSURE_CONTRACT_SINCE, DISCLOSURE_FIXES
from revi_warehouse_diff.goldens import GoldenResult
from revi_warehouse_diff.replay import BASIS_AMBIGUOUS, BOUND_UPHELD, ROUNDED_INPUTS, ReplayReport

RULE = "=" * 78


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "n/a"


def divergence_class(item: Any) -> str:
    """Group a divergence by what the explainer was able to say about it.

    The classes are the fix queue: each names a distinct way a published
    number can fail to be its contract's reading of the context the answer
    disclosed.
    """
    reason = item.reason or ""
    if "reproduced exactly" in reason:
        return "A. number is correct over a window/basis the answer did not disclose"
    if "published filter" in reason:
        return "B. published filter value does not exist in the data (case)"
    if item.value_name.endswith(("__prior", "__delta")) or item.value_name in (
        "prior_cents",
        "delta_cents",
        "pct_change",
    ):
        return "C. period comparison: the prior-window component does not reproduce"
    return "D. level divergence with no explanation found"


def render(
    replay: ReplayReport,
    goldens: list[GoldenResult],
    key_results: list[KeyResult],
    show_sql: int = 10,
) -> str:
    lines: list[str] = []
    add = lines.append

    add(RULE)
    add("FN-17 WAREHOUSE-DIFF — structurally independent recomputation of every")
    add("published finding value, anchored at human-verified points.")
    add(RULE)

    # --- goldens ---------------------------------------------------------
    golden_counts = Counter(r.outcome for r in goldens)
    add("")
    add("1. HUMAN-VERIFIED GOLDENS (audit path vs numbers a human computed)")
    add(f"   total {len(goldens)}  " + "  ".join(f"{k}={v}" for k, v in sorted(golden_counts.items())))
    for result in goldens:
        if result.outcome in ("matched", "refused_as_expected"):
            continue
        add(f"   ! {result.golden.id}: {result.outcome} — {result.detail}")
        for block in result.sql[:2]:
            add("     " + block.replace("\n", "\n     "))

    # --- answer key ------------------------------------------------------
    key_counts = Counter(r.outcome for r in key_results)
    add("")
    add("2. ANSWER-KEY CROSS-CHECK (audit path vs the generator's own key)")
    add(f"   total {len(key_results)}  " + "  ".join(f"{k}={v}" for k, v in sorted(key_counts.items())))
    gap_reasons = Counter(
        r.detail.split(":")[0] for r in key_results if r.outcome == "underivable"
    )
    for reason, count in gap_reasons.most_common():
        add(f"   - underivable/{reason}: {count}")
    for result in key_results:
        if result.outcome != "diverged" or result.check is None:
            continue
        add(f"   ! {result.check.key_path}: {result.detail}")
        for block in result.sql[:2]:
            add("     " + block.replace("\n", "\n     "))

    # --- corpus ----------------------------------------------------------
    counts = replay.counts()
    total = len(replay.audited)
    add("")
    add("3. CORPUS REPLAY (audit path vs every published finding value)")
    add(
        f"   investigations {replay.investigations}  findings {replay.findings}  "
        f"published values {replay.values_seen}"
    )
    add(f"   audited        {total}")
    outcomes = (
        "matched",
        BASIS_AMBIGUOUS,
        ROUNDED_INPUTS,
        BOUND_UPHELD,
        "diverged",
        "underivable",
        "error",
    )
    for outcome in outcomes:
        add(f"   {outcome:<22} {counts.get(outcome, 0):>6}  {_pct(counts.get(outcome, 0), total)}")
    reproduced = (
        counts.get("matched", 0)
        + counts.get(BASIS_AMBIGUOUS, 0)
        + counts.get(ROUNDED_INPUTS, 0)
        + counts.get(BOUND_UPHELD, 0)
    )
    derivable = reproduced + counts.get("diverged", 0)
    add(
        f"   coverage: {derivable}/{total} values were derivable by v1 "
        f"({_pct(derivable, total)}); of those, {_pct(reproduced, derivable)} reproduced."
    )
    rounded = [a for a in replay.audited if a.outcome == ROUNDED_INPUTS]
    if rounded:
        add("")
        add(f"   rounded-input arithmetic: {len(rounded)} delta/percent-change values are the")
        add("   arithmetic over the finding's own 6-dp-rounded components rather than over")
        add("   the raw ratio-of-sums. Example:")
        add(f"     {rounded[0].investigation_id} {rounded[0].referent} {rounded[0].value_name}: "
            f"{rounded[0].reason}")
    add("")
    add("   refusal reasons (the coverage v1 does NOT claim):")
    for reason, count in replay.reasons().most_common():
        add(f"     {count:>6}  {reason}")

    ambiguous = [a for a in replay.audited if a.outcome == BASIS_AMBIGUOUS]
    if ambiguous:
        add("")
        add("   basis-ambiguous cells (reproduced exactly, but only on a basis the")
        add("   answer's context header does not name — a disclosure defect):")
        by_metric = Counter(f"{a.metric_id} published={a.published_basis} read={a.basis}" for a in ambiguous)
        for label, count in by_metric.most_common(15):
            add(f"     {count:>6}  {label}")

    divergences = replay.divergences
    live = replay.live_divergences
    fossils = replay.archaeology_divergences
    add("")
    add(f"   DIVERGENCES: {len(divergences)}  (live {len(live)} · archaeology {len(fossils)})")
    add(
        f"   disclosure contract in force since {DISCLOSURE_CONTRACT_SINCE.isoformat()} — an "
        "answer published"
    )
    add("   before it was written under a different one and is dated, not excused:")
    for fix in DISCLOSURE_FIXES:
        add(f"     {fix.landed.date()}  {fix.code} ({fix.commit}): {fix.what}")
    if live:
        add("")
        add("   LIVE — the engine would publish these again today:")
        for label, count in Counter(divergence_class(a) for a in live).most_common():
            add(f"     {count:>6}  {label}")
    if fossils:
        add("")
        add("   ARCHAEOLOGY — published before the disclosure fix that covers them:")
        for label, count in Counter(divergence_class(a) for a in fossils).most_common():
            add(f"     {count:>6}  {label}")
    # Live first: the fix queue leads, the fossils follow.
    for item in (live + fossils)[:show_sql]:
        add("")
        add(
            f"   ! [{item.era}] {item.investigation_id} {item.referent} {item.value_name} "
            f"({item.metric_id})"
        )
        add(f"     question:  {item.question}")
        add(f"     finding:   {item.finding_title}")
        add(f"     slice:     {', '.join(item.coordinate) or '(ungrouped)'}"
            f"   [confirmed by {item.coordinate_confirmed_by or 'n/a'}]")
        add(f"     window:    {item.window}  basis published={item.published_basis} read={item.basis}")
        add(f"     published: {item.published}")
        add(f"     derived:   {item.derived}")
        if item.reason:
            add(f"     reason:    {item.reason}")
        for block in item.sql:
            add("     " + block.replace("\n", "\n     "))
    if len(divergences) > show_sql:
        add(f"   … {len(divergences) - show_sql} more (see the JSON report)")

    add("")
    add(RULE)
    add(
        f"warehouse queries {replay.warehouse_queries}   wall clock {replay.seconds:.1f}s"
    )
    verdict = (
        "PASS"
        if not live and not golden_counts.get("diverged") and not key_counts.get("diverged")
        else "FAIL"
    )
    add(f"VERDICT: {verdict}")
    add(RULE)
    return "\n".join(lines)


def as_json(
    replay: ReplayReport, goldens: list[GoldenResult], key_results: list[KeyResult]
) -> dict[str, Any]:
    return {
        "goldens": {
            "counts": dict(Counter(r.outcome for r in goldens)),
            "failures": [
                {"id": r.golden.id, "outcome": r.outcome, "detail": r.detail, "sql": list(r.sql)}
                for r in goldens
                if r.outcome not in ("matched", "refused_as_expected")
            ],
        },
        "answer_key": {
            "counts": dict(Counter(r.outcome for r in key_results)),
            "failures": [
                {
                    "key_path": r.check.key_path if r.check else None,
                    "detail": r.detail,
                    "sql": list(r.sql),
                }
                for r in key_results
                if r.outcome == "diverged"
            ],
            "gaps": [r.detail for r in key_results if r.outcome == "underivable"],
        },
        "corpus": {
            "investigations": replay.investigations,
            "findings": replay.findings,
            "published_values": replay.values_seen,
            "audited": len(replay.audited),
            "counts": dict(replay.counts()),
            "live_divergences": len(replay.live_divergences),
            "archaeology_divergences": len(replay.archaeology_divergences),
            "disclosure_contract_since": DISCLOSURE_CONTRACT_SINCE.isoformat(),
            "refusal_reasons": dict(replay.reasons()),
            "seconds": replay.seconds,
            "warehouse_queries": replay.warehouse_queries,
            "divergences": [
                {
                    "investigation_id": a.investigation_id,
                    "era": a.era,
                    "session_id": a.session_id,
                    "question": a.question,
                    "referent": a.referent,
                    "finding_title": a.finding_title,
                    "value_name": a.value_name,
                    "metric_id": a.metric_id,
                    "window": a.window,
                    "slice": list(a.coordinate),
                    "coordinate_confirmed_by": a.coordinate_confirmed_by,
                    "published_basis": a.published_basis,
                    "basis_read": a.basis,
                    "published": str(a.published),
                    "derived": str(a.derived),
                    "reason": a.reason,
                    "audit_sql": list(a.sql),
                }
                for a in replay.divergences
            ],
            "basis_ambiguous": [
                {
                    "investigation_id": a.investigation_id,
                    "referent": a.referent,
                    "metric_id": a.metric_id,
                    "value_name": a.value_name,
                    "published_basis": a.published_basis,
                    "basis_read": a.basis,
                    "slice": list(a.coordinate),
                }
                for a in replay.audited
                if a.outcome == BASIS_AMBIGUOUS
            ],
        },
    }
