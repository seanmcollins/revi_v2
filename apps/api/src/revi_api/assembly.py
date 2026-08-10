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

from revi_api.chart_integrity import apply_axis_order, enforce_row_keys
from revi_api.cohort_payload import cohort_payload_for
from revi_api.debug_trace import build_debug_trace
from revi_api.evidence import build_evidence
from revi_api.metric_display import MetricDisplayRules
from revi_api.metric_provenance import build_metric_provenance
from revi_api.warning_codes import structured_warnings
from revi_api.wiring import ApiComponents
from revi_api.worklist import (
    worklist_first_action,
    worklist_lead_warning,
    worklist_warning,
)
from revi_investigation.application.capability_ports import BenchmarkSpec
from revi_investigation.application.findings import published_window_note
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
    ContextHeaderPayload,
    DebugTracePayload,
    DefinitionalPayload,
    EvidencePayload,
    FindingPayload,
    FindingValue,
    InvestigationResponse,
    MetaAnswerPayload,
    MetricProvenancePayload,
    MonitorRefusedPayload,
    ReferentPayload,
    TermPayload,
    TurnAnswer,
    TurnClarification,
    UsageSummary,
    WorklistPayload,
)
from revi_investigation_contracts.header import build_header_payload
from revi_kernel.filters import iter_predicates
from revi_kernel.frame import EvidenceFrame
from revi_presentation import (
    NARRATIVE_TEMPLATE_ID,
    NARRATIVE_TEMPLATE_VERSION,
    apply_metric_display,
    build_chart_specs,
    build_narrative_facts,
    build_narrative_prompt,
    compose_narrative,
    empty_narrative,
    mandatory_disclosures,
    reconciliation_disclosure,
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


def _police_charts(
    specs: Sequence[ChartSpec],
    frames: Sequence[tuple[str, EvidenceFrame]],
    components: ApiComponents,
) -> tuple[list[ChartSpec], list[str]]:
    """Row-key uniqueness and declared bucket order, before publish.

    Both checks need the frame the spec was built from — the spec alone
    cannot tell a legitimately repeated x from a grouping column nobody
    declared — so they run here, where the frames are still in hand, rather
    than inside the chart builder.

    The bucket order is read straight off the catalog by the x column's
    dimension id, which is the same authority ``InvestigationPlan.
    bucket_orders`` reads: doing it here means a RESTORED or re-derived
    turn, which rebuilds its charts from persisted frames with no plan in
    hand, gets the same axis as the live one.
    """
    by_id = dict(frames)
    orders: dict[str, tuple[str, ...]] = {}
    for _, frame in frames:
        for column in frame.schema.columns:
            declared = components.catalog.dimension(column.ref.id)
            if declared is not None and declared.buckets:
                orders[column.ref.id] = tuple(declared.buckets)
    kept: list[ChartSpec] = []
    warnings: list[str] = []
    for spec in specs:
        frame = by_id.get(spec.frame_id)
        repaired, warning = enforce_row_keys(spec, frame)
        if warning is not None:
            warnings.append(warning)
        if repaired is None:
            continue
        kept.append(apply_axis_order(repaired, frame, orders))
    return kept, warnings


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
    finding: Finding,
    benchmarks: Sequence[BenchmarkSpec] = (),
    metric_display: MetricDisplayRules | None = None,
) -> FindingPayload:
    """One finding on the wire, with governed display names applied.

    Round-2 FN-5: the display name reached ``metric_display`` and the
    narrative body and *not* the title — the field a reader scans and a
    screenshot carries. The rewrite is done here, at the rendering
    boundary, because the engine may not read pack-adjacent display
    content and a client-side rewrite is not a correction (replay, export
    and any second client keep the mislabel). The metric's governed caveat
    rides along on the payload so it can be rendered as visible text under
    the title rather than behind a hover.
    """
    metric_ids = [ref.id for ref in finding.metric_refs]
    names = metric_display.names if metric_display is not None else None
    caveats = [
        entry.caveat
        for mid in metric_ids
        if (entry := (metric_display.by_metric.get(mid) if metric_display else None))
        is not None
        and entry.caveat
    ]
    return FindingPayload(
        referent=finding.referent.value,
        title=apply_metric_display(finding.title, names),
        statement=apply_metric_display(finding.statement, names),
        metric_ids=metric_ids,
        values=[_finding_value(name, value) for name, value in finding.values],
        grade=finding.grade.value,
        impact_cents=finding.impact_cents,
        confidence=finding.confidence,
        suggested_refinements=list(finding.suggested_refinements),
        benchmarks=[
            benchmark_payload(b) for b in benchmarks if b.metric_id in metric_ids
        ],
        metric_caveats=caveats,
    )


