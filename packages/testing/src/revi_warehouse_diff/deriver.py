"""The naive SQL deriver — the audit path's whole brain.

Input: a metric contract id plus the context a published answer discloses
(window, date basis, scope filters, cohort, watermark, and the finding's own
slice). Output: the dumbest correct SQL that satisfies the contract's
definition, and nothing else.

Design rules, in force everywhere in this module:

* **Read only governed inputs.** Contract YAML, catalog YAML, the published
  context. Never the compiler, never the planner, never a stored frame value.
* **One aggregate per contract side.** A ratio compiles to two independent
  scalar queries; the division happens here, in Python, over the two scalars.
  There is no clever single-pass SQL, on purpose — clever is how a bug hides.
* **Refuse loudly.** Anything v1 cannot derive raises :class:`Underivable`
  with a machine-readable reason, and the caller counts it. There is no
  silent skip and no "close enough".

Date-basis policy (declared, not guessed)
-----------------------------------------
A published answer discloses exactly one basis in its context header, and
that basis may not be legal for every metric the answer cites. The harness
applies the rule the design states for the product (§5.3 / §6.6 step 3),
re-implemented here from the contract's own ``allowed_date_bases``:

    the published context basis, if the contract allows it and the catalog
    binds it at the contract's entity; otherwise the first of
    (primary basis, then the allowed alternates in authored order) that the
    catalog binds at that entity.

When the resolved basis does not reproduce the published number, the caller
re-derives on every *other* allowed-and-bound basis before calling the cell
diverged. A cell that only matches on an alternate basis is reported as
``basis_ambiguous`` — a real disclosure defect (the published provenance does
not pin the basis the number was read on), not a pass.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from revi_warehouse_diff.governed import (
    DERIVED_MEASURES,
    LATE_CHARGE_THRESHOLD_DAYS,
    Catalog,
    MetricContract,
)


class Underivable(Exception):
    """v1 cannot derive this honestly. Carries a machine-readable reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------
# Published context, normalised
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Predicate:
    """A filter, from either the contract YAML or the published scope."""

    dimension: str
    op: str
    values: tuple[Any, ...] = ()

    @classmethod
    def from_contract_yaml(cls, node: dict[str, Any]) -> Predicate:
        if "value" in node:
            values: tuple[Any, ...] = (node["value"],)
        else:
            values = tuple(node.get("values", ()))
        return cls(dimension=str(node["dimension"]), op=str(node["op"]).lower(), values=values)


@dataclass(frozen=True)
class CohortPin:
    """A pinned cohort: the entity its ids are at, and where they live."""

    entity: str
    entity_ids_ref: str


@dataclass(frozen=True)
class AuditContext:
    """Everything the audit path is allowed to know about a published cell."""

    schema: str
    watermark_id: str
    window_start: dt.date
    window_end: dt.date
    #: The basis the answer's context header published (may be illegal for
    #: the metric being audited — the policy above then falls back).
    published_basis: str | None = None
    #: Filters the answer published (its context header's filter chips).
    scope: tuple[Predicate, ...] = ()
    #: The cell's own coordinate, e.g. {"payer": "State Medicaid"}.
    slice: tuple[Predicate, ...] = ()
    #: ("month" | "week" | "day", bucket start date) when the cell is a bucket.
    time_bucket: tuple[str, dt.date] | None = None
    cohort: CohortPin | None = None
    #: Basis to force instead of running the resolution policy (used to probe
    #: alternates when the policy basis does not reproduce a number).
    force_basis: str | None = None


