"""The Monitors walk: watermark-triggered, never fatal, switch-offable.

The same posture as the cohort sweep and for the same reason: a surface
whose evaluation only happens when somebody opens the app is not proactive,
and the deployment where that matters most is the idle one.

Four properties, each of which is a real failure mode rather than a
hypothesis:

* evaluation happens on watermark ADVANCE, not on every tick — re-running
  every monitor against data that has not changed is how a background loop
  becomes expensive enough to switch off;
* one tenant's broken monitor must not cost every other tenant their brief;
* a typo in the interval falls back LOUDLY rather than silently turning the
  proactive half of the product off;
* a process wired with no Monitors service says so and does nothing, instead
  of raising in a lifespan hook.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from revi_api.monitors_sweep import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    MonitorsSweepScheduler,
    evaluate_once,
    sweep_interval_seconds,
)
from revi_kernel.watermark import DataWatermark

WM_1 = DataWatermark(id="wm_001", loaded_at=datetime(2026, 8, 1, tzinfo=UTC),
                     newest_data_date=date(2026, 7, 31))
WM_2 = DataWatermark(id="wm_002", loaded_at=datetime(2026, 8, 2, tzinfo=UTC),
                     newest_data_date=date(2026, 8, 1))


class _Watermarks:
    def __init__(self, watermark: DataWatermark) -> None:
        self.watermark = watermark

    async def newest_watermark(self) -> DataWatermark:
        return self.watermark


class _Pins:
    def __init__(self, tenants: tuple[str, ...]) -> None:
        self._tenants = tenants

    async def tenants_with_pins(self) -> tuple[str, ...]:
        return self._tenants


class _Monitors:
    def __init__(self, *, failing: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, str]] = []
        self._failing = failing

    async def evaluate_load(
        self, tenant: str, watermark: DataWatermark, *, force: bool = False
    ) -> object:
        self.calls.append((tenant, watermark.id))
        if tenant in self._failing:
            raise RuntimeError("this tenant's monitor is broken")
        return None


class TestInterval:
    def test_the_default_applies_when_unset(self) -> None:
        assert sweep_interval_seconds({}) == DEFAULT_SWEEP_INTERVAL_SECONDS

    def test_zero_disables_the_loop(self) -> None:
        assert sweep_interval_seconds({"REVI_MONITORS_SWEEP_INTERVAL_SECONDS": "0"}) == 0

    def test_a_typo_falls_back_loudly_rather_than_disabling(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A typo must not quietly turn the proactive half of the product
        off — which is exactly the failure this module exists to prevent."""
        with caplog.at_level("ERROR"):
            value = sweep_interval_seconds({"REVI_MONITORS_SWEEP_INTERVAL_SECONDS": "fifteen"})
        assert value == DEFAULT_SWEEP_INTERVAL_SECONDS
        assert "is not a number" in caplog.text


class TestEvaluateOnce:
    async def test_it_walks_every_tenant_holding_a_monitor(self) -> None:
        monitors = _Monitors()
        reached = await evaluate_once(monitors, _Pins(("a", "b")), _Watermarks(WM_1))
        assert reached == "wm_001"
        assert monitors.calls == [("a", "wm_001"), ("b", "wm_001")]

    async def test_an_unadvanced_watermark_evaluates_nothing(self) -> None:
        """The interval decides how often the process ASKS; evaluation
        happens on advance."""
        monitors = _Monitors()
        reached = await evaluate_once(
            monitors, _Pins(("a",)), _Watermarks(WM_1), last_watermark_id="wm_001"
        )
        assert reached == "wm_001"
        assert monitors.calls == []

    async def test_an_advance_evaluates_again(self) -> None:
        monitors = _Monitors()
        reached = await evaluate_once(
            monitors, _Pins(("a",)), _Watermarks(WM_2), last_watermark_id="wm_001"
        )
        assert reached == "wm_002"
        assert monitors.calls == [("a", "wm_002")]

    async def test_one_broken_tenant_does_not_cost_the_others(self) -> None:
        monitors = _Monitors(failing=frozenset({"a"}))
        reached = await evaluate_once(monitors, _Pins(("a", "b")), _Watermarks(WM_1))
        assert reached == "wm_001"
        assert ("b", "wm_001") in monitors.calls

    async def test_no_tenant_with_a_monitor_is_a_no_op(self) -> None:
        monitors = _Monitors()
        reached = await evaluate_once(monitors, _Pins(()), _Watermarks(WM_1))
        assert reached == "wm_001"
        assert monitors.calls == []

    async def test_an_unwired_process_says_so_and_does_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A wiring failure must not raise inside a lifespan hook: the
        surface that answers questions has to come up regardless."""
        with caplog.at_level("WARNING"):
            reached = await evaluate_once(object(), object(), _Watermarks(WM_1))
        assert reached is None
        assert "NOT running in this process" in caplog.text


class TestScheduler:
    async def test_a_disabled_scheduler_evaluates_nothing_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        monitors = _Monitors()
        scheduler = MonitorsSweepScheduler(
            monitors, _Pins(("a",)), _Watermarks(WM_1), interval_seconds=0
        )
        with caplog.at_level("WARNING"):
            assert await scheduler.start() is None
        assert not scheduler.enabled
        assert monitors.calls == []
        assert "DISABLED" in caplog.text
        await scheduler.stop()

    async def test_startup_walks_the_current_load_once(self) -> None:
        """What makes a restarted deployment current: the load that landed
        while the process was down is walked before the first request."""
        monitors = _Monitors()
        scheduler = MonitorsSweepScheduler(
            monitors, _Pins(("a",)), _Watermarks(WM_1), interval_seconds=3600
        )
        try:
            assert await scheduler.start() == "wm_001"
            assert monitors.calls == [("a", "wm_001")]
            assert scheduler.last_watermark_id == "wm_001"
        finally:
            await scheduler.stop()