#: Prefix of the §6.6 warning that says a filter value was corrected before
#: the query ran (see :mod:`revi_api.warning_codes`). A restored header is
#: rebuilt from the stored spec, which keeps the value as the analyst typed
#: it, so a turn carrying this warning gets a note saying so.
_VALUE_CORRECTED_PREFIX = "value_corrected:"


def restored_context_header(
    investigation: Investigation,
    snapshot_metric_ids: frozenset[str] = frozenset(),
) -> ContextHeaderPayload | None:
    """The §7.2 header of a STORED turn, rebuilt from its own spec.

    Round-2 deferred P0: a re-opened session restored dollar figures with
    no window, no scope, no cohort and no data date, while the caveats
    survived — so the restored view was caveats about a number whose
    meaning had been dropped. Every one of those facts is on the persisted
    :class:`~revi_investigation.domain.context.AnalysisSpec`, so nothing
    here is reconstructed by inference:
    :func:`~revi_investigation_contracts.header.build_header_payload` — the
    same canonical builder the live turn used — is run over the stored
    window, comparison, scope, pins and cohort.

    Two deliberate differences from the live path:

    * the watermark is the **turn's own** (``spec.context.watermark``),
      not the session's current one: a session that has re-anchored since
      must not restamp an old answer with a load it never read;
    * ``corrections`` is empty, because the §6.6 value-resolution map is
      not persisted. The chips therefore state the values the spec holds;
      when the turn recorded a correction warning, the response says so in
      ``restoration_notes`` rather than letting the chip imply it queried
      the analyst's spelling.
    * ``window_note`` is rebuilt from the stored FINDINGS rather than from
      a plan (a stored turn holds a ``plan_hash``, not a plan). Every
      finding whose probe read its own window publishes that window as
      named values — ``<measure>__window_start`` / ``__window_end``, the
      same shape ``__is_bound`` uses — precisely so this and every other
      consumer can ask rather than parse a sentence.

    ``None`` when the turn stored no measures and no scope at all — a
    minimal record (a clarification, a context-control turn) has no
    effective context to publish, and inventing one would be worse than
    the absence.
    """
    spec = getattr(investigation, "spec", None)
    context = getattr(spec, "context", None)
    if spec is None or context is None:
        return None
    as_of = (
        context.watermark.newest_data_date
        if spec.measures
        and all(ref.id in snapshot_metric_ids for ref in spec.measures)
        else None
    )
    return build_header_payload(
        window=context.window,
        comparison=context.comparison,
        predicates=tuple(iter_predicates(context.scope)),
        pinned_predicates=tuple(pin.predicate for pin in context.pins),
        cohort=context.cohort,
        watermark_id=context.watermark.id,
        as_of=as_of,
        # The SAME composer the live turn uses, over the same named values
        # on the same findings — so a restored header and the live one it
        # restores say the identical sentence about the identical answer.
        window_note=published_window_note(getattr(investigation, "findings", ())),
    )


