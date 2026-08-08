"""The planner validation pass (design §6.6), in order, over a typed plan.

1. **Resolution.** Every probe dimension and scope-predicate dimension must
   resolve in the semantic catalog (``UNSUPPORTED_CONCEPT`` otherwise), and
   each node is graded on two independent axes, weakest wins:
   *certification* — any uncertified dimension anywhere in the chain
   downgrades the node to DISCOVERY (design §2.3); and *binding strength* —
   the pack's declared strength of each touched field as evidence for the
   concepts under investigation (§5.5), so certified-but-proxy evidence
   (a CARC standing in for coordination of benefits) cannot launder into a
   certified conclusion. Resolution also covers
   measure *fields*: a contract whose measure fields are not answerable at
   the source (no catalog measure at the probe's entity, no declared
   column) prunes its probe from the plan with a surfaced warning — the
   honest-limitation path — and the whole plan failing to resolve is an
   ``UNSUPPORTED_CONCEPT`` error. Comparison twins and transform steps that
   consumed pruned probes are pruned with them.
2. **Grain legality.** For every ratio metric, probe dimensions must be a
   subset of the contract's ``scope_dimensions`` (``GRAIN_INCOMPATIBLE``);
   ``time_bucket:*`` pseudo-dimensions are exempt. Additive money/count
   metrics accept any certified dimension. Independently, every group-by
   and scope dimension must be bound at the probe's entity grain.
3. **Date basis.** The probe's basis (window basis for flow, aging basis
   for snapshots) must be allowed by every referenced contract
   (``DATE_BASIS_INVALID``); a legal non-primary basis yields an
   ``alternate_basis_used`` warning so the header can label it.
4. **Cardinality budget.** The product of catalog cardinality estimates
   over the probe's dimensions must fit the cell budget, or the probe must
   carry a top-N limit (``QUERY_BUDGET_EXCEEDED``); a limited over-budget
   probe warns that results are truncated.
5. **Exclusion intersection.** A user scope predicate touching a
   contract's internal exclusions or filtered-numerator dimensions (the
   "denial rate for denied claims" confusion) yields a warning surfaced
   with the answer.
6. **Suppression plan.** The catalog's small-cell threshold is noted so the
   execution service applies it and the answer can say so.
7. **Capability negotiation.** Cohort semi-joins, server-side top-N,
   HAVING pushdown, and as-of reads are checked against
   ``repository.capabilities()`` (``SOURCE_CAPABILITY_UNSUPPORTED``).
8. **Policy limits.** Simple plan-level budgets (probe count) enforce the
   read-only/row/time posture hooks (``QUERY_BUDGET_EXCEEDED``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from revi_calculation_contracts.contract import (
    Count,
    Filtered,
    MeasureExpr,
    MetricContract,
    MetricKind,
)
from revi_catalog_contracts.model import CatalogSnapshot, DimensionKind, EntityDef
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.planning import (
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
)
from revi_investigation.domain.context import AnalysisSpec
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import (
    DateBasisInvalidError,
    GrainIncompatibleError,
    QueryBudgetExceededError,
    SourceCapabilityUnsupportedError,
    UnsupportedConceptError,
)
from revi_kernel.filters import Predicate, iter_cohorts, iter_predicates
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.probes import AggregationProbe, SnapshotProbe
from revi_kernel.refs import SERVICE, DateBasisRef, DimensionRef

_TIME_BUCKET_PREFIX = "time_bucket:"
_PRIOR_SUFFIX = "__prior"
_SNAPSHOT_DERIVED_FIELDS = frozenset({"open_balance_cents"})  # adapter-computed


@dataclass(frozen=True, slots=True)
class ValidationLimits:
    """Simple pre-execution budgets (§6.6 steps 4 and 8)."""

    max_group_cells: int = 5000
    max_probes: int = 16


DEFAULT_LIMITS = ValidationLimits()


@dataclass(frozen=True, slots=True)
class ValidatedPlan:
    plan: InvestigationPlan
    grades: tuple[tuple[str, EvidenceGrade], ...]
    warnings: tuple[str, ...]

    def grade_of(self, node_id: str) -> EvidenceGrade:
        for name, grade in self.grades:
            if name == node_id:
                return grade
        raise KeyError(f"no grade recorded for node {node_id!r}")


def _measure_fields(expr: MeasureExpr | None) -> tuple[str, ...]:
    if expr is None or isinstance(expr, Count):
        return ()
    if isinstance(expr, Filtered):
        return _measure_fields(expr.inner)
    return (expr.field.id,)  # Sum | CountDistinct


def _internal_filter_dimensions(contract: MetricContract) -> frozenset[str]:
    """Dimensions inside the contract's own definition: exclusions plus any
    Filtered wrappers on numerator/denominator."""
    dims: set[str] = set()
    if contract.exclusions is not None:
        dims.update(p.dimension.id for p in iter_predicates(contract.exclusions))
    for expr in (contract.numerator, contract.denominator):
        if isinstance(expr, Filtered):
            dims.update(p.dimension.id for p in iter_predicates(expr.where))
    return frozenset(dims)


class PlanValidationService:
    """The full §6.6 pass. Stateless; safe to share."""

    def __init__(
        self,
        catalog: CatalogSnapshot,
        pack: PackPort,
        repository: AnalyticalRepository,
        limits: ValidationLimits = DEFAULT_LIMITS,
    ) -> None:
        self._catalog = catalog
        self._pack = pack
        self._repository = repository
        self._limits = limits

    # ------------------------------------------------------------------ api

    def validate(self, plan: InvestigationPlan, spec: AnalysisSpec) -> ValidatedPlan:
        warnings: list[str] = []

        plan = self._prune_unanswerable(plan, warnings)

        grades: list[tuple[str, EvidenceGrade]] = []
        for node in plan.nodes:
            grade = self._resolve_and_grade(node, spec.concepts)  # step 1
            self._check_grain(node)  # step 2
            self._check_basis(node, warnings)  # step 3
            self._check_cardinality(node, warnings)  # step 4
            self._check_exclusion_intersection(node, warnings)  # step 5
            grades.append((node.id, grade))

        self._note_suppression(plan, warnings)  # step 6
        self._check_capabilities(plan)  # step 7
        self._check_limits(plan)  # step 8

        return ValidatedPlan(plan=plan, grades=tuple(grades), warnings=tuple(warnings))

    # ----------------------------------------------------- step 1: resolve

    def _entity_for(self, node: ProbeNode) -> EntityDef:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        entity = self._catalog.entity(probe.grain.entity)
        if entity is None:
            raise UnsupportedConceptError(
                f"no catalog entity is bound to grain {probe.grain.entity.value!r}",
                details={"grain": probe.grain.entity.value, "probe": node.id},
            )
        return entity

    def _contracts_for(self, node: ProbeNode) -> tuple[MetricContract, ...]:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        contracts: list[MetricContract] = []
        for ref in probe.measures:
            contract = self._pack.metric(ref.id)
            if contract is None:
                raise UnsupportedConceptError(
                    f"unknown metric {ref.id!r}", details={"metric": ref.id, "probe": node.id}
                )
            contracts.append(contract)
        return tuple(contracts)

    def _prune_unanswerable(self, plan: InvestigationPlan, warnings: list[str]) -> InvestigationPlan:
        """Drop probes whose measure fields cannot be answered at the source
        (with a surfaced warning); an empty result is UNSUPPORTED_CONCEPT."""
        dropped: set[str] = set()
        for node in plan.nodes:
            if not isinstance(node.probe, (AggregationProbe, SnapshotProbe)):
                continue
            entity = self._entity_for(node)
            unresolved: list[str] = []
            for contract in self._contracts_for(node):
                for field_id in (
                    *_measure_fields(contract.numerator),
                    *_measure_fields(contract.denominator),
                ):
                    if self._field_resolves(field_id, entity, snapshot=isinstance(node.probe, SnapshotProbe)):
                        continue
                    unresolved.append(f"{contract.id}.{field_id}")
            if unresolved:
                dropped.add(node.id)
        # a pruned base prunes its comparison twin, and vice versa
        for node_id in tuple(dropped):
            twin = (
                node_id.removesuffix(_PRIOR_SUFFIX)
                if node_id.endswith(_PRIOR_SUFFIX)
                else f"{node_id}{_PRIOR_SUFFIX}"
            )
            if any(node.id == twin for node in plan.nodes):
                dropped.add(twin)
        if not dropped:
            return plan
        for node in plan.nodes:
            if node.id in dropped and not node.id.endswith(_PRIOR_SUFFIX):
                warnings.append(
                    f"probe '{node.id}' omitted: its measures are not answerable at the "
                    "source for this catalog (probe-time derived fields not yet available)"
                )
        kept_nodes = tuple(node for node in plan.nodes if node.id not in dropped)
        if not any(not node.id.endswith(_PRIOR_SUFFIX) for node in kept_nodes):
            raise UnsupportedConceptError(
                "no probe in the plan is answerable at the source",
                details={"dropped": sorted(dropped)},
            )
        kept_ids = {node.id for node in kept_nodes}
        steps = []
        for step in plan.transforms.steps:
            if all(inp in kept_ids for inp in step.inputs):
                steps.append(step)
                kept_ids.add(step.id)
        return replace(plan, nodes=kept_nodes, transforms=TransformPlan(steps=tuple(steps)))

    def _field_resolves(self, field_id: str, entity: EntityDef, *, snapshot: bool) -> bool:
        if snapshot and field_id in _SNAPSHOT_DERIVED_FIELDS:
            return True
        measure = self._catalog.measure(field_id)
        if measure is not None:
            return measure.entity == entity.name
        return field_id in self._catalog.declared_columns(entity.name)

    def _scope_predicates(self, node: ProbeNode) -> tuple[Predicate, ...]:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        return tuple(iter_predicates(probe.scope))

    def _probe_dimensions(self, node: ProbeNode) -> tuple[DimensionRef, ...]:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        return probe.dimensions

    def _resolve_and_grade(self, node: ProbeNode, concepts: tuple[str, ...]) -> EvidenceGrade:
        uncertified = False
        for ref in (
            *self._probe_dimensions(node),
            *(p.dimension for p in self._scope_predicates(node)),
        ):
            if ref.id.startswith(_TIME_BUCKET_PREFIX):
                continue
            dim = self._catalog.dimension(ref.id)
            if dim is None:
                raise UnsupportedConceptError(
                    f"unknown dimension {ref.id!r}",
                    details={"dimension": ref.id, "probe": node.id},
                )
            if not dim.certified:
                uncertified = True
        catalog_grade = EvidenceGrade.DISCOVERY if uncertified else EvidenceGrade.DIRECT
        return min_grade(catalog_grade, *self._binding_grades(node, concepts))

    def _binding_grades(
        self, node: ProbeNode, concepts: tuple[str, ...]
    ) -> tuple[EvidenceGrade, ...]:
        """Declared binding strengths for the concepts under investigation
        (design §5.5), over every field this probe actually touches.

        Certification says a field is trustworthy; a *binding* says how well
        that field stands in for the concept being asked about. A COB probe
        cut by CARC is perfectly certified data and still only proxy
        evidence that a COB problem exists — the code is the payer's
        assertion about coverage, not the coverage. Without this the grade
        law would let proxy evidence carry a certified conclusion, which is
        exactly the laundering §5.5 forbids.

        Fields the pack declares no binding for contribute nothing: silence
        is not a downgrade.
        """
        if not concepts:
            return ()
        fields: set[str] = set()
        for ref in (
            *self._probe_dimensions(node),
            *(p.dimension for p in self._scope_predicates(node)),
        ):
            if not ref.id.startswith(_TIME_BUCKET_PREFIX):
                fields.add(ref.id)
        for contract in self._contracts_for(node):
            fields.add(contract.id)
            fields.update(_measure_fields(contract.numerator))
            fields.update(_measure_fields(contract.denominator))
            fields.update(_internal_filter_dimensions(contract))
        grades: list[EvidenceGrade] = []
        for concept_id in concepts:
            for field_id in sorted(fields):
                strength = self._pack.binding_strength(concept_id, field_id)
                if strength is not None:
                    grades.append(strength)
        return tuple(grades)

    # ------------------------------------------------------- step 2: grain

    def _check_grain(self, node: ProbeNode) -> None:
        probe = node.probe
        entity = self._entity_for(node)
        contracts = self._contracts_for(node)
        expected_kind = MetricKind.SNAPSHOT if isinstance(probe, SnapshotProbe) else MetricKind.FLOW
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        for contract in contracts:
            if contract.kind is not expected_kind:
                raise GrainIncompatibleError(
                    f"metric {contract.id!r} is a {contract.kind.value} metric and cannot run "
                    f"on a {expected_kind.value} probe",
                    details={"metric": contract.id, "probe": node.id},
                )
            if contract.entity_grain is not probe.grain.entity:
                raise GrainIncompatibleError(
                    f"metric {contract.id!r} is defined at the {contract.entity_grain.value!r} "
                    f"grain, but probe '{node.id}' runs at {probe.grain.entity.value!r}",
                    details={"metric": contract.id, "probe": node.id},
                )
            if contract.is_ratio:
                for dim in self._probe_dimensions(node):
                    if dim.id.startswith(_TIME_BUCKET_PREFIX):
                        continue
                    if not contract.allows_dimension(dim):
                        raise GrainIncompatibleError(
                            f"dimension {dim.id!r} is not a legal scope dimension for ratio "
                            f"metric {contract.id!r}",
                            details={"metric": contract.id, "dimension": dim.id, "probe": node.id},
                        )
        # every group-by and scope dimension must be bound at the probe grain
        for ref in (
            *self._probe_dimensions(node),
            *(p.dimension for p in self._scope_predicates(node)),
        ):
            if ref.id.startswith(_TIME_BUCKET_PREFIX):
                continue
            dim_def = self._catalog.dimension(ref.id)
            assert dim_def is not None  # step 1 resolved it
            if dim_def.kind is DimensionKind.DERIVED_BUCKET and isinstance(probe, SnapshotProbe):
                continue
            if dim_def.column_for(entity.name) is None:
                raise GrainIncompatibleError(
                    f"dimension {ref.id!r} is not available at the {entity.name!r} grain",
                    details={"dimension": ref.id, "entity": entity.name, "probe": node.id},
                )

    # -------------------------------------------------------- step 3: basis

    def _check_basis(self, node: ProbeNode, warnings: list[str]) -> None:
        probe = node.probe
        if isinstance(probe, SnapshotProbe):
            basis: DateBasisRef = probe.aging_basis if probe.aging_basis is not None else SERVICE
            label = "aging basis"
        elif isinstance(probe, AggregationProbe):
            basis = probe.window.basis
            label = "basis"
        else:  # pragma: no cover - row evidence probes are not planned yet
            return
        for contract in self._contracts_for(node):
            if not contract.allows_date_basis(basis):
                raise DateBasisInvalidError(
                    f"date basis {basis.id!r} is not allowed for metric {contract.id!r} "
                    f"(allowed: {[b.id for b in contract.allowed_date_bases]})",
                    details={"metric": contract.id, "basis": basis.id, "probe": node.id},
                )
            if basis != contract.primary_date_basis:
                warnings.append(
                    f"alternate_basis_used: probe '{node.id}' reads {contract.id!r} on the "
                    f"{basis.id!r} {label} (primary is {contract.primary_date_basis.id!r})"
                )

    # -------------------------------------------------- step 4: cardinality

    def _check_cardinality(self, node: ProbeNode, warnings: list[str]) -> None:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        cells = 1
        for dim_ref in self._probe_dimensions(node):
            if dim_ref.id.startswith(_TIME_BUCKET_PREFIX):
                continue
            dim = self._catalog.dimension(dim_ref.id)
            assert dim is not None
            cells = cells * max(1, dim.cardinality_estimate)
            if cells > self._limits.max_group_cells * 1000:
                break  # avoid pointless overflow-scale products
        if cells <= self._limits.max_group_cells:
            return
        limit = probe.limit if isinstance(probe, AggregationProbe) else None
        if limit is None:
            raise QueryBudgetExceededError(
                f"probe '{node.id}' groups an estimated {cells} cells, over the "
                f"{self._limits.max_group_cells}-cell budget; a top-N limit is required",
                details={"probe": node.id, "cells": cells, "budget": self._limits.max_group_cells},
            )
        warnings.append(
            f"probe '{node.id}' truncated to the top {limit} of an estimated {cells} cells"
        )

    # ------------------------------------------- step 5: exclusion overlap

    def _check_exclusion_intersection(self, node: ProbeNode, warnings: list[str]) -> None:
        scope_dims = {p.dimension.id for p in self._scope_predicates(node)}
        if not scope_dims:
            return
        for contract in self._contracts_for(node):
            overlap = scope_dims & _internal_filter_dimensions(contract)
            for dim in sorted(overlap):
                warnings.append(
                    f"scope on '{dim}' interacts with metric '{contract.id}' — the contract "
                    "already constrains that dimension internally (exclusions or numerator "
                    "filter); the result reflects both conditions"
                )

    # ------------------------------------------------- step 6: suppression

    def _note_suppression(self, plan: InvestigationPlan, warnings: list[str]) -> None:
        if any(self._probe_dimensions(node) for node in plan.nodes):
            threshold = self._catalog.suppression.threshold
            warnings.append(
                f"suppression: cells counting fewer than {threshold} entities are suppressed "
                "before results leave the engine"
            )

    # ------------------------------------------------ step 7: capabilities

    def _check_capabilities(self, plan: InvestigationPlan) -> None:
        caps = self._repository.capabilities()
        for node in plan.nodes:
            probe = node.probe
            if not isinstance(probe, (AggregationProbe, SnapshotProbe)):
                continue
            if any(iter_cohorts(probe.scope)) and not caps.cohort_semijoin:
                raise SourceCapabilityUnsupportedError(
                    f"probe '{node.id}' needs a cohort semi-join the source does not support",
                    details={"probe": node.id, "capability": "cohort_semijoin"},
                )
            if isinstance(probe, AggregationProbe):
                if probe.limit is not None and not caps.server_side_top_n:
                    raise SourceCapabilityUnsupportedError(
                        f"probe '{node.id}' needs server-side top-N the source does not support",
                        details={"probe": node.id, "capability": "server_side_top_n"},
                    )
                if probe.having and not caps.having_pushdown:
                    raise SourceCapabilityUnsupportedError(
                        f"probe '{node.id}' needs HAVING pushdown the source does not support",
                        details={"probe": node.id, "capability": "having_pushdown"},
                    )
            if isinstance(probe, SnapshotProbe) and not caps.as_of_reads:
                raise SourceCapabilityUnsupportedError(
                    f"probe '{node.id}' needs as-of reads the source does not support",
                    details={"probe": node.id, "capability": "as_of_reads"},
                )

    # ------------------------------------------------------ step 8: limits

    def _check_limits(self, plan: InvestigationPlan) -> None:
        if len(plan.nodes) > self._limits.max_probes:
            raise QueryBudgetExceededError(
                f"plan holds {len(plan.nodes)} probes, over the {self._limits.max_probes} "
                "probe budget",
                details={"probes": len(plan.nodes), "budget": self._limits.max_probes},
            )
        # read-only posture and row/time budgets are enforced at the
        # repository boundary (design §15); this plan-level hook exists so
        # tenants can tighten limits without touching adapters.
