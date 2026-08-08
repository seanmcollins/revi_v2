"""Cohort reclamation in the API process (review finding D6).

The defect being pinned is not "the sweep drops the wrong tables" — it is
that the sweep was *never called* by the only long-lived process that creates
cohort tables. So the load-bearing assertions here are about the lifecycle:
that starting the app sweeps, that it keeps sweeping, that stopping it stops,
and that none of those can take the app down.

No warehouse and no DuckDB: the repository is a fake, and the app-level test
drives ``app.router.lifespan_context`` directly rather than standing up a
server.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from revi_api.app import create_app
from revi_api.cohort_sweep import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    SWEEP_INTERVAL_ENV,
    CohortSweepScheduler,
    sweep_interval_seconds,
    sweep_once,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


class FakeRepository:
    """Counts sweeps, and can be told to fail."""

    def __init__(self, *, dropped: tuple[str, ...] = (), fail: bool = False) -> None:
        self._dropped = dropped
        self._fail = fail
        self.calls: list[datetime] = []

    async def drop_expired_cohorts(self, now: datetime) -> tuple[str, ...]:
        self.calls.append(now)
        if self._fail:
            raise RuntimeError("warehouse is on fire")
        return self._dropped


class ReadOnlyRepository:
    """A repository that cannot materialize cohorts, and so has none to drop."""


async def _settle(predicate: Any, *, timeout: float = 2.0) -> None:
    """Wait for a background task to reach a state, without sleeping blindly."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("the sweep task never reached the expected state")
        await asyncio.sleep(0.005)


class TestInterval:
    def test_default_is_hourly(self) -> None:
        assert sweep_interval_seconds({}) == DEFAULT_SWEEP_INTERVAL_SECONDS

    def test_environment_wins(self) -> None:
        assert sweep_interval_seconds({SWEEP_INTERVAL_ENV: "90"}) == 90.0

    def test_zero_disables(self) -> None:
        assert sweep_interval_seconds({SWEEP_INTERVAL_ENV: "0"}) == 0.0

    def test_a_typo_falls_back_to_the_default_rather_than_disabling(self) -> None:
        """A mistyped interval must not silently turn the garbage collector
        off — that is the exact failure this feature exists to end."""
        assert sweep_interval_seconds({SWEEP_INTERVAL_ENV: "hourly"}) == DEFAULT_SWEEP_INTERVAL_SECONDS


class TestSweepOnce:
    async def test_drops_and_reports(self) -> None:
        repository = FakeRepository(dropped=("cohort_a", "cohort_b"))

        assert await sweep_once(repository, now=NOW) == ("cohort_a", "cohort_b")
        assert repository.calls == [NOW]

    async def test_a_repository_without_the_capability_is_named_not_crashed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            assert await sweep_once(ReadOnlyRepository(), now=NOW) == ()
        assert "ReadOnlyRepository" in caplog.text
        assert "NOT running" in caplog.text

    async def test_a_failing_sweep_is_swallowed(self, caplog: pytest.LogCaptureFixture) -> None:
        """Reclaiming storage must never take down the surface that answers
        questions."""
        with caplog.at_level("ERROR"):
            assert await sweep_once(FakeRepository(fail=True), now=NOW) == ()
        assert "cohort sweep failed" in caplog.text

    async def test_now_defaults_to_utc_now(self) -> None:
        repository = FakeRepository()
        before = datetime.now(UTC)

        await sweep_once(repository)

        assert before <= repository.calls[0] <= datetime.now(UTC)


class TestScheduler:
    async def test_start_sweeps_immediately(self) -> None:
        """The startup sweep is what reclaims whatever the last run leaked."""
        repository = FakeRepository(dropped=("cohort_a",))
        scheduler = CohortSweepScheduler(repository, interval_seconds=3600)

        assert await scheduler.start() == ("cohort_a",)
        assert len(repository.calls) == 1
        await scheduler.stop()

    async def test_the_loop_keeps_sweeping(self) -> None:
        repository = FakeRepository()
        scheduler = CohortSweepScheduler(repository, interval_seconds=0.01)

        await scheduler.start()
        await _settle(lambda: len(repository.calls) >= 3)
        await scheduler.stop()

    async def test_stop_cancels_the_task(self) -> None:
        repository = FakeRepository()
        scheduler = CohortSweepScheduler(repository, interval_seconds=0.01)
        await scheduler.start()
        await _settle(lambda: len(repository.calls) >= 2)

        await scheduler.stop()
        settled = len(repository.calls)
        await asyncio.sleep(0.05)

        assert len(repository.calls) == settled  # nothing runs after shutdown

    async def test_stop_is_safe_before_start(self) -> None:
        await CohortSweepScheduler(FakeRepository(), interval_seconds=0).stop()

    async def test_a_non_positive_interval_disables_the_whole_thing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        repository = FakeRepository()
        scheduler = CohortSweepScheduler(repository, interval_seconds=0)

        with caplog.at_level("WARNING"):
            assert await scheduler.start() == ()

        assert not scheduler.enabled
        assert repository.calls == []  # not even the startup sweep
        assert "DISABLED" in caplog.text
        await scheduler.stop()

    async def test_a_failing_sweep_does_not_kill_the_loop(self) -> None:
        repository = FakeRepository(fail=True)
        scheduler = CohortSweepScheduler(repository, interval_seconds=0.01)

        await scheduler.start()
        await _settle(lambda: len(repository.calls) >= 3)  # it kept going
        await scheduler.stop()


class TestAppLifespan:
    """The whole of D6's wiring half: entering the app's lifespan must sweep."""

    async def test_startup_sweeps_the_wired_repository(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import revi_api.app as app_module

        repository = FakeRepository(dropped=("cohort_a",))

        class StubComponents:
            def __init__(self) -> None:
                self.repository = repository

        monkeypatch.setattr(app_module, "build_components", lambda: cast(Any, StubComponents()))
        app = create_app(env={SWEEP_INTERVAL_ENV: "3600"})

        async with app.router.lifespan_context(app):
            assert len(repository.calls) == 1

    async def test_a_wiring_failure_does_not_stop_the_app_from_starting(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import revi_api.app as app_module

        def explode() -> Any:
            raise RuntimeError("no warehouse at that path")

        monkeypatch.setattr(app_module, "build_components", explode)
        app = create_app(env={SWEEP_INTERVAL_ENV: "3600"})

        with caplog.at_level("ERROR"):
            async with app.router.lifespan_context(app):
                pass  # the app came up

        assert "could not wire components at startup" in caplog.text

    async def test_the_interval_env_can_switch_reclamation_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import revi_api.app as app_module

        def explode() -> Any:  # never called: a disabled scheduler wires nothing
            raise AssertionError("components were wired for a disabled sweep")

        monkeypatch.setattr(app_module, "build_components", explode)
        app = create_app(env={SWEEP_INTERVAL_ENV: "0"})

        async with app.router.lifespan_context(app):
            pass