@dataclass(frozen=True)
class Mutation:
    """A deliberate perturbation of the AUDIT path, for the self-test.

    A harness that cannot catch a planted error is theater. Each flag below
    breaks the audit path in a way a real implementation bug would, and the
    mutation self-test asserts the resulting diff fires.
    """

    name: str = "none"
    #: Keep the excluded population instead of removing it.
    flip_exclusion_polarity: bool = False
    #: Slide the window by N days.
    window_shift_days: int = 0
    #: Read on a different allowed basis than the policy resolves.
    swap_basis: bool = False
    #: Ignore the catalog measure's governed row filter (txn_type = 'PAYMENT'…).
    drop_measure_filter: bool = False
    #: Ignore the contract's own ``filtered:`` inner scope.
    drop_inner_filter: bool = False
    #: Ignore the published scope + the cell's own slice.
    drop_scope: bool = False
    #: Swap numerator and denominator on a ratio.
    invert_ratio: bool = False
    #: Ignore the cohort semi-join.
    drop_cohort: bool = False

    @property
    def active(self) -> bool:
        return self != Mutation()


NO_MUTATION = Mutation()


# --------------------------------------------------------------------------
# SQL fragments
# --------------------------------------------------------------------------


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dt.datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, int | float | Decimal):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


_COMPARISON_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _numerator_leaf(expr: dict[str, Any]) -> str | None:
    """The measure/column name a contract's numerator ultimately aggregates."""
    node = expr
    while isinstance(node, dict) and "filtered" in node:
        node = node["filtered"]["inner"]
    if not isinstance(node, dict):
        return None
    for key in ("sum", "count_distinct"):
        if key in node:
            return str(node[key])
    return None


@dataclass
class SqlSide:
    """One aggregate: its entity, its SELECT expression, its extra joins."""

    entity: str
    select_expr: str
    joins: tuple[str, ...] = ()
    #: Row filters this measure needs in WHERE rather than in FILTER
    #: (used by the derived measures that join a rollup).
    extra_where: tuple[str, ...] = ()


@dataclass(frozen=True)
class SideResult:
    entity: str
    sql: str
    value: Decimal


@dataclass(frozen=True)
class Plan:
    """The SQL the audit path will run for one cell, before it runs."""

    contract: MetricContract
    basis: str
    numerator_entity: str
    numerator_sql: str
    denominator_entity: str | None
    denominator_sql: str | None


@dataclass(frozen=True)
class Derivation:
    """A recomputed cell: the number and the SQL that produced it."""

    metric_id: str
    contract_version: int
    basis: str
    unit: str
    numerator: SideResult
    denominator: SideResult | None
    value: Decimal
    basis_was_substituted: bool
    mutation: str = "none"

    @property
    def sql_blocks(self) -> tuple[str, ...]:
        if self.denominator is None:
            return (f"-- numerator\n{self.numerator.sql}",)
        return (
            f"-- numerator\n{self.numerator.sql}",
            f"-- denominator\n{self.denominator.sql}",
        )


