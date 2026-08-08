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

The frame kinds and their payloads are published in the OpenAPI spec as
``TurnStreamEvent`` (``components.schemas``), because a streaming media
type has no response schema of its own — a generated client would
otherwise have no contract for the stream at all.

Error statuses: domain failures carry the stable §12 :class:`ErrorEnvelope`
at 400/401/403/404/409/503 (declared on every route). 422 stays FastAPI's
own ``HTTPValidationError`` for malformed request bodies — one status, one
model, so a generated client never has to guess which shape it got.

Every ``/v1`` route except ``/v1/health`` requires an
``Authorization: Bearer`` token carrying the tenant
(:mod:`revi_api.auth`), and the scheme is published in the OpenAPI spec so
a generated client codes against it rather than discovering 401s at
runtime. ``/v1/health`` stays open on purpose: it is a liveness probe for
an orchestrator, it names no tenant, and requiring a credential to answer
"am I up" makes the credential a single point of failure for restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from revi_api.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthPolicy,
    Principal,
    auth_policy_from_env,
    cors_origins_from_env,
)
from revi_api.cohort_sweep import CohortSweepScheduler, sweep_interval_seconds
from revi_api.service import ApiService, NotFoundError, TurnResult
from revi_api.wiring import build_components
from revi_investigation.application.ports import TurnEvent
from revi_investigation_contracts.api import (
    TURN_EVENT_PAYLOADS,
    CapabilitiesResponse,
    ErrorEnvelope,
    InvestigationResponse,
    OpenSessionRequest,
    PortfolioResponse,
    SessionLineageResponse,
    SessionResponse,
    TurnRequest,
    TurnResponse,
    TurnStreamEvent,
)
from revi_kernel.errors import ErrorCode, ReviError

logger = logging.getLogger("revi.api")

#: Domain errors never use 422 — that status belongs to FastAPI's own
#: request-shape validation, so each documented status has exactly one model.
_DEFAULT_ERROR_STATUS = 400

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.SOURCE_UNAVAILABLE: 503,
    ErrorCode.DATA_LOADING: 503,
    ErrorCode.WATERMARK_STALE: 409,
    ErrorCode.CONTEXT_CONFLICT: 409,
    ErrorCode.REFERENT_NOT_FOUND: 404,
}

#: Declared on every /v1 route so the published spec — not tribal
#: knowledge — is what a generated client codes its error path against.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ErrorEnvelope,
        "description": "Missing, malformed, or expired bearer token "
        "(POLICY_DENIED). The token carries the tenant; it is never taken "
        "from the request body.",
    },
    403: {
        "model": ErrorEnvelope,
        "description": "A valid credential for a different tenant "
        "(POLICY_DENIED). Session and investigation ids are not secrets, so "
        "a cross-tenant read is refused rather than disguised as a 404.",
    },
    400: {
        "model": ErrorEnvelope,
        "description": "Stable §12 error code: the request was understood but "
        "could not be answered (BINDING_AMBIGUOUS, UNSUPPORTED_CONCEPT, "
        "INSUFFICIENT_EVIDENCE, GRAIN_INCOMPATIBLE, POLICY_DENIED, …).",
    },
    404: {
        "model": ErrorEnvelope,
        "description": "Unknown session, investigation, or referent "
        "(REFERENT_NOT_FOUND).",
    },
    409: {
        "model": ErrorEnvelope,
        "description": "WATERMARK_STALE or CONTEXT_CONFLICT — the pinned "
        "context cannot absorb the request without an explicit decision.",
    },
    503: {
        "model": ErrorEnvelope,
        "description": "SOURCE_UNAVAILABLE or DATA_LOADING — the analytical "
        "source cannot serve this watermark right now.",
    },
}

_SSE_DESCRIPTION = "\n".join(
    [
        "**`Accept: text/event-stream`** streams Server-Sent Events; ",
        "**`Accept: application/json`** blocks and returns the same ",
        "`TurnResponse` body. One route, two transports.",
        "",
        "Each SSE frame is `event: <kind>` + `data: <json>` — see the ",
        "`TurnStreamEvent` schema. Frame kinds and payloads:",
        "",
        *(f"- `{kind}` — {doc}" for kind, doc in TURN_EVENT_PAYLOADS.items()),
        "",
        "The stream is progress; the final `turn_complete` frame carries the ",
        "authoritative `TurnResponse`. `CLARIFICATION_REQUIRED` is a 200 ",
        "outcome, never an error.",
    ]
)


