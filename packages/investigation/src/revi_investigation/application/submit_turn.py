"""Turn orchestration: the §8.1 compile path, the §8.2 refinement path, and
the §8.3 zero-probe paths, over one explicit typed context.

Turn dispatch (after the session watermark check):

- **Typed first turn** — a request carrying a ``TypedInvestigationSpec``
  is a NEW_INVESTIGATION by construction and needs no parent: the spec
  states the metrics, dimensions, scope and window outright, so
  classification and interpretation are simply *known*, not guessed
  (zero model calls). Everything after the spec is built is the ordinary
  pipeline, §6.6 validation included. This is the anchor typed
  refinements always needed: a portfolio card's drill handle, or a chart
  click in a session with no prior answer, opens an investigation
  instead of returning "nothing to refine yet".
- **Typed gesture** — a request carrying refinement DTOs skips NL entirely
  and enters the refinement pipeline at the operator converter. Unlike
  the typed first turn, this one *edits* the session's latest
  investigation and therefore does require a parent.
- **NEW_INVESTIGATION** — classify → interpret → plan → §6.6 validation →
  cache-first execution → deterministic calculation → findings/referents.
- **DEFINITIONAL** — governed pack content with provenance; zero probes.
- **REFINEMENT** — resolve referents against the live registry → emit
  operators from the closed set → convert → ``apply_refinements`` (context
  conflicts surface *before* execution as clarification outcomes, never
  500s) → DrillInto targets pin ONE cohort at the session watermark →
  replan → plan diff vs the deterministically rebuilt parent plan →
  cache-first execution (unchanged probes never touch the warehouse) →
  auto-reconciliation against the parent totals on splits/drills
  (RECONCILIATION_FAILED is a surfaced warning + event, never silent,
  never fatal) → child Investigation + RefinementEdge.
- **PRESENTATION_ONLY / META / CONTEXT_CONTROL / kernel-only refinements**
  — answered from persisted frames, traces, and the context object with
  ZERO repository calls (spy-asserted per §18.1-14).

Watermark epochs (§7.1): every turn compares the session pin against the
newest completed load; staleness is surfaced (``watermark_stale``, a
warning event) and the analyst chooses — ``re_anchor=True`` starts a new
epoch, re-resolves relative windows against the new anchor, and records
the transition in the trace. Pinned continuation stays byte-stable.

Clarifications are successful outcomes: they cross this boundary as data
on the :class:`TurnOutcome`, never as exceptions.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from revi_investigation.application.anchoring import window_anchor
from revi_investigation.application.calculation_glue import (
    CalculateMetricsService,
    CalculationResult,
    EmptinessFact,
)
from revi_investigation.application.capability_ports import BenchmarkSpec, PackPort, TransformPort
from revi_investigation.application.cohorts import PinCohortService
from revi_investigation.application.comparison import window_mismatch_warning
from revi_investigation.application.execution import (
    ExecutedProbe,
    ExecuteInvestigationService,
    SuppressionCensus,
    bounded_cells_warning,
    suppression_census,
)
from revi_investigation.application.findings import (
    EvaluateFindingsService,
    FindingsResult,
    find_primary_compare,
)
from revi_investigation.application.gestures import drill_suggestion, parse_gesture
from revi_investigation.application.interpretation import (
    ClassificationOutcome,
    ClassifyTurnService,
    DefinitionalAnswer,
    InterpretationOutcome,
    InterpretedInvestigation,
    InterpretQuestionService,
    PendingClarification,
)
from revi_investigation.application.llm.schemas import AnyRefinementOperator
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    DiffPlanService,
    InvestigationPlan,
    PlanDiff,
    resolved_orderings,
)
from revi_investigation.application.ports import (
    FrameStore,
    InvestigationStore,
    LlmCallPolicy,
    LlmFailureKind,
    LlmUsage,
    ReferentRegistryStore,
    RegisteredReferent,
    SessionStore,
    TraceRecord,
    TraceStore,
    TurnEvent,
    TurnEventBus,
)
from revi_investigation.application.refinement_llm import (
    REFERENT_HANDLE,
    EmitRefinementsService,
    ReferentResolution,
    ResolveReferentsService,
    resolve_referent_tokens,
    to_domain_operators,
)
from revi_investigation.application.rendering import MONEY_UNIT as _MONEY_UNIT_NAME
from revi_investigation.application.rendering import money
from revi_investigation.application.validation import (
    PlanClarificationNeeded,
    PlanValidationService,
    ValidatedPlan,
    map_predicates,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    ContextPin,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    InvestigationStatus,
    RefinementEdge,
    Session,
)
from revi_investigation.domain.refinements import (
    AddFilter,
    DrillInto,
    Expand,
    RankBy,
    Refinement,
    SetDimensions,
    apply_refinements,
    detect_conflict,
)
from revi_investigation.domain.settings import DEFAULT_SESSION_SETTINGS, SessionSettings
from revi_investigation.domain.turns import (
    ClarificationBinding,
    ClarificationRequest,
    TurnClass,
    TurnClassification,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.header import ContextHeaderPayload, build_header_payload
from revi_investigation_contracts.refinements import AbsoluteWindowModel, AddFilterModel
from revi_investigation_contracts.settings import EvidenceDepth
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.cohort import CohortRef
from revi_kernel.errors import (
    ContextConflictError,
    DataLoadingError,
    DateBasisInvalidError,
    GrainIncompatibleError,
    ReviError,
    UnsupportedConceptError,
)
from revi_kernel.filters import EMPTY_SCOPE, Predicate, PredicateOp, and_merge, iter_predicates
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import AggregationProbe, EvidenceProbe, SnapshotProbe
from revi_kernel.refs import SERVICE, DimensionRef, EntityGrain, Grain, MetricRef, ReferentId
from revi_kernel.scope import (
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    derive_comparison,
    resolve_window,
)
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

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
    #: channel: round-3 R3-07 sent a verbatim option on it and watched the
    #: reply get re-classified as a bare ``refinement`` at confidence 0.45,
    #: as a ROOT investigation, dropping the analyst's question. The class
    #: is known by construction here — no model call decides it.
    clarification_response: bool = False
    #: This turn asks for the ranked worklist and nothing else — a typed
    #: request the API answers from the detection feed. Complete by
    #: construction: there is nothing here to classify, and classifying it
    #: is what produced "That came through as a gesture rather than a
    #: request" at confidence 0.15, over a lane chip the platform drew
    #: (round-3 R3-09).
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
    #: ``(frame id, column, descending)`` for every frame the PLAN ordered
    #: (round-3 R3-13). The chart builder reads it so a ranked answer and
    #: the chart under it cannot disagree about which cell is first.
    chart_sorts: tuple[tuple[str, str, bool], ...] = ()


#: The contract ``kind`` that reports a balance at a moment rather than a
#: quantity accumulated over a window. Compared as a string so this module
#: does not import the calculation contracts package (import-linter).
_SNAPSHOT_KIND = "snapshot"


def snapshot_as_of(
    spec: AnalysisSpec, session: Session, pack: PackPort | None,
    measure_ids: Sequence[str] = (),
) -> date | None:
    """The as-of date for a turn measured entirely by snapshot contracts.

    ``None`` — i.e. "render the window" — unless EVERY measure this turn
    READ is ``kind: snapshot``. Eight contracts are (the whole A/R and
    inventory family), and they read a balance standing at the watermark:
    they apply no start..end predicate, so a header, a title or a sentence
    that names one is asserting a scoping that did not happen (round-2
    FN-2). A turn mixing a snapshot with a flow keeps the window, because
    the window governs the flow half and is real.

    Round-3 R3-15: the test was ``spec.measures``, which a *playbook* turn
    leaves empty — so ``timely_filing_at_risk_dollars`` (``kind: snapshot``)
    rendered as ``2026-07-01..2026-07-31 (service)`` with the narrative
    "across the July 2026 service period … $1,424,231.54 in Unbilled open
    inventory", and the very next turn published the same metric as an
    as-of balance ~14x larger with no bridging sentence. ``measure_ids``
    carries what the PLAN read, so the rule is per-kind rather than
    per-route.
    """
    if pack is None:
        return None
    names = [ref.id for ref in spec.measures] or list(measure_ids)
    if not names:
        return None
    for metric_id in names:
        contract = pack.metric(metric_id)
        if contract is None or str(contract.kind) != _SNAPSHOT_KIND:
            return None
    return session.watermark.newest_data_date


def plan_measure_ids(plan: InvestigationPlan | None) -> tuple[str, ...]:
    """Every metric id this plan's probes read, in plan order."""
    if plan is None:
        return ()
    out: list[str] = []
    for node in plan.nodes:
        probe = node.probe
        if not isinstance(probe, (AggregationProbe, SnapshotProbe)):
            continue
        for measure in probe.measures:
            if measure.id not in out:
                out.append(measure.id)
    return tuple(out)


def build_context_header(
    spec: AnalysisSpec,
    session: Session,
    *,
    pack: PackPort | None = None,
    corrections: Mapping[str, Mapping[str, str]] | None = None,
    measure_ids: Sequence[str] = (),
) -> ContextHeaderPayload:
    """Delegate to the canonical contracts builder (§7.2 single source)."""
    context = spec.context
    return build_header_payload(
        window=context.window,
        comparison=context.comparison,
        predicates=tuple(iter_predicates(context.scope)),
        pinned_predicates=tuple(pin.predicate for pin in context.pins),
        cohort=context.cohort,
        watermark_id=session.watermark.id,
        as_of=snapshot_as_of(spec, session, pack, measure_ids),
        corrections=corrections,
    )


def probe_families_empty_warning(
    validated: ValidatedPlan,
    executed: tuple[ExecutedProbe, ...],
    findings: tuple[Finding, ...],
) -> str | None:
    """Name every probe family that ran and published nothing (round-2 FN-10).

    A nine-family portfolio playbook read ~98 rows across nine probes —
    denial trend, denied dollars, cash trend, underpayment, filing risk,
    A/R health, unbilled, credits, posting lag — and published three
    findings, all from one of them. AR>90, DNFB, credit-balance liability,
    underpayment variance, cash trend and posting lag were all measured
    and all discarded, and nothing on the answer said so: the reply to
    "what are the three biggest problems in my revenue cycle" was a claim
    of coverage the pipeline had not kept.

    Cross-probe harvesting and comparable cross-metric ranking are the real
    fix and are a redesign. This is the disclosure that must ship first: a
    coded warning naming each probe, its metric ids and the rows it
    returned, so a reader can see what was looked at and dropped.

    Silent on a turn that published nothing at all — that is the emptiness
    fact's job, and two statements of the same nothing is one too many.
    """
    if not findings:
        return None
    published = {ref.id for finding in findings for ref in finding.metric_refs}
    if not published:
        return None
    by_node = {item.node_id: item for item in executed}
    # Grouped by metric family, not by node: a comparison twin and its
    # current-window probe are one family measured once, and reporting them
    # as two dropped probes would inflate the very count this warning
    # exists to state honestly.
    families: dict[tuple[str, ...], list[str]] = {}
    rows_by_family: dict[tuple[str, ...], int] = {}
    for node in validated.plan.nodes:
        item = by_node.get(node.id)
        metrics = tuple(
            sorted(
                {
                    entry["id"]
                    for entry in _probe_metrics(
                        node.probe, item.frame if item is not None else None
                    )
                }
            )
        )
        if not metrics or published.intersection(metrics):
            continue
        families.setdefault(metrics, []).append(node.id)
        rows_by_family[metrics] = rows_by_family.get(metrics, 0) + (
            len(item.frame.rows) if item is not None else 0
        )
    if not families:
        return None
    named = "; ".join(
        f"{', '.join(metrics)} ({', '.join(nodes)}, {rows_by_family[metrics]} row(s))"
        for metrics, nodes in families.items()
    )
    return (
        f"probe_families_empty: {len(families)} metric famil(ies) on this plan were read and "
        f"produced no published finding, so nothing above speaks for them: {named}. The "
        "findings rank within the families that did publish — they are not a cross-family "
        "comparison, and a family's absence is not evidence that it is fine."
    )


#: How close a child's total must land to the parent finding it drilled to
#: be called agreed. The same half-percent the card/answer reconciliation
#: uses, for the same reason: below it the difference is rounding, above it
#: two published figures for one cell disagree and a reader must be told.
_CONTAINMENT_TOLERANCE = Decimal("0.005")


def _frame_money_total(
    frames: tuple[tuple[str, EvidenceFrame], ...],
) -> tuple[int | None, str | None]:
    """Sum the money column of the last frame that has one.

    "Last" because frames are listed in creation order, so the final
    money-bearing frame is the one after every transform — the frame the
    findings stage read.
    """
    for _, frame in reversed(frames):
        for index, column in enumerate(frame.schema.columns):
            if column.unit != _MONEY_UNIT_NAME:
                continue
            total = 0
            for row in frame.rows:
                value = row[index]
                if value is not None:
                    total += int(value)
            measure = column.ref.id if isinstance(column.ref, MetricRef) else column.name
            return total, measure
    return None, None


