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
a ``FILTER (WHERE …)`` clause), then the probe-time derived registry below,
then as a catalog-declared view column. Contract-internal ``Filtered`` scopes
and contract ``exclusions`` also become ``FILTER`` clauses.

**Probe-time derived measures.** The fields in ``_DERIVED_MEASURES`` are
computed by this compiler rather than stored (the base pack's ``NOTES.md``
mirrors the list). Each is a deterministic expression over the entity's
curated base view — a date difference, a floored variance, a filing deadline
read off the claim's plan, or a per-claim rollup joined on the certified
``claim_id`` path. They are *adapter conventions over the catalog*, like
``resolved_date`` and the ``ar_age_bucket`` CASE arms: the catalog governs
every column, row filter and join they touch, and each declares the probe
shapes it is valid for — a snapshot-time age cannot be computed inside a flow
aggregation, and the compiler refuses rather than inventing an as-of.

**Cross-entity ratio-of-sums.** A ratio metric may name a numerator and a
denominator that live at different entity grains (``net_collection_rate``:
transaction cash over claim expected). This is legal precisely when both
sides aggregate to the *same scope* — same window on the same date basis,
same scope filter, same group keys — which the pre-joined base views make
true, since a transaction's payer/facility/service dates are its parent
claim's. Such a probe compiles to one aggregate **block per entity**, each
selecting the identical group columns from its own base view, joined
``FULL OUTER … IS NOT DISTINCT FROM`` on those keys so neither side can drop
a cell the other has. Both sides therefore remain plain SUMs over a shared
population and the kernel still computes the ratio (slicing law, design
§5.3): the adapter never divides, and never fans out a claim across its
transactions. ``HAVING`` is rejected on cross-entity probes rather than
silently applied to one side.

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