def _sse_frame(kind: str, payload: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


_SSE_MEDIA_TYPE = "text/event-stream"
_TURNS_PATH = "/v1/sessions/{session_id}/turns"


def _publish_stream_schema(app: FastAPI) -> None:
    """Teach the spec about the SSE frames.

    FastAPI can only attach a schema to a route's JSON body, so the
    event-stream media type would otherwise be published as an untyped
    blob and a generated client would have no contract for the stream at
    all. This registers :class:`TurnStreamEvent` as a real component and
    points the ``text/event-stream`` content at it, leaving the
    ``application/json`` schema (the blocking ``TurnResponse``) alone.
    """
    base = app.openapi

    def openapi() -> dict[str, Any]:
        spec = base()
        schemas = spec.setdefault("components", {}).setdefault("schemas", {})
        if TurnStreamEvent.__name__ not in schemas:
            schemas[TurnStreamEvent.__name__] = TurnStreamEvent.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
        content = spec["paths"][_TURNS_PATH]["post"]["responses"]["200"]["content"]
        content[_SSE_MEDIA_TYPE] = {
            "schema": {"$ref": f"#/components/schemas/{TurnStreamEvent.__name__}"}
        }
        app.openapi_schema = spec
        return spec

    app.openapi = openapi  # type: ignore[method-assign]


_BEARER = HTTPBearer(auto_error=False, description="Tenant-bearing signed token")


async def _principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
) -> Principal:
    """Resolve the request's credential against the app's auth policy.

    Module-level on purpose: with ``from __future__ import annotations``
    every annotation is a string that FastAPI resolves against the
    *module* globals, so a dependency closed over ``create_app``'s locals
    would be an unresolvable forward reference (and FastAPI would silently
    fall back to treating the parameter as a query field).
    """
    policy: AuthPolicy = request.app.state.auth_policy
    header = (
        f"{credentials.scheme} {credentials.credentials}" if credentials is not None else None
    )
    return policy.authenticate(header)


#: The caller, on every /v1 route that touches tenant-scoped data.
CallerPrincipal = Annotated[Principal, Depends(_principal)]


