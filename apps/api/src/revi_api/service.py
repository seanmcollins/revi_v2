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
from typing import Any

from revi_api.assembly import (
    NARRATIVE_TRACE_SUFFIX,
    OnEvent,
    assemble_turn_response,
    investigation_response,
    restored_chart_specs,
)
from revi_api.auth import AuthorizationError, Principal
from revi_api.debug_trace import build_debug_trace
from revi_api.portfolio import build_portfolio
from revi_api.settings_policy import DEBUG_TRACE_ENV
from revi_api.wiring import ApiComponents
from revi_investigation.application.dto_mapping import refinement_to_dto
from revi_investigation.application.ports import TraceRecord
from revi_investigation.application.submit_turn import SubmitTurnRequest
from revi_investigation.domain.records import Session
from revi_investigation.domain.settings import SessionSettings
from revi_investigation_contracts.api import (
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
)
from revi_investigation_contracts.settings import SessionSettingsModel
from revi_kernel.errors import ErrorCode, PolicyDeniedError, ReviError

logger = logging.getLogger("revi.api.service")

TurnResult = TurnAnswer | TurnClarification | TurnError

#: Page size for ``GET /v1/sessions`` when the caller names none.
DEFAULT_SESSION_LIST_LIMIT = 50
#: Hard cap on that page. A list route with an unbounded page size is a
#: denial-of-service handle a client can pull by accident.
MAX_SESSION_LIST_LIMIT = 200


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
        self._idempotent: dict[tuple[str, str, str], TurnResult] = {}

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
            stored = self._idempotent.get(
                (principal.tenant, session_id, request.idempotency_key)
            )
            if stored is not None:
                return stored
        default_question = (
            "(typed investigation)" if request.spec is not None else "(typed gesture)"
        )
        utterance = request.utterance or request.clarification_response or default_question
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
            response: TurnResult = await assemble_turn_response(
                self._components, outcome, on_event=on_event
            )
        except ReviError as exc:
            logger.warning("turn failed with %s: %s", exc.code.value, exc.message)
            response = TurnError(
                outcome="error",
                session_id=session_id,
                error=ErrorEnvelope(
                    code=exc.code.value, message=exc.message, correlation_id=correlation_id
                ),
            )
            if on_event is not None:
                await on_event("error", response.error.model_dump(mode="json"))
        if request.idempotency_key is not None:
            self._idempotent[(principal.tenant, session_id, request.idempotency_key)] = response
        return response

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
        return SessionLineageResponse(
            session=_session_response(lineage.session),
            investigations=[investigation_response(inv) for inv in lineage.investigations],
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
        components = self._components
        newest = await components.open_session.newest_watermark()
        records = await components.anomaly_source.list_anomalies(newest)
        return build_portfolio(
            records,
            watermark=newest,
            policy=components.priority_policy,
            rules=components.actionability,
            tenant=principal.tenant,
            drillability=components.drillability,
        )
