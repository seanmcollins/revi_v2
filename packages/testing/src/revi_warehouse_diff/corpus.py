"""The stored corpus: every investigation the product has published, read
straight out of Postgres with raw SQL.

Why Postgres and not the HTTP API
---------------------------------
``/v1/investigations`` has no list endpoint (only
``/v1/investigations/{id}``), so a corpus replay would have to get its ids
from the database regardless. Reading the two trace tables directly with
``psycopg`` is therefore both simpler *and* stricter on independence: the
audit path touches no product code at all, not even the store's ORM models.
The trade is that this module has to decode the store's serde envelope by
hand; the mutation self-test exercises that decoding.

What is read, and what it is used for
-------------------------------------
* ``revi_trace.investigations.spec`` → the answer's **published context**:
  window, date basis, comparison window, scope filters, cohort, watermark.
  This is the same context the API renders as the answer's context header.
* ``revi_trace.investigations.findings`` → the **published values** under
  audit.
* ``revi_trace.frames`` → the **coordinates** of the published cells (which
  payer, which CARC, which month). Only the group-key columns are taken from
  a frame; every measured value is recomputed from the warehouse. Each
  resolved coordinate is then re-checked against the finding's own published
  title so the harness is never quietly auditing a cell the answer did not
  claim.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

from revi_warehouse_diff.deriver import CohortPin, Predicate

DEFAULT_DSN = "postgresql://revi:revi_dev_only@localhost:5433/revi"


def resolve_dsn(explicit: str | None = None) -> str:
    """The corpus DSN: explicit, else ``REVI_DATABASE_URL``, else the dev default."""
    if explicit:
        return explicit
    raw = os.environ.get("REVI_DATABASE_URL")
    if not raw:
        return DEFAULT_DSN
    # SQLAlchemy-style URLs carry a driver suffix psycopg does not accept.
    return raw.replace("postgresql+psycopg://", "postgresql://")


# --------------------------------------------------------------------------
# serde envelope decoding
# --------------------------------------------------------------------------


def plain(node: Any) -> Any:
    """Strip the store's serde envelope: enums, decimals, dates, types."""
    if isinstance(node, dict):
        if "__enum__" in node:
            return node.get("value")
        if "__decimal__" in node:
            return Decimal(node["__decimal__"])
        if "__date__" in node:
            return dt.date.fromisoformat(node["__date__"])
        if "__datetime__" in node:
            return dt.datetime.fromisoformat(node["__datetime__"])
        if "__map__" in node:
            return {k: plain(v) for k, v in node["__map__"].items()}
        if "__list__" in node:
            return [plain(v) for v in node["__list__"]]
        return {k: plain(v) for k, v in node.items() if k != "__type__"}
    if isinstance(node, list):
        return [plain(v) for v in node]
    return node


def _predicates(scope: Any) -> tuple[Predicate, ...]:
    """Flatten a published scope expression into AND-ed predicates.

    A scope that is not a pure conjunction of predicates is *not* flattened
    into one — it is returned as an empty tuple with the caller left to refuse,
    because silently dropping an OR would fabricate a wider population.
    """
    node = plain(scope)
    if not node:
        return ()
    if "clauses" in node:
        out: list[Predicate] = []
        for clause in node["clauses"]:
            out.extend(_predicates_from_plain(clause))
        return tuple(out)
    return tuple(_predicates_from_plain(node))


def _predicates_from_plain(node: Any) -> list[Predicate]:
    if not isinstance(node, dict):
        return []
    if "clauses" in node:
        out: list[Predicate] = []
        for clause in node["clauses"]:
            out.extend(_predicates_from_plain(clause))
        return out
    if "dimension" not in node:
        # OR / NOT / InCohort — not expressible as a flat conjunction.
        raise UnsupportedScope(str(sorted(node))[:120])
    dimension = node["dimension"]
    dim_id = dimension["id"] if isinstance(dimension, dict) else str(dimension)
    return [
        Predicate(
            dimension=str(dim_id),
            op=str(node["op"]),
            values=tuple(node.get("values", ())),
        )
    ]


class UnsupportedScope(Exception):
    """The published scope is not a flat conjunction of predicates."""


# --------------------------------------------------------------------------
# corpus model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedFinding:
    referent: str
    title: str
    statement: str
    metric_ids: tuple[str, ...]
    values: tuple[tuple[str, Any], ...]
    grade: str


@dataclass(frozen=True)
class FrameCell:
    """One published cell coordinate + the value the product published for it."""

    probe: str
    window: str  # "current" | "prior"
    metric_id: str
    coordinate: tuple[Predicate, ...]
    time_bucket: tuple[str, dt.date] | None
    published: Any
    labels: tuple[str, ...]


@dataclass(frozen=True)
class StoredInvestigation:
    id: str
    session_id: str
    turn_class: str
    status: str
    question: str | None
    watermark_id: str
    window_start: dt.date
    window_end: dt.date
    basis: str
    comparison: tuple[dt.date, dt.date] | None
    scope: tuple[Predicate, ...]
    scope_error: str | None
    cohort: CohortPin | None
    findings: tuple[PublishedFinding, ...]
    cells: tuple[FrameCell, ...]
    #: When the product published this answer. Read so a divergence can be
    #: dated against the disclosure contract in force when it was written —
    #: see :mod:`revi_warehouse_diff.archaeology`.
    created_at: dt.datetime | None = None


