"""Findings evaluation (design §8.1 steps 12-13): certified, referent-
addressable results built from the final frames, with drillable cohorts.

Selection: the primary findings frame is the first ``compare`` output that
carries at least one dimension column and a money measure with its delta.
Rows are ranked by delta **ascending** (the biggest declines of a
higher-is-good measure first) and the top N become findings F1, F2, ...
Each finding carries the current/prior/delta cents and pct change, its
frame's evidence grade, ``impact_cents`` equal to the delta, and — via the
referent registry — a drillable :class:`CohortDefinition` at the CLAIM
entity scoped to the finding's dimension values plus the analysis window,
so a later ``DrillInto`` refinement can pin the exact population shown.

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
_QUALIFIED_GRADES = (EvidenceGrade.PROXY, EvidenceGrade.DISCOVERY, EvidenceGrade.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class FindingsResult:
    findings: tuple[Finding, ...]
    referents: tuple[RegisteredReferent, ...]


@dataclass(frozen=True, slots=True)
class _CompareShape:
    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    money_measure: str


def _dimension_columns(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, DimensionRef) and not col.ref.id.startswith(_TIME_BUCKET_PREFIX)
    )


def _money_measure(frame: EvidenceFrame) -> str | None:
    names = set(frame.schema.names)
    for col in frame.schema.columns:
        if (
            isinstance(col.ref, MetricRef)
            and col.unit == "money_cents"
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
        shape = self._primary_compare(plan, calculation)
        if shape is None:
            return FindingsResult(findings=(), referents=())

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

        findings: list[Finding] = []
        referents: list[RegisteredReferent] = []
        for row in rows[: self._top_n]:
            if _as_int(row[idx_delta]) is None:
                continue
            n = len(findings) + 1
            finding, referent = self._build_finding(
                f"F{n}", row, shape, spec, period_phrase, qualified, session_id, investigation_id
            )
            findings.append(finding)
            referents.append(referent)

        for i, row in enumerate(shape.frame.rows):
            referents.append(
                self._dimension_value_referent(
                    f"D{i + 1}", row, shape, spec, session_id, investigation_id
                )
            )

        await self._registry.register(tuple(referents))
        return FindingsResult(findings=tuple(findings), referents=tuple(referents))

    # ------------------------------------------------------------ selection

    def _primary_compare(
        self, plan: InvestigationPlan, calculation: CalculationResult
    ) -> _CompareShape | None:
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
                return _CompareShape(
                    frame_id=step.id, frame=frame, dimension_columns=dims, money_measure=money
                )
        return None

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
        self, row: tuple[Scalar, ...], shape: _CompareShape, spec: AnalysisSpec
    ) -> CohortDefinition:
        """Drillable cohort: CLAIM entity, current scope narrowed to this
        row's dimension values, over the analysis window."""
        predicates = tuple(
            Predicate(DimensionRef(dim), PredicateOp.EQ, (row[shape.frame.schema.index_of(dim)],))
            for dim in shape.dimension_columns
        )
        return CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=and_merge(spec.context.effective_scope(), *predicates),
            window=spec.context.window,
        )

    def _row_label(self, row: tuple[Scalar, ...], shape: _CompareShape) -> str:
        return " / ".join(
            str(row[shape.frame.schema.index_of(dim)]) for dim in shape.dimension_columns
        )

    def _build_finding(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        shape: _CompareShape,
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

        label = self._row_label(row, shape)
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
        single_dim = (
            (
                shape.dimension_columns[0],
                str(row[schema.index_of(shape.dimension_columns[0])]),
            )
            if len(shape.dimension_columns) == 1
            else None
        )
        registered = RegisteredReferent(
            referent=referent,
            session_id=session_id,
            investigation_id=investigation_id,
            label=title,
            cohort_definition=self._cohort_definition(row, shape, spec),
            finding=finding,
            dimension_value=single_dim,
        )
        return finding, registered

    def _dimension_value_referent(
        self,
        referent_value: str,
        row: tuple[Scalar, ...],
        shape: _CompareShape,
        spec: AnalysisSpec,
        session_id: str,
        investigation_id: str,
    ) -> RegisteredReferent:
        label = self._row_label(row, shape)
        single_dim = (
            (
                shape.dimension_columns[0],
                str(row[shape.frame.schema.index_of(shape.dimension_columns[0])]),
            )
            if len(shape.dimension_columns) == 1
            else None
        )
        return RegisteredReferent(
            referent=ReferentId(value=referent_value, kind=ReferentKind.DIMENSION_VALUE),
            session_id=session_id,
            investigation_id=investigation_id,
            label=label,
            cohort_definition=self._cohort_definition(row, shape, spec),
            dimension_value=single_dim,
        )
