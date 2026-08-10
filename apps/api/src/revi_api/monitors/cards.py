"""Decorating worklist cards with lead state, and the cash-timing lanes over them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import TYPE_CHECKING

from revi_api.monitors_policy import (
    MonitorsPolicy,
    time_to_impact_for,
)
from revi_investigation.application.ports import (
    AnomalyRecord,
)
from revi_investigation_contracts.api import (
    AnomalyCard,
    PortfolioLanePayload,
    PortfolioResponse,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    pass

from revi_api.monitors.common import _MonitorsBase
from revi_api.monitors.leads import _assert_no_confirmed_lead_in_feed, _publishable_lead_status


class _CardDecoration(_MonitorsBase):
    """Putting lead state onto the worklist cards the portfolio built."""

    # ------------------------------------------------ lead decoration for cards

    async def decorate_cards(self, tenant: str, portfolio: PortfolioResponse) -> PortfolioResponse:
        """Ride lead statuses onto the portfolio's cards (additive fields).

        The rail renders the lifecycle from the same payload it already
        fetches, and a card and a brief entry cannot disagree about whether
        somebody is working a lead.

        This is also where the leads panel gets its groups, and every card
        decorated here IS in this load's detection feed — so none of them may
        leave carrying a confirmation, or the panel would count a lead as
        "confirmed fixed" while it is open in the same load's feed. See
        :func:`_publishable_lead_status` and the assertion below it.
        """
        leads = await self.lead_states(tenant)
        if not leads:
            return portfolio
        items = []
        published: dict[str, str] = {}
        for card in portfolio.items:
            lead = leads.get(card.anomaly_id)
            if lead is None:
                items.append(card)
                continue
            status, note = _publishable_lead_status(
                lead, tenant=tenant, watermark_id=portfolio.watermark_id
            )
            published[card.anomaly_id] = status
            items.append(
                card.model_copy(
                    update={
                        "lead_status": status,
                        "lead_status_note": note or lead.note,
                        "lead_updated_at": lead.updated_at,
                    }
                )
            )
        _assert_no_confirmed_lead_in_feed(tenant, portfolio.watermark_id, published)
        return portfolio.model_copy(update={"items": items})


def annotate_time_to_impact(
    portfolio: PortfolioResponse,
    records: Mapping[str, AnomalyRecord],
    *,
    newest_data_date: date,
    policy: MonitorsPolicy,
) -> PortfolioResponse:
    """Publish each card's cash timing (additive; the ranking is untouched).

    ``anomaly_priority@3`` still decides the order. Time-to-impact is
    context a reader uses, not a silent re-rank: a rank change needs its own
    versioned formula decision, and smuggling urgency into an existing
    version would make two builds of the same data disagree with no version
    string to explain it.
    """
    if not policy.time_to_impact.categories:
        return portfolio
    items = []
    for card in portfolio.items:
        record = records.get(card.anomaly_id)
        if record is None:
            items.append(card)
            continue
        items.append(
            card.model_copy(
                update={
                    "time_to_impact": time_to_impact_for(
                        record,
                        newest_data_date=newest_data_date,
                        policy=policy.time_to_impact,
                    )
                }
            )
        )
    return portfolio.model_copy(
        update={"items": items, "cash_timing_lanes": _cash_timing_lanes(items)}
    )


#: The cash-timing partition, in render order, with the words a section
#: header should use. Pre-cash leads because it is the half a director can
#: still do something about, which is the question the split exists to
#: answer.
_CASH_LANES: tuple[tuple[str, str, str], ...] = (
    (
        "pre_cash",
        "Still catchable",
        "The cash effect has not landed yet. Working these changes what gets paid.",
    ),
    (
        "already_hit",
        "Already hit cash",
        "The cash effect has landed — a denial that did not pay, an allowance already "
        "taken. What is left is recovery, where a window is still open.",
    ),
    (
        "unknown",
        "No honest timing",
        "This platform has no basis for dating the cash effect on these, and each card "
        "says why. A guess here would be indistinguishable from the real dates beside it.",
    ),
)


def _cash_timing_lanes(cards: Sequence[AnomalyCard]) -> list[PortfolioLanePayload]:
    """The worklist split by WHEN the money moves, with its own totals.

    Every card already carries a governed :attr:`TimeToImpactPayload.lane`,
    but until it is totalled here "of everything on the worklist, how much
    has not hit cash yet, and when are the deadlines?" is answered with one
    undifferentiated figure and no deadlines at all.

    The horizon is built only from REAL dates a detector published — a
    filing limit, an appeal window — never from a projection, for the same
    reason :class:`TimeToImpactPayload` refuses to put a projection in
    ``deadline_date``.
    """
    lanes: list[PortfolioLanePayload] = []
    for lane_id, label, description in _CASH_LANES:
        members = [
            card
            for card in cards
            if (card.time_to_impact.lane if card.time_to_impact is not None else "unknown")
            == lane_id
        ]
        if not members:
            continue
        dated = [
            (card.time_to_impact.deadline_date, card.time_to_impact.days)
            for card in members
            if card.time_to_impact is not None
            and card.time_to_impact.deadline_date is not None
        ]
        # A recovery window is a real dated limit too, and on the
        # already-hit lane it is the ONLY one there is.
        dated += [
            (card.time_to_impact.recovery_deadline_date, card.time_to_impact.recovery_days)
            for card in members
            if card.time_to_impact is not None
            and card.time_to_impact.recovery_deadline_date is not None
        ]
        # The soonest limit somebody can still hit. Sorting on the soonest
        # limit FULL STOP puts a window that closed in April at the top of a
        # header, and "closes in -94 days" is not a horizon.
        open_dates = [pair for pair in dated if pair[1] is None or pair[1] >= 0]
        soonest = min(open_dates, default=None, key=lambda pair: pair[0])
        lanes.append(
            PortfolioLanePayload(
                id=lane_id,
                label=label,
                description=description,
                kind="cash_timing",
                anomaly_ids=[card.anomaly_id for card in members],
                item_count=len(members),
                impact_cents=sum(abs(card.impact_cents) for card in members),
                ranked_impact_cents=sum(abs(card.ranked_impact_cents) for card in members),
                recoverable_cents_estimate=sum(
                    card.recoverable_cents_estimate for card in members
                ),
                soonest_deadline_date=soonest[0] if soonest is not None else None,
                soonest_deadline_days=soonest[1] if soonest is not None else None,
                dated_item_count=len(dated),
                passed_deadline_count=len(dated) - len(open_dates),
            )
        )
    return lanes
