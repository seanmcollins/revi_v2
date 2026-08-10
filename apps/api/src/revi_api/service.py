"""Transport-neutral ``InvestigationApi`` implementation.

The FastAPI routes and the in-process client both delegate here — one
contract, two transports. Turn errors normalize to the ``TurnError``
variant with a stable kernel-code :class:`ErrorEnvelope` on BOTH
transports; an idempotency key returns the stored response without
re-executing anything.

**Tenant scoping lives here, not in middleware.** Every method takes the
caller's :class:`~revi_api.auth.Principal`, and every ``{session_id}`` /
``{investigation_id}`` lookup resolves the owning session and compares its
tenant before returning a byte. That placement is the point: an
authorization check in HTTP middleware protects the HTTP transport and
leaves ``InProcessInvestigationClient`` — the same API, a different
door — wide open. The rule belongs to the service, so both doors get it.

The tenant a turn executes under comes from the principal, never from the
request body.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter

from revi_api.assembly import (
    NARRATIVE_TRACE_SUFFIX,
    OnEvent,
    assemble_turn_response,
    investigation_response,
    restored_chart_specs,
)
from revi_api.auth import AuthorizationError, Principal
from revi_api.cohort_payload import cohort_id_from_trace, cohort_payload_for
from revi_api.debug_trace import build_debug_trace
from revi_api.error_copy import budget_subcode, plain_message
from revi_api.monitor_intent import (
    MonitorDeclaration,
    legal_threshold_phrases,
    parse_monitor_declaration,
)
from revi_api.monitors import MonitorsService, annotate_time_to_impact
from revi_api.portfolio import (
    SNAPSHOT_NOT_COMPARABLE,
    build_portfolio,
    dimension_repointed_warning,
    dimension_repoints_for,
    drill_spec_for,
    is_active,
    reconciliation_note,
)
from revi_api.rederive import (
    ReDerivedImpact,
    compare_impact,
    money_total,
    non_money_reason,
)
from revi_api.settings_policy import DEBUG_TRACE_ENV
from revi_api.usage_ledger import bind_ledger, unbind_ledger
from revi_api.warning_codes import structured_warnings
from revi_api.wiring import ApiComponents
from revi_api.worklist import (
    WorklistReference,
    build_worklist,
    resolve_worklist_reference,
    worklist_reference_warning,
)
from revi_investigation.application.dto_mapping import refinement_to_dto
from revi_investigation.application.ports import AnomalyRecord, Monitor, TraceRecord
from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.records import Investigation, Session
from revi_investigation.domain.settings import SessionSettings
from revi_investigation_contracts.api import (
    AnomalyCard,
    AnomalyReconciliationPayload,
    CapabilitiesResponse,
    DebugTracePayload,
    ErrorEnvelope,
    InvestigationResponse,
    LineageEdgePayload,
    MonitorRefusedPayload,
    OpenSessionRequest,
    PortfolioResponse,
    SessionLineageResponse,
    SessionListResponse,
    SessionResponse,
    SessionSummary,
    TurnAnswer,
    TurnClarification,
    TurnError,
    TurnRequest,
    TurnResponse,
    WorklistPayload,
    WorklistQuery,
)
from revi_investigation_contracts.settings import SessionSettingsModel
from revi_kernel.errors import ErrorCode, PolicyDeniedError, ReviError
from revi_kernel.watermark import DataWatermark

logger = logging.getLogger("revi.api.service")

TurnResult = TurnAnswer | TurnClarification | TurnError

#: Rebuilds a stored idempotency receipt into its typed outcome. The stored
#: value is the serialized response, so a replay returns what the first
#: execution published rather than a second run of the same turn.
_TURN_RESULT_ADAPTER: TypeAdapter[TurnResult] = TypeAdapter(TurnResponse)

#: Suffix of the supplementary record a turn writes to remember which page
#: of the ranked worklist it published, so a later "open the top item"
#: resolves against the rows the analyst was actually shown.
WORKLIST_TRACE_SUFFIX = ":worklist"

#: Suffix of the supplementary record a turn writes when a monitor DECLARATION
#: ended in a clarification, so the declaration survives the question it
#: triggered.
#:
#: The engine returns early on a clarification reply and carries nothing
#: across the boundary, so without this record a declaration that clarified
#: registered no monitor and said nothing about it. In a pack that refuses
#: imprecise payer names by design, clarification is the modal branch of the
#: declaration path, not an edge case.
#:
#: Written as a separate record rather than folded into the decision trace:
#: the engine finishes its own trace before the API knows any of this, and
#: rewriting somebody else's finished record is how two writers come to
#: disagree about one row.
MONITOR_TRACE_SUFFIX = ":monitor"

#: Suffix of the record carrying what the API added to the published answer
#: AFTER the engine saved its own.
#:
#: The engine stores the investigation with the warnings IT produced; the
#: named-cut disclosure a monitor declaration earns and the refusal that says
#: nothing is being monitored are appended by this module afterwards. Without
#: this write, a restored or permalinked turn lost exactly the warnings whose
#: whole purpose is to survive being read later.
#:
#: The SENTENCES are merged back onto the investigation record itself, so
#: every restore path gets them with no extra read. This record carries the
#: structured payload beside them — a refusal is a shape, not a sentence.
API_TRACE_SUFFIX = ":api"

#: Every supplementary record's suffix. The decision trace is the one with
#: none of them, and this is the single list that decides it — the same rule
#: spread across call sites breaks the next time a writer is added.
_SUPPLEMENTARY_SUFFIXES = (
    NARRATIVE_TRACE_SUFFIX,
    WORKLIST_TRACE_SUFFIX,
    MONITOR_TRACE_SUFFIX,
    API_TRACE_SUFFIX,
)

#: How far back a worklist reference looks for the list it names. Three
#: turns: the list, a question about it, and a follow-up to that. Beyond
#: that "the top item" is a memory rather than a reference, and answering it
#: from a list four turns gone would point at rows nobody is looking at.
_WORKLIST_CONTEXT_DEPTH = 3

#: Page size for ``GET /v1/sessions`` when the caller names none.
DEFAULT_SESSION_LIST_LIMIT = 50
#: Hard cap on that page. A list route with an unbounded page size is a
#: denial-of-service handle a client can pull by accident.
MAX_SESSION_LIST_LIMIT = 200


def _cohort_id_of(investigation: Investigation) -> str | None:
    """The cohort a stored turn was computed over, from its own spec.

    The spec's context is where an INHERITED cohort lives — a turn two
    steps after the drill that pinned it carries the population without
    having pinned anything, and the trace's ``refinement.cohort`` block
    (written only by the pinning turn) would report nothing for it.
    """
    context = getattr(getattr(investigation, "spec", None), "context", None)
    cohort = getattr(context, "cohort", None)
    return getattr(cohort, "id", None)


def _lane_of(worklist: WorklistPayload) -> str | None:
    """The lane this page was filtered to, read back off the cards.

    Taken from the rows rather than from the request: a lane the caller
    asked for and the builder rejected is not the lane that was shown.
    """
    lanes = {card.lane for card in worklist.items}
    return next(iter(lanes)) if len(lanes) == 1 and worklist.total_items else None


def _playbook_of(trace: TraceRecord | None) -> str | None:
    """The governed playbook this turn planned from, off its own trace."""
    if trace is None:
        return None
    raw = (trace.payload.get("plan_context") or {}).get("playbook_id")
    return raw if isinstance(raw, str) else None


def _resolve_monitor_turn(request: TurnRequest) -> tuple[TurnRequest, MonitorDeclaration | None]:
    """Strip a monitor lead-in and run the remainder as an ordinary turn.

    "Monitor Silverline's denial rate" is one instruction and one question.
    The instruction is read here — from a closed lead-in vocabulary, see
    :mod:`revi_api.monitor_intent` — and the question is handed to the
    ordinary pipeline unchanged, so a declaration earns the same
    interpretation, the same §6.6 validation and the same clarification a
    bare question would.

    Left alone on every complete request (a typed spec, typed refinements,
    a clarification reply): none of those is language, and none of them is
    a place a lead-in could appear.
    """
    if (
        not request.utterance
        or request.spec is not None
        or request.refinements is not None
        or request.clarification_response
    ):
        return request, None
    declaration = parse_monitor_declaration(request.utterance)
    if declaration is None:
        return request, None
    return request.model_copy(update={"utterance": declaration.subject}), declaration


def _declaration_payload(declaration: MonitorDeclaration) -> dict[str, Any]:
    """The parsed declaration, flattened for the supplementary record."""
    monitor = declaration.monitor
    return {
        "matched_phrase": declaration.matched_phrase,
        "subject": declaration.subject,
        "threshold_phrase": declaration.threshold_phrase,
        "threshold_unreadable": declaration.threshold_unreadable,
        "monitor": None
        if monitor is None
        else {
            "mode": monitor.mode,
            "value": None if monitor.value is None else str(monitor.value),
            "unit": monitor.unit,
            "direction": monitor.direction,
            "note": monitor.note,
        },
    }


def _declaration_from_payload(raw: Mapping[str, Any]) -> MonitorDeclaration | None:
    """The declaration a clarification interrupted, read back."""
    subject = str(raw.get("subject") or "")
    if not subject:
        return None
    monitor_raw = raw.get("monitor")
    monitor = None
    if isinstance(monitor_raw, dict):
        value = monitor_raw.get("value")
        monitor = Monitor(
            mode=str(monitor_raw.get("mode", "governed_default")),
            value=None if value is None else Decimal(str(value)),
            unit=monitor_raw.get("unit"),
            direction=str(monitor_raw.get("direction", "any")),
            note=str(monitor_raw.get("note", "")),
        )
    return MonitorDeclaration(
        matched_phrase=str(raw.get("matched_phrase") or ""),
        subject=subject,
        monitor=monitor,
        threshold_phrase=str(raw.get("threshold_phrase") or ""),
        threshold_unreadable=bool(raw.get("threshold_unreadable")),
    )


#: What the platform says while a monitor declaration is waiting on a
#: clarification. Silence is the one unacceptable option here: the analyst
#: has said "monitor this", and an ordinary-looking question with no mention
#: of the monitor is how somebody walks away believing they are being monitored.
MONITOR_PENDING_WARNING = (
    "monitor_pending_clarification: this turn read as a monitor declaration ({phrase!r}), and the "
    "question above has to be answered before it can be. NOTHING is being monitored yet — "
    "answer it and Revi answers {subject!r} once and starts monitoring that."
)


def monitor_declaration_warning(declaration: MonitorDeclaration) -> str:
    """What the platform read, said on the answer that acted on it.

    The analyst's own words are not thrown away by the rewrite: this states
    the lead-in that was matched and the question it was reduced to, so a
    reader can see the platform's reading rather than infer it from a monitor
    appearing.
    """
    threshold = (
        f" Sensitivity read from {declaration.threshold_phrase.strip()!r}."
        if declaration.threshold_phrase
        else " No sensitivity was stated, so the governed threshold for the measure applies."
    )
    return (
        f"named_cut_applied: read {declaration.matched_phrase!r} as a monitor declaration and "
        f"investigated the rest of the sentence — {declaration.subject!r} — as an ordinary "
        f"question, so this monitor is defined by a spec that was planned, validated and "
        f"answered rather than by the phrasing.{threshold}"
    )


def _with_warning(outcome: TurnOutcome, warning: str) -> TurnOutcome:
    """The same outcome with one more warning on it.

    Used where the API has something to say that the engine could not know
    — a card reference it could not resolve, say. Appended rather than
    prepended: the engine's own assumptions and validation notes lead, as
    they are ordered to.
    """
    return replace(outcome, warnings=(*outcome.warnings, warning))


class NotFoundError(ReviError):
    """Resource-miss for GET routes (mapped to HTTP 404).

    ``REFERENT_NOT_FOUND`` is the §12 code for "the thing you named does
    not exist here" — the same failure whether the handle is F2 or an
    investigation id. UNSUPPORTED_CONCEPT would say something different and
    false: that the platform cannot express what was asked."""

    code = ErrorCode.REFERENT_NOT_FOUND


def settings_payload(settings: SessionSettings) -> SessionSettingsModel:
    """The engine's settings as the wire shape — the *effective* values, so
    a client sees what it got rather than what it asked for."""
    return SessionSettingsModel(
        model_tier=settings.model_tier,
        max_turn_cost_usd=(
            str(settings.max_turn_cost_usd) if settings.max_turn_cost_usd is not None else None
        ),
        narrative_depth=settings.narrative_depth,
        evidence_depth=settings.evidence_depth,
        debug=settings.debug,
    )


class TurnIdentityError(ReviError):
    """A turn envelope that does not belong to the request it answers.

    Its own code would need a wire-contract addition; ``POLICY_DENIED`` is
    the honest existing one — the platform is refusing to publish something,
    and the analyst's recovery (retry) is the same either way. What matters
    is that the turn FAILS rather than returning a stranger's identifiers,
    because every client on this platform adopts response ids: they become
    the permalink, the "Copy link" target, and the provenance of anything
    pinned from the answer.
    """

    code = ErrorCode.POLICY_DENIED


def _assert_own_turn(session_id: str, outcome: TurnOutcome) -> None:
    """The engine answered THIS request's session, or nothing goes out."""
    if outcome.session.id == session_id:
        return
    logger.error(
        "turn identity violation: POST to session %s produced an outcome for session %s "
        "(investigation %s) — refusing to publish another caller's identifiers",
        session_id,
        outcome.session.id,
        outcome.investigation.id,
    )
    raise TurnIdentityError(
        "this turn was answered against a different session than the one it was posted to, "
        "so nothing is published for it: an answer carrying another session's identifiers "
        "would become this answer's permalink and the provenance of anything pinned from "
        "it. Retry the turn",
        details={"session_id": session_id},
    )


