"""Divergence explainer.

A bare "published 87,997,568, derived 158,979,695" is a bug report nobody can
act on. When a cell diverges, this module asks one further question — *is the
published number a correct reading of this contract over some context the
answer did not disclose?* — by re-deriving it over a small, fixed vocabulary
of candidate windows and every basis the contract allows.

The vocabulary is not invented: it is the window shapes the base pack's own
playbooks declare (``window: {quantity: N, unit: week|month, mode:
full_periods}`` in ``packs/base-rcm/playbooks/*.yaml``), anchored on the
watermark's newest data date and on the answer's own window end.

An explanation NEVER turns a divergence into a pass. The cell stays diverged;
the explanation just names the context that reproduces the number, so the fix
queue gets "this finding published a 2026-07-06..2026-08-02 number under a
2026-07-01..2026-07-31 label" instead of "numbers disagree".
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from revi_warehouse_diff.deriver import AuditContext, DerivationRun, Underivable
from revi_warehouse_diff.governed import MetricContract

MAX_PERIODS = 8


@dataclass(frozen=True)
class CandidateWindow:
    label: str
    start: dt.date
    end: dt.date


def _last_full_week_end(anchor: dt.date) -> dt.date:
    """The last ISO week end (Sunday) on or before ``anchor``."""
    return anchor - dt.timedelta(days=(anchor.weekday() + 1) % 7)


def _last_full_month_end(anchor: dt.date) -> dt.date:
    last_day = calendar.monthrange(anchor.year, anchor.month)[1]
    if anchor.day == last_day:
        return anchor
    first = anchor.replace(day=1)
    previous = first - dt.timedelta(days=1)
    return previous


def _month_start_back(end: dt.date, quantity: int) -> dt.date:
    year, month = end.year, end.month
    month -= quantity - 1
    while month <= 0:
        month += 12
        year -= 1
    return dt.date(year, month, 1)


def candidate_windows(anchors: tuple[dt.date, ...]) -> list[CandidateWindow]:
    """The playbook window vocabulary, anchored at each supplied date."""
    out: list[CandidateWindow] = []
    seen: set[tuple[dt.date, dt.date]] = set()
    for anchor in anchors:
        week_end = _last_full_week_end(anchor)
        for quantity in range(1, MAX_PERIODS + 1):
            start = week_end - dt.timedelta(days=7 * quantity - 1)
            if (start, week_end) not in seen:
                seen.add((start, week_end))
                out.append(
                    CandidateWindow(f"{quantity} full week(s) to {week_end}", start, week_end)
                )
        month_end = _last_full_month_end(anchor)
        for quantity in range(1, 7):
            start = _month_start_back(month_end, quantity)
            if (start, month_end) not in seen:
                seen.add((start, month_end))
                out.append(
                    CandidateWindow(f"{quantity} full month(s) to {month_end}", start, month_end)
                )
    return out


def explain(
    run: DerivationRun,
    metric_id: str,
    contract: MetricContract,
    ctx: AuditContext,
    published: Any,
    anchors: tuple[dt.date, ...],
) -> str:
    """Name a context that reproduces ``published``, or say nothing did."""
    try:
        target = Decimal(str(published))
    except Exception:
        return ""
    exact = contract.unit in ("money_cents", "count")
    bases = run.deriver.bound_bases(contract)
    for window in candidate_windows(anchors):
        for basis in bases:
            probe = replace(
                ctx,
                window_start=window.start,
                window_end=window.end,
                force_basis=basis,
            )
            try:
                derivation = run.derive(metric_id, probe)
            except Underivable:
                continue
            except Exception:
                continue
            ok = (
                derivation.value == target
                if exact
                else abs(derivation.value - target) <= Decimal("1e-6")
            )
            if ok:
                return (
                    f"reproduced exactly over {window.start}..{window.end} "
                    f"({window.label}) on the {basis!r} basis — the answer published "
                    f"{ctx.window_start}..{ctx.window_end} on {ctx.published_basis!r}"
                )
    return _filter_value_diagnosis(run, contract, ctx)


def _filter_value_diagnosis(
    run: DerivationRun, contract: MetricContract, ctx: AuditContext
) -> str:
    """Does a published filter value simply not exist in the data?

    A context header that publishes ``payer_type = 'Medicaid'`` when the
    certified value domain is ``MEDICAID`` describes a population nobody can
    re-select. The audit path finds an empty population; this says why.
    """
    try:
        entity = run.deriver.entity_of(contract)
        view = run.deriver.catalog.base_view(entity)
    except Exception:
        return ""
    if view is None:
        return ""
    notes: list[str] = []
    for predicate in (*ctx.scope, *ctx.slice):
        if predicate.op not in ("eq", "in") or not predicate.values:
            continue
        column = run.deriver.catalog.dimension_column(predicate.dimension, entity)
        if column is None:
            continue
        for value in predicate.values:
            if not isinstance(value, str):
                continue
            literal = value.replace("'", "''")
            exact = run.execute(
                f"SELECT count(*) FROM {ctx.schema}.{view} WHERE {column} = '{literal}'"
            )
            if exact:
                continue
            insensitive = run.execute(
                f"SELECT count(*) FROM {ctx.schema}.{view} "
                f"WHERE lower({column}) = lower('{literal}')"
            )
            if insensitive:
                notes.append(
                    f"published filter {predicate.dimension}={value!r} matches NO row of "
                    f"{ctx.schema}.{view}.{column} exactly, but {insensitive} rows "
                    f"case-insensitively — the published context value is not the data's value"
                )
    return "; ".join(notes)
