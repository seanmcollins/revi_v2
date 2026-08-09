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
from pathlib import Path

from revi_api.adapters import CalculationTransforms, PackSnapshotPort
from revi_catalog import load_catalog
from revi_catalog_contracts.model import CatalogSnapshot
from revi_connector_duckdb import DuckDbAnalyticalRepository
from revi_investigation.application.calculation_glue import CalculateMetricsService
from revi_investigation.application.cohorts import PinCohortService
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
from revi_investigation.application.refinement_llm import (
    EmitRefinementsService,
    ResolveReferentsService,
)
from revi_investigation.application.submit_turn import OpenSessionService, SubmitTurnService
from revi_investigation.application.validation import PlanValidationService, ValidationLimits
from revi_kernel.capabilities import AnalyticalRepository
from revi_pack.domain import PackSnapshot
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
    planner = BuildInvestigationPlanService(pack_port, catalog)
    validator = PlanValidationService(
        catalog, pack_port, spy, limits if limits is not None else ValidationLimits()
    )
    executor = ExecuteInvestigationService(spy, evidence_cache, event_bus, catalog)
    transforms = CalculationTransforms(pack_port.snapshot.metric)
    calculator = CalculateMetricsService(transforms, pack_port)
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
        referent_resolver=ResolveReferentsService(llm),
        refinement_emitter=EmitRefinementsService(llm),
        cohort_pinner=PinCohortService(spy, cohort_store, referent_registry, catalog),
        differ=DiffPlanService(),
        transforms=transforms,
        pack=pack_port,
        referents=referent_registry,
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
    """The reference-path wiring: real DuckDB warehouse, real catalog, real
    pack snapshot resolving the pack's own metric contracts."""
    catalog = load_catalog(catalog_dir if catalog_dir is not None else DEFAULT_CATALOG_DIR)
    pack = load_base_pack(pack_dir)
    repository = DuckDbAnalyticalRepository(warehouse_path, catalog, pack.metric)
    return build_engine(repository=repository, llm=llm, catalog=catalog, pack=pack)
