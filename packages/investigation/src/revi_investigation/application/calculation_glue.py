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

from dataclasses import dataclass
from decimal import Decimal

from revi_investigation.application.capability_ports import PackPort, TransformPort
from revi_investigation.application.execution import ExecutedProbe
from revi_investigation.application.planning import InvestigationPlan, TransformPlanStep
from revi_kernel.errors import ReconciliationFailedError, UnsupportedConceptError
from revi_kernel.frame import EvidenceFrame, TransformProvenance
from revi_kernel.probes import AggregationProbe, SnapshotProbe
from revi_kernel.refs import MetricRef


@dataclass(frozen=True, slots=True)
class OperatorApplication:
    operator: str
    version: str
    inputs: tuple[str, ...]
    output: str


@dataclass(frozen=True, slots=True)
class CalculationResult:
    """All logical frames (probe frames post-ratio plus derived frames), in
    creation order, with the operator log."""

    frames: tuple[tuple[str, EvidenceFrame], ...]
    operations: tuple[OperatorApplication, ...]

    def frame(self, frame_id: str) -> EvidenceFrame:
        for name, frame in self.frames:
            if name == frame_id:
                return frame
        raise KeyError(f"no frame {frame_id!r} in calculation result")


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

        for item in executed:
            frame = self._evaluate_ratios(plan, item, operations)
            frames[item.node_id] = frame
            order.append(item.node_id)

        for step in plan.transforms.steps:
            derived = self._apply_step(step, frames, operations)
            if derived is not None:
                frames[step.id] = derived
                order.append(step.id)

        return CalculationResult(
            frames=tuple((name, frames[name]) for name in order),
            operations=tuple(operations),
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
    ) -> EvidenceFrame | None:
        transforms = self._transforms
        operator = step.operator

        if operator == "compare":
            current, prior = self._input_frames(step, frames, 2)
            out = transforms.compare(current, prior)
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
