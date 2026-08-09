"""The two ``InvestigationApi`` clients — one contract, two transports.

``InProcessInvestigationClient`` wraps :class:`ApiService` directly (no
HTTP); ``HttpInvestigationClient`` talks to the FastAPI app over httpx
(real network or ASGI transport in tests) and additionally exposes
``stream_turn`` for consuming the SSE frames.

Both carry a caller identity. The in-process client holds a
:class:`~revi_api.auth.Principal` because it *is* the trusted embedding —
there is no token to verify when there is no transport — and the HTTP
client holds a bearer token because there is. Neither can skip the tenant:
``ApiService`` enforces scoping below both of them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import TypeAdapter

from revi_api.auth import Principal
from revi_api.service import ApiService, TurnResult
from revi_investigation_contracts.api import (
    CapabilitiesResponse,
    DebugTracePayload,
    ErrorEnvelope,
    InvestigationResponse,
    OpenSessionRequest,
    PortfolioResponse,
    SessionLineageResponse,
    SessionListResponse,
    SessionResponse,
    TurnError,
    TurnRequest,
    TurnResponse,
)

_TURN_RESPONSE: TypeAdapter[TurnResult] = TypeAdapter(TurnResponse)


class InProcessInvestigationClient:
    """``InvestigationApi`` over the service object — zero transport."""

    def __init__(self, service: ApiService, principal: Principal | None = None) -> None:
        self._service = service
        self._principal = principal or Principal(
            tenant="demo", subject="in-process", development=True
        )

    @property
    def principal(self) -> Principal:
        return self._principal

    async def open_session(self, request: OpenSessionRequest) -> SessionResponse:
        return await self._service.open_session(self._principal, request)

    async def list_sessions(self, limit: int = 50) -> SessionListResponse:
        return await self._service.list_sessions(self._principal, limit=limit)

    async def submit_turn(self, session_id: str, request: TurnRequest) -> TurnResult:
        return await self._service.submit_turn(self._principal, session_id, request)

    async def get_investigation(self, investigation_id: str) -> InvestigationResponse:
        return await self._service.get_investigation(self._principal, investigation_id)

    async def get_trace(self, investigation_id: str) -> DebugTracePayload:
        return await self._service.get_trace(self._principal, investigation_id)

    async def get_session_lineage(self, session_id: str) -> SessionLineageResponse:
        return await self._service.get_session_lineage(self._principal, session_id)

    async def get_capabilities(self) -> CapabilitiesResponse:
        return await self._service.get_capabilities(self._principal)

    async def get_portfolio(self) -> PortfolioResponse:
        return await self._service.get_portfolio(self._principal)


class HttpInvestigationClient:
    """``InvestigationApi`` over HTTP; non-2xx bodies normalize into the
    ``TurnError`` variant (turn route) or raise via response handling."""

    def __init__(self, client: httpx.AsyncClient, token: str | None = None) -> None:
        self._client = client
        self._headers = {"authorization": f"Bearer {token}"} if token else {}

    async def open_session(self, request: OpenSessionRequest) -> SessionResponse:
        response = await self._client.post(
            "/v1/sessions", json=request.model_dump(mode="json"), headers=self._headers
        )
        response.raise_for_status()
        return SessionResponse.model_validate(response.json())

    async def list_sessions(self, limit: int = 50) -> SessionListResponse:
        response = await self._client.get(
            "/v1/sessions", params={"limit": limit}, headers=self._headers
        )
        response.raise_for_status()
        return SessionListResponse.model_validate(response.json())

    async def submit_turn(self, session_id: str, request: TurnRequest) -> TurnResult:
        response = await self._client.post(
            f"/v1/sessions/{session_id}/turns",
            json=request.model_dump(mode="json"),
            headers={**self._headers, "accept": "application/json"},
        )
        if response.status_code >= 400:
            return TurnError(
                outcome="error",
                session_id=session_id,
                error=ErrorEnvelope.model_validate(response.json()),
            )
        return _TURN_RESPONSE.validate_python(response.json())

    async def stream_turn(
        self, session_id: str, request: TurnRequest
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield (event kind, payload) SSE frames, ``turn_complete`` last."""
        async with self._client.stream(
            "POST",
            f"/v1/sessions/{session_id}/turns",
            json=request.model_dump(mode="json"),
            headers={**self._headers, "accept": "text/event-stream"},
        ) as response:
            response.raise_for_status()
            kind: str | None = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    kind = line.removeprefix("event: ").strip()
                elif line.startswith("data: ") and kind is not None:
                    yield kind, json.loads(line.removeprefix("data: "))
                    kind = None

    async def get_investigation(self, investigation_id: str) -> InvestigationResponse:
        response = await self._client.get(f"/v1/investigations/{investigation_id}", headers=self._headers)
        response.raise_for_status()
        return InvestigationResponse.model_validate(response.json())

    async def get_trace(self, investigation_id: str) -> DebugTracePayload:
        response = await self._client.get(
            f"/v1/investigations/{investigation_id}/trace", headers=self._headers
        )
        response.raise_for_status()
        return DebugTracePayload.model_validate(response.json())

    async def get_session_lineage(self, session_id: str) -> SessionLineageResponse:
        response = await self._client.get(f"/v1/sessions/{session_id}/lineage", headers=self._headers)
        response.raise_for_status()
        return SessionLineageResponse.model_validate(response.json())

    async def get_capabilities(self) -> CapabilitiesResponse:
        response = await self._client.get("/v1/capabilities", headers=self._headers)
        response.raise_for_status()
        return CapabilitiesResponse.model_validate(response.json())

    async def get_portfolio(self) -> PortfolioResponse:
        response = await self._client.get("/v1/portfolio/latest", headers=self._headers)
        response.raise_for_status()
        return PortfolioResponse.model_validate(response.json())
