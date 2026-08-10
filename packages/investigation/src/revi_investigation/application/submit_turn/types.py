"""The shapes a turn is made of: its request, its state, and its outcome."""

from __future__ import annotations

import time
import uuid
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from revi_investigation.application.calculation_glue import (
    EmptinessFact,
)
from revi_investigation.application.capability_ports import BenchmarkSpec
from revi_investigation.application.interpretation import (
    DefinitionalAnswer,
    PendingClarification,
)
from revi_investigation.application.llm.schemas import AnyRefinementOperator
from revi_investigation.application.planning import (
    PlanDiff,
)
from revi_investigation.application.ports import (
    LlmCallPolicy,
    LlmFailureKind,
    LlmUsage,
    RegisteredReferent,
)
from revi_investigation.application.refinement_llm import (
    REFERENT_HANDLE,
)
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    Session,
)
from revi_investigation.domain.refinements import (
    Expand,
    RankBy,
)
from revi_investigation.domain.settings import DEFAULT_SESSION_SETTINGS, SessionSettings
from revi_investigation.domain.turns import (
    ClarificationRequest,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_investigation_contracts.settings import EvidenceDepth
from revi_kernel.filters import (
    Predicate,
)
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import AggregationProbe, EvidenceProbe, SnapshotProbe
from revi_kernel.refs import MetricRef
from revi_kernel.scope import (
    AbsoluteRange,
    RangeMode,
    RelativeRange,
    TimeUnit,
)

_FALLBACK_WINDOW = RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS)


def _probe_kind(probe: EvidenceProbe) -> str:
    """Which member of the §6.2 probe union this node is.

    Recorded rather than left to be re-derived from a hash: "we read an
    aggregate" and "we read rows" are different promises to an analyst,
    and only the plan knows which was made.
    """
    if isinstance(probe, AggregationProbe):
        return "aggregation"
    if isinstance(probe, SnapshotProbe):
        return "snapshot"
    return "row_evidence"


def _probe_metrics(
    probe: EvidenceProbe, frame: EvidenceFrame | None
) -> list[dict[str, Any]]:
    """The metrics a probe read, with the contract version it read them at.

    Taken off the *executed* frame's schema when there is one — the
    connector stamps the version it actually compiled against, which is
    the version an audit needs. A probe that never executed falls back to
    the metric ids the plan asked for, with no version claimed.
    """
    out: dict[str, dict[str, Any]] = {}
    if frame is not None:
        for column in frame.schema.columns:
            if isinstance(column.ref, MetricRef):
                out.setdefault(
                    column.ref.id,
                    {"id": column.ref.id, "contract_version": column.contract_version},
                )
    if not out and isinstance(probe, (AggregationProbe, SnapshotProbe)):
        for measure in probe.measures:
            out.setdefault(measure.id, {"id": measure.id, "contract_version": None})
    return list(out.values())


def _not_applicable(reason: str) -> str:
    """The third reconciliation state: checked nothing, and says why.

    ``null`` used to mean both "not checked" and, indistinguishably from
    the outside, "nothing to say" — the one place in a product built on
    "never claim more than the evidence supports" where silence was
    ambiguous."""
    return f"status=not_applicable; reason={reason}"


def _period_phrase(window: AbsoluteRange) -> str:
    """A window as a reader would say it: "June 2026", or the two dates.

    Only ever a month NAME when the range really is one whole calendar
    month — a phrase that rounds 2026-06-08..2026-08-02 to "June" would be
    the misattribution this platform spends its warnings avoiding.
    """
    last_day = monthrange(window.end.year, window.end.month)[1]
    one_month = (window.start.year, window.start.month) == (window.end.year, window.end.month)
    if one_month and window.start.day == 1 and window.end.day == last_day:
        return window.start.strftime("%B %Y")
    return f"{window.start.isoformat()}..{window.end.isoformat()}"


_KERNEL_ONLY = (RankBy, Expand)


_REFERENT_TOKEN = REFERENT_HANDLE


#: The smallest per-call budget worth handing to a provider. Below it a
#: call cannot complete, so the turn stops here and says so — an honest
#: "this turn hit its cost ceiling" beats a provider budget refusal that
#: reads like an outage, and beats far worse: quietly answering with less.
_MIN_CALL_BUDGET_USD = Decimal("0.001")


