"""The findings stage: selecting what to publish, and qualifying it."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from revi_investigation.application.calculation_glue import (
    CalculationResult,
    EmptinessFact,
    EmptinessKind,
)
from revi_investigation.application.capability_ports import PackPort, PlaybookSpec
from revi_investigation.application.comparison import (
    render_comparison,
)
from revi_investigation.application.execution import (
    BoundedCell,
)
from revi_investigation.application.findings.bounds import (
    MAX_BOUNDED_SHARE_FOR_RANKING,
    FindingsResult,
    SelectionCensus,
    _declared_bucket_order,
    _direction_omission_warning,
    _is_additive,
    _truncation_warning,
    _unranked_bounds_warning,
    _with_premise,
    row_noun,
)
from revi_investigation.application.findings.builders import _FindingBuilders
from revi_investigation.application.findings.premise import (
    PremiseCheck,
    _premise_verified_warning,
    _premise_warning,
    verify_premise,
)
from revi_investigation.application.findings.shapes import (
    ConcentrationShape,
    ScalarShape,
    TrendShape,
    _as_int,
    as_number,
    find_primary_concentration,
    find_primary_movement,
    find_scalar_shapes,
    find_trend_shapes,
    terminal_bucket_censoring,
)
from revi_investigation.application.findings.windows import (
    _PREMISE_PREFIX,
)
from revi_investigation.application.planning import InvestigationPlan
from revi_investigation.application.ports import ReferentRegistryStore, RegisteredReferent
from revi_investigation.application.window_maturity import WindowMaturity
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import Finding
from revi_kernel.filters import Scalar
from revi_kernel.refs import (
    ReferentKind,
)
from revi_kernel.scope import AbsoluteRange


class EvaluateFindingsService(_FindingBuilders):
    """The findings stage: what to evaluate, and what is worth publishing.

    The per-shape builders live in
    :mod:`~revi_investigation.application.findings.builders`; this class
    decides which shapes a turn has, runs them, and selects what the answer
    states.
    """

    def __init__(self, registry: ReferentRegistryStore, *, top_n: int = 3) -> None:
        self._registry = registry
        self._top_n = top_n
        #: The §15 threshold of the turn being evaluated, set per call. A
        #: bound is only recognisable against the threshold that produced
        #: it, and the evaluator has no catalog of its own.
        self._threshold: int | None = None


    async def evaluate(
        self,
        *,
        plan: InvestigationPlan,
        calculation: CalculationResult,
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
        suppression_threshold: int | None = None,
        window_maturity: Mapping[AbsoluteRange, WindowMaturity] | None = None,
    ) -> FindingsResult:
        self._threshold = suppression_threshold
        # The premise first, always: a question that STATES a movement is
        # answered honestly only once that movement has been measured.
        #
        # ``window_maturity`` is the settling verdict for every window this
        # PLAN reads, computed by the caller (which owns the repository)
        # and passed in as an integrity input, exactly like the suppression
        # threshold. The premise probe on a playbook path reads the
        # playbook's window, so a guard that only knows the spec's window
        # judges a period nothing was computed over.
        premise = verify_premise(
            plan,
            calculation,
            spec,
            pack,
            premise_prefix=_PREMISE_PREFIX,
            suppression_threshold=suppression_threshold,
            window_maturity=window_maturity,
        )
        # The verdict is published on EVERY premise turn, holds or not, and
        # it is published FIRST — registered before any other shape reads
        # the registry, so the aggregate the question assumed is F1 and the
        # cells that explain it are F2 onward.
        premise_lead: tuple[Finding, RegisteredReferent, str] | None = None
        if premise is not None:
            premise_lead = await self._publish_premise(
                premise, spec, pack, session_id, investigation_id
            )

        shape = find_primary_movement(plan, calculation)
        if shape is None:
            concentration = find_primary_concentration(plan, calculation)
            if concentration is not None:
                return _with_premise(
                    await self._evaluate_concentration(
                        shape=concentration,
                        spec=spec,
                        plan=plan,
                        pack=pack,
                        playbook=playbook,
                        session_id=session_id,
                        investigation_id=investigation_id,
                    ),
                    premise_lead,
                )
            trends = find_trend_shapes(plan, calculation)
            if trends:
                return _with_premise(
                    await self._evaluate_trends(
                        shapes=trends,
                        spec=spec,
                        pack=pack,
                        playbook=playbook,
                        session_id=session_id,
                        investigation_id=investigation_id,
                    ),
                    premise_lead,
                )
            scalars = find_scalar_shapes(plan, calculation)
            if not scalars:
                # Frames exist and no shape could publish from them: the
                # turn read data and has nothing to say about it. Which of
                # the two nothings this is, said as data.
                return _with_premise(
                    FindingsResult(
                        findings=(),
                        referents=(),
                        emptiness=self._no_findings(plan, calculation, "no publishable shape"),
                    ),
                    premise_lead,
                )
            return _with_premise(
                await self._evaluate_scalars(
                    shapes=scalars,
                    spec=spec,
                    pack=pack,
                    playbook=playbook,
                    session_id=session_id,
                    investigation_id=investigation_id,
                ),
                premise_lead,
            )

        delta_col = f"{shape.measure}__delta"
        idx_delta = shape.frame.schema.index_of(delta_col)
        rows, selection_warnings, counter = self._select_directional(
            shape.frame.rows, idx_delta, spec, pack, shape.measure
        )

        qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
        comparison = render_comparison(spec, window=shape.window)

        # Referent handles are session-monotonic (design §7.6): F2 keeps
        # meaning the finding it named when it was shown — later turns mint
        # new handles instead of overwriting old ones.
        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(
            1 for entry in existing if entry.referent.kind is ReferentKind.FINDING
        )
        row_offset = sum(
            1 for entry in existing if entry.referent.kind is ReferentKind.DIMENSION_VALUE
        )

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        limit = self._limit(spec)
        eligible = [row for row in rows if as_number(row[idx_delta]) is not None]
        bounds = self._bounds(shape.frame)
        row_positions = {id(row): i for i, row in enumerate(shape.frame.rows)}
        # The cells the direction filter removed are not gone, they are
        # LAST. Dropping them is a systematically premise-flattering
        # omission: "show me all twelve" returns ten and the two missing are
        # the only two that improved. When the analyst named a count, the
        # asked-for set is published first and the counter-direction cells
        # fill the rest of it, labelled.
        counter_eligible = [row for row in counter if as_number(row[idx_delta]) is not None]
        # Only a count the ANALYST named opens the set: the default top-3 is
        # this platform's own choice of how much to show, and filling it
        # with cells that moved the other way would answer a question
        # nobody asked.
        room = max(limit - len(eligible), 0) if spec.limit is not None else 0
        publishable = [*eligible[:limit], *counter_eligible[:room]]
        counter_published = len(publishable) - len(eligible[:limit])
        for row in publishable:
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_finding(
                f"F{n}",
                row,
                shape,
                spec,
                comparison,
                qualified,
                pack,
                session_id,
                investigation_id,
                bound=bounds.get(row_positions.get(id(row), -1), {}).get(shape.measure),
                counter_direction=len(findings) >= len(eligible[:limit]),
            )
            findings.append(finding)
            referents.append(referent)
        # Counted over the frame the CHART draws, never over the
        # direction-filtered candidate set: the card that published "3 of 10
        # computed cells" pointed at a chart drawing twelve payer rows.
        omission = _direction_omission_warning(
            counter_eligible, counter_published, shape, spec, pack
        )
        if omission is not None:
            selection_warnings = (*selection_warnings, omission)
        truncation = _truncation_warning(len(findings), len(shape.frame.rows), spec)
        if truncation is not None:
            selection_warnings = (*selection_warnings, truncation)

        for i, row in enumerate(shape.frame.rows):
            referents.append(
                self._dimension_value_referent(
                    f"D{row_offset + i + 1}",
                    row,
                    shape.frame,
                    shape.dimension_columns,
                    spec,
                    pack,
                    session_id,
                    investigation_id,
                )
            )

        await self._registry.register(tuple(referents))
        # The cells below moved the way the question assumed; whether the
        # population they sit in did is the verdict, and it leads either
        # way — as a correction when it refutes the question, as the
        # measured aggregate when it confirms it.
        return _with_premise(
            FindingsResult(
                findings=tuple(findings),
                referents=tuple(referents),
                warnings=selection_warnings,
                emptiness=(
                    None
                    if findings
                    else EmptinessFact(
                        kind=EmptinessKind.NO_FINDINGS,
                        frame_id=shape.frame_id,
                        detail=(
                            f"{len(shape.frame.rows)} compared row(s) on {shape.measure!r}, and "
                            "none carried a movement that could be published (every delta was "
                            "suppressed or filtered out by the asked direction)"
                        ),
                    )
                ),
            ),
            premise_lead,
        )


    # -------------------------------------------------------------- premise

    async def _publish_premise(
        self,
        premise: PremiseCheck,
        spec: AnalysisSpec,
        pack: PackPort,
        session_id: str,
        investigation_id: str,
    ) -> tuple[Finding, RegisteredReferent, str]:
        """Register the premise verdict as this turn's first finding.

        Registered here, before any shape reads the registry, so the
        aggregate the question assumed always takes F1 and every other
        shape numbers itself after it — the alternative was threading a
        "reserve one handle" flag through four independent branches.
        """
        comparison = render_comparison(spec, window=premise.window)
        existing = await self._registry.list_for_session(session_id)
        offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)
        finding, referent = self._build_premise_finding(
            f"F{offset + 1}", premise, spec, comparison, pack, session_id, investigation_id
        )
        await self._registry.register((referent,))
        warning = (
            _premise_verified_warning(premise, spec, comparison=comparison)
            if premise.holds
            else _premise_warning(premise, spec, comparison=comparison)
        )
        return finding, referent, warning


    # --------------------------------------------------------- scalar shape

    async def _evaluate_scalars(
        self,
        *,
        shapes: tuple[ScalarShape, ...],
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
    ) -> FindingsResult:
        """Findings from ungrouped metric cells — the direct answer."""
        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        for shape in shapes[: self._limit(spec)]:
            row = shape.frame.rows[0]
            value = row[shape.frame.schema.index_of(shape.measure)]
            # A suppressed cell has no level to publish. Saying so is the
            # frame's job (suppressed_cells) and the warning's; a finding
            # titled "net collection rate: suppressed" would be a headline
            # asserting a measurement that was withheld.
            if value is None:
                continue
            qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
            # Per SHAPE: one playbook answer can carry a 4-week denial-rate
            # probe beside a 3-month underpayment probe, and a comparison
            # phrase rendered once for the turn would name the wrong prior
            # range on at least one of them.
            comparison = render_comparison(spec, window=shape.window)
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_scalar_finding(
                f"F{n}",
                shape,
                value,
                spec,
                comparison,
                qualified,
                pack,
                session_id,
                investigation_id,
                bound=self._bounds(shape.frame).get(0, {}).get(shape.measure),
            )
            findings.append(finding)
            referents.append(referent)

        await self._registry.register(tuple(referents))
        return FindingsResult(
            findings=tuple(findings),
            referents=tuple(referents),
            emptiness=(
                None
                if findings
                else EmptinessFact(
                    kind=EmptinessKind.NO_FINDINGS,
                    frame_id=shapes[0].frame_id,
                    detail="every scalar cell this turn produced was suppressed",
                )
            ),
        )


    # ---------------------------------------------------------- trend shape

    async def _evaluate_trends(
        self,
        *,
        shapes: tuple[TrendShape, ...],
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
    ) -> FindingsResult:
        """Findings from an ungrouped series — the "by month" answer."""
        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        warnings: list[str] = []
        for shape in shapes[: self._limit(spec)]:
            censoring = terminal_bucket_censoring(shape, spec)
            finding = self._build_trend_finding(
                f"F{finding_offset + len(findings) + 1}",
                shape,
                spec,
                pack,
                playbook,
                censoring=censoring,
            )
            if finding is None:
                continue
            if censoring is not None:
                warnings.append(censoring.warning)
            findings.append(finding)
            referents.append(
                RegisteredReferent(
                    referent=finding.referent,
                    session_id=session_id,
                    investigation_id=investigation_id,
                    label=finding.title,
                    cohort_definition=self._cohort_definition(
                        shape.frame.rows[0], shape.frame, (), spec
                    ),
                    finding=finding,
                )
            )

        await self._registry.register(tuple(referents))
        return FindingsResult(
            findings=tuple(findings),
            referents=tuple(referents),
            warnings=tuple(dict.fromkeys(warnings)),
            emptiness=(
                None
                if findings
                else EmptinessFact(
                    kind=EmptinessKind.NO_FINDINGS,
                    frame_id=shapes[0].frame_id,
                    detail="every bucket in this series was suppressed or empty",
                )
            ),
        )


    # -------------------------------------------------- concentration shape

    async def _evaluate_concentration(
        self,
        *,
        shape: ConcentrationShape,
        spec: AnalysisSpec,
        plan: InvestigationPlan | None = None,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
    ) -> FindingsResult:
        """Findings from a ranked population — the no-comparison answer.

        Measured cells are ranked; bounded cells are not. Ordering a ceiling
        against a measurement sorts by *panel size*: a frame of 147 bounds
        in 150 values, sorted descending, ranks by ascending population with
        "ranks #1 … (worst first, as asked)" written over it. The bounded
        cells still publish — dropping them is the censorship the bound
        exists to avoid — in their own block, unranked, and saying so.
        """
        schema = shape.frame.schema
        idx_rank = schema.index_of(shape.rank_column)
        idx_measure = schema.index_of(shape.measure)
        bounds = self._bounds(shape.frame)
        positions = {id(row): i for i, row in enumerate(shape.frame.rows)}

        def is_empty(row: tuple[Scalar, ...]) -> bool:
            """A zero that is padding rather than a result.

            Only for additive units. "Payer X: 0 mismatched claims" is the
            tail every ranked list has, and it dilutes the one row that
            matters. "Dr. X: 0% denial rate" is the opposite — it is the
            best cell in the population, and dropping those rows drives the
            measured count to zero and manufactures a refusal to rank over a
            frame full of perfect performers.
            """
            value = row[idx_measure]
            return (
                _is_additive(shape.unit)
                and isinstance(value, (int, Decimal))
                and not isinstance(value, bool)
                and value == 0
            )

        def publishable(row: tuple[Scalar, ...]) -> bool:
            # A suppressed (NULL) cell has no value to publish; an empty
            # one is padding only where zero means "nothing happened".
            return row[idx_measure] is not None and not is_empty(row)

        def bound_of(row: tuple[Scalar, ...]) -> BoundedCell | None:
            return bounds.get(positions.get(id(row), -1), {}).get(shape.measure)

        ordered = sorted(
            shape.frame.rows,
            key=lambda row: (
                _as_int(row[idx_rank]) is None,  # unranked rows last
                _as_int(row[idx_rank]) or 0,
            ),
        )
        candidates = [row for row in ordered if publishable(row)]
        # An ordinal bucket dimension carries its own direction, and it is
        # urgency, not size. Sequencing "90+" ahead of
        # "61-90" tells a team to work the least urgent band first and let
        # the 61-90 band age into expired; the catalog declares the order
        # and the plan carries it here.
        urgency = _declared_bucket_order(plan, shape)
        if urgency is not None:
            idx_dim = schema.index_of(shape.dimension_columns[0])
            bucket_order = {value: i for i, value in enumerate(urgency)}
            candidates.sort(
                key=lambda row: bucket_order.get(str(row[idx_dim]), len(bucket_order))
            )
        measured = [row for row in candidates if bound_of(row) is None]
        bounded = [row for row in candidates if bound_of(row) is not None]
        # A bound's ceiling is (threshold - 1) / population, so ordering
        # bounded cells by VALUE orders them by inverse panel size and the
        # three published are always the three smallest populations — the
        # loosest, least useful ceilings in the frame. Order them by the
        # population the bound was taken over instead: the tightest ceiling
        # is the one a reader can do something with.
        bounded.sort(
            key=lambda row: (
                -(bound.population if (bound := bound_of(row)) is not None else 0),
                self._row_label(row, shape.frame, shape.dimension_columns, pack),
            )
        )
        census = SelectionCensus(
            total=len(shape.frame.rows),
            bounded=len(bounded),
            measured=len(measured),
            empty=sum(1 for row in ordered if is_empty(row)),
        )
        # Past a governed share of bounds there is no measured population
        # left to order, and an ordinal claim over it is arithmetic about
        # panel size. The answer is then the population arithmetic. Measured
        # ZEROS count toward the measured side of that share: they are the
        # cells the ranking is most about.
        unrankable = census.bounded_share > MAX_BOUNDED_SHARE_FOR_RANKING
        qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)

        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)
        row_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.DIMENSION_VALUE)

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        warnings: list[str] = []
        limit = self._limit(spec)
        for position, row in enumerate(measured[:limit], start=1):
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_concentration_finding(
                f"F{n}",
                row,
                shape,
                spec,
                qualified,
                pack,
                session_id,
                investigation_id,
                display_rank=None if (unrankable or urgency is not None) else position,
                measured_total=len(measured),
                bound=None,
                urgency_position=(
                    None if urgency is None else (position, len(measured))
                ),
            )
            findings.append(finding)
            referents.append(referent)
        for row in bounded[:limit]:
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_concentration_finding(
                f"F{n}",
                row,
                shape,
                spec,
                qualified,
                pack,
                session_id,
                investigation_id,
                display_rank=None,
                measured_total=len(measured),
                bound=bound_of(row),
            )
            findings.append(finding)
            referents.append(referent)
        if bounded:
            warnings.append(
                _unranked_bounds_warning(
                    census=census,
                    measure=shape.measure,
                    unrankable=unrankable,
                    order=spec.order,
                    # The reader's own noun for the rows in front of them —
                    # "plans", "payers", "providers" — rather than "cells",
                    # which is the engine's word for its own arithmetic.
                    noun=row_noun(shape.dimension_columns),
                )
            )
        truncation = _truncation_warning(
            min(len(measured), limit) + min(len(bounded), limit), len(candidates), spec
        )
        if truncation is not None:
            warnings.append(truncation)

        for i, row in enumerate(shape.frame.rows):
            referents.append(
                self._dimension_value_referent(
                    f"D{row_offset + i + 1}",
                    row,
                    shape.frame,
                    shape.dimension_columns,
                    spec,
                    pack,
                    session_id,
                    investigation_id,
                )
            )

        await self._registry.register(tuple(referents))
        return FindingsResult(
            findings=tuple(findings),
            referents=tuple(referents),
            warnings=tuple(warnings),
            emptiness=(
                None
                if findings
                else EmptinessFact(
                    kind=EmptinessKind.NO_FINDINGS,
                    frame_id=shape.frame_id,
                    detail=(
                        f"{len(shape.frame.rows)} ranked row(s) on {shape.measure!r}, and "
                        "every one was zero or suppressed"
                    ),
                )
            ),
        )
