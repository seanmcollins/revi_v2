"""Soft archive, and idempotency receipts that outlive the process.

Two lifecycle gaps a demo hides. A session could never be dismissed, so
the rail grew forever; and the idempotency key was honored out of a
process-local dict, so a restart between a client's POST and its retry
executed the turn a second time — fresh model spend, a second
investigation in the DAG, two different answers to one request.

The Postgres adapters are exercised by the live deployment and the
``-m postgres`` suite; these pin the behavior the API depends on and the
in-memory fallback's fidelity to it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from revi_api.memory_stores import (
    MemoryInvestigationStore,
    MemorySessionStore,
    MemoryTurnReceiptStore,
)
from revi_api.session_lifecycle import ArchivableSessionStore, TurnReceiptStore
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import Session
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

WATERMARK = DataWatermark(
    id="wm_003",
    loaded_at=datetime(2026, 8, 3, 4, 10, tzinfo=UTC),
    newest_data_date=datetime(2026, 8, 2, tzinfo=UTC).date(),
)


def _session(session_id: str, tenant: str = "demo") -> Session:
    return Session(
        id=session_id,
        tenant=tenant,
        pack_version=PackVersionRef("base-rcm", "1.0.0"),
        epochs=(WatermarkEpoch(index=0, watermark=WATERMARK),),
        created_at=datetime(2026, 8, 8, 9, 0, tzinfo=UTC),
    )


@pytest.fixture
def sessions() -> MemorySessionStore:
    store = MemorySessionStore()
    MemoryInvestigationStore(store)  # binds the list join
    return store


class TestSoftArchive:
    async def test_the_adapter_satisfies_the_api_port(
        self, sessions: MemorySessionStore
    ) -> None:
        store: ArchivableSessionStore = sessions  # structural, no inheritance
        assert store is sessions

    async def test_an_archived_session_leaves_the_list_and_keeps_its_record(
        self, sessions: MemorySessionStore
    ) -> None:
        await sessions.save(_session("sess_a"))
        await sessions.save(_session("sess_b"))
        assert (await sessions.list_for_tenant("demo", limit=50)).total == 2

        await sessions.archive("sess_a")
        page = await sessions.list_for_tenant("demo", limit=50)
        assert [row.session_id for row in page.sessions] == ["sess_b"]
        # The total is counted over the same predicate the page selects on,
        # or a client is told its page is missing rows that are not in the
        # list at all.
        assert page.total == 1
        # Nothing was deleted: a linked conversation must not 404 because
        # somebody tidied the rail.
        assert await sessions.get("sess_a") is not None

    async def test_archiving_twice_keeps_the_first_dismissal(
        self, sessions: MemorySessionStore
    ) -> None:
        await sessions.save(_session("sess_a"))
        await sessions.archive("sess_a")
        first = sessions.archived["sess_a"]
        await sessions.archive("sess_a")
        assert sessions.archived["sess_a"] == first

    async def test_a_session_can_be_restored(self, sessions: MemorySessionStore) -> None:
        await sessions.save(_session("sess_a"))
        await sessions.archive("sess_a")
        await sessions.archive("sess_a", archived=False)
        assert (await sessions.list_for_tenant("demo", limit=50)).total == 1


class TestTurnReceipts:
    async def test_the_adapter_satisfies_the_api_port(self) -> None:
        store: TurnReceiptStore = MemoryTurnReceiptStore()
        assert await store.get("demo", "sess_a", "key") is None

    async def test_a_stored_response_replays_verbatim(self) -> None:
        store = MemoryTurnReceiptStore()
        payload = {"outcome": "answer", "investigation_id": "inv_1"}
        await store.put("demo", "sess_a", "key", payload)
        assert await store.get("demo", "sess_a", "key") == payload

    async def test_first_write_wins(self) -> None:
        """Two concurrent retries of one key converge on one answer."""
        store = MemoryTurnReceiptStore()
        await store.put("demo", "sess_a", "key", {"investigation_id": "inv_1"})
        await store.put("demo", "sess_a", "key", {"investigation_id": "inv_2"})
        stored = await store.get("demo", "sess_a", "key")
        assert stored == {"investigation_id": "inv_1"}

    async def test_the_key_is_scoped_by_tenant_and_session(self) -> None:
        store = MemoryTurnReceiptStore()
        await store.put("demo", "sess_a", "key", {"investigation_id": "inv_1"})
        assert await store.get("other", "sess_a", "key") is None
        assert await store.get("demo", "sess_b", "key") is None