#: The usage a stage that made no model call reports. Recorded nowhere (a
#: turn's ``llm`` ledger lists calls that happened), but the outcome types
#: carry a usage by construction, and a zero is honest where a fabricated
#: model name would not be.
_NO_MODEL_USAGE = LlmUsage(
    model="none",
    cost_usd=Decimal(0),
    input_tokens=0,
    output_tokens=0,
    schema_retries=0,
    duration_ms=0,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class SubmitTurnRequest:
    tenant: str
    question: str
    session_id: str | None = None
    # Typed FIRST turn (§8.1): an explicit investigation spec, no parent.
    spec: TypedInvestigationSpec | None = None
    # Typed-gesture path (§12): validated refinement DTOs skip NL entirely.
    refinements: tuple[AnyRefinementOperator, ...] | None = None
    # Watermark epochs (§7.1): opt into re-anchoring on a newer load.
    re_anchor: bool = False
    #: This turn is an ANSWER to the clarification the platform is holding,
    #: sent on the dedicated channel rather than typed into the chat box.
    #: A dedicated channel that is flattened into an utterance is not a
    #: channel: a verbatim option sent on it was once re-classified as a
    #: bare ``refinement`` at confidence 0.45, as a ROOT investigation,
    #: dropping the analyst's question. The class is known by construction
    #: here — no model call decides it.
    clarification_response: bool = False
    #: This turn asks for the ranked worklist and nothing else — a typed
    #: request the API answers from the detection feed. Complete by
    #: construction: there is nothing here to classify, and classifying it
    #: is what produced "That came through as a gesture rather than a
    #: request" at confidence 0.15, over a lane chip the platform drew.
    worklist_only: bool = False
    # Settings for THIS turn only, already bounds-checked by the API layer.
    # None runs the session's own settings; a per-turn override never
    # rewrites the session record, so one deep sweep or one debug turn does
    # not quietly become the session's new normal.
    settings: SessionSettings | None = None


# The canonical header shape and its builder live in the contracts package
# (single source of truth for API payloads, traces, and outcomes — §7.2).
ContextHeader = ContextHeaderPayload


#: Depth values a stored trace may legitimately carry (an unknown string
#: reads as STANDARD rather than crashing a replay of an older trace).
_EVIDENCE_DEPTHS = frozenset(depth.value for depth in EvidenceDepth)


@dataclass(frozen=True, slots=True)
class _PlanContext:
    """How one turn was planned, recovered from its trace record."""

    playbook_id: str | None = None
    window_explicit: bool = True
    evidence_depth: EvidenceDepth = EvidenceDepth.STANDARD
    #: ``(frame id, column, descending)`` the parent plan resolved. Carried
    #: so a turn that RE-SERVES the parent's plan re-serves its chart
    #: ordering too; without it, ``chart sort`` came back ``null`` on
    #: exactly the turns whose rows had not changed at all.
    chart_sorts: tuple[tuple[str, str, bool], ...] = ()


@dataclass(frozen=True, slots=True)
class MetaAnswer:
    """A META turn's answer: recorded provenance behind a referent (§8.3)."""

    referent: str
    label: str
    investigation_id: str
    probes: tuple[Mapping[str, Any], ...]
    operators: tuple[Mapping[str, Any], ...]
    grades: Mapping[str, str]
    reconciliation: str | None
    finding_values: tuple[tuple[str, Any], ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """The typed result of one submitted turn."""

    session: Session
    investigation: Investigation
    findings: tuple[Finding, ...]
    header: ContextHeaderPayload | None
    frames: tuple[tuple[str, EvidenceFrame], ...]
    warnings: tuple[str, ...]
    clarification: ClarificationRequest | None
    definitional: DefinitionalAnswer | None
    trace_id: str
    referents: tuple[RegisteredReferent, ...] = ()
    watermark_stale: bool = False
    meta: MetaAnswer | None = None
    reconciliation: str | None = None
    diff: PlanDiff | None = None
    #: Governed benchmark ranges for the metrics this turn's findings cite
    #: (design §9.1). Authored, sourced and cohort-labelled since KB wave 1
    #: and unreachable until now: the engine had no field to carry them and
    #: the API passed a literal empty tuple to the narrative composer.
    benchmarks: tuple[BenchmarkSpec, ...] = ()
    #: The controls this turn actually ran under — the presentation stage
    #: reads its narrative depth and remaining budget from here rather than
    #: re-deriving them from the session (which a per-turn override would
    #: have made wrong).
    settings: SessionSettings = DEFAULT_SESSION_SETTINGS
    #: Why this turn has nothing to say, when it has nothing to say. An
    #: empty answer used to be indistinguishable from a quiet one; this is
    #: the structured fact a presentation layer writes the difference from
    #: (empty population vs. data with no publishable finding).
    emptiness: EmptinessFact | None = None
    #: ``(frame id, column, descending)`` for every frame the PLAN ordered.
    #: The chart builder reads it so a ranked answer and the chart under it
    #: cannot disagree about which cell is first.
    chart_sorts: tuple[tuple[str, str, bool], ...] = ()



#: How many clarifications one thread may issue back-to-back before the
#: engine commits to its best reading and answers (design §2.8).
#:
#: Two, because the first is a legitimate dialogue move and the second is
#: the follow-up that should have landed. A third is the platform talking
#: to itself: a thread left unbounded ran to four, and the turn that
#: finally executed had dropped the analyst's question entirely.
MAX_CONSECUTIVE_CLARIFICATIONS = 2


def _join_question_and_answer(question: str | None, answer: str) -> str:
    """One utterance from the analyst's question and their clarifying reply.

    Both halves are the analyst's own words and the join is deterministic —
    nothing is paraphrased or invented, and the combined text is recorded
    on the trace so the resolution is auditable.
    """
    original = (question or "").strip()
    reply = answer.strip()
    if not original or original == reply or reply in original:
        return reply
    return f"{original} — {reply}"


def _predicate_label(predicate: Predicate) -> str:
    values = ", ".join(str(v) for v in predicate.values)
    return f"{predicate.dimension.id} {predicate.op.value} [{values}]".strip()


@dataclass
class _TurnState:
    """Mutable per-turn bookkeeping feeding the §14 trace."""

    turn_id: str
    investigation_id: str
    trace_id: str
    question: str
    #: What the analyst actually typed, before any resume joined the
    #: interrupted question onto it. ``question`` is the RESOLVED utterance
    #: the pipeline runs; this is the one to quote back at them and the one
    #: to match against options they were offered.
    utterance: str = ""
    #: The controls in force for this turn (session settings, or the
    #: per-turn override the caller sent).
    settings: SessionSettings = DEFAULT_SESSION_SETTINGS
    started: float = field(default_factory=time.monotonic)
    timings_ms: dict[str, int] = field(default_factory=dict)
    llm_usages: list[tuple[str, LlmUsage, LlmFailureKind | None]] = field(default_factory=list)
    template_hashes: dict[str, str] = field(default_factory=dict)
    watermark_stale: bool = False
    epoch_transition: bool = False
    #: The clarification this turn is answering, when there is one.
    pending: PendingClarification | None = None
    #: What the engine decided on the analyst's behalf, in their words —
    #: surfaced as turn warnings and recorded on the trace. A committed
    #: interpretation that is not stated is a guess.
    assumptions: list[str] = field(default_factory=list)
    #: The investigation this turn continues in the SESSION GRAPH without
    #: refining it — a clarification being answered. Distinct from the
    #: refinement parent: there is no plan diff and no sum-of-cells
    #: reconciliation to make against a turn that produced no plan, but the
    #: edge is real and the lineage is a forest without it.
    lineage_parent: str | None = None
    #: A date basis this turn is required to read on, because the analyst
    #: picked it from a clarification the platform asked.
    basis_override: str | None = None
    #: ``(dimension, values)`` the analyst chose from a value clarification,
    #: substituted into the resumed question's scope.
    scope_override: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: Clarification options this turn applied on the analyst's behalf. One
    #: is a disclosure; a second would be the engine holding a conversation
    #: with itself, so the ceiling is one per turn.
    applied_bindings: list[str] = field(default_factory=list)

    def time_stage(self, stage: str) -> None:
        now = time.monotonic()
        self.timings_ms[stage] = int((now - self.started) * 1000)
        self.started = now

    # -- the per-turn cost ledger (design §7.1 settings, §14 trace) --------

    def record_llm(
        self, template: str, usage: LlmUsage, failure: LlmFailureKind | None = None
    ) -> None:
        self.llm_usages.append((template, usage, failure))

    @property
    def llm_spend(self) -> Decimal:
        """What this turn's model calls have cost so far."""
        return sum((usage.cost_usd for _, usage, _ in self.llm_usages), Decimal(0))

    @property
    def budget_remaining(self) -> Decimal | None:
        """What is left of the turn's ceiling, or ``None`` when unset.

        ``None`` is not "unlimited spending" — it is "no per-turn ledger",
        which leaves every call bounded by the deployment's own per-call
        cap exactly as it was before this control existed.
        """
        ceiling = self.settings.max_turn_cost_usd
        return None if ceiling is None else ceiling - self.llm_spend

    def call_policy(self) -> LlmCallPolicy:
        """Model tier + what is left of the budget, for the next call."""
        return LlmCallPolicy(
            model=self.settings.model_tier, max_cost_usd=self.budget_remaining
        )
