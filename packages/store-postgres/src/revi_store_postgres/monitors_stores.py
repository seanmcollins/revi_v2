"""Postgres adapters for the Monitors ports (design §15; migration 0005).

The four stores behind the proactive surface. Same conventions as
:mod:`revi_store_postgres.stores` — sync SQLAlchemy Core off the event loop
via ``asyncio.to_thread``, JSONB for anything whose shape is a wire
contract, typed columns for what queries filter and order on.

Two constraints:

**Ordering is by the watermark's own clock.** ``pin_results`` and ``loads``
order by ``watermark_loaded_at``, never by ``watermark_id``. That
``wm_001`` sorts before ``wm_002`` is a coincidence of this warehouse's
naming; a deployment whose loads are identified by hash or by date would
otherwise diff the wrong pair of loads.

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
    Monitor,
    MonitorsLead,
    MonitorsLoad,
    MonitorsPin,
    MonitorsPinResult,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_store_postgres import tables as t


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


# --- pins --------------------------------------------------------------------


def _monitor_json(monitor: Monitor | None) -> dict[str, Any] | None:
    if monitor is None:
        return None
    return {
        "mode": monitor.mode,
        # Decimals are stored as STRINGS: a threshold that round-trips
        # through a float is not the threshold that was set.
        "value": None if monitor.value is None else str(monitor.value),
        "unit": monitor.unit,
        "direction": monitor.direction,
        "note": monitor.note,
    }


def _monitor_from_json(payload: Any) -> Monitor | None:
    if not isinstance(payload, dict):
        return None
    return Monitor(
        mode=str(payload.get("mode", "governed_default")),
        value=_decimal(payload.get("value")),
        unit=payload.get("unit"),
        direction=str(payload.get("direction", "any")),
        note=str(payload.get("note", "")),
    )


def _row_to_pin(row: sa.RowMapping) -> MonitorsPin:
    return MonitorsPin(
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
        monitor=_monitor_from_json(row["monitor"]),
        archived_at=row["archived_at"],
        created_by=row["created_by"],
        baseline_watermark_id=row["baseline_watermark_id"],
        baseline_value=_decimal(row["baseline_value"]),
        baseline_unit=row["baseline_unit"],
        baseline_captured_at=row["baseline_captured_at"],
    )


class PostgresMonitorsPinStore:
    """``MonitorsPinStore`` adapter: pinned typed specs, tenant-scoped."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def save(self, pin: MonitorsPin) -> None:
        await asyncio.to_thread(self._save, pin)

    async def get(self, pin_id: str) -> MonitorsPin | None:
        return await asyncio.to_thread(self._get, pin_id)

    async def list_for_tenant(
        self, tenant: str, *, include_archived: bool = False
    ) -> tuple[MonitorsPin, ...]:
        return await asyncio.to_thread(self._list_for_tenant, tenant, include_archived)

    async def archive(self, pin_id: str) -> None:
        await asyncio.to_thread(self._archive, pin_id)

    async def tenants_with_pins(self) -> tuple[str, ...]:
        return await asyncio.to_thread(self._tenants_with_pins)

    def _save(self, pin: MonitorsPin) -> None:
        values = {
            "id": pin.id,
            "tenant": pin.tenant,
            "label": pin.label,
            "presentation": pin.presentation,
            "window_mode": pin.window_mode,
            "spec": pin.spec.model_dump(mode="json"),
            "monitor": _monitor_json(pin.monitor),
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
        stmt = pg_insert(t.monitors_pins).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.monitors_pins.c.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, pin_id: str) -> MonitorsPin | None:
        stmt = sa.select(t.monitors_pins).where(t.monitors_pins.c.id == pin_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_pin(row)

    def _list_for_tenant(self, tenant: str, include_archived: bool) -> tuple[MonitorsPin, ...]:
        stmt = sa.select(t.monitors_pins).where(t.monitors_pins.c.tenant == tenant)
        if not include_archived:
            stmt = stmt.where(t.monitors_pins.c.archived_at.is_(None))
        stmt = stmt.order_by(t.monitors_pins.c.created_at, t.monitors_pins.c.id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(_row_to_pin(row) for row in rows)

    def _archive(self, pin_id: str) -> None:
        # Idempotent, and the FIRST un-pin keeps its timestamp — the same
        # rule the session archive follows.
        stmt = (
            sa.update(t.monitors_pins)
            .where(t.monitors_pins.c.id == pin_id, t.monitors_pins.c.archived_at.is_(None))
            .values(archived_at=datetime.now(UTC))
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _tenants_with_pins(self) -> tuple[str, ...]:
        stmt = (
            sa.select(t.monitors_pins.c.tenant)
            .where(t.monitors_pins.c.archived_at.is_(None))
            .distinct()
            .order_by(t.monitors_pins.c.tenant)
        )
        with self._engine.connect() as conn:
            return tuple(conn.execute(stmt).scalars().all())


# --- pin results -------------------------------------------------------------


def _row_to_result(row: sa.RowMapping) -> MonitorsPinResult:
    return MonitorsPinResult(
        pin_id=row["pin_id"],
        tenant=row["tenant"],
        watermark_id=row["watermark_id"],
        watermark_loaded_at=row["watermark_loaded_at"],
        evaluated_at=row["evaluated_at"],
        payload=cast("dict[str, Any]", row["payload"]),
    )


class PostgresMonitorsPinResultStore:
    """``MonitorsPinResultStore`` adapter: one evaluated tile per (pin, load)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def put(self, result: MonitorsPinResult) -> None:
        await asyncio.to_thread(self._put, result)

    async def get(self, pin_id: str, watermark_id: str) -> MonitorsPinResult | None:
        return await asyncio.to_thread(self._get, pin_id, watermark_id)

    async def history(self, pin_id: str, *, limit: int = 12) -> tuple[MonitorsPinResult, ...]:
        return await asyncio.to_thread(self._history, pin_id, limit)

    def _put(self, result: MonitorsPinResult) -> None:
        values = {
            "pin_id": result.pin_id,
            "watermark_id": result.watermark_id,
            "tenant": result.tenant,
            "watermark_loaded_at": _utc(result.watermark_loaded_at),
            "evaluated_at": _utc(result.evaluated_at),
            "payload": dict(result.payload),
        }
        stmt = pg_insert(t.monitors_pin_results).values(values)
        # Last write wins, unlike the evidence cache: re-evaluating a load
        # is legitimate (a redeployed pack, a repaired warehouse snapshot)
        # and the newer tile is the authoritative one. The key still
        # asserts (pin, load), so there is never more than one tile per
        # pair.
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.monitors_pin_results.c.pin_id, t.monitors_pin_results.c.watermark_id],
            set_={
                k: v for k, v in values.items() if k not in ("pin_id", "watermark_id")
            },
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, pin_id: str, watermark_id: str) -> MonitorsPinResult | None:
        stmt = sa.select(t.monitors_pin_results).where(
            t.monitors_pin_results.c.pin_id == pin_id,
            t.monitors_pin_results.c.watermark_id == watermark_id,
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_result(row)

    def _history(self, pin_id: str, limit: int) -> tuple[MonitorsPinResult, ...]:
        stmt = (
            sa.select(t.monitors_pin_results)
            .where(t.monitors_pin_results.c.pin_id == pin_id)
            .order_by(
                t.monitors_pin_results.c.watermark_loaded_at.desc(),
                t.monitors_pin_results.c.watermark_id.desc(),
            )
            .limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(_row_to_result(row) for row in rows)


# --- load census -------------------------------------------------------------


def _row_to_load(row: sa.RowMapping) -> MonitorsLoad:
    return MonitorsLoad(
        tenant=row["tenant"],
        watermark_id=row["watermark_id"],
        watermark_loaded_at=row["watermark_loaded_at"],
        evaluated_at=row["evaluated_at"],
        payload=cast("dict[str, Any]", row["payload"]),
    )


class PostgresMonitorsLoadStore:
    """``MonitorsLoadStore`` adapter: the detection-feed census per load."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def put(self, load: MonitorsLoad) -> None:
        await asyncio.to_thread(self._put, load)

    async def get(self, tenant: str, watermark_id: str) -> MonitorsLoad | None:
        return await asyncio.to_thread(self._get, tenant, watermark_id)

    async def list_for_tenant(self, tenant: str, *, limit: int = 12) -> tuple[MonitorsLoad, ...]:
        return await asyncio.to_thread(self._list_for_tenant, tenant, limit)

    def _put(self, load: MonitorsLoad) -> None:
        values = {
            "tenant": load.tenant,
            "watermark_id": load.watermark_id,
            "watermark_loaded_at": _utc(load.watermark_loaded_at),
            "evaluated_at": _utc(load.evaluated_at),
            "payload": dict(load.payload),
        }
        stmt = pg_insert(t.monitors_loads).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.monitors_loads.c.tenant, t.monitors_loads.c.watermark_id],
            set_={k: v for k, v in values.items() if k not in ("tenant", "watermark_id")},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, tenant: str, watermark_id: str) -> MonitorsLoad | None:
        stmt = sa.select(t.monitors_loads).where(
            t.monitors_loads.c.tenant == tenant,
            t.monitors_loads.c.watermark_id == watermark_id,
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_load(row)

    def _list_for_tenant(self, tenant: str, limit: int) -> tuple[MonitorsLoad, ...]:
        stmt = (
            sa.select(t.monitors_loads)
            .where(t.monitors_loads.c.tenant == tenant)
            .order_by(
                t.monitors_loads.c.watermark_loaded_at.desc(),
                t.monitors_loads.c.watermark_id.desc(),
            )
            .limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(_row_to_load(row) for row in rows)


# --- lead lifecycle ----------------------------------------------------------


def _row_to_lead(row: sa.RowMapping) -> MonitorsLead:
    return MonitorsLead(
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


class PostgresMonitorsLeadStore:
    """``MonitorsLeadStore`` adapter: lifecycle keyed by the detector's id."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def put(self, lead: MonitorsLead) -> None:
        await asyncio.to_thread(self._put, lead)

    async def get(self, tenant: str, anomaly_id: str) -> MonitorsLead | None:
        return await asyncio.to_thread(self._get, tenant, anomaly_id)

    async def list_for_tenant(self, tenant: str) -> tuple[MonitorsLead, ...]:
        return await asyncio.to_thread(self._list_for_tenant, tenant)

    def _put(self, lead: MonitorsLead) -> None:
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
        stmt = pg_insert(t.monitors_leads).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.monitors_leads.c.tenant, t.monitors_leads.c.anomaly_id],
            set_={k: v for k, v in values.items() if k not in ("tenant", "anomaly_id")},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, tenant: str, anomaly_id: str) -> MonitorsLead | None:
        stmt = sa.select(t.monitors_leads).where(
            t.monitors_leads.c.tenant == tenant,
            t.monitors_leads.c.anomaly_id == anomaly_id,
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_lead(row)

    def _list_for_tenant(self, tenant: str) -> tuple[MonitorsLead, ...]:
        stmt = (
            sa.select(t.monitors_leads)
            .where(t.monitors_leads.c.tenant == tenant)
            .order_by(t.monitors_leads.c.anomaly_id)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(_row_to_lead(row) for row in rows)
