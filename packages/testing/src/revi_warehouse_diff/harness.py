"""Wiring: build the audit path and run all three checks."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from revi_warehouse_diff.answer_key import KeyResult, cross_check
from revi_warehouse_diff.corpus import StoredInvestigation, load_corpus
from revi_warehouse_diff.deriver import NO_MUTATION, DerivationRun, Mutation, NaiveDeriver
from revi_warehouse_diff.goldens import GoldenResult, check_goldens
from revi_warehouse_diff.governed import load_catalog, load_contracts
from revi_warehouse_diff.replay import CorpusReplay, ReplayReport
from revi_warehouse_diff.warehouse import Warehouse

#: The watermark the corpus was overwhelmingly published at; the answer-key
#: cross-check runs against its snapshot.
DEFAULT_WATERMARK = "wm_003"


@dataclass
class HarnessResult:
    replay: ReplayReport
    goldens: list[GoldenResult]
    answer_key: list[KeyResult]

    @property
    def failed(self) -> bool:
        """LIVE divergences fail the run; fossils are reported, never dropped.

        A divergence on an answer published before the disclosure contract
        that governs it landed is a record of history, not a bug the engine
        would commit again (see :mod:`revi_warehouse_diff.archaeology`).
        Failing on those makes the harness permanently red for a reason
        nobody can fix, which is how a gate gets muted.
        """
        return bool(
            self.replay.live_divergences
            or [g for g in self.goldens if g.outcome not in ("matched", "refused_as_expected")]
            or [k for k in self.answer_key if k.outcome == "diverged"]
        )


def build_run(warehouse: Warehouse, schema: str) -> DerivationRun:
    contracts = load_contracts()
    catalog = load_catalog()
    deriver = NaiveDeriver(
        contracts,
        catalog,
        warehouse.columns(schema, catalog),
        warehouse.materialized_cohorts(),
    )
    return DerivationRun(deriver=deriver, execute=warehouse.scalar)


def _value_domain(warehouse: Warehouse) -> Callable[[str, str, str], list[str]]:
    """The distinct values a column actually holds — the §6.6 certified domain.

    Read from the warehouse, not from the catalog: the audit asks what the
    DATA says a value is, which is the whole question a published filter
    value has to answer.
    """

    def domain(schema: str, view: str, column: str) -> list[str]:
        rows = warehouse.rows(
            f"SELECT DISTINCT {column} FROM {schema}.{view} WHERE {column} IS NOT NULL"
        )
        return [str(row[0]) for row in rows]

    return domain


def run_harness(
    warehouse_path: Path | None = None,
    dsn: str | None = None,
    limit: int | None = None,
    mutation: Mutation = NO_MUTATION,
    corpus: list[StoredInvestigation] | None = None,
    skip_corpus: bool = False,
    skip_answer_key: bool = False,
    skip_goldens: bool = False,
    watermark: str = DEFAULT_WATERMARK,
    explain_divergences: bool = True,
) -> HarnessResult:
    started = time.monotonic()
    with Warehouse(warehouse_path) as warehouse:
        watermarks = warehouse.watermarks()
        schemas = {k: v.schema_name for k, v in watermarks.items()}
        schema = schemas[watermark]
        run = build_run(warehouse, schema)

        goldens = [] if skip_goldens else check_goldens(run, schemas, mutation=mutation)
        key_results = (
            []
            if skip_answer_key
            else cross_check(run, schema, watermark, schema, mutation=mutation)
        )

        report = ReplayReport()
        if not skip_corpus:
            stored = corpus if corpus is not None else load_corpus(dsn, limit)
            replay = CorpusReplay(
                run,
                load_contracts(),
                schemas,
                mutation=mutation,
                newest_data_date={k: v.newest_data_date for k, v in watermarks.items()},
                explain_divergences=explain_divergences,
                value_domain=_value_domain(warehouse),
            )
            replay.run(stored, report)
        report.warehouse_queries = warehouse.queries
        report.seconds = time.monotonic() - started
    return HarnessResult(replay=report, goldens=goldens, answer_key=key_results)
