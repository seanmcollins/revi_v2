"""Opening or resuming the session a turn runs in."""

from __future__ import annotations

from datetime import UTC, datetime

from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.ports import (
    SessionStore,
)
from revi_investigation.application.submit_turn.types import _new_id
from revi_investigation.domain.context import (
    PackVersionRef,
)
from revi_investigation.domain.records import (
    Session,
)
from revi_investigation.domain.settings import DEFAULT_SESSION_SETTINGS, SessionSettings
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import (
    DataLoadingError,
)
from revi_kernel.watermark import DataWatermark, WatermarkEpoch


class OpenSessionService:
    """Open or join a session; new sessions pin the newest completed
    watermark and the pack version at epoch 0 (design §8.1 step 2)."""

    def __init__(
        self, sessions: SessionStore, repository: AnalyticalRepository, pack: PackPort
    ) -> None:
        self._sessions = sessions
        self._repository = repository
        self._pack = pack

    async def open(
        self,
        *,
        tenant: str,
        session_id: str | None,
        settings: SessionSettings | None = None,
    ) -> Session:
        """Open or re-join a session, optionally (re-)applying settings.

        ``settings=None`` leaves an existing session's controls exactly as
        they were: a turn re-opens its own session on every call, and a
        reconnect that quietly reset the analyst's model tier to the
        deployment default would be the worst kind of silent downgrade.
        """
        if session_id is not None:
            existing = await self._sessions.get(session_id)
            if existing is not None:
                if settings is not None and settings != existing.settings:
                    existing = existing.with_settings(settings)
                    await self._sessions.save(existing)
                return existing
        newest = await self.newest_watermark()
        session = Session(
            id=session_id if session_id is not None else _new_id("sess"),
            tenant=tenant,
            pack_version=PackVersionRef(self._pack.pack_id, self._pack.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=newest),),
            created_at=datetime.now(UTC),
            settings=settings if settings is not None else DEFAULT_SESSION_SETTINGS,
        )
        await self._sessions.save(session)
        return session

    async def newest_watermark(self) -> DataWatermark:
        watermarks = await self._repository.list_watermarks()
        if not watermarks:
            raise DataLoadingError("no completed warehouse load is available yet")
        return watermarks[-1]

    async def re_anchor(self, session: Session, newest: DataWatermark, turn_id: str) -> Session:
        """Start a new watermark epoch (§7.1) — an explicit, recorded event."""
        updated = session.with_new_epoch(
            WatermarkEpoch(index=len(session.epochs), watermark=newest, started_at_turn=turn_id)
        )
        await self._sessions.save(updated)
        return updated
