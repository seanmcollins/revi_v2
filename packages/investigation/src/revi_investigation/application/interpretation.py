"""Turn classification and question interpretation (design §8.1 steps 3-7).

The LLM proposes; deterministic code disposes. Every id the model returns
is validated against the pinned pack snapshot and semantic catalog — an
unknown metric/dimension/playbook/concept is ``UNSUPPORTED_CONCEPT``, and
model ambiguity (missing structured output, an explicit clarification, or
low classification confidence) becomes a :class:`ClarificationRequest`,
which is a successful outcome, never a guess.

Window resolution happens exactly once, here: the anchor is the session
watermark's ``loaded_at.date()`` and the concrete dates are stored on the
spec (replay uses the stored dates). The date basis defaults to the
primary governing metric's primary basis; an explicit basis is validated
against the contract's ``allowed_date_bases`` (``DATE_BASIS_INVALID``).

The DEFINITIONAL path answers from governed pack content with provenance
and ZERO probes: lead-in phrases are stripped deterministically and the
remainder resolves through ``PackSnapshot.resolve_term`` semantics via the
:class:`PackPort` seam ("what is PR3" → the PR group code and CARC 3).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from revi_calculation_contracts.contract import MetricContract
from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.capability_ports import PackPort, TermDefinition
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.llm.render import (
    LoadedTemplate,
    load_template,
    render_template,
)
from revi_investigation.application.llm.schemas import (
    InterpretationResponse,
    TurnClassificationResponse,
    sanitize_json_schema,
)
from revi_investigation.application.ports import (
    LanguageModelPort,
    LlmUsage,
    StructuredLlmRequest,
)
from revi_investigation.domain.context import AnalysisSpec, InvestigationContext
from revi_investigation.domain.records import Session
from revi_investigation.domain.turns import ClarificationRequest, TurnClass, TurnClassification
from revi_kernel.errors import DateBasisInvalidError, UnsupportedConceptError
from revi_kernel.filters import (
    EMPTY_SCOPE,
    FilterExpr,
    Predicate,
    PredicateOp,
    Scalar,
    and_merge,
)
from revi_kernel.refs import DateBasisRef, DimensionRef, Grain, MetricRef
from revi_kernel.scope import (
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    derive_comparison,
    resolve_window,
)

_MIN_CLASSIFICATION_CONFIDENCE = 0.5
_DEFAULT_WINDOW = RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS)
_DESCRIPTION_CLIP = 160

# Deterministic definitional lead-ins, longest first.
_DEFINITIONAL_LEAD_INS = (
    "tell me about",
    "what is the meaning of",
    "what is a",
    "what is an",
    "what does",
    "what are",
    "meaning of",
    "what is",
    "what's",
    "whats",
    "define",
    "explain",
)


@dataclass(frozen=True, slots=True)
class DefinitionalAnswer:
    """A zero-probe answer from governed pack content, with provenance."""

    question: str
    terms: tuple[TermDefinition, ...]
    pack_id: str
    pack_version: str
    pack_snapshot_id: str


@dataclass(frozen=True, slots=True)
class ClassificationOutcome:
    classification: TurnClassification | None
    clarification: ClarificationRequest | None
    usage: LlmUsage
    template_hash: str


@dataclass(frozen=True, slots=True)
class InterpretedInvestigation:
    spec: AnalysisSpec
    playbook_id: str | None
    window_explicit: bool
    intent_summary: str
    metric_ids: tuple[str, ...]
    dimension_ids: tuple[str, ...]
    concept_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterpretationOutcome:
    investigation: InterpretedInvestigation | None
    clarification: ClarificationRequest | None
    definitional: DefinitionalAnswer | None
    usage: LlmUsage
    template_hash: str


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:_DESCRIPTION_CLIP]


def strip_definitional_lead_in(question: str) -> str:
    """Deterministically strip definitional lead-in phrases and trailing
    filler ("what does PR3 mean?" → "PR3")."""
    text = question.strip().strip("?!.").strip().lower()
    for lead in _DEFINITIONAL_LEAD_INS:
        if text.startswith(lead + " "):
            text = text[len(lead) :].strip()
            break
    for trailer in (" mean", " stand for"):
        if text.endswith(trailer):
            text = text[: -len(trailer)].strip()
    return text


class ClassifyTurnService:
    """LLM turn classification against the closed §7.3 taxonomy."""

    def __init__(self, llm: LanguageModelPort) -> None:
        self._llm = llm
        self._template: LoadedTemplate = load_template("classify_turn", "v1")
        self._schema = sanitize_json_schema(TurnClassificationResponse.model_json_schema())

    async def classify(self, question: str) -> ClassificationOutcome:
        prompt = render_template(self._template.text, {"question": question})
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
            return ClassificationOutcome(
                classification=None,
                clarification=ClarificationRequest(
                    question="I couldn't confidently read that request — could you rephrase it?",
                    reason="turn classification returned no structured output",
                ),
                usage=result.usage,
                template_hash=self._template.sha256,
            )
        try:
            parsed = TurnClassificationResponse.model_validate(dict(result.output))
        except ValidationError:
            return ClassificationOutcome(
                classification=None,
                clarification=ClarificationRequest(
                    question="I couldn't confidently read that request — could you rephrase it?",
                    reason="turn classification failed schema validation",
                ),
                usage=result.usage,
                template_hash=self._template.sha256,
            )
        classification = TurnClassification(
            turn_class=TurnClass(parsed.turn_class),
            confidence=parsed.confidence,
            clarification_question=parsed.clarification_question,
        )
        clarification: ClarificationRequest | None = None
        if (
            classification.clarification_question is not None
            or classification.confidence < _MIN_CLASSIFICATION_CONFIDENCE
        ):
            clarification = ClarificationRequest(
                question=classification.clarification_question
                or "Could you say more about what you'd like to investigate?",
                reason=f"turn classification confidence {classification.confidence:.2f}",
            )
        return ClassificationOutcome(
            classification=classification,
            clarification=clarification,
            usage=result.usage,
            template_hash=self._template.sha256,
        )


class InterpretQuestionService:
    """Question → validated AnalysisSpec (or clarification / definitional)."""

    def __init__(self, llm: LanguageModelPort, pack: PackPort, catalog: CatalogSnapshot) -> None:
        self._llm = llm
        self._pack = pack
        self._catalog = catalog
        self._template: LoadedTemplate = load_template("interpret_question", "v1")
        self._schema = sanitize_json_schema(InterpretationResponse.model_json_schema())

    # ---------------------------------------------------------- vocabulary

    def _vocabulary(self) -> dict[str, str]:
        metrics = "\n".join(
            f"- {mid}: {_clip(desc)}" for mid, desc in self._pack.metric_summaries()
        )
        dimensions = "\n".join(
            f"- {dim.id}: {dim.label}" for dim in self._catalog.dimensions if dim.certified
        )
        playbooks = "\n".join(
            f"- {pid}: {_clip(desc)}" for pid, desc in self._pack.playbook_summaries()
        )
        concepts = "\n".join(f"- {cid}: {name}" for cid, name in self._pack.concept_summaries())
        date_bases = ", ".join(basis.id for basis in self._catalog.date_bases)
        return {
            "metrics": metrics,
            "dimensions": dimensions,
            "playbooks": playbooks,
            "concepts": concepts,
            "date_bases": date_bases,
        }

    # ----------------------------------------------------------------- api

    def definitional_answer(self, question: str) -> DefinitionalAnswer:
        """Deterministic pack lookup for the DEFINITIONAL path (zero probes)."""
        stripped = strip_definitional_lead_in(question)
        terms = self._pack.resolve_term(stripped) if stripped else ()
        if not terms and " " in stripped:
            terms = self._pack.resolve_term(stripped.split()[-1])
        return DefinitionalAnswer(
            question=question,
            terms=terms,
            pack_id=self._pack.pack_id,
            pack_version=self._pack.pack_version,
            pack_snapshot_id=self._pack.snapshot_id,
        )

    async def interpret(
        self, question: str, *, session: Session, turn_id: str
    ) -> InterpretationOutcome:
        prompt = render_template(self._template.text, {**self._vocabulary(), "question": question})
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
            )
        )
        template_hash = self._template.sha256
        if result.output is None:
            return self._clarify(
                "I couldn't map that question onto governed content — could you rephrase it?",
                "interpretation returned no structured output",
                result.usage,
                template_hash,
            )
        try:
            parsed = InterpretationResponse.model_validate(dict(result.output))
        except ValidationError:
            return self._clarify(
                "I couldn't map that question onto governed content — could you rephrase it?",
                "interpretation failed schema validation",
                result.usage,
                template_hash,
            )
        if parsed.clarification:
            return self._clarify(parsed.clarification, "model requested clarification",
                                 result.usage, template_hash)

        analytical = bool(parsed.metric_ids or parsed.playbook_id or parsed.dimension_ids)
        if parsed.definitional_terms and not analytical:
            answer = self._definitional_from_terms(question, tuple(parsed.definitional_terms))
            return InterpretationOutcome(
                investigation=None,
                clarification=None,
                definitional=answer,
                usage=result.usage,
                template_hash=template_hash,
            )

        # -- validate EVERY returned id against pack/catalog ---------------
        for metric_id in parsed.metric_ids:
            if self._pack.metric(metric_id) is None:
                raise UnsupportedConceptError(
                    f"interpreted metric {metric_id!r} is not in the pack",
                    details={"metric": metric_id},
                )
        if parsed.playbook_id is not None and self._pack.playbook(parsed.playbook_id) is None:
            raise UnsupportedConceptError(
                f"interpreted playbook {parsed.playbook_id!r} is not in the pack",
                details={"playbook": parsed.playbook_id},
            )
        for dimension_id in parsed.dimension_ids:
            if self._catalog.dimension(dimension_id) is None:
                raise UnsupportedConceptError(
                    f"interpreted dimension {dimension_id!r} is not in the catalog",
                    details={"dimension": dimension_id},
                )
        for concept_id in parsed.concept_ids:
            if not self._pack.has_concept(concept_id):
                raise UnsupportedConceptError(
                    f"interpreted concept {concept_id!r} is not in the pack",
                    details={"concept": concept_id},
                )

        governing = self._governing_contracts(parsed)
        if not governing:
            return self._clarify(
                "Which metric or investigation should I use for that?",
                "no governing metric or playbook resolved",
                result.usage,
                template_hash,
            )
        primary = governing[0]

        basis = self._resolve_basis(parsed.basis, primary)
        anchor = session.watermark.loaded_at.date()
        window_explicit = parsed.window is not None
        requested = self._relative_range(parsed) if parsed.window is not None else _DEFAULT_WINDOW
        window = resolve_window(requested, anchor, basis=basis)

        scope = self._resolve_scope(parsed, turn_id)
        context = InvestigationContext(
            window=window,
            comparison=None,
            scope=scope,
            cohort=None,
            grain=Grain(primary.entity_grain),
            watermark=session.watermark,
            pack_version=session.pack_version,
        )
        if parsed.comparison is not None:
            context = replace(
                context, comparison=derive_comparison(window, ComparisonKind(parsed.comparison))
            )
        spec = AnalysisSpec(
            context=context,
            measures=tuple(MetricRef(mid) for mid in parsed.metric_ids),
            dimensions=tuple(DimensionRef(did) for did in parsed.dimension_ids),
        )
        return InterpretationOutcome(
            investigation=InterpretedInvestigation(
                spec=spec,
                playbook_id=parsed.playbook_id,
                window_explicit=window_explicit,
                intent_summary=parsed.intent_summary,
                metric_ids=tuple(parsed.metric_ids),
                dimension_ids=tuple(parsed.dimension_ids),
                concept_ids=tuple(parsed.concept_ids),
            ),
            clarification=None,
            definitional=None,
            usage=result.usage,
            template_hash=template_hash,
        )

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _clarify(
        question: str, reason: str, usage: LlmUsage, template_hash: str
    ) -> InterpretationOutcome:
        return InterpretationOutcome(
            investigation=None,
            clarification=ClarificationRequest(question=question, reason=reason),
            definitional=None,
            usage=usage,
            template_hash=template_hash,
        )

    def _definitional_from_terms(
        self, question: str, raw_terms: tuple[str, ...]
    ) -> DefinitionalAnswer:
        matches: list[TermDefinition] = []
        for term in raw_terms:
            for match in self._pack.resolve_term(term):
                if match not in matches:
                    matches.append(match)
        return DefinitionalAnswer(
            question=question,
            terms=tuple(matches),
            pack_id=self._pack.pack_id,
            pack_version=self._pack.pack_version,
            pack_snapshot_id=self._pack.snapshot_id,
        )

    def _governing_contracts(self, parsed: InterpretationResponse) -> tuple[MetricContract, ...]:
        contracts: list[MetricContract] = []
        for metric_id in parsed.metric_ids:
            contract = self._pack.metric(metric_id)
            assert contract is not None  # validated above
            contracts.append(contract)
        if not contracts and parsed.playbook_id is not None:
            playbook = self._pack.playbook(parsed.playbook_id)
            assert playbook is not None  # validated above
            seen: set[str] = set()
            for template in playbook.probes:
                for metric_id in template.metric_ids:
                    if metric_id in seen:
                        continue
                    seen.add(metric_id)
                    contract = self._pack.metric(metric_id)
                    if contract is not None:
                        contracts.append(contract)
        return tuple(contracts)

    @staticmethod
    def _resolve_basis(raw: str | None, primary: MetricContract) -> DateBasisRef:
        if raw is None:
            return primary.primary_date_basis
        basis = DateBasisRef(raw.strip().lower())
        if not primary.allows_date_basis(basis):
            raise DateBasisInvalidError(
                f"date basis {basis.id!r} is not allowed for metric {primary.id!r} "
                f"(allowed: {[b.id for b in primary.allowed_date_bases]})",
                details={"metric": primary.id, "basis": basis.id},
            )
        return basis

    @staticmethod
    def _relative_range(parsed: InterpretationResponse) -> RelativeRange:
        assert parsed.window is not None
        try:
            quantity = Decimal(parsed.window.quantity)
        except InvalidOperation:
            raise UnsupportedConceptError(
                f"window quantity {parsed.window.quantity!r} is not a decimal",
                details={"quantity": parsed.window.quantity},
            ) from None
        return RelativeRange(
            quantity=quantity,
            unit=TimeUnit(parsed.window.unit),
            mode=RangeMode(parsed.window.mode),
        )

    def _resolve_scope(self, parsed: InterpretationResponse, turn_id: str) -> FilterExpr:
        predicates: list[Predicate] = []
        for entry in parsed.scope:
            if self._catalog.dimension(entry.dimension) is None:
                raise UnsupportedConceptError(
                    f"scope dimension {entry.dimension!r} is not in the catalog",
                    details={"dimension": entry.dimension},
                )
            values = tuple(self._scalar(value) for value in entry.values)
            predicates.append(
                Predicate(
                    dimension=DimensionRef(entry.dimension),
                    op=PredicateOp(entry.op),
                    values=values,
                    origin_turn=turn_id,
                )
            )
        if not predicates:
            return EMPTY_SCOPE
        return and_merge(*predicates)

    @staticmethod
    def _scalar(value: str | int | float | bool | None) -> Scalar:
        if isinstance(value, float):
            return Decimal(str(value))
        return value