class NaiveDeriver:
    """Contract + context → SQL → number. No product code anywhere in here."""

    def __init__(
        self,
        contracts: dict[str, MetricContract],
        catalog: Catalog,
        columns: dict[str, frozenset[str]] | None = None,
        materialized_cohorts: frozenset[str] | None = None,
    ) -> None:
        self._contracts = contracts
        self._catalog = catalog
        #: Cohort tables that still exist. A pinned cohort whose table the TTL
        #: sweep has dropped is refused (``cohort_materialization_expired``)
        #: rather than reported as a divergence — the audit path genuinely
        #: cannot re-read a population that no longer exists.
        self._cohorts = materialized_cohorts
        #: entity -> the columns its base view actually has, introspected once
        #: from the warehouse so an unresolvable field refuses instead of
        #: producing a SQL error mid-corpus.
        self._columns = columns or {}

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    # -- entity -----------------------------------------------------------

    def entity_of(self, contract: MetricContract) -> str:
        """Resolve a contract's declared grain to a catalog ENTITY.

        ``entity_grain:`` names either an entity (``denial``) or a grain
        (``line``). Two entities share the LINE grain — ``claim_line`` and
        ``denial`` — so a bare grain is disambiguated by the entity the
        contract's own numerator binds to (its catalog measure's entity, or
        the entity whose primary key the numerator counts). An unresolvable
        grain refuses rather than guessing.
        """
        declared = contract.entity_grain
        if declared in self._catalog.entities:
            return declared
        candidates = [
            name
            for name, spec in self._catalog.entities.items()
            if str(spec.get("grain", "")).lower() == declared.lower()
        ]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise Underivable("entity_grain_unknown", f"{contract.id}/{declared}")
        leaf = _numerator_leaf(contract.numerator)
        if leaf is not None:
            measure = self._catalog.measures.get(leaf)
            if measure is not None and str(measure["entity"]) in candidates:
                return str(measure["entity"])
            derived = DERIVED_MEASURES.get(leaf)
            if derived is not None and derived.entity in candidates:
                return derived.entity
            for candidate in candidates:
                if self._catalog.primary_key(candidate) == leaf:
                    return candidate
        raise Underivable("entity_grain_ambiguous", f"{contract.id}/{declared}")

    # -- basis ------------------------------------------------------------

    def bound_bases(self, contract: MetricContract) -> tuple[str, ...]:
        """Allowed bases the catalog actually binds at the contract's entity."""
        entity = self.entity_of(contract)
        bound: list[str] = []
        for basis in (contract.primary_date_basis, *contract.allowed_date_bases):
            if basis in bound:
                continue
            if self._catalog.basis_column(basis, entity) is not None:
                bound.append(basis)
        return tuple(bound)

    def resolve_basis(self, contract: MetricContract, ctx: AuditContext) -> str:
        if ctx.force_basis is not None:
            if not contract.allows_basis(ctx.force_basis):
                raise Underivable("basis_not_allowed", f"{contract.id}/{ctx.force_basis}")
            return ctx.force_basis
        bound = self.bound_bases(contract)
        if not bound:
            raise Underivable(
                "basis_unbound",
                f"{contract.id}: none of {list(contract.allowed_date_bases)} bind at "
                f"{contract.entity_grain}",
            )
        published = (ctx.published_basis or "").lower()
        if published and contract.allows_basis(published) and published in bound:
            return published
        return bound[0]

    # -- predicates -------------------------------------------------------

    def _predicate_sql(self, predicate: Predicate, entity: str, alias: str = "base") -> str:
        kind = self._catalog.dimension_kind(predicate.dimension)
        if kind == "derived_bucket":
            raise Underivable("derived_bucket_dimension", predicate.dimension)
        column = self._catalog.dimension_column(predicate.dimension, entity)
        if column is None:
            raise Underivable("dimension_unbound", f"{predicate.dimension}@{entity}")
        self._require_column(entity, column)
        ref = f"{alias}.{column}"
        op = predicate.op
        if op == "is_null":
            return f"{ref} IS NULL"
        if op == "is_not_null":
            return f"{ref} IS NOT NULL"
        if not predicate.values:
            raise Underivable("predicate_without_values", f"{predicate.dimension}/{op}")
        if op == "eq":
            return f"{ref} = {sql_literal(predicate.values[0])}"
        if op == "neq":
            return f"{ref} <> {sql_literal(predicate.values[0])}"
        if op in ("in", "not_in"):
            rendered = ", ".join(sql_literal(v) for v in predicate.values)
            keyword = "IN" if op == "in" else "NOT IN"
            return f"{ref} {keyword} ({rendered})"
        if op in _COMPARISON_OPS:
            return f"{ref} {_COMPARISON_OPS[op]} {sql_literal(predicate.values[0])}"
        raise Underivable("predicate_op_unsupported", op)

    def _where_sql(self, node: dict[str, Any], entity: str, alias: str = "base") -> str:
        if "and" in node:
            parts = [self._where_sql(child, entity, alias) for child in node["and"]]
            return "(" + " AND ".join(parts) + ")"
        if "or" in node:
            parts = [self._where_sql(child, entity, alias) for child in node["or"]]
            return "(" + " OR ".join(parts) + ")"
        if "not" in node:
            return "NOT (" + self._where_sql(node["not"], entity, alias) + ")"
        return self._predicate_sql(Predicate.from_contract_yaml(node), entity, alias)

    def _require_column(self, entity: str, column: str) -> None:
        known = self._columns.get(entity)
        if known is not None and column not in known:
            raise Underivable("column_absent", f"{entity}.{column}")

    # -- measures ---------------------------------------------------------

    def _side(
        self,
        expr: dict[str, Any],
        contract: MetricContract,
        mutation: Mutation,
    ) -> SqlSide:
        if "filtered" in expr:
            inner = self._side(expr["filtered"]["inner"], contract, mutation)
            if mutation.drop_inner_filter:
                return inner
            condition = self._where_sql(expr["filtered"]["where"], inner.entity)
            return replace(inner, select_expr=self._add_filter(inner.select_expr, condition))
        if "count" in expr:
            return SqlSide(entity=self.entity_of(contract), select_expr="COUNT(*)")
        if "sum" in expr:
            return self._aggregate(str(expr["sum"]), "sum", contract, mutation)
        if "count_distinct" in expr:
            return self._aggregate(str(expr["count_distinct"]), "count_distinct", contract, mutation)
        raise Underivable("measure_expr_unsupported", ",".join(sorted(expr)))

    @staticmethod
    def _add_filter(select_expr: str, condition: str) -> str:
        if select_expr.endswith(")") and " FILTER (WHERE " in select_expr:
            head, _, tail = select_expr.rpartition(" FILTER (WHERE ")
            existing = tail[:-1]
            return f"{head} FILTER (WHERE ({existing}) AND ({condition}))"
        return f"{select_expr} FILTER (WHERE {condition})"

    def _aggregate(
        self,
        name: str,
        wanted_agg: str,
        contract: MetricContract,
        mutation: Mutation,
    ) -> SqlSide:
        # 1. a governed catalog measure (carries its own entity + row filter)
        measure = self._catalog.measures.get(name)
        if measure is not None:
            entity = str(measure["entity"])
            column = str(measure["column"])
            self._require_column(entity, column)
            aggregation = str(measure.get("aggregation", wanted_agg))
            if aggregation == "sum":
                select = f"SUM(base.{column})"
            elif aggregation == "count_distinct":
                select = f"COUNT(DISTINCT base.{column})"
            elif aggregation == "count":
                select = "COUNT(*)"
            else:
                raise Underivable("aggregation_unsupported", aggregation)
            row_filter = measure.get("filter")
            if row_filter and not mutation.drop_measure_filter:
                select = self._add_filter(select, str(row_filter))
            return SqlSide(entity=entity, select_expr=select)

        # 2. a probe-time derived measure the pack declares
        derived = DERIVED_MEASURES.get(name)
        if derived is not None:
            if derived.shape != "aggregation":
                raise Underivable("derived_measure_snapshot_shape", name)
            return self._derived_side(derived.id, derived.entity)

        # 3. a plain column on the contract entity's base view
        entity = self.entity_of(contract)
        self._require_column(entity, name)
        if wanted_agg == "count_distinct":
            return SqlSide(entity=entity, select_expr=f"COUNT(DISTINCT base.{name})")
        if wanted_agg == "sum":
            return SqlSide(entity=entity, select_expr=f"SUM(base.{name})")
        raise Underivable("aggregation_unsupported", wanted_agg)

    def _derived_side(self, measure_id: str, entity: str) -> SqlSide:
        """Re-implement the pack's declared derived-measure formula in SQL."""
        if measure_id == "payment_lag_days":
            # NOTES: PAYMENT transactions only; post_date - submission_date.
            return SqlSide(
                entity=entity,
                select_expr=(
                    "SUM(CASE WHEN base.txn_type = 'PAYMENT' "
                    "THEN date_diff('day', base.submission_date, base.post_date) END)"
                ),
            )
        if measure_id == "submission_lag_days":
            return SqlSide(
                entity=entity,
                select_expr="SUM(date_diff('day', base.service_date, base.submission_date))",
            )
        if measure_id == "charge_entry_lag_days":
            return SqlSide(
                entity=entity,
                select_expr="SUM(date_diff('day', base.service_date, base.charge_entry_date))",
            )
        if measure_id == "late_charge_cents":
            return SqlSide(
                entity=entity,
                select_expr=(
                    "SUM(CASE WHEN date_diff('day', base.service_date, base.charge_entry_date) > "
                    f"{LATE_CHARGE_THRESHOLD_DAYS} THEN base.billed_amount_cents ELSE 0 END)"
                ),
            )
        if measure_id == "underpayment_cents":
            # NOTES: adjudicated claims only (visible line allowed amounts);
            # max(0, expected - summed line allowed), floored PER CLAIM.
            line_view = self._catalog.base_view("claim_line")
            if line_view is None:
                raise Underivable("entity_unknown", "claim_line")
            return SqlSide(
                entity=entity,
                select_expr=(
                    "SUM(CASE WHEN lines.allowed_n > 0 THEN "
                    "GREATEST(0, base.expected_amount_cents - lines.allowed_sum) ELSE 0 END)"
                ),
                joins=(
                    "LEFT JOIN (SELECT claim_id, SUM(allowed_amount_cents) AS allowed_sum, "
                    "COUNT(allowed_amount_cents) AS allowed_n FROM "
                    "{schema}." + line_view + " GROUP BY claim_id) AS lines "
                    "ON lines.claim_id = base.claim_id",
                ),
            )
        raise Underivable("derived_measure_unsupported", measure_id)

    # -- cohort -----------------------------------------------------------

    def _cohort_sql(self, cohort: CohortPin, entity: str) -> str:
        if self._cohorts is not None and cohort.entity_ids_ref not in self._cohorts:
            raise Underivable("cohort_materialization_expired", cohort.entity_ids_ref)
        if entity == cohort.entity:
            key = self._catalog.primary_key(cohort.entity)
        else:
            key = self._catalog.join_column(entity, cohort.entity)
        if key is None:
            raise Underivable("cohort_join_uncertified", f"{entity}->{cohort.entity}")
        self._require_column(entity, key)
        return f"base.{key} IN (SELECT entity_id FROM {cohort.entity_ids_ref})"

    # -- the query --------------------------------------------------------

    def build_side_sql(
        self,
        side: SqlSide,
        contract: MetricContract,
        basis: str,
        ctx: AuditContext,
        mutation: Mutation,
    ) -> str:
        view = self._catalog.base_view(side.entity)
        if view is None:
            raise Underivable("entity_unknown", side.entity)
        basis_column = self._catalog.basis_column(basis, side.entity)
        if basis_column is None:
            raise Underivable("basis_unbound", f"{basis}@{side.entity}")
        self._require_column(side.entity, basis_column)

        start = ctx.window_start + dt.timedelta(days=mutation.window_shift_days)
        end = ctx.window_end + dt.timedelta(days=mutation.window_shift_days)
        where: list[str] = [
            f"base.{basis_column} BETWEEN {sql_literal(start)} AND {sql_literal(end)}"
        ]
        if ctx.time_bucket is not None:
            unit, bucket = ctx.time_bucket
            if unit not in ("day", "week", "month", "quarter", "year"):
                raise Underivable("time_bucket_unsupported", unit)
            where.append(
                f"CAST(date_trunc('{unit}', base.{basis_column}) AS DATE) = {sql_literal(bucket)}"
            )
        if not mutation.drop_scope:
            for predicate in (*ctx.scope, *ctx.slice):
                where.append(self._predicate_sql(predicate, side.entity))
        if contract.exclusions is not None:
            condition = self._where_sql(contract.exclusions, side.entity)
            if mutation.flip_exclusion_polarity:
                where.append(f"COALESCE({condition}, FALSE)")
            else:
                where.append(f"NOT COALESCE({condition}, FALSE)")
        if ctx.cohort is not None and not mutation.drop_cohort:
            where.append(self._cohort_sql(ctx.cohort, side.entity))
        where.extend(side.extra_where)

        joins = "".join("\n  " + j.replace("{schema}", ctx.schema) for j in side.joins)
        conditions = "\n    AND ".join(where)
        return (
            f"SELECT {side.select_expr} AS v\n"
            f"  FROM {ctx.schema}.{view} AS base{joins}\n"
            f" WHERE {conditions}"
        )

    def plan(
        self,
        metric_id: str,
        ctx: AuditContext,
        mutation: Mutation = NO_MUTATION,
    ) -> Plan:
        contract = self._contracts.get(metric_id)
        if contract is None:
            raise Underivable("contract_unknown", metric_id)
        if contract.kind == "snapshot":
            raise Underivable("snapshot_contract", metric_id)
        if contract.kind != "flow":
            raise Underivable("contract_kind_unsupported", f"{metric_id}/{contract.kind}")

        basis = self.resolve_basis(contract, ctx)
        if mutation.swap_basis:
            alternates = [b for b in self.bound_bases(contract) if b != basis]
            if not alternates:
                raise Underivable("no_alternate_basis", metric_id)
            basis = alternates[0]

        numerator_expr = contract.numerator
        denominator_expr = contract.denominator
        if mutation.invert_ratio and denominator_expr is not None:
            numerator_expr, denominator_expr = denominator_expr, numerator_expr

        numerator = self._side(numerator_expr, contract, mutation)
        numerator_sql = self.build_side_sql(numerator, contract, basis, ctx, mutation)
        denominator_entity: str | None = None
        denominator_sql: str | None = None
        if denominator_expr is not None:
            denominator = self._side(denominator_expr, contract, mutation)
            denominator_entity = denominator.entity
            denominator_sql = self.build_side_sql(denominator, contract, basis, ctx, mutation)
        return Plan(
            contract=contract,
            basis=basis,
            numerator_entity=numerator.entity,
            numerator_sql=numerator_sql,
            denominator_entity=denominator_entity,
            denominator_sql=denominator_sql,
        )


