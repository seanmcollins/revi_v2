"""The one model call a deep-research run makes: which angles to look at.

It selects and it explains; it does not compute, and it cannot name an
analysis outside the catalogue — the response schema is closed and
everything that comes back is re-validated against the grammar before a
denial is read. A call that fails does not fail the run: the standing set
of angles is a complete answer on its own, and the report says which of the
two produced its plan rather than presenting a fallback as a choice.
"""

from __future__ import annotations

from revi_investigation.application.deep_research.copy import population_label
from revi_investigation.application.deep_research.grammar import (
    DeepResearchPlan,
    TargetPopulation,
    build_angle,
    standing_plan,
    validate_plan,
)
from revi_investigation.application.deep_research.policy import DeepResearchSettings
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.llm.render import LoadedTemplate, load_template, render_template
from revi_investigation.application.llm.schemas import (
    DeepResearchPlanResponse,
    sanitize_json_schema,
)
from revi_investigation.application.ports import (
    DEFAULT_LLM_CALL_POLICY,
    LanguageModelPort,
    LlmCallPolicy,
    StructuredLlmRequest,
)

TEMPLATE_ID = "plan_deep_research"
TEMPLATE_VERSION = "v1"


class PlanDeepResearchService:
    """Ask the control plane which angles this run should look at."""

    def __init__(self, llm: LanguageModelPort) -> None:
        self._llm = llm
        self._template: LoadedTemplate = load_template(TEMPLATE_ID, TEMPLATE_VERSION)
        self._schema = sanitize_json_schema(DeepResearchPlanResponse.model_json_schema())

    @property
    def template_hash(self) -> str:
        return self._template.sha256

    async def plan(
        self,
        *,
        question: str,
        population: TargetPopulation,
        settings: DeepResearchSettings,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> DeepResearchPlan:
        prompt = render_template(
            self._template.text,
            {
                "population": population_label(str(population.kind), population.values),
                "question": question.strip() or "(none — answer the standing question)",
            },
        )
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
                policy=policy,
            )
        )
        if result.output is None:
            return standing_plan(question)
        try:
            parsed = DeepResearchPlanResponse.model_validate(dict(result.output))
        except Exception:
            return standing_plan(question)

        angles = []
        for proposed in parsed.angles:
            angle = build_angle(
                proposed.family,
                stratify_by=proposed.stratify_by,
                within=proposed.within,
                basis=proposed.basis,
            )
            if angle is not None:
                angles.append(angle)
        if not angles:
            return standing_plan(question)
        supported = set(settings.supported_stratifiers())
        angles = [
            angle
            for angle in angles
            if all(stratum in supported for stratum in (*angle.stratify_by, *angle.within))
        ]
        if not angles:
            return standing_plan(question)
        return validate_plan(
            research_question=parsed.research_question or question,
            angles=angles,
            rationale=parsed.rationale,
            authored_by="model",
        )
