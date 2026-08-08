"""Per-request turn-event channel (``TurnEventBus`` → SSE bridge).

``publish`` routes events to the channel bound in the *current* asyncio
context (``contextvars`` propagate into tasks at creation), so concurrent
turns never see each other's events; with no channel bound (the JSON,
non-streaming path) publishing is a no-op beyond the rolling log.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token

from revi_investigation.application.ports import TurnEvent

_CHANNEL: ContextVar[asyncio.Queue[TurnEvent] | None] = ContextVar(
    "revi_turn_event_channel", default=None
)


class ContextTurnEventBus:
    """``TurnEventBus`` implementation bridging engine events to SSE."""

    def __init__(self, log_limit: int = 500) -> None:
        self._log: list[TurnEvent] = []
        self._log_limit = log_limit

    @property
    def log(self) -> tuple[TurnEvent, ...]:
        return tuple(self._log)

    def bind(self) -> tuple[asyncio.Queue[TurnEvent], Token[asyncio.Queue[TurnEvent] | None]]:
        queue: asyncio.Queue[TurnEvent] = asyncio.Queue()
        token = _CHANNEL.set(queue)
        return queue, token

    def unbind(self, token: Token[asyncio.Queue[TurnEvent] | None]) -> None:
        _CHANNEL.reset(token)

    async def publish(self, event: TurnEvent) -> None:
        self._log.append(event)
        if len(self._log) > self._log_limit:
            del self._log[: len(self._log) - self._log_limit]
        queue = _CHANNEL.get()
        if queue is not None:
            queue.put_nowait(event)
