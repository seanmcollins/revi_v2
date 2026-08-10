"""Human-verified goldens — the harness's anchor points.

``goldens.json`` holds numbers a human (or a persona reviewer running SQL by
hand) computed against the generated warehouse, each with the passage it came
from. Re-deriving them proves the *audit* path is still honest; the corpus
replay proves the *product* path agrees with the audit path. Together the two
close the triangle: human == audit == product.

Entries flagged ``v1_derivable: false`` record a human-verified number the v1
deriver deliberately refuses. The harness asserts the refusal *reason* instead
of the number, so a hole stays visible and cannot silently become coverage.
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

GOLDENS_PATH = Path(__file__).with_name("goldens.json")


@dataclass(frozen=True)
class Golden:
    id: str
    metric_id: str
    watermark: str
    window_start: dt.date
    window_end: dt.date
    basis: str | None
    scope: tuple[Predicate, ...]
    numerator: int | None
    denominator: int | None
    v1_derivable: bool
    expected_refusal: str
    provenance: str


@dataclass(frozen=True)
class GoldenResult:
    golden: Golden
    outcome: str  # matched | diverged | refused_as_expected | refusal_mismatch | error
    detail: str = ""
    sql: tuple[str, ...] = ()


def load_goldens(path: Path | None = None) -> list[Golden]:
    document = json.loads((path or GOLDENS_PATH).read_text())
    out: list[Golden] = []
    for raw in document["goldens"]:
        expect = raw.get("expect", {})
        out.append(
            Golden(
                id=str(raw["id"]),
                metric_id=str(raw["metric_id"]),
                watermark=str(raw["watermark"]),
                window_start=dt.date.fromisoformat(raw["window"]["start"]),
                window_end=dt.date.fromisoformat(raw["window"]["end"]),
                basis=raw.get("basis"),
                scope=tuple(
                    Predicate(str(p["dimension"]), str(p["op"]), tuple(p.get("values", ())))
                    for p in raw.get("scope", ())
                ),
                numerator=expect.get("numerator"),
                denominator=expect.get("denominator"),
                v1_derivable=bool(raw.get("v1_derivable", True)),
                expected_refusal=str(raw.get("expected_refusal", "")),
                provenance=str(raw.get("provenance", "")),
            )
        )
    return out


def check_goldens(
    run: DerivationRun,
    schema_for_watermark: dict[str, str],
    goldens: list[Golden] | None = None,
    mutation: Mutation = NO_MUTATION,
) -> list[GoldenResult]:
    results: list[GoldenResult] = []
    for golden in goldens if goldens is not None else load_goldens():
        schema = schema_for_watermark.get(golden.watermark)
        if schema is None:
            results.append(GoldenResult(golden, "error", f"unknown watermark {golden.watermark}"))
            continue
        ctx = AuditContext(
            schema=schema,
            watermark_id=golden.watermark,
            window_start=golden.window_start,
            window_end=golden.window_end,
            published_basis=golden.basis,
            scope=golden.scope,
        )
        try:
            derivation = run.derive(golden.metric_id, ctx, mutation)
        except Underivable as exc:
            if golden.v1_derivable:
                results.append(GoldenResult(golden, "error", f"unexpected refusal {exc.reason}"))
            elif exc.reason == golden.expected_refusal:
                results.append(GoldenResult(golden, "refused_as_expected", exc.reason))
            else:
                results.append(
                    GoldenResult(
                        golden,
                        "refusal_mismatch",
                        f"expected {golden.expected_refusal}, got {exc.reason}",
                    )
                )
            continue
        if not golden.v1_derivable:
            results.append(
                GoldenResult(golden, "refusal_mismatch", "derived a value v1 claims to refuse")
            )
            continue
        problems: list[str] = []
        if golden.numerator is not None and derivation.numerator.value != Decimal(golden.numerator):
            problems.append(f"numerator {derivation.numerator.value} != {golden.numerator}")
        if golden.denominator is not None:
            observed = derivation.denominator.value if derivation.denominator else None
            if observed != Decimal(golden.denominator):
                problems.append(f"denominator {observed} != {golden.denominator}")
        results.append(
            GoldenResult(
                golden,
                "diverged" if problems else "matched",
                "; ".join(problems),
                derivation.sql_blocks,
            )
        )
    return results


def golden_counts(results: list[GoldenResult]) -> dict[str, int]:
    counts: dict[str, Any] = {}
    for result in results:
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    return counts
