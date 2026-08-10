"""The API's fallback in-memory stores against the shared port contract.

These stores are what a deployment runs on when ``REVI_DATABASE_URL`` is
unset — which is every local demo and every CI run of the API suite. They
were the one implementation of the seven application-state ports with no
contract coverage at all: the Postgres adapters were held to the suite in
:mod:`revi_store_postgres.tests`, and the stores actually serving the demo
were held to nothing.

Same suite, no database required, so a divergence between the two backings
(ordering, tenant scoping, round-trip fidelity) fails here first.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from revi_api.memory_stores import (
    MemoryCohortStore,
    MemoryEvidenceCache,
    MemoryFrameStore,
    MemoryInvestigationStore,
    MemoryReferentRegistryStore,
    MemoryRoundsLeadStore,
    MemoryRoundsLoadStore,
    MemoryRoundsPinResultStore,
    MemoryRoundsPinStore,
    MemorySessionStore,
    MemoryTraceStore,
)
from revi_investigation.application.ports import EMPTY_SESSION_TITLE
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import Session
from revi_kernel.watermark import DataWatermark, WatermarkEpoch
from revi_testing.rounds_store_contract import RoundsStoreContract, RoundsStores
from revi_testing.store_contract import ApplicationStateStoreContract, ApplicationStores


class TestMemoryApplicationStores(ApplicationStateStoreContract):
    @pytest.fixture
    def stores(self) -> ApplicationStores:
        sessions = MemorySessionStore()
        return ApplicationStores(
            sessions=sessions,
            referents=MemoryReferentRegistryStore(),
            # Constructing this binds the session↔investigation join the
            # list read needs; the wiring builds the pair the same way.
            investigations=MemoryInvestigationStore(sessions),
            traces=MemoryTraceStore(),
            frames=MemoryFrameStore(),
            cohorts=MemoryCohortStore(),
            evidence=MemoryEvidenceCache(),
        )


class TestMemorySessionListJoin:
    async def test_a_session_store_with_no_investigations_still_lists(self) -> None:
        """An unbound session store lists sessions with no turn counts
        rather than raising: the API's fallback wiring always binds the
        pair, but a store built alone must degrade, not explode."""
        sessions = MemorySessionStore()
        await sessions.save(
            Session(
                id="sess_solo",
                tenant="t_solo",
                pack_version=PackVersionRef(pack_id="base-rcm", version="1.0.0"),
                epochs=(
                    WatermarkEpoch(
                        index=0,
                        watermark=DataWatermark(
                            id="wm_1",
                            loaded_at=datetime(2026, 8, 8, tzinfo=UTC),
                            newest_data_date=date(2026, 8, 7),
                        ),
                    ),
                ),
                created_at=datetime(2026, 8, 8, tzinfo=UTC),
            )
        )

        page = await sessions.list_for_tenant("t_solo", limit=10)

        assert [(row.session_id, row.turn_count) for row in page.sessions] == [
            ("sess_solo", 0)
        ]
        assert page.sessions[0].title == EMPTY_SESSION_TITLE


class TestMemoryRoundsStores(RoundsStoreContract):
    """The API's fallback Rounds stores against the shared port contract.

    Same reason the application-state stores are held to theirs: these are
    what every local demo and every CI run of the API suite actually uses,
    and a divergence from the Postgres adapters (ordering, tenant scoping,
    spec fidelity, soft archive) has to fail here first.
    """

    @pytest.fixture
    def rounds(self) -> RoundsStores:
        return RoundsStores(
            pins=MemoryRoundsPinStore(),
            results=MemoryRoundsPinResultStore(),
            loads=MemoryRoundsLoadStore(),
            leads=MemoryRoundsLeadStore(),
        )
