"""Turn-outcome → TurnResponse assembly: charts, narrative, payload maps.

This is the presentation stage of the turn pipeline (§8.1 steps 13-14).
It runs OUTSIDE the engine — the capability-independence contract bars
``revi_investigation`` from importing ``revi_presentation`` — and is
shared by both clients, so the SSE ordering falls out naturally:

    stage* (engine) → context_header → finding* → chart_spec* →
    narrative_delta* → turn_complete (the full TurnResponse)

The narrative stream is provisional; the grounding-validated text on the
final TurnResponse is authoritative (violating sentences are redacted with
a bracketed note, warnings recorded, and a supplementary narrative trace
record persisted).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from revi_api.debug_trace import build_debug_trace
from revi_api.evidence import build_evidence
from revi_api.metric_provenance import build_metric_provenance
from revi_api.wiring import ApiComponents
from revi_investigation.application.capability_ports import BenchmarkSpec
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.ports import LlmCallPolicy, TextLlmRequest, TraceRecord
from revi_investigation.application.submit_turn import TurnOutcome
from revi_investigation.domain.records import Finding, Investigation
from revi_investigation_contracts.api import (
    BenchmarkPayload,
    ChartSpec,
    DebugTracePayload,
    DefinitionalPayload,
    EvidencePayload,
    FindingPayload,
    FindingValue,
    InvestigationResponse,
    MetaAnswerPayload,
    MetricProvenancePayload,
    ReferentPayload,
    TermPayload,
    TurnAnswer,
    TurnClarification,
    UsageSummary,
)
from revi_kernel.frame import EvidenceFrame
from revi_presentation import (
    NARRATIVE_TEMPLATE_ID,
    NARRATIVE_TEMPLATE_VERSION,
    build_chart_specs,
    build_narrative_facts,
    build_narrative_prompt,
    template_hash,
    validate_narrative,
)

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]

#: Suffix of the supplementary trace record the narrative validator writes,
#: so a reader of ``for_investigation`` can tell it from the decision trace.
NARRATIVE_TRACE_SUFFIX = ":narrative"

#: Warning surfaced when a turn's cost ceiling left nothing for the
#: narrative. The findings, charts and grades are untouched — only the
#: prose is missing, and the answer says so rather than reading as though
#: there were nothing to write.
NARRATIVE_BUDGET_WARNING = (
    "narrative not composed: this turn reached its cost ceiling after the evidence was "
    "computed; the findings and charts below are complete"
)

#: Mirrors the engine's per-call floor: below this there is no call worth
#: making, only a provider refusal that would read like an outage.
_MIN_NARRATIVE_BUDGET_USD = Decimal("0.001")


def _finding_value(name: str, value: object) -> FindingValue:
    if isinstance(value, Decimal):
        return FindingValue(name=name, value=float(value))
    if value is None or isinstance(value, (str, int, float, bool)):
        return FindingValue(name=name, value=value)
    return FindingValue(name=name, value=str(value))


def benchmark_payload(benchmark: BenchmarkSpec) -> BenchmarkPayload:
    return BenchmarkPayload(
        id=benchmark.id,
        metric_id=benchmark.metric_id,
        cohort_label=benchmark.cohort_label,
        value_low=benchmark.value_low,
        value_high=benchmark.value_high,
        unit=benchmark.unit,
        period=benchmark.period,
        authority=benchmark.authority,
        review_status=benchmark.review_status,
        cautions=list(benchmark.cautions),
        sources=list(benchmark.source_titles),
    )


def finding_payload(
    finding: Finding, benchmarks: Sequence[BenchmarkSpec] = ()
) -> FindingPayload:
    metric_ids = [ref.id for ref in finding.metric_refs]
    return FindingPayload(
        referent=finding.referent.value,
        title=finding.title,
        statement=finding.statement,
        metric_ids=metric_ids,
        values=[_finding_value(name, value) for name, value in finding.values],
        grade=finding.grade.value,
        impact_cents=finding.impact_cents,
        confidence=finding.confidence,
        suggested_refinements=list(finding.suggested_refinements),
        benchmarks=[
            benchmark_payload(b) for b in benchmarks if b.metric_id in metric_ids
        ],
    )


def investigation_response(
    investigation: Investigation,
    trace: TraceRecord | None = None,
    chart_specs: Sequence[ChartSpec] = (),
) -> InvestigationResponse:
    """A stored investigation on the wire.

    ``trace`` is the turn's recorded decision trace when the caller has
    it. Passed, the response carries the same evidence bundle the live
    answer published — a turn restored when a session is re-opened shows
    the probes it ran rather than an empty drawer. Omitted (the lineage
    listing, which would otherwise read one trace per node), the field is
    ``None``: absent because it was not looked up, never an empty bundle
    implying the turn ran nothing.

    ``chart_specs`` are rebuilt from the turn's persisted frames by
    :func:`restored_chart_specs`. There is no equivalent for the
    narrative: nothing stores the composed prose (the narrative trace
    record keeps its template, its redactions and its length, not its
    text), so a restored turn shows its findings and charts and does not
    pretend to have kept the sentences.

    The governed-provenance block rides on the same ``trace``, for the
    same reason: a restored turn that lost its badge would read as an
    ungoverned answer.
    """
    return InvestigationResponse(
        investigation_id=investigation.id,
        session_id=investigation.session_id,
        parent_id=investigation.parent_id,
        turn_id=investigation.turn_id,
        turn_class=investigation.turn_class.value,
        status=investigation.status.value,
        question=investigation.question,
        plan_hash=investigation.plan_hash,
        findings=[finding_payload(f) for f in investigation.findings],
        warnings=list(investigation.warnings),
        evidence=build_evidence(trace) if trace is not None else None,
        metric=build_metric_provenance(trace) if trace is not None else None,
        chart_specs=list(chart_specs),
        created_at=investigation.created_at,
    )


async def restored_chart_specs(
    components: ApiComponents,
    investigation: Investigation,
    trace: TraceRecord | None = None,
) -> list[ChartSpec]:
    """Rebuild a stored turn's charts from the frames it persisted.

    Not a second computation of the answer: ``frame_refs`` point at the
    very frames the live turn charted, saved verbatim, and this runs the
    same :func:`build_chart_specs` over them with the same playbook id
    (read back off the trace) and the same row referents (read back off
    the registry, scoped to this investigation). A frame the store has
    since dropped simply yields no chart rather than a partial one.
    """
    frames: list[tuple[str, EvidenceFrame]] = []
    for key in investigation.frame_refs:
        frame = await components.frames.get(key)
        if frame is None:
            continue
        _, _, frame_id = key.partition(":")
        frames.append((frame_id or key, frame))
    if not frames:
        return []
    playbook_id: str | None = None
    if trace is not None:
        raw = (trace.payload.get("plan_context") or {}).get("playbook_id")
        playbook_id = raw if isinstance(raw, str) else None
    entries = await components.referents.list_for_session(investigation.session_id)
    row_referents = {
        entry.dimension_value: entry.referent.value
        for entry in entries
        if entry.dimension_value is not None and entry.investigation_id == investigation.id
    }
    return list(
        build_chart_specs(
            frames,
            recipes=components.recipes,
            playbook_id=playbook_id,
            row_referents=row_referents,
        )
    )


def _usage_from_trace(trace: TraceRecord | None) -> UsageSummary:
    if trace is None:
        return UsageSummary()
    entries = trace.payload.get("llm", [])
    cost = Decimal(0)
    input_tokens = output_tokens = retries = 0
    for entry in entries:
        cost += Decimal(str(entry.get("cost_usd", "0")))
        input_tokens += int(entry.get("input_tokens", 0))
        output_tokens += int(entry.get("output_tokens", 0))
        retries += int(entry.get("schema_retries", 0))
    return UsageSummary(
        llm_calls=len(entries),
        cost_usd=str(cost),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        schema_retries=retries,
    )


def _narrative_budget(outcome: TurnOutcome, spent: Decimal) -> Decimal | None:
    """What is left of the turn's ceiling for the narrative call.

    ``None`` means no ceiling was set, which leaves the call bounded by the
    deployment's own per-call cap — the behavior that predates the control.
    The engine ran the same ledger over its structured calls; this is the
    last stage, and it reads the spend back off the recorded trace rather
    than keeping a second counter that could disagree with the first.
    """
    ceiling = outcome.settings.max_turn_cost_usd
    return None if ceiling is None else ceiling - spent


async def _compose_narrative(
    components: ApiComponents,
    outcome: TurnOutcome,
    findings: list[FindingPayload],
    warnings: list[str],
    on_event: OnEvent | None,
    spent: Decimal = Decimal(0),
) -> str | None:
    header = outcome.header
    if header is None or not findings:
        return None
    remaining = _narrative_budget(outcome, spent)
    if remaining is not None and remaining < _MIN_NARRATIVE_BUDGET_USD:
        # Never a silent omission: the evidence stands, the prose does not,
        # and the warning says which and why.
        warnings.append(NARRATIVE_BUDGET_WARNING)
        return None
    depth = outcome.settings.narrative_depth
    prompt = build_narrative_prompt(
        findings=findings,
        header=header,
        reconciliation=outcome.reconciliation,
        # The governed ranges, at last: authored with cohort labels,
        # cautions and sources since KB wave 1, and passed here as a
        # literal empty tuple until the round-1 review counted them.
        benchmarks=[b.prompt_line for b in outcome.benchmarks],
        # A real composition parameter: depth selects the template that is
        # rendered, so the model writes a different piece rather than the
        # same one cut short.
        depth=depth,
    )
    assert_safe_payload(prompt)
    chunks: list[str] = []
    stream = components.llm.stream_text(
        TextLlmRequest(
            template_id=NARRATIVE_TEMPLATE_ID,
            template_version=NARRATIVE_TEMPLATE_VERSION,
            rendered_prompt=prompt,
            policy=LlmCallPolicy(
                model=outcome.settings.model_tier, max_cost_usd=remaining
            ),
        )
    )
    async for delta in stream:
        chunks.append(delta)
        if on_event is not None:
            await on_event("narrative_delta", {"delta": delta})
    provisional = "".join(chunks).strip()
    if not provisional:
        return None
    facts = build_narrative_facts(findings=findings, header=header)
    validation = validate_narrative(provisional, facts)
    warnings.extend(validation.warnings)
    await components.traces.save(
        TraceRecord(
            trace_id=f"{outcome.trace_id}:narrative",
            session_id=outcome.session.id,
            investigation_id=outcome.investigation.id,
            turn_id=outcome.investigation.turn_id,
            created_at=datetime.now(UTC),
            payload={
                "narrative": {
                    "template": f"{NARRATIVE_TEMPLATE_ID}@{NARRATIVE_TEMPLATE_VERSION}",
                    "depth": depth.value,
                    "template_hash": template_hash(depth),
                    "provisional_chars": len(provisional),
                    "redactions": [r.model_dump() for r in validation.redactions],
                    "validated": True,
                }
            },
        )
    )
    return validation.text


def _debug_payload(outcome: TurnOutcome, trace: TraceRecord | None) -> DebugTracePayload | None:
    """The decision trace, when the settings in force asked for it.

    Projected from the record the engine already wrote — never recomputed,
    because a debug view that derived its own numbers could disagree with
    the answer it is supposed to explain. Absent ``debug``, the trace is
    still recorded and still readable at
    ``GET /v1/investigations/{id}/trace``; it is simply not published with
    the answer.
    """
    if not outcome.settings.debug or trace is None:
        return None
    return build_debug_trace(trace)


async def assemble_turn_response(
    components: ApiComponents,
    outcome: TurnOutcome,
    *,
    on_event: OnEvent | None = None,
) -> TurnAnswer | TurnClarification:
    """Map an engine outcome to the wire shape, emitting presentation
    events along the way (see module docstring for the ordering)."""
    trace = await components.traces.get(outcome.trace_id)
    usage = _usage_from_trace(trace)
    debug = _debug_payload(outcome, trace)
    # Published on every answer, from the same record the debug view
    # reads. An empty bundle here means the trace store had nothing for
    # this turn — which the drawer says in those words rather than
    # drawing a reassuring "no queries ran".
    evidence = build_evidence(trace) if trace is not None else EvidencePayload()
    # Whose definition the numbers are, from that same record. ``None``
    # here means there was no trace to read — distinct from a recorded
    # turn that measured nothing governed, which publishes the block with
    # an empty metric list and says so.
    metric: MetricProvenancePayload | None = (
        build_metric_provenance(trace) if trace is not None else None
    )

    if outcome.clarification is not None:
        return TurnClarification(
            outcome="clarification_required",
            session_id=outcome.session.id,
            investigation_id=outcome.investigation.id,
            question=outcome.clarification.question,
            options=list(outcome.clarification.options),
            reason=outcome.clarification.reason,
            watermark_stale=outcome.watermark_stale,
            usage=usage,
            debug=debug,
        )

    findings = [finding_payload(f, outcome.benchmarks) for f in outcome.findings]
    if outcome.header is not None and on_event is not None:
        await on_event("context_header", outcome.header.model_dump(mode="json"))
    if on_event is not None:
        for payload in findings:
            await on_event("finding", payload.model_dump(mode="json"))

    playbook_id: str | None = None
    if trace is not None:
        plan_context = trace.payload.get("plan_context") or {}
        raw = plan_context.get("playbook_id")
        playbook_id = raw if isinstance(raw, str) else None
    row_referents = {
        entry.dimension_value: entry.referent.value
        for entry in outcome.referents
        if entry.dimension_value is not None
    }
    chart_specs: list[ChartSpec] = list(
        build_chart_specs(
            outcome.frames,
            recipes=components.recipes,
            playbook_id=playbook_id,
            row_referents=row_referents,
        )
    )
    if on_event is not None:
        for spec in chart_specs:
            await on_event("chart_spec", spec.model_dump(mode="json"))

    warnings = list(outcome.warnings)
    narrative = await _compose_narrative(
        components, outcome, findings, warnings, on_event, spent=Decimal(usage.cost_usd)
    )

    definitional: DefinitionalPayload | None = None
    if outcome.definitional is not None:
        definitional = DefinitionalPayload(
            question=outcome.definitional.question,
            terms=[
                TermPayload(
                    term=t.term,
                    kind=t.kind,
                    title=t.title,
                    definition=t.definition,
                    source=t.source,
                )
                for t in outcome.definitional.terms
            ],
            pack_id=outcome.definitional.pack_id,
            pack_version=outcome.definitional.pack_version,
            pack_snapshot_id=outcome.definitional.pack_snapshot_id,
        )
    meta: MetaAnswerPayload | None = None
    if outcome.meta is not None:
        meta = MetaAnswerPayload(
            referent=outcome.meta.referent,
            label=outcome.meta.label,
            investigation_id=outcome.meta.investigation_id,
            probes=[dict(p) for p in outcome.meta.probes],
            operators=[dict(op) for op in outcome.meta.operators],
            grades=dict(outcome.meta.grades),
            reconciliation=outcome.meta.reconciliation,
            finding_values=[_finding_value(n, v) for n, v in outcome.meta.finding_values],
            warnings=list(outcome.meta.warnings),
        )

    return TurnAnswer(
        outcome="answer",
        session_id=outcome.session.id,
        investigation_id=outcome.investigation.id,
        turn_class=outcome.investigation.turn_class.value,
        context_header=outcome.header,
        findings=findings,
        chart_specs=chart_specs,
        narrative=narrative,
        warnings=warnings,
        meta_answer=meta,
        definitional=definitional,
        referents=[
            ReferentPayload(id=e.referent.value, kind=e.referent.kind.value, label=e.label)
            for e in outcome.referents
        ],
        benchmarks=[benchmark_payload(b) for b in outcome.benchmarks],
        reconciliation=outcome.reconciliation,
        plan_hash=outcome.investigation.plan_hash,
        watermark_stale=outcome.watermark_stale,
        usage=usage,
        evidence=evidence,
        metric=metric,
        debug=debug,
    )
