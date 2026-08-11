"""Deep research — any research question, answered over governed semantics.

A run has two shapes, and they share every rule.

**The recovery domain** (M48) reads denial rows once at the pinned load and
cuts them in memory through the closed angle catalogue. It is unchanged: the
same executors, the same estimators, the same report, byte for byte.

**Generalized research** (:mod:`.loop`) answers any research question. It
orients with the discovery family, consults the pack's RCM knowledge to
decide what deserves checking, plans angles over the FULL catalog in the
generalized grammar (:mod:`.general`), executes them deterministically
through the ordinary probe path (:mod:`.measures`), **reads its own
certified results and iterates** inside a depth-scaled budget, and
synthesizes. The recovery families are the recovery domain's instances of
the generalized shapes, which is why the two are one mode rather than two.

Whichever shape a run takes, the phases fail differently and so are kept
apart.

A deep-research run is a long-running investigation in three phases.

**PLAN.** The control plane picks research angles from a closed catalogue
(:mod:`~revi_investigation.application.deep_research.grammar`). It selects
and parameterizes; it never invents an angle and never computes a number.
Everything it returns is re-validated against the catalogue before a single
row is read, and a run whose model call fails falls back to the standing
set rather than guessing — and says which of the two happened.

**EXECUTE.** Each angle runs deterministically
(:mod:`~revi_investigation.application.deep_research.angles`). Rows are
read once, at the pinned data load, through the ordinary analytical port,
and handed to ``revi_statistics``. Every estimate comes back labelled: a
population's own history supported a rate, or it did not and no rate is
published for it.

**SYNTHESIZE.** The report is composed from the certified findings
(:mod:`~revi_investigation.application.deep_research.report`), under the
same discipline every other answer obeys — the composer is shown the
question and its first sentence must answer it.

This is a MODE of investigation, not a parallel system: the same analytical
port, the same evidence cache, the same data-load pinning, the same
findings and warnings vocabulary, the same composer.
"""

from revi_investigation.application.deep_research.general import (
    RECOVERY_SHAPES,
    AngleShape,
    AngleVocabulary,
    MeasureAngle,
    PlannedAngle,
    ResearchWalk,
    TimeStep,
    WalkStep,
    normalize_measure_angle,
    walk_fingerprint,
)
from revi_investigation.application.deep_research.grammar import (
    STANDING_ANGLES,
    AngleFamily,
    DeepResearchPlan,
    PopulationKind,
    RateBasisChoice,
    ResearchAngle,
    Stratum,
    TargetPopulation,
    plan_fingerprint,
    standing_plan,
    validate_plan,
)
from revi_investigation.application.deep_research.knowledge import (
    KnowledgeConsultation,
    as_prompt_context,
    consult,
)
from revi_investigation.application.deep_research.loop import (
    GeneralizedResearchLoop,
    GeneralPlanner,
    Orientation,
    ResearchOrienter,
    ResearchRound,
    default_window,
    iteration_budget,
    standing_angles,
)
from revi_investigation.application.deep_research.measures import (
    MeasureAngleRunner,
    MeasureCell,
    MeasureResult,
)
from revi_investigation.application.deep_research.policy import (
    BandSpec,
    DeepResearchSettings,
    ResearchPolicy,
)
from revi_investigation.application.deep_research.rows import DenialRows, DenialRowSource
from revi_investigation.application.deep_research.service import (
    DeepResearchProgress,
    DeepResearchResult,
    DeepResearchService,
)

__all__ = [
    "RECOVERY_SHAPES",
    "STANDING_ANGLES",
    "AngleFamily",
    "AngleShape",
    "AngleVocabulary",
    "BandSpec",
    "DeepResearchPlan",
    "DeepResearchProgress",
    "DeepResearchResult",
    "DeepResearchService",
    "DeepResearchSettings",
    "DenialRowSource",
    "DenialRows",
    "GeneralPlanner",
    "GeneralizedResearchLoop",
    "KnowledgeConsultation",
    "MeasureAngle",
    "MeasureAngleRunner",
    "MeasureCell",
    "MeasureResult",
    "Orientation",
    "PlannedAngle",
    "PopulationKind",
    "RateBasisChoice",
    "ResearchAngle",
    "ResearchOrienter",
    "ResearchPolicy",
    "ResearchRound",
    "ResearchWalk",
    "Stratum",
    "TargetPopulation",
    "TimeStep",
    "WalkStep",
    "as_prompt_context",
    "consult",
    "default_window",
    "iteration_budget",
    "normalize_measure_angle",
    "plan_fingerprint",
    "standing_angles",
    "standing_plan",
    "validate_plan",
    "walk_fingerprint",
]
