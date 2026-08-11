"""Deciding whether to ask a clarification, and what to put in it."""

from __future__ import annotations

from dataclasses import replace

from revi_investigation.application.interpretation import (
    PendingClarification,
)
from revi_investigation.application.submit_turn.clarification import (
    CLARIFICATION_NOT_CONVERGING_REASON,
    _answers_pending,
    _bindings_for,
    _bindings_from_trace,
    _option_window,
    refuted_values,
)
from revi_investigation.application.submit_turn.guards import _AnalysisGuards
from revi_investigation.application.submit_turn.types import (
    _MIN_CALL_BUDGET_USD,
    MAX_CONSECUTIVE_CLARIFICATIONS,
    _TurnState,
)
from revi_investigation.application.validation import (
    PlanClarificationNeeded,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import (
    Investigation,
    InvestigationStatus,
    Session,
)
from revi_investigation.domain.turns import (
    ClarificationBinding,
    ClarificationRequest,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import (
    AddFilterModel,
)
from revi_kernel.errors import (
    ReviError,
)


class _ClarificationPolicy(_AnalysisGuards):
    """When to ask, what may be offered, and what a pending clarification
    means for the turn that answers it."""

    def _spec_for_binding(
        self, session: Session, binding: ClarificationBinding
    ) -> AnalysisSpec | None:
        """The typed investigation an option stands for, or ``None``.

        Built through the same ``from_typed_spec`` disposal a portfolio
        card's drill handle goes through, so a dry run exercises the path
        the accepted option would actually take rather than an approximation
        of it.
        """
        if not binding.metric_ids:
            return None
        try:
            typed = TypedInvestigationSpec(
                metric_ids=list(binding.metric_ids),
                dimensions=list(binding.dimension_ids),
                filters=[
                    AddFilterModel(
                        op="add_filter",
                        dimension=dimension,
                        predicate_op="in" if len(values) > 1 else "eq",
                        values=list(values),
                    )
                    for dimension, values in binding.scope
                    if values
                ],
                window=_option_window(session),
                basis=binding.basis,
            )
            interpreted = self._interpreter.from_typed_spec(
                typed, session=session, turn_id="__option_check__"
            )
        except (ReviError, ValueError, AssertionError):
            return None
        return interpreted.spec

    async def _refuted_in_session(self, session: Session) -> frozenset[str]:
        """Dimension values this session has already proved do not exist.

        Read back off the recorded clarification reasons rather than held
        in memory: a turn is a stateless request and the session may resume
        in another process — the same reason ``_pending_clarification``
        reads the lineage.
        """
        lineage = await self._investigations.lineage(session.id)
        if lineage is None:
            return frozenset()
        reasons: list[str] = []
        for investigation in lineage.investigations:
            if investigation.status is not InvestigationStatus.CLARIFICATION_REQUIRED:
                continue
            for record in await self._traces.for_investigation(investigation.id):
                reason = record.payload.get("clarification_reason")
                if isinstance(reason, str) and reason:
                    reasons.append(reason)
        return refuted_values(reasons)

    async def _recorded_clarification(
        self, investigation_id: str
    ) -> tuple[str | None, tuple[str, ...], tuple[ClarificationBinding, ...]]:
        """The clarification question, options and bindings a turn published."""
        for record in await self._traces.for_investigation(investigation_id):
            question = record.payload.get("clarification")
            if isinstance(question, str) and question:
                raw = record.payload.get("clarification_options") or ()
                options = tuple(str(option) for option in raw)
                return question, options, _bindings_from_trace(record.payload)
        return None, (), ()

    @staticmethod
    def _repeats_pending(state: _TurnState, clarification: ClarificationRequest) -> bool:
        """Is this the question already on screen, word for word?

        The strictest possible test, because it is the one the analyst
        applies: same text, same options. Anything the funnel has already
        changed — a dropped refuted value, a narrowed subject — is a
        different question and gets asked.
        """
        pending = state.pending
        if pending is None:
            return False
        return clarification.question.strip() == pending.question.strip() and tuple(
            clarification.options
        ) == tuple(pending.options)

    @staticmethod
    def _bounded_clarification(
        state: _TurnState, clarification: ClarificationRequest
    ) -> ClarificationRequest:
        """The same clarification, said once more and then differently.

        Interpretation clarifies when the question maps onto no governed
        metric — and there the §2.8 convergence rule must NOT force an
        answer: committing would mean inventing a metric, which is exactly
        the "confident no-issue answer over missing coverage" §2.8 forbids.
        What it can do is stop reissuing near-identical questions. Past the
        allowance the ask becomes a plain statement of the impasse, naming
        the question that started it, so the thread has an exit instead of
        a cycle.
        """
        pending = state.pending
        if pending is None or pending.streak < MAX_CONSECUTIVE_CLARIFICATIONS:
            return clarification
        reason = (clarification.reason or "").strip()
        if reason.startswith(CLARIFICATION_NOT_CONVERGING_REASON):
            # Already bounded on this turn. The funnel reaches this guard
            # from two sites (the clarification-resume path and the funnel
            # proper), and wrapping a second time nests the reason inside
            # itself — "…; original reason: CLARIFICATION_NOT_CONVERGING:
            # 2 consecutive clarifications; original reason: …" — which is
            # what the analyst then reads in the fine print.
            return clarification
        original = pending.original_question or state.question
        trailer = f"; original reason: {reason}" if reason else ""
        return ClarificationRequest(
            question=(
                f"We're going in circles — I've asked {pending.streak} questions about this "
                f"and still can't map it onto anything I measure. Rather than ask again: "
                f"state the whole question in one sentence, naming the metric and the period "
                f"you want. The thread started with {original!r}."
            ),
            options=clarification.options,
            reason=(
                f"{CLARIFICATION_NOT_CONVERGING_REASON}: {pending.streak} consecutive "
                f"clarifications{trailer}"
            ),
            # Bindings are what make an option tappable. A bounded
            # clarification that keeps its options and drops their bindings
            # offers rows that resolve against nothing.
            bindings=clarification.bindings,
        )

    @staticmethod
    def _supersede_pending(
        state: _TurnState, pending: PendingClarification | None
    ) -> str | None:
        """Drop the pending question rather than ask a second one.

        Fires only where the analyst has plainly moved on: a clarification
        is on screen, the new utterance is a self-contained question that
        answers none of its options (``_answers_pending``), and this turn
        was about to raise ANOTHER clarification on top of it. Then the
        honest move is not a third question about which question we are
        answering — it is to answer the one they just asked and say the
        other was dropped.

        Returns the sentence to publish, or ``None`` to clarify as usual.
        A turn with nothing pending is untouched, and so is a genuine
        clarification answer: superseding those would throw away the reply
        the platform asked for.
        """
        if pending is None or _answers_pending(state.question, pending):
            return None
        return (
            "Assumed: this is a new question and it replaces the one I had open. I had a "
            "question of my own on screen and you asked something else, so I dropped mine "
            "rather than ask a second one about which of the two we are doing — ask it "
            "again whenever you want it, and it will run as its own turn."
        )

    @staticmethod
    def _commit_instead_of_clarifying(
        state: _TurnState, pending: PendingClarification | None
    ) -> str | None:
        """Should this turn stop asking and answer? (§2.8)

        A clarification is a dialogue move, not an error — but a dialogue
        that only ever asks is not a dialogue. After
        :data:`MAX_CONSECUTIVE_CLARIFICATIONS` in one thread the engine
        commits to its best reading and answers, stating the assumption
        prominently instead of asking a third time. Returns the assumption
        to publish, or ``None`` to clarify as usual.

        The rule is deliberately narrow. It fires on *ambiguity* loops —
        "which of these did you mean?" — and never converts a refusal into
        a guess: a question that maps onto no governed content still comes
        back as an honest non-answer, because there is nothing there to
        commit to.
        """
        if pending is None or pending.streak < MAX_CONSECUTIVE_CLARIFICATIONS:
            return None
        return (
            f"Assumed: this is a fresh question, asked as written. I had asked "
            f"{pending.streak} clarifying questions in a row without converging, so rather "
            f"than ask again I answered {state.question!r} on my best reading of it. If "
            "that is not what you meant, say what to change and I will re-run it."
        )

    @staticmethod
    def _budget_stop(state: _TurnState, stage_label: str) -> ClarificationRequest | None:
        """Stop the turn when its cost ceiling leaves nothing to spend.

        Called before every model call. The alternative — carrying on with
        a budget too small to complete — buys a provider refusal that looks
        like an outage, and the alternative to *that* is worse: answering
        with fewer model calls and not saying so. A turn that ran out of
        money says it ran out of money, and the ceiling is the analyst's
        own setting, so the recovery is in their hands.
        """
        remaining = state.budget_remaining
        if remaining is None or remaining >= _MIN_CALL_BUDGET_USD:
            return None
        ceiling = state.settings.max_turn_cost_usd
        return ClarificationRequest(
            question=(
                f"This question reached its ${ceiling} cost ceiling while {stage_label}. "
                "Raise the ceiling and ask again, or ask something narrower."
            ),
            options=("Raise the cost ceiling for one question", "Ask a narrower question"),
            reason=(
                f"TURN_BUDGET_EXHAUSTED: spent ${state.llm_spend} of a ${ceiling} "
                f"ceiling for one question, before {stage_label}"
            ),
        )

    async def _resumed_context(
        self, session: Session, state: _TurnState
    ) -> AnalysisSpec | None:
        """The context a clarification resume continues, if there is one.

        A clarification interrupts a THREAD, and the thread's window,
        filters, comparison and cohort are the analyst's, not the
        platform's. They are read off the session's latest analytical
        answer — the one on screen when the question was asked — and
        applied only where the resumed sentence states nothing itself.
        """
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            return None
        if state.lineage_parent is None:
            state.lineage_parent = parent.id
        return parent.spec

    async def _option_answerable(
        self, session: Session, binding: ClarificationBinding
    ) -> bool:
        """Would this option produce a plan the platform can execute?

        The check applies where an option could be WRONG. A
        ``predicate_value`` option is a value read out of the warehouse's
        own domain one moment earlier and a ``date_basis`` option is a
        basis the contract declares and this warehouse binds: re-checking
        the validator's own output against the validator would be a
        tautology, and a failing round trip would drop the twelve real
        payers the analyst needs to choose from.
        """
        if binding.kind in ("predicate_value", "date_basis"):
            return True
        if not binding.metric_ids:
            # A playbook-only option carries no measures to dry-run; the
            # pack either holds the playbook or the option is hollow.
            return binding.playbook_id is not None and (
                self._pack.playbook(binding.playbook_id) is not None
            )
        spec = self._spec_for_binding(session, binding)
        if spec is None:
            return False
        try:
            plan = self._planner.build(
                spec,
                playbook_id=binding.playbook_id if not spec.measures else None,
                window_explicit=False,
            )
            validated = self._validator.validate(plan, spec)
            await self._validator.resolve_predicate_values(
                validated, watermark=session.watermark
            )
        except (PlanClarificationNeeded, ReviError, ValueError, KeyError, AssertionError):
            return False
        return True

    async def _pending_clarification(
        self, session: Session, reply: str | None = None
    ) -> PendingClarification | None:
        """The clarification this session is still waiting on, if any.

        Read back off the lineage rather than held in memory: a turn is a
        stateless request, and the session may be resumed in another
        process. The streak counts *consecutive* clarification turns at the
        tail, which is what §2.8 convergence is measured in.

        ``reply`` is the analyst's current utterance, used only to pick
        BETWEEN outstanding clarifications when more than one is open —
        never to decide whether one is open at all.
        """
        lineage = await self._investigations.lineage(session.id)
        if lineage is None or not lineage.investigations:
            return None
        ordered = sorted(lineage.investigations, key=lambda inv: inv.created_at, reverse=True)
        streak: list[Investigation] = []
        for investigation in ordered:
            if investigation.status is not InvestigationStatus.CLARIFICATION_REQUIRED:
                break
            streak.append(investigation)
        if not streak:
            return None
        # The oldest turn in the run is the analyst's actual question; the
        # ones after it are their replies to us.
        oldest = streak[-1]
        # With more than one clarification outstanding, "which question is
        # this answering" is decided by the OPTIONS, not by recency —
        # deciding by recency spliced a reply onto the question of the
        # OLDEST while parenting it to the NEWEST, in one self-contradicting
        # disclosure sentence. The question text, the option set, the
        # bindings and the parent pointer must all come from a single
        # clarification id — and when the reply is one of the options this
        # platform offered, that id is the one that offered it.
        candidates: list[PendingClarification] = []
        for investigation in streak:
            question, options, bindings = await self._recorded_clarification(investigation.id)
            if question is None:
                continue
            candidates.append(
                PendingClarification(
                    question=question,
                    options=options,
                    original_question=oldest.question,
                    streak=len(streak),
                    investigation_id=investigation.id,
                    bindings=bindings,
                )
            )
        if not candidates:
            return None
        if reply is not None:
            matched = next(
                (c for c in candidates if c.binding_for(reply) is not None), None
            ) or next(
                (c for c in candidates if reply.strip() in c.options), None
            )
            if matched is not None:
                return matched
        return candidates[0]

    async def _validated_options(
        self, session: Session, clarification: ClarificationRequest
    ) -> ClarificationRequest:
        """Drop every option this platform could not actually answer.

        The value-existence guard that produces this platform's refusal —
        *"There is no payer named UnitedHealthcare in this data"*, all
        twelve real values enumerated, ``PREDICATE_VALUE_UNMATCHED`` — was
        never applied to the options the platform OFFERS. Two holes:

        * ``_option_resolves`` checks scope values only against a
          dimension's DECLARED ``value_domain`` and skips the open
          dimensions outright, so "Summit Peak is a facility — walk through
          the medical-necessity denial spike in cardiology at that facility"
          was offered over a warehouse holding six facilities, none of them
          Summit Peak: an option the engine refuses the moment it is
          selected, costing a whole turn to discover.
        * Nothing dry-ran an option against the planner, so an option naming
          a legal metric and an illegal cut for it survived to be tapped.

        Both are closed by running the option the way the turn that accepts
        it will run it: build its spec, plan it, validate it, and resolve
        its predicate values against this watermark. An option that raises
        anything is dropped — including :class:`PlanClarificationNeeded`,
        which is precisely the phantom-value refusal arriving one turn early
        and for free (the observed-value read is cached per watermark, so
        the check costs at most one warehouse round trip per dimension).

        Options with no binding are left alone: a platform-authored recovery
        chip ("Raise the cost ceiling for one question") is not a query and has
        nothing to dry-run. When every *checkable* option fails, the
        question keeps its text and says it has no suggestions rather than
        rendering as a question above a blank row of buttons.
        """
        if not clarification.bindings:
            return clarification
        kept: list[str] = []
        dropped: list[str] = []
        for option in clarification.options:
            binding = clarification.binding_for(option)
            if binding is None or await self._option_answerable(session, binding):
                kept.append(option)
            else:
                dropped.append(option)
        if not dropped:
            return clarification
        surviving = tuple(kept)
        if surviving:
            return replace(
                clarification,
                options=surviving,
                bindings=_bindings_for(clarification, surviving),
                reason=(
                    f"{clarification.reason}; {len(dropped)} option(s) dropped: they name "
                    "content your definitions library, the standard cuts or this data load "
                    "does not hold"
                ),
            )
        return replace(
            clarification,
            question=(
                f"{clarification.question} (I had suggestions here and dropped all "
                f"{len(dropped)} of them: each named a metric, cut or value this data "
                "load does not hold, so tapping one would only have bought "
                "you the same refusal one question later. Say what you want in your own "
                "words, or ask me what exists.)"
            ),
            options=(),
            bindings=(),
            reason=(
                f"{clarification.reason}; CLARIFICATION_OPTIONS_UNANSWERABLE: all "
                f"{len(dropped)} generated options failed value or plan validation"
            ),
        )
