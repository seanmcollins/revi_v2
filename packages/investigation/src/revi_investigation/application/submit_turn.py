"""Turn orchestration: the §8.1 compile path, the §8.2 refinement path, and
the §8.3 zero-probe paths, over one explicit typed context.

Turn dispatch (after the session watermark check):

- **Typed first turn** — a request carrying a ``TypedInvestigationSpec``
  is a NEW_INVESTIGATION by construction and needs no parent: the spec
  states the metrics, dimensions, scope and window outright, so
  classification and interpretation are simply *known*, not guessed
  (zero model calls). Everything after the spec is built is the ordinary
  pipeline, §6.6 validation included. This is the anchor typed
  refinements always needed: a portfolio card's drill handle, or a chart
  click in a session with no prior answer, opens an investigation
  instead of returning "nothing to refine yet".
- **Typed gesture** — a request carrying refinement DTOs skips NL entirely
  and enters the refinement pipeline at the operator converter. Unlike
  the typed first turn, this one *edits* the session's latest
  investigation and therefore does require a parent.
- **NEW_INVESTIGATION** — classify → interpret → plan → §6.6 validation →
  cache-first execution → deterministic calculation → findings/referents.
- **DEFINITIONAL** — governed pack content with provenance; zero probes.
- **REFINEMENT** — resolve referents against the live registry → emit
  operators from the closed set → convert → ``apply_refinements`` (context
  conflicts surface *before* execution as clarification outcomes, never
  500s) → DrillInto targets pin ONE cohort at the session watermark →
  replan → plan diff vs the deterministically rebuilt parent plan →
  cache-first execution (unchanged probes never touch the warehouse) →
  auto-reconciliation against the parent totals on splits/drills
  (RECONCILIATION_FAILED is a surfaced warning + event, never silent,
  never fatal) → child Investigation + RefinementEdge.
- **PRESENTATION_ONLY / META / CONTEXT_CONTROL / kernel-only refinements**
  — answered from persisted frames, traces, and the context object with
  ZERO repository calls (spy-asserted per §18.1-14).

Watermark epochs (§7.1): every turn compares the session pin against the
newest completed load; staleness is surfaced (``watermark_stale``, a
warning event) and the analyst chooses — ``re_anchor=True`` starts a new
epoch, re-resolves relative windows against the new anchor, and records
the transition in the trace. Pinned continuation stays byte-stable.

Clarifications are successful outcomes: they cross this boundary as data
on the :class:`TurnOutcome`, never as exceptions.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from revi_investigation.application.calculation_glue import (
    CalculateMetricsService,
    CalculationResult,
)
from revi_investigation.application.capability_ports import BenchmarkSpec, PackPort, TransformPort
from revi_investigation.application.cohorts import PinCohortService
from revi_investigation.application.comparison import window_mismatch_warning
from revi_investigation.application.execution import ExecutedProbe, ExecuteInvestigationService
from revi_investigation.application.findings import (
    EvaluateFindingsService,
    FindingsResult,
    find_primary_compare,
)
from revi_investigation.application.interpretation import (
    ClassificationOutcome,
    ClassifyTurnService,
    DefinitionalAnswer,
    InterpretationOutcome,
    InterpretedInvestigation,
    InterpretQuestionService,
)
from revi_investigation.application.llm.schemas import AnyRefinementOperator
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    DiffPlanService,
    InvestigationPlan,
    PlanDiff,
)
from revi_investigation.application.ports import (
    FrameStore,
    InvestigationStore,
    LlmUsage,
    ReferentRegistryStore,
    RegisteredReferent,
    SessionStore,
    TraceRecord,
    TraceStore,
    TurnEvent,
    TurnEventBus,
)
from revi_investigation.application.refinement_llm import (
    EmitRefinementsService,
    ReferentResolution,
    ResolveReferentsService,
    to_domain_operators,
)
from revi_investigation.application.validation import PlanValidationService, ValidatedPlan
from revi_investigation.domain.context import (
    AnalysisSpec,
    ContextPin,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    InvestigationStatus,
    RefinementEdge,
    Session,
)
from revi_investigation.domain.refinements import (
    AddFilter,
    DrillInto,
    Expand,
    RankBy,
    Refinement,
    SetDimensions,
    apply_refinements,
    detect_conflict,
)
from revi_investigation.domain.turns import ClarificationRequest, TurnClass
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.header import ContextHeaderPayload, build_header_payload
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.cohort import CohortRef
from revi_kernel.errors import ContextConflictError, DataLoadingError, ReferentNotFoundError
from revi_kernel.filters import EMPTY_SCOPE, Predicate, iter_predicates
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import SERVICE, DimensionRef, EntityGrain, Grain, ReferentId
from revi_kernel.scope import (
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    derive_comparison,
    resolve_window,
)
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

_FALLBACK_WINDOW = RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS)


def _not_applicable(reason: str) -> str:
    """The third reconciliation state: checked nothing, and says why.

    ``null`` used to mean both "not checked" and, indistinguishably from
    the outside, "nothing to say" — the one place in a product built on
    "never claim more than the evidence supports" where silence was
    ambiguous."""
    return f"status=not_applicable; reason={reason}"


_KERNEL_ONLY = (RankBy, Expand)
_REFERENT_TOKEN = re.compile(r"\b([FD]\d+)\b", re.IGNORECASE)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class SubmitTurnRequest:
    tenant: str
    question: str
    session_id: str | None = None
    # Typed FIRST turn (§8.1): an explicit investigation spec, no parent.
    spec: TypedInvestigationSpec | None = None
    # Typed-gesture path (§12): validated refinement DTOs skip NL entirely.
    refinements: tuple[AnyRefinementOperator, ...] | None = None
    # Watermark epochs (§7.1): opt into re-anchoring on a newer load.
    re_anchor: bool = False


# The canonical header shape and its builder live in the contracts package
# (single source of truth for API payloads, traces, and outcomes — §7.2).
ContextHeader = ContextHeaderPayload


@dataclass(frozen=True, slots=True)
class MetaAnswer:
    """A META turn's answer: recorded provenance behind a referent (§8.3)."""

    referent: str
    label: str
    investigation_id: str
    probes: tuple[Mapping[str, Any], ...]
    operators: tuple[Mapping[str, Any], ...]
    grades: Mapping[str, str]
    reconciliation: str | None
    finding_values: tuple[tuple[str, Any], ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """The typed result of one submitted turn."""

    session: Session
    investigation: Investigation
    findings: tuple[Finding, ...]
    header: ContextHeaderPayload | None
    frames: tuple[tuple[str, EvidenceFrame], ...]
    warnings: tuple[str, ...]
    clarification: ClarificationRequest | None
    definitional: DefinitionalAnswer | None
    trace_id: str
    referents: tuple[RegisteredReferent, ...] = ()
    watermark_stale: bool = False
    meta: MetaAnswer | None = None
    reconciliation: str | None = None
    diff: PlanDiff | None = None
    #: Governed benchmark ranges for the metrics this turn's findings cite
    #: (design §9.1). Authored, sourced and cohort-labelled since KB wave 1
    #: and unreachable until now: the engine had no field to carry them and
    #: the API passed a literal empty tuple to the narrative composer.
    benchmarks: tuple[BenchmarkSpec, ...] = ()


def build_context_header(spec: AnalysisSpec, session: Session) -> ContextHeaderPayload:
    """Delegate to the canonical contracts builder (§7.2 single source)."""
    context = spec.context
    return build_header_payload(
        window=context.window,
        comparison=context.comparison,
        predicates=tuple(iter_predicates(context.scope)),
        pinned_predicates=tuple(pin.predicate for pin in context.pins),
        cohort=context.cohort,
        watermark_id=session.watermark.id,
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
        newest = await self.newest_watermark()
        session = Session(
            id=session_id if session_id is not None else _new_id("sess"),
            tenant=tenant,
            pack_version=PackVersionRef(self._pack.pack_id, self._pack.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=newest),),
            created_at=datetime.now(UTC),
        )
        await self._sessions.save(session)
        return session

    async def newest_watermark(self) -> DataWatermark:
        watermarks = await self._repository.list_watermarks()
        if not watermarks:
            raise DataLoadingError("no completed warehouse load is available yet")
        return watermarks[-1]

    async def re_anchor(self, session: Session, newest: DataWatermark, turn_id: str) -> Session:
        """Start a new watermark epoch (§7.1) — an explicit, recorded event."""
        updated = session.with_new_epoch(
            WatermarkEpoch(index=len(session.epochs), watermark=newest, started_at_turn=turn_id)
        )
        await self._sessions.save(updated)
        return updated


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
    watermark_stale: bool = False
    epoch_transition: bool = False

    def time_stage(self, stage: str) -> None:
        now = time.monotonic()
        self.timings_ms[stage] = int((now - self.started) * 1000)
        self.started = now


class SubmitTurnService:
    """§8 turn engine on injected services."""

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
        referent_resolver: ResolveReferentsService,
        refinement_emitter: EmitRefinementsService,
        cohort_pinner: PinCohortService,
        differ: DiffPlanService,
        transforms: TransformPort,
        pack: PackPort,
        referents: ReferentRegistryStore,
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
        self._referent_resolver = referent_resolver
        self._refinement_emitter = refinement_emitter
        self._cohort_pinner = cohort_pinner
        self._differ = differ
        self._transforms = transforms
        self._pack = pack
        self._referents = referents
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
        session = await self._check_watermark(session, state, request)

        if request.spec is not None:
            # typed FIRST turn: an explicit investigation, never a
            # refinement — no NL, no classification, no LLM
            return await self._typed_investigation_turn(session, state, request.spec)

        if request.refinements is not None:
            # typed-gesture path: no NL, no classification, no LLM
            return await self._refinement_turn(
                session, state, None, dto_ops=tuple(request.refinements)
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
        if turn_class is TurnClass.NEW_INVESTIGATION:
            return await self._new_investigation_turn(session, state, classified)
        if turn_class is TurnClass.REFINEMENT:
            return await self._refinement_turn(session, state, classified, dto_ops=None)
        if turn_class is TurnClass.PRESENTATION_ONLY:
            return await self._presentation_turn(session, state, classified)
        if turn_class is TurnClass.META:
            return await self._meta_turn(session, state, classified)
        if turn_class is TurnClass.CONTEXT_CONTROL:
            return await self._context_control_turn(session, state, classified)
        clarification = ClarificationRequest(
            question=(
                "That reads like an answer to a question I haven't asked — what would "
                "you like to investigate?"
            ),
            reason=f"turn class {turn_class.value} is not actionable here",
        )
        return await self._clarification_outcome(session, state, classified, clarification)

    # ------------------------------------------------------ watermark epochs

    async def _check_watermark(
        self, session: Session, state: _TurnState, request: SubmitTurnRequest
    ) -> Session:
        newest = await self._open_session.newest_watermark()
        if newest.id == session.watermark.id:
            return session
        if request.re_anchor:
            session = await self._open_session.re_anchor(session, newest, state.turn_id)
            state.epoch_transition = True
            return session
        state.watermark_stale = True
        await self._events.publish(
            TurnEvent(
                kind="warning",
                turn_id=state.turn_id,
                payload={
                    "code": "WATERMARK_STALE",
                    "pinned": session.watermark.id,
                    "newest": newest.id,
                },
            )
        )
        return session

    # ----------------------------------------------------- new investigation

    async def _new_investigation_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        await self._stage(state, "interpret")
        interpretation = await self._interpreter.interpret(
            state.question, session=session, turn_id=state.turn_id
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

        # carryover law 5: session pins persist until explicitly cleared
        pins = await self._inherited_pins(session)
        spec = interpreted.spec
        if pins:
            spec = spec.with_context(replace(spec.context, pins=pins))

        return await self._run_analysis(
            session,
            state,
            classified,
            spec=spec,
            playbook_id=interpreted.playbook_id,
            window_explicit=interpreted.window_explicit,
            turn_class=TurnClass.NEW_INVESTIGATION,
            parent=None,
            operators=(),
            interpreted=interpreted,
        )

    # -------------------------------------------------- typed first turn

    async def _typed_investigation_turn(
        self, session: Session, state: _TurnState, typed: TypedInvestigationSpec
    ) -> TurnOutcome:
        """A NEW_INVESTIGATION stated in the typed vocabulary (§8.1).

        The twin of the interpreted first turn with the probabilistic
        stage removed: the caller supplies what the model would have
        proposed, ``from_typed_spec`` disposes it against the pack and
        catalog, and the *identical* planning → §6.6 validation →
        cache-first execution → calculation → findings pipeline runs. Zero
        model calls, and no parent required — which is what lets a
        portfolio card, or a chart click in a fresh session, become an
        investigation instead of a clarification.
        """
        await self._stage(state, "interpret")
        interpreted = self._interpreter.from_typed_spec(
            typed, session=session, turn_id=state.turn_id
        )
        state.time_stage("interpret")

        # carryover law 5: session pins persist until explicitly cleared
        spec = interpreted.spec
        pins = await self._inherited_pins(session)
        if pins:
            spec = spec.with_context(replace(spec.context, pins=pins))

        return await self._run_analysis(
            session,
            state,
            None,
            spec=spec,
            playbook_id=None,
            window_explicit=True,
            turn_class=TurnClass.NEW_INVESTIGATION,
            parent=None,
            operators=(),
            interpreted=interpreted,
            trace_extra={"typed_spec": typed.model_dump(mode="json")},
        )

    # ------------------------------------------------------------ refinement

    async def _refinement_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        *,
        dto_ops: tuple[AnyRefinementOperator, ...] | None,
    ) -> TurnOutcome:
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question=(
                        "There's no prior answer in this session to refine yet — what "
                        "would you like to investigate?"
                    ),
                    reason="refinement without a parent investigation",
                ),
            )
        playbook_id, window_explicit = await self._plan_context_of(parent.id)
        entries = await self._referents.list_for_session(session.id)
        resolutions: tuple[ReferentResolution, ...] = ()
        rationale = ""

        if dto_ops is None:
            await self._stage(state, "resolve_referents")
            resolved = await self._referent_resolver.resolve(state.question, entries)
            state.llm_usages.append(("resolve_referents", resolved.usage))
            state.template_hashes["resolve_referents@v1"] = resolved.template_hash
            state.time_stage("resolve_referents")
            if resolved.clarification is not None:
                return await self._clarification_outcome(
                    session, state, classified, resolved.clarification
                )
            resolutions = resolved.resolutions

            await self._stage(state, "emit_refinements")
            emission = await self._refinement_emitter.emit(
                state.question,
                context_summary=self._context_lines(parent.spec),
                entries=entries,
                resolutions=resolutions,
                dimension_lines=self._dimension_lines(),
                metric_lines=self._metric_lines(),
            )
            state.llm_usages.append(("emit_refinements", emission.usage))
            state.template_hashes["emit_refinements@v1"] = emission.template_hash
            state.time_stage("emit_refinements")
            if emission.clarification is not None or emission.operators is None:
                clarification = emission.clarification or ClarificationRequest(
                    question="What would you like to change?", reason="AMBIGUOUS_REFINEMENT"
                )
                return await self._clarification_outcome(session, state, classified, clarification)
            dto_ops = emission.operators
            rationale = emission.rationale

        registry_index = {entry.referent.value: entry.referent for entry in entries}
        domain_ops = to_domain_operators(dto_ops, registry_index)
        ops_json = tuple(op.model_dump(mode="json") for op in dto_ops)

        prelude_warnings: list[str] = []
        cohort = await self._pin_drill_cohort(session, parent.spec, domain_ops, prelude_warnings)

        def resolve_cohort(_: ReferentId) -> CohortRef | None:
            return cohort

        try:
            new_spec = apply_refinements(
                parent.spec,
                domain_ops,
                turn_id=state.turn_id,
                resolve_cohort=resolve_cohort if cohort is not None else None,
            )
        except ContextConflictError as conflict:
            # detected BEFORE execution (§7.7 law 4); a conversational
            # outcome, never a server error
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question=(
                        f"That contradicts the current context: {conflict.message}. "
                        "Widen the context first (remove the filter or reset), or "
                        "rephrase what you want."
                    ),
                    reason=f"CONTEXT_CONFLICT: {conflict.message}",
                ),
                extra={"refinement": {"operators": list(ops_json), "rationale": rationale}},
            )
        new_spec = self._rebase_context(new_spec, session)

        if domain_ops and all(isinstance(op, _KERNEL_ONLY) for op in domain_ops):
            kernel_outcome = await self._kernel_only_turn(
                session, state, classified, parent, new_spec, domain_ops, ops_json
            )
            if kernel_outcome is not None:
                return kernel_outcome

        refinement_extra: dict[str, Any] = {
            "operators": list(ops_json),
            "rationale": rationale,
            "resolutions": [
                {"mention": r.mention, "referent": r.referent.value, "confidence": r.confidence}
                for r in resolutions
            ],
        }
        if cohort is not None:
            refinement_extra["cohort"] = {"id": cohort.id, "size": cohort.size}
        return await self._run_analysis(
            session,
            state,
            classified,
            spec=new_spec,
            playbook_id=playbook_id,
            window_explicit=window_explicit,
            turn_class=TurnClass.REFINEMENT,
            parent=parent,
            operators=domain_ops,
            refinement_extra=refinement_extra,
            prelude_warnings=tuple(prelude_warnings),
        )

    async def _pin_drill_cohort(
        self,
        session: Session,
        parent_spec: AnalysisSpec,
        domain_ops: tuple[Refinement, ...],
        warnings: list[str],
    ) -> CohortRef | None:
        targets = tuple(
            dict.fromkeys(op.target for op in domain_ops if isinstance(op, DrillInto))
        )
        if not targets:
            return None
        return await self._cohort_pinner.pin(
            session=session, parent_spec=parent_spec, targets=targets, warnings=warnings
        )

    # ------------------------------------------------ shared analysis runner

    async def _run_analysis(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        *,
        spec: AnalysisSpec,
        playbook_id: str | None,
        window_explicit: bool,
        turn_class: TurnClass,
        parent: Investigation | None,
        operators: tuple[Refinement, ...],
        interpreted: InterpretedInvestigation | None = None,
        refinement_extra: Mapping[str, Any] | None = None,
        trace_extra: Mapping[str, Any] | None = None,
        prelude_warnings: tuple[str, ...] = (),
    ) -> TurnOutcome:
        effective_playbook = playbook_id if not spec.measures else None

        await self._stage(state, "plan")
        plan = self._planner.build(
            spec, playbook_id=effective_playbook, window_explicit=window_explicit
        )
        diff: PlanDiff | None = None
        if parent is not None:
            parent_playbook = playbook_id if not parent.spec.measures else None
            parent_plan = self._planner.build(
                parent.spec, playbook_id=parent_playbook, window_explicit=window_explicit
            )
            diff = self._differ.diff(parent_plan, plan)
        state.time_stage("plan")

        await self._stage(state, "validate")
        validated = self._validator.validate(plan, spec)
        state.time_stage("validate")

        # the longest stage of the pipeline; it published no progress frame
        # until now, so a streaming client watched the rail stall on
        # "validate" for the whole warehouse round trip
        await self._stage(state, "execute")
        executed = await self._executor.execute(
            validated.plan,
            watermark=session.watermark,
            pack_snapshot_id=self._pack.snapshot_id,
            turn_id=state.turn_id,
            grades=dict(validated.grades),
        )
        state.time_stage("execute")

        await self._stage(state, "calculate")
        calculation = self._calculator.calculate(validated.plan, executed)
        state.time_stage("calculate")

        await self._stage(state, "findings")
        playbook = (
            self._pack.playbook(effective_playbook) if effective_playbook is not None else None
        )
        findings_result = await self._evaluator.evaluate(
            plan=validated.plan,
            calculation=calculation,
            spec=spec,
            pack=self._pack,
            playbook=playbook,
            session_id=session.id,
            investigation_id=state.investigation_id,
        )
        state.time_stage("findings")

        extra_warnings: list[str] = list(prelude_warnings)
        # A comparison against a different-length window is answerable but
        # not a delta anyone should act on; the warning rides with the
        # answer and the findings withhold impact (see comparison.py).
        mismatch = window_mismatch_warning(spec)
        if mismatch is not None:
            extra_warnings.append(mismatch)
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "COMPARISON_WINDOW_MISMATCH", "detail": mismatch},
                )
            )
        reconciliation = (
            await self._reconcile_with_parent(
                parent, validated.plan, calculation, operators, extra_warnings, state
            )
            if parent is not None
            else _not_applicable("this is a first turn; there is no parent answer to reconcile to")
        )

        warnings = (*validated.warnings, *validated.plan.notes, *extra_warnings)
        benchmarks = self._benchmarks_for(findings_result)
        header = build_context_header(spec, session)
        frame_refs = await self._persist_frames(state, calculation)
        investigation = Investigation(
            id=state.investigation_id,
            session_id=session.id,
            parent_id=parent.id if parent is not None else None,
            turn_id=state.turn_id,
            turn_class=turn_class,
            question=state.question,
            spec=spec,
            plan_hash=validated.plan.plan_hash,
            status=InvestigationStatus.COMPLETE,
            findings=findings_result.findings,
            created_at=datetime.now(UTC),
            frame_refs=frame_refs,
            warnings=warnings,
        )
        edge = (
            RefinementEdge(
                parent_id=parent.id,
                child_id=investigation.id,
                turn_id=state.turn_id,
                operators=operators,
            )
            if parent is not None
            else None
        )
        await self._investigations.save(investigation, edge)

        extra: dict[str, Any] = {
            "plan_context": {"playbook_id": playbook_id, "window_explicit": window_explicit}
        }
        if trace_extra is not None:
            extra.update(dict(trace_extra))
        if refinement_extra is not None:
            refinement_payload = dict(refinement_extra)
            if diff is not None:
                refinement_payload["diff"] = {
                    "added": [node.hash for node in diff.added],
                    "removed": [node.hash for node in diff.removed],
                    "unchanged": [node.hash for node in diff.unchanged],
                }
            refinement_payload["reconciliation"] = reconciliation
            extra["refinement"] = refinement_payload
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                interpreted=interpreted,
                validated=validated,
                executed=executed,
                calculation=calculation,
                findings=findings_result,
                warnings=warnings,
                extra=extra,
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
            watermark_stale=state.watermark_stale,
            reconciliation=reconciliation,
            diff=diff,
            benchmarks=benchmarks,
        )

    def _benchmarks_for(self, findings: FindingsResult) -> tuple[BenchmarkSpec, ...]:
        """Benchmark ranges for every metric the turn's findings cite, in
        finding order, deduplicated by benchmark id."""
        seen: dict[str, BenchmarkSpec] = {}
        for finding in findings.findings:
            for ref in finding.metric_refs:
                for benchmark in self._pack.benchmarks_for_metric(ref.id):
                    seen.setdefault(benchmark.id, benchmark)
        return tuple(seen.values())

    # ------------------------------------------------- reconciliation (§7.8)

    async def _reconcile_with_parent(
        self,
        parent: Investigation,
        plan: InvestigationPlan,
        calculation: CalculationResult,
        operators: tuple[Refinement, ...],
        warnings: list[str],
        state: _TurnState,
    ) -> str:
        """On splits (SetDimensions) and drills (DrillInto), check that the
        child's cells sum to the parent totals the analyst was shown.

        Every exit from this method now says *something*. It used to return
        ``None`` from four different paths, and the caller returned ``None``
        for a fifth — with the wire type ``string | null`` and no third
        state, "we checked and it agreed" and "we never checked" were the
        same value. Running the reference conversation produced ``None`` on
        the turn that actually drilled three payers and pivoted the measure,
        and ``"status=passed"`` on the turn that was a no-op; a reader had
        no way to tell those apart, and the one that looked reassuring was
        the one that had done nothing.

        The grammar is the existing one: ``status=<verdict>`` with
        semicolon-separated detail, so ``not_applicable`` carries a
        machine-readable ``reason=`` naming which path was taken.
        """
        if not any(isinstance(op, (SetDimensions, DrillInto)) for op in operators):
            return _not_applicable("this turn neither split nor drilled the parent's population")
        shape = find_primary_compare(plan, calculation)
        if shape is None:
            return _not_applicable(
                "this turn produced no compared money frame to reconcile against the parent"
            )
        measure = shape.money_measure
        parent_totals: EvidenceFrame | None = None
        for key in parent.frame_refs:
            frame = await self._frames.get(key)
            if frame is None or measure not in frame.schema.names:
                continue
            if any(isinstance(col.ref, DimensionRef) for col in frame.schema.columns):
                continue
            parent_totals = frame
            if f"{measure}__prior" in frame.schema.names:
                break  # prefer the compare totals (they carry the prior side)
        if parent_totals is None:
            return _not_applicable(
                f"the parent investigation holds no undimensioned {measure!r} total to "
                "reconcile against"
            )
        if parent_totals.watermark != shape.frame.watermark:
            return _not_applicable(
                "the parent's totals were read at a different watermark "
                f"({parent_totals.watermark.id}) than this turn ({shape.frame.watermark.id})"
            )
        measures: tuple[str, ...] = (measure,)
        if (
            f"{measure}__prior" in parent_totals.schema.names
            and f"{measure}__prior" in shape.frame.schema.names
        ):
            measures = (measure, f"{measure}__prior")
        verdict = self._transforms.reconcile(parent_totals, shape.frame, measures=measures)
        if not verdict.passed:
            warnings.append(f"RECONCILIATION_FAILED: {verdict.summary}")
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "RECONCILIATION_FAILED", "detail": verdict.summary},
                )
            )
        return verdict.summary

    # ------------------------------------------------- zero-probe turn paths

    async def _presentation_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question="There's nothing to re-present yet — what should I investigate?",
                    reason="presentation turn without a prior answer",
                ),
            )
        frames = await self._load_frames(parent)
        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=parent.spec,
            parent_id=parent.id,
            findings=parent.findings,
            frame_refs=parent.frame_refs,
        )
        edge = RefinementEdge(
            parent_id=parent.id, child_id=investigation.id, turn_id=state.turn_id, operators=()
        )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(session, state, classified, extra={"presentation_of": parent.id})
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=parent.findings,
            header=build_context_header(parent.spec, session),
            frames=frames,
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
        )

    async def _meta_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        token_match = _REFERENT_TOKEN.search(state.question)
        if token_match is None:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question="Which finding do you mean? Name it by its handle (F1, F2, ...).",
                    reason="meta turn names no referent",
                ),
            )
        token = token_match.group(1).upper()
        entries = await self._referents.list_for_session(session.id)
        entry = next((e for e in entries if e.referent.value == token), None)
        if entry is None:
            raise ReferentNotFoundError(
                f"referent {token!r} is not in the live registry",
                details={"referent": token},
            )
        cited = await self._investigations.get(entry.investigation_id)
        cited_traces = await self._traces.for_investigation(entry.investigation_id)
        payload: Mapping[str, Any] = cited_traces[0].payload if cited_traces else {}
        refinement_payload = payload.get("refinement") or {}
        meta = MetaAnswer(
            referent=token,
            label=entry.label,
            investigation_id=entry.investigation_id,
            probes=tuple(payload.get("probes", ())),
            operators=tuple(payload.get("operators", ())),
            grades=dict(payload.get("grades", {})),
            reconciliation=refinement_payload.get("reconciliation"),
            finding_values=tuple(entry.finding.values) if entry.finding is not None else (),
            warnings=tuple(payload.get("warnings", ())),
        )
        parent = await self._latest_investigation(session, analytical=False)
        investigation = self._minimal_investigation(
            session, state, InvestigationStatus.COMPLETE, classified
        )
        if cited is not None:
            investigation = replace(investigation, spec=cited.spec)
        edge = None
        if parent is not None:
            investigation = replace(investigation, parent_id=parent.id)
            edge = RefinementEdge(
                parent_id=parent.id,
                child_id=investigation.id,
                turn_id=state.turn_id,
                operators=(),
            )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                extra={"meta": {"referent": token, "cites": entry.investigation_id}},
            )
        )
        await self._turn_complete(state, investigation)
        header = build_context_header(cited.spec, session) if cited is not None else None
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=header,
            frames=(),
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            meta=meta,
        )

    async def _context_control_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        parent = await self._latest_investigation(session, analytical=False)
        base_spec = parent.spec if parent is not None else self._fallback_spec(session)
        entries = await self._referents.list_for_session(session.id)

        await self._stage(state, "emit_refinements")
        emission = await self._refinement_emitter.emit(
            state.question,
            context_summary=self._context_lines(base_spec),
            entries=entries,
            resolutions=(),
            dimension_lines=self._dimension_lines(),
            metric_lines=self._metric_lines(),
        )
        state.llm_usages.append(("emit_refinements", emission.usage))
        state.template_hashes["emit_refinements@v1"] = emission.template_hash
        state.time_stage("emit_refinements")
        if emission.clarification is not None or emission.operators is None:
            clarification = emission.clarification or ClarificationRequest(
                question="What should I change about the session context?",
                reason="AMBIGUOUS_REFINEMENT",
            )
            return await self._clarification_outcome(session, state, classified, clarification)

        registry_index = {entry.referent.value: entry.referent for entry in entries}
        domain_ops = to_domain_operators(emission.operators, registry_index)
        ops_json = [op.model_dump(mode="json") for op in emission.operators]

        # On a context-control turn, AddFilter means a sticky session pin
        # (carryover law 5); everything else applies as a normal edit.
        pin_ops = tuple(op for op in domain_ops if isinstance(op, AddFilter))
        other_ops = tuple(op for op in domain_ops if not isinstance(op, AddFilter))
        try:
            spec = apply_refinements(base_spec, other_ops, turn_id=state.turn_id)
            new_pins: list[ContextPin] = []
            for op in pin_ops:
                conflict = detect_conflict(spec, op.predicate)
                if conflict is not None:
                    raise ContextConflictError(
                        f"pin contradicts active context: {conflict}",
                        details={"conflict": conflict},
                    )
                new_pins.append(
                    ContextPin(
                        predicate=replace(op.predicate, origin_turn=state.turn_id),
                        declared_at_turn=state.turn_id,
                    )
                )
        except ContextConflictError as conflict:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question=f"That contradicts the current context: {conflict.message}.",
                    reason=f"CONTEXT_CONFLICT: {conflict.message}",
                ),
            )
        if new_pins:
            spec = spec.with_context(replace(spec.context, pins=(*spec.context.pins, *new_pins)))

        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=spec,
        )
        edge = None
        if parent is not None:
            investigation = replace(investigation, parent_id=parent.id)
            edge = RefinementEdge(
                parent_id=parent.id,
                child_id=investigation.id,
                turn_id=state.turn_id,
                operators=domain_ops,
            )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                extra={
                    "context_control": {
                        "operators": ops_json,
                        "pins": [_predicate_label(pin.predicate) for pin in new_pins],
                    }
                },
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=build_context_header(spec, session),
            frames=(),
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
        )

    async def _kernel_only_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        parent: Investigation,
        new_spec: AnalysisSpec,
        domain_ops: tuple[Refinement, ...],
        ops_json: tuple[Mapping[str, Any], ...],
    ) -> TurnOutcome | None:
        """RankBy/Expand within cached, untruncated frames: zero probes
        (§7.9). Returns None to fall back to the full path when the cached
        frames cannot honestly answer (missing column, truncated frame)."""
        frames = await self._load_frames(parent)
        if not frames:
            return None
        working: dict[str, EvidenceFrame] = dict(frames)
        order: list[str] = [fid for fid, _ in frames]
        for op in domain_ops:
            if isinstance(op, RankBy):
                rank_column = f"{op.by.id}__rank"
                target_id = next(
                    (
                        fid
                        for fid in reversed(order)
                        if op.by.id in working[fid].schema.names
                        and rank_column not in working[fid].schema.names
                    ),
                    None,
                )
                if target_id is None:
                    return None
                ranked_id = f"{target_id}__{op.by.id}__rank"
                working[ranked_id] = self._transforms.rank(
                    working[target_id], by=op.by.id, descending=op.descending
                )
                order.append(ranked_id)
            else:
                assert isinstance(op, Expand)
                if any(frame.truncated for frame in working.values()):
                    return None  # expanding a truncated frame needs the source
        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=new_spec,
            parent_id=parent.id,
            findings=parent.findings,
            plan_hash=parent.plan_hash,
        )
        frame_pairs = tuple((fid, working[fid]) for fid in order)
        refs: list[str] = []
        for fid, frame in frame_pairs:
            key = f"{state.investigation_id}:{fid}"
            await self._frames.save(key, frame)
            refs.append(key)
        investigation = replace(investigation, frame_refs=tuple(refs))
        edge = RefinementEdge(
            parent_id=parent.id,
            child_id=investigation.id,
            turn_id=state.turn_id,
            operators=domain_ops,
        )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                extra={
                    "refinement": {"operators": list(ops_json), "kernel_only": True},
                    "plan_context": {"playbook_id": None, "window_explicit": True},
                },
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=parent.findings,
            header=build_context_header(new_spec, session),
            frames=frame_pairs,
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
        )

    # ------------------------------------------------------- outcome shapes

    async def _clarification_outcome(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        clarification: ClarificationRequest,
        interpretation: InterpretationOutcome | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> TurnOutcome:
        del interpretation  # usage already tracked on state
        investigation = self._minimal_investigation(
            session, state, InvestigationStatus.CLARIFICATION_REQUIRED, classified
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            self._trace_record(
                session, state, classified, clarification=clarification, extra=extra
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
            watermark_stale=state.watermark_stale,
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
            watermark_stale=state.watermark_stale,
        )

    # -------------------------------------------------------------- helpers

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

    async def _plan_context_of(self, investigation_id: str) -> tuple[str | None, bool]:
        traces = await self._traces.for_investigation(investigation_id)
        for record in traces:
            plan_context = record.payload.get("plan_context")
            if isinstance(plan_context, Mapping):
                playbook_id = plan_context.get("playbook_id")
                return (
                    playbook_id if isinstance(playbook_id, str) else None,
                    bool(plan_context.get("window_explicit", True)),
                )
        return None, True

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
                session.watermark.loaded_at.date(),
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
        seen: dict[str, None] = {}
        for metric_id, _ in self._pack.metric_summaries():
            contract = self._pack.metric(metric_id)
            if contract is None:
                continue
            for dim in contract.scope_dimensions:
                seen.setdefault(dim.id)
        return "\n".join(f"- {dim_id}" for dim_id in seen) or "- (none)"

    def _metric_lines(self) -> str:
        return "\n".join(f"- {mid}" for mid, _ in self._pack.metric_summaries()) or "- (none)"

    def _fallback_spec(self, session: Session) -> AnalysisSpec:
        anchor = session.watermark.loaded_at.date()
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
            parent_id=None,
            turn_id=state.turn_id,
            turn_class=turn_class,
            question=state.question,
            spec=self._fallback_spec(session),
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
                    # Transport attempts (1 = clean). Distinct from
                    # schema_retries: a retried connection and a retried
                    # schema are different diagnoses.
                    "attempts": usage.attempts,
                    "duration_ms": usage.duration_ms,
                }
                for template, usage in state.llm_usages
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