def create_app(
    service: ApiService | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> FastAPI:
    """Build the FastAPI app; ``service=None`` wires from the environment
    on first use (kept lazy so OpenAPI export needs no warehouse)."""
    settings = env if env is not None else dict(os.environ)
    policy: AuthPolicy = auth_policy_from_env(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Cohort reclamation runs in the process that creates cohorts.

        Components are wired here rather than left to the first request,
        because a sweep that only happens on a request is a sweep that never
        happens on an idle deployment — and the idle deployment is exactly the
        one whose cohort tables nobody is watching. A wiring failure is logged
        and the app still starts: reclaiming storage must not be able to keep
        the surface that answers questions from coming up.
        """
        interval = sweep_interval_seconds(settings)
        # Wiring is skipped outright when reclamation is off, so switching
        # the sweep off cannot make an OpenAPI export or a smoke start
        # demand a warehouse it never needed.
        scheduler = CohortSweepScheduler(
            _repository_or_none() if interval > 0 else None, interval_seconds=interval
        )
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(title="Revi Investigation API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins_from_env(settings),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.auth_policy = policy
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

    def _repository_or_none() -> object:
        """The wired analytical repository, or ``None`` if wiring failed.

        Startup must not die because the warehouse is momentarily missing —
        that would turn a reclamation feature into an availability regression.
        """
        try:
            return _service().components.repository
        except Exception:
            logger.exception("could not wire components at startup — cohort reclamation is off")
            return None

    @app.exception_handler(ReviError)
    async def _revi_error(request: Request, exc: ReviError) -> JSONResponse:
        if isinstance(exc, AuthenticationError):
            status = 401
        elif isinstance(exc, AuthorizationError):
            status = 403
        elif isinstance(exc, NotFoundError):
            status = 404
        else:
            status = _STATUS_BY_CODE.get(exc.code, _DEFAULT_ERROR_STATUS)
        envelope = ErrorEnvelope(
            code=exc.code.value,
            message=exc.message,
            correlation_id=request.headers.get("x-correlation-id", "unset"),
        )
        return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))

    @app.post("/v1/sessions", response_model=SessionResponse, responses=ERROR_RESPONSES)
    async def open_session(
        request: OpenSessionRequest, caller: CallerPrincipal
    ) -> SessionResponse:
        """Open a session pinned to the newest watermark and active pack.

        The tenant comes from the token. A body naming a different one is
        refused rather than honored — that field used to be the only thing
        deciding which tenant a session belonged to."""
        return await _service().open_session(caller, request)

    @app.post(
        "/v1/sessions/{session_id}/turns",
        summary="Submit one turn (utterance OR typed refinement operators)",
        description=_SSE_DESCRIPTION,
        responses={
            **ERROR_RESPONSES,
            200: {
                "model": TurnResponse,
                "description": "The turn's `TurnResponse` (JSON), or — with "
                "`Accept: text/event-stream` — the SSE stream whose frames are "
                "`TurnStreamEvent`.",
                "content": {"application/json": {}, _SSE_MEDIA_TYPE: {}},
            },
        },
    )
    async def submit_turn(
        session_id: str,
        turn: TurnRequest,
        request: Request,
        caller: CallerPrincipal,
    ) -> Any:
        service = _service()
        accept = request.headers.get("accept", "")
        if "text/event-stream" not in accept:
            response = await service.submit_turn(caller, session_id, turn)
            return JSONResponse(content=_dump(response))

        async def stream() -> AsyncIterator[str]:
            bus = service.components.event_bus
            queue, token = bus.bind()

            async def on_event(kind: str, payload: dict[str, Any]) -> None:
                # presentation frames ride the same channel as engine events
                queue.put_nowait(TurnEvent(kind=kind, turn_id="", payload=payload))

            task = asyncio.create_task(
                service.submit_turn(caller, session_id, turn, on_event=on_event)
            )
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

    @app.get(
        "/v1/investigations/{investigation_id}",
        response_model=InvestigationResponse,
        responses=ERROR_RESPONSES,
    )
    async def get_investigation(
        investigation_id: str, caller: CallerPrincipal
    ) -> InvestigationResponse:
        """Re-fetch a completed turn (reconnect / refresh recovery)."""
        return await _service().get_investigation(caller, investigation_id)

    @app.get(
        "/v1/sessions/{session_id}/lineage",
        response_model=SessionLineageResponse,
        responses=ERROR_RESPONSES,
    )
    async def get_lineage(
        session_id: str, caller: CallerPrincipal
    ) -> SessionLineageResponse:
        """The session's investigation DAG: nodes plus refinement edges."""
        return await _service().get_session_lineage(caller, session_id)

    @app.get("/v1/portfolio/latest", response_model=PortfolioResponse, responses=ERROR_RESPONSES)
    async def get_portfolio(caller: CallerPrincipal) -> PortfolioResponse:
        """Detected anomalies at the pinned watermark, governed-priority
        ranked. Cards carry `provenance` rather than an evidence grade."""
        return await _service().get_portfolio(caller)

    @app.get("/v1/capabilities", response_model=CapabilitiesResponse, responses=ERROR_RESPONSES)
    async def get_capabilities(
        caller: CallerPrincipal,
    ) -> CapabilitiesResponse:
        """Repository capabilities, pinned pack, newest watermark, LLM mode."""
        return await _service().get_capabilities(caller)

    @app.get("/v1/health", responses=ERROR_RESPONSES)
    async def health() -> dict[str, Any]:
        """Liveness plus the wiring actually in effect (stores, LLM mode)."""
        service = _service()
        newest = await service.components.open_session.newest_watermark()
        return {
            "status": "ok",
            "watermark": newest.id,
            "store_mode": service.components.store_mode,
            "llm_mode": service.components.llm_mode,
            # An operator must be able to see, without a credential, that a
            # deployment is running the development auth bypass.
            "auth_mode": policy.mode,
        }

    _publish_stream_schema(app)
    return app


def _dump(response: TurnResult | BaseModel) -> dict[str, Any]:
    dumped: dict[str, Any] = response.model_dump(mode="json")
    return dumped
