"""The lead lifecycle: claiming a fix, and the platform confirming it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from revi_api.auth import Principal
from revi_api.monitors_policy import (
    ResolutionPolicy,
)
from revi_investigation.application.ports import (
    LEAD_STATUSES_HUMAN_SETTABLE,
    MonitorsLead,
    MonitorsLoad,
)
from revi_investigation.application.rendering import (
    magnitude,
)
from revi_investigation_contracts.api import (
    AnomalyCard,
    PortfolioResponse,
)
from revi_investigation_contracts.monitors import (
    MonitorsLeadPatchRequest,
    MonitorsLeadPayload,
)
from revi_kernel.errors import PolicyDeniedError, ReviError
from revi_kernel.watermark import DataWatermark

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    pass

from revi_api.monitors.common import MonitorsNotFoundError, _MonitorsBase, _plural, _utc, logger


@dataclass(frozen=True, slots=True)
class _Verification:
    """One lead's verification outcome at one load: the updated lead, and
    the brief entry it earned (``None`` when it earned none — an
    unconfirmed claim is a state change, not news).

    ``changed`` is False when this load had nothing to add, in which case
    the lead is NOT written back: re-reading the surface at the same load
    must not rewrite a sentence into a weaker one.
    """

    lead: MonitorsLead
    entry: dict[str, Any] | None
    changed: bool = True


class _LeadLifecycle(_MonitorsBase):
    """Reading, patching and verifying leads across loads."""

    # ------------------------------------------------------ lead lifecycle

    async def lead_states(self, tenant: str) -> dict[str, MonitorsLead]:
        """Every lead status this tenant holds, for decorating cards."""
        return {
            lead.anomaly_id: lead
            for lead in await self._components.monitors_leads.list_for_tenant(tenant)
        }

    async def patch_lead(
        self,
        principal: Principal,
        anomaly_id: str,
        request: MonitorsLeadPatchRequest,
        *,
        watermark: DataWatermark | None = None,
    ) -> MonitorsLeadPayload:
        """Move one lead along its lifecycle.

        Only the four human-settable statuses are accepted: confirmation is
        a measurement across two loads, and a lead that could be confirmed
        by assertion would make the whole verification path decorative.

        ``watermark`` names the load the claim is made AT — the newest one
        for a real request, and an explicit one for the simulated-load
        suite, which has to be able to claim at wm_002 and confirm at
        wm_003 through this same code.
        """
        tenant = principal.tenant
        if request.status not in LEAD_STATUSES_HUMAN_SETTABLE:
            raise PolicyDeniedError(
                f"{request.status!r} is a verdict this platform reaches from data, not a "
                "status a person may set: claim the resolution and the next loads confirm it "
                "or refuse it",
                details={"anomaly_id": anomaly_id, "status": request.status},
            )
        if watermark is None:
            watermark = await self._components.open_session.newest_watermark()
        portfolio = await self._portfolio_for(tenant, watermark)
        card = next((c for c in portfolio.items if c.anomaly_id == anomaly_id), None)
        if card is None:
            raise MonitorsNotFoundError(
                f"{anomaly_id!r} is not in the detection feed at watermark {watermark.id}; a "
                "lead that is not detected cannot have its status changed",
                details={"anomaly_id": anomaly_id, "watermark_id": watermark.id},
            )
        existing = await self._components.monitors_leads.get(tenant, anomaly_id)
        previous = existing.status if existing is not None else "open"
        now = datetime.now(UTC)
        baseline_cents: int | None = existing.baseline_cents if existing else None
        baseline_basis = existing.baseline_basis if existing else ""
        claimed_at = existing.claimed_at_watermark if existing else None
        confirming: tuple[str, ...] = existing.confirming_watermarks if existing else ()
        verification_note = existing.verification_note if existing else ""

        if request.status == "resolved_claimed":
            # The baseline is captured at the CLAIM, from the platform's own
            # re-derivation of the lead's drill — not the detector's figure.
            # Verification then measures like against like: this platform's
            # number at the claim load against this platform's number now.
            baseline_cents, baseline_basis = await self._claim_baseline(card, watermark)
            claimed_at = watermark.id
            confirming = ()
            verification_note = (
                "resolution claimed; this platform will re-run the lead's own drill at each "
                f"load and confirm only after {self.policy.resolution.consecutive_loads_required}"
                " consecutive loads verify it"
            )
        elif request.status != previous:
            # Moving off a claim discards the verification in progress: the
            # streak measured a claim that no longer stands.
            claimed_at = None
            baseline_cents = None
            baseline_basis = ""
            confirming = ()
            verification_note = ""

        lead = MonitorsLead(
            tenant=tenant,
            anomaly_id=anomaly_id,
            status=request.status,
            updated_at=now,
            note=request.note,
            claimed_at_watermark=claimed_at,
            baseline_cents=baseline_cents,
            baseline_basis=baseline_basis,
            confirming_watermarks=confirming,
            verification_note=verification_note,
            history=(
                *(existing.history if existing is not None else ()),
                {
                    "at": now.isoformat(),
                    "watermark_id": watermark.id,
                    "from": previous,
                    "to": request.status,
                    "by": principal.subject or principal.tenant,
                    "note": request.note,
                },
            ),
        )
        await self._components.monitors_leads.put(lead)
        logger.info(
            "monitors lead %s for tenant %s: %s -> %s", anomaly_id, tenant, previous, request.status
        )
        return lead_payload(lead, self.policy.resolution.consecutive_loads_required)

    async def get_lead(self, principal: Principal, anomaly_id: str) -> MonitorsLeadPayload:
        lead = await self._components.monitors_leads.get(principal.tenant, anomaly_id)
        if lead is None:
            raise MonitorsNotFoundError(
                f"no lifecycle state is recorded for lead {anomaly_id!r}",
                details={"anomaly_id": anomaly_id},
            )
        return lead_payload(lead, self.policy.resolution.consecutive_loads_required)

    async def _claim_baseline(
        self, card: AnomalyCard, watermark: DataWatermark
    ) -> tuple[int | None, str]:
        """This platform's own exposure for a lead at the load it was claimed.

        ``None`` with a stated basis when the drill cannot be re-derived —
        an undrillable card, or a contract that produces no money column. A
        lead with no measurable baseline is never auto-confirmed by
        measurement; the detector-cleared basis is the only one left to it,
        and the lead says so.
        """
        if not card.drillable:
            return None, (
                "this card cannot be investigated at this catalog and pack version, so this "
                "platform has no figure of its own to measure the fix against; confirmation "
                "can only come from the lead leaving the detection feed"
            )
        rederived = await self._components.rederive_impact(card.drill_spec, watermark)
        if rederived.cents is None:
            return None, (
                "this platform could not re-derive an exposure for the lead's drill at the "
                f"claim load ({rederived.unavailable_reason or 'no reason recorded'}), so "
                "confirmation can only come from the lead leaving the detection feed"
            )
        return rederived.cents, (
            f"this platform's own re-derivation of the lead's drill "
            f"({rederived.measure_id or 'metric'}) at the claim load {watermark.id}"
        )

    async def _load_order(self, tenant: str) -> dict[str, datetime]:
        """Every load this tenant could name, by the clock that orders them.

        The repository is the authority — it knows every completed load,
        including the ones this tenant has never had evaluated — and the
        stored censuses fill in any load the repository has since rotated
        out. Ids are NEVER compared as strings: ``wm_010`` sorts before
        ``wm_002`` and a lexicographic "after" would be a second, wrong
        answer to a question the load's own ``loaded_at`` already answers.

        Read only when a lead actually needs ordering, which on most loads
        is never: a tenant with nothing claimed pays nothing for this.
        """
        order: dict[str, datetime] = {}
        try:
            for load in await self._components.monitors_loads.list_for_tenant(tenant, limit=24):
                order[load.watermark_id] = _utc(load.watermark_loaded_at)
        except Exception:  # pragma: no cover - defensive
            logger.exception("monitors: could not read %s's evaluated loads for ordering", tenant)
        try:
            for wm in await self._components.repository.list_watermarks():
                order[wm.id] = _utc(wm.loaded_at)
        except Exception:  # pragma: no cover - defensive
            logger.exception("monitors: could not list watermarks for lead verification ordering")
        return order

    async def _verify_claimed_leads(
        self, tenant: str, watermark: DataWatermark, portfolio: PortfolioResponse
    ) -> list[dict[str, Any]]:
        """Confirm, refuse, or regress every claimed resolution at this load.

        Two governed bases, either of which counts as a load verifying the
        claim: the lead has left the detection feed, or this platform's own
        re-derivation of its drill has fallen by the governed fraction. Where
        neither can be evaluated the lead stays claimed with a stated
        reason — which is the honest outcome and the whole point of having a
        verification path rather than a checkbox.

        Both of the module docstring's first two rules live here. A load
        counts only if it is STRICTLY AFTER the claim — checked against the
        loads' own clocks, and banked pre-claim evidence is repaired away
        before the walk rather than left to rot — and a lead that this
        platform already confirmed is walked too, because a confirmed lead
        back in the detection feed is a regression and the only way to never
        notice one is to stop looking.
        """
        resolution = self.policy.resolution
        cards = {card.anomaly_id: card for card in portfolio.items}
        verifications: list[dict[str, Any]] = []
        walkable = [
            lead
            for lead in await self._components.monitors_leads.list_for_tenant(tenant)
            if lead.status in ("resolved_claimed", "resolved_confirmed", "regressed")
        ]
        if not walkable:
            return verifications
        order = await self._load_order(tenant)
        for stored in walkable:
            lead = _repaired_lead(stored, order, resolution.consecutive_loads_required)
            if lead is not stored:
                logger.warning(
                    "monitors: repaired lead %s for tenant %s — %s",
                    lead.anomaly_id,
                    tenant,
                    lead.verification_note,
                )
                await self._components.monitors_leads.put(lead)
            card = cards.get(lead.anomaly_id)
            if lead.status == "resolved_confirmed":
                if card is None:
                    continue  # confirmed, and still gone. Nothing new to say.
                confirmed_at = (
                    lead.confirming_watermarks[-1]
                    if lead.confirming_watermarks
                    else lead.claimed_at_watermark
                )
                if (
                    confirmed_at is not None
                    and _is_strictly_after(watermark.id, confirmed_at, order) is not True
                ):
                    # Walking a load that is not after the confirmation — a
                    # brief being re-read for an older load. The lead was in
                    # the feed THEN and was confirmed LATER; that is a
                    # history, not a regression, and rewriting the verdict
                    # from it would let an old page undo a current fact.
                    continue
                outcome = _regressed_on_reappearance(lead, card, watermark)
                await self._components.monitors_leads.put(outcome.lead)
                if outcome.entry is not None:
                    verifications.append(outcome.entry)
                continue
            if lead.claimed_at_watermark is None:  # pragma: no cover - defensive
                continue
            if _is_strictly_after(watermark.id, lead.claimed_at_watermark, order) is not True:
                # The claim load is not evidence for its own claim, and a
                # load that ran BEFORE it is not evidence either. An
                # unorderable pair (a rotated-out load) fails closed: this
                # platform does not count evidence it cannot place in time.
                continue
            if watermark.id in lead.confirming_watermarks:
                continue  # already counted
            outcome = await self._verify_one(lead, card, watermark, resolution)
            if outcome.changed:
                await self._components.monitors_leads.put(outcome.lead)
            if outcome.entry is not None:
                verifications.append(outcome.entry)
        return verifications

    async def _verify_one(
        self,
        lead: MonitorsLead,
        card: AnomalyCard | None,
        watermark: DataWatermark,
        resolution: ResolutionPolicy,
    ) -> _Verification:
        required = resolution.consecutive_loads_required
        confirming = (*lead.confirming_watermarks, watermark.id)
        # The loads that verified it, named. A single-load span is written as
        # one id rather than "wm_003-wm_003", which reads like a bug.
        span = (
            f"{confirming[0]}-{watermark.id}" if len(confirming) > 1 else watermark.id
        )

        if card is None:
            note = (
                f"{lead.anomaly_id} is no longer in the detection feed at {watermark.id}: the "
                "detector's own rule has stopped firing for this cell"
            )
            return self._advance(lead, confirming, required, note, span, watermark, in_feed=False)

        current: int | None = None
        if lead.baseline_cents is not None and card.drillable:
            rederived = await self._components.rederive_impact(card.drill_spec, watermark)
            current = rederived.cents
        if lead.baseline_cents is None or current is None:
            basis = lead.baseline_basis or "no baseline was captured"
            held = replace(
                lead,
                verification_note=(
                    f"still detected at {watermark.id}, and this platform has no comparable "
                    f"figure to measure the fix against ({basis}). The claim stands "
                    "unconfirmed rather than being confirmed on an assertion."
                ),
                updated_at=datetime.now(UTC),
            )
            return _Verification(held, None)

        baseline = lead.baseline_cents
        if baseline == 0:
            reduction = Decimal(1) if current == 0 else Decimal(0)
        else:
            reduction = Decimal(baseline - current) / Decimal(abs(baseline))
        if reduction >= resolution.measured_reduction_fraction:
            note = (
                f"{lead.anomaly_id} is back within tolerance at {watermark.id}: this "
                f"platform's re-derived exposure fell from {magnitude(baseline, 'money_cents')} "
                f"at the claim load to {magnitude(current, 'money_cents')} "
                f"({float(reduction):.0%} down, against a governed threshold of "
                f"{float(resolution.measured_reduction_fraction):.0%})"
            )
            return self._advance(lead, confirming, required, note, span, watermark, in_feed=True)
        if -reduction >= resolution.regression_increase_fraction:
            regressed = replace(
                lead,
                status="regressed",
                confirming_watermarks=(),
                updated_at=datetime.now(UTC),
                verification_note=(
                    f"Regressed: {lead.anomaly_id} moved the wrong way. This platform's "
                    f"re-derived exposure rose from {magnitude(baseline, 'money_cents')} at "
                    f"the claim load to {magnitude(current, 'money_cents')} at {watermark.id} "
                    f"({float(-reduction):.0%} up, against a governed regression threshold of "
                    f"{float(resolution.regression_increase_fraction):.0%}). The claimed fix "
                    "did not hold."
                ),
            )
            return _Verification(
                regressed,
                {
                    "anomaly_id": lead.anomaly_id,
                    "status": "regressed",
                    "title": card.title,
                    "impact_cents": current,
                    "note": regressed.verification_note,
                },
            )
        if lead.status == "regressed" and not lead.confirming_watermarks:
            # A regression already stated, still detected, still not
            # measurably better: nothing has happened since, so the sentence
            # that named the regression STANDS rather than being overwritten
            # by a weaker restatement every time the surface is re-read.
            return _Verification(lead, None, changed=False)
        held = replace(
            lead,
            confirming_watermarks=(),
            updated_at=datetime.now(UTC),
            verification_note=(
                f"still detected at {watermark.id}: this platform's re-derived exposure is "
                f"{magnitude(current, 'money_cents')} against {magnitude(baseline, 'money_cents')} "
                f"at the claim load ({float(reduction):.0%} down, short of the governed "
                f"{float(resolution.measured_reduction_fraction):.0%}). Not confirmed, and the "
                "streak restarts."
            ),
        )
        return _Verification(held, None)

    def _advance(
        self,
        lead: MonitorsLead,
        confirming: tuple[str, ...],
        required: int,
        note: str,
        span: str,
        watermark: DataWatermark,
        *,
        in_feed: bool,
    ) -> _Verification:
        """One verifying load recorded; confirmed only once the streak is long
        enough. One load is a coincidence — a card can drop out of a single
        snapshot because a window moved — and confirming on it would publish
        "confirmed" for a problem that returns tomorrow."""
        if len(confirming) < required:
            return _Verification(
                replace(
                    lead,
                    confirming_watermarks=confirming,
                    updated_at=datetime.now(UTC),
                    verification_note=(
                        f"{note}. That is {len(confirming)} of the {required} consecutive "
                        "loads the governed rule requires before this platform will call it "
                        "confirmed."
                    ),
                ),
                None,
            )
        if in_feed:
            # The streak is long enough and the detector is STILL FIRING for
            # this cell. A lead on the board is not a fixed lead, whatever
            # the money did — so the exposure that fell is reported as the
            # good news it is, under a claim that stays unconfirmed.
            return _Verification(
                replace(
                    lead,
                    confirming_watermarks=confirming,
                    updated_at=datetime.now(UTC),
                    verification_note=(
                        f"{note}. That is {len(confirming)} of the {required} consecutive loads "
                        f"the governed rule requires, but {lead.anomaly_id} is still in the "
                        f"detection feed at {watermark.id}, so this platform will not call it "
                        "confirmed: a lead the detector is still firing on is not a fixed one."
                    ),
                ),
                None,
            )
        loads = "load" if required == 1 else "consecutive loads"
        sentence = f"Confirmed: {note}, for {required} {loads}, {span}."
        return _Verification(
            replace(
                lead,
                status="resolved_confirmed",
                confirming_watermarks=confirming,
                updated_at=datetime.now(UTC),
                verification_note=sentence,
            ),
            {
                "anomaly_id": lead.anomaly_id,
                "status": "resolved_confirmed",
                "title": lead.anomaly_id,
                "note": sentence,
            },
        )


#: Said in the sentence a repaired lead carries, and looked for again on
#: the next load so the repair runs ONCE. Naming the discarded loads inside
#: the note would otherwise make the note look, to the very scan that wrote
#: it, like the pre-claim verdict it replaced — and the repair would rewrite
#: the same sentence at every load forever.
_PRE_CLAIM_DISCARDED = (
    "A load that ran before a fix was claimed is not evidence the fix worked, so this platform "
    "has discarded it."
)


def _is_strictly_after(
    candidate_id: str, claimed_at_watermark: str, order: Mapping[str, datetime]
) -> bool | None:
    """Did ``candidate_id`` land strictly after the claim load?

    ``None`` means unorderable — one of the two loads is not in ``order`` —
    and every caller treats that as "not evidence" rather than guessing.
    Ids are never compared: ``wm_010`` sorts before ``wm_002``.
    """
    candidate = order.get(candidate_id)
    claimed = order.get(claimed_at_watermark)
    if candidate is None or claimed is None:
        return None
    return candidate > claimed


def _pre_claim_loads_named(
    note: str, claimed_at_watermark: str, order: Mapping[str, datetime]
) -> tuple[str, ...]:
    """Loads a verification sentence names that ran before the claim.

    What a stale verdict looks like: one reached at a load BEFORE the claim
    it judges ("still detected at wm_002" on a lead claimed at wm_003). The
    strict-after rule stops new ones; this is how the ones already written
    are found.
    """
    if _PRE_CLAIM_DISCARDED in note:
        return ()
    return tuple(
        sorted(
            wid
            for wid in order
            if wid != claimed_at_watermark
            and wid in note
            and _is_strictly_after(wid, claimed_at_watermark, order) is False
        )
    )


def _repaired_lead(lead: MonitorsLead, order: Mapping[str, datetime], required: int) -> MonitorsLead:
    """The lead with every pre-claim 'confirmation' taken back off it.

    Returns the SAME OBJECT when there was nothing to repair, so the walk
    writes back only what it changed.

    The rule is one sentence — a load that ran before the fix was claimed is
    not evidence the fix worked — and it has to be applied to state already
    on disk, not only to the next load: wm_001 and wm_002 banked against a
    claim made at wm_003 otherwise publish "Fix confirmed in the data" on a
    lead sitting in wm_003's own detection feed. Only DEMONSTRABLY pre-claim
    loads are dropped; a load this platform cannot place in time is left
    alone rather than deleted on a guess.
    """
    claimed_at = lead.claimed_at_watermark
    if claimed_at is None or claimed_at not in order:
        return lead
    dropped = tuple(
        wid
        for wid in lead.confirming_watermarks
        if _is_strictly_after(wid, claimed_at, order) is False
    )
    stale = _pre_claim_loads_named(lead.verification_note, claimed_at, order)
    if not dropped and not stale:
        return lead
    kept = tuple(wid for wid in lead.confirming_watermarks if wid not in dropped)
    if dropped:
        discarded = (
            f"{_plural(len(dropped), 'load', 'loads')} banked against this claim ran BEFORE it. "
            f"{_PRE_CLAIM_DISCARDED}"
        )
    else:
        discarded = (
            "An earlier verdict on this lead was reached at a load that ran BEFORE the claim. "
            f"{_PRE_CLAIM_DISCARDED}"
        )
    if lead.status == "resolved_confirmed" and len(kept) >= required:
        note = (
            f"Confirmed: {lead.anomaly_id} verified on {_plural(len(kept), 'load', 'loads')} "
            f"after the claim at {claimed_at} ({', '.join(kept)}). {discarded}"
        )
        status = "resolved_confirmed"
    else:
        note = (
            f"Resolution claimed at {claimed_at}, and no load since has verified it. {discarded} "
            f"This platform re-runs the lead's own drill at every load after {claimed_at} and "
            f"confirms only once {required} consecutive loads verify it."
        )
        status = "resolved_claimed"
    return replace(
        lead,
        status=status,
        confirming_watermarks=kept,
        verification_note=note,
        updated_at=datetime.now(UTC),
    )


def _withdrawn_confirmation_sentence(lead: MonitorsLead, watermark_id: str) -> str:
    """Both facts, in one sentence, in the order a reader needs them."""
    confirmed_at = (
        lead.confirming_watermarks[-1]
        if lead.confirming_watermarks
        else (lead.claimed_at_watermark or "an earlier load")
    )
    on_loads = (
        f" on {_plural(len(lead.confirming_watermarks), 'load', 'loads')} "
        f"{', '.join(lead.confirming_watermarks)}"
        if lead.confirming_watermarks
        else ""
    )
    return (
        f"Regressed: {lead.anomaly_id} was confirmed fixed at {confirmed_at}{on_loads}; the "
        f"detector fired again at {watermark_id} — the confirmation is withdrawn, because a lead "
        "in this load's own detection feed is not a fixed lead."
    )


def _regressed_on_reappearance(
    lead: MonitorsLead, card: AnomalyCard, watermark: DataWatermark
) -> _Verification:
    """A confirmed lead back in the feed: regressed, and it is news.

    ``resolution_regressed`` is first in the governed priority order and is
    never capped, so this takes its slot in the brief rather than being
    narrated in an eyebrow above a green check.
    """
    sentence = _withdrawn_confirmation_sentence(lead, watermark.id)
    regressed = replace(
        lead,
        status="regressed",
        confirming_watermarks=(),
        updated_at=datetime.now(UTC),
        verification_note=sentence,
    )
    return _Verification(
        regressed,
        {
            "anomaly_id": lead.anomaly_id,
            "status": "regressed",
            "title": card.title,
            "impact_cents": card.impact_cents,
            "note": sentence,
        },
    )


def _merged_verifications(
    stored: MonitorsLoad | None,
    fresh: Sequence[Mapping[str, Any]],
    leads: Mapping[str, MonitorsLead],
) -> list[dict[str, Any]]:
    """This load's verdicts on claimed fixes, kept across a re-walk.

    A load is walked more than once — the brief route and the scheduled
    sweep both evaluate it, and a reader refreshing the page evaluates it
    again — and each walk only produces the verdicts it REACHED. Replacing
    the stored list would drop "fix confirmed" and "fix did not hold" out of
    the brief the second time anybody opened it, which is the one entry
    class the governed cap is forbidden to drop.

    A stored verdict survives only while the lead still holds it: a
    confirmation that has since been withdrawn (regressed, or repaired away
    as pre-claim) is not a record, it is a contradiction.
    """
    out: dict[str, dict[str, Any]] = {}
    prior = (stored.payload.get("verifications") or []) if stored is not None else []
    for entry in prior if isinstance(prior, Sequence) else []:
        if not isinstance(entry, Mapping):  # pragma: no cover - defensive
            continue
        anomaly_id = str(entry.get("anomaly_id", ""))
        lead = leads.get(anomaly_id)
        if lead is None or lead.status != str(entry.get("status", "")):
            continue
        out[anomaly_id] = dict(entry)
    for entry in fresh:
        out[str(entry.get("anomaly_id", ""))] = dict(entry)
    return list(out.values())


def _publishable_lead_status(
    lead: MonitorsLead, *, tenant: str, watermark_id: str
) -> tuple[str, str]:
    """The (status, sentence) a payload may carry for a lead IN THE FEED.

    The verification walk demotes a reappearing confirmation to
    ``regressed`` and is the authority on the stored state. This is the READ
    path, which can run at a load the walk has not reached — so it publishes
    the same verdict from the same facts rather than the stored word, and
    shouts, because a green "Fix confirmed in the data" over a lead on
    today's board is the one lie this surface cannot afford.

    Unconditional, deliberately. A payload is built FOR a load, and on any
    load whose feed holds this card, "confirmed fixed" is false as printed.
    (Re-reading an old brief for a lead confirmed at a later load therefore
    shows the demotion too — a page about the past saying "regressed" is a
    reading of history; saying "fixed" about a lead the reader can see on
    the board is a false claim about the present.)
    """
    if lead.status != "resolved_confirmed":
        return lead.status, lead.verification_note or lead.note
    logger.error(
        "monitors: lead %s for tenant %s is stored resolved_confirmed while in the detection feed "
        "at %s — published as regressed instead; the verification walk has not reached this load",
        lead.anomaly_id,
        tenant,
        watermark_id,
    )
    return "regressed", _withdrawn_confirmation_sentence(lead, watermark_id)


def _assert_no_confirmed_lead_in_feed(
    tenant: str, watermark_id: str, statuses: Mapping[str, str]
) -> None:
    """A card in this load's feed may not be published as confirmed-fixed.

    Checked at PAYLOAD BUILD, on every card, rather than in a test: a card
    rendered "NEW LEAD, ALREADY CONFIRMED FIXED" while open in the same
    load's feed is a contradiction no other stage is in a position to
    notice.
    """
    offenders = sorted(a for a, status in statuses.items() if status == "resolved_confirmed")
    if offenders:  # pragma: no cover - unreachable by construction
        raise ReviError(
            f"monitors would publish {', '.join(offenders)} as a confirmed fix for tenant "
            f"{tenant!r} while it is in the detection feed at {watermark_id}: a lead the "
            "detector is still firing on is not a fixed lead, and this payload is refused "
            "rather than rendered",
            details={"anomaly_ids": offenders, "watermark_id": watermark_id},
        )


def lead_payload(lead: MonitorsLead, confirmations_required: int) -> MonitorsLeadPayload:
    return MonitorsLeadPayload(
        anomaly_id=lead.anomaly_id,
        tenant=lead.tenant,
        status=lead.status,  # type: ignore[arg-type]
        note=lead.note,
        updated_at=lead.updated_at,
        claimed_at_watermark=lead.claimed_at_watermark,
        baseline_cents=lead.baseline_cents,
        baseline_basis=lead.baseline_basis,
        confirming_watermarks=list(lead.confirming_watermarks),
        confirmations_required=confirmations_required,
        verification_note=lead.verification_note,
        history=[dict(entry) for entry in lead.history],
    )
