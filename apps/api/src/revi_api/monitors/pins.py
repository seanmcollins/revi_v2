"""Pins: creating them, listing them, repairing them, and what each one measures."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from revi_api.auth import AuthorizationError, Principal
from revi_api.monitors_policy import (
    format_threshold,
    validate_monitor,
)
from revi_investigation.application.ports import (
    Monitor,
    MonitorsPin,
    MonitorsPinResult,
    RegisteredReferent,
)
from revi_investigation.application.rendering import (
    format_value,
    metric_label,
)
from revi_investigation.application.submit_turn import TurnOutcome
from revi_investigation.domain.records import Investigation
from revi_investigation_contracts.api import (
    MonitorDeclarationPayload,
    MonitorModel,
    TypedInvestigationSpec,
)
from revi_investigation_contracts.monitors import (
    CreateMonitorsPinRequest,
    MonitorsPinListResponse,
    MonitorsPinPayload,
    MonitorsTilePayload,
)
from revi_investigation_contracts.refinements import (
    WindowSpecModel,
)
from revi_kernel.errors import PolicyDeniedError
from revi_kernel.filters import PredicateOp, iter_predicates

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    pass

from revi_api.monitors.common import MonitorsNotFoundError, _MonitorsBase, logger
from revi_api.monitors.spec import (
    _WINDOW_NOTES,
    _cell_phrase,
    _eq_filters_of,
    _narrowed_to_cell,
    _window_phrase,
    spec_hash,
    typed_spec_from_analysis,
)
from revi_api.monitors.tiles import _Headline, _spec_names_one_cell


class _PinApi(_MonitorsBase):
    """The pin CRUD surface, and the questions "what does this pin measure?"."""

    # ------------------------------------------------------------------ pins

    async def create_pin(
        self, principal: Principal, request: CreateMonitorsPinRequest
    ) -> MonitorsPinPayload:
        """Add a monitor, from an investigation on screen or from a typed spec."""
        if (request.investigation_id is None) == (request.spec is None):
            raise PolicyDeniedError(
                "a pin names exactly one of `investigation_id` (monitor what is on screen) or "
                "`spec` (monitor a typed spec you already hold) — a body carrying both, or "
                "neither, would leave the platform guessing which was meant",
                details={"tenant": principal.tenant},
            )
        notes: list[str] = []
        label = request.label.strip()
        created_from_kind = "spec"
        if request.investigation_id is not None:
            investigation = await self._authorized_investigation(
                principal, request.investigation_id
            )
            spec, window_mode, notes = typed_spec_from_analysis(investigation.spec)
            created_from_kind = "artifact"
            # THE CELL, not the ranking it was drawn from. A finding on a
            # ranked breakdown names ONE payer; the investigation's spec
            # names all of them, so pinning the spec unnarrowed yields a
            # tile titled for one payer whose number belongs to whichever
            # payer ranks first — wrong on the day it is created, not merely
            # after a rank flip.
            cell = await self._referent_cell(investigation, request.referent, spec)
            if cell:
                spec = _narrowed_to_cell(spec, cell)
                notes.append(
                    "this monitor was narrowed at creation to the cell you pinned "
                    f"({_cell_phrase(cell, self._components.pack_port)}), so it measures that "
                    "cell at every load rather than whatever ranks first"
                )
            elif self._referent_is_a_cell(investigation, request.referent) and spec.dimensions:
                raise PolicyDeniedError(
                    f"this monitor was pinned from {request.referent!r}, which names one cell of "
                    "a ranked breakdown, and that cell can no longer be resolved from the "
                    "investigation it came from — so the only thing left to pin is the whole "
                    "ranking, which would put one cell's name over whatever ranks first at "
                    "every future load. Re-run the answer and pin it again, or pin the "
                    "breakdown itself with a label that says so",
                    details={
                        "tenant": principal.tenant,
                        "investigation_id": request.investigation_id,
                        "referent": request.referent,
                    },
                )
            if not label:
                label = self._composed_label(spec)
        else:
            assert request.spec is not None
            spec = request.spec
            window_mode = (
                "relative" if isinstance(spec.window, WindowSpecModel) else "absolute"
            )
            if not label:
                label = self._composed_label(spec)
        # The threshold is checked BEFORE the duplicate lookup, so a request
        # carrying an illegal one is refused on its own terms rather than
        # answered with somebody else's monitor.
        monitor = _monitor_from_model(request.monitor)
        if monitor is not None:
            refusal = validate_monitor(monitor, units=self._units_for(spec.metric_ids))
            if refusal is not None:
                raise PolicyDeniedError(
                    f"this monitor's threshold cannot be applied honestly: {refusal}",
                    details={"tenant": principal.tenant, "monitor": request.monitor.model_dump()
                             if request.monitor is not None else None},
                )
        # Already monitoring this? Return THAT monitor rather than a second copy
        # of it. Every duplicate is re-evaluated every load and can brief one
        # movement N times, which is the alert fatigue the pack spends 300
        # lines preventing.
        existing_pin = await self._pin_with_same_spec(
            principal.tenant, spec, request.presentation
        )
        if existing_pin is not None:
            if monitor is not None and monitor != existing_pin.monitor:
                # A DIFFERENT sensitivity over the same spec is a different
                # instruction, and quietly answering it with the existing
                # monitor's threshold would be the silent substitution this
                # platform refuses everywhere else. Refused, naming the
                # monitor to adjust — creating a second one would brief the
                # same movement twice every morning.
                raise PolicyDeniedError(
                    f"you are already monitoring this spec as {existing_pin.label!r}, and this "
                    "request states a different sensitivity for it. A second monitor over the "
                    "same spec would brief the same movement twice every morning, and "
                    "quietly keeping the existing threshold would apply a number you did not "
                    "ask for — change that monitor's sensitivity instead",
                    details={"tenant": principal.tenant, "pin_id": existing_pin.id},
                )
            logger.info(
                "monitors pin create for tenant %s returned existing pin %s (same spec)",
                principal.tenant,
                existing_pin.id,
            )
            return self._pin_payload(
                existing_pin,
                notes=[
                    f"you are already monitoring this — {existing_pin.label!r} measures the same "
                    "spec, so this returned that monitor instead of creating a second one that "
                    "would brief the same movement twice every morning"
                ],
                already_existed=True,
            )
        pin = MonitorsPin(
            id=f"pin_{uuid.uuid4().hex[:12]}",
            tenant=principal.tenant,
            label=label or "Monitored spec",
            spec=spec,
            presentation=request.presentation,
            window_mode=window_mode,
            created_at=datetime.now(UTC),
            created_from_kind=created_from_kind,
            created_from_investigation_id=request.investigation_id,
            created_from_referent=request.referent,
            monitor=monitor,
            created_by=principal.subject,
        )
        await self._components.monitors_pins.save(pin)
        logger.info(
            "monitors pin %s created for tenant %s from %s (%s window)",
            pin.id,
            pin.tenant,
            created_from_kind,
            window_mode,
        )
        return self._pin_payload(pin, notes=notes)

    async def register_intent_pin(
        self,
        principal: Principal,
        outcome: TurnOutcome,
        *,
        stated_subject: str,
        monitor: Monitor | None,
        matched_phrase: str,
    ) -> MonitorDeclarationPayload:
        """Register a monitor declared in words, from the turn that answered it.

        The declaration turn has already run as an ordinary investigation —
        same interpretation, same planning, same §6.6 validation — so the
        spec pinned here is the one the analyst just saw answered, and that
        answer doubles as the baseline. Nothing is re-interpreted: this is
        the same ``typed_spec_from_analysis`` resolution the on-screen pin
        path uses, over the same stored spec.

        THE LABEL IS COMPOSED FROM THE RESOLVED SPEC, never from the words.
        ``stated_subject`` is what the analyst SAID, which is not always what
        the platform resolved: "monitor Silverline Health" resolves to
        Silverline Medicare Advantage, "monitor denied dollars for Meridian
        HMO Care" names a payer this warehouse does not contain, and "monitor
        this" would title a monitor with the pronoun. A monitor titled after
        an unresolved phrase is a monitor nobody can check. The analyst's own
        words are not thrown away — the confirmation names the resolution, so
        they can see what happened and correct it on the spot.

        WRITE ORDER IS LOAD-BEARING: the confirmation payload is composed
        BEFORE the pin is stored. Composed after, a monitor whose sensitivity
        the wire cannot describe (a ``days`` threshold) is written to the
        store and then fails to serialize, and the caller's blanket handler
        reports ``not_stored`` for a monitor that is live and briefing —
        so the analyst declares it again. Nothing is written until the
        sentence that reports it has been built.
        """
        investigation = outcome.investigation
        watermark = outcome.session.watermark
        watermark_id = watermark.id
        spec, window_mode, _ = typed_spec_from_analysis(investigation.spec)
        baseline = self._headline(outcome, spec)
        if monitor is not None:
            refusal = validate_monitor(monitor, units=self._units_for(spec.metric_ids))
            if refusal is not None:
                raise PolicyDeniedError(
                    f"this monitor's threshold cannot be applied honestly: {refusal}",
                    details={"tenant": principal.tenant},
                )
        label = self._composed_label(spec)
        # Already monitoring exactly this? The declaration path runs the same
        # spec-hash dedupe as the on-screen pin path: without it, two
        # identical monitors over one spec brief one movement twice every
        # morning — the alert fatigue the pack spends 300 lines preventing,
        # created by the platform itself.
        existing_pin = await self._pin_with_same_spec(principal.tenant, spec, "scalar")
        if existing_pin is not None:
            return self._existing_monitor_payload(
                existing_pin,
                spec=spec,
                stated_subject=stated_subject,
                stated_monitor=monitor,
                baseline=baseline,
                matched_phrase=matched_phrase,
            )
        pin = MonitorsPin(
            id=f"pin_{uuid.uuid4().hex[:12]}",
            tenant=principal.tenant,
            label=label,
            spec=spec,
            presentation="scalar",
            window_mode=window_mode,
            created_at=datetime.now(UTC),
            created_from_kind="intent",
            created_from_investigation_id=investigation.id,
            monitor=monitor,
            created_by=principal.subject,
            # The declaration turn IS the baseline load: the analyst saw
            # this number and said "monitor that". Capturing it here rather
            # than waiting for the next evaluation means the first brief can
            # already say how far it has moved since they asked.
            baseline_watermark_id=watermark_id if baseline is not None else None,
            baseline_value=baseline.value if baseline is not None else None,
            baseline_unit=baseline.unit if baseline is not None else None,
            baseline_captured_at=datetime.now(UTC) if baseline is not None else None,
        )
        threshold_statement = _threshold_statement(monitor, baseline.unit if baseline else None)
        alternative = _threshold_alternative(
            monitor, baseline.unit if baseline else None, baseline.value if baseline else None
        )
        value_text = baseline.text if baseline is not None else ""
        statement = _monitor_confirmation(
            label,
            value_text,
            threshold_statement,
            alternative,
            resolution=_resolution_clause(stated_subject, label, spec),
        )
        # Composed first, stored second: everything above can raise, and a
        # raise above this line leaves NOTHING monitoring, which is exactly
        # what the caller's refusal then says.
        payload = MonitorDeclarationPayload(
            pin_id=pin.id,
            label=label,
            statement=statement,
            spec=spec,
            monitor=_monitor_model(monitor),
            threshold_statement=threshold_statement,
            threshold_alternative=alternative,
            baseline_value_text=value_text,
            baseline_watermark_id=pin.baseline_watermark_id or "",
            matched_phrase=matched_phrase,
        )
        await self._components.monitors_pins.save(pin)
        # The declaration turn IS an evaluation of this monitor at this load,
        # so it is stored as one. Without it the baseline is a bare number
        # with no recorded cell and no recorded window, and every later
        # baseline delta has to refuse for want of the two facts that decide
        # whether it is a like-for-like comparison.
        if baseline is not None:
            try:
                # Against the pin's OWN newest earlier evaluation, not against
                # nothing. A monitor minted fresh has no history, but a
                # re-minted one whose history was restored does: hard-coding
                # ``None`` here would publish "first reading — nothing to
                # compare" while the brief, one screen above, back-walks the
                # same history and reports a movement.
                await self._store_tile(
                    pin,
                    watermark,
                    await self._tile_from_outcome(
                        pin, outcome, watermark, await self._prior_result(pin, watermark)
                    ),
                )
            except Exception:  # pragma: no cover - defensive; the monitor still stands
                logger.warning(
                    "monitors: baseline evaluation for monitor %s could not be stored", pin.id,
                    exc_info=True,
                )
        logger.info("monitor %s declared by intent for tenant %s", pin.id, pin.tenant)
        return payload

    def _existing_monitor_payload(
        self,
        pin: MonitorsPin,
        *,
        spec: TypedInvestigationSpec,
        stated_subject: str,
        stated_monitor: Monitor | None,
        baseline: _Headline | None,
        matched_phrase: str,
    ) -> MonitorDeclarationPayload:
        """The confirmation for a declaration that names a monitor this tenant
        already holds.

        No second pin, and no silent adoption of the existing sensitivity
        either: when the analyst stated one and it differs, the sentence says
        which threshold is actually in force and where to change it. Quietly
        answering "monitor this, more than 3 points" with an existing monitor set
        to 0.5 is the silent substitution this platform refuses everywhere
        else — and creating the second monitor instead would brief the same
        movement twice every morning.
        """
        unit = baseline.unit if baseline is not None else pin.baseline_unit
        threshold_statement = _threshold_statement(pin.monitor, unit)
        value_text = baseline.text if baseline is not None else ""
        current = f" — currently {value_text}" if value_text else ""
        sentence = (
            f"You are already monitoring this, as {pin.label!r}{current}. I have not created a "
            f"second monitor over the same spec — it would brief the same movement twice every "
            f"morning — so that one stands, and it briefs you {threshold_statement}."
        )
        if stated_monitor is not None and stated_monitor != pin.monitor:
            sentence += (
                f" You stated a different sensitivity just now; I have not quietly changed "
                f"{pin.label!r} to it. Change that monitor's threshold if the new one is what "
                "you want."
            )
        resolution = _resolution_clause(stated_subject, pin.label, spec)
        statement = f"{sentence} {resolution}".strip() if resolution else sentence
        return MonitorDeclarationPayload(
            pin_id=pin.id,
            label=pin.label,
            statement=statement,
            spec=pin.spec,
            monitor=_monitor_model(pin.monitor),
            threshold_statement=threshold_statement,
            threshold_alternative="",
            baseline_value_text=value_text,
            baseline_watermark_id=pin.baseline_watermark_id or "",
            matched_phrase=matched_phrase,
        )

    async def list_pins(self, principal: Principal) -> MonitorsPinListResponse:
        """Every monitor this tenant holds, composed ONE AT A TIME.

        A list comprehension here means one stored monitor the wire cannot
        describe — a ``days`` threshold, legal in the engine and missing from
        the wire's own enum — raises out of it and returns 500 for the
        ENTIRE TENANT, punishing every other pin in the list.

        A monitor that cannot be fully described is instead described as far
        as it can be, with the reason on the row: the analyst can still see
        WHAT is monitored, still un-pin it, and still read why its threshold
        is not rendering. A pin whose stored spec itself cannot be read has
        nothing left to publish, so it is named in :attr:`unreadable` rather
        than dropped silently.
        """
        pins = await self._components.monitors_pins.list_for_tenant(principal.tenant)
        payloads: list[MonitorsPinPayload] = []
        unreadable: list[str] = []
        for pin in pins:
            try:
                payloads.append(self._pin_payload(pin))
                continue
            except Exception:
                logger.exception(
                    "monitors: pin %s could not be composed for the pin list; degrading it "
                    "rather than failing the tenant's whole list",
                    pin.id,
                )
            try:
                payloads.append(self._degraded_pin_payload(pin))
            except Exception:
                logger.exception("monitors: pin %s cannot be published at all", pin.id)
                unreadable.append(pin.id)
        return MonitorsPinListResponse(
            tenant=principal.tenant,
            pins=payloads,
            total=len(payloads),
            unreadable=unreadable,
        )

    def _degraded_pin_payload(self, pin: MonitorsPin) -> MonitorsPinPayload:
        """What can still be published about a monitor the wire cannot fully
        describe: the spec, the label, the window — everything except the
        part that failed, with the reason where that part would have been."""
        return MonitorsPinPayload(
            pin_id=pin.id,
            tenant=pin.tenant,
            label=pin.label,
            presentation=pin.presentation,  # type: ignore[arg-type]
            spec=pin.spec,
            window_mode=pin.window_mode,  # type: ignore[arg-type]
            window_note=_WINDOW_NOTES.get(pin.window_mode, ""),
            created_from_kind=pin.created_from_kind,  # type: ignore[arg-type]
            created_from_investigation_id=pin.created_from_investigation_id,
            created_from_referent=pin.created_from_referent,
            notes=[
                "part of this monitor could not be described on this API version (the attempt "
                "is recorded in the API log), so it is shown without its sensitivity "
                "settings rather than removed from your list or failing the whole list. "
                "What it MEASURES, below, is the stored spec verbatim and is unaffected."
            ],
            monitor=None,
            baseline_watermark_id=pin.baseline_watermark_id,
            baseline_value=None if pin.baseline_value is None else float(pin.baseline_value),
            baseline_unit=pin.baseline_unit,
            created_at=pin.created_at,
            archived_at=pin.archived_at,
        )

    async def archive_pin(self, principal: Principal, pin_id: str) -> None:
        """Un-pin. SOFT, like every other dismissal on this platform: the
        evaluated history a brief already published stays readable, and a
        permalink into a tile's investigation does not 404 because somebody
        tidied their Monitors."""
        pin = await self._authorized_pin(principal, pin_id)
        await self._components.monitors_pins.archive(pin.id)
        logger.info("monitors pin %s archived by tenant %s", pin_id, principal.tenant)

    async def repair_pins(self, tenant: str) -> dict[str, list[str]]:
        """Bring monitors created before the narrowed-cell rule onto it.

        A monitor pinned from one cell of a ranked breakdown used to store
        the WHOLE ranking and title itself with that cell's finding, so its
        tile has been showing another subject's number since the day it was
        created. Narrowing at creation does not fix those stored rows, and
        leaving them is leaving the defect in production.

        Two outcomes, both stated:

        * the investigation it was pinned from still resolves the cell —
          the spec is narrowed to it, the label is recomposed from the
          narrowed spec, and the baseline is CLEARED so the next load
          captures it from the right cell. Keeping the old baseline would
          measure this cell against another cell's number, which is the
          same defect with a longer half-life;
        * it does not — the monitor is archived (softly, like every other
          dismissal here) and its last tile says why, because a monitor that
          silently kept publishing the wrong subject is worse than one that
          stopped and explained itself.

        Idempotent: a monitor already narrowed to its cell is left alone.

        RETURNS THE IDS IT TOUCHED, and the caller re-derives them. This
        rewrites the PIN row and nothing else, while the surface renders the
        stored TILE — and ``evaluate_load`` reuses any stored result for the
        current watermark, so without the re-derivation ``GET /v1/monitors``
        goes on serving pre-repair tiles until a watermark that may not be
        coming. A repair that does not change what the surface serves is not
        a repair.
        """
        repaired: list[str] = []
        archived: list[str] = []
        for pin in await self._components.monitors_pins.list_for_tenant(tenant):
            if not pin.created_from_referent or _spec_names_one_cell(pin.spec):
                continue
            investigation = (
                await self._components.investigations.get(pin.created_from_investigation_id)
                if pin.created_from_investigation_id
                else None
            )
            cell = (
                await self._referent_cell(investigation, pin.created_from_referent, pin.spec)
                if investigation is not None
                else ()
            )
            if not cell:
                await self._archive_unrepairable(pin)
                archived.append(pin.id)
                continue
            narrowed = _narrowed_to_cell(pin.spec, cell)
            await self._components.monitors_pins.save(
                replace(
                    pin,
                    spec=narrowed,
                    label=self._composed_label(narrowed),
                    baseline_watermark_id=None,
                    baseline_value=None,
                    baseline_unit=None,
                    baseline_captured_at=None,
                )
            )
            repaired.append(pin.id)
            logger.info(
                "monitors: pin %s narrowed to its pinned cell (%s) and its baseline reset",
                pin.id,
                _cell_phrase(cell, self._components.pack_port),
            )
        return {"repaired": repaired, "archived": archived}

    async def _archive_unrepairable(self, pin: MonitorsPin) -> None:
        """Stop a monitor that cannot be told which cell it is about, and say so
        where its number used to be."""
        newest = await self._components.monitors_results.history(pin.id, limit=1)
        note = (
            "this monitor was pinned from one cell of a ranked breakdown and stored the whole "
            "ranking, so its title named one subject and its number was whichever subject "
            "ranked first at each load. The answer it was created from can no longer resolve "
            "that cell, so the monitor has been stopped rather than left publishing a number "
            "under somebody else's name. Pin it again from a current answer to resume it."
        )
        for result in newest:
            tile = MonitorsTilePayload.model_validate(result.payload)
            await self._components.monitors_results.put(
                MonitorsPinResult(
                    pin_id=pin.id,
                    tenant=pin.tenant,
                    watermark_id=result.watermark_id,
                    watermark_loaded_at=result.watermark_loaded_at,
                    evaluated_at=datetime.now(UTC),
                    payload=tile.model_copy(
                        update={
                            "status": "unavailable",
                            "unavailable_reason": note,
                            "delta": None,
                            "baseline_delta": None,
                        }
                    ).model_dump(mode="json"),
                )
            )
        await self._components.monitors_pins.archive(pin.id)
        logger.warning("monitors: pin %s archived — its pinned cell cannot be resolved", pin.id)

    async def _authorized_pin(self, principal: Principal, pin_id: str) -> MonitorsPin:
        pin = await self._components.monitors_pins.get(pin_id)
        if pin is None:
            raise MonitorsNotFoundError(
                f"pin {pin_id!r} does not exist", details={"pin_id": pin_id}
            )
        if pin.tenant != principal.tenant:
            # Refused, not disguised as a 404: pin ids are not secrets, and
            # the same rule the session reads follow applies here.
            raise AuthorizationError(
                f"pin {pin_id!r} belongs to another tenant",
                details={"pin_id": pin_id, "tenant": principal.tenant},
            )
        return pin

    async def _authorized_investigation(
        self, principal: Principal, investigation_id: str
    ) -> Investigation:
        investigation = await self._components.investigations.get(investigation_id)
        if investigation is None:
            raise MonitorsNotFoundError(
                f"investigation {investigation_id!r} does not exist",
                details={"investigation_id": investigation_id},
            )
        session = await self._components.sessions.get(investigation.session_id)
        if session is None or session.tenant != principal.tenant:
            raise AuthorizationError(
                f"investigation {investigation_id!r} belongs to another tenant",
                details={"investigation_id": investigation_id, "tenant": principal.tenant},
            )
        return investigation

    # ------------------------------------------------- what a pin measures

    async def _referent_cell(
        self,
        investigation: Investigation,
        referent: str | None,
        spec: TypedInvestigationSpec,
    ) -> tuple[tuple[str, str], ...]:
        """The dimension members the pinned artifact stands for.

        This is what keeps a tile's label and its number naming the same
        subject. A finding on a ranked breakdown IS a cell — the referent
        registry has held its dimension members since §7.6, because that is
        how "drill into F1" works. Storing the parent spec and using the
        finding's TITLE as the label instead makes the tile headline
        whatever ranks first at each load under a title naming a different
        payer, and certify the result ``grade: direct``.

        Only members on dimensions this SPEC actually breaks out are
        returned: narrowing by a dimension the monitor does not measure would
        change the population without changing what the tile says it is.

        Empty when the referent names no cell (a scalar answer, a chart of
        the whole breakdown) or when its registry entry no longer belongs to
        this investigation — a referent id is session-scoped and a later
        turn reuses it, so the entry is only evidence about THIS
        investigation when it says so.
        """
        if not referent or not spec.dimensions:
            return ()
        entry = await self._referent_entry(investigation, referent)
        if entry is None:
            return ()
        wanted = list(spec.dimensions)
        members: dict[str, str] = {}
        # The cohort definition is the row's own scope: the spec's scope
        # AND one eq-predicate per dimension column. It carries every
        # dimension of a multi-dimension row, where ``dimension_value``
        # carries one and is None above a single column.
        if entry.cohort_definition is not None:
            for predicate in iter_predicates(entry.cohort_definition.scope):
                if predicate.op is not PredicateOp.EQ:
                    continue
                if predicate.dimension.id not in wanted:
                    continue
                members[predicate.dimension.id] = str(predicate.values[0])
        if not members and entry.dimension_value is not None:
            dimension, value = entry.dimension_value
            if dimension in wanted:
                members[dimension] = str(value)
        # All or nothing. A partial narrowing still leaves a ranking behind
        # the label, which is the defect with fewer dimensions.
        if set(members) != set(wanted):
            return ()
        return tuple((dimension, members[dimension]) for dimension in wanted)

    async def _referent_entry(
        self, investigation: Investigation, referent: str
    ) -> RegisteredReferent | None:
        for entry in await self._components.referents.list_for_session(
            investigation.session_id
        ):
            if entry.referent.value == referent and entry.investigation_id == investigation.id:
                return entry
        return None

    @staticmethod
    def _referent_is_a_cell(investigation: Investigation, referent: str | None) -> bool:
        """Does this referent name a FINDING on the pinned investigation?

        A finding referent on a breakdown is a cell and must resolve to one.
        A chart id is not: pinning a chart is a legitimate monitor of the
        whole ranking, and it gets a label that says so.
        """
        if not referent:
            return False
        return any(finding.referent.value == referent for finding in investigation.findings)

    def _composed_label(self, spec: TypedInvestigationSpec) -> str:
        """The tile's title, composed from what the monitor MEASURES.

        Never a finding title. A finding title carries the value it had on
        the load it was written ("Pinnacle Health Plan: 22.9% denial rate"),
        so it goes stale on the most prominent line of a surface whose whole
        job is to be current — and it names a subject the spec may not have
        been narrowed to. Composed from the spec, the label and the number
        cannot come from different loads or different cells.
        """
        pack = self._components.pack_port
        metrics = " and ".join(
            self._components.metric_display.name_for(metric_id) or metric_label(metric_id)
            for metric_id in spec.metric_ids
        ) or "Monitored spec"
        cell = _cell_phrase(_eq_filters_of(spec), pack)
        if cell:
            return f"{cell} — {metrics}"
        if spec.dimensions:
            dimensions = " and ".join(metric_label(d) for d in spec.dimensions)
            return f"{metrics} by {dimensions}"
        return metrics[:1].upper() + metrics[1:]

    def _spec_summary(self, spec: TypedInvestigationSpec, window_mode: str) -> str:
        """The stored spec in the reader's own nouns.

        The panel headed "What this monitor measures" is the one control
        that lets somebody catch a monitor measuring the wrong thing, so it
        names the metric, the breakdown and the filters — not just the
        window note and the analyst's own note.
        """
        pack = self._components.pack_port
        metrics = " and ".join(
            self._components.metric_display.name_for(metric_id) or metric_label(metric_id)
            for metric_id in spec.metric_ids
        )
        parts = [metrics[:1].upper() + metrics[1:] if metrics else "A typed spec"]
        if spec.dimensions:
            parts.append(
                "broken down by " + " and ".join(metric_label(d) for d in spec.dimensions)
            )
        filters = _eq_filters_of(spec)
        if filters:
            parts.append("filtered to " + _cell_phrase(filters, pack))
        other = [
            f"{metric_label(f.dimension)} {f.predicate_op} "
            f"{', '.join(str(v) for v in f.values)}"
            for f in spec.filters
            if f.predicate_op != "eq" or len(f.values) != 1
        ]
        if other:
            parts.append("filtered where " + "; ".join(other))
        window = _window_phrase(spec, window_mode)
        summary = ", ".join(parts)
        basis = f" on the {spec.basis} basis" if spec.basis else ""
        return f"{summary} — {window}{basis}."

    async def _pin_with_same_spec(
        self, tenant: str, spec: TypedInvestigationSpec, presentation: str
    ) -> MonitorsPin | None:
        """An ACTIVE monitor on this tenant already measuring exactly this."""
        digest = spec_hash(spec, presentation)
        for pin in await self._components.monitors_pins.list_for_tenant(tenant):
            if pin.archived_at is not None:
                continue
            if spec_hash(pin.spec, pin.presentation) == digest:
                return pin
        return None

    def _pin_payload(
        self,
        pin: MonitorsPin,
        *,
        notes: Sequence[str] = (),
        already_existed: bool = False,
    ) -> MonitorsPinPayload:
        return pin_payload(
            pin,
            notes=notes,
            spec_summary=self._spec_summary(pin.spec, pin.window_mode),
            already_existed=already_existed,
        )

    def _units_for(self, metric_ids: Sequence[str]) -> tuple[str | None, ...]:
        pack = self._components.pack_port
        units: list[str | None] = []
        for metric_id in metric_ids:
            contract = pack.metric(metric_id)
            unit = getattr(contract, "unit", None)
            units.append(None if unit is None else str(unit))
        return tuple(units)


def _monitor_from_model(model: MonitorModel | None) -> Monitor | None:
    if model is None:
        return None
    return Monitor(
        mode=model.mode,
        value=None if model.value is None else Decimal(str(model.value)),
        unit=model.unit,
        direction=model.direction,
        note=model.note,
    )


def _monitor_model(monitor: Monitor | None) -> MonitorModel:
    if monitor is None:
        return MonitorModel()
    return MonitorModel(
        mode=monitor.mode,  # type: ignore[arg-type]
        value=None if monitor.value is None else float(monitor.value),
        unit=monitor.unit,  # type: ignore[arg-type]
        direction=monitor.direction,  # type: ignore[arg-type]
        note=monitor.note,
    )


def _threshold_statement(monitor: Monitor | None, unit: str | None) -> str:
    """The gate in words, for the confirmation sentence."""
    if monitor is None or monitor.mode == "governed_default":
        return "when it moves more than the governed threshold for this measure"
    if monitor.mode == "any_movement":
        return "on any movement at all"
    if monitor.mode == "crosses":
        return f"when it crosses {format_threshold(monitor, unit)}"
    return f"when it moves {format_threshold(monitor, unit)} or more"


def _threshold_alternative(
    monitor: Monitor | None, unit: str | None, reference: Decimal | None
) -> str:
    """The OTHER honest reading of the analyst's threshold words.

    "more than 2%" against a rate is genuinely ambiguous — two percentage
    points, or two percent of the current value — and this platform refuses
    that ambiguity everywhere else. The reading committed to is the one
    legal against every contract (``relative_pct``), which on a 25.9% base
    makes the gate about half a point: four times tighter than the pack's
    own, with the fatigue advisory then telling the analyst to tighten
    thresholds they never loosened.

    Empty when the words admit only one reading.
    """
    if monitor is None or monitor.unit != "relative_pct" or monitor.value is None:
        return ""
    if unit != "ratio":
        return ""
    stated = f"{float(monitor.value):.10g}%"
    points = f"{float(monitor.value):.10g} points"
    if reference is None or not reference:
        return (
            f"I read {stated} as {stated} of the current value, not as {points} — say "
            f"{points!r} if you meant percentage points."
        )
    gate = abs(reference) * monitor.value / 100
    return (
        f"I read {stated} as {stated} of the current value, which is about "
        f"{format_value(gate, 'ratio')} at today's level — say {points!r} if you meant "
        "percentage points, which is the larger gate."
    )


def _resolution_clause(
    stated_subject: str, label: str, spec: TypedInvestigationSpec
) -> str:
    """"You said X; that resolved to Y" — or nothing, when they match.

    A monitor is titled from the RESOLVED spec, the only title that can be
    checked against the number under it. That is also a substitution, so it
    is stated: "monitor Silverline Health" becomes a monitor on Silverline
    Medicare Advantage, and an analyst who is never told cannot catch the
    day the resolution was wrong. Silent only when there is nothing to
    report — the words the analyst used already appear in the label.
    """
    stated = " ".join(stated_subject.split())
    if not stated:
        return ""
    said = stated.casefold()
    if said in label.casefold():
        return ""
    cells = [str(value) for _, value in _eq_filters_of(spec)]
    # They named the cell it resolved to, in either direction — "monitor
    # Pinnacle" against a spec fixed to "Pinnacle Health Plan", or "days in
    # A/R for Atlas Commercial" against "Atlas Commercial". Nothing was
    # substituted, so there is nothing to report, and a sentence that fires
    # every time is a sentence nobody reads on the day it matters.
    if cells and all(
        value.casefold() in said or said in value.casefold() for value in cells
    ):
        return ""
    return (
        f"You said {stated!r}; that resolved to {label!r}, which is what this monitor measures "
        "and what its tile is titled — say so now if that is not the one you meant."
    )


def _monitor_confirmation(
    label: str,
    value_text: str,
    threshold_statement: str,
    alternative: str = "",
    resolution: str = "",
) -> str:
    """The one-time baseline confirmation, composed from the answer.

    Every clause is a fact the payload also carries: what is monitored, what
    it reads right now, and what will bring it back. Never composed by a
    model — a generated sentence could not be validated against the answer
    beside it any more cheaply than writing it from that answer.
    """
    current = f" — currently {value_text}" if value_text else ""
    sentence = (
        f"Monitoring: {label}{current}. I'll brief you {threshold_statement}, and the answer "
        "above is the baseline I'll measure that from."
    )
    return " ".join(part for part in (sentence, resolution, alternative) if part)


def pin_payload(
    pin: MonitorsPin,
    *,
    notes: Sequence[str] = (),
    spec_summary: str = "",
    already_existed: bool = False,
) -> MonitorsPinPayload:
    window_note = _WINDOW_NOTES.get(pin.window_mode, "")
    if notes:
        window_note = " ".join([window_note, *notes]).strip()
    return MonitorsPinPayload(
        pin_id=pin.id,
        tenant=pin.tenant,
        label=pin.label,
        presentation=pin.presentation,  # type: ignore[arg-type]
        spec=pin.spec,
        spec_summary=spec_summary,
        notes=list(notes),
        already_existed=already_existed,
        window_mode=pin.window_mode,  # type: ignore[arg-type]
        window_note=window_note,
        created_from_kind=pin.created_from_kind,  # type: ignore[arg-type]
        created_from_investigation_id=pin.created_from_investigation_id,
        created_from_referent=pin.created_from_referent,
        monitor=_monitor_model(pin.monitor) if pin.monitor is not None else None,
        baseline_watermark_id=pin.baseline_watermark_id,
        baseline_value=None if pin.baseline_value is None else float(pin.baseline_value),
        baseline_value_text=(
            format_value(pin.baseline_value, pin.baseline_unit)
            if pin.baseline_value is not None
            else ""
        ),
        baseline_unit=pin.baseline_unit,
        created_at=pin.created_at,
        archived_at=pin.archived_at,
    )
