"""Presentation: chart specs, canonical context headers, narrative
grounding validation. Pure functions over kernel frames and contract DTOs
— this package never imports capability implementations."""

from revi_investigation_contracts.header import build_header_payload
from revi_presentation.charts import (
    ChartSuggestion,
    RecipeSpec,
    build_chart_spec,
    build_chart_specs,
)
from revi_presentation.narrative import (
    NARRATIVE_TEMPLATE,
    NARRATIVE_TEMPLATE_ANALYST,
    NARRATIVE_TEMPLATE_ID,
    NARRATIVE_TEMPLATE_VERSION,
    NARRATIVE_TEMPLATES,
    REDACTION_NOTE,
    REDACTION_WARNING_PREFIX,
    build_narrative_facts,
    build_narrative_prompt,
    template_hash,
    validate_narrative,
)

__all__ = [
    "NARRATIVE_TEMPLATE",
    "NARRATIVE_TEMPLATES",
    "NARRATIVE_TEMPLATE_ANALYST",
    "NARRATIVE_TEMPLATE_ID",
    "NARRATIVE_TEMPLATE_VERSION",
    "REDACTION_NOTE",
    "REDACTION_WARNING_PREFIX",
    "ChartSuggestion",
    "RecipeSpec",
    "build_chart_spec",
    "build_chart_specs",
    "build_header_payload",
    "build_narrative_facts",
    "build_narrative_prompt",
    "template_hash",
    "validate_narrative",
]
