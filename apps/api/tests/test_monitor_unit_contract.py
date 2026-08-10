"""The wire's monitor vocabulary must be the engine's monitor vocabulary.

Regression: ``days`` was legal in
:data:`revi_investigation.application.ports.MONITOR_THRESHOLD_UNITS` and absent
from the ``MonitorUnit`` literal on the wire. One token of skew took the pin
list to a 500 for a whole tenant, stored a pin while reporting it unstored, and
disabled the settings control on every tile. The two lists are asserted equal as
sets, in both directions, so the next unit added to one alone fails here.
"""

from __future__ import annotations

from typing import Literal, get_args, get_origin

from revi_investigation.application.ports import MONITOR_MODES, MONITOR_THRESHOLD_UNITS
from revi_investigation_contracts.api import MonitorMode, MonitorUnit


def test_wire_monitor_units_are_exactly_the_engine_s_monitor_units() -> None:
    assert get_origin(MonitorUnit) is Literal
    assert set(get_args(MonitorUnit)) == set(MONITOR_THRESHOLD_UNITS)


def test_wire_monitor_modes_are_exactly_the_engine_s_monitor_modes() -> None:
    assert get_origin(MonitorMode) is Literal
    assert set(get_args(MonitorMode)) == set(MONITOR_MODES)


def test_days_is_one_of_them() -> None:
    """The specific token that shipped skewed, named so the regression is
    readable in a failure log rather than inferred from a set difference."""
    assert "days" in get_args(MonitorUnit)
    assert "days" in MONITOR_THRESHOLD_UNITS
