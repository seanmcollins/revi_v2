"""Deep research — the recoverability mode of the investigation engine.

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
from revi_investigation.application.deep_research.policy import (
    BandSpec,
    DeepResearchSettings,
)
from revi_investigation.application.deep_research.rows import DenialRows, DenialRowSource
from revi_investigation.application.deep_research.service import (
    DeepResearchProgress,
    DeepResearchResult,
    DeepResearchService,
)

__all__ = [
    "STANDING_ANGLES",
    "AngleFamily",
    "BandSpec",
    "DeepResearchPlan",
    "DeepResearchProgress",
    "DeepResearchResult",
    "DeepResearchService",
    "DeepResearchSettings",
    "DenialRowSource",
    "DenialRows",
    "PopulationKind",
    "RateBasisChoice",
    "ResearchAngle",
    "Stratum",
    "TargetPopulation",
    "plan_fingerprint",
    "standing_plan",
    "validate_plan",
]
