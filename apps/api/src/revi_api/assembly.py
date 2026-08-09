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

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from revi_api.cohort_payload import cohort_payload_for
from revi_api.debug_trace import build_debug_trace
from revi_api.evidence import build_evidence
from revi_api.metric_provenance import build_metric_provenance
from revi_api.warning_codes import structured_warnings
from revi_api.wiring import ApiComponents
from revi_investigation.application.capability_ports import BenchmarkSpec
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.ports import (
    LlmCallPolicy,
    LlmUsage,
    TextLlmRequest,
    TraceRecord,
)
from revi_investigation.application.submit_turn import TurnOutcome
from revi_investigation.domain.records import Finding, Investigation
from revi_investigation_contracts.api import (
    AnomalyReconciliationPayload,
    BenchmarkPayload,
    ChartSpec,
    CohortPayload,
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

#: Surfaced when grounding validation redacted EVERY sentence the composer
#: wrote. The turn keeps its findings, charts and grades; only the prose is
#: gone, and the answer says so rather than publishing an empty string that
#: renders as a narrative which exists and says nothing.
NARRATIVE_FULLY_REDACTED_WARNING = (
    "narrative not composed: every sentence the composer wrote cited something outside "
    "this answer's certified values and was redacted; the findings and charts below are "
    "complete and unaffected"
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
    cohort: CohortPayload | None = None,
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
        warnings_v2=structured_warnings(investigation.warnings),
        cohort=cohort,
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
    """The turn's model spend, summed from the recorded per-call entries.

    ``input_tokens`` on each entry is the *whole* prompt — the engine's port
    folds the provider's cached and uncached buckets together (see
    :class:`~revi_investigation.application.ports.LlmUsage`), so summing it
    here yields a prompt-token total rather than the uncached remainder that
    published turns reading ``input_tokens: 4`` beside 953 output tokens.
    """
    if trace is None:
        return UsageSummary()
    entries = trace.payload.get("llm", [])
    return _add_usage(UsageSummary(), entries)


def _add_usage(summary: UsageSummary, entries: Sequence[Mapping[str, Any]]) -> UsageSummary:
    """Fold recorded LLM-call entries into a running usage summary."""
    cost = Decimal(summary.cost_usd)
    calls = summary.llm_calls
    input_tokens = summary.input_tokens
    output_tokens = summary.output_tokens
    cache_read = summary.cache_read_tokens
    cache_creation = summary.cache_creation_tokens
    retries = summary.schema_retries
    for entry in entries:
        calls += 1
        cost += Decimal(str(entry.get("cost_usd", "0")))
        input_tokens += int(entry.get("input_tokens", 0))
        output_tokens += int(entry.get("output_tokens", 0))
        cache_read += int(entry.get("cache_read_tokens", 0))
        cache_creation += int(entry.get("cache_creation_tokens", 0))
        retries += int(entry.get("schema_retries", 0))
    return UsageSummary(
        llm_calls=calls,
        cost_usd=str(cost),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        schema_retries=retries,
    )


def usage_entry(template: str, usage: LlmUsage) -> dict[str, Any]:
    """One recorded LLM call, in the shape :func:`_add_usage` reads."""
    return {
        "template": template,
        "model": usage.model,
        "cost_usd": str(usage.cost_usd),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
        "schema_retries": usage.schema_retries,
        "attempts": usage.attempts,
        "duration_ms": usage.duration_ms,
    }


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
    usage_out: list[LlmUsage] | None = None,
) -> str | None:
    """Compose the answer's prose, and report what it cost.

    ``usage_out`` collects this call's usage. It exists because the
    narrative is the last stage of the turn and runs *after* the engine
    wrote the decision trace, so its tokens were absent from the recorded
    ``llm`` entries the envelope is summed from — the single largest
    generation on most turns, missing from the turn's own cost. The port's
    per-request sink hands it back here rather than through
    ``last_usage()``, which two concurrent turns would race on.
    """
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
    # Population caveats govern how the prose may characterize the figures;
    # display names stop a raw metric id from promising more than its
    # formula delivers. Both are the same governed content §6.6 and the
    # metric-display overlay already published on this turn.
    caveats = [
        w.split(": ", 1)[1]
        for w in warnings
        if w.startswith("population_caveat: ") and ": " in w
    ]
    display_names = {
        metric_id: entry.display_name
        for metric_id, entry in components.metric_display.by_metric.items()
    }
    prompt = build_narrative_prompt(
        findings=findings,
        header=header,
        reconciliation=outcome.reconciliation,
        # The governed ranges, at last: authored with cohort labels,
        # cautions and sources since KB wave 1, and passed here as a
        # literal empty tuple until the round-1 review counted them.
        benchmarks=[b.prompt_line for b in outcome.benchmarks],
        caveats=caveats,
        metric_display=display_names,
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
            usage_sink=None if usage_out is None else usage_out.append,
        )
    )
    async for delta in stream:
        chunks.append(delta)
        if on_event is not None:
            await on_event("narrative_delta", {"delta": delta})
    provisional = "".join(chunks).strip()
    if not provisional:
        return None
    facts = build_narrative_facts(
        findings=findings,
        header=header,
        # The governed ranges the composer was actually shown. They were
        # in the prompt and absent from the fact set for as long as both
        # have existed, so a narrative that quoted a benchmark — which the
        # analyst template asks it to do — had that sentence redacted for
        # citing a figure "matching no certified value". A range put in
        # front of the model is certified material and is admitted as such.
        benchmarks=[b.prompt_line for b in outcome.benchmarks],
        # Anything the prompt instructs the model to write, the validator
        # must be willing to admit — caveat figures and display names both.
        caveats=caveats,
        metric_display=display_names,
    )
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
                },
                # Recorded in the same shape as the engine's own entries, so
                # the narrative call is visible to a trace reader instead of
                # being a cost the turn paid and never wrote down.
                "llm": [
                    usage_entry(f"{NARRATIVE_TEMPLATE_ID}@{NARRATIVE_TEMPLATE_VERSION}", u)
                    for u in (usage_out or ())
                ],
            },
        )
    )
    if not validation.text.strip():
        # Every sentence failed grounding, so there is no prose left. An
        # empty string on the wire renders as a narrative that exists and
        # says nothing, which reads as a bug in the composer rather than
        # as what happened; the answer says the prose was withheld and the
        # findings, charts and grades stand untouched.
        warnings.append(NARRATIVE_FULLY_REDACTED_WARNING)
        return None
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
    anomaly_reconciliation: AnomalyReconciliationPayload | None = None,
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
    if outcome.emptiness is not None:
        # An empty result is a first-class fact, not a blank card: say which
        # kind of nothing this was and, for an empty population, which
        # predicates could have emptied it.
        _empty_detail = outcome.emptiness.detail
        if outcome.emptiness.predicates:
            _empty_detail += " — predicates in play: " + "; ".join(
                outcome.emptiness.predicates
            )
        warnings.append(f"empty_result: {_empty_detail}")
    narrative_usage: list[LlmUsage] = []
    narrative = await _compose_narrative(
        components,
        outcome,
        findings,
        warnings,
        on_event,
        spent=Decimal(usage.cost_usd),
        usage_out=narrative_usage,
    )
    # The last model call of the turn, folded in after it ran. Everything
    # else was summed from the trace the engine wrote before this stage
    # existed, which is exactly why the narrative was missing from it.
    usage = _add_usage(usage, [usage_entry("compose_narrative", u) for u in narrative_usage])

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

    # The pinned population, said in words rather than as a hash. Read
    # back off the cohort store by the id the header already names, so
    # this adds one metadata read on the turns that pinned a cohort and
    # nothing at all on the ones that did not.
    cohort = await cohort_payload_for(
        outcome.header.cohort if outcome.header is not None else None,
        session_id=outcome.session.id,
        cohorts=components.cohorts,
        referents=components.referents,
        investigations=components.investigations,
    )
    # Governed display names for the ids this answer cites, for the ids
    # whose name overclaims what they measure. Findings first (the order
    # the analyst reads them in), then anything else the probes named.
    cited: list[str] = [mid for f in findings for mid in f.metric_ids]
    if metric is not None:
        cited.extend(m.id for m in metric.metrics)

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
        warnings_v2=structured_warnings(warnings),
        cohort=cohort,
        metric_display=components.metric_display.payloads_for(cited),
        anomaly_reconciliation=anomaly_reconciliation,
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
