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
   the source — no catalog measure at the probe's entity, no declared
   column, and nothing the repository *advertises* that it computes (see
   "Answerability is negotiated" below) — prunes its probe from the plan
   with a surfaced warning naming the field and the reason: the
   honest-limitation path. The whole plan failing to resolve is an
   ``UNSUPPORTED_CONCEPT`` error. Comparison twins and transform steps that
   consumed pruned probes are pruned with them.
2. **Grain legality.** For every ratio metric, probe dimensions must be a
   subset of the contract's ``scope_dimensions`` (``GRAIN_INCOMPATIBLE``);
   ``time_bucket:*`` pseudo-dimensions are exempt. Additive money/count
   metrics accept any certified dimension. Independently, every group-by
   and scope dimension must be bound at the probe's entity grain — and, for
   a metric whose components live at a *second* entity, at that entity too,
   since each side aggregates the identical keys against its own base view.
3. **Date basis.** The probe's basis (window basis for flow, aging basis
   for snapshots) must be allowed by every referenced contract **and bound
   by the catalog at that contract's entity grain**
   (``DATE_BASIS_INVALID`` either way); a legal non-primary basis yields an
   ``alternate_basis_used`` warning so the header can label it, and when
   the primary was passed over because this warehouse does not bind it,
   the warning says that rather than implying a preference.
4. **Cardinality budget.** The product of catalog cardinality estimates
   over the probe's dimensions must fit the cell budget, or the probe must
   carry a top-N limit (``QUERY_BUDGET_EXCEEDED``); a limited over-budget
   probe warns that results are truncated. A dimension the probe's own
   scope pins to an enumerated value set (a conjunctive ``eq``/``in``)
   counts at that size rather than its catalog estimate — a group-by on
   four dimensions each pinned to one value is one cell, not their
   cross-product, and must not be refused for a budget it cannot spend.
5. **Exclusion intersection.** A user scope predicate touching a
   contract's internal exclusions or filtered-numerator dimensions (the
   "denial rate for denied claims" confusion) yields a warning surfaced
   with the answer. In the same step, any contract whose description
   declares a **population caveat** publishes that caveat as a warning on
   every answer that reads the metric — see below.
6. **Suppression plan.** The catalog's small-cell threshold is noted so the
   execution service applies it and the answer can say so.
7. **Capability negotiation.** Cohort semi-joins, server-side top-N,
   HAVING pushdown, and as-of reads are checked against
   ``repository.capabilities()`` (``SOURCE_CAPABILITY_UNSUPPORTED``).
   Probe-time derived measures and cross-entity components are negotiated
   against the same declaration, in step 1 where answerability is decided.
8. **Policy limits.** Simple plan-level budgets (probe count) enforce the
   read-only/row/time posture hooks (``QUERY_BUDGET_EXCEEDED``).

Answerability is negotiated, not assumed
========================================
Step 1 used to decide answerability from the catalog alone, plus one
hardcoded exception — a module constant naming ``open_balance_cents``,
the single field the DuckDB adapter computed at probe time on the day the
predicate was written. It was true then. It stopped being true when the
adapter grew seven probe-time derivations and cross-entity ratio-of-sums,
and the consequence was not a warning but a refusal: nine metric
contracts that the source executes correctly were pruned to an empty plan
and answered ``UNSUPPORTED_CONCEPT: no probe in the plan is answerable at
the source`` — a sentence about the source that the source disproves.

The fix is not a longer constant. It is §6.3's capability negotiation:
the repository advertises what it computes (``derived_measures``, each
with its entity and the probe shapes that can compute it, and
``cross_entity_ratio_of_sums``), and this pass reads the advertisement.
Three properties follow, and each is pinned by a test:

- **Silence is not permission.** A repository that advertises nothing
  extra gets exactly the old behaviour, refusal text included. Adapters
  that never learned the new tricks degrade honestly rather than being
  assumed capable.
- **Shape verdicts cannot disagree.** The adapter refuses a snapshot-age
  measure inside a flow aggregation; because the same declaration drives
  both, so does this pass — at plan time, with a §6.6 reason, instead of
  as an exception after the click.
- **Cross-entity is aggregation-only.** A component declared at another
  entity compiles to a same-scope block per entity joined on the shared
  group keys, which is why the group-by and scope dimensions must bind at
  *both* entities and the window basis must be bound at both (step 2). A
  snapshot aggregates one entity as-of a date and is never eligible.

