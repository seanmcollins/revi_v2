"""AnalysisSpec → typed InvestigationPlan (design §8.1 step 8) and plan diffs.

Two planning modes:

**Direct metric query** — the spec's measures compile into probes grouped by
(metric kind, entity grain, effective date basis): FLOW groups become one
:class:`AggregationProbe` each; SNAPSHOT groups become a
:class:`SnapshotProbe` as-of the watermark's newest data date.

**Playbook mode** — the pack playbook's probe templates expand through the
same grouping. Template semantics:

- ``window=None`` inherits the spec window. A template's own window applies
  only when the analyst gave no explicit window (``window_explicit=False``);
  an explicit analyst window governs every probe — the user's stated scope
  always wins over playbook defaults.
- ``basis_override`` wins over the spec basis; otherwise the spec basis
  applies when the contract allows it, else the contract's primary basis.
- ``top_n`` becomes the probe ``limit`` plus a server-side ordering by the
  group's first additive measure (descending) when one exists.
- ``$dimension`` placeholders bind to the spec's interpreted dimensions;
  templates needing a dimension parameter are skipped (with a surfaced
  note) when the spec names none.

**Comparison pairing** — when the context carries a comparison, every flow
probe gets a ``<id>__prior`` twin over the deterministically derived prior
range, and the playbook's ``compare`` transform step consumes the pairs.
Snapshot probes are a point in time and are never paired.

**Transform steps** are emitted only when they can be fully typed against
the planned probes; anything else becomes an honest plan note (surfaced as
a warning), never a silent drop. Playbook arg conventions handled here:
``by: impact_cents`` resolves to the paired money measure's ``__delta``
column on compare outputs, ranked ascending so the most negative movement
(the biggest decline of a higher-is-good measure) comes first.

``plan_hash`` is a SHA-256 over the sorted probe hashes — stable across
runs, sensitive to any probe change (the evidence-cache and plan-diff key).

**Content-stable probe hashing.** ``ProbeNode.hash`` (the cache/diff/replay
identity) hashes a *normalized* projection of the probe: predicate
``origin_turn`` tags are stripped and ``InCohort`` cohort refs are reduced
to their *definition* (volatile identity — cohort id, size, pinned
materialization handle — removed, nested predicates normalized). What a
probe retrieves at a given (watermark, pack) depends only on its logical
content, never on which turn asked or which uuid a re-materialization drew;
identical drill-downs therefore share cache entries and replays reproduce
identical plan hashes (§7.9, §18.1-15). Repository execution still receives
the full probe with its pinned cohort handle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from revi_calculation_contracts.contract import MetricContract, MetricKind, MetricUnit
from revi_investigation.application.capability_ports import (
    PackPort,
    PlaybookSpec,
    ProbeTemplateSpec,
    TransformStepSpec,
)
from revi_investigation.domain.context import AnalysisSpec
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.errors import UnsupportedConceptError
from revi_kernel.filters import (
    And,
    FilterExpr,
    InCohort,
    Not,
    Or,
    Predicate,
    and_merge,
)
from revi_kernel.probes import (
    AggregationProbe,
    EvidenceProbe,
    Ordering,
    SnapshotProbe,
    probe_hash,
)
from revi_kernel.refs import (
    DateBasisRef,
    DimensionRef,
    EntityGrain,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
)
from revi_kernel.scope import (
    AbsoluteRange,
    Comparison,
    ComparisonKind,
    TimeWindow,
    derive_comparison,
    resolve_window,
)

_DIMENSION_PARAM = "$dimension"
_IMPACT_ARG = "impact_cents"
_PRIOR_SUFFIX = "__prior"

_NORMALIZED_ORIGIN = ReferentId(value="__cohort__", kind=ReferentKind.COHORT)


def _normalize_scope(expr: FilterExpr) -> FilterExpr:
    """Strip turn provenance and volatile cohort identity for hashing."""
    if isinstance(expr, Predicate):
        return replace(expr, origin_turn=None)
    if isinstance(expr, InCohort):
        definition = expr.cohort.definition
        normalized = CohortDefinition(
            entity=definition.entity,
            scope=_normalize_scope(definition.scope),
            window=definition.window,
        )
        return InCohort(
            cohort=CohortRef(
                id="__cohort__",
                definition=normalized,
                origin=_NORMALIZED_ORIGIN,
                size=0,
                pinned=None,
            ),
            origin_turn=None,
        )
    if isinstance(expr, And):
        return And(tuple(_normalize_scope(clause) for clause in expr.clauses))
    if isinstance(expr, Or):
        return Or(tuple(_normalize_scope(clause) for clause in expr.clauses))
    return Not(_normalize_scope(expr.clause))


def content_probe_hash(probe: EvidenceProbe) -> str:
    """The content-stable probe identity (see module docstring)."""
    normalized: EvidenceProbe = replace(probe, scope=_normalize_scope(probe.scope))
    return probe_hash(normalized)


@dataclass(frozen=True, slots=True)
class ProbeNode:
    id: str
    probe: EvidenceProbe
    purpose: str
    consumes_cohorts: tuple[str, ...] = ()

    @property
    def hash(self) -> str:
        return content_probe_hash(self.probe)


@dataclass(frozen=True, slots=True)
class TransformPlanStep:
    """A fully typed transform application: named inputs, string args."""

    id: str
    operator: str
    inputs: tuple[str, ...]
    args: tuple[tuple[str, str], ...] = ()

    def arg(self, name: str) -> str | None:
        for key, value in self.args:
            if key == name:
                return value
        return None


@dataclass(frozen=True, slots=True)
class TransformPlan:
    steps: tuple[TransformPlanStep, ...] = ()


@dataclass(frozen=True, slots=True)
class InvestigationPlan:
    nodes: tuple[ProbeNode, ...]
    transforms: TransformPlan
    playbook_id: str | None = None
    notes: tuple[str, ...] = ()

    def node(self, node_id: str) -> ProbeNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(f"no probe node {node_id!r} in plan")

    @property
    def plan_hash(self) -> str:
        blob = "\n".join(sorted(node.hash for node in self.nodes))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanDiff:
    added: tuple[ProbeNode, ...]
    removed: tuple[ProbeNode, ...]
    unchanged: tuple[ProbeNode, ...]


# ---------------------------------------------------------------------------
# probe grouping


@dataclass(frozen=True, slots=True)
class _MetricGroup:
    kind: MetricKind
    grain_entity: str  # EntityGrain value
    basis: DateBasisRef
    contracts: tuple[MetricContract, ...]


class BuildInvestigationPlanService:
    """Compile an AnalysisSpec (plus optional playbook) into a typed plan."""

    def __init__(self, pack: PackPort) -> None:
        self._pack = pack

    # ------------------------------------------------------------------ api

    def build(
        self,
        spec: AnalysisSpec,
        *,
        playbook_id: str | None = None,
        window_explicit: bool = True,
    ) -> InvestigationPlan:
        # Explicit measures win: a spec that names metrics (first-turn
        # interpretation or a Pivot refinement) plans a direct query even
        # when a playbook context is inherited from the parent turn.
        if not spec.measures and playbook_id is not None:
            playbook = self._pack.playbook(playbook_id)
            if playbook is None:
                raise UnsupportedConceptError(
                    f"unknown playbook {playbook_id!r}", details={"playbook": playbook_id}
                )
            return self._build_playbook(spec, playbook, window_explicit=window_explicit)
        if not spec.measures and playbook_id is None:
            raise UnsupportedConceptError(
                "the question resolved to no governed measures or playbook",
                details={"reason": "empty measures"},
            )
        return self._build_direct(spec)

    # --------------------------------------------------------------- direct

    def _build_direct(self, spec: AnalysisSpec) -> InvestigationPlan:
        if not spec.measures:
            raise UnsupportedConceptError(
                "the question resolved to no governed measures or playbook",
                details={"reason": "empty measures"},
            )
        notes: list[str] = []
        nodes: list[ProbeNode] = []
        groups = self._group_metrics(
            tuple(ref.id for ref in spec.measures), spec, basis_override=None
        )
        dimensions = spec.dimensions
        for index, group in enumerate(groups):
            node_id = "main" if index == 0 else f"main_{index + 1}"
            nodes.append(
                self._node_for_group(
                    node_id,
                    group,
                    spec,
                    dimensions=dimensions,
                    window=spec.context.window,
                    limit=spec.limit,
                    rank_by=spec.rank_by,
                    rank_descending=spec.rank_descending,
                    purpose="direct metric query",
                )
            )
        steps = self._pair_comparisons(nodes, spec)
        return InvestigationPlan(
            nodes=tuple(nodes),
            transforms=TransformPlan(steps=tuple(steps)),
            playbook_id=None,
            notes=tuple(notes),
        )

    # ------------------------------------------------------------- playbook

    def _build_playbook(
        self, spec: AnalysisSpec, playbook: PlaybookSpec, *, window_explicit: bool
    ) -> InvestigationPlan:
        notes: list[str] = []
        nodes: list[ProbeNode] = []
        node_contracts: dict[str, tuple[MetricContract, ...]] = {}
        anchor = spec.context.watermark.loaded_at.date()

        for template in playbook.probes:
            dimensions = self._template_dimensions(template, spec)
            if dimensions is None:
                notes.append(
                    f"probe template '{template.id}' skipped: it parameterizes a dimension "
                    "and the question names none"
                )
                continue
            window = spec.context.window
            if template.window is not None and not window_explicit:
                window = resolve_window(
                    template.window,
                    anchor,
                    basis=spec.context.window.basis,
                    calendar=spec.context.window.calendar,
                )
            groups = self._group_metrics(
                template.metric_ids, spec, basis_override=template.basis_override
            )
            for index, group in enumerate(groups):
                node_id = template.id if index == 0 else f"{template.id}_{index + 1}"
                nodes.append(
                    self._node_for_group(
                        node_id,
                        group,
                        spec,
                        dimensions=dimensions,
                        window=window,
                        limit=template.top_n,
                        rank_by=None,
                        rank_descending=True,
                        purpose=template.purpose or f"playbook probe {template.id}",
                    )
                )
                node_contracts[node_id] = group.contracts

        prior_steps = self._pair_comparisons(nodes, spec)
        steps, transform_notes = self._playbook_transforms(
            playbook, nodes, node_contracts, prior_steps, spec
        )
        notes.extend(transform_notes)
        return InvestigationPlan(
            nodes=tuple(nodes),
            transforms=TransformPlan(steps=tuple(steps)),
            playbook_id=playbook.id,
            notes=tuple(notes),
        )

    # ------------------------------------------------------------- helpers

    def _contract(self, metric_id: str) -> MetricContract:
        contract = self._pack.metric(metric_id)
        if contract is None:
            raise UnsupportedConceptError(
                f"unknown metric {metric_id!r}", details={"metric": metric_id}
            )
        return contract

    def _group_metrics(
        self, metric_ids: tuple[str, ...], spec: AnalysisSpec, *, basis_override: str | None
    ) -> tuple[_MetricGroup, ...]:
        """Group metrics by (kind, entity grain, effective basis) — one probe
        per group; a probe can only aggregate one entity at one basis."""
        spec_basis = spec.context.window.basis
        grouped: dict[tuple[MetricKind, str, str], list[MetricContract]] = {}
        order: list[tuple[MetricKind, str, str]] = []
        for metric_id in metric_ids:
            contract = self._contract(metric_id)
            if basis_override is not None:
                basis = DateBasisRef(basis_override.lower())
            elif contract.allows_date_basis(spec_basis):
                basis = spec_basis
            else:
                basis = contract.primary_date_basis
            key = (contract.kind, contract.entity_grain.value, basis.id)
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(contract)
        return tuple(
            _MetricGroup(
                kind=key[0],
                grain_entity=key[1],
                basis=DateBasisRef(key[2]),
                contracts=tuple(grouped[key]),
            )
            for key in order
        )

    def _template_dimensions(
        self, template: ProbeTemplateSpec, spec: AnalysisSpec
    ) -> tuple[DimensionRef, ...] | None:
        """Resolve template dimensions; ``None`` means the template cannot
        bind (a ``$dimension`` parameter with nothing to bind to)."""
        resolved: list[DimensionRef] = []
        for dim in template.dimensions:
            if dim == _DIMENSION_PARAM:
                if not spec.dimensions:
                    return None
                resolved.extend(d for d in spec.dimensions if d not in resolved)
            else:
                ref = DimensionRef(dim)
                if ref not in resolved:
                    resolved.append(ref)
        return tuple(resolved)

    def _node_for_group(
        self,
        node_id: str,
        group: _MetricGroup,
        spec: AnalysisSpec,
        *,
        dimensions: tuple[DimensionRef, ...],
        window: TimeWindow,
        limit: int | None,
        rank_by: MetricRef | None,
        rank_descending: bool,
        purpose: str,
    ) -> ProbeNode:
        measures = tuple(MetricRef(contract.id) for contract in group.contracts)
        scope = spec.context.effective_scope()
        if spec.context.cohort is not None:
            # the active cohort is part of every probe's population (§7.5)
            scope = and_merge(scope, InCohort(cohort=spec.context.cohort))
        grain_entity = EntityGrain(group.grain_entity)
        window = replace(window, basis=group.basis) if window.basis != group.basis else window

        if group.kind is MetricKind.SNAPSHOT:
            aging = self._aging_basis(group, spec)
            probe: EvidenceProbe = SnapshotProbe(
                measures=measures,
                dimensions=dimensions,
                scope=scope,
                as_of=spec.context.watermark.newest_data_date,
                grain=Grain(grain_entity),
                aging_basis=aging,
            )
            return ProbeNode(id=node_id, probe=probe, purpose=purpose)

        order_by = self._ordering(group, limit, rank_by, rank_descending)
        probe = AggregationProbe(
            measures=measures,
            dimensions=dimensions,
            scope=scope,
            window=window,
            grain=Grain(grain_entity, spec.context.grain.time_bucket),
            order_by=order_by,
            limit=limit,
        )
        return ProbeNode(id=node_id, probe=probe, purpose=purpose)

    def _aging_basis(self, group: _MetricGroup, spec: AnalysisSpec) -> DateBasisRef:
        """Snapshot aging basis: the spec basis when every snapshot contract
        allows it, else the group's primary basis."""
        spec_basis = spec.context.window.basis
        if all(contract.allows_date_basis(spec_basis) for contract in group.contracts):
            return spec_basis
        return group.contracts[0].primary_date_basis

    def _ordering(
        self,
        group: _MetricGroup,
        limit: int | None,
        rank_by: MetricRef | None,
        rank_descending: bool,
    ) -> tuple[Ordering, ...]:
        if limit is None:
            return ()
        additive = {c.id for c in group.contracts if not c.is_ratio}
        if rank_by is not None and rank_by.id in additive:
            return (Ordering(by=rank_by, descending=rank_descending),)
        for contract in group.contracts:
            if contract.id in additive:
                return (Ordering(by=MetricRef(contract.id), descending=True),)
        return ()  # ratio-only probe: limit still caps rows; kernel ranks later

    # ---------------------------------------------------------- comparisons

    def _pair_comparisons(
        self, nodes: list[ProbeNode], spec: AnalysisSpec
    ) -> list[TransformPlanStep]:
        """Give every flow probe a prior-window twin and a compare step."""
        comparison = spec.context.comparison
        if comparison is None:
            return []
        steps: list[TransformPlanStep] = []
        for node in list(nodes):
            probe = node.probe
            if not isinstance(probe, AggregationProbe):
                continue
            prior_range = self._prior_range(probe.window, spec.context.window, comparison)
            prior_probe = replace(
                probe,
                window=TimeWindow(
                    basis=probe.window.basis,
                    range=prior_range,
                    requested=None,
                    calendar=probe.window.calendar,
                ),
            )
            prior_id = f"{node.id}{_PRIOR_SUFFIX}"
            nodes.append(ProbeNode(id=prior_id, probe=prior_probe, purpose="comparison baseline"))
            steps.append(
                TransformPlanStep(
                    id=f"{node.id}__compare",
                    operator="compare",
                    inputs=(node.id, prior_id),
                    args=(("kind", comparison.kind.value),),
                )
            )
        return steps

    @staticmethod
    def _prior_range(
        probe_window: TimeWindow, spec_window: TimeWindow, comparison: Comparison
    ) -> AbsoluteRange:
        if probe_window.range == spec_window.range or comparison.kind is ComparisonKind.CUSTOM:
            return comparison.window.range
        return derive_comparison(probe_window, comparison.kind).window.range

    # ----------------------------------------------------------- transforms

    def _playbook_transforms(
        self,
        playbook: PlaybookSpec,
        nodes: list[ProbeNode],
        node_contracts: dict[str, tuple[MetricContract, ...]],
        compare_steps: list[TransformPlanStep],
        spec: AnalysisSpec,
    ) -> tuple[list[TransformPlanStep], list[str]]:
        steps: list[TransformPlanStep] = list(compare_steps)
        notes: list[str] = []
        # latest logical frame per base node (updated as steps derive frames)
        latest: dict[str, str] = {
            node.id: node.id for node in nodes if not node.id.endswith(_PRIOR_SUFFIX)
        }
        compared: set[str] = set()
        for step in compare_steps:
            latest[step.inputs[0]] = step.id
            compared.add(step.inputs[0])

        def money_measure(node_id: str) -> str | None:
            for contract in node_contracts.get(node_id, ()):
                if contract.unit is MetricUnit.MONEY_CENTS:
                    return contract.id
            return None

        for requested in playbook.transforms:
            operator = requested.operator
            if operator == "compare":
                if not compare_steps:
                    notes.append(
                        "transform 'compare' skipped: the question carries no comparison window"
                    )
                continue
            if operator == "share_of_total":
                measure = requested.arg("measure")
                emitted = False
                for node_id, contracts in node_contracts.items():
                    if measure is not None and any(c.id == measure for c in contracts):
                        step_id = f"{latest[node_id]}__share"
                        steps.append(
                            TransformPlanStep(
                                id=step_id,
                                operator="share_of_total",
                                inputs=(latest[node_id],),
                                args=(("measure", measure),),
                            )
                        )
                        latest[node_id] = step_id
                        emitted = True
                if not emitted:
                    notes.append(
                        f"transform 'share_of_total' skipped: measure {measure!r} is not "
                        "produced by any planned probe"
                    )
                continue
            if operator in ("rank", "top_k"):
                by = requested.arg("by")
                emitted = False
                for node_id in latest:
                    column, descending = self._rank_binding(
                        by, requested, node_id, node_contracts, node_id in compared
                    )
                    if column is None:
                        continue
                    step_id = f"{latest[node_id]}__{operator}"
                    args: list[tuple[str, str]] = [
                        ("by", column),
                        ("descending", "true" if descending else "false"),
                    ]
                    if operator == "top_k":
                        args.append(("k", requested.arg("k") or "10"))
                    steps.append(
                        TransformPlanStep(
                            id=step_id,
                            operator=operator,
                            inputs=(latest[node_id],),
                            args=tuple(args),
                        )
                    )
                    latest[node_id] = step_id
                    emitted = True
                if not emitted:
                    notes.append(
                        f"transform {operator!r} skipped: ranking column {by!r} does not "
                        "resolve on any planned frame"
                    )
                continue
            if operator == "decompose":
                # needs volume and value measures in one frame; surfaced when
                # the playbook's measures live at different grains
                candidate = None
                for node_id, contracts in node_contracts.items():
                    units = {c.unit for c in contracts}
                    if MetricUnit.MONEY_CENTS in units and MetricUnit.COUNT in units:
                        candidate = node_id
                        break
                if candidate is None:
                    notes.append(
                        "transform 'decompose' skipped: volume and value measures are "
                        "retrieved by separate probes at different grains in this plan"
                    )
                continue
            notes.append(
                f"transform {operator!r} is not executable on this milestone's engine; "
                "recorded and skipped"
            )
        return steps, notes

    def _rank_binding(
        self,
        by: str | None,
        requested: TransformStepSpec,
        node_id: str,
        node_contracts: dict[str, tuple[MetricContract, ...]],
        has_compare: bool,
    ) -> tuple[str | None, bool]:
        """Resolve a playbook ranking arg to a concrete frame column.

        ``impact_cents`` is the governed alias for the money measure's
        ``__delta`` on compare outputs, ranked **ascending** so the most
        negative movement of a higher-is-good measure surfaces first.
        """
        if by is None:
            return None, True
        descending = (requested.arg("descending") or "true").lower() != "false"
        if by == _IMPACT_ARG:
            if not has_compare:
                return None, True
            for contract in node_contracts.get(node_id, ()):
                if contract.unit is MetricUnit.MONEY_CENTS:
                    return f"{contract.id}__delta", False
            return None, True
        if any(c.id == by for c in node_contracts.get(node_id, ())):
            return by, descending
        return None, True


class DiffPlanService:
    """Probe-hash diff between two plans (the refinement path re-executes
    only ``added``; ``unchanged`` is served from the evidence cache)."""

    def diff(self, old_plan: InvestigationPlan, new_plan: InvestigationPlan) -> PlanDiff:
        old_by_hash = {node.hash: node for node in old_plan.nodes}
        new_by_hash = {node.hash: node for node in new_plan.nodes}
        added = tuple(node for digest, node in new_by_hash.items() if digest not in old_by_hash)
        removed = tuple(node for digest, node in old_by_hash.items() if digest not in new_by_hash)
        unchanged = tuple(node for digest, node in new_by_hash.items() if digest in old_by_hash)
        return PlanDiff(added=added, removed=removed, unchanged=unchanged)
