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

A RESEARCH STUDY IS COMPOSED HERE TOO, through its own template and the
same validator (:func:`compose_determination`). The difference is what the
composer is shown — the walk's own reasons, and the consulted background
notes as quotable context — and the difference is bounded by the fact set
rather than by the wording beside it. See that function for the argument.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal

from revi_investigation.application.deep_research.general_report import (
    GeneralizedReportDraft,
)
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
    DETERMINATION_TEMPLATE_ID,
    DETERMINATION_TEMPLATE_VERSION,
    NARRATIVE_TEMPLATE_ID,
    NARRATIVE_TEMPLATE_VERSION,
    build_determination_facts,
    build_determination_prompt,
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


async def compose_determination(
    *,
    llm: LanguageModelPort,
    draft: GeneralizedReportDraft,
    warnings: Sequence[WarningPayload],
    emit: Callable[[str], Awaitable[None]] | None = None,
    metric_display: Mapping[str, str] | None = None,
    model_tier: str | None = None,
    max_cost_usd: Decimal | None = None,
    usage_out: list[LlmUsage] | None = None,
) -> tuple[str, bool, tuple[str, ...]]:
    """The determination, whether a composer wrote it, and what was dropped.

    The same seam the recoverability review's write-up goes through, with
    one template of its own (``compose_research_determination``) and one
    widening of the fact set. Everything that makes the prose trustworthy
    is identical: the composer sees only certified readings, every figure
    it writes is checked against a value an estimator produced, and a
    sentence that fails is dropped rather than marked.

    THE THREE THINGS IT SEES THAT AN ANSWER'S COMPOSER DOES NOT.

    **The question**, as the first thing in the prompt, with the
    instruction that its first sentence must answer it — and that a
    composite question ("why is it climbing AND what will it take to bring
    it down") owes an answer to both halves or an explicit statement that
    one could not be given.

    **The walk's reasons.** The run already wrote down why each reading
    exists; quoting them back is what lets the determination explain the
    shape of its own answer rather than describing a pile of tables.

    **The background notes, as quotable context.** They may inform what the
    answer MEANS; they may never be a number. That wall is held by the fact
    set rather than by the instruction beside it: their figures are not
    admitted, so a sentence lifting an industry benchmark out of one fails
    grounding and is dropped like any other ungrounded claim.

    ONE THING THE FACT SET SEES THAT THE COMPOSER DOES NOT: the ranges.
    The readings go into ``build_determination_facts`` carrying the
    confidence interval under every figure, which is what lets the
    validator refuse a claim whose numbers are all certified and whose
    DIRECTION is not — six monthly rates inside overlapping intervals
    narrated as a decline, a best and a worst named 1.1 points apart. The
    composer is not asked to police itself about this; it is checked.

    Returns ``(text, composed, redactions)``. ``composed`` is ``False`` when
    the model produced nothing publishable, in which case the disclosures
    stand alone and the report says so rather than presenting an empty
    determination as an answer.
    """
    report = draft.report
    caveats = [warning.message for warning in warnings if warning.severity == "caution"]
    published_cautions = len(caveats)
    disclosures = list(draft.disclosures)
    trailing = list(draft.trailing)
    # Every label the readings published is certified vocabulary: a payer
    # name a figure is over is exactly as certified as the figure itself,
    # and a determination that could not name it would be unable to say
    # which population its own answer is about.
    names: list[str] = [reading.measure_label for reading in report.readings]
    for reading in report.readings:
        names.extend(figure.label for figure in reading.figures if figure.evidence == "measured")

    prompt = build_determination_prompt(
        findings=report.findings,
        header=draft.header,
        question=report.research_question,
        walk=draft.walk_reasons,
        knowledge=draft.knowledge_context,
        caveats=caveats,
        disclosures=[*disclosures, *trailing],
        metric_display=dict(metric_display or {}),
        published_cautions=published_cautions,
    )
    facts = build_determination_facts(
        findings=report.findings,
        header=draft.header,
        extra_names=tuple(dict.fromkeys(names)),
        caveats=caveats,
        disclosures=[*disclosures, *trailing],
        knowledge=draft.knowledge_context,
        # The readings themselves, for the ranges under their figures. A
        # determination may cite a figure only if a finding certifies it;
        # it may call the movement between two of them a DIRECTION only if
        # the ranges around them say so. The second check needs the
        # intervals, and this is where they are.
        readings=report.readings,
        metric_display=dict(metric_display or {}),
        published_cautions=published_cautions,
        question=report.research_question,
    )

    body = ""
    try:
        assert_safe_payload(prompt)
        chunks: list[str] = []
        stream = llm.stream_text(
            TextLlmRequest(
                template_id=DETERMINATION_TEMPLATE_ID,
                template_version=DETERMINATION_TEMPLATE_VERSION,
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
        logger.warning("research determination refused before it was composed")
    except Exception:
        logger.exception("research determination failed; publishing the disclosures alone")

    redactions: tuple[str, ...] = ()
    if body:
        validation = validate_narrative(body, facts)
        body = validation.text
        redactions = tuple(redaction.sentence for redaction in validation.redactions)
    text, _ = compose_narrative(disclosures, body, trailing)
    return text, bool(body), redactions
