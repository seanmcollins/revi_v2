"""Session lifecycle ports the API owns: archive, and turn receipts.

Two gaps that are invisible in a demo and disqualifying in a deployment.

**Dismissing a session.** ``GET /v1/sessions`` grew forever: there was no
way to remove a session from the rail, so the only tidy-up available was
starting another one. Archiving is SOFT and stays that way: a session owns
investigations, traces, frames and cohorts that other reads resolve
through it, so the row is kept and fetchable by id — the list simply stops
showing it. A hard delete would turn a tidy-up into dangling lineage, and
"delete my data" is a different feature with different obligations.

**Idempotency that survives a restart.** The API honored
``TurnRequest.idempotency_key`` out of a process-local dict, which is
correct for exactly one process's lifetime. A restart between a client's
POST and its retry — or a second worker behind a load balancer — turned
"return the stored response" into a second EXECUTION of the same turn:
fresh model spend, a second investigation in the session DAG, and two
different answers to one request. The receipt now lives in the session
store beside the sessions it belongs to.

Both are declared here, as Protocols over the adapters the composition
root wires, rather than added to
``revi_investigation.application.ports``: the engine neither archives
sessions nor knows what an idempotency key is, and a port it does not use
does not belong in its application layer. Protocols are structural, so the
memory and Postgres session stores satisfy :class:`ArchivableSessionStore`
by having the method.
"""

from __future__ import annotations

from typing import Any, Protocol

from revi_investigation.application.ports import SessionPage
from revi_investigation.domain.records import Session


class ArchivableSessionStore(Protocol):
    """``SessionStore`` plus soft archive.

    Restates the port's three methods so an adapter typed as this is
    accepted anywhere the engine asks for a ``SessionStore`` — structural
    typing, no inheritance, no edit to the engine's port module.
    """

    async def get(self, session_id: str) -> Session | None: ...

    async def save(self, session: Session) -> None: ...

    async def list_for_tenant(self, tenant: str, *, limit: int) -> SessionPage: ...

    async def archive(self, session_id: str, *, archived: bool = True) -> None:
        """Dismiss (or restore) a session. Idempotent; deletes nothing."""
        ...


class TurnReceiptStore(Protocol):
    """Executed turns, keyed by ``(tenant, session, idempotency key)``.

    The value is the serialized ``TurnResponse``: a replay returns the
    ORIGINAL payload rather than a re-run that could differ from it. First
    write wins, so two concurrent retries of one key converge on one
    answer instead of racing to overwrite each other.
    """

    async def get(
        self, tenant: str, session_id: str, key: str
    ) -> dict[str, Any] | None: ...

    async def put(
        self, tenant: str, session_id: str, key: str, response: dict[str, Any]
    ) -> None: ...
