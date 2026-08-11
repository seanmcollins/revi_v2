"""Everything a turn leaves behind, and everything it reads back."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from revi_investigation.application.anchoring import window_anchor
from revi_investigation.application.calculation_glue import (
    CalculationResult,
)
from revi_investigation.application.execution import (
    ExecutedProbe,
)
from revi_investigation.application.findings import (
    FindingsResult,
)
from revi_investigation.application.interpretation import (
    ClassificationOutcome,
    DefinitionalAnswer,
    InterpretedInvestigation,
)
from revi_investigation.application.ports import (
    TraceRecord,
    TurnEvent,
)
from revi_investigation.application.submit_turn.base import _SubmitTurnBase
from revi_investigation.application.submit_turn.presentation import (
    _chart_sorts_from_trace,
    _frame_windows_from_trace,
)
from revi_investigation.application.submit_turn.types import (
    _EVIDENCE_DEPTHS,
    _FALLBACK_WINDOW,
    _PlanContext,
    _predicate_label,
    _probe_kind,
    _probe_metrics,
    _TurnState,
)
from revi_investigation.application.validation import (
    ValidatedPlan,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    ContextPin,
    InvestigationContext,
)
from revi_investigation.domain.records import (
    Investigation,
    InvestigationStatus,
    Session,
)
from revi_investigation.domain.turns import (
    ClarificationRequest,
    TurnClass,
)
from revi_investigation_contracts.settings import EvidenceDepth
from revi_kernel.filters import (
    EMPTY_SCOPE,
    iter_predicates,
)
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import SERVICE, EntityGrain, Grain
from revi_kernel.scope import (
    ComparisonKind,
    derive_comparison,
    resolve_window,
)

#: How much of the answer on screen the reading prompts are shown. Enough
#: to resolve "the other one" and "that payer"; bounded so a twelve-row
#: ranking cannot crowd out the governed vocabulary beside it.
_CONVERSATION_FINDING_LINES = 6
_CONVERSATION_ROW_LINES = 12

#: How much of a closed dimension's value domain the operator prompt sees.
#: Enough to recognise a value the analyst names; bounded so a 20-value
#: CARC list does not crowd out the other dimensions beside it.
_DECLARED_VALUES_SHOWN = 12


class _TurnRecording(_SubmitTurnBase):
    """Frames, the trace record, turn events, and the context the later
    phases read back off a stored investigation."""

    async def _latest_investigation(
        self, session: Session, *, analytical: bool
    ) -> Investigation | None:
        lineage = await self._investigations.lineage(session.id)
        if lineage is None:
            return None
        candidates = [
            inv
            for inv in lineage.investigations
            if inv.status is InvestigationStatus.COMPLETE
            and (not analytical or inv.plan_hash is not None)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda inv: inv.created_at)

    async def _standing_context(self, session: Session) -> AnalysisSpec | None:
        """The context this conversation is standing in, if it has one.

        The window, filters, comparison and cohort of the answer on screen.
        Read off the session's latest ANALYTICAL answer and handed to the
        turn that continues it, so a follow-up that names no period inherits
        the one the conversation has been reading instead of falling back to
        the last full month.

        Deliberately without the lineage side effect ``_resumed_context``
        carries: an answer to a question this platform asked belongs under
        that question, and an ordinary follow-up does not.
        """
        parent = await self._latest_investigation(session, analytical=True)
        return None if parent is None else parent.spec

    async def _conversation_summary(self, session: Session) -> str:
        """What is on screen, as a block for the two reading prompts.

        ``classify_turn`` and ``interpret_question`` were composed from the
        utterance and the governed vocabulary and NOTHING about the
        conversation — the one exception being a pending clarification,
        i.e. the single piece of context that misleads. So "and June?" after
        a July answer, "what about Silverline?" after an Atlas Commercial
        scorecard and "what's ours?" after a clean-claim definition were all
        genuinely ambiguous *from inside the prompt*, and the model did the
        right thing with what it had: it asked.

        Everything here is a fact this platform published a moment ago —
        the analyst's own question, the ids the answer measured, the period
        it read, the cut it made and the handles it printed. Nothing is
        inferred and nothing is invented, which is what makes it safe to
        reason against.
        """
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            # A DEFINITION IS SOMETHING ON SCREEN. "What's a clean claim?"
            # then "what's ours?" came back asserting there was no prior
            # answer to anchor "ours" to — true of the analytical lineage
            # and false of the conversation, and the model said it because
            # this block told it so. It measured nothing, and that is the
            # fact to state; the term it answered about is the anchor.
            answered = await self._latest_investigation(session, analytical=False)
            if answered is None:
                return (
                    "Nothing has been answered in this conversation yet, so this utterance "
                    "stands on its own."
                )
            return (
                "THE ANSWER ON SCREEN — what this conversation has been about:\n"
                f"- the analyst's last question: {answered.question!r}\n"
                "- it was answered from your definitions library and measured nothing, so "
                "there are no figures on screen — but the term it explained is what a "
                "follow-up like 'what's ours?' is asking about."
            )
        spec = parent.spec
        context = spec.context
        window = context.window.range
        period = f"{window.start.isoformat()}..{window.end.isoformat()}"
        lines = [
            "THE ANSWER ON SCREEN — what this conversation has been about. These are facts",
            "this platform published a moment ago, not guesses:",
            f"- the analyst's last question: {parent.question!r}",
            f"- the period that answer read: {period}"
            + (
                f", which the analyst named as {spec.period_label!r}"
                if spec.period_label
                else " (no period was named; this was the platform's own choice)"
            ),
        ]
        measures = [ref.id for ref in spec.measures]
        if spec.subject_metric is not None and spec.subject_metric.id not in measures:
            measures.insert(0, spec.subject_metric.id)
        lines.append(
            "- what it measured: " + (", ".join(measures) if measures else "(a full review)")
        )
        if spec.subject_metric is not None:
            lines.append(f"- the metric it was ABOUT: {spec.subject_metric.id}")
        lines.append(
            "- broken out by: "
            + (", ".join(d.id for d in spec.dimensions) if spec.dimensions else "(nothing)")
        )
        filters = [_predicate_label(p) for p in iter_predicates(context.effective_scope())]
        lines.append("- narrowed to: " + ("; ".join(filters) if filters else "(nothing)"))
        if context.comparison is not None:
            comparison = context.comparison
            lines.append(
                f"- compared against: {comparison.kind.value} "
                f"({comparison.window.range.start.isoformat()}.."
                f"{comparison.window.range.end.isoformat()})"
            )
        if context.cohort is not None:
            lines.append(f"- pinned to a population of {context.cohort.size} claim(s)")
        published = [
            f"{finding.referent.value} — {' '.join(finding.title.split())}"
            for finding in parent.findings[:_CONVERSATION_FINDING_LINES]
        ]
        lines.append(
            "- the figures it published: " + ("; ".join(published) if published else "(none)")
        )
        entries = [
            entry
            for entry in await self._referents.list_for_session(session.id)
            if entry.investigation_id == parent.id and entry.dimension_value is not None
        ]
        rows = [
            f"{entry.referent.value} — {entry.dimension_value[1]} "
            f"({entry.dimension_value[0]})"
            for entry in entries[:_CONVERSATION_ROW_LINES]
            if entry.dimension_value is not None
        ]
        if rows:
            lines.append("- the rows it printed: " + "; ".join(rows))
        return "\n".join(lines)

    async def _plan_context_of(self, investigation_id: str) -> _PlanContext:
        """How the parent turn was planned, read back from its trace.

        The evidence depth belongs here for the same reason the playbook id
        does: the parent's plan is *rebuilt* to diff against, and rebuilding
        it under this turn's depth would produce a plan the parent never
        ran — every rescaled probe would read as unchanged.
        """
        traces = await self._traces.for_investigation(investigation_id)
        for record in traces:
            plan_context = record.payload.get("plan_context")
            if isinstance(plan_context, Mapping):
                playbook_id = plan_context.get("playbook_id")
                raw_depth = plan_context.get("evidence_depth")
                return _PlanContext(
                    playbook_id=playbook_id if isinstance(playbook_id, str) else None,
                    window_explicit=bool(plan_context.get("window_explicit", True)),
                    evidence_depth=(
                        EvidenceDepth(raw_depth)
                        if isinstance(raw_depth, str) and raw_depth in _EVIDENCE_DEPTHS
                        else EvidenceDepth.STANDARD
                    ),
                    chart_sorts=_chart_sorts_from_trace(plan_context.get("chart_sorts")),
                    frame_windows=_frame_windows_from_trace(
                        plan_context.get("frame_windows")
                    ),
                )
        return _PlanContext()

    async def _inherited_pins(self, session: Session) -> tuple[ContextPin, ...]:
        latest = await self._latest_investigation(session, analytical=False)
        return latest.spec.context.pins if latest is not None else ()

    async def _load_frames(
        self, investigation: Investigation
    ) -> tuple[tuple[str, EvidenceFrame], ...]:
        out: list[tuple[str, EvidenceFrame]] = []
        for key in investigation.frame_refs:
            frame = await self._frames.get(key)
            if frame is not None:
                _, _, frame_id = key.partition(":")
                out.append((frame_id or key, frame))
        return tuple(out)

    def _rebase_context(self, spec: AnalysisSpec, session: Session) -> AnalysisSpec:
        """Align the context to the session watermark after an epoch change:
        relative windows re-resolve against the new anchor; stored concrete
        dates otherwise stand (§6.1)."""
        context = spec.context
        if context.watermark.id == session.watermark.id:
            return spec
        window = context.window
        if window.requested is not None:
            window = resolve_window(
                window.requested,
                window_anchor(session.watermark, window.requested.mode),
                basis=window.basis,
                calendar=window.calendar,
            )
        new_context = replace(context, watermark=session.watermark, window=window)
        if (
            context.comparison is not None
            and context.comparison.kind is not ComparisonKind.CUSTOM
            and window != context.window
        ):
            new_context = replace(
                new_context, comparison=derive_comparison(window, context.comparison.kind)
            )
        return spec.with_context(new_context)

    def _context_lines(self, spec: AnalysisSpec) -> str:
        context = spec.context
        window = context.window
        lines = [
            f"- window: {window.range.start.isoformat()}..{window.range.end.isoformat()} "
            f"on the {window.basis.id} basis"
        ]
        if context.comparison is not None:
            comparison = context.comparison
            lines.append(
                f"- comparison: {comparison.kind.value} "
                f"({comparison.window.range.start.isoformat()}.."
                f"{comparison.window.range.end.isoformat()})"
            )
        filters = [_predicate_label(p) for p in iter_predicates(context.effective_scope())]
        lines.append("- filters: " + ("; ".join(filters) if filters else "(none)"))
        if context.cohort is not None:
            lines.append(f"- cohort: {context.cohort.id} ({context.cohort.size} claims)")
        lines.append(
            "- dimensions: "
            + (", ".join(d.id for d in spec.dimensions) if spec.dimensions else "(none)")
        )
        lines.append(
            "- measures: "
            + (", ".join(m.id for m in spec.measures) if spec.measures else "(playbook-driven)")
        )
        return "\n".join(lines)

    def _dimension_lines(self) -> str:
        """The cuts a follow-up may name — with the values a closed one holds.

        "Same for Silverline" reached the operator emitter as a proper noun
        with no home: nothing in the prompt said whether Silverline was a
        payer, a plan or a financial class, so the turn came back unable to
        compile a filter it had every id for. The catalog declares the
        closed domains and no prompt was ever shown them.
        """
        domains = {dim_id: (label, values) for dim_id, label, values in
                   self._validator.declared_domains()}
        seen: dict[str, None] = {}
        for metric_id, _ in self._pack.metric_summaries():
            contract = self._pack.metric(metric_id)
            if contract is None:
                continue
            for dim in contract.scope_dimensions:
                seen.setdefault(dim.id)
        lines: list[str] = []
        for dim_id in seen:
            declared = domains.get(dim_id)
            if declared is None:
                lines.append(f"- {dim_id}")
                continue
            label, values = declared
            shown = ", ".join(values[:_DECLARED_VALUES_SHOWN])
            more = "" if len(values) <= _DECLARED_VALUES_SHOWN else ", …"
            lines.append(f"- {dim_id} ({label}) — values: {shown}{more}")
        return "\n".join(lines) or "- (none)"

    def _metric_lines(self) -> str:
        return "\n".join(f"- {mid}" for mid, _ in self._pack.metric_summaries()) or "- (none)"

    def _fallback_spec(self, session: Session) -> AnalysisSpec:
        anchor = window_anchor(session.watermark, _FALLBACK_WINDOW.mode)
        window = resolve_window(_FALLBACK_WINDOW, anchor, basis=SERVICE)
        return AnalysisSpec(
            context=InvestigationContext(
                window=window,
                comparison=None,
                scope=EMPTY_SCOPE,
                cohort=None,
                grain=Grain(EntityGrain.CLAIM),
                watermark=session.watermark,
                pack_version=session.pack_version,
            ),
            measures=(),
        )

    def _minimal_investigation(
        self,
        session: Session,
        state: _TurnState,
        status: InvestigationStatus,
        classified: ClassificationOutcome | None,
    ) -> Investigation:
        """A persisted node for turns that never built a full spec."""
        turn_class = (
            classified.classification.turn_class
            if classified is not None and classified.classification is not None
            else TurnClass.REFINEMENT
        )
        return Investigation(
            id=state.investigation_id,
            session_id=session.id,
            # A follow-up clarification in a thread hangs off the one before
            # it, so a dialogue reads as a dialogue rather than as a row of
            # unrelated roots.
            parent_id=state.lineage_parent,
            turn_id=state.turn_id,
            turn_class=turn_class,
            question=state.question,
            spec=self._fallback_spec(session),
            plan_hash=None,
            status=status,
            findings=(),
            created_at=datetime.now(UTC),
        )

    async def _persist_frames(
        self, state: _TurnState, calculation: CalculationResult
    ) -> tuple[str, ...]:
        refs: list[str] = []
        for frame_id, frame in calculation.frames:
            key = f"{state.investigation_id}:{frame_id}"
            await self._frames.save(key, frame)
            refs.append(key)
        return tuple(refs)

    def _trace_record(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        *,
        interpreted: InterpretedInvestigation | None = None,
        validated: ValidatedPlan | None = None,
        executed: tuple[ExecutedProbe, ...] = (),
        calculation: CalculationResult | None = None,
        findings: FindingsResult | None = None,
        warnings: tuple[str, ...] = (),
        clarification: ClarificationRequest | None = None,
        definitional: DefinitionalAnswer | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> TraceRecord:
        """Assemble the §14 observability payload (JSON-serializable)."""
        classification_payload: dict[str, Any] | None = None
        if classified is not None and classified.classification is not None:
            classification_payload = {
                "turn_class": classified.classification.turn_class.value,
                "confidence": classified.classification.confidence,
            }
        interpreted_payload: dict[str, Any] | None = None
        if interpreted is not None:
            interpreted_payload = {
                "intent_summary": interpreted.intent_summary,
                "metric_ids": list(interpreted.metric_ids),
                "dimension_ids": list(interpreted.dimension_ids),
                "concept_ids": list(interpreted.concept_ids),
                "playbook_id": interpreted.playbook_id,
                "window": {
                    "start": interpreted.spec.context.window.range.start.isoformat(),
                    "end": interpreted.spec.context.window.range.end.isoformat(),
                    "basis": interpreted.spec.context.window.basis.id,
                },
            }
        # Executed probes carry what the plan alone cannot: how many rows
        # came back, whether the frame was truncated or suppressed, what
        # grade it earned, and how long it took. A debug view that reported
        # only the plan would answer "what did you intend to read?" while
        # the question is always "what did you actually read?".
        by_node = {item.node_id: item for item in executed}
        probes_payload = []
        if validated is not None:
            grades = dict(validated.grades)
            for node in validated.plan.nodes:
                item = by_node.get(node.id)
                probe = node.probe
                grade = grades.get(node.id)
                probes_payload.append(
                    {
                        "id": node.id,
                        "hash": node.hash,
                        "purpose": node.purpose,
                        "kind": _probe_kind(probe),
                        "metrics": _probe_metrics(
                            probe, item.frame if item is not None else None
                        ),
                        "cache_hit": item.cache_hit if item is not None else False,
                        "rows": len(item.frame.rows) if item is not None else None,
                        "limit": probe.limit if isinstance(probe, AggregationProbe) else None,
                        "truncated": item.frame.truncated if item is not None else False,
                        "suppressed_cells": (
                            item.frame.suppressed_cells if item is not None else 0
                        ),
                        "grade": grade.value if grade is not None else None,
                        "duration_ms": item.duration_ms if item is not None else 0,
                    }
                )
        payload: dict[str, Any] = {
            "tenant": session.tenant,
            "question": state.question,
            # The controls this turn ran under, recorded with the turn: a
            # trace that cannot say which model tier or evidence depth
            # produced it cannot explain the answer it belongs to.
            "settings": {
                "model_tier": state.settings.model_tier,
                "max_turn_cost_usd": (
                    str(state.settings.max_turn_cost_usd)
                    if state.settings.max_turn_cost_usd is not None
                    else None
                ),
                "narrative_depth": state.settings.narrative_depth.value,
                "evidence_depth": state.settings.evidence_depth.value,
                "debug": state.settings.debug,
            },
            "pack": {
                "id": session.pack_version.pack_id,
                "version": session.pack_version.version,
                "snapshot_id": self._pack.snapshot_id,
            },
            "watermark": {
                "id": session.watermark.id,
                "loaded_at": session.watermark.loaded_at.isoformat(),
                "newest_data_date": session.watermark.newest_data_date.isoformat(),
            },
            "watermark_stale": state.watermark_stale,
            "epoch": {
                "index": session.epochs[-1].index,
                "watermark": session.watermark.id,
                "re_anchored": state.epoch_transition,
            },
            "classification": classification_payload,
            "interpretation": interpreted_payload,
            "plan_hash": validated.plan.plan_hash if validated is not None else None,
            "probes": probes_payload,
            "operators": [
                {
                    "operator": op.operator,
                    "version": op.version,
                    "inputs": list(op.inputs),
                    "output": op.output,
                }
                for op in (calculation.operations if calculation is not None else ())
            ],
            "grades": (
                {node_id: grade.value for node_id, grade in validated.grades}
                if validated is not None
                else {}
            ),
            "findings": [
                finding.referent.value for finding in (findings.findings if findings else ())
            ],
            # Referent → grade: the derivation the answer's caveats rest on,
            # recorded beside the per-node grades it was taken from.
            "finding_grades": {
                finding.referent.value: finding.grade.value
                for finding in (findings.findings if findings else ())
            },
            "warnings": list(warnings),
            "clarification": clarification.question if clarification is not None else None,
            # The options the analyst was offered. Recorded because the NEXT
            # turn reads them back: a reply that repeats one verbatim is the
            # strongest signal a turn is an answer, and without them the
            # classifier was told nothing had been asked at all.
            "clarification_options": (
                list(clarification.options) if clarification is not None else []
            ),
            # …and what each of them MEANT. Held server-side, keyed by the
            # investigation that asked, so a reply naming an option is
            # resolved by lookup rather than re-interpreted.
            "clarification_bindings": (
                [
                    {
                        "option": b.option,
                        "kind": b.kind,
                        "metric_ids": list(b.metric_ids),
                        "dimension_ids": list(b.dimension_ids),
                        "playbook_id": b.playbook_id,
                        "scope": [
                            {"dimension": dimension, "values": list(values)}
                            for dimension, values in b.scope
                        ],
                        "basis": b.basis,
                    }
                    for b in clarification.bindings
                ]
                if clarification is not None
                else []
            ),
            # The reason carries the stage that stopped and, for a model
            # call, which kind of empty-handed it was — the half of a
            # clarification a debug reader is actually looking for.
            "clarification_reason": clarification.reason if clarification is not None else None,
            # The dialogue state this turn ran under, and what (if anything)
            # the engine decided on the analyst's behalf.
            "pending_clarification": (
                {
                    "question": state.pending.question,
                    "options": list(state.pending.options),
                    "original_question": state.pending.original_question,
                    "streak": state.pending.streak,
                }
                if state.pending is not None
                else None
            ),
            "assumptions": list(state.assumptions),
            "definitional": (
                {
                    "terms": [term.term for term in definitional.terms],
                    "pack_snapshot_id": definitional.pack_snapshot_id,
                }
                if definitional is not None
                else None
            ),
            "llm": [
                {
                    "template": template,
                    "model": usage.model,
                    "cost_usd": str(usage.cost_usd),
                    # Every prompt token, cached or not (see LlmUsage) —
                    # with the cached split beside it so a cost reader can
                    # tell a warm prompt from a cold one.
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "cache_creation_tokens": usage.cache_creation_tokens,
                    "schema_retries": usage.schema_retries,
                    # Transport attempts (1 = clean). Distinct from
                    # schema_retries: a retried connection and a retried
                    # schema are different diagnoses.
                    "attempts": usage.attempts,
                    "duration_ms": usage.duration_ms,
                    # Why the call produced nothing usable, when it did.
                    "failure": failure.value if failure is not None else None,
                }
                for template, usage, failure in state.llm_usages
            ],
            "template_hashes": dict(state.template_hashes),
            "timings_ms": dict(state.timings_ms),
        }
        if extra is not None:
            payload.update(dict(extra))
        return TraceRecord(
            trace_id=state.trace_id,
            session_id=session.id,
            investigation_id=state.investigation_id,
            turn_id=state.turn_id,
            created_at=datetime.now(UTC),
            payload=payload,
        )

    async def _stage(self, state: _TurnState, stage: str) -> None:
        await self._events.publish(
            TurnEvent(kind="stage", turn_id=state.turn_id, payload={"stage": stage})
        )

    async def _turn_complete(self, state: _TurnState, investigation: Investigation) -> None:
        await self._events.publish(
            TurnEvent(
                kind="turn_complete",
                turn_id=state.turn_id,
                payload={
                    "investigation_id": investigation.id,
                    "status": investigation.status.value,
                },
            )
        )