Population caveats are structural, not per-metric
=================================================
Several contracts volunteer, in prose, that their population is not the one
a reader would assume — ``denial_rate`` excludes un-adjudicated claims,
``ar_balance`` values A/R at gross billed charges, ``days_in_ar`` is the
aging form rather than MAP FM-1. That honesty was authored and then never
left the pack: the live API published ``denial_rate`` at 49.94% with a
``warnings`` array carrying only basis and suppression notes, while the
contract's own caveat sat in a description nothing rendered.

So the convention is mechanical: a contract description may carry exactly
one sentence group introduced by ``Population caveat:``, and every answer
that reads that metric emits it as a warning. Prose stays prose (the
semantic fingerprint still excludes ``description``, so writing a caveat
never forces a version bump), but a caveat that exists is a caveat the
reader sees. Authoring one is a pack edit; publishing it is not optional.
"""

from __future__ import annotations

import re
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
from revi_kernel.capabilities import AnalyticalRepository, RepositoryCapabilities
from revi_kernel.errors import (
    DateBasisInvalidError,
    GrainIncompatibleError,
    QueryBudgetExceededError,
    SourceCapabilityUnsupportedError,
    UnsupportedConceptError,
)
from revi_kernel.filters import (
    And,
    FilterExpr,
    Predicate,
    PredicateOp,
    iter_cohorts,
    iter_predicates,
)
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.probes import AggregationProbe, ProbeShape, SnapshotProbe, probe_shape
from revi_kernel.refs import SERVICE, DateBasisRef, DimensionRef

_TIME_BUCKET_PREFIX = "time_bucket:"
_PRIOR_SUFFIX = "__prior"

#: The governed marker a metric contract uses to declare that its population
#: is narrower or wider than a reader would assume. Case-insensitive, one per
#: contract, terminated by the next sentence that starts a new topic — in
#: practice by the end of the paragraph the author wrote for it.
_POPULATION_CAVEAT_MARKER = re.compile(r"population caveat:\s*", re.IGNORECASE)
_CAVEAT_TERMINATORS = re.compile(
    r"(?:^|(?<=\s))(?:Primary basis is|Point of clarification:|Benchmark context:|"
    r"Denominator note|Valuation caveat:)",
)


def population_caveat(description: str) -> str | None:
    """The contract's declared population caveat, or ``None``.

    Pure text, deliberately: the caveat is prose the pack author wrote, and
    lifting it verbatim is the point — a paraphrase generated here would be
    a second, ungoverned statement of the population.
    """
    match = _POPULATION_CAVEAT_MARKER.search(description)
    if match is None:
        return None
    tail = description[match.end() :]
    stop = _CAVEAT_TERMINATORS.search(tail)
    if stop is not None:
        tail = tail[: stop.start()]
    return " ".join(tail.split()).rstrip() or None


@dataclass(frozen=True, slots=True)
class ValidationLimits:
    """Simple pre-execution budgets (§6.6 steps 4 and 8)."""

    max_group_cells: int = 5000
    max_probes: int = 16


DEFAULT_LIMITS = ValidationLimits()


@dataclass(frozen=True, slots=True)
class _FieldVerdict:
    """Whether one measure field is answerable, and where it aggregates.

    ``entity`` is set only when the field lives at a *second* entity — the
    cross-entity case step 2 then re-checks the group keys against.
    """

    resolved: bool
    entity: str | None = None
    reason: str | None = None


_RESOLVED = _FieldVerdict(True)


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
            self._publish_population_caveats(node, warnings)  # step 5 (cont.)
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
        reasons: dict[str, list[str]] = {}
        for node in plan.nodes:
            if not isinstance(node.probe, (AggregationProbe, SnapshotProbe)):
                continue
            entity = self._entity_for(node)
            shape = probe_shape(node.probe)
            unresolved: list[str] = []
            for contract in self._contracts_for(node):
                for field_id in (
                    *_measure_fields(contract.numerator),
                    *_measure_fields(contract.denominator),
                ):
                    verdict = self._resolve_field(field_id, entity, shape)
                    if verdict.resolved:
                        continue
                    unresolved.append(f"{contract.id}.{field_id} — {verdict.reason}")
                # A contract-internal filter dimension the catalog does not
                # define is exactly as fatal as an unresolvable measure
                # field — the adapter raises UNSUPPORTED_CONCEPT when it
                # compiles the predicate — but until now nothing checked it
                # here, so the failure surfaced as an error dialog after a
                # click rather than as an unanswerable probe before one.
                # (Round-1 review D4/D5: this is the ``filtered:`` half of
                # the conformance gap ``packs/base-rcm/NOTES.md`` names.)
                for dimension_id in sorted(_internal_filter_dimensions(contract)):
                    if self._catalog.dimension(dimension_id) is None:
                        unresolved.append(
                            f"{contract.id}[{dimension_id}] — the contract filters on a "
                            "dimension the catalog does not define"
                        )
            if unresolved:
                dropped.add(node.id)
                reasons[node.id] = unresolved
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
                why = "; ".join(reasons.get(node.id, ()))
                warnings.append(
                    f"probe '{node.id}' omitted: its measures are not answerable at the "
                    f"source for this catalog and this repository ({why})"
                )
        kept_nodes = tuple(node for node in plan.nodes if node.id not in dropped)
        if not any(not node.id.endswith(_PRIOR_SUFFIX) for node in kept_nodes):
            # The refusal carries the per-field reasons, not only the probe
            # ids: "no probe is answerable" was true and useless for as long
            # as the reason was a hardcoded predicate nobody could read from
            # the outside. What is missing — a catalog measure, a source
            # that computes it, a probe shape that can — belongs in the
            # error the caller renders.
            raise UnsupportedConceptError(
                "no probe in the plan is answerable at the source",
                details={
                    "dropped": sorted(dropped),
                    "reasons": sorted(
                        reason for items in reasons.values() for reason in items
                    ),
                },
            )
        kept_ids = {node.id for node in kept_nodes}
        steps = []
        for step in plan.transforms.steps:
            if all(inp in kept_ids for inp in step.inputs):
                steps.append(step)
                kept_ids.add(step.id)
        return replace(plan, nodes=kept_nodes, transforms=TransformPlan(steps=tuple(steps)))

    def _capabilities(self) -> RepositoryCapabilities:
        return self._repository.capabilities()

    def _resolve_field(
        self, field_id: str, entity: EntityDef, shape: ProbeShape
    ) -> _FieldVerdict:
        """Can this source answer ``field_id`` on a probe of this shape?

        Four ways a field can be answerable, in the order the adapter
        itself tries them: a catalog measure at the probe's own entity; a
        measure the repository *advertises* that it derives at probe time;
        either of those declared at a **second** entity, when the source
        advertises cross-entity aggregation and the probe is a flow
        aggregation; or a plain declared column of the entity.

        Everything else is unanswerable, and says which of those it
        failed — the warning the analyst reads is the reason, not a
        category.
        """
        caps = self._capabilities()
        measure = self._catalog.measure(field_id)
        if measure is not None:
            if measure.entity == entity.name:
                return _RESOLVED
            return self._resolve_foreign(field_id, measure.entity, shape, caps)

        derived = caps.derived_anywhere(field_id)
        home = caps.derived_at(field_id, entity.name)
        if home is not None:
            if home.computable_in(shape):
                return _RESOLVED
            return _FieldVerdict(
                False,
                None,
                f"the source computes {field_id!r} only in "
                f"{sorted(s.value for s in home.shapes)} probes, not {shape.value!r}",
            )
        if derived:
            # advertised, but at another entity — the cross-entity path,
            # subject to the same shape rule as its home declaration
            elsewhere = derived[0]
            if not elsewhere.computable_in(shape):
                return _FieldVerdict(
                    False,
                    None,
                    f"the source computes {field_id!r} only in "
                    f"{sorted(s.value for s in elsewhere.shapes)} probes, not {shape.value!r}",
                )
            return self._resolve_foreign(field_id, elsewhere.entity, shape, caps)

        if field_id in self._catalog.declared_columns(entity.name):
            return _RESOLVED
        return _FieldVerdict(
            False,
            None,
            f"{field_id!r} is neither a catalog measure at the {entity.name!r} grain, nor a "
            f"measure this source computes, nor a declared column of {entity.name!r}",
        )

    def _resolve_foreign(
        self,
        field_id: str,
        home_entity: str,
        shape: ProbeShape,
        caps: RepositoryCapabilities,
    ) -> _FieldVerdict:
        """A field that lives at another entity than the probe's own.

        Legal exactly where the source says it is: an aggregation probe
        compiles one same-scope block per entity and joins them on the
        shared group keys, so both sides read the identical window, scope
        and cuts. A snapshot has no such construction — it aggregates one
        entity as-of a date — so it is refused here whatever the source
        advertises, which is also what the adapter does.
        """
        if not caps.cross_entity_ratio_of_sums:
            return _FieldVerdict(
                False,
                None,
                f"{field_id!r} is defined at the {home_entity!r} grain and this source cannot "
                f"aggregate components across entity grains in one probe",
            )
        if shape is not ProbeShape.AGGREGATION:
            return _FieldVerdict(
                False,
                None,
                f"{field_id!r} is defined at the {home_entity!r} grain and a "
                f"{shape.value} probe aggregates a single entity",
            )
        if self._catalog.entity_named(home_entity) is None:
            return _FieldVerdict(
                False,
                None,
                f"{field_id!r} names entity {home_entity!r}, which this catalog does not define",
            )
        return _FieldVerdict(True, home_entity, None)

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
        self._check_cross_entity_grain(node, entity)

    def _check_cross_entity_grain(self, node: ProbeNode, entity: EntityDef) -> None:
        """The same legality, at the *other* entity of a cross-entity metric.

        A metric whose components span two grains compiles to one
        same-scope aggregate per entity, joined on the shared group keys —
        so every group-by and scope dimension has to exist on the second
        base view too, and the window's date basis has to be bound there.
        Checked here rather than discovered at execute time: the second
        side reading a different population (or failing to compile at all)
        is not something an answer can be honest about after the fact.
        """
        probe = node.probe
        if not isinstance(probe, AggregationProbe):
            return  # a snapshot is single-entity by construction (step 1)
        foreign: dict[str, str] = {}  # entity name → the field that put it there
        for contract in self._contracts_for(node):
            for field_id in (
                *_measure_fields(contract.numerator),
                *_measure_fields(contract.denominator),
            ):
                verdict = self._resolve_field(field_id, entity, ProbeShape.AGGREGATION)
                if verdict.resolved and verdict.entity is not None:
                    foreign.setdefault(verdict.entity, field_id)
        for entity_name, field_id in foreign.items():
            other = self._catalog.entity_named(entity_name)
            assert other is not None  # step 1 resolved it
            for ref in (
                *self._probe_dimensions(node),
                *(p.dimension for p in self._scope_predicates(node)),
            ):
                if ref.id.startswith(_TIME_BUCKET_PREFIX):
                    continue
                dim_def = self._catalog.dimension(ref.id)
                assert dim_def is not None  # step 1 resolved it
                if dim_def.column_for(other.name) is None:
                    raise GrainIncompatibleError(
                        f"probe '{node.id}' reads {field_id!r} at the {other.name!r} grain, but "
                        f"dimension {ref.id!r} is not available there — both sides of a "
                        "cross-grain metric must be cut by the same keys",
                        details={"dimension": ref.id, "entity": other.name, "probe": node.id},
                    )
            if other.date_basis_column(probe.window.basis) is None:
                raise DateBasisInvalidError(
                    f"probe '{node.id}' reads {field_id!r} at the {other.name!r} grain, but date "
                    f"basis {probe.window.basis.id!r} is not bound there — both sides of a "
                    "cross-grain metric must read the same window",
                    details={
                        "basis": probe.window.basis.id,
                        "entity": other.name,
                        "probe": node.id,
                    },
                )

    # -------------------------------------------------------- step 3: basis

    def _check_basis(self, node: ProbeNode, warnings: list[str]) -> None:
        """Contract legality *and* warehouse bindability, then the label.

        Legality was always checked here; bindability was not, and the gap
        was a live P0: ``denial_rate`` declares ``remit`` primary at the
        CLAIM grain, this warehouse binds ``remit`` only on the
        remit/transaction/denial views, and the year-over-year denial-rate
        question passed this pass and then died inside the SQL compiler
        with ``DATE_BASIS_INVALID``. A §12 code raised past the pass that
        exists to raise it is a §6.6 bypass, whatever the message says.

        The planner now reduces every basis to one the catalog binds
        (:mod:`revi_investigation.application.date_basis`); this step is the
        independent check that it did, so a hand-built plan, a replayed
        one, or a future planner change cannot slip past.
        """
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
            entity = self._catalog.entity(contract.entity_grain)
            if entity is not None and entity.date_basis_column(basis) is None:
                raise DateBasisInvalidError(
                    f"probe '{node.id}' reads {contract.id!r} on the {basis.id!r} {label}, but "
                    f"that basis is not bound at the {entity.name!r} grain in this warehouse "
                    f"(bound here: {[b for b, _ in entity.date_basis_columns]})",
                    details={
                        "metric": contract.id,
                        "basis": basis.id,
                        "entity": entity.name,
                        "probe": node.id,
                    },
                )
            if basis == contract.primary_date_basis:
                continue
            # An alternate basis is permitted and labeled (§5.3). When the
            # primary was passed over because the warehouse cannot read it,
            # the label says so — otherwise "primary is 'remit'" reads as a
            # choice somebody made rather than a binding that does not exist.
            primary_unbound = (
                entity is not None
                and entity.date_basis_column(contract.primary_date_basis) is None
            )
            if primary_unbound:
                assert entity is not None
                warnings.append(
                    f"alternate_basis_used: probe '{node.id}' computes {contract.id!r} on the "
                    f"{basis.id!r} {label} — its primary {contract.primary_date_basis.id!r} "
                    f"basis is not available at the {entity.name!r} grain in this warehouse"
                )
            else:
                warnings.append(
                    f"alternate_basis_used: probe '{node.id}' reads {contract.id!r} on the "
                    f"{basis.id!r} {label} (primary is {contract.primary_date_basis.id!r})"
                )

    # -------------------------------------------------- step 4: cardinality

    def _check_cardinality(self, node: ProbeNode, warnings: list[str]) -> None:
        probe = node.probe
        assert isinstance(probe, (AggregationProbe, SnapshotProbe))
        pinned = self._pinned_cardinalities(node)
        cells = 1
        for dim_ref in self._probe_dimensions(node):
            if dim_ref.id.startswith(_TIME_BUCKET_PREFIX):
                continue
            dim = self._catalog.dimension(dim_ref.id)
            assert dim is not None
            estimate = max(1, dim.cardinality_estimate)
            # A dimension the scope pins to an enumerated value set can only
            # produce that many groups, whatever the catalog estimate says.
            # Without this, grouping by four dimensions that are each pinned
            # to ONE value is scored at their full cross-product and refused
            # for a budget it cannot possibly spend — which is exactly the
            # shape of a drill into one detected cell.
            narrowed = pinned.get(dim_ref.id)
            if narrowed is not None:
                estimate = min(estimate, narrowed)
            cells = cells * estimate
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

    def _pinned_cardinalities(self, node: ProbeNode) -> dict[str, int]:
        """Per-dimension group counts the probe's own scope already forces.

        Only *conjunctive* equality/membership narrows a group-by: an
        ``eq``/``in`` predicate caps the distinct values at what it
        enumerates. Anything under an ``Or``/``Not`` widens or inverts and
        is ignored, so the estimate stays an upper bound (``iter_predicates``
        walks every clause, so the conjunctive test is explicit below).
        """
        capped: dict[str, int] = {}
        for predicate in self._top_level_predicates(node.probe.scope):
            if predicate.op not in (PredicateOp.EQ, PredicateOp.IN) or not predicate.values:
                continue
            distinct = len(set(predicate.values))
            existing = capped.get(predicate.dimension.id)
            capped[predicate.dimension.id] = (
                distinct if existing is None else min(existing, distinct)
            )
        return capped

    @staticmethod
    def _top_level_predicates(expr: FilterExpr) -> tuple[Predicate, ...]:
        """Predicates that hold unconditionally — the top-level AND chain."""
        if isinstance(expr, Predicate):
            return (expr,)
        if isinstance(expr, And):
            return tuple(
                p for clause in expr.clauses for p in PlanValidationService._top_level_predicates(clause)
            )
        return ()

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

    def _publish_population_caveats(self, node: ProbeNode, warnings: list[str]) -> None:
        """Every governed population caveat, on every answer that reads the
        metric (see the module docstring)."""
        for contract in self._contracts_for(node):
            caveat = population_caveat(contract.description)
            if caveat is None:
                continue
            warning = f"population_caveat: {contract.id} — {caveat}"
            if warning not in warnings:  # one probe per comparison side
                warnings.append(warning)

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