def _and_list(items: Sequence[str]) -> str:
    """"a, b and c" — an inventory a reader can check item by item."""
    if not items:  # pragma: no cover - callers always pass at least two
        return "nothing"
    if len(items) == 1:  # pragma: no cover - callers always pass at least two
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _restoration_notes(
    investigation: Investigation,
    trace: TraceRecord | None,
    header: ContextHeaderPayload | None,
    *,
    chart_specs: Sequence[ChartSpec] = (),
) -> list[str]:
    """What restoring this turn recovered, and what it could not.

    Written from facts about the record in hand — never a guess about why
    something is missing, and never a claim about something not in hand.

    Round-10 R10-4 fixed both halves of this note. It used to end *"Its
    findings, warnings and charts are its own record"* unconditionally, on
    a response that shipped ``chart_specs: []`` — false on every lineage
    node, where charts are not looked up at all, and false again whenever
    the frames behind a turn had been swept. And it opened by asserting the
    prose was gone, which stopped being true the moment the narrative was
    persisted. Both sentences are now composed from what this response
    actually carries.
    """
    notes: list[str] = []
    if header is not None:
        notes.append(
            "Restored context: the window, scope, cohort and watermark below are rebuilt "
            f"from this turn's stored investigation spec at watermark "
            f"{header.watermark_id}, not re-computed — the figures are the ones this turn "
            "published when it ran."
        )
    else:
        notes.append(
            "This turn stored no analysable context (no measures and no scope), so there "
            "is no effective-context header to restore for it."
        )
    # What this link actually carries, item by item, and nothing else. A
    # restored turn is read by someone who was not in the room, so an
    # inventory that overstates by one noun is worse than no inventory.
    kept = ["the findings", "their warnings and caveats"]
    if chart_specs:
        kept.append("the charts")
    if investigation.narrative:
        kept.insert(0, "the written analysis exactly as it was published")
        notes.append(
            "This turn restores with " + _and_list(kept) + ". Nothing here was re-computed "
            "or re-written: these are the sentences and figures this turn published when it "
            "ran."
        )
    else:
        notes.append(
            "The written analysis was not stored for this turn — the narrative trace keeps "
            "its template, redactions and length, not its sentences — so this turn restores "
            "without prose. What this link carries is " + _and_list(kept) + "."
            + ("" if chart_specs else " Its charts are not part of this record.")
        )
    if any(w.startswith(_VALUE_CORRECTED_PREFIX) for w in investigation.warnings):
        notes.append(
            "This turn corrected at least one filter value at validation time (see its "
            "warnings). The correction map is not persisted with the spec, so the scope "
            "chips above show the values as stored rather than as queried."
        )
    if trace is None:
        notes.append(
            "No decision trace was recorded for this turn, so its evidence and "
            "governed-provenance blocks are absent rather than empty."
        )
    return notes


#: Reads governed benchmark ranges for a metric id — the pack port's own
#: method, taken structurally so this module does not import the adapter.
BenchmarksForMetric = Callable[[str], Sequence[BenchmarkSpec]]


def _restored_benchmarks(
    investigation: Investigation, benchmarks_for_metric: BenchmarksForMetric | None
) -> tuple[BenchmarkSpec, ...]:
    """The governed ranges this turn's findings cite, re-read from the pack.

    The same harvest the live turn ran (every benchmark for every metric a
    finding cites, deduplicated by id) — governed content keyed by metric
    id, so restoring it is a pack lookup rather than a stored copy. Without
    this a re-opened turn lost the peer context the live answer carried and
    the narrative had quoted.
    """
    if benchmarks_for_metric is None:
        return ()
    seen: dict[str, BenchmarkSpec] = {}
    for finding in investigation.findings:
        for ref in finding.metric_refs:
            for benchmark in benchmarks_for_metric(ref.id):
                seen.setdefault(benchmark.id, benchmark)
    return tuple(seen.values())


