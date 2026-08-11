"""Writing up a deep-research run — the same composer, the same discipline.

Nothing here is special-cased. The report's prose goes through the exact
path every other answer's prose goes through: the composer is shown the
question and told its first sentence must answer it, it may cite only
certified findings, and every figure it writes is checked against a value
some estimator actually produced. A sentence that cites a figure no finding
carries is dropped and counted, not marked and kept.

Two choices are specific to this mode and worth stating.

**The shape is "how much".** A recoverability review asks what the open
inventory is worth, so the composer is given the same directive a how-much
question gets: state the total first, then what it is made of.

**The disclosures lead, and they are not optional.** What could not be
priced, what the range assumes, and what the edge of the data excluded are
composed in front of the prose. A reader who stops after the first
paragraph has the number and its limits; they never have the number alone.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal

from revi_investigation.application.deep_research.report import ReportDraft
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.ports import (
    LanguageModelPort,
    LlmCallPolicy,
    LlmUsage,
    TextLlmRequest,
)
from revi_investigation_contracts.api import WarningPayload
from revi_investigation_contracts.settings import NarrativeDepth
from revi_kernel.errors import PolicyDeniedError, ReviError
from revi_presentation import (
    NARRATIVE_TEMPLATE_ID,
    NARRATIVE_TEMPLATE_VERSION,
    build_narrative_facts,
    build_narrative_prompt,
    compose_narrative,
    validate_narrative,
)

logger = logging.getLogger("revi.api.deep_research.narrative")

#: A recoverability review is a how-much question: the total comes first.
ANSWER_SHAPE = "scalar"


async def compose_report_narrative(
    *,
    llm: LanguageModelPort,
    draft: ReportDraft,
    warnings: Sequence[WarningPayload],
    emit: Callable[[str], Awaitable[None]] | None = None,
    metric_display: Mapping[str, str] | None = None,
    model_tier: str | None = None,
    max_cost_usd: Decimal | None = None,
    usage_out: list[LlmUsage] | None = None,
) -> tuple[str, tuple[str, ...]]:
    """The written report, and the sentences that were dropped from it."""
    report = draft.report
    caveats = [
        warning.message
        for warning in warnings
        if warning.severity == "caution"
    ]
    published_cautions = len(caveats)
    disclosures = list(draft.disclosures)

    prompt = build_narrative_prompt(
        findings=report.findings,
        header=draft.header,
        reconciliation=None,
        benchmarks=(),
        caveats=caveats,
        metric_display=dict(metric_display or {}),
        depth=NarrativeDepth.ANALYST,
        disclosures=disclosures,
        published_cautions=published_cautions,
        question=report.research_question,
        answer_shape=ANSWER_SHAPE,
    )
    facts = build_narrative_facts(
        findings=report.findings,
        header=draft.header,
        benchmarks=(),
        caveats=caveats,
        metric_display=dict(metric_display or {}),
        disclosures=disclosures,
        published_cautions=published_cautions,
        question=report.research_question,
        extra_names=tuple(note for note in report.context_notes),
    )

    body = ""
    try:
        assert_safe_payload(prompt)
        chunks: list[str] = []
        stream = llm.stream_text(
            TextLlmRequest(
                template_id=NARRATIVE_TEMPLATE_ID,
                template_version=NARRATIVE_TEMPLATE_VERSION,
                rendered_prompt=prompt,
                policy=LlmCallPolicy(model=model_tier, max_cost_usd=max_cost_usd),
                usage_sink=None if usage_out is None else usage_out.append,
            )
        )
        async for chunk in stream:
            chunks.append(chunk)
            if emit is not None and chunk:
                await emit(chunk)
        body = "".join(chunks).strip()
    except (PolicyDeniedError, ReviError):
        logger.warning("deep research narrative refused before it was composed")
    except Exception:
        logger.exception("deep research narrative failed; publishing the disclosures alone")

    redactions: tuple[str, ...] = ()
    if body:
        validation = validate_narrative(body, facts)
        body = validation.text
        redactions = tuple(
            redaction.sentence for redaction in validation.redactions
        )
    text, _ = compose_narrative(disclosures, body, ())
    return text, redactions