@dataclass
class DerivationRun:
    """Deriver + a live warehouse connection."""

    deriver: NaiveDeriver
    execute: Any  # Callable[[str], Decimal | None]
    _cache: dict[str, Decimal] = field(default_factory=dict)

    def scalar(self, sql: str) -> Decimal:
        cached = self._cache.get(sql)
        if cached is not None:
            return cached
        raw = self.execute(sql)
        value = Decimal(0) if raw is None else Decimal(str(raw))
        self._cache[sql] = value
        return value

    def derive(
        self,
        metric_id: str,
        ctx: AuditContext,
        mutation: Mutation = NO_MUTATION,
    ) -> Derivation:
        plan = self.deriver.plan(metric_id, ctx, mutation)
        numerator_value = self.scalar(plan.numerator_sql)
        numerator = SideResult(plan.numerator_entity, plan.numerator_sql, numerator_value)
        if plan.denominator_sql is None:
            value = numerator_value
            denominator = None
        else:
            denominator_value = self.scalar(plan.denominator_sql)
            denominator = SideResult(
                plan.denominator_entity or "", plan.denominator_sql, denominator_value
            )
            if denominator_value == 0:
                raise Underivable("denominator_zero", metric_id)
            value = numerator_value / denominator_value
        published = (ctx.published_basis or "").lower()
        return Derivation(
            metric_id=metric_id,
            contract_version=plan.contract.version,
            basis=plan.basis,
            unit=plan.contract.unit,
            numerator=numerator,
            denominator=denominator,
            value=value,
            basis_was_substituted=bool(published) and published != plan.basis,
            mutation=mutation.name,
        )
