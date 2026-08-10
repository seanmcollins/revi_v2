"""Building one finding of each kind, and the small decisions each needs."""

from __future__ import annotations

from decimal import Decimal

from revi_calculation_contracts.contract import SignConvention
from revi_investigation.application.calculation_glue import (
    CalculationResult,
    EmptinessFact,
    EmptinessKind,
)
from revi_investigation.application.capability_ports import PackPort, PlaybookSpec
from revi_investigation.application.comparison import (
    ComparisonRendering,
)
from revi_investigation.application.execution import (
    BoundedCell,
    SuppressionCensus,
    bound_index,
    suppression_census,
)
from revi_investigation.application.findings.bounds import (
    _QUALIFIED_GRADES,
    _bound_values,
    _is_additive,
    bound_text,
)
from revi_investigation.application.findings.premise import (
    PremiseCheck,
    _asserted_claim,
    _unverifiable_reason,
    movement_forms,
    premise_verdict_sentence,
)
from revi_investigation.application.findings.shapes import (
    ConcentrationShape,
    MovementShape,
    ScalarShape,
    TerminalCensoring,
    TrendShape,
    _as_int,
    _bucket_noun,
    _bucket_text,
    _dimension_columns,
    _direction,
    as_number,
)
from revi_investigation.application.findings.windows import (
    _measured_range,
    _period_paren,
    _period_phrase,
    _window_values,
    _with_window_note,
)
from revi_investigation.application.gestures import suggested_refinements_for
from revi_investigation.application.planning import InvestigationPlan
from revi_investigation.application.ports import ReferentRegistryStore, RegisteredReferent
from revi_investigation.application.rendering import (
    format_value,
    magnitude,
    magnitude_money,
    measure_phrase,
    metric_label,
    ratio_pct,
    render_row_label,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    AskedMagnitude,
    adverse_delta_sign,
    descending_for_order,
    wanted_delta_sign,
)
from revi_investigation.domain.records import Finding
from revi_kernel.cohort import CohortDefinition
from revi_kernel.filters import Predicate, PredicateOp, Scalar, and_merge
from revi_kernel.frame import EvidenceFrame
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import (
    DimensionRef,
    EntityGrain,
    MetricRef,
    ReferentId,
    ReferentKind,
)


