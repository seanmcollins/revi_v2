"""Turn classification and question interpretation (design §8.1 steps 3-7).

The LLM proposes; deterministic code disposes. Every id the model returns
is validated against the pinned pack snapshot and semantic catalog — an
unknown metric/dimension/playbook/concept is ``UNSUPPORTED_CONCEPT``, and
model ambiguity (missing structured output, an explicit clarification, or
low classification confidence) becomes a :class:`ClarificationRequest`,
which is a successful outcome, never a guess.

A clarification says which of two things happened, because the recoveries
differ: ``LlmFailureKind.SCHEMA`` means the answer never arrived in a
readable shape and asking again may simply work, while a model that
declined (or a demo script with no entry) will decline identically until
the question changes. When the model proposes ways forward they ride along
as ``ClarificationRequest.options`` — deterministically trimmed, never
invented here.

Window resolution happens exactly once, here: the anchor is the session
watermark's ``newest_data_date`` — the newest activity the load can see,
never the load's own clock and never wall-clock today — and the concrete
dates are stored on the spec (replay uses the stored dates). A window
nobody asked for is stated as an assumption rather than left in the debug
payload. The date basis defaults to the
primary governing metric's primary basis; an explicit basis is validated
against the contract's ``allowed_date_bases`` (``DATE_BASIS_INVALID``) and
then against what this warehouse actually binds at the metric's grain —
see :mod:`revi_investigation.application.date_basis`, which is why the
window's basis (and therefore the context header) can never name a basis
no probe was able to read.

The DEFINITIONAL path answers from governed pack content with provenance
and ZERO probes: lead-in phrases are stripped deterministically and the
remainder resolves through ``PackSnapshot.resolve_term`` semantics via the
:class:`PackPort` seam ("what is PR3" → the PR group code and CARC 3).

``from_typed_spec`` is the typed twin of ``interpret``: a caller that
already knows what it wants states it in the typed vocabulary and the same
deterministic disposal runs — pack/catalog id validation, basis legality,
one-shot window resolution — with zero model calls. It exists so that
surfaces with an already-typed intent (a portfolio card, a chart click in
a fresh session) can open an investigation instead of being told there is
nothing to refine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from revi_calculation_contracts.contract import MetricContract
from revi_catalog_contracts.model import CatalogSnapshot, normalize_synonym
from revi_investigation.application.anchoring import window_anchor
from revi_investigation.application.capability_ports import PackPort, TermDefinition
from revi_investigation.application.date_basis import resolve_answerable_basis
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.llm.render import (
    LoadedTemplate,
    load_template,
    render_template,
)
from revi_investigation.application.llm.schemas import (
    GroundedOptionModel,
    InterpretationResponse,
    TurnClassificationResponse,
    clarification_options,
    sanitize_json_schema,
)
from revi_investigation.application.ports import (
    DEFAULT_LLM_CALL_POLICY,
    LanguageModelPort,
    LlmCallPolicy,
    LlmFailureKind,
    LlmUsage,
    StructuredLlmRequest,
    failure_note,
    retry_may_help,
)
from revi_investigation.application.validation import contract_pinned_values
from revi_investigation.domain.context import (
    AnalysisSpec,
    AskedDirection,
    AskedMagnitude,
    InvestigationContext,
)
from revi_investigation.domain.records import Session
from revi_investigation.domain.turns import ClarificationRequest, TurnClass, TurnClassification
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    WindowSpecModel,
)
from revi_kernel.errors import UnsupportedConceptError
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
    AbsoluteRange,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    TimeWindow,
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

# Coming up empty has two honest shapes and they want opposite advice. A
# model that read the utterance and had no mapping for it wants a different
# wording; an answer that never arrived in a readable shape wants the same
# wording again. Telling an analyst to rephrase a question that was never
# the problem is how a platform teaches people it cannot be trusted.
_CLASSIFY_REPHRASE = "I couldn't confidently read that request — could you rephrase it?"
_CLASSIFY_RETRY = "I hit a problem reading that just now — please try again."
_INTERPRET_REPHRASE = "I couldn't map that question onto governed content — could you rephrase it?"
_INTERPRET_RETRY = "I hit a problem working that out just now — please try again."


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
    #: Why the call came back empty-handed, when it did. The clarification
    #: reason already spells it for a reader; this carries it as data so a
    #: trace consumer does not have to parse English to chart it.
    failure: LlmFailureKind | None = None


@dataclass(frozen=True, slots=True)
class InterpretedInvestigation:
    spec: AnalysisSpec
    playbook_id: str | None
    window_explicit: bool
    intent_summary: str
    metric_ids: tuple[str, ...]
    dimension_ids: tuple[str, ...]
    concept_ids: tuple[str, ...]
    #: Interpretation decisions the analyst has to be told about, in their
    #: terms — a filter dropped as redundant, a period nobody asked for.
    #: Surfaced as turn warnings; never left in the debug payload alone.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InterpretationOutcome:
    investigation: InterpretedInvestigation | None
    clarification: ClarificationRequest | None
    definitional: DefinitionalAnswer | None
    usage: LlmUsage
    template_hash: str
    #: See :attr:`ClassificationOutcome.failure`.
    failure: LlmFailureKind | None = None


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


@dataclass(frozen=True, slots=True)
class PendingClarification:
    """A clarification this session asked and has not had answered yet.

    Classification without it is classification without the one fact that
    decides the answer: an utterance is only "an answer to a question I
    haven't asked" if no question is outstanding. Live, a session that
    replied to a clarification with a VERBATIM option string was read fresh
    each time and clarified four turns running — the model was never told
    it had asked anything.
    """

    #: The question the platform put to the analyst.
    question: str
    #: The options it offered, if any — a verbatim reply is the strongest
    #: possible signal that this turn is an answer.
    options: tuple[str, ...] = ()
    #: The analyst's own question that the clarification interrupted, so a
    #: resolved turn can resume it rather than dropping it.
    original_question: str | None = None
    #: How many clarifications this thread has issued back-to-back.
    streak: int = 0


def render_pending_clarification(pending: PendingClarification | None) -> str:
    """The pending-clarification block for the classification prompt."""
    if pending is None:
        return "No clarification is pending; this utterance stands on its own."
    lines = [
        "A clarification IS pending. The platform asked the analyst:",
        f"  {pending.question}",
    ]
    if pending.options:
        lines.append("and offered these options:")
        lines.extend(f"  - {option}" for option in pending.options)
    if pending.original_question:
        lines.append(f"The question it interrupted was: {pending.original_question}")
    return "\n".join(lines)


class ClassifyTurnService:
    """LLM turn classification against the closed §7.3 taxonomy."""

    def __init__(self, llm: LanguageModelPort) -> None:
        self._llm = llm
        self._template: LoadedTemplate = load_template("classify_turn", "v1")
        self._schema = sanitize_json_schema(TurnClassificationResponse.model_json_schema())

    async def classify(
        self,
        question: str,
        *,
        pending: PendingClarification | None = None,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> ClassificationOutcome:
        prompt = render_template(
            self._template.text,
            {"question": question, "pending": render_pending_clarification(pending)},
        )
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
                policy=policy,
            )
        )
        if result.output is None:
            return self._unusable(
                "turn classification returned no structured output", result.failure, result.usage
            )
        try:
            parsed = TurnClassificationResponse.model_validate(dict(result.output))
        except ValidationError:
            return self._unusable(
                "turn classification failed schema validation",
                LlmFailureKind.SCHEMA,
                result.usage,
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
                options=clarification_options(parsed.clarification_options),
                reason=f"turn classification confidence {classification.confidence:.2f}",
            )
        return ClassificationOutcome(
            classification=classification,
            clarification=clarification,
            usage=result.usage,
            template_hash=self._template.sha256,
        )

    def _unusable(
        self, reason: str, failure: LlmFailureKind | None, usage: LlmUsage
    ) -> ClassificationOutcome:
        """No classification came back — ask for the right thing.

        A schema failure is the platform's problem and the analyst's
        question may have been fine, so the ask is "again", not "differently".
        The failure kind rides into the trace either way. No options on this
        path by construction: there is no parsed response for the model to
        have proposed any on.
        """
        retry = retry_may_help(failure)
        return ClassificationOutcome(
            classification=None,
            clarification=ClarificationRequest(
                question=_CLASSIFY_RETRY if retry else _CLASSIFY_REPHRASE,
                reason=reason + failure_note(failure),
            ),
            usage=usage,
            template_hash=self._template.sha256,
            failure=failure,
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

    def definitional_match(self, question: str) -> bool:
        """Is this utterance a definitional question, decidably?

        Strictly: the question must *open* with one of the governed
        lead-ins and what remains must resolve in the pack **whole**. Both
        halves matter. Without the lead-in, "denial rate by payer" would
        qualify; with the last-word fallback :meth:`definitional_answer`
        uses for recovery, "what is our net collection rate over the last
        90 days" would resolve on ``days`` and be answered with a
        dictionary entry instead of a number.

        Used only where the alternative is a model call that cannot do
        better: deciding the first utterance of a session, where nothing
        else in the taxonomy is available (see
        ``SubmitTurnService._classification_by_construction``). A lookup
        against governed content is not a guess, so it does not need one.
        """
        stripped = strip_definitional_lead_in(question)
        if not stripped or stripped == question.strip().strip("?!.").strip().lower():
            return False  # no lead-in was present: not phrased as a definition
        return bool(self._pack.resolve_term(stripped))

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

    def from_typed_spec(
        self, typed: TypedInvestigationSpec, *, session: Session, turn_id: str
    ) -> InterpretedInvestigation:
        """The typed twin of :meth:`interpret`: same governance, no model.

        A caller that already knows what it wants (a portfolio card, a
        chart click in a fresh session, a saved view) states it in the
        typed vocabulary instead of a sentence. Everything the LLM would
        have *proposed* is supplied; everything deterministic code
        *disposes* is unchanged — every metric id is checked against the
        pinned pack, every dimension (breakdown and scope alike) against
        the semantic catalog, the date basis against the governing
        contract's ``allowed_date_bases``, and the window resolves exactly
        once into stored concrete dates (§6.1) just as it does here.

        Zero LLM calls by construction: nothing on this path touches
        :class:`LanguageModelPort`.
        """
        contracts: list[MetricContract] = []
        for metric_id in typed.metric_ids:
            contract = self._pack.metric(metric_id)
            if contract is None:
                raise UnsupportedConceptError(
                    f"typed metric {metric_id!r} is not in the pack",
                    details={"metric": metric_id},
                )
            contracts.append(contract)
        for dimension_id in typed.dimensions:
            if self._catalog.dimension(dimension_id) is None:
                raise UnsupportedConceptError(
                    f"typed dimension {dimension_id!r} is not in the catalog",
                    details={"dimension": dimension_id},
                )
        primary = contracts[0]
        basis = self._resolve_basis(typed.basis, primary)
        window = self._typed_window(typed.window, basis, session)
        context = InvestigationContext(
            window=window,
            comparison=None,
            scope=self._typed_scope(typed.filters, turn_id),
            cohort=None,
            grain=Grain(primary.entity_grain),
            watermark=session.watermark,
            pack_version=session.pack_version,
        )
        if typed.comparison is not None:
            context = replace(
                context, comparison=derive_comparison(window, ComparisonKind(typed.comparison))
            )
        return InterpretedInvestigation(
            spec=AnalysisSpec(
                context=context,
                measures=tuple(MetricRef(mid) for mid in typed.metric_ids),
                dimensions=tuple(DimensionRef(did) for did in typed.dimensions),
            ),
            playbook_id=None,
            window_explicit=True,
            intent_summary="typed investigation spec (no interpretation)",
            metric_ids=tuple(typed.metric_ids),
            dimension_ids=tuple(typed.dimensions),
            concept_ids=(),
        )

    async def interpret(
        self,
        question: str,
        *,
        session: Session,
        turn_id: str,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> InterpretationOutcome:
        prompt = render_template(self._template.text, {**self._vocabulary(), "question": question})
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
                policy=policy,
            )
        )
        template_hash = self._template.sha256
        if result.output is None:
            return self._unusable(
                "interpretation returned no structured output", result.failure, result.usage
            )
        try:
            parsed = InterpretationResponse.model_validate(dict(result.output))
        except ValidationError:
            return self._unusable(
                "interpretation failed schema validation", LlmFailureKind.SCHEMA, result.usage
            )
        options = self._grounded_options(parsed.clarification_options)
        if parsed.clarification:
            if parsed.clarification_options and not options:
                # Every way forward the model proposed named something this
                # pack cannot do. A clarification whose options are all
                # unanswerable is worse than a refusal: it costs the analyst
                # a turn to discover the same "no". Refuse honestly instead
                # — the API's capability copy is written for exactly this.
                raise UnsupportedConceptError(
                    "the question maps onto no governed content, and every alternative "
                    "proposed for it names content this pack does not define",
                    details={
                        "clarification": parsed.clarification,
                        "rejected_options": [o.label for o in parsed.clarification_options],
                    },
                )
            return self._clarify(parsed.clarification, "model requested clarification",
                                 result.usage, template_hash, options=options)

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
                options=options,
            )
        primary = governing[0]

        basis = self._resolve_basis(parsed.basis, primary)
        window_explicit = parsed.window is not None
        requested = self._relative_range(parsed) if parsed.window is not None else _DEFAULT_WINDOW
        # Anchored to the data, never to the load's clock or to wall-clock
        # now — see :mod:`revi_investigation.application.anchoring`.
        anchor = window_anchor(session.watermark, requested.mode)
        window = resolve_window(requested, anchor, basis=basis)

        notes: list[str] = []
        scope = self._resolve_scope(parsed, turn_id, governing, notes)
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
            # already validated against the pack above — closed set only
            concepts=tuple(parsed.concept_ids),
            # …and the movement the question asked about, if it asked about
            # one. Closed sets by schema; carried so selection can honor them.
            direction=AskedDirection(parsed.direction) if parsed.direction else None,
            magnitude=AskedMagnitude(parsed.magnitude) if parsed.magnitude else None,
        )
        if not window_explicit:
            # An assumed period is a decision the analyst did not make. It
            # used to live in the debug intent_summary; it belongs beside
            # the number it scoped.
            notes.append(
                f"window_assumed: the question named no period, so I used "
                f"{window.range.start.isoformat()}..{window.range.end.isoformat()} on the "
                f"{basis.id} basis — the last full month this load can see (newest data "
                f"date {session.watermark.newest_data_date.isoformat()})."
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
                notes=tuple(notes),
            ),
            clarification=None,
            definitional=None,
            usage=result.usage,
            template_hash=template_hash,
        )

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _clarify(
        question: str,
        reason: str,
        usage: LlmUsage,
        template_hash: str,
        *,
        options: tuple[str, ...] = (),
        failure: LlmFailureKind | None = None,
    ) -> InterpretationOutcome:
        return InterpretationOutcome(
            investigation=None,
            clarification=ClarificationRequest(question=question, options=options, reason=reason),
            definitional=None,
            usage=usage,
            template_hash=template_hash,
            failure=failure,
        )

    def _unusable(
        self, reason: str, failure: LlmFailureKind | None, usage: LlmUsage
    ) -> InterpretationOutcome:
        """Nothing interpretable came back — ask for the right thing.

        Same split as classification: a shape that never arrived is worth
        asking again for, a model that had no mapping is not. No options
        here either — there is no parsed response to have carried any.
        """
        retry = retry_may_help(failure)
        return self._clarify(
            _INTERPRET_RETRY if retry else _INTERPRET_REPHRASE,
            reason + failure_note(failure),
            usage,
            self._template.sha256,
            failure=failure,
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

    def _resolve_basis(self, raw: str | None, primary: MetricContract) -> DateBasisRef:
        """The basis this window will be read on (§5.3, §6.6 step 3).

        A basis the contract forbids is still ``DATE_BASIS_INVALID``. A
        basis the contract allows but this warehouse does not bind at the
        metric's grain falls back to an allowed basis it does bind — here
        rather than in the planner alone, because the window's basis is
        what the context header publishes, and a header naming a basis
        nothing read is a header that misstates the answer.
        """
        requested = DateBasisRef(raw.strip().lower()) if raw is not None else None
        return resolve_answerable_basis(primary, requested, self._catalog).basis

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

    def _typed_window(
        self,
        window: WindowSpecModel | AbsoluteWindowModel,
        basis: DateBasisRef,
        session: Session,
    ) -> TimeWindow:
        """Resolve a typed window once, into stored concrete dates (§6.1)."""
        if isinstance(window, AbsoluteWindowModel):
            if window.end < window.start:
                raise UnsupportedConceptError(
                    f"typed window {window.start.isoformat()}..{window.end.isoformat()} "
                    "ends before it starts",
                    details={"start": window.start.isoformat(), "end": window.end.isoformat()},
                )
            return TimeWindow(
                basis=basis,
                range=AbsoluteRange(start=window.start, end=window.end),
                requested=None,
            )
        try:
            quantity = Decimal(window.quantity)
        except InvalidOperation:
            raise UnsupportedConceptError(
                f"window quantity {window.quantity!r} is not a decimal",
                details={"quantity": window.quantity},
            ) from None
        requested = RelativeRange(
            quantity=quantity, unit=TimeUnit(window.unit), mode=RangeMode(window.mode)
        )
        # Same anchor rule as the interpreted path.
        return resolve_window(
            requested, window_anchor(session.watermark, requested.mode), basis=basis
        )

    def _typed_scope(self, filters: Sequence[AddFilterModel], turn_id: str) -> FilterExpr:
        """Typed filter clauses → kernel scope, catalog-validated like any
        interpreted one (an unknown dimension is UNSUPPORTED_CONCEPT)."""
        predicates: list[Predicate] = []
        for clause in filters:
            if self._catalog.dimension(clause.dimension) is None:
                raise UnsupportedConceptError(
                    f"typed filter dimension {clause.dimension!r} is not in the catalog",
                    details={"dimension": clause.dimension},
                )
            predicates.append(
                Predicate(
                    dimension=DimensionRef(clause.dimension),
                    op=PredicateOp(clause.predicate_op),
                    values=tuple(self._scalar(value) for value in clause.values),
                    origin_turn=turn_id,
                )
            )
        if not predicates:
            return EMPTY_SCOPE
        return and_merge(*predicates)

    def _resolve_scope(
        self,
        parsed: InterpretationResponse,
        turn_id: str,
        governing: tuple[MetricContract, ...] = (),
        notes: list[str] | None = None,
    ) -> FilterExpr:
        predicates: list[Predicate] = []
        pinned = self._pinned_by_contracts(governing)
        for entry in parsed.scope:
            if self._catalog.dimension(entry.dimension) is None:
                raise UnsupportedConceptError(
                    f"scope dimension {entry.dimension!r} is not in the catalog",
                    details={"dimension": entry.dimension},
                )
            values = tuple(self._scalar(value) for value in entry.values)
            redundant = self._redundant_note(entry.dimension, entry.op, values, pinned)
            if redundant is not None:
                if notes is not None:
                    notes.append(redundant)
                continue
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

    def _pinned_by_contracts(
        self, governing: tuple[MetricContract, ...]
    ) -> dict[str, tuple[frozenset[str], str]]:
        """Dimension values the governing contracts already pin, by dimension."""
        pinned: dict[str, tuple[frozenset[str], str]] = {}
        for contract in governing:
            for dimension_id, values in contract_pinned_values(contract).items():
                if values and dimension_id not in pinned:
                    pinned[dimension_id] = (values, contract.id)
        return pinned

    @staticmethod
    def _redundant_note(
        dimension_id: str,
        op: str,
        values: tuple[Scalar, ...],
        pinned: dict[str, tuple[frozenset[str], str]],
    ) -> str | None:
        """Is this filter a restatement of what the metric already is?

        ``ar_over_90_pct`` *is* the 91-120 and 120+ buckets: its numerator
        pins them. An analyst filter repeating that pin narrows nothing —
        and, because ``ar_age_bucket`` is not a declared scope dimension of
        the metric, it turns an answerable question into a
        ``GRAIN_INCOMPATIBLE`` refusal. Dropping the restatement (and
        saying so) answers the question that was asked.

        The rule is exactly "unless values differ": a filter naming a
        *subset* of the pinned values is dropped as redundant, a filter
        naming anything outside them is kept — it means something else, and
        the §6.6 exclusion-overlap warning is what explains the interaction.
        """
        entry = pinned.get(dimension_id)
        if entry is None or op not in (PredicateOp.EQ.value, PredicateOp.IN.value) or not values:
            return None
        pinned_values, metric_id = entry
        asked = {normalize_synonym(str(value)) for value in values}
        if not asked <= pinned_values:
            return None
        stated = ", ".join(repr(str(value)) for value in values)
        return (
            f"filter_redundant: dropped the {dimension_id} filter {stated} — metric "
            f"{metric_id!r} already pins that population in its own definition, so the "
            "filter narrowed nothing and is not a cut this metric supports."
        )

    def _grounded_options(
        self, options: Sequence[GroundedOptionModel]
    ) -> tuple[str, ...]:
        """Keep only the recovery options this pack and catalog can honor.

        A clarification option is a promise: tap it and you get an answer.
        The platform offered "Compare denial rates across all Medicare
        Advantage payers" and refused that request on the very next turn —
        the option was a sentence, and a sentence resolves against nothing.
        So every option now carries the ids it would use and they go
        through the same disposal an interpretation does: metrics and
        playbooks against the pinned pack, dimensions and scope dimensions
        against the catalog, scope values against a declared
        ``value_domain`` where the catalog states one, and a breakdown
        dimension against the governing contract's own
        ``scope_dimensions`` — the ratio-grain rule §6.6 would refuse it by
        one turn later.

        Failures are dropped silently *as options*; the caller decides what
        an empty survivor list means (see
        :meth:`InterpretQuestionService.interpret`).
        """
        return clarification_options(
            [option.label for option in options if self._option_resolves(option)]
        )

    def _option_resolves(self, option: GroundedOptionModel) -> bool:
        if not option.label.strip():
            return False
        contracts: list[MetricContract] = []
        for metric_id in option.metric_ids:
            contract = self._pack.metric(metric_id)
            if contract is None:
                return False
            contracts.append(contract)
        if option.playbook_id is not None and self._pack.playbook(option.playbook_id) is None:
            return False
        if not contracts and option.playbook_id is None:
            # An option naming no governed content is exactly the hollow
            # kind: a restatement whose answerability nobody can check.
            return False
        for dimension_id in option.dimension_ids:
            dim = self._catalog.dimension(dimension_id)
            if dim is None:
                return False
            ref = DimensionRef(dimension_id)
            if any(c.is_ratio and not c.allows_dimension(ref) for c in contracts):
                return False  # §6.6 step 2 would refuse this one turn later
        for entry in option.scope:
            dim = self._catalog.dimension(entry.dimension)
            if dim is None:
                return False
            if dim.value_domain is None:
                continue
            allowed = {normalize_synonym(value) for value in dim.value_domain}
            if any(normalize_synonym(str(value)) not in allowed for value in entry.values):
                return False
        return True

    @staticmethod
    def _scalar(value: str | int | float | bool | None) -> Scalar:
        if isinstance(value, float):
            return Decimal(str(value))
        return value
