"""Postgres adapters for the Rounds ports (design §15; migration 0005).

The four stores behind the proactive surface. Same conventions as
:mod:`revi_store_postgres.stores` — sync SQLAlchemy Core off the event loop
via ``asyncio.to_thread``, JSONB for anything whose shape is a wire
contract, typed columns for what queries filter and order on.

Two decisions worth stating, because both are places a plausible
implementation would be quietly wrong:

**Ordering is by the watermark's own clock.** ``pin_results`` and ``loads``
order by ``watermark_loaded_at``, never by ``watermark_id``. That
``wm_001`` sorts before ``wm_002`` is a coincidence of this warehouse's
naming; the first deployment whose loads are identified by hash or by date
would silently diff the wrong pair of loads, and a load-over-load surface
that compares the wrong pair is worse than one that compares none.

**The spec round-trips through pydantic, not through the serde envelope.**
A pin's ``spec`` is a ``TypedInvestigationSpec`` — a wire contract, not a
domain dataclass — so it is stored as its own JSON and re-validated on the
way back. That is what makes an unknown key in a stored spec a loud
validation error rather than an attribute that silently vanishes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from revi_investigation.application.ports import (
    RoundsLead,
    RoundsLoad,
    RoundsPin,
    RoundsPinResult,
    RoundsWatch,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_store_postgres import tables as t


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


# --- pins --------------------------------------------------------------------


def _watch_json(watch: RoundsWatch | None) -> dict[str, Any] | None:
    if watch is None:
        return None
    return {
        "mode": watch.mode,
        # Decimals are stored as STRINGS: a threshold that round-trips
        # through a float is not the threshold the analyst set, and a watch
        # is a promise about a specific number.
        "value": None if watch.value is None else str(watch.value),
        "unit": watch.unit,
        "direction": watch.direction,
        "note": watch.note,
    }


def _watch_from_json(payload: Any) -> RoundsWatch | None:
    if not isinstance(payload, dict):
        return None
    return RoundsWatch(
        mode=str(payload.get("mode", "governed_default")),
        value=_decimal(payload.get("value")),
        unit=payload.get("unit"),
        direction=str(payload.get("direction", "any")),
        note=str(payload.get("note", "")),
    )


def _row_to_pin(row: sa.RowMapping) -> RoundsPin:
    return RoundsPin(
        id=row["id"],
        tenant=row["tenant"],
        label=row["label"],
        spec=TypedInvestigationSpec.model_validate(row["spec"]),
        presentation=row["presentation"],
        window_mode=row["window_mode"],
        created_at=row["created_at"],
        created_from_kind=row["created_from_kind"],
        created_from_investigation_id=row["created_from_investigation_id"],
        created_from_referent=row["created_from_referent"],
        watch=_watch_from_json(row["watch"]),
        archived_at=row["archived_at"],
        created_by=row["created_by"],
        baseline_watermark_id=row["baseline_watermark_id"],
        baseline_value=_decimal(row["baseline_value"]),
        baseline_unit=row["baseline_unit"],
        baseline_captured_at=row["baseline_captured_at"],
    )


class PostgresRoundsPinStore:
    """``RoundsPinStore`` adapter: pinned typed specs, tenant-scoped."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def save(self, pin: RoundsPin) -> None:
        await asyncio.to_thread(self._save, pin)

    async def get(self, pin_id: str) -> RoundsPin | None:
        return await asyncio.to_thread(self._get, pin_id)

    async def list_for_tenant(
        self, tenant: str, *, include_archived: bool = False
    ) -> tuple[RoundsPin, ...]:
        return await asyncio.to_thread(self._list_for_tenant, tenant, include_archived)

    async def archive(self, pin_id: str) -> None:
        await asyncio.to_thread(self._archive, pin_id)

    async def tenants_with_pins(self) -> tuple[str, ...]:
        return await asyncio.to_thread(self._tenants_with_pins)

    def _save(self, pin: RoundsPin) -> None:
        values = {
            "id": pin.id,
            "tenant": pin.tenant,
            "label": pin.label,
            "presentation": pin.presentation,
            "window_mode": pin.window_mode,
            "spec": pin.spec.model_dump(mode="json"),
            "watch": _watch_json(pin.watch),
            "created_from_kind": pin.created_from_kind,
            "created_from_investigation_id": pin.created_from_investigation_id,
            "created_from_referent": pin.created_from_referent,
            "created_by": pin.created_by,
            "created_at": _utc(pin.created_at),
            "archived_at": None if pin.archived_at is None else _utc(pin.archived_at),
            "baseline_watermark_id": pin.baseline_watermark_id,
            "baseline_value": None if pin.baseline_value is None else str(pin.baseline_value),
            "baseline_unit": pin.baseline_unit,
            "baseline_captured_at": (
                None if pin.baseline_captured_at is None else _utc(pin.baseline_captured_at)
            ),
        }
        stmt = pg_insert(t.rounds_pins).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.rounds_pins.c.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, pin_id: str) -> RoundsPin | None:
        stmt = sa.select(t.rounds_pins).where(t.rounds_pins.c.id == pin_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_pin(row)

    def _list_for_tenant(self, tenant: str, include_archived: bool) -> tuple[RoundsPin, ...]:
        stmt = sa.select(t.rounds_pins).where(t.rounds_pins.c.tenant == tenant)
        if not include_archived:
            stmt = stmt.where(t.rounds_pins.c.archived_at.is_(None))
        stmt = stmt.order_by(t.rounds_pins.c.created_at, t.rounds_pins.c.id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(_row_to_pin(row) for row in rows)

    def _archive(self, pin_id: str) -> None:
        # Idempotent, and the FIRST un-pin keeps its timestamp — the same
        # rule the session archive follows, for the same reason.
        stmt = (
            sa.update(t.rounds_pins)
            .where(t.rounds_pins.c.id == pin_id, t.rounds_pins.c.archived_at.is_(None))
            .values(archived_at=datetime.now(UTC))
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _tenants_with_pins(self) -> tuple[str, ...]:
        stmt = (
            sa.select(t.rounds_pins.c.tenant)
            .where(t.rounds_pins.c.archived_at.is_(None))
            .distinct()
            .order_by(t.rounds_pins.c.tenant)
        )
        with self._engine.connect() as conn:
            return tuple(conn.execute(stmt).scalars().all())


# --- pin results -------------------------------------------------------------


def _row_to_result(row: sa.RowMapping) -> RoundsPinResult:
    return RoundsPinResult(
        pin_id=row["pin_id"],
        tenant=row["tenant"],
        watermark_id=row["watermark_id"],
        watermark_loaded_at=row["watermark_loaded_at"],
        evaluated_at=row["evaluated_at"],
        payload=cast("dict[str, Any]", row["payload"]),
    )


class PostgresRoundsPinResultStore:
    """``RoundsPinResultStore`` adapter: one evaluated tile per (pin, load)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def put(self, result: RoundsPinResult) -> None:
        await asyncio.to_thread(self._put, result)

    async def get(self, pin_id: str, watermark_id: str) -> RoundsPinResult | None:
        return await asyncio.to_thread(self._get, pin_id, watermark_id)

    async def history(self, pin_id: str, *, limit: int = 12) -> tuple[RoundsPinResult, ...]:
        return await asyncio.to_thread(self._history, pin_id, limit)

    def _put(self, result: RoundsPinResult) -> None:
        values = {
            "pin_id": result.pin_id,
            "watermark_id": result.watermark_id,
            "tenant": result.tenant,
            "watermark_loaded_at": _utc(result.watermark_loaded_at),
            "evaluated_at": _utc(result.evaluated_at),
            "payload": dict(result.payload),
        }
        stmt = pg_insert(t.rounds_pin_results).values(values)
        # Last write wins, unlike the evidence cache: re-evaluating a load
        # is a legitimate operation (a redeployed pack, a repaired warehouse
        # snapshot), and the newer tile is the one the platform stands
        # behind. The key still asserts (pin, load), so there is never more
        # than one tile per pair.
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.rounds_pin_results.c.pin_id, t.rounds_pin_results.c.watermark_id],
            set_={
                k: v for k, v in values.items() if k not in ("pin_id", "watermark_id")
            },
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, pin_id: str, watermark_id: str) -> RoundsPinResult | None:
        stmt = sa.select(t.rounds_pin_results).where(
            t.rounds_pin_results.c.pin_id == pin_id,
            t.rounds_pin_results.c.watermark_id == watermark_id,
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_result(row)

    def _history(self, pin_id: str, limit: int) -> tuple[RoundsPinResult, ...]:
        stmt = (
            sa.select(t.rounds_pin_results)
            .where(t.rounds_pin_results.c.pin_id == pin_id)
            .order_by(
                t.rounds_pin_results.c.watermark_loaded_at.desc(),
                t.rounds_pin_results.c.watermark_id.desc(),
            )
            .limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(_row_to_result(row) for row in rows)


# --- load census -------------------------------------------------------------


def _row_to_load(row: sa.RowMapping) -> RoundsLoad:
    return RoundsLoad(
        tenant=row["tenant"],
        watermark_id=row["watermark_id"],
        watermark_loaded_at=row["watermark_loaded_at"],
        evaluated_at=row["evaluated_at"],
        payload=cast("dict[str, Any]", row["payload"]),
    )


class PostgresRoundsLoadStore:
    """``RoundsLoadStore`` adapter: the detection-feed census per load."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def put(self, load: RoundsLoad) -> None:
        await asyncio.to_thread(self._put, load)

    async def get(self, tenant: str, watermark_id: str) -> RoundsLoad | None:
        return await asyncio.to_thread(self._get, tenant, watermark_id)

    async def list_for_tenant(self, tenant: str, *, limit: int = 12) -> tuple[RoundsLoad, ...]:
        return await asyncio.to_thread(self._list_for_tenant, tenant, limit)

    def _put(self, load: RoundsLoad) -> None:
        values = {
            "tenant": load.tenant,
            "watermark_id": load.watermark_id,
            "watermark_loaded_at": _utc(load.watermark_loaded_at),
            "evaluated_at": _utc(load.evaluated_at),
            "payload": dict(load.payload),
        }
        stmt = pg_insert(t.rounds_loads).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.rounds_loads.c.tenant, t.rounds_loads.c.watermark_id],
            set_={k: v for k, v in values.items() if k not in ("tenant", "watermark_id")},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, tenant: str, watermark_id: str) -> RoundsLoad | None:
        stmt = sa.select(t.rounds_loads).where(
            t.rounds_loads.c.tenant == tenant,
            t.rounds_loads.c.watermark_id == watermark_id,
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_load(row)

    def _list_for_tenant(self, tenant: str, limit: int) -> tuple[RoundsLoad, ...]:
        stmt = (
            sa.select(t.rounds_loads)
            .where(t.rounds_loads.c.tenant == tenant)
            .order_by(
                t.rounds_loads.c.watermark_loaded_at.desc(),
                t.rounds_loads.c.watermark_id.desc(),
            )
            .limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(_row_to_load(row) for row in rows)


# --- lead lifecycle ----------------------------------------------------------


def _row_to_lead(row: sa.RowMapping) -> RoundsLead:
    return RoundsLead(
        tenant=row["tenant"],
        anomaly_id=row["anomaly_id"],
        status=row["status"],
        updated_at=row["updated_at"],
        note=row["note"],
        claimed_at_watermark=row["claimed_at_watermark"],
        baseline_cents=row["baseline_cents"],
        baseline_basis=row["baseline_basis"],
        confirming_watermarks=tuple(row["confirming_watermarks"] or ()),
        verification_note=row["verification_note"],
        history=tuple(row["history"] or ()),
    )


class PostgresRoundsLeadStore:
    """``RoundsLeadStore`` adapter: lifecycle keyed by the detector's id."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def put(self, lead: RoundsLead) -> None:
        await asyncio.to_thread(self._put, lead)

    async def get(self, tenant: str, anomaly_id: str) -> RoundsLead | None:
        return await asyncio.to_thread(self._get, tenant, anomaly_id)

    async def list_for_tenant(self, tenant: str) -> tuple[RoundsLead, ...]:
        return await asyncio.to_thread(self._list_for_tenant, tenant)

    def _put(self, lead: RoundsLead) -> None:
        values = {
            "tenant": lead.tenant,
            "anomaly_id": lead.anomaly_id,
            "status": lead.status,
            "note": lead.note,
            "updated_at": _utc(lead.updated_at),
            "claimed_at_watermark": lead.claimed_at_watermark,
            "baseline_cents": lead.baseline_cents,
            "baseline_basis": lead.baseline_basis,
            "confirming_watermarks": list(lead.confirming_watermarks),
            "verification_note": lead.verification_note,
            "history": [dict(entry) for entry in lead.history],
        }
        stmt = pg_insert(t.rounds_leads).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.rounds_leads.c.tenant, t.rounds_leads.c.anomaly_id],
            set_={k: v for k, v in values.items() if k not in ("tenant", "anomaly_id")},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, tenant: str, anomaly_id: str) -> RoundsLead | None:
        stmt = sa.select(t.rounds_leads).where(
            t.rounds_leads.c.tenant == tenant,
            t.rounds_leads.c.anomaly_id == anomaly_id,
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_lead(row)

    def _list_for_tenant(self, tenant: str) -> tuple[RoundsLead, ...]:
        stmt = (
            sa.select(t.rounds_leads)
            .where(t.rounds_leads.c.tenant == tenant)
            .order_by(t.rounds_leads.c.anomaly_id)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(_row_to_lead(row) for row in rows)