**Snapshot probes** (see :meth:`ProbeCompiler.compile_snapshot`): state
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
``filing_runway_bucket`` buckets the mirror image — ``datediff('day', as_of,
service_date + timely_filing_days)``, the claim → plan → filing rule join —
with two non-numeric arms (``filed``, ``expired``); ``days_to_filing_deadline``
is the same quantity as a summable measure. Watermark-derived flags the
snapshot CAN restate as-of are restated: the projection carries
``SELECT * REPLACE (discharge_date <= as_of AS discharged_flag)`` so a
back-dated DNFB reading does not count discharges that had not happened yet.
``status`` cannot be restated from claim columns and the catalog says so.

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
    MeasureDef,
    PhiClass,
)
from revi_kernel.capabilities import DerivedMeasure
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
    ProbeShape,
    RowEvidenceProbe,
    SnapshotProbe,
)
from revi_kernel.refs import (
    DISCHARGE,
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
#: Charge-capture date on the line base view. Declared in the catalog under
#: `claim_line.declared_columns` (warehouse/catalog/entities.yaml) and checked
#: against it before use, so this name is a lookup key, not a private constant.
_CHARGE_ENTRY_DATE_COLUMN = "charge_entry_date"
#: The plan's configured timely-filing limit, pre-joined onto the claim base
#: view and declared in the catalog under `claim.declared_columns`. This is the
#: limit half of the claim -> plan -> filing rule join; the anchor half is the
#: catalog's SERVICE basis column. Checked against the catalog before use.
_FILING_LIMIT_DAYS_COLUMN = "timely_filing_days"
#: The certified boolean marking a claim that has already been submitted. Only
#: an unsubmitted claim has a filing clock still running, so it gates both the
#: derived runway measure and the `filed` arm of the runway bucket.
_BILLED_FLAG_DIMENSION = "billed_flag"
#: The certified discharge flag. Stored in the base view from the CURRENT
#: discharge date; the snapshot builder re-projects it at the probe's as-of
#: (see `_as_of_flag_projections`), the same treatment `resolved_date` gets.
_DISCHARGED_FLAG_DIMENSION = "discharged_flag"
_APPLIED_MEASURE_IDS = ("payment_cents", "patient_payment_cents", "contractual_adj_cents", "other_adj_cents")
_CASH_IN_MEASURE_IDS = ("payment_cents", "patient_payment_cents")
_REVERSAL_MEASURE_IDS = ("refund_cents",)
_UNIT_BY_CATALOG_UNIT = {"cents": "money_cents", "count": "count"}

#: Late-charge threshold, per the base pack's derived-measure registry: a line
#: entered more than this many days after its service date is a late charge.
_LATE_CHARGE_THRESHOLD_DAYS = 3

#: Claim statuses that count as unresolved A/R, per the same registry
#: (`ar_age_days_billed_cents` ages these and zeroes everything else). Values
#: are from the catalog `status` dimension's declared domain.
_UNRESOLVED_STATUSES = ("OPEN", "DENIED")

# Rollup column names produced by the per-claim LEFT JOINs (double underscore
# so they can never collide with a curated base-view column).
_APPLIED_CENTS = "__applied_cents"
_CASH_IN_CENTS = "__cash_in_cents"
_REFUND_CENTS = "__refund_cents"
_LINE_ALLOWED_CENTS = "__line_allowed_cents"
_AGE_DAYS = "__age_days"
_FILING_RUNWAY_DAYS = "__filing_runway_days"

#: `filing_runway_bucket` arms that are not day ranges. Declared here and
#: required to be present in the catalog's bucket list, so dropping either one
#: from the catalog breaks the build instead of silently mislabelling claims.
_FILING_FILED_LABEL = "filed"  # already submitted: the initial clock is closed
_FILING_EXPIRED_LABEL = "expired"  # unsubmitted, deadline already passed
_FILING_RUNWAY_BUCKET = "filing_runway_bucket"

_MONEY_ROLLUP = "claim_money"  # claim ← transaction, as-of (snapshot only)
_LINE_ROLLUP = "claim_lines"  # claim ← claim_line (flow aggregation)

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


#: Probe shapes a derived measure can be computed for. Spelled in the
#: kernel's own vocabulary so the shape this compiler enforces and the shape
#: the planner negotiates in §6.6 are the same value, not two spellings.
AGGREGATION_SHAPE = ProbeShape.AGGREGATION
SNAPSHOT_SHAPE = ProbeShape.SNAPSHOT


@dataclass(frozen=True, slots=True)
class _DerivedSpec:
    """One probe-time derived measure: what it means, where it lives, and
    which probe shapes can compute it.

    The governed declaration; the SQL is built by
    :meth:`ProbeCompiler._derived_binding` and
    :func:`derived_measure_capabilities` publishes it across the repository
    port, so the planner refuses exactly what this compiler would refuse."""

    id: str
    entity: str  # catalog entity name
    unit: str | None  # frame-column unit ("days", "money_cents", None)
    shapes: frozenset[ProbeShape]
    formula: str


_DERIVED_MEASURES: Mapping[str, _DerivedSpec] = {
    spec.id: spec
    for spec in (
        _DerivedSpec(
            "payment_lag_days",
            "transaction",
            "days",
            frozenset({AGGREGATION_SHAPE}),
            "PAYMENT transactions only (the catalog's payment_cents row filter): "
            "post date minus the parent claim's submission date, in days.",
        ),
        _DerivedSpec(
            "submission_lag_days",
            "claim",
            "days",
            frozenset({AGGREGATION_SHAPE}),
            "Submission date minus service date, in days; NULL (contributes "
            "nothing) for a claim that was never submitted.",
        ),
        _DerivedSpec(
            "charge_entry_lag_days",
            "claim_line",
            "days",
            frozenset({AGGREGATION_SHAPE}),
            "Charge-entry date minus the line's service date, in days.",
        ),
        _DerivedSpec(
            "late_charge_cents",
            "claim_line",
            "money_cents",
            frozenset({AGGREGATION_SHAPE}),
            f"Line billed cents when charge entry is more than "
            f"{_LATE_CHARGE_THRESHOLD_DAYS} days after the line's service date, else 0.",
        ),
        _DerivedSpec(
            "underpayment_cents",
            "claim",
            "money_cents",
            frozenset({AGGREGATION_SHAPE}),
            "Adjudicated claims only (the claim has visible line allowed "
            "amounts): max(0, expected cents minus summed line allowed cents). "
            "Floored per claim, so underpayments never net against overpayments.",
        ),
        _DerivedSpec(
            "ar_age_days_billed_cents",
            "claim",
            None,  # cents x days — a weighting product, not a reportable unit
            frozenset({SNAPSHOT_SHAPE}),
            "Unresolved claims (status OPEN or DENIED) only: billed cents times "
            "the claim's age in days at the snapshot's as-of date; else 0.",
        ),
        _DerivedSpec(
            "days_to_filing_deadline",
            "claim",
            "days",
            frozenset({SNAPSHOT_SHAPE}),
            "Unsubmitted claims only (the filing clock a submitted claim was "
            "racing is closed): the claim's service date plus the filing limit "
            "its plan configures, minus the snapshot's as-of date, in days. "
            "Negative means the deadline has already passed.",
        ),
        _DerivedSpec(
            "credit_balance_cents",
            "claim",
            "money_cents",
            frozenset({SNAPSHOT_SHAPE}),
            "max(0, max(0, payer + patient cash posted on/before as-of minus "
            "expected cents) minus refunds already posted).",
        ),
        _DerivedSpec(
            "open_balance_cents",
            "claim",
            "money_cents",
            frozenset({SNAPSHOT_SHAPE}),
            "Billed cents minus money applied on/before as-of (payments, patient "
            "payments and adjustments; refunds added back).",
        ),
    )
}

_OPEN_BALANCE_FIELD = "open_balance_cents"

_DERIVED_CAPABILITIES: tuple[DerivedMeasure, ...] = tuple(
    DerivedMeasure(field=spec.id, entity=spec.entity, shapes=spec.shapes)
    for spec in _DERIVED_MEASURES.values()
)


def derived_measure_capabilities() -> tuple[DerivedMeasure, ...]:
    """The registry above, stated in the repository port's vocabulary.

    This is the whole of what §6.6 is told about probe-time derivation: which
    field, at which catalog entity, in which probe shapes. Derived from
    ``_DERIVED_MEASURES`` rather than restated, so the compiler's verdict and
    the planner's cannot drift apart — there is only one list.
    """
    return _DERIVED_CAPABILITIES


@dataclass
class _CompileState:
    """Mutable accumulation across the fragments of one probe compilation."""

    watermark: DataWatermark
    schema: str = ""
    shape: ProbeShape = AGGREGATION_SHAPE
    as_of: date | None = None
    uncertified: bool = False
    # Extra dimension-id → SQL expression bindings (snapshot derived buckets).
    bindings: dict[str, str] = field(default_factory=dict)
    # dimension id → the inner-subquery column its binding reads. Consulted when
    # the binding is USED, so the snapshot builder only projects a derived
    # column some fragment actually asked for.
    binding_needs: dict[str, str] = field(default_factory=dict)
    # Inner-subquery columns this compilation needs projected (snapshot only).
    needs: set[str] = field(default_factory=set)
    # (entity name, rollup id) → LEFT JOIN clause required by a derived measure.
    rollups: dict[tuple[str, str], _Fragment] = field(default_factory=dict)

    @property
    def grade(self) -> EvidenceGrade:
        return EvidenceGrade.DISCOVERY if self.uncertified else EvidenceGrade.DIRECT

    def joins_for(self, entity_name: str) -> tuple[_Fragment, ...]:
        """Rollup LEFT JOINs this compilation needs on one entity, deterministically
        ordered by rollup id."""
        return tuple(
            fragment for (name, _rollup), fragment in sorted(self.rollups.items()) if name == entity_name
        )


@dataclass(frozen=True)
class _ValueBinding:
    expr: str
    filter_sql: str | None
    unit: str | None
    aggregation: MeasureAggregation | None


@dataclass(frozen=True, slots=True)
class _MetricComponent:
    """One aggregate column of one metric, with the entity it aggregates over."""

    alias: str
    ref: MetricRef
    contract: MetricContract
    expr: MeasureExpr
    entity: EntityDef
    additive: bool  # a metric with no denominator: one column, contract unit


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
            need = state.binding_needs.get(dimension_id)
            if need is not None:
                state.needs.add(need)
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
        derived = _DERIVED_MEASURES.get(field_id)
        if derived is not None:
            return self._derived_binding(derived, entity, state)
        if field_id in self._catalog.declared_columns(entity.name):
            return _ValueBinding(expr=_ident(field_id), filter_sql=None, unit=None, aggregation=None)
        raise UnsupportedConceptError(
            f"field {field_id!r} does not resolve to a catalog measure, a probe-time "
            f"derived measure, or a declared column of entity {entity.name!r}",
            details={"field": field_id, "entity": entity.name},
        )

    # ----------------------------------------------- probe-time derived measures

    def _derived_binding(
        self, spec: _DerivedSpec, entity: EntityDef, state: _CompileState
    ) -> _ValueBinding:
        """SQL for one registered derived measure, built from catalog bindings.

        Raises rather than improvising when the measure is asked for at the
        wrong grain or in a probe shape that cannot compute it (a snapshot-time
        age has no meaning inside a flow aggregation)."""
        if spec.entity != entity.name:
            raise GrainIncompatibleError(
                f"derived measure {spec.id!r} is defined at the {spec.entity!r} grain, "
                f"not {entity.name!r}",
                details={"measure": spec.id, "entity": entity.name},
            )
        if state.shape not in spec.shapes:
            raise SourceCapabilityUnsupportedError(
                f"derived measure {spec.id!r} is computable only in "
                f"{sorted(shape.value for shape in spec.shapes)} probes, not "
                f"{state.shape.value!r}",
                details={"measure": spec.id, "shape": state.shape.value},
            )
        builder = getattr(self, f"_derive_{spec.id}")
        expr, filter_sql = builder(entity, state)
        return _ValueBinding(
            expr=expr, filter_sql=filter_sql, unit=spec.unit, aggregation=MeasureAggregation.SUM
        )

    def _derive_payment_lag_days(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        submission = _ident(self._basis_column(entity, SUBMISSION))
        post = _ident(self._basis_column(entity, POST))
        payment = self._require_measure("payment_cents", entity)
        return f"datediff('day', {submission}, {post})", payment.filter_sql

    def _derive_submission_lag_days(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        service = _ident(self._basis_column(entity, SERVICE))
        submission = _ident(self._basis_column(entity, SUBMISSION))
        return f"datediff('day', {service}, {submission})", None

    def _derive_charge_entry_lag_days(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        service = _ident(self._basis_column(entity, SERVICE))
        entry = _ident(self._require_declared_column(_CHARGE_ENTRY_DATE_COLUMN, entity))
        return f"datediff('day', {service}, {entry})", None

    def _derive_late_charge_cents(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        service = _ident(self._basis_column(entity, SERVICE))
        entry = _ident(self._require_declared_column(_CHARGE_ENTRY_DATE_COLUMN, entity))
        billed = _ident(self._require_measure("line_billed_amount_cents", entity).column)
        late = f"datediff('day', {service}, {entry}) > {_LATE_CHARGE_THRESHOLD_DAYS}"
        return f"CASE WHEN {late} THEN {billed} ELSE 0 END", None

    def _derive_underpayment_cents(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        expected = _ident(self._require_measure("expected_amount_cents", entity).column)
        self._require_line_rollup(entity, state)
        return (
            f"GREATEST({expected} - {_LINE_ALLOWED_CENTS}, 0)",
            f"{_LINE_ALLOWED_CENTS} IS NOT NULL",  # adjudicated claims only
        )

    def _derive_ar_age_days_billed_cents(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        billed = _ident(self._require_measure("billed_amount_cents", entity).column)
        status = self._dimension_expr("status", entity, state)
        self._check_status_domain(_UNRESOLVED_STATUSES)
        unresolved = ", ".join(f"'{value}'" for value in _UNRESOLVED_STATUSES)
        return f"{billed} * {_AGE_DAYS}", f"{status} IN ({unresolved})"

    def _derive_days_to_filing_deadline(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        """Runway to the plan's timely-filing deadline, at the snapshot's as-of.

        Both halves of the claim → plan → filing rule join come from the
        catalog: the anchor is the SERVICE basis column, the limit is the
        plan's configured ``timely_filing_days`` declared on the claim entity.
        The arithmetic itself lives in the snapshot builder's inner subquery
        (it needs the as-of parameter); this method asks for that column and
        states the population the number is meaningful for."""
        self._basis_column(entity, SERVICE)  # anchor must be catalog-bound
        self._require_declared_column(_FILING_LIMIT_DAYS_COLUMN, entity)
        state.needs.add(_FILING_RUNWAY_DAYS)
        billed = self._dimension_expr(_BILLED_FLAG_DIMENSION, entity, state)
        return _FILING_RUNWAY_DAYS, f"NOT {billed}"

    def _check_status_domain(self, values: tuple[str, ...]) -> None:
        """A derived measure may only name statuses the catalog declares — a
        renamed status must break the build, not silently age nothing."""
        dim = self._catalog.dimension("status")
        domain = dim.value_domain if dim is not None else None
        if domain is not None and not set(values) <= set(domain):
            raise UnsupportedConceptError(
                f"claim statuses {sorted(set(values) - set(domain))} are not in the catalog's "
                "declared status domain",
                details={"dimension": "status", "values": sorted(values)},
            )

    def _derive_credit_balance_cents(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        expected = _ident(self._require_measure("expected_amount_cents", entity).column)
        self._require_money_rollup(entity, state)
        # A claim with no posted transactions at all misses the rollup entirely;
        # zero cash and zero refunds is what that means.
        overpaid = f"GREATEST(COALESCE({_CASH_IN_CENTS}, 0) - {expected}, 0)"
        return f"GREATEST({overpaid} - COALESCE({_REFUND_CENTS}, 0), 0)", None

    def _derive_open_balance_cents(
        self, entity: EntityDef, state: _CompileState
    ) -> tuple[str, str | None]:
        billed = _ident(self._require_measure("billed_amount_cents", entity).column)
        self._require_money_rollup(entity, state)
        return f"({billed} - COALESCE({_APPLIED_CENTS}, 0))", None

    def _require_measure(self, measure_id: str, entity: EntityDef) -> MeasureDef:
        measure = self._catalog.measure(measure_id)
        if measure is None or measure.entity != entity.name:
            raise UnsupportedConceptError(
                f"probe-time derived measures on {entity.name!r} require catalog measure "
                f"{measure_id!r}",
                details={"measure": measure_id, "entity": entity.name},
            )
        return measure

    def _require_declared_column(self, column: str, entity: EntityDef) -> str:
        """A base-view column a derived measure reads, checked against the
        catalog's declared set for the entity (``declared_columns:`` in
        ``entities.yaml``). Stops an adapter constant from outrunning the
        catalog: if the declaration is dropped, the derivation refuses rather
        than emitting SQL for a column the catalog never bound."""
        if column not in self._catalog.declared_columns(entity.name):
            raise UnsupportedConceptError(
                f"probe-time derived measures on {entity.name!r} require declared "
                f"column {column!r}",
                details={"column": column, "entity": entity.name},
            )
        return column

    def _child_entity(self, grain: EntityGrain, claim_entity: EntityDef) -> tuple[EntityDef, str]:
        """A child entity plus the certified column joining it to the claim grain.

        Both halves come from the catalog: no rollup is built over a path the
        catalog has not certified."""
        child = self._catalog.entity(grain)
        if child is None:
            raise UnsupportedConceptError(
                f"no catalog entity is bound to grain {grain.value!r} for the per-claim rollup",
                details={"grain": grain.value},
            )
        join_column = self._catalog.join_column(child.name, claim_entity.name)
        if join_column != claim_entity.primary_key:
            raise UnsupportedConceptError(
                f"catalog declares no {child.name!r} → {claim_entity.name!r} join for the rollup",
                details={"from": child.name, "to": claim_entity.name},
            )
        return child, join_column

    def _require_line_rollup(self, entity: EntityDef, state: _CompileState) -> None:
        """Per-claim summed line allowed cents (NULL when no line carries one,
        which is exactly "not adjudicated yet")."""
        key = (entity.name, _LINE_ROLLUP)
        if key in state.rollups:
            return
        line_entity, join_column = self._child_entity(EntityGrain.LINE, entity)
        allowed = self._catalog.measure("allowed_amount_cents")
        if allowed is None or allowed.entity != line_entity.name:
            raise UnsupportedConceptError(
                "the per-claim line rollup requires catalog measure 'allowed_amount_cents'",
                details={"measure": "allowed_amount_cents"},
            )
        inner = (
            f"SELECT {_ident(join_column)}, SUM({_ident(allowed.column)}) AS {_LINE_ALLOWED_CENTS} "
            f"FROM {_ident(state.schema)}.{_ident(line_entity.base_view)} "
            f"GROUP BY {_ident(join_column)}"
        )
        state.rollups[key] = (
            f"LEFT JOIN ({inner}) AS {_ident(_LINE_ROLLUP)} USING ({_ident(join_column)})",
            [],
        )

    def _require_money_rollup(self, entity: EntityDef, state: _CompileState) -> None:
        """Per-claim money posted on/before the snapshot's as-of date, built from
        the catalog's governed transaction measures.

        One rollup serves both money-derived fields: ``open_balance_cents``
        reads the applied total (payments and adjustments reduce the balance,
        refunds add back), ``credit_balance_cents`` reads payer + patient cash
        and refunds separately."""
        key = (entity.name, _MONEY_ROLLUP)
        if key in state.rollups:
            return
        if state.as_of is None:  # pragma: no cover - snapshot-only fields are gated above
            raise SourceCapabilityUnsupportedError(
                "the per-claim money rollup needs a snapshot as-of date",
                details={"entity": entity.name},
            )
        txn_entity, join_column = self._child_entity(EntityGrain.TRANSACTION, entity)
        post_column = self._basis_column(txn_entity, POST)
        applied: list[str] = []
        for measure_id in _APPLIED_MEASURE_IDS + _REVERSAL_MEASURE_IDS:
            term = self._txn_term(measure_id, txn_entity)
            applied.append(("-" if measure_id in _REVERSAL_MEASURE_IDS else "+") + " " + term)
        cash_in = " + ".join(self._txn_term(m, txn_entity) for m in _CASH_IN_MEASURE_IDS)
        refunds = " + ".join(self._txn_term(m, txn_entity) for m in _REVERSAL_MEASURE_IDS)
        inner = (
            f"SELECT {_ident(join_column)}, "
            f"{' '.join(applied).removeprefix('+ ')} AS {_APPLIED_CENTS}, "
            f"{cash_in} AS {_CASH_IN_CENTS}, "
            f"{refunds} AS {_REFUND_CENTS} "
            f"FROM {_ident(state.schema)}.{_ident(txn_entity.base_view)} "
            f"WHERE {_ident(post_column)} <= ? GROUP BY {_ident(join_column)}"
        )
        state.rollups[key] = (
            f"LEFT JOIN ({inner}) AS {_ident(_MONEY_ROLLUP)} USING ({_ident(join_column)})",
            [state.as_of],
        )

    def _txn_term(self, measure_id: str, txn_entity: EntityDef) -> str:
        measure = self._catalog.measure(measure_id)
        if measure is None or measure.entity != txn_entity.name or measure.filter_sql is None:
            raise UnsupportedConceptError(
                f"the per-claim money rollup requires catalog measure {measure_id!r}",
                details={"measure": measure_id},
            )
        return f"COALESCE(SUM({_ident(measure.column)}) FILTER (WHERE {measure.filter_sql}), 0)"

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

    def _component_entity(self, expr: MeasureExpr, entity: EntityDef) -> EntityDef:
        """The entity one ratio component aggregates over.

        Almost always the probe's own entity. A component whose field is a
        catalog measure (or derived measure) declared at *another* grain is a
        cross-entity component: legal, and compiled as its own same-scope
        aggregate block (see the module docstring)."""
        inner = expr.inner if isinstance(expr, Filtered) else expr
        if isinstance(inner, (Sum, CountDistinct)):
            measure = self._catalog.measure(inner.field.id)
            home = measure.entity if measure is not None else None
            if home is None:
                derived = _DERIVED_MEASURES.get(inner.field.id)
                home = derived.entity if derived is not None else None
            if home is not None and home != entity.name:
                foreign = self._catalog.entity_named(home)
                if foreign is None:  # pragma: no cover - catalog integrity forbids it
                    raise UnsupportedConceptError(
                        f"measure {inner.field.id!r} names unknown entity {home!r}",
                        details={"measure": inner.field.id, "entity": home},
                    )
                return foreign
        return entity

    def _metric_components(
        self,
        measures: tuple[MetricRef, ...],
        probe: AggregationProbe | SnapshotProbe,
        entity: EntityDef,
        expected_kind: MetricKind,
    ) -> list[_MetricComponent]:
        """The probe's metrics flattened into aggregate components in frame-column
        order (additive → one column, ratio → ``__num``/``__den``)."""
        components: list[_MetricComponent] = []
        for ref in measures:
            contract = self._resolve_contract(ref.id)
            self._check_contract(contract, probe, expected_kind)
            if contract.denominator is None:
                components.append(
                    _MetricComponent(
                        alias=ref.id,
                        ref=ref,
                        contract=contract,
                        expr=contract.numerator,
                        entity=self._component_entity(contract.numerator, entity),
                        additive=True,
                    )
                )
                continue
            for alias, expr in (
                (numerator_column(ref.id), contract.numerator),
                (denominator_column(ref.id), contract.denominator),
            ):
                components.append(
                    _MetricComponent(
                        alias=alias,
                        ref=ref,
                        contract=contract,
                        expr=expr,
                        entity=self._component_entity(expr, entity),
                        additive=False,
                    )
                )
        return components

    def _compile_components(
        self, components: list[_MetricComponent], state: _CompileState
    ) -> tuple[dict[str, list[_Fragment]], list[FrameColumn]]:
        """Compile every component against its own entity; SELECT fragments come
        back grouped by entity, frame columns stay in component order."""
        fragments: dict[str, list[_Fragment]] = {}
        columns: list[FrameColumn] = []
        for component in components:
            sql, params, unit = self._measure_expr_sql(
                component.expr, component.entity, state, component.contract.exclusions
            )
            fragments.setdefault(component.entity.name, []).append(
                (f"{sql} AS {_ident(component.alias)}", params)
            )
            columns.append(
                FrameColumn(
                    name=component.alias,
                    ref=component.ref,
                    contract_version=component.contract.version,
                    unit=component.contract.unit.value if component.additive else unit,
                )
            )
        return fragments, columns

    # ---------------------------------------------------------- aggregation

    def compile_aggregation(
        self, probe: AggregationProbe, *, schema: str, watermark: DataWatermark
    ) -> CompiledQuery:
        entity = self._entity_for(probe)
        state = _CompileState(watermark=watermark, schema=schema, shape=AGGREGATION_SHAPE)

        components = self._metric_components(probe.measures, probe, entity, MetricKind.FLOW)
        metric_fragments, columns = self._compile_components(components, state)
        additive_aliases = {c.alias for c in components if c.additive}

        # Entity order: the probe's own entity first, then any cross-entity
        # component's entity in first-appearance order (deterministic SQL).
        entities: list[EntityDef] = [entity]
        for component in components:
            if all(component.entity.name != known.name for known in entities):
                entities.append(component.entity)

        group_fragments, group_columns, group_aliases = self._group_fragments(probe, entity, state)
        columns = group_columns + columns

        if len(entities) == 1:
            select_fragments = group_fragments + metric_fragments.get(entity.name, [])
            where_fragments = self._window_and_scope(probe, entity, state)
            having_fragments = [self._having_fragment(pred, probe, entity, state) for pred in probe.having]
            sql_parts = [
                "SELECT " + ", ".join(sql for sql, _ in select_fragments),
                self._from_clause(entity, state),
                "WHERE " + " AND ".join(sql for sql, _ in where_fragments),
            ]
            if group_aliases:
                sql_parts.append("GROUP BY " + ", ".join(_ident(a) for a in group_aliases))
            if having_fragments:
                sql_parts.append("HAVING " + " AND ".join(sql for sql, _ in having_fragments))
            order_sql = self._order_by_sql(probe.order_by, group_aliases, additive_aliases)
            if order_sql:
                sql_parts.append(order_sql)
            if probe.limit is not None:
                sql_parts.append(f"LIMIT {probe.limit + 1}")
            join_params = [p for _, params in state.joins_for(entity.name) for p in params]
            params = [
                *(p for _, fragment_params in group_fragments for p in fragment_params),
                *(p for _, fragment_params in metric_fragments.get(entity.name, []) for p in fragment_params),
                *join_params,
                *(p for _, fragment_params in where_fragments for p in fragment_params),
                *(p for _, fragment_params in having_fragments for p in fragment_params),
            ]
            return CompiledQuery(
                sql="\n".join(sql_parts),
                params=tuple(params),
                columns=tuple(columns),
                grade=state.grade,
                row_limit=probe.limit,
            )

        if probe.having:
            raise SourceCapabilityUnsupportedError(
                "HAVING is not supported on a cross-entity ratio probe: the predicate "
                "would filter one side of the ratio only",
                details={"metrics": sorted({c.ref.id for c in components})},
            )
        return self._compile_cross_entity(
            probe, entities, components, metric_fragments, columns, group_aliases, additive_aliases, state
        )

    def _group_fragments(
        self, probe: AggregationProbe, entity: EntityDef, state: _CompileState
    ) -> tuple[list[_Fragment], list[FrameColumn], list[str]]:
        """Group-by SELECT fragments (dimensions, then the optional time bucket)
        for one entity, plus their frame columns and aliases."""
        fragments: list[_Fragment] = []
        columns: list[FrameColumn] = []
        aliases: list[str] = []
        for dim_ref in probe.dimensions:
            expr = self._dimension_expr(dim_ref.id, entity, state)
            fragments.append((f"{expr} AS {_ident(dim_ref.id)}", []))
            columns.append(FrameColumn(name=dim_ref.id, ref=dim_ref))
            aliases.append(dim_ref.id)
        bucket = probe.grain.time_bucket
        if bucket is not None:
            alias = bucket.value  # "day" | "week" | "month"
            basis_column = self._basis_column(entity, probe.window.basis)
            expr = f"CAST(date_trunc('{bucket.value}', {_ident(basis_column)}) AS DATE)"
            fragments.append((f"{expr} AS {_ident(alias)}", []))
            columns.append(FrameColumn(name=alias, ref=DimensionRef(f"time_bucket:{bucket.value}")))
            aliases.append(alias)
        return fragments, columns, aliases

    def _window_and_scope(
        self, probe: AggregationProbe, entity: EntityDef, state: _CompileState
    ) -> list[_Fragment]:
        basis_column = self._basis_column(entity, probe.window.basis)
        fragments: list[_Fragment] = [
            (f"{_ident(basis_column)} BETWEEN ? AND ?", [probe.window.range.start, probe.window.range.end])
        ]
        if not is_empty(probe.scope):
            fragments.append(self._compile_filter(probe.scope, entity, state))
        return fragments

    def _from_clause(self, entity: EntityDef, state: _CompileState) -> str:
        parts = [f"FROM {_ident(state.schema)}.{_ident(entity.base_view)}"]
        parts.extend(sql for sql, _ in state.joins_for(entity.name))
        return " ".join(parts)

    def _compile_cross_entity(
        self,
        probe: AggregationProbe,
        entities: list[EntityDef],
        components: list[_MetricComponent],
        metric_fragments: dict[str, list[_Fragment]],
        columns: list[FrameColumn],
        group_aliases: list[str],
        additive_aliases: set[str],
        state: _CompileState,
    ) -> CompiledQuery:
        """Ratio-of-sums across entity grains: one same-scope aggregate per
        entity, FULL OUTER joined on the shared group keys.

        Each block repeats the identical window, scope and group-by against its
        own base view — which is why the sides remain comparable — and the join
        uses ``IS NOT DISTINCT FROM`` so a NULL group value matches itself and
        neither side can silently drop a cell the other has."""
        block_sql: list[str] = []
        params: list[SqlParam] = []
        block_alias: dict[str, str] = {}
        for index, block_entity in enumerate(entities):
            alias = f"b{index}"
            block_alias[block_entity.name] = alias
            group_frags, _, _ = self._group_fragments(probe, block_entity, state)
            selects = group_frags + metric_fragments.get(block_entity.name, [])
            where_frags = self._window_and_scope(probe, block_entity, state)
            parts = [
                "SELECT " + ", ".join(sql for sql, _ in selects),
                self._from_clause(block_entity, state),
                "WHERE " + " AND ".join(sql for sql, _ in where_frags),
            ]
            if group_aliases:
                parts.append("GROUP BY " + ", ".join(_ident(a) for a in group_aliases))
            block_sql.append(" ".join(parts))
            params.extend(p for _, fragment_params in selects for p in fragment_params)
            joins = state.joins_for(block_entity.name)
            params.extend(p for _, fragment_params in joins for p in fragment_params)
            params.extend(p for _, fragment_params in where_frags for p in fragment_params)

        outer_selects: list[str] = []
        for alias in group_aliases:
            coalesced = ", ".join(f"{_ident(block_alias[e.name])}.{_ident(alias)}" for e in entities)
            outer_selects.append(f"COALESCE({coalesced}) AS {_ident(alias)}")
        for component in components:
            owner = _ident(block_alias[component.entity.name])
            outer_selects.append(f"{owner}.{_ident(component.alias)} AS {_ident(component.alias)}")

        sql_parts = ["SELECT " + ", ".join(outer_selects), f"FROM ({block_sql[0]}) AS b0"]
        for index in range(1, len(entities)):
            conditions = [
                f"{_ident(f'b{index}')}.{_ident(alias)} IS NOT DISTINCT FROM "
                f"COALESCE({', '.join(f'b{j}.{_ident(alias)}' for j in range(index))})"
                for alias in group_aliases
            ]
            on_sql = " AND ".join(conditions) if conditions else "TRUE"
            sql_parts.append(f"FULL OUTER JOIN ({block_sql[index]}) AS b{index} ON {on_sql}")
        order_sql = self._order_by_sql(probe.order_by, group_aliases, additive_aliases)
        if order_sql:
            sql_parts.append(order_sql)
        if probe.limit is not None:
            sql_parts.append(f"LIMIT {probe.limit + 1}")
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
        state = _CompileState(
            watermark=watermark, schema=schema, shape=SNAPSHOT_SHAPE, as_of=probe.as_of
        )
        aging_basis = probe.aging_basis if probe.aging_basis is not None else SERVICE
        aging_column = self._basis_column(entity, aging_basis)
        service_column = self._basis_column(entity, SERVICE)
        submission_column = self._basis_column(entity, SUBMISSION)

        state.bindings.update(self._ar_bucket_binding())
        runway_binding = self._filing_runway_bucket_binding(entity)
        state.bindings.update(runway_binding)
        for dim_id in runway_binding:
            state.binding_needs[dim_id] = _FILING_RUNWAY_DAYS

        # -- outer SELECT (text order first: its params precede the inner ones)
        select_fragments: list[_Fragment] = []
        columns: list[FrameColumn] = []
        group_aliases: list[str] = []
        for dim_ref in probe.dimensions:
            expr = self._dimension_expr(dim_ref.id, entity, state)
            select_fragments.append((f"{expr} AS {_ident(dim_ref.id)}", []))
            columns.append(FrameColumn(name=dim_ref.id, ref=dim_ref))
            group_aliases.append(dim_ref.id)
        components = self._metric_components(probe.measures, probe, entity, MetricKind.SNAPSHOT)
        cross_entity = [c for c in components if c.entity.name != entity.name]
        if cross_entity:
            raise GrainIncompatibleError(
                f"snapshot metric {cross_entity[0].ref.id!r} names a component at the "
                f"{cross_entity[0].entity.name!r} grain; snapshots aggregate one entity as-of",
                details={"metric": cross_entity[0].ref.id, "entity": cross_entity[0].entity.name},
            )
        metric_fragments, metric_columns = self._compile_components(components, state)
        select_fragments.extend(metric_fragments.get(entity.name, []))
        columns.extend(metric_columns)

        # -- inner subquery. The scope is compiled BEFORE the projection is
        # assembled (though it is appended after it) because a scope predicate
        # on a derived bucket is one of the things that decides which derived
        # columns the projection has to carry.
        as_of = probe.as_of
        scope_fragment: _Fragment | None = None
        if not is_empty(probe.scope):
            scope_fragment = self._compile_filter(probe.scope, entity, state)
        rollups = state.joins_for(entity.name)
        inner_params: list[SqlParam] = []
        projection = "*" if rollups else "c.*"
        replacements = self._as_of_flag_projections(entity)
        if replacements:
            projection += " REPLACE (" + ", ".join(replacements) + ")"
            inner_params.extend(as_of for _ in replacements)
        inner_parts: list[str] = [f"SELECT {projection}"]
        inner_parts[0] += f", datediff('day', c.{_ident(aging_column)}, ?) AS {_AGE_DAYS}"
        inner_params.append(as_of)
        if _FILING_RUNWAY_DAYS in state.needs:
            limit_column = self._require_declared_column(_FILING_LIMIT_DAYS_COLUMN, entity)
            inner_parts[0] += (
                f", datediff('day', ?, c.{_ident(service_column)} + c.{_ident(limit_column)}) "
                f"AS {_FILING_RUNWAY_DAYS}"
            )
            inner_params.append(as_of)
        inner_parts.append(f"FROM {_ident(schema)}.{_ident(entity.base_view)} AS c")
        for join_sql, join_params in rollups:
            inner_parts.append(join_sql)
            inner_params.extend(join_params)
        open_inventory = (
            f"WHERE c.{_ident(service_column)} <= ? "
            f"AND (c.{_ident(submission_column)} IS NULL OR c.{_ident(submission_column)} <= ?) "
            f"AND (c.{_ident(_RESOLVED_DATE_COLUMN)} IS NULL OR c.{_ident(_RESOLVED_DATE_COLUMN)} > ?)"
        )
        inner_parts.append(open_inventory)
        inner_params.extend([as_of, as_of, as_of])
        if scope_fragment is not None:
            scope_sql, scope_params = scope_fragment
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

    def _filing_runway_bucket_binding(self, entity: EntityDef) -> Mapping[str, str]:
        """``filing_runway_bucket`` → CASE expression over ``__filing_runway_days``.

        Two arms are not day ranges and are named constants here rather than
        parsed: ``filed`` (the claim has been submitted, so the clock it was
        racing is closed) and ``expired`` (unsubmitted, deadline already
        passed). Both must be declared in the catalog's bucket list, and the
        numeric edges must ascend, or the binding refuses rather than
        mislabelling claims.

        Returns ``{}`` when the catalog does not carry the dimension or the
        claim entity does not declare the plan's filing limit, so a catalog
        without the join simply has no such dimension rather than a broken one.
        """
        dim = self._catalog.dimension(_FILING_RUNWAY_BUCKET)
        if dim is None or not dim.buckets:
            return {}
        if _FILING_LIMIT_DAYS_COLUMN not in self._catalog.declared_columns(entity.name):
            return {}
        billed_dim = self._catalog.dimension(_BILLED_FLAG_DIMENSION)
        billed_column = None if billed_dim is None else billed_dim.column_for(entity.name)
        if billed_column is None:
            return {}
        labels = set(dim.buckets)
        missing = {_FILING_FILED_LABEL, _FILING_EXPIRED_LABEL} - labels
        if missing:
            raise UnsupportedConceptError(
                f"{_FILING_RUNWAY_BUCKET} catalog buckets are missing {sorted(missing)}",
                details={"dimension": _FILING_RUNWAY_BUCKET, "missing": sorted(missing)},
            )
        arms: list[str] = []
        edges: list[int] = []
        fallback: str | None = None
        for label in dim.buckets:
            if label in (_FILING_FILED_LABEL, _FILING_EXPIRED_LABEL):
                continue
            if label.endswith("+"):
                fallback = label
                continue
            _, _, upper = label.partition("-")
            edges.append(int(upper))
            arms.append(f"WHEN {_FILING_RUNWAY_DAYS} <= {int(upper)} THEN '{label}'")
        if fallback is None:
            raise UnsupportedConceptError(
                f"{_FILING_RUNWAY_BUCKET} catalog buckets declare no open-ended bucket",
                details={"dimension": _FILING_RUNWAY_BUCKET},
            )
        if edges != sorted(edges) or len(set(edges)) != len(edges):
            raise UnsupportedConceptError(
                f"{_FILING_RUNWAY_BUCKET} catalog buckets do not ascend: {edges}",
                details={"dimension": _FILING_RUNWAY_BUCKET, "edges": edges},
            )
        case = (
            f"CASE WHEN {_ident(billed_column)} THEN '{_FILING_FILED_LABEL}' "
            f"WHEN {_FILING_RUNWAY_DAYS} < 0 THEN '{_FILING_EXPIRED_LABEL}' "
            + " ".join(arms)
            + f" ELSE '{fallback}' END"
        )
        return {dim.id: case}

    def _as_of_flag_projections(self, entity: EntityDef) -> tuple[str, ...]:
        """``SELECT * REPLACE`` clauses restating watermark-derived flags at the
        probe's as-of. Each fragment carries exactly one ``?`` (the as-of).

        Only ``discharged_flag`` qualifies today: it restates a nullable date
        the base view still carries, so the snapshot builder can re-derive it
        the way it already re-derives ``resolved_date``'s effect instead of
        reading a truth that belongs to the watermark. ``status``,
        ``clean_claim`` and ``first_pass_paid`` cannot be re-derived from
        claim columns at all — they summarise remits and cash — and the
        catalog's ``status`` note records that.
        """
        dim = self._catalog.dimension(_DISCHARGED_FLAG_DIMENSION)
        if dim is None:
            return ()
        column = dim.column_for(entity.name)
        if column is None:
            return ()
        try:
            discharge_column = self._basis_column(entity, DISCHARGE)
        except DateBasisInvalidError:
            return ()
        return (
            f"(c.{_ident(discharge_column)} IS NOT NULL AND c.{_ident(discharge_column)} <= ?) "
            f"AS {_ident(column)}",
        )

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
