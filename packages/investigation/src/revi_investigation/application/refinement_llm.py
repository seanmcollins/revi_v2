"""Follow-up-turn LLM services (design §8.2 steps 2-3) and the DTO → domain
operator converter.

The LLM's job here is exactly two closed-set choices: which shown referents
an utterance points at (validated against the live registry —
``REFERENT_NOT_FOUND`` on drift) and which of the twelve refinement
operators express the request (``AMBIGUOUS_REFINEMENT`` → clarification
when the model can't say). Everything downstream of the validated
operators is deterministic; the typed-gesture path enters at the converter
with no LLM involvement at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.llm.render import (
    LoadedTemplate,
    load_template,
    render_template,
)
from revi_investigation.application.llm.schemas import (
    AbsoluteWindowModel,
    AddFilterModel,
    AnyRefinementOperator,
    DrillIntoModel,
    ExpandModel,
    ExplainModel,
    PivotModel,
    RankByModel,
    ReferentResolutionResponse,
    RefinementEmissionResponse,
    RemoveFilterModel,
    ResetContextModel,
    SetComparisonModel,
    SetDimensionsModel,
    SetGrainModel,
    SetWindowModel,
    WindowSpecModel,
    sanitize_json_schema,
)
from revi_investigation.application.ports import (
    LanguageModelPort,
    LlmUsage,
    RegisteredReferent,
    StructuredLlmRequest,
)
from revi_investigation.domain.refinements import (
    AddFilter,
    DrillInto,
    Expand,
    Explain,
    Pivot,
    RankBy,
    Refinement,
    RemoveFilter,
    ResetContext,
    SetComparison,
    SetDimensions,
    SetGrain,
    SetWindow,
)
from revi_investigation.domain.turns import ClarificationRequest
from revi_kernel.errors import ReferentNotFoundError
from revi_kernel.filters import Predicate, PredicateOp, Scalar
from revi_kernel.refs import (
    DateBasisRef,
    DimensionRef,
    EntityGrain,
    Grain,
    MetricRef,
    ReferentId,
    TimeBucket,
)
from revi_kernel.scope import (
    AbsoluteRange,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
)

_MIN_RESOLUTION_CONFIDENCE = 0.5
_MAX_REFERENT_LINES = 60


def referent_lines(entries: tuple[RegisteredReferent, ...]) -> str:
    """Serialize the live registry for prompts: ids + labels, never data."""
    lines = [f"- {entry.referent.value}: {entry.label}" for entry in entries]
    if not lines:
        return "- (nothing has been shown yet)"
    return "\n".join(lines[-_MAX_REFERENT_LINES:])


@dataclass(frozen=True, slots=True)
class ReferentResolution:
    mention: str
    referent: ReferentId
    confidence: float


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    resolutions: tuple[ReferentResolution, ...]
    clarification: ClarificationRequest | None
    usage: LlmUsage
    template_hash: str


class ResolveReferentsService:
    """LLM anaphora resolution against the live registry (design §7.6)."""

    def __init__(self, llm: LanguageModelPort) -> None:
        self._llm = llm
        self._template: LoadedTemplate = load_template("resolve_referents", "v1")
        self._schema = sanitize_json_schema(ReferentResolutionResponse.model_json_schema())

    async def resolve(
        self, question: str, entries: tuple[RegisteredReferent, ...]
    ) -> ResolutionOutcome:
        prompt = render_template(
            self._template.text,
            {"question": question, "referents": referent_lines(entries)},
        )
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
            )
        )
        clarify = ClarificationRequest(
            question="Which of the shown items do you mean?",
            reason="referent resolution returned no structured output",
        )
        if result.output is None:
            return ResolutionOutcome((), clarify, result.usage, self._template.sha256)
        try:
            parsed = ReferentResolutionResponse.model_validate(dict(result.output))
        except ValidationError:
            return ResolutionOutcome((), clarify, result.usage, self._template.sha256)
        by_value = {entry.referent.value: entry.referent for entry in entries}
        resolutions: list[ReferentResolution] = []
        for item in parsed.resolutions:
            referent = by_value.get(item.referent_id)
            if referent is None:
                raise ReferentNotFoundError(
                    f"resolved referent {item.referent_id!r} is not in the live registry",
                    details={"referent": item.referent_id, "mention": item.mention},
                )
            if item.confidence < _MIN_RESOLUTION_CONFIDENCE:
                return ResolutionOutcome(
                    (),
                    ClarificationRequest(
                        question=(
                            f"By {item.mention!r}, do you mean {item.referent_id} "
                            f"({by_value[item.referent_id].kind.value})?"
                        ),
                        reason=f"referent resolution confidence {item.confidence:.2f}",
                    ),
                    result.usage,
                    self._template.sha256,
                )
            resolutions.append(
                ReferentResolution(
                    mention=item.mention, referent=referent, confidence=item.confidence
                )
            )
        return ResolutionOutcome(tuple(resolutions), None, result.usage, self._template.sha256)


@dataclass(frozen=True, slots=True)
class EmissionOutcome:
    operators: tuple[AnyRefinementOperator, ...] | None
    rationale: str
    clarification: ClarificationRequest | None
    usage: LlmUsage
    template_hash: str


class EmitRefinementsService:
    """LLM compilation of a follow-up into the closed operator set (§7.4)."""

    def __init__(self, llm: LanguageModelPort) -> None:
        self._llm = llm
        self._template: LoadedTemplate = load_template("emit_refinements", "v1")
        self._schema = sanitize_json_schema(RefinementEmissionResponse.model_json_schema())

    async def emit(
        self,
        question: str,
        *,
        context_summary: str,
        entries: tuple[RegisteredReferent, ...],
        resolutions: tuple[ReferentResolution, ...],
        dimension_lines: str,
        metric_lines: str,
    ) -> EmissionOutcome:
        resolution_lines = (
            "\n".join(
                f"- {r.mention!r} -> {r.referent.value} (confidence {r.confidence:.2f})"
                for r in resolutions
            )
            or "- (none)"
        )
        prompt = render_template(
            self._template.text,
            {
                "question": question,
                "context": context_summary,
                "referents": referent_lines(entries),
                "resolutions": resolution_lines,
                "dimensions": dimension_lines,
                "metrics": metric_lines,
            },
        )
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
            )
        )
        if result.output is None:
            return EmissionOutcome(
                operators=None,
                rationale="",
                clarification=ClarificationRequest(
                    question=(
                        "I couldn't turn that into a concrete refinement of the current "
                        "answer — could you say it another way?"
                    ),
                    reason="AMBIGUOUS_REFINEMENT: no structured operator emission",
                ),
                usage=result.usage,
                template_hash=self._template.sha256,
            )
        try:
            parsed = RefinementEmissionResponse.model_validate(dict(result.output))
        except ValidationError:
            return EmissionOutcome(
                operators=None,
                rationale="",
                clarification=ClarificationRequest(
                    question=(
                        "I couldn't turn that into a concrete refinement of the current "
                        "answer — could you say it another way?"
                    ),
                    reason="AMBIGUOUS_REFINEMENT: operator emission failed schema validation",
                ),
                usage=result.usage,
                template_hash=self._template.sha256,
            )
        if not parsed.operators:
            return EmissionOutcome(
                operators=None,
                rationale=parsed.rationale,
                clarification=ClarificationRequest(
                    question="What would you like to change about the current answer?",
                    reason=f"AMBIGUOUS_REFINEMENT: {parsed.rationale or 'no operators emitted'}",
                ),
                usage=result.usage,
                template_hash=self._template.sha256,
            )
        return EmissionOutcome(
            operators=tuple(parsed.operators),
            rationale=parsed.rationale,
            clarification=None,
            usage=result.usage,
            template_hash=self._template.sha256,
        )


# ---------------------------------------------------------------------------
# DTO → domain conversion (shared by the LLM path and the typed-gesture path)


def _scalar(value: str | int | float | bool | None) -> Scalar:
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _window(model: WindowSpecModel | AbsoluteWindowModel) -> RelativeRange | AbsoluteRange:
    if isinstance(model, AbsoluteWindowModel):
        return AbsoluteRange(start=model.start, end=model.end)
    return RelativeRange(
        quantity=Decimal(model.quantity), unit=TimeUnit(model.unit), mode=RangeMode(model.mode)
    )


def _referent(value: str, registry_index: dict[str, ReferentId]) -> ReferentId:
    referent = registry_index.get(value)
    if referent is None:
        raise ReferentNotFoundError(
            f"operator targets referent {value!r}, which is not in the live registry",
            details={"referent": value},
        )
    return referent


def to_domain_operators(
    operators: tuple[AnyRefinementOperator, ...],
    registry_index: dict[str, ReferentId],
) -> tuple[Refinement, ...]:
    """Convert validated DTO operators into domain operators.

    ``drill_into``/``explain`` targets resolve through the live registry
    (``REFERENT_NOT_FOUND`` on unknown handles). A ``set_comparison`` with
    a ``custom`` range means a CUSTOM comparison regardless of ``kind``.
    """
    out: list[Refinement] = []
    for op in operators:
        if isinstance(op, SetDimensionsModel):
            out.append(SetDimensions(tuple(DimensionRef(d) for d in op.dimensions)))
        elif isinstance(op, AddFilterModel):
            out.append(
                AddFilter(
                    Predicate(
                        dimension=DimensionRef(op.dimension),
                        op=PredicateOp(op.predicate_op),
                        values=tuple(_scalar(v) for v in op.values),
                    )
                )
            )
        elif isinstance(op, RemoveFilterModel):
            out.append(RemoveFilter(DimensionRef(op.dimension)))
        elif isinstance(op, SetWindowModel):
            out.append(
                SetWindow(
                    window=_window(op.window),
                    basis=DateBasisRef(op.basis.lower()) if op.basis is not None else None,
                )
            )
        elif isinstance(op, SetComparisonModel):
            if op.custom is not None:
                out.append(
                    SetComparison(
                        kind=ComparisonKind.CUSTOM,
                        custom=AbsoluteRange(start=op.custom.start, end=op.custom.end),
                    )
                )
            elif op.kind is not None:
                out.append(SetComparison(kind=ComparisonKind(op.kind)))
            else:
                out.append(SetComparison(kind=None))
        elif isinstance(op, SetGrainModel):
            bucket = TimeBucket(op.time_bucket) if op.time_bucket is not None else None
            out.append(SetGrain(Grain(EntityGrain(op.entity), bucket)))
        elif isinstance(op, DrillIntoModel):
            out.append(DrillInto(_referent(op.target, registry_index)))
        elif isinstance(op, PivotModel):
            out.append(Pivot(tuple(MetricRef(m) for m in op.measures)))
        elif isinstance(op, ExplainModel):
            out.append(Explain(_referent(op.target, registry_index)))
        elif isinstance(op, RankByModel):
            out.append(RankBy(by=MetricRef(op.by), descending=op.descending))
        elif isinstance(op, ExpandModel):
            out.append(Expand(limit=op.limit))
        else:  # ResetContextModel — the union is closed
            assert isinstance(op, ResetContextModel)
            out.append(ResetContext(keep_pins=op.keep_pins))
    return tuple(out)