def containment_reconciliation(
    parent: Investigation,
    calculation: CalculationResult,
    operators: tuple[Refinement, ...],
) -> tuple[str, bool] | None:
    """Reconcile a drill against the PARENT FINDING it was launched from.

    Round-2 FN-4. Clicking "drill into DNFB accumulation: Northgate
    general-surgery discharges" published ``dnfb_dollars = $195,873.92``;
    clicking the platform's own "drill into F1" chip on that same answer
    published ``$178,216.82`` — same metric id, same contract version, same
    pack, same window, same scope, both graded direct, neither warning —
    and the child's reconciliation read ``not_applicable; this turn
    produced no compared money frame to reconcile against the parent``.
    The predicate was true and beside the point: it asked whether there was
    a *compare* frame and an *undimensioned parent total*, when what the
    reader had in front of them was one finding's figure and one drill's.

    The mirror-image symptom, same predicate: a drill decomposing a
    parent's $410,166.15 into nine categories that sum to $410,166.15
    exactly reported ``not_applicable; the parent investigation holds no
    undimensioned 'denied_dollars' total`` — a perfect tie-out available
    and withheld.

    So a child scoped to a parent finding's cell reconciles against that
    finding's own published figure, and publishes both numbers, the delta
    and the percentage **even when they agree**: "we checked and it agreed"
    is the verdict this whole grammar exists to be able to say.

    Returns ``(summary, passed)``, or ``None`` when this turn drilled no
    parent finding that published a money figure — in which case the
    caller's existing verdicts stand.
    """
    targets = {op.target.value for op in operators if isinstance(op, DrillInto)}
    if not targets:
        return None
    finding = next((f for f in parent.findings if f.referent.value in targets), None)
    if finding is None or finding.impact_cents is None:
        return None
    child_cents, measure = _frame_money_total(calculation.frames)
    if child_cents is None:
        return None
    parent_cents = finding.impact_cents
    delta = child_cents - parent_cents
    fraction = Decimal(delta) / Decimal(abs(parent_cents) or 1)
    passed = abs(fraction) <= _CONTAINMENT_TOLERANCE
    same_measure = measure is not None and measure in {ref.id for ref in finding.metric_refs}
    scope = (
        f"the same metric ({measure}) over the cell {finding.referent.value} names"
        if same_measure
        else f"{measure or 'this turn'} against {finding.referent.value}"
    )
    summary = (
        f"status={'passed' if passed else 'failed'}; scope=containment; "
        f"parent {finding.referent.value}={money(parent_cents)}; child={money(child_cents)}; "
        f"delta={money(delta)} ({float(fraction):+.1%}); basis={scope}"
    )
    return summary, passed


#: How a refuted value is recorded on the turn that refuted it. Parsed back
#: so a LATER clarification cannot re-offer the value the engine has already
#: proved does not exist (round-2 FN-6).
_REFUTED_VALUES = re.compile(r"^PREDICATE_VALUE_UNMATCHED: \S+ \[(?P<values>.*?)\]")
_QUOTED = re.compile(r"'([^']+)'|\"([^\"]+)\"")


def refuted_values(reasons: Iterable[str]) -> frozenset[str]:
    """Every dimension value this session has already proved does not exist."""
    out: set[str] = set()
    for reason in reasons:
        match = _REFUTED_VALUES.match(reason.strip())
        if match is None:
            continue
        for single, double in _QUOTED.findall(match.group("values")):
            value = (single or double).strip()
            if value:
                out.add(value.casefold())
    return frozenset(out)


def drop_refuted_options(
    clarification: ClarificationRequest, refuted: frozenset[str]
) -> ClarificationRequest:
    """The same question, minus every option naming a refuted value.

    Round-2 FN-6, and the single most-reported defect of the review: a
    reply of ``"Federal Medicare"`` — typed verbatim from the options the
    platform had just offered — came back with three new options, two of
    which re-proposed the hallucinated payer the platform had CORRECTLY
    refused one turn earlier ("UnitedHealthcare, limited to the Federal
    Medicare financial class"; "The Federal Medicare payer instead of
    UnitedHealthcare"). Three turns, $0.3583, zero answers, and the loop
    was closed by the platform's own suggestions.

    The existence check that produced the refusal is the check every option
    must pass. Nothing is rewritten: an option naming a value this session
    has proved absent is dropped, and if that empties the list the question
    says so rather than shipping a choice with nothing in it.
    """
    if not refuted or not clarification.options:
        return clarification
    kept = tuple(
        option
        for option in clarification.options
        if not any(value in option.casefold() for value in refuted)
    )
    if len(kept) == len(clarification.options):
        return clarification
    if kept:
        return replace(clarification, options=kept, bindings=_bindings_for(clarification, kept))
    return replace(
        clarification,
        question=(
            f"{clarification.question} (I had suggestions here and dropped them: each one "
            "named a value this data does not hold, which is the thing I already refused. "
            "Name a different one, or ask me what exists.)"
        ),
        options=(),
        bindings=(),
        reason=f"{clarification.reason}; all generated options named a refuted value",
    )


def _bindings_for(
    clarification: ClarificationRequest, kept: tuple[str, ...]
) -> tuple[ClarificationBinding, ...]:
    """The bindings of the options that survived a drop.

    Dropping an option must drop its meaning with it: a binding left behind
    for an option nobody can see is a resolution the analyst never chose.
    """
    surviving = {" ".join(option.split()).casefold().rstrip(".") for option in kept}
    return tuple(
        binding
        for binding in clarification.bindings
        if " ".join(binding.option.split()).casefold().rstrip(".") in surviving
    )


#: How far back a clarification option's DRY RUN looks. Deliberately the
#: widest window the load will admit — a value check asks "does this exist
#: in the data", and asking it over one narrow month would refuse a payer
#: that is simply quiet in that month. The observed-value read is cached per
#: (watermark, entity, dimension, window), so every option in a session
#: shares one read.
_OPTION_CHECK_YEARS = 3


def _option_window(session: Session) -> AbsoluteWindowModel:
    """The window a clarification option is dry-run over (see above)."""
    end = session.watermark.newest_data_date
    floor = session.watermark.oldest_data_date
    start = date(end.year - _OPTION_CHECK_YEARS, 1, 1)
    return AbsoluteWindowModel(start=max(start, floor) if floor is not None else start, end=end)


def _with_chosen_values(
    spec: AnalysisSpec, chosen: tuple[tuple[str, tuple[str, ...]], ...]
) -> AnalysisSpec:
    """Substitute the values an analyst picked from a value clarification.

    Round-3 R3-07. Every predicate on a chosen dimension is REPLACED, not
    added to: the clarification exists because the value in the question
    does not exist in the data, so carrying it alongside the real one would
    re-raise the refusal that started the dialogue — which is exactly what
    it did live. The dimension the analyst never mentioned is untouched,
    and a dimension the re-interpretation dropped is re-added, because the
    choice is the analyst's and it must survive the model's second reading.
    """
    if not chosen:
        return spec
    values_by_dimension = {dimension: values for dimension, values in chosen if values}
    if not values_by_dimension:
        return spec

    def substitute(predicate: Predicate) -> Predicate:
        values = values_by_dimension.get(predicate.dimension.id)
        if values is None:
            return predicate
        op = PredicateOp.IN if len(values) > 1 else PredicateOp.EQ
        return replace(predicate, op=op, values=tuple(values))

    scope = map_predicates(spec.context.scope, substitute)
    present = {p.dimension.id for p in iter_predicates(scope)}
    missing = [
        Predicate(
            dimension=DimensionRef(dimension),
            op=PredicateOp.IN if len(values) > 1 else PredicateOp.EQ,
            values=tuple(values),
        )
        for dimension, values in values_by_dimension.items()
        if dimension not in present
    ]
    if missing:
        scope = and_merge(scope, *missing)
    return spec.with_context(replace(spec.context, scope=scope))


def _bindings_from_trace(payload: Mapping[str, Any]) -> tuple[ClarificationBinding, ...]:
    """Rebuild the option bindings a clarification turn recorded.

    Read back off the trace for the same reason ``_pending_clarification``
    reads the lineage: a turn is a stateless request and the session may
    resume in another process. A record written before this field existed
    simply yields nothing, and the reply falls back to being read as text.
    """
    raw = payload.get("clarification_bindings") or ()
    out: list[ClarificationBinding] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        option, kind = entry.get("option"), entry.get("kind")
        if not isinstance(option, str) or not isinstance(kind, str):
            continue
        scope: list[tuple[str, tuple[str, ...]]] = []
        for item in entry.get("scope") or ():
            if not isinstance(item, Mapping):
                continue
            dimension = item.get("dimension")
            if isinstance(dimension, str):
                scope.append(
                    (dimension, tuple(str(v) for v in (item.get("values") or ())))
                )
        basis, playbook_id = entry.get("basis"), entry.get("playbook_id")
        out.append(
            ClarificationBinding(
                option=option,
                kind=kind,
                metric_ids=tuple(str(m) for m in (entry.get("metric_ids") or ())),
                dimension_ids=tuple(str(d) for d in (entry.get("dimension_ids") or ())),
                playbook_id=playbook_id if isinstance(playbook_id, str) else None,
                scope=tuple(scope),
                basis=basis if isinstance(basis, str) else None,
            )
        )
    return tuple(out)


def _turn_census(
    calculation: CalculationResult, threshold: int
) -> SuppressionCensus | None:
    """This turn's cell arithmetic, counted once (round-3 R3-18).

    Read off the frame the published figures came from — the widest
    dimensional frame the plan produced — because that is the population
    the reader is counting. ``EvidenceFrame.suppressed_cells`` counts nulled
    VALUES, several per row, and quoting it as a population is how "3 of 15
    cells" was published over 12 payer cells of which none were withheld.
    """
    best: SuppressionCensus | None = None
    for _, frame in calculation.frames:
        if not frame.rows:
            continue
        census = suppression_census(frame, threshold)
        if best is None or census.total > best.total:
            best = census
    return best


def _predicate_label(predicate: Predicate) -> str:
    values = ", ".join(str(v) for v in predicate.values)
    return f"{predicate.dimension.id} {predicate.op.value} [{values}]".strip()


class OpenSessionService:
    """Open or join a session; new sessions pin the newest completed
    watermark and the pack version at epoch 0 (design §8.1 step 2)."""

    def __init__(
        self, sessions: SessionStore, repository: AnalyticalRepository, pack: PackPort
    ) -> None:
        self._sessions = sessions
        self._repository = repository
        self._pack = pack

    async def open(
        self,
        *,
        tenant: str,
        session_id: str | None,
        settings: SessionSettings | None = None,
    ) -> Session:
        """Open or re-join a session, optionally (re-)applying settings.

        ``settings=None`` leaves an existing session's controls exactly as
        they were: a turn re-opens its own session on every call, and a
        reconnect that quietly reset the analyst's model tier to the
        deployment default would be the worst kind of silent downgrade.
        """
        if session_id is not None:
            existing = await self._sessions.get(session_id)
            if existing is not None:
                if settings is not None and settings != existing.settings:
                    existing = existing.with_settings(settings)
                    await self._sessions.save(existing)
                return existing
        newest = await self.newest_watermark()
        session = Session(
            id=session_id if session_id is not None else _new_id("sess"),
            tenant=tenant,
            pack_version=PackVersionRef(self._pack.pack_id, self._pack.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=newest),),
            created_at=datetime.now(UTC),
            settings=settings if settings is not None else DEFAULT_SESSION_SETTINGS,
        )
        await self._sessions.save(session)
        return session

    async def newest_watermark(self) -> DataWatermark:
        watermarks = await self._repository.list_watermarks()
        if not watermarks:
            raise DataLoadingError("no completed warehouse load is available yet")
        return watermarks[-1]

    async def re_anchor(self, session: Session, newest: DataWatermark, turn_id: str) -> Session:
        """Start a new watermark epoch (§7.1) — an explicit, recorded event."""
        updated = session.with_new_epoch(
            WatermarkEpoch(index=len(session.epochs), watermark=newest, started_at_turn=turn_id)
        )
        await self._sessions.save(updated)
        return updated


#: How many clarifications one thread may issue back-to-back before the
#: engine commits to its best reading and answers (design §2.8).
#:
#: Two, because the first is a legitimate dialogue move and the second is
#: the follow-up that should have landed. A third is the platform talking
#: to itself: the live transcript that motivated this ran to four, and the
#: turn that finally executed had dropped the analyst's question entirely.
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


