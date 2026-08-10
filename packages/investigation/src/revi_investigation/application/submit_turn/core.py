"""The mutually recursive heart of a turn."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from revi_investigation.application.comparison import (
    window_mismatch_warning,
)
from revi_investigation.application.execution import (
    bounded_cells_warning,
)
from revi_investigation.application.findings import (
    row_noun,
)
from revi_investigation.application.interpretation import (
    OPTIONS_DROPPED_MARKER,
    ClassificationOutcome,
    DefinitionalAnswer,
    InterpretationOutcome,
    InterpretedInvestigation,
)
from revi_investigation.application.planning import (
    ANSWERING_TRANSFORMS,
    PlanDiff,
    resolved_orderings,
)
from revi_investigation.application.ports import (
    TurnEvent,
)
from revi_investigation.application.rendering import metric_label
from revi_investigation.application.submit_turn.census import (
    _bounds_by_window,
    _same_findings,
    _turn_census,
    probe_families_empty_warning,
)
from revi_investigation.application.submit_turn.clarification import (
    _COMMITTED_REASONS,
    _drop_interrogative_options,
    _no_options_card,
    _no_replay,
    _option_window_assumed,
    _state_the_survivor,
    _with_binding,
    _with_chosen_values,
    _with_resumed_context,
    claim_referent_predicates,
    drop_refuted_options,
    options_named,
    scope_names_a_handle,
)
from revi_investigation.application.submit_turn.clarifying import _ClarificationPolicy
from revi_investigation.application.submit_turn.header import build_context_header, plan_measure_ids
from revi_investigation.application.submit_turn.types import (
    TurnOutcome,
    _join_question_and_answer,
    _not_applicable,
    _TurnState,
)
from revi_investigation.application.validation import (
    PlanClarificationNeeded,
    PlanValidationService,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import (
    Investigation,
    InvestigationStatus,
    RefinementEdge,
    Session,
)
from revi_investigation.domain.refinements import (
    Refinement,
)
from revi_investigation.domain.turns import (
    ClarificationBinding,
    ClarificationRequest,
    TurnClass,
)
from revi_investigation_contracts.settings import EvidenceDepth
from revi_kernel.errors import (
    DateBasisInvalidError,
    GrainIncompatibleError,
    ReviError,
    UnsupportedConceptError,
)
from revi_kernel.filters import PredicateOp
from revi_kernel.refs import MetricRef


@dataclass(frozen=True, slots=True)
class _DirectRoute:
    """A plan built around a playbook this engine cannot run, and the
    sentence that says so."""

    spec: AnalysisSpec
    disclosure: str


class _TurnCore(_ClarificationPolicy):
    """Running an analysis, starting an investigation, applying a binding, and
    the two outcome shapes.

    These six methods call one another in a cycle — an analysis may end in a
    clarification, and an answered clarification re-enters the analysis — so
    they are one class rather than several."""

    async def _apply_binding(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        binding: ClarificationBinding,
    ) -> TurnOutcome:
        """Re-run the interrupted question with one option applied.

        The application is a substitution into the ORIGINAL question's
        pipeline, never a new question:

        * ``date_basis`` re-interprets the same words with the basis fixed,
          so the playbook routing, the window vocabulary and the concepts
          the analyst's sentence carried all survive — the runway question
          comes back as the runway question;
        * everything else re-interprets the same words too, with the
          option's own ids PINNED over whatever the second reading proposes.

        The third branch used to skip interpretation entirely and run
        ``_spec_for_binding``'s dry-run spec, whose window is the
        three-year *value-existence* window and
        whose scope is the option's alone. So "how many dollars did we lose
        to denials in July 2026?" → the platform's own clarification → the
        platform's own offered option came back as **$25,929,558.84 over
        2023-01-01..2026-08-02** — the whole warehouse, at high confidence,
        under a disclosure asserting the July question had been resumed.
        Same line, second symptom: "break that down by CARC code." on a
        payer/service-line/July thread came back as a ROOT investigation
        with no filters, no cohort and no parent.

        The two branches beside it were always right, and they are right
        for the same reason: they re-read the analyst's own sentence. So
        does this one now — the option contributes IDS (measures, cuts,
        values), and the sentence contributes everything else. What the
        sentence does not state is carried from the answer this resume
        continues rather than defaulted (see ``_with_resumed_context``), and
        the typed spec survives as the fallback for the case where the
        second reading cannot resolve the question either.
        """
        if binding.kind == "date_basis" and binding.basis:
            state.basis_override = binding.basis
            return await self._new_investigation_turn(session, state, classified)
        if binding.kind == "predicate_value" and binding.scope:
            # The analyst picked a value from the twelve this warehouse
            # actually holds, offered because the one they typed does not
            # exist. Joining their reply onto the original sentence and
            # re-interpreting it leaves the refuted value in the question —
            # live, that came straight back as the SAME refusal. The choice
            # is a substitution and is applied as one.
            state.scope_override = binding.scope
            return await self._new_investigation_turn(session, state, classified)
        # The option is the analyst's answer, so it joins their question:
        # both halves are their own words and the join is the same
        # deterministic one the typed-reply path uses.
        state.question = _join_question_and_answer(state.question, binding.option)
        resumed = await self._resumed_context(session, state)
        return await self._new_investigation_turn(session, state, classified, binding=binding, resume=resumed)

    async def _new_investigation_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        *,
        binding: ClarificationBinding | None = None,
        resume: AnalysisSpec | None = None,
    ) -> TurnOutcome:
        """Interpret the analyst's sentence and run it (§8.1).

        ``binding`` and ``resume`` are set only on a clarification resume.
        The binding's ids are pinned over the second reading — the analyst
        chose a thing this platform named and that choice is not re-decided
        by a model — and ``resume`` is the context of the answer the
        clarification interrupted, applied only where the sentence itself
        states nothing.

        ``classified`` is ``None`` on the turns this engine decides WITHOUT
        a model — the deterministic display-limit expansion, a budget stop
        taken before classification — and those turns reach here through
        ``_apply_binding``. It is threaded, never invented: the trace and
        the persisted node both record "no classification" rather than a
        fabricated one (see ``_trace_record``/``_minimal_investigation``).
        """
        exhausted = self._budget_stop(state, "working out what to measure")
        if exhausted is not None:
            return await self._clarification_outcome(session, state, classified, exhausted)

        await self._stage(state, "interpret")
        try:
            interpretation = await self._interpreter.interpret(
                state.question,
                session=session,
                turn_id=state.turn_id,
                policy=state.call_policy(),
                # Set when a clarification about the date basis has been
                # answered — or answered itself, see _recoverable_refusal.
                basis_override=state.basis_override,
            )
        except DateBasisInvalidError as refusal:
            # The basis is fixed at interpretation (it is what the context
            # header publishes), so this refusal never reached the planner's
            # recovery path and ended as a §12 banner. Same machinery, same
            # honesty rule: alternatives or nothing.
            recovered = await self._recoverable_refusal(session, state, classified, refusal, None)
            if recovered is not None:
                return recovered
            raise
        state.record_llm("interpret_question", interpretation.usage, interpretation.failure)
        state.template_hashes["interpret_question@v1"] = interpretation.template_hash
        state.time_stage("interpret")

        interpreted: InterpretedInvestigation | None = None
        prelude: list[str] = []
        if interpretation.clarification is not None:
            # A resume that cannot be re-read is still a resume: the option
            # the analyst tapped carries ids, and running them is better
            # than asking the same question a second time. Only reachable
            # on the clarification-resume path, where the alternative is a
            # loop the analyst has already answered once.
            typed = self._spec_for_binding(session, binding) if binding is not None else None
            if typed is None:
                return await self._clarification_outcome(
                    session,
                    state,
                    classified,
                    self._bounded_clarification(state, interpretation.clarification),
                    interpretation,
                )
            spec = typed
            playbook_id = binding.playbook_id if binding is not None else None
            window_explicit = False
            prelude.append(_option_window_assumed(spec))
        elif interpretation.definitional is not None:
            return await self._definitional_outcome(
                session, state, classified, answer=interpretation.definitional
            )
        else:
            assert interpretation.investigation is not None
            interpreted = interpretation.investigation
            # The analyst's choice is ids this platform published; a second
            # reading of their sentence does not get to overrule it — and
            # that includes a playbook the option named, which is the only
            # thing a playbook-only option carries.
            spec = _with_binding(interpreted.spec, binding)
            playbook_id = interpreted.playbook_id
            if binding is not None and binding.playbook_id is not None:
                playbook_id = binding.playbook_id
            window_explicit = interpreted.window_explicit

        # What the resumed sentence did not state is carried from the answer
        # it continues, never defaulted to the widest window the load allows.
        spec, window_explicit, carried = _with_resumed_context(spec, resume, window_explicit)
        prelude.extend(carried)

        # carryover law 5: session pins persist until explicitly cleared
        pins = await self._inherited_pins(session)
        spec = _with_chosen_values(spec, state.scope_override)
        if pins:
            spec = spec.with_context(replace(spec.context, pins=pins))
        # A handle this platform minted must never reach the value-existence
        # guard as a dimension value. Gated on the pure test so the registry
        # read is paid only by a turn that could use it.
        if scope_names_a_handle(spec):
            spec, claimed = claim_referent_predicates(
                spec, await self._referents.list_for_session(session.id)
            )
            prelude.extend(claimed)

        return await self._run_analysis(
            session,
            state,
            classified,
            spec=spec,
            playbook_id=playbook_id,
            window_explicit=window_explicit,
            turn_class=TurnClass.NEW_INVESTIGATION,
            parent=None,
            operators=(),
            interpreted=interpreted,
            prelude_warnings=tuple(prelude),
            trace_extra=(
                {
                    "clarification_binding": {
                        "option": binding.option,
                        "kind": binding.kind,
                        "metric_ids": list(binding.metric_ids),
                        "dimension_ids": list(binding.dimension_ids),
                        "resumed_investigation_id": state.lineage_parent,
                        "resumed_window": (
                            f"{spec.context.window.range.start.isoformat()}.."
                            f"{spec.context.window.range.end.isoformat()}"
                        ),
                    }
                }
                if binding is not None
                else None
            ),
        )

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
        parent_evidence_depth: EvidenceDepth | None = None,
    ) -> TurnOutcome:
        effective_playbook = playbook_id if not spec.measures else None
        evidence_depth = state.settings.evidence_depth

        await self._stage(state, "plan")
        try:
            plan = self._planner.build(
                spec,
                playbook_id=effective_playbook,
                window_explicit=window_explicit,
                evidence_depth=evidence_depth,
            )
        except (DateBasisInvalidError, GrainIncompatibleError, UnsupportedConceptError) as refusal:
            direct = self._direct_route_for_unexecutable_playbook(refusal, spec)
            rebuilt = None
            if direct is not None:
                try:
                    rebuilt = self._planner.build(
                        direct.spec,
                        playbook_id=None,
                        window_explicit=window_explicit,
                        evidence_depth=evidence_depth,
                    )
                except ReviError:
                    rebuilt = None  # the direct route is no route: refuse as before
            if direct is None or rebuilt is None:
                recovered = await self._recoverable_refusal(session, state, classified, refusal, spec)
                if recovered is not None:
                    return recovered
                raise
            plan = rebuilt
            spec = direct.spec
            effective_playbook = None
            playbook_id = None
            state.assumptions.append(direct.disclosure)
        diff: PlanDiff | None = None
        if parent is not None:
            parent_playbook = playbook_id if not parent.spec.measures else None
            parent_plan = self._planner.build(
                parent.spec,
                playbook_id=parent_playbook,
                window_explicit=window_explicit,
                # the depth the PARENT ran at, so the diff compares this
                # plan against the one that actually produced the parent
                evidence_depth=(
                    parent_evidence_depth if parent_evidence_depth is not None else evidence_depth
                ),
            )
            diff = self._differ.diff(parent_plan, plan)
        state.time_stage("plan")

        await self._stage(state, "validate")
        try:
            validated = self._validator.validate(plan, spec)
            # …then the values those filters name, against the values that
            # exist at this watermark. A separate (async) pass because it
            # reads the source; the refusal it can raise is a question for
            # the analyst, not an error code (§6.6 step 4b).
            validated = await self._validator.resolve_predicate_values(validated, watermark=session.watermark)
        except PlanClarificationNeeded as needed:
            return await self._clarification_outcome(session, state, classified, needed.clarification)
        except (DateBasisInvalidError, GrainIncompatibleError, UnsupportedConceptError) as refusal:
            recovered = await self._recoverable_refusal(session, state, classified, refusal, spec)
            if recovered is not None:
                return recovered
            raise
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
        playbook = self._pack.playbook(effective_playbook) if effective_playbook is not None else None
        # Every window this plan READS, judged against the load's settling
        # curve before any verdict is composed over one. A playbook probe
        # declares its own window, so a premise verdict could be computed
        # over a period the spec never named, which the maturity guard —
        # reading the spec — never saw.
        maturity_by_window = await self._plan_window_maturity(validated.plan, spec)
        findings_result = await self._evaluator.evaluate(
            plan=validated.plan,
            calculation=calculation,
            spec=spec,
            pack=self._pack,
            playbook=playbook,
            session_id=session.id,
            investigation_id=state.investigation_id,
            window_maturity=maturity_by_window,
            # The §15 threshold the frames were policed under: a bound is
            # only recognisable against the threshold that produced it, and
            # without it findings publish `(threshold - 1) / n` as a
            # measured value.
            suppression_threshold=self._executor.suppression_threshold,
        )
        state.time_stage("findings")

        extra_warnings: list[str] = list(prelude_warnings)
        # A refinement that produced the parent's own plan changed nothing
        # the evidence can see: an identical ``plan_hash`` was published as
        # a NEW investigation, with a new referent and 1,500 fresh words of
        # narrative, and no disclosure that the grain the question asked for
        # had been dropped. The answer is legitimate; presenting it as a
        # different one is not.
        #
        # The note is owed to the HASH, not to a branch. While the reuse
        # disclosure lived only in the kernel-only path — which returns None
        # the moment an Expand asks for more rows than the parent published
        # — "show me all twelve" against a three-finding parent never
        # reached it, and neither string fired at all. Which of the two
        # notes applies is decided by what the reader can see: a re-served
        # plan whose FINDINGS changed did apply the operator (the analyst
        # asked for more rows and got them), and one whose findings are
        # byte-identical did not.
        if parent is not None and operators and validated.plan.plan_hash == parent.plan_hash:
            if _same_findings(findings_result.findings, parent.findings):
                extra_warnings.append(
                    "refinement_not_applied: what you asked to change does not alter this plan "
                    f"({parent.plan_hash}) — the operator(s) "
                    + ", ".join(sorted({type(op).__name__ for op in operators}))
                    + " left the evidence identical to the previous answer, so these are the "
                    "same numbers re-measured, not a new result. Say what to change about the "
                    "metric, the cut or the window and I will re-run it."
                )
            else:
                extra_warnings.append(
                    "refinement_reused_plan: this answer re-serves the previous turn's plan "
                    f"({parent.plan_hash}) — the same evidence and every caveat that came with "
                    "it. What changed is how much of it is published: "
                    + ", ".join(sorted({type(op).__name__ for op in operators}))
                    + f" took the published set from {len(parent.findings)} finding(s) to "
                    f"{len(findings_result.findings)}."
                )
        # A comparison against a different-length window is answerable but
        # not a delta anyone should act on; the warning rides with the
        # answer and the findings withhold impact (see comparison.py).
        families = probe_families_empty_warning(validated, executed, findings_result.findings)
        if families is not None:
            extra_warnings.append(families)
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "PROBE_FAMILIES_EMPTY", "detail": families},
                )
            )
        # What the §15 policy bounded rather than dropped, said once for the
        # turn: a ranking that mixes measured and bounded figures without
        # saying so is as misleading as one that censors the bounded rows.
        # Counted once, at frame level, and published as the integers the
        # narrator must cite rather than derive: three surfaces once
        # published three different numbers for one control, and in one case
        # the narrator invented the population outright.
        census = _turn_census(calculation, self._executor.suppression_threshold)
        # …and composed from the CURRENT window's cells. A plan that
        # compares reads two windows, and folding the prior window's
        # ceilings into one list put the same payer in the disclosure twice
        # — the second row being the figure quoted three sentences earlier
        # as the prior month — under a count that said four over a list of
        # five.
        current_bounds, prior_bounds = _bounds_by_window(validated.plan, executed, spec)
        bounds = bounded_cells_warning(
            current_bounds,
            self._executor.suppression_threshold,
            census=census,
            comparison_cells=prior_bounds,
            # The reader's own word for these rows, so this sentence and the
            # ranking's own ("4 of 12 payers…") cannot describe one set of
            # cells with two nouns.
            noun=row_noun(tuple(ref.id for ref in spec.dimensions) if spec else ()),
        )
        if bounds is not None:
            extra_warnings.append(bounds)
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "SUPPRESSION_BOUNDED", "detail": bounds},
                )
            )
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
        # A cell whose prior side was never retrieved publishes UNKNOWN
        # rather than $0.00, and says so.
        extra_warnings.extend(calculation.warnings)
        # …and the data-maturity guard the trend path has always had,
        # applied to the axis it could not see: two windows settled to
        # different degrees are not comparable at high confidence.
        findings_result = self._guard_comparison_maturity(findings_result, calculation, extra_warnings)
        # …and to the leg neither of those can measure: a metric whose own
        # contract declares two windows non-comparable. The panel rule keys
        # on adjudicated-record counts and is structurally blind to a metric
        # whose denominator is dollars.
        findings_result = self._guard_declared_comparability(findings_result, calculation, extra_warnings)
        # …and the same guard applied to the shape neither the series rule
        # nor the comparison rule can see: ONE window, one number.
        findings_result = await self._guard_window_maturity(
            findings_result,
            spec,
            extra_warnings,
            state,
            maturity_by_window,
            window_explicit=window_explicit,
        )
        for detail in calculation.warnings:
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "COMPARISON_PRIOR_UNKNOWN", "detail": detail},
                )
            )
        reconciliation = (
            await self._reconcile_with_parent(
                parent, validated.plan, calculation, operators, extra_warnings, state, spec
            )
            if parent is not None
            else _not_applicable("this is a first turn; there is no parent answer to reconcile to")
        )

        # Assumptions lead: when the engine committed to a reading rather
        # than asking again (§2.8), or read an utterance as an answer to its
        # own question, the analyst must meet that decision BEFORE the
        # numbers it produced — not below a stack of basis and suppression
        # notes they will not scroll to. The same rule covers what
        # interpretation decided on their behalf (a period nobody named, a
        # filter dropped as redundant) and what selection could not find
        # (nothing moved the way the question asked about): those are
        # statements about whether the answer answers the question, and
        # they cannot sit under the answer.
        warnings = (
            *state.assumptions,
            *(interpreted.notes if interpreted is not None else ()),
            *findings_result.warnings,
            *validated.warnings,
            *validated.plan.notes,
            *extra_warnings,
        )
        # An empty population is the stronger fact: when nothing was
        # retrieved at all, "no finding survived" is a consequence, not the
        # explanation.
        emptiness = calculation.emptiness or findings_result.emptiness
        benchmarks = self._benchmarks_for(findings_result, warnings)
        header = build_context_header(
            spec,
            session,
            pack=self._pack,
            corrections=validated.corrections_map,
            # What the PLAN read, not only what the utterance named: a
            # playbook turn carries no spec.measures and its snapshot
            # contracts would otherwise render under a start..end window.
            measure_ids=plan_measure_ids(validated.plan),
            findings=findings_result.findings,
        )
        frame_refs = await self._persist_frames(state, calculation)
        # A refinement's parent is the answer it edits; a clarification
        # answer's parent is the question that asked it. Either is a real
        # edge and neither used to be recorded for the second kind.
        lineage_parent_id = parent.id if parent is not None else state.lineage_parent
        investigation = Investigation(
            id=state.investigation_id,
            session_id=session.id,
            parent_id=lineage_parent_id,
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
                parent_id=lineage_parent_id,
                child_id=investigation.id,
                turn_id=state.turn_id,
                # No operators on a clarification edge: nothing was refined,
                # a question was answered.
                operators=operators if parent is not None else (),
            )
            if lineage_parent_id is not None
            else None
        )
        await self._investigations.save(investigation, edge)

        chart_sorts = resolved_orderings(validated.plan)
        extra: dict[str, Any] = {
            "plan_context": {
                "playbook_id": playbook_id,
                "window_explicit": window_explicit,
                "evidence_depth": evidence_depth.value,
                # The orderings this plan resolved, recorded so a RESTORED
                # turn's charts sort the way the live ones did.
                # Rebuilt charts read the persisted frames, and the frame
                # carries the rows but not the decision that ordered them.
                "chart_sorts": [
                    {"frame_id": frame_id, "by": by, "descending": descending}
                    for frame_id, by, descending in chart_sorts
                ],
            },
            # Recorded for every analytical turn, not only refinements.
            # It used to live under ``refinement`` alone, which meant a
            # first turn's verdict — "not_applicable, there is no parent"
            # — was computed, returned on the wire, and then lost: a
            # rehydrated turn had no way to say whether anything had been
            # checked. The ``refinement`` copy stays for readers that
            # already know where to look.
            "reconciliation": reconciliation,
            # Structured, never prose: which kind of nothing this turn
            # produced, and what was filtering when it did.
            "emptiness": emptiness.as_payload() if emptiness is not None else None,
            # The §15 cell arithmetic in full. It used to be appended to the
            # answer's own suppression sentence, so the paragraph a reader
            # most needs to parse stated its census twice — once in English
            # and once in the engine's. The page now says the count once, in
            # words; the partition is auditable here.
            "census": census.as_payload() if census is not None else None,
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
            settings=state.settings,
            emptiness=emptiness,
            chart_sorts=chart_sorts,
        )

    def _direct_route_for_unexecutable_playbook(
        self, refusal: ReviError, spec: AnalysisSpec
    ) -> _DirectRoute | None:
        """A one-probe direct route for a DRILL a playbook cannot answer.

        "Drill into Eastmere Medical Center", one turn after a facility
        ranking, was refused: interpretation had reached for
        ``dimension_scorecard``, that playbook answers by ``pivot``, and
        ``pivot`` is one of the two transforms this engine does not
        implement — so a question with an obvious one-probe answer became
        a second clarification in a thread that had already asked one.

        The refusal itself is right and stays: a question that asks FOR a
        scorecard gets told the scorecard does not exist here rather than
        being handed its columns dressed as one (``ANSWERING_TRANSFORMS``,
        and the payer-scorecard and cash-outlook cases that pin it). What
        was wrong is applying it to a question that asked for something
        else. This is the entity half of a rule this engine already keeps
        on the measure side — *a text naming a governed measure is a direct
        query whatever playbook words it also contains*
        (``PlanValidationService.unanswerable_playbook``) — and it is
        one-sided in the same way: it only ever converts a refusal into an
        answer, never an answer into a refusal.

        The discriminator is the SCOPE, not the wording. A spec whose
        effective scope already pins a dimension to specific values is
        addressing one cell; a pivot across cells is not what it asked for,
        and measuring the cell it names is. Where nothing is pinned — "score
        my facilities" — no route is offered and the refusal stands.
        """
        if not isinstance(refusal, UnsupportedConceptError):
            return None
        details = refusal.details or {}
        playbook_id, transform = details.get("playbook"), details.get("transform")
        if not isinstance(playbook_id, str) or transform not in ANSWERING_TRANSFORMS:
            return None
        if spec.measures:
            return None  # a named measure already routes directly
        pinned = {
            predicate.dimension.id
            for predicate in PlanValidationService._top_level_predicates(spec.context.effective_scope())
            if predicate.op in (PredicateOp.EQ, PredicateOp.IN) and predicate.values
        }
        if not pinned:
            return None  # nothing is named: this really is a cross-cell ask
        playbook = self._pack.playbook(playbook_id)
        if playbook is None:  # pragma: no cover - the planner read it a moment ago
            return None
        measures: list[MetricRef] = []
        for probe in playbook.probes:
            for metric_id in probe.metric_ids:
                if self._pack.metric(metric_id) is None:
                    continue
                ref = MetricRef(id=metric_id)
                if ref not in measures:
                    measures.append(ref)
            if measures:
                break  # ONE probe family: a drill is a measurement, not a sweep
        if not measures:
            return None
        named = ", ".join(metric_label(ref.id) for ref in measures)
        return _DirectRoute(
            spec=replace(spec, measures=tuple(measures)),
            disclosure=(
                f"Assumed: measured {named} directly for the population this question "
                f"already names, rather than through the {playbook_id!r} playbook. That "
                f"playbook answers by a {transform!r} step this engine does not implement, "
                "and a question already scoped to a named entity is a measurement of that "
                "entity — not a comparison across entities. Ask for the others by name and "
                "they run the same way."
            ),
        )

    async def _recoverable_refusal(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        refusal: ReviError,
        spec: AnalysisSpec | None = None,
    ) -> TurnOutcome | None:
        """A §6.6 refusal the pack can offer a way out of (design §2.8).

        ``GRAIN_INCOMPATIBLE`` and ``UNSUPPORTED_CONCEPT`` were terminal:
        an error banner naming a code, and no next move — while the pack
        often held a metric that answers the same question at a cut it does
        declare. The validator derives those alternatives from content
        (never invents them); when it can, the turn ends as a clarification
        with them as options. When it cannot, ``None`` comes back and the
        refusal stands: a clarification with no way forward is worse than
        an error that says what happened.
        """
        clarification = self._validator.clarification_for(refusal, spec)
        if clarification is None:
            return None
        # A "question" with one answer is not a question.
        # ``timely_filing_at_risk_dollars`` cannot be read on the submission
        # basis and this warehouse binds exactly one alternative, so the
        # platform asked "which should I use?" over a list of one — and the
        # analyst's runway question was then lost answering it. Where the
        # lone option is a binding this platform derived itself, it is
        # applied and DISCLOSED, which is the move this product already
        # makes well everywhere else.
        applied = self._lone_binding(clarification)
        if applied is not None and not state.applied_bindings:
            state.applied_bindings.append(applied.option)
            state.assumptions.append(
                f"clarification_answer_applied: {applied.option} — this was the only "
                f"answer available to '{clarification.question}', so it was applied rather "
                "than asked about, and your question was run with it. Say so if you wanted "
                "something else and I will re-run it."
            )
            return await self._apply_binding(session, state, classified, applied)
        return await self._clarification_outcome(session, state, classified, clarification)

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
        # ONE funnel, in one order, for every clarification this engine can
        # emit. Every defect below arose in a path that skipped one of these
        # steps.
        #
        offered = len(clarification.options)
        # 0. An option is something the analyst can SAY. "Why did it go up?"
        #    was once answered with option 3 "Which metric are you asking
        #    about? — I mean the last figure you charted." — the platform's
        #    own question pasted into the list of things the reader might
        #    reply. Sending it back answers nothing, which is how a
        #    clarification becomes a loop.
        clarification = _drop_interrogative_options(clarification)
        # 1. Values this session has already proved do not exist…
        clarification = drop_refuted_options(clarification, await self._refuted_in_session(session))
        # 2. …then the warehouse and the planner…
        clarification = await self._validated_options(session, clarification)
        # 2b. …then the plan grammar, applied to the option's WORDS as well
        #     as to its ids. ``_validated_options`` above dry-runs an option
        #     that carries a binding and waves through one that does not —
        #     which is every option the classifier writes in free text. "Why
        #     did it go up?" was answered with the platform's own "Yes —
        #     re-group the figure F1 result by denial reason", and tapping it
        #     produced GRAIN_INCOMPATIBLE with the engine's internal
        #     predicate on screen.
        clarification = await self._grammatical_options(session, clarification)
        # 3. …then the subject: a clarification about rendering-provider
        #    denial rates must not offer payer options.
        clarification = await self._on_subject(session, clarification, state.question)
        # 3a. …and a question this session has already answered is not a
        #     question: asking WHICH measure of a session holding exactly
        #     one is how "why did it go up" burned its second turn.
        clarification = await self._settled_measure(session, clarification)
        # 3b. …then the one thing a dialogue may never do: ask the question
        #     it has just asked, word for word, of an analyst who answered
        #     it. This branch covers the clarifications the VALIDATOR raises
        #     — the value-existence refusal — which reach this funnel from
        #     `_run_analysis` and never through `classified.clarification`,
        #     so the model-requested convergence counter below has never
        #     seen them.
        if self._repeats_pending(state, clarification):
            narrowed = options_named(state.utterance or state.question, clarification.options)
            if len(narrowed) == 1 and not state.applied_bindings:
                lone = clarification.binding_for(narrowed[0])
                if lone is not None:
                    state.applied_bindings.append(lone.option)
                    state.assumptions.append(
                        f"clarification_answer_applied: {lone.option} — I had already asked "
                        f"{clarification.question!r} once and your reply "
                        f"({state.question!r}) names exactly one of the values it offered, so "
                        "it was applied rather than asked about a second time. Say so if you "
                        "meant a different one and I will re-run it."
                    )
                    return await self._apply_binding(session, state, classified, lone)
            clarification = _no_replay(state, clarification, narrowed)
        # 4. …then convergence, counted across EVERY clarification the
        #    engine issues rather than only the interpreter's.
        clarification = await self._converge_on_subject(session, state, clarification)
        clarification = self._bounded_clarification(state, clarification)
        # 5. …and finally: a question with one answer is not a question.
        #    ``_lone_binding`` was reachable only from the validator-derived
        #    refusal path, so a model-requested clarification whose options
        #    validation had reduced to exactly one still charged a turn to
        #    ask it.
        reason_so_far = clarification.reason or ""
        reduced = (
            offered > len(clarification.options) or OPTIONS_DROPPED_MARKER in reason_so_far
        ) and not any(marker in reason_so_far for marker in _COMMITTED_REASONS)
        lone = self._lone_binding(clarification, reduced=reduced)
        if lone is not None and reduced:
            # …but a COLLAPSE is not an answer. "Give me a payer scorecard
            # for July 2026" mid-session came back as ``outcome: answer``
            # over a single finding — "F5 | Atlas Commercial: 179.5 days in
            # ar", a payer the turn never named — with the refusal demoted
            # into a CLARIFICATION_ANSWER_APPLIED warning that the client's
            # caution fold then hid. The engine had asked a real question,
            # watched the data drop every option but one, and treated the
            # survivor as though the analyst had chosen it.
            #
            # Nobody chose it. A collapse to one surviving option is a fact
            # about this warehouse, and the honest move is to SAY it: the
            # refusal keeps the lead, the survivor is stated, and the
            # analyst decides whether it answers what they asked. Bindings
            # this platform DERIVED from governed content are untouched —
            # there the single answer is the pack's, not the data's, and
            # asking about it is the one-answer "question" above.
            clarification = _state_the_survivor(clarification, lone)
        elif lone is not None and not state.applied_bindings:
            state.applied_bindings.append(lone.option)
            state.assumptions.append(
                f"clarification_answer_applied: {lone.option} — this was the only answer "
                f"available to '{clarification.question}', so it was applied rather than "
                "asked about, and your question was run with it. Say so if you wanted "
                "something else and I will re-run it."
            )
            return await self._apply_binding(session, state, classified, lone)
        clarification = _no_options_card(clarification)
        investigation = self._minimal_investigation(
            session, state, InvestigationStatus.CLARIFICATION_REQUIRED, classified
        )
        await self._investigations.save(
            investigation,
            RefinementEdge(
                parent_id=state.lineage_parent,
                child_id=investigation.id,
                turn_id=state.turn_id,
                operators=(),
            )
            if state.lineage_parent is not None
            else None,
        )
        await self._traces.save(
            self._trace_record(session, state, classified, clarification=clarification, extra=extra)
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
            settings=state.settings,
        )

    async def _definitional_from_model(
        self, session: Session, state: _TurnState
    ) -> DefinitionalAnswer | None:
        """The model's reading of WHICH term a definitional turn asked about.

        The deterministic strip handles the phrasings it knows and cannot
        know all of them; ``interpret_question`` already has a
        ``definitional_terms`` field and resolves each term against the
        pack individually. This is the same path the NEW_INVESTIGATION
        route takes — reached here so that a correct classification is
        never worse than an absent one.

        ``None`` on any refusal or budget stop: this runs where the
        alternative is an amber card, so it may not turn one into an error.
        """
        if self._budget_stop(state, "looking up that definition") is not None:
            return None
        try:
            interpretation = await self._interpreter.interpret(
                state.question,
                session=session,
                turn_id=state.turn_id,
                policy=state.call_policy(),
            )
        except ReviError:
            return None
        state.record_llm("interpret_question", interpretation.usage, interpretation.failure)
        state.template_hashes["interpret_question@v1"] = interpretation.template_hash
        state.time_stage("interpret")
        return interpretation.definitional

    async def _definitional_outcome(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        answer: DefinitionalAnswer | None = None,
    ) -> TurnOutcome:
        # ZERO probes by construction: the executor is never invoked on this
        # path (asserted by the zero-probe tests with a spy repository).
        if answer is None:
            answer = self._interpreter.definitional_answer(state.question)
        state.time_stage("definitional")
        if not answer.terms:
            # Classifying a turn DEFINITIONAL must not DOWNGRADE the term
            # extractor. The deterministic strip is a fast path, not the
            # only reading: when it comes up empty, the model that just
            # read the sentence is asked which term it was about, exactly
            # as it would be on the NEW_INVESTIGATION route. Refusing
            # without asking is how "what counts as denied dollars here?"
            # came back as "no pack content matched" over a pack that
            # defines denied_dollars.
            recovered = await self._definitional_from_model(session, state)
            if recovered is not None and recovered.terms:
                answer = recovered
        if not answer.terms:
            clarification = ClarificationRequest(
                question=(
                    "I couldn't find a governed definition for that term — could you name "
                    "the code, concept, or metric another way?"
                ),
                reason="no pack content matched the definitional lookup",
            )
            return await self._clarification_outcome(session, state, classified, clarification)
        investigation = self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified)
        await self._investigations.save(investigation, None)
        await self._traces.save(self._trace_record(session, state, classified, definitional=answer))
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
            settings=state.settings,
        )
