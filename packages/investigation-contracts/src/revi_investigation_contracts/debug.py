"""The per-turn decision trace, as a wire shape (design §14).

The engine has always recorded this material — plan hash, §6.6 validation
outcomes, probe cache hits, per-call LLM usage, template hashes, stage
timings, watermark epoch. It lived in the trace store and nothing could
read it back out. These models are that record, typed, so an internal
operator can answer "why did it say that?" from the same bytes the engine
wrote rather than from a log grep.

Two rules bound what appears here:

- **It is a read of the recorded trace, never a second computation.**
  Every field is projected from one :class:`TraceRecord` payload. A debug
  view that recomputed anything could disagree with the answer it claims
  to explain.
- **The outbound-payload guard decides what free text may travel.** Any
  string the guard (``assert_safe_payload``) considers sensitive is
  replaced by a marker and named in :attr:`DebugTracePayload.redactions`;
  it is never emitted and never silently dropped.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from revi_investigation_contracts.refinements import ClosedModel
from revi_investigation_contracts.settings import SessionSettingsModel


class DebugLlmCall(ClosedModel):
    """One model call: what was asked, on what, at what price.

    ``failure`` is the :class:`LlmFailureKind` value when the call came
    back with nothing usable — ``declined`` / ``schema`` / ``off_script``
    — because "the model had no mapping for this" and "the answer never
    arrived in a readable shape" want opposite recoveries.
    """

    template: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: str = "0"
    #: Turns the model burned failing its own output schema.
    schema_retries: int = 0
    #: Transport attempts the adapter made, including the first.
    attempts: int = 1
    duration_ms: int = 0
    failure: str | None = None


class DebugProbe(ClosedModel):
    """One planned probe and what executing it actually produced."""

    id: str
    hash: str
    purpose: str = ""
    #: Which member of the §6.2 probe union — ``aggregation`` |
    #: ``snapshot`` | ``row_evidence``. Empty for traces recorded before
    #: the kind was written down.
    kind: str = ""
    #: Metric ids this probe read, each with the contract version the
    #: executed frame was stamped with (``{"id", "contract_version"}``).
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    cache_hit: bool = False
    #: Rows the frame carried after suppression; ``None`` for a probe that
    #: was planned but never executed (a clarification turn, a pruned node).
    rows: int | None = None
    #: The top-N cutoff the plan gave this probe, if any.
    limit: int | None = None
    truncated: bool = False
    suppressed_cells: int = 0
    #: The §6.6 evidence grade recorded for this node.
    grade: str | None = None
    duration_ms: int = 0


class DebugInterpretation(ClosedModel):
    """What the interpretation stage chose, by id."""

    intent_summary: str = ""
    metric_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    playbook_id: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    basis: str | None = None


class DebugTracePayload(ClosedModel):
    """One turn's decision breakdown, projected from its trace record."""

    trace_id: str
    session_id: str
    investigation_id: str
    turn_id: str
    #: The settings actually in force for this turn (session settings plus
    #: any per-turn override), not the ones the client asked for.
    settings: SessionSettingsModel = Field(default_factory=SessionSettingsModel)
    question: str | None = None

    # --- what the model decided -------------------------------------
    turn_class: str | None = None
    classification_confidence: float | None = None
    interpretation: DebugInterpretation | None = None
    #: Typed refinement operators emitted on a follow-up turn.
    refinement_operators: list[dict[str, Any]] = Field(default_factory=list)
    refinement_rationale: str | None = None
    #: Referent resolutions: mention → referent id with confidence.
    referent_resolutions: list[dict[str, Any]] = Field(default_factory=list)
    clarification_reason: str | None = None

    # --- what the platform computed ---------------------------------
    plan_hash: str | None = None
    playbook_id: str | None = None
    probes: list[DebugProbe] = Field(default_factory=list)
    #: Node id → §6.6 evidence grade.
    grades: dict[str, str] = Field(default_factory=dict)
    #: The weakest node grade, which is the ceiling on what the answer may
    #: claim — the derivation, not a restatement.
    weakest_grade: str | None = None
    #: Referent → grade for every certified finding of this turn.
    finding_grades: dict[str, str] = Field(default_factory=dict)
    calculation_operators: list[dict[str, Any]] = Field(default_factory=list)
    reconciliation: str | None = None
    warnings: list[str] = Field(default_factory=list)

    # --- what it cost and what it ran against -----------------------
    llm_calls: list[DebugLlmCall] = Field(default_factory=list)
    template_hashes: dict[str, str] = Field(default_factory=dict)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    watermark_id: str = ""
    watermark_stale: bool = False
    epoch: int = 0
    re_anchored: bool = False
    pack_id: str = ""
    pack_version: str = ""
    pack_snapshot_id: str = ""

    #: Fields whose text the outbound-payload guard refused to release,
    #: named so the omission is visible rather than mistaken for absence.
    redactions: list[str] = Field(default_factory=list)
