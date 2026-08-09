"""The analyst-facing evidence bundle (design §6.4, §14).

This is the *second door* onto the material :mod:`revi_investigation_contracts.debug`
publishes: one recorded :class:`TraceRecord` per turn, two projections. The
debug payload speaks the engine's vocabulary to an operator who asked "why
did it say that?"; this one answers the analyst's "what did you actually
read, and does it add up?" — and it travels on every answer rather than
only when ``debug`` is on.

Both projections read the same stored bytes, so their numbers agree by
construction. Nothing here is recomputed from the frames.

**What is deliberately absent.** There is no masked row sample. The
planner emits only :class:`AggregationProbe` and :class:`SnapshotProbe`
nodes, so no frame this platform stores holds row-level content, and a
"sample rows" section would have to invent the rows it displayed. When
:class:`RowEvidenceProbe` planning lands (authorization-gated, purpose
recorded, PHI masked at the connector — design §15), the probe entries
below already carry its ``kind`` and its ``purpose``, and the sample
itself becomes an additive field.
"""

from __future__ import annotations

from pydantic import Field

from revi_investigation_contracts.refinements import ClosedModel


class EvidenceMetricRef(ClosedModel):
    """A metric a probe read, with the contract version it was read under.

    Both halves come off the executed frame's schema — the version the
    connector stamped on the column, not the version the plan asked for,
    because those differ exactly when a pack promotion lands mid-session
    and that is the case worth seeing.
    """

    id: str
    contract_version: int | None = None


class EvidenceProbePayload(ClosedModel):
    """One data check: what it was for, and what came back.

    ``rows`` is ``None`` for a probe that was planned but never executed
    (a turn that stopped at clarification, a pruned node) — distinct from
    ``0``, which means the warehouse was asked and answered "nothing".
    """

    id: str
    hash: str
    #: The plan's own statement of what this probe is for. Platform- and
    #: pack-authored (never the analyst's words), which is why it travels
    #: unguarded, exactly as it does in the debug projection.
    purpose: str = ""
    #: ``aggregation`` | ``snapshot`` | ``row_evidence`` — the probe union
    #: member (design §6.2), recorded at plan time.
    kind: str = ""
    metrics: list[EvidenceMetricRef] = Field(default_factory=list)
    #: Served from the evidence cache instead of the warehouse (§7.9).
    cache_hit: bool = False
    rows: int | None = None
    #: The top-N cutoff the plan applied, when it applied one.
    limit: int | None = None
    truncated: bool = False
    suppressed_cells: int = 0
    grade: str | None = None
    duration_ms: int = 0


class EvidenceReconciliation(ClosedModel):
    """The §7.8 verdict this turn recorded, split for display.

    ``summary`` is the recorded string verbatim (``status=<verdict>`` with
    optional ``; reason=...`` / ``; failed measures: ...``); ``status`` and
    ``detail`` are that same string parsed, never a second judgement.
    """

    #: ``passed`` | ``passed_with_suppression`` | ``failed`` |
    #: ``not_applicable`` — or ``unknown`` when a stored summary predates
    #: this grammar, which is reported rather than guessed at.
    status: str
    detail: str | None = None
    summary: str


class EvidencePayload(ClosedModel):
    """Everything an answer can honestly say about its own working."""

    probes: list[EvidenceProbePayload] = Field(default_factory=list)
    #: ``None`` when the turn recorded no reconciliation verdict at all —
    #: a kernel-only refinement, a META citation, a definitional answer.
    #: Distinct from a recorded ``not_applicable``, which means the check
    #: was reached and declined, and says why.
    reconciliation: EvidenceReconciliation | None = None
    #: Probes that actually went to the warehouse this turn.
    warehouse_queries: int = 0
    #: Probes served from the evidence cache instead.
    cache_hits: int = 0
    #: ``warehouse_queries == 0``: this answer cost the warehouse nothing.
    zero_probe_turn: bool = True
    #: The grade law (§5.3) over the finding grades recorded for this turn
    #: — the ceiling on what the whole answer may claim. ``None`` when the
    #: turn certified no findings.
    answer_grade: str | None = None