def _own_envelope(
    session_id: str, outcome: TurnOutcome, response: TurnResult
) -> TurnResult:
    """The envelope's identity, taken from THIS request's resolved objects.

    ``session_id`` is the path parameter and ``outcome.investigation`` is the
    record this call awaited — both facts about this request that were in
    hand before the response was assembled. Anything else on the envelope is
    a read of shared state, and under concurrency that is how two turns
    posted to one session came back naming a stranger's.

    A mismatch is a defect and is logged as one; the envelope is corrected
    rather than published wrong, because the alternative is a permalink into
    somebody else's investigation.
    """
    if isinstance(response, TurnError):
        return response.model_copy(update={"session_id": session_id})
    if (
        response.session_id == session_id
        and response.investigation_id == outcome.investigation.id
    ):
        return response
    logger.error(
        "turn envelope identity mismatch on session %s: assembled session_id=%s "
        "investigation_id=%s, this request resolved session_id=%s investigation_id=%s",
        session_id,
        response.session_id,
        response.investigation_id,
        session_id,
        outcome.investigation.id,
    )
    return response.model_copy(
        update={
            "session_id": session_id,
            "investigation_id": outcome.investigation.id,
        }
    )


def _session_response(session: Session) -> SessionResponse:
    return SessionResponse(
        session_id=session.id,
        tenant=session.tenant,
        pack_id=session.pack_version.pack_id,
        pack_version=session.pack_version.version,
        watermark_id=session.watermark.id,
        watermark_loaded_at=session.watermark.loaded_at,
        newest_data_date=session.watermark.newest_data_date,
        epoch=session.epochs[-1].index,
        settings=settings_payload(session.settings),
    )


