"""Build a fully wired first-turn engine from real parts plus fakes.

``revi_investigation`` services depend on narrow Protocols
(``application/capability_ports.py``); this module provides the real
adapters — :class:`PackSnapshotPort` over a composed ``revi_pack``
snapshot and :class:`CalculationTransforms` over ``revi_calculation``
operators — and assembles a :class:`WiredEngine`: every store faked, the
analytical repository wrapped in a call-counting spy, and a
``SubmitTurnService`` ready to run. The API app performs the equivalent
wiring for production; tests import it from here because ``revi_testing``
already depends on the capability implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

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
from revi_catalog import load_catalog
from revi_catalog_contracts.model import CatalogSnapshot
from revi_connector_duckdb import DuckDbAnalyticalRepository
from revi_investigation.application.calculation_glue import CalculateMetricsService
from revi_investigation.application.capability_ports import (
    ConclusionPolicySpec,
    PlaybookSpec,
    ProbeTemplateSpec,
    ReconcileVerdict,
    TermDefinition,
    TransformStepSpec,
)
from revi_investigation.application.execution import ExecuteInvestigationService
from revi_investigation.application.findings import EvaluateFindingsService
from revi_investigation.application.interpretation import (
    ClassifyTurnService,
    InterpretQuestionService,
)
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    DiffPlanService,
)
from revi_investigation.application.submit_turn import OpenSessionService, SubmitTurnService
from revi_investigation.application.validation import PlanValidationService, ValidationLimits
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import MetricRef
from revi_pack.domain import CodeDefinition, Concept, PackSnapshot
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot
from revi_testing.fakes import (
    FakeCohortStore,
    FakeEvidenceCache,
    FakeFrameStore,
    FakeInvestigationStore,
    FakeReferentRegistryStore,
    FakeSessionStore,
    FakeTraceStore,
    FakeTurnEventBus,
    SpyAnalyticalRepository,
)
from revi_testing.mock_llm import MockLanguageModel

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CATALOG_DIR = _REPO_ROOT / "warehouse" / "catalog"
DEFAULT_PACK_DIR = _REPO_ROOT / "packs" / "base-rcm"


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
            else:  # MetricContract
                out.append(
                    TermDefinition(
                        term=match.id,
                        kind="metric",
                        title=match.id.replace("_", " "),
                        definition=match.description,
                        source=None,
                    )
                )
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


def load_base_pack(pack_dir: Path | None = None) -> PackSnapshot:
    return build_snapshot([load_layer(pack_dir if pack_dir is not None else DEFAULT_PACK_DIR)])


@dataclass
class WiredEngine:
    """Everything the first-turn tests touch, in one place."""

    submit: SubmitTurnService
    open_session: OpenSessionService
    planner: BuildInvestigationPlanService
    validator: PlanValidationService
    differ: DiffPlanService
    llm: MockLanguageModel
    repository: SpyAnalyticalRepository
    pack_port: PackSnapshotPort
    catalog: CatalogSnapshot
    session_store: FakeSessionStore
    investigation_store: FakeInvestigationStore
    trace_store: FakeTraceStore
    frame_store: FakeFrameStore
    referent_registry: FakeReferentRegistryStore
    cohort_store: FakeCohortStore
    evidence_cache: FakeEvidenceCache
    event_bus: FakeTurnEventBus


def build_engine(
    *,
    repository: AnalyticalRepository,
    llm: MockLanguageModel | None = None,
    catalog: CatalogSnapshot | None = None,
    pack: PackSnapshot | None = None,
    limits: ValidationLimits | None = None,
) -> WiredEngine:
    """Wire a complete SubmitTurnService: real pack/catalog/transforms +
    the given repository (spy-wrapped) + fakes for every store."""
    catalog = catalog if catalog is not None else load_catalog(DEFAULT_CATALOG_DIR)
    pack_port = PackSnapshotPort(pack if pack is not None else load_base_pack())
    llm = llm if llm is not None else MockLanguageModel()
    spy = SpyAnalyticalRepository(repository)

    session_store = FakeSessionStore()
    investigation_store = FakeInvestigationStore(session_store)
    trace_store = FakeTraceStore()
    frame_store = FakeFrameStore()
    referent_registry = FakeReferentRegistryStore()
    cohort_store = FakeCohortStore()
    evidence_cache = FakeEvidenceCache()
    event_bus = FakeTurnEventBus()

    open_session = OpenSessionService(session_store, spy, pack_port)
    planner = BuildInvestigationPlanService(pack_port)
    validator = PlanValidationService(
        catalog, pack_port, spy, limits if limits is not None else ValidationLimits()
    )
    executor = ExecuteInvestigationService(spy, evidence_cache, event_bus, catalog)
    calculator = CalculateMetricsService(CalculationTransforms(), pack_port)
    evaluator = EvaluateFindingsService(referent_registry)
    submit = SubmitTurnService(
        open_session=open_session,
        classifier=ClassifyTurnService(llm),
        interpreter=InterpretQuestionService(llm, pack_port, catalog),
        planner=planner,
        validator=validator,
        executor=executor,
        calculator=calculator,
        evaluator=evaluator,
        pack=pack_port,
        investigations=investigation_store,
        traces=trace_store,
        frames=frame_store,
        events=event_bus,
    )
    return WiredEngine(
        submit=submit,
        open_session=open_session,
        planner=planner,
        validator=validator,
        differ=DiffPlanService(),
        llm=llm,
        repository=spy,
        pack_port=pack_port,
        catalog=catalog,
        session_store=session_store,
        investigation_store=investigation_store,
        trace_store=trace_store,
        frame_store=frame_store,
        referent_registry=referent_registry,
        cohort_store=cohort_store,
        evidence_cache=evidence_cache,
        event_bus=event_bus,
    )


def build_duckdb_engine(
    *,
    warehouse_path: Path,
    llm: MockLanguageModel | None = None,
    catalog_dir: Path | None = None,
    pack_dir: Path | None = None,
) -> WiredEngine:
    """The golden-path wiring: real DuckDB warehouse, real catalog, real
    pack snapshot resolving the pack's own metric contracts."""
    catalog = load_catalog(catalog_dir if catalog_dir is not None else DEFAULT_CATALOG_DIR)
    pack = load_base_pack(pack_dir)
    repository = DuckDbAnalyticalRepository(warehouse_path, catalog, pack.metric)
    return build_engine(repository=repository, llm=llm, catalog=catalog, pack=pack)
