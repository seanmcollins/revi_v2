"""Findings evaluation (design §8.1 steps 12-13): certified, referent-
addressable results built from the final frames, with drillable cohorts.

Three finding shapes, tried in order — all generic, none keyed to any
question or playbook id:

**Movement** (preferred). The primary findings frame is the first
``compare`` output that carries at least one dimension column and a money
measure with its delta. Rows are ranked by delta **ascending** (the biggest
declines of a higher-is-good measure first) and the top N become findings
F1, F2, ... Each carries current/prior/delta cents and pct change, and
``impact_cents`` equal to the delta. That ascending default holds only
while the question asserted no direction: when the spec carries one
(``AskedDirection``, resolved against the metric's sign convention), rows
moving the other way are not eligible to be the answer, and an empty
direction-matched set says so before the opposite is offered as context —
see ``_select_directional``.

**Concentration** (fallback). Plenty of real questions have no comparison
at all — "do I have a COB problem?", "score my facilities", "what's aging
out of timely filing?". Their playbooks rank a population instead of
comparing two windows, and before this path existed they executed
perfectly and then answered *nothing*: no compare frame, no findings, an
empty answer over correct evidence. So when no compare shape exists, the
first ``rank`` output carrying a dimension column and a measure supplies
the findings, in rank order, with ``impact_cents`` set only when the
ranked measure is money (a claim count is not dollars, and pretending
otherwise would invent an impact). Share-of-total columns ride along when
the playbook computed them.

**Scalar** (the ungrouped answer). The plainest question there is — "what
is our net collection rate over the last 90 days?" — plans one probe, no
dimensions, no comparison, and produces one frame with one row and one
cell. It has no dimension column, so neither shape above could see it:
``find_primary_compare`` and ``find_primary_concentration`` both begin by
requiring ``_dimension_columns(frame)`` to be non-empty and return ``None``
when it is. The probe executed, the number was computed, the grade was
DIRECT, the chart drew it — and ``findings`` came back empty, which also
meant the narrative stage short-circuited and the answer was silent. A
computed number the analyst never saw is the same failure the concentration
path was added to fix, one shape further down. So a frame with no
dimension columns and exactly one row publishes its metric cells as
findings: the level, the window, the grade, and — when the turn carried a
comparison, so the frame also holds ``__prior``/``__delta``/``__pct_change``
— the movement. Both sides are rendered in the metric contract's own unit,
so a ratio reads as a percentage and money as dollars; ``impact_cents`` is
set only for money, exactly as in the concentration path. A suppressed cell
publishes no finding: "suppressed" is not a level.

Whichever shape applies, each finding gets — via the referent registry — a drillable
:class:`CohortDefinition` at the CLAIM entity scoped to the finding's
dimension values plus the analysis window, so a later ``DrillInto``
refinement can pin the exact population shown.

Conclusion policies gate confidence: when a playbook's policies demand a
stronger grade than the frame provides (proxy or discovery evidence, or a
policy requiring DIRECT), the finding's confidence drops to "qualified" —
weak evidence can surface, but never in certified language. A comparison
whose two windows are different lengths qualifies a finding for the same
reason and additionally withholds ``impact_cents``
(:mod:`revi_investigation.application.comparison` documents why).

Every value that reaches a title or a statement is rendered through
:mod:`revi_investigation.application.rendering`, in the unit the metric
contract declares — never ``repr``, never floor-divided dollars beside raw
cents, and never a bare CARC integer without its group code and title.

Every compare row is also registered as a dimension-value referent
(D1, D2, ...) so table rows are addressable in follow-up turns.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from revi_calculation_contracts.contract import SignConvention
from revi_investigation.application.calculation_glue import (
    CalculationResult,
    EmptinessFact,
    EmptinessKind,
)
from revi_investigation.application.capability_ports import PackPort, PlaybookSpec
from revi_investigation.application.comparison import ComparisonRendering, render_comparison
from revi_investigation.application.planning import InvestigationPlan
from revi_investigation.application.ports import ReferentRegistryStore, RegisteredReferent
from revi_investigation.application.rendering import (
    COUNT_UNIT as _COUNT_UNIT,
)
from revi_investigation.application.rendering import (
    MONEY_UNIT as _MONEY_UNIT,
)
from revi_investigation.application.rendering import (
    format_value,
    magnitude,
    magnitude_money,
    metric_label,
    ratio_pct,
    render_row_label,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    AskedMagnitude,
    adverse_delta_sign,
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

_TIME_BUCKET_PREFIX = "time_bucket:"
_PRIOR_SUFFIX = "__prior"
_QUALIFIED_GRADES = (EvidenceGrade.PROXY, EvidenceGrade.DISCOVERY, EvidenceGrade.UNAVAILABLE)

#: Units whose totals scale with the length of the window they are measured
#: over. A length-mismatched comparison distorts these and leaves a rate
#: alone, which is why the mismatch caveat is applied per unit rather than
#: per turn.
_ADDITIVE_UNITS = (_MONEY_UNIT, _COUNT_UNIT)


def _is_additive(unit: str | None) -> bool:
    return unit in _ADDITIVE_UNITS


@dataclass(frozen=True, slots=True)
class FindingsResult:
    findings: tuple[Finding, ...]
    referents: tuple[RegisteredReferent, ...]
    #: What the analyst has to be told about the *selection* before they
    #: read the rows — chiefly that nothing moved the way they asked about.
    #: These lead the turn's warnings: a caveat published under the findings
    #: it contradicts is a caveat nobody reads.
    warnings: tuple[str, ...] = ()
    #: Set when frames had rows and no finding survived selection. The
    #: other half of :class:`EmptinessFact`: "there is nothing here" and
    #: "there is plenty here and none of it is notable" are different
    #: answers, and publishing both as silence made them the same one.
    emptiness: EmptinessFact | None = None


@dataclass(frozen=True, slots=True)
class CompareShape:
    """A compare frame suitable for findings/reconciliation: at least one
    dimension column plus a money measure with its delta."""

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    money_measure: str


def find_primary_compare(
    plan: InvestigationPlan, calculation: CalculationResult
) -> CompareShape | None:
    """The first compare output carrying dimensions and a money measure —
    the findings frame, and the child side of the reconciliation invariant."""
    for step in plan.transforms.steps:
        if step.operator != "compare":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        money = _money_measure(frame)
        if dims and money is not None:
            return CompareShape(
                frame_id=step.id, frame=frame, dimension_columns=dims, money_measure=money
            )
    return None


@dataclass(frozen=True, slots=True)
class MovementShape:
    """A compare frame the movement path can publish findings from.

    The generalization of :class:`CompareShape`. That one required a
    **money** measure with a delta, because the first questions this engine
    answered were about dollars — and the requirement quietly became a
    filter on which questions could be answered at all. "Denial rate by
    payer for the last 90 days compared to the prior 90 days" plans two
    probes, compares them correctly, produces a frame with a payer column,
    a rate, a prior rate, a delta and a percentage change, and published
    **zero findings and a null narrative**: no column in it was money, so
    the shape came back ``None``, and ``evaluate`` only fell through to the
    concentration path when there was no compare shape — the compare step
    existed, so nothing looked further.

    A movement is a movement in whatever unit the contract declares.
    ``impact_cents`` is still money-only (a rate is not dollars), and
    reconciliation still requires money (children of a rate do not sum),
    which is why :func:`find_primary_compare` stays as it was.
    """

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    measure: str
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


def find_primary_movement(
    plan: InvestigationPlan, calculation: CalculationResult
) -> MovementShape | None:
    """The first compare output carrying dimensions and any measure delta.

    Money wins when a frame holds several compared measures — a dollar
    movement is what a worklist is built from, and preferring it keeps
    every answer this engine already gave byte-identical. Otherwise the
    first compared metric column is the answer, which is the case that used
    to publish nothing.
    """
    for step in plan.transforms.steps:
        if step.operator != "compare":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        if not dims:
            continue
        compared = _compared_measures(frame)
        if not compared:
            continue
        best = next((name for name in compared if _unit_of(frame, name) == _MONEY_UNIT), None)
        measure = best if best is not None else compared[0]
        return MovementShape(
            frame_id=step.id,
            frame=frame,
            dimension_columns=dims,
            measure=measure,
            unit=_unit_of(frame, measure),
        )
    return None


def _compared_measures(frame: EvidenceFrame) -> tuple[str, ...]:
    """Metric columns in this frame that carry a ``__delta`` sibling."""
    names = set(frame.schema.names)
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, MetricRef)
        and "__" not in col.name
        and f"{col.name}__delta" in names
    )


@dataclass(frozen=True, slots=True)
class ConcentrationShape:
    """A ranked frame suitable for findings when nothing was compared: at
    least one dimension column plus the measure the rank was taken on."""

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    measure: str
    rank_column: str
    share_column: str | None
    is_money: bool
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None


def find_primary_concentration(
    plan: InvestigationPlan, calculation: CalculationResult
) -> ConcentrationShape | None:
    """The first ``rank`` output carrying dimensions and a base measure —
    the findings frame for playbooks that rank rather than compare."""
    for step in plan.transforms.steps:
        if step.operator != "rank":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        if not dims:
            continue
        ranked_by = step.arg("by")
        if ranked_by is None:
            continue
        measure = _base_measure(frame, ranked_by)
        if measure is None:
            continue
        rank_column = f"{ranked_by}__rank"
        if rank_column not in frame.schema.names:
            continue
        share_column = f"{measure}__share"
        unit = _unit_of(frame, measure)
        return ConcentrationShape(
            frame_id=step.id,
            frame=frame,
            dimension_columns=dims,
            measure=measure,
            rank_column=rank_column,
            share_column=share_column if share_column in frame.schema.names else None,
            is_money=unit == _MONEY_UNIT,
            unit=unit,
        )
    return None


@dataclass(frozen=True, slots=True)
class ScalarShape:
    """One ungrouped metric cell: the whole answer to a direct question.

    ``prior_column``/``delta_column``/``pct_column`` are set only when the
    turn carried a comparison and the ``compare`` operator produced them,
    which is what separates "the rate is 5.2%" from "the rate is 5.2%, up
    from 4.9%".
    """

    frame_id: str
    frame: EvidenceFrame
    measure: str
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None
    prior_column: str | None
    delta_column: str | None
    pct_column: str | None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT

    @property
    def compared(self) -> bool:
        return self.prior_column is not None and self.delta_column is not None


def find_scalar_shapes(
    plan: InvestigationPlan, calculation: CalculationResult
) -> tuple[ScalarShape, ...]:
    """Every ungrouped single-row metric cell this plan produced.

    Reads the *final* logical frame for each probe node — the node's
    ``compare`` output when it has one, else the probe frame itself — so a
    scalar with a comparison is described by its movement rather than twice
    by its level. Prior-window twins are skipped: they are an input to the
    comparison, never an answer.

    A frame with more than one row is not a scalar. That is deliberate:
    an ungrouped frame with several rows is a time-bucketed series, which
    is a trend and wants a trend's treatment, not N headline levels.
    """
    compare_of: dict[str, str] = {}
    for step in plan.transforms.steps:
        if step.operator == "compare" and step.inputs:
            compare_of[step.inputs[0]] = step.id

    shapes: list[ScalarShape] = []
    for node in plan.nodes:
        if node.id.endswith(_PRIOR_SUFFIX):
            continue
        frame_id = compare_of.get(node.id, node.id)
        try:
            frame = calculation.frame(frame_id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        if _dimension_columns(frame) or len(frame.rows) != 1:
            continue
        names = set(frame.schema.names)
        for column in frame.schema.columns:
            if not isinstance(column.ref, MetricRef) or "__" in column.name:
                continue
            measure = column.name
            prior = f"{measure}__prior"
            delta = f"{measure}__delta"
            pct = f"{measure}__pct_change"
            compared = prior in names and delta in names
            shapes.append(
                ScalarShape(
                    frame_id=frame_id,
                    frame=frame,
                    measure=measure,
                    unit=column.unit,
                    prior_column=prior if compared else None,
                    delta_column=delta if compared else None,
                    pct_column=pct if pct in names else None,
                )
            )
    return tuple(shapes)


def _dimension_columns(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, DimensionRef) and not col.ref.id.startswith(_TIME_BUCKET_PREFIX)
    )


def _unit_of(frame: EvidenceFrame, name: str) -> str | None:
    for col in frame.schema.columns:
        if col.name == name:
            return col.unit
    return None


def _base_measure(frame: EvidenceFrame, ranked_by: str) -> str | None:
    """The undecorated metric column behind a rank arg (``x__delta`` → ``x``)."""
    names = set(frame.schema.names)
    candidate = ranked_by.split("__", 1)[0]
    if candidate not in names:
        return None
    for col in frame.schema.columns:
        if col.name == candidate and isinstance(col.ref, MetricRef):
            return candidate
    return None


def _money_measure(frame: EvidenceFrame) -> str | None:
    names = set(frame.schema.names)
    for col in frame.schema.columns:
        if (
            isinstance(col.ref, MetricRef)
            and col.unit == _MONEY_UNIT
            and "__" not in col.name
            and f"{col.name}__delta" in names
        ):
            return col.name
    return None


def _as_int(value: Scalar) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_number(value: Scalar) -> Decimal | None:
    """Any numeric cell as a Decimal, or ``None`` when there is no number.

    Movement selection used to read deltas through :func:`_as_int`, which
    is correct for money (integer cents) and silently wrong for everything
    else: a ratio delta is a ``Decimal``, so every row of a compared *rate*
    read as "no movement" and sorted into the NULL bucket. Ordering a
    frame by a value the ordering cannot see is how a rate comparison came
    back with nothing to say.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return Decimal(value)