@dataclass
class _TurnState:
    """Mutable per-turn bookkeeping feeding the §14 trace."""

    turn_id: str
    investigation_id: str
    trace_id: str
    question: str
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
    #: edge is real and the lineage was a forest without it (round-3 R3-07).
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


class SubmitTurnService:
    """§8 turn engine on injected services."""

    def __init__(
        self,
        *,
        open_session: OpenSessionService,
        classifier: ClassifyTurnService,
        interpreter: InterpretQuestionService,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        executor: ExecuteInvestigationService,
        calculator: CalculateMetricsService,
        evaluator: EvaluateFindingsService,
        referent_resolver: ResolveReferentsService,
        refinement_emitter: EmitRefinementsService,
        cohort_pinner: PinCohortService,
        differ: DiffPlanService,
        transforms: TransformPort,
        pack: PackPort,
        referents: ReferentRegistryStore,
        investigations: InvestigationStore,
        traces: TraceStore,
        frames: FrameStore,
        events: TurnEventBus,
    ) -> None:
        self._open_session = open_session
        self._classifier = classifier
        self._interpreter = interpreter
        self._planner = planner
        self._validator = validator
        self._executor = executor
        self._calculator = calculator
        self._evaluator = evaluator
        self._referent_resolver = referent_resolver
        self._refinement_emitter = refinement_emitter
        self._cohort_pinner = cohort_pinner
        self._differ = differ
        self._transforms = transforms
        self._pack = pack
        self._referents = referents
        self._investigations = investigations
        self._traces = traces
        self._frames = frames
        self._events = events

    # ----------------------------------------------------------------- api

    async def submit(self, request: SubmitTurnRequest) -> TurnOutcome:
        session = await self._open_session.open(
            tenant=request.tenant, session_id=request.session_id
        )
        state = _TurnState(
            turn_id=_new_id("turn"),
            investigation_id=_new_id("inv"),
            trace_id=_new_id("trace"),
            question=request.question,
            # a per-turn override applies to this turn only; the session's
            # own settings are the default and are never rewritten here
            settings=request.settings if request.settings is not None else session.settings,
        )
        session = await self._check_watermark(session, state, request)

        if request.worklist_only:
            # A typed worklist request is a whole request. Zero probes, zero
            # model calls, and a COMPLETE investigation rather than a
            # clarification: the answer is the ranked list the API attaches,
            # and asking the analyst what they meant by a control this
            # platform drew is the platform not recognising its own output.
            return await self._worklist_turn(session, state)

        if request.spec is not None:
            # typed FIRST turn: an explicit investigation, never a
            # refinement — no NL, no classification, no LLM
            return await self._typed_investigation_turn(session, state, request.spec)

        if request.refinements is not None:
            # typed-gesture path: no NL, no classification, no LLM
            return await self._refinement_turn(
                session, state, None, dto_ops=tuple(request.refinements)
            )

        # A gesture this platform printed is read back before anything is
        # sent to a model: "drill into F1" is a string we emitted, not
        # language to be interpreted (§7.6, extended to the whole
        # utterance). It used to reach the classifier, come back with a
        # clarification question, and dead-end on the platform's own
        # suggestion.
        gesture = parse_gesture(state.question, await self._referents.list_for_session(session.id))
        if gesture is not None:
            state.time_stage("classify")
            return await self._refinement_turn(
                session, state, None, dto_ops=gesture.operators
            )

        exhausted = self._budget_stop(state, "reading your question")
        if exhausted is not None:
            return await self._clarification_outcome(session, state, None, exhausted)

        # What (if anything) this session already asked and has not had
        # answered. Classification without it cannot tell an answer from a
        # fresh question — see PendingClarification.
        pending = await self._pending_clarification(session)
        state.pending = pending

        if request.clarification_response and pending is not None:
            # The analyst answered on the dedicated channel. There is
            # nothing left to classify: this IS a clarification response,
            # by construction, at zero model cost — and re-classifying it
            # is exactly how the original question got dropped (R3-07).
            state.time_stage("classify")
            return await self._clarification_response_turn(
                session,
                state,
                ClassificationOutcome(
                    classification=TurnClassification(
                        turn_class=TurnClass.CLARIFICATION_RESPONSE, confidence=1.0
                    ),
                    clarification=None,
                    usage=_NO_MODEL_USAGE,
                    template_hash="by_construction",
                ),
                pending,
            )

        known = await self._classification_by_construction(session, pending, state.question)
        if known is not None:
            state.time_stage("classify")
            assert known.classification is not None
            if known.classification.turn_class is TurnClass.DEFINITIONAL:
                return await self._definitional_outcome(session, state, known)
            return await self._new_investigation_turn(session, state, known)

        await self._stage(state, "classify")
        classified = await self._classifier.classify(
            request.question, pending=pending, policy=state.call_policy()
        )
        state.record_llm("classify_turn", classified.usage, classified.failure)
        state.template_hashes["classify_turn@v1"] = classified.template_hash
        state.time_stage("classify")

        if classified.clarification is not None or classified.classification is None:
            committed = self._commit_instead_of_clarifying(state, pending)
            if committed is None:
                clarification = classified.clarification or ClarificationRequest(
                    question="Could you rephrase that?", reason="unclassifiable turn"
                )
                return await self._clarification_outcome(
                    session, state, classified, clarification
                )
            # §2.8 convergence: stop asking, commit, and say what was assumed.
            state.assumptions.append(committed)
            return await self._new_investigation_turn(session, state, classified)

        turn_class = classified.classification.turn_class
        if turn_class is TurnClass.DEFINITIONAL:
            return await self._definitional_outcome(session, state, classified)
        if turn_class is TurnClass.NEW_INVESTIGATION:
            return await self._new_investigation_turn(session, state, classified)
        if turn_class is TurnClass.REFINEMENT:
            return await self._refinement_turn(session, state, classified, dto_ops=None)
        if turn_class is TurnClass.PRESENTATION_ONLY:
            return await self._presentation_turn(session, state, classified)
        if turn_class is TurnClass.META:
            return await self._meta_turn(session, state, classified)
        if turn_class is TurnClass.CONTEXT_CONTROL:
            return await self._context_control_turn(session, state, classified)
        if turn_class is TurnClass.CLARIFICATION_RESPONSE:
            return await self._clarification_response_turn(session, state, classified, pending)
        clarification = ClarificationRequest(
            question=(
                "That reads like an answer to a question I haven't asked — what would "
                "you like to investigate?"
            ),
            reason=f"turn class {turn_class.value} is not actionable here",
        )
        return await self._clarification_outcome(session, state, classified, clarification)

    async def _classification_by_construction(
        self, session: Session, pending: PendingClarification | None, state_question: str
    ) -> ClassificationOutcome | None:
        """The turn class this session's state already determines.

        Six of the seven classes in the §7.3 taxonomy describe an utterance
        *relative to something already on screen*: a refinement edits a
        prior answer, a presentation re-presents one, a meta turn asks how
        one was produced, a context-control turn adjusts a context a prior
        turn established, a clarification response answers a question this
        platform asked. A session that has completed no turn has none of
        those to point at. The first utterance of a session is a new
        investigation — not probably, by construction.

        It was nonetheless sent to a model every time, at the cost of a
        call and a chance of being wrong: live, a first-turn question came
        back REFINEMENT and was answered "there's no prior answer in this
        session to refine yet", which is true and useless. DEFINITIONAL is
        not lost here — interpretation still routes "what is PR3" to the
        pack lookup with zero probes; it is one stage later, not one
        classification away.

        A pending clarification is the one thing that makes a first
        utterance ambiguous again, so its presence hands the turn back to
        the model.
        """
        if pending is not None:
            return None
        if await self._latest_investigation(session, analytical=False) is not None:
            return None
        # DEFINITIONAL is the one other class a first utterance can be, and
        # it is decidable without a model too: a governed lead-in over a
        # term the pack resolves whole. Deciding it here keeps the
        # zero-probe path zero-*call* as well.
        definitional = self._interpreter.definitional_match(state_question)
        return ClassificationOutcome(
            classification=TurnClassification(
                turn_class=(
                    TurnClass.DEFINITIONAL if definitional else TurnClass.NEW_INVESTIGATION
                ),
                confidence=1.0,
            ),
            clarification=None,
            usage=_NO_MODEL_USAGE,
            template_hash="by_construction",
        )

    async def _worklist_turn(self, session: Session, state: _TurnState) -> TurnOutcome:
        """A typed worklist request: complete, zero-probe, zero-call (R3-09).

        The engine holds no worklist — it is the detection feed's, projected
        by the API — so this turn's job is to be a real node in the session:
        a COMPLETE investigation the lineage can hang follow-ups off, with
        the analyst's own request recorded as its question. The ranked cards
        ride on the response the API assembles around it.
        """
        state.time_stage("worklist")
        investigation = replace(
            self._minimal_investigation(
                session,
                state,
                InvestigationStatus.COMPLETE,
                ClassificationOutcome(
                    classification=TurnClassification(
                        turn_class=TurnClass.NEW_INVESTIGATION, confidence=1.0
                    ),
                    clarification=None,
                    usage=_NO_MODEL_USAGE,
                    template_hash="by_construction",
                ),
            ),
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            self._trace_record(session, state, None, extra={"worklist_request": True})
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=None,
            frames=(),
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            settings=state.settings,
        )

    # -------------------------------------------- answering a clarification

    async def _clarification_response_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome,
        pending: PendingClarification | None,
    ) -> TurnOutcome:
        """The analyst answered the question the platform asked.

        ``CLARIFICATION_RESPONSE`` has been in the §7.3 taxonomy since the
        beginning and had no branch here: it fell through to "that reads
        like an answer to a question I haven't asked", *which was returned
        as another clarification*. So a correctly-classified answer — even
        a verbatim option string — produced the loop it was meant to end.

        Resolution is deterministic and invents nothing: the analyst's
        original question and their answer are both their own words, joined
        and re-entered as one utterance. The original comes from the turn
        the clarification interrupted, so answering "just the imaging
        service line" resumes the denial-rate question instead of becoming
        a standalone request to look at imaging.
        """
        if pending is None:
            # Nothing outstanding: the old honest fallback still applies,
            # because there genuinely is no question this answers.
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question=(
                        "That reads like an answer to a question I haven't asked — what "
                        "would you like to investigate?"
                    ),
                    reason="clarification_response with no clarification pending",
                ),
            )
        # The strongest resolution first: the reply IS one of the options
        # the platform offered, and that option already carries the ids it
        # stands for. Nothing is re-read as language — the analyst chose a
        # thing this platform named, and the thing is applied to the
        # question it interrupted (round-3 R3-07).
        binding = pending.binding_for(state.question)
        if binding is not None and pending.original_question:
            state.question = pending.original_question
            state.lineage_parent = pending.investigation_id
            state.assumptions.append(
                f"Read as an answer to the question above: {pending.question!r} → "
                f"{binding.option!r}. Resuming {pending.original_question!r} with that "
                "applied; this answer is recorded as a child of the turn that asked."
            )
            return await self._apply_binding(session, state, classified, binding)
        resolved = _join_question_and_answer(pending.original_question, state.question)
        if resolved != state.question:
            state.assumptions.append(
                f"Read as an answer to the question above: {pending.question!r} → "
                f"{state.question!r}. Answering the original question with that applied."
            )
            state.question = resolved
        # Parented either way: an answer to a question this platform asked
        # belongs under that question, whether it matched an option or was
        # typed in the analyst's own words. Live, every clarification reply
        # in a 13-investigation session was saved as a ROOT node.
        state.lineage_parent = pending.investigation_id
        return await self._new_investigation_turn(session, state, classified)

    async def _apply_binding(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        binding: ClarificationBinding,
    ) -> TurnOutcome:
        """Re-run the interrupted question with one option applied.

        The application is a substitution into the ORIGINAL question's
        pipeline, never a new question:

        * ``date_basis`` re-interprets the same words with the basis fixed,
          so the playbook routing, the window vocabulary and the concepts
          the analyst's sentence carried all survive — the runway question
          comes back as the runway question;
        * everything else runs as the typed investigation the option's own
          ids describe, which is the same disposal a portfolio card's drill
          handle goes through.
        """
        if binding.kind == "date_basis" and binding.basis:
            state.basis_override = binding.basis
            return await self._new_investigation_turn(session, state, classified)
        if binding.kind == "predicate_value" and binding.scope:
            # The analyst picked a value from the twelve this warehouse
            # actually holds, offered because the one they typed does not
            # exist. Joining their reply onto the original sentence and
            # re-interpreting it leaves the refuted value in the question —
            # live, that came straight back as the SAME refusal. The choice
            # is a substitution and is applied as one.
            state.scope_override = binding.scope
            return await self._new_investigation_turn(session, state, classified)
        spec = self._spec_for_binding(session, binding)
        if spec is None:
            # Nothing typed to run: fall back to the analyst's words, which
            # is what happened before bindings existed and is still honest.
            state.question = _join_question_and_answer(state.question, binding.option)
            return await self._new_investigation_turn(session, state, classified)
        pins = await self._inherited_pins(session)
        if pins:
            spec = spec.with_context(replace(spec.context, pins=pins))
        return await self._run_analysis(
            session,
            state,
            classified,
            spec=spec,
            playbook_id=binding.playbook_id,
            window_explicit=False,
            turn_class=TurnClass.NEW_INVESTIGATION,
            parent=None,
            operators=(),
            trace_extra={
                "clarification_binding": {
                    "option": binding.option,
                    "kind": binding.kind,
                    "resumed_investigation_id": state.lineage_parent,
                }
            },
        )

    # ------------------------------------------- validating what we offer

    async def _validated_options(
        self, session: Session, clarification: ClarificationRequest
    ) -> ClarificationRequest:
        """Drop every option this platform could not actually answer.

        Round-3 R3-17. The value-existence guard that produces this
        platform's best refusal — *"There is no payer named
        UnitedHealthcare in this data"*, all twelve real values enumerated,
        ``PREDICATE_VALUE_UNMATCHED`` — was never applied to the options the
        platform OFFERS. Two holes, both found live:

        * ``_option_resolves`` checks scope values only against a
          dimension's DECLARED ``value_domain`` and skips the open
          dimensions outright, so "Summit Peak is a facility — walk through
          the medical-necessity denial spike in cardiology at that facility"
          was offered over a warehouse holding six facilities, none of them
          Summit Peak: an option the engine will refuse the moment it is
          selected, $0.1428 to be asked and another turn to discover.
        * Nothing dry-ran an option against the planner, so an option naming
          a legal metric and an illegal cut for it survived to be tapped.

        Both are closed by running the option the way the turn that accepts
        it will run it: build its spec, plan it, validate it, and resolve
        its predicate values against this watermark. An option that raises
        anything is dropped — including :class:`PlanClarificationNeeded`,
        which is precisely the phantom-value refusal arriving one turn early
        and for free (the observed-value read is cached per watermark, so
        the check costs at most one warehouse round trip per dimension).

        Options with no binding are left alone: a platform-authored recovery
        chip ("Raise the per-turn cost ceiling") is not a query and has
        nothing to dry-run. When every *checkable* option fails, the
        question keeps its text and says it has no suggestions rather than
        rendering as a question above a blank row of buttons.
        """
        if not clarification.bindings:
            return clarification
        kept: list[str] = []
        dropped: list[str] = []
        for option in clarification.options:
            binding = clarification.binding_for(option)
            if binding is None or await self._option_answerable(session, binding):
                kept.append(option)
            else:
                dropped.append(option)
        if not dropped:
            return clarification
        surviving = tuple(kept)
        if surviving:
            return replace(
                clarification,
                options=surviving,
                bindings=_bindings_for(clarification, surviving),
                reason=(
                    f"{clarification.reason}; {len(dropped)} option(s) dropped: they name "
                    "content this pack, catalog or watermark does not hold"
                ),
            )
        return replace(
            clarification,
            question=(
                f"{clarification.question} (I had suggestions here and dropped all "
                f"{len(dropped)} of them: each named a metric, cut or value this data "
                "does not hold at this watermark, so tapping one would only have bought "
                "you the same refusal a turn later. Say what you want in your own words, "
                "or ask me what exists.)"
            ),
            options=(),
            bindings=(),
            reason=(
                f"{clarification.reason}; CLARIFICATION_OPTIONS_UNANSWERABLE: all "
                f"{len(dropped)} generated options failed value or plan validation"
            ),
        )

    async def _option_answerable(
        self, session: Session, binding: ClarificationBinding
    ) -> bool:
        """Would this option produce a plan the platform can execute?

        The check applies where an option could be WRONG. A
        ``predicate_value`` option is a value read out of the warehouse's
        own domain one moment earlier and a ``date_basis`` option is a
        basis the contract declares and this warehouse binds: re-checking
        the validator's own output against the validator would be a
        tautology, and a failing round trip would drop the twelve real
        payers the analyst needs to choose from.
        """
        if binding.kind in ("predicate_value", "date_basis"):
            return True
        if not binding.metric_ids:
            # A playbook-only option carries no measures to dry-run; the
            # pack either holds the playbook or the option is hollow.
            return binding.playbook_id is not None and (
                self._pack.playbook(binding.playbook_id) is not None
            )
        spec = self._spec_for_binding(session, binding)
        if spec is None:
            return False
        try:
            plan = self._planner.build(
                spec,
                playbook_id=binding.playbook_id if not spec.measures else None,
                window_explicit=False,
            )
            validated = self._validator.validate(plan, spec)
            await self._validator.resolve_predicate_values(
                validated, watermark=session.watermark
            )
        except (PlanClarificationNeeded, ReviError, ValueError, KeyError, AssertionError):
            return False
        return True

    def _spec_for_binding(
        self, session: Session, binding: ClarificationBinding
    ) -> AnalysisSpec | None:
        """The typed investigation an option stands for, or ``None``.

        Built through the same ``from_typed_spec`` disposal a portfolio
        card's drill handle goes through, so a dry run exercises the path
        the accepted option would actually take rather than an approximation
        of it.
        """
        if not binding.metric_ids:
            return None
        try:
            typed = TypedInvestigationSpec(
                metric_ids=list(binding.metric_ids),
                dimensions=list(binding.dimension_ids),
                filters=[
                    AddFilterModel(
                        op="add_filter",
                        dimension=dimension,
                        predicate_op="in" if len(values) > 1 else "eq",
                        values=list(values),
                    )
                    for dimension, values in binding.scope
                    if values
                ],
                window=_option_window(session),
                basis=binding.basis,
            )
            interpreted = self._interpreter.from_typed_spec(
                typed, session=session, turn_id="__option_check__"
            )
        except (ReviError, ValueError, AssertionError):
            return None
        return interpreted.spec

    async def _refuted_in_session(self, session: Session) -> frozenset[str]:
        """Dimension values this session has already proved do not exist.

        Read back off the recorded clarification reasons rather than held
        in memory: a turn is a stateless request and the session may resume
        in another process — the same reason ``_pending_clarification``
        reads the lineage.
        """
        lineage = await self._investigations.lineage(session.id)
        if lineage is None:
            return frozenset()
        reasons: list[str] = []
        for investigation in lineage.investigations:
            if investigation.status is not InvestigationStatus.CLARIFICATION_REQUIRED:
                continue
            for record in await self._traces.for_investigation(investigation.id):
                reason = record.payload.get("clarification_reason")
                if isinstance(reason, str) and reason:
                    reasons.append(reason)
        return refuted_values(reasons)

    async def _pending_clarification(
        self, session: Session
    ) -> PendingClarification | None:
        """The clarification this session is still waiting on, if any.

        Read back off the lineage rather than held in memory: a turn is a
        stateless request, and the session may be resumed in another
        process. The streak counts *consecutive* clarification turns at the
        tail, which is what §2.8 convergence is measured in.
        """
        lineage = await self._investigations.lineage(session.id)
        if lineage is None or not lineage.investigations:
            return None
        ordered = sorted(lineage.investigations, key=lambda inv: inv.created_at, reverse=True)
        streak: list[Investigation] = []
        for investigation in ordered:
            if investigation.status is not InvestigationStatus.CLARIFICATION_REQUIRED:
                break
            streak.append(investigation)
        if not streak:
            return None
        latest = streak[0]
        # The oldest turn in the run is the analyst's actual question; the
        # ones after it are their replies to us.
        oldest = streak[-1]
        question, options, bindings = await self._recorded_clarification(latest.id)
        if question is None:
            return None
        return PendingClarification(
            question=question,
            options=options,
            original_question=oldest.question,
            streak=len(streak),
            investigation_id=latest.id,
            bindings=bindings,
        )

    async def _recorded_clarification(
        self, investigation_id: str
    ) -> tuple[str | None, tuple[str, ...], tuple[ClarificationBinding, ...]]:
        """The clarification question, options and bindings a turn published."""
        for record in await self._traces.for_investigation(investigation_id):
            question = record.payload.get("clarification")
            if isinstance(question, str) and question:
                raw = record.payload.get("clarification_options") or ()
                options = tuple(str(option) for option in raw)
                return question, options, _bindings_from_trace(record.payload)
        return None, (), ()

    @staticmethod
    def _bounded_clarification(
        state: _TurnState, clarification: ClarificationRequest
    ) -> ClarificationRequest:
        """The same clarification, said once more and then differently.

        Interpretation clarifies when the question maps onto no governed
        metric — and there the §2.8 convergence rule must NOT force an
        answer: committing would mean inventing a metric, which is exactly
        the "confident no-issue answer over missing coverage" §2.8 forbids.
        What it can do is stop reissuing near-identical questions. Past the
        allowance the ask becomes a plain statement of the impasse, naming
        the question that started it, so the thread has an exit instead of
        a cycle.
        """
        pending = state.pending
        if pending is None or pending.streak < MAX_CONSECUTIVE_CLARIFICATIONS:
            return clarification
        original = pending.original_question or state.question
        return ClarificationRequest(
            question=(
                f"We're going in circles — I've asked {pending.streak} questions about this "
                f"and still can't map it onto anything I measure. Rather than ask again: "
                f"state the whole question in one sentence, naming the metric and the period "
                f"you want. The thread started with {original!r}."
            ),
            options=clarification.options,
            reason=(
                f"CLARIFICATION_NOT_CONVERGING: {pending.streak} consecutive clarifications; "
                f"original reason: {clarification.reason}"
            ),
        )

    @staticmethod
    def _commit_instead_of_clarifying(
        state: _TurnState, pending: PendingClarification | None
    ) -> str | None:
        """Should this turn stop asking and answer? (§2.8)

        A clarification is a dialogue move, not an error — but a dialogue
        that only ever asks is not a dialogue. After
        :data:`MAX_CONSECUTIVE_CLARIFICATIONS` in one thread the engine
        commits to its best reading and answers, stating the assumption
        prominently instead of asking a third time. Returns the assumption
        to publish, or ``None`` to clarify as usual.

        The rule is deliberately narrow. It fires on *ambiguity* loops —
        "which of these did you mean?" — and never converts a refusal into
        a guess: a question that maps onto no governed content still comes
        back as an honest non-answer, because there is nothing there to
        commit to.
        """
        if pending is None or pending.streak < MAX_CONSECUTIVE_CLARIFICATIONS:
            return None
        return (
            f"Assumed: this is a fresh question, asked as written. I had asked "
            f"{pending.streak} clarifying questions in a row without converging, so rather "
            f"than ask again I answered {state.question!r} on my best reading of it. If "
            "that is not what you meant, say what to change and I will re-run it."
        )

    # --------------------------------------------------- the per-turn budget

    @staticmethod
    def _budget_stop(state: _TurnState, stage_label: str) -> ClarificationRequest | None:
        """Stop the turn when its cost ceiling leaves nothing to spend.

        Called before every model call. The alternative — carrying on with
        a budget too small to complete — buys a provider refusal that looks
        like an outage, and the alternative to *that* is worse: answering
        with fewer model calls and not saying so. A turn that ran out of
        money says it ran out of money, and the ceiling is the analyst's
        own setting, so the recovery is in their hands.
        """
        remaining = state.budget_remaining
        if remaining is None or remaining >= _MIN_CALL_BUDGET_USD:
            return None
        ceiling = state.settings.max_turn_cost_usd
        return ClarificationRequest(
            question=(
                f"This turn reached its ${ceiling} cost ceiling while {stage_label}. "
                "Raise the per-turn ceiling and ask again, or ask something narrower."
            ),
            options=("Raise the per-turn cost ceiling", "Ask a narrower question"),
            reason=(
                f"TURN_BUDGET_EXHAUSTED: spent ${state.llm_spend} of a ${ceiling} "
                f"per-turn ceiling before {stage_label}"
            ),
        )

    # ------------------------------------------------------ watermark epochs

    async def _check_watermark(
        self, session: Session, state: _TurnState, request: SubmitTurnRequest
    ) -> Session:
        newest = await self._open_session.newest_watermark()
        if newest.id == session.watermark.id:
            return session
        if request.re_anchor:
            session = await self._open_session.re_anchor(session, newest, state.turn_id)
            state.epoch_transition = True
            return session
        state.watermark_stale = True
        await self._events.publish(
            TurnEvent(
                kind="warning",
                turn_id=state.turn_id,
                payload={
                    "code": "WATERMARK_STALE",
                    "pinned": session.watermark.id,
                    "newest": newest.id,
                },
            )
        )
        return session

    # ----------------------------------------------------- new investigation

    async def _new_investigation_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        exhausted = self._budget_stop(state, "working out what to measure")
        if exhausted is not None:
            return await self._clarification_outcome(session, state, classified, exhausted)

        await self._stage(state, "interpret")
        try:
            interpretation = await self._interpreter.interpret(
                state.question,
                session=session,
                turn_id=state.turn_id,
                policy=state.call_policy(),
                # Set when a clarification about the date basis has been
                # answered — or answered itself, see _recoverable_refusal.
                basis_override=state.basis_override,
            )
        except DateBasisInvalidError as refusal:
            # The basis is fixed at interpretation (it is what the context
            # header publishes), so this refusal never reached the planner's
            # recovery path and ended as a §12 banner. Same machinery, same
            # honesty rule: alternatives or nothing.
            recovered = await self._recoverable_refusal(session, state, classified, refusal, None)
            if recovered is not None:
                return recovered
            raise
        state.record_llm("interpret_question", interpretation.usage, interpretation.failure)
        state.template_hashes["interpret_question@v1"] = interpretation.template_hash
        state.time_stage("interpret")

        if interpretation.clarification is not None:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                self._bounded_clarification(state, interpretation.clarification),
                interpretation,
            )
        if interpretation.definitional is not None:
            return await self._definitional_outcome(
                session, state, classified, answer=interpretation.definitional
            )
        assert interpretation.investigation is not None
        interpreted = interpretation.investigation

        # carryover law 5: session pins persist until explicitly cleared
        pins = await self._inherited_pins(session)
        spec = _with_chosen_values(interpreted.spec, state.scope_override)
        if pins:
            spec = spec.with_context(replace(spec.context, pins=pins))

        return await self._run_analysis(
            session,
            state,
            classified,
            spec=spec,
            playbook_id=interpreted.playbook_id,
            window_explicit=interpreted.window_explicit,
            turn_class=TurnClass.NEW_INVESTIGATION,
            parent=None,
            operators=(),
            interpreted=interpreted,
        )

    # -------------------------------------------------- typed first turn

    async def _typed_investigation_turn(
        self, session: Session, state: _TurnState, typed: TypedInvestigationSpec
    ) -> TurnOutcome:
        """A NEW_INVESTIGATION stated in the typed vocabulary (§8.1).

        The twin of the interpreted first turn with the probabilistic
        stage removed: the caller supplies what the model would have
        proposed, ``from_typed_spec`` disposes it against the pack and
        catalog, and the *identical* planning → §6.6 validation →
        cache-first execution → calculation → findings pipeline runs. Zero
        model calls, and no parent required — which is what lets a
        portfolio card, or a chart click in a fresh session, become an
        investigation instead of a clarification.
        """
        await self._stage(state, "interpret")
        interpreted = self._interpreter.from_typed_spec(
            typed, session=session, turn_id=state.turn_id
        )
        state.time_stage("interpret")

        # carryover law 5: session pins persist until explicitly cleared
        spec = interpreted.spec
        pins = await self._inherited_pins(session)
        if pins:
            spec = spec.with_context(replace(spec.context, pins=pins))

        return await self._run_analysis(
            session,
            state,
            None,
            spec=spec,
            playbook_id=None,
            window_explicit=True,
            turn_class=TurnClass.NEW_INVESTIGATION,
            parent=None,
            operators=(),
            interpreted=interpreted,
            trace_extra={"typed_spec": typed.model_dump(mode="json")},
        )

    # ------------------------------------------------------------ refinement

    async def _refinement_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        *,
        dto_ops: tuple[AnyRefinementOperator, ...] | None,
    ) -> TurnOutcome:
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question=(
                        "There's no prior answer in this session to refine yet — what "
                        "would you like to investigate?"
                    ),
                    reason="refinement without a parent investigation",
                ),
            )
        parent_plan_context = await self._plan_context_of(parent.id)
        playbook_id = parent_plan_context.playbook_id
        window_explicit = parent_plan_context.window_explicit
        entries = await self._referents.list_for_session(session.id)
        resolutions: tuple[ReferentResolution, ...] = ()
        rationale = ""

        if dto_ops is None:
            # Handles the analyst typed are resolved against the registry
            # BEFORE any model call — they are identifiers this platform
            # minted, not language to be interpreted (§7.6).
            await self._stage(state, "resolve_referents")
            deterministic, unknown = resolve_referent_tokens(state.question, entries)
            if unknown:
                return await self._clarification_outcome(
                    session, state, classified, self._unknown_handle(unknown, entries)
                )
            if deterministic or not entries:
                # Nothing left for a model to resolve: every handle matched,
                # or nothing has been shown that anaphora could point at.
                resolutions = deterministic
                state.time_stage("resolve_referents")
            else:
                exhausted = self._budget_stop(state, "resolving what you referred to")
                if exhausted is not None:
                    return await self._clarification_outcome(
                        session, state, classified, exhausted
                    )
                resolved = await self._referent_resolver.resolve(
                    state.question, entries, policy=state.call_policy()
                )
                state.record_llm("resolve_referents", resolved.usage, resolved.failure)
                state.template_hashes["resolve_referents@v1"] = resolved.template_hash
                state.time_stage("resolve_referents")
                if resolved.clarification is not None:
                    return await self._clarification_outcome(
                        session, state, classified, resolved.clarification
                    )
                resolutions = resolved.resolutions

            exhausted = self._budget_stop(state, "compiling your follow-up")
            if exhausted is not None:
                return await self._clarification_outcome(session, state, classified, exhausted)

            await self._stage(state, "emit_refinements")
            emission = await self._refinement_emitter.emit(
                state.question,
                context_summary=self._context_lines(parent.spec),
                entries=entries,
                resolutions=resolutions,
                dimension_lines=self._dimension_lines(),
                metric_lines=self._metric_lines(),
                policy=state.call_policy(),
            )
            state.record_llm("emit_refinements", emission.usage, emission.failure)
            state.template_hashes["emit_refinements@v1"] = emission.template_hash
            state.time_stage("emit_refinements")
            if emission.clarification is not None or emission.operators is None:
                clarification = emission.clarification or ClarificationRequest(
                    question="What would you like to change?", reason="AMBIGUOUS_REFINEMENT"
                )
                return await self._clarification_outcome(session, state, classified, clarification)
            dto_ops = emission.operators
            rationale = emission.rationale

        registry_index = {entry.referent.value: entry.referent for entry in entries}
        domain_ops = to_domain_operators(dto_ops, registry_index)
        ops_json = tuple(op.model_dump(mode="json") for op in dto_ops)

        prelude_warnings: list[str] = []
        cohort = await self._pin_drill_cohort(session, parent.spec, domain_ops, prelude_warnings)

        def resolve_cohort(_: ReferentId) -> CohortRef | None:
            return cohort

        try:
            new_spec = apply_refinements(
                parent.spec,
                domain_ops,
                turn_id=state.turn_id,
                resolve_cohort=resolve_cohort if cohort is not None else None,
            )
        except ContextConflictError as conflict:
            # detected BEFORE execution (§7.7 law 4); a conversational
            # outcome, never a server error
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question=(
                        f"That contradicts the current context: {conflict.message}. "
                        "Widen the context first (remove the filter or reset), or "
                        "rephrase what you want."
                    ),
                    reason=f"CONTEXT_CONFLICT: {conflict.message}",
                ),
                extra={"refinement": {"operators": list(ops_json), "rationale": rationale}},
            )
        new_spec = self._rebase_context(new_spec, session)

        if domain_ops and all(isinstance(op, _KERNEL_ONLY) for op in domain_ops):
            kernel_outcome = await self._kernel_only_turn(
                session, state, classified, parent, new_spec, domain_ops, ops_json
            )
            if kernel_outcome is not None:
                return kernel_outcome

        refinement_extra: dict[str, Any] = {
            "operators": list(ops_json),
            "rationale": rationale,
            "resolutions": [
                {"mention": r.mention, "referent": r.referent.value, "confidence": r.confidence}
                for r in resolutions
            ],
        }
        if cohort is not None:
            refinement_extra["cohort"] = {"id": cohort.id, "size": cohort.size}
        return await self._run_analysis(
            session,
            state,
            classified,
            spec=new_spec,
            playbook_id=playbook_id,
            window_explicit=window_explicit,
            parent_evidence_depth=parent_plan_context.evidence_depth,
            turn_class=TurnClass.REFINEMENT,
            parent=parent,
            operators=domain_ops,
            refinement_extra=refinement_extra,
            prelude_warnings=tuple(prelude_warnings),
        )

    @staticmethod
    def _unknown_handle(
        unknown: tuple[str, ...], entries: tuple[RegisteredReferent, ...]
    ) -> ClarificationRequest:
        """A handle this session never published — say so, and list what it did.

        Deterministic resolution can fail honestly, and this is the shape:
        not ``REFERENT_NOT_FOUND`` raised past the analyst as an error, and
        not a model call hoping to find something close. The registry is
        the answer, so the registry is what gets shown.
        """
        named = ", ".join(unknown)
        available = [entry.referent.value for entry in entries]
        return ClarificationRequest(
            question=(
                f"I haven't shown {named} in this session. "
                + (
                    f"What I have shown: {', '.join(available[:12])}. Which did you mean?"
                    if available
                    else "Nothing has been shown yet — what would you like to investigate?"
                )
            ),
            options=tuple(drill_suggestion(value) for value in available[:4]),
            reason=f"REFERENT_NOT_FOUND: {list(unknown)} not in the live registry",
        )

    async def _pin_drill_cohort(
        self,
        session: Session,
        parent_spec: AnalysisSpec,
        domain_ops: tuple[Refinement, ...],
        warnings: list[str],
    ) -> CohortRef | None:
        targets = tuple(
            dict.fromkeys(op.target for op in domain_ops if isinstance(op, DrillInto))
        )
        if not targets:
            return None
        return await self._cohort_pinner.pin(
            session=session, parent_spec=parent_spec, targets=targets, warnings=warnings
        )

    # ------------------------------------------------ shared analysis runner

    async def _run_analysis(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        *,
        spec: AnalysisSpec,
        playbook_id: str | None,
        window_explicit: bool,
        turn_class: TurnClass,
        parent: Investigation | None,
        operators: tuple[Refinement, ...],
        interpreted: InterpretedInvestigation | None = None,
        refinement_extra: Mapping[str, Any] | None = None,
        trace_extra: Mapping[str, Any] | None = None,
        prelude_warnings: tuple[str, ...] = (),
        parent_evidence_depth: EvidenceDepth | None = None,
    ) -> TurnOutcome:
        effective_playbook = playbook_id if not spec.measures else None
        evidence_depth = state.settings.evidence_depth

        await self._stage(state, "plan")
        try:
            plan = self._planner.build(
                spec,
                playbook_id=effective_playbook,
                window_explicit=window_explicit,
                evidence_depth=evidence_depth,
            )
        except (DateBasisInvalidError, GrainIncompatibleError, UnsupportedConceptError) as refusal:
            recovered = await self._recoverable_refusal(
                session, state, classified, refusal, spec
            )
            if recovered is not None:
                return recovered
            raise
        diff: PlanDiff | None = None
        if parent is not None:
            parent_playbook = playbook_id if not parent.spec.measures else None
            parent_plan = self._planner.build(
                parent.spec,
                playbook_id=parent_playbook,
                window_explicit=window_explicit,
                # the depth the PARENT ran at, so the diff compares this
                # plan against the one that actually produced the parent
                evidence_depth=(
                    parent_evidence_depth if parent_evidence_depth is not None else evidence_depth
                ),
            )
            diff = self._differ.diff(parent_plan, plan)
        state.time_stage("plan")

        await self._stage(state, "validate")
        try:
            validated = self._validator.validate(plan, spec)
            # …then the values those filters name, against the values that
            # exist at this watermark. A separate (async) pass because it
            # reads the source; the refusal it can raise is a question for
            # the analyst, not an error code (§6.6 step 4b).
            validated = await self._validator.resolve_predicate_values(
                validated, watermark=session.watermark
            )
        except PlanClarificationNeeded as needed:
            return await self._clarification_outcome(
                session, state, classified, needed.clarification
            )
        except (DateBasisInvalidError, GrainIncompatibleError, UnsupportedConceptError) as refusal:
            recovered = await self._recoverable_refusal(
                session, state, classified, refusal, spec
            )
            if recovered is not None:
                return recovered
            raise
        state.time_stage("validate")

        # the longest stage of the pipeline; it published no progress frame
        # until now, so a streaming client watched the rail stall on
        # "validate" for the whole warehouse round trip
        await self._stage(state, "execute")
        executed = await self._executor.execute(
            validated.plan,
            watermark=session.watermark,
            pack_snapshot_id=self._pack.snapshot_id,
            turn_id=state.turn_id,
            grades=dict(validated.grades),
        )
        state.time_stage("execute")

        await self._stage(state, "calculate")
        calculation = self._calculator.calculate(validated.plan, executed)
        state.time_stage("calculate")

        await self._stage(state, "findings")
        playbook = (
            self._pack.playbook(effective_playbook) if effective_playbook is not None else None
        )
        findings_result = await self._evaluator.evaluate(
            plan=validated.plan,
            calculation=calculation,
            spec=spec,
            pack=self._pack,
            playbook=playbook,
            session_id=session.id,
            investigation_id=state.investigation_id,
            # The §15 threshold the frames were policed under: a bound is
            # only recognisable against the threshold that produced it, and
            # without it findings publish `(threshold - 1) / n` as a
            # measured value (round-3 R3-01).
            suppression_threshold=self._executor.suppression_threshold,
        )
        state.time_stage("findings")

        extra_warnings: list[str] = list(prelude_warnings)
        # A comparison against a different-length window is answerable but
        # not a delta anyone should act on; the warning rides with the
        # answer and the findings withhold impact (see comparison.py).
        families = probe_families_empty_warning(
            validated, executed, findings_result.findings
        )
        if families is not None:
            extra_warnings.append(families)
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "PROBE_FAMILIES_EMPTY", "detail": families},
                )
            )
        # What the §15 policy bounded rather than dropped, said once for the
        # turn: a ranking that mixes measured and bounded figures without
        # saying so is as misleading as one that censors the bounded rows.
        # Counted once, at frame level, and published as the integers the
        # narrator must cite rather than derive (round-3 R3-18): three
        # surfaces published three different numbers for one control, and
        # in one case the narrator invented the population outright.
        census = _turn_census(calculation, self._executor.suppression_threshold)
        bounds = bounded_cells_warning(
            tuple(cell for item in executed for cell in item.bounded_cells),
            self._executor.suppression_threshold,
            census=census,
        )
        if bounds is not None:
            extra_warnings.append(bounds)
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "SUPPRESSION_BOUNDED", "detail": bounds},
                )
            )
        mismatch = window_mismatch_warning(spec)
        if mismatch is not None:
            extra_warnings.append(mismatch)
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "COMPARISON_WINDOW_MISMATCH", "detail": mismatch},
                )
            )
        reconciliation = (
            await self._reconcile_with_parent(
                parent, validated.plan, calculation, operators, extra_warnings, state
            )
            if parent is not None
            else _not_applicable("this is a first turn; there is no parent answer to reconcile to")
        )

        # Assumptions lead: when the engine committed to a reading rather
        # than asking again (§2.8), or read an utterance as an answer to its
        # own question, the analyst must meet that decision BEFORE the
        # numbers it produced — not below a stack of basis and suppression
        # notes they will not scroll to. The same rule covers what
        # interpretation decided on their behalf (a period nobody named, a
        # filter dropped as redundant) and what selection could not find
        # (nothing moved the way the question asked about): those are
        # statements about whether the answer answers the question, and
        # they cannot sit under the answer.
        warnings = (
            *state.assumptions,
            *(interpreted.notes if interpreted is not None else ()),
            *findings_result.warnings,
            *validated.warnings,
            *validated.plan.notes,
            *extra_warnings,
        )
        # An empty population is the stronger fact: when nothing was
        # retrieved at all, "no finding survived" is a consequence, not the
        # explanation.
        emptiness = calculation.emptiness or findings_result.emptiness
        benchmarks = self._benchmarks_for(findings_result)
        header = build_context_header(
            spec,
            session,
            pack=self._pack,
            corrections=validated.corrections_map,
            # What the PLAN read, not only what the utterance named: a
            # playbook turn carries no spec.measures and its snapshot
            # contracts were rendering under a start..end window (R3-15).
            measure_ids=plan_measure_ids(validated.plan),
        )
        frame_refs = await self._persist_frames(state, calculation)
        # A refinement's parent is the answer it edits; a clarification
        # answer's parent is the question that asked it. Either is a real
        # edge and neither used to be recorded for the second kind.
        lineage_parent_id = parent.id if parent is not None else state.lineage_parent
        investigation = Investigation(
            id=state.investigation_id,
            session_id=session.id,
            parent_id=lineage_parent_id,
            turn_id=state.turn_id,
            turn_class=turn_class,
            question=state.question,
            spec=spec,
            plan_hash=validated.plan.plan_hash,
            status=InvestigationStatus.COMPLETE,
            findings=findings_result.findings,
            created_at=datetime.now(UTC),
            frame_refs=frame_refs,
            warnings=warnings,
        )
        edge = (
            RefinementEdge(
                parent_id=lineage_parent_id,
                child_id=investigation.id,
                turn_id=state.turn_id,
                # No operators on a clarification edge: nothing was refined,
                # a question was answered.
                operators=operators if parent is not None else (),
            )
            if lineage_parent_id is not None
            else None
        )
        await self._investigations.save(investigation, edge)

        chart_sorts = resolved_orderings(validated.plan)
        extra: dict[str, Any] = {
            "plan_context": {
                "playbook_id": playbook_id,
                "window_explicit": window_explicit,
                "evidence_depth": evidence_depth.value,
                # The orderings this plan resolved, recorded so a RESTORED
                # turn's charts sort the way the live ones did (R3-13).
                # Rebuilt charts read the persisted frames, and the frame
                # carries the rows but not the decision that ordered them.
                "chart_sorts": [
                    {"frame_id": frame_id, "by": by, "descending": descending}
                    for frame_id, by, descending in chart_sorts
                ],
            },
            # Recorded for every analytical turn, not only refinements.
            # It used to live under ``refinement`` alone, which meant a
            # first turn's verdict — "not_applicable, there is no parent"
            # — was computed, returned on the wire, and then lost: a
            # rehydrated turn had no way to say whether anything had been
            # checked. The ``refinement`` copy stays for readers that
            # already know where to look.
            "reconciliation": reconciliation,
            # Structured, never prose: which kind of nothing this turn
            # produced, and what was filtering when it did.
            "emptiness": emptiness.as_payload() if emptiness is not None else None,
        }
        if trace_extra is not None:
            extra.update(dict(trace_extra))
        if refinement_extra is not None:
            refinement_payload = dict(refinement_extra)
            if diff is not None:
                refinement_payload["diff"] = {
                    "added": [node.hash for node in diff.added],
                    "removed": [node.hash for node in diff.removed],
                    "unchanged": [node.hash for node in diff.unchanged],
                }
            refinement_payload["reconciliation"] = reconciliation
            extra["refinement"] = refinement_payload
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                interpreted=interpreted,
                validated=validated,
                executed=executed,
                calculation=calculation,
                findings=findings_result,
                warnings=warnings,
                extra=extra,
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=findings_result.findings,
            header=header,
            frames=calculation.frames,
            warnings=warnings,
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            referents=findings_result.referents,
            watermark_stale=state.watermark_stale,
            reconciliation=reconciliation,
            diff=diff,
            benchmarks=benchmarks,
            settings=state.settings,
            emptiness=emptiness,
            chart_sorts=chart_sorts,
        )

    async def _recoverable_refusal(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        refusal: ReviError,
        spec: AnalysisSpec | None = None,
    ) -> TurnOutcome | None:
        """A §6.6 refusal the pack can offer a way out of (design §2.8).

        ``GRAIN_INCOMPATIBLE`` and ``UNSUPPORTED_CONCEPT`` were terminal:
        an error banner naming a code, and no next move — while the pack
        often held a metric that answers the same question at a cut it does
        declare. The validator derives those alternatives from content
        (never invents them); when it can, the turn ends as a clarification
        with them as options. When it cannot, ``None`` comes back and the
        refusal stands: a clarification with no way forward is worse than
        an error that says what happened.
        """
        clarification = self._validator.clarification_for(refusal, spec)
        if clarification is None:
            return None
        # A "question" with one answer is not a question (round-3 R3-07).
        # ``timely_filing_at_risk_dollars`` cannot be read on the submission
        # basis and this warehouse binds exactly one alternative, so the
        # platform asked "which should I use?" over a list of one — and the
        # analyst's runway question was then lost answering it. Where the
        # lone option is a binding this platform derived itself, it is
        # applied and DISCLOSED, which is the move this product already
        # makes well everywhere else.
        applied = self._lone_binding(clarification)
        if applied is not None and not state.applied_bindings:
            state.applied_bindings.append(applied.option)
            state.assumptions.append(
                f"clarification_answer_applied: {applied.option} — this was the only "
                f"answer available to '{clarification.question}', so it was applied rather "
                "than asked about, and your question was run with it. Say so if you wanted "
                "something else and I will re-run it."
            )
            return await self._apply_binding(session, state, classified, applied)
        return await self._clarification_outcome(session, state, classified, clarification)

    @staticmethod
    def _lone_binding(
        clarification: ClarificationRequest,
    ) -> ClarificationBinding | None:
        """The single unambiguous choice a clarification leaves, if any.

        Deliberately narrow: exactly one option, exactly one binding, and a
        binding this platform derived from governed content rather than one
        a model proposed. Anything else is a real choice and is asked.
        """
        if len(clarification.options) != 1 or len(clarification.bindings) != 1:
            return None
        binding = clarification.bindings[0]
        return binding if binding.deterministic else None

    def _benchmarks_for(self, findings: FindingsResult) -> tuple[BenchmarkSpec, ...]:
        """Benchmark ranges for every metric the turn's findings cite, in
        finding order, deduplicated by benchmark id."""
        seen: dict[str, BenchmarkSpec] = {}
        for finding in findings.findings:
            for ref in finding.metric_refs:
                for benchmark in self._pack.benchmarks_for_metric(ref.id):
                    seen.setdefault(benchmark.id, benchmark)
        return tuple(seen.values())

    # ------------------------------------------------- reconciliation (§7.8)

    async def _reconcile_with_parent(
        self,
        parent: Investigation,
        plan: InvestigationPlan,
        calculation: CalculationResult,
        operators: tuple[Refinement, ...],
        warnings: list[str],
        state: _TurnState,
    ) -> str:
        """On splits (SetDimensions) and drills (DrillInto), check that the
        child's cells sum to the parent totals the analyst was shown.

        Every exit from this method now says *something*. It used to return
        ``None`` from four different paths, and the caller returned ``None``
        for a fifth — with the wire type ``string | null`` and no third
        state, "we checked and it agreed" and "we never checked" were the
        same value. Running the reference conversation produced ``None`` on
        the turn that actually drilled three payers and pivoted the measure,
        and ``"status=passed"`` on the turn that was a no-op; a reader had
        no way to tell those apart, and the one that looked reassuring was
        the one that had done nothing.

        The grammar is the existing one: ``status=<verdict>`` with
        semicolon-separated detail, so ``not_applicable`` carries a
        machine-readable ``reason=`` naming which path was taken.
        """
        if not any(isinstance(op, (SetDimensions, DrillInto)) for op in operators):
            return _not_applicable("this turn neither split nor drilled the parent's population")
        # A drill of a named parent finding reconciles against THAT
        # finding's published figure, whether or not this turn produced a
        # compared money frame and whether or not the parent kept an
        # undimensioned total (round-2 FN-4). Tried first because it is the
        # comparison the reader actually made — two figures on two
        # consecutive screens — and the sum-of-cells check below is the
        # comparison the lineage makes.
        containment = containment_reconciliation(parent, calculation, operators)
        if containment is not None:
            summary, passed = containment
            if not passed:
                await self._fail_reconciliation(summary, warnings, state)
            return summary
        shape = find_primary_compare(plan, calculation)
        if shape is None:
            return _not_applicable(
                "this turn produced no compared money frame to reconcile against the parent"
            )
        measure = shape.money_measure
        parent_totals: EvidenceFrame | None = None
        for key in parent.frame_refs:
            frame = await self._frames.get(key)
            if frame is None or measure not in frame.schema.names:
                continue
            if any(isinstance(col.ref, DimensionRef) for col in frame.schema.columns):
                continue
            parent_totals = frame
            if f"{measure}__prior" in frame.schema.names:
                break  # prefer the compare totals (they carry the prior side)
        if parent_totals is None:
            return _not_applicable(
                f"the parent investigation holds no undimensioned {measure!r} total to "
                "reconcile against"
            )
        if parent_totals.watermark != shape.frame.watermark:
            return _not_applicable(
                "the parent's totals were read at a different watermark "
                f"({parent_totals.watermark.id}) than this turn ({shape.frame.watermark.id})"
            )
        measures: tuple[str, ...] = (measure,)
        if (
            f"{measure}__prior" in parent_totals.schema.names
            and f"{measure}__prior" in shape.frame.schema.names
        ):
            measures = (measure, f"{measure}__prior")
        verdict = self._transforms.reconcile(parent_totals, shape.frame, measures=measures)
        if not verdict.passed:
            await self._fail_reconciliation(verdict.summary, warnings, state)
        return verdict.summary

    async def _fail_reconciliation(
        self, summary: str, warnings: list[str], state: _TurnState
    ) -> None:
        """One coded warning + one event for every failed reconciliation.

        Shared so the containment check and the sum-of-cells check cannot
        report the same class of disagreement two different ways.
        """
        warnings.append(f"RECONCILIATION_FAILED: {summary}")
        await self._events.publish(
            TurnEvent(
                kind="warning",
                turn_id=state.turn_id,
                payload={"code": "RECONCILIATION_FAILED", "detail": summary},
            )
        )

    # ------------------------------------------------- zero-probe turn paths

    async def _presentation_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question="There's nothing to re-present yet — what should I investigate?",
                    reason="presentation turn without a prior answer",
                ),
            )
        frames = await self._load_frames(parent)
        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=parent.spec,
            parent_id=parent.id,
            findings=parent.findings,
            frame_refs=parent.frame_refs,
        )
        edge = RefinementEdge(
            parent_id=parent.id, child_id=investigation.id, turn_id=state.turn_id, operators=()
        )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(session, state, classified, extra={"presentation_of": parent.id})
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=parent.findings,
            header=build_context_header(parent.spec, session, pack=self._pack),
            frames=frames,
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            settings=state.settings,
        )

    async def _meta_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        token_match = _REFERENT_TOKEN.search(state.question)
        if token_match is None:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question="Which finding do you mean? Name it by its handle (F1, F2, ...).",
                    reason="meta turn names no referent",
                ),
            )
        token = token_match.group(1).upper()
        entries = await self._referents.list_for_session(session.id)
        entry = next((e for e in entries if e.referent.value == token), None)
        if entry is None:
            # The same deterministic honesty the refinement path gives: a
            # handle nobody published is a question, not a 4xx.
            return await self._clarification_outcome(
                session, state, classified, self._unknown_handle((token,), entries)
            )
        cited = await self._investigations.get(entry.investigation_id)
        cited_traces = await self._traces.for_investigation(entry.investigation_id)
        payload: Mapping[str, Any] = cited_traces[0].payload if cited_traces else {}
        refinement_payload = payload.get("refinement") or {}
        meta = MetaAnswer(
            referent=token,
            label=entry.label,
            investigation_id=entry.investigation_id,
            probes=tuple(payload.get("probes", ())),
            operators=tuple(payload.get("operators", ())),
            grades=dict(payload.get("grades", {})),
            reconciliation=(
                payload.get("reconciliation") or refinement_payload.get("reconciliation")
            ),
            finding_values=tuple(entry.finding.values) if entry.finding is not None else (),
            warnings=tuple(payload.get("warnings", ())),
        )
        parent = await self._latest_investigation(session, analytical=False)
        investigation = self._minimal_investigation(
            session, state, InvestigationStatus.COMPLETE, classified
        )
        if cited is not None:
            investigation = replace(investigation, spec=cited.spec)
        edge = None
        if parent is not None:
            investigation = replace(investigation, parent_id=parent.id)
            edge = RefinementEdge(
                parent_id=parent.id,
                child_id=investigation.id,
                turn_id=state.turn_id,
                operators=(),
            )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                extra={"meta": {"referent": token, "cites": entry.investigation_id}},
            )
        )
        await self._turn_complete(state, investigation)
        header = (
            build_context_header(cited.spec, session, pack=self._pack)
            if cited is not None
            else None
        )
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=header,
            frames=(),
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            meta=meta,
            settings=state.settings,
        )

    async def _context_control_turn(
        self, session: Session, state: _TurnState, classified: ClassificationOutcome
    ) -> TurnOutcome:
        parent = await self._latest_investigation(session, analytical=False)
        base_spec = parent.spec if parent is not None else self._fallback_spec(session)
        entries = await self._referents.list_for_session(session.id)

        exhausted = self._budget_stop(state, "applying that context change")
        if exhausted is not None:
            return await self._clarification_outcome(session, state, classified, exhausted)

        await self._stage(state, "emit_refinements")
        emission = await self._refinement_emitter.emit(
            state.question,
            context_summary=self._context_lines(base_spec),
            entries=entries,
            resolutions=(),
            dimension_lines=self._dimension_lines(),
            metric_lines=self._metric_lines(),
            policy=state.call_policy(),
        )
        state.record_llm("emit_refinements", emission.usage, emission.failure)
        state.template_hashes["emit_refinements@v1"] = emission.template_hash
        state.time_stage("emit_refinements")
        if emission.clarification is not None or emission.operators is None:
            clarification = emission.clarification or ClarificationRequest(
                question="What should I change about the session context?",
                reason="AMBIGUOUS_REFINEMENT",
            )
            return await self._clarification_outcome(session, state, classified, clarification)

        registry_index = {entry.referent.value: entry.referent for entry in entries}
        domain_ops = to_domain_operators(emission.operators, registry_index)
        ops_json = [op.model_dump(mode="json") for op in emission.operators]

        # On a context-control turn, AddFilter means a sticky session pin
        # (carryover law 5); everything else applies as a normal edit.
        pin_ops = tuple(op for op in domain_ops if isinstance(op, AddFilter))
        other_ops = tuple(op for op in domain_ops if not isinstance(op, AddFilter))
        try:
            spec = apply_refinements(base_spec, other_ops, turn_id=state.turn_id)
            new_pins: list[ContextPin] = []
            for op in pin_ops:
                conflict = detect_conflict(spec, op.predicate)
                if conflict is not None:
                    raise ContextConflictError(
                        f"pin contradicts active context: {conflict}",
                        details={"conflict": conflict},
                    )
                new_pins.append(
                    ContextPin(
                        predicate=replace(op.predicate, origin_turn=state.turn_id),
                        declared_at_turn=state.turn_id,
                    )
                )
        except ContextConflictError as conflict:
            return await self._clarification_outcome(
                session,
                state,
                classified,
                ClarificationRequest(
                    question=f"That contradicts the current context: {conflict.message}.",
                    reason=f"CONTEXT_CONFLICT: {conflict.message}",
                ),
            )
        if new_pins:
            spec = spec.with_context(replace(spec.context, pins=(*spec.context.pins, *new_pins)))

        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=spec,
        )
        edge = None
        if parent is not None:
            investigation = replace(investigation, parent_id=parent.id)
            edge = RefinementEdge(
                parent_id=parent.id,
                child_id=investigation.id,
                turn_id=state.turn_id,
                operators=domain_ops,
            )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                extra={
                    "context_control": {
                        "operators": ops_json,
                        "pins": [_predicate_label(pin.predicate) for pin in new_pins],
                    }
                },
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=build_context_header(spec, session, pack=self._pack),
            frames=(),
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            settings=state.settings,
        )

    async def _kernel_only_turn(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        parent: Investigation,
        new_spec: AnalysisSpec,
        domain_ops: tuple[Refinement, ...],
        ops_json: tuple[Mapping[str, Any], ...],
    ) -> TurnOutcome | None:
        """RankBy/Expand within cached, untruncated frames: zero probes
        (§7.9). Returns None to fall back to the full path when the cached
        frames cannot honestly answer (missing column, truncated frame)."""
        frames = await self._load_frames(parent)
        if not frames:
            return None
        working: dict[str, EvidenceFrame] = dict(frames)
        order: list[str] = [fid for fid, _ in frames]
        for op in domain_ops:
            if isinstance(op, RankBy):
                rank_column = f"{op.by.id}__rank"
                target_id = next(
                    (
                        fid
                        for fid in reversed(order)
                        if op.by.id in working[fid].schema.names
                        and rank_column not in working[fid].schema.names
                    ),
                    None,
                )
                if target_id is None:
                    return None
                ranked_id = f"{target_id}__{op.by.id}__rank"
                working[ranked_id] = self._transforms.rank(
                    working[target_id], by=op.by.id, descending=op.descending
                )
                order.append(ranked_id)
            else:
                assert isinstance(op, Expand)
                if any(frame.truncated for frame in working.values()):
                    return None  # expanding a truncated frame needs the source
        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=new_spec,
            parent_id=parent.id,
            findings=parent.findings,
            plan_hash=parent.plan_hash,
        )
        frame_pairs = tuple((fid, working[fid]) for fid in order)
        refs: list[str] = []
        for fid, frame in frame_pairs:
            key = f"{state.investigation_id}:{fid}"
            await self._frames.save(key, frame)
            refs.append(key)
        investigation = replace(investigation, frame_refs=tuple(refs))
        edge = RefinementEdge(
            parent_id=parent.id,
            child_id=investigation.id,
            turn_id=state.turn_id,
            operators=domain_ops,
        )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                extra={
                    "refinement": {"operators": list(ops_json), "kernel_only": True},
                    "plan_context": {"playbook_id": None, "window_explicit": True},
                },
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=parent.findings,
            header=build_context_header(new_spec, session, pack=self._pack),
            frames=frame_pairs,
            warnings=(),
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            settings=state.settings,
        )

    # ------------------------------------------------------- outcome shapes

    async def _clarification_outcome(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        clarification: ClarificationRequest,
        interpretation: InterpretationOutcome | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> TurnOutcome:
        del interpretation  # usage already tracked on state
        # Every option this platform offers goes through the same value
        # existence check that produced its refusals (round-2 FN-6)…
        clarification = drop_refuted_options(
            clarification, await self._refuted_in_session(session)
        )
        # …and then through the warehouse and the planner (round-3 R3-17).
        clarification = await self._validated_options(session, clarification)
        investigation = self._minimal_investigation(
            session, state, InvestigationStatus.CLARIFICATION_REQUIRED, classified
        )
        await self._investigations.save(
            investigation,
            RefinementEdge(
                parent_id=state.lineage_parent,
                child_id=investigation.id,
                turn_id=state.turn_id,
                operators=(),
            )
            if state.lineage_parent is not None
            else None,
        )
        await self._traces.save(
            self._trace_record(
                session, state, classified, clarification=clarification, extra=extra
            )
        )
        await self._events.publish(
            TurnEvent(
                kind="clarification",
                turn_id=state.turn_id,
                payload={"question": clarification.question, "reason": clarification.reason},
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=None,
            frames=(),
            warnings=(),
            clarification=clarification,
            definitional=None,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            settings=state.settings,
        )

    async def _definitional_outcome(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome,
        answer: DefinitionalAnswer | None = None,
    ) -> TurnOutcome:
        # ZERO probes by construction: the executor is never invoked on this
        # path (asserted by the zero-probe tests with a spy repository).
        if answer is None:
            answer = self._interpreter.definitional_answer(state.question)
        state.time_stage("definitional")
        if not answer.terms:
            clarification = ClarificationRequest(
                question=(
                    "I couldn't find a governed definition for that term — could you name "
                    "the code, concept, or metric another way?"
                ),
                reason="no pack content matched the definitional lookup",
            )
            return await self._clarification_outcome(session, state, classified, clarification)
        investigation = self._minimal_investigation(
            session, state, InvestigationStatus.COMPLETE, classified
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            self._trace_record(session, state, classified, definitional=answer)
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=(),
            header=None,
            frames=(),
            warnings=(),
            clarification=None,
            definitional=answer,
            trace_id=state.trace_id,
            watermark_stale=state.watermark_stale,
            settings=state.settings,
        )

    # -------------------------------------------------------------- helpers

    async def _latest_investigation(
        self, session: Session, *, analytical: bool
    ) -> Investigation | None:
        lineage = await self._investigations.lineage(session.id)
        if lineage is None:
            return None
        candidates = [
            inv
            for inv in lineage.investigations
            if inv.status is InvestigationStatus.COMPLETE
            and (not analytical or inv.plan_hash is not None)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda inv: inv.created_at)

    async def _plan_context_of(self, investigation_id: str) -> _PlanContext:
        """How the parent turn was planned, read back from its trace.

        The evidence depth belongs here for the same reason the playbook id
        does: the parent's plan is *rebuilt* to diff against, and rebuilding
        it under this turn's depth would produce a plan the parent never
        ran — every rescaled probe would read as unchanged.
        """
        traces = await self._traces.for_investigation(investigation_id)
        for record in traces:
            plan_context = record.payload.get("plan_context")
            if isinstance(plan_context, Mapping):
                playbook_id = plan_context.get("playbook_id")
                raw_depth = plan_context.get("evidence_depth")
                return _PlanContext(
                    playbook_id=playbook_id if isinstance(playbook_id, str) else None,
                    window_explicit=bool(plan_context.get("window_explicit", True)),
                    evidence_depth=(
                        EvidenceDepth(raw_depth)
                        if isinstance(raw_depth, str) and raw_depth in _EVIDENCE_DEPTHS
                        else EvidenceDepth.STANDARD
                    ),
                )
        return _PlanContext()

    async def _inherited_pins(self, session: Session) -> tuple[ContextPin, ...]:
        latest = await self._latest_investigation(session, analytical=False)
        return latest.spec.context.pins if latest is not None else ()

    async def _load_frames(
        self, investigation: Investigation
    ) -> tuple[tuple[str, EvidenceFrame], ...]:
        out: list[tuple[str, EvidenceFrame]] = []
        for key in investigation.frame_refs:
            frame = await self._frames.get(key)
            if frame is not None:
                _, _, frame_id = key.partition(":")
                out.append((frame_id or key, frame))
        return tuple(out)

    def _rebase_context(self, spec: AnalysisSpec, session: Session) -> AnalysisSpec:
        """Align the context to the session watermark after an epoch change:
        relative windows re-resolve against the new anchor; stored concrete
        dates otherwise stand (§6.1)."""
        context = spec.context
        if context.watermark.id == session.watermark.id:
            return spec
        window = context.window
        if window.requested is not None:
            window = resolve_window(
                window.requested,
                window_anchor(session.watermark, window.requested.mode),
                basis=window.basis,
                calendar=window.calendar,
            )
        new_context = replace(context, watermark=session.watermark, window=window)
        if (
            context.comparison is not None
            and context.comparison.kind is not ComparisonKind.CUSTOM
            and window != context.window
        ):
            new_context = replace(
                new_context, comparison=derive_comparison(window, context.comparison.kind)
            )
        return spec.with_context(new_context)

    def _context_lines(self, spec: AnalysisSpec) -> str:
        context = spec.context
        window = context.window
        lines = [
            f"- window: {window.range.start.isoformat()}..{window.range.end.isoformat()} "
            f"on the {window.basis.id} basis"
        ]
        if context.comparison is not None:
            comparison = context.comparison
            lines.append(
                f"- comparison: {comparison.kind.value} "
                f"({comparison.window.range.start.isoformat()}.."
                f"{comparison.window.range.end.isoformat()})"
            )
        filters = [_predicate_label(p) for p in iter_predicates(context.effective_scope())]
        lines.append("- filters: " + ("; ".join(filters) if filters else "(none)"))
        if context.cohort is not None:
            lines.append(f"- cohort: {context.cohort.id} ({context.cohort.size} claims)")
        lines.append(
            "- dimensions: "
            + (", ".join(d.id for d in spec.dimensions) if spec.dimensions else "(none)")
        )
        lines.append(
            "- measures: "
            + (", ".join(m.id for m in spec.measures) if spec.measures else "(playbook-driven)")
        )
        return "\n".join(lines)

    def _dimension_lines(self) -> str:
        seen: dict[str, None] = {}
        for metric_id, _ in self._pack.metric_summaries():
            contract = self._pack.metric(metric_id)
            if contract is None:
                continue
            for dim in contract.scope_dimensions:
                seen.setdefault(dim.id)
        return "\n".join(f"- {dim_id}" for dim_id in seen) or "- (none)"

    def _metric_lines(self) -> str:
        return "\n".join(f"- {mid}" for mid, _ in self._pack.metric_summaries()) or "- (none)"

    def _fallback_spec(self, session: Session) -> AnalysisSpec:
        anchor = window_anchor(session.watermark, _FALLBACK_WINDOW.mode)
        window = resolve_window(_FALLBACK_WINDOW, anchor, basis=SERVICE)
        return AnalysisSpec(
            context=InvestigationContext(
                window=window,
                comparison=None,
                scope=EMPTY_SCOPE,
                cohort=None,
                grain=Grain(EntityGrain.CLAIM),
                watermark=session.watermark,
                pack_version=session.pack_version,
            ),
            measures=(),
        )

    def _minimal_investigation(
        self,
        session: Session,
        state: _TurnState,
        status: InvestigationStatus,
        classified: ClassificationOutcome | None,
    ) -> Investigation:
        """A persisted node for turns that never built a full spec."""
        turn_class = (
            classified.classification.turn_class
            if classified is not None and classified.classification is not None
            else TurnClass.REFINEMENT
        )
        return Investigation(
            id=state.investigation_id,
            session_id=session.id,
            # A follow-up clarification in a thread hangs off the one before
            # it, so a dialogue reads as a dialogue rather than as a row of
            # unrelated roots (round-3 R3-07).
            parent_id=state.lineage_parent,
            turn_id=state.turn_id,
            turn_class=turn_class,
            question=state.question,
            spec=self._fallback_spec(session),
            plan_hash=None,
            status=status,
            findings=(),
            created_at=datetime.now(UTC),
        )

    # ---------------------------------------------------------- persistence

    async def _persist_frames(
        self, state: _TurnState, calculation: CalculationResult
    ) -> tuple[str, ...]:
        refs: list[str] = []
        for frame_id, frame in calculation.frames:
            key = f"{state.investigation_id}:{frame_id}"
            await self._frames.save(key, frame)
            refs.append(key)
        return tuple(refs)

    def _trace_record(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        *,
        interpreted: InterpretedInvestigation | None = None,
        validated: ValidatedPlan | None = None,
        executed: tuple[ExecutedProbe, ...] = (),
        calculation: CalculationResult | None = None,
        findings: FindingsResult | None = None,
        warnings: tuple[str, ...] = (),
        clarification: ClarificationRequest | None = None,
        definitional: DefinitionalAnswer | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> TraceRecord:
        """Assemble the §14 observability payload (JSON-serializable)."""
        classification_payload: dict[str, Any] | None = None
        if classified is not None and classified.classification is not None:
            classification_payload = {
                "turn_class": classified.classification.turn_class.value,
                "confidence": classified.classification.confidence,
            }
        interpreted_payload: dict[str, Any] | None = None
        if interpreted is not None:
            interpreted_payload = {
                "intent_summary": interpreted.intent_summary,
                "metric_ids": list(interpreted.metric_ids),
                "dimension_ids": list(interpreted.dimension_ids),
                "concept_ids": list(interpreted.concept_ids),
                "playbook_id": interpreted.playbook_id,
                "window": {
                    "start": interpreted.spec.context.window.range.start.isoformat(),
                    "end": interpreted.spec.context.window.range.end.isoformat(),
                    "basis": interpreted.spec.context.window.basis.id,
                },
            }
        # Executed probes carry what the plan alone cannot: how many rows
        # came back, whether the frame was truncated or suppressed, what
        # grade it earned, and how long it took. A debug view that reported
        # only the plan would answer "what did you intend to read?" while
        # the question is always "what did you actually read?".
        by_node = {item.node_id: item for item in executed}
        probes_payload = []
        if validated is not None:
            grades = dict(validated.grades)
            for node in validated.plan.nodes:
                item = by_node.get(node.id)
                probe = node.probe
                grade = grades.get(node.id)
                probes_payload.append(
                    {
                        "id": node.id,
                        "hash": node.hash,
                        "purpose": node.purpose,
                        "kind": _probe_kind(probe),
                        "metrics": _probe_metrics(
                            probe, item.frame if item is not None else None
                        ),
                        "cache_hit": item.cache_hit if item is not None else False,
                        "rows": len(item.frame.rows) if item is not None else None,
                        "limit": probe.limit if isinstance(probe, AggregationProbe) else None,
                        "truncated": item.frame.truncated if item is not None else False,
                        "suppressed_cells": (
                            item.frame.suppressed_cells if item is not None else 0
                        ),
                        "grade": grade.value if grade is not None else None,
                        "duration_ms": item.duration_ms if item is not None else 0,
                    }
                )
        payload: dict[str, Any] = {
            "tenant": session.tenant,
            "question": state.question,
            # The controls this turn ran under, recorded with the turn: a
            # trace that cannot say which model tier or evidence depth
            # produced it cannot explain the answer it belongs to.
            "settings": {
                "model_tier": state.settings.model_tier,
                "max_turn_cost_usd": (
                    str(state.settings.max_turn_cost_usd)
                    if state.settings.max_turn_cost_usd is not None
                    else None
                ),
                "narrative_depth": state.settings.narrative_depth.value,
                "evidence_depth": state.settings.evidence_depth.value,
                "debug": state.settings.debug,
            },
            "pack": {
                "id": session.pack_version.pack_id,
                "version": session.pack_version.version,
                "snapshot_id": self._pack.snapshot_id,
            },
            "watermark": {
                "id": session.watermark.id,
                "loaded_at": session.watermark.loaded_at.isoformat(),
                "newest_data_date": session.watermark.newest_data_date.isoformat(),
            },
            "watermark_stale": state.watermark_stale,
            "epoch": {
                "index": session.epochs[-1].index,
                "watermark": session.watermark.id,
                "re_anchored": state.epoch_transition,
            },
            "classification": classification_payload,
            "interpretation": interpreted_payload,
            "plan_hash": validated.plan.plan_hash if validated is not None else None,
            "probes": probes_payload,
            "operators": [
                {
                    "operator": op.operator,
                    "version": op.version,
                    "inputs": list(op.inputs),
                    "output": op.output,
                }
                for op in (calculation.operations if calculation is not None else ())
            ],
            "grades": (
                {node_id: grade.value for node_id, grade in validated.grades}
                if validated is not None
                else {}
            ),
            "findings": [
                finding.referent.value for finding in (findings.findings if findings else ())
            ],
            # Referent → grade: the derivation the answer's caveats rest on,
            # recorded beside the per-node grades it was taken from.
            "finding_grades": {
                finding.referent.value: finding.grade.value
                for finding in (findings.findings if findings else ())
            },
            "warnings": list(warnings),
            "clarification": clarification.question if clarification is not None else None,
            # The options the analyst was offered. Recorded because the NEXT
            # turn reads them back: a reply that repeats one verbatim is the
            # strongest signal a turn is an answer, and without them the
            # classifier was told nothing had been asked at all.
            "clarification_options": (
                list(clarification.options) if clarification is not None else []
            ),
            # …and what each of them MEANT. Held server-side, keyed by the
            # investigation that asked, so a reply naming an option is
            # resolved by lookup rather than re-interpreted (round-3 R3-07).
            "clarification_bindings": (
                [
                    {
                        "option": b.option,
                        "kind": b.kind,
                        "metric_ids": list(b.metric_ids),
                        "dimension_ids": list(b.dimension_ids),
                        "playbook_id": b.playbook_id,
                        "scope": [
                            {"dimension": dimension, "values": list(values)}
                            for dimension, values in b.scope
                        ],
                        "basis": b.basis,
                    }
                    for b in clarification.bindings
                ]
                if clarification is not None
                else []
            ),
            # The reason carries the stage that stopped and, for a model
            # call, which kind of empty-handed it was — the half of a
            # clarification a debug reader is actually looking for.
            "clarification_reason": clarification.reason if clarification is not None else None,
            # The dialogue state this turn ran under, and what (if anything)
            # the engine decided on the analyst's behalf.
            "pending_clarification": (
                {
                    "question": state.pending.question,
                    "options": list(state.pending.options),
                    "original_question": state.pending.original_question,
                    "streak": state.pending.streak,
                }
                if state.pending is not None
                else None
            ),
            "assumptions": list(state.assumptions),
            "definitional": (
                {
                    "terms": [term.term for term in definitional.terms],
                    "pack_snapshot_id": definitional.pack_snapshot_id,
                }
                if definitional is not None
                else None
            ),
            "llm": [
                {
                    "template": template,
                    "model": usage.model,
                    "cost_usd": str(usage.cost_usd),
                    # Every prompt token, cached or not (see LlmUsage) —
                    # with the cached split beside it so a cost reader can
                    # tell a warm prompt from a cold one.
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "cache_creation_tokens": usage.cache_creation_tokens,
                    "schema_retries": usage.schema_retries,
                    # Transport attempts (1 = clean). Distinct from
                    # schema_retries: a retried connection and a retried
                    # schema are different diagnoses.
                    "attempts": usage.attempts,
                    "duration_ms": usage.duration_ms,
                    # Why the call produced nothing usable, when it did.
                    "failure": failure.value if failure is not None else None,
                }
                for template, usage, failure in state.llm_usages
            ],
            "template_hashes": dict(state.template_hashes),
            "timings_ms": dict(state.timings_ms),
        }
        if extra is not None:
            payload.update(dict(extra))
        return TraceRecord(
            trace_id=state.trace_id,
            session_id=session.id,
            investigation_id=state.investigation_id,
            turn_id=state.turn_id,
            created_at=datetime.now(UTC),
            payload=payload,
        )

    # --------------------------------------------------------------- events

    async def _stage(self, state: _TurnState, stage: str) -> None:
        await self._events.publish(
            TurnEvent(kind="stage", turn_id=state.turn_id, payload={"stage": stage})
        )

    async def _turn_complete(self, state: _TurnState, investigation: Investigation) -> None:
        await self._events.publish(
            TurnEvent(
                kind="turn_complete",
                turn_id=state.turn_id,
                payload={
                    "investigation_id": investigation.id,
                    "status": investigation.status.value,
                },
            )
        )
