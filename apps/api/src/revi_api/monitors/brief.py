"""The per-load brief: what changed, what was withheld, and the census that ties out."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from revi_api.auth import Principal
from revi_api.monitors_policy import (
    MaterialityVerdict,
    MonitorsPolicy,
    assess_movement,
    assess_new_lead,
    assess_self_resolved,
)
from revi_api.portfolio import PRIORITY_FORMULA_VERSION
from revi_api.warning_codes import structured_warnings
from revi_investigation.application.ports import (
    MonitorsLoad,
    MonitorsPin,
)
from revi_investigation.application.rendering import (
    magnitude,
)
from revi_investigation_contracts.api import (
    PortfolioResponse,
    TimeToImpactPayload,
)
from revi_investigation_contracts.monitors import (
    MonitorsBriefEntry,
    MonitorsBriefResponse,
    MonitorsDeltaPayload,
    MonitorsFatigueAdvisory,
    MonitorsImmaterialSummary,
    MonitorsProvenancePayload,
    MonitorsTilePayload,
)
from revi_kernel.errors import PolicyDeniedError
from revi_kernel.watermark import DataWatermark

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    pass

from revi_api.monitors.common import (
    MonitorsNotFoundError,
    _date_range_phrase,
    _decimal,
    _load_phrase,
    _monitors_warnings,
    _MonitorsBase,
    _plural,
    _utc,
)
from revi_api.monitors.leads import (
    _assert_no_confirmed_lead_in_feed,
    _merged_verifications,
    _publishable_lead_status,
)
from revi_api.monitors.spec import SAME_WINDOW_NOTE
from revi_api.monitors.tiles import (
    _adds_something,
    _delta_payload,
    _headline_of,
    _not_comparable_reason,
)


@dataclass(slots=True)
class _MonitorCensus:
    """Where every active monitor landed in one brief.

    Mutable and passed around by one method on purpose: the four buckets
    have to sum to :attr:`evaluated` and keeping them in one object is what
    makes that checkable in one place rather than in four counters that
    drift apart.
    """

    entries: list[MonitorsBriefEntry]
    evaluated: int = 0
    immaterial: int = 0
    not_yet_comparable: int = 0
    unavailable: int = 0
    below_gate: int = 0
    #: Monitors counted in :attr:`unavailable` that HAD a reading at the load
    #: this brief diffs from — one phrase each. A monitor that has never been
    #: measurable is a setup problem and belongs in a count; one that was
    #: measured last night and is not now is a change in the data, which is
    #: the only thing a brief exists to report. Kept apart so the second is
    #: never delivered as the first.
    lost: list[str] = field(default_factory=list)


class _BriefComposition(_MonitorsBase):
    """Composing one load's brief, and counting where every monitor landed."""

    # ------------------------------------------------------------- the brief

    async def brief(
        self, principal: Principal, *, since: str | None = None
    ) -> MonitorsBriefResponse:
        """What changed at this load: gated, capped, counted and provenanced."""
        return await self.brief_at(
            principal,
            await self._components.open_session.newest_watermark(),
            since=since,
        )

    async def brief_at(
        self,
        principal: Principal,
        watermark: DataWatermark,
        *,
        since: str | None = None,
    ) -> MonitorsBriefResponse:
        """The brief FOR a named load. See :meth:`monitors_at` for why this
        seam exists: the simulated-load suite drives every watermark
        transition through the same code the newest-load route runs."""
        tenant = principal.tenant
        load = await self.evaluate_load(tenant, watermark)
        prior = await self._named_prior_load(tenant, watermark, since)

        pins = {
            pin.id: pin
            for pin in await self._components.monitors_pins.list_for_tenant(
                tenant, include_archived=True
            )
        }
        current_leads = _leads_of(load)
        prior_leads = _leads_of(prior) if prior is not None else {}

        entries: list[MonitorsBriefEntry] = []
        new_lead_skipped = 0
        self_resolved_skipped = 0
        if prior is not None:
            new_entries, new_lead_skipped = self._new_lead_entries(
                load, prior_leads, current_leads
            )
            resolved_entries, self_resolved_skipped = self._self_resolved_entries(
                load,
                prior_leads,
                current_leads,
                frozenset(
                    lead.anomaly_id
                    for lead in (await self._components.monitors_leads.list_for_tenant(tenant))
                    if lead.status in ("resolved_claimed", "resolved_confirmed", "regressed")
                ),
            )
            entries.extend(new_entries)
            entries.extend(resolved_entries)
        # ONE reference frame for the whole brief. If the lead census diffed
        # against the load the caller named while monitor movements diffed
        # against last night's, `since=wm_001` would produce a headline about
        # wm_001..wm_003 containing an entry measured wm_002..wm_003.
        census = await self._movement_entries(tenant, watermark, pins, prior, named=since is not None)
        entries.extend(census.entries)
        entries.extend(self._verification_entries(load, watermark))

        total = len(entries)
        published, dropped_by_kind = _cap(entries, self.policy)
        immaterial = MonitorsImmaterialSummary(
            pin_movements=census.immaterial,
            new_leads=new_lead_skipped,
            self_resolved=self_resolved_skipped,
            entries_withheld_by_cap=total - len(published),
            not_yet_comparable=census.not_yet_comparable,
            unavailable=census.unavailable,
            entries_withheld_by_kind=dropped_by_kind,
        )
        immaterial = immaterial.model_copy(
            update={"note": _immaterial_note(immaterial, census.lost)}
        )
        status = (
            "first_load"
            if prior is None
            else ("material_changes" if published else "nothing_material")
        )
        fatigue = await self._fatigue(tenant, watermark, census.below_gate)
        warnings = _monitors_warnings(self.policy)
        prior_data_date = _data_date_of(prior)
        return MonitorsBriefResponse(
            tenant=tenant,
            status=status,  # type: ignore[arg-type]
            watermark_id=watermark.id,
            newest_data_date=watermark.newest_data_date,
            prior_watermark_id=prior.watermark_id if prior is not None else None,
            prior_newest_data_date=prior_data_date,
            headline=_headline_sentence(
                status=status,
                newest_data_date=watermark.newest_data_date,
                prior_newest_data_date=prior_data_date,
                has_prior=prior is not None,
                entries=published,
                pins_evaluated=census.evaluated,
                leads=len(current_leads),
            ),
            entries=published,
            entries_total=total,
            immaterial=immaterial,
            fatigue=fatigue,
            materiality=self.policy.payload(),
            pins_evaluated=census.evaluated,
            leads_verified=int(load.payload.get("leads_verified", 0) or 0),
            generated_at=datetime.now(UTC),
            warnings=warnings,
            warnings_v2=structured_warnings(warnings),
        )

    async def _named_prior_load(
        self, tenant: str, watermark: DataWatermark, since: str | None
    ) -> MonitorsLoad | None:
        """The load this brief diffs against.

        ``since`` names it explicitly (the client knows which brief the
        analyst last read); absent, it is the newest evaluated load before
        this one. A ``since`` naming a load that was never evaluated is a
        404 rather than a silent fall-back — a brief that quietly diffed
        against a different load than the one it was asked for would
        misreport every entry on it.
        """
        if since is None:
            return await self._prior_load(tenant, watermark)
        if since == watermark.id:
            raise PolicyDeniedError(
                "this brief is for the same load you asked to compare it against, so there is "
                "nothing to compare. Leave the comparison load out and Revi uses the previous "
                "evaluated load.",
                details={"since": since, "watermark_id": watermark.id},
            )
        stored = await self._components.monitors_loads.get(tenant, since)
        if stored is None:
            raise MonitorsNotFoundError(
                "the load you asked to compare against has no recorded Monitors evaluation, so "
                "this brief has nothing to compare with. A brief can only be taken since a load "
                "Revi has already walked your Monitors on.",
                details={"since": since, "tenant": tenant},
            )
        return stored

    def _new_lead_entries(
        self,
        load: MonitorsLoad,
        prior_leads: Mapping[str, Mapping[str, Any]],
        current_leads: Mapping[str, Mapping[str, Any]],
    ) -> tuple[list[MonitorsBriefEntry], int]:
        out: list[MonitorsBriefEntry] = []
        skipped = 0
        for anomaly_id, row in current_leads.items():
            if anomaly_id in prior_leads:
                continue
            verdict = assess_new_lead(
                impact_cents=int(row.get("ranked_impact_cents", 0) or 0),
                lane=str(row.get("lane", "value")),
                policy=self.policy.materiality,
            )
            if not verdict.material:
                skipped += 1
                continue
            out.append(
                MonitorsBriefEntry(
                    kind="new_lead",
                    title=str(row.get("title", anomaly_id)),
                    # The money is said ONCE. Repeated in this sentence and
                    # again on the meta row above it, it is rounded
                    # differently each time and reads as three figures.
                    statement=(
                        f"New at this load: {anomaly_id} — {row.get('title', '')}, "
                        f"{magnitude(int(row.get('ranked_impact_cents', 0) or 0), 'money_cents')}"
                        f" on the {row.get('ranked_on', 'detector')}'s figure. "
                        f"{_sentence(verdict.note)}"
                    ),
                    anomaly_id=anomaly_id,
                    category=row.get("category"),
                    lane=row.get("lane"),
                    impact_cents=int(row.get("ranked_impact_cents", 0) or 0),
                    time_to_impact=_time_to_impact_payload(row),
                    lead_status=str(row.get("lead_status", "open")),
                    provenance=MonitorsProvenancePayload(
                        source="detection_feed",
                        watermark_id=load.watermark_id,
                        evaluated_at=load.evaluated_at,
                        formula_version=PRIORITY_FORMULA_VERSION,
                        method="present in the detection feed at this load and absent at the "
                        "prior one",
                    ),
                )
            )
        return out, skipped

    def _self_resolved_entries(
        self,
        load: MonitorsLoad,
        prior_leads: Mapping[str, Mapping[str, Any]],
        current_leads: Mapping[str, Mapping[str, Any]],
        claimed: frozenset[str],
    ) -> tuple[list[MonitorsBriefEntry], int]:
        """Leads that left the feed with nobody claiming them.

        ``claimed`` comes from the LIFECYCLE STORE, not from the prior
        load's census: the census records what the feed said at that load,
        and a claim made after it was written would be invisible to it. The
        store is the authority on whether a human said they fixed something,
        and reading the snapshot instead published one lead twice — once as
        a confirmation and once as having fixed itself.
        """
        out: list[MonitorsBriefEntry] = []
        skipped = 0
        for anomaly_id, row in prior_leads.items():
            if anomaly_id in current_leads or anomaly_id in claimed:
                # A lead somebody CLAIMED and that then left the feed is a
                # confirmation, not a self-resolution: reporting both would
                # tell one fact twice, and the second telling would credit
                # nobody for work somebody did.
                continue
            impact = int(row.get("ranked_impact_cents", 0) or 0)
            verdict = assess_self_resolved(
                impact_cents=impact, policy=self.policy.materiality
            )
            if not verdict.material:
                skipped += 1
                continue
            out.append(
                MonitorsBriefEntry(
                    kind="self_resolved",
                    title=str(row.get("title", anomaly_id)),
                    statement=(
                        f"Gone without being worked: {anomaly_id} — {row.get('title', '')} "
                        f"({magnitude(impact, 'money_cents')}) was in the detection feed at "
                        f"the prior load and is not in this one. Nobody claimed it, so this "
                        "is the detector's rule no longer firing rather than a fix this "
                        "platform verified."
                    ),
                    anomaly_id=anomaly_id,
                    category=row.get("category"),
                    lane=row.get("lane"),
                    impact_cents=impact,
                    lead_status=str(row.get("lead_status", "open")),
                    provenance=MonitorsProvenancePayload(
                        source="detection_feed",
                        watermark_id=load.watermark_id,
                        prior_watermark_id=str(row.get("watermark_id") or "") or None,
                        evaluated_at=load.evaluated_at,
                        formula_version=PRIORITY_FORMULA_VERSION,
                        method="absent from the detection feed at this load and present at "
                        "the prior one, with no resolution claimed",
                    ),
                )
            )
        return out, skipped

    async def _movement_entries(
        self,
        tenant: str,
        watermark: DataWatermark,
        pins: Mapping[str, MonitorsPin],
        prior_load: MonitorsLoad | None,
        *,
        named: bool = True,
    ) -> _MonitorCensus:
        """Every active monitor, diffed against the load this brief is FOR.

        The census closes: every monitor lands in exactly one of briefed,
        immaterial, not-yet-comparable or unavailable, so
        ``pins_evaluated == briefed + immaterial + not_yet_comparable +
        unavailable`` is an identity rather than a hope. Anything that falls
        through is a monitor neither briefed nor counted as held back, on a
        surface whose stated discipline is "withheld visibly, never
        silently".

        ``named`` says whether the caller asked for a specific ``since``.
        When they did, the reference frame is theirs and a monitor with no
        evaluation at that load has nothing to say. When they did not, this
        brief and the tile grid are two renderings of one default view, and
        they must count each pin once and identically — see
        :meth:`_delta_against`.
        """
        out: list[MonitorsBriefEntry] = []
        census = _MonitorCensus(entries=out)
        prior_watermark = prior_load.watermark_id if prior_load is not None else None
        prior_date = _data_date_of(prior_load)
        for pin in pins.values():
            if pin.archived_at is not None:
                continue
            census.evaluated += 1
            stored = await self._components.monitors_results.get(pin.id, watermark.id)
            if stored is None:
                census.unavailable += 1
                continue
            tile = MonitorsTilePayload.model_validate(stored.payload)
            if tile.status != "ok" or tile.value is None:
                census.unavailable += 1
                lost = await self._lost_reading(pin, prior_watermark, prior_date)
                if lost is not None:
                    census.lost.append(lost)
                continue
            delta = await self._delta_against(pin, tile, prior_watermark, named=named)
            if delta is None:
                census.not_yet_comparable += 1
                continue
            if delta.below_governed_gate:
                census.below_gate += 1
            # A rank flip is not a movement and never carries a delta: it is
            # the fact that the worst cell is now a different cell, which is
            # the headline the fabricated movement was standing in for.
            if not delta.comparable and delta.prior_subject_label and delta.subject_label:
                out.append(
                    self._rank_flip_entry(pin, tile, delta, prior_date)
                )
                continue
            if not delta.comparable:
                census.not_yet_comparable += 1
                continue
            if not delta.material:
                census.immaterial += 1
                continue
            baseline = tile.baseline_delta if _adds_something(tile) else None
            out.append(
                MonitorsBriefEntry(
                    kind="pin_movement",
                    title=pin.label,
                    statement=_movement_sentence(pin, tile, delta, baseline, prior_date),
                    pin_id=pin.id,
                    investigation_id=tile.investigation_id,
                    delta=delta,
                    baseline_delta=baseline,
                    integrity=tile.integrity,
                    provenance=MonitorsProvenancePayload(
                        source="pinned_spec",
                        watermark_id=tile.watermark_id,
                        prior_watermark_id=delta.prior_watermark_id or None,
                        evaluated_at=tile.evaluated_at,
                        method="Revi re-measured what this monitor measures at this load, by "
                        "the same steps it uses every load and with nothing re-interpreted. "
                        "That reading is compared against this monitor's reading at the load "
                        "this brief is taken since.",
                    ),
                )
            )
        return census

    async def _lost_reading(
        self,
        pin: MonitorsPin,
        prior_watermark_id: str | None,
        prior_date: date | None,
    ) -> str | None:
        """This monitor published a number last load and publishes none now.

        The census counts it as unavailable either way — the identity has to
        close — but the two cases are not one fact. "This monitor has never
        been measurable" is a setup problem the analyst can look at whenever
        they like; "the payer this monitor is about published 22.9% at the
        last load and is not in this one" is the load's news, and delivering
        it inside a sentence that opens "held back and counted rather than
        hidden" would file a disappearance under housekeeping.

        Returns the phrase, or ``None`` when there was no earlier reading to
        lose.
        """
        if prior_watermark_id is None:
            return None
        stored = await self._components.monitors_results.get(pin.id, prior_watermark_id)
        if stored is None:
            return None
        prior_tile = MonitorsTilePayload.model_validate(stored.payload)
        if prior_tile.status != "ok" or prior_tile.value is None:
            return None
        subject = prior_tile.headline_subject_label
        about = f" ({subject})" if subject and subject not in pin.label else ""
        measured = prior_tile.value_text or "a value"
        return (
            f"{pin.label!r}{about} measured {measured} at {_load_phrase(prior_date)} and "
            "returns nothing at this one"
        )

    async def _delta_against(
        self,
        pin: MonitorsPin,
        tile: MonitorsTilePayload,
        prior_watermark_id: str | None,
        *,
        named: bool = True,
    ) -> MonitorsDeltaPayload | None:
        """This tile's movement since the NAMED load, not since last night.

        ``None`` when there is nothing to compare against at that load — a
        first reading, or a monitor created after it. The tile's own stored
        delta is reused when it already measures the right pair, so the
        common case (``since`` absent) costs no extra read.

        When no load was named (``named=False``) the tile's stored delta is
        also the FALL-BACK, not just the fast path. The default brief and the
        tile grid are one view rendered twice; a monitor with a gap in its
        history — no evaluation at last night's load, one at the load before
        — otherwise reads "down 3.0 points" on the tile and "nothing to
        compare against yet" in the brief, on one screen. The tile is the
        stored evaluation, so the tile wins, and the payload carries the
        ``prior_watermark_id`` it was actually measured against.
        """
        if prior_watermark_id is None:
            return None
        if tile.delta is not None and tile.delta.prior_watermark_id == prior_watermark_id:
            return tile.delta if tile.delta.prior_value is not None else None
        stored = await self._components.monitors_results.get(pin.id, prior_watermark_id)
        if stored is None:
            if named or tile.delta is None or tile.delta.prior_value is None:
                return None
            return tile.delta
        prior_tile = MonitorsTilePayload.model_validate(stored.payload)
        if prior_tile.status != "ok" or prior_tile.value is None:
            return None
        reason = _not_comparable_reason(pin, prior_tile, _headline_of(tile))
        prior_value = _decimal(prior_tile.value)
        current = _decimal(tile.value)
        verdict = (
            assess_movement(
                unit=tile.unit,
                prior=prior_value,
                current=current,
                policy=self.policy.materiality,
                monitor=pin.monitor,
            )
            if reason is None
            else MaterialityVerdict(False, "not_comparable", reason)
        )
        return _delta_payload(
            prior_watermark_id=prior_tile.watermark_id,
            prior_value=prior_value,
            current=current,
            unit=tile.unit,
            verdict=verdict,
            comparable=reason is None,
            not_comparable_reason=reason,
            reference="prior_load",
            same_window=(
                prior_tile.window_start is not None
                and (prior_tile.window_start, prior_tile.window_end)
                == (tile.window_start, tile.window_end)
            ),
            subject_label=tile.headline_subject_label,
            prior_subject_label=prior_tile.headline_subject_label,
        )

    def _rank_flip_entry(
        self,
        pin: MonitorsPin,
        tile: MonitorsTilePayload,
        delta: MonitorsDeltaPayload,
        prior_date: date | None,
    ) -> MonitorsBriefEntry:
        since = f"Since {_load_phrase(prior_date)}: " if prior_date is not None else ""
        return MonitorsBriefEntry(
            kind="rank_flip",
            title=pin.label,
            statement=(
                f"{since}{delta.subject_label} overtook {delta.prior_subject_label} at the top "
                f"of {pin.label}, now at {tile.value_text}. This is a change of subject and "
                "not a movement, so no change is reported between them — they are two "
                "different cells."
            ),
            pin_id=pin.id,
            investigation_id=tile.investigation_id,
            integrity=tile.integrity,
            provenance=MonitorsProvenancePayload(
                source="pinned_spec",
                watermark_id=tile.watermark_id,
                prior_watermark_id=delta.prior_watermark_id or None,
                evaluated_at=tile.evaluated_at,
                method="Revi re-measured what this monitor measures at both loads. The cell it "
                "ranks first is not the cell it ranked first before.",
            ),
        )

    def _verification_entries(
        self, load: MonitorsLoad, watermark: DataWatermark
    ) -> list[MonitorsBriefEntry]:
        out: list[MonitorsBriefEntry] = []
        for row in load.payload.get("verifications", []) or []:
            status = str(row.get("status", ""))
            if status not in ("resolved_confirmed", "regressed"):
                continue
            out.append(
                MonitorsBriefEntry(
                    kind=(
                        "resolution_confirmed"
                        if status == "resolved_confirmed"
                        else "resolution_regressed"
                    ),
                    title=str(row.get("title", row.get("anomaly_id", ""))),
                    statement=str(row.get("note", "")),
                    anomaly_id=str(row.get("anomaly_id", "")),
                    impact_cents=row.get("impact_cents"),
                    lead_status=status,
                    provenance=MonitorsProvenancePayload(
                        source="pinned_spec",
                        watermark_id=watermark.id,
                        evaluated_at=load.evaluated_at,
                        method="Revi re-derived the lead's own drill at every load since the "
                        "fix was claimed. Each reading is measured against the exposure "
                        "recorded at the claim load.",
                    ),
                )
            )
        return out

    async def _fatigue(
        self, tenant: str, watermark: DataWatermark, below_gate: int
    ) -> MonitorsFatigueAdvisory:
        """The brief noticing that somebody's own thresholds are too loose.

        Counted across loads from the stored census, so the advisory fires
        on a PATTERN rather than on one noisy morning — and never more than
        once per load, because an advisory that nagged would be the fatigue
        it is warning about.
        """
        policy = self.policy.materiality.fatigue
        if not policy.enabled:
            return MonitorsFatigueAdvisory()
        streak = 1 if below_gate else 0
        if streak:
            for load in await self._components.monitors_loads.list_for_tenant(tenant, limit=12):
                if _utc(load.watermark_loaded_at) >= _utc(watermark.loaded_at):
                    continue
                if int(load.payload.get("monitors_below_governed_gate", 0) or 0) > 0:
                    streak += 1
                else:
                    break
        active = streak >= policy.consecutive_loads
        return MonitorsFatigueAdvisory(
            active=active,
            monitors_below_governed_gate=below_gate,
            consecutive_loads=streak,
            loads_required=policy.consecutive_loads,
            message=(
                policy.message.format(count=below_gate, ordinal=_ordinal(streak))
                if active
                else ""
            ),
        )

    # ------------------------------------------------------------- the census

    async def _census(
        self,
        tenant: str,
        watermark: DataWatermark,
        portfolio: PortfolioResponse,
        pins: Sequence[MonitorsPin],
        verifications: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """What the feed said, and what the monitors did, at this load.

        Stored so the NEXT load can diff against it. Everything a brief
        needs about a prior load is here, because re-reading a warehouse
        snapshot to answer a question already answered is how a proactive
        surface becomes expensive enough to switch off.
        """
        leads = await self.lead_states(tenant)
        below_gate = 0
        for pin in pins:
            stored = await self._components.monitors_results.get(pin.id, watermark.id)
            if stored is None:
                continue
            delta = (stored.payload.get("delta") or {}) if isinstance(stored.payload, dict) else {}
            if delta.get("below_governed_gate"):
                below_gate += 1
        # Every card here is IN this load's feed by construction, so the
        # brief that diffs against this census can never call one of them a
        # confirmed fix.
        statuses = {
            card.anomaly_id: (
                _publishable_lead_status(
                    leads[card.anomaly_id], tenant=tenant, watermark_id=watermark.id
                )[0]
                if card.anomaly_id in leads
                else "open"
            )
            for card in portfolio.items
        }
        _assert_no_confirmed_lead_in_feed(tenant, watermark.id, statuses)
        merged = _merged_verifications(
            await self._components.monitors_loads.get(tenant, watermark.id), verifications, leads
        )
        return {
            "leads": {
                card.anomaly_id: {
                    "title": card.title,
                    "category": card.category,
                    "lane": card.lane,
                    "impact_cents": card.impact_cents,
                    "ranked_impact_cents": card.ranked_impact_cents,
                    "ranked_on": card.ranked_on,
                    "lead_status": statuses[card.anomaly_id],
                    "watermark_id": watermark.id,
                    "time_to_impact": (
                        card.time_to_impact.model_dump(mode="json")
                        if card.time_to_impact is not None
                        else None
                    ),
                }
                for card in portfolio.items
            },
            "verifications": merged,
            "leads_verified": len(merged),
            "monitors_below_governed_gate": below_gate,
            "pins_evaluated": len(pins),
            # So the NEXT brief can name this load the way a reader does —
            # "since the Aug 1 load" — instead of by the warehouse handle.
            # Stored rather than re-resolved: the watermark this census was
            # written at is the only authority on it, and looking it up
            # later would be a second answer to a question already answered.
            "newest_data_date": watermark.newest_data_date.isoformat()
            if watermark.newest_data_date is not None
            else None,
        }


def _looser_than_recommended(delta: MonitorsDeltaPayload) -> str:
    """The clause for a monitor briefing inside normal variation.

    States the recommended level as a NUMBER wherever the delta carries
    one. "Looser than Revi's recommended level" tells somebody their
    threshold is wrong without telling them what right would be, which is
    the shape ``docs/client-language.md`` §2.1 exists to stop; the bare
    form is kept only for a deployment that recommends nothing here.
    """
    if delta.recommended_threshold_text:
        return (
            f" — which is looser than the {delta.recommended_threshold_text} Revi "
            "recommends for this measure, so this movement is inside what Revi would "
            "call normal variation."
        )
    return (
        " — which is looser than Revi's recommended level for this measure, so this "
        "movement is inside what Revi would call normal variation."
    )


def _movement_sentence(
    pin: MonitorsPin,
    tile: MonitorsTilePayload,
    delta: MonitorsDeltaPayload,
    baseline: MonitorsDeltaPayload | None,
    prior_date: date | None = None,
) -> str:
    """One monitor's movement, in the words a reader uses.

    Nothing a warehouse calls a thing appears in this sentence: loads are
    named by their data date, the surface's own noun is "monitor" and never
    "tile", and the SUBJECT is named — no movement is published without
    saying what moved.
    """
    subject = (
        f" ({delta.subject_label})"
        if delta.subject_label and delta.subject_label not in pin.label
        else ""
    )
    since = (
        f" since {_load_phrase(prior_date)}" if prior_date is not None else ""
    )
    parts = [
        f"{pin.label}{subject}: {delta.value_text}, {_change_clause(delta)}{since}."
    ]
    if delta.threshold_source == "monitor":
        parts.append(
            "Briefed on this monitor's own threshold"
            + (
                _looser_than_recommended(delta)
                if delta.below_governed_gate
                else f": {_sentence(delta.materiality_note)}"
            )
        )
    else:
        parts.append(_sentence(delta.materiality_note))
    if baseline is not None:
        parts.append(
            f"Since you started monitoring it, it is {_change_clause(baseline)}."
            + (
                ""
                if baseline.same_window
                else " Against where you started, those two readings cover different date "
                "ranges, so part of that is the window moving rather than the measure."
            )
        )
    if delta.same_window and tile.window_start is not None and tile.window_end is not None:
        # Said from the DATES the two loads measured, not from the pin's
        # declared window mode: a relative window usually moves and
        # sometimes does not, and only the resolved dates know which.
        parts.append(
            SAME_WINDOW_NOTE.format(
                dates=_date_range_phrase(tile.window_start, tile.window_end)
            )
        )
    if tile.integrity.is_bound:
        parts.append(
            "The value is an upper bound: a suppressed numerator was replaced by the largest "
            "value it could have held."
        )
    if tile.integrity.provisional:
        parts.append("The value is provisional — the window is still adjudicating.")
    return " ".join(parts)


def _change_clause(delta: MonitorsDeltaPayload) -> str:
    """The change between two readings, in words rather than in tokens.

    ``direction`` is a WIRE ENUM clients branch on, and three of its four
    values do not survive being dropped into a sentence: "flat 0.0 points
    from 22.9%" is machine grammar, and "unknown from 22.9%" names a
    direction that does not exist. Said here so the payload keeps its
    branch handle and the reader gets a sentence.
    """
    if delta.direction in ("up", "down"):
        return f"{delta.direction} {delta.delta_text} from {delta.prior_value_text}"
    if delta.direction == "flat":
        return f"unchanged from {delta.prior_value_text}"
    # No direction was established, so none is claimed: both readings are
    # published and the change between them is not.
    return f"measured against {delta.prior_value_text}"


def _cap(
    entries: list[MonitorsBriefEntry], policy: MonitorsPolicy
) -> tuple[list[MonitorsBriefEntry], dict[str, int]]:
    """Cap the brief: per kind first, then overall — worst-to-lose LAST.

    Per kind first so one noisy category cannot fill the brief and push
    every other kind of change off the end of it — which is precisely how a
    daily surface trains somebody to stop reading it.

    Then by governed PRIORITY rather than by insertion order: insertion
    order puts ``resolution_regressed`` and ``resolution_confirmed`` last,
    so the platform's verdicts on the team's own work are the first thing
    the cap deletes, silently, on any tenant with a normal card count.
    Within a kind, entries sort by consequence, so the cap takes the
    smallest of the least important kind.

    Returns the published entries and WHAT WAS DROPPED, by kind: "12 further
    entries" does not tell a reader whether a confirmed fix was among them.
    """
    materiality = policy.materiality
    per_kind = materiality.max_entries_per_kind
    ordered = sorted(
        entries,
        key=lambda e: (materiality.rank_of(e.kind), -_consequence(e)),
    )
    dropped: dict[str, int] = {}
    seen: dict[str, int] = {}
    kept: list[MonitorsBriefEntry] = []
    for entry in ordered:
        count = seen.get(entry.kind, 0)
        if per_kind and count >= per_kind:
            dropped[entry.kind] = dropped.get(entry.kind, 0) + 1
            continue
        seen[entry.kind] = count + 1
        kept.append(entry)
    if not materiality.max_entries or len(kept) <= materiality.max_entries:
        return kept, dropped
    # The overall cap, with the exempt kinds taken out of its reach first.
    exempt_count = sum(1 for e in kept if e.kind in materiality.never_capped)
    room = max(materiality.max_entries - exempt_count, 0)
    published: list[MonitorsBriefEntry] = []
    for entry in kept:
        if entry.kind in materiality.never_capped:
            published.append(entry)
        elif room:
            published.append(entry)
            room -= 1
        else:
            dropped[entry.kind] = dropped.get(entry.kind, 0) + 1
    return published, dropped


def _consequence(entry: MonitorsBriefEntry) -> float:
    """How much this entry costs to lose, within its kind.

    Money for a lead; how far past its own gate a monitor moved, as a
    multiple, for a movement — so a monitor that tripled its threshold
    outranks one that grazed it, whatever their units.
    """
    if entry.impact_cents is not None:
        return float(abs(entry.impact_cents))
    delta = entry.delta
    if delta is not None and delta.delta is not None:
        if delta.prior_value:
            return abs(delta.delta / delta.prior_value)
        return abs(delta.delta)
    return 0.0


#: The nouns this surface uses for what a load can change, singular and
#: plural. One vocabulary, shared by the headline, the held-back line and
#: the cap's own report: without it the headline prints raw enum ids ("2 new
#: lead, 1 pin movement") directly above rows the UI labels "A MONITOR
#: MOVED", and "pin" is a word this product's own naming rule bans.
_KIND_NOUNS: dict[str, tuple[str, str]] = {
    "new_lead": ("new lead", "new leads"),
    "pin_movement": ("monitor moved", "monitors moved"),
    "self_resolved": ("resolved on its own", "resolved on their own"),
    "resolution_confirmed": ("fix confirmed", "fixes confirmed"),
    "resolution_regressed": ("fix did not hold", "fixes did not hold"),
    "rank_flip": ("new leader", "new leaders"),
}

#: What a kind this vocabulary has not learned yet is called. A missing
#: entry used to print the raw enum id ("2 pin_movement"), which is the
#: exact defect :data:`_KIND_NOUNS` exists to prevent — so the fall-back is
#: a word, and a vaguer count beats a token.
_KIND_NOUN_FALLBACK = ("change", "changes")


def _sentence(text: str) -> str:
    """One clause, capitalised and stopped. Brief prose is assembled from
    fragments the gate wrote for a different context, and joining them raw
    produced lowercase sentence starts mid-paragraph."""
    stripped = text.strip().rstrip(".")
    if not stripped:
        return ""
    return f"{stripped[:1].upper()}{stripped[1:]}."


def _data_date_of(load: MonitorsLoad | None) -> date | None:
    """A stored load's newest data date, when the census recorded one.

    Recorded from this change on; ``None`` for loads written before it, in
    which case the prose says "the previous load" rather than inventing a
    date or falling back to a warehouse id.
    """
    if load is None:
        return None
    raw = load.payload.get("newest_data_date")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:  # pragma: no cover - defensive
            return None
    return raw if isinstance(raw, date) else None


def _immaterial_note(
    immaterial: MonitorsImmaterialSummary, lost: Sequence[str] = ()
) -> str:
    """What the gate held back, in one sentence.

    Counted rather than hidden: suppressing a movement silently and
    suppressing it visibly are different products, and the first is a
    filter the analyst cannot audit.

    ``lost`` is the exception that goes FIRST and is not called held back: a
    monitor that had a reading at the previous load and has none at this one
    lost it because the data changed, and that is the brief's own news. It
    is still counted in ``unavailable`` so the census closes; it is just not
    delivered as housekeeping.
    """
    bits: list[str] = []
    if immaterial.pin_movements:
        bits.append(
            _plural(
                immaterial.pin_movements,
                "monitor moved by less than the level that would brief it",
                "monitors moved by less than the level that would brief them",
            )
        )
    if immaterial.new_leads:
        bits.append(
            f"{_plural(immaterial.new_leads, 'new lead was', 'new leads were')} too small "
            "to brief"
        )
    if immaterial.self_resolved:
        bits.append(
            f"{_plural(immaterial.self_resolved, 'lead', 'leads')} left the detection feed "
            "too small to brief"
        )
    if immaterial.not_yet_comparable:
        bits.append(
            f"{_plural(immaterial.not_yet_comparable, 'monitor has', 'monitors have')} nothing to "
            "compare against yet"
        )
    if immaterial.unavailable:
        bits.append(
            f"{_plural(immaterial.unavailable, 'monitor', 'monitors')} could not be measured at "
            "this load"
        )
    if immaterial.entries_withheld_by_cap:
        dropped = immaterial.entries_withheld_by_kind
        detail = (
            " ("
            + ", ".join(
                _plural(count, *_KIND_NOUNS.get(kind, _KIND_NOUN_FALLBACK))
                for kind, count in sorted(dropped.items())
            )
            + ")"
            if dropped
            else ""
        )
        bits.append(
            f"{_plural(immaterial.entries_withheld_by_cap, 'further entry', 'further entries')}"
            f"{detail} were held back by the brief's own cap"
        )
    held = (
        "Nothing was held back."
        if not bits
        else "Held back and counted rather than hidden: " + "; ".join(bits) + "."
    )
    if not lost:
        return held
    opening = (
        f"{_plural(len(lost), 'monitor', 'monitors')} stopped being measurable at this load: "
        + "; ".join(lost)
        + ". Each one says why."
    )
    return f"{opening} {held}"


def _headline_sentence(
    *,
    status: str,
    newest_data_date: date | None,
    prior_newest_data_date: date | None,
    has_prior: bool,
    entries: Sequence[MonitorsBriefEntry],
    pins_evaluated: int,
    leads: int,
) -> str:
    """The first sentence on the surface, in human words.

    No raw enum ids, no unpluralised counts, no warehouse handles: "4
    thing(s) changed between wm_002 and wm_003: 2 new lead, 1 pin movement"
    is not wrong, and it is also the last line anybody reads.

    The held-back clause is not repeated here: it has its own line, and one
    fact printed twice on one screen reads as a bug.
    """
    if status == "first_load" or not has_prior:
        return (
            "This is the first load Revi has walked your Monitors on. "
            f"{_plural(pins_evaluated, 'monitor', 'monitors')} and "
            f"{_plural(leads, 'detected lead', 'detected leads')} are now the baseline; from "
            "the next load on, this brief says what changed."
        )
    since = _load_phrase(prior_newest_data_date)
    if status == "nothing_material":
        # An ANSWER, not an empty page: it names what was measured and what
        # was found to be within tolerance.
        return (
            f"Nothing material has changed since {since}. Revi re-ran "
            f"{_plural(pins_evaluated, 'monitor', 'monitors')} against the new data and diffed "
            f"{_plural(leads, 'detected lead', 'detected leads')}."
        )
    kinds: dict[str, int] = {}
    for entry in entries:
        kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
    described = ", ".join(
        _plural(kinds[kind], *_KIND_NOUNS.get(kind, _KIND_NOUN_FALLBACK))
        for kind in sorted(kinds, key=lambda k: (-kinds[k], k))
    )
    return f"Since {since}: {described}."


def _leads_of(load: MonitorsLoad | None) -> dict[str, Mapping[str, Any]]:
    if load is None:
        return {}
    raw = load.payload.get("leads")
    return dict(raw) if isinstance(raw, dict) else {}


def _time_to_impact_payload(row: Mapping[str, Any]) -> TimeToImpactPayload | None:
    raw = row.get("time_to_impact")
    return TimeToImpactPayload.model_validate(raw) if isinstance(raw, dict) else None


def _ordinal(value: int) -> str:
    words = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}
    return words.get(value, f"{value}th")