_DERIVED_PROBE_SUFFIXES = ("__compare", "__rank")


def _probe_window(probe: str) -> str | None:
    """Which published window a probe read, or None when it is a derived view.

    ``__compare`` / ``__rank`` frames are calculation-layer views over probe
    frames, not probe results; they carry no window of their own and are not
    audit inputs.
    """
    name = probe
    for suffix in _DERIVED_PROBE_SUFFIXES:
        if name.endswith(suffix):
            return None
    return "prior" if name.endswith("__prior") else "current"


def _cells(probe: str, frame: dict[str, Any]) -> list[FrameCell]:
    window = _probe_window(probe)
    if window is None:
        return []
    value = frame["value"]
    columns = value["schema"]["columns"]
    dimension_columns: list[tuple[int, str]] = []
    metric_columns: list[tuple[int, str]] = []
    for index, column in enumerate(columns):
        ref = column["ref"]
        if ref.get("__type__") == "DimensionRef":
            dimension_columns.append((index, str(ref["id"])))
        # A ratio frame publishes three columns per metric — ``<id>__num``,
        # ``<id>__den`` and ``<id>`` — plus ``<id>__rank`` on ranked frames.
        # Only the column NAMED for the metric carries the metric's value.
        elif ref.get("__type__") == "MetricRef" and str(column.get("name")) == str(ref["id"]):
            metric_columns.append((index, str(ref["id"])))
    out: list[FrameCell] = []
    for row in value["rows"]:
        coordinate: list[Predicate] = []
        bucket: tuple[str, dt.date] | None = None
        labels: list[str] = []
        for index, dim_id in dimension_columns:
            raw = row[index]
            if dim_id.startswith("time_bucket:"):
                unit = dim_id.split(":", 1)[1]
                parsed = plain(raw)
                if isinstance(parsed, str):
                    parsed = dt.date.fromisoformat(parsed)
                if isinstance(parsed, dt.datetime):
                    parsed = parsed.date()
                if isinstance(parsed, dt.date):
                    bucket = (unit, parsed)
                continue
            if raw is None:
                coordinate.append(Predicate(dim_id, "is_null", ()))
            else:
                coordinate.append(Predicate(dim_id, "eq", (raw,)))
                labels.append(str(raw))
        for index, metric_id in metric_columns:
            out.append(
                FrameCell(
                    probe=probe,
                    window=window,
                    metric_id=metric_id,
                    coordinate=tuple(coordinate),
                    time_bucket=bucket,
                    published=plain(row[index]),
                    labels=tuple(labels),
                )
            )
    return out


def load_corpus(dsn: str | None = None, limit: int | None = None) -> list[StoredInvestigation]:
    """Every stored investigation with its published context, findings and cells."""
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        sql = (
            "SELECT id, session_id, turn_class, status, question, spec, findings, "
            "frame_refs, created_at "
            "FROM revi_trace.investigations ORDER BY created_at, id"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        frames = dict(conn.execute("SELECT key, frame FROM revi_trace.frames").fetchall())

    out: list[StoredInvestigation] = []
    for (
        iid,
        session_id,
        turn_class,
        status,
        question,
        spec,
        findings,
        frame_refs,
        created_at,
    ) in rows:
        context = spec["value"]["context"]
        window = plain(context["window"])
        comparison_node = context.get("comparison")
        comparison: tuple[dt.date, dt.date] | None = None
        if comparison_node:
            comparison_window = plain(comparison_node["window"])
            comparison = (
                comparison_window["range"]["start"],
                comparison_window["range"]["end"],
            )
        scope: tuple[Predicate, ...] = ()
        scope_error: str | None = None
        try:
            scope = _predicates(context["scope"])
        except UnsupportedScope as exc:
            scope_error = f"scope_not_conjunctive: {exc}"

        cohort: CohortPin | None = None
        cohort_node = context.get("cohort")
        if cohort_node:
            pinned = cohort_node.get("pinned") or {}
            definition = cohort_node.get("definition") or {}
            entity = plain(definition.get("entity")) if definition else None
            ref = pinned.get("entity_ids_ref")
            if entity and ref:
                cohort = CohortPin(entity=str(entity), entity_ids_ref=str(ref))

        published_findings = tuple(
            PublishedFinding(
                referent=str(plain(f["referent"])["value"]),
                title=str(f.get("title") or ""),
                statement=str(f.get("statement") or ""),
                metric_ids=tuple(str(m["id"]) for m in f.get("metric_refs", ())),
                values=tuple((str(name), plain(value)) for name, value in f.get("values", ())),
                grade=str(plain(f.get("grade")) or ""),
            )
            for f in findings["value"]
        )

        cells: list[FrameCell] = []
        for ref in frame_refs:
            frame = frames.get(ref)
            if frame is None:
                continue
            cells.extend(_cells(ref.split(":", 1)[1], frame))

        out.append(
            StoredInvestigation(
                id=str(iid),
                session_id=str(session_id),
                turn_class=str(turn_class),
                status=str(status),
                question=question,
                watermark_id=str(plain(context["watermark"])["id"]),
                window_start=window["range"]["start"],
                window_end=window["range"]["end"],
                basis=str(window["basis"]["id"]),
                comparison=comparison,
                scope=scope,
                scope_error=scope_error,
                cohort=cohort,
                findings=published_findings,
                cells=tuple(cells),
                created_at=created_at,
            )
        )
    return out