def investigation_response(
    investigation: Investigation,
    trace: TraceRecord | None = None,
    chart_specs: Sequence[ChartSpec] = (),
    cohort: CohortPayload | None = None,
    metric_display: MetricDisplayRules | None = None,
    snapshot_metric_ids: frozenset[str] = frozenset(),
    benchmarks_for_metric: BenchmarksForMetric | None = None,
    pack_version: str | None = None,
    monitor_refused: MonitorRefusedPayload | None = None,
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
    :func:`restored_chart_specs`, and are what the restoration note counts
    when it says what this link carries — the note used to claim charts
    while the same response shipped ``[]`` (round-10 R10-4).

    The narrative is the turn's OWN, read off the stored record and never
    reconstructed. A turn that ran before the narrative was persisted, or
    that composed no prose, publishes ``None`` here and a note saying so.

    The governed-provenance block rides on the same ``trace``, for the
    same reason: a restored turn that lost its badge would read as an
    ungoverned answer.

    The §7.2 context header is rebuilt here from the stored spec (see
    :func:`restored_context_header`) and marked as restored, together with
    the turn's own watermark and data date — the facts that say what the
    restored figures count.
    """
    header = restored_context_header(investigation, snapshot_metric_ids)
    watermark = getattr(getattr(investigation.spec, "context", None), "watermark", None)
    benchmarks = _restored_benchmarks(investigation, benchmarks_for_metric)
    notes = _restoration_notes(investigation, trace, header, chart_specs=chart_specs)
    stored_pack = getattr(getattr(investigation.spec, "context", None), "pack_version", None)
    if (
        benchmarks
        and pack_version is not None
        and stored_pack is not None
        and stored_pack.version != pack_version
    ):
        # Governed content moves; the turn does not. A restored range read
        # from a later pack is still governed and still sourced, but it is
        # not necessarily the one this turn was answered beside, and saying
        # so costs one sentence.
        notes.append(
            f"The benchmark ranges below were re-read from pack version {pack_version}; "
            f"this turn ran on {stored_pack.version}. Governed ranges are keyed by "
            "metric id rather than stored with the turn, so they are the pack's "
            "current figures, not a snapshot of what was shown at the time."
        )
    return InvestigationResponse(
        # A monitor declaration this turn refused. Restored beside the
        # warnings that say the same thing in prose, because a refusal
        # renders where the confirmation would have gone and a client that
        # only had the sentence had nowhere to put it (round-7 FN-3).
        monitor_refused=monitor_refused,
        investigation_id=investigation.id,
        session_id=investigation.session_id,
        parent_id=investigation.parent_id,
        turn_id=investigation.turn_id,
        turn_class=investigation.turn_class.value,
        status=investigation.status.value,
        question=investigation.question,
        plan_hash=investigation.plan_hash,
        context_header=header,
        # Never defaulted to False beside a populated header: a client that
        # cannot tell restored context from live context will present one
        # as the other.
        context_header_restored=header is not None,
        watermark_id=watermark.id if watermark is not None else "",
        newest_data_date=watermark.newest_data_date if watermark is not None else None,
        restoration_notes=notes,
        # The prose this turn published, when the record kept it. Read, not
        # rebuilt: an empty stored narrative publishes ``None`` so the note
        # above and this field can never contradict each other.
        narrative=investigation.narrative or None,
        # Governed peer ranges, restored with the finding that cited them —
        # ``finding_payload`` keeps only the ones whose metric the finding
        # actually names, exactly as on the live turn.
        findings=[
            finding_payload(f, benchmarks, metric_display) for f in investigation.findings
        ],
        warnings=list(investigation.warnings),
        warnings_v2=structured_warnings(investigation.warnings),
        cohort=cohort,
        # Round-2 FN-5: the stored turn publishes the same governed display
        # block the live answer does. Without it a restored or exported
        # investigation carries titles the reader has no way to correct.
        metric_display=(
            metric_display.payloads_for(
                [ref.id for f in investigation.findings for ref in f.metric_refs]
            )
            if metric_display is not None
            else []
        ),
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
    specs = list(
        build_chart_specs(
            frames,
            recipes=components.recipes,
            playbook_id=playbook_id,
            row_referents=row_referents,
            suppression_threshold=components.catalog.suppression.threshold,
            # The plan is gone by restore time; the ordering it resolved was
            # recorded with it, so a re-opened turn's charts sort the way the
            # live ones did instead of falling back to frame order (R3-13).
            sorts=chart_sorts_from_trace(trace),
            # The window this turn ran over, rebuilt from its own stored spec
            # by the same builder the restored response publishes: a frame
            # with no dimension is charted against its PERIOD, and a restored
            # turn must name the same one the live answer did (FIX-8).
            windows=restored_context_header(investigation),
        )
    )
    # The same two contract checks the live turn ran (R4-09, R4-14): a
    # restored chart that keeps a collapsing key or an alphabetised aging
    # axis is a second, wrong rendering of the answer already published.
    policed, _ = _police_charts(specs, frames, components)
    return policed


def chart_sorts_from_trace(trace: TraceRecord | None) -> dict[str, tuple[str, bool]]:
    """``{frame id: (column, descending)}`` off a recorded plan context."""
    if trace is None:
        return {}
    raw = (trace.payload.get("plan_context") or {}).get("chart_sorts") or ()
    out: dict[str, tuple[str, bool]] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        frame_id, by = entry.get("frame_id"), entry.get("by")
        if isinstance(frame_id, str) and isinstance(by, str) and by:
            out[frame_id] = (by, bool(entry.get("descending", True)))
    return out


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


def _suppression_counts(outcome: TurnOutcome) -> tuple[int, int]:
    """(suppressed cells, cells the frame would have had) for this turn.

    Read off the frame that lost the most, because that is the frame the
    published figures came from. The probe knows this number; the warning
    never carried it, so the narrative could say "three payers were
    measured" on a turn where nine were computable and four were censored.
    """
    worst = 0
    visible = 0
    for _, frame in outcome.frames:
        if frame.suppressed_cells > worst:
            worst = frame.suppressed_cells
            visible = len(frame.rows)
    return worst, (visible + worst if worst else 0)


def _narrative_disclosures(
    outcome: TurnOutcome,
    warnings: Sequence[str],
    anomaly_reconciliation: AnomalyReconciliationPayload | None,
) -> tuple[list[str], list[str]]:
    """The sentences this turn's prose may not be published without.

    Composed from the SAME structured facts the response already carries —
    ``warnings_v2`` and the card/answer reconciliation strip — because the
    defect was never that the facts were missing (round-2 FN-3).
    """
    classified = [(w.code, w.message) for w in structured_warnings(warnings)]
    suppressed, total = _suppression_counts(outcome)
    sentence: str | None = None
    if anomaly_reconciliation is not None:
        sentence = reconciliation_disclosure(
            status=anomaly_reconciliation.status,
            card_cents=anomaly_reconciliation.card_impact_cents,
            answer_cents=anomaly_reconciliation.answer_impact_cents,
            delta_cents=anomaly_reconciliation.delta_cents,
            delta_fraction=anomaly_reconciliation.delta_fraction,
        )
    return mandatory_disclosures(
        classified,
        reconciliation_sentence=sentence,
        suppressed_cells=suppressed,
        total_cells=total,
    )


async def _compose_narrative(
    components: ApiComponents,
    outcome: TurnOutcome,
    findings: list[FindingPayload],
    warnings: list[str],
    on_event: OnEvent | None,
    spent: Decimal = Decimal(0),
    usage_out: list[LlmUsage] | None = None,
    anomaly_reconciliation: AnomalyReconciliationPayload | None = None,
    worklist: WorklistPayload | None = None,
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
    lead, trail = _narrative_disclosures(outcome, warnings, anomaly_reconciliation)
    disclosures = [*lead, *trail]
    # The ANSWER's own caution census — the same list the banners render
    # from — so nothing downstream has to infer "this turn has no caveats"
    # from the emptiness of a prompt slot (round-5 C-01). ``disclosures``
    # is the MANDATORY subset and is routinely empty on turns carrying
    # WINDOW_ASSUMED, ALTERNATE_BASIS_USED or POPULATION_CAVEAT.
    published_cautions = sum(
        1 for w in structured_warnings(warnings) if w.severity == "caution"
    )
    if not findings and worklist is not None and worklist.items:
        # The worklist IS the answer and its statement is already composed —
        # deterministically, from the build, naming the formula, the lanes,
        # the top card and the recoverable total (round-3 R3-09/R3-10). A
        # turn that carries it and no findings used to publish
        # ``narrative: null`` beside a fully-populated ranked list, so the
        # client wrote "No findings for this question" over the answer.
        # Nothing is generated here and no model is called.
        stated, _ = compose_narrative(lead, worklist.statement, trail)
        if on_event is not None:
            await on_event("narrative_delta", {"delta": stated})
        return stated
    if header is None or not findings:
        # A turn with nothing to publish is exactly the turn whose reader
        # most needs a sentence. ``narrative: null`` shipped beside
        # ``EMPTY_RESULT`` on four personas' turns and the client rendered
        # "No findings for this question" over a population that has one
        # (round-2 FN-3). No model call: the cause is already structured.
        stated = empty_narrative(
            [(w.code, w.message) for w in structured_warnings(warnings)]
        )
        if stated is not None and on_event is not None:
            await on_event("narrative_delta", {"delta": stated})
        return stated
    remaining = _narrative_budget(outcome, spent)
    if remaining is not None and remaining < _MIN_NARRATIVE_BUDGET_USD:
        # Never a silent omission: the evidence stands, the prose does not,
        # and the warning says which and why. The mandatory disclosures
        # still ship — they cost nothing and are the part a reader cannot
        # be left without.
        warnings.append(NARRATIVE_BUDGET_WARNING)
        return " ".join(disclosures) or None
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
    display_names = components.metric_display.names
    # When a card-to-drill strip is on this answer, THAT is the
    # reconciliation the reader compared — the lineage verdict is about
    # something else and reading it out as "reconciliation was not
    # performed" beside a divergence strip is the sentence the strip exists
    # to eliminate (round-2 FN-3).
    reconciliation_line = outcome.reconciliation
    if anomaly_reconciliation is not None:
        reconciliation_line = (
            f"{anomaly_reconciliation.summary} (card-to-answer). Investigation lineage: "
            f"{outcome.reconciliation or 'not applicable on this turn'}"
        )
    prompt = build_narrative_prompt(
        findings=findings,
        header=header,
        reconciliation=reconciliation_line,
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
        # Shown to the composer so it writes AROUND them rather than
        # against them — they are published either way.
        disclosures=disclosures,
        # …and how many caveats the ANSWER carries, so an empty slot above
        # can never be read as "this answer has no caveats" (round-5 C-01).
        published_cautions=published_cautions,
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
        return " ".join(disclosures) or None
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
        # Published verbatim above the composer's text, so if the composer
        # restates one it must validate rather than be cut.
        disclosures=disclosures,
        # On a turn that routed to the worklist, no prose instruction may
        # name a first action other than the ranked list's own rank 1
        # (R3-10): two orderings on one card, and only one was asked for.
        worklist_first_action=worklist_first_action(worklist),
        # The census the "no caveats" affirmation is derived from, and the
        # one a sentence claiming otherwise is redacted against.
        published_cautions=published_cautions,
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
        # findings, charts and grades stand untouched. The mandatory
        # disclosures are not the composer's sentences and survive.
        warnings.append(NARRATIVE_FULLY_REDACTED_WARNING)
        return " ".join(disclosures) or None
    # The refusal LEADS: a statement that the question was not answered
    # cannot sit beneath two paragraphs about what was found instead. The
    # bounding disclosures follow the prose, where a caveat belongs.
    narrative_text, _repeated = compose_narrative(lead, validation.text, trail)
    return narrative_text


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
    worklist: WorklistPayload | None = None,
    trace: TraceRecord | None = None,
) -> TurnAnswer | TurnClarification:
    """Map an engine outcome to the wire shape, emitting presentation
    events along the way (see module docstring for the ordering).

    ``worklist`` is the ranked anomaly worklist this turn routed to
    (:mod:`revi_api.worklist`), when it routed to one. It rides on the
    response and is disclosed in the turn's own warnings, so a reader is
    never shown a ranked card list without being told it came from the
    detection feed rather than from this turn's probes.

    ``trace`` is the turn's recorded decision trace when the caller has
    already read it (the worklist routing needs its plan context), so the
    same row is not fetched twice per turn."""
    if trace is None:
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
            # Only ever an explicitly requested one here: the platform
            # could not answer the question, which is not a reason to
            # withhold the list the caller asked for by name.
            worklist=worklist,
            watermark_stale=outcome.watermark_stale,
            usage=usage,
            debug=debug,
        )

    findings = [
        finding_payload(f, outcome.benchmarks, components.metric_display)
        for f in outcome.findings
    ]
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
            suppression_threshold=components.catalog.suppression.threshold,
            # The ordering the plan resolved (R3-13): the findings obey it
            # and the chart under them used to be drawn in frame order.
            sorts={
                frame_id: (by, descending)
                for frame_id, by, descending in outcome.chart_sorts
            },
            # The turn's own window, off the header it already published: a
            # frame with no dimension has no category to key marks by, and
            # its axis is the period rather than — as it was live — the
            # measured value itself (FIX-8).
            windows=outcome.header,
        )
    )
    # …and then the two contract checks a published chart must pass: its
    # rows must be uniquely addressable by the axes it declares (R4-09),
    # and an ordinal bucket axis must come off the wire in the order the
    # catalog declares for it rather than alphabetically (R4-14).
    chart_specs, chart_warnings = _police_charts(chart_specs, outcome.frames, components)
    # A chart title names a metric to a human, so it is a surface the
    # governed display name owns too (round-6 E-04). Copied only where the
    # rewrite changes something: most titles name no corrected id.
    display_names = components.metric_display.names
    chart_specs = tuple(
        spec
        if (titled := apply_metric_display(spec.title, display_names)) == spec.title
        else spec.model_copy(update={"title": titled})
        for spec in chart_specs
    )
    if on_event is not None:
        for spec in chart_specs:
            await on_event("chart_spec", spec.model_dump(mode="json"))

    warnings = [*outcome.warnings, *chart_warnings]
    if worklist is not None:
        # Disclosed as an ordinary warning, before the narrative is
        # composed: the prose is written against this turn's findings, and
        # a reader must be told that the ranked cards beside them are the
        # detection feed's work rather than this answer's evidence.
        warnings.append(worklist_warning(worklist))
        # …and when the worklist ROUTED, it does not sit beside the answer,
        # it IS the answer, and it leads (R3-10). Prepended rather than
        # appended so it is the first thing in the warning order the lead
        # disclosures are composed from.
        leads = worklist_lead_warning(worklist)
        if leads is not None:
            warnings.insert(0, leads)
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
        anomaly_reconciliation=anomaly_reconciliation,
        # No prose instruction may name a first action other than the ranked
        # worklist's own rank 1, on a turn that routed to it (R3-10).
        worklist=worklist,
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
    # Round-6 E-04. ``apply_metric_display`` had exactly one caller — the
    # finding card — so every OTHER surface that carries a finding's title
    # to a human carried the raw metric id instead: the referent chip (the
    # most-clicked control in the refinement loop) read "timely filing at
    # risk dollars: $22,426,000.28" beside a card reading "Unbilled open
    # inventory on a running filing clock: $22,426,000.28", and the meta
    # answer's label said the same thing a third way. One map, every seam.
    meta: MetaAnswerPayload | None = None
    if outcome.meta is not None:
        meta = MetaAnswerPayload(
            referent=outcome.meta.referent,
            label=apply_metric_display(outcome.meta.label, display_names),
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
            ReferentPayload(
                id=e.referent.value,
                kind=e.referent.kind.value,
                label=apply_metric_display(e.label, display_names),
            )
            for e in outcome.referents
        ],
        benchmarks=[benchmark_payload(b) for b in outcome.benchmarks],
        worklist=worklist,
        reconciliation=outcome.reconciliation,
        plan_hash=outcome.investigation.plan_hash,
        watermark_stale=outcome.watermark_stale,
        usage=usage,
        evidence=evidence,
        metric=metric,
        debug=debug,
    )
