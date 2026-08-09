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
authorization check in an HTTP middleware protects the HTTP transport and
leaves ``InProcessInvestigationClient`` — the same API, a different
door — wide open. The rule belongs to the service, so both doors get it.

The tenant a turn executes under now comes from the principal. It used to
be the literal string ``"api"``, hardcoded, which meant every session
opened over HTTP belonged to the same tenant no matter who asked.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
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
from revi_api.portfolio import (
    SNAPSHOT_NOT_COMPARABLE,
    build_portfolio,
    drill_spec_for,
    is_active,
)
from revi_api.rederive import (
    ReDerivedImpact,
    compare_impact,
    money_total,
    non_money_reason,
)
from revi_api.settings_policy import DEBUG_TRACE_ENV
from revi_api.usage_ledger import bind_ledger, unbind_ledger
from revi_api.wiring import ApiComponents
from revi_api.worklist import build_worklist
from revi_investigation.application.dto_mapping import refinement_to_dto
from revi_investigation.application.ports import AnomalyRecord, TraceRecord
from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.records import Investigation, Session
from revi_investigation.domain.settings import SessionSettings
from revi_investigation_contracts.api import (
    AnomalyReconciliationPayload,
    CapabilitiesResponse,
    DebugTracePayload,
    ErrorEnvelope,
    InvestigationResponse,
    LineageEdgePayload,
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


def _playbook_of(trace: TraceRecord | None) -> str | None:
    """The governed playbook this turn planned from, off its own trace."""
    if trace is None:
        return None
    raw = (trace.payload.get("plan_context") or {}).get("playbook_id")
    return raw if isinstance(raw, str) else None


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

    @property
    def components(self) -> ApiComponents:
        return self._components

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
        default_question = (
            "(typed investigation)" if request.spec is not None else "(typed gesture)"
        )
        utterance = request.utterance or request.clarification_response or default_question
        # Every model call this turn makes is tallied here, so a turn that
        # FAILS can still say what it spent (review F19). The binding is a
        # contextvar, so concurrent turns cannot read each other's ledger.
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
            )
            outcome = await self._components.submit.submit(engine_request)
            outcome, strip = await self._anomaly_reconciliation(request, outcome)
            # Read once here and handed to the assembler: the worklist
            # routing reads the plan context off it and the assembler needs
            # the same row for usage, evidence and provenance.
            trace = await self._components.traces.get(outcome.trace_id)
            try:
                worklist = await self._worklist_for(request, outcome, trace)
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

    async def _anomaly_reconciliation(
        self, request: TurnRequest, outcome: TurnOutcome
    ) -> tuple[TurnOutcome, AnomalyReconciliationPayload | None]:
        """Reconcile a drill's answer against the card it was launched from.

        The defect this closes (review F1): a card said ``$178,217``, its
        own drill answered ``$195,873.92``, and the only reconciliation
        anywhere on the answer read ``not_applicable — this is a first
        turn``. That verdict is about the investigation LINEAGE and is
        correct; it is simply not about the two numbers the analyst had
        just compared, and nothing else was.

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
        # The same declared-unit gate the portfolio's re-derivation applies
        # (FN-1): a ratio contract's frame may carry a money numerator, and
        # summing it publishes a dollar figure the metric does not measure.
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
            detail=comparison.note,
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
        )

    async def _primary_trace(self, investigation_id: str) -> TraceRecord | None:
        """The turn's decision trace, if one was recorded.

        The narrative validator persists a supplementary record against
        the same investigation; the decision trace is the other one.
        """
        records = await self._components.traces.for_investigation(investigation_id)
        return next(
            (r for r in records if not r.trace_id.endswith(NARRATIVE_TRACE_SUFFIX)), None
        )

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
            # id can show what the number actually is (review F9).
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
        return build_portfolio(
            records,
            watermark=watermark,
            policy=components.priority_policy,
            rules=components.actionability,
            tenant=tenant,
            drillability=components.drillability,
            rederived=await self._rederived_impacts(records, watermark),
            metric_display=components.metric_display,
            # FN-2: a snapshot contract is an as-of balance and applies no
            # window, so the gap between it and a card's windowed figure is
            # not a divergence anybody can lay at the detector's door.
            snapshot_metric_ids=self._snapshot_metric_ids(),
            # Which cuts each governed contract accepts — so a card whose
            # detector cut has no legal equivalent at the drilled
            # contract's grain can be repointed onto the one that does,
            # and only where the pack actually allows it.
            scope_dimensions=self._scope_dimensions,
        )

    def _scope_dimensions(self, metric_id: str) -> frozenset[str]:
        """The dimensions the pack's contract for ``metric_id`` may be cut by."""
        contract = self._components.pack_port.metric(metric_id)
        if contract is None:
            return frozenset()
        return frozenset(dimension.id for dimension in contract.scope_dimensions)

    async def _worklist_for(
        self, request: TurnRequest, outcome: TurnOutcome, trace: TraceRecord | None
    ) -> WorklistPayload | None:
        """The ranked worklist this turn should carry, or ``None``.

        Round-2 deferred P1: the conversation could not reach the worklist
        the platform already computes. It reaches it through governed
        content — ``packs/base-rcm/worklist.yaml`` names the playbook and
        concept ids that mean "which work should I pick up" — and through
        an explicit typed request, and through nothing else. No question
        text is matched here or anywhere else in the platform.

        A failure to build one is a warning on the answer, never an error:
        an unreadable detection feed must not cost the analyst the answer
        they actually asked for.
        """
        routing = self._components.worklist
        query = request.worklist
        if query is None and not routing.enabled:
            return None
        matched: tuple[str, str] | None = ("typed_query", "") if query is not None else None
        if matched is None:
            concepts = tuple(getattr(outcome.investigation.spec, "concepts", ()) or ())
            matched = routing.match(playbook_id=None, concepts=concepts)
        if matched is None:
            # The playbook lives on the recorded plan context, not on the
            # outcome.
            matched = routing.match(playbook_id=_playbook_of(trace), concepts=())
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
