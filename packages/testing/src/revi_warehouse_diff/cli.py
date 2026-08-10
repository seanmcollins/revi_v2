"""``python -m revi_warehouse_diff`` — the ``make warehouse-diff`` entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from revi_warehouse_diff.harness import DEFAULT_WATERMARK, run_harness
from revi_warehouse_diff.report import as_json, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="revi_warehouse_diff",
        description=(
            "Recompute every published finding value by a structurally independent "
            "SQL path and diff it against what the product published."
        ),
    )
    parser.add_argument("--warehouse", type=Path, default=None, help="DuckDB file (default: data/)")
    parser.add_argument("--dsn", default=None, help="Postgres DSN for the stored corpus")
    parser.add_argument("--limit", type=int, default=None, help="cap investigations replayed")
    parser.add_argument("--watermark", default=DEFAULT_WATERMARK)
    parser.add_argument("--json", type=Path, default=None, help="write the machine report here")
    parser.add_argument("--show-sql", type=int, default=10, help="divergences to print SQL for")
    parser.add_argument("--skip-corpus", action="store_true")
    parser.add_argument("--skip-answer-key", action="store_true")
    parser.add_argument("--skip-goldens", action="store_true")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="always exit 0 (reporting mode; CI uses the default)",
    )
    args = parser.parse_args(argv)

    result = run_harness(
        warehouse_path=args.warehouse,
        dsn=args.dsn,
        limit=args.limit,
        watermark=args.watermark,
        skip_corpus=args.skip_corpus,
        skip_answer_key=args.skip_answer_key,
        skip_goldens=args.skip_goldens,
    )
    print(render(result.replay, result.goldens, result.answer_key, show_sql=args.show_sql))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(as_json(result.replay, result.goldens, result.answer_key), indent=2)
        )
        print(f"machine report: {args.json}")
    if result.failed and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
