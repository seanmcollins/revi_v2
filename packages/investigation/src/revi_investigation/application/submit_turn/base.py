"""The services a turn engine runs on, declared once for every section below."""

from __future__ import annotations

from revi_investigation.application.calculation_glue import (
    CalculateMetricsService,
)
from revi_investigation.application.capability_ports import PackPort, TransformPort
from revi_investigation.application.cohorts import PinCohortService
from revi_investigation.application.execution import (
    ExecuteInvestigationService,
)
from revi_investigation.application.findings import (
    EvaluateFindingsService,
)
from revi_investigation.application.interpretation import (
    ClassifyTurnService,
    InterpretQuestionService,
)
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    DiffPlanService,
)
from revi_investigation.application.ports import (
    FrameStore,
    InvestigationStore,
    ReferentRegistryStore,
    TraceStore,
    TurnEventBus,
)
from revi_investigation.application.refinement_llm import (
    EmitRefinementsService,
    ResolveReferentsService,
)
from revi_investigation.application.submit_turn.open_session import OpenSessionService
from revi_investigation.application.validation import (
    PlanValidationService,
)
from revi_investigation.application.window_maturity import (
    WindowMaturityService,
)


class _SubmitTurnBase:
    """The collaborators every section of :class:`SubmitTurnService` reads.

    Declarations only: :class:`SubmitTurnService` assigns them in its
    constructor. The engine is assembled from one class per turn phase so
    each phase stays readable on its own — see
    :mod:`~revi_investigation.application.submit_turn.service`.
    """

    _open_session: OpenSessionService
    _classifier: ClassifyTurnService
    _interpreter: InterpretQuestionService
    _planner: BuildInvestigationPlanService
    _validator: PlanValidationService
    _executor: ExecuteInvestigationService
    _calculator: CalculateMetricsService
    _evaluator: EvaluateFindingsService
    _referent_resolver: ResolveReferentsService
    _refinement_emitter: EmitRefinementsService
    _cohort_pinner: PinCohortService
    _differ: DiffPlanService
    _transforms: TransformPort
    _pack: PackPort
    _referents: ReferentRegistryStore
    _investigations: InvestigationStore
    _traces: TraceStore
    _frames: FrameStore
    _events: TurnEventBus
    _window_maturity: WindowMaturityService | None

