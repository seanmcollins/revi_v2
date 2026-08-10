"""The wire's watch vocabulary must be the engine's watch vocabulary.

Round-8 FIX-3. ``days`` was legal in
:data:`revi_investigation.application.ports.WATCH_THRESHOLD_UNITS` — the
list the phrase parser, the pack and the materiality policy all read — and
absent from the ``RoundsWatchUnit`` literal on the wire. One token of skew
cost three separate failures on one live tenant:

* ``GET /v1/rounds/pins`` returned **500 for the whole tenant** off ONE
  stored ``days`` watch, because composing the list validates every pin's
  watch through ``RoundsWatchModel``;
* "tell me when it moves more than 2 days" STORED its pin and then reported
  ``watch_refused: not_stored``, because the confirmation payload could not
  be built after the write had already happened — and the analyst, told
  nothing was watched, said it again, storing a second pin;
* the settings control on every tile was disabled, because the client reads
  the pin list to render it.

An enum that exists twice is a defect waiting for the next unit. These are
asserted equal as SETS on every CI run, in both directions, so adding a unit
to either list without the other fails here rather than in a demo.
"""

from __future__ import annotations

from typing import Literal, get_args, get_origin

from revi_investigation.application.ports import WATCH_MODES, WATCH_THRESHOLD_UNITS
from revi_investigation_contracts.api import RoundsWatchMode, RoundsWatchUnit


def test_wire_watch_units_are_exactly_the_engine_s_watch_units() -> None:
    assert get_origin(RoundsWatchUnit) is Literal
    assert set(get_args(RoundsWatchUnit)) == set(WATCH_THRESHOLD_UNITS)


def test_wire_watch_modes_are_exactly_the_engine_s_watch_modes() -> None:
    assert get_origin(RoundsWatchMode) is Literal
    assert set(get_args(RoundsWatchMode)) == set(WATCH_MODES)


def test_days_is_one_of_them() -> None:
    """The specific token that shipped skewed, named so the regression is
    readable in a failure log rather than inferred from a set difference."""
    assert "days" in get_args(RoundsWatchUnit)
    assert "days" in WATCH_THRESHOLD_UNITS
