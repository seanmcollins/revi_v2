"""Production adapters bridging capability implementations onto the
investigation engine's narrow Protocol seams (``capability_ports``).

``revi_investigation`` may not import ``revi_pack`` / ``revi_calculation``
(capability independence); these adapters live in the composition root and
are shared by the API wiring and the test wiring
(``revi_testing.engine_wiring`` re-exports them). This module stays free
of FastAPI imports so non-API packages can import it.
"""

from __future__ import annotations

from decimal import Decimal

from revi_calculation.operators import (
    compare,
    decompose,
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
    ConclusionPolicySpec,
    PlaybookSpec,
    ProbeTemplateSpec,
    ReconcileVerdict,
    TermDefinition,
    TransformStepSpec,
)
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import MetricRef
from revi_pack.domain import CodeDefinition, Concept, KnowledgeCard, PackSnapshot


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

    def conclusion_policy(self, policy_id: str) -> ConclusionPolicySpec | None:
        for policy in self._snapshot.conclusion_policies:
            if policy.id == policy_id:
                return ConclusionPolicySpec(
                    id=policy.id,
                    required_grade=policy.required_grade,
                    estimate_label_required=policy.estimate_label_required,
                )
        return None


class CalculationTransforms:
    """``TransformPort`` adapter over the versioned kernel operators."""

    def ratio(
        self,
        frame: EvidenceFrame,
        *,
        numerator: str,
        denominator: str,
        out: str,
        out_ref: MetricRef,
        contract_version: int | None = None,
    ) -> EvidenceFrame:
        return ratio(
            frame,
            numerator=numerator,
            denominator=denominator,
            out=out,
            out_ref=out_ref,
            contract_version=contract_version,
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
