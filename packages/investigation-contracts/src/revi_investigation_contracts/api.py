"""Public request/response DTOs and the ``InvestigationApi`` protocol.

These are the wire shapes served over HTTP and returned by the in-process
client — one contract, two transports (plan §7). ``TurnResponse`` is a
discriminated union on ``outcome``: a clarification is a *successful*
outcome (design §2.8, §12), and errors normalize to the stable
:class:`ErrorEnvelope` kernel codes on both transports.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Protocol, Union

from pydantic import Field, model_validator

from revi_investigation_contracts.debug import DebugTracePayload
from revi_investigation_contracts.evidence import EvidencePayload
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_investigation_contracts.provenance import MetricProvenancePayload
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    ClosedModel,
    ComparisonLiteral,
    RefinementOperatorModel,
    WindowSpecModel,
)
from revi_investigation_contracts.settings import (
    SessionSettingsModel,
    SettingsBoundsPayload,
)

# ---------------------------------------------------------------------------
# sessions


class OpenSessionRequest(ClosedModel):
    """Open (or re-join) a session.

    ``tenant`` is **advisory**. It used to be the only thing deciding which
    tenant a session belonged to, and nothing verified it; the tenant now
    comes from the caller's signed credential and this field is only
    cross-checked against it, so naming a different tenant is refused
    rather than honored. Leave it empty and the credential decides.
    """

    tenant: str = ""
    session_id: str | None = None
    #: Session-scoped settings (model tier, cost ceiling, depths, debug).
    #: Validated against the deployment's bounds and REFUSED when out of
    #: them — never clamped. Omitted leaves the session on the defaults;
    #: re-opening an existing session with settings re-applies them.
    settings: SessionSettingsModel | None = None


class SessionResponse(ClosedModel):
    session_id: str
    tenant: str
    pack_id: str
    pack_version: str
    watermark_id: str
    watermark_loaded_at: datetime
    newest_data_date: date
    epoch: int
    #: The settings actually in force — the resolved values, so a client
    #: can see what it got rather than what it asked for.
    settings: SessionSettingsModel = Field(default_factory=SessionSettingsModel)


class SessionSummary(ClosedModel):
    """One row of ``GET /v1/sessions`` — enough to pick a session, and
    nothing more.

    ``title`` is the session's FIRST question, verbatim, or ``"New
    session"`` when nothing has been asked yet: a session record carries no
    name, and a generated one would be a label the analyst never wrote.
    ``last_activity`` is derived from the session's newest investigation
    (its own ``created_at`` when it has none), so the ordering an analyst
    sees is when a session was last *worked*, not when it was opened.

    Deliberately not a :class:`SessionResponse`: a list row must not imply
    a pinned watermark or an epoch, both of which are facts about a session
    you have actually joined.
    """

    session_id: str
    title: str
    created_at: datetime
    last_activity: datetime
    turn_count: int


class SessionListResponse(ClosedModel):
    """The caller tenant's sessions, newest activity first.

    ``total`` counts every session the tenant owns, so a page truncated by
    ``limit`` cannot be mistaken for the whole history. ``tenant`` names
    whose list this is — the same reason :class:`PortfolioResponse` carries
    it: "whose worklist is this?" must be answerable from the payload.
    """

    tenant: str = ""
    sessions: list[SessionSummary] = Field(default_factory=list)
    total: int = 0
    #: The cap actually applied (the request's, bounded by the deployment).
    limit: int = 0


# ---------------------------------------------------------------------------
# turn requests


class TypedInvestigationSpec(ClosedModel):
    """An explicit, typed investigation — the typed twin of interpretation.

    A turn carrying one of these is a NEW_INVESTIGATION *by construction*:
    it names its own governed metrics, dimensions, scope and window, so
    there is nothing to infer and no parent investigation to refine. The
    engine builds the ``AnalysisSpec`` from it directly with **zero model
    calls**; the §6.6 validation pass then runs unchanged, so a typed spec
    earns exactly the same grades, warnings and refusals an interpreted
    one does. It skips the guessing, never the governance.

    This is the anchor a typed *refinement* has always needed and never
    had. A refinement edits a parent investigation; a portfolio card, a
    chart click in a fresh session, a saved view or a scheduled brief has
    no parent — it has an intent that is already typed. Expressing that as
    a first turn (rather than minting a hidden portfolio-anchored session
    per surface) keeps one investigation pipeline: whatever posts this
    gets the same planning, validation, grading, findings and trace as a
    sentence would.
    """

    #: At least one governed metric: without a model there is no playbook
    #: to infer, so "no governing metric" is impossible by construction
    #: rather than a runtime clarification.
    metric_ids: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    #: Scope clauses, spelled as the same ``add_filter`` operator a chart
    #: click emits — one closed shape for "a typed filter clause".
    filters: list[AddFilterModel] = Field(default_factory=list)
    window: WindowSpecModel | AbsoluteWindowModel
    basis: str | None = None
    comparison: ComparisonLiteral | None = None


class TurnRequest(ClosedModel):
    """One turn: an utterance, a typed investigation spec, OR typed
    refinement operators (§12)."""

    utterance: str | None = None
    #: A typed FIRST turn (new investigation, no parent) — see
    #: :class:`TypedInvestigationSpec`.
    spec: TypedInvestigationSpec | None = None
    #: The anomaly card this typed turn was launched from, when it was.
    #:
    #: Purely additive and purely optional: a ``spec`` posted without it
    #: behaves exactly as before. Supplied on a typed FIRST turn, the
    #: platform loads that card from the detection feed at the session's
    #: watermark and publishes a
    #: :class:`AnomalyReconciliationPayload` on the answer — because the
    #: card said ``$178,217`` and its own drill answered ``$195,873.92``
    #: and nothing on either screen said so. See that class for why the
    #: two figures legitimately differ and what the strip states.
    #:
    #: Ignored (with a warning, never an error) on a turn that carries no
    #: ``spec``: an anomaly reference on an utterance or a refinement
    #: would name a card whose drill this turn is not running.
    anomaly_ref: str | None = None
    #: A typed gesture (refines the session's latest investigation).
    refinements: list[RefinementOperatorModel] | None = None
    clarification_response: str | None = None
    re_anchor: bool = False
    idempotency_key: str | None = None
    correlation_id: str | None = None
    #: Settings for THIS turn only. Same bounds, same refusal; the session
    #: record is not rewritten, so a one-off debug turn or a one-off deeper
    #: sweep does not silently become the session's new normal.
    settings: SessionSettingsModel | None = None

    @model_validator(mode="after")
    def _one_typed_intent(self) -> TurnRequest:
        """A turn either starts an investigation or edits one, never both.

        Rejected as a malformed body (422) rather than silently resolved,
        because either resolution order would be a guess about intent.
        """
        if self.spec is not None and self.refinements is not None:
            raise ValueError(
                "a turn carries either `spec` (a typed new investigation) or "
                "`refinements` (a typed edit to the session's latest "
                "investigation) — never both"
            )
        return self


# ---------------------------------------------------------------------------
# answer components


class FindingValue(ClosedModel):
    name: str
    value: str | int | float | bool | None = None


class BenchmarkPayload(ClosedModel):
    """One governed external benchmark range for a metric.

    A range, never a point target, and never separable from its context:
    ``cohort_label``, ``period``, ``authority``, ``cautions`` and
    ``review_status`` are all required on the wire because a figure quoted
    without them is a different claim from the one its source made.
    ``review_status`` is ``machine_researched`` for every figure shipped so
    far — a consumer that renders these as certified truth is asserting
    more than the pack does."""

    id: str
    metric_id: str
    cohort_label: str
    value_low: str
    value_high: str
    unit: str
    period: str
    authority: str
    review_status: str
    cautions: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class FindingPayload(ClosedModel):
    referent: str
    title: str
    statement: str
    metric_ids: list[str] = Field(default_factory=list)
    values: list[FindingValue] = Field(default_factory=list)
    grade: str = "direct"
    impact_cents: int | None = None
    confidence: str = "high"
    suggested_refinements: list[str] = Field(default_factory=list)
    #: Governed external context for this finding's metrics. Ranges with
    #: their cohorts and cautions, or empty when the pack has none — the
    #: field is never omitted, so a client can tell "no benchmark exists"
    #: from "benchmarks were not plumbed".
    benchmarks: list[BenchmarkPayload] = Field(default_factory=list)
    #: The governed caveats attached to this finding's metric ids, in the
    #: pack's own words (round-2 FN-5). Published ON the finding so a card
    #: can render the correction as visible text under the title instead of
    #: behind a hover — a screenshotted card that ships the label and
    #: leaves the correction behind is the defect this closes. Empty when
    #: every metric on the finding says what it measures.
    metric_caveats: list[str] = Field(default_factory=list)


ChartType = Literal["bar", "grouped_bar", "stacked_bar", "line", "waterfall", "table", "range_band"]


class ChartRow(ClosedModel):
    x: str
    series: str | None = None
    value: str | int | float | None = None
    referent_id: str | None = None


class ChartSpec(ClosedModel):
    """A renderable chart; row referent ids make clicks compile to
    ``DrillInto`` — no natural language in the gesture loop."""

    id: str
    chart_type: ChartType
    title: str
    frame_id: str
    x: str
    series: str | None = None
    value: str
    unit: str | None = None
    grade: str = "direct"
    rows: list[ChartRow] = Field(default_factory=list)
    annotations: list[str] = Field(default_factory=list)
    recipe_id: str | None = None


class ReferentPayload(ClosedModel):
    id: str
    kind: str
    label: str


class TermPayload(ClosedModel):
    term: str
    kind: str
    title: str
    definition: str
    source: str | None = None


class DefinitionalPayload(ClosedModel):
    question: str
    terms: list[TermPayload] = Field(default_factory=list)
    pack_id: str
    pack_version: str
    pack_snapshot_id: str


class MetaAnswerPayload(ClosedModel):
    referent: str
    label: str
    investigation_id: str
    probes: list[dict[str, Any]] = Field(default_factory=list)
    operators: list[dict[str, Any]] = Field(default_factory=list)
    grades: dict[str, str] = Field(default_factory=dict)
    reconciliation: str | None = None
    finding_values: list[FindingValue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class UsageSummary(ClosedModel):
    """What this turn spent on the model.

    ``input_tokens`` is EVERY prompt token the turn read — the uncached
    remainder plus what was written to and served from the provider's
    prompt cache. The provider reports those three separately and names
    only the first ``input_tokens``; publishing that one field made turns
    read ``input_tokens: 4`` against ``output_tokens: 953``. The cached
    split rides on the two fields below rather than being lost, so a reader
    can still tell a cheap cached prompt from an expensive cold one.
    """

    llm_calls: int = 0
    cost_usd: str = "0"
    input_tokens: int = 0
    output_tokens: int = 0
    #: Prompt tokens served from the provider's cache. Part of `input_tokens`.
    cache_read_tokens: int = 0
    #: Prompt tokens written to the provider's cache. Part of `input_tokens`.
    cache_creation_tokens: int = 0
    schema_retries: int = 0


class WarningPayload(ClosedModel):
    """One warning, with a handle a client can branch on (review F14).

    Warnings used to travel as prose alone, so a client that wanted to
    group, count, filter or icon them had to match substrings — and a
    client matching substrings breaks the day the wording improves.

    ``message`` is the platform's own sentence VERBATIM: the code is a
    handle added beside the text, never a replacement for it. ``count``
    is how many identical warnings collapsed into this entry (a
    four-probe plan emitting one population caveat four times is one
    caveat seen four times). See :mod:`revi_api.warning_codes` for the
    code list; an unrecognized sentence is published as ``UNCLASSIFIED``
    rather than dropped.
    """

    #: Stable, branchable family (``SUPPRESSION_APPLIED``,
    #: ``POPULATION_CAVEAT``, ``ALTERNATE_BASIS_USED``, …).
    code: str
    #: ``caution`` — this changes how you should read the number;
    #: ``info`` — worth knowing, does not change the reading. There is no
    #: third level: anything more serious is a refusal, and refusals are
    #: errors with §12 codes.
    severity: Literal["caution", "info"]
    message: str
    count: int = 1


class MetricDisplayPayload(ClosedModel):
    """The honest display name for a metric id that cannot be renamed.

    ``timely_filing_at_risk_dollars`` measures unbilled open inventory. It
    applies no deadline predicate at all — ``filing_rules`` is governed
    content the contract does not yet join — so the id promises filing
    exposure and delivers an upper bound that is three orders of magnitude
    larger than the real filing cards ($22.4M against ~$62K). The id is a
    reference anchor across the pack, the answer key and the review
    record, so renaming it would break more than it fixed; instead the
    platform publishes what it actually measures, next to the id, on
    every surface that shows one.

    ``caveat`` is the same sentence the contract emits as a mandatory
    warning on every answer that reads the metric. It travels here too so
    a card or a chip can carry it without waiting for an answer.
    """

    metric_id: str
    #: What the number is, in the analyst's words.
    display_name: str
    #: The mandatory qualification, or ``None`` when the id needs no
    #: correction beyond a friendlier name.
    caveat: str | None = None
    #: Why this id needed a governed display name — recorded so the entry
    #: is auditable rather than somebody's preference.
    rationale: str | None = None


class ErrorEnvelope(ClosedModel):
    code: str
    message: str
    correlation_id: str
    #: A narrower family within ``code``, when the code alone conflates
    #: failures that want different responses from the reader.
    #:
    #: ``QUERY_BUDGET_EXCEEDED`` covered two unrelated events: a question
    #: that would read more of the WAREHOUSE than one turn may, and a turn
    #: that ran out of MODEL SPEND. One is fixed by narrowing the
    #: question; the other by raising a ceiling or waiting — and the
    #: analyst who reads "narrow your question" after a budget stop
    #: narrows a question that was never too wide. The §12 code is stable
    #: and unchanged (clients still branch on it); this says which of the
    #: two it was: ``WAREHOUSE_READ_BUDGET`` | ``MODEL_SPEND_BUDGET``.
    subcode: str | None = None


class CohortPayload(ClosedModel):
    """The pinned population behind an answer, said in words (review F15).

    The context header carried ``cohort: coh_9f2a11…`` and a size. A hash
    is a correct identifier and a useless label: the analyst who drilled
    "the top three payers" was shown a string that names their own
    selection back to them in a vocabulary nobody speaks, and a chip that
    cannot be read cannot be checked.

    So the same object the platform pinned is published as its parts: what
    the members ARE (``entity_grain``), which rule selected them
    (``definition``, the pinned predicate rendered as text), where the
    selection came from (``origin_referent`` and the turn that introduced
    it), and how many there are. ``id`` stays — it is the handle a later
    turn re-addresses the population by — it is simply no longer the only
    thing on the wire.
    """

    id: str
    #: What one member is: ``claim``, ``claim_line``, ``remit``, …
    entity_grain: str
    #: The selecting rule as text — ``payer in [A, B, C]``, conjoined as
    #: the definition conjoins it. This is the *intensional* definition
    #: (§7.5), the thing that would be re-evaluated against fresh data in
    #: another session, not a description of the pinned rows.
    definition: str
    size: int
    #: The referent the drill started from (``F2``), and the turn and
    #: investigation that introduced it — so "where did this population
    #: come from?" is answerable from the payload rather than by walking
    #: the lineage. ``None`` when the registry no longer holds the entry.
    origin_referent: str | None = None
    origin_turn_id: str | None = None
    origin_investigation_id: str | None = None
    #: The cohort's own window, when its definition kept one. ``None`` is
    #: meaningful: a cohort pinned without a window covers the scoped
    #: population across all time, and a warning said so when that
    #: happened (``COHORT_WINDOW_DROPPED``).
    window_start: date | None = None
    window_end: date | None = None
    #: Whether an extensional set was materialized, and at which watermark
    #: — an unpinned cohort is a definition that has not been evaluated.
    pinned: bool = False
    pinned_watermark_id: str | None = None


class AnomalyReconciliationPayload(ClosedModel):
    """Card figure vs re-derived figure, stated (review F1).

    An anomaly card published ``$178,217``; drilling it answered
    ``$195,873.92``; the turn's own reconciliation verdict said
    ``not_applicable — this is a first turn``, which is true about the
    investigation lineage and silent about the two numbers the reader had
    just compared. 9.9% of disagreement, on consecutive screens, with no
    reconciliation anywhere.

    The figures are two different claims and both are honest:

    * ``card_impact_cents`` is the EXTERNAL DETECTION SYSTEM's assertion —
      its window, its population, its basis, computed when it fired and
      read here as-of a watermark. The card carries
      ``provenance="external_detection"`` for exactly this reason.
    * ``answer_impact_cents`` is THIS PLATFORM's governed metric contract,
      re-derived at the pinned watermark over the population the card
      names, and carrying a real evidence grade.

    They diverge when the detector's window, valuation basis or population
    is not the contract's — which is normal, is not an error, and must be
    *stated* rather than left for a reader to notice. ``status`` says
    which of the three it is; ``detail`` says why in the platform's own
    words. This reuses the shape of the §7.8 refinement verdict
    (:class:`~revi_investigation_contracts.evidence.EvidenceReconciliation`)
    deliberately: an analyst who has learned to read one reconciliation
    strip should not have to learn a second.
    """

    anomaly_id: str
    #: ``agreed`` — the figures match within tolerance;
    #: ``diverged`` — they do not, and ``detail`` says what differs;
    #: ``unavailable`` — the platform could not re-derive its own figure,
    #: and ``detail`` says why. Never silence.
    #: ``not_comparable`` — both figures exist and are honest, and they
    #: measure different kinds of thing (an as-of snapshot balance against
    #: a windowed flow), so no percentage delta is published and the gap is
    #: not attributed to the detector.
    status: Literal["agreed", "diverged", "not_comparable", "unavailable"]
    card_impact_cents: int
    answer_impact_cents: int | None = None
    delta_cents: int | None = None
    #: Signed, as a fraction of the card figure (``0.099`` = the answer is
    #: 9.9% above the card). ``None`` when there is nothing to compare.
    delta_fraction: float | None = None
    #: The governed contract the answer's figure came from — not always
    #: the metric the detector named (see ``AnomalyCard.drill_repointed_from``).
    answer_metric_id: str | None = None
    #: The detector's own metric id, window and category, so the strip is
    #: readable without fetching the card again.
    card_metric_id: str = ""
    card_window_start: date | None = None
    card_window_end: date | None = None
    #: The recorded reason the two may differ, in full sentences.
    detail: str = ""
    #: The one-line rendering, in the grammar the §7.8 verdict uses.
    summary: str = ""


# ---------------------------------------------------------------------------
# turn outcomes (discriminated on `outcome`)


class TurnAnswer(ClosedModel):
    outcome: Literal["answer"]
    session_id: str
    investigation_id: str
    turn_class: str
    context_header: ContextHeaderPayload | None = None
    findings: list[FindingPayload] = Field(default_factory=list)
    chart_specs: list[ChartSpec] = Field(default_factory=list)
    narrative: str | None = None
    warnings: list[str] = Field(default_factory=list)
    #: The same warnings, classified and deduplicated — ``{code, severity,
    #: message, count}``. Additive: :attr:`warnings` is unchanged and
    #: still authoritative prose; this is the handle a client branches on
    #: instead of matching substrings. See :class:`WarningPayload`.
    warnings_v2: list[WarningPayload] = Field(default_factory=list)
    meta_answer: MetaAnswerPayload | None = None
    definitional: DefinitionalPayload | None = None
    referents: list[ReferentPayload] = Field(default_factory=list)
    #: The pinned population this answer was computed over, said in words
    #: rather than as a hash (review F15). ``None`` when the turn pinned
    #: none — the ordinary case for a first turn.
    cohort: CohortPayload | None = None
    #: Governed display names for the metric ids this answer cites, for
    #: the ids whose name overclaims what they measure (review F9).
    #: Empty when every metric on this answer says what it is.
    metric_display: list[MetricDisplayPayload] = Field(default_factory=list)
    #: Card-vs-answer reconciliation, present only when the turn was
    #: launched from an anomaly card (``TurnRequest.anomaly_ref``). See
    #: :class:`AnomalyReconciliationPayload`.
    anomaly_reconciliation: AnomalyReconciliationPayload | None = None
    #: Every benchmark cited by any finding on this turn, deduplicated —
    #: the turn-level view of the same governed content the findings carry.
    benchmarks: list[BenchmarkPayload] = Field(default_factory=list)
    reconciliation: str | None = None
    plan_hash: str | None = None
    watermark_stale: bool = False
    usage: UsageSummary = Field(default_factory=UsageSummary)
    #: The working behind the answer: probes executed, what each returned,
    #: the reconciliation verdict, and whether the warehouse was touched at
    #: all. Projected from the same recorded trace as :attr:`debug` and
    #: published on **every** answer — an analyst does not need debug mode
    #: to ask what was read. See :mod:`revi_investigation_contracts.evidence`.
    evidence: EvidencePayload = Field(default_factory=EvidencePayload)
    #: Whose definition produced these numbers: the governed metric
    #: contract(s) at the versions they were read at, the playbook that
    #: chose them, and the pack version and snapshot they came from.
    #: Projected from the same recorded trace as :attr:`evidence` and
    #: :attr:`debug` — see :mod:`revi_investigation_contracts.provenance`.
    #: ``None`` only when no trace was recorded for the turn, never as a
    #: quiet stand-in for "nothing governed ran" (that is an empty
    #: ``metrics`` list, which is a different and stated fact).
    metric: MetricProvenancePayload | None = None
    #: The turn's decision trace, present only when the settings in force
    #: asked for it (``debug=true``). Always ``None`` otherwise — the
    #: trace is still recorded; it is simply not published.
    debug: DebugTracePayload | None = None


class TurnClarification(ClosedModel):
    outcome: Literal["clarification_required"]
    session_id: str
    investigation_id: str
    question: str
    options: list[str] = Field(default_factory=list)
    reason: str | None = None
    watermark_stale: bool = False
    usage: UsageSummary = Field(default_factory=UsageSummary)
    #: See :attr:`TurnAnswer.debug`. A clarification is the outcome whose
    #: trace matters most: it names which stage stopped and why.
    debug: DebugTracePayload | None = None


class TurnError(ClosedModel):
    outcome: Literal["error"]
    session_id: str | None = None
    error: ErrorEnvelope
    #: What the failed turn spent before it failed (review F19).
    #:
    #: A failure used to carry no usage at all, which made the cost ledger
    #: quietly wrong in the one direction that matters: a turn that
    #: classified, interpreted, planned and *then* refused had spent real
    #: model tokens, and the envelope reported nothing. Zeroes here are a
    #: measured zero — a turn that failed before any model call — not a
    #: missing field.
    usage: UsageSummary = Field(default_factory=UsageSummary)


TurnResponse = Annotated[
    Union[TurnAnswer, TurnClarification, TurnError],  # noqa: UP007 - discriminated union
    Field(discriminator="outcome"),
]


# ---------------------------------------------------------------------------
# reads


class InvestigationResponse(ClosedModel):
    investigation_id: str
    session_id: str
    parent_id: str | None = None
    turn_id: str
    turn_class: str
    status: str
    question: str | None = None
    plan_hash: str | None = None
    findings: list[FindingPayload] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: The classified twin of :attr:`warnings`, exactly as
    #: :attr:`TurnAnswer.warnings_v2` — a restored turn must not lose the
    #: handles the live answer had.
    warnings_v2: list[WarningPayload] = Field(default_factory=list)
    #: The pinned population this turn was computed over, said in words
    #: (review F15) — the same payload the live answer carried, rebuilt
    #: from the cohort the turn recorded. ``None`` when it pinned none.
    cohort: CohortPayload | None = None
    #: The same bundle :attr:`TurnAnswer.evidence` carries, from the same
    #: recorded trace, so a turn restored when a session is re-opened can
    #: show its working instead of an empty drawer. ``None`` only when no
    #: decision trace was recorded for this investigation (nothing to
    #: project) — never an empty bundle standing in for one.
    evidence: EvidencePayload | None = None
    #: The same governed-provenance block :attr:`TurnAnswer.metric`
    #: carries, from the same recorded trace, so a turn restored when a
    #: session is re-opened keeps its "Governed" badge instead of losing
    #: the one field that says whose definition its numbers are.
    metric: MetricProvenancePayload | None = None
    #: Rebuilt from the frames this turn persisted, so a restored turn
    #: renders its charts rather than findings alone. Empty when the
    #: frames are gone or the turn charted nothing. There is deliberately
    #: no ``narrative`` here: the composed prose is not stored anywhere,
    #: and a restored turn says what it kept instead of inventing it.
    chart_specs: list[ChartSpec] = Field(default_factory=list)
    #: Governed display names for the metric ids this turn's findings cite
    #: (round-2 FN-5). Published here as well as on :class:`TurnAnswer`:
    #: without it, replay and export carried a title the reader had no way
    #: to correct, and the correction lived only in one client.
    metric_display: list[MetricDisplayPayload] = Field(default_factory=list)
    created_at: datetime


class LineageEdgePayload(ClosedModel):
    parent_id: str
    child_id: str
    turn_id: str
    operators: list[dict[str, Any]] = Field(default_factory=list)


class SessionLineageResponse(ClosedModel):
    session: SessionResponse
    investigations: list[InvestigationResponse] = Field(default_factory=list)
    edges: list[LineageEdgePayload] = Field(default_factory=list)


class AnomalyDimension(ClosedModel):
    dimension: str
    value: str


class PriorityDecompositionPayload(ClosedModel):
    """Every term of ``anomaly_priority``, published (review F17).

    The formula was documented and its inputs were on the card, but the
    arithmetic was not: a reader could see ``priority_score: 0.6`` beside
    ``impact_cents: 82437`` and had no way to tell that the score was a
    floor rather than a computation. Publishing the three normalized
    components, the three weighted terms and the normalizer costs nothing
    at build time and makes the ranking checkable with a calculator.

    ``score = (impact_term + recency_term + actionability_term) /
    weight_sum``, then raised to ``floor_value`` when
    ``floor_applied``. ``score_before_floor`` is kept so the floor is
    visible as an intervention rather than hidden inside the result.
    """

    #: |impact| / max|impact| over the ranked population.
    impact_norm: float = 0.0
    #: ``0.5 ** (age_days / half_life_days)``.
    recency: float = 0.0
    #: governed recoverable estimate / max|impact| — the same denominator
    #: as ``impact_norm``, which is why an unrecoverable pile of dollars
    #: cannot outrank a fixable one.
    recoverable_norm: float = 0.0
    impact_term: float = 0.0
    recency_term: float = 0.0
    actionability_term: float = 0.0
    #: ``w_impact + w_recency + w_actionability`` — the denominator that
    #: makes the score a 0..1 quantity rather than a weight sum.
    weight_sum: float = 1.0
    score_before_floor: float = 0.0
    floor_applied: bool = False
    #: The floor in force for this build. Under ``anomaly_priority@2`` it
    #: is RELATIVE — the median score of the non-floored population — so a
    #: compliance item lands among ordinary work instead of above the
    #: largest critical finding on the list.
    floor_value: float = 0.0
    #: ``relative_median`` | ``governed_absolute``. The second is the
    #: fallback when nothing was left un-floored to take a median of.
    floor_basis: str = "relative_median"


class PortfolioLanePayload(ClosedModel):
    """One section of the worklist, with what belongs in it and why.

    ``items`` stays one ranked array — clients that already read it are
    untouched — and each card names its lane, so a UI can render
    "must-do regardless of size" as its own section instead of letting a
    $824 compliance item sit at rank 1 above a $178K critical finding
    and look like the most important thing in the building.
    """

    id: str
    label: str
    #: What the lane means, in the words a section header should use.
    description: str
    anomaly_ids: list[str] = Field(default_factory=list)
    item_count: int = 0
    impact_cents: int = 0


class AnomalyCard(ClosedModel):
    """One detected anomaly, ranked by the governed priority formula.

    The decomposed components (impact, age, recoverable estimate,
    actionability rationale) travel with the score — no black-box
    ordering — and ``drill_spec`` is the typed handle the UI posts to
    start an ordinary investigation turn.

    ``drill_spec`` is a complete :class:`TypedInvestigationSpec`, not a
    bag of operators: the card's own metric, its dimensions (both as the
    breakdown and, at their detected values, as the scope), and its
    observation window. Posting it re-derives the detector's assertion
    from the platform's certified semantics and versioned contracts — so
    the answer carries a real evidence grade where the card itself only
    carries provenance. Earlier milestones shipped ``drill_filters`` +
    ``drill_window``, which were sound operators with nowhere to land: a
    refinement refines a parent investigation and a card is not one.

    **No evidence grade, by construction.** A grade certifies how a number
    was *computed by this platform* from certified semantics (design §5.3);
    an anomaly card is not that. It is a record read from an external
    detection system as-of a watermark, so the platform cannot honestly
    stamp DIRECT/DERIVED/PROXY on it. Instead every card declares its
    ``provenance`` (``external_detection``), the ``priority_formula_version``
    that ordered it, and the ``source_watermark_id`` it was read at — the
    three facts a grade would otherwise have implied. Drilling a card starts
    an ordinary investigation turn, and *that* answer carries a real grade."""

    anomaly_id: str
    # Required, never defaulted: a card that could omit its provenance is a
    # card a client could mistake for platform-computed evidence.
    provenance: Literal["external_detection"]
    priority_formula_version: str
    source_watermark_id: str
    title: str
    description: str
    category: str
    metric_id: str
    severity: str
    confidence: str
    status: str
    detected_at: datetime
    window_start: date
    window_end: date
    dimensions: list[AnomalyDimension] = Field(default_factory=list)
    #: The DETECTOR's figure, as it fired. Not this platform's number —
    #: see :attr:`reconciled_impact_cents`, which is, and which is
    #: published beside it precisely so the two can never silently differ.
    impact_cents: int = 0
    #: The SAME cell, re-derived by this platform's governed metric
    #: contract at the source watermark, at portfolio build time (review
    #: F1). ``None`` when the drill does not plan at this catalog version
    #: or produces no money column — with the reason stated below rather
    #: than a zero standing in for "unknown".
    #:
    #: These two figures legitimately differ: the detector used its own
    #: window, population and valuation basis, and the contract uses the
    #: pack's. A 9.9% gap on the largest card is the kind of thing a
    #: reader must be able to see without opening the drill, so it is
    #: computed once at build time and published on the card.
    reconciled_impact_cents: int | None = None
    #: Which governed contract produced it (not always ``metric_id`` —
    #: see ``drill_repointed_from``).
    reconciled_impact_metric_id: str | None = None
    #: ``agreed`` (within tolerance) | ``diverged`` | ``not_comparable``
    #: (the contract is an as-of snapshot and the card a windowed flow, so
    #: the two do not measure the same kind of quantity) | ``unavailable``.
    impact_agreement: Literal["agreed", "diverged", "not_comparable", "unavailable"] = (
        "unavailable"
    )
    #: ``reconciled - detector``, and that difference as a signed fraction
    #: of the detector's figure. ``None`` when nothing was re-derived.
    impact_delta_cents: int | None = None
    impact_delta_fraction: float | None = None
    #: Why the two figures may differ, or why one is missing — always
    #: populated, because "the card and the drill disagree" with no
    #: explanation is the defect this field exists to close.
    impact_reconciliation_note: str = ""
    age_days: int = 0
    recoverable_cents_estimate: int = 0
    actionability_label: str = ""
    actionability_rationale: str = ""
    priority_score: float = 0.0
    compliance_floor_applied: bool = False
    #: The full arithmetic behind :attr:`priority_score` (review F17).
    priority: PriorityDecompositionPayload = Field(
        default_factory=PriorityDecompositionPayload
    )
    #: Which lane this card belongs to: ``compliance`` for a floored
    #: compliance-mandatory item ("must do regardless of size"),
    #: ``value`` for everything ranked on the money. See
    #: :class:`PortfolioLanePayload`.
    lane: Literal["compliance", "value"] = "value"
    #: The honest display name for :attr:`metric_id` when the id
    #: overclaims what it measures (review F9); ``None`` when it does not.
    metric_display_name: str | None = None
    #: Required, never defaulted: a card whose drill handle could be
    #: absent is a card the UI would have to invent a question for.
    drill_spec: TypedInvestigationSpec
    #: Whether ``drill_spec`` can actually be answered at this catalog and
    #: pack version — decided by running the real planning + §6.6
    #: validation pass over it, without touching the warehouse.
    #:
    #: The worklist used to rank 33 cards of which 6 could be opened, and
    #: the first one that opened was rank 17: ranks 1-16 all returned an
    #: error dialog, and ~90% of the ranked dollars were un-investigable.
    #: A worklist that leads with work nobody can start is worse than a
    #: shorter one, so an undrillable card now says so on the wire and
    #: sorts below every card that can be opened. Its detected evidence
    #: still shows — the detection is real; only the investigation is
    #: unavailable.
    drillable: bool = True
    #: Why not, in the platform's own error vocabulary
    #: (``UNSUPPORTED_CONCEPT: ...``), or ``None`` when drillable.
    drill_unavailable_reason: str | None = None
    #: Set when the drill investigates a different metric than the detector
    #: named, with the governed rationale for the substitution. Never
    #: silent: a repointed drill does not confirm the card's own figure, it
    #: measures the same cell with a contract that can express it.
    drill_repointed_from: str | None = None
    drill_repoint_rationale: str | None = None


class PortfolioResponse(ClosedModel):
    """Detected anomalies at the pinned watermark, governed-priority ranked."""

    status: Literal["ok", "empty"] = "empty"
    #: The tenant this worklist was built for. The route used to take no
    #: tenant at all, which made "whose worklist is this?" unanswerable from
    #: the payload.
    tenant: str = ""
    watermark_id: str = ""
    formula_version: str = ""
    weights: dict[str, float] = Field(default_factory=dict)
    items: list[AnomalyCard] = Field(default_factory=list)
    #: The sections the ranked ``items`` fall into, in the order a client
    #: should render them. Never a second ordering of the same cards: the
    #: array stays authoritative and each lane names its members by id.
    lanes: list[PortfolioLanePayload] = Field(default_factory=list)
    #: The compliance floor actually applied in this build, and where it
    #: came from (``relative_median`` | ``governed_absolute``). Under
    #: ``anomaly_priority@2`` the floor is the median score of the
    #: non-floored population, so a compliance-mandatory item is lifted to
    #: "as important as the middle of the worklist" rather than to a
    #: constant that outranked every real finding on it.
    compliance_floor_value: float = 0.0
    compliance_floor_basis: str = ""
    warnings: list[str] = Field(default_factory=list)
    #: The classified twin of :attr:`warnings` — see :class:`WarningPayload`.
    warnings_v2: list[WarningPayload] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SSE frames (POST /v1/sessions/{sid}/turns with Accept: text/event-stream)


TurnEventKind = Literal[
    "stage",
    "warning",
    "clarification",
    "context_header",
    "finding",
    "chart_spec",
    "narrative_delta",
    "error",
    "turn_complete",
]

#: Wire payload per frame kind — the contract the stream parser codes to.
TURN_EVENT_PAYLOADS: dict[str, str] = {
    "stage": "{stage: str} — pipeline progress (classify, plan, validate, "
    "execute, calculate, findings, present, narrate).",
    "warning": "{code: str, ...} — a stable §12 code plus code-specific "
    "detail (e.g. WATERMARK_STALE carries pinned/newest, "
    "RECONCILIATION_FAILED carries detail).",
    "clarification": "{question: str, reason: str|null} — a successful "
    "outcome, never an error.",
    "context_header": "ContextHeaderPayload — the effective context of the "
    "answer (§7.2); emitted before any finding.",
    "finding": "FindingPayload — one certified, referent-addressable result.",
    "chart_spec": "ChartSpec — a renderable chart whose row referent ids "
    "compile clicks into typed DrillInto refinements.",
    "narrative_delta": "{delta: str} — one streamed narrative chunk; "
    "provisional until turn_complete.",
    "error": "ErrorEnvelope — a failed turn; the stream then ends.",
    "turn_complete": "TurnResponse — the FULL authoritative payload. The "
    "stream is progress; this last frame is the answer.",
}


class TurnStreamEvent(ClosedModel):
    """One Server-Sent Event frame on the turn route.

    On the wire each frame is ``event: <kind>\\ndata: <json>\\n\\n``; this
    model documents that pairing (it is never serialized as a JSON body).
    Ordering: ``stage*`` interleaved with ``warning*``, then either
    ``clarification`` or (``context_header``, ``finding*``, ``chart_spec*``,
    ``narrative_delta*``), always terminated by exactly one
    ``turn_complete`` — or by ``error`` if the turn failed.
    """

    event: TurnEventKind
    data: dict[str, Any]


class CapabilitiesResponse(ClosedModel):
    repository: dict[str, Any] = Field(default_factory=dict)
    pack_id: str = ""
    pack_version: str = ""
    pack_snapshot_id: str = ""
    newest_watermark_id: str = ""
    llm: str = "mock"
    #: The deployment's governed display names for metric ids whose name
    #: overclaims what they measure (review F9). Fetched once, so any
    #: surface that shows a metric id can show what it actually is.
    metric_display: list[MetricDisplayPayload] = Field(default_factory=list)
    #: What this deployment will accept in ``SessionSettingsModel``, so a
    #: client renders the controls it actually has rather than offering
    #: one that will be refused (or, worse, one that changes nothing).
    settings: SettingsBoundsPayload = Field(default_factory=SettingsBoundsPayload)


# ---------------------------------------------------------------------------
# the API protocol (one contract, two transports)


class InvestigationApi(Protocol):
    async def open_session(self, request: OpenSessionRequest) -> SessionResponse: ...

    async def list_sessions(self, limit: int = 50) -> SessionListResponse: ...

    async def submit_turn(self, session_id: str, request: TurnRequest) -> TurnResponse: ...

    async def get_investigation(self, investigation_id: str) -> InvestigationResponse: ...

    async def get_trace(self, investigation_id: str) -> DebugTracePayload: ...

    async def get_session_lineage(self, session_id: str) -> SessionLineageResponse: ...

    async def get_capabilities(self) -> CapabilitiesResponse: ...

    async def get_portfolio(self) -> PortfolioResponse: ...
