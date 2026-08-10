"""Shared state and small helpers used by every part of Monitors."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from revi_api.monitors_policy import (
    MonitorsPolicy,
)
from revi_investigation_contracts.api import (
    PortfolioResponse,
)
from revi_kernel.errors import ErrorCode, ReviError
from revi_kernel.watermark import DataWatermark

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    from revi_api.wiring import ApiComponents


logger = logging.getLogger("revi.api.monitors")


#: Builds the ranked portfolio for one tenant at one watermark. Supplied by
#: :class:`~revi_api.service.ApiService` rather than re-implemented here, so
#: a brief's "new lead" and the rail's card are the same object from the
#: same build — the rule the conversational worklist already follows.
PortfolioFor = Callable[[str, DataWatermark], Awaitable[PortfolioResponse]]


class _MonitorsBase:
    """The state every section of :class:`~revi_api.monitors.MonitorsService` reads.

    Declarations only: :class:`MonitorsService` assigns them in its
    constructor. The service is assembled from one class per section — pins,
    tiles, the brief, leads, card decoration — so each section stays readable
    on its own without any of them being a separate object with its own
    lifetime.
    """

    _components: ApiComponents
    _portfolio_for: PortfolioFor
    policy: MonitorsPolicy


class MonitorsNotFoundError(ReviError):
    """A pin, lead or load the caller named does not exist (HTTP 404).

    ``REFERENT_NOT_FOUND`` for the same reason
    :class:`~revi_api.service.NotFoundError` uses it: "the thing you named
    does not exist here" is one failure whether the handle is a pin id or a
    watermark, and ``UNSUPPORTED_CONCEPT`` would say something different and
    false.
    """

    code = ErrorCode.REFERENT_NOT_FOUND


def _plural(count: int, singular: str, plural: str) -> str:
    """``2 new leads``. Never ``2 new lead(s)``: the parenthetical plural is
    the mark of a sentence a machine wrote."""
    return f"{count} {singular if count == 1 else plural}"


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _utc(value: datetime) -> datetime:
    """Interpret a naive datetime as UTC before comparing it to anything.

    The two sides of "is this load older than that one?" genuinely arrive
    with different awareness: the DuckDB connector reads a warehouse
    ``TIMESTAMP`` and hands back a NAIVE ``loaded_at``, while a stored
    ``timestamptz`` comes back AWARE. Comparing them raises — and only
    against a real database, since the in-memory store round-trips whatever
    it was given.

    Normalised here, at the comparison, on the same convention the Postgres
    adapters already use for their typed columns (a naive value is UTC).
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _monitors_warnings(policy: MonitorsPolicy) -> list[str]:
    if policy.enabled:
        return []
    return [
        "population_caveat: this deployment's pack ships no governed Monitors content, so no "
        "materiality gate was applied and no time-to-impact was derived — every movement is "
        "reported as measured, and nothing here has been judged material"
    ]
