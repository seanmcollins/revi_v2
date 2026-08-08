"""FastAPI application: /v1 routes over the transport-neutral ApiService.

SSE frame format (``Accept: text/event-stream`` on the turn route)::

    event: <kind>
    data: <json>

Kinds mirror the engine's TurnEventBus (``stage``, ``warning``,
``clarification``) plus the presentation frames (``context_header``,
``finding``, ``chart_spec``, ``narrative_delta``, ``error``) and a final
``turn_complete`` whose data is the FULL TurnResponse JSON — the stream is
progress; the last frame is the authoritative payload. With
``Accept: application/json`` the same route blocks and returns the
TurnResponse. CLARIFICATION_REQUIRED is a 200 outcome, never an error.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from revi_api.service import ApiService, NotFoundError, TurnResult
from revi_api.wiring import build_components
from revi_investigation.application.ports import TurnEvent
from revi_investigation_contracts.api import (
    CapabilitiesResponse,
    ErrorEnvelope,
    InvestigationResponse,
    OpenSessionRequest,
    PortfolioResponse,
    SessionLineageResponse,
    SessionResponse,
    TurnRequest,
)
from revi_kernel.errors import ErrorCode, ReviError

logger = logging.getLogger("revi.api")

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.SOURCE_UNAVAILABLE: 503,
    ErrorCode.DATA_LOADING: 503,
    ErrorCode.WATERMARK_STALE: 409,
    ErrorCode.CONTEXT_CONFLICT: 409,
    ErrorCode.REFERENT_NOT_FOUND: 404,
}


def _sse_frame(kind: str, payload: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def create_app(service: ApiService | None = None) -> FastAPI:
    """Build the FastAPI app; ``service=None`` wires from the environment
    on first use (kept lazy so OpenAPI export needs no warehouse)."""
    app = FastAPI(title="Revi Investigation API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if service is not None:
        app.state.service = service

    def _service() -> ApiService:
        existing = getattr(app.state, "service", None)
        if existing is None:
            logger.info("wiring API components from the environment")
            existing = ApiService(build_components())
            app.state.service = existing
        assert isinstance(existing, ApiService)
        return existing

    @app.exception_handler(ReviError)
    async def _revi_error(request: Request, exc: ReviError) -> JSONResponse:
        status = 404 if isinstance(exc, NotFoundError) else _STATUS_BY_CODE.get(exc.code, 422)
        envelope = ErrorEnvelope(
            code=exc.code.value,
            message=exc.message,
            correlation_id=request.headers.get("x-correlation-id", "unset"),
        )
        return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))

    @app.post("/v1/sessions", response_model=SessionResponse)
    async def open_session(request: OpenSessionRequest) -> SessionResponse:
        return await _service().open_session(request)

    @app.post("/v1/sessions/{session_id}/turns")
    async def submit_turn(session_id: str, turn: TurnRequest, request: Request) -> Any:
        service = _service()
        accept = request.headers.get("accept", "")
        if "text/event-stream" not in accept:
            response = await service.submit_turn(session_id, turn)
            return JSONResponse(content=_dump(response))

        async def stream() -> AsyncIterator[str]:
            bus = service.components.event_bus
            queue, token = bus.bind()

            async def on_event(kind: str, payload: dict[str, Any]) -> None:
                # presentation frames ride the same channel as engine events
                queue.put_nowait(TurnEvent(kind=kind, turn_id="", payload=payload))

            task = asyncio.create_task(service.submit_turn(session_id, turn, on_event=on_event))
            try:
                while True:
                    drain = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait(
                        {drain, task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if drain in done:
                        event = drain.result()
                        if event.kind != "turn_complete":  # the API emits the final frame
                            yield _sse_frame(event.kind, dict(event.payload))
                        continue
                    drain.cancel()
                    while not queue.empty():
                        event = queue.get_nowait()
                        if event.kind != "turn_complete":
                            yield _sse_frame(event.kind, dict(event.payload))
                    break
                response = task.result()
                yield _sse_frame("turn_complete", _dump(response))
            finally:
                bus.unbind(token)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/v1/investigations/{investigation_id}", response_model=InvestigationResponse)
    async def get_investigation(investigation_id: str) -> InvestigationResponse:
        return await _service().get_investigation(investigation_id)

    @app.get("/v1/sessions/{session_id}/lineage", response_model=SessionLineageResponse)
    async def get_lineage(session_id: str) -> SessionLineageResponse:
        return await _service().get_session_lineage(session_id)

    @app.get("/v1/portfolio/latest", response_model=PortfolioResponse)
    async def get_portfolio() -> PortfolioResponse:
        return await _service().get_portfolio()

    @app.get("/v1/capabilities", response_model=CapabilitiesResponse)
    async def get_capabilities() -> CapabilitiesResponse:
        return await _service().get_capabilities()

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        service = _service()
        newest = await service.components.open_session.newest_watermark()
        return {
            "status": "ok",
            "watermark": newest.id,
            "store_mode": service.components.store_mode,
            "llm_mode": service.components.llm_mode,
        }

    return app


def _dump(response: TurnResult | BaseModel) -> dict[str, Any]:
    dumped: dict[str, Any] = response.model_dump(mode="json")
    return dumped
