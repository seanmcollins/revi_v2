"""Transport-neutral ``InvestigationApi`` implementation.

The FastAPI routes and the in-process client both delegate here — one
contract, two transports. Turn errors normalize to the ``TurnError``
variant with a stable kernel-code :class:`ErrorEnvelope` on BOTH
transports; an idempotency key returns the stored response without
re-executing anything.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from revi_api.assembly import OnEvent, assemble_turn_response, investigation_response
from revi_api.portfolio import build_portfolio
from revi_api.wiring import ApiComponents
from revi_investigation.application.dto_mapping import refinement_to_dto
from revi_investigation.application.submit_turn import SubmitTurnRequest
from revi_investigation.domain.records import Session
from revi_investigation_contracts.api import (
    CapabilitiesResponse,
    ErrorEnvelope,
    InvestigationResponse,
    LineageEdgePayload,
    OpenSessionRequest,
    PortfolioResponse,
    SessionLineageResponse,
    SessionResponse,
    TurnAnswer,
    TurnClarification,
    TurnError,
    TurnRequest,
)
from revi_kernel.errors import ErrorCode, ReviError

logger = logging.getLogger("revi.api.service")

TurnResult = TurnAnswer | TurnClarification | TurnError


class NotFoundError(ReviError):
    """Resource-miss for GET routes (mapped to HTTP 404).

    ``REFERENT_NOT_FOUND`` is the §12 code for "the thing you named does
    not exist here" — the same failure whether the handle is F2 or an
    investigation id. UNSUPPORTED_CONCEPT would say something different and
    false: that the platform cannot express what was asked."""

    code = ErrorCode.REFERENT_NOT_FOUND


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
    )


class ApiService:
    """The one implementation behind both clients and the HTTP routes."""

    def __init__(self, components: ApiComponents) -> None:
        self._components = components
        self._idempotent: dict[tuple[str, str], TurnResult] = {}

    @property
    def components(self) -> ApiComponents:
        return self._components

    # ------------------------------------------------------- InvestigationApi

    async def open_session(self, request: OpenSessionRequest) -> SessionResponse:
        session = await self._components.open_session.open(
            tenant=request.tenant, session_id=request.session_id
        )
        return _session_response(session)

    async def submit_turn(
        self,
        session_id: str,
        request: TurnRequest,
        *,
        on_event: OnEvent | None = None,
    ) -> TurnResult:
        correlation_id = request.correlation_id or f"corr_{uuid.uuid4().hex[:12]}"
        if request.idempotency_key is not None:
            stored = self._idempotent.get((session_id, request.idempotency_key))
            if stored is not None:
                return stored
        utterance = request.utterance or request.clarification_response or "(typed gesture)"
        engine_request = SubmitTurnRequest(
            tenant="api",  # sessions carry the tenant; new implicit sessions use this
            question=utterance,
            session_id=session_id,
            refinements=tuple(request.refinements) if request.refinements is not None else None,
            re_anchor=request.re_anchor,
        )
        try:
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
            self._idempotent[(session_id, request.idempotency_key)] = response
        return response

    async def get_investigation(self, investigation_id: str) -> InvestigationResponse:
        investigation = await self._components.investigations.get(investigation_id)
        if investigation is None:
            raise NotFoundError(
                f"investigation {investigation_id!r} does not exist",
                details={"investigation_id": investigation_id},
            )
        return investigation_response(investigation)

    async def get_session_lineage(self, session_id: str) -> SessionLineageResponse:
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

    async def get_capabilities(self) -> CapabilitiesResponse:
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
        )

    async def get_portfolio(self) -> PortfolioResponse:
        components = self._components
        newest = await components.open_session.newest_watermark()
        records = await components.anomaly_source.list_anomalies(newest)
        return build_portfolio(
            records,
            watermark=newest,
            policy=components.priority_policy,
            rules=components.actionability,
        )
