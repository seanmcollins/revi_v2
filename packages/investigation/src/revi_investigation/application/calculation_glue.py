"""Deterministic post-retrieval calculation (design §8.1 step 11).

Two stages, both through the :class:`TransformPort` seam (versioned kernel
operators — the engine itself never does arithmetic):

1. **Ratio evaluation.** Every ratio contract's component columns
   (``<id>__num`` / ``<id>__den``, the adapter convention) are folded into
   the metric column via the ``ratio`` operator (ratio-of-sums per cell —
   the slicing law is structural).
2. **Transform steps.** The plan's typed steps run in order through a
   small registry mapping operator names to port methods, each with typed
   argument parsing. Steps reference logical frame ids (probe node ids and
   prior step ids); every application is logged with the operator version
   from the output frame's provenance, for the trace.

``reconcile`` is the one non-frame operator: its verdict is logged, and a
failed reconciliation raises ``RECONCILIATION_FAILED`` — never silent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from revi_investigation.application.capability_ports import PackPort, TransformPort
from revi_investigation.application.execution import ExecutedProbe
from revi_investigation.application.planning import InvestigationPlan, TransformPlanStep
from revi_kernel.errors import ReconciliationFailedError, UnsupportedConceptError
from revi_kernel.filters import Predicate, Scalar, iter_predicates
from revi_kernel.frame import EvidenceFrame, FrameRow, TransformProvenance
from revi_kernel.probes import AggregationProbe, SnapshotProbe
from revi_kernel.refs import DimensionRef, MetricRef


@dataclass(frozen=True, slots=True)
class OperatorApplication:
    operator: str
    version: str
    inputs: tuple[str, ...]
    output: str


class EmptinessKind(StrEnum):
    """Which kind of nothing a turn produced."""

    #: Every probe came back with zero rows. The population is empty.
    NO_ROWS = "no_rows"
    #: Rows exist, and no finding survived selection out of them.
    NO_FINDINGS = "no_findings"


@dataclass(frozen=True, slots=True)
class EmptinessFact:
    """Why a turn has nothing to say, as data rather than as absence.

    An empty answer is the one outcome this engine could not explain. Rows
    came back empty and the turn published a chart with no bars, a
    suppression note, and no statement of the obvious: *nothing matched*.
    Worse, "the filter matched nothing" and "the numbers are all there and
    none of them rose to a finding" rendered identically — as silence —
    while their recoveries are opposite (widen the filter / ask a different
    question).

    So the fact is recorded where it is known and carried to the surface:
    which kind of nothing it was, which frame was looked at, and — for an
    empty population — the predicates that could have emptied it, in the
    analyst's own vocabulary. Nothing here is prose for a user; it is the
    structure a presentation layer writes prose from.
    """

    kind: EmptinessKind
    #: The frame the verdict was reached on (a ranked/compared frame for
    #: ``NO_FINDINGS``, the first empty probe for ``NO_ROWS``).
    frame_id: str | None
    #: One-line machine-readable summary, for traces and logs.
    detail: str
    #: Filter clauses in force on the emptied probe — the suspects, listed
    #: in the order they were applied, never ranked by a guess at which one
    #: did it.
    predicates: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, object]:
        """Trace/API shape (§14) — a mapping, not an English sentence."""
        return {
            "kind": self.kind.value,
            "frame_id": self.frame_id,
            "detail": self.detail,
            "predicates": list(self.predicates),
        }


def _predicate_label(predicate: Predicate) -> str:
    values = ", ".join(str(v) for v in predicate.values)
    return f"{predicate.dimension.id} {predicate.op.value} [{values}]".strip()


@dataclass(frozen=True, slots=True)
class CalculationResult:
    """All logical frames (probe frames post-ratio plus derived frames), in
    creation order, with the operator log."""

    frames: tuple[tuple[str, EvidenceFrame], ...]
    operations: tuple[OperatorApplication, ...]
    #: Set when every probe in the plan returned zero rows — see
    #: :class:`EmptinessFact`. ``None`` means at least one probe had data.
    emptiness: EmptinessFact | None = None
    #: Turn warnings this stage produced — today, the comparison cells whose
    #: prior side was never retrieved (see :func:`_mark_unmatched_unknown`).
    #: Carried rather than logged because a fabricated zero that is silently
    #: repaired is still an answer the reader cannot check.
    warnings: tuple[str, ...] = ()

    def frame(self, frame_id: str) -> EvidenceFrame:
        for name, frame in self.frames:
            if name == frame_id:
                return frame
        raise KeyError(f"no frame {frame_id!r} in calculation result")


#: Suffixes the ``compare`` operator appends per measure. ``__prior`` is the
#: baseline value, the other two are derived from it — so when the baseline
#: is unknown, all three are.
_COMPARE_SUFFIXES = ("__prior", "__delta", "__pct_change")


def _dimension_columns(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(c.name for c in frame.schema.columns if isinstance(c.ref, DimensionRef))


def _key_set(frame: EvidenceFrame, columns: tuple[str, ...]) -> set[tuple[Scalar, ...]]:
    indices = tuple(frame.schema.index_of(name) for name in columns)
    return {tuple(row[i] for i in indices) for row in frame.rows}


def _mark_unmatched_unknown(
    step_id: str, current: EvidenceFrame, prior: EvidenceFrame, out: EvidenceFrame
) -> tuple[EvidenceFrame, str | None]:
    """A cell missing from a TRUNCATED side is UNKNOWN, never zero.

    ``compare`` fills a missing side with 0 for additive units, which is
    correct when the side was read whole: a denial code with no denied
    dollars last quarter really did have none. It is a fabrication when the
    side was top-N limited, because the cell was not absent — it was not
    retrieved. That publishes "CO / 16 — Missing or invalid information:
    denied dollars moved from $0.00 to $41,918.23" at direct/high with an
    impact figure over a cell that had FALLEN $15,780 across the same
    window: the sign inverted, the movement invented, and the number
    ranked.

    The planner now reads the prior side whole wherever the catalog says it
    can (see ``_pair_comparisons``), so this is the backstop for the cuts it
    cannot. Nothing is repaired quietly: the affected cells lose their
    prior, delta and pct_change — a NULL delta is excluded from every
    movement ranking downstream by construction — and the turn says how
    many and why.
    """
    keys = _dimension_columns(out)
    if not keys:
        return out, None
    unknown_prior = prior.truncated
    unknown_current = current.truncated
    if not (unknown_prior or unknown_current):
        return out, None
    prior_keys = _key_set(prior, keys) if unknown_prior else None
    current_keys = _key_set(current, keys) if unknown_current else None
    names = out.schema.names
    key_idx = tuple(out.schema.index_of(name) for name in keys)
    measures = tuple(
        name
        for name in names
        if isinstance(out.schema.columns[out.schema.index_of(name)].ref, MetricRef)
        and not name.endswith(_COMPARE_SUFFIXES)
    )
    derived = tuple(
        out.schema.index_of(f"{m}{suffix}")
        for m in measures
        for suffix in _COMPARE_SUFFIXES
        if f"{m}{suffix}" in names
    )
    current_cols = tuple(out.schema.index_of(m) for m in measures)
    rows: list[FrameRow] = []
    missing_prior = 0
    missing_current = 0
    for row in out.rows:
        key = tuple(row[i] for i in key_idx)
        values = list(row)
        blank: set[int] = set()
        if prior_keys is not None and key not in prior_keys:
            missing_prior += 1
            blank.update(derived)
        if current_keys is not None and key not in current_keys:
            missing_current += 1
            blank.update(derived)
            blank.update(current_cols)
        for i in blank:
            values[i] = None
        rows.append(tuple(values))
    if not (missing_prior or missing_current):
        return out, None
    parts: list[str] = []
    if missing_prior:
        parts.append(
            f"{missing_prior} cell(s) present now were outside the prior window's top-N and "
            "their prior value was never retrieved"
        )
    if missing_current:
        parts.append(
            f"{missing_current} cell(s) in the prior window were outside this window's top-N "
            "and their current value was never retrieved"
        )
    warning = (
        f"comparison_prior_unknown: on {step_id}, " + "; ".join(parts) + ". Those cells publish "
        "no prior figure, no movement and no impact — a value this plan did not read is "
        "UNKNOWN, not zero — and they are excluded from every movement ranking on this turn. "
        "Ask for the full breakdown to compare them."
    )
    return replace(out, rows=tuple(rows)), warning


def _operator_version(frame: EvidenceFrame, fallback: str) -> str:
    if isinstance(frame.provenance, TransformProvenance):
        return frame.provenance.operator_version
    return fallback


def _required_arg(step: TransformPlanStep, name: str) -> str:
    value = step.arg(name)
    if value is None:
        raise UnsupportedConceptError(
            f"transform step '{step.id}' ({step.operator}) is missing argument {name!r}",
            details={"step": step.id, "operator": step.operator, "argument": name},
        )
    return value


def _bool_arg(step: TransformPlanStep, name: str, default: bool) -> bool:
    value = step.arg(name)
    if value is None:
        return default
    return value.strip().lower() not in ("false", "0", "no")


def _int_arg(step: TransformPlanStep, name: str) -> int:
    raw = _required_arg(step, name)
    try:
        return int(raw)
    except ValueError:
        raise UnsupportedConceptError(
            f"transform step '{step.id}': argument {name!r} must be an integer, got {raw!r}",
            details={"step": step.id, "argument": name},
        ) from None


def _tuple_arg(step: TransformPlanStep, name: str) -> tuple[str, ...]:
    raw = step.arg(name)
    if raw is None or not raw.strip():
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


class CalculateMetricsService:
    """Apply metric contracts and planned transforms deterministically."""

    def __init__(self, transforms: TransformPort, pack: PackPort) -> None:
        self._transforms = transforms
        self._pack = pack

    def calculate(
        self, plan: InvestigationPlan, executed: tuple[ExecutedProbe, ...]
    ) -> CalculationResult:
        frames: dict[str, EvidenceFrame] = {}
        order: list[str] = []
        operations: list[OperatorApplication] = []
        warnings: list[str] = []

        for item in executed:
            frame = self._evaluate_ratios(plan, item, operations)
            frames[item.node_id] = frame
            order.append(item.node_id)

        for step in plan.transforms.steps:
            derived = self._apply_step(step, frames, operations, warnings)
            if derived is not None:
                frames[step.id] = derived
                order.append(step.id)

        return CalculationResult(
            frames=tuple((name, frames[name]) for name in order),
            operations=tuple(operations),
            emptiness=self._emptiness(plan, executed),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _emptiness(
        plan: InvestigationPlan, executed: tuple[ExecutedProbe, ...]
    ) -> EmptinessFact | None:
        """Did every probe come back empty, and what was filtering them?

        Recorded here because this is the last stage that can still see the
        probes *and* their results together: by the time an answer is being
        composed, "no rows" is indistinguishable from "no findings", and
        both look like a system with nothing to say rather than a
        population with nothing in it.
        """
        if not executed or any(item.frame.rows for item in executed):
            return None
        first = executed[0]
        predicates: list[str] = []
        try:
            probe = plan.node(first.node_id).probe
        except KeyError:  # pragma: no cover - executed nodes come from the plan
            probe = None
        if isinstance(probe, (AggregationProbe, SnapshotProbe)):
            predicates = [_predicate_label(p) for p in iter_predicates(probe.scope)]
        return EmptinessFact(
            kind=EmptinessKind.NO_ROWS,
            frame_id=first.node_id,
            detail=(
                f"every probe in this plan returned zero rows "
                f"({len(executed)} probe(s) executed)"
            ),
            predicates=tuple(predicates),
        )

    # -------------------------------------------------------------- ratios

    def _evaluate_ratios(
        self,
        plan: InvestigationPlan,
        item: ExecutedProbe,
        operations: list[OperatorApplication],
    ) -> EvidenceFrame:
        node = plan.node(item.node_id)
        probe = node.probe
        if not isinstance(probe, (AggregationProbe, SnapshotProbe)):
            return item.frame
        frame = item.frame
        for ref in probe.measures:
            contract = self._pack.metric(ref.id)
            if contract is None or not contract.is_ratio:
                continue
            numerator = f"{ref.id}__num"
            denominator = f"{ref.id}__den"
            if numerator not in frame.schema.names or denominator not in frame.schema.names:
                continue
            frame = self._transforms.ratio(
                frame,
                numerator=numerator,
                denominator=denominator,
                out=ref.id,
                out_ref=MetricRef(ref.id),
                contract_version=contract.version,
            )
            operations.append(
                OperatorApplication(
                    operator="ratio",
                    version=_operator_version(frame, "?"),
                    inputs=(item.node_id,),
                    output=item.node_id,
                )
            )
        return frame

    # --------------------------------------------------------------- steps

    def _input_frames(
        self, step: TransformPlanStep, frames: dict[str, EvidenceFrame], count: int
    ) -> tuple[EvidenceFrame, ...]:
        if len(step.inputs) != count:
            raise UnsupportedConceptError(
                f"transform step '{step.id}' ({step.operator}) expects {count} inputs, "
                f"got {len(step.inputs)}",
                details={"step": step.id, "operator": step.operator},
            )
        resolved: list[EvidenceFrame] = []
        for input_id in step.inputs:
            if input_id not in frames:
                raise LookupError(
                    f"transform step '{step.id}' references unknown frame {input_id!r}"
                )
            resolved.append(frames[input_id])
        return tuple(resolved)

    def _apply_step(
        self,
        step: TransformPlanStep,
        frames: dict[str, EvidenceFrame],
        operations: list[OperatorApplication],
        warnings: list[str],
    ) -> EvidenceFrame | None:
        transforms = self._transforms
        operator = step.operator

        if operator == "compare":
            current, prior = self._input_frames(step, frames, 2)
            out = transforms.compare(current, prior)
            out, unknown = _mark_unmatched_unknown(step.id, current, prior, out)
            if unknown is not None:
                warnings.append(unknown)
        elif operator == "share_of_total":
            (frame,) = self._input_frames(step, frames, 1)
            out = transforms.share_of_total(
                frame, measure=_required_arg(step, "measure"), within=_tuple_arg(step, "within")
            )
        elif operator == "rank":
            (frame,) = self._input_frames(step, frames, 1)
            out = transforms.rank(
                frame,
                by=_required_arg(step, "by"),
                descending=_bool_arg(step, "descending", True),
            )
        elif operator == "top_k":
            (frame,) = self._input_frames(step, frames, 1)
            per_group = _tuple_arg(step, "per_group")
            out = transforms.top_k(
                frame,
                by=_required_arg(step, "by"),
                k=_int_arg(step, "k"),
                per_group=per_group or None,
            )
        elif operator == "pivot":
            (frame,) = self._input_frames(step, frames, 1)
            out = transforms.pivot(
                frame,
                index=_tuple_arg(step, "index"),
                column=_required_arg(step, "column"),
                measure=_required_arg(step, "measure"),
            )
        elif operator == "decompose":
            current, prior = self._input_frames(step, frames, 2)
            cells = _tuple_arg(step, "cells")
            out = transforms.decompose(
                current,
                prior,
                volume=_required_arg(step, "volume"),
                value=_required_arg(step, "value"),
                cells=cells or None,
            )
        elif operator == "project_lagged_realization":
            inventory, curves, inflow, baseline = self._input_frames(step, frames, 4)
            out = transforms.project_lagged_realization(
                inventory,
                curves,
                inflow,
                baseline,
                horizon_weeks=_int_arg(step, "horizon_weeks"),
            )
        elif operator == "reconcile":
            parent, children = self._input_frames(step, frames, 2)
            allowance = step.arg("suppression_allowance")
            verdict = transforms.reconcile(
                parent,
                children,
                measures=_tuple_arg(step, "measures"),
                suppression_allowance=Decimal(allowance) if allowance is not None else Decimal(0),
            )
            operations.append(
                OperatorApplication(
                    operator="reconcile",
                    version="port",
                    inputs=step.inputs,
                    output=f"{step.id}:{'passed' if verdict.passed else 'failed'}",
                )
            )
            if not verdict.passed:
                raise ReconciliationFailedError(
                    f"reconciliation failed at step '{step.id}': {verdict.summary}",
                    details={"step": step.id},
                )
            return None
        else:
            raise UnsupportedConceptError(
                f"unknown transform operator {operator!r} in plan step '{step.id}'",
                details={"step": step.id, "operator": operator},
            )

        operations.append(
            OperatorApplication(
                operator=operator,
                version=_operator_version(out, "?"),
                inputs=step.inputs,
                output=step.id,
            )
        )
        return out