class ApiService:
    """The one implementation behind both clients and the HTTP routes."""

    def __init__(self, components: ApiComponents) -> None:
        self._components = components
        # Monitors is handed THIS service's portfolio builder rather than
        # building its own: a brief's "new lead" and the rail's card have to
        # be the same object from the same build, which is the rule the
        # conversational worklist already follows.
        self._monitors = MonitorsService(components, portfolio_for=self._portfolio_for)

    @property
    def components(self) -> ApiComponents:
        return self._components

    @property
    def monitors(self) -> MonitorsService:
        """The Monitors surface: monitors, per-load evaluation, brief, leads."""
        return self._monitors

    # ------------------------------------------------------------ authorization

    async def _authorized_session(self, principal: Principal, session_id: str) -> Session:
        """The session, or a typed refusal. Never a foreign tenant's session."""
        session = await self._components.sessions.get(session_id)
        if session is None:
            raise NotFoundError(
                f"session {session_id!r} does not exist", details={"session_id": session_id}
            )
        self._assert_tenant(principal, session, resource=f"session {session_id!r}")
        return session

    @staticmethod
    def _assert_tenant(principal: Principal, session: Session, *, resource: str) -> None:
        if session.tenant != principal.tenant:
            logger.warning(
                "cross-tenant access refused: principal tenant=%r asked for %s owned by %r",
                principal.tenant,
                resource,
                session.tenant,
            )
            raise AuthorizationError(
                f"{resource} belongs to another tenant",
                details={"resource": resource, "tenant": principal.tenant},
            )

    # ------------------------------------------------------- InvestigationApi

    async def open_session(
        self, principal: Principal, request: OpenSessionRequest
    ) -> SessionResponse:
        # The token is the authority on tenant; a body that names a
        # different one is a mistake worth reporting, not worth honoring.
        if request.tenant and request.tenant != principal.tenant:
            raise AuthorizationError(
                f"cannot open a session for tenant {request.tenant!r}: this credential is "
                f"for tenant {principal.tenant!r}",
                details={"requested": request.tenant, "principal": principal.tenant},
            )
        if request.session_id is not None:
            existing = await self._components.sessions.get(request.session_id)
            if existing is not None:
                self._assert_tenant(
                    principal, existing, resource=f"session {request.session_id!r}"
                )
        # Bounds-checked here, before anything is opened: a session that
        # exists with settings the deployment would refuse is a session
        # whose next turn fails for a reason nobody asked for.
        settings = self._components.settings_policy.resolve(request.settings)
        session = await self._components.open_session.open(
            tenant=principal.tenant,
            session_id=request.session_id,
            # None means "leave this session's settings alone" — a
            # reconnect must not silently reset the analyst's controls.
            settings=settings if request.settings is not None else None,
        )
        return _session_response(session)

    async def list_sessions(
        self, principal: Principal, *, limit: int = DEFAULT_SESSION_LIST_LIMIT
    ) -> SessionListResponse:
        """The caller tenant's sessions, newest activity first.

        Scoped by the *store call*, not by filtering rows after reading
        them: the port takes the tenant, so there is no code path here that
        could hold another tenant's session in memory long enough to leak
        it into a response. The tenant comes from the signed token like
        everywhere else — there is no query parameter for it.
        """
        bounded = min(max(limit, 1), MAX_SESSION_LIST_LIMIT)
        page = await self._components.sessions.list_for_tenant(
            principal.tenant, limit=bounded
        )
        return SessionListResponse(
            tenant=principal.tenant,
            sessions=[
                SessionSummary(
                    session_id=row.session_id,
                    title=row.title,
                    created_at=row.created_at,
                    last_activity=row.last_activity,
                    turn_count=row.turn_count,
                )
                for row in page.sessions
            ],
            total=page.total,
            limit=bounded,
        )

    async def archive_session(self, principal: Principal, session_id: str) -> None:
        """Dismiss a session from the caller tenant's list.

        Soft, and deliberately so: the session keeps its investigations,
        traces, frames and cohorts, and stays fetchable by id — a
        conversation somebody linked to does not 404 because the rail was
        tidied. Tenant-scoped like every other session operation, and
        idempotent: archiving an archived session is a no-op, and a missing
        one is a 404 rather than a silent success.
        """
        await self._authorized_session(principal, session_id)
        await self._components.sessions.archive(session_id)
        logger.info("session %s archived by tenant %s", session_id, principal.tenant)

    async def submit_turn(
        self,
        principal: Principal,
        session_id: str,
        request: TurnRequest,
        *,
        on_event: OnEvent | None = None,
    ) -> TurnResult:
        correlation_id = request.correlation_id or f"corr_{uuid.uuid4().hex[:12]}"
        existing = await self._components.sessions.get(session_id)
        if existing is not None:
            self._assert_tenant(principal, existing, resource=f"session {session_id!r}")
        if request.idempotency_key is not None:
            stored = await self._components.receipts.get(
                principal.tenant, session_id, request.idempotency_key
            )
            if stored is not None:
                # The ORIGINAL payload, re-validated into its typed shape —
                # never a re-execution. See revi_api.session_lifecycle.
                return _TURN_RESULT_ADAPTER.validate_python(stored)
        # A row of the ranked worklist, addressed by the id or the position
        # this platform printed, becomes the card's own stored drill —
        # before anything is classified or interpreted.
        request, worklist_reference = await self._resolve_worklist_turn(session_id, request)
        # "Monitor X" is an INSTRUCTION about the question that follows it, so
        # the lead-in is stripped here and the remainder runs as an ordinary
        # turn: same classification, same interpretation, same §6.6 pass.
        # The monitor is registered from the answer, not from the words.
        request, declaration = _resolve_monitor_turn(request)
        if declaration is None and request.clarification_response:
            # A monitor this session declared, whose question is being
            # answered right now. Picked up here — before the turn runs —
            # so the resolved answer registers it through exactly the same
            # path a declaration that never clarified goes through.
            declaration = await self._pending_monitor_declaration(session_id)
        default_question = (
            "(typed investigation)" if request.spec is not None else "(typed gesture)"
        )
        utterance = request.utterance or request.clarification_response or default_question
        # Every model call this turn makes is tallied here, so a turn that
        # FAILS can still say what it spent. The binding is a contextvar, so
        # concurrent turns cannot read each other's ledger.
        ledger, ledger_token = bind_ledger()
        try:
            # A per-turn override is bounds-checked exactly like a session
            # one, and applies to this turn alone (the session record is
            # untouched). Inside the try on purpose: a refused setting is a
            # turn failure, and both transports must see the same
            # ``TurnError`` envelope for it.
            turn_settings = (
                self._components.settings_policy.resolve(request.settings)
                if request.settings is not None
                else None
            )
            engine_request = SubmitTurnRequest(
                tenant=principal.tenant,  # from the signed token, never from the body
                question=utterance,
                session_id=session_id,
                spec=request.spec,
                refinements=(
                    tuple(request.refinements) if request.refinements is not None else None
                ),
                re_anchor=request.re_anchor,
                settings=turn_settings,
                # The dedicated channel, carried as the fact it is rather
                # than flattened into ``utterance``. Flattened, a verbatim
                # option sent on it came back re-classified as a bare
                # refinement at confidence 0.45, rooted, with the analyst's
                # question dropped.
                clarification_response=bool(request.clarification_response),
                # A body carrying only ``worklist`` is the lane chip the
                # platform drew, and it is a complete request.
                worklist_only=(
                    request.worklist is not None
                    and not request.utterance
                    and request.spec is None
                    and request.refinements is None
                    and not request.clarification_response
                ),
            )
            outcome = await self._components.submit.submit(engine_request)
            # THE TURN THAT CAME BACK IS THE TURN THAT WENT OUT. The session
            # is a PATH PARAMETER — this request's own resolved object, known
            # for certain before the engine was called — so an outcome
            # carrying a different one is not a fact about the analyst's
            # session, it is another caller's identity on this caller's wire.
            # Under concurrent load two turns posted to one session came back
            # naming a stranger's session and investigations, and clients
            # adopt response ids: "Copy link" copied a stranger's
            # investigation and the permalink rendered their transcript.
            # Refused here, loudly, rather than published: an error the
            # analyst can retry is recoverable, and a permalink into
            # somebody else's data is not.
            _assert_own_turn(session_id, outcome)
            if worklist_reference is not None:
                outcome = _with_warning(
                    outcome, worklist_reference_warning(worklist_reference)
                )
            if declaration is not None:
                outcome = _with_warning(outcome, monitor_declaration_warning(declaration))
            outcome, strip = await self._anomaly_reconciliation(request, outcome)
            # Read once here and handed to the assembler: the worklist
            # routing reads the plan context off it and the assembler needs
            # the same row for usage, evidence and provenance.
            trace = await self._components.traces.get(outcome.trace_id)
            try:
                worklist = await self._worklist_for(
                    request, outcome, trace, carried=worklist_reference is not None
                )
            except Exception:
                logger.warning("worklist could not be built for this turn", exc_info=True)
                worklist = None
                outcome = _with_warning(
                    outcome,
                    "the ranked anomaly worklist was requested for this turn but could "
                    "not be built (the detection feed or its re-derivation failed; the "
                    "attempt is recorded in the API log), so this answer carries the "
                    "findings alone",
                )
            response: TurnResult = await assemble_turn_response(
                self._components,
                outcome,
                on_event=on_event,
                anomaly_reconciliation=strip,
                worklist=worklist,
                trace=trace,
            )
            # The same identity, re-asserted on what will actually go out.
            # The check above covers the engine's boundary; this one covers
            # everything between it and the wire, which is where a
            # read-back-shaped assembly could still substitute a newest-row
            # for this request's own resolved objects.
            response = _own_envelope(session_id, outcome, response)
            if worklist is not None:
                # Which page was shown, so the NEXT turn can address it by
                # id or by position. Best-effort: a lost context record costs
                # a later reference, never this answer.
                try:
                    await self._record_worklist_context(outcome, worklist)
                except Exception:  # pragma: no cover - defensive
                    logger.warning("worklist context not recorded", exc_info=True)
            if declaration is not None and isinstance(response, TurnAnswer):
                # The monitor is registered from the ANSWER: the spec that was
                # just planned, validated and measured, with the measured
                # value as its baseline. A monitor registered from a spec
                # nobody confirmed would brief the wrong number every
                # morning, silently, forever.
                response = await self._register_declared_monitor(
                    principal, declaration, outcome, response
                )
            elif declaration is not None and isinstance(response, TurnClarification):
                # The declaration outlives the question it triggered, and
                # the question says so while it is on screen.
                response = await self._defer_declared_monitor(
                    declaration, outcome, response
                )
            if isinstance(response, TurnAnswer):
                # What was PUBLISHED is what is STORED. Everything above
                # this line can add to the answer after the engine has
                # already written its own record, so without this write a
                # permalink drops precisely the warnings this module added.
                await self._persist_published_extras(outcome, response)
        except ReviError as exc:
            # The engine's own sentence, always, in the log: the plain
            # message below is for the analyst, and this is the copy an
            # operator greps for. Nothing published is the only record.
            logger.warning("turn failed with %s: %s", exc.code.value, exc.message)
            response = TurnError(
                outcome="error",
                session_id=session_id,
                error=ErrorEnvelope(
                    code=exc.code.value,
                    # Plain language for the user; the technical message
                    # rides along in debug mode (§12 shape unchanged).
                    message=plain_message(
                        exc.code,
                        exc.message,
                        debug=await self._debug_in_force(session_id, request),
                        details=exc.details,
                    ),
                    correlation_id=correlation_id,
                    # Which budget stopped it, when a budget did — the two
                    # QUERY_BUDGET_EXCEEDED failures want opposite recoveries.
                    subcode=budget_subcode(exc.code, exc.details),
                ),
                # What the failed turn actually spent. A refusal at §6.6
                # arrives after classification and interpretation have both
                # billed; reporting nothing made the ledger short by exactly
                # the turns most likely to be retried.
                usage=ledger.summary(),
            )
            if on_event is not None:
                await on_event("error", response.error.model_dump(mode="json"))
        finally:
            unbind_ledger(ledger_token)
        if request.idempotency_key is not None:
            await self._components.receipts.put(
                principal.tenant,
                session_id,
                request.idempotency_key,
                response.model_dump(mode="json"),
            )
        return response

    async def _register_declared_monitor(
        self,
        principal: Principal,
        declaration: MonitorDeclaration,
        outcome: TurnOutcome,
        response: TurnAnswer,
    ) -> TurnAnswer:
        """Register the monitor this turn declared, and confirm it on the answer.

        Best-effort by design: a Monitors store hiccup must not cost the
        analyst the answer they were shown. When registration fails the
        answer stands and says the monitor was NOT created, because a
        declaration that silently registered nothing is the worst outcome
        available — the analyst walks away believing they are being monitored.
        """
        # What the analyst SAID, carried through as said. The monitor's title
        # is composed from the RESOLVED spec inside register_intent_pin, not
        # from this subject: used as the label, "monitor Silverline Health"
        # kept a payer name the platform had already resolved to a different
        # one, and "monitor this" produced a tile titled by the pronoun.
        units = self._units_for_answer(outcome)
        if declaration.threshold_unreadable:
            # A stated sensitivity this grammar could not read is NEVER
            # silently replaced by the pack's. "more than half a point"
            # registered `governed_default` with no mention of the
            # instruction, so "three points" would have briefed at 0.5
            # forever, silently.
            logger.info(
                "monitor declaration not registered: unreadable threshold %r",
                declaration.threshold_phrase,
            )
            return self._monitor_refused(
                response,
                MonitorRefusedPayload(
                    reason_code="threshold_unreadable",
                    reason=(
                        f"I could not read {declaration.threshold_phrase.strip()!r} as a "
                        "sensitivity, and I will not quietly substitute the governed "
                        "threshold for one you stated — say it again in one of the forms "
                        "below and the monitor is created from this same answer"
                    ),
                    subject=declaration.subject,
                    threshold_phrase=declaration.threshold_phrase,
                    legal_alternatives=legal_threshold_phrases(
                        units[0] if units else None
                    ),
                ),
            )
        try:
            payload = await self._monitors.register_intent_pin(
                principal,
                outcome,
                stated_subject=declaration.subject,
                monitor=declaration.monitor,
                matched_phrase=declaration.matched_phrase,
            )
        except ReviError as exc:
            logger.warning("monitor declaration refused: %s", exc.message)
            return self._monitor_refused(
                response,
                MonitorRefusedPayload(
                    reason_code="threshold_illegal",
                    reason=exc.message,
                    subject=declaration.subject,
                    threshold_phrase=declaration.threshold_phrase,
                    legal_alternatives=legal_threshold_phrases(units[0] if units else None),
                ),
            )
        except Exception:  # pragma: no cover - defensive
            # ``not_stored`` is a claim about the STORE, and it has to be
            # true. Composing the confirmation payload AFTER the pin was
            # written broke it: a monitor the wire could not describe was
            # live in the store while this sentence said nothing was
            # monitoring, so the analyst declared it again and the tenant
            # ended up with two identical monitors. register_intent_pin
            # composes before it writes, so reaching here means nothing was
            # written.
            logger.exception("monitor declaration could not be registered")
            return self._monitor_refused(
                response,
                MonitorRefusedPayload(
                    reason_code="not_stored",
                    reason=(
                        "this turn read as a monitor declaration and the monitor could not be "
                        "stored (the attempt is recorded in the API log); nothing was "
                        "written, so there is no half-created monitor to clean up"
                    ),
                    subject=declaration.subject,
                    threshold_phrase=declaration.threshold_phrase,
                    legal_alternatives=legal_threshold_phrases(units[0] if units else None),
                ),
            )
        return response.model_copy(update={"monitor": payload})

    def _units_for_answer(self, outcome: TurnOutcome) -> list[str | None]:
        """The declared units of the metrics this answer measured."""
        pack = self._components.pack_port
        units: list[str | None] = []
        for measure in outcome.investigation.spec.measures:
            contract = pack.metric(measure.id)
            unit = getattr(contract, "unit", None)
            units.append(None if unit is None else str(unit))
        return units

    @staticmethod
    def _monitor_refused(
        response: TurnAnswer, refusal: MonitorRefusedPayload
    ) -> TurnAnswer:
        """Publish a refused declaration where the confirmation would have gone.

        Three things happen together, or none does: the payload field, the
        prose warning, and its classified twin. Appending to ``warnings``
        alone — after the assembler has already built ``warnings_v2`` — puts
        the refusal on no screen, because every client renders the structured
        list whenever it is non-empty.
        """
        alternatives = (
            " Phrasings that work here: "
            + "; ".join(f"{phrase!r}" for phrase in refusal.legal_alternatives)
            + "."
            if refusal.legal_alternatives
            else ""
        )
        sentence = (
            f"monitor_not_created: this turn read as a monitor declaration and NO monitor was "
            f"created: {refusal.reason}. The answer above stands on its own; nothing is "
            f"being monitored.{alternatives}"
        )
        return response.model_copy(
            update={
                "monitor_refused": refusal,
                "warnings": [*response.warnings, sentence],
                "warnings_v2": [
                    *response.warnings_v2,
                    *structured_warnings([sentence]),
                ],
            }
        )

    async def _defer_declared_monitor(
        self,
        declaration: MonitorDeclaration,
        outcome: TurnOutcome,
        response: TurnClarification,
    ) -> TurnClarification:
        """Hold a declaration across the clarification it triggered.

        Best-effort on the STORE and never on the SPEECH: if the record
        cannot be written the clarification still says the monitor is not
        created yet, because the failure mode this closes is silence and a
        silent failure to defer would be the same silence one level down.
        """
        sentence = MONITOR_PENDING_WARNING.format(
            phrase=declaration.matched_phrase, subject=declaration.subject
        )
        try:
            await self._components.traces.save(
                TraceRecord(
                    trace_id=f"{outcome.trace_id}{MONITOR_TRACE_SUFFIX}",
                    session_id=outcome.session.id,
                    investigation_id=outcome.investigation.id,
                    turn_id=outcome.investigation.turn_id,
                    created_at=datetime.now(UTC),
                    payload={"monitor_declaration": _declaration_payload(declaration)},
                )
            )
        except Exception:  # pragma: no cover - defensive
            logger.warning("pending monitor declaration not recorded", exc_info=True)
            sentence = (
                "monitor_pending_clarification: this turn read as a monitor declaration and the "
                "monitor was NOT created; the declaration could not be held across this "
                "question either (the attempt is recorded in the API log), so repeat it once "
                "you have answered."
            )
        return response.model_copy(
            update={
                "warnings": [*response.warnings, sentence],
                "warnings_v2": [
                    *response.warnings_v2,
                    *structured_warnings([sentence]),
                ],
            }
        )

    async def _persist_published_extras(
        self, outcome: TurnOutcome, response: TurnAnswer
    ) -> None:
        """Make the stored turn carry what the published turn carried.

        The engine saves the investigation with the warnings IT produced;
        this module then appends the ones only it can know — the named-cut
        disclosure a monitor declaration earns, the worklist reference it
        resolved, the refusal that says nothing is being monitored. Without
        this write, a permalinked or re-opened turn restores the engine's
        warnings and loses exactly the ones whose purpose is to be readable
        later.

        Two writes and both are additive: the SENTENCES merge onto the
        investigation itself, so every restore path picks them up with no
        extra read, and the structured refusal rides on a supplementary
        record because a refusal is a shape rather than a sentence.

        The composed NARRATIVE rides the same write. The engine has already
        stored this investigation by the time the prose exists — it is
        composed here, one layer up, from the outcome the engine returned —
        so the turn's own record could never carry it, and a restored turn
        rendered "The written analysis was not stored for this turn" where
        the live turn published two thousand characters. What is stored is
        the POST-VALIDATION text — the sentences that survived grounding — so
        a restored turn shows what was published and never what was composed
        and then redacted.

        Best-effort. The analyst has their answer; a store hiccup here costs
        a restore, never the turn.
        """
        investigation = outcome.investigation
        stored = list(investigation.warnings)
        added = [w for w in response.warnings if w and w not in stored]
        prose = response.narrative.strip() if response.narrative else ""
        narrative = prose or investigation.narrative
        if not added and narrative == investigation.narrative and response.monitor_refused is None:
            return
        try:
            if added or narrative != investigation.narrative:
                await self._components.investigations.save(
                    replace(
                        investigation, warnings=(*stored, *added), narrative=narrative
                    ),
                    None,
                )
            if response.monitor_refused is not None:
                await self._components.traces.save(
                    TraceRecord(
                        trace_id=f"{outcome.trace_id}{API_TRACE_SUFFIX}",
                        session_id=investigation.session_id,
                        investigation_id=investigation.id,
                        turn_id=investigation.turn_id,
                        created_at=datetime.now(UTC),
                        payload={
                            "monitor_refused": response.monitor_refused.model_dump(mode="json")
                        },
                    )
                )
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "published warnings could not be persisted for %s",
                investigation.id,
                exc_info=True,
            )

    async def _pending_monitor_declaration(self, session_id: str) -> MonitorDeclaration | None:
        """The monitor declaration the outstanding clarification interrupted.

        Read from the MOST RECENT investigation in the session only. That is
        what makes it self-clearing: once the resolving turn writes its own
        investigation, the record is no longer the newest and cannot be
        applied twice — a declaration that registered a second monitor three
        turns later would be its own defect.
        """
        try:
            lineage = await self._components.investigations.lineage(session_id)
        except Exception:  # pragma: no cover - defensive
            logger.warning("session lineage unreadable for %s", session_id, exc_info=True)
            return None
        if lineage is None or not lineage.investigations:
            return None
        newest = max(lineage.investigations, key=lambda inv: inv.created_at)
        for record in await self._components.traces.for_investigation(newest.id):
            raw = record.payload.get("monitor_declaration")
            if isinstance(raw, dict):
                return _declaration_from_payload(raw)
        return None

    async def _anomaly_reconciliation(
        self, request: TurnRequest, outcome: TurnOutcome
    ) -> tuple[TurnOutcome, AnomalyReconciliationPayload | None]:
        """Reconcile a drill's answer against the card it was launched from.

        The defect this closes: a card said ``$178,217``, its own drill
        answered ``$195,873.92``, and the only reconciliation anywhere on
        the answer read ``not_applicable — this is a first turn``. That
        verdict is about the investigation LINEAGE and is correct; it is
        simply not about the two numbers the analyst had just compared, and
        nothing else was.

        The figure compared is the money total of the answer's own final
        frame — the same quantity :mod:`revi_api.rederive` sums for the
        card's ``reconciled_impact_cents`` — so the strip and the card can
        never describe this pair of numbers differently.

        Every non-answer path returns a warning rather than an error: a
        reference to a card that no longer exists in the feed must not
        cost the analyst the answer they asked for.
        """
        ref = request.anomaly_ref
        if not ref:
            return outcome, None
        if request.spec is None:
            return (
                _with_warning(
                    outcome,
                    f"anomaly_ref {ref!r} ignored: it names the card a TYPED drill was "
                    "launched from, and this turn carries no typed spec, so there is no "
                    "drill of that card to reconcile against",
                ),
                None,
            )
        try:
            records = await self._components.anomaly_source.list_anomalies(
                outcome.session.watermark
            )
        except Exception:
            logger.warning("detection feed unreadable while reconciling %s", ref, exc_info=True)
            return (
                _with_warning(
                    outcome,
                    f"anomaly_ref {ref!r}: the detection feed could not be read at this "
                    "watermark, so this answer is published without the card-to-drill "
                    "reconciliation it would otherwise carry",
                ),
                None,
            )
        record = next((r for r in records if r.anomaly_id == ref), None)
        if record is None:
            return (
                _with_warning(
                    outcome,
                    f"anomaly_ref {ref!r} is not in the detection feed at watermark "
                    f"{outcome.session.watermark.id}; the answer below stands on its own "
                    "evidence, with no card figure to reconcile against",
                ),
                None,
            )
        # The same declared-unit gate the portfolio's re-derivation applies:
        # a ratio contract's frame may carry a money numerator, and summing
        # it publishes a dollar figure the metric does not measure.
        refusal = non_money_reason(tuple(request.spec.metric_ids), self._components.pack_port)
        if refusal is None:
            cents, measure, rows = money_total(outcome.frames)
        else:
            cents, measure, rows = None, None, 0
        snapshots = self._snapshot_metric_ids()
        comparison = compare_impact(
            detector_cents=record.impact_cents,
            window_start=record.window_start,
            window_end=record.window_end,
            rederived=ReDerivedImpact(
                cents=cents,
                measure_id=measure,
                rows=rows,
                unavailable_reason=(
                    None
                    if cents is not None
                    else refusal
                    or "this answer produces no money column, so there is no figure to "
                    "compare against the card's dollar impact"
                ),
            ),
            unattempted_note="",
            not_comparable_reason=(
                SNAPSHOT_NOT_COMPARABLE
                if any(mid in snapshots for mid in request.spec.metric_ids)
                else None
            ),
        )
        # The card's OWN reconciliation sentence, not the raw comparison
        # note. ``detail=comparison.note`` published "the detector's window,
        # population or valuation basis is not the contract's" on a drill
        # whose population differs because THIS PLATFORM substituted the cut
        # — laying the platform's own dimension swap at the detector's door.
        # :func:`revi_api.portfolio.reconciliation_note` is what the card
        # uses; routing through it is what makes the drill say what the card
        # says.
        repoints = dimension_repoints_for(
            record,
            self._components.actionability,
            request.spec.metric_ids[0],
            self._scope_dimensions,
        )
        detail = reconciliation_note(comparison, repoints)
        if repoints:
            outcome = _with_warning(outcome, dimension_repointed_warning(record, repoints))
        strip = AnomalyReconciliationPayload(
            anomaly_id=record.anomaly_id,
            status=comparison.status,  # type: ignore[arg-type]
            card_impact_cents=comparison.detector_cents,
            answer_impact_cents=comparison.platform_cents,
            delta_cents=comparison.delta_cents,
            delta_fraction=comparison.delta_fraction,
            answer_metric_id=comparison.measure_id,
            card_metric_id=record.metric_id,
            card_window_start=record.window_start,
            card_window_end=record.window_end,
            detail=detail,
            summary=(
                f"status={comparison.status}; card=${record.impact_cents / 100:,.2f}; "
                + (
                    f"answer=${comparison.platform_cents / 100:,.2f}"
                    if comparison.platform_cents is not None
                    else "answer=unavailable"
                )
                + (
                    f"; delta={comparison.delta_fraction:+.1%}"
                    if comparison.delta_fraction is not None
                    else ""
                )
            ),
        )
        return outcome, strip

    async def _debug_in_force(self, session_id: str, request: TurnRequest) -> bool:
        """Was this turn asked to show its working?

        Read the same way the turn itself resolves it: a per-turn override
        first, then the session's own setting. Best-effort on purpose — a
        store hiccup while assembling an error must not replace the error
        with a different one, so an unreadable session simply means "not
        debug" and the analyst still gets the plain sentence.
        """
        if request.settings is not None:
            return bool(request.settings.debug)
        try:
            session = await self._components.sessions.get(session_id)
        except Exception:  # pragma: no cover - defensive; see docstring
            logger.debug("could not read session settings for error copy", exc_info=True)
            return False
        return session is not None and session.settings.debug

    async def get_investigation(
        self, principal: Principal, investigation_id: str
    ) -> InvestigationResponse:
        investigation = await self._components.investigations.get(investigation_id)
        if investigation is None:
            raise NotFoundError(
                f"investigation {investigation_id!r} does not exist",
                details={"investigation_id": investigation_id},
            )
        await self._authorized_session(principal, investigation.session_id)
        # The evidence bundle rides along whenever the turn's trace is
        # still there, and the charts are rebuilt from the frames the turn
        # persisted. This is the route the web calls to rebuild a re-opened
        # session, and without them every restored turn showed an evidence
        # drawer saying nothing was ever read, next to no charts at all.
        trace = await self._primary_trace(investigation_id)
        extras = await self._api_extras(investigation_id)
        return investigation_response(
            investigation,
            trace,
            await restored_chart_specs(self._components, investigation, trace),
            # The population the turn was computed over — a restored turn
            # keeps its chip instead of losing the one field that says what
            # it measured. The stored spec's own context is the first
            # source and the honest one: it carries the cohort a turn
            # INHERITED as well as one it pinned, where the trace's
            # ``refinement.cohort`` block is written only by the turn that
            # did the pinning. Without the fallback, a comparison turn two
            # steps after the drill would restore with no population at all.
            cohort=await cohort_payload_for(
                _cohort_id_of(investigation)
                or (cohort_id_from_trace(trace.payload) if trace is not None else None),
                session_id=investigation.session_id,
                cohorts=self._components.cohorts,
                referents=self._components.referents,
                investigations=self._components.investigations,
            ),
            metric_display=self._components.metric_display,
            # So the restored header says "as of <date>" for a turn measured
            # entirely by snapshot contracts, exactly as the live one did.
            snapshot_metric_ids=self._snapshot_metric_ids(),
            # Governed peer ranges are keyed by metric id, so a restored
            # turn can carry the same ones the live answer did instead of
            # losing the context its narrative quoted.
            benchmarks_for_metric=self._components.pack_port.benchmarks_for_metric,
            pack_version=self._components.pack_port.pack_version,
            # The refusal, restored as the shape it is. Its prose twin rode
            # back on the investigation's own warnings, which is why the
            # lineage listing needs no second read to stay honest.
            monitor_refused=(
                MonitorRefusedPayload.model_validate(extras["monitor_refused"])
                if isinstance(extras.get("monitor_refused"), dict)
                else None
            ),
        )

    async def _primary_trace(self, investigation_id: str) -> TraceRecord | None:
        """The turn's decision trace, if one was recorded.

        Supplementary records persist against the same investigation — the
        narrative validator's, the worklist page this turn published, the
        declaration a clarification is holding, the API's own additions to
        the published answer — and are excluded by suffix. The decision
        trace is the one with no suffix at all; picking "the first one that
        is not the narrative" was already a rule that would break the next
        time a second writer appeared, which is what happened.
        """
        records = await self._components.traces.for_investigation(investigation_id)
        return next(
            (r for r in records if not r.trace_id.endswith(_SUPPLEMENTARY_SUFFIXES)),
            None,
        )

    async def _api_extras(self, investigation_id: str) -> Mapping[str, Any]:
        """What the API added to the published answer after the engine
        finished, read back for a restore. Empty when it added nothing."""
        for record in await self._components.traces.for_investigation(investigation_id):
            if record.trace_id.endswith(API_TRACE_SUFFIX):
                return record.payload
        return {}

    async def get_trace(
        self, principal: Principal, investigation_id: str
    ) -> DebugTracePayload:
        """One turn's decision trace, tenant-scoped like every other read.

        The trace is recorded on every turn whether or not ``debug`` was
        on — the setting decides what is *published with the answer*, not
        what is kept. This route is the other door onto the same record,
        for the turn nobody thought to debug until afterwards. A
        deployment that does not want traces served at all sets
        ``REVI_DEBUG_TRACE=0`` and this refuses.
        """
        if not self._components.settings_policy.debug_available:
            raise PolicyDeniedError(
                f"decision traces are disabled on this deployment ({DEBUG_TRACE_ENV}=0)",
                details={"investigation_id": investigation_id},
            )
        investigation = await self._components.investigations.get(investigation_id)
        if investigation is None:
            raise NotFoundError(
                f"investigation {investigation_id!r} does not exist",
                details={"investigation_id": investigation_id},
            )
        await self._authorized_session(principal, investigation.session_id)
        primary = await self._primary_trace(investigation_id)
        if primary is None:
            raise NotFoundError(
                f"no decision trace was recorded for investigation {investigation_id!r}",
                details={"investigation_id": investigation_id},
            )
        return build_debug_trace(primary)

    async def get_session_lineage(
        self, principal: Principal, session_id: str
    ) -> SessionLineageResponse:
        await self._authorized_session(principal, session_id)
        lineage = await self._components.investigations.lineage(session_id)
        if lineage is None:
            raise NotFoundError(
                f"session {session_id!r} does not exist", details={"session_id": session_id}
            )
        # One pack read for the whole DAG, not one per node.
        snapshots = self._snapshot_metric_ids()
        return SessionLineageResponse(
            session=_session_response(lineage.session),
            investigations=[
                investigation_response(
                    inv,
                    metric_display=self._components.metric_display,
                    snapshot_metric_ids=snapshots,
                    benchmarks_for_metric=self._components.pack_port.benchmarks_for_metric,
                    pack_version=self._components.pack_port.pack_version,
                )
                for inv in lineage.investigations
            ],
            edges=[
                LineageEdgePayload(
                    parent_id=edge.parent_id,
                    child_id=edge.child_id,
                    turn_id=edge.turn_id,
                    operators=[
                        refinement_to_dto(op).model_dump(mode="json") for op in edge.operators
                    ],
                )
                for edge in lineage.edges
            ],
        )

    async def get_capabilities(self, principal: Principal) -> CapabilitiesResponse:
        del principal  # capabilities are deployment-wide, but still authenticated
        components = self._components
        caps = components.repository.capabilities()
        newest = await components.open_session.newest_watermark()
        repository: dict[str, Any] = {
            "as_of_reads": caps.as_of_reads,
            "cohort_semijoin": caps.cohort_semijoin,
            "max_cohort_size": caps.max_cohort_size,
            "having_pushdown": caps.having_pushdown,
            "server_side_top_n": caps.server_side_top_n,
        }
        return CapabilitiesResponse(
            repository=repository,
            pack_id=components.pack_port.pack_id,
            pack_version=components.pack_port.pack_version,
            pack_snapshot_id=components.pack_port.snapshot_id,
            newest_watermark_id=newest.id,
            llm=components.llm_mode,
            # Fetched once by a client, so any surface that shows a metric
            # id can show what the number actually is.
            metric_display=components.metric_display.all_payloads(),
            # Published so a client renders the controls this deployment
            # actually has — and does not render one that would be refused
            # or, worse, one that would change nothing.
            settings=components.settings_policy.bounds_payload(),
        )

    async def get_portfolio(self, principal: Principal) -> PortfolioResponse:
        """The worklist for one tenant.

        The route used to take no tenant at all. It still reads a single
        shared detection feed — the mock warehouse has one — so the tenant
        is carried on the response rather than pretended into the query:
        a caller can see which tenant a worklist was built for, and the
        day the feed becomes per-tenant this signature does not change.
        """
        return await self._portfolio_for(
            principal.tenant, await self._components.open_session.newest_watermark()
        )

    async def _portfolio_for(
        self, tenant: str, watermark: DataWatermark
    ) -> PortfolioResponse:
        """One build, used by the rail route and by the conversational
        worklist alike — so a chat answer and the portfolio panel can never
        disagree about the order, the figures or the warnings."""
        components = self._components
        records = await components.anomaly_source.list_anomalies(watermark)
        portfolio = build_portfolio(
            records,
            watermark=watermark,
            policy=components.priority_policy,
            rules=components.actionability,
            tenant=tenant,
            drillability=components.drillability,
            rederived=await self._rederived_impacts(records, watermark),
            metric_display=components.metric_display,
            # A snapshot contract is an as-of balance and applies no window,
            # so the gap between it and a card's windowed figure is not a
            # divergence anybody can lay at the detector's door.
            snapshot_metric_ids=self._snapshot_metric_ids(),
            # Which cuts each governed contract accepts — so a card whose
            # detector cut has no legal equivalent at the drilled
            # contract's grain can be repointed onto the one that does,
            # and only where the pack actually allows it.
            scope_dimensions=self._scope_dimensions,
        )
        # Two ADDITIVE passes, both of which leave the ranking exactly as
        # ``anomaly_priority@3`` computed it. Time-to-impact is published
        # context, not a silent re-rank: a rank change needs its own
        # versioned formula decision, and smuggling urgency into an existing
        # version would make two builds of one dataset disagree with no
        # version string to explain it. Lead status is what the humans have
        # done about each card, read from the Monitors lifecycle store so a
        # card and a brief entry cannot disagree about who is working what.
        portfolio = annotate_time_to_impact(
            portfolio,
            {record.anomaly_id: record for record in records},
            newest_data_date=watermark.newest_data_date,
            policy=components.monitors_policy,
        )
        return await self._monitors.decorate_cards(tenant, portfolio)

    def _scope_dimensions(self, metric_id: str) -> frozenset[str]:
        """The dimensions the pack's contract for ``metric_id`` may be cut by."""
        contract = self._components.pack_port.metric(metric_id)
        if contract is None:
            return frozenset()
        return frozenset(dimension.id for dimension in contract.scope_dimensions)

    async def _resolve_worklist_turn(
        self, session_id: str, request: TurnRequest
    ) -> tuple[TurnRequest, WorklistReference | None]:
        """Rewrite a turn that names a worklist row into that row's drill.

        The rewrite is total and deliberate: the request becomes ``{spec:
        <the card's own stored drill_spec>, anomaly_ref: <the card's id>}``
        — byte-for-byte what the portfolio panel posts when the analyst
        clicks the same row — while keeping the analyst's own words as the
        utterance so the turn is titled by what they asked. One path, so the
        reconciliation strip and the repoint disclosure fire for a typed
        reference exactly as they do for a click, and there is no second
        definition of "open a card".

        Only ever attempted against a worklist this session has actually
        SHOWN (see :meth:`_session_worklist`): resolving "the top item"
        against a list nobody has seen would be the platform answering
        about rows the analyst is not looking at.

        A body that already carries a typed spec, typed refinements, or a
        clarification reply is left alone — those are complete requests and
        none of them is a reference to a list.
        """
        if (
            not request.utterance
            or request.spec is not None
            or request.refinements is not None
            or request.clarification_response
        ):
            return request, None
        try:
            cards = await self._session_worklist(session_id)
        except Exception:  # pragma: no cover - defensive; see docstring
            logger.warning("worklist context unreadable for %s", session_id, exc_info=True)
            return request, None
        reference = resolve_worklist_reference(request.utterance, cards)
        if reference is None:
            return request, None
        return (
            request.model_copy(
                update={
                    "spec": reference.card.drill_spec,
                    "anomaly_ref": reference.card.anomaly_id,
                }
            ),
            reference,
        )

    async def _session_worklist(self, session_id: str) -> tuple[AnomalyCard, ...]:
        """The ranked rows this session most recently published, in order.

        The worklist is a deterministic projection of the portfolio at a
        watermark, so what has to persist is not the cards but WHICH page of
        them was shown: the lane and the limit. Those ride on a supplementary
        trace record the turn writes (see :data:`WORKLIST_TRACE_SUFFIX`), the
        same way the narrative validator persists its own, and the rows are
        rebuilt from the same ``build_portfolio`` call the rail uses.

        Empty when this session has shown no worklist — which is what makes
        "the top item" a reference rather than a guess.
        """
        lineage = await self._components.investigations.lineage(session_id)
        if lineage is None or not lineage.investigations:
            return ()
        ordered = sorted(
            lineage.investigations, key=lambda inv: inv.created_at, reverse=True
        )
        for investigation in ordered[:_WORKLIST_CONTEXT_DEPTH]:
            for record in await self._components.traces.for_investigation(investigation.id):
                context = record.payload.get("worklist_context")
                if not isinstance(context, dict):
                    continue
                session = await self._components.sessions.get(session_id)
                if session is None:
                    return ()
                portfolio = await self._portfolio_for(session.tenant, session.watermark)
                built = build_worklist(
                    portfolio,
                    self._components.worklist,
                    matched_on=str(context.get("matched_on") or "typed_query"),
                    matched_id=str(context.get("matched_id") or ""),
                    query=WorklistQuery(
                        limit=context.get("limit"), lane=context.get("lane")
                    ),
                )
                return tuple(built.items)
        return ()

    async def _record_worklist_context(
        self, outcome: TurnOutcome, worklist: WorklistPayload
    ) -> None:
        """Remember which page of the worklist this turn published.

        A supplementary record rather than a field on the decision trace:
        the engine writes that record before the API has built the worklist,
        and rewriting somebody else's finished trace to add a field is how
        two writers end up disagreeing about one row.
        """
        await self._components.traces.save(
            TraceRecord(
                trace_id=f"{outcome.trace_id}{WORKLIST_TRACE_SUFFIX}",
                session_id=outcome.session.id,
                investigation_id=outcome.investigation.id,
                turn_id=outcome.investigation.turn_id,
                created_at=datetime.now(UTC),
                payload={
                    "worklist_context": {
                        "matched_on": worklist.matched_on,
                        "matched_id": worklist.matched_id,
                        "lane": _lane_of(worklist),
                        "limit": worklist.limit,
                        "anomaly_ids": [card.anomaly_id for card in worklist.items],
                    }
                },
            )
        )

    async def _worklist_for(
        self,
        request: TurnRequest,
        outcome: TurnOutcome,
        trace: TraceRecord | None,
        *,
        carried: bool = False,
    ) -> WorklistPayload | None:
        """The ranked worklist this turn should carry, or ``None``.

        The conversation reaches the worklist through governed content —
        ``packs/base-rcm/worklist.yaml`` names the playbook and concept ids
        that mean "which work should I pick up" — and through an explicit
        typed request, and through nothing else. No question text is matched
        here or anywhere else in the platform.

        A failure to build one is a warning on the answer, never an error:
        an unreadable detection feed must not cost the analyst the answer
        they actually asked for.

        ``carried`` says this turn is a worklist-scoped follow-up — it
        opened a row of the list by name — so the list is re-attached rather
        than dropped. Without it, "which items are compliance-mandatory",
        asked one turn after the list was published, came back without the
        list, and the analyst had to re-ask for it to ask about it.
        """
        routing = self._components.worklist
        query = request.worklist
        if query is None and not routing.enabled and not carried:
            return None
        matched: tuple[str, str] | None = ("typed_query", "") if query is not None else None
        if matched is None:
            concepts = tuple(getattr(outcome.investigation.spec, "concepts", ()) or ())
            matched = routing.match(playbook_id=None, concepts=concepts)
        if matched is None:
            # The playbook lives on the recorded plan context, not on the
            # outcome.
            matched = routing.match(playbook_id=_playbook_of(trace), concepts=())
        if matched is None and carried:
            matched = ("typed_query", "")
        if matched is None:
            return None
        portfolio = await self._portfolio_for(
            outcome.session.tenant, outcome.session.watermark
        )
        return build_worklist(
            portfolio,
            routing,
            matched_on=matched[0],
            matched_id=matched[1],
            query=query,
        )

    def _snapshot_metric_ids(self) -> frozenset[str]:
        """Pack metrics whose contract ``kind`` is ``snapshot``.

        Read from the pinned pack rather than listed here: the eight A/R
        and inventory contracts that are snapshots today are a fact about
        the pack, and a hardcoded list would go stale the first time one is
        added.
        """
        pack = self._components.pack_port
        out: set[str] = set()
        for metric_id, _ in pack.metric_summaries():
            contract = pack.metric(metric_id)
            if contract is not None and str(contract.kind) == "snapshot":
                out.add(metric_id)
        return frozenset(out)

    async def _rederived_impacts(
        self, records: tuple[AnomalyRecord, ...], watermark: DataWatermark
    ) -> dict[str, ReDerivedImpact]:
        """This platform's own figure for every card that can be drilled.

        Sequential on purpose. These reads go through the ordinary
        evidence cache, so the second build of a watermark is nearly free
        and the analyst's later drill of a card reuses the very frame
        computed here; firing thirty concurrent warehouse queries to save
        a few seconds on the first build would trade a bounded wait for an
        unbounded load spike on the one connection the whole API shares.

        A card whose drill does not plan is skipped before any query — the
        re-deriver returns the refusal, and the card says "not
        investigable at this catalog version" rather than "unreconciled".
        """
        components = self._components
        out: dict[str, ReDerivedImpact] = {}
        for record in records:
            if not is_active(record):
                continue
            # The SAME spec the card will publish, repoints included, or
            # the re-derived figure would belong to a different population
            # from the one the card offers to open.
            spec = drill_spec_for(record, components.actionability, self._scope_dimensions)
            out[record.anomaly_id] = await components.rederive_impact(spec, watermark)
        return out
