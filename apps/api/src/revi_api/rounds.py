"""Rounds: the proactive surface. Revi walks it every load and briefs it.

Four capabilities, one seam each — none of them a new pipeline:

**PIN = WATCH.** A pin stores the ``TypedInvestigationSpec`` behind an
artifact, never the artifact. Evaluating it re-runs that spec through
``SubmitTurnService`` as an ordinary TYPED first turn: zero model calls,
the §6.6 validation pass, the real findings stage, the real warnings. That
choice is the load-bearing one in this module. The obvious alternative —
a lightweight evaluator that sums a frame, in the shape of
:mod:`revi_api.rederive` — would be a SECOND implementation of the honesty
rules, and every caveat the answer path has learned to publish (bounded
cells, provisional buckets, population caveats, alternate bases, grade
demotion) would have to be re-earned there or silently lost. Six
adversarial rounds went into those rules. A tile is an answer, so a tile
runs the answer path.

The consequence is deliberate and good: every tile IS a real
``Investigation``, with a real trace and a real permalink, so tapping a
tile opens the full investigation rather than a number computed off to the
side.

**PER-LOAD EVALUATION.** :meth:`RoundsService.evaluate_load` is idempotent
per (pin, watermark) and is called from two places for the same reason the
cohort sweep is: the scheduled tick (``revi_api.rounds_sweep``) keeps an
idle deployment current, and the brief route calls it too, so a brief for
a load nobody swept is computed rather than empty. One primitive, two
callers, no drift.

**MATERIALITY.** Every gate is governed content
(``packs/base-rcm/rounds.yaml`` via :mod:`revi_api.rounds_policy`); this
module holds no threshold. Alert fatigue is the death mode, so when in
doubt the gate holds: an unmeasurable movement is counted, not briefed.
Everything withheld is counted on the response — withheld visibly, never
silently.

**LEAD LIFECYCLE.** A human may claim a lead is resolved; only the
platform may confirm it, by re-running the lead's own drill across
consecutive loads. That asymmetry is the product.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from revi_api.assembly import finding_payload
from revi_api.auth import AuthorizationError, Principal
from revi_api.evidence import build_evidence
from revi_api.portfolio import PRIORITY_FORMULA_VERSION
from revi_api.rounds_policy import (
    MaterialityVerdict,
    ResolutionPolicy,
    RoundsPolicy,
    assess_movement,
    assess_new_lead,
    assess_self_resolved,
    format_threshold,
    time_to_impact_for,
    validate_watch,
)
from revi_api.warning_codes import CAUTION, structured_warnings
from revi_investigation.application.dto_mapping import refinement_to_dto
from revi_investigation.application.ports import (
    LEAD_STATUSES_HUMAN_SETTABLE,
    AnomalyRecord,
    RoundsLead,
    RoundsLoad,
    RoundsPin,
    RoundsPinResult,
    RoundsWatch,
)
from revi_investigation.application.rendering import format_value, magnitude
from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.context import AnalysisSpec, PackVersionRef
from revi_investigation.domain.records import Finding, Investigation, Session
from revi_investigation.domain.refinements import AddFilter
from revi_investigation_contracts.api import (
    AnomalyCard,
    PortfolioResponse,
    RoundsWatchModel,
    TimeToImpactPayload,
    TypedInvestigationSpec,
    WatchDeclarationPayload,
)
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    WindowSpecModel,
)
from revi_investigation_contracts.rounds import (
    CreateRoundsPinRequest,
    RoundsBriefEntry,
    RoundsBriefResponse,
    RoundsDeltaPayload,
    RoundsFatigueAdvisory,
    RoundsImmaterialSummary,
    RoundsLeadPatchRequest,
    RoundsLeadPayload,
    RoundsPinListResponse,
    RoundsPinPayload,
    RoundsProvenancePayload,
    RoundsResponse,
    RoundsTileIntegrity,
    RoundsTilePayload,
)
from revi_kernel.errors import ErrorCode, PolicyDeniedError, ReviError
from revi_kernel.filters import iter_predicates
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.scope import ComparisonKind
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    from revi_api.wiring import ApiComponents

logger = logging.getLogger("revi.api.rounds")

#: Builds the ranked portfolio for one tenant at one watermark. Supplied by
#: :class:`~revi_api.service.ApiService` rather than re-implemented here, so
#: a brief's "new lead" and the rail's card are the same object from the
#: same build — the rule the conversational worklist already follows.
PortfolioFor = Callable[[str, DataWatermark], Awaitable[PortfolioResponse]]

#: What a pin evaluation's turn records as its question. Never shown as an
#: analyst's words, because it is not one: it names the watch it re-ran.
_EVALUATION_QUESTION = "(Rounds: re-running a watched spec at this load)"


class RoundsNotFoundError(ReviError):
    """A pin, lead or load the caller named does not exist (HTTP 404).

    ``REFERENT_NOT_FOUND`` for the same reason
    :class:`~revi_api.service.NotFoundError` uses it: "the thing you named
    does not exist here" is one failure whether the handle is a pin id or a
    watermark, and ``UNSUPPORTED_CONCEPT`` would say something different and
    false.
    """

    code = ErrorCode.REFERENT_NOT_FOUND


# ---------------------------------------------------------------------------
# resolving a stored investigation into a re-runnable typed spec


#: Comparison kinds a typed spec can express. ``CUSTOM`` is a pair of
#: literal dates that meant something on the turn that set it; carrying it
#: onto a watch would freeze a comparison window while the primary window
#: moved, which is a different measurement every load.
_COMPARISON_LITERALS = {
    ComparisonKind.PRIOR_PERIOD: "prior_period",
    ComparisonKind.PRIOR_YEAR: "prior_year",
}


def typed_spec_from_analysis(spec: AnalysisSpec) -> tuple[TypedInvestigationSpec, str, list[str]]:
    """The stored ``AnalysisSpec`` as a re-runnable typed spec.

    This is what "pin-from-investigation resolves the stored spec
    server-side" means concretely: the investigation already holds the
    disposed, validated spec its answer was computed from, so the pin is
    built from THAT and no text is re-interpreted and no model is called.
    Re-deriving the spec from the question would be a second, worse answer
    to a question already answered — and it would drift the day the
    interpreter improved.

    Returns the spec, its ``window_mode``, and any notes about what could
    not be carried across. The notes matter: a watch that silently dropped
    a scope clause would measure a different population from the answer the
    analyst was looking at when they pinned it.
    """
    notes: list[str] = []
    window = spec.context.window
    # A RELATIVE window re-anchors per load, so the watch tracks a moving
    # period and a delta is a real movement. An absolute one re-measures
    # fixed dates, so a delta is late-arriving data. Both are legitimate
    # watches; only one of them is a movement, which is why the mode is
    # published rather than inferred by a reader.
    if window.requested is not None:
        window_model: WindowSpecModel | AbsoluteWindowModel = WindowSpecModel(
            quantity=str(window.requested.quantity),
            unit=window.requested.unit.value,
            mode=window.requested.mode.value,
        )
        window_mode = "relative"
    else:
        window_model = AbsoluteWindowModel(start=window.range.start, end=window.range.end)
        window_mode = "absolute"

    filters: list[AddFilterModel] = []
    for predicate in iter_predicates(spec.context.scope):
        dto = refinement_to_dto(AddFilter(predicate))
        assert isinstance(dto, AddFilterModel)  # AddFilter maps to exactly this
        filters.append(dto)
    # Pins (session-sticky scope) are deliberately NOT carried: they belong
    # to the session that declared them, and a watch that inherited another
    # conversation's sticky filter would narrow every future load by a
    # decision nobody made about this watch.
    if spec.context.pins:
        notes.append(
            f"{len(spec.context.pins)} session-pinned scope clause(s) were not carried onto "
            "this watch: a pin belongs to the conversation that declared it, and inheriting "
            "one here would narrow every future load by a decision nobody made about the watch"
        )
    if spec.context.cohort is not None:
        notes.append(
            "the answer this watch was created from was computed over a pinned cohort "
            f"({spec.context.cohort.id}), which is an extensional set materialized at one "
            "watermark; the watch carries the scope predicates instead, so it re-selects the "
            "population at every load rather than re-reading a frozen one"
        )

    comparison: str | None = None
    if spec.context.comparison is not None:
        comparison = _COMPARISON_LITERALS.get(spec.context.comparison.kind)
        if comparison is None:
            notes.append(
                "the answer's custom comparison window was not carried onto this watch: it is "
                "a pair of literal dates, and holding it fixed while the primary window moves "
                "would make every load a different measurement"
            )

    typed = TypedInvestigationSpec(
        metric_ids=[measure.id for measure in spec.measures],
        dimensions=[dimension.id for dimension in spec.dimensions],
        filters=filters,
        window=window_model,
        basis=window.basis.id,
        comparison=comparison,  # type: ignore[arg-type]
    )
    return typed, window_mode, notes


_WINDOW_NOTES = {
    "relative": (
        "This watch re-anchors its window to each load's newest data date, so it usually "
        "tracks a moving period. Where two loads land inside the same period it resolves to "
        "the same dates, and the change between them is late-arriving data rather than a "
        "movement — each tile publishes the dates it actually measured, so a reader never "
        "has to guess which of the two they are looking at."
    ),
    "absolute": (
        "This watch re-measures the SAME fixed dates every load, so a load-over-load change "
        "is always late-arriving data (adjudication run-out, back-dated charges) rather than "
        "a movement in the period itself."
    ),
}
_WINDOW_NOTES["anchored"] = _WINDOW_NOTES["absolute"]

#: The sentence a movement earns when both loads measured the SAME dates.
#: Not a caveat about the number — the number is right — but about what the
#: change MEANS, which is the thing a delta on a daily surface is read for.
SAME_WINDOW_NOTE = (
    "Both loads measured the same dates ({start}..{end}), so this change is late-arriving "
    "data settling — adjudication run-out, back-dated charges — rather than a movement in "
    "the period itself."
)


# ---------------------------------------------------------------------------
# the service


class RoundsService:
    """Pins, per-load evaluation, the brief, and the lead lifecycle."""

    def __init__(
        self,
        components: ApiComponents,
        *,
        portfolio_for: PortfolioFor,
    ) -> None:
        self._components = components
        self._portfolio_for = portfolio_for

    @property
    def policy(self) -> RoundsPolicy:
        return self._components.rounds_policy

    # ------------------------------------------------------------------ pins

    async def create_pin(
        self, principal: Principal, request: CreateRoundsPinRequest
    ) -> RoundsPinPayload:
        """Add a watch, from an investigation on screen or from a typed spec."""
        if (request.investigation_id is None) == (request.spec is None):
            raise PolicyDeniedError(
                "a pin names exactly one of `investigation_id` (watch what is on screen) or "
                "`spec` (watch a typed spec you already hold) — a body carrying both, or "
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
            if not label:
                label = self._label_for(investigation, request.referent)
        else:
            assert request.spec is not None
            spec = request.spec
            window_mode = (
                "relative" if isinstance(spec.window, WindowSpecModel) else "absolute"
            )
            if not label:
                label = ", ".join(spec.metric_ids)
        watch = _watch_from_model(request.watch)
        if watch is not None:
            refusal = validate_watch(watch, units=self._units_for(spec.metric_ids))
            if refusal is not None:
                raise PolicyDeniedError(
                    f"this watch's threshold cannot be applied honestly: {refusal}",
                    details={"tenant": principal.tenant, "watch": request.watch.model_dump()
                             if request.watch is not None else None},
                )
        pin = RoundsPin(
            id=f"pin_{uuid.uuid4().hex[:12]}",
            tenant=principal.tenant,
            label=label or "Watched spec",
            spec=spec,
            presentation=request.presentation,
            window_mode=window_mode,
            created_at=datetime.now(UTC),
            created_from_kind=created_from_kind,
            created_from_investigation_id=request.investigation_id,
            created_from_referent=request.referent,
            watch=watch,
            created_by=principal.subject,
        )
        await self._components.rounds_pins.save(pin)
        logger.info(
            "rounds pin %s created for tenant %s from %s (%s window)",
            pin.id,
            pin.tenant,
            created_from_kind,
            window_mode,
        )
        return pin_payload(pin, notes=notes)

    async def register_intent_pin(
        self,
        principal: Principal,
        outcome: TurnOutcome,
        *,
        label: str,
        watch: RoundsWatch | None,
        matched_phrase: str,
    ) -> WatchDeclarationPayload:
        """Register a watch declared in words, from the turn that answered it.

        The declaration turn has already run as an ordinary investigation —
        same interpretation, same planning, same §6.6 validation — so the
        spec pinned here is the one the analyst just saw answered, and that
        answer doubles as the baseline. Nothing is re-interpreted: this is
        the same ``typed_spec_from_analysis`` resolution the on-screen pin
        path uses, over the same stored spec.
        """
        investigation = outcome.investigation
        watermark_id = outcome.session.watermark.id
        spec, window_mode, _ = typed_spec_from_analysis(investigation.spec)
        baseline = self._headline(outcome, spec)
        if watch is not None:
            refusal = validate_watch(watch, units=self._units_for(spec.metric_ids))
            if refusal is not None:
                raise PolicyDeniedError(
                    f"this watch's threshold cannot be applied honestly: {refusal}",
                    details={"tenant": principal.tenant},
                )
        pin = RoundsPin(
            id=f"pin_{uuid.uuid4().hex[:12]}",
            tenant=principal.tenant,
            label=label,
            spec=spec,
            presentation="scalar",
            window_mode=window_mode,
            created_at=datetime.now(UTC),
            created_from_kind="intent",
            created_from_investigation_id=investigation.id,
            watch=watch,
            created_by=principal.subject,
            # The declaration turn IS the baseline load: the analyst saw
            # this number and said "watch that". Capturing it here rather
            # than waiting for the next evaluation means the first brief can
            # already say how far it has moved since they asked.
            baseline_watermark_id=watermark_id if baseline is not None else None,
            baseline_value=baseline.value if baseline is not None else None,
            baseline_unit=baseline.unit if baseline is not None else None,
            baseline_captured_at=datetime.now(UTC) if baseline is not None else None,
        )
        await self._components.rounds_pins.save(pin)
        logger.info("rounds watch %s declared by intent for tenant %s", pin.id, pin.tenant)
        threshold_statement = _threshold_statement(watch, baseline.unit if baseline else None)
        value_text = baseline.text if baseline is not None else ""
        statement = _watch_confirmation(label, value_text, threshold_statement)
        return WatchDeclarationPayload(
            pin_id=pin.id,
            label=label,
            statement=statement,
            spec=spec,
            watch=_watch_model(watch),
            threshold_statement=threshold_statement,
            baseline_value_text=value_text,
            baseline_watermark_id=pin.baseline_watermark_id or "",
            matched_phrase=matched_phrase,
        )

    async def list_pins(self, principal: Principal) -> RoundsPinListResponse:
        pins = await self._components.rounds_pins.list_for_tenant(principal.tenant)
        return RoundsPinListResponse(
            tenant=principal.tenant,
            pins=[pin_payload(pin) for pin in pins],
            total=len(pins),
        )

    async def archive_pin(self, principal: Principal, pin_id: str) -> None:
        """Un-pin. SOFT, like every other dismissal on this platform: the
        evaluated history a brief already published stays readable, and a
        permalink into a tile's investigation does not 404 because somebody
        tidied their Rounds."""
        pin = await self._authorized_pin(principal, pin_id)
        await self._components.rounds_pins.archive(pin.id)
        logger.info("rounds pin %s archived by tenant %s", pin_id, principal.tenant)

    async def _authorized_pin(self, principal: Principal, pin_id: str) -> RoundsPin:
        pin = await self._components.rounds_pins.get(pin_id)
        if pin is None:
            raise RoundsNotFoundError(
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
            raise RoundsNotFoundError(
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

    def _label_for(self, investigation: Investigation, referent: str | None) -> str:
        """The tile's title: the pinned finding's own, else the question.

        Never a generated label. The analyst wrote one of these two, and a
        title nobody wrote is the first thing on a tile that cannot be
        checked against anything.
        """
        if referent:
            for finding in investigation.findings:
                if finding.referent.value == referent:
                    return finding.title
        if investigation.findings:
            return investigation.findings[0].title
        return investigation.question or "Watched spec"

    def _units_for(self, metric_ids: Sequence[str]) -> tuple[str | None, ...]:
        pack = self._components.pack_port
        units: list[str | None] = []
        for metric_id in metric_ids:
            contract = pack.metric(metric_id)
            unit = getattr(contract, "unit", None)
            units.append(None if unit is None else str(unit))
        return tuple(units)

    # ------------------------------------------------------- per-load evaluation

    async def evaluate_load(
        self, tenant: str, watermark: DataWatermark, *, force: bool = False
    ) -> RoundsLoad:
        """Re-run every active pin at this load, verify claimed resolutions,
        and record the detection-feed census.

        Idempotent per (pin, watermark): a stored result is reused rather
        than recomputed, so calling this from the scheduled sweep and from
        the brief route costs one evaluation between them. ``force``
        re-evaluates — for a redeployed pack, or a repaired snapshot.
        """
        pins = await self._components.rounds_pins.list_for_tenant(tenant)
        evaluated = 0
        for pin in pins:
            existing = await self._components.rounds_results.get(pin.id, watermark.id)
            if existing is not None and not force:
                continue
            await self._evaluate_pin(pin, watermark)
            evaluated += 1

        portfolio = await self._portfolio_for(tenant, watermark)
        verifications = await self._verify_claimed_leads(tenant, watermark, portfolio)
        census = await self._census(tenant, watermark, portfolio, pins, verifications)
        load = RoundsLoad(
            tenant=tenant,
            watermark_id=watermark.id,
            watermark_loaded_at=watermark.loaded_at,
            evaluated_at=datetime.now(UTC),
            payload=census,
        )
        await self._components.rounds_loads.put(load)
        logger.info(
            "rounds: evaluated %d of %d pin(s) and verified %d claimed lead(s) for tenant %s "
            "at %s",
            evaluated,
            len(pins),
            len(verifications),
            tenant,
            watermark.id,
        )
        return load

    async def _evaluate_pin(self, pin: RoundsPin, watermark: DataWatermark) -> RoundsTilePayload:
        """Run one pin's stored spec at one load, and store the tile.

        The evaluation is an ordinary TYPED first turn — see the module
        docstring for why this is the answer path and not a lighter one.
        """
        prior = await self._prior_result(pin, watermark)
        session = await self._rounds_session(pin.tenant, watermark)
        try:
            outcome = await self._components.submit.submit(
                SubmitTurnRequest(
                    tenant=pin.tenant,
                    question=_EVALUATION_QUESTION,
                    session_id=session.id,
                    spec=pin.spec,
                )
            )
        except ReviError as exc:
            # A stored spec can stop being answerable — a catalog change, a
            # pack that retired a metric. The tile says so in the platform's
            # own error vocabulary rather than going blank, which would read
            # as a zero.
            tile = RoundsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="unavailable",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                unavailable_reason=f"{exc.code.value}: {exc.message}",
            )
            await self._store_tile(pin, watermark, tile)
            return tile
        except Exception:
            logger.exception("rounds: pin %s could not be evaluated at %s", pin.id, watermark.id)
            tile = RoundsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="unavailable",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                unavailable_reason="this watch could not be evaluated at this load (the "
                "attempt is recorded in the API log); no value is published rather than a "
                "stale one",
            )
            await self._store_tile(pin, watermark, tile)
            return tile

        tile = await self._tile_from_outcome(pin, outcome, watermark, prior)
        # The baseline is captured ONCE, at the first load that produces a
        # value. A watch created between loads has no baseline until then,
        # and taking the previous load's value would attribute a movement to
        # a period nobody was watching.
        if pin.baseline_value is None and tile.value is not None:
            pin = replace(
                pin,
                baseline_watermark_id=watermark.id,
                baseline_value=Decimal(str(tile.value)),
                baseline_unit=tile.unit,
                baseline_captured_at=datetime.now(UTC),
            )
            await self._components.rounds_pins.save(pin)
        tile = tile.model_copy(update={"baseline_delta": self._baseline_delta(pin, tile)})
        await self._store_tile(pin, watermark, tile)
        return tile

    async def _store_tile(
        self, pin: RoundsPin, watermark: DataWatermark, tile: RoundsTilePayload
    ) -> None:
        await self._components.rounds_results.put(
            RoundsPinResult(
                pin_id=pin.id,
                tenant=pin.tenant,
                watermark_id=watermark.id,
                watermark_loaded_at=watermark.loaded_at,
                evaluated_at=datetime.now(UTC),
                payload=tile.model_dump(mode="json"),
            )
        )

    async def _prior_result(
        self, pin: RoundsPin, watermark: DataWatermark
    ) -> RoundsTilePayload | None:
        """This pin's newest evaluation STRICTLY BEFORE this load.

        Ordered by the load's own clock, never by watermark id: that
        ``wm_001`` sorts before ``wm_002`` is a coincidence of one
        warehouse's naming, and diffing the wrong pair of loads is worse
        than diffing none.
        """
        for result in await self._components.rounds_results.history(pin.id, limit=12):
            if _utc(result.watermark_loaded_at) < _utc(watermark.loaded_at):
                return RoundsTilePayload.model_validate(result.payload)
        return None

    async def _rounds_session(self, tenant: str, watermark: DataWatermark) -> Session:
        """The session a load's evaluations run in, pinned AT that load.

        Deterministic per (tenant, watermark) so re-evaluating a load reuses
        one session instead of minting one per tile, and created ARCHIVED so
        it never appears in the analyst's rail — a soft archive keeps every
        investigation inside it fetchable by id, which is what makes a tile's
        permalink work.

        Pinned at the requested watermark rather than the newest, because
        that is what evaluating a load MEANS. Re-running a historical load
        therefore produces an honest ``watermark_stale`` on the turn: the
        session really is behind the newest data, and the tile names the
        load it was measured at.
        """
        digest = hashlib.sha256(f"{tenant}|{watermark.id}".encode()).hexdigest()[:16]
        session_id = f"rounds_{digest}"
        existing = await self._components.sessions.get(session_id)
        if existing is not None:
            return existing
        pack = self._components.pack_port
        session = Session(
            id=session_id,
            tenant=tenant,
            pack_version=PackVersionRef(pack.pack_id, pack.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=watermark),),
            created_at=datetime.now(UTC),
        )
        await self._components.sessions.save(session)
        await self._components.sessions.archive(session_id)
        return session

    async def _tile_from_outcome(
        self,
        pin: RoundsPin,
        outcome: TurnOutcome,
        watermark: DataWatermark,
        prior: RoundsTilePayload | None,
    ) -> RoundsTilePayload:
        if outcome.clarification is not None:
            # A typed spec should never clarify. If one does, that is
            # reported rather than swallowed: it means the stored spec has
            # become ambiguous against the current pack, which is a fact
            # about the watch and not a blank tile.
            return RoundsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="clarification",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                investigation_id=outcome.investigation.id,
                unavailable_reason=(
                    "re-running this watch's stored spec at this load asked a question rather "
                    f"than answering: {outcome.clarification.question}"
                ),
            )
        trace = await self._components.traces.get(outcome.trace_id)
        evidence = build_evidence(trace) if trace is not None else None
        warnings = list(outcome.warnings)
        classified = structured_warnings(warnings)
        headline = self._headline(outcome, pin.spec)
        grade = evidence.answer_grade if evidence is not None else None
        if grade is None and outcome.findings:
            grade = min_grade(*(f.grade for f in outcome.findings)).value
        integrity = RoundsTileIntegrity(
            grade=grade or EvidenceGrade.UNAVAILABLE.value,
            things_to_know=len(classified),
            things_to_know_caution=sum(1 for w in classified if w.severity == CAUTION),
            caveat_codes=list(dict.fromkeys(w.code for w in classified)),
            checks=len(evidence.probes) if evidence is not None else 0,
            is_bound=headline.is_bound if headline is not None else False,
            # "adjudication_incomplete" is the engine's own statement that a
            # terminal bucket is not yet a settled measurement. Read off the
            # warning rather than re-derived, so the tile and the answer
            # cannot disagree about whether a number has settled.
            provisional=any(w.code == "ADJUDICATION_INCOMPLETE" for w in classified),
        )
        return RoundsTilePayload(
            pin_id=pin.id,
            label=pin.label,
            presentation=pin.presentation,  # type: ignore[arg-type]
            status="ok",
            watermark_id=watermark.id,
            newest_data_date=watermark.newest_data_date,
            evaluated_at=datetime.now(UTC),
            # The dates this load actually measured, off the turn's own §7.2
            # header — so a reader can tell a moving period from a re-measured
            # one without re-resolving the window themselves.
            window_start=outcome.header.window_start if outcome.header is not None else None,
            window_end=outcome.header.window_end if outcome.header is not None else None,
            investigation_id=outcome.investigation.id,
            headline_referent=headline.referent if headline is not None else None,
            headline_title=headline.title if headline is not None else "",
            headline_statement=headline.statement if headline is not None else "",
            value_text=headline.text if headline is not None else "",
            value=float(headline.value) if headline is not None else None,
            unit=headline.unit if headline is not None else None,
            metric_id=headline.metric_id if headline is not None else None,
            integrity=integrity,
            warnings=warnings,
            warnings_v2=classified,
            findings=[
                finding_payload(f, outcome.benchmarks, self._components.metric_display)
                for f in outcome.findings
            ],
            delta=self._delta(
                pin,
                headline,
                prior,
                (
                    (outcome.header.window_start, outcome.header.window_end)
                    if outcome.header is not None
                    else None
                ),
            ),
        )

    def _headline(self, outcome: TurnOutcome, spec: TypedInvestigationSpec) -> _Headline | None:
        """The tile's number: the first finding's value for the watched metric.

        Read off the FINDING rather than the frame, so a tile shows exactly
        what the answer published — including the ``≤`` a suppressed
        numerator earned it (:func:`bound_text`'s rule, applied here through
        the finding's own ``__is_bound`` value rather than re-derived).
        """
        pack = self._components.pack_port
        for finding in outcome.findings:
            candidates = [ref.id for ref in finding.metric_refs]
            preferred = [m for m in spec.metric_ids if m in candidates] + candidates
            for metric_id in preferred:
                value = _named_value(finding, metric_id)
                if value is None:
                    continue
                contract = pack.metric(metric_id)
                unit = getattr(contract, "unit", None)
                unit_str = None if unit is None else str(unit)
                bounded = _named_value(finding, f"{metric_id}__is_bound") is not None
                text = format_value(value, unit_str)
                return _Headline(
                    referent=finding.referent.value,
                    title=finding.title,
                    statement=finding.statement,
                    metric_id=metric_id,
                    value=value,
                    unit=unit_str,
                    text=f"≤ {text}" if bounded else text,
                    is_bound=bounded,
                )
        return None

    def _delta(
        self,
        pin: RoundsPin,
        headline: _Headline | None,
        prior: RoundsTilePayload | None,
        window: tuple[date, date] | None = None,
    ) -> RoundsDeltaPayload | None:
        """Movement since the PRIOR load, gated by the governed materiality
        content and by this watch's own threshold."""
        if prior is None:
            return None
        prior_value = _decimal(prior.value)
        current = headline.value if headline is not None else None
        unit = headline.unit if headline is not None else prior.unit
        reason = _not_comparable_reason(prior, headline)
        verdict = (
            assess_movement(
                unit=unit,
                prior=prior_value,
                current=current,
                policy=self.policy.materiality,
                watch=pin.watch,
            )
            if reason is None
            else MaterialityVerdict(False, "not_comparable", reason)
        )
        return _delta_payload(
            prior_watermark_id=prior.watermark_id,
            prior_value=prior_value,
            current=current,
            unit=unit,
            verdict=verdict,
            comparable=reason is None,
            not_comparable_reason=reason,
            reference="prior_load",
            # Did the window actually move? A relative window usually does
            # and sometimes does not (two nightly loads inside one month),
            # and the difference decides whether this delta is a movement or
            # data settling.
            same_window=(
                window is not None
                and prior.window_start is not None
                and (prior.window_start, prior.window_end) == window
            ),
        )

    def _baseline_delta(
        self, pin: RoundsPin, tile: RoundsTilePayload
    ) -> RoundsDeltaPayload | None:
        """Movement since the watch's CREATION-LOAD baseline.

        Published only when it says something the prior-load delta does not:
        a tile that has drifted four points since it was created while
        moving 0.2 overnight is telling two true stories, and a surface
        showing only the overnight one would hide the reason the watch
        exists. When the baseline IS the load being evaluated there is
        nothing to say, and nothing is published.
        """
        if pin.baseline_value is None or tile.value is None:
            return None
        if pin.baseline_watermark_id == tile.watermark_id:
            return None
        if pin.baseline_unit != tile.unit:
            was = pin.baseline_unit or "an unknown unit"
            now = tile.unit or "an unknown unit"
            return _delta_payload(
                prior_watermark_id=pin.baseline_watermark_id or "",
                prior_value=pin.baseline_value,
                current=_decimal(tile.value),
                unit=tile.unit,
                verdict=MaterialityVerdict(
                    False,
                    "not_comparable",
                    f"this watch's baseline was measured in {was} and it now measures {now}, "
                    "so the two are not two measurements of one thing",
                ),
                comparable=False,
                not_comparable_reason="the metric's declared unit changed since the baseline "
                "was captured",
                reference="baseline",
            )
        verdict = assess_movement(
            unit=tile.unit,
            prior=pin.baseline_value,
            current=_decimal(tile.value),
            policy=self.policy.materiality,
            watch=pin.watch,
        )
        return _delta_payload(
            prior_watermark_id=pin.baseline_watermark_id or "",
            prior_value=pin.baseline_value,
            current=_decimal(tile.value),
            unit=tile.unit,
            verdict=verdict,
            comparable=True,
            not_comparable_reason=None,
            reference="baseline",
        )

    # ------------------------------------------------------------ the surface

    async def rounds(self, principal: Principal) -> RoundsResponse:
        """Every active watch, evaluated at the newest load."""
        return await self.rounds_at(
            principal, await self._components.open_session.newest_watermark()
        )

    async def rounds_at(
        self, principal: Principal, watermark: DataWatermark
    ) -> RoundsResponse:
        """The surface AT a named load.

        The explicit-watermark form exists because a load-over-load product
        has to be testable across loads: the simulated-load suite drives
        wm_001 → wm_002 → wm_003 through this, which is the same code the
        newest-load route runs. A seam that tests exercise and production
        does not is a seam that proves nothing.
        """
        await self.evaluate_load(principal.tenant, watermark)
        pins = await self._components.rounds_pins.list_for_tenant(principal.tenant)
        tiles: list[RoundsTilePayload] = []
        for pin in pins:
            stored = await self._components.rounds_results.get(pin.id, watermark.id)
            if stored is not None:
                tiles.append(RoundsTilePayload.model_validate(stored.payload))
        prior = await self._prior_load(principal.tenant, watermark)
        warnings = _rounds_warnings(self.policy)
        return RoundsResponse(
            tenant=principal.tenant,
            watermark_id=watermark.id,
            newest_data_date=watermark.newest_data_date,
            prior_watermark_id=prior.watermark_id if prior is not None else None,
            tiles=tiles,
            warnings=warnings,
            warnings_v2=structured_warnings(warnings),
        )

    async def _prior_load(
        self, tenant: str, watermark: DataWatermark
    ) -> RoundsLoad | None:
        for load in await self._components.rounds_loads.list_for_tenant(tenant, limit=12):
            if _utc(load.watermark_loaded_at) < _utc(watermark.loaded_at):
                return load
        return None

    # ------------------------------------------------------------- the brief

    async def brief(
        self, principal: Principal, *, since: str | None = None
    ) -> RoundsBriefResponse:
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
    ) -> RoundsBriefResponse:
        """The brief FOR a named load. See :meth:`rounds_at` for why this
        seam exists: the simulated-load suite drives every watermark
        transition through the same code the newest-load route runs."""
        tenant = principal.tenant
        load = await self.evaluate_load(tenant, watermark)
        prior = await self._named_prior_load(tenant, watermark, since)

        pins = {
            pin.id: pin
            for pin in await self._components.rounds_pins.list_for_tenant(
                tenant, include_archived=True
            )
        }
        current_leads = _leads_of(load)
        prior_leads = _leads_of(prior) if prior is not None else {}

        entries: list[RoundsBriefEntry] = []
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
                    for lead in (await self._components.rounds_leads.list_for_tenant(tenant))
                    if lead.status in ("resolved_claimed", "resolved_confirmed", "regressed")
                ),
            )
            entries.extend(new_entries)
            entries.extend(resolved_entries)
        movement_entries, movement_skipped, below_gate = await self._movement_entries(
            tenant, watermark, pins
        )
        entries.extend(movement_entries)
        entries.extend(self._verification_entries(load, watermark))

        total = len(entries)
        published = _cap(entries, self.policy)
        immaterial = RoundsImmaterialSummary(
            pin_movements=movement_skipped,
            new_leads=new_lead_skipped,
            self_resolved=self_resolved_skipped,
            entries_withheld_by_cap=total - len(published),
        )
        immaterial = immaterial.model_copy(update={"note": _immaterial_note(immaterial)})
        status = (
            "first_load"
            if prior is None
            else ("material_changes" if published else "nothing_material")
        )
        fatigue = await self._fatigue(tenant, watermark, below_gate)
        warnings = _rounds_warnings(self.policy)
        return RoundsBriefResponse(
            tenant=tenant,
            status=status,  # type: ignore[arg-type]
            watermark_id=watermark.id,
            newest_data_date=watermark.newest_data_date,
            prior_watermark_id=prior.watermark_id if prior is not None else None,
            headline=_headline_sentence(
                status=status,
                watermark_id=watermark.id,
                prior_watermark_id=prior.watermark_id if prior is not None else None,
                entries=published,
                immaterial=immaterial,
                pins_evaluated=len(pins),
                leads=len(current_leads),
            ),
            entries=published,
            entries_total=total,
            immaterial=immaterial,
            fatigue=fatigue,
            materiality=self.policy.payload(),
            pins_evaluated=sum(1 for pin in pins.values() if pin.archived_at is None),
            leads_verified=int(load.payload.get("leads_verified", 0) or 0),
            generated_at=datetime.now(UTC),
            warnings=warnings,
            warnings_v2=structured_warnings(warnings),
        )

    async def _named_prior_load(
        self, tenant: str, watermark: DataWatermark, since: str | None
    ) -> RoundsLoad | None:
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
                f"`since={since}` names the load this brief is FOR, so there is nothing to "
                "diff against; omit it to diff against the previous evaluated load",
                details={"since": since, "watermark_id": watermark.id},
            )
        stored = await self._components.rounds_loads.get(tenant, since)
        if stored is None:
            raise RoundsNotFoundError(
                f"no Rounds evaluation is recorded for load {since!r}, so this brief has "
                "nothing to diff against; the loads this tenant has evaluated are the only "
                "ones a brief can be taken since",
                details={"since": since, "tenant": tenant},
            )
        return stored

    def _new_lead_entries(
        self,
        load: RoundsLoad,
        prior_leads: Mapping[str, Mapping[str, Any]],
        current_leads: Mapping[str, Mapping[str, Any]],
    ) -> tuple[list[RoundsBriefEntry], int]:
        out: list[RoundsBriefEntry] = []
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
                RoundsBriefEntry(
                    kind="new_lead",
                    title=str(row.get("title", anomaly_id)),
                    statement=(
                        f"New at this load: {anomaly_id} — {row.get('title', '')} "
                        f"({magnitude(int(row.get('ranked_impact_cents', 0) or 0), 'money_cents')}"
                        f" ranked on the {row.get('ranked_on', 'detector')}'s figure). "
                        f"{verdict.note}."
                    ),
                    anomaly_id=anomaly_id,
                    category=row.get("category"),
                    lane=row.get("lane"),
                    impact_cents=int(row.get("ranked_impact_cents", 0) or 0),
                    time_to_impact=_time_to_impact_payload(row),
                    lead_status=str(row.get("lead_status", "open")),
                    provenance=RoundsProvenancePayload(
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
        load: RoundsLoad,
        prior_leads: Mapping[str, Mapping[str, Any]],
        current_leads: Mapping[str, Mapping[str, Any]],
        claimed: frozenset[str],
    ) -> tuple[list[RoundsBriefEntry], int]:
        """Leads that left the feed with nobody claiming them.

        ``claimed`` comes from the LIFECYCLE STORE, not from the prior
        load's census: the census records what the feed said at that load,
        and a claim made after it was written would be invisible to it. The
        store is the authority on whether a human said they fixed something,
        and reading the snapshot instead published one lead twice — once as
        a confirmation and once as having fixed itself.
        """
        out: list[RoundsBriefEntry] = []
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
                RoundsBriefEntry(
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
                    provenance=RoundsProvenancePayload(
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
        self, tenant: str, watermark: DataWatermark, pins: Mapping[str, RoundsPin]
    ) -> tuple[list[RoundsBriefEntry], int, int]:
        out: list[RoundsBriefEntry] = []
        skipped = 0
        below_gate = 0
        for pin in pins.values():
            if pin.archived_at is not None:
                continue
            stored = await self._components.rounds_results.get(pin.id, watermark.id)
            if stored is None:
                continue
            tile = RoundsTilePayload.model_validate(stored.payload)
            delta = tile.delta
            if delta is None:
                continue
            if delta.below_governed_gate:
                below_gate += 1
            if not delta.material:
                skipped += 1
                continue
            baseline = tile.baseline_delta if _adds_something(tile) else None
            out.append(
                RoundsBriefEntry(
                    kind="pin_movement",
                    title=pin.label,
                    statement=_movement_sentence(pin, tile, delta, baseline),
                    pin_id=pin.id,
                    investigation_id=tile.investigation_id,
                    delta=delta,
                    baseline_delta=baseline,
                    integrity=tile.integrity,
                    provenance=RoundsProvenancePayload(
                        source="pinned_spec",
                        watermark_id=tile.watermark_id,
                        prior_watermark_id=delta.prior_watermark_id or None,
                        evaluated_at=tile.evaluated_at,
                        method="this watch's stored typed spec, re-run at this load through "
                        "the ordinary governed pipeline (no model call), and diffed against "
                        "the same spec's result at the prior load",
                    ),
                )
            )
        return out, skipped, below_gate

    def _verification_entries(
        self, load: RoundsLoad, watermark: DataWatermark
    ) -> list[RoundsBriefEntry]:
        out: list[RoundsBriefEntry] = []
        for row in load.payload.get("verifications", []) or []:
            status = str(row.get("status", ""))
            if status not in ("resolved_confirmed", "regressed"):
                continue
            out.append(
                RoundsBriefEntry(
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
                    provenance=RoundsProvenancePayload(
                        source="pinned_spec",
                        watermark_id=watermark.id,
                        evaluated_at=load.evaluated_at,
                        method="the lead's own drill spec, re-derived by this platform at "
                        "each load since resolution was claimed, against the exposure "
                        "measured at the claim load",
                    ),
                )
            )
        return out

    async def _fatigue(
        self, tenant: str, watermark: DataWatermark, below_gate: int
    ) -> RoundsFatigueAdvisory:
        """The brief noticing that somebody's own thresholds are too loose.

        Counted across loads from the stored census, so the advisory fires
        on a PATTERN rather than on one noisy morning — and never more than
        once per load, because an advisory that nagged would be the fatigue
        it is warning about.
        """
        policy = self.policy.materiality.fatigue
        if not policy.enabled:
            return RoundsFatigueAdvisory()
        streak = 1 if below_gate else 0
        if streak:
            for load in await self._components.rounds_loads.list_for_tenant(tenant, limit=12):
                if _utc(load.watermark_loaded_at) >= _utc(watermark.loaded_at):
                    continue
                if int(load.payload.get("watches_below_governed_gate", 0) or 0) > 0:
                    streak += 1
                else:
                    break
        active = streak >= policy.consecutive_loads
        return RoundsFatigueAdvisory(
            active=active,
            watches_below_governed_gate=below_gate,
            consecutive_loads=streak,
            loads_required=policy.consecutive_loads,
            message=(
                policy.message.format(count=below_gate, ordinal=_ordinal(streak))
                if active
                else ""
            ),
        )

    # ------------------------------------------------------ lead lifecycle

    async def lead_states(self, tenant: str) -> dict[str, RoundsLead]:
        """Every lead status this tenant holds, for decorating cards."""
        return {
            lead.anomaly_id: lead
            for lead in await self._components.rounds_leads.list_for_tenant(tenant)
        }

    async def patch_lead(
        self,
        principal: Principal,
        anomaly_id: str,
        request: RoundsLeadPatchRequest,
        *,
        watermark: DataWatermark | None = None,
    ) -> RoundsLeadPayload:
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
            raise RoundsNotFoundError(
                f"{anomaly_id!r} is not in the detection feed at watermark {watermark.id}; a "
                "lead that is not detected cannot have its status changed",
                details={"anomaly_id": anomaly_id, "watermark_id": watermark.id},
            )
        existing = await self._components.rounds_leads.get(tenant, anomaly_id)
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

        lead = RoundsLead(
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
        await self._components.rounds_leads.put(lead)
        logger.info(
            "rounds lead %s for tenant %s: %s -> %s", anomaly_id, tenant, previous, request.status
        )
        return lead_payload(lead, self.policy.resolution.consecutive_loads_required)

    async def get_lead(self, principal: Principal, anomaly_id: str) -> RoundsLeadPayload:
        lead = await self._components.rounds_leads.get(principal.tenant, anomaly_id)
        if lead is None:
            raise RoundsNotFoundError(
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
        """
        resolution = self.policy.resolution
        cards = {card.anomaly_id: card for card in portfolio.items}
        verifications: list[dict[str, Any]] = []
        for lead in await self._components.rounds_leads.list_for_tenant(tenant):
            if lead.status not in ("resolved_claimed", "regressed"):
                continue
            if lead.claimed_at_watermark == watermark.id:
                continue  # the claim load is not evidence for its own claim
            if watermark.id in lead.confirming_watermarks:
                continue  # already counted
            outcome = await self._verify_one(
                lead, cards.get(lead.anomaly_id), watermark, resolution
            )
            await self._components.rounds_leads.put(outcome.lead)
            if outcome.entry is not None:
                verifications.append(outcome.entry)
        return verifications

    async def _verify_one(
        self,
        lead: RoundsLead,
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
            return self._advance(lead, confirming, required, note, span, watermark)

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
            return self._advance(lead, confirming, required, note, span, watermark)
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
        lead: RoundsLead,
        confirming: tuple[str, ...],
        required: int,
        note: str,
        span: str,
        watermark: DataWatermark,
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

    # ------------------------------------------------------------- the census

    async def _census(
        self,
        tenant: str,
        watermark: DataWatermark,
        portfolio: PortfolioResponse,
        pins: Sequence[RoundsPin],
        verifications: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """What the feed said, and what the watches did, at this load.

        Stored so the NEXT load can diff against it. Everything a brief
        needs about a prior load is here, because re-reading a warehouse
        snapshot to answer a question already answered is how a proactive
        surface becomes expensive enough to switch off.
        """
        leads = await self.lead_states(tenant)
        below_gate = 0
        for pin in pins:
            stored = await self._components.rounds_results.get(pin.id, watermark.id)
            if stored is None:
                continue
            delta = (stored.payload.get("delta") or {}) if isinstance(stored.payload, dict) else {}
            if delta.get("below_governed_gate"):
                below_gate += 1
        return {
            "leads": {
                card.anomaly_id: {
                    "title": card.title,
                    "category": card.category,
                    "lane": card.lane,
                    "impact_cents": card.impact_cents,
                    "ranked_impact_cents": card.ranked_impact_cents,
                    "ranked_on": card.ranked_on,
                    "lead_status": (
                        leads[card.anomaly_id].status if card.anomaly_id in leads else "open"
                    ),
                    "watermark_id": watermark.id,
                    "time_to_impact": (
                        card.time_to_impact.model_dump(mode="json")
                        if card.time_to_impact is not None
                        else None
                    ),
                }
                for card in portfolio.items
            },
            "verifications": [dict(entry) for entry in verifications],
            "leads_verified": len(verifications),
            "watches_below_governed_gate": below_gate,
            "pins_evaluated": len(pins),
        }

    # ------------------------------------------------ lead decoration for cards

    async def decorate_cards(self, tenant: str, portfolio: PortfolioResponse) -> PortfolioResponse:
        """Ride lead statuses onto the portfolio's cards (additive fields).

        The rail renders the lifecycle from the same payload it already
        fetches, and a card and a brief entry cannot disagree about whether
        somebody is working a lead.
        """
        leads = await self.lead_states(tenant)
        if not leads:
            return portfolio
        items = []
        for card in portfolio.items:
            lead = leads.get(card.anomaly_id)
            if lead is None:
                items.append(card)
                continue
            items.append(
                card.model_copy(
                    update={
                        "lead_status": lead.status,
                        "lead_status_note": lead.verification_note or lead.note,
                        "lead_updated_at": lead.updated_at,
                    }
                )
            )
        return portfolio.model_copy(update={"items": items})


# ---------------------------------------------------------------------------
# small value objects and helpers


@dataclass(frozen=True, slots=True)
class _Headline:
    """The tile's number, with everything needed to render it honestly."""

    referent: str
    title: str
    statement: str
    metric_id: str
    value: Decimal
    unit: str | None
    text: str
    is_bound: bool


@dataclass(frozen=True, slots=True)
class _Verification:
    """One lead's verification outcome at one load: the updated lead, and
    the brief entry it earned (``None`` when it earned none — an
    unconfirmed claim is a state change, not news)."""

    lead: RoundsLead
    entry: dict[str, Any] | None


def _named_value(finding: Finding, name: str) -> Decimal | None:
    for key, value in finding.values:
        if key != name:
            continue
        if value is None or isinstance(value, bool):
            return Decimal(1) if value is True else None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    return None


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _utc(value: datetime) -> datetime:
    """Interpret a naive datetime as UTC before comparing it to anything.

    The two sides of "is this load older than that one?" genuinely arrive
    with different awareness: the DuckDB connector reads a warehouse
    ``TIMESTAMP`` and hands back a NAIVE ``loaded_at``, while a stored
    ``timestamptz`` comes back AWARE. Comparing them raises, so the whole
    surface fell over the first time it ran against a real database — and
    not once against the in-memory store, which round-trips whatever it was
    given.

    Normalised here, at the comparison, on the same convention the Postgres
    adapters already use for their typed columns (a naive value is UTC).
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _not_comparable_reason(
    prior: RoundsTilePayload, headline: _Headline | None
) -> str | None:
    """Are these two loads two measurements of one thing?

    A percentage between mismatched sides is worse than no percentage: it
    looks exactly like a movement.
    """
    if headline is None:
        return (
            "this load produced no value for the watched metric, so there is nothing to "
            "compare against the prior load"
        )
    if prior.value is None:
        return (
            f"the prior load ({prior.watermark_id}) produced no value for this watch, so no "
            "movement can be claimed"
        )
    if prior.unit != headline.unit:
        return (
            f"the prior load measured this watch in {prior.unit or 'an unknown unit'} and this "
            f"one measures it in {headline.unit or 'an unknown unit'}, so the two are not two "
            "measurements of one thing"
        )
    if prior.metric_id and prior.metric_id != headline.metric_id:
        return (
            f"the prior load's headline came from {prior.metric_id!r} and this one's from "
            f"{headline.metric_id!r}, so a delta between them would be a comparison of two "
            "different contracts"
        )
    return None


def _delta_payload(
    *,
    prior_watermark_id: str,
    prior_value: Decimal | None,
    current: Decimal | None,
    unit: str | None,
    verdict: MaterialityVerdict,
    comparable: bool,
    not_comparable_reason: str | None,
    reference: str,
    same_window: bool = False,
) -> RoundsDeltaPayload:
    delta = (
        current - prior_value if current is not None and prior_value is not None else None
    )
    # A rate's movement is percentage POINTS and nothing else. A relative
    # fraction beside a rate delta is the ambiguity the platform already
    # refuses everywhere else ("up 3.2%" — of what?).
    fraction: float | None = None
    if delta is not None and prior_value not in (None, 0) and unit != "ratio":
        assert prior_value is not None
        fraction = round(float(delta / abs(prior_value)), 6)
    direction = "unknown"
    if delta is not None:
        direction = "flat" if delta == 0 else ("up" if delta > 0 else "down")
    return RoundsDeltaPayload(
        prior_watermark_id=prior_watermark_id,
        prior_value=float(prior_value) if prior_value is not None else None,
        prior_value_text=format_value(prior_value, unit) if prior_value is not None else "",
        value=float(current) if current is not None else None,
        value_text=format_value(current, unit) if current is not None else "",
        unit=unit,
        delta=float(delta) if delta is not None else None,
        delta_text=magnitude(delta, unit) if delta is not None else "",
        delta_fraction=fraction,
        direction=direction,  # type: ignore[arg-type]
        comparable=comparable,
        not_comparable_reason=not_comparable_reason,
        reference=reference,  # type: ignore[arg-type]
        same_window=same_window,
        material=verdict.material,
        threshold_source=verdict.threshold_source,  # type: ignore[arg-type]
        below_governed_gate=verdict.below_governed_gate,
        materiality_rule=verdict.rule,
        materiality_note=verdict.note,
    )


def _adds_something(tile: RoundsTilePayload) -> bool:
    """Does the baseline delta say anything the prior-load delta does not?

    Published only when it does. Two numbers that tell the same story are
    one number and a distraction.
    """
    baseline = tile.baseline_delta
    if baseline is None or not baseline.comparable:
        return False
    if tile.delta is None:
        return True
    return baseline.material and baseline.prior_watermark_id != tile.delta.prior_watermark_id


def _movement_sentence(
    pin: RoundsPin,
    tile: RoundsTilePayload,
    delta: RoundsDeltaPayload,
    baseline: RoundsDeltaPayload | None,
) -> str:
    parts = [
        f"{pin.label}: {delta.value_text} at {tile.watermark_id}, "
        f"{delta.direction} {delta.delta_text} from {delta.prior_value_text} at "
        f"{delta.prior_watermark_id}."
    ]
    if delta.threshold_source == "watch":
        parts.append(
            "Briefed on this watch's own threshold"
            + (
                " — which is looser than the governed gate for this unit, so the movement is "
                "inside what the pack calls normal variation."
                if delta.below_governed_gate
                else f": {delta.materiality_note}."
            )
        )
    else:
        parts.append(f"{delta.materiality_note[:1].upper()}{delta.materiality_note[1:]}.")
    if baseline is not None:
        parts.append(
            f"Since you started watching it at {baseline.prior_watermark_id} it is "
            f"{baseline.direction} {baseline.delta_text} from {baseline.prior_value_text}."
        )
    if delta.same_window and tile.window_start is not None and tile.window_end is not None:
        # Said from the DATES the two loads measured, not from the pin's
        # declared window mode: a relative window usually moves and
        # sometimes does not, and only the resolved dates know which.
        parts.append(
            SAME_WINDOW_NOTE.format(start=tile.window_start, end=tile.window_end)
        )
    if tile.integrity.is_bound:
        parts.append(
            "The value is an upper bound: a suppressed numerator was replaced by the largest "
            "value it could have held."
        )
    if tile.integrity.provisional:
        parts.append("The value is provisional — the window is still adjudicating.")
    return " ".join(parts)


def _cap(entries: list[RoundsBriefEntry], policy: RoundsPolicy) -> list[RoundsBriefEntry]:
    """Cap the brief: per kind first, then overall.

    Per kind first so one noisy category cannot fill the brief and push
    every other kind of change off the end of it — which is precisely how a
    daily surface trains somebody to stop reading it.
    """
    per_kind = policy.materiality.max_entries_per_kind
    seen: dict[str, int] = {}
    kept: list[RoundsBriefEntry] = []
    for entry in entries:
        count = seen.get(entry.kind, 0)
        if per_kind and count >= per_kind:
            continue
        seen[entry.kind] = count + 1
        kept.append(entry)
    return kept[: policy.materiality.max_entries] if policy.materiality.max_entries else kept


def _immaterial_note(immaterial: RoundsImmaterialSummary) -> str:
    """What the gate held back, in one sentence.

    Counted rather than hidden: suppressing a movement silently and
    suppressing it visibly are different products, and the first is a
    filter the analyst cannot audit.
    """
    bits: list[str] = []
    if immaterial.pin_movements:
        bits.append(
            f"{immaterial.pin_movements} watched item(s) moved by less than the threshold for "
            "their unit"
        )
    if immaterial.new_leads:
        bits.append(f"{immaterial.new_leads} newly detected lead(s) fell below the brief floor")
    if immaterial.self_resolved:
        bits.append(
            f"{immaterial.self_resolved} lead(s) left the detection feed below the brief floor"
        )
    if immaterial.entries_withheld_by_cap:
        bits.append(
            f"{immaterial.entries_withheld_by_cap} further entry/entries were held back by the "
            "brief's own cap"
        )
    if not bits:
        return "Nothing was held back."
    return "Held back and counted rather than hidden: " + "; ".join(bits) + "."


def _headline_sentence(
    *,
    status: str,
    watermark_id: str,
    prior_watermark_id: str | None,
    entries: Sequence[RoundsBriefEntry],
    immaterial: RoundsImmaterialSummary,
    pins_evaluated: int,
    leads: int,
) -> str:
    if status == "first_load":
        return (
            f"This is the first load Revi has walked your Rounds on ({watermark_id}). "
            f"{pins_evaluated} watch(es) and {leads} detected lead(s) are now the baseline; "
            "from the next load on, this brief says what changed."
        )
    if status == "nothing_material":
        # The proud shape. This is an ANSWER, not an empty page: it names
        # what was measured and what was found to be within tolerance.
        return (
            f"Nothing material changed between {prior_watermark_id} and {watermark_id}. "
            f"Revi re-ran {pins_evaluated} watch(es) against the new load and diffed "
            f"{leads} detected lead(s). {immaterial.note}"
        )
    kinds: dict[str, int] = {}
    for entry in entries:
        kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
    described = ", ".join(
        f"{count} {kind.replace('_', ' ')}" for kind, count in sorted(kinds.items())
    )
    return (
        f"{len(entries)} thing(s) changed between {prior_watermark_id} and {watermark_id}: "
        f"{described}. {immaterial.note}"
    )


def _rounds_warnings(policy: RoundsPolicy) -> list[str]:
    if policy.enabled:
        return []
    return [
        "population_caveat: this deployment's pack ships no governed Rounds content, so no "
        "materiality gate was applied and no time-to-impact was derived — every movement is "
        "reported as measured, and nothing here has been judged material"
    ]


def _leads_of(load: RoundsLoad | None) -> dict[str, Mapping[str, Any]]:
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


def _watch_from_model(model: RoundsWatchModel | None) -> RoundsWatch | None:
    if model is None:
        return None
    return RoundsWatch(
        mode=model.mode,
        value=None if model.value is None else Decimal(str(model.value)),
        unit=model.unit,
        direction=model.direction,
        note=model.note,
    )


def _watch_model(watch: RoundsWatch | None) -> RoundsWatchModel:
    if watch is None:
        return RoundsWatchModel()
    return RoundsWatchModel(
        mode=watch.mode,  # type: ignore[arg-type]
        value=None if watch.value is None else float(watch.value),
        unit=watch.unit,  # type: ignore[arg-type]
        direction=watch.direction,  # type: ignore[arg-type]
        note=watch.note,
    )


def _threshold_statement(watch: RoundsWatch | None, unit: str | None) -> str:
    """The gate in words, for the confirmation sentence."""
    if watch is None or watch.mode == "governed_default":
        return "when it moves more than the governed threshold for this measure"
    if watch.mode == "any_movement":
        return "on any movement at all"
    if watch.mode == "crosses":
        return f"when it crosses {format_threshold(watch, unit)}"
    return f"when it moves {format_threshold(watch, unit)} or more"


def _watch_confirmation(label: str, value_text: str, threshold_statement: str) -> str:
    """The one-time baseline confirmation, composed from the answer.

    Every clause is a fact the payload also carries: what is watched, what
    it reads right now, and what will bring it back. Never composed by a
    model — a generated sentence could not be validated against the answer
    beside it any more cheaply than writing it from that answer.
    """
    current = f" — currently {value_text}" if value_text else ""
    return (
        f"Watching: {label}{current}. I'll brief you {threshold_statement}, and the answer "
        "above is the baseline I'll measure that from."
    )


def pin_payload(pin: RoundsPin, *, notes: Sequence[str] = ()) -> RoundsPinPayload:
    window_note = _WINDOW_NOTES.get(pin.window_mode, "")
    if notes:
        window_note = " ".join([window_note, *notes]).strip()
    return RoundsPinPayload(
        pin_id=pin.id,
        tenant=pin.tenant,
        label=pin.label,
        presentation=pin.presentation,  # type: ignore[arg-type]
        spec=pin.spec,
        window_mode=pin.window_mode,  # type: ignore[arg-type]
        window_note=window_note,
        created_from_kind=pin.created_from_kind,  # type: ignore[arg-type]
        created_from_investigation_id=pin.created_from_investigation_id,
        created_from_referent=pin.created_from_referent,
        watch=_watch_model(pin.watch) if pin.watch is not None else None,
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


def lead_payload(lead: RoundsLead, confirmations_required: int) -> RoundsLeadPayload:
    return RoundsLeadPayload(
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


def annotate_time_to_impact(
    portfolio: PortfolioResponse,
    records: Mapping[str, AnomalyRecord],
    *,
    newest_data_date: date,
    policy: RoundsPolicy,
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
    return portfolio.model_copy(update={"items": items})


__all__ = [
    "RoundsNotFoundError",
    "RoundsService",
    "annotate_time_to_impact",
    "lead_payload",
    "pin_payload",
    "typed_spec_from_analysis",
]
