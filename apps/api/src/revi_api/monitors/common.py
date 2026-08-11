"""Shared state and small helpers used by every part of Monitors."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from revi_api.monitors_policy import (
    MonitorsPolicy,
)
from revi_investigation_contracts.api import (
    PortfolioResponse,
    TypedInvestigationSpec,
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


#: Do the values a monitored spec names still EXIST at this load? Returns the
#: platform's own refusal sentence when one of them matches nothing at this
#: watermark, and ``None`` when they all resolve (or when the question could
#: not be put to the source, which is not evidence of absence).
#:
#: Supplied by the composition root rather than re-implemented here: it is the
#: §6.6 value-resolution pass a live turn already runs, stopped before
#: execution — so "this payer is gone" is decided by the one component that
#: decides it for an answer, and the tile and the answer cannot disagree.
SubjectPresenceProbe = Callable[
    [TypedInvestigationSpec, DataWatermark], Awaitable[str | None]
]


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


def _load_phrase(data_date: date | None, *, unknown: str = "the previous load") -> str:
    """A load, named the way a reader names it: by its data date.

    Lives here rather than in one section, because every section says it:
    the brief names the load it diffs against, a tile names the load that
    produced no value, and a lead's verdict names the load that reached it.
    A warehouse handle (``wm_003``) is not a name a reader has ever seen.

    ``unknown`` is what to say when the load recorded no data date, and it
    is the CALLER's word: a brief looking backwards means "the previous
    load", while a verdict being written at this load means "this load".
    """
    if data_date is None:
        return unknown
    return f"the {data_date:%b %-d} load"


def _date_range_phrase(start: date, end: date) -> str:
    """A window, said the way a reader says it: Jul 1 to Aug 2, 2026.

    (With an en dash between the ends, not the word "to".)
    ``2026-07-01..2026-07-31`` is the Evidence rail's form and belongs
    there; on a default surface it is two machine tokens and a typo. The
    year is said once when both ends share it.
    """
    if start.year == end.year:
        return f"{start:%b %-d} – {end:%b %-d}, {end:%Y}"  # noqa: RUF001 - en dash, a date range
    return f"{start:%b %-d, %Y} – {end:%b %-d, %Y}"  # noqa: RUF001 - en dash, a date range


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
    # The ``population_caveat:`` prefix is machine-facing — `warning_codes`
    # matches on it and strips it — and everything after the colon is
    # written for the reader.
    return [
        "population_caveat: your definitions library has no monitoring rules set up here, so "
        "nothing has been judged big enough to flag and no cash timing was worked out. Every "
        "movement is reported as measured."
    ]
