"""The guards an analysis passes through before it may publish."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from revi_investigation.application.calculation_glue import (
    CalculationResult,
)
from revi_investigation.application.capability_ports import BenchmarkSpec
from revi_investigation.application.comparison import (
    comparison_maturity,
    declared_non_comparabilities,
)
from revi_investigation.application.findings import (
    FindingsResult,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
)
from revi_investigation.application.ports import (
    TurnEvent,
)
from revi_investigation.application.rendering import (
    format_value,
    metric_label,
)
from revi_investigation.application.submit_turn.census import _qualify_every_finding
from revi_investigation.application.submit_turn.clarification import (
    _ASKS_WHICH_MEASURE,
    CLARIFICATION_CONVERGED_REASON,
    CLARIFICATION_MEASURE_SETTLED_REASON,
    _bindings_for,
    _subject_option,
)
from revi_investigation.application.submit_turn.containment import _Reconciliation
from revi_investigation.application.submit_turn.types import _period_phrase, _TurnState
from revi_investigation.application.window_maturity import (
    WindowMaturity,
    covered_months,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import (
    Session,
)
from revi_investigation.domain.turns import (
    ClarificationBinding,
    ClarificationRequest,
)
from revi_kernel.scope import (
    AbsoluteRange,
    TimeWindow,
)


class _AnalysisGuards(_Reconciliation):
    """Maturity, comparability and subject guards — each one a reason a
    number may not be stated the way it was asked for."""

    @staticmethod
    def _guard_comparison_maturity(
        findings: FindingsResult,
        calculation: CalculationResult,
        warnings: list[str],
    ) -> FindingsResult:
        """Apply the adjudication guard to COMPARISONS.

        ``terminal_bucket_censoring`` needs a trend of three-plus buckets
        and runs inside the trend loop, so a two-window prior-period
        comparison — the shape a month-end close actually asks for — was
        never tested for data maturity at all: a July that was 23%
        adjudicated was compared against a June that was 91% and produced a
        confident, un-caveated percentage.

        Two effects, both stated: ``ADJUDICATION_INCOMPLETE`` naming both
        panels and the share between them, and every finding on the turn
        dropped out of ``high`` confidence. Confidence is lowered, never
        raised — a finding already ``qualified`` for another reason keeps
        the stronger caveat.
        """
        verdicts = comparison_maturity(calculation.frames)
        if not verdicts:
            return findings
        warnings.extend(v.warning for v in verdicts)
        return _qualify_every_finding(findings)

    def _guard_declared_comparability(
        self,
        findings: FindingsResult,
        calculation: CalculationResult,
        warnings: list[str],
    ) -> FindingsResult:
        """Apply the adjudication guard to what the CONTRACT declares.

        The two guards above are signals measured off the frame: a panel
        share, a settling curve. Both are structurally blind to a metric
        whose immaturity is not expressed as a record count — and
        ``net_collection_rate``'s denominator is contract-expected DOLLARS,
        so nothing fired while the pack's own caveat, published as a caution
        on the same payload, said in so many words that "two windows of
        unequal maturity are not comparable as levels". A 53.9-point
        collapse that did not happen shipped as "Premise confirmed", at
        ``grade: direct`` and ``confidence: high``.

        So the declaration is read as an integrity input, exactly like the
        measured ones, and it has the same two effects: the reason is
        stated, and nothing on the turn survives at ``high``. Confidence is
        lowered, never raised.

        Scoped to metrics this turn actually DIFFERENCED. A caveat about
        comparing two windows says nothing about reading one, and demoting
        an uncompared level for it would be a caution that is not true.
        """
        verdicts = declared_non_comparabilities(self._pack, calculation.frames)
        if not verdicts:
            return findings
        warnings.extend(v.warning for v in verdicts)
        return _qualify_every_finding(findings)

    async def _plan_window_maturity(
        self, plan: InvestigationPlan, spec: AnalysisSpec
    ) -> dict[AbsoluteRange, WindowMaturity]:
        """The settling verdict for every window this plan actually reads.

        Asking about ``spec.context.window`` and nothing else covers only
        the window the header ANNOUNCES. A playbook probe declares its own
        — a premise probe reading 2026-06-08..2026-08-02 under a turn that
        announced July against June — so the one window that mattered went
        untested, and "your denial spike did not happen" was published over
        a window three quarters of which had not come back.

        Costs one aggregation per (watermark, basis, entity, yardstick):
        the curve is cached inside the service and every window after the
        first is arithmetic over it.
        """
        if self._window_maturity is None:
            return {}
        windows: dict[AbsoluteRange, TimeWindow] = {spec.context.window.range: spec.context.window}
        if spec.context.comparison is not None:
            comparison = spec.context.comparison.window
            windows.setdefault(comparison.range, comparison)
        for node in plan.nodes:
            window = getattr(node.probe, "window", None)
            if isinstance(window, TimeWindow):
                windows.setdefault(window.range, window)
        # …and every whole month inside each of them. A window wider than a
        # month blends settled and settling data and passes as a blend: the
        # playbook's 2026-06-08..2026-08-02 holds a settled June, a July at
        # 26% and two days of August, and only the July inside it explains
        # a 39.5% "fall". The premise verdict reads these (see
        # ``verify_premise``); the turn-level guard above still keys on the
        # window the header announced.
        for window in tuple(windows.values()):
            for month in covered_months(window.range):
                windows.setdefault(month, replace(window, range=month, requested=None))
        out: dict[AbsoluteRange, WindowMaturity] = {}
        for window_range, window in windows.items():
            verdict = await self._window_maturity.verdict_for(
                window,
                grain=spec.context.grain.entity,
                watermark=spec.context.watermark,
            )
            if verdict is not None:
                out[window_range] = verdict
        return out

    async def _guard_window_maturity(
        self,
        findings: FindingsResult,
        spec: AnalysisSpec,
        warnings: list[str],
        state: _TurnState,
        maturity_by_window: Mapping[AbsoluteRange, WindowMaturity] | None = None,
        *,
        window_explicit: bool = True,
    ) -> FindingsResult:
        """Apply the adjudication guard to a SINGLE WINDOW.

        The series rule needs three buckets and a time axis; the comparison
        rule needs a prior panel. A month-end aggregate — "how many dollars
        did we lose to denials in July" — has neither, so the most common
        answer in the product was the one shape no maturity rule could
        reach. Two July denied-dollar figures for one payer landed 6.7x
        apart at ``confidence: high`` with nothing on either card about it.

        Silent where another guard has already spoken: a turn that carries
        ``adjudication_incomplete`` from its series or its comparison does
        not need a third sentence about the same fact.

        NOT silent on a comparison, though. The panel rule owns that axis
        only where it can see it — it sees a ratio's adjudicated
        denominator — so a comparison of an additive money measure, or one
        whose two sides are equally unsettled, otherwise leaves the turn
        with no maturity statement at all. Where the panel rule spoke, the
        warning check above still stands this one down.
        """
        if self._window_maturity is None or not findings.findings:
            return findings
        if any(w.startswith("adjudication_incomplete:") for w in warnings):
            return findings
        verdict = (maturity_by_window or {}).get(spec.context.window.range)
        if verdict is None:
            return findings
        warnings.append(await self._with_settled_reading(verdict, spec, window_explicit))
        await self._events.publish(
            TurnEvent(
                kind="warning",
                turn_id=state.turn_id,
                payload={
                    "code": "ADJUDICATION_INCOMPLETE",
                    "detail": verdict.warning,
                    "window_population": verdict.population,
                    "settled_window_population": verdict.expected,
                },
            )
        )
        return replace(
            findings,
            findings=tuple(
                f if f.confidence != "high" else replace(f, confidence="qualified")
                for f in findings.findings
            ),
        )

    async def _with_settled_reading(
        self, verdict: WindowMaturity, spec: AnalysisSpec, window_explicit: bool
    ) -> str:
        """The maturity caveat, with the settled figure in front of it.

        "What is my denial rate?" led with 12.8% — the July figure this
        product's own trend answer excludes as provisional — and every
        caveat that governed it sat underneath. The reader leaves with the
        number, not with the paragraph.

        So when the period was one this platform CHOSE (no window in the
        question) and that period has not settled, the answer states what
        the last settled month reads before it states the provisional one.
        Measured, never estimated; over the analyst's own scope; and only
        for a question with a single measure, because "the settled reading"
        of four metrics at once is a table, not a sentence.

        Silent — the plain caveat, unchanged — whenever the reading cannot
        be had honestly, and whenever the analyst NAMED the period: asking
        about July and being told about June is answering a different
        question.
        """
        if self._window_maturity is None or window_explicit or len(spec.measures) != 1:
            return verdict.warning
        measure = spec.measures[0].id
        reading = await self._window_maturity.settled_reading(
            spec,
            measure=measure,
            # The §15 threshold this turn's own frames were policed under:
            # a settled reading over a handful of claims is the small cell
            # the rest of the engine withholds.
            suppression_threshold=self._executor.suppression_threshold,
        )
        if reading is None:
            return verdict.warning
        return (
            f"adjudication_incomplete: through {_period_phrase(reading.window)} — the last "
            f"period of this metric that has finished settling — {metric_label(measure)} reads "
            f"{format_value(reading.value, reading.unit)}. The window below "
            f"({verdict.window.start.isoformat()}..{verdict.window.end.isoformat()}) is the one "
            "this platform assumed, and it has NOT finished settling: it holds "
            f"{verdict.population:,} settled record(s) where a window of that length normally "
            f"holds about {verdict.expected:,}, {verdict.share:.1%} of it. Read the settled "
            "figure as the level and the one below as provisional — what has settled is not a "
            "random sample of what has not, so a total there is understated and a rate there is "
            f"skewed. The count is the one {verdict.yardstick!r} declares, and the norm is the "
            "median month of this load."
        )

    def _benchmarks_for(
        self, findings: FindingsResult, warnings: Sequence[str] = ()
    ) -> tuple[BenchmarkSpec, ...]:
        """Benchmark ranges for every metric the turn's findings cite, in
        finding order, deduplicated by benchmark id.

        Withheld entirely when the turn's own window has not finished
        settling. A benchmark comparison is a judgement about a level, and
        a provisional level has no judgement to pass on it yet: "the denial
        rate stands at 12.8% (F1), which sits below the benchmark range of
        19-20 percent" once led an answer about a month that was 26%
        adjudicated, against an insurer-reported cohort whose own caution
        ("plan-reported data with no initial/final distinction") never made
        it into the clause.
        """
        if any(w.startswith("adjudication_incomplete:") for w in warnings):
            return ()
        seen: dict[str, BenchmarkSpec] = {}
        for finding in findings.findings:
            for ref in finding.metric_refs:
                for benchmark in self._pack.benchmarks_for_metric(ref.id):
                    seen.setdefault(benchmark.id, benchmark)
        return tuple(seen.values())

    @staticmethod
    def _lone_binding(
        clarification: ClarificationRequest, *, reduced: bool = False
    ) -> ClarificationBinding | None:
        """The single unambiguous choice a clarification leaves, if any.

        Exactly one option and exactly one binding, and one of two origins:

        * a binding this platform DERIVED from governed content — the
          original rule, and the ``timely_filing_at_risk_dollars`` basis
          case it was written for;
        * a set this platform REDUCED to one — a dry-run once dropped two
          of three model-authored options and the engine asked about the
          survivor anyway. By then the survivor is not a model's
          suggestion: it is the option
          that came through value existence, plan validation and the
          subject check, i.e. content this platform has verified it can
          answer, and the alternatives are gone because this platform
          removed them.

        A model that authors a single option on its own is still asking a
        real question, and it is still asked.
        """
        if len(clarification.options) != 1 or len(clarification.bindings) != 1:
            return None
        binding = clarification.bindings[0]
        if binding.option not in clarification.options:
            return None
        return binding if (binding.deterministic or reduced) else None

    async def _converge_on_subject(
        self,
        session: Session,
        state: _TurnState,
        clarification: ClarificationRequest,
    ) -> ClarificationRequest:
        """Give a second consecutive optionless clarification a way out.

        The documented "after two consecutive clarifications the engine
        commits" rule never fired, so a thread that had already asked two
        questions ended on a third with an empty options array. The §2.8
        objection to committing —
        that it would mean inventing a metric — does not apply once a
        session HAS an answer on screen: its metric and its cut are
        governed content this platform published a moment ago, so
        committing to them invents nothing.

        The commitment is expressed as the one thing the funnel already
        knows how to apply: a single deterministic ``metric_cut`` binding
        over the subject's own ids, which the lone-option rule below then
        applies with the disclosure sentence that already exists.
        """
        if clarification.options or state.pending is None or state.pending.streak < 1:
            return clarification
        parent = await self._latest_investigation(session, analytical=True)
        option = _subject_option(parent)
        if option is None:
            return clarification
        return replace(
            clarification,
            options=(option.option,),
            bindings=(option,),
            reason=(
                f"{clarification.reason}; {CLARIFICATION_CONVERGED_REASON}: "
                f"{state.pending.streak + 1} consecutive clarifications with nothing to "
                "choose from, so this turn commits to the subject already on screen"
            ),
        )

    async def _grammatical_options(
        self, session: Session, clarification: ClarificationRequest
    ) -> ClarificationRequest:
        """Drop every option this engine's own grammar would refuse.

        An option that
        carries a binding is dry-run against the planner
        (:meth:`_option_answerable`); an option that is only a SENTENCE —
        which is every option ``classify_turn`` and ``emit_refinements``
        propose — was published unchecked. So the platform offered "Yes —
        re-group the figure F1 result by denial reason" on a denial-rate
        thread, refused it with ``GRAIN_INCOMPATIBLE`` the moment it was
        tapped, and fired its own circuit breaker two turns later on the
        loop that made.

        The capability predicate is the validator's
        (:meth:`PlanValidationService.unexecutable_cut`) — the same
        ``allows_dimension`` check that produces the refusal — so an option
        cannot survive here and fail there. Options are only ever removed,
        and the set is never emptied by this rule: an imperfect suggestion
        beats a blank row of buttons, and the funnel's own
        "dropped everything" card exists for the case where it is right to
        show nothing.

        A cut is one way an option can dead-end; the other is the PLAYBOOK
        it routes to. "Who is my worst payer?" offered *"Run a full payer
        scorecard across all measures"*, which lands on ``payer_scorecard``,
        which answers by ``pivot``, which this engine refuses at plan time —
        a button the engine had already decided it could not press. That
        check needs no parent, which is why this method does not return
        early without one: a first-turn clarification is exactly where it
        fires.
        """
        if not clarification.options:
            return clarification
        parent = await self._latest_investigation(session, analytical=True)
        metrics = tuple(ref.id for ref in parent.spec.measures) if parent is not None else ()
        kept: list[str] = []
        refused: list[str] = []
        for option in clarification.options:
            cut = self._validator.unexecutable_cut(option, metrics)
            if cut is not None:
                refused.append(f"{cut[0]} is not a cut of {cut[1]}")
                continue
            playbook = self._validator.unanswerable_playbook(option)
            if playbook is not None:
                refused.append(
                    f"the {playbook[0]!r} playbook answers by {playbook[1]!r}, which this "
                    "engine does not implement"
                )
                continue
            kept.append(option)
        if not refused or not kept:
            return clarification
        surviving = tuple(kept)
        return replace(
            clarification,
            options=surviving,
            bindings=_bindings_for(clarification, surviving),
            reason=(
                f"{clarification.reason}; {len(refused)} option(s) dropped before offer: "
                f"{', '.join(refused)}"
            ),
        )

    async def _settled_measure(
        self, session: Session, clarification: ClarificationRequest
    ) -> ClarificationRequest:
        """Never ask which measure of a session that holds exactly one.

        "Why did it go up?" → clarification → the analyst clicked the
        platform's own option → a SECOND clarification asking *which metric
        are you asking about* in a session whose every answer had measured
        one thing over one window → the analyst clicked again → a loop. The
        question was answerable from what was on screen at every step.

        Where the session's subject is unambiguous, the measure question is
        replaced by the subject itself as a deterministic ``metric_cut``
        option — the same commitment :meth:`_converge_on_subject` makes,
        one turn earlier and without waiting for a streak.
        """
        if not _ASKS_WHICH_MEASURE.search(clarification.question):
            return clarification
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None or len(parent.spec.measures) != 1:
            return clarification
        option = _subject_option(parent)
        if option is None:  # pragma: no cover - a single measure always yields one
            return clarification
        return replace(
            clarification,
            question=(
                f"This session has measured one thing — {parent.spec.measures[0].id} over "
                f"{_period_phrase(parent.spec.context.window.range)} — so I have not asked "
                "you which. Say a different metric if you meant one."
            ),
            options=(option.option,),
            bindings=(option,),
            reason=(
                f"{clarification.reason}; {CLARIFICATION_MEASURE_SETTLED_REASON}: the session "
                "holds exactly one measure and one window, so which-measure is not a question"
            ),
        )

    async def _on_subject(
        self, session: Session, clarification: ClarificationRequest, question: str
    ) -> ClarificationRequest:
        """Drop options that walk away from what the session is about.

        Tapping this platform's own "Re-rank by value and show the top
        five" on a providers/denial-rate answer once produced a THIRD
        clarification whose four options were about payers, denied dollars,
        underpayment variance and A/R balance — a different subject
        entirely, three turns into one question.

        The test is deliberately one-sided and cheap. It only ever removes,
        and only when the session HAS an established subject (a completed
        analytical turn). Two ways to be off it:

        * the option's governed METRICS are all different from the ones the
          subject measures — a denial-rate thread offered underpayment
          variance;
        * the option's governed CUT is all different from the subject's,
          and the analyst's own utterance never named it — the providers →
          payers swap, where the question said "re-rank by value" and the
          option answered about a dimension nobody mentioned.

        An option that names no governed content, or that re-cuts along
        something the analyst just asked for, is a legitimate narrowing and
        survives. Where every option is off-subject the whole set is kept
        rather than emptied: an off-subject suggestion beats a blank row of
        buttons only when there is nothing better to show.
        """
        if not clarification.bindings:
            return clarification
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            return clarification
        metrics = {ref.id for ref in parent.spec.measures}
        metrics.update(ref.id for finding in parent.findings for ref in finding.metric_refs)
        cuts = {ref.id for ref in parent.spec.dimensions}
        if not metrics and not cuts:
            return clarification
        asked = question.casefold()

        def on_subject(option: str) -> bool:
            binding = clarification.binding_for(option)
            if binding is None:
                return True
            if binding.metric_ids and metrics and not (set(binding.metric_ids) & metrics):
                return False
            if binding.dimension_ids and cuts and not (set(binding.dimension_ids) & cuts):
                # …unless the analyst named that cut themselves, in which
                # case changing subject is exactly what they asked for.
                return any(
                    part in asked
                    for dim in binding.dimension_ids
                    for part in (dim.replace("_", " "), dim.split("_")[-1])
                )
            return True

        kept = [option for option in clarification.options if on_subject(option)]
        if len(kept) == len(clarification.options):
            return clarification
        if not kept:
            # Every way forward walked away from the thread. Offering them
            # anyway is how a provider question ends up with four payer
            # options; offering nothing is a dead end. The subject itself
            # is the honest third answer.
            fallback = _subject_option(parent)
            if fallback is None:
                return clarification
            return replace(
                clarification,
                options=(fallback.option,),
                bindings=(fallback,),
                reason=(
                    f"{clarification.reason}; all {len(clarification.options)} option(s) "
                    "dropped: every one measured or cut by something this thread is not "
                    "about"
                ),
            )
        surviving = tuple(kept)
        return replace(
            clarification,
            options=surviving,
            bindings=_bindings_for(clarification, surviving),
            reason=(
                f"{clarification.reason}; "
                f"{len(clarification.options) - len(kept)} option(s) dropped: they measure "
                "something this thread is not about"
            ),
        )
