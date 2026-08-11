"""Composition root: environment-driven assembly of the full engine.

Wiring matrix (each choice is logged loudly at startup):

- **Warehouse**: ``REVI_WAREHOUSE_PATH`` (default
  ``data/revi_warehouse.duckdb``) → real DuckDB repository + anomaly source.
- **Catalog**: ``REVI_CATALOG_DIR`` (default ``warehouse/catalog``).
- **Pack**: ``REVI_PACK_DIR`` (default ``packs/base-rcm``) plus the
  demo-tenant overlay when present; the anomaly-actionability rules ride
  alongside as governed pack-adjacent content. The composed pack is checked
  against the catalog before anything is wired to it
  (:func:`revi_pack.conformance.validate_pack_catalog_conformance`) — a
  non-conforming pack fails startup instead of failing one probe later.
- **Stores**: Postgres adapters when ``REVI_DATABASE_URL`` is set and
  reachable, else in-memory stores.
- **LLM**: the Claude Agent SDK adapter when ``REVI_MODEL_PIN`` is set and
  ``REVI_LLM_MOCK`` != 1, else the scripted demo model (the reference
  conversation script; unmatched calls clarify, never guess).
- **Settings bounds**: :class:`~revi_api.settings_policy.SettingsPolicy`
  from ``REVI_MODEL_TIERS`` / ``REVI_LLM_MAX_BUDGET_USD`` /
  ``REVI_DEBUG_TRACE``. It is told whether the chosen language model
  actually applies a per-call model override, so a deployment running the
  scripted script refuses a model tier rather than accepting a control
  that would change nothing.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from revi_api.actionability import ActionabilityRules, load_actionability_rules
from revi_api.adapters import CalculationTransforms, PackSnapshotPort
from revi_api.deep_research_policy import (
    DEEP_RESEARCH_FILENAME,
    FILING_RULES_FILENAME,
    FilingRuleLadder,
    load_deep_research_settings,
    load_filing_rule_ladder,
)
from revi_api.events import ContextTurnEventBus
from revi_api.memory_stores import (
    MemoryCohortStore,
    MemoryEvidenceCache,
    MemoryFrameStore,
    MemoryInvestigationStore,
    MemoryMonitorsLeadStore,
    MemoryMonitorsLoadStore,
    MemoryMonitorsPinResultStore,
    MemoryMonitorsPinStore,
    MemoryReferentRegistryStore,
    MemorySessionStore,
    MemoryTraceStore,
    MemoryTurnReceiptStore,
)
from revi_api.metric_display import MetricDisplayRules, load_metric_display
from revi_api.monitors_policy import MONITORS_FILENAME, MonitorsPolicy, load_monitors_policy
from revi_api.portfolio import DrillabilityProbe, PriorityPolicy, priority_policy_from_pack
from revi_api.rederive import ImpactReDeriver, build_rederiver
from revi_api.scripted_llm import demo_language_model
from revi_api.session_lifecycle import ArchivableSessionStore, TurnReceiptStore
from revi_api.settings_policy import SettingsPolicy
from revi_api.usage_ledger import MeteredLanguageModel
from revi_api.worklist import WORKLIST_FILENAME, WorklistRouting, load_worklist_routing
from revi_catalog import load_catalog
from revi_catalog_contracts.model import CatalogSnapshot
from revi_connector_duckdb import DuckDbAnalyticalRepository, DuckDbAnomalySource
from revi_investigation.application.calculation_glue import CalculateMetricsService
from revi_investigation.application.cohorts import PinCohortService
from revi_investigation.application.deep_research import DeepResearchService, DenialRowSource
from revi_investigation.application.deep_research.llm import PlanDeepResearchService
from revi_investigation.application.deep_research.policy import DeepResearchSettings
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
from revi_investigation.application.ports import (
    AnomalySource,
    CohortStore,
    EvidenceCache,
    FrameStore,
    InvestigationStore,
    LanguageModelPort,
    MonitorsLeadStore,
    MonitorsLoadStore,
    MonitorsPinResultStore,
    MonitorsPinStore,
    ReferentRegistryStore,
    TraceStore,
)
from revi_investigation.application.refinement_llm import (
    EmitRefinementsService,
    ResolveReferentsService,
)
from revi_investigation.application.submit_turn import OpenSessionService, SubmitTurnService
from revi_investigation.application.validation import (
    PlanClarificationNeeded,
    PlanValidationService,
)
from revi_investigation.application.window_maturity import WindowMaturityService
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import Session
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import ReviError
from revi_kernel.watermark import DataWatermark, WatermarkEpoch
from revi_pack.conformance import validate_pack_catalog_conformance
from revi_pack.domain import PackSnapshot
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot
from revi_presentation import RecipeSpec

if TYPE_CHECKING:  # pragma: no cover - annotation only; see below
    # Monitors imports this module (through :mod:`revi_api.assembly`) at
    # runtime, so the alias for the probe THIS module supplies to it is
    # taken as an annotation only. ``from __future__ import annotations``
    # makes that sound for a dataclass field.
    from revi_api.monitors.common import SubjectPresenceProbe


logger = logging.getLogger("revi.api.wiring")

# apps/api/src/revi_api/wiring.py → repo root
_REPO_ROOT = Path(__file__).resolve().parents[4]

#: The §6.6 refusal that means "no such value exists in this data" — the one
#: clarification the subject-presence probe below is allowed to read as "the
#: cell this monitor is about is gone". Written by
#: :meth:`PlanValidationService.resolve_predicate_values`.
_VALUE_UNMATCHED = "PREDICATE_VALUE_UNMATCHED"


@dataclass
class ApiComponents:
    """Everything the API service layer needs, wired once at startup."""

    submit: SubmitTurnService
    open_session: OpenSessionService
    llm: LanguageModelPort
    repository: AnalyticalRepository
    anomaly_source: AnomalySource
    pack_port: PackSnapshotPort
    pack_snapshot: PackSnapshot
    catalog: CatalogSnapshot
    #: The engine's ``SessionStore`` plus soft archive — see
    #: :mod:`revi_api.session_lifecycle`.
    sessions: ArchivableSessionStore
    investigations: InvestigationStore
    traces: TraceStore
    frames: FrameStore
    referents: ReferentRegistryStore
    cohorts: CohortStore
    cache: EvidenceCache
    #: Executed turns by idempotency key, so a retry after a restart
    #: returns the original answer instead of executing a second turn.
    receipts: TurnReceiptStore
    #: Monitors (the proactive surface): monitored typed specs, one evaluated
    #: tile per (monitor, load), the detection-feed census per load, and the
    #: lead lifecycle. See :mod:`revi_api.monitors`.
    monitors_pins: MonitorsPinStore
    monitors_results: MonitorsPinResultStore
    monitors_loads: MonitorsLoadStore
    monitors_leads: MonitorsLeadStore
    event_bus: ContextTurnEventBus
    recipes: tuple[RecipeSpec, ...]
    priority_policy: PriorityPolicy
    actionability: ActionabilityRules
    #: Can this typed spec be answered at all? Runs the real planning +
    #: §6.6 validation pass and returns the platform's own refusal text,
    #: never a guess and never a warehouse query.
    drillability: DrillabilityProbe
    #: What does THIS platform's governed contract say the cell an anomaly
    #: card names is worth? The drillability pipeline continued through
    #: execute and calculate, memoized per (watermark, plan hash) and
    #: reading the warehouse through the ordinary evidence cache. Used to
    #: publish ``reconciled_impact_cents`` beside the detector's own figure
    #: so the two can never silently diverge.
    rederive_impact: ImpactReDeriver
    #: Is the cell a monitor is about still IN this load's data? Asked
    #: before a stored tile is republished, because a stored evaluation is
    #: only reusable while it still describes the data as well as the
    #: monitor. See :meth:`revi_api.monitors.tiles._LoadEvaluation.evaluate_load`.
    subject_presence: SubjectPresenceProbe
    #: Governed display names for metric ids whose name overclaims what
    #: they measure.
    metric_display: MetricDisplayRules
    #: Which governed playbook and concept ids mean "show me the ranked
    #: worklist", so a conversation can reach the portfolio the platform
    #: already computes without any question string being matched anywhere.
    #: See :mod:`revi_api.worklist`.
    worklist: WorklistRouting
    #: The governed Monitors content: materiality thresholds per unit kind,
    #: the resolution-confirmation rule, and per-category time-to-impact
    #: derivation. Empty (``enabled`` false) when the pack ships none, in
    #: which case the brief says so rather than applying a threshold nobody
    #: agreed to. See :mod:`revi_api.monitors_policy`.
    monitors_policy: MonitorsPolicy
    store_mode: str
    llm_mode: str
    #: Whether the wired language model applies ``LlmCallPolicy`` — the
    #: session's model tier and per-call budget. False for the scripted
    #: demo model, which is not a model and cannot honor either; the
    #: settings policy refuses a tier rather than pretending.
    llm_applies_call_policy: bool
    #: Admin bounds for session settings, and the resolver that enforces
    #: them (out-of-bounds is refused, never clamped).
    settings_policy: SettingsPolicy
    #: Deep research (the recoverability mode): the governed content one run
    #: is executed under, which plans' filing limits stand without a
    #: confirmation caveat, and the service that runs the angles.
    #: See :mod:`revi_api.deep_research`.
    deep_research_settings: DeepResearchSettings
    filing_rules: FilingRuleLadder
    deep_research: DeepResearchService


@dataclass(frozen=True)
class _Stores:
    sessions: ArchivableSessionStore
    investigations: InvestigationStore
    traces: TraceStore
    frames: FrameStore
    referents: ReferentRegistryStore
    cohorts: CohortStore
    cache: EvidenceCache
    receipts: TurnReceiptStore
    monitors_pins: MonitorsPinStore
    monitors_results: MonitorsPinResultStore
    monitors_loads: MonitorsLoadStore
    monitors_leads: MonitorsLeadStore
    mode: str


def _memory_stores() -> _Stores:
    sessions = MemorySessionStore()
    return _Stores(
        sessions=sessions,
        investigations=MemoryInvestigationStore(sessions),
        traces=MemoryTraceStore(),
        frames=MemoryFrameStore(),
        referents=MemoryReferentRegistryStore(),
        cohorts=MemoryCohortStore(),
        cache=MemoryEvidenceCache(),
        receipts=MemoryTurnReceiptStore(),
        monitors_pins=MemoryMonitorsPinStore(),
        monitors_results=MemoryMonitorsPinResultStore(),
        monitors_loads=MemoryMonitorsLoadStore(),
        monitors_leads=MemoryMonitorsLeadStore(),
        mode="memory",
    )


def _build_stores(env: Mapping[str, str]) -> _Stores:
    url = env.get("REVI_DATABASE_URL", "").strip()
    if not url:
        logger.warning("REVI_DATABASE_URL unset — using IN-MEMORY stores (state is process-local)")
        return _memory_stores()
    try:
        import sqlalchemy

        from revi_store_postgres import (
            PostgresCohortStore,
            PostgresEvidenceCache,
            PostgresFrameStore,
            PostgresInvestigationStore,
            PostgresMonitorsLeadStore,
            PostgresMonitorsLoadStore,
            PostgresMonitorsPinResultStore,
            PostgresMonitorsPinStore,
            PostgresReferentRegistryStore,
            PostgresSessionStore,
            PostgresTraceStore,
            PostgresTurnReceiptStore,
            create_engine,
        )

        engine = create_engine(url)
        with engine.connect() as connection:  # reachability probe
            connection.execute(sqlalchemy.text("SELECT 1"))
        logger.info("using POSTGRES stores at %s", url.split("@")[-1])
        return _Stores(
            sessions=PostgresSessionStore(engine),
            investigations=PostgresInvestigationStore(engine),
            traces=PostgresTraceStore(engine),
            frames=PostgresFrameStore(engine),
            referents=PostgresReferentRegistryStore(engine),
            cohorts=PostgresCohortStore(engine),
            cache=PostgresEvidenceCache(engine),
            receipts=PostgresTurnReceiptStore(engine),
            monitors_pins=PostgresMonitorsPinStore(engine),
            monitors_results=PostgresMonitorsPinResultStore(engine),
            monitors_loads=PostgresMonitorsLoadStore(engine),
            monitors_leads=PostgresMonitorsLeadStore(engine),
            mode="postgres",
        )
    except Exception as exc:
        logger.error(
            "REVI_DATABASE_URL set but unreachable (%s) — FALLING BACK to in-memory stores",
            exc,
        )
        return _memory_stores()


def _build_llm(env: Mapping[str, str]) -> tuple[LanguageModelPort, str]:
    if env.get("REVI_LLM_MOCK", "").strip() == "1" or not env.get("REVI_MODEL_PIN", "").strip():
        logger.warning(
            "LLM in SCRIPTED DEMO mode (set REVI_MODEL_PIN and unset REVI_LLM_MOCK for Claude)"
        )
        return demo_language_model(), "scripted-demo"
    from revi_adapter_claude.config import from_env

    adapter = from_env(env)
    logger.info("LLM: Claude Agent SDK adapter, pin=%s", env.get("REVI_MODEL_PIN"))
    return adapter, "claude-agent-sdk"


def build_components(
    env: Mapping[str, str] | None = None,
    *,
    llm: LanguageModelPort | None = None,
) -> ApiComponents:
    """Assemble the engine per the wiring matrix (see module docstring).

    ``llm`` overrides the environment choice; tests inject mocks here.
    """
    env = env if env is not None else dict(os.environ)

    warehouse = Path(env.get("REVI_WAREHOUSE_PATH", str(_REPO_ROOT / "data/revi_warehouse.duckdb")))
    catalog_dir = Path(env.get("REVI_CATALOG_DIR", str(_REPO_ROOT / "warehouse/catalog")))
    pack_dir = Path(env.get("REVI_PACK_DIR", str(_REPO_ROOT / "packs/base-rcm")))
    overlay_dir = pack_dir.parent / "overlays" / "demo-tenant"

    logger.info("warehouse=%s catalog=%s pack=%s", warehouse, catalog_dir, pack_dir)
    catalog = load_catalog(catalog_dir)
    layers = [load_layer(pack_dir)]
    if overlay_dir.is_dir():
        layers.append(load_layer(overlay_dir))
        logger.info("pack overlay applied: %s", overlay_dir.name)
    pack_snapshot = build_snapshot(layers)
    # Governed content is only as good as the semantics it names. A metric
    # exclusion that resolves nowhere removes nothing, reports nothing, and
    # waits for a question that may never come — so the deployment refuses to
    # start rather than serving a pack whose meaning the catalog cannot carry
    # (design §5.2/§12; see packs/base-rcm/NOTES.md, "Exclusion polarity").
    validate_pack_catalog_conformance(pack_snapshot, catalog)
    pack_port = PackSnapshotPort(pack_snapshot)

    repository = DuckDbAnalyticalRepository(warehouse, catalog, pack_snapshot.metric)
    anomaly_source = DuckDbAnomalySource(warehouse)
    stores = _build_stores(env)
    if llm is not None:
        llm_mode = "injected"
    else:
        llm, llm_mode = _build_llm(env)
    # Asked of the object itself rather than inferred from the mode string:
    # an adapter that applies per-call policy declares it, and anything
    # that does not is treated as not applying it.
    applies_call_policy = bool(getattr(llm, "applies_call_policy", False))
    # Every model call the engine makes passes through a meter, so a turn
    # that FAILS can still report what it spent. Pure decoration: with no
    # ledger bound it is indistinguishable from the port it wraps, and
    # attribute access falls through to the adapter.
    llm = MeteredLanguageModel(llm)
    event_bus = ContextTurnEventBus()
    # The pack's own metric declarations reach the operators: a ratio over a
    # days-unit contract publishes days, not a percentage.
    transforms = CalculationTransforms(pack_snapshot.metric)

    open_session = OpenSessionService(stores.sessions, repository, pack_port)
    interpreter = InterpretQuestionService(llm, pack_port, catalog)
    planner = BuildInvestigationPlanService(pack_port, catalog)
    validator = PlanValidationService(catalog, pack_port, repository)
    executor = ExecuteInvestigationService(repository, stores.cache, event_bus, catalog)
    calculator = CalculateMetricsService(transforms, pack_port)
    submit = SubmitTurnService(
        open_session=open_session,
        classifier=ClassifyTurnService(llm),
        interpreter=interpreter,
        planner=planner,
        validator=validator,
        executor=executor,
        calculator=calculator,
        evaluator=EvaluateFindingsService(stores.referents),
        referent_resolver=ResolveReferentsService(llm),
        refinement_emitter=EmitRefinementsService(llm),
        cohort_pinner=PinCohortService(repository, stores.cohorts, stores.referents, catalog),
        differ=DiffPlanService(),
        transforms=transforms,
        pack=pack_port,
        referents=stores.referents,
        investigations=stores.investigations,
        traces=stores.traces,
        frames=stores.frames,
        events=event_bus,
        window_maturity=WindowMaturityService(repository, pack_port),
    )

    def drillability(spec: TypedInvestigationSpec, watermark: DataWatermark) -> str | None:
        """Plan and validate a drill handle without executing it.

        Runs the platform's own pipeline up to the point where it would
        touch data: interpretation disposes the typed spec against the pack
        and catalog, the planner builds the probe DAG, and §6.6 validation
        resolves every dimension, grain, basis and budget. Anything that
        would have surfaced as an error on a click surfaces here instead,
        with the same message.
        """
        probe_session = Session(
            id="portfolio-drillability-probe",
            tenant="portfolio",
            pack_version=PackVersionRef(pack_port.pack_id, pack_port.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=watermark),),
            created_at=datetime.now(UTC),
        )
        try:
            interpreted = interpreter.from_typed_spec(
                spec, session=probe_session, turn_id="drillability-probe"
            )
            plan = planner.build(interpreted.spec, playbook_id=None, window_explicit=True)
            validator.validate(plan, interpreted.spec)
        except ReviError as exc:
            return f"{exc.code.value}: {exc.message}"
        return None

    async def subject_presence(
        spec: TypedInvestigationSpec, watermark: DataWatermark
    ) -> str | None:
        """Do the values this spec's filters name still exist at this load?

        :func:`drillability` stops before the pipeline touches data, which
        is right for "can this be answered at all" and blind to "is the
        thing it is about still there". This goes one stage further and
        runs §6.6 step 4b — the same async pass a live turn runs — which
        resolves every filter value against the values the warehouse HOLDS
        at this watermark and raises the analyst-facing question when one
        of them matches nothing.

        Cheap enough to ask for every monitor on every load: the values are
        read once per (watermark, entity, dimension, window) and memoized on
        the validator, so a whole surface of payer monitors costs one
        grouped read between them.

        Returns the refusal SENTENCE, never a bool, so the caller can put
        the platform's own words on the tile. ``None`` covers both "every
        value resolves" and "the question could not be put" — a spec that no
        longer plans, or a source that could not answer, is not evidence
        that a subject vanished, and this must never be the reason a monitor
        loses its reading.
        """
        probe_session = Session(
            id="monitors-subject-presence-probe",
            tenant="monitors",
            pack_version=PackVersionRef(pack_port.pack_id, pack_port.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=watermark),),
            created_at=datetime.now(UTC),
        )
        try:
            interpreted = interpreter.from_typed_spec(
                spec, session=probe_session, turn_id="subject-presence-probe"
            )
            plan = planner.build(interpreted.spec, playbook_id=None, window_explicit=True)
            validated = validator.validate(plan, interpreted.spec)
        except (ReviError, PlanClarificationNeeded):
            return None
        try:
            await validator.resolve_predicate_values(validated, watermark=watermark)
        except PlanClarificationNeeded as needed:
            # ONLY the unmatched-value refusal counts. The pass can ask other
            # questions, and answering "your subject is gone" to one of those
            # would take a monitor's reading away for a reason that is not
            # true.
            if (needed.clarification.reason or "").startswith(_VALUE_UNMATCHED):
                return needed.clarification.question
            return None
        except ReviError:
            return None
        except Exception:  # pragma: no cover - a probe must not cost a load
            logger.exception("subject-presence probe failed at %s", watermark.id)
            return None
        return None

    rederive_impact: ImpactReDeriver = build_rederiver(
        interpreter=interpreter,
        planner=planner,
        validator=validator,
        executor=executor,
        calculator=calculator,
        # The drill metric's DECLARED unit decides whether a dollar figure
        # exists at all, before any frame is summed.
        pack=pack_port,
        pack_snapshot_id=pack_port.snapshot_id,
        pack_id=pack_port.pack_id,
        pack_version=pack_port.pack_version,
    )

    actionability_path = pack_dir / "anomaly_actionability.yaml"
    actionability: ActionabilityRules = load_actionability_rules(actionability_path)
    deep_research_settings = load_deep_research_settings(pack_dir / DEEP_RESEARCH_FILENAME)
    filing_rules = load_filing_rule_ladder(pack_dir / FILING_RULES_FILENAME)
    logger.info(
        "deep research: rate floor %d, naming floor %d, %d maturity window(s), content %s",
        deep_research_settings.min_cohort,
        deep_research_settings.disclosure_floor,
        len(deep_research_settings.maturity_days),
        deep_research_settings.content_hash[:12],
    )
    deep_research = DeepResearchService(
        DenialRowSource(
            repository,
            stores.cache,
            filing_rule_confirmed=filing_rules.confirmed,
        ),
        planner=PlanDeepResearchService(llm),
    )
    metric_display: MetricDisplayRules = load_metric_display(pack_dir / "metric_display.yaml")
    worklist: WorklistRouting = load_worklist_routing(pack_dir / WORKLIST_FILENAME)
    monitors_policy: MonitorsPolicy = load_monitors_policy(pack_dir / MONITORS_FILENAME)
    if monitors_policy.enabled:
        logger.info(
            "governed Monitors content loaded: materiality thresholds for %s; brief cap %d; "
            "resolution confirmed after %d consecutive load(s); time-to-impact rules for %d "
            "categor(ies) [%s]",
            ", ".join(sorted(monitors_policy.materiality.unit_kinds)) or "-",
            monitors_policy.materiality.max_entries,
            monitors_policy.resolution.consecutive_loads_required,
            len(monitors_policy.time_to_impact.categories),
            monitors_policy.content_hash[:12],
        )
    else:
        logger.warning(
            "no governed Monitors content in this pack (%s) — monitors still evaluate, but NO "
            "materiality gate is applied and no time-to-impact is derived; briefs say so",
            pack_dir / MONITORS_FILENAME,
        )
    if worklist.enabled:
        logger.info(
            "worklist routing loaded: playbooks=%s concepts=%s (limit %d, max %d)",
            ", ".join(sorted(worklist.playbook_ids)) or "-",
            ", ".join(sorted(worklist.concept_ids)) or "-",
            worklist.default_limit,
            worklist.max_limit,
        )
    else:
        logger.info("no governed worklist routing in this pack — answers carry no worklist")
    if metric_display.by_metric:
        logger.info(
            "governed metric display names loaded for %d metric id(s): %s",
            len(metric_display.by_metric),
            ", ".join(sorted(metric_display.by_metric)),
        )
    recipes = tuple(
        RecipeSpec(id=r.id, applies_to=r.applies_to, chart_type=r.chart_type, notes=r.notes)
        for r in pack_snapshot.presentation_recipes
    )

    return ApiComponents(
        submit=submit,
        open_session=open_session,
        llm=llm,
        repository=repository,
        anomaly_source=anomaly_source,
        pack_port=pack_port,
        pack_snapshot=pack_snapshot,
        catalog=catalog,
        sessions=stores.sessions,
        investigations=stores.investigations,
        traces=stores.traces,
        frames=stores.frames,
        referents=stores.referents,
        cohorts=stores.cohorts,
        cache=stores.cache,
        receipts=stores.receipts,
        monitors_pins=stores.monitors_pins,
        monitors_results=stores.monitors_results,
        monitors_loads=stores.monitors_loads,
        monitors_leads=stores.monitors_leads,
        event_bus=event_bus,
        recipes=recipes,
        priority_policy=priority_policy_from_pack(pack_snapshot),
        actionability=actionability,
        drillability=drillability,
        rederive_impact=rederive_impact,
        subject_presence=subject_presence,
        metric_display=metric_display,
        worklist=worklist,
        monitors_policy=monitors_policy,
        store_mode=stores.mode,
        llm_mode=llm_mode,
        llm_applies_call_policy=applies_call_policy,
        settings_policy=SettingsPolicy.from_env(
            env, model_tier_effective=applies_call_policy
        ),
        deep_research_settings=deep_research_settings,
        filing_rules=filing_rules,
        deep_research=deep_research,
    )