class _FindingBuilders:
    """One builder per shape a finding can take.

    Declaration-only state: :class:`EvaluateFindingsService` assigns these in
    its constructor. Split from the stage itself so the shape-by-shape
    building can be read without the selection logic on top of it.
    """

    _registry: ReferentRegistryStore
    _top_n: int
    _threshold: int | None


    def _limit(self, spec: AnalysisSpec) -> int:
        """How many findings this turn may publish.

        ``top_n`` is a constructor default; ``spec.limit`` is what the
        analyst asked for. Reading only the default means
        ``Expand(limit=12)`` — parsed perfectly from "show me all twelve
        payers, not just three" — changes nothing. The analyst's own limit
        is not a suggestion.
        """
        return spec.limit if spec.limit is not None and spec.limit > 0 else self._top_n


    def _bounds(self, frame: EvidenceFrame) -> dict[int, dict[str, BoundedCell]]:
        """Which of this frame's cells carry a ceiling instead of a value."""
        if self._threshold is None:
            return {}
        return bound_index(frame, self._threshold)


    def _census(self, frame: EvidenceFrame) -> SuppressionCensus | None:
        if self._threshold is None:
            return None
        return suppression_census(frame, self._threshold)


    @staticmethod
    def _no_findings(
        plan: InvestigationPlan, calculation: CalculationResult, why: str
    ) -> EmptinessFact:
        """The emptiness fact for a turn whose frames could publish nothing.

        Names the frame that was looked at, so a reader can go and see the
        rows the answer declined to conclude from.
        """
        candidate = next(
            (
                frame_id
                for frame_id, frame in calculation.frames
                if frame.rows and _dimension_columns(frame)
            ),
            None,
        )
        rows = sum(len(frame.rows) for _, frame in calculation.frames)
        return EmptinessFact(
            kind=EmptinessKind.NO_FINDINGS,
            frame_id=candidate,
            detail=(
                f"{rows} row(s) were retrieved across {len(calculation.frames)} frame(s) and "
                f"no finding could be published from them ({why})"
            ),
        )


    # ---------------------------------------------------------- direction

    def _select_directional(
        self,
        rows: tuple[tuple[Scalar, ...], ...],
        idx_delta: int,
        spec: AnalysisSpec,
        pack: PackPort,
        money_measure: str,
    ) -> tuple[list[tuple[Scalar, ...]], tuple[str, ...], list[tuple[Scalar, ...]]]:
        """Order (and, when a direction was asked, restrict) the compare rows.

        Without a direction this is the old rule: rank by delta ascending,
        biggest declines of a higher-is-good measure first — the right
        default for "what moved?".

        With one it is not a default any more, it is the question. Running
        the default over "which payers had the biggest INCREASE in denials"
        publishes the three biggest *decreases*, narrated as improvements: a
        confident, well-evidenced, exactly-backwards answer. So rows whose
        delta has the wrong sign are not eligible to be the answer at all,
        and the remaining ones are ordered by the extremity the analyst
        phrased.

        When nothing moved the asked-for way the answer says that FIRST and
        then shows the opposite as context — the honest shape of an empty
        direction-matched set. Rows with a NULL delta are never eligible
        either way: a suppressed movement is not a movement.

        The third return is the cells the direction filter REMOVED, in the
        frame's own order. They are not the answer to what was asked and
        they are not nothing — "which payers improved?" asked directly
        returns exactly the cells an expand would otherwise drop silently —
        so the caller publishes them, labelled, and counts them.
        """
        contract = pack.metric(money_measure)
        sign = contract.sign if contract is not None else SignConvention.NEUTRAL
        wanted = wanted_delta_sign(spec.direction, sign)
        biggest_first = spec.magnitude is not AskedMagnitude.SMALLEST

        def ordered(
            candidates: list[tuple[Scalar, ...]], descending: bool
        ) -> list[tuple[Scalar, ...]]:
            return sorted(
                candidates,
                key=lambda row: (
                    as_number(row[idx_delta]) is None,  # NULL deltas last
                    -(as_number(row[idx_delta]) or 0)
                    if descending
                    else (as_number(row[idx_delta]) or 0),
                ),
            )

        if wanted is None:
            # No direction was asked. An ORDER may still have been ("best to
            # worst"), and it wins: it is the analyst's own instruction about
            # which end to show first, resolved against the metric's sign.
            asked_order = descending_for_order(spec.order, sign)
            if asked_order is not None:
                return ordered(list(rows), descending=asked_order), (), []
            # Otherwise the default is not "ascending" — it is *worst
            # first*, read off the contract's own sign convention: a
            # higher-is-bad measure's worst movement is a rise. Ascending
            # was only ever right because the first metrics through here
            # were higher-is-good dollars, and it published the biggest
            # improvements of a higher-is-bad metric as its headline.
            adverse = adverse_delta_sign(sign)
            return ordered(list(rows), descending=adverse is not None and adverse > 0), (), []

        matched = [
            row
            for row in rows
            if (value := as_number(row[idx_delta])) is not None
            and (value > 0 if wanted > 0 else value < 0)
        ]
        assert spec.direction is not None
        movement = "rose" if wanted > 0 else "fell"
        if matched:
            removed = [
                row
                for row in rows
                if (value := as_number(row[idx_delta])) is not None
                and not (value > 0 if wanted > 0 else value < 0)
            ]
            return (
                ordered(matched, descending=(wanted > 0) == biggest_first),
                (),
                ordered(removed, descending=not (wanted > 0)),
            )
        warning = (
            f"direction_unmatched: nothing {movement} — no cell's "
            f"{metric_label(money_measure)} moved the way {spec.direction.value!r} asks about "
            "over this window. The movements below are the opposite direction, shown as "
            "context, not as an answer to what was asked."
        )
        return ordered(list(rows), descending=not (wanted > 0)), (warning,), []


    def _build_premise_finding(
        self,
        referent_value: str,
        premise: PremiseCheck,
        spec: AnalysisSpec,
        comparison: ComparisonRendering | None,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
    ) -> tuple[Finding, RegisteredReferent]:
        """The refutation as a first-class finding, not a footnote.

        It carries the same certified values every other finding does —
        level, prior, delta, pct — because the reader's next question is
        "by how much, then?", and a correction that cannot answer that is
        just a contradiction.
        """
        assert spec.direction is not None
        sentence = premise_verdict_sentence(premise, spec, comparison=comparison)
        claim_noun, _ = _asserted_claim(spec)
        if premise.unverifiable:
            # A fourth title, because there are four outcomes. "Premise
            # confirmed" over two ceilings, an immature panel or a size
            # nobody parsed asserts what cannot be checked; "Premise not
            # supported" would be the opposite error.
            title = f"Premise cannot be verified: {sentence}"
            statement = (
                f"{sentence}. Nothing below may be called {claim_noun} or offered as evidence "
                "against it: the cells that follow are the composition of a movement this "
                "answer cannot certify."
            )
        elif premise.magnitude_short:
            # A third title, because there are three outcomes. "Premise
            # confirmed" over a movement 27% short of the claim overstates
            # it; "Premise not supported" over a real 72.6% rise would be
            # the opposite error.
            title = f"Premise partly supported: {sentence}"
            statement = (
                f"{sentence}. The movement is real and it is not the movement the question "
                f"names, so nothing here or below may be called {claim_noun}: the cells that "
                "follow compose a movement that fell short of the claim."
            )
        elif premise.holds:
            title = f"Premise confirmed: {sentence}"
            statement = (
                f"{sentence}. That is the movement the question takes as given, measured on the "
                "population it names, so the cells below are its composition rather than a "
                "separate claim."
            )
        else:
            title = f"Premise not supported: {sentence}"
            statement = (
                f"{sentence}. The population the question names does not show the movement it "
                "assumes, so the movements below are the exceptions inside it rather than the "
                "story."
            )
        statement = _with_window_note(statement, spec, premise.window)
        values: list[tuple[str, Scalar]] = [
            (premise.measure, premise.current),
            (f"{premise.measure}__prior", premise.prior),
            (
                f"{premise.measure}__delta",
                int(premise.delta) if premise.is_money else premise.delta,
            ),
            ("pct_change", premise.pct),
            # The verdict as data, so a client never has to read the title
            # to know whether the question's own assumption survived.
            ("premise_holds", premise.holds),
            # …and how it landed against the SIZE that was asserted, so
            # "confirmed" can never sit beside an asserted_multiple of 2.0
            # and a pct_change of 0.726.
            ("premise_magnitude", premise.magnitude.value),
            # …and WHY nothing could be concluded, when nothing could.
            # ``premise_holds: false`` on its own reads as a refutation, and
            # an unverifiable premise is not a refuted one — a client that
            # renders the two the same way publishes "it did not happen"
            # over a movement between two ceilings.
            ("premise_unverifiable", premise.unverifiable),
        ]
        if premise.unverifiable:
            values.append(("premise_unverifiable_reason", _unverifiable_reason(premise)))
        if premise.not_comparable is not None:
            # The declaration as data, so a client (and the invariant test)
            # can branch on it without parsing prose, and so the metric that
            # carries it is named rather than inferred.
            values.append(("premise_not_comparable_metric", premise.not_comparable.measure))
        if premise.current_bound is not None:
            values.extend(_bound_values(premise.measure, premise.current_bound))
        if premise.prior_bound is not None:
            values.append((f"{premise.measure}__prior__is_bound", True))
            values.append(
                (f"{premise.measure}__prior__bound_population", premise.prior_bound.population)
            )
        if premise.asserted_multiple is not None:
            values.append(("asserted_multiple", premise.asserted_multiple))
            # The question's own word for the size it asserted, published as
            # data so the narrative validator can forbid it when the verdict
            # is short-of. "Roughly doubled" written under a finding that
            # says the movement fell 27 points short of doubling contradicts
            # the answer's own first claim.
            values.append(("premise_asserted_verb", _asserted_claim(spec)[1]))
        if premise.actual_multiple is not None:
            values.append(("actual_multiple", premise.actual_multiple))
        values.extend(_window_values(premise.measure, spec, premise.window))
        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(premise.measure),),
            values=tuple(values),
            grade=premise.frame.evidence_grade,
            # A refutation is not a recoverable opportunity: ranking it in a
            # worklist would put "this did not happen" on somebody's queue.
            impact_cents=None,
            # A verdict that could not test the claim is not a high-
            # confidence verdict, whatever the arithmetic behind it looks
            # like.
            confidence="qualified" if premise.unverifiable else "high",
            suggested_refinements=suggested_refinements_for(referent_value),
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            cohort_definition=self._cohort_definition(
                premise.frame.rows[0], premise.frame, (), spec
            ),
            finding=finding,
        )
        return finding, registered


    # ------------------------------------------------------------- building

    def _requires_qualification(
        self, grade: EvidenceGrade, pack: PackPort, playbook: PlaybookSpec | None
    ) -> bool:
        if grade in _QUALIFIED_GRADES:
            return True
        if playbook is not None:
            for policy_id in playbook.conclusion_policies:
                policy = pack.conclusion_policy(policy_id)
                if policy is not None and grade.strength < policy.required_grade.strength:
                    return True
        return False


    def _cohort_definition(
        self,
        row: tuple[Scalar, ...],
        frame: EvidenceFrame,
        dimension_columns: tuple[str, ...],
        spec: AnalysisSpec,
    ) -> CohortDefinition:
        """Drillable cohort: CLAIM entity, current scope narrowed to this
        row's dimension values, over the analysis window."""
        predicates = tuple(
            Predicate(DimensionRef(dim), PredicateOp.EQ, (row[frame.schema.index_of(dim)],))
            for dim in dimension_columns
        )
        return CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=and_merge(spec.context.effective_scope(), *predicates),
            window=spec.context.window,
        )


    @staticmethod
    def _row_label(
        row: tuple[Scalar, ...],
        frame: EvidenceFrame,
        dimension_columns: tuple[str, ...],
        pack: PackPort,
    ) -> str:
        """Dimension values as a label, with remittance codes rendered as
        ``GROUP / CARC — Title`` rather than as bare integers."""
        values = {dim: row[frame.schema.index_of(dim)] for dim in dimension_columns}
        return render_row_label(pack, dimension_columns, values)


    @staticmethod
    def _single_dim(
        row: tuple[Scalar, ...], frame: EvidenceFrame, dimension_columns: tuple[str, ...]
    ) -> tuple[str, str] | None:
        if len(dimension_columns) != 1:
            return None
        name = dimension_columns[0]
        return (name, str(row[frame.schema.index_of(name)]))


    def _build_finding(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        shape: MovementShape,
        spec: AnalysisSpec,
        comparison: ComparisonRendering | None,
        qualified: bool,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
        bound: BoundedCell | None = None,
        counter_direction: bool = False,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        measure = shape.measure
        current = row[schema.index_of(measure)]
        prior = row[schema.index_of(f"{measure}__prior")]
        delta = as_number(row[schema.index_of(f"{measure}__delta")])
        pct = row[schema.index_of(f"{measure}__pct_change")]
        assert delta is not None  # caller filtered NULL deltas

        label = self._row_label(row, shape.frame, shape.dimension_columns, pack)
        measure_label = metric_label(measure)
        direction = "down" if delta < 0 else "up"
        # In the contract's own unit: dollars for money, percentage POINTS
        # for a rate. Rendering a rate's movement through the money path is
        # how "denial rate up $0.01" gets published.
        amount = magnitude(delta, shape.unit)
        period_phrase = comparison.phrase if comparison is not None else "vs prior period"
        # A comparison over a *materially* different-length window is not a
        # delta the platform will stand behind for an additive measure: the
        # phrase says so, the impact is withheld, and the confidence is
        # qualified. A rate is length-invariant and carries no such caveat
        # (see comparison.py).
        mismatched = (
            comparison is not None
            and comparison.material_length_mismatch
            and _is_additive(shape.unit)
        )
        current_text = bound_text(current, shape.unit, bounded=bound is not None)
        title = f"{label} {measure_label} {direction} {amount} {period_phrase}"
        # Both readings, each named. "up 0.8 points, a 11.5% relative
        # change" — never the bare "11.5%" beside "7.1% → 7.9%", which reads
        # as points.
        movement = movement_forms(delta, pct, shape.unit, bounded=bound is not None)
        statement = (
            f"{label}: {measure_label} moved from {format_value(prior, shape.unit)} to "
            f"{current_text} ({direction} {movement} {period_phrase})."
        )
        if bound is not None:
            # A movement computed off a bounded endpoint is a movement
            # toward a ceiling, not a measured one. Said in the title as
            # well as the statement, because the title travels alone.
            title = f"{label} {measure_label} at most {current_text} {period_phrase}"
            statement = (
                f"{statement[:-1]}. The current side is an UPPER BOUND: its numerator was "
                f"suppressed over a population of {bound.population:,}, so the movement is at "
                "most this large and may be smaller."
            )
        if counter_direction:
            # Published because the analyst named a count that the
            # direction-matched set could not fill, and labelled in both
            # fields because the title travels alone: this cell is IN the
            # population and is not an answer to what was asked.
            assert spec.direction is not None
            title = f"{title} — moved the other way"
            statement = (
                f"{statement[:-1]}. This cell moved the OPPOSITE way to the "
                f"{spec.direction.value!r} the question asks about; it is published because you "
                "asked for the whole set, not as an instance of what was asked."
            )

        statement = _with_window_note(statement, spec, shape.window)

        delta_value: Scalar = int(delta) if shape.is_money else delta
        values: list[tuple[str, Scalar]] = [
            ("current_cents" if shape.is_money else measure, current),
            ("prior_cents" if shape.is_money else f"{measure}__prior", prior),
            ("delta_cents" if shape.is_money else f"{measure}__delta", delta_value),
            ("pct_change", pct),
        ]
        values.extend(_bound_values(measure, bound))
        values.extend(_window_values(measure, spec, shape.window))
        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # A rate is not dollars: an impact is a figure this platform is
            # willing to rank, sum and put in a worklist, and a percentage
            # point is none of those. Nor is a bounded movement: a ceiling
            # is not a recoverable dollar figure.
            impact_cents=(
                int(delta) if (shape.is_money and not mismatched and bound is None) else None
            ),
            confidence=(
                "qualified" if (qualified or mismatched or bound is not None) else "high"
            ),
            suggested_refinements=suggested_refinements_for(referent_value),
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            cohort_definition=self._cohort_definition(
                row, shape.frame, shape.dimension_columns, spec
            ),
            finding=finding,
            dimension_value=self._single_dim(row, shape.frame, shape.dimension_columns),
        )
        return finding, registered


    def _dimension_value_referent(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        frame: EvidenceFrame,
        dimension_columns: tuple[str, ...],
        spec: AnalysisSpec,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
    ) -> RegisteredReferent:
        return RegisteredReferent(
            referent=ReferentId(value=referent_value, kind=ReferentKind.DIMENSION_VALUE),
            session_id=session_id,
            investigation_id=investigation_id,
            label=self._row_label(row, frame, dimension_columns, pack),
            cohort_definition=self._cohort_definition(row, frame, dimension_columns, spec),
            dimension_value=self._single_dim(row, frame, dimension_columns),
        )


    def _build_scalar_finding(
        self,
        referent_value: str,
        shape: ScalarShape,
        value: Scalar,
        spec: AnalysisSpec,
        comparison: ComparisonRendering | None,
        qualified: bool,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
        bound: BoundedCell | None = None,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        row = shape.frame.rows[0]
        label = metric_label(shape.measure)
        current_text = bound_text(value, shape.unit, bounded=bound is not None)
        period_text = _period_phrase(spec, pack, shape.measure, shape.frame, shape.window)
        period_paren = _period_paren(spec, pack, shape.measure, shape.frame, shape.window)

        values: list[tuple[str, Scalar]] = [(shape.measure, value)]
        prior: Scalar = None
        delta: Scalar = None
        pct: Scalar = None
        if shape.compared:
            assert shape.prior_column is not None and shape.delta_column is not None
            prior = row[schema.index_of(shape.prior_column)]
            delta = row[schema.index_of(shape.delta_column)]
            pct = row[schema.index_of(shape.pct_column)] if shape.pct_column else None
            values.extend(
                [(f"{shape.measure}__prior", prior), (f"{shape.measure}__delta", delta)]
            )
            if pct is not None:
                values.append(("pct_change", pct))

        # Same per-unit rule as the movement path: only an additive measure
        # is distorted by a length mismatch, and only a material one.
        mismatched = (
            comparison is not None
            and comparison.material_length_mismatch
            and _is_additive(shape.unit)
        )
        movement = _direction(delta)
        # Both sides are stated in the contract's unit rather than the delta
        # being rendered in it: "up 3.2%" on a *rate* is ambiguous between
        # relative change and percentage points, and there is no reason to
        # publish an ambiguity when "5.2%, up from 4.9%" is available. A
        # delta that is not a number (a suppressed prior cell) publishes the
        # level alone — there is no movement to name.
        if prior is not None and movement is not None:
            period_phrase = comparison.phrase if comparison is not None else "vs prior period"
            prior_text = format_value(prior, shape.unit)
            title = f"{label}: {current_text}, {movement} {prior_text} {period_phrase}"
            # Both readings, each named. "(39.6% change)" printed under
            # "12.8%, up from 9.1%" reads as 39.6 POINTS — the ambiguity the
            # title above already refuses to publish, re-introduced one line
            # down.
            # The direction is already in ``movement`` ("up from"), so the
            # parenthesis carries only the SIZE — in both of its readings.
            moved = (
                f" ({movement_forms(delta, pct, shape.unit, bounded=bound is not None)})"
                if delta is not None
                else ""
            )
            statement = (
                f"{label} is {current_text} {period_text}, {movement} {prior_text} "
                f"{period_phrase}{moved}."
            )
        else:
            title = f"{label}: {current_text} {period_paren}"
            statement = f"{label} is {current_text} {period_text}."
        if bound is not None:
            statement = (
                f"{statement[:-1]} — an UPPER BOUND, not a measurement: the numerator was "
                f"suppressed over a population of {bound.population:,}, so the true figure is "
                "at or below this one."
            )
        statement = _with_window_note(statement, spec, shape.window)
        values.extend(_bound_values(shape.measure, bound))
        values.extend(_window_values(shape.measure, spec, shape.window))

        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(shape.measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # A rate is not dollars, and a length-mismatched difference is
            # not an impact — the same two rules the other shapes apply.
            impact_cents=(
                _as_int(delta)
                if (shape.is_money and not mismatched and bound is None)
                else None
            ),
            confidence=(
                "qualified" if (qualified or mismatched or bound is not None) else "high"
            ),
            suggested_refinements=suggested_refinements_for(referent_value),
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            # No dimension values to pin: the drillable cohort is the
            # answer's own population over the analysis window.
            cohort_definition=self._cohort_definition(row, shape.frame, (), spec),
            finding=finding,
        )
        return finding, registered


    def _build_trend_finding(
        self,
        referent_value: str,
        shape: TrendShape,
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        censoring: TerminalCensoring | None = None,
    ) -> Finding | None:
        """One series, stated as a series: ends first, then its extremes.

        A right-censored terminal bucket never becomes the "end". Letting
        it publishes ``7.3% → 12.8% (up 5.5 points)`` at grade ``direct``,
        confidence ``high``, with a benchmark attached, over a July point
        computed on 22.9% of July's claims — the fastest-adjudicating
        subset, which skews heavily to denials. The series is stated to its
        last SETTLED bucket and the provisional point is named as
        provisional.
        """
        schema = shape.frame.schema
        idx_bucket = schema.index_of(shape.bucket_column)
        idx_value = schema.index_of(shape.measure)
        # A trend has to ask which of its points are ceilings. Otherwise the
        # chart draws them as bounds while the title above states
        # "7.5% → 9.0% (up 1.5 points)" — a measured-looking movement between
        # two ceilings — and the export, which prints the title verbatim,
        # inherits the claim.
        row_bounds = self._bounds(shape.frame)
        points = [
            (row[idx_bucket], value, row_bounds.get(index, {}).get(shape.measure))
            for index, row in enumerate(shape.frame.rows)
            if (value := as_number(row[idx_value])) is not None
        ]
        if len(points) < 2:
            return None  # a series of one is not a trend, and nor is silence
        points.sort(key=lambda point: str(point[0]))
        provisional = points[-1] if censoring is not None else None
        settled = points[:-1] if (censoring is not None and len(points) > 2) else points
        (first_bucket, first_value, first_bound) = settled[0]
        (last_bucket, last_value, last_bound) = settled[-1]
        low = min(settled, key=lambda point: point[1])
        high = max(settled, key=lambda point: point[1])
        delta = last_value - first_value
        label = metric_label(shape.measure)
        window = _measured_range(spec, shape.window)
        noun = _bucket_noun(shape.frame, shape.bucket_column)
        direction = "down" if delta < 0 else ("up" if delta > 0 else "flat")
        bounded_ends = first_bound is not None or last_bound is not None
        if bounded_ends:
            # A movement between ceilings is not a movement. The direction
            # word survives (the ceilings really did move), the SIZE does
            # not: the true endpoints sit somewhere at or below the two
            # bounds, so their difference is unknown.
            movement = "movement between ceilings — size unknown"
        else:
            movement = (
                f"{direction} {magnitude(delta, shape.unit)}"
                if delta
                else "unchanged end to end"
            )
        first_text = bound_text(first_value, shape.unit, bounded=first_bound is not None)
        last_text = bound_text(last_value, shape.unit, bounded=last_bound is not None)
        title = (
            f"{label} by {noun}, "
            f"{window.start.isoformat()}..{window.end.isoformat()}: "
            f"{first_text} → {last_text} ({movement})"
        )
        statement = (
            f"{label} ran from {first_text} in "
            f"{_bucket_text(first_bucket, noun)} to {last_text} in "
            f"{_bucket_text(last_bucket, noun)} ({movement} over {len(settled)} {noun}s); highest "
            f"{bound_text(high[1], shape.unit, bounded=high[2] is not None)} in "
            f"{_bucket_text(high[0], noun)}, lowest "
            f"{bound_text(low[1], shape.unit, bounded=low[2] is not None)} in "
            f"{_bucket_text(low[0], noun)}."
        )
        statement = _with_window_note(statement, spec, shape.window)
        values: list[tuple[str, Scalar]] = [
            ("first", first_value),
            ("last", last_value),
            ("delta", int(delta) if shape.is_money else delta),
            ("high", high[1]),
            ("low", low[1]),
            ("periods", len(settled)),
        ]
        # The bound, as named values rather than as prose, so a card, a CSV
        # and an emailed export can all ask "is this a measurement?" of a
        # trend without re-deriving the suppression policy from the chart.
        #
        # ``__is_bound`` is the scalar contract's own name and means what it
        # means everywhere: this figure is not a measurement. There is
        # deliberately no series-level ``__bound`` — a series has no single
        # ceiling, and publishing one endpoint's as the trend's would be a
        # third answer to a question the two per-end names already answer.
        if bounded_ends:
            values.append((f"{shape.measure}__is_bound", True))
        for side, bound in (("first", first_bound), ("last", last_bound)):
            if bound is not None:
                values.extend(
                    [
                        (f"{shape.measure}__{side}__is_bound", True),
                        (f"{shape.measure}__{side}__bound", bound.bound),
                        (f"{shape.measure}__{side}__bound_population", bound.population),
                    ]
                )
        if provisional is not None and censoring is not None:
            # The point is published — dropping it would hide the newest
            # data the analyst asked for — and it is published as
            # provisional, outside the movement the sentence claims.
            statement = (
                f"{statement} The {_bucket_text(provisional[0], noun)} point "
                f"({bound_text(provisional[1], shape.unit, bounded=provisional[2] is not None)}) "
                f"is PROVISIONAL and is excluded from that movement: {censoring.reason}"
            )
            title = f"{title}; {_bucket_text(provisional[0], noun)} provisional"
            values.extend(
                [
                    ("provisional_bucket", str(provisional[0])),
                    ("provisional_value", provisional[1]),
                    ("terminal_provisional", True),
                ]
            )
        values.extend(_window_values(shape.measure, spec, shape.window))
        return Finding(
            referent=ReferentId(value=referent_value, kind=ReferentKind.FINDING),
            title=title,
            statement=statement,
            metric_refs=(MetricRef(shape.measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # End-to-end movement of a series is not a recoverable dollar
            # figure, whatever the unit: it is a description, not a target.
            impact_cents=None,
            confidence=(
                "qualified"
                if (
                    censoring is not None
                    # A movement measured between two ceilings is not a
                    # measurement, whatever the grade of the frame it came
                    # from.
                    or bounded_ends
                    or self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
                )
                else "high"
            ),
            suggested_refinements=suggested_refinements_for(referent_value),
        )


    def _build_concentration_finding(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        shape: ConcentrationShape,
        spec: AnalysisSpec,
        qualified: bool,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
        display_rank: int | None = None,
        measured_total: int = 0,
        bound: BoundedCell | None = None,
        urgency_position: tuple[int, int] | None = None,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        value = row[schema.index_of(shape.measure)]
        rank = _as_int(row[schema.index_of(shape.rank_column)])
        share = row[schema.index_of(shape.share_column)] if shape.share_column else None
        label = self._row_label(row, shape.frame, shape.dimension_columns, pack)
        measure_label = metric_label(shape.measure)
        period_text = _period_phrase(spec, pack, shape.measure, shape.frame, shape.window)

        amount = _as_int(value)
        # The unit is the metric contract's, carried on the frame column —
        # a ratio renders as a percentage and a count as a count, instead of
        # falling through to a Python repr.
        magnitude = (
            magnitude_money(amount)
            if shape.is_money and amount is not None
            else format_value(value, shape.unit)
        )
        # share_of_total divides by the VISIBLE total, so with suppressed
        # cells "% of total" would overstate the concentration. Say which
        # total it is rather than quietly meaning a different one.
        share_basis = "visible total" if shape.frame.suppressed_cells else "total"
        share_text = (
            f" ({ratio_pct(share)} of {share_basis})" if isinstance(share, Decimal) else ""
        )
        # "Ranks #1" is meaningless until the sentence says which end.
        # Otherwise "rank payers best to worst" returns the worst payer
        # first and narrates it "ranks first at a 29.5% denial rate" — the
        # ordering the analyst asked for neither honored nor stated. When an
        # order WAS asked for, the rows now arrive in it (planning resolves
        # "best" against the contract's sign) and the sentence names it; when
        # none was, nothing is claimed about what first means.
        order_text = f" ({spec.order.phrase}, as asked)" if spec.order is not None else ""
        # The figure and the measure name, said with the unit ONCE. "179.5
        # days" beside a measure whose display name is "days in ar"
        # otherwise renders "Atlas Commercial: 179.5 days days in ar"; the
        # collision is invisible to an f-string and visible to
        # :func:`measure_phrase`.
        measured_text = measure_phrase(magnitude, measure_label, shape.unit)
        title = f"{label}: {measured_text}{share_text}"
        if bound is not None:
            # No ordinal, in either field. A bound cannot hold a position in
            # an order it was not measured for, and "ranks #1" over a
            # ceiling is the sentence this whole branch exists to delete.
            title = f"{label}: ≤ {measured_text} (upper bound){share_text}"
            statement = (
                f"{label}: {measure_label} is AT MOST {magnitude} {period_text} — the numerator "
                f"was suppressed over a population of {bound.population:,}, so this is a ceiling "
                "and not a measurement. It is published unranked: a bound cannot be ordered "
                "against measured cells."
            )
        elif urgency_position is not None:
            place, total = urgency_position
            statement = (
                f"{label}: {measured_text}{share_text} {period_text}. This is band "
                f"{place} of {total} in the catalog's declared order for "
                f"{shape.dimension_columns[0]}, which runs most urgent first — it is sequenced "
                "by urgency, not by size."
            )
        elif display_rank is not None:
            of_text = f" of {measured_total} measured" if measured_total else ""
            statement = (
                f"{label} ranks #{display_rank}{of_text} by {measure_label}{order_text} "
                f"{period_text}: {magnitude}{share_text}."
            )
        else:
            statement = (
                f"{label}: {measured_text}{share_text} {period_text}. No position is "
                "claimed for it — too much of this population carries suppressed numerators for "
                "an order to mean anything."
            )
        statement = _with_window_note(statement, spec, shape.window)

        values: list[tuple[str, Scalar]] = [(shape.measure, value)]
        if bound is None:
            values.append(("rank", display_rank if display_rank is not None else rank))
        if share is not None:
            values.append(("share_of_total", share))
        values.extend(_bound_values(shape.measure, bound))
        values.extend(_window_values(shape.measure, spec, shape.window))

        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(shape.measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # A count is not dollars: impact stays unset unless the ranked
            # measure is money, rather than inventing a figure. Nor is a
            # ceiling: nobody can work a bound.
            impact_cents=amount if (shape.is_money and bound is None) else None,
            confidence="qualified" if (qualified or bound is not None) else "high",
            suggested_refinements=suggested_refinements_for(referent_value),
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            cohort_definition=self._cohort_definition(
                row, shape.frame, shape.dimension_columns, spec
            ),
            finding=finding,
            dimension_value=self._single_dim(row, shape.frame, shape.dimension_columns),
        )
        return finding, registered
