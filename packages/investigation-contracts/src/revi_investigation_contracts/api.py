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

from pydantic import Field

from revi_investigation_contracts.header import ContextHeaderPayload
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    ClosedModel,
    RefinementOperatorModel,
)

# ---------------------------------------------------------------------------
# sessions


class OpenSessionRequest(ClosedModel):
    tenant: str
    session_id: str | None = None


class SessionResponse(ClosedModel):
    session_id: str
    tenant: str
    pack_id: str
    pack_version: str
    watermark_id: str
    watermark_loaded_at: datetime
    newest_data_date: date
    epoch: int


# ---------------------------------------------------------------------------
# turn requests


class TurnRequest(ClosedModel):
    """One turn: an utterance OR typed refinement operators (§12)."""

    utterance: str | None = None
    refinements: list[RefinementOperatorModel] | None = None
    clarification_response: str | None = None
    re_anchor: bool = False
    idempotency_key: str | None = None
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# answer components


class FindingValue(ClosedModel):
    name: str
    value: str | int | float | bool | None = None


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
    llm_calls: int = 0
    cost_usd: str = "0"
    input_tokens: int = 0
    output_tokens: int = 0
    schema_retries: int = 0


class ErrorEnvelope(ClosedModel):
    code: str
    message: str
    correlation_id: str


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
    meta_answer: MetaAnswerPayload | None = None
    definitional: DefinitionalPayload | None = None
    referents: list[ReferentPayload] = Field(default_factory=list)
    reconciliation: str | None = None
    plan_hash: str | None = None
    watermark_stale: bool = False
    usage: UsageSummary = Field(default_factory=UsageSummary)


class TurnClarification(ClosedModel):
    outcome: Literal["clarification_required"]
    session_id: str
    investigation_id: str
    question: str
    options: list[str] = Field(default_factory=list)
    reason: str | None = None
    watermark_stale: bool = False
    usage: UsageSummary = Field(default_factory=UsageSummary)


class TurnError(ClosedModel):
    outcome: Literal["error"]
    session_id: str | None = None
    error: ErrorEnvelope


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


class AnomalyCard(ClosedModel):
    """One detected anomaly, ranked by the governed priority formula.

    The decomposed components (impact, age, recoverable estimate,
    actionability rationale) travel with the score — no black-box
    ordering — and ``drill_filters`` + ``drill_window`` are the typed
    handle the UI uses to start an ordinary investigation turn."""

    anomaly_id: str
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
    impact_cents: int = 0
    age_days: int = 0
    recoverable_cents_estimate: int = 0
    actionability_label: str = ""
    actionability_rationale: str = ""
    priority_score: float = 0.0
    compliance_floor_applied: bool = False
    drill_filters: list[AddFilterModel] = Field(default_factory=list)
    drill_window: AbsoluteWindowModel | None = None


class PortfolioResponse(ClosedModel):
    """Detected anomalies at the pinned watermark, governed-priority ranked."""

    status: Literal["ok", "empty"] = "empty"
    watermark_id: str = ""
    formula_version: str = ""
    weights: dict[str, float] = Field(default_factory=dict)
    items: list[AnomalyCard] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CapabilitiesResponse(ClosedModel):
    repository: dict[str, Any] = Field(default_factory=dict)
    pack_id: str = ""
    pack_version: str = ""
    pack_snapshot_id: str = ""
    newest_watermark_id: str = ""
    llm: str = "mock"


# ---------------------------------------------------------------------------
# the API protocol (one contract, two transports)


class InvestigationApi(Protocol):
    async def open_session(self, request: OpenSessionRequest) -> SessionResponse: ...

    async def submit_turn(self, session_id: str, request: TurnRequest) -> TurnResponse: ...

    async def get_investigation(self, investigation_id: str) -> InvestigationResponse: ...

    async def get_session_lineage(self, session_id: str) -> SessionLineageResponse: ...

    async def get_capabilities(self) -> CapabilitiesResponse: ...

    async def get_portfolio(self) -> PortfolioResponse: ...
