"""First-turn orchestration (design §8.1) plus the DEFINITIONAL path.

``SubmitTurnService`` runs the compile path end to end: open/join session
(pinning the newest watermark and the pack version, epoch 0) → classify →
either the zero-probe DEFINITIONAL answer, a clarification, or the full
pipeline interpret → plan → §6.6 validation → cache-first execution →
deterministic calculation → findings + referents → typed
:class:`TurnOutcome` with the effective-context header — then persists the
Investigation, evidence frames, and the full §14 trace record, and
publishes ``turn_complete``.

Clarifications are successful outcomes: they cross this boundary as data
on the :class:`TurnOutcome`, never as exceptions. The refinement path
(follow-up turns) lands with the conversational-core milestone; a turn
classified as anything other than NEW_INVESTIGATION or DEFINITIONAL is
answered with an honest clarification here.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from revi_investigation.application.calculation_glue import (
    CalculateMetricsService,
    CalculationResult,
)
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.execution import ExecutedProbe, ExecuteInvestigationService
from revi_investigation.application.findings import EvaluateFindingsService, FindingsResult
from revi_investigation.application.interpretation import (
    ClassificationOutcome,
    ClassifyTurnService,
    DefinitionalAnswer,
    InterpretationOutcome,
    InterpretQuestionService,
)
from revi_investigation.application.planning import BuildInvestigationPlanService
from revi_investigation.application.ports import (
    FrameStore,
    InvestigationStore,
    LlmUsage,
    RegisteredReferent,
    SessionStore,
    TraceRecord,
    TraceStore,
    TurnEvent,
    TurnEventBus,
)
from revi_investigation.application.validation import PlanValidationService, ValidatedPlan
from revi_investigation.domain.context import (
    AnalysisSpec,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    InvestigationStatus,
    Session,
)
from revi_investigation.domain.turns import ClarificationRequest, TurnClass
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import DataLoadingError
from revi_kernel.filters import EMPTY_SCOPE, Predicate, iter_predicates
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import SERVICE, EntityGrain, Grain
from revi_kernel.scope import RangeMode, RelativeRange, TimeUnit, resolve_window
from revi_kernel.watermark import WatermarkEpoch

_FALLBACK_WINDOW = RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class SubmitTurnRequest:
    tenant: str
    question: str
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContextHeader:
    """The effective-context header attached to every answer (design §7.2)."""

    window_start: date
    window_end: date
    basis: str
    comparison_kind: str | None
    comparison_start: date | None
    comparison_end: date | None
    filters: tuple[str, ...]
    cohort: str | None
    watermark_id: str
    display: str


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """The typed result of one submitted turn."""

    session: Session
    investigation: Investigation
    findings: tuple[Finding, ...]
    header: ContextHeader | None
    frames: tuple[tuple[str, EvidenceFrame], ...]
    warnings: tuple[str, ...]
    clarification: ClarificationRequest | None
    definitional: DefinitionalAnswer | None
    trace_id: str
    referents: tuple[RegisteredReferent, ...] = ()


def build_context_header(spec: AnalysisSpec, session: Session) -> ContextHeader:
    context = spec.context
    window = context.window
    filters = tuple(
        _predicate_label(p) for p in iter_predicates(context.effective_scope())
    )
    comparison = context.comparison
    parts = [f"{window.range.start.isoformat()}..{window.range.end.isoformat()} ({window.basis.id})"]
    if comparison is not None:
        parts.append(
            f"vs {comparison.window.range.start.isoformat()}.."
            f"{comparison.window.range.end.isoformat()}"
        )
    if filters:
        parts.append("filters: " + "; ".join(filters))
    if context.cohort is not None:
        parts.append(f"cohort: {context.cohort.id}")
    parts.append(f"watermark {session.watermark.id}")
    return ContextHeader(
        window_start=window.range.start,
        window_end=window.range.end,
        basis=window.basis.id,
        comparison_kind=comparison.kind.value if comparison is not None else None,
        comparison_start=comparison.window.range.start if comparison is not None else None,
        comparison_end=comparison.window.range.end if comparison is not None else None,
        filters=filters,
        cohort=context.cohort.id if context.cohort is not None else None,
        watermark_id=session.watermark.id,
        display=" · ".join(parts),
    )


def _predicate_label(predicate: Predicate) -> str:
    values = ", ".join(str(v) for v in predicate.values)
    return f"{predicate.dimension.id} {predicate.op.value} [{values}]".strip()


class OpenSessionService:
    """Open or join a session; new sessions pin the newest completed
    watermark and the pack version at epoch 0 (design §8.1 step 2)."""

    def __init__(
        self, sessions: SessionStore, repository: AnalyticalRepository, pack: PackPort
    ) -> None:
        self._sessions = sessions
        self._repository = repository
        self._pack = pack

    async def open(self, *, tenant: str, session_id: str | None) -> Session:
        if session_id is not None:
            existing = await self._sessions.get(session_id)
            if existing is not None:
                return existing
        watermarks = await self._repository.list_watermarks()
        if not watermarks:
            raise DataLoadingError("no completed warehouse load is available yet")
        session = Session(
            id=session_id if session_id is not None else _new_id("sess"),
            tenant=tenant,
            pack_version=PackVersionRef(self._pack.pack_id, self._pack.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=watermarks[-1]),),
            created_at=datetime.now(UTC),
        )
        await self._sessions.save(session)
        return session


@dataclass
class _TurnState:
    """Mutable per-turn bookkeeping feeding the §14 trace."""

    turn_id: str
    investigation_id: str
    trace_id: str
    question: str
    started: float = field(default_factory=time.monotonic)
    timings_ms: dict[str, int] = field(default_factory=dict)
    llm_usages: list[tuple[str, LlmUsage]] = field(default_factory=list)
    template_hashes: dict[str, str] = field(default_factory=dict)

    def time_stage(self, stage: str) -> None:
        now = time.monotonic()
        self.timings_ms[stage] = int((now - self.started) * 1000)
        self.started = now


class SubmitTurnService:
    """§8.1 first-turn engine (plus DEFINITIONAL), on injected services."""

    def __init__(
        self,
        *,
        open_session: OpenSessionService,
        classifier: ClassifyTurnService,
        interpreter: InterpretQuestionService,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        executor: ExecuteInvestigationService,
        calculator: CalculateMetricsService,
        evaluator: EvaluateFindingsService,
        pack: PackPort,
        investigations: InvestigationStore,
        traces: TraceStore,
        frames: FrameStore,
        events: TurnEventBus,
    ) -> None:
        self._open_session = open_session
        self._classifier = classifier
        self._interpreter = interpreter
        self._planner = planner
        self._validator = validator
        self._executor = executor
        self._calculator = calculator
        self._evaluator = evaluator
        self._pack = pack
        self._investigations = investigations
        self._traces = traces
        self._frames = frames
        self._events = events

    # ----------------------------------------------------------------- api

    async def submit(self, request: SubmitTurnRequest) -> TurnOutcome:
        session = await self._open_session.open(
            tenant=request.tenant, session_id=request.session_id
        )
        state = _TurnState(
            turn_id=_new_id("turn"),
            investigation_id=_new_id("inv"),
            trace_id=_new_id("trace"),
            question=request.question,
        )

        await self._stage(state, "classify")
        classified = await self._classifier.classify(request.question)
        state.llm_usages.append(("classify_turn", classified.usage))
        state.template_hashes["classify_turn@v1"] = classified.template_hash
        state.time_stage("classify")

        if classified.clarification is not None or classified.classification is None:
            clarification = classified.clarification or ClarificationRequest(
                question="Could you rephrase that?", reason="unclassifiable turn"
            )
            return await self._clarification_outcome(session, state, classified, clarification)

        turn_class = classified.classification.turn_class
        if turn_class is TurnClass.DEFINITIONAL:
            return await self._definitional_outcome(session, state, classified)
        if turn_class is not TurnClass.NEW_INVESTIGATION:
            clarification = ClarificationRequest(
                question=(
                    "That reads like a follow-up, but this session has no prior answer to "
                    "refine yet — what would you like to investigate?"
                ),
                reason=f"turn class {turn_class.value} is not supported on a first turn",
            )
            return await self._clarification_outcome(session, state, classified, clarification)

        await self._stage(state, "interpret")
        interpretation = await self._interpreter.interpret(
            request.question, session=session, turn_id=state.turn_id
        )
        state.llm_usages.append(("interpret_question", interpretation.usage))
        state.template_hashes["interpret_question@v1"] = interpretation.template_hash
        state.time_stage("interpret")

        if interpretation.clarification is not None:
            return await self._clarification_outcome(
                session, state, classified, interpretation.clarification, interpretation
            )
        if interpretation.definitional is not None:
            return await self._definitional_outcome(
                session, state, classified, answer=interpretation.definitional
            )
        assert interpretation.investigation is not None
        interpreted = interpretation.investigation

        await self._stage(state, "plan")
        plan = self._planner.build(
            interpreted.spec,
            playbook_id=interpreted.playbook_id,
            window_explicit=interpreted.window_explicit,
        )
        state.time_stage("plan")

        await self._stage(state, "validate")
        validated = self._validator.validate(plan, interpreted.spec)
        state.time_stage("validate")

        executed = await self._executor.execute(
            validated.plan,
            watermark=session.watermark,
            pack_snapshot_id=self._pack.snapshot_id,
            turn_id=state.turn_id,
        )
        state.time_stage("execute")

        await self._stage(state, "calculate")
        calculation = self._calculator.calculate(validated.plan, executed)
        state.time_stage("calculate")

        await self._stage(state, "findings")
        playbook = (
            self._pack.playbook(interpreted.playbook_id)
            if interpreted.playbook_id is not None
            else None
        )
        findings_result = await self._evaluator.evaluate(
            plan=validated.plan,
            calculation=calculation,
            spec=interpreted.spec,
            pack=self._pack,
            playbook=playbook,
            session_id=session.id,
            investigation_id=state.investigation_id,
        )
        state.time_stage("findings")

        warnings = (*validated.warnings, *validated.plan.notes)
        header = build_context_header(interpreted.spec, session)
        frame_refs = await self._persist_frames(state, calculation)
        investigation = Investigation(
            id=state.investigation_id,
            session_id=session.id,
            parent_id=None,
            turn_id=state.turn_id,
            turn_class=turn_class,
            question=request.question,
            spec=interpreted.spec,
            plan_hash=validated.plan.plan_hash,
            status=InvestigationStatus.COMPLETE,
            findings=findings_result.findings,
            created_at=datetime.now(UTC),
            frame_refs=frame_refs,
            warnings=warnings,
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                interpretation=interpretation,
                validated=validated,
                executed=executed,
                calculation=calculation,
                findings=findings_result,
                warnings=warnings,
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=findings_result.findings,
            header=header,
            frames=calculation.frames,
            warnings=warnings,
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            referents=findings_result.referents,
        )

    # ------------------------------------------------------ outcome shapes

    async def _clarification_outcome(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome,
        clarification: ClarificationRequest,
        interpretation: InterpretationOutcome | None = None,
    ) -> TurnOutcome:
        investigation = self._minimal_investigation(
            session, state, InvestigationStatus.CLARIFICATION_REQUIRED, classified
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                interpretation=interpretation,
                clarification=clarification,
            )
        )
        await self._events.publish(
            TurnEvent(
                kind="clarification",
                turn_id=state.turn_id,
                payload={"question": clarification.question, "reason": clarification.reason},
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=None,
            frames=(),
            warnings=(),
            clarification=clarification,
            definitional=None,
            trace_id=state.trace_id,
        )

    async def _definitional_outcome(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome,
        answer: DefinitionalAnswer | None = None,
    ) -> TurnOutcome:
        # ZERO probes by construction: the executor is never invoked on this
        # path (asserted by the zero-probe tests with a spy repository).
        if answer is None:
            answer = self._interpreter.definitional_answer(state.question)
        state.time_stage("definitional")
        if not answer.terms:
            clarification = ClarificationRequest(
                question=(
                    "I couldn't find a governed definition for that term — could you name "
                    "the code, concept, or metric another way?"
                ),
                reason="no pack content matched the definitional lookup",
            )
            return await self._clarification_outcome(session, state, classified, clarification)
        investigation = self._minimal_investigation(
            session, state, InvestigationStatus.COMPLETE, classified
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            self._trace_record(session, state, classified, definitional=answer)
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=None,
            frames=(),
            warnings=(),
            clarification=None,
            definitional=answer,
            trace_id=state.trace_id,
        )

    def _minimal_investigation(
        self,
        session: Session,
        state: _TurnState,
        status: InvestigationStatus,
        classified: ClassificationOutcome,
    ) -> Investigation:
        """A persisted node for turns that never built a full spec: the
        context is the empty default over the session pins."""
        anchor = session.watermark.loaded_at.date()
        window = resolve_window(_FALLBACK_WINDOW, anchor, basis=SERVICE)
        spec = AnalysisSpec(
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
        turn_class = (
            classified.classification.turn_class
            if classified.classification is not None
            else TurnClass.NEW_INVESTIGATION
        )
        return Investigation(
            id=state.investigation_id,
            session_id=session.id,
            parent_id=None,
            turn_id=state.turn_id,
            turn_class=turn_class,
            question=state.question,
            spec=spec,
            plan_hash=None,
            status=status,
            findings=(),
            created_at=datetime.now(UTC),
        )

    # ---------------------------------------------------------- persistence

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
        classified: ClassificationOutcome,
        *,
        interpretation: InterpretationOutcome | None = None,
        validated: ValidatedPlan | None = None,
        executed: tuple[ExecutedProbe, ...] = (),
        calculation: CalculationResult | None = None,
        findings: FindingsResult | None = None,
        warnings: tuple[str, ...] = (),
        clarification: ClarificationRequest | None = None,
        definitional: DefinitionalAnswer | None = None,
    ) -> TraceRecord:
        """Assemble the §14 observability payload (JSON-serializable)."""
        classification_payload: dict[str, Any] | None = None
        if classified.classification is not None:
            classification_payload = {
                "turn_class": classified.classification.turn_class.value,
                "confidence": classified.classification.confidence,
            }
        interpreted_payload: dict[str, Any] | None = None
        if interpretation is not None and interpretation.investigation is not None:
            interpreted = interpretation.investigation
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
        cache_hits = {item.node_id: item.cache_hit for item in executed}
        probes_payload = []
        if validated is not None:
            for node in validated.plan.nodes:
                probes_payload.append(
                    {
                        "id": node.id,
                        "hash": node.hash,
                        "purpose": node.purpose,
                        "cache_hit": cache_hits.get(node.id, False),
                    }
                )
        payload: dict[str, Any] = {
            "tenant": session.tenant,
            "question": state.question,
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
            "warnings": list(warnings),
            "clarification": clarification.question if clarification is not None else None,
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
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "schema_retries": usage.schema_retries,
                    "duration_ms": usage.duration_ms,
                }
                for template, usage in state.llm_usages
            ],
            "template_hashes": dict(state.template_hashes),
            "timings_ms": dict(state.timings_ms),
        }
        return TraceRecord(
            trace_id=state.trace_id,
            session_id=session.id,
            investigation_id=state.investigation_id,
            turn_id=state.turn_id,
            created_at=datetime.now(UTC),
            payload=payload,
        )

    # --------------------------------------------------------------- events

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
