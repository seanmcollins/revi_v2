"""Production adapters bridging capability implementations onto the
investigation engine's narrow Protocol seams (``capability_ports``).

``revi_investigation`` may not import ``revi_pack`` / ``revi_calculation``
(capability independence); these adapters live in the composition root and
are shared by the API wiring and the test wiring
(``revi_testing.engine_wiring`` re-exports them). This module stays free
of FastAPI imports so non-API packages can import it.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from revi_calculation.operators import (
    compare,
    decompose,
    panel,
    pivot,
    project_lagged_realization,
    rank,
    ratio,
    reconcile,
    share_of_total,
    top_k,
)
from revi_calculation.operators.reconcile import ReconciliationStatus
from revi_calculation_contracts.contract import MetricContract
from revi_investigation.application.capability_ports import (
    BenchmarkSpec,
    ConceptBinding,
    ConclusionPolicySpec,
    KnowledgeEntry,
    PlaybookSpec,
    ProbeTemplateSpec,
    ReconcileVerdict,
    ScorecardVerdictSpec,
    TermDefinition,
    TransformStepSpec,
)
from revi_kernel.frame import EvidenceFrame
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef
from revi_pack.domain import (
    BindingState,
    CodeDefinition,
    CodeSystem,
    Concept,
    KnowledgeCard,
    PackSnapshot,
)

#: ``PackSnapshot.metric`` — metric id → contract, or ``None`` when the
#: pack declares no such metric.
type MetricResolver = Callable[[str], MetricContract | None]


class PackSnapshotPort:
    """``PackPort`` adapter over a composed ``revi_pack`` snapshot."""

    def __init__(self, snapshot: PackSnapshot) -> None:
        self._snapshot = snapshot

    @property
    def snapshot(self) -> PackSnapshot:
        return self._snapshot

    @property
    def snapshot_id(self) -> str:
        return self._snapshot.id

    @property
    def pack_id(self) -> str:
        return self._snapshot.version.pack_id

    @property
    def pack_version(self) -> str:
        return self._snapshot.version.version

    def metric(self, metric_id: str) -> MetricContract | None:
        return self._snapshot.metric(metric_id)

    def metric_summaries(self) -> tuple[tuple[str, str], ...]:
        return tuple((m.id, m.description) for m in self._snapshot.metric_contracts)

    def playbook(self, playbook_id: str) -> PlaybookSpec | None:
        for playbook in self._snapshot.playbooks:
            if playbook.id == playbook_id:
                return PlaybookSpec(
                    id=playbook.id,
                    description=playbook.description,
                    probes=tuple(
                        ProbeTemplateSpec(
                            id=probe.id,
                            metric_ids=probe.metric_ids,
                            dimensions=probe.dimensions,
                            window=probe.window,
                            basis_override=probe.basis_override,
                            top_n=probe.top_n,
                            purpose=" ".join(probe.scope_note.split()),
                        )
                        for probe in playbook.probes
                    ),
                    transforms=tuple(
                        TransformStepSpec(operator=step.operator, args=step.args)
                        for step in playbook.transforms
                    ),
                    conclusion_policies=playbook.conclusion_policies,
                    ranking_policy=playbook.ranking_policy,
                    triggers=playbook.triggers,
                    verdict=(
                        None
                        if playbook.verdict is None
                        else ScorecardVerdictSpec(
                            leader_min_measures=playbook.verdict.leader_min_measures,
                            measures=playbook.verdict.measures,
                        )
                    ),
                )
        return None

    def playbook_summaries(self) -> tuple[tuple[str, str], ...]:
        return tuple((p.id, p.description) for p in self._snapshot.playbooks)

    def concept_summaries(self) -> tuple[tuple[str, str], ...]:
        return tuple((c.id, c.name) for c in self._snapshot.concepts)

    def has_concept(self, concept_id: str) -> bool:
        return self._snapshot.concept(concept_id) is not None

    def concept_for_alias(self, text: str) -> str | None:
        concept = self._snapshot.concept_for_alias(text)
        return concept.id if concept is not None else None

    def resolve_term(self, text: str) -> tuple[TermDefinition, ...]:
        out: list[TermDefinition] = []
        for match in self._snapshot.resolve_term(text):
            if isinstance(match, Concept):
                out.append(
                    TermDefinition(
                        term=match.id,
                        kind="concept",
                        title=match.name,
                        definition=match.definition,
                        source=match.sources[0].title if match.sources else None,
                    )
                )
            elif isinstance(match, CodeDefinition):
                out.append(
                    TermDefinition(
                        term=match.code,
                        kind=f"code:{match.code_system.value}",
                        title=match.title,
                        definition=match.definition_paraphrase,
                        source=match.sources[0].title if match.sources else None,
                    )
                )
            elif isinstance(match, MetricContract):
                out.append(
                    TermDefinition(
                        term=match.id,
                        kind="metric",
                        title=match.id.replace("_", " "),
                        definition=match.description,
                        source=None,
                    )
                )
            elif isinstance(match, KnowledgeCard):
                out.append(
                    TermDefinition(
                        term=match.id,
                        kind="knowledge_card",
                        title=match.title,
                        definition=match.summary,
                        source=match.sources[0].title if match.sources else None,
                    )
                )
            # future pack artifact types pass through silently — the engine
            # only consumes what the TermDefinition shape can carry
        return tuple(out)

    def benchmarks_for_metric(self, metric_id: str) -> tuple[BenchmarkSpec, ...]:
        return tuple(
            BenchmarkSpec(
                id=figure.id,
                metric_id=figure.metric_id,
                cohort_label=figure.cohort_label,
                value_low=figure.value_low,
                value_high=figure.value_high,
                unit=figure.unit,
                period=figure.period,
                authority=figure.authority,
                review_status=figure.review_status.value,
                cautions=figure.cautions,
                source_titles=tuple(source.title for source in figure.sources),
            )
            for figure in self._snapshot.benchmarks_for_metric(metric_id)
        )

    def code_title(self, system: str, code: str) -> str | None:
        try:
            code_system = CodeSystem(system)
        except ValueError:
            return None
        definition = self._snapshot.code(code_system, code)
        return definition.title if definition is not None else None

    def conclusion_policy(self, policy_id: str) -> ConclusionPolicySpec | None:
        for policy in self._snapshot.conclusion_policies:
            if policy.id == policy_id:
                return ConclusionPolicySpec(
                    id=policy.id,
                    required_grade=policy.required_grade,
                    estimate_label_required=policy.estimate_label_required,
                )
        return None

    def binding_strength(self, concept_id: str, field_id: str) -> EvidenceGrade | None:
        for binding in self._snapshot.bindings:
            if (
                binding.concept_id == concept_id
                and binding.dimension_or_measure_id == field_id
                and binding.state is not BindingState.DEPRECATED
            ):
                return binding.strength
        return None

    def concept_bindings(self, concept_id: str) -> tuple[ConceptBinding, ...]:
        return tuple(
            sorted(
                (
                    ConceptBinding(
                        concept_id=binding.concept_id,
                        field_id=binding.dimension_or_measure_id,
                        state=binding.state.value,
                        strength=binding.strength,
                    )
                    for binding in self._snapshot.bindings
                    if binding.concept_id == concept_id
                    and binding.state is not BindingState.DEPRECATED
                ),
                key=lambda binding: (-binding.strength.strength, binding.field_id),
            )
        )

    def knowledge_entries(self) -> tuple[KnowledgeEntry, ...]:
        return tuple(
            KnowledgeEntry(
                id=card.id,
                title=card.title,
                domains=card.domains,
                aliases=card.aliases,
                summary=" ".join(card.summary.split()),
                key_points=tuple(" ".join(point.split()) for point in card.key_points),
                cautions=tuple(" ".join(caution.split()) for caution in card.cautions),
                review_status=card.review_status.value,
                source_titles=tuple(source.title for source in card.sources),
            )
            for card in sorted(self._snapshot.knowledge_cards, key=lambda card: card.id)
        )


class CalculationTransforms:
    """``TransformPort`` adapter over the versioned kernel operators.

    ``metric`` is the pack's metric-contract resolver (``snapshot.metric``),
    supplied so a ratio's output column can carry the unit the CONTRACT
    declares. Optional; with ``None`` the kernel falls back to ``ratio``.
    """

    def __init__(self, metric: MetricResolver | None = None) -> None:
        self._metric = metric

    def _declared_unit(self, metric_id: str) -> str | None:
        """The metric contract's declared unit, or ``None`` if unknowable.

        ``days_in_ar`` declares ``unit: days`` and is numerator/denominator
        shaped, so it goes through ``ratio()``; stamping it ``ratio``
        published "days in ar: 15,941.2%". A ratio is a shape, the unit is
        a declaration, and the declaration is resolved here, where the
        contract is in hand.
        """
        if self._metric is None:
            return None
        contract = self._metric(metric_id)
        return contract.unit.value if contract is not None else None

    def ratio(
        self,
        frame: EvidenceFrame,
        *,
        numerator: str,
        denominator: str,
        out: str,
        out_ref: MetricRef,
        contract_version: int | None = None,
        unit: str | None = None,
    ) -> EvidenceFrame:
        return ratio(
            frame,
            numerator=numerator,
            denominator=denominator,
            out=out,
            out_ref=out_ref,
            contract_version=contract_version,
            unit=unit if unit is not None else self._declared_unit(out_ref.id),
        )

    def compare(
        self,
        current: EvidenceFrame,
        prior: EvidenceFrame,
        *,
        join_on: tuple[str, ...] | None = None,
        measures: tuple[str, ...] | None = None,
    ) -> EvidenceFrame:
        return compare(current, prior, join_on=join_on, measures=measures)

    def share_of_total(
        self, frame: EvidenceFrame, *, measure: str, within: tuple[str, ...] = ()
    ) -> EvidenceFrame:
        return share_of_total(frame, measure=measure, within=within)

    def top_k(
        self, frame: EvidenceFrame, *, by: str, k: int, per_group: tuple[str, ...] | None = None
    ) -> EvidenceFrame:
        return top_k(frame, by=by, k=k, per_group=per_group)

    def rank(self, frame: EvidenceFrame, *, by: str, descending: bool = True) -> EvidenceFrame:
        return rank(frame, by=by, descending=descending)

    def pivot(
        self, frame: EvidenceFrame, *, index: tuple[str, ...], column: str, measure: str
    ) -> EvidenceFrame:
        return pivot(frame, index=index, column=column, measure=measure)

    def panel(
        self,
        *frames: EvidenceFrame,
        entity: str,
        better_high: tuple[str, ...] = (),
        better_low: tuple[str, ...] = (),
    ) -> EvidenceFrame:
        return panel(*frames, entity=entity, better_high=better_high, better_low=better_low)

    def decompose(
        self,
        current: EvidenceFrame,
        prior: EvidenceFrame,
        *,
        volume: str,
        value: str,
        cells: tuple[str, ...] | None = None,
    ) -> EvidenceFrame:
        return decompose(current, prior, volume=volume, value=value, cells=cells)

    def reconcile(
        self,
        parent: EvidenceFrame,
        children: EvidenceFrame,
        *,
        measures: tuple[str, ...],
        suppression_allowance: Decimal = Decimal(0),
    ) -> ReconcileVerdict:
        result = reconcile(
            parent, children, measures=measures, suppression_allowance=suppression_allowance
        )
        failed = [m.measure for m in result.measures if not m.passed]
        return ReconcileVerdict(
            passed=result.status is not ReconciliationStatus.FAILED,
            summary=(
                f"status={result.status.value}"
                + (f"; failed measures: {', '.join(failed)}" if failed else "")
            ),
        )

    def project_lagged_realization(
        self,
        inventory: EvidenceFrame,
        curves: EvidenceFrame,
        inflow: EvidenceFrame,
        baseline: EvidenceFrame,
        *,
        horizon_weeks: int,
        coverage_min: Decimal = Decimal("0.8"),
    ) -> EvidenceFrame:
        return project_lagged_realization(
            inventory,
            curves,
            inflow,
            baseline,
            horizon_weeks=horizon_weeks,
            coverage_min=coverage_min,
        )
