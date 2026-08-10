"""The turn engine itself: what a turn dispatches to, and nothing else."""

from __future__ import annotations

from dataclasses import replace
from typing import assert_never

from revi_investigation.application.calculation_glue import (
    CalculateMetricsService,
)
from revi_investigation.application.capability_ports import PackPort, TransformPort
from revi_investigation.application.cohorts import PinCohortService
from revi_investigation.application.execution import (
    ExecuteInvestigationService,
)
from revi_investigation.application.findings import (
    EvaluateFindingsService,
)
from revi_investigation.application.gestures import parse_gesture
from revi_investigation.application.interpretation import (
    ClassificationOutcome,
    ClassifyTurnService,
    InterpretQuestionService,
    PendingClarification,
    display_scope_limit,
    presentation_order_request,
)
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    DiffPlanService,
)
from revi_investigation.application.ports import (
    FrameStore,
    InvestigationStore,
    ReferentRegistryStore,
    TraceStore,
    TurnEvent,
    TurnEventBus,
)
from revi_investigation.application.refinement_llm import (
    EmitRefinementsService,
    ResolveReferentsService,
    referent_tokens,
)
from revi_investigation.application.submit_turn.clarification import _answers_pending
from revi_investigation.application.submit_turn.open_session import OpenSessionService
from revi_investigation.application.submit_turn.refinement import _RefinementTurns
from revi_investigation.application.submit_turn.types import (
    _NO_MODEL_USAGE,
    RESUMED_QUESTION_LEAD,
    SubmitTurnRequest,
    TurnOutcome,
    _anchor_phrase,
    _join_question_and_answer,
    _new_id,
    _TurnState,
)
from revi_investigation.application.validation import (
    PlanValidationService,
)
from revi_investigation.application.window_maturity import (
    WindowMaturityService,
)
from revi_investigation.domain.records import (
    InvestigationStatus,
    Session,
)
from revi_investigation.domain.turns import (
    ClarificationBinding,
    ClarificationRequest,
    TurnClass,
    TurnClassification,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import (
    ExpandModel,
)


class SubmitTurnService(_RefinementTurns):
    """§8 turn engine on injected services.

    :meth:`submit` dispatches a turn to exactly one path and to nothing else.
    The paths themselves, and the decisions they make, are the base classes
    below this one — each in its own module, each readable on its own, in
    dependency order:

    * :mod:`~revi_investigation.application.submit_turn.recording` — frames,
      traces, events, and reading a stored investigation back.
    * :mod:`~revi_investigation.application.submit_turn.containment` —
      reconciling a child answer against its parent.
    * :mod:`~revi_investigation.application.submit_turn.guards` — maturity,
      comparability and subject guards.
    * :mod:`~revi_investigation.application.submit_turn.clarifying` — whether
      to ask a clarification, and what may go in it.
    * :mod:`~revi_investigation.application.submit_turn.core` — the analysis
      runner and the outcome shapes, which call one another in a cycle.
    * :mod:`~revi_investigation.application.submit_turn.refinement` — the
      refinement path and the turns that run no probes.

    Each class calls only into the ones below it, so any of them can be read
    without the ones above.
    """

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
        #: The load's settling curve. Optional so a test
        #: harness with no warehouse still builds an engine; a deployment
        #: without it simply makes no maturity claim about a window.
        window_maturity: WindowMaturityService | None = None,
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
        self._window_maturity = window_maturity

    async def submit(self, request: SubmitTurnRequest) -> TurnOutcome:
        session = await self._open_session.open(
            tenant=request.tenant, session_id=request.session_id
        )
        state = _TurnState(
            turn_id=_new_id("turn"),
            investigation_id=_new_id("inv"),
            trace_id=_new_id("trace"),
            question=request.question,
            utterance=request.question,
            # a per-turn override applies to this turn only; the session's
            # own settings are the default and are never rewritten here
            settings=request.settings if request.settings is not None else session.settings,
        )
        session = await self._check_watermark(session, state, request)

        if request.worklist_only:
            # A typed worklist request is a whole request. Zero probes, zero
            # model calls, and a COMPLETE investigation rather than a
            # clarification: the answer is the ranked list the API attaches,
            # and asking the analyst what they meant by a control this
            # platform drew is the platform not recognising its own output.
            return await self._worklist_turn(session, state)

        if request.spec is not None:
            # typed FIRST turn: an explicit investigation, never a
            # refinement — no NL, no classification, no LLM
            return await self._typed_investigation_turn(session, state, request.spec)

        if request.refinements is not None:
            # typed-gesture path: no NL, no classification, no LLM
            return await self._refinement_turn(
                session, state, None, dto_ops=tuple(request.refinements)
            )

        # A gesture this platform printed is read back before anything is
        # sent to a model: "drill into F1" is a string we emitted, not
        # language to be interpreted (§7.6, extended to the whole
        # utterance). It used to reach the classifier, come back with a
        # clarification question, and dead-end on the platform's own
        # suggestion.
        gesture = parse_gesture(state.question, await self._referents.list_for_session(session.id))
        if gesture is not None:
            state.time_stage("classify")
            return await self._refinement_turn(
                session, state, None, dto_ops=gesture.operators
            )

        # "Show me all twelve" is a statement about DISPLAY SCOPE over a
        # frame this platform is already holding, and it is decidable
        # without a model. Left to the classifier it came back at 0.45-0.50
        # and the turn ended in a clarification asking whether the twelve
        # had already been computed, which the engine could answer itself.
        # Read here, before classification, so the expansion costs nothing
        # and cannot be lost to a confidence threshold.
        widened = display_scope_limit(state.question)
        if widened is not None:
            parent = await self._latest_investigation(session, analytical=True)
            if parent is not None and widened > len(parent.findings):
                state.time_stage("classify")
                return await self._refinement_turn(
                    session,
                    state,
                    None,
                    dto_ops=(ExpandModel(op="expand", limit=widened),),
                )

        exhausted = self._budget_stop(state, "reading your question")
        if exhausted is not None:
            return await self._clarification_outcome(session, state, None, exhausted)

        # What (if anything) this session already asked and has not had
        # answered. Classification without it cannot tell an answer from a
        # fresh question — see PendingClarification.
        pending = await self._pending_clarification(session, state.question)
        state.pending = pending

        if request.clarification_response and pending is not None:
            # The analyst answered on the dedicated channel. There is
            # nothing left to classify: this IS a clarification response,
            # by construction, at zero model cost — and re-classifying it
            # is exactly how the original question got dropped.
            state.time_stage("classify")
            return await self._clarification_response_turn(
                session,
                state,
                ClassificationOutcome(
                    classification=TurnClassification(
                        turn_class=TurnClass.CLARIFICATION_RESPONSE, confidence=1.0
                    ),
                    clarification=None,
                    usage=_NO_MODEL_USAGE,
                    template_hash="by_construction",
                ),
                pending,
            )

        # "Sort them by percent change, largest first" is a statement about
        # the ORDER of rows this platform is already holding, and it is
        # decidable without a model for the same reason a display-scope
        # request is. The classifier returned presentation_only at 0.76 and
        # 0.68 — below its threshold — so the utterance
        # ``refinement_not_applied`` was written for diverted into a
        # clarification asking whether percent change was already a column,
        # which the engine can answer from the findings it published.
        #
        # Never while a question of ours is on screen: with a clarification
        # outstanding the same words are an ANSWER to it, and reading them
        # as a fresh instruction would drop the dialogue this platform
        # started.
        if pending is None and presentation_order_request(state.question):
            ordered_parent = await self._latest_investigation(session, analytical=True)
            if ordered_parent is not None and ordered_parent.findings:
                state.time_stage("classify")
                return await self._presentation_turn(session, state, None, ordered_parent)

        known = await self._classification_by_construction(session, pending, state.question)
        if known is not None:
            state.time_stage("classify")
            assert known.classification is not None
            if known.classification.turn_class is TurnClass.DEFINITIONAL:
                return await self._definitional_outcome(session, state, known)
            return await self._new_investigation_turn(session, state, known)

        await self._stage(state, "classify")
        classified = await self._classifier.classify(
            request.question, pending=pending, policy=state.call_policy()
        )
        state.record_llm("classify_turn", classified.usage, classified.failure)
        state.template_hashes["classify_turn@v1"] = classified.template_hash
        state.time_stage("classify")

        if classified.clarification is not None or classified.classification is None:
            # One question of ours on screen at a time. An analyst who left
            # "Who is my worst payer?" unanswered and asked their next real
            # question, "Show me AR aging", used to get
            # "Is this answering the 'worst payer' question by picking the
            # days-in-A/R measure, or a new A/R aging request?", with "Drop
            # the worst payer question entirely" among the options. Asking a
            # reader to adjudicate the relationship between their own two
            # sentences is not a clarification; it is the platform's
            # bookkeeping handed over as a question. A self-contained
            # question supersedes whatever was pending, and says so.
            # …after the convergence rule, which describes the same move
            # more precisely when it applies: "I asked twice and did not
            # converge" is a better sentence than "you asked something
            # else", and it names the question being answered.
            committed = self._commit_instead_of_clarifying(
                state, pending
            ) or self._supersede_pending(state, pending)
            if committed is None:
                clarification = classified.clarification or ClarificationRequest(
                    question="Could you rephrase that?", reason="unclassifiable turn"
                )
                return await self._clarification_outcome(
                    session, state, classified, clarification
                )
            # §2.8 convergence: stop asking, commit, and say what was
            # assumed. Parented on the question that was dropped, so the
            # lineage shows what this turn replaced.
            state.assumptions.append(committed)
            if pending is not None and state.lineage_parent is None:
                state.lineage_parent = pending.investigation_id
            return await self._new_investigation_turn(session, state, classified)

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
        if turn_class is TurnClass.CLARIFICATION_RESPONSE:
            return await self._clarification_response_turn(session, state, classified, pending)
        # Every class in the §7.3 taxonomy is dispatched above, so there is
        # nothing left to fall through to. This used to be the "that reads
        # like an answer to a question I haven't asked" clarification, which
        # was CLARIFICATION_RESPONSE's fallthrough before that class got the
        # branch above it (the sentence still lives on the path where it is
        # TRUE — a clarification response with nothing pending). Leaving it
        # here would tell an analyst their question read as an answer when
        # what actually happened is that the taxonomy grew a class the
        # engine does not route; exhaustiveness says so instead, and says it
        # at type-check time.
        assert_never(turn_class)

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

    async def _classification_by_construction(
        self, session: Session, pending: PendingClarification | None, state_question: str
    ) -> ClassificationOutcome | None:
        """The turn class this session's state already determines.

        Six of the seven classes in the §7.3 taxonomy describe an utterance
        *relative to something already on screen*: a refinement edits a
        prior answer, a presentation re-presents one, a meta turn asks how
        one was produced, a context-control turn adjusts a context a prior
        turn established, a clarification response answers a question this
        platform asked. A session that has completed no turn has none of
        those to point at. The first utterance of a session is a new
        investigation — not probably, by construction.

        It was nonetheless sent to a model every time, at the cost of a
        call and a chance of being wrong: live, a first-turn question came
        back REFINEMENT and was answered "there's no prior answer in this
        session to refine yet", which is true and useless. DEFINITIONAL is
        not lost here — interpretation still routes "what is PR3" to the
        pack lookup with zero probes; it is one stage later, not one
        classification away.

        A pending clarification is the one thing that makes a first
        utterance ambiguous again, so its presence hands the turn back to
        the model.
        """
        if pending is not None:
            return None
        if await self._latest_investigation(session, analytical=False) is not None:
            return None
        # DEFINITIONAL is the one other class a first utterance can be, and
        # it is decidable without a model too: a governed lead-in over a
        # term the pack resolves whole. Deciding it here keeps the
        # zero-probe path zero-*call* as well.
        definitional = self._interpreter.definitional_match(state_question)
        return ClassificationOutcome(
            classification=TurnClassification(
                turn_class=(
                    TurnClass.DEFINITIONAL if definitional else TurnClass.NEW_INVESTIGATION
                ),
                confidence=1.0,
            ),
            clarification=None,
            usage=_NO_MODEL_USAGE,
            template_hash="by_construction",
        )

    async def _worklist_turn(self, session: Session, state: _TurnState) -> TurnOutcome:
        """A typed worklist request: complete, zero-probe, zero-call.

        The engine holds no worklist — it is the detection feed's, projected
        by the API — so this turn's job is to be a real node in the session:
        a COMPLETE investigation the lineage can hang follow-ups off, with
        the analyst's own request recorded as its question. The ranked cards
        ride on the response the API assembles around it.
        """
        state.time_stage("worklist")
        investigation = replace(
            self._minimal_investigation(
                session,
                state,
                InvestigationStatus.COMPLETE,
                ClassificationOutcome(
                    classification=TurnClassification(
                        turn_class=TurnClass.NEW_INVESTIGATION, confidence=1.0
                    ),
                    clarification=None,
                    usage=_NO_MODEL_USAGE,
                    template_hash="by_construction",
                ),
            ),
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            self._trace_record(session, state, None, extra={"worklist_request": True})
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
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            settings=state.settings,
        )

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

    async def _referent_resume(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        pending: PendingClarification,
    ) -> TurnOutcome | None:
        """A reply that names a HANDLE is a referent answer, never a value.

        The threaded-drill dead end. The platform asked "By 'that', do you
        mean F1 (finding)?" — optionless, over a handle it had minted
        itself — and the analyst answered in its own words:
        "Yes, F1 — Summit Peak Medicare Advantage". That reply was joined
        onto the original sentence and re-interpreted as a NEW
        investigation, where ``F1`` was read as a dimension value and the
        §6.6 value-existence guard refused it: *"There is no facility named
        'F1' in this data"*. Excellent machinery pointed at the platform's
        own identifier, and the payer name supplied beside it dropped.

        §7.6 already says a handle is resolved by lookup and not by
        language. This is that rule applied one turn earlier than the
        refinement path: a reply carrying a handle re-enters the REFINEMENT
        pipeline, where ``resolve_referent_tokens`` claims the token at
        confidence 1.0 before any planner or validator sees it.
        """
        tokens = referent_tokens(state.question)
        if not tokens:
            return None
        entries = await self._referents.list_for_session(session.id)
        if not any(entry.referent.value in tokens for entry in entries):
            return None
        if await self._latest_investigation(session, analytical=True) is None:
            return None
        state.question = _join_question_and_answer(pending.original_question, state.question)
        state.lineage_parent = pending.investigation_id
        state.assumptions.append(
            f"{RESUMED_QUESTION_LEAD}: {', '.join(tokens)}. Those are handles this session "
            "published, so they are resolved against what was shown rather than read as "
            f"values in the data, and {_anchor_phrase(pending.original_question)} is "
            "resumed against them."
        )
        return await self._refinement_turn(session, state, classified, dto_ops=None)

    async def _presentation_resume(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        pending: PendingClarification,
        binding: ClarificationBinding | None,
    ) -> TurnOutcome | None:
        """Resume a clarification that was raised on a RE-PRESENTATION.

        "Show me all twelve" published twelve payer rows; "sort them by
        percent change, largest first" came back as a clarification; the
        analyst answered it with its own first option — and the resume ran
        ``_new_investigation_turn``, which re-planned the sentence from
        scratch. Three findings, the engine's own order, ``reconciliation:
        this is a first turn``, and a lineage in which a
        ``new_investigation`` hangs off a ``presentation_only``. The twelve
        rows the analyst was looking at were simply gone.

        A clarification is an interruption, and what it interrupts decides
        what resumes. When the turn that ASKED was a re-presentation, the
        answer to it is a re-presentation of the same served set — the
        analytical answer on screen when the question was asked — with the
        presentation op applied to THAT, never to a freshly planned one.

        ``None`` when this clarification did not come from a presentation
        turn, or when there is no served set left to re-present, in which
        case the ordinary resume paths stand.
        """
        asking = await self._investigations.get(pending.investigation_id or "")
        if asking is None or asking.turn_class is not TurnClass.PRESENTATION_ONLY:
            return None
        if not _answers_pending(state.question, pending):
            return None  # a new question is a new question, whoever asked last
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None or not parent.findings:
            return None
        answer = binding.option if binding is not None else state.question
        state.question = _join_question_and_answer(pending.original_question, answer)
        state.lineage_parent = pending.investigation_id
        state.assumptions.append(
            f"{RESUMED_QUESTION_LEAD}: {answer!r}. That question was asked about a "
            f"re-presentation, so this answer re-serves the {len(parent.findings)} row(s) "
            "the turn above it published, with your request applied to them — nothing was "
            "re-planned and nothing was re-measured."
        )
        return await self._presentation_turn(session, state, classified, parent)

    async def _clarification_response_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome,
        pending: PendingClarification | None,
    ) -> TurnOutcome:
        """The analyst answered the question the platform asked.

        ``CLARIFICATION_RESPONSE`` has been in the §7.3 taxonomy since the
        beginning and had no branch here: it fell through to "that reads
        like an answer to a question I haven't asked", *which was returned
        as another clarification*. So a correctly-classified answer — even
        a verbatim option string — produced the loop it was meant to end.

        Resolution is deterministic and invents nothing: the analyst's
        original question and their answer are both their own words, joined
        and re-entered as one utterance. The original comes from the turn
        the clarification interrupted, so answering "just the imaging
        service line" resumes the denial-rate question instead of becoming
        a standalone request to look at imaging.
        """
        if pending is None:
            # Nothing outstanding: the old honest fallback still applies,
            # because there genuinely is no question this answers.
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question=(
                        "That reads like an answer to a question I haven't asked — what "
                        "would you like to investigate?"
                    ),
                    reason="clarification_response with no clarification pending",
                ),
            )
        # The strongest resolution first: the reply IS one of the options
        # the platform offered, and that option already carries the ids it
        # stands for. Nothing is re-read as language — the analyst chose a
        # thing this platform named, and the thing is applied to the
        # question it interrupted.
        binding = pending.binding_for(state.question)
        # …but WHICH pipeline resumes is decided by the turn that asked, not
        # by the default. A clarification raised on a
        # re-presentation is answered by re-presenting: replanning it as a
        # first turn is how a twelve-row answer came back as three, in the
        # engine's default order, narrated as a new investigation with
        # ``reason=this is a first turn``.
        presented = await self._presentation_resume(session, state, classified, pending, binding)
        if presented is not None:
            return presented
        # A handle this platform minted is an identifier, never a dimension
        # value — on every path, including this one.
        claimed = await self._referent_resume(session, state, classified, pending)
        if claimed is not None:
            return claimed
        if binding is not None and pending.original_question:
            state.question = pending.original_question
            state.lineage_parent = pending.investigation_id
            state.assumptions.append(
                f"{RESUMED_QUESTION_LEAD}: {binding.option!r}. Resuming "
                f"{_anchor_phrase(pending.original_question)} with that applied; this "
                "answer is recorded as a child of the turn that asked."
            )
            return await self._apply_binding(session, state, classified, binding)
        # A reply that answers nothing is not an answer: a genuinely new
        # question was once swallowed as a clarification response and
        # spliced onto the abandoned one under a
        # CLARIFICATION_ANSWER_APPLIED disclosure that was simply false.
        # A self-contained question that matches no option we offered is
        # run as itself, and the dropped clarification is disclosed as
        # dropped rather than as applied.
        if not _answers_pending(state.question, pending):
            state.assumptions.append(
                "Assumed: this is a new question, not an answer to the question above — it "
                "matches none of the options that question offered and stands on its own. "
                "That question is left unanswered; ask it again if you still want it."
            )
            state.lineage_parent = pending.investigation_id
            return await self._new_investigation_turn(session, state, classified)
        resolved = _join_question_and_answer(pending.original_question, state.question)
        if resolved != state.question:
            state.assumptions.append(
                f"{RESUMED_QUESTION_LEAD}: {state.question!r}. Answering "
                f"{_anchor_phrase(pending.original_question)} with that applied."
            )
            state.question = resolved
        # Parented either way: an answer to a question this platform asked
        # belongs under that question, whether it matched an option or was
        # typed in the analyst's own words. Without this, every clarification
        # reply in a 13-investigation session was saved as a ROOT node.
        state.lineage_parent = pending.investigation_id
        return await self._new_investigation_turn(session, state, classified)
