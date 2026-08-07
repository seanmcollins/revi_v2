"""Probe → DuckDB SQL compilation (pure; no driver imports).

Compilation conventions
=======================

**Views and columns.** Every probe compiles against the catalog entity's
curated base view for the resolved snapshot schema; the compiler never invents
joins. Dimensions, measures, and date bases resolve through the catalog only;
a field may fall back to a raw view column *only* when the catalog declares it
(primary key, dimension/measure/date-basis column, or certified join column).

**Time bucket.** ``probe.grain.time_bucket`` adds a group column
``CAST(date_trunc('<bucket>', <basis column>) AS DATE)`` aliased ``"week"`` /
``"month"`` / ``"day"`` with frame ref ``DimensionRef("time_bucket:<bucket>")``,
included in GROUP BY and in the default ordering.

**Measures.** Additive metrics (no denominator) produce one column named the
metric id. Ratio metrics produce exactly two component columns
``<id>__num`` / ``<id>__den`` (``revi_calculation_contracts`` naming); the
kernel computes ratio-of-sums downstream — the adapter performs **zero
arithmetic beyond aggregates**. ``Sum``/``CountDistinct`` fields resolve via
catalog ``MeasureDef`` first (inheriting the measure's governed row filter as
a ``FILTER (WHERE …)`` clause), then as a catalog-declared view column.
Contract-internal ``Filtered`` scopes and contract ``exclusions`` also become
``FILTER`` clauses.

**Determinism.** When a probe specifies no ordering, results are ordered by
the group columns ascending so identical probes yield identical frames.

**Cohort semi-joins.** ``InCohort`` compiles to
``<column> IN (SELECT entity_id FROM <pinned entity_ids_ref>)``. Same grain:
the column is the probe entity's primary key. Cross grain: allowed **iff** the
catalog declares a certified join path from the probe entity to the cohort
entity ON the cohort entity's primary key — locally that means a CLAIM cohort
may filter line/transaction/remit/denial probes through their ``claim_id``
column (and a REMIT cohort may filter denial/transaction probes through
``remit_id``); any other combination raises
``SourceCapabilityUnsupportedError``. The pinned materialization must be at
the executing watermark (``WatermarkStaleError`` otherwise — drill-down
children must reconcile with the parent's numbers).

**Snapshot probes** (the subtlest builder — see ``compile_snapshot``): state
as-of a date at the CLAIM grain. Open inventory is claims with
``service_date <= as_of`` (the claim exists as-of) AND
``(submission_date IS NULL OR submission_date <= as_of)`` AND
``(resolved_date IS NULL OR resolved_date > as_of)``. The derived measure
field ``open_balance_cents`` is billed minus money applied on/before as-of
(payments + patient payments + contractual/other adjustments, refunds added
back — each term built from the catalog's governed transaction measures).
``ar_age_bucket`` buckets ``datediff('day', <aging basis column>, as_of)``
using the bucket labels declared on the catalog dimension; the aging basis
defaults to SERVICE and honors ``probe.aging_basis``.

**Evidence grade.** Frames are DIRECT unless any uncertified catalog
dimension participates (group-by, scope, or row-evidence column), which
downgrades the whole frame to DISCOVERY (design §2.3).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from revi_calculation_contracts.contract import (
    Count,
    CountDistinct,
    Filtered,
    MeasureExpr,
    MetricContract,
    MetricKind,
    Sum,
    denominator_column,
    numerator_column,
)
from revi_catalog_contracts.model import (
    CatalogSnapshot,
    DimensionKind,
    EntityDef,
    MeasureAggregation,
    PhiClass,
)
from revi_kernel.cohort import CohortDefinition
from revi_kernel.errors import (
    BindingAmbiguousError,
    DataLoadingError,
    DateBasisInvalidError,
    GrainIncompatibleError,
    SourceCapabilityUnsupportedError,
    UnsupportedConceptError,
    WatermarkStaleError,
)
from revi_kernel.filters import And, FilterExpr, InCohort, Not, Or, Predicate, PredicateOp, is_empty
from revi_kernel.frame import FrameColumn
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import (
    AggregationProbe,
    EvidenceProbe,
    MeasurePredicate,
    Ordering,
    RowEvidenceProbe,
    SnapshotProbe,
)
from revi_kernel.refs import (
    POST,
    SERVICE,
    SUBMISSION,
    DateBasisRef,
    DimensionRef,
    EntityGrain,
    FieldRef,
    MetricRef,
)
from revi_kernel.watermark import DataWatermark

SqlParam = str | int | bool | Decimal | date | None
_Fragment = tuple[str, list[SqlParam]]

# Adapter conventions over the catalog for the claim-grain snapshot builder.
_RESOLVED_DATE_COLUMN = "resolved_date"  # derived status field on the claim base view
_OPEN_BALANCE_FIELD = "open_balance_cents"
_APPLIED_MEASURE_IDS = ("payment_cents", "patient_payment_cents", "contractual_adj_cents", "other_adj_cents")
_REVERSAL_MEASURE_IDS = ("refund_cents",)
_UNIT_BY_CATALOG_UNIT = {"cents": "money_cents", "count": "count"}

_LIKE_SPECIALS = ("\\", "%", "_")


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _escape_like(value: str) -> str:
    for special in _LIKE_SPECIALS:
        value = value.replace(special, "\\" + special)
    return value


@dataclass(frozen=True)
class CompiledQuery:
    """One executable statement plus everything needed to build the frame."""

    sql: str
    params: tuple[SqlParam, ...]
    columns: tuple[FrameColumn, ...]
    grade: EvidenceGrade
    row_limit: int | None = None  # probe.limit; the SQL requests limit+1 rows
    sample_size: int | None = None  # row-evidence sample n
    mask: tuple[PhiClass, ...] = ()  # per-column PHI mask plan (row evidence)
    single_thread: bool = False  # force threads=1 (deterministic sampling)


@dataclass
class _CompileState:
    """Mutable accumulation across the fragments of one probe compilation."""

    watermark: DataWatermark
    uncertified: bool = False
    # Extra dimension-id → SQL expression bindings (snapshot derived buckets).
    bindings: dict[str, str] = field(default_factory=dict)

    @property
    def grade(self) -> EvidenceGrade:
        return EvidenceGrade.DISCOVERY if self.uncertified else EvidenceGrade.DIRECT


@dataclass(frozen=True)
class _ValueBinding:
    expr: str
    filter_sql: str | None
    unit: str | None
    aggregation: MeasureAggregation | None


class ProbeCompiler:
    """Compiles the closed probe union against one catalog + metric resolver."""

    def __init__(
        self,
        catalog: CatalogSnapshot,
        metrics: Callable[[str], MetricContract | None],
    ) -> None:
        self._catalog = catalog
        self._metrics = metrics

    # ------------------------------------------------------------------ api

    def compile(self, probe: EvidenceProbe, *, schema: str, watermark: DataWatermark) -> CompiledQuery:
        if isinstance(probe, AggregationProbe):
            return self.compile_aggregation(probe, schema=schema, watermark=watermark)
        if isinstance(probe, SnapshotProbe):
            return self.compile_snapshot(probe, schema=schema, watermark=watermark)
        return self.compile_row_evidence(probe, schema=schema, watermark=watermark)

    def compile_cohort_selection(
        self, definition: CohortDefinition, *, watermark: DataWatermark
    ) -> tuple[EntityDef, str, tuple[SqlParam, ...]]:
        """Entity + WHERE clause + params selecting a cohort definition's rows
        (window on its basis column, then scope)."""
        entity = self._catalog.entity(definition.entity)
        if entity is None:
            raise UnsupportedConceptError(
                f"no catalog entity is bound to grain {definition.entity.value!r}",
                details={"grain": definition.entity.value},
            )
        state = _CompileState(watermark=watermark)
        parts: list[str] = []
        params: list[SqlParam] = []
        if definition.window is not None:
            basis_column = self._basis_column(entity, definition.window.basis)
            parts.append(f"{_ident(basis_column)} BETWEEN ? AND ?")
            params.extend([definition.window.range.start, definition.window.range.end])
        if not is_empty(definition.scope):
            scope_sql, scope_params = self._compile_filter(definition.scope, entity, state)
            parts.append(scope_sql)
            params.extend(scope_params)
        where_sql = " AND ".join(parts) if parts else "TRUE"
        return entity, where_sql, tuple(params)

    # ------------------------------------------------------- shared helpers

    def _entity_for(self, probe: AggregationProbe | SnapshotProbe) -> EntityDef:
        entity = self._catalog.entity(probe.grain.entity)
        if entity is None:
            raise UnsupportedConceptError(
                f"no catalog entity is bound to grain {probe.grain.entity.value!r}",
                details={"grain": probe.grain.entity.value},
            )
        return entity

    def _dimension_expr(self, dimension_id: str, entity: EntityDef, state: _CompileState) -> str:
        bound = state.bindings.get(dimension_id)
        if bound is not None:
            return bound
        dim = self._catalog.dimension(dimension_id)
        if dim is None:
            raise UnsupportedConceptError(
                f"unknown dimension {dimension_id!r}", details={"dimension": dimension_id}
            )
        if not dim.certified:
            state.uncertified = True
        if dim.kind is DimensionKind.DERIVED_BUCKET:
            raise UnsupportedConceptError(
                f"derived dimension {dimension_id!r} is not available for this probe shape",
                details={"dimension": dimension_id},
            )
        column = dim.column_for(entity.name)
        if column is None:
            raise UnsupportedConceptError(
                f"dimension {dimension_id!r} is not available at the {entity.name!r} grain",
                details={"dimension": dimension_id, "entity": entity.name},
            )
        return _ident(column)

    def _basis_column(self, entity: EntityDef, basis: DateBasisRef) -> str:
        column = entity.date_basis_column(basis)
        if column is None:
            raise DateBasisInvalidError(
                f"date basis {basis.id!r} is not bound for entity {entity.name!r}",
                details={"basis": basis.id, "entity": entity.name},
            )
        return column

    # ------------------------------------------------------------- filters

    def _compile_filter(self, expr: FilterExpr, entity: EntityDef, state: _CompileState) -> _Fragment:
        if isinstance(expr, And):
            if not expr.clauses:
                return "TRUE", []
            return self._join_clauses(expr.clauses, " AND ", entity, state)
        if isinstance(expr, Or):
            return self._join_clauses(expr.clauses, " OR ", entity, state)
        if isinstance(expr, Not):
            sql, params = self._compile_filter(expr.clause, entity, state)
            return f"(NOT {sql})", params
        if isinstance(expr, Predicate):
            return self._compile_predicate(expr, entity, state)
        return self._compile_in_cohort(expr, entity, state)

    def _join_clauses(
        self, clauses: tuple[FilterExpr, ...], sep: str, entity: EntityDef, state: _CompileState
    ) -> _Fragment:
        parts: list[str] = []
        params: list[SqlParam] = []
        for clause in clauses:
            sql, clause_params = self._compile_filter(clause, entity, state)
            parts.append(sql)
            params.extend(clause_params)
        return "(" + sep.join(parts) + ")", params

    def _compile_predicate(self, pred: Predicate, entity: EntityDef, state: _CompileState) -> _Fragment:
        column = self._dimension_expr(pred.dimension.id, entity, state)
        op = pred.op
        values = list(pred.values)
        if op is PredicateOp.IS_NULL:
            return f"({column} IS NULL)", []
        if op in (PredicateOp.EQ, PredicateOp.NEQ):
            value = values[0]
            if value is None:
                null_sql = "IS NULL" if op is PredicateOp.EQ else "IS NOT NULL"
                return f"({column} {null_sql})", []
            comparator = "=" if op is PredicateOp.EQ else "<>"
            return f"({column} {comparator} ?)", [value]
        if op in (PredicateOp.IN, PredicateOp.NOT_IN):
            placeholders = ", ".join("?" for _ in values)
            keyword = "IN" if op is PredicateOp.IN else "NOT IN"
            return f"({column} {keyword} ({placeholders}))", values
        if op is PredicateOp.RANGE:
            return f"({column} BETWEEN ? AND ?)", values
        # CONTAINS — case-insensitive substring, escaped.
        pattern = f"%{_escape_like(str(values[0]))}%"
        return f"({column} ILIKE ? ESCAPE '\\')", [pattern]

    def _compile_in_cohort(self, clause: InCohort, entity: EntityDef, state: _CompileState) -> _Fragment:
        ref = clause.cohort
        pinned = ref.pinned
        if pinned is None:
            raise SourceCapabilityUnsupportedError(
                f"cohort {ref.id!r} has no pinned materialization; materialize before probing",
                details={"cohort": ref.id},
            )
        if pinned.watermark.id != state.watermark.id:
            raise WatermarkStaleError(
                f"cohort {ref.id!r} is pinned at watermark {pinned.watermark.id!r}, "
                f"but the probe executes at {state.watermark.id!r}",
                details={"cohort": ref.id, "pinned": pinned.watermark.id, "executing": state.watermark.id},
            )
        table = pinned.entity_ids_ref
        schema_part, dot, table_part = table.partition(".")
        if not dot:
            raise SourceCapabilityUnsupportedError(
                f"cohort {ref.id!r} has a malformed entity_ids_ref", details={"cohort": ref.id}
            )
        cohort_entity = self._catalog.entity(ref.definition.entity)
        if cohort_entity is None:
            raise UnsupportedConceptError(
                f"cohort {ref.id!r} is defined at unknown grain {ref.definition.entity.value!r}",
                details={"cohort": ref.id},
            )
        if cohort_entity.name == entity.name:
            column = entity.primary_key
        else:
            join_column = self._catalog.join_column(entity.name, cohort_entity.name)
            if join_column is None or join_column != cohort_entity.primary_key:
                raise SourceCapabilityUnsupportedError(
                    f"no certified path maps a {cohort_entity.name!r} cohort onto {entity.name!r} probes",
                    details={"cohort_entity": cohort_entity.name, "probe_entity": entity.name},
                )
            column = join_column
        table_sql = f"{_ident(schema_part)}.{_ident(table_part)}"
        return f"({_ident(column)} IN (SELECT entity_id FROM {table_sql}))", []

    # ------------------------------------------------------------- measures

    def _value_binding(self, field_id: str, entity: EntityDef, state: _CompileState) -> _ValueBinding:
        extra = state.bindings.get(field_id)
        if extra is not None:
            return _ValueBinding(expr=extra, filter_sql=None, unit="money_cents", aggregation=None)
        measure = self._catalog.measure(field_id)
        if measure is not None:
            if measure.entity != entity.name:
                raise GrainIncompatibleError(
                    f"measure {field_id!r} is defined at the {measure.entity!r} grain, "
                    f"not {entity.name!r}",
                    details={"measure": field_id, "entity": entity.name},
                )
            return _ValueBinding(
                expr=_ident(measure.column),
                filter_sql=measure.filter_sql,
                unit=_UNIT_BY_CATALOG_UNIT.get(measure.unit),
                aggregation=measure.aggregation,
            )
        if field_id in self._catalog.declared_columns(entity.name):
            return _ValueBinding(expr=_ident(field_id), filter_sql=None, unit=None, aggregation=None)
        raise UnsupportedConceptError(
            f"field {field_id!r} does not resolve to a catalog measure or declared column "
            f"of entity {entity.name!r}",
            details={"field": field_id, "entity": entity.name},
        )

    def _measure_expr_sql(
        self,
        expr: MeasureExpr,
        entity: EntityDef,
        state: _CompileState,
        exclusions: FilterExpr | None,
    ) -> tuple[str, list[SqlParam], str | None]:
        """Compile one MeasureExpr into an aggregate expression + params + unit."""
        filters: list[_Fragment] = []
        inner: Sum | Count | CountDistinct
        if isinstance(expr, Filtered):
            inner = expr.inner
        else:
            inner = expr

        if isinstance(inner, Sum):
            binding = self._value_binding(inner.field.id, entity, state)
            if binding.aggregation not in (None, MeasureAggregation.SUM):
                raise UnsupportedConceptError(
                    f"measure {inner.field.id!r} aggregates by "
                    f"{binding.aggregation.value if binding.aggregation else '?'}, not sum",
                    details={"field": inner.field.id},
                )
            base = f"SUM({binding.expr})"
            unit = binding.unit
        elif isinstance(inner, CountDistinct):
            binding = self._value_binding(inner.field.id, entity, state)
            if binding.aggregation not in (None, MeasureAggregation.COUNT_DISTINCT):
                raise UnsupportedConceptError(
                    f"measure {inner.field.id!r} aggregates by "
                    f"{binding.aggregation.value if binding.aggregation else '?'}, not count_distinct",
                    details={"field": inner.field.id},
                )
            base = f"COUNT(DISTINCT {binding.expr})"
            unit = "count"
        else:
            binding = None
            base = "COUNT(*)"
            unit = "count"

        if binding is not None and binding.filter_sql is not None:
            filters.append((f"({binding.filter_sql})", []))
        if isinstance(expr, Filtered):
            filters.append(self._compile_filter(expr.where, entity, state))
        if exclusions is not None and not is_empty(exclusions):
            excl_sql, excl_params = self._compile_filter(exclusions, entity, state)
            filters.append((f"(NOT {excl_sql})", excl_params))

        if not filters:
            return base, [], unit
        filter_sql = " AND ".join(sql for sql, _ in filters)
        params = [p for _, fragment_params in filters for p in fragment_params]
        return f"{base} FILTER (WHERE {filter_sql})", params, unit

    def _resolve_contract(self, metric_id: str) -> MetricContract:
        contract = self._metrics(metric_id)
        if contract is None:
            raise UnsupportedConceptError(f"unknown metric {metric_id!r}", details={"metric": metric_id})
        return contract

    def _check_contract(
        self, contract: MetricContract, probe: AggregationProbe | SnapshotProbe, expected_kind: MetricKind
    ) -> None:
        if contract.kind is not expected_kind:
            need = "SnapshotProbe" if contract.kind is MetricKind.SNAPSHOT else "AggregationProbe"
            raise GrainIncompatibleError(
                f"metric {contract.id!r} is a {contract.kind.value} metric and needs a {need}",
                details={"metric": contract.id, "kind": contract.kind.value},
            )
        if contract.entity_grain is not probe.grain.entity:
            raise GrainIncompatibleError(
                f"metric {contract.id!r} is defined at the {contract.entity_grain.value!r} grain, "
                f"but the probe runs at {probe.grain.entity.value!r}",
                details={"metric": contract.id, "probe_grain": probe.grain.entity.value},
            )

    def _metric_select_fragments(
        self,
        measures: tuple[MetricRef, ...],
        probe: AggregationProbe | SnapshotProbe,
        entity: EntityDef,
        state: _CompileState,
        expected_kind: MetricKind,
    ) -> tuple[list[_Fragment], list[FrameColumn], set[str]]:
        """SELECT fragments for the probe's metrics (additive → one column,
        ratio → ``__num``/``__den`` component columns)."""
        fragments: list[_Fragment] = []
        columns: list[FrameColumn] = []
        additive_aliases: set[str] = set()
        for ref in measures:
            contract = self._resolve_contract(ref.id)
            self._check_contract(contract, probe, expected_kind)
            if contract.denominator is None:
                sql, params, _ = self._measure_expr_sql(
                    contract.numerator, entity, state, contract.exclusions
                )
                fragments.append((f"{sql} AS {_ident(ref.id)}", params))
                columns.append(
                    FrameColumn(
                        name=ref.id,
                        ref=ref,
                        contract_version=contract.version,
                        unit=contract.unit.value,
                    )
                )
                additive_aliases.add(ref.id)
            else:
                for alias, expr in (
                    (numerator_column(ref.id), contract.numerator),
                    (denominator_column(ref.id), contract.denominator),
                ):
                    sql, params, unit = self._measure_expr_sql(expr, entity, state, contract.exclusions)
                    fragments.append((f"{sql} AS {_ident(alias)}", params))
                    columns.append(
                        FrameColumn(name=alias, ref=ref, contract_version=contract.version, unit=unit)
                    )
        return fragments, columns, additive_aliases

    # ---------------------------------------------------------- aggregation

    def compile_aggregation(
        self, probe: AggregationProbe, *, schema: str, watermark: DataWatermark
    ) -> CompiledQuery:
        entity = self._entity_for(probe)
        state = _CompileState(watermark=watermark)
        basis_column = self._basis_column(entity, probe.window.basis)

        select_fragments: list[_Fragment] = []
        columns: list[FrameColumn] = []
        group_aliases: list[str] = []

        for dim_ref in probe.dimensions:
            expr = self._dimension_expr(dim_ref.id, entity, state)
            select_fragments.append((f"{expr} AS {_ident(dim_ref.id)}", []))
            columns.append(FrameColumn(name=dim_ref.id, ref=dim_ref))
            group_aliases.append(dim_ref.id)

        bucket = probe.grain.time_bucket
        if bucket is not None:
            alias = bucket.value  # "day" | "week" | "month"
            expr = f"CAST(date_trunc('{bucket.value}', {_ident(basis_column)}) AS DATE)"
            select_fragments.append((f"{expr} AS {_ident(alias)}", []))
            columns.append(FrameColumn(name=alias, ref=DimensionRef(f"time_bucket:{bucket.value}")))
            group_aliases.append(alias)

        metric_fragments, metric_columns, additive_aliases = self._metric_select_fragments(
            probe.measures, probe, entity, state, MetricKind.FLOW
        )
        select_fragments.extend(metric_fragments)
        columns.extend(metric_columns)

        where_fragments: list[_Fragment] = [
            (f"{_ident(basis_column)} BETWEEN ? AND ?", [probe.window.range.start, probe.window.range.end])
        ]
        if not is_empty(probe.scope):
            where_fragments.append(self._compile_filter(probe.scope, entity, state))

        having_fragments = [self._having_fragment(pred, probe, entity, state) for pred in probe.having]
        order_sql = self._order_by_sql(probe.order_by, group_aliases, additive_aliases)

        sql_parts = [
            "SELECT " + ", ".join(sql for sql, _ in select_fragments),
            f"FROM {_ident(schema)}.{_ident(entity.base_view)}",
            "WHERE " + " AND ".join(sql for sql, _ in where_fragments),
        ]
        if group_aliases:
            sql_parts.append("GROUP BY " + ", ".join(_ident(a) for a in group_aliases))
        if having_fragments:
            sql_parts.append("HAVING " + " AND ".join(sql for sql, _ in having_fragments))
        if order_sql:
            sql_parts.append(order_sql)
        if probe.limit is not None:
            sql_parts.append(f"LIMIT {probe.limit + 1}")

        params = [
            p
            for fragments in (select_fragments, where_fragments, having_fragments)
            for _, fragment_params in fragments
            for p in fragment_params
        ]
        return CompiledQuery(
            sql="\n".join(sql_parts),
            params=tuple(params),
            columns=tuple(columns),
            grade=state.grade,
            row_limit=probe.limit,
        )

    def _having_fragment(
        self,
        pred: MeasurePredicate,
        probe: AggregationProbe,
        entity: EntityDef,
        state: _CompileState,
    ) -> _Fragment:
        contract = self._resolve_contract(pred.measure.id)
        self._check_contract(contract, probe, MetricKind.FLOW)
        if contract.denominator is not None:
            raise SourceCapabilityUnsupportedError(
                f"HAVING over ratio metric {pred.measure.id!r} requires kernel-side computation",
                details={"metric": pred.measure.id},
            )
        measure_sql, measure_params, _ = self._measure_expr_sql(
            contract.numerator, entity, state, contract.exclusions
        )
        values = list(pred.values)
        if pred.op in (PredicateOp.EQ, PredicateOp.NEQ):
            comparator = "=" if pred.op is PredicateOp.EQ else "<>"
            return f"({measure_sql} {comparator} ?)", [*measure_params, *values]
        if pred.op in (PredicateOp.IN, PredicateOp.NOT_IN):
            keyword = "IN" if pred.op is PredicateOp.IN else "NOT IN"
            placeholders = ", ".join("?" for _ in values)
            return f"({measure_sql} {keyword} ({placeholders}))", [*measure_params, *values]
        # RANGE (inclusive) — IS_NULL/CONTAINS are rejected by MeasurePredicate itself.
        return f"({measure_sql} BETWEEN ? AND ?)", [*measure_params, *values]

    def _order_by_sql(
        self,
        order_by: tuple[Ordering, ...],
        group_aliases: list[str],
        additive_aliases: set[str],
    ) -> str:
        if not order_by:
            if not group_aliases:
                return ""
            return "ORDER BY " + ", ".join(f"{_ident(a)} ASC" for a in group_aliases)
        terms: list[str] = []
        for ordering in order_by:
            ref = ordering.by
            if isinstance(ref, MetricRef):
                if ref.id not in additive_aliases:
                    raise SourceCapabilityUnsupportedError(
                        f"ordering by metric {ref.id!r} requires an additive metric in the probe "
                        "(ratios are computed by the kernel)",
                        details={"metric": ref.id},
                    )
                alias = ref.id
            else:
                alias = ref.id.removeprefix("time_bucket:")
                if alias not in group_aliases:
                    raise UnsupportedConceptError(
                        f"ordering dimension {ref.id!r} is not among the probe's group columns",
                        details={"dimension": ref.id},
                    )
            terms.append(f"{_ident(alias)} {'DESC' if ordering.descending else 'ASC'}")
        return "ORDER BY " + ", ".join(terms)

    # ------------------------------------------------------------- snapshot

    def compile_snapshot(
        self, probe: SnapshotProbe, *, schema: str, watermark: DataWatermark
    ) -> CompiledQuery:
        """State as-of ``probe.as_of`` (see module docstring for semantics).

        Query shape::

            SELECT <dims / ar_age_bucket>, <aggregates>
            FROM (
              SELECT c.*, COALESCE(t.__applied_cents, 0) AS __applied_cents,
                     datediff('day', c.<aging>, ?) AS __age_days
              FROM <schema>.v_claim AS c
              LEFT JOIN (per-claim money applied on/before as_of) AS t USING (claim_id)
              WHERE <open-inventory conditions as-of> AND <scope>
            ) AS open_inventory
            GROUP BY <dims> ORDER BY <dims>

        The outer SELECT is assembled first, the inner subquery second, and
        parameters are concatenated in SQL text order (DuckDB binds ``?``
        positionally).
        """
        if probe.grain.time_bucket is not None:
            raise GrainIncompatibleError(
                "a snapshot is a point in time; time_bucket is not applicable",
                details={"time_bucket": probe.grain.time_bucket.value},
            )
        entity = self._entity_for(probe)
        if entity.primary_key != "claim_id":
            raise SourceCapabilityUnsupportedError(
                f"snapshot probes are supported at the claim grain only, not {entity.name!r}",
                details={"entity": entity.name},
            )
        if probe.as_of > watermark.newest_data_date:
            raise DataLoadingError(
                f"as-of {probe.as_of.isoformat()} is beyond watermark "
                f"{watermark.id!r} (newest data {watermark.newest_data_date.isoformat()})",
                details={"as_of": probe.as_of.isoformat(), "watermark": watermark.id},
            )
        state = _CompileState(watermark=watermark)
        aging_basis = probe.aging_basis if probe.aging_basis is not None else SERVICE
        aging_column = self._basis_column(entity, aging_basis)
        service_column = self._basis_column(entity, SERVICE)
        submission_column = self._basis_column(entity, SUBMISSION)

        needs_balance = any(
            self._references_open_balance(self._resolve_contract(ref.id)) for ref in probe.measures
        )
        if needs_balance:
            billed = self._value_binding("billed_amount_cents", entity, state)
            state.bindings[_OPEN_BALANCE_FIELD] = f"({billed.expr} - __applied_cents)"
        state.bindings.update(self._ar_bucket_binding())

        # -- outer SELECT (text order first: its params precede the inner ones)
        select_fragments: list[_Fragment] = []
        columns: list[FrameColumn] = []
        group_aliases: list[str] = []
        for dim_ref in probe.dimensions:
            expr = self._dimension_expr(dim_ref.id, entity, state)
            select_fragments.append((f"{expr} AS {_ident(dim_ref.id)}", []))
            columns.append(FrameColumn(name=dim_ref.id, ref=dim_ref))
            group_aliases.append(dim_ref.id)
        metric_fragments, metric_columns, _ = self._metric_select_fragments(
            probe.measures, probe, entity, state, MetricKind.SNAPSHOT
        )
        select_fragments.extend(metric_fragments)
        columns.extend(metric_columns)

        # -- inner subquery
        as_of = probe.as_of
        inner_parts: list[str] = ["SELECT c.*"]
        inner_params: list[SqlParam] = []
        if needs_balance:
            inner_parts[0] += ", COALESCE(t.__applied_cents, 0) AS __applied_cents"
        inner_parts[0] += f", datediff('day', c.{_ident(aging_column)}, ?) AS __age_days"
        inner_params.append(as_of)
        inner_parts.append(f"FROM {_ident(schema)}.{_ident(entity.base_view)} AS c")
        if needs_balance:
            applied_sql, applied_params = self._applied_subquery(schema, entity)
            inner_parts.append(f"LEFT JOIN ({applied_sql}) AS t USING ({_ident(entity.primary_key)})")
            inner_params.extend(applied_params)
            inner_params.append(as_of)  # post_date <= as_of inside the subquery
        open_inventory = (
            f"WHERE c.{_ident(service_column)} <= ? "
            f"AND (c.{_ident(submission_column)} IS NULL OR c.{_ident(submission_column)} <= ?) "
            f"AND (c.{_ident(_RESOLVED_DATE_COLUMN)} IS NULL OR c.{_ident(_RESOLVED_DATE_COLUMN)} > ?)"
        )
        inner_parts.append(open_inventory)
        inner_params.extend([as_of, as_of, as_of])
        if not is_empty(probe.scope):
            scope_sql, scope_params = self._compile_filter(probe.scope, entity, state)
            inner_parts.append(f"AND {scope_sql}")
            inner_params.extend(scope_params)

        sql_parts = [
            "SELECT " + ", ".join(sql for sql, _ in select_fragments),
            "FROM (" + " ".join(inner_parts) + ") AS open_inventory",
        ]
        if group_aliases:
            sql_parts.append("GROUP BY " + ", ".join(_ident(a) for a in group_aliases))
            sql_parts.append("ORDER BY " + ", ".join(f"{_ident(a)} ASC" for a in group_aliases))

        params = [p for _, fragment_params in select_fragments for p in fragment_params]
        params.extend(inner_params)
        return CompiledQuery(
            sql="\n".join(sql_parts),
            params=tuple(params),
            columns=tuple(columns),
            grade=state.grade,
        )

    def _references_open_balance(self, contract: MetricContract) -> bool:
        def walk(expr: MeasureExpr | None) -> bool:
            if expr is None:
                return False
            if isinstance(expr, Filtered):
                return walk(expr.inner)
            if isinstance(expr, (Sum, CountDistinct)):
                return expr.field.id == _OPEN_BALANCE_FIELD
            return False

        return walk(contract.numerator) or walk(contract.denominator)

    def _applied_subquery(self, schema: str, claim_entity: EntityDef) -> _Fragment:
        """Per-claim money applied on/before as-of, from the catalog's governed
        transaction measures (payments and adjustments reduce the balance;
        refunds add back). Emits one trailing ``?`` for the as-of post date."""
        txn_entity = self._catalog.entity(EntityGrain.TRANSACTION)
        if txn_entity is None:
            raise UnsupportedConceptError("no transaction entity in catalog for balance computation")
        join_column = self._catalog.join_column(txn_entity.name, claim_entity.name)
        if join_column != claim_entity.primary_key:
            raise UnsupportedConceptError(
                "catalog declares no transaction → claim join for balance computation"
            )
        post_column = self._basis_column(txn_entity, POST)
        terms: list[str] = []
        for measure_id in _APPLIED_MEASURE_IDS + _REVERSAL_MEASURE_IDS:
            measure = self._catalog.measure(measure_id)
            if measure is None or measure.entity != txn_entity.name or measure.filter_sql is None:
                raise UnsupportedConceptError(
                    f"open-balance computation requires catalog measure {measure_id!r}",
                    details={"measure": measure_id},
                )
            term = f"COALESCE(SUM({_ident(measure.column)}) FILTER (WHERE {measure.filter_sql}), 0)"
            sign = "-" if measure_id in _REVERSAL_MEASURE_IDS else "+"
            terms.append(f"{sign} {term}")
        applied = " ".join(terms).removeprefix("+ ")
        sql = (
            f"SELECT {_ident(join_column)}, {applied} AS __applied_cents "
            f"FROM {_ident(schema)}.{_ident(txn_entity.base_view)} "
            f"WHERE {_ident(post_column)} <= ? GROUP BY {_ident(join_column)}"
        )
        return sql, []  # the trailing as-of param is appended by the caller

    def _ar_bucket_binding(self) -> Mapping[str, str]:
        """``ar_age_bucket`` → CASE expression over ``__age_days``, with bucket
        edges parsed from the labels the catalog declares (ground truth)."""
        dim = self._catalog.dimension("ar_age_bucket")
        if dim is None or not dim.buckets:
            return {}
        arms: list[str] = []
        fallback: str | None = None
        for label in dim.buckets:
            if label.endswith("+"):
                fallback = label
                continue
            _, _, upper = label.partition("-")
            arms.append(f"WHEN __age_days <= {int(upper)} THEN '{label}'")
        if fallback is None:
            raise UnsupportedConceptError("ar_age_bucket catalog buckets declare no open-ended bucket")
        case = "CASE " + " ".join(arms) + f" ELSE '{fallback}' END"
        return {dim.id: case}

    # --------------------------------------------------------- row evidence

    def compile_row_evidence(
        self, probe: RowEvidenceProbe, *, schema: str, watermark: DataWatermark
    ) -> CompiledQuery:
        state = _CompileState(watermark=watermark)
        entity = self._infer_row_entity(probe.columns)

        select_fragments: list[_Fragment] = []
        columns: list[FrameColumn] = []
        mask: list[PhiClass] = []
        for field_ref in probe.columns:
            expr, phi, uncertified = self._field_binding(field_ref.id, entity)
            state.uncertified = state.uncertified or uncertified
            select_fragments.append((f"{expr} AS {_ident(field_ref.id)}", []))
            columns.append(FrameColumn(name=field_ref.id, ref=field_ref))
            mask.append(phi)

        where_fragments: list[_Fragment] = []
        if probe.window is not None:
            basis_column = self._basis_column(entity, probe.window.basis)
            where_fragments.append(
                (
                    f"{_ident(basis_column)} BETWEEN ? AND ?",
                    [probe.window.range.start, probe.window.range.end],
                )
            )
        if not is_empty(probe.scope):
            where_fragments.append(self._compile_filter(probe.scope, entity, state))

        inner = (
            "SELECT "
            + ", ".join(sql for sql, _ in select_fragments)
            + f" FROM {_ident(schema)}.{_ident(entity.base_view)}"
        )
        if where_fragments:
            inner += " WHERE " + " AND ".join(sql for sql, _ in where_fragments)
        sample = probe.sample
        sql = (
            f"SELECT * FROM ({inner}) AS sample_source\n"
            f"USING SAMPLE reservoir({int(sample.n)} ROWS) REPEATABLE ({int(sample.seed)})\n"
            "ORDER BY ALL"
        )
        params = [p for _, fragment_params in where_fragments for p in fragment_params]
        return CompiledQuery(
            sql=sql,
            params=tuple(params),
            columns=tuple(columns),
            grade=state.grade,
            sample_size=sample.n,
            mask=tuple(mask),
            single_thread=True,  # reservoir sampling is deterministic single-threaded
        )

    def _split_field(self, field_id: str) -> tuple[str | None, str]:
        head, dot, rest = field_id.partition(".")
        if dot and self._catalog.entity_named(head) is not None:
            return head, rest
        return None, field_id

    def _infer_row_entity(self, refs: tuple[FieldRef, ...]) -> EntityDef:
        """RowEvidenceProbe carries no grain; the entity is the unique one that
        binds every requested column (an explicit ``entity.`` qualifier wins)."""
        qualified = {head for head in (self._split_field(ref.id)[0] for ref in refs) if head is not None}
        if len(qualified) > 1:
            raise BindingAmbiguousError(
                f"row-evidence columns name multiple entities: {sorted(qualified)}",
                details={"entities": sorted(qualified)},
            )
        if qualified:
            entity = self._catalog.entity_named(next(iter(qualified)))
            assert entity is not None  # _split_field only returns known entities
            return entity
        candidates = [
            entity
            for entity in self._catalog.entities
            if all(self._binds(self._split_field(ref.id)[1], entity) for ref in refs)
        ]
        if not candidates:
            raise UnsupportedConceptError(
                "no catalog entity binds every requested row-evidence column",
                details={"columns": [ref.id for ref in refs]},
            )
        if len(candidates) > 1:
            raise BindingAmbiguousError(
                f"row-evidence columns bind at multiple grains {[e.name for e in candidates]}; "
                "qualify columns as entity.field",
                details={"entities": [e.name for e in candidates]},
            )
        return candidates[0]

    def _binds(self, field_id: str, entity: EntityDef) -> bool:
        try:
            self._field_binding(field_id, entity)
        except UnsupportedConceptError:
            return False
        return True

    def _field_binding(self, field_id: str, entity: EntityDef) -> tuple[str, PhiClass, bool]:
        """Resolve one row-evidence field on an entity → (SQL expression, PHI
        class, uses-uncertified-dimension flag)."""
        _, raw = self._split_field(field_id)
        dim = self._catalog.dimension(raw)
        if dim is not None:
            column = dim.column_for(entity.name)
            if column is not None:
                return _ident(column), dim.phi, not dim.certified
        measure = self._catalog.measure(raw)
        if measure is not None and measure.entity == entity.name:
            return _ident(measure.column), PhiClass.NONE, False
        basis_column = entity.date_basis_column(DateBasisRef(raw)) if raw else None
        if basis_column is not None:
            return _ident(basis_column), PhiClass.NONE, False
        if raw in self._catalog.declared_columns(entity.name):
            return _ident(raw), PhiClass.NONE, False
        raise UnsupportedConceptError(
            f"row-evidence column {field_id!r} does not resolve at the {entity.name!r} grain",
            details={"column": field_id, "entity": entity.name},
        )
