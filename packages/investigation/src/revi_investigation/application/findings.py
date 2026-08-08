"""Findings evaluation (design §8.1 steps 12-13): certified, referent-
addressable results built from the final frames, with drillable cohorts.

Two finding shapes, tried in order — both generic, neither keyed to any
question or playbook id:

**Movement** (preferred). The primary findings frame is the first
``compare`` output that carries at least one dimension column and a money
measure with its delta. Rows are ranked by delta **ascending** (the biggest
declines of a higher-is-good measure first) and the top N become findings
F1, F2, ... Each carries current/prior/delta cents and pct change, and
``impact_cents`` equal to the delta.

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

Either way each finding gets — via the referent registry — a drillable
:class:`CohortDefinition` at the CLAIM entity scoped to the finding's
dimension values plus the analysis window, so a later ``DrillInto``
refinement can pin the exact population shown.

Conclusion policies gate confidence: when a playbook's policies demand a
stronger grade than the frame provides (proxy or discovery evidence, or a
policy requiring DIRECT), the finding's confidence drops to "qualified" —
weak evidence can surface, but never in certified language.

Every compare row is also registered as a dimension-value referent
(D1, D2, ...) so table rows are addressable in follow-up turns.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.capability_ports import PackPort, PlaybookSpec
from revi_investigation.application.planning import InvestigationPlan
from revi_investigation.application.ports import ReferentRegistryStore, RegisteredReferent
from revi_investigation.domain.context import AnalysisSpec
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
from revi_kernel.scope import ComparisonKind

_TIME_BUCKET_PREFIX = "time_bucket:"
_MONEY_UNIT = "money_cents"
_QUALIFIED_GRADES = (EvidenceGrade.PROXY, EvidenceGrade.DISCOVERY, EvidenceGrade.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class FindingsResult:
    findings: tuple[Finding, ...]
    referents: tuple[RegisteredReferent, ...]


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
        return ConcentrationShape(
            frame_id=step.id,
            frame=frame,
            dimension_columns=dims,
            measure=measure,
            rank_column=rank_column,
            share_column=share_column if share_column in frame.schema.names else None,
            is_money=_unit_of(frame, measure) == _MONEY_UNIT,
        )
    return None


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
        shape = find_primary_compare(plan, calculation)
        if shape is None:
            concentration = find_primary_concentration(plan, calculation)
            if concentration is None:
                return FindingsResult(findings=(), referents=())
            return await self._evaluate_concentration(
                shape=concentration,
                spec=spec,
                pack=pack,
                playbook=playbook,
                session_id=session_id,
                investigation_id=investigation_id,
            )

        delta_col = f"{shape.money_measure}__delta"
        idx_delta = shape.frame.schema.index_of(delta_col)
        rows = sorted(
            shape.frame.rows,
            key=lambda row: (
                _as_int(row[idx_delta]) is None,  # NULL deltas last
                _as_int(row[idx_delta]) or 0,
            ),
        )

        qualified = self._requires_qualification(shape.frame.evidence_grade, pack, playbook)
        period_phrase = self._period_phrase(spec)

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
            if _as_int(row[idx_delta]) is None:
                continue
            n = finding_offset + len(findings) + 1
            finding, referent = self._build_finding(
                f"F{n}", row, shape, spec, period_phrase, qualified, session_id, investigation_id
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
                    session_id,
                    investigation_id,
                )
            )

        await self._registry.register(tuple(referents))
        return FindingsResult(findings=tuple(findings), referents=tuple(referents))

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

    @staticmethod
    def _period_phrase(spec: AnalysisSpec) -> str:
        comparison = spec.context.comparison
        if comparison is None:
            return "vs prior period"
        if comparison.kind is ComparisonKind.PRIOR_YEAR:
            return "vs prior year"
        requested = spec.context.window.requested
        if requested is not None and requested.quantity == 1:
            return f"vs prior {requested.unit.value}"
        return "vs prior period"

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
        row: tuple[Scalar, ...], frame: EvidenceFrame, dimension_columns: tuple[str, ...]
    ) -> str:
        return " / ".join(str(row[frame.schema.index_of(dim)]) for dim in dimension_columns)

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
        shape: CompareShape,
        spec: AnalysisSpec,
        period_phrase: str,
        qualified: bool,
        session_id: str,
        investigation_id: str,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        measure = shape.money_measure
        current = row[schema.index_of(measure)]
        prior = row[schema.index_of(f"{measure}__prior")]
        delta = _as_int(row[schema.index_of(f"{measure}__delta")])
        pct = row[schema.index_of(f"{measure}__pct_change")]
        assert delta is not None  # caller filtered NULL deltas

        label = self._row_label(row, shape.frame, shape.dimension_columns)
        metric_label = measure.replace("_", " ")
        direction = "down" if delta < 0 else "up"
        dollars = f"${abs(delta) // 100:,}"
        title = f"{label} {metric_label} {direction} {dollars} {period_phrase}"
        pct_text = f"{float(pct):.1%}" if isinstance(pct, Decimal) else "n/a"
        statement = (
            f"{label}: {metric_label} moved from {prior!r} to {current!r} cents "
            f"({direction} {dollars}, {pct_text} {period_phrase})."
        )

        referent = ReferentId(value=referent_value, kind=ReferentKind.FINDING)
        finding = Finding(
            referent=referent,
            title=title,
            statement=statement,
            metric_refs=(MetricRef(measure),),
            values=(
                ("current_cents", current),
                ("prior_cents", prior),
                ("delta_cents", delta),
                ("pct_change", pct),
            ),
            grade=shape.frame.evidence_grade,
            impact_cents=delta,
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

    def _dimension_value_referent(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        frame: EvidenceFrame,
        dimension_columns: tuple[str, ...],
        spec: AnalysisSpec,
        session_id: str,
        investigation_id: str,
    ) -> RegisteredReferent:
        return RegisteredReferent(
            referent=ReferentId(value=referent_value, kind=ReferentKind.DIMENSION_VALUE),
            session_id=session_id,
            investigation_id=investigation_id,
            label=self._row_label(row, frame, dimension_columns),
            cohort_definition=self._cohort_definition(row, frame, dimension_columns, spec),
            dimension_value=self._single_dim(row, frame, dimension_columns),
        )

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
                f"F{n}", row, shape, spec, qualified, session_id, investigation_id
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
                    session_id,
                    investigation_id,
                )
            )

        await self._registry.register(tuple(referents))
        return FindingsResult(findings=tuple(findings), referents=tuple(referents))

    def _build_concentration_finding(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        shape: ConcentrationShape,
        spec: AnalysisSpec,
        qualified: bool,
        session_id: str,
        investigation_id: str,
    ) -> tuple[Finding, RegisteredReferent]:
        schema = shape.frame.schema
        value = row[schema.index_of(shape.measure)]
        rank = _as_int(row[schema.index_of(shape.rank_column)])
        share = row[schema.index_of(shape.share_column)] if shape.share_column else None
        label = self._row_label(row, shape.frame, shape.dimension_columns)
        metric_label = shape.measure.replace("_", " ")
        window = spec.context.window.range

        amount = _as_int(value)
        magnitude = (
            f"${abs(amount) // 100:,}" if shape.is_money and amount is not None else f"{value!r}"
        )
        # share_of_total divides by the VISIBLE total, so with suppressed
        # cells "% of total" would overstate the concentration. Say which
        # total it is rather than quietly meaning a different one.
        share_basis = "visible total" if shape.frame.suppressed_cells else "total"
        share_text = (
            f" ({float(share):.1%} of {share_basis})" if isinstance(share, Decimal) else ""
        )
        title = f"{label}: {magnitude} {metric_label}{share_text}"
        statement = (
            f"{label} ranks #{rank} by {metric_label} over "
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
