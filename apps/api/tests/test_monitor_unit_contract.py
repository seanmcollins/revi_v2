"""The wire's monitor vocabulary must be the engine's monitor vocabulary.

Round-8 FIX-3. ``days`` was legal in
:data:`revi_investigation.application.ports.MONITOR_THRESHOLD_UNITS` — the
list the phrase parser, the pack and the materiality policy all read — and
absent from the ``MonitorUnit`` literal on the wire. One token of skew
cost three separate failures on one live tenant:

* ``GET /v1/monitors/pins`` returned **500 for the whole tenant** off ONE
  stored ``days`` monitor, because composing the list validates every pin's
  monitor through ``MonitorModel``;
* "tell me when it moves more than 2 days" STORED its pin and then reported
  ``monitor_refused: not_stored``, because the confirmation payload could not
  be built after the write had already happened — and the analyst, told
  nothing was monitored, said it again, storing a second pin;
* the settings control on every tile was disabled, because the client reads
  the pin list to render it.

An enum that exists twice is a defect waiting for the next unit. These are
asserted equal as SETS on every CI run, in both directions, so adding a unit
to either list without the other fails here rather than in a demo.
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