def _direction(delta: Scalar) -> str | None:
    """"up from" / "down from" / "unchanged from", or ``None`` when the
    delta is not a number and there is therefore no movement to name.

    Read off the delta in whatever numeric type the operator produced —
    money deltas are integer cents, ratio deltas are ``Decimal``. Deciding
    direction from an int-only coercion published a *rate* that rose 1.0
    point as "unchanged", which is the one thing a headline must never be.
    """
    if delta is None or isinstance(delta, bool) or not isinstance(delta, (int, Decimal)):
        return None
    if delta == 0:
        return "unchanged from"
    return "down from" if delta < 0 else "up from"


class EvaluateFindingsService:
    def __init__(self, registry: ReferentRegistryStore, *, top_n: int = 3) -> None:
        self._registry = registry
        self._top_n = top_n

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
    ) -> FindingsResult:
        shape = find_primary_movement(plan, calculation)
        if shape is None:
            concentration = find_primary_concentration(plan, calculation)
            if concentration is not None:
                return await self._evaluate_concentration(
                    shape=concentration,
                    spec=spec,
                    pack=pack,
                    playbook=playbook,
                    session_id=session_id,
                    investigation_id=investigation_id,
                )
            scalars = find_scalar_shapes(plan, calculation)
            if not scalars:
                # Frames exist and no shape could publish from them: the
                # turn read data and has nothing to say about it. Which of
                # the two nothings this is, said as data.
                return FindingsResult(
                    findings=(),
                    referents=(),
                    emptiness=self._no_findings(plan, calculation, "no publishable shape"),
                )
            return await self._evaluate_scalars(
                shapes=scalars,
                spec=spec,
                pack=pack,
                playbook=playbook,
                session_id=session_id,
                investigation_id=investigation_id,
            )

        delta_col = f"{shape.measure}__delta"
        idx_delta = shape.frame.schema.index_of(delta_col)
        rows, selection_warnings = self._select_directional(
            shape.frame.rows, idx_delta, spec, pack, shape.measure
        )

        qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
        comparison = render_comparison(spec)

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
        for row in rows[: self._top_n]:
            if _as_number(row[idx_delta]) is None:
                continue
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
            )
            findings.append(finding)
            referents.append(referent)

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
        )

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
    ) -> tuple[list[tuple[Scalar, ...]], tuple[str, ...]]:
        """Order (and, when a direction was asked, restrict) the compare rows.

        Without a direction this is the old rule: rank by delta ascending,
        biggest declines of a higher-is-good measure first — the right
        default for "what moved?".

        With one it is not a default any more, it is the question. Live,
        "which payers had the biggest INCREASE in denials" ran the default
        and published the three biggest *decreases*, narrated as
        improvements: a confident, well-evidenced, exactly-backwards answer.
        So rows whose delta has the wrong sign are not eligible to be the
        answer at all, and the remaining ones are ordered by the extremity
        the analyst phrased.

        When nothing moved the asked-for way the answer says that FIRST and
        then shows the opposite as context — the honest shape of an empty
        direction-matched set. Rows with a NULL delta are never eligible
        either way: a suppressed movement is not a movement.
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
                    _as_number(row[idx_delta]) is None,  # NULL deltas last
                    -(_as_number(row[idx_delta]) or 0)
                    if descending
                    else (_as_number(row[idx_delta]) or 0),
                ),
            )

        if wanted is None:
            # No direction was asked. The default is not "ascending" — it is
            # *worst first*, read off the contract's own sign convention: a
            # higher-is-bad measure's worst movement is a rise. Ascending
            # was only ever right because the first metrics through here
            # were higher-is-good dollars, and it published the biggest
            # improvements of a higher-is-bad metric as its headline.
            adverse = adverse_delta_sign(sign)
            return ordered(list(rows), descending=adverse is not None and adverse > 0), ()

        matched = [
            row
            for row in rows
            if (value := _as_number(row[idx_delta])) is not None
            and (value > 0 if wanted > 0 else value < 0)
        ]
        assert spec.direction is not None
        movement = "rose" if wanted > 0 else "fell"
        if matched:
            return ordered(matched, descending=(wanted > 0) == biggest_first), ()
        warning = (
            f"direction_unmatched: nothing {movement} — no cell's "
            f"{metric_label(money_measure)} moved the way {spec.direction.value!r} asks about "
            "over this window. The movements below are the opposite direction, shown as "
            "context, not as an answer to what was asked."
        )
        return ordered(list(rows), descending=not (wanted > 0)), (warning,)

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
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        measure = shape.measure
        current = row[schema.index_of(measure)]
        prior = row[schema.index_of(f"{measure}__prior")]
        delta = _as_number(row[schema.index_of(f"{measure}__delta")])
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
        title = f"{label} {measure_label} {direction} {amount} {period_phrase}"
        pct_text = ratio_pct(pct) if isinstance(pct, Decimal) else "n/a"
        statement = (
            f"{label}: {measure_label} moved from {format_value(prior, shape.unit)} to "
            f"{format_value(current, shape.unit)} "
            f"({direction} {amount}, {pct_text} {period_phrase})."
        )

        delta_value: Scalar = int(delta) if shape.is_money else delta
        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(measure),),
            values=(
                ("current_cents" if shape.is_money else measure, current),
                ("prior_cents" if shape.is_money else f"{measure}__prior", prior),
                ("delta_cents" if shape.is_money else f"{measure}__delta", delta_value),
                ("pct_change", pct),
            ),
            grade=shape.frame.evidence_grade,
            # A rate is not dollars: an impact is a figure this platform is
            # willing to rank, sum and put in a worklist, and a percentage
            # point is none of those.
            impact_cents=(
                int(delta) if (shape.is_money and not mismatched) else None
            ),
            confidence="qualified" if (qualified or mismatched) else "high",
            suggested_refinements=(f"drill into {referent_value}",),
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
        comparison = render_comparison(spec)
        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        for shape in shapes[: self._top_n]:
            row = shape.frame.rows[0]
            value = row[shape.frame.schema.index_of(shape.measure)]
            # A suppressed cell has no level to publish. Saying so is the
            # frame's job (suppressed_cells) and the warning's; a finding
            # titled "net collection rate: suppressed" would be a headline
            # asserting a measurement that was withheld.
            if value is None:
                continue
            qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_scalar_finding(
                f"F{n}", shape, value, spec, comparison, qualified, session_id, investigation_id
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

    def _build_scalar_finding(
        self,
        referent_value: str,
        shape: ScalarShape,
        value: Scalar,
        spec: AnalysisSpec,
        comparison: ComparisonRendering | None,
        qualified: bool,
        session_id: str,
        investigation_id: str,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        row = shape.frame.rows[0]
        label = metric_label(shape.measure)
        current_text = format_value(value, shape.unit)
        window = spec.context.window.range
        window_text = f"{window.start.isoformat()}..{window.end.isoformat()}"

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
            pct_text = f" ({ratio_pct(pct)} change)" if isinstance(pct, Decimal) else ""
            statement = (
                f"{label} is {current_text} over {window_text}, {movement} {prior_text} "
                f"{period_phrase}{pct_text}."
            )
        else:
            title = f"{label}: {current_text} ({window_text})"
            statement = f"{label} is {current_text} over {window_text}."

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
                _as_int(delta) if (shape.is_money and not mismatched) else None
            ),
            confidence="qualified" if (qualified or mismatched) else "high",
            suggested_refinements=(f"drill into {referent_value}",),
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

    # -------------------------------------------------- concentration shape

    async def _evaluate_concentration(
        self,
        *,
        shape: ConcentrationShape,
        spec: AnalysisSpec,
        pack: PackPort,
        playbook: PlaybookSpec | None,
        session_id: str,
        investigation_id: str,
    ) -> FindingsResult:
        """Findings from a ranked population — the no-comparison answer."""
        schema = shape.frame.schema
        idx_rank = schema.index_of(shape.rank_column)
        rows = sorted(
            shape.frame.rows,
            key=lambda row: (
                _as_int(row[idx_rank]) is None,  # unranked rows last
                _as_int(row[idx_rank]) or 0,
            ),
        )
        qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)

        existing = await self._registry.list_for_session(session_id)
        finding_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.FINDING)
        row_offset = sum(1 for e in existing if e.referent.kind is ReferentKind.DIMENSION_VALUE)

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        for row in rows[: self._top_n]:
            value = row[schema.index_of(shape.measure)]
            # Suppressed (NULL) and empty (zero) rows are not findings. A
            # ranked list always has a tail; "Payer X: 0 mismatched claims"
            # is padding that dilutes the one row that matters.
            if value is None or (isinstance(value, int | Decimal) and value == 0):
                continue
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_concentration_finding(
                f"F{n}", row, shape, spec, qualified, pack, session_id, investigation_id
            )
            findings.append(finding)
            referents.append(referent)

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
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        value = row[schema.index_of(shape.measure)]
        rank = _as_int(row[schema.index_of(shape.rank_column)])
        share = row[schema.index_of(shape.share_column)] if shape.share_column else None
        label = self._row_label(row, shape.frame, shape.dimension_columns, pack)
        measure_label = metric_label(shape.measure)
        window = spec.context.window.range

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
        title = f"{label}: {magnitude} {measure_label}{share_text}"
        statement = (
            f"{label} ranks #{rank} by {measure_label} over "
            f"{window.start.isoformat()}..{window.end.isoformat()}: {magnitude}{share_text}."
        )

        values: list[tuple[str, Scalar]] = [(shape.measure, value), ("rank", rank)]
        if share is not None:
            values.append(("share_of_total", share))

        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(shape.measure),),
            values=tuple(values),
            grade=shape.frame.evidence_grade,
            # A count is not dollars: impact stays unset unless the ranked
            # measure is money, rather than inventing a figure.
            impact_cents=amount if shape.is_money else None,
            confidence="qualified" if qualified else "high",
            suggested_refinements=(f"drill into {referent_value}",),
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
