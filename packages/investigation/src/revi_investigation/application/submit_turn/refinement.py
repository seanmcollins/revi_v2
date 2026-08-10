"""Refining an existing investigation, including the paths that run no probes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from revi_investigation.application.findings import (
    FindingsResult,
)
from revi_investigation.application.gestures import drill_suggestion
from revi_investigation.application.interpretation import (
    ClassificationOutcome,
    requested_finding_limit,
)
from revi_investigation.application.llm.schemas import AnyRefinementOperator
from revi_investigation.application.ports import (
    RegisteredReferent,
)
from revi_investigation.application.refinement_llm import (
    ReferentResolution,
    resolve_ordinal_referent,
    resolve_referent_tokens,
    to_domain_operators,
)
from revi_investigation.application.submit_turn.core import _TurnCore
from revi_investigation.application.submit_turn.header import build_context_header
from revi_investigation.application.submit_turn.presentation import (
    _chart_sorts_for,
    _presentation_refusal,
    _reordered,
    presentation_ordering,
)
from revi_investigation.application.submit_turn.types import (
    _KERNEL_ONLY,
    _REFERENT_TOKEN,
    MetaAnswer,
    TurnOutcome,
    _PlanContext,
    _predicate_label,
    _TurnState,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    ContextPin,
)
from revi_investigation.domain.records import (
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
    apply_refinements,
    detect_conflict,
)
from revi_investigation.domain.turns import (
    ClarificationRequest,
    TurnClass,
)
from revi_investigation_contracts.refinements import (
    ExpandModel,
)
from revi_kernel.cohort import CohortRef
from revi_kernel.errors import (
    ContextConflictError,
)
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import ReferentId


class _RefinementTurns(_TurnCore):
    """The refinement path and the turns it can resolve to: kernel-only,
    presentation-only, meta, and context control."""

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
        parent_plan_context = await self._plan_context_of(parent.id)
        playbook_id = parent_plan_context.playbook_id
        window_explicit = parent_plan_context.window_explicit
        entries = await self._referents.list_for_session(session.id)
        resolutions: tuple[ReferentResolution, ...] = ()
        rationale = ""

        # "Show me all twelve" is a COUNT the analyst named, and a count the
        # analyst names is an instruction — on every path, not only on the
        # one that opens an investigation.
        # ``requested_finding_limit`` had exactly one production call site,
        # inside the new-investigation spec build, so a follow-up asking for
        # the rest of a truncated list could not reach it: the model was
        # left to emit an Expand operator, and when it did the kernel path
        # handed back the parent's own three findings. Resolving it here is
        # deterministic and free — no model call decides how many rows the
        # analyst asked to see.
        typed_limit = requested_finding_limit(state.question)
        if dto_ops is None and typed_limit is not None and typed_limit > len(parent.findings):
            dto_ops = (ExpandModel(op="expand", limit=typed_limit),)
            rationale = (
                f"the question asks for {typed_limit} rows and the previous answer published "
                f"{len(parent.findings)}"
            )

        if dto_ops is None:
            # Handles the analyst typed are resolved against the registry
            # BEFORE any model call — they are identifiers this platform
            # minted, not language to be interpreted (§7.6).
            await self._stage(state, "resolve_referents")
            deterministic, unknown = resolve_referent_tokens(state.question, entries)
            if unknown:
                return await self._clarification_outcome(
                    session, state, classified, self._unknown_handle(unknown, entries)
                )
            if not deterministic:
                # An ordinal points at a POSITION, and a position exists
                # exactly when the previous answer published one. Reading
                # it off that answer's own findings is a lookup, not an
                # inference — see ``resolve_ordinal_referent``.
                deterministic = resolve_ordinal_referent(
                    state.question, parent.findings, entries
                )
            if deterministic or not entries:
                # Nothing left for a model to resolve: every handle matched,
                # or nothing has been shown that anaphora could point at.
                resolutions = deterministic
                state.time_stage("resolve_referents")
            else:
                exhausted = self._budget_stop(state, "resolving what you referred to")
                if exhausted is not None:
                    return await self._clarification_outcome(
                        session, state, classified, exhausted
                    )
                resolved = await self._referent_resolver.resolve(
                    state.question, entries, policy=state.call_policy()
                )
                state.record_llm("resolve_referents", resolved.usage, resolved.failure)
                state.template_hashes["resolve_referents@v1"] = resolved.template_hash
                state.time_stage("resolve_referents")
                if resolved.clarification is not None:
                    return await self._clarification_outcome(
                        session, state, classified, resolved.clarification
                    )
                resolutions = resolved.resolutions

            exhausted = self._budget_stop(state, "compiling your follow-up")
            if exhausted is not None:
                return await self._clarification_outcome(session, state, classified, exhausted)

            await self._stage(state, "emit_refinements")
            emission = await self._refinement_emitter.emit(
                state.question,
                context_summary=self._context_lines(parent.spec),
                entries=entries,
                resolutions=resolutions,
                dimension_lines=self._dimension_lines(),
                metric_lines=self._metric_lines(),
                policy=state.call_policy(),
            )
            state.record_llm("emit_refinements", emission.usage, emission.failure)
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
                session,
                state,
                classified,
                parent,
                new_spec,
                domain_ops,
                ops_json,
                parent_plan_context,
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
            parent_evidence_depth=parent_plan_context.evidence_depth,
            turn_class=TurnClass.REFINEMENT,
            parent=parent,
            operators=domain_ops,
            refinement_extra=refinement_extra,
            prelude_warnings=tuple(prelude_warnings),
        )

    @staticmethod
    def _unknown_handle(
        unknown: tuple[str, ...], entries: tuple[RegisteredReferent, ...]
    ) -> ClarificationRequest:
        """A handle this session never published — say so, and list what it did.

        Deterministic resolution can fail honestly, and this is the shape:
        not ``REFERENT_NOT_FOUND`` raised past the analyst as an error, and
        not a model call hoping to find something close. The registry is
        the answer, so the registry is what gets shown.
        """
        named = ", ".join(unknown)
        available = [entry.referent.value for entry in entries]
        return ClarificationRequest(
            question=(
                f"I haven't shown {named} in this session. "
                + (
                    f"What I have shown: {', '.join(available[:12])}. Which did you mean?"
                    if available
                    else "Nothing has been shown yet — what would you like to investigate?"
                )
            ),
            options=tuple(drill_suggestion(value) for value in available[:4]),
            reason=f"REFERENT_NOT_FOUND: {list(unknown)} not in the live registry",
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

    async def _referents_of(
        self, session: Session, investigation_id: str
    ) -> tuple[RegisteredReferent, ...]:
        """The handles one investigation published, for a turn re-serving it.

        A re-served answer that publishes no referents is an answer whose
        rows cannot be drilled and whose findings cannot be cited by handle
        — ``referents 15 → 0`` on a turn whose findings were byte-identical
        to the one before it.
        """
        entries = await self._referents.list_for_session(session.id)
        return tuple(e for e in entries if e.investigation_id == investigation_id)

    async def _kernel_only_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        parent: Investigation,
        new_spec: AnalysisSpec,
        domain_ops: tuple[Refinement, ...],
        ops_json: tuple[Mapping[str, Any], ...],
        plan_context: _PlanContext,
    ) -> TurnOutcome | None:
        """RankBy/Expand within cached, untruncated frames: zero probes
        (§7.9). Returns None to fall back to the full path when the cached
        frames cannot honestly answer (missing column, truncated frame).

        **Warnings are a property of the answer, never of the turn class.**
        This path re-serves the parent's plan, the parent's frames and the
        parent's findings byte-for-byte, and it used to publish them under
        ``warnings=()``: 11 warnings became 0 and 7 became 1 across an
        IDENTICAL ``plan_hash``, and the CSV export then printed "The
        platform attached no caveats to this answer." over the same numbers
        that had carried a suppression bound, a truncation disclosure and an
        alternate-basis note one turn earlier. Re-serving an answer
        re-serves everything that qualified it: the same warnings, the same
        referents, the same chart ordering.
        """
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
                # "Show me all twelve" over a frame that holds twelve is a
                # display-scope request; over an answer that PUBLISHED three
                # it is a re-selection, and re-selection is the findings
                # builder's job. This branch could only ever hand back the
                # parent's own finding list, so a request to widen it was a
                # no-op that cost a model call and changed nothing.
                if op.limit > len(parent.findings):
                    return None
        # A parent's warnings were computed for the plan this turn re-serves,
        # so they ride with it verbatim — plus one line saying that is what
        # happened, so the reuse is legible rather than inferred from a
        # matching hash.
        warnings = (
            *parent.warnings,
            "refinement_reused_plan: this answer re-serves the previous turn's plan "
            f"({parent.plan_hash}) — the same evidence, the same findings and every caveat "
            "that came with them. What changed is the presentation only: "
            + ", ".join(str(op.get("op", "?")) for op in ops_json)
            + ".",
        )
        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=new_spec,
            parent_id=parent.id,
            findings=parent.findings,
            plan_hash=parent.plan_hash,
            warnings=warnings,
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
                warnings=warnings,
                extra={
                    "refinement": {"operators": list(ops_json), "kernel_only": True},
                    # The parent's plan context, because this IS the parent's
                    # plan: publishing ``playbook_id: None`` here made a
                    # re-served answer look like a different, playbook-less
                    # one to every reader of the trace.
                    "plan_context": {
                        "playbook_id": plan_context.playbook_id,
                        "window_explicit": plan_context.window_explicit,
                        "evidence_depth": plan_context.evidence_depth.value,
                        "chart_sorts": [
                            {"frame_id": frame_id, "by": by, "descending": descending}
                            for frame_id, by, descending in plan_context.chart_sorts
                        ],
                    },
                },
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=parent.findings,
            header=build_context_header(new_spec, session, pack=self._pack),
            frames=frame_pairs,
            warnings=warnings,
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            referents=await self._referents_of(session, parent.id),
            watermark_stale=state.watermark_stale,
            benchmarks=self._benchmarks_for(
                FindingsResult(findings=parent.findings, referents=())
            ),
            settings=state.settings,
            chart_sorts=plan_context.chart_sorts,
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
            # The same deterministic honesty the refinement path gives: a
            # handle nobody published is a question, not a 4xx.
            return await self._clarification_outcome(
                session, state, classified, self._unknown_handle((token,), entries)
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
            reconciliation=(
                payload.get("reconciliation") or refinement_payload.get("reconciliation")
            ),
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
        header = (
            build_context_header(cited.spec, session, pack=self._pack)
            if cited is not None
            else None
        )
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
            settings=state.settings,
        )

    async def _context_control_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        parent = await self._latest_investigation(session, analytical=False)
        base_spec = parent.spec if parent is not None else self._fallback_spec(session)
        entries = await self._referents.list_for_session(session.id)

        exhausted = self._budget_stop(state, "applying that context change")
        if exhausted is not None:
            return await self._clarification_outcome(session, state, classified, exhausted)

        await self._stage(state, "emit_refinements")
        emission = await self._refinement_emitter.emit(
            state.question,
            context_summary=self._context_lines(base_spec),
            entries=entries,
            resolutions=(),
            dimension_lines=self._dimension_lines(),
            metric_lines=self._metric_lines(),
            policy=state.call_policy(),
        )
        state.record_llm("emit_refinements", emission.usage, emission.failure)
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
            header=build_context_header(spec, session, pack=self._pack),
            frames=(),
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            settings=state.settings,
        )

    async def _presentation_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        parent: Investigation | None = None,
    ) -> TurnOutcome:
        if parent is None:
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
        # "Show me all twelve" classifies as PRESENTATION_ONLY about as often
        # as it classifies as REFINEMENT — it IS a statement about display
        # scope. When the count named exceeds what the parent published,
        # re-presenting the same three rows is a no-op, so the turn is routed
        # to the expansion it asked for. Deterministic and zero-LLM: the
        # number is read out of the analyst's own sentence.
        typed_limit = requested_finding_limit(state.question)
        if typed_limit is not None and typed_limit > len(parent.findings):
            return await self._refinement_turn(
                session,
                state,
                classified,
                dto_ops=(ExpandModel(op="expand", limit=typed_limit),),
            )
        frames = await self._load_frames(parent)
        plan_context = await self._plan_context_of(parent.id)
        # The parent's answer, re-presented — and therefore the parent's
        # caveats, re-presented. A re-presentation that drops them is a
        # second, cleaner-looking answer over identical numbers.
        #
        # …plus the two notes that say so. The reuse note was composed in the
        # kernel-only branch alone and this path never appended it, so every
        # re-presentation read like a fresh analysis; and
        # REFINEMENT_NOT_APPLIED was registered, titled in the web, and never
        # fired anywhere. A turn could ask for a sort, receive the parent's
        # own order, and be told nothing.
        warnings = (
            # Assumptions lead here too (§2.8). A re-presentation reached by
            # RESUMING a clarification has a decision in it — "I read your
            # reply as an answer to the question above" — and this path
            # published the parent's caveats without it, so the one sentence
            # the reader has to meet before the rows was the one dropped.
            *state.assumptions,
            *parent.warnings,
            "refinement_reused_plan: this answer re-serves the previous turn's plan "
            f"({parent.plan_hash}) — the same evidence, the same findings and every caveat "
            "that came with them. Nothing was re-measured and nothing changed but the "
            "presentation.",
        )
        # An ordering the analyst named over a column the served rows already
        # carry is APPLIED, not merely reported as unapplied: the rows are
        # the parent's, and putting the parent's rows in the order that was
        # asked for is the whole of what a presentation turn is for.
        # ``refinement_not_applied`` is what is owed when the request cannot
        # be resolved against them, and only then.
        served = parent.findings
        chart_sorts = plan_context.chart_sorts
        ordering = presentation_ordering(state.question, served)
        if ordering is not None:
            key, descending = ordering
            served = _reordered(served, key, descending)
            direction = "largest first" if descending else "smallest first"
            column = key or "row label"
            warnings = (
                *warnings,
                f"presentation_applied: these are the previous turn's {len(served)} row(s), "
                f"re-served in the order you asked for — sorted by {column}, {direction}. "
                "Nothing was re-measured: the figures, the handles and every caveat are the "
                "ones the answer above carried.",
            )
            chart_sorts = _chart_sorts_for(frames, key, descending) or chart_sorts
        else:
            # An export asked for in words is refused BY NAME, ahead of the
            # generic "that request was not applied": the reader needs to
            # know where the export is, not only that this turn did not do
            # it.
            #
            # …and it is refused as a REFUSAL. Returning ``outcome: answer``
            # while ``REFINEMENT_NOT_APPLIED`` sits in the same payload is
            # the engine recording that the instruction changed nothing and
            # shipping it as though it had.
            refusal = _presentation_refusal(state.question)
            if refusal is not None:
                return await self._clarification_outcome(
                    session,
                    state,
                    classified,
                    refusal,
                    extra={"presentation_of": parent.id},
                )
        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=parent.spec,
            parent_id=parent.id,
            findings=served,
            frame_refs=parent.frame_refs,
            plan_hash=parent.plan_hash,
            warnings=warnings,
        )
        edge = RefinementEdge(
            parent_id=parent.id, child_id=investigation.id, turn_id=state.turn_id, operators=()
        )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                warnings=warnings,
                extra={
                    "presentation_of": parent.id,
                    "plan_context": {
                        "playbook_id": plan_context.playbook_id,
                        "window_explicit": plan_context.window_explicit,
                        "evidence_depth": plan_context.evidence_depth.value,
                        "chart_sorts": [
                            {"frame_id": frame_id, "by": by, "descending": descending}
                            for frame_id, by, descending in chart_sorts
                        ],
                    },
                },
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=served,
            header=build_context_header(parent.spec, session, pack=self._pack),
            frames=frames,
            warnings=warnings,
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            referents=await self._referents_of(session, parent.id),
            watermark_stale=state.watermark_stale,
            benchmarks=self._benchmarks_for(FindingsResult(findings=served, referents=())),
            settings=state.settings,
            chart_sorts=chart_sorts,
        )
