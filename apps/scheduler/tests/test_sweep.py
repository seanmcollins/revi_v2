"""Cohort TTL sweep: argument parsing, the two halves, and the report.

No Postgres: the repository and the cohort store are replaced by small fakes.
The ``main`` tests point ``REVI_WAREHOUSE_PATH`` at a real but empty DuckDB
file, because a dry run now genuinely opens the warehouse — reading the
cohort registry is what makes the dry run truthful rather than a guess.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from revi_connector_duckdb import CohortInventory, CohortSweepResult
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.filters import Predicate, PredicateOp
from revi_kernel.refs import DimensionRef, EntityGrain, ReferentId, ReferentKind
from revi_scheduler.sweep import (
    CohortStoreChoice,
    SweepReport,
    build_cohort_store,
    build_parser,
    main,
    run_sweep,
    warehouse_path,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def _cohort(cohort_id: str) -> CohortRef:
    return CohortRef(
        id=cohort_id,
        definition=CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",)),
        ),
        origin=ReferentId(cohort_id.upper(), ReferentKind.COHORT),
        size=137,
    )


class FakeSweeper:
    """Stands in for ``DuckDbAnalyticalRepository`` — records every call so a
    dry run can prove it asked for one."""

    def __init__(
        self,
        expired: tuple[str, ...] = (),
        orphaned: tuple[str, ...] = (),
        *,
        before: CohortInventory | None = None,
    ) -> None:
        self._result = CohortSweepResult(expired=expired, orphaned=orphaned)
        self._before = before or CohortInventory(
            tables=len(expired) + len(orphaned), registered=len(expired), rows=99
        )
        self.calls: list[tuple[datetime, bool]] = []
        self.inventories = 0

    async def sweep_cohorts(self, now: datetime, *, dry_run: bool = False) -> CohortSweepResult:
        self.calls.append((now, dry_run))
        return self._result

    async def cohort_inventory(self) -> CohortInventory:
        self.inventories += 1
        return self._before if self.inventories == 1 else CohortInventory(tables=0, registered=0, rows=0)


class FakeCohorts:
    """The metadata half: returns the given ids as expired ``CohortRef``s."""

    def __init__(self, *cohort_ids: str) -> None:
        self._cohorts = tuple(_cohort(c) for c in cohort_ids)
        self.calls: list[datetime] = []

    async def save(self, cohort: CohortRef, *, tenant: str, session_id: str) -> None:
        raise AssertionError("the sweep never writes cohort metadata")

    async def get(self, cohort_id: str) -> CohortRef | None:
        raise AssertionError("the sweep never reads a single cohort")

    async def expired(self, now: datetime) -> tuple[CohortRef, ...]:
        self.calls.append(now)
        return self._cohorts


def _postgres(store: FakeCohorts) -> CohortStoreChoice:
    return CohortStoreChoice(store, "postgres")


class TestParser:
    def test_defaults(self) -> None:
        args = build_parser().parse_args([])
        assert args.now is None
        assert args.dry_run is False

    def test_naive_now_is_read_as_utc(self) -> None:
        args = build_parser().parse_args(["--now", "2026-08-07T12:00:00"])
        assert args.now == NOW

    def test_offset_aware_now_is_preserved(self) -> None:
        args = build_parser().parse_args(["--now", "2026-08-07T08:00:00-04:00"])
        assert args.now.utcoffset() == timedelta(hours=-4)
        assert args.now == NOW

    def test_dry_run_flag(self) -> None:
        assert build_parser().parse_args(["--dry-run"]).dry_run is True

    def test_unparseable_now_is_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["--now", "yesterday"])
        assert exc.value.code == 2


class TestRunSweep:
    async def test_drops_and_reports_both_halves(self) -> None:
        repository = FakeSweeper(("cohort_a", "cohort_b"))
        cohorts = FakeCohorts("cohort_a", "cohort_b")

        report = await run_sweep(repository, _postgres(cohorts), now=NOW, dry_run=False)

        assert repository.calls == [(NOW, False)]
        assert cohorts.calls == [NOW]
        assert report.dropped == ("cohort_a", "cohort_b")
        assert report.expired == ("cohort_a", "cohort_b")
        assert report.after.tables == 0  # the census is re-read after dropping

    async def test_dry_run_asks_the_connector_for_a_dry_run(self) -> None:
        repository = FakeSweeper(("cohort_a",))
        cohorts = FakeCohorts("cohort_a")

        report = await run_sweep(repository, _postgres(cohorts), now=NOW, dry_run=True)

        assert repository.calls == [(NOW, True)]  # truthful, and writes nothing
        assert report.dropped == ("cohort_a",)  # what a real run *would* drop
        assert report.after == report.before  # …so the census cannot have moved
        assert report.expired == ("cohort_a",)  # the metadata half still ran

    async def test_orphans_are_reported_apart_from_expiries(self) -> None:
        repository = FakeSweeper(("cohort_a",), ("cohort_legacy1",))

        report = await run_sweep(repository, _postgres(FakeCohorts()), now=NOW, dry_run=False)

        assert report.result.expired == ("cohort_a",)
        assert report.result.orphaned == ("cohort_legacy1",)
        assert report.dropped == ("cohort_a", "cohort_legacy1")

    async def test_without_a_store_the_metadata_half_is_none_not_zero(self) -> None:
        repository = FakeSweeper(("cohort_a",))

        report = await run_sweep(
            repository,
            CohortStoreChoice(None, "no database configured (REVI_DATABASE_URL unset)"),
            now=NOW,
            dry_run=False,
        )

        assert report.expired is None
        assert report.dropped == ("cohort_a",)


class TestSummary:
    BASE = SweepReport(
        now=NOW,
        dry_run=False,
        store_detail="postgres",
        result=CohortSweepResult(expired=("cohort_a",)),
        before=CohortInventory(tables=1, registered=1, rows=137),
        after=CohortInventory(tables=0, registered=0, rows=0),
        expired=("cohort_a",),
    )

    def _report(self, **overrides: object) -> SweepReport:
        return replace(self.BASE, **overrides)

    def test_headline_carries_the_instant(self) -> None:
        assert self._report().summary()[0] == f"cohort TTL sweep @ {NOW.isoformat()}"

    def test_agreeing_halves_need_no_note(self) -> None:
        text = "\n".join(self._report().summary())
        assert "dropped 1 expired + 0 orphaned cohort table(s)" in text
        assert "1 expired cohort record(s): cohort_a" in text
        assert "note:" not in text

    def test_the_census_brackets_the_sweep(self) -> None:
        text = "\n".join(self._report().summary())
        assert "before:    1 cohort table(s), 137 row(s), 1 registry row(s)" in text
        assert "after:     0 cohort table(s), 0 row(s), 0 registry row(s)" in text

    def test_dry_run_says_would_drop_not_dropped(self) -> None:
        text = "\n".join(self._report(dry_run=True, after=self.BASE.before).summary())
        assert "[DRY RUN]" in text
        assert "would drop 1 expired" in text
        assert "1 expired cohort record(s): cohort_a" in text

    def test_skipped_metadata_half_says_why(self) -> None:
        report = self._report(expired=None, store_detail="no database configured (REVI_DATABASE_URL unset)")
        text = "\n".join(report.summary())
        assert "metadata:  skipped — no database configured (REVI_DATABASE_URL unset)" in text

    def test_orphans_say_what_an_orphan_is(self) -> None:
        report = self._report(result=CohortSweepResult(orphaned=("cohort_legacy1",)), expired=())
        text = "\n".join(report.summary())
        assert "0 expired + 1 orphaned cohort table(s)" in text
        assert "no registry row" in text
        assert "cohort_legacy1" in text

    def test_a_long_orphan_list_is_elided_not_dumped(self) -> None:
        ids = tuple(f"cohort_{n:012x}" for n in range(40))
        text = "\n".join(self._report(result=CohortSweepResult(orphaned=ids), expired=()).summary())
        assert "(+35 more)" in text
        assert ids[39] not in text

    def test_disagreeing_halves_are_named_not_averaged(self) -> None:
        report = self._report(result=CohortSweepResult(expired=("cohort_b",)), expired=("cohort_a",))
        text = "\n".join(report.summary())
        assert "1 expired record(s) had no table dropped here" in text
        assert "cohort_a" in text
        assert "1 dropped table(s) had no expired record" in text
        assert "cohort_b" in text


class TestEnvironment:
    def test_warehouse_path_defaults_under_the_repo_root(self) -> None:
        path = warehouse_path({})
        assert path.name == "revi_warehouse.duckdb"
        assert path.parent.name == "data"
        assert path.is_absolute()

    def test_warehouse_path_honours_the_environment(self, tmp_path: Path) -> None:
        assert warehouse_path({"REVI_WAREHOUSE_PATH": str(tmp_path / "w.duckdb")}) == (tmp_path / "w.duckdb")

    def test_no_database_url_skips_the_metadata_half(self) -> None:
        choice = build_cohort_store({})
        assert choice.store is None
        assert "REVI_DATABASE_URL" in choice.detail

    def test_blank_database_url_is_treated_as_unset(self) -> None:
        assert build_cohort_store({"REVI_DATABASE_URL": "   "}).store is None

    def test_unreachable_database_skips_rather_than_crashes(self) -> None:
        # Port 1 on loopback refuses instantly; no server is contacted.
        choice = build_cohort_store({"REVI_DATABASE_URL": "postgresql+psycopg://revi:revi@127.0.0.1:1/revi"})
        assert choice.store is None
        assert "unreachable" in choice.detail


def _empty_warehouse(tmp_path: Path) -> Path:
    """A real, empty DuckDB file. The dry run genuinely opens it now: reading
    the cohort registry is what makes the dry run truthful."""
    import duckdb

    path = tmp_path / "revi_warehouse.duckdb"
    duckdb.connect(str(path)).close()
    return path


class TestMain:
    def test_missing_warehouse_exits_non_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--dry-run"], env={"REVI_WAREHOUSE_PATH": str(tmp_path / "absent.duckdb")})
        assert code == 2
        assert capsys.readouterr().out == ""  # no report is printed for a sweep that never ran

    def test_unreadable_warehouse_exits_non_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A file that exists but is not a database is a source failure, not a
        clean sweep of zero tables."""
        warehouse = tmp_path / "revi_warehouse.duckdb"
        warehouse.write_text("not a duckdb file")

        assert main(["--dry-run"], env={"REVI_WAREHOUSE_PATH": str(warehouse)}) == 1
        assert capsys.readouterr().out == ""

    def test_dry_run_end_to_end_without_a_database(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        warehouse = _empty_warehouse(tmp_path)

        code = main(
            ["--dry-run", "--now", "2026-08-07T12:00:00Z"], env={"REVI_WAREHOUSE_PATH": str(warehouse)}
        )

        out = capsys.readouterr().out
        assert code == 0
        assert f"cohort TTL sweep @ {NOW.isoformat()} [DRY RUN]" in out
        assert "would drop 0 expired + 0 orphaned cohort table(s)" in out
        assert "metadata:  skipped" in out

    def test_now_defaults_to_utc_now(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        warehouse = _empty_warehouse(tmp_path)
        before = datetime.now(UTC)

        assert main(["--dry-run"], env={"REVI_WAREHOUSE_PATH": str(warehouse)}) == 0

        headline = capsys.readouterr().out.splitlines()[0]
        stamped = datetime.fromisoformat(headline.split("@ ")[1].split(" [")[0])
        assert stamped.tzinfo == UTC
        assert before <= stamped <= datetime.now(UTC)
