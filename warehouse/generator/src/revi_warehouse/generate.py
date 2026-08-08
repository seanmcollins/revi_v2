"""CLI: python -m revi_warehouse.generate --out data/revi_warehouse.duckdb [--small] [--verify]

Generates the deterministic mock warehouse (three snapshot schemas + watermarks),
computes the answer key from the written data, and optionally runs the
self-check suite (nonzero exit on any failure).
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from revi_warehouse.anomalies import inject_anomalies
from revi_warehouse.answer_key import compute_answer_key, write_answer_key
from revi_warehouse.config import GeneratorConfig, make_rng
from revi_warehouse.dims import build_dims
from revi_warehouse.verify import Check, run_verification
from revi_warehouse.world import build_world
from revi_warehouse.writer import write_warehouse


@dataclass(frozen=True)
class GenerationResult:
    db_path: Path
    answer_key_path: Path
    row_counts: dict[str, dict[str, int]]
    answer_key: dict[str, Any]
    seconds: float


def run_generation(
    config: GeneratorConfig, out_path: Path, answer_key_path: Path | None = None
) -> GenerationResult:
    """Generate warehouse + answer key. The single rng is created here and only here."""
    started = time.perf_counter()
    ak_path = answer_key_path if answer_key_path is not None else out_path.parent / "answer_key.json"
    rng = make_rng()
    dims = build_dims(config, rng)
    world = build_world(config, rng, dims)
    world = inject_anomalies(world, config)  # own RNG streams; base arrays untouched
    row_counts = write_warehouse(out_path, config, world)
    key = compute_answer_key(out_path, config)
    write_answer_key(key, ak_path)
    return GenerationResult(
        db_path=out_path,
        answer_key_path=ak_path,
        row_counts=row_counts,
        answer_key=key,
        seconds=time.perf_counter() - started,
    )


def _print_summary(result: GenerationResult) -> None:
    print(f"wrote {result.db_path} in {result.seconds:.1f}s")
    print(f"answer key: {result.answer_key_path}")
    for schema, tables in result.row_counts.items():
        total = sum(tables.values())
        print(f"  {schema}: {total} rows " + ", ".join(f"{t}={n}" for t, n in sorted(tables.items())))
    s3 = result.answer_key["scenarios"]["3_cash_decline"]["snap_003"]
    print(
        "scenario 3 @ snap_003: "
        f"prior week {s3['week_prior']['payer_cash_cents']} c, "
        f"decline week {s3['week_decline']['payer_cash_cents']} c, "
        f"delta {s3['delta_pct']:+.3%}"
    )


def _print_checks(checks: list[Check]) -> int:
    failures = [c for c in checks if not c.ok]
    for check in checks:
        marker = "ok " if check.ok else "FAIL"
        print(f"[{marker}] {check.name}" + ("" if check.ok else f" -- {check.detail}"))
    print(f"verification: {len(checks) - len(failures)}/{len(checks)} checks passed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revi_warehouse.generate", description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="DuckDB output path")
    parser.add_argument(
        "--answer-key", type=Path, default=None, help="answer key path (default: <out dir>/answer_key.json)"
    )
    parser.add_argument("--small", action="store_true", help="use the ~5%% small() preset")
    parser.add_argument("--verify", action="store_true", help="run self-check SQL; nonzero exit on failure")
    args = parser.parse_args(argv)

    config = GeneratorConfig.small() if args.small else GeneratorConfig()
    result = run_generation(config, args.out, args.answer_key)
    _print_summary(result)
    if args.verify:
        checks = run_verification(result.db_path, result.answer_key, config)
        return _print_checks(checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
