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
from calendar import monthrange
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, assert_never

from revi_investigation.application.anchoring import window_anchor
from revi_investigation.application.calculation_glue import (
    CalculateMetricsService,
    CalculationResult,
    EmptinessFact,
)
from revi_investigation.application.capability_ports import BenchmarkSpec, PackPort, TransformPort
from revi_investigation.application.cohorts import PinCohortService
from revi_investigation.application.comparison import (
    comparison_maturity,
    declared_non_comparabilities,
    window_mismatch_warning,
)
from revi_investigation.application.execution import (
    BoundedCell,
    ExecutedProbe,
    ExecuteInvestigationService,
    SuppressionCensus,
    bound_index,
    bounded_cells_warning,
    suppression_census,
)
from revi_investigation.application.findings import (
    EvaluateFindingsService,
    FindingsResult,
    find_primary_compare,
    published_window_note,
    row_noun,
)
from revi_investigation.application.findings import (
    as_number as _as_number,
)
from revi_investigation.application.gestures import drill_suggestion, parse_gesture
from revi_investigation.application.interpretation import (
    OPTIONS_DROPPED_MARKER,
    PRESENTATION_CHANGE_REQUEST,
    ClassificationOutcome,
    ClassifyTurnService,
    DefinitionalAnswer,
    InterpretationOutcome,
    InterpretedInvestigation,
    InterpretQuestionService,
    PendingClarification,
    display_scope_limit,
    presentation_order_request,
    requested_finding_limit,
)
from revi_investigation.application.llm.schemas import AnyRefinementOperator
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    DiffPlanService,
    InvestigationPlan,
    PlanDiff,
    frame_window,
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
    referent_tokens,
    resolve_referent_tokens,
    to_domain_operators,
)
from revi_investigation.application.rendering import MONEY_UNIT as _MONEY_UNIT_NAME
from revi_investigation.application.rendering import RATIO_UNIT as _RATIO_UNIT_NAME
from revi_investigation.application.rendering import (
    format_value,
    metric_label,
    money,
    points,
    ratio_pct,
)
from revi_investigation.application.validation import (
    PlanClarificationNeeded,
    PlanValidationService,
    ValidatedPlan,
    map_predicates,
)
from revi_investigation.application.window_maturity import (
    WindowMaturity,
    WindowMaturityService,
    covered_months,
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
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    ExpandModel,
)
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
from revi_kernel.filters import (
    EMPTY_SCOPE,
    Predicate,
    PredicateOp,
    Scalar,
    and_merge,
    iter_predicates,
)
from revi_kernel.frame import EvidenceFrame, primary_measure
from revi_kernel.probes import AggregationProbe, EvidenceProbe, SnapshotProbe
from revi_kernel.refs import SERVICE, DimensionRef, EntityGrain, Grain, MetricRef, ReferentId
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
    #: ``(frame id, column, descending)`` the parent plan resolved. Carried
    #: so a turn that RE-SERVES the parent's plan re-serves its chart
    #: ordering too: round-4 R4-04 found ``chart sort → null`` on exactly
    #: the turns whose rows had not changed at all.
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
    findings: Sequence[Finding] = (),
) -> ContextHeaderPayload:
    """Delegate to the canonical contracts builder (§7.2 single source).

    ``findings`` is read only for :func:`published_window_note` — the
    sentence that says not every figure below was computed over the window
    this header names. It is composed from the findings rather than from
    the plan so that a live turn and a RESTORED one (which has no plan)
    produce the identical string from the identical facts.
    """
    context = spec.context
    return build_header_payload(
        window=context.window,
        comparison=context.comparison,
        predicates=tuple(iter_predicates(context.scope)),
        pinned_predicates=tuple(pin.predicate for pin in context.pins),
        cohort=context.cohort,
        watermark_id=session.watermark.id,
        as_of=snapshot_as_of(spec, session, pack, measure_ids),
        window_note=published_window_note(findings),
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

    Round-8 FIX-9(1) is the same defect one turn further on. "Build me a
    payer scorecard for Pinnacle, I have a JOC next week" ran six probe
    families, got **direct**-grade rows from every one of them, published
    ZERO findings, and this warning stayed silent — because it was silent
    on any turn with no findings at all, on the reasoning that the
    emptiness fact would speak for the turn. The emptiness fact spoke for
    exactly one family ("1 ranked row(s) on 'denied_dollars', and every one
    was zero or suppressed") and the other five were dropped without a
    word.

    So the rule is now the one the gate is named for: a family that was
    read publishes a finding or is NAMED. The single-family case keeps the
    old behaviour — there the emptiness fact really does speak for the
    whole turn, and two statements of the same nothing is one too many.
    """
    published = {ref.id for finding in findings for ref in finding.metric_refs}
    if findings and not published:
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
    if not findings and len(families) < 2:
        # One family, nothing published: ``empty_result`` says the same
        # thing with the reason attached, and it says it better.
        return None
    named = "; ".join(
        f"{', '.join(metrics)} ({', '.join(nodes)}, {rows_by_family[metrics]} row(s))"
        for metrics, nodes in families.items()
    )
    tail = (
        "Nothing on this turn speaks for any of them: the emptiness stated above is about "
        "the family that was ranked, not about these."
        if not findings
        else "The findings rank within the families that did publish — they are not a "
        "cross-family comparison, and a family's absence is not evidence that it is fine."
    )
    return (
        f"probe_families_empty: {len(families)} metric famil(ies) on this plan were read and "
        f"produced no published finding, so nothing above speaks for them: {named}. {tail}"
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
    level, _, measure = _frame_money_totals(frames)
    return level, measure


def _frame_money_totals(
    frames: tuple[tuple[str, EvidenceFrame], ...],
) -> tuple[int | None, int | None, str | None]:
    """``(level, movement, measure)`` for the last money-bearing frame.

    Both quantities, because a child answer can be either kind and the
    reconciliation has to know which it is holding (round-5 A-03). The
    level is the sum of the measure column — the figure the child card
    publishes. The movement is the sum of the compare operator's
    ``<measure>__delta`` column when this turn compared, and ``None`` when
    it did not: a turn with no prior side has no movement to tie out, and
    inventing one from a level is precisely the mismatch that made every
    drill of a comparison finding fail.
    """
    for _, frame in reversed(frames):
        for index, column in enumerate(frame.schema.columns):
            if column.unit != _MONEY_UNIT_NAME or column.name.endswith(
                (_PRIOR_COLUMN_SUFFIX, _DELTA_COLUMN_SUFFIX)
            ):
                continue
            measure = column.ref.id if isinstance(column.ref, MetricRef) else column.name
            return (
                _sum_money_column(frame, index),
                _sum_named_money_column(frame, f"{column.name}{_DELTA_COLUMN_SUFFIX}"),
                measure,
            )
    return None, None, None


def _sum_money_column(frame: EvidenceFrame, index: int) -> int:
    """Sum one money column in cents, skipping cells that are not amounts.

    Same guard as :func:`_sum_named_money_column` below, and for the same
    reason: a column carries the whole ``Scalar`` vocabulary, and a date or
    a label in a money-united column is not a smaller amount — it is not an
    amount. ``bool`` is refused ahead of ``int`` because Python counts
    ``True`` as 1.
    """
    total = 0
    for row in frame.rows:
        value = row[index]
        if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            continue
        total += int(value)
    return total


def _sum_named_money_column(frame: EvidenceFrame, name: str) -> int | None:
    """Sum one named integer column, or ``None`` when the frame has none.

    ``None`` and ``0`` are different answers here: a frame with no delta
    column produced no movement, while a frame whose deltas sum to zero
    produced a movement of nothing.
    """
    if name not in frame.schema.names:
        return None
    index = frame.schema.index_of(name)
    total = 0
    seen = False
    for row in frame.rows:
        value = row[index]
        if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            continue
        seen = True
        total += int(value)
    return total if seen else None


#: Column suffixes the compare operator adds beside a measure.
_PRIOR_COLUMN_SUFFIX = "__prior"
_DELTA_COLUMN_SUFFIX = "__delta"


#: Resolves a metric id to its contract unit, or ``None`` when this
#: reconciliation was handed no pack to ask. Supplied by the turn service,
#: which holds the pinned snapshot; defaulted so the pure function stays
#: callable from a test with nothing but two findings.
MetricUnitLookup = Callable[[str], str | None]


def _is_money_metric(metric_id: str, metric_unit: MetricUnitLookup | None) -> bool:
    """Does ``metric_id`` publish CENTS?

    Round-9 R9-03. ``int(Decimal("0.295082"))`` is ``0``, so a denial-rate
    finding read through the money path published "a figure of zero" rather
    than "no figure", and the drill launched off the product's own "drill
    into F1" chip reported ``RECONCILIATION_FAILED … parent F1=$0.00;
    child=$31,174.49 (+311744900.0%)`` in a red alert — two numbers that
    were never in the same unit. :func:`_parent_whole` had the mitigation
    (match on the child's own measure) and this readback did not.

    So the unit is ASKED FOR rather than inferred from the value's Python
    type. Where the pack can be reached that is the contract's own
    ``unit``; where it cannot, the structural rule stands in for it — money
    is cents-as-int everywhere in this system, so a Decimal carrying a
    fraction of a cent is, whatever else it is, not money.
    """
    if metric_unit is None:
        return True
    unit = metric_unit(metric_id)
    return unit is None or unit == _MONEY_UNIT_NAME


def _finding_money(
    finding: Finding, metric_unit: MetricUnitLookup | None = None
) -> tuple[int | None, int | None]:
    """``(level, movement)`` a parent finding published, in cents.

    ``impact_cents`` alone cannot answer this: on a movement finding it is
    the DELTA and on a concentration or ranking finding it is the LEVEL,
    and the reconciliation compared it against a child level either way.
    The named values disambiguate — a movement finding carries
    ``current_cents``/``delta_cents``, a compared scalar carries
    ``<metric>``/``<metric>__delta``, and a level-only finding carries
    neither delta.

    ``metric_unit`` makes the read UNIT-AWARE (R9-03): a metric whose
    contract does not publish cents has no money figure here, and reading
    one out of it by truncation is how a rate parent became ``$0.00``.
    """
    named = dict(finding.values)

    def cents(key: str) -> int | None:
        value = named.get(key)
        if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            return None
        if isinstance(value, Decimal) and value != value.to_integral_value():
            # Cents are whole. A fraction of one is another unit wearing
            # the same Python type, and int() would silently truncate it.
            return None
        return int(value)

    level = cents("current_cents")
    delta = cents("delta_cents")
    for ref in finding.metric_refs:
        if not _is_money_metric(ref.id, metric_unit):
            continue
        if level is None:
            level = cents(ref.id)
        if delta is None:
            delta = cents(f"{ref.id}{_DELTA_COLUMN_SUFFIX}")
    if level is None and delta is None:
        # A finding that published only an impact figure: a level unless a
        # delta value named it otherwise, which is the pre-comparison case
        # this check was originally written for.
        return finding.impact_cents, None
    return level, delta


def _parent_whole(
    parent: Investigation,
    measure: str | None,
    has_figure: Callable[[Finding], bool],
) -> Finding | None:
    """The parent finding that speaks for the WHOLE population, if any.

    Only an undimensioned parent has one: an answer already cut by payer
    published twelve cells and no total, and calling any one of them "the
    whole" would reconcile a breakdown against a slice. One finding over a
    spec with no cuts is the figure a breakdown of it must recompose to.

    Matched on the child's OWN measure, which is load-bearing rather than
    tidy: money is cents-as-int on a finding and ``_finding_money``
    truncates, so a RATE finding carrying ``Decimal("0.294")`` reads back as
    ``0`` — "a figure of zero" rather than "no figure" — and a breakdown
    reconciled against it would publish ``RECONCILIATION_FAILED … parent
    F1=$0.00`` about two numbers that were never comparable. ``has_figure``
    is therefore supplied by the caller that knows which KIND of quantity
    this child holds, and money and rate never answer for each other.
    """
    if parent.spec.dimensions or measure is None:
        return None
    speaking = [
        finding
        for finding in parent.findings
        if measure in {ref.id for ref in finding.metric_refs} and has_figure(finding)
    ]
    return speaking[0] if len(speaking) == 1 else None


def _parent_finding(
    parent: Investigation,
    operators: tuple[Refinement, ...],
    measure: str | None,
    spec: AnalysisSpec | None,
    session_findings: Callable[[], Sequence[Finding]],
    has_figure: Callable[[Finding], bool],
) -> tuple[Finding, bool] | None:
    """The published figure this child descends from, and how it got there.

    Returns ``(finding, breakdown)`` — ``breakdown`` true when this turn cut
    an undimensioned parent rather than drilling a named handle. ``None``
    when nothing on screen contains this child's population.

    Lifted out of :func:`containment_reconciliation` unchanged when the rate
    path was added (FN-10): which finding a child descends from is a
    question about the THREAD, and it has the same answer whether the
    quantity being tied out is dollars or a ratio.

    **Matched on the child's own measure, on every branch** (R9-03). The
    immediate-parent branch matched on the referent alone, so drilling F1
    "State Medicaid MCO: 29.5% denial rate" from a money child located F1,
    read its ratio back through the money path as ``$0.00`` and published a
    red ``+311,744,900.0%`` disagreement between a rate and a pile of
    dollars. ``_parent_whole`` has carried this predicate since FN-10 and
    documents exactly this trap; the drill branch is the half that never
    got it. A parent that published no figure of the child's kind does not
    contain the child — it is a different measurement, and the caller says
    so as ``not_applicable``.
    """
    targets = {op.target.value for op in operators if isinstance(op, DrillInto)}
    finding = next(
        (
            f
            for f in parent.findings
            if f.referent.value in targets
            and measure is not None
            and measure in {ref.id for ref in f.metric_refs}
            and has_figure(f)
        ),
        None,
    )
    if finding is None and targets:
        # A thread drills what is ON SCREEN, and what is on screen is every
        # handle the session has published — not only the last turn's
        # (round-6 E-02). Live, a CARC breakdown of a payer cell reconciled
        # against nothing because the cell it decomposed had been published
        # two turns earlier: 13 cells summing to $176,112.25 beside a figure
        # of $176,112.25, and the product made the reader do the arithmetic.
        #
        # Strictly the SAME METRIC across turns, which the immediate-parent
        # case can take for granted and this one cannot: an older handle in
        # a long thread is as likely to measure something else, and tying a
        # denied-dollar drill out against a cash finding is a disagreement
        # this platform would then have to explain.
        finding = next(
            (
                f
                for f in session_findings()
                if f.referent.value in targets
                and measure is not None
                and measure in {ref.id for ref in f.metric_refs}
                and has_figure(f)
            ),
            None,
        )
    if finding is not None:
        return finding, False
    # Round-6 E-02: a BREAKDOWN of a whole is the same containment question
    # a drill asks, and it was never asked. "Break that out by payer" off a
    # $1,193,126.92 July total published twelve cells that sum to
    # $1,193,126.92 and said ``not_applicable; this turn produced no
    # compared money frame`` — the arithmetic every reader of a breakdown
    # does by hand, available and withheld.
    if targets or not _splits_parent(spec, parent):
        return None
    whole = _parent_whole(parent, measure, has_figure)
    return None if whole is None else (whole, True)


def _splits_parent(spec: AnalysisSpec | None, parent: Investigation) -> bool:
    """Did this turn cut the parent's population along a new dimension?

    Read off the SPECS, not off the operator names (round-6 E-02). "Break
    that out by payer" reached the engine as a ``set_dimensions`` on one
    session and as something else on another, and the second reported
    ``this turn neither split nor drilled the parent's population`` about a
    turn that plainly had. A turn that gained a cut split the population,
    whichever operator got it there.
    """
    if spec is None:
        return False
    gained = {d.id for d in spec.dimensions} - {d.id for d in parent.spec.dimensions}
    return bool(gained)


#: Component-column suffixes a ratio contract carries beside its value.
_NUMERATOR_COLUMN_SUFFIX = "__num"
_DENOMINATOR_COLUMN_SUFFIX = "__den"


@dataclass(frozen=True, slots=True)
class _RateTotals:
    """A rate child's cells, ready to be recombined into their parent.

    A rate does not sum. ``29.5% + 22.9% + 18.8%`` is not a number, which is
    why the money reconciliation could not simply be pointed at a ratio and
    why rate breakdowns had no seam at all (FN-10). What DOES recompose is
    the pair of components every ratio cell carries: the parent rate is
    ``Σ numerator / Σ denominator`` across the cells, weighted by each
    cell's own population without anybody having to say so.
    """

    measure: str
    numerator: Decimal
    denominator: Decimal
    cells: int
    #: Cells the §15 policy left without a usable numerator — nulled, or
    #: CLAMPED to a ceiling, which is the same fact wearing a number. Their
    #: population is known and their numerator is not, so they cannot enter
    #: the recomposition, and their denominator is carried here rather than
    #: dropped because it is exactly the slack that makes a gap honest.
    withheld_cells: int
    withheld_denominator: Decimal
    #: The tightest cap the policy itself puts on those numerators' SUM.
    #: A cell is bounded because its numerator is under the §15 threshold,
    #: so ``cells * (threshold - 1)`` is knowledge, not a guess — and it is
    #: the difference between an interval a reader can act on and one so
    #: wide a real disagreement could hide inside it.
    withheld_ceiling: Decimal

    @property
    def rate(self) -> Decimal | None:
        return self.numerator / self.denominator if self.denominator > 0 else None

    @property
    def bounds(self) -> tuple[Decimal, Decimal] | None:
        """What the parent rate COULD be, given the cells that were withheld.

        Every withheld numerator lies in ``[0, min(its denominator, the §15
        threshold - 1)]``, so the whole population's true rate lies between
        the two extremes of putting all of that slack, or none of it, in the
        numerator. With nothing withheld the interval collapses to the
        recomposed point and the ordinary tolerance decides.
        """
        total_den = self.denominator + self.withheld_denominator
        if total_den <= 0:
            return None
        return (
            self.numerator / total_den,
            (self.numerator + self.withheld_ceiling) / total_den,
        )


def _frame_rate_totals(
    frames: tuple[tuple[str, EvidenceFrame], ...],
    threshold: int | None,
) -> _RateTotals | None:
    """Recompose the last ratio-bearing frame's cells into one rate.

    "Last" for the same reason the money reader uses it: frames are listed
    in creation order, so the final one is the frame the findings stage
    read. Nothing is inferred where the components do not exist — a ratio
    published without its numerator and denominator columns cannot be
    recomposed, and saying so is the honest outcome.

    **A ceiling is not a numerator** (the round-5 A-02a rule, one layer up).
    The §15 policy publishes a small numerator as ``threshold - 1`` rather
    than dropping the cell, so a bounded cell arrives carrying the integer
    ``10`` and reads exactly like a measurement. Summing those tens gave
    12 live payer cells recomposing to 13.5% against a parent of 12.8% and
    a ``RECONCILIATION_FAILED`` about a gap that was entirely the policy's.
    So bounded cells are recognised by :func:`bound_index` — the same
    governed definition the findings and charts use — and contribute their
    population, never their ceiling.
    """
    for _, frame in reversed(frames):
        bounded = bound_index(frame, threshold) if threshold is not None else {}
        for column in frame.schema.columns:
            if column.unit != _RATIO_UNIT_NAME or "__" in column.name:
                continue
            num_col = f"{column.name}{_NUMERATOR_COLUMN_SUFFIX}"
            den_col = f"{column.name}{_DENOMINATOR_COLUMN_SUFFIX}"
            names = frame.schema.names
            if num_col not in names or den_col not in names:
                continue
            idx_num, idx_den = frame.schema.index_of(num_col), frame.schema.index_of(den_col)
            numerator = denominator = withheld_den = Decimal(0)
            cells = withheld = 0
            for index, row in enumerate(frame.rows):
                den = _as_decimal(row[idx_den])
                if den is None or den <= 0:
                    continue
                num = _as_decimal(row[idx_num])
                if num is None or column.name in bounded.get(index, {}):
                    withheld += 1
                    withheld_den += den
                    continue
                numerator += num
                denominator += den
                cells += 1
            if cells == 0 and withheld == 0:
                continue
            cap = withheld_den
            if threshold is not None:
                cap = min(cap, Decimal(withheld * (threshold - 1)))
            measure = column.ref.id if isinstance(column.ref, MetricRef) else column.name
            return _RateTotals(
                measure=measure,
                numerator=numerator,
                denominator=denominator,
                cells=cells,
                withheld_cells=withheld,
                withheld_denominator=withheld_den,
                withheld_ceiling=cap,
            )
    return None


def _as_decimal(value: Scalar) -> Decimal | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return Decimal(value)


def _finding_rate(finding: Finding, measure: str) -> Decimal | None:
    """The ratio a parent finding published for ``measure``, if any."""
    for name, value in finding.values:
        if name != measure:
            continue
        return _as_decimal(value)
    return None


@dataclass(frozen=True, slots=True)
class Containment:
    """A child's tie-out against the whole it descends from.

    ``anchor`` is the half of FN-10 the summary cannot carry: the parent's
    own level, restated ON THE CHILD as a mandatory disclosure. The exec's
    live t2 stated 29.5%, 22.9% and 18.8% by payer and never once said the
    12.8% it descends from, so "a reader who lands on the breakdown — which
    is exactly what a Monitors tile links to — comes away believing denial
    rates run 19-29%". ``None`` when there is no parent LEVEL to anchor to
    (a movement tied out against a movement anchors nothing).
    """

    summary: str
    passed: bool
    anchor: str | None = None


def containment_reconciliation(
    parent: Investigation,
    calculation: CalculationResult,
    operators: tuple[Refinement, ...],
    spec: AnalysisSpec | None = None,
    #: The session's published findings, as a THUNK: only a drill whose
    #: handle is not on the immediate parent reads it, and on a breakdown —
    #: the shape this function was extended for — the store round trip it
    #: costs would buy nothing.
    session_findings: Callable[[], Sequence[Finding]] = tuple,
    #: The §15 threshold this turn ran under. A ceiling is only recognisable
    #: against the threshold that produced it, and a ceiling summed as a
    #: numerator is the FN-10 fix reintroducing the round-5 A-02a defect.
    suppression_threshold: int | None = None,
    #: The pinned pack's unit for a metric id (R9-03). Without it a rate
    #: finding's ``Decimal("0.295082")`` truncates to ``0`` and reconciles
    #: as a dollar figure of nothing.
    metric_unit: MetricUnitLookup | None = None,
) -> Containment | None:
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

    Returns a :class:`Containment`, or ``None`` when this turn drilled no
    parent finding that published a figure of the child's own kind — in
    which case the caller's existing verdicts stand.

    **Rates recompose; they do not sum** (round-7 FN-10, exec gate G6). The
    money seam above is verified closed and holds. The rate seam did not
    exist: "For July 2026 on a service basis, the denial rate came in at
    12.8% (F1)" followed by "Break that down by payer" reported *"this turn
    produced no compared money frame to reconcile against the parent, so
    reconciliation is not applicable"*, published 29.5% / 22.9% / 18.8% plus
    four ceilings, and never restated the 12.8% it descends from. "The seam
    is closed on money and open on rates, which is the drill an RCM director
    performs every single day."

    A rate breakdown therefore ties out through its COMPONENTS: each cell
    carries its own numerator and denominator, and ``Σ num / Σ den`` is the
    parent rate weighted by each cell's population. Where the §15 policy
    withheld a numerator the cell cannot enter that sum, so the verdict
    states the interval its population could move the parent inside, and
    calls a gap inside that interval what it is — the suppression, not a
    disagreement.

    **The two sides must be the same KIND of quantity** (round-5 A-03).
    ``impact_cents`` on a comparison finding is a MOVEMENT and the child
    frame's total is a LEVEL, so every drill of a top mover — the single
    most common action in a close — reported ``RECONCILIATION_FAILED``
    against numbers that agreed perfectly: parent F5 "$82,623.40 vs prior
    period" against a child of $102,409.87, whose residual $19,786.47 was
    exactly the prior side the parent had differenced away. So the quantity
    is chosen before it is compared: movement against movement when both
    sides compare, level against level otherwise, and ``not_applicable``
    naming the mismatch when they cannot be matched at all — never
    ``failed``, which is a claim that two figures for one cell disagree.
    """
    child_level, child_delta, measure = _frame_money_totals(calculation.frames)
    if child_level is None:
        return _rate_containment(
            parent, calculation, operators, spec, session_findings, suppression_threshold
        )
    located = _parent_finding(
        parent,
        operators,
        measure,
        spec,
        session_findings,
        lambda f: any(value is not None for value in _finding_money(f, metric_unit)),
    )
    if located is None:
        return _rate_containment(
            parent, calculation, operators, spec, session_findings, suppression_threshold
        )
    finding, breakdown = located
    parent_level, parent_delta = _finding_money(finding, metric_unit)
    if parent_level is None and parent_delta is None:
        # The handle on screen published no dollar figure. A turn carrying
        # both a money column and a rate one still has a rate to tie out,
        # so the rate path gets its turn rather than the seam closing here.
        return _rate_containment(
            parent, calculation, operators, spec, session_findings, suppression_threshold
        )
    same_measure = measure is not None and measure in {ref.id for ref in finding.metric_refs}
    if not same_measure:  # pragma: no cover - _parent_finding now matches on measure
        scope = f"{measure or 'this turn'} against {finding.referent.value}"
    elif breakdown:
        cells = max((len(frame.rows) for _, frame in calculation.frames), default=0)
        scope = (
            f"the {cells} row(s) this breakdown published, summed, against the whole "
            f"{finding.referent.value} measured"
        )
    else:
        scope = f"the same metric ({measure}) over the cell {finding.referent.value} names"
    if parent_delta is not None and child_delta is not None:
        kind, parent_cents, child_cents = "movement vs movement", parent_delta, child_delta
    elif parent_level is not None:
        kind, parent_cents, child_cents = "level vs level", parent_level, child_level
    else:
        # The parent published a movement and this turn published only a
        # level. Nothing disagrees; there is simply nothing to tie out.
        return Containment(
            summary=_not_applicable(
                f"the parent finding {finding.referent.value} published a movement "
                f"({money(parent_delta or 0)} vs its prior period) and this turn published a "
                f"level ({money(child_level)}) — two different kinds of quantity, so neither "
                "contains the other. Compare this turn against the same prior period to tie "
                "the two out."
            ),
            passed=True,
        )
    delta = child_cents - parent_cents
    fraction = Decimal(delta) / Decimal(abs(parent_cents) or 1)
    passed = abs(fraction) <= _CONTAINMENT_TOLERANCE
    summary = (
        f"status={'passed' if passed else 'failed'}; "
        f"scope={'breakdown' if breakdown else 'containment'} ({kind}); "
        f"parent {finding.referent.value}={money(parent_cents)}; child={money(child_cents)}; "
        f"delta={money(delta)} ({float(fraction):+.1%}); basis={scope}"
    )
    return Containment(
        summary=summary,
        passed=passed,
        anchor=(
            _parent_anchor(
                finding,
                measure,
                money(parent_level),
                breakdown=breakdown,
                recombines="by addition",
            )
            if same_measure and parent_level is not None
            else None
        ),
    )


def measure_mismatch_reason(
    findings: Sequence[Finding],
    operators: tuple[Refinement, ...],
    measure: str | None,
) -> str | None:
    """Why a drill had nothing to tie out: the handle measures something else.

    Round-9 R9-03. The thread is real — the analyst clicked a chip this
    product put on that finding — and there is still nothing to reconcile:
    F1 published a denial RATE and the drill published dollars, so neither
    contains the other. Before the measure predicate on
    :func:`_parent_finding` this case produced a red ``failed`` banner
    asserting that two figures for one cell disagreed
    (``parent F1=$0.00; child=$31,174.49; +311,744,900.0%``) over two
    quantities that were never the same kind. It now produces nothing at
    all, and the caller's generic "this turn produced no compared money
    frame" is false on a turn that plainly holds one — so this is the
    sentence that goes in its place.

    ``None`` when no drilled handle is on screen, or when the handle does
    publish the child's measure: those are the caller's other verdicts.
    """
    targets = {op.target.value for op in operators if isinstance(op, DrillInto)}
    if not targets or measure is None:
        return None
    finding = next((f for f in findings if f.referent.value in targets), None)
    if finding is None:
        return None
    published = sorted({ref.id for ref in finding.metric_refs})
    if measure in published:
        return None
    named = ", ".join(published) or "no metric of its own"
    return (
        f"the finding this turn drilled ({finding.referent.value}) published {named}, and "
        f"this answer publishes {measure} — two different measurements of that cell rather "
        "than a part and its whole, so neither contains the other. Ask for "
        f"{measure} over the parent population to tie the two out."
    )


def _parent_anchor(
    finding: Finding,
    measure: str | None,
    figure: str,
    *,
    breakdown: bool,
    recombines: str,
) -> str:
    """The parent's own level, restated on the child (FN-10).

    The reconciliation summary states it too, and that is not enough: a
    reader who lands on a breakdown from a Monitors tile reads the cells, and
    the seam verdict is a line they have to go looking for. This sentence is
    a MANDATORY disclosure — composed here from a figure the parent already
    certified, published verbatim ahead of whatever the composer writes, and
    exempt from the grounding pass for the same reason every other mandatory
    disclosure is (it carries no number that is not already certified).
    """
    opening = (
        "decomposes a population this session already measured"
        if breakdown
        else "drills into a cell of a population this session already measured"
    )
    label = metric_label(measure) if measure else "that measure"
    return (
        f"parent_level: this answer {opening} — {label} over the parent population is "
        f"{figure} ({finding.referent.value}). The cells below are parts of that {figure}: they "
        f"recombine to it {recombines}, and none of them is a second measurement of the whole."
    )


def _rate_containment(
    parent: Investigation,
    calculation: CalculationResult,
    operators: tuple[Refinement, ...],
    spec: AnalysisSpec | None,
    session_findings: Callable[[], Sequence[Finding]],
    suppression_threshold: int | None,
) -> Containment | None:
    """Recompose a RATE child into the rate it was cut out of (FN-10).

    The money path answers "do the parts sum to the whole". A rate has no
    such question — ``29.5% + 22.9% + 18.8%`` is not a number — so the
    question asked here is the one an analyst actually asks: *given these
    cells, what is the population rate, and is it the one the parent
    published?* Answered from each cell's own numerator and denominator,
    which is a weighted recomposition without anybody having to weight
    anything.

    Suppression is stated rather than absorbed. A cell whose numerator the
    §15 policy withheld keeps its population and loses its contribution, so
    the recomposed figure is one point in an interval — and a parent inside
    that interval AGREES with these cells, while one outside it does not.
    Calling the first case ``failed`` would publish a disagreement between
    two figures that are both correct.
    """
    totals = _frame_rate_totals(calculation.frames, suppression_threshold)
    if totals is None:
        return None
    located = _parent_finding(
        parent,
        operators,
        totals.measure,
        spec,
        session_findings,
        lambda f: _finding_rate(f, totals.measure) is not None,
    )
    if located is None:
        return None
    finding, breakdown = located
    parent_rate = _finding_rate(finding, totals.measure)
    recomposed = totals.rate
    if parent_rate is None:
        return None
    if recomposed is None:
        # Every cell's numerator was withheld: the components exist and
        # none of them may be read, so there is no recomposition to state.
        return Containment(
            summary=_not_applicable(
                f"every one of the {totals.withheld_cells} cell(s) this turn published for "
                f"{totals.measure} had its numerator withheld by the small-cell policy, so the "
                f"parent rate cannot be recomposed from them. The parent figure "
                f"{finding.referent.value}={ratio_pct(parent_rate)} stands as published."
            ),
            passed=True,
            anchor=_parent_anchor(
                finding,
                totals.measure,
                ratio_pct(parent_rate),
                breakdown=breakdown,
                recombines="through their own denominators, not by addition",
            ),
        )
    interval = totals.bounds
    delta = recomposed - parent_rate
    fraction = delta / (abs(parent_rate) or Decimal(1))
    within_tolerance = abs(fraction) <= _CONTAINMENT_TOLERANCE
    explained = (
        interval is not None
        and totals.withheld_cells > 0
        and interval[0] <= parent_rate <= interval[1]
    )
    passed = within_tolerance or explained
    cells_text = (
        f"the {totals.cells} measurable cell(s) this "
        f"{'breakdown' if breakdown else 'drill'} published, recombined through their own "
        f"denominators, against the whole {finding.referent.value} measured"
    )
    withheld_text = ""
    if totals.withheld_cells:
        assert interval is not None
        withheld_text = (
            f"; withheld={totals.withheld_cells} cell(s) over "
            f"{totals.withheld_denominator:,f} population whose numerator the small-cell policy "
            f"suppressed, so the population rate lies in "
            f"{ratio_pct(interval[0])}..{ratio_pct(interval[1])}"
        )
        if explained and not within_tolerance:
            # Said, not left to be inferred: a reader looking at a passing
            # verdict beside a 1.1-point delta is owed the reason it is not
            # a disagreement, in the same line.
            withheld_text += (
                " — the parent sits inside that interval, so the gap is the suppression "
                "and not a disagreement"
            )
    # ``passed_with_suppression`` is the grammar's own third state
    # (:class:`revi_calculation.operators.reconcile.ReconciliationStatus`),
    # and it is exactly this case: the two figures do not meet at a point
    # and they do not disagree, because the §15 policy is standing between
    # them. Calling it plain ``passed`` would overstate the tie-out;
    # ``failed`` would invent a conflict.
    status = "failed"
    if within_tolerance:
        status = "passed"
    elif explained:
        status = "passed_with_suppression"
    summary = (
        f"status={status}; "
        f"scope={'breakdown' if breakdown else 'containment'} (rate recomposition); "
        f"parent {finding.referent.value}={ratio_pct(parent_rate)}; "
        f"child recomposed={ratio_pct(recomposed)} "
        f"({totals.numerator:,f}/{totals.denominator:,f}); "
        # Signed: ``points`` is unsigned by design (a rate's movement is
        # said with a direction word beside it), and a reconciliation delta
        # has no such word — an unsigned "1.5 points" beside "-11.6%" reads
        # as two different answers to one question.
        f"delta={'-' if delta < 0 else '+'}{points(delta)} ({float(fraction):+.1%})"
        f"{withheld_text}; basis={cells_text}"
    )
    return Containment(
        summary=summary,
        passed=passed,
        anchor=_parent_anchor(
            finding,
            totals.measure,
            ratio_pct(parent_rate),
            breakdown=breakdown,
            recombines="through their own denominators, not by addition",
        ),
    )


def _qualify_every_finding(findings: FindingsResult) -> FindingsResult:
    """Drop every finding on the turn out of ``high`` confidence.

    What an integrity guard does once it has spoken, in one place: a turn
    whose comparison is not a comparison has no high-confidence finding on
    it, whichever guard established that. Lowered, never raised — a finding
    already ``qualified`` for another reason keeps the stronger caveat.
    """
    return replace(
        findings,
        findings=tuple(
            finding if finding.confidence != "high" else replace(finding, confidence="qualified")
            for finding in findings.findings
        ),
    )


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


#: An option that is itself a question (round-8 FIX-12(d)). Live, on "Why
#: did it go up?": option 3 read "Which metric are you asking about? — I
#: mean the last figure you charted." An option is a sentence the analyst
#: SENDS BACK; a question sent back resolves nothing, and the reader is
#: left choosing between answering and being asked again.
#:
#: Matched on the shape rather than on a keyword list: an interrogative
#: opener plus a question mark. "What if we exclude Medicare?" would be a
#: legitimate thing to say and is not one of these — it has no
#: interrogative opener asking the ANALYST to supply the thing this
#: platform is missing.
_INTERROGATIVE_OPTION = re.compile(
    r"^\s*(?:what|which|who|why|how|when|where|do|does|did|are|is|should|could|would)\b"
    r"[^?]*\?",
    re.IGNORECASE,
)


def _drop_interrogative_options(
    clarification: ClarificationRequest,
) -> ClarificationRequest:
    """The same question, minus every "option" that is another question."""
    if not clarification.options:
        return clarification
    kept = tuple(
        option
        for option in clarification.options
        if _INTERROGATIVE_OPTION.match(option) is None
    )
    if len(kept) == len(clarification.options):
        return clarification
    if not kept:
        # Better an honest optionless card than a row of buttons that ask
        # the reader the same thing the heading does; ``_no_options_card``
        # already renders that state as a statement of what is needed.
        return replace(
            clarification,
            options=(),
            bindings=(),
            reason=(
                f"{clarification.reason}; every generated option was itself a question, "
                "so none of them could be sent back as an answer"
            ),
        )
    return replace(clarification, options=kept, bindings=_bindings_for(clarification, kept))


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


def _with_binding(spec: AnalysisSpec, binding: ClarificationBinding | None) -> AnalysisSpec:
    """Pin the ids a clarification option stands for onto an interpretation.

    The option is not a suggestion the model may re-litigate: the analyst
    tapped a thing this platform named, in this platform's own ids, and
    those ids win over whatever a second reading of the sentence proposes.
    Everything the option is silent about — window, comparison, cuts it
    does not name — is left exactly as the sentence was read.
    """
    if binding is None:
        return spec
    if binding.metric_ids:
        spec = replace(spec, measures=tuple(MetricRef(m) for m in binding.metric_ids))
    if binding.dimension_ids:
        spec = replace(spec, dimensions=tuple(DimensionRef(d) for d in binding.dimension_ids))
    return _with_chosen_values(spec, binding.scope)


def _with_resumed_context(
    spec: AnalysisSpec, resume: AnalysisSpec | None, window_explicit: bool
) -> tuple[AnalysisSpec, bool, list[str]]:
    """Carry the interrupted thread's context onto the resumed answer.

    Round-5 A-01's second symptom. A clarification interrupts a THREAD, and
    the thread's window, filters, comparison and cohort belong to the
    analyst: "break that down by CARC code." on a Meridian / imaging / July
    thread came back with ``filters: []``, ``cohort: null`` and a
    three-year window, narrated as a first turn.

    Applied only where the resumed sentence states nothing itself, so a
    resume that names its own period keeps it, and a dimension the analyst
    re-scoped is never widened back. Every carry is disclosed: an inherited
    window the analyst did not say out loud is an assumption, and §2.8
    assumptions are published, not buried.
    """
    if resume is None:
        return spec, window_explicit, []
    notes: list[str] = []
    context = spec.context
    if not window_explicit and resume.context.window.range != context.window.range:
        carried = resume.context.window.range
        context = replace(context, window=resume.context.window)
        window_explicit = True
        notes.append(
            "resumed_context: this answers a question that interrupted an existing thread, so "
            f"it is measured over that thread's window ({carried.start.isoformat()}.."
            f"{carried.end.isoformat()}) rather than a default one. Say a period if you want a "
            "different one."
        )
    if context.comparison is None and resume.context.comparison is not None:
        context = replace(context, comparison=resume.context.comparison)
        notes.append(
            "resumed_context: the comparison the interrupted thread was reading against "
            f"({resume.context.comparison.window.range.start.isoformat()}.."
            f"{resume.context.comparison.window.range.end.isoformat()}) is carried onto this "
            "answer."
        )
    constrained = {p.dimension.id for p in iter_predicates(context.scope)}
    # A dimension this turn CUTS BY is a dimension it is asking about across
    # its whole population, and pinning it to the thread's one value answers
    # a different question under the asked question's heading (round-9
    # R9-02). Live: "Give me a payer scorecard for July 2026" inherited
    # ``payer eq [Atlas Commercial]`` from the thread it interrupted and
    # published one payer's A/R as the scorecard.
    cut_by = {ref.id for ref in spec.dimensions}
    inherited = [
        predicate
        for predicate in iter_predicates(resume.context.scope)
        if predicate.dimension.id not in constrained and predicate.dimension.id not in cut_by
    ]
    declined = [
        predicate
        for predicate in iter_predicates(resume.context.scope)
        if predicate.dimension.id not in constrained and predicate.dimension.id in cut_by
    ]
    if inherited:
        context = replace(context, scope=and_merge(context.scope, *inherited))
        notes.append(
            "resumed_context: the filters the interrupted thread was scoped by are carried onto "
            "this answer — " + "; ".join(_predicate_label(p) for p in inherited) + "."
        )
    if declined:
        # Said, not silently dropped: the analyst can see which scope the
        # thread had and that this answer deliberately widened past it.
        notes.append(
            "resumed_context: the interrupted thread was scoped by "
            + "; ".join(_predicate_label(p) for p in declined)
            + ", and this question breaks out BY that same cut — so the filter is NOT carried "
            "and the figures below cover the whole population. Name it again if you wanted "
            "just that one."
        )
    if context.cohort is None and resume.context.cohort is not None:
        context = replace(context, cohort=resume.context.cohort)
        notes.append(
            f"resumed_context: the pinned cohort ({resume.context.cohort.id}) the interrupted "
            "thread was scoped to is carried onto this answer."
        )
    return spec.with_context(context), window_explicit, notes


def claim_referent_predicates(
    spec: AnalysisSpec, entries: Sequence[RegisteredReferent]
) -> tuple[AnalysisSpec, list[str]]:
    """Take every referent handle back out of the scope before it is judged.

    Round-6 E-03, defence in depth behind ``_referent_resume``. A predicate
    whose value is ``F1`` is not a claim about the data; it is a claim about
    something this platform published, and the value-existence guard has no
    business refusing it as a missing facility. Where the registry knows
    what the handle stood for — a row IS a ``(dimension, value)`` pair — the
    predicate is rewritten to that pair; where it does not, the predicate is
    dropped, and either way the substitution is disclosed.
    """
    predicates = list(iter_predicates(spec.context.scope))
    # Rewritten only where the rewrite is provably meaning-preserving: a
    # flat conjunction of POSITIVE membership tests. Taking a value out of
    # one side of an OR changes what the other side means, and dropping a
    # NOT turns an exclusion into an inclusion — a scope this engine did
    # not build is one it must not silently edit. Anything else keeps its
    # handle and gets the value-existence guard's honest refusal, which is
    # worse copy but a true statement.
    if not predicates or and_merge(*predicates) != spec.context.scope:
        return spec, []
    if any(p.op not in (PredicateOp.EQ, PredicateOp.IN) for p in predicates):
        return spec, []
    known = {entry.referent.value: entry.dimension_value for entry in entries}
    notes: list[str] = []
    kept_predicates: list[Predicate] = []
    additions: list[Predicate] = []
    for predicate in predicates:
        claimed = {
            handle: known[handle]
            for value in predicate.values
            if REFERENT_HANDLE.fullmatch(str(value).strip())
            and (handle := str(value).strip().upper()) in known
        }
        if not claimed:
            kept_predicates.append(predicate)
            continue
        for handle, pair in claimed.items():
            if pair is None:
                notes.append(
                    f"referent_claimed: {handle} is a handle this session published, not a "
                    f"{predicate.dimension.id} value, so it was not applied as a filter."
                )
                continue
            dimension, value = pair
            notes.append(
                f"referent_claimed: {handle} is the row this session published for "
                f"{dimension} {value!r}, so it was read as that rather than as a "
                f"{predicate.dimension.id} value this warehouse does not hold."
            )
            additions.append(
                Predicate(dimension=DimensionRef(dimension), op=PredicateOp.EQ, values=(value,))
            )
        remaining = tuple(
            value for value in predicate.values if str(value).strip().upper() not in claimed
        )
        if remaining:
            kept_predicates.append(
                replace(
                    predicate,
                    op=PredicateOp.IN if len(remaining) > 1 else PredicateOp.EQ,
                    values=remaining,
                )
            )
    if not notes:
        return spec, []
    constrained = {p.dimension.id for p in kept_predicates}
    kept_predicates.extend(p for p in additions if p.dimension.id not in constrained)
    scope = and_merge(*kept_predicates) if kept_predicates else EMPTY_SCOPE
    return spec.with_context(replace(spec.context, scope=scope)), notes


def scope_names_a_handle(spec: AnalysisSpec) -> bool:
    """Does this scope filter on something SHAPED like a referent handle?

    The pure precondition for :func:`claim_referent_predicates` doing
    anything at all, so the registry read behind it is not paid on every
    new-investigation turn — a first turn has no referents to claim and a
    handle-shaped filter value is rare on any turn.
    """
    return any(
        REFERENT_HANDLE.fullmatch(str(value).strip())
        for predicate in iter_predicates(spec.context.scope)
        for value in predicate.values
    )


def _same_findings(served: Sequence[Finding], parent: Sequence[Finding]) -> bool:
    """Would a reader see the same rows? (round-5 E-01)

    Compared on what is PUBLISHED — titles, statements and values —
    deliberately not on referents: a reused plan mints new handles, and
    keying identity off them would say "these are different findings"
    about two byte-identical lists.
    """
    return [(f.title, f.statement, f.values) for f in served] == [
        (f.title, f.statement, f.values) for f in parent
    ]


#: "Export this" and the ways people say it. A file is not something this
#: engine can hand over: the export is composed in the client, from the
#: answer already on screen, and nothing on the wire produces a document.
_EXPORT_REQUEST = re.compile(
    r"(?<!\w)(?:export|download|save\s+(?:this|that|it)|send\s+(?:me\s+)?(?:this|that|it)|"
    r"email\s+(?:me\s+)?(?:this|that|it)|(?:as|to)\s+(?:a\s+)?(?:csv|excel|xlsx|pdf|"
    r"spreadsheet)|give\s+me\s+(?:a\s+)?(?:csv|file|spreadsheet))(?!\w)",
    re.IGNORECASE,
)


def _export_refusal_sentence(question: str) -> str | None:
    """Refuse an export by name rather than re-answering (FIX-12(b)).

    Live: "Export this" came back ``turn_class: presentation_only`` with a
    freshly written paraphrase of the same finding — no file, no download,
    no sentence saying where export lives. A director who says "export
    this" and gets a re-worded paragraph concludes the export failed or the
    assistant is stalling, and neither is recoverable on stage.

    The export itself is real and good: Copy answer and the CSV download
    sit on the answer card, run entirely in the browser, and carry the
    provenance line and the provisional marks. What does not exist is a
    way to ASK for it in words, so this says exactly that and points at the
    control — the shape this platform uses for every other thing it cannot
    do.
    """
    match = _EXPORT_REQUEST.search(question)
    if match is None:
        return None
    # "export" and "download" are bare verbs and want an object; "give me a
    # spreadsheet" already has one, and appending another produced "you
    # asked to give me a spreadsheet this" on a demo surface.
    asked = match.group(0).lower()
    asked = f"{asked} this" if " " not in asked else asked
    return (
        f"you asked to {asked}, and I cannot hand you a file from here — nothing I return "
        "is a document. The export is on the answer itself: 'Copy answer' puts the "
        "findings, the analysis and every caveat on your clipboard, and the CSV download "
        "saves the rows with their provisional marks and the data load in the filename. "
        "There is no new answer here and nothing was exported by this turn."
    )


def _export_request_refusal(question: str) -> str | None:
    """The export refusal as a warning code, for the turn's own record."""
    sentence = _export_refusal_sentence(question)
    return None if sentence is None else f"refinement_not_applied: {sentence}"


def _unapplied_presentation_sentence(question: str) -> str | None:
    """Name what a re-presentation was asked to change and did not do.

    ``REFINEMENT_NOT_APPLIED`` has been registered since round 4 and had no
    caller on this path, so turn two of a session re-served the parent's
    rows in the parent's order while reading like a fresh analysis that had
    honoured the request.
    """
    match = PRESENTATION_CHANGE_REQUEST.search(question)
    if match is None:
        return None
    return (
        f"you asked to {match.group(0).lower()} — and I could not resolve that against the "
        "rows on screen, so nothing was re-ordered and no new answer was produced. Name the "
        "column to order by (or ask for the cut you want) and I will re-run it."
    )


def _unapplied_presentation_request(question: str) -> str | None:
    """The unapplied-presentation note as a warning code."""
    sentence = _unapplied_presentation_sentence(question)
    return None if sentence is None else f"refinement_not_applied: {sentence}"


#: Marks the turn that asked this platform to re-present something and got
#: nothing new for it. The turn is a REFUSAL, not an answer (round-10
#: R10-5): a re-served paragraph under ``outcome: answer`` is what made
#: "Export this" the demo battery's only outright fail, three rounds
#: running, and by round 10 the payload was carrying
#: ``REFINEMENT_NOT_APPLIED`` beside ``outcome: answer`` — the engine
#: recording that the instruction changed nothing while shipping it as
#: though it had.
PRESENTATION_PRODUCED_NOTHING_REASON = "PRESENTATION_PRODUCED_NOTHING"


def _presentation_refusal(question: str) -> ClarificationRequest | None:
    """The refusal card for a re-presentation that produced no artifact.

    One shape for both dead ends this path can reach — an export asked for
    in words, and an ordering that resolves against no column the rows
    carry. Neither produces a file, a chart or a row that was not already
    on screen, and this platform's word for that is a refusal.

    No options on purpose: there is nothing to tap. The control is NAMED in
    the sentence, which is what the reader needs, and
    ``_no_options_card`` labels the card so a renderer draws a statement
    rather than a question above an empty row of buttons.
    """
    sentence = _export_refusal_sentence(question) or _unapplied_presentation_sentence(question)
    if sentence is None:
        return None
    return ClarificationRequest(
        question=sentence[0].upper() + sentence[1:],
        options=(),
        reason=(
            f"{PRESENTATION_PRODUCED_NOTHING_REASON}: this turn re-presented an existing "
            "answer and produced no new artifact, so it is refused rather than served as a "
            "second answer over the same rows"
        ),
    )


#: Words that say which END of an ordering comes first.
_ORDER_DESCENDING = re.compile(
    r"(?<!\w)(?:largest|biggest|highest|greatest|most|worst|descending|desc|"
    r"high\s+to\s+low)(?!\w)",
    re.IGNORECASE,
)
_ORDER_ASCENDING = re.compile(
    r"(?<!\w)(?:smallest|lowest|least|best|ascending|asc|low\s+to\s+high)(?!\w)",
    re.IGNORECASE,
)
_ORDER_BY_NAME = re.compile(r"(?<!\w)(?:alphabetical(?:ly)?|by\s+name|by\s+title)(?!\w)", re.IGNORECASE)

#: The analyst's words for the parts a published value name is built from.
#: A key is only chosen when EVERY one of its parts is named, so "percent
#: change" selects ``pct_change`` and never ``delta_cents``.
_VALUE_PART_WORDS: Mapping[str, tuple[str, ...]] = {
    "pct": ("pct", "percent", "percentage"),
    "change": ("change", "changes", "movement", "moved", "difference", "delta"),
    "delta": ("delta", "change", "changes", "movement", "moved", "difference"),
    "cents": ("dollars", "dollar", "amount", "amounts", "value", "values", "size", "magnitude"),
    "current": ("current", "value", "values", "level", "size"),
    "prior": ("prior", "previous", "baseline"),
    "rank": ("rank", "ranking", "position"),
    "share": ("share", "percent", "percentage"),
    "first": ("first",),
    "last": ("last",),
    "high": ("high", "highest"),
    "low": ("low", "lowest"),
    "periods": ("periods",),
}


def presentation_ordering(
    question: str, findings: Sequence[Finding]
) -> tuple[str, bool] | None:
    """``(value key, descending)`` the utterance asks the SHOWN rows to be in.

    Round-6 A-01/A-03. "Sort them by percent change, largest first" names a
    column the served findings already carry (``pct_change``), so the honest
    answer is to serve them in that order — not to re-plan the question from
    scratch (which collapsed twelve rows back to three), and not to re-serve
    the parent's order under a note saying the request was ignored.

    Deliberately conservative. A key is chosen only when every part of its
    name is named by the analyst and exactly one key qualifies, and only
    when EVERY served finding carries a number for it — a partial order over
    a list where some rows have no value is a different list, not a sorted
    one. ``("", ascending)`` is the by-title case; ``None`` means the
    request could not be resolved, and the caller says so.
    """
    if not findings:
        return None
    # An ordering is applied only when one was ASKED for. "Show me that
    # ranking again" names a column and instructs nothing; re-ordering it
    # would be the engine rearranging an answer nobody asked it to touch.
    if PRESENTATION_CHANGE_REQUEST.search(question) is None:
        return None
    descending = _ORDER_ASCENDING.search(question) is None
    if _ORDER_BY_NAME.search(question) is not None:
        # "alphabetically" states its own direction; "reverse alphabetical"
        # is the one that needs the descending word to be read.
        return "", _ORDER_DESCENDING.search(question) is not None
    tokens = {token.casefold() for token in re.findall(r"[A-Za-z']+", question)}
    published = [dict(finding.values) for finding in findings]
    # The free test first — a name the analyst did not say cannot be the
    # column they asked to sort by, and the coverage test below reads every
    # row of every candidate.
    named = [
        key
        for key in published[0]
        if (parts := [part for part in key.split("_") if part])
        and all(
            any(word in tokens for word in _VALUE_PART_WORDS.get(part, (part,)))
            for part in parts
        )
    ]
    # …and only then: a partial order over a list where some rows have no
    # value is a different list, not a sorted one.
    qualifying = [
        key
        for key in named
        if all(_as_number(values.get(key)) is not None for values in published)
    ]
    if len(qualifying) != 1:
        return None
    return qualifying[0], descending


def _reordered(
    findings: Sequence[Finding], key: str, descending: bool
) -> tuple[Finding, ...]:
    """The same findings, in the order the analyst asked for.

    ``key`` is one :func:`presentation_ordering` has already proved every
    finding carries a number for, so the sort key cannot be missing.
    """
    if not key:
        return tuple(sorted(findings, key=lambda f: f.title.casefold(), reverse=descending))
    return tuple(
        sorted(
            findings,
            key=lambda f: _as_number(dict(f.values).get(key)) or Decimal(0),
            reverse=descending,
        )
    )


def _chart_sorts_for(
    frames: Sequence[tuple[str, EvidenceFrame]], key: str, descending: bool
) -> tuple[tuple[str, str, bool], ...]:
    """The chart ordering that matches a re-served finding order.

    A ranked answer and the chart under it must not disagree about which
    cell is first (R3-13). The finding value name and the frame column name
    are not always the same string — a compared rate publishes ``pct_change``
    on the finding and ``<measure>__pct_change`` on the frame — so the
    PUBLISHED measure's own spelling is looked for first. That precedence is
    load-bearing: a ratio frame also carries ``<measure>__num__pct_change``,
    the numerator's movement, which sorts by a different number entirely.
    """
    if not key:
        return ()
    out: list[tuple[str, str, bool]] = []
    for frame_id, frame in frames:
        names = frame.schema.names
        measure = primary_measure(frame)
        candidates = [
            f"{measure}__{key}" if measure else None,
            key,
            *sorted(
                (name for name in names if name.endswith(f"__{key}")),
                key=lambda name: name.count("__"),
            ),
        ]
        column = next((c for c in candidates if c is not None and c in names), None)
        if column is not None:
            out.append((frame_id, column, descending))
    return tuple(out)


def _option_window_assumed(spec: AnalysisSpec) -> str:
    """Say out loud that a resumed answer's window is the platform's default.

    The fallback path builds its spec from the option's ids alone, and the
    only window available there is the widest one the load admits. That is
    a decision the analyst did not make, so it is disclosed as one — the
    silence is what let a whole-warehouse total be published under a
    disclosure claiming a July question had been resumed.
    """
    window = spec.context.window.range
    return (
        "window_assumed: your question did not resolve to a period on this reading, so the "
        f"answer covers {window.start.isoformat()}..{window.end.isoformat()} — everything this "
        "load holds — rather than a period you named. Say the period you want and I will re-run "
        "it."
    )


def _chart_sorts_from_trace(raw: Any) -> tuple[tuple[str, str, bool], ...]:
    """The orderings a recorded plan resolved, read back off its trace."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    out: list[tuple[str, str, bool]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        frame_id, by = entry.get("frame_id"), entry.get("by")
        if isinstance(frame_id, str) and isinstance(by, str):
            out.append((frame_id, by, bool(entry.get("descending", True))))
    return tuple(out)


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


def _bounds_by_window(
    plan: InvestigationPlan,
    executed: Sequence[ExecutedProbe],
    spec: AnalysisSpec | None,
) -> tuple[tuple[BoundedCell, ...], tuple[BoundedCell, ...]]:
    """Split this turn's ceilings into ``(this window, the one compared)``.

    Round-9 R9-06. The disclosure was composed from every probe the plan
    ran, so a comparison turn published the prior window's ceilings inside
    the current window's census: five rows under a count of four, with
    Veritas Comp Fund named twice — once at ≤4.7% over 214 (July) and once
    at ≤9.0% over 111 (June, the figure the answer had already quoted as
    the prior month).

    The probe knows its own window (:func:`frame_window`), so this asks it
    rather than guessing from node order. A probe whose window this spec
    does not name is context, not answer; a plan with no windows at all —
    every probe a snapshot — is all current, which is what an as-of answer
    is.
    """
    current: list[BoundedCell] = []
    prior: list[BoundedCell] = []
    asked = spec.context.window.range if spec is not None else None
    for item in executed:
        if not item.bounded_cells:
            continue
        window = frame_window(plan, item.node_id)
        if asked is not None and window is not None and window.range != asked:
            prior.extend(item.bounded_cells)
        else:
            current.extend(item.bounded_cells)
    return tuple(current), tuple(prior)


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


#: A reply that opens like this and matches no offered option is a new
#: question, not an answer to the one on screen. Deliberately narrow —
#: fragments ("just imaging", "the last full month", "denied dollars") do
#: not match, and those are what a real clarification answer looks like.
_FRESH_QUESTION = re.compile(
    r"^\s*(?:what|which|who|why|how|when|where|show me|give me|list|compare|break\s+down|"
    r"tell me)\b",
    re.IGNORECASE,
)


def _answers_pending(reply: str, pending: PendingClarification) -> bool:
    """Does this utterance answer the question that is on screen?

    True whenever it matches an option (verbatim or by binding), or when it
    reads as a fragment — the shape of every genuine clarification answer.
    False only for a self-contained question that matches nothing offered,
    which is the case that used to be swallowed under a false disclosure.
    """
    text = reply.strip()
    if not text:
        return True
    if pending.binding_for(text) is not None:
        return True
    folded = " ".join(text.split()).casefold().rstrip(".")
    for option in pending.options:
        candidate = " ".join(option.split()).casefold().rstrip(".")
        if folded == candidate or folded in candidate or candidate in folded:
            return True
    return _FRESH_QUESTION.match(text) is None


#: Marks a clarification the analyst cannot tap their way out of, so the
#: client renders it as a statement of what the platform needs rather than
#: as a question above an empty row of buttons (round-4 R4-12 defect 1).
NO_OPTIONS_REASON = "CLARIFICATION_NO_OPTIONS"

#: Words that carry no identity in a reply to "which of these did you mean?"
#: — function words, and the counting/superlative words that describe a
#: choice without naming one ("the two biggest commercial ones").
_UNSELECTIVE_WORDS = frozenset(
    {
        "all", "and", "any", "are", "both", "but", "each", "for", "from", "how",
        "its", "just", "largest", "least", "biggest", "bigger", "smallest",
        "smaller", "most", "much", "many", "one", "ones", "only", "other", "our",
        "out", "please", "rest", "same", "several", "some", "that", "the", "their",
        "them", "then", "these", "they", "this", "those", "three", "top", "two",
        "want", "was", "were", "what", "which", "with", "you", "your", "four",
        "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    }
)


def _identifying_words(text: str) -> frozenset[str]:
    return frozenset(
        word
        for word in re.findall(r"[A-Za-z]{3,}", text.casefold())
        if word not in _UNSELECTIVE_WORDS
    )


def options_named(reply: str, options: Sequence[str]) -> tuple[str, ...]:
    """Which of the offered options the analyst's reply actually names.

    Round-6 A-04. The value-existence refusal is the best thing this
    platform does — "there is no payer named 'UnitedHealthcare' in this
    data", all twelve real values enumerated — and answering it with
    anything but a verbatim value replayed it BYTE-IDENTICALLY: same
    question, same twelve options, same reason, for a reply ("the two
    biggest commercial ones") that had narrowed the twelve to two.

    Word overlap only, and only on words that identify: a reply naming a
    count or a superlative names no value, and this must never invent one.
    The BEST overlap wins outright — "federal medicare" names 'Federal
    Medicare' and not also 'Summit Peak Medicare Advantage', which shares
    one word with it and none of the ones that pick it out.
    """
    words = _identifying_words(reply)
    if not words:
        return ()
    scored = [(len(words & _identifying_words(option)), option) for option in options]
    best = max((score for score, _ in scored), default=0)
    if best == 0:
        return ()
    return tuple(option for score, option in scored if score == best)


def _subject_option(parent: Investigation | None) -> ClarificationBinding | None:
    """The thread's own subject, as an option that can be applied.

    A ``metric_cut`` over ids this platform published a moment ago:
    deterministic, dry-runnable, and the one recovery that is guaranteed
    to be on-subject. ``None`` when the session has no analytical answer to
    point at — there is nothing to commit to, and §2.8 forbids guessing one.
    """
    if parent is None or not parent.spec.measures:
        return None
    metrics = tuple(ref.id for ref in parent.spec.measures)
    cuts = tuple(ref.id for ref in parent.spec.dimensions)
    label = (
        "Answer it for "
        + ", ".join(metrics)
        + (" by " + ", ".join(cuts) if cuts else "")
        + " — the answer already on screen"
    )
    return ClarificationBinding(
        option=label, kind="metric_cut", metric_ids=metrics, dimension_ids=cuts
    )


def _no_options_card(clarification: ClarificationRequest) -> ClarificationRequest:
    """Label a clarification that offers nothing to choose from.

    A one-option clarification never reaches here — it is applied, not
    asked (see ``_lone_binding``) — so "fewer than two" means zero, and
    zero is an error card: the question keeps its text, and the reason
    carries the marker a renderer keys the card shape off. Two independent
    sessions reached the page with ``options: []`` and no buttons at all,
    one of them after $0.10 spent to deny a capability.
    """
    if clarification.options:
        return clarification
    reason = clarification.reason or ""
    if NO_OPTIONS_REASON in reason:
        return clarification
    # Appended, never prefixed: the reason's own opening code is what every
    # other reader keys off, and moving it would break them to label this.
    return replace(
        clarification,
        reason=f"{reason}; {NO_OPTIONS_REASON}" if reason else NO_OPTIONS_REASON,
    )


#: Marks a question that was ASKED AGAIN because the reply could not be
#: matched — narrowed where the reply narrowed it, and named as a repeat
#: either way, so the second ask is never mistaken for the first.
CLARIFICATION_REPEATED_REASON = "CLARIFICATION_REPEATED"

#: Marks a clarification whose option set the DATA reduced to one. The
#: option is stated and offered; it is never selected on the analyst's
#: behalf (round-9 R9-02).
CLARIFICATION_SOLE_SURVIVOR_REASON = "CLARIFICATION_SOLE_SURVIVOR"

#: Markers for the two places this ENGINE authors the single option rather
#: than being left with it. A commitment to the subject already on screen is
#: not a survivor of a cull — nothing was dropped to reach it — so the
#: R9-02 "state, never select" rule does not apply to it, and applying it is
#: exactly what stops "why did it go up" going round again (R9-07).
CLARIFICATION_CONVERGED_REASON = "CLARIFICATION_CONVERGED"
CLARIFICATION_MEASURE_SETTLED_REASON = "CLARIFICATION_MEASURE_SETTLED"
_COMMITTED_REASONS = (CLARIFICATION_CONVERGED_REASON, CLARIFICATION_MEASURE_SETTLED_REASON)

#: A clarification asking the analyst to name the measure. Narrow on
#: purpose: it fires on the question this engine composes for that, never
#: on a question that merely contains the word "metric" (R9-07).
_ASKS_WHICH_MEASURE = re.compile(
    r"\bwhich\s+(?:\w+\s+){0,2}?(?:metric|measure|figure|number)\b"
    r"|\bwhat\s+(?:metric|measure)\s+(?:are|do|did|would)\b",
    re.IGNORECASE,
)


def _state_the_survivor(
    clarification: ClarificationRequest, lone: ClarificationBinding
) -> ClarificationRequest:
    """Say that one option survived, without answering as if it were chosen.

    Round-9 R9-02. The refusal keeps the lead — it is the first thing in the
    question text and the first thing the analyst reads — and the surviving
    option is named as what this warehouse could answer INSTEAD, with the
    difference said out loud. What the previous behaviour published was the
    survivor's answer under the original question's heading, with the
    refusal moved into a warning; a reader who trusted the heading was
    reading the wrong number.
    """
    reason = clarification.reason or ""
    return replace(
        clarification,
        question=(
            f"{clarification.question} Only one of the options I could offer survives what "
            f"this data holds at this watermark: “{lone.option}”. That answers less than you "
            "asked for, so I have not run it on your behalf — say it and I will, or say what "
            "you want in your own words."
        ),
        reason=(
            f"{reason}; {CLARIFICATION_SOLE_SURVIVOR_REASON}: one option left after value and "
            "plan validation; stated rather than applied"
            if reason
            else f"{CLARIFICATION_SOLE_SURVIVOR_REASON}: one option left after validation"
        ),
    )


def _no_replay(
    state: _TurnState, clarification: ClarificationRequest, narrowed: Sequence[str]
) -> ClarificationRequest:
    """The same impasse, said differently — never the same words twice.

    Round-6 A-04. Two outcomes, both honest and neither a replay: when the
    reply narrowed the offered set, the question narrows with it and says
    what it read; when it narrowed nothing, the question stops being a
    question about values and becomes a plain statement that the reply
    could not be matched to any of them.
    """
    pending = state.pending
    asked = pending.streak + 1 if pending is not None else 2
    reply = state.utterance or state.question
    if narrowed and len(narrowed) < len(clarification.options):
        return replace(
            clarification,
            question=(
                f"I read {reply!r} as pointing at "
                f"{', '.join(repr(option) for option in narrowed)} — that is as far as I can "
                "narrow it without guessing which you meant. Name the one you want."
            ),
            options=tuple(narrowed),
            bindings=_bindings_for(clarification, tuple(narrowed)),
            reason=(
                f"{clarification.reason}; {CLARIFICATION_REPEATED_REASON}: ask {asked} "
                f"narrowed {len(clarification.options)} option(s) to {len(narrowed)} from the "
                "reply"
            ),
        )
    return replace(
        clarification,
        question=(
            f"I could not match {reply!r} to any of the "
            f"{len(clarification.options)} value(s) I offered, and asking you the same "
            "question a second time would not change that. Name one of them exactly — or "
            "ask me which of them is largest and I will measure it, which is a question I "
            "can answer."
        ),
        reason=(
            f"{clarification.reason}; {CLARIFICATION_REPEATED_REASON}: ask {asked} would have "
            "repeated ask 1 verbatim; the reply matched none of the offered values"
        ),
    )


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
    #: What the analyst actually typed, before any resume joined the
    #: interrupted question onto it. ``question`` is the RESOLVED utterance
    #: the pipeline runs; this is the one to quote back at them and the one
    #: to match against options they were offered (round-6 A-04).
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
        #: The load's settling curve (round-6 E-01). Optional so a test
        #: harness with no warehouse still builds an engine; a deployment
        #: without it simply makes no maturity claim about a window.
        window_maturity: WindowMaturityService | None = None,
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
        self._window_maturity = window_maturity

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
            utterance=request.question,
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

        # "Show me all twelve" is a statement about DISPLAY SCOPE over a
        # frame this platform is already holding, and it is decidable
        # without a model (round-4 R4-11: 6 of 6 personas, four distinct
        # failure modes, zero successes — the classifier returned 0.45-0.50
        # and the turn ended in a clarification asking whether the twelve
        # had already been computed, which the engine could answer itself).
        # Read here, before classification, so the expansion costs nothing
        # and cannot be lost to a confidence threshold.
        widened = display_scope_limit(state.question)
        if widened is not None:
            parent = await self._latest_investigation(session, analytical=True)
            if parent is not None and widened > len(parent.findings):
                state.time_stage("classify")
                return await self._refinement_turn(
                    session,
                    state,
                    None,
                    dto_ops=(ExpandModel(op="expand", limit=widened),),
                )

        exhausted = self._budget_stop(state, "reading your question")
        if exhausted is not None:
            return await self._clarification_outcome(session, state, None, exhausted)

        # What (if anything) this session already asked and has not had
        # answered. Classification without it cannot tell an answer from a
        # fresh question — see PendingClarification.
        pending = await self._pending_clarification(session, state.question)
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

        # "Sort them by percent change, largest first" is a statement about
        # the ORDER of rows this platform is already holding, and it is
        # decidable without a model for the same reason a display-scope
        # request is (round-6 A-03: the classifier returned presentation_only
        # at 0.76 and 0.68 — below its threshold — so the utterance
        # ``refinement_not_applied`` was written for diverted into a
        # clarification asking whether percent change was already a column,
        # which the engine can answer from the findings it published).
        #
        # Never while a question of ours is on screen: with a clarification
        # outstanding the same words are an ANSWER to it, and reading them
        # as a fresh instruction would drop the dialogue this platform
        # started.
        if pending is None and presentation_order_request(state.question):
            ordered_parent = await self._latest_investigation(session, analytical=True)
            if ordered_parent is not None and ordered_parent.findings:
                state.time_stage("classify")
                return await self._presentation_turn(session, state, None, ordered_parent)

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
            # One question of ours on screen at a time (round-8 FIX-12(d)).
            # Live: the analyst left "Who is my worst payer?" unanswered and
            # asked their next real question, "Show me AR aging" — and got
            # "Is this answering the 'worst payer' question by picking the
            # days-in-A/R measure, or a new A/R aging request?", with "Drop
            # the worst payer question entirely" among the options. Asking a
            # reader to adjudicate the relationship between their own two
            # sentences is not a clarification; it is the platform's
            # bookkeeping handed over as a question. A self-contained
            # question supersedes whatever was pending, and says so.
            # …after the convergence rule, which describes the same move
            # more precisely when it applies: "I asked twice and did not
            # converge" is a better sentence than "you asked something
            # else", and it names the question being answered.
            committed = self._commit_instead_of_clarifying(
                state, pending
            ) or self._supersede_pending(state, pending)
            if committed is None:
                clarification = classified.clarification or ClarificationRequest(
                    question="Could you rephrase that?", reason="unclassifiable turn"
                )
                return await self._clarification_outcome(
                    session, state, classified, clarification
                )
            # §2.8 convergence: stop asking, commit, and say what was
            # assumed. Parented on the question that was dropped, so the
            # lineage shows what this turn replaced.
            state.assumptions.append(committed)
            if pending is not None and state.lineage_parent is None:
                state.lineage_parent = pending.investigation_id
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
        # Every class in the §7.3 taxonomy is dispatched above, so there is
        # nothing left to fall through to. This used to be the "that reads
        # like an answer to a question I haven't asked" clarification, which
        # was CLARIFICATION_RESPONSE's fallthrough before that class got the
        # branch above it (the sentence still lives on the path where it is
        # TRUE — a clarification response with nothing pending). Leaving it
        # here would tell an analyst their question read as an answer when
        # what actually happened is that the taxonomy grew a class the
        # engine does not route; exhaustiveness says so instead, and says it
        # at type-check time.
        assert_never(turn_class)

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
        # …but WHICH pipeline resumes is decided by the turn that asked, not
        # by the default (round-6 A-01). A clarification raised on a
        # re-presentation is answered by re-presenting: replanning it as a
        # first turn is how a twelve-row answer came back as three, in the
        # engine's default order, narrated as a new investigation with
        # ``reason=this is a first turn``.
        presented = await self._presentation_resume(session, state, classified, pending, binding)
        if presented is not None:
            return presented
        # A handle this platform minted is an identifier, never a dimension
        # value — on every path, including this one (round-6 E-03).
        claimed = await self._referent_resume(session, state, classified, pending)
        if claimed is not None:
            return claimed
        if binding is not None and pending.original_question:
            state.question = pending.original_question
            state.lineage_parent = pending.investigation_id
            state.assumptions.append(
                f"Read as an answer to the question above: {pending.question!r} → "
                f"{binding.option!r}. Resuming {pending.original_question!r} with that "
                "applied; this answer is recorded as a child of the turn that asked."
            )
            return await self._apply_binding(session, state, classified, binding)
        # A reply that answers nothing is not an answer (round-4 R4-12
        # defect 6b): rcm-analyst's genuinely new question was swallowed as
        # a clarification response and spliced onto the abandoned one under
        # a CLARIFICATION_ANSWER_APPLIED disclosure that was simply false.
        # A self-contained question that matches no option we offered is
        # run as itself, and the dropped clarification is disclosed as
        # dropped rather than as applied.
        if not _answers_pending(state.question, pending):
            state.assumptions.append(
                f"Assumed: this is a new question, not an answer to {pending.question!r} — it "
                "matches none of the options that question offered and stands on its own. "
                "That question is left unanswered; ask it again if you still want it."
            )
            state.lineage_parent = pending.investigation_id
            return await self._new_investigation_turn(session, state, classified)
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

    async def _presentation_resume(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        pending: PendingClarification,
        binding: ClarificationBinding | None,
    ) -> TurnOutcome | None:
        """Resume a clarification that was raised on a RE-PRESENTATION.

        Round-6 A-01, a regression the round-5 close chain found in one
        move. "Show me all twelve" published twelve payer rows; "sort them
        by percent change, largest first" came back as a clarification; the
        analyst answered it with its own first option — and the resume ran
        ``_new_investigation_turn``, which re-planned the sentence from
        scratch. Three findings, the engine's own order, ``reconciliation:
        this is a first turn``, and a lineage in which a
        ``new_investigation`` hangs off a ``presentation_only``. The twelve
        rows the analyst was looking at were simply gone.

        A clarification is an interruption, and what it interrupts decides
        what resumes. When the turn that ASKED was a re-presentation, the
        answer to it is a re-presentation of the same served set — the
        analytical answer on screen when the question was asked — with the
        presentation op applied to THAT, never to a freshly planned one.

        ``None`` when this clarification did not come from a presentation
        turn, or when there is no served set left to re-present, in which
        case the ordinary resume paths stand.
        """
        asking = await self._investigations.get(pending.investigation_id or "")
        if asking is None or asking.turn_class is not TurnClass.PRESENTATION_ONLY:
            return None
        if not _answers_pending(state.question, pending):
            return None  # a new question is a new question, whoever asked last
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None or not parent.findings:
            return None
        answer = binding.option if binding is not None else state.question
        state.question = _join_question_and_answer(pending.original_question, answer)
        state.lineage_parent = pending.investigation_id
        state.assumptions.append(
            f"Read as an answer to the question above: {pending.question!r} → {answer!r}. "
            f"That question was asked about a re-presentation, so this answer re-serves the "
            f"{len(parent.findings)} row(s) the turn above it published, with your request "
            "applied to them — nothing was re-planned and nothing was re-measured."
        )
        return await self._presentation_turn(session, state, classified, parent)

    async def _referent_resume(
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        pending: PendingClarification,
    ) -> TurnOutcome | None:
        """A reply that names a HANDLE is a referent answer, never a value.

        Round-6 E-03, the threaded-drill dead end. The platform asked "By
        'that', do you mean F1 (finding)?" — optionless, over a handle it
        had minted itself — and the analyst answered in its own words:
        "Yes, F1 — Summit Peak Medicare Advantage". That reply was joined
        onto the original sentence and re-interpreted as a NEW
        investigation, where ``F1`` was read as a dimension value and the
        §6.6 value-existence guard refused it: *"There is no facility named
        'F1' in this data"*. Excellent machinery pointed at the platform's
        own identifier, and the payer name supplied beside it dropped.

        §7.6 already says a handle is resolved by lookup and not by
        language. This is that rule applied one turn earlier than the
        refinement path: a reply carrying a handle re-enters the REFINEMENT
        pipeline, where ``resolve_referent_tokens`` claims the token at
        confidence 1.0 before any planner or validator sees it.
        """
        tokens = referent_tokens(state.question)
        if not tokens:
            return None
        entries = await self._referents.list_for_session(session.id)
        if not any(entry.referent.value in tokens for entry in entries):
            return None
        if await self._latest_investigation(session, analytical=True) is None:
            return None
        state.question = _join_question_and_answer(pending.original_question, state.question)
        state.lineage_parent = pending.investigation_id
        state.assumptions.append(
            f"Read as an answer to the question above: {pending.question!r} → "
            f"{', '.join(tokens)}. Those are handles this session published, so they are "
            "resolved against what was shown rather than read as values in the data, and "
            f"{pending.original_question!r} is resumed against them."
        )
        return await self._refinement_turn(session, state, classified, dto_ops=None)

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
        * everything else re-interprets the same words too, with the
          option's own ids PINNED over whatever the second reading proposes.

        Round-5 A-01, the highest-conviction finding of the round: two
        adversaries, four sessions, one line. The third branch used to skip
        interpretation entirely and run ``_spec_for_binding``'s dry-run
        spec, whose window is the three-year *value-existence* window and
        whose scope is the option's alone. So "how many dollars did we lose
        to denials in July 2026?" → the platform's own clarification → the
        platform's own offered option came back as **$25,929,558.84 over
        2023-01-01..2026-08-02** — the whole warehouse, at high confidence,
        under a disclosure asserting the July question had been resumed.
        Same line, second symptom: "break that down by CARC code." on a
        payer/service-line/July thread came back as a ROOT investigation
        with no filters, no cohort and no parent.

        The two branches beside it were always right, and they are right
        for the same reason: they re-read the analyst's own sentence. So
        does this one now — the option contributes IDS (measures, cuts,
        values), and the sentence contributes everything else. What the
        sentence does not state is carried from the answer this resume
        continues rather than defaulted (see ``_with_resumed_context``), and
        the typed spec survives as the fallback for the case where the
        second reading cannot resolve the question either.
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
        # The option is the analyst's answer, so it joins their question:
        # both halves are their own words and the join is the same
        # deterministic one the typed-reply path uses.
        state.question = _join_question_and_answer(state.question, binding.option)
        resumed = await self._resumed_context(session, state)
        return await self._new_investigation_turn(
            session, state, classified, binding=binding, resume=resumed
        )

    async def _resumed_context(
        self, session: Session, state: _TurnState
    ) -> AnalysisSpec | None:
        """The context a clarification resume continues, if there is one.

        A clarification interrupts a THREAD, and the thread's window,
        filters, comparison and cohort are the analyst's, not the
        platform's. They are read off the session's latest analytical
        answer — the one on screen when the question was asked — and
        applied only where the resumed sentence states nothing itself.
        """
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            return None
        if state.lineage_parent is None:
            state.lineage_parent = parent.id
        return parent.spec

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
        self, session: Session, reply: str | None = None
    ) -> PendingClarification | None:
        """The clarification this session is still waiting on, if any.

        Read back off the lineage rather than held in memory: a turn is a
        stateless request, and the session may be resumed in another
        process. The streak counts *consecutive* clarification turns at the
        tail, which is what §2.8 convergence is measured in.

        ``reply`` is the analyst's current utterance, used only to pick
        BETWEEN outstanding clarifications when more than one is open —
        never to decide whether one is open at all.
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
        # The oldest turn in the run is the analyst's actual question; the
        # ones after it are their replies to us.
        oldest = streak[-1]
        # With more than one clarification outstanding, "which question is
        # this answering" is decided by the OPTIONS, not by recency: round-4
        # R4-12 defect 6 found a reply spliced onto the question of the
        # OLDEST and parented to the NEWEST, in one self-contradicting
        # disclosure sentence. The question text, the option set, the
        # bindings and the parent pointer must all come from a single
        # clarification id — and when the reply is one of the options this
        # platform offered, that id is the one that offered it.
        candidates: list[PendingClarification] = []
        for investigation in streak:
            question, options, bindings = await self._recorded_clarification(investigation.id)
            if question is None:
                continue
            candidates.append(
                PendingClarification(
                    question=question,
                    options=options,
                    original_question=oldest.question,
                    streak=len(streak),
                    investigation_id=investigation.id,
                    bindings=bindings,
                )
            )
        if not candidates:
            return None
        if reply is not None:
            matched = next(
                (c for c in candidates if c.binding_for(reply) is not None), None
            ) or next(
                (c for c in candidates if reply.strip() in c.options), None
            )
            if matched is not None:
                return matched
        return candidates[0]

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
    def _repeats_pending(state: _TurnState, clarification: ClarificationRequest) -> bool:
        """Is this the question already on screen, word for word?

        The strictest possible test, because it is the one the analyst
        applies: same text, same options. Anything the funnel has already
        changed — a dropped refuted value, a narrowed subject — is a
        different question and gets asked.
        """
        pending = state.pending
        if pending is None:
            return False
        return clarification.question.strip() == pending.question.strip() and tuple(
            clarification.options
        ) == tuple(pending.options)

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
    def _supersede_pending(
        state: _TurnState, pending: PendingClarification | None
    ) -> str | None:
        """Drop the pending question rather than ask a second one (FIX-12(d)).

        Fires only where the analyst has plainly moved on: a clarification
        is on screen, the new utterance is a self-contained question that
        answers none of its options (``_answers_pending``), and this turn
        was about to raise ANOTHER clarification on top of it. Then the
        honest move is not a third question about which question we are
        answering — it is to answer the one they just asked and say the
        other was dropped.

        Returns the sentence to publish, or ``None`` to clarify as usual.
        A turn with nothing pending is untouched, and so is a genuine
        clarification answer: superseding those would throw away the reply
        the platform asked for.
        """
        if pending is None or _answers_pending(state.question, pending):
            return None
        return (
            f"Assumed: this is a new question and it replaces the one I had open. I asked "
            f"{pending.question!r} and you asked something else, so I dropped my question "
            "rather than ask a second one about which of the two we are doing — ask it "
            "again whenever you want it, and it will run as its own turn."
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
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        *,
        binding: ClarificationBinding | None = None,
        resume: AnalysisSpec | None = None,
    ) -> TurnOutcome:
        """Interpret the analyst's sentence and run it (§8.1).

        ``binding`` and ``resume`` are set only on a clarification resume.
        The binding's ids are pinned over the second reading — the analyst
        chose a thing this platform named and that choice is not re-decided
        by a model — and ``resume`` is the context of the answer the
        clarification interrupted, applied only where the sentence itself
        states nothing (round-5 A-01).

        ``classified`` is ``None`` on the turns this engine decides WITHOUT
        a model — the deterministic display-limit expansion, a budget stop
        taken before classification — and those turns reach here through
        ``_apply_binding``. It is threaded, never invented: the trace and
        the persisted node both record "no classification" rather than a
        fabricated one (see ``_trace_record``/``_minimal_investigation``).
        """
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

        interpreted: InterpretedInvestigation | None = None
        prelude: list[str] = []
        if interpretation.clarification is not None:
            # A resume that cannot be re-read is still a resume: the option
            # the analyst tapped carries ids, and running them is better
            # than asking the same question a second time. Only reachable
            # on the clarification-resume path, where the alternative is a
            # loop the analyst has already answered once.
            typed = (
                self._spec_for_binding(session, binding) if binding is not None else None
            )
            if typed is None:
                return await self._clarification_outcome(
                    session,
                    state,
                    classified,
                    self._bounded_clarification(state, interpretation.clarification),
                    interpretation,
                )
            spec = typed
            playbook_id = binding.playbook_id if binding is not None else None
            window_explicit = False
            prelude.append(_option_window_assumed(spec))
        elif interpretation.definitional is not None:
            return await self._definitional_outcome(
                session, state, classified, answer=interpretation.definitional
            )
        else:
            assert interpretation.investigation is not None
            interpreted = interpretation.investigation
            # The analyst's choice is ids this platform published; a second
            # reading of their sentence does not get to overrule it — and
            # that includes a playbook the option named, which is the only
            # thing a playbook-only option carries.
            spec = _with_binding(interpreted.spec, binding)
            playbook_id = interpreted.playbook_id
            if binding is not None and binding.playbook_id is not None:
                playbook_id = binding.playbook_id
            window_explicit = interpreted.window_explicit

        # What the resumed sentence did not state is carried from the answer
        # it continues, never defaulted to the widest window the load allows.
        spec, window_explicit, carried = _with_resumed_context(spec, resume, window_explicit)
        prelude.extend(carried)

        # carryover law 5: session pins persist until explicitly cleared
        pins = await self._inherited_pins(session)
        spec = _with_chosen_values(spec, state.scope_override)
        if pins:
            spec = spec.with_context(replace(spec.context, pins=pins))
        # A handle this platform minted must never reach the value-existence
        # guard as a dimension value (round-6 E-03). Gated on the pure test
        # so the registry read is paid only by a turn that could use it.
        if scope_names_a_handle(spec):
            spec, claimed = claim_referent_predicates(
                spec, await self._referents.list_for_session(session.id)
            )
            prelude.extend(claimed)

        return await self._run_analysis(
            session,
            state,
            classified,
            spec=spec,
            playbook_id=playbook_id,
            window_explicit=window_explicit,
            turn_class=TurnClass.NEW_INVESTIGATION,
            parent=None,
            operators=(),
            interpreted=interpreted,
            prelude_warnings=tuple(prelude),
            trace_extra=(
                {
                    "clarification_binding": {
                        "option": binding.option,
                        "kind": binding.kind,
                        "metric_ids": list(binding.metric_ids),
                        "dimension_ids": list(binding.dimension_ids),
                        "resumed_investigation_id": state.lineage_parent,
                        "resumed_window": (
                            f"{spec.context.window.range.start.isoformat()}.."
                            f"{spec.context.window.range.end.isoformat()}"
                        ),
                    }
                }
                if binding is not None
                else None
            ),
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

        # "Show me all twelve" is a COUNT the analyst named, and a count the
        # analyst names is an instruction — on every path, not only on the
        # one that opens an investigation (round-4 R4-11: 6 of 6 personas,
        # four distinct failure modes, zero successes).
        # ``requested_finding_limit`` had exactly one production call site,
        # inside the new-investigation spec build, so a follow-up asking for
        # the rest of a truncated list could not reach it: the model was
        # left to emit an Expand operator, and when it did the kernel path
        # handed back the parent's own three findings. Resolving it here is
        # deterministic and free — no model call decides how many rows the
        # analyst asked to see.
        typed_limit = requested_finding_limit(state.question)
        if dto_ops is None and typed_limit is not None and typed_limit > len(parent.findings):
            dto_ops = (ExpandModel(op="expand", limit=typed_limit),)
            rationale = (
                f"the question asks for {typed_limit} rows and the previous answer published "
                f"{len(parent.findings)}"
            )

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
                session,
                state,
                classified,
                parent,
                new_spec,
                domain_ops,
                ops_json,
                parent_plan_context,
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
        # Every window this plan READS, judged against the load's settling
        # curve before any verdict is composed over one (round-8 FIX-9(3)).
        # A playbook probe declares its own window, so the premise verdict
        # was being computed over a period the spec never named and the
        # maturity guard — which read the spec — never saw it.
        maturity_by_window = await self._plan_window_maturity(validated.plan, spec)
        findings_result = await self._evaluator.evaluate(
            plan=validated.plan,
            calculation=calculation,
            spec=spec,
            pack=self._pack,
            playbook=playbook,
            session_id=session.id,
            investigation_id=state.investigation_id,
            window_maturity=maturity_by_window,
            # The §15 threshold the frames were policed under: a bound is
            # only recognisable against the threshold that produced it, and
            # without it findings publish `(threshold - 1) / n` as a
            # measured value (round-3 R3-01).
            suppression_threshold=self._executor.suppression_threshold,
        )
        state.time_stage("findings")

        extra_warnings: list[str] = list(prelude_warnings)
        # A refinement that produced the parent's own plan changed nothing
        # the evidence can see (round-4 R4-04): product-designer's a3
        # published an identical ``plan_hash`` as a NEW investigation, with
        # a new referent and 1,500 fresh words of narrative, and no
        # disclosure that the grain the question asked for had been dropped.
        # The answer is legitimate; presenting it as a different one is not.
        #
        # Round-5 E-01: the note is owed to the HASH, not to a branch. The
        # reuse disclosure lived only in the kernel-only path, which returns
        # None the moment an Expand asks for more rows than the parent
        # published — so "show me all twelve" against a three-finding parent
        # never reached it, and neither string fired on any live session.
        # Which of the two notes applies is decided by what the reader can
        # see: a re-served plan whose FINDINGS changed did apply the
        # operator (the analyst asked for more rows and got them), and one
        # whose findings are byte-identical did not.
        if parent is not None and operators and validated.plan.plan_hash == parent.plan_hash:
            if _same_findings(findings_result.findings, parent.findings):
                extra_warnings.append(
                    "refinement_not_applied: what you asked to change does not alter this plan "
                    f"({parent.plan_hash}) — the operator(s) "
                    + ", ".join(sorted({type(op).__name__ for op in operators}))
                    + " left the evidence identical to the previous answer, so these are the "
                    "same numbers re-measured, not a new result. Say what to change about the "
                    "metric, the cut or the window and I will re-run it."
                )
            else:
                extra_warnings.append(
                    "refinement_reused_plan: this answer re-serves the previous turn's plan "
                    f"({parent.plan_hash}) — the same evidence and every caveat that came with "
                    "it. What changed is how much of it is published: "
                    + ", ".join(sorted({type(op).__name__ for op in operators}))
                    + f" took the published set from {len(parent.findings)} finding(s) to "
                    f"{len(findings_result.findings)}."
                )
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
        # …and composed from the CURRENT window's cells (R9-06). A plan that
        # compares reads two windows, and folding the prior window's
        # ceilings into one list put the same payer in the disclosure twice
        # — the second row being the figure quoted three sentences earlier
        # as the prior month — under a count that said four over a list of
        # five.
        current_bounds, prior_bounds = _bounds_by_window(validated.plan, executed, spec)
        bounds = bounded_cells_warning(
            current_bounds,
            self._executor.suppression_threshold,
            census=census,
            comparison_cells=prior_bounds,
            # The reader's own word for these rows, so this sentence and the
            # ranking's own ("4 of 12 payers…") cannot describe one set of
            # cells with two nouns.
            noun=row_noun(tuple(ref.id for ref in spec.dimensions) if spec else ()),
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
        # A cell whose prior side was never retrieved publishes UNKNOWN
        # rather than $0.00, and says so (R4-08).
        extra_warnings.extend(calculation.warnings)
        # …and the data-maturity guard the trend path has always had,
        # applied to the axis it could not see: two windows settled to
        # different degrees are not comparable at high confidence (R4-07).
        findings_result = self._guard_comparison_maturity(
            findings_result, calculation, extra_warnings
        )
        # …and to the leg neither of those can measure: a metric whose own
        # contract declares two windows non-comparable (FN-4). The panel
        # rule keys on adjudicated-record counts and is structurally blind
        # to a metric whose denominator is dollars.
        findings_result = self._guard_declared_comparability(
            findings_result, calculation, extra_warnings
        )
        # …and the same guard applied to the shape neither the series rule
        # nor the comparison rule can see: ONE window, one number (E-01).
        findings_result = await self._guard_window_maturity(
            findings_result,
            spec,
            extra_warnings,
            state,
            maturity_by_window,
            window_explicit=window_explicit,
        )
        for detail in calculation.warnings:
            await self._events.publish(
                TurnEvent(
                    kind="warning",
                    turn_id=state.turn_id,
                    payload={"code": "COMPARISON_PRIOR_UNKNOWN", "detail": detail},
                )
            )
        reconciliation = (
            await self._reconcile_with_parent(
                parent, validated.plan, calculation, operators, extra_warnings, state, spec
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
        benchmarks = self._benchmarks_for(findings_result, warnings)
        header = build_context_header(
            spec,
            session,
            pack=self._pack,
            corrections=validated.corrections_map,
            # What the PLAN read, not only what the utterance named: a
            # playbook turn carries no spec.measures and its snapshot
            # contracts were rendering under a start..end window (R3-15).
            measure_ids=plan_measure_ids(validated.plan),
            findings=findings_result.findings,
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
            # The §15 cell arithmetic in full. It used to be appended to the
            # answer's own suppression sentence, so the paragraph a reader
            # most needs to parse stated its census twice — once in English
            # and once in the engine's. The page now says the count once, in
            # words; the partition is auditable here.
            "census": census.as_payload() if census is not None else None,
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
        clarification: ClarificationRequest, *, reduced: bool = False
    ) -> ClarificationBinding | None:
        """The single unambiguous choice a clarification leaves, if any.

        Exactly one option and exactly one binding, and one of two origins:

        * a binding this platform DERIVED from governed content — the
          original rule, and the ``timely_filing_at_risk_dollars`` basis
          case it was written for;
        * a set this platform REDUCED to one — round-4 R4-12 defect 5, where
          a dry-run dropped two of three model-authored options and the
          engine asked about the survivor anyway, at $0.146 and a turn. By
          then the survivor is not a model's suggestion: it is the option
          that came through value existence, plan validation and the
          subject check, i.e. content this platform has verified it can
          answer, and the alternatives are gone because this platform
          removed them.

        A model that authors a single option on its own is still asking a
        real question, and it is still asked.
        """
        if len(clarification.options) != 1 or len(clarification.bindings) != 1:
            return None
        binding = clarification.bindings[0]
        if binding.option not in clarification.options:
            return None
        return binding if (binding.deterministic or reduced) else None

    async def _converge_on_subject(
        self,
        session: Session,
        state: _TurnState,
        clarification: ClarificationRequest,
    ) -> ClarificationRequest:
        """Give a second consecutive optionless clarification a way out.

        Round-4 R4-12 defect 4: the documented "after two consecutive
        clarifications the engine commits" rule never fired, and a thread
        that had already spent $0.163 on two questions ended on a third
        with an empty options array. The §2.8 objection to committing —
        that it would mean inventing a metric — does not apply once a
        session HAS an answer on screen: its metric and its cut are
        governed content this platform published a moment ago, so
        committing to them invents nothing.

        The commitment is expressed as the one thing the funnel already
        knows how to apply: a single deterministic ``metric_cut`` binding
        over the subject's own ids, which the lone-option rule below then
        applies with the disclosure sentence that already exists.
        """
        if clarification.options or state.pending is None or state.pending.streak < 1:
            return clarification
        parent = await self._latest_investigation(session, analytical=True)
        option = _subject_option(parent)
        if option is None:
            return clarification
        return replace(
            clarification,
            options=(option.option,),
            bindings=(option,),
            reason=(
                f"{clarification.reason}; {CLARIFICATION_CONVERGED_REASON}: "
                f"{state.pending.streak + 1} consecutive clarifications with nothing to "
                "choose from, so this turn commits to the subject already on screen"
            ),
        )

    async def _grammatical_options(
        self, session: Session, clarification: ClarificationRequest
    ) -> ClarificationRequest:
        """Drop every option this engine's own grammar would refuse.

        Round-9 R9-07, three personas and one root cause. An option that
        carries a binding is dry-run against the planner
        (:meth:`_option_answerable`); an option that is only a SENTENCE —
        which is every option ``classify_turn`` and ``emit_refinements``
        propose — was published unchecked. So the platform offered "Yes —
        re-group the figure F1 result by denial reason" on a denial-rate
        thread, refused it with ``GRAIN_INCOMPATIBLE`` the moment it was
        tapped, and fired its own circuit breaker two turns later on the
        loop that made.

        The capability predicate is the validator's
        (:meth:`PlanValidationService.unexecutable_cut`) — the same
        ``allows_dimension`` check that produces the refusal — so an option
        cannot survive here and fail there. Options are only ever removed,
        and the set is never emptied by this rule: an imperfect suggestion
        beats a blank row of buttons, and the funnel's own
        "dropped everything" card exists for the case where it is right to
        show nothing.

        Round-10 R10-6 adds the second half of answerability. A cut is one
        way an option can dead-end; the other is the PLAYBOOK it routes to.
        "Who is my worst payer?" offered *"Run a full payer scorecard
        across all measures"*, which lands on ``payer_scorecard``, which
        answers by ``pivot``, which this engine refuses at plan time — so
        the first basics question of the demo handed the room a button the
        engine had already decided it could not press. That check needs no
        parent, which is why this method no longer returns early without
        one: a first-turn clarification is exactly where it fires.
        """
        if not clarification.options:
            return clarification
        parent = await self._latest_investigation(session, analytical=True)
        metrics = tuple(ref.id for ref in parent.spec.measures) if parent is not None else ()
        kept: list[str] = []
        refused: list[str] = []
        for option in clarification.options:
            cut = self._validator.unexecutable_cut(option, metrics)
            if cut is not None:
                refused.append(f"{cut[0]} is not a cut of {cut[1]}")
                continue
            playbook = self._validator.unanswerable_playbook(option)
            if playbook is not None:
                refused.append(
                    f"the {playbook[0]!r} playbook answers by {playbook[1]!r}, which this "
                    "engine does not implement"
                )
                continue
            kept.append(option)
        if not refused or not kept:
            return clarification
        surviving = tuple(kept)
        return replace(
            clarification,
            options=surviving,
            bindings=_bindings_for(clarification, surviving),
            reason=(
                f"{clarification.reason}; {len(refused)} option(s) dropped before offer: "
                f"{', '.join(refused)}"
            ),
        )

    async def _settled_measure(
        self, session: Session, clarification: ClarificationRequest
    ) -> ClarificationRequest:
        """Never ask which measure of a session that holds exactly one.

        Round-9 R9-07(a). "Why did it go up?" → clarification → the analyst
        clicked the platform's own option → a SECOND clarification asking
        *which metric are you asking about* in a session whose every answer
        had measured one thing over one window → the analyst clicked again
        → "We're going in circles". The question was answerable from what
        was on screen at every step.

        Where the session's subject is unambiguous, the measure question is
        replaced by the subject itself as a deterministic ``metric_cut``
        option — the same commitment :meth:`_converge_on_subject` makes,
        one turn earlier and without waiting for a streak.
        """
        if not _ASKS_WHICH_MEASURE.search(clarification.question):
            return clarification
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None or len(parent.spec.measures) != 1:
            return clarification
        option = _subject_option(parent)
        if option is None:  # pragma: no cover - a single measure always yields one
            return clarification
        return replace(
            clarification,
            question=(
                f"This session has measured one thing — {parent.spec.measures[0].id} over "
                f"{_period_phrase(parent.spec.context.window.range)} — so I have not asked "
                "you which. Say a different metric if you meant one."
            ),
            options=(option.option,),
            bindings=(option,),
            reason=(
                f"{clarification.reason}; {CLARIFICATION_MEASURE_SETTLED_REASON}: the session "
                "holds exactly one measure and one window, so which-measure is not a question"
            ),
        )

    async def _on_subject(
        self, session: Session, clarification: ClarificationRequest, question: str
    ) -> ClarificationRequest:
        """Drop options that walk away from what the session is about.

        Round-4 R4-12 defect 3, the one an analyst quits over: rcm-exec
        tapped this platform's own "Re-rank by value and show the top five"
        on a providers/denial-rate answer and got a THIRD clarification
        whose four options were about payers, denied dollars, underpayment
        variance and A/R balance — a different subject entirely, three
        turns into one question.

        The test is deliberately one-sided and cheap. It only ever removes,
        and only when the session HAS an established subject (a completed
        analytical turn). Two ways to be off it:

        * the option's governed METRICS are all different from the ones the
          subject measures — a denial-rate thread offered underpayment
          variance;
        * the option's governed CUT is all different from the subject's,
          and the analyst's own utterance never named it — the providers →
          payers swap, where the question said "re-rank by value" and the
          option answered about a dimension nobody mentioned.

        An option that names no governed content, or that re-cuts along
        something the analyst just asked for, is a legitimate narrowing and
        survives. Where every option is off-subject the whole set is kept
        rather than emptied: an off-subject suggestion beats a blank row of
        buttons only when there is nothing better to show.
        """
        if not clarification.bindings:
            return clarification
        parent = await self._latest_investigation(session, analytical=True)
        if parent is None:
            return clarification
        metrics = {ref.id for ref in parent.spec.measures}
        metrics.update(ref.id for finding in parent.findings for ref in finding.metric_refs)
        cuts = {ref.id for ref in parent.spec.dimensions}
        if not metrics and not cuts:
            return clarification
        asked = question.casefold()

        def on_subject(option: str) -> bool:
            binding = clarification.binding_for(option)
            if binding is None:
                return True
            if binding.metric_ids and metrics and not (set(binding.metric_ids) & metrics):
                return False
            if binding.dimension_ids and cuts and not (set(binding.dimension_ids) & cuts):
                # …unless the analyst named that cut themselves, in which
                # case changing subject is exactly what they asked for.
                return any(
                    part in asked
                    for dim in binding.dimension_ids
                    for part in (dim.replace("_", " "), dim.split("_")[-1])
                )
            return True

        kept = [option for option in clarification.options if on_subject(option)]
        if len(kept) == len(clarification.options):
            return clarification
        if not kept:
            # Every way forward walked away from the thread. Offering them
            # anyway is how rcm-exec got four payer options on a provider
            # question; offering nothing is a dead end. The subject itself
            # is the honest third answer.
            fallback = _subject_option(parent)
            if fallback is None:
                return clarification
            return replace(
                clarification,
                options=(fallback.option,),
                bindings=(fallback,),
                reason=(
                    f"{clarification.reason}; all {len(clarification.options)} option(s) "
                    "dropped: every one measured or cut by something this thread is not "
                    "about"
                ),
            )
        surviving = tuple(kept)
        return replace(
            clarification,
            options=surviving,
            bindings=_bindings_for(clarification, surviving),
            reason=(
                f"{clarification.reason}; "
                f"{len(clarification.options) - len(kept)} option(s) dropped: they measure "
                "something this thread is not about"
            ),
        )

    @staticmethod
    def _guard_comparison_maturity(
        findings: FindingsResult,
        calculation: CalculationResult,
        warnings: list[str],
    ) -> FindingsResult:
        """Apply the adjudication guard to COMPARISONS (round-4 R4-07).

        ``terminal_bucket_censoring`` needs a trend of three-plus buckets
        and runs inside the trend loop, so a two-window prior-period
        comparison — the shape a month-end close actually asks for — was
        never tested for data maturity at all. adonis compared a July that
        was 23% adjudicated against a June that was 91% and got a
        confident, un-caveated percentage; the only warning on the turn was
        about a one-day calendar difference.

        Two effects, both stated: ``ADJUDICATION_INCOMPLETE`` naming both
        panels and the share between them, and every finding on the turn
        dropped out of ``high`` confidence. Confidence is lowered, never
        raised — a finding already ``qualified`` for another reason keeps
        the stronger caveat.
        """
        verdicts = comparison_maturity(calculation.frames)
        if not verdicts:
            return findings
        warnings.extend(v.warning for v in verdicts)
        return _qualify_every_finding(findings)

    def _guard_declared_comparability(
        self,
        findings: FindingsResult,
        calculation: CalculationResult,
        warnings: list[str],
    ) -> FindingsResult:
        """Apply the adjudication guard to what the CONTRACT declares (FN-4).

        The two guards above are signals measured off the frame: a panel
        share, a settling curve. Both are structurally blind to a metric
        whose immaturity is not expressed as a record count — and
        ``net_collection_rate``'s denominator is contract-expected DOLLARS,
        so nothing fired while the pack's own caveat, published as a caution
        on the same payload, said in so many words that "two windows of
        unequal maturity are not comparable as levels".

        What shipped: "Premise confirmed: net collection rate 72.5% →
        18.5%, fell 53.9 points", ``grade: direct``, ``confidence: high``,
        stated three times on one answer — the finding title, the narrative
        lead and the ``PREMISE_VERIFIED`` warning — over a 53.9-point
        collapse that did not happen.

        So the declaration is read as an integrity input, exactly like the
        measured ones, and it has the same two effects: the reason is
        stated, and nothing on the turn survives at ``high``. Confidence is
        lowered, never raised.

        Scoped to metrics this turn actually DIFFERENCED. A caveat about
        comparing two windows says nothing about reading one, and demoting
        an uncompared level for it would be a caution that is not true.
        """
        verdicts = declared_non_comparabilities(self._pack, calculation.frames)
        if not verdicts:
            return findings
        warnings.extend(v.warning for v in verdicts)
        return _qualify_every_finding(findings)

    async def _plan_window_maturity(
        self, plan: InvestigationPlan, spec: AnalysisSpec
    ) -> dict[AbsoluteRange, WindowMaturity]:
        """The settling verdict for every window this plan actually reads.

        Round-8 FIX-9(3). The guard used to ask about ``spec.context.window``
        and nothing else, which is the window the header ANNOUNCES. A
        playbook probe declares its own — the denial_spike premise probe
        read 2026-06-08..2026-08-02 while the turn announced July against
        June — so the one window that mattered was the one nothing asked
        about, and "your denial spike did not happen" was published over a
        window three quarters of which had not come back.

        Costs one aggregation per (watermark, basis, entity, yardstick):
        the curve is cached inside the service and every window after the
        first is arithmetic over it.
        """
        if self._window_maturity is None:
            return {}
        windows: dict[AbsoluteRange, TimeWindow] = {spec.context.window.range: spec.context.window}
        if spec.context.comparison is not None:
            comparison = spec.context.comparison.window
            windows.setdefault(comparison.range, comparison)
        for node in plan.nodes:
            window = getattr(node.probe, "window", None)
            if isinstance(window, TimeWindow):
                windows.setdefault(window.range, window)
        # …and every whole month inside each of them. A window wider than a
        # month blends settled and settling data and passes as a blend: the
        # playbook's 2026-06-08..2026-08-02 holds a settled June, a July at
        # 26% and two days of August, and only the July inside it explains
        # a 39.5% "fall". The premise verdict reads these (see
        # ``verify_premise``); the turn-level guard above still keys on the
        # window the header announced.
        for window in tuple(windows.values()):
            for month in covered_months(window.range):
                windows.setdefault(month, replace(window, range=month, requested=None))
        out: dict[AbsoluteRange, WindowMaturity] = {}
        for window_range, window in windows.items():
            verdict = await self._window_maturity.verdict_for(
                window,
                grain=spec.context.grain.entity,
                watermark=spec.context.watermark,
            )
            if verdict is not None:
                out[window_range] = verdict
        return out

    async def _guard_window_maturity(
        self,
        findings: FindingsResult,
        spec: AnalysisSpec,
        warnings: list[str],
        state: _TurnState,
        maturity_by_window: Mapping[AbsoluteRange, WindowMaturity] | None = None,
        *,
        window_explicit: bool = True,
    ) -> FindingsResult:
        """Apply the adjudication guard to a SINGLE WINDOW (round-6 E-01).

        The series rule needs three buckets and a time axis; the comparison
        rule needs a prior panel. A month-end aggregate — "how many dollars
        did we lose to denials in July" — has neither, so the most common
        answer in the product was the one shape no maturity rule could
        reach. Two July denied-dollar figures for one payer landed 6.7x
        apart at ``confidence: high`` with nothing on either card about it.

        Silent where another guard has already spoken: a turn that carries
        ``adjudication_incomplete`` from its series or its comparison does
        not need a third sentence about the same fact.

        It is NOT silent on a comparison any more (round-8 FIX-9(3)). The
        panel rule owns that axis where it can see it — and it sees only a
        ratio's adjudicated denominator, so a comparison of an additive
        money measure, or one whose two sides are equally unsettled, left
        the turn with no maturity statement at all. That is the hole the
        playbook premise fell through. Where the panel rule spoke, the
        warning check above still stands this one down.
        """
        if self._window_maturity is None or not findings.findings:
            return findings
        if any(w.startswith("adjudication_incomplete:") for w in warnings):
            return findings
        verdict = (maturity_by_window or {}).get(spec.context.window.range)
        if verdict is None:
            return findings
        warnings.append(await self._with_settled_reading(verdict, spec, window_explicit))
        await self._events.publish(
            TurnEvent(
                kind="warning",
                turn_id=state.turn_id,
                payload={
                    "code": "ADJUDICATION_INCOMPLETE",
                    "detail": verdict.warning,
                    "window_population": verdict.population,
                    "settled_window_population": verdict.expected,
                },
            )
        )
        return replace(
            findings,
            findings=tuple(
                f if f.confidence != "high" else replace(f, confidence="qualified")
                for f in findings.findings
            ),
        )

    async def _with_settled_reading(
        self, verdict: WindowMaturity, spec: AnalysisSpec, window_explicit: bool
    ) -> str:
        """The maturity caveat, with the settled figure in front of it.

        Round-8 FIX-12(a). "What is my denial rate?" led with 12.8% — the
        July figure this product's own trend answer excludes as provisional
        — and every caveat that governed it sat underneath. The reader
        leaves with the number, not with the paragraph.

        So when the period was one this platform CHOSE (no window in the
        question) and that period has not settled, the answer states what
        the last settled month reads before it states the provisional one.
        Measured, never estimated; over the analyst's own scope; and only
        for a question with a single measure, because "the settled reading"
        of four metrics at once is a table, not a sentence.

        Silent — the plain caveat, unchanged — whenever the reading cannot
        be had honestly, and whenever the analyst NAMED the period: asking
        about July and being told about June is answering a different
        question.
        """
        if self._window_maturity is None or window_explicit or len(spec.measures) != 1:
            return verdict.warning
        measure = spec.measures[0].id
        reading = await self._window_maturity.settled_reading(
            spec,
            measure=measure,
            # The §15 threshold this turn's own frames were policed under:
            # a settled reading over a handful of claims is the small cell
            # the rest of the engine withholds.
            suppression_threshold=self._executor.suppression_threshold,
        )
        if reading is None:
            return verdict.warning
        return (
            f"adjudication_incomplete: through {_period_phrase(reading.window)} — the last "
            f"period of this metric that has finished settling — {metric_label(measure)} reads "
            f"{format_value(reading.value, reading.unit)}. The window below "
            f"({verdict.window.start.isoformat()}..{verdict.window.end.isoformat()}) is the one "
            "this platform assumed, and it has NOT finished settling: it holds "
            f"{verdict.population:,} settled record(s) where a window of that length normally "
            f"holds about {verdict.expected:,}, {verdict.share:.1%} of it. Read the settled "
            "figure as the level and the one below as provisional — what has settled is not a "
            "random sample of what has not, so a total there is understated and a rate there is "
            f"skewed. The count is the one {verdict.yardstick!r} declares, and the norm is the "
            "median month of this load."
        )

    def _benchmarks_for(
        self, findings: FindingsResult, warnings: Sequence[str] = ()
    ) -> tuple[BenchmarkSpec, ...]:
        """Benchmark ranges for every metric the turn's findings cite, in
        finding order, deduplicated by benchmark id.

        Withheld entirely when the turn's own window has not finished
        settling (round-8 FIX-12(a)). Live: "the denial rate stands at 12.8%
        (F1), which sits below the benchmark range of 19-20 percent" — a
        favourable verdict, in the reader's first sentence, on a month that
        was 26% adjudicated, against an insurer-reported cohort whose
        caution ("plan-reported data with no initial/final distinction")
        did not make it into the clause. A benchmark comparison is a
        judgement about a level; a provisional level has no judgement to
        pass on it yet, and passing one anyway is the most quotable
        sentence on the answer.
        """
        if any(w.startswith("adjudication_incomplete:") for w in warnings):
            return ()
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
        spec: AnalysisSpec | None = None,
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
        # Split-or-drill is read off the SPECS as well as the operators: a
        # turn that gained a cut split the parent's population whichever
        # operator got it there, and "this turn neither split nor drilled"
        # was published over a turn that plainly had (round-6 E-02).
        if not any(
            isinstance(op, (SetDimensions, DrillInto)) for op in operators
        ) and not _splits_parent(spec, parent):
            return _not_applicable("this turn neither split nor drilled the parent's population")
        # A drill of a named parent finding reconciles against THAT
        # finding's published figure, whether or not this turn produced a
        # compared money frame and whether or not the parent kept an
        # undimensioned total (round-2 FN-4). Tried first because it is the
        # comparison the reader actually made — two figures on two
        # consecutive screens — and the sum-of-cells check below is the
        # comparison the lineage makes.
        # Only a drill whose handle the immediate parent did not publish
        # needs the session's older findings, and that is the rarer half of
        # the rarer branch — a breakdown never reads them. The registry is
        # therefore fetched under exactly that condition rather than as an
        # argument, which is a store round trip on every split turn.
        drilled = {op.target.value for op in operators if isinstance(op, DrillInto)}
        elsewhere: tuple[Finding, ...] = ()
        if drilled and not any(f.referent.value in drilled for f in parent.findings):
            elsewhere = tuple(
                entry.finding
                for entry in await self._referents.list_for_session(parent.session_id)
                if entry.finding is not None
            )
        containment = containment_reconciliation(
            parent,
            calculation,
            operators,
            spec,
            session_findings=lambda: elsewhere,
            suppression_threshold=self._executor.suppression_threshold,
            # The pinned snapshot's own unit for each metric (R9-03), so a
            # ratio finding is never read back through the money path.
            metric_unit=self._metric_unit,
        )
        if containment is not None:
            if not containment.passed:
                await self._fail_reconciliation(containment.summary, warnings, state)
            # The parent's own level, restated on the child as a mandatory
            # disclosure (FN-10). The seam verdict states it too and that is
            # a line a reader has to go looking for; this one is published
            # ahead of the prose, so a breakdown can never again publish
            # 29.5% / 22.9% / 18.8% without saying what they are parts of.
            if containment.anchor is not None:
                warnings.append(containment.anchor)
            return containment.summary
        shape = find_primary_compare(plan, calculation)
        if shape is None:
            # A drill whose handle measures something else says so (R9-03):
            # "no compared money frame" is a true sentence about the wrong
            # question on a turn that published dollars off a rate finding.
            mismatch = measure_mismatch_reason(
                (*parent.findings, *elsewhere),
                operators,
                _frame_money_totals(calculation.frames)[2],
            )
            return _not_applicable(
                mismatch
                or "this turn produced no compared money frame to reconcile against the parent"
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

    def _metric_unit(self, metric_id: str) -> str | None:
        """The pinned pack's declared unit for a metric id, or ``None``.

        One lookup, one authority. The reconciliation asks it before it
        reads a figure out of a finding, because "is this cents?" is a
        contract question and answering it from the Python type of the
        value is what published ``parent F1=$0.00`` over a 29.5% rate.
        """
        contract = self._pack.metric(metric_id)
        return None if contract is None else str(contract.unit)

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
        self,
        session: Session,
        state: _TurnState,
        classified: ClassificationOutcome | None,
        parent: Investigation | None = None,
    ) -> TurnOutcome:
        if parent is None:
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
        # "Show me all twelve" classifies as PRESENTATION_ONLY about as often
        # as it classifies as REFINEMENT — it IS a statement about display
        # scope. When the count named exceeds what the parent published,
        # re-presenting the same three rows is the no-op R4-11 measured, so
        # the turn is routed to the expansion it asked for. Deterministic and
        # zero-LLM: the number is read out of the analyst's own sentence.
        typed_limit = requested_finding_limit(state.question)
        if typed_limit is not None and typed_limit > len(parent.findings):
            return await self._refinement_turn(
                session,
                state,
                classified,
                dto_ops=(ExpandModel(op="expand", limit=typed_limit),),
            )
        frames = await self._load_frames(parent)
        plan_context = await self._plan_context_of(parent.id)
        # The parent's answer, re-presented — and therefore the parent's
        # caveats, re-presented (R4-04). A re-presentation that drops them
        # is a second, cleaner-looking answer over identical numbers.
        #
        # …plus the two notes that say so (round-5 E-01). The reuse note was
        # composed in the kernel-only branch alone and this path never
        # appended it, so every re-presentation read like a fresh analysis;
        # and REFINEMENT_NOT_APPLIED was registered, titled in the web, and
        # never fired anywhere. Turn two of a session asked for a sort,
        # received the parent's own order, and was told nothing.
        warnings = (
            # Assumptions lead here too (§2.8). A re-presentation reached by
            # RESUMING a clarification has a decision in it — "I read your
            # reply as an answer to the question above" — and this path
            # published the parent's caveats without it, so the one sentence
            # the reader has to meet before the rows was the one dropped.
            *state.assumptions,
            *parent.warnings,
            "refinement_reused_plan: this answer re-serves the previous turn's plan "
            f"({parent.plan_hash}) — the same evidence, the same findings and every caveat "
            "that came with them. Nothing was re-measured and nothing changed but the "
            "presentation.",
        )
        # An ordering the analyst named over a column the served rows already
        # carry is APPLIED, not merely reported as unapplied (round-6 A-01,
        # A-03): the rows are the parent's, and putting the parent's rows in
        # the order that was asked for is the whole of what a presentation
        # turn is for. ``refinement_not_applied`` is what is owed when the
        # request cannot be resolved against them, and only then.
        served = parent.findings
        chart_sorts = plan_context.chart_sorts
        ordering = presentation_ordering(state.question, served)
        if ordering is not None:
            key, descending = ordering
            served = _reordered(served, key, descending)
            direction = "largest first" if descending else "smallest first"
            column = key or "row label"
            warnings = (
                *warnings,
                f"presentation_applied: these are the previous turn's {len(served)} row(s), "
                f"re-served in the order you asked for — sorted by {column}, {direction}. "
                "Nothing was re-measured: the figures, the handles and every caveat are the "
                "ones the answer above carried.",
            )
            chart_sorts = _chart_sorts_for(frames, key, descending) or chart_sorts
        else:
            # An export asked for in words is refused BY NAME, ahead of the
            # generic "that request was not applied" (round-8 FIX-12(b)):
            # the reader needs to know where the export is, not only that
            # this turn did not do it.
            #
            # …and it is refused as a REFUSAL (round-10 R10-5). Returning
            # ``outcome: answer`` while ``REFINEMENT_NOT_APPLIED`` sits in
            # the same payload is the engine recording that the instruction
            # changed nothing and shipping it as though it had — the
            # battery's only outright fail, filed in three consecutive
            # rounds, and the last sentence of every demo before "can you
            # send me that?".
            refusal = _presentation_refusal(state.question)
            if refusal is not None:
                return await self._clarification_outcome(
                    session,
                    state,
                    classified,
                    refusal,
                    extra={"presentation_of": parent.id},
                )
        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=parent.spec,
            parent_id=parent.id,
            findings=served,
            frame_refs=parent.frame_refs,
            plan_hash=parent.plan_hash,
            warnings=warnings,
        )
        edge = RefinementEdge(
            parent_id=parent.id, child_id=investigation.id, turn_id=state.turn_id, operators=()
        )
        await self._investigations.save(investigation, edge)
        await self._traces.save(
            self._trace_record(
                session,
                state,
                classified,
                warnings=warnings,
                extra={
                    "presentation_of": parent.id,
                    "plan_context": {
                        "playbook_id": plan_context.playbook_id,
                        "window_explicit": plan_context.window_explicit,
                        "evidence_depth": plan_context.evidence_depth.value,
                        "chart_sorts": [
                            {"frame_id": frame_id, "by": by, "descending": descending}
                            for frame_id, by, descending in chart_sorts
                        ],
                    },
                },
            )
        )
        await self._turn_complete(state, investigation)
        return TurnOutcome(
            session=session,
            investigation=investigation,
            findings=served,
            header=build_context_header(parent.spec, session, pack=self._pack),
            frames=frames,
            warnings=warnings,
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            referents=await self._referents_of(session, parent.id),
            watermark_stale=state.watermark_stale,
            benchmarks=self._benchmarks_for(FindingsResult(findings=served, referents=())),
            settings=state.settings,
            chart_sorts=chart_sorts,
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
        plan_context: _PlanContext,
    ) -> TurnOutcome | None:
        """RankBy/Expand within cached, untruncated frames: zero probes
        (§7.9). Returns None to fall back to the full path when the cached
        frames cannot honestly answer (missing column, truncated frame).

        **Warnings are a property of the answer, never of the turn class**
        (round-4 R4-04, the round's most dangerous defect because it is
        silent and it lands on the second turn of every demo). This path
        re-serves the parent's plan, the parent's frames and the parent's
        findings byte-for-byte, and it used to publish them under
        ``warnings=()``: three sessions saw 11 warnings become 0 and 7
        become 1 across an IDENTICAL ``plan_hash``, and the CSV export then
        printed "The platform attached no caveats to this answer." over the
        same numbers that had carried a suppression bound, a truncation
        disclosure and an alternate-basis note one turn earlier. Re-serving
        an answer re-serves everything that qualified it: the same
        warnings, the same referents, the same chart ordering.
        """
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
                # "Show me all twelve" over a frame that holds twelve is a
                # display-scope request; over an answer that PUBLISHED three
                # it is a re-selection, and re-selection is the findings
                # builder's job. This branch could only ever hand back the
                # parent's own finding list, so a request to widen it was a
                # no-op that cost a model call and changed nothing (R4-11).
                if op.limit > len(parent.findings):
                    return None
        # A parent's warnings were computed for the plan this turn re-serves,
        # so they ride with it verbatim — plus one line saying that is what
        # happened, so the reuse is legible rather than inferred from a
        # matching hash.
        warnings = (
            *parent.warnings,
            "refinement_reused_plan: this answer re-serves the previous turn's plan "
            f"({parent.plan_hash}) — the same evidence, the same findings and every caveat "
            "that came with them. What changed is the presentation only: "
            + ", ".join(str(op.get("op", "?")) for op in ops_json)
            + ".",
        )
        investigation = replace(
            self._minimal_investigation(session, state, InvestigationStatus.COMPLETE, classified),
            spec=new_spec,
            parent_id=parent.id,
            findings=parent.findings,
            plan_hash=parent.plan_hash,
            warnings=warnings,
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
                warnings=warnings,
                extra={
                    "refinement": {"operators": list(ops_json), "kernel_only": True},
                    # The parent's plan context, because this IS the parent's
                    # plan: publishing ``playbook_id: None`` here made a
                    # re-served answer look like a different, playbook-less
                    # one to every reader of the trace.
                    "plan_context": {
                        "playbook_id": plan_context.playbook_id,
                        "window_explicit": plan_context.window_explicit,
                        "evidence_depth": plan_context.evidence_depth.value,
                        "chart_sorts": [
                            {"frame_id": frame_id, "by": by, "descending": descending}
                            for frame_id, by, descending in plan_context.chart_sorts
                        ],
                    },
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
            warnings=warnings,
            clarification=None,
            definitional=None,
            trace_id=state.trace_id,
            referents=await self._referents_of(session, parent.id),
            watermark_stale=state.watermark_stale,
            benchmarks=self._benchmarks_for(
                FindingsResult(findings=parent.findings, referents=())
            ),
            settings=state.settings,
            chart_sorts=plan_context.chart_sorts,
        )

    async def _referents_of(
        self, session: Session, investigation_id: str
    ) -> tuple[RegisteredReferent, ...]:
        """The handles one investigation published, for a turn re-serving it.

        A re-served answer that publishes no referents is an answer whose
        rows cannot be drilled and whose findings cannot be cited by handle
        — live, ``referents 15 → 0`` on a turn whose findings were
        byte-identical to the one before it (R4-04).
        """
        entries = await self._referents.list_for_session(session.id)
        return tuple(e for e in entries if e.investigation_id == investigation_id)

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
        # ONE funnel, in one order, for every clarification this engine can
        # emit (round-4 R4-12: five personas, six defects, all of them in
        # paths that skipped one of these steps).
        #
        offered = len(clarification.options)
        # 0. An option is something the analyst can SAY. Live (round-8
        #    FIX-12(d)): "Why did it go up?" was answered with option 3
        #    "Which metric are you asking about? — I mean the last figure
        #    you charted." — the platform's own question pasted into the
        #    list of things the reader might reply. Sending it back answers
        #    nothing, which is how a clarification becomes a loop.
        clarification = _drop_interrogative_options(clarification)
        # 1. Values this session has already proved do not exist (FN-6)…
        clarification = drop_refuted_options(
            clarification, await self._refuted_in_session(session)
        )
        # 2. …then the warehouse and the planner (R3-17)…
        clarification = await self._validated_options(session, clarification)
        # 2b. …then the plan grammar, applied to the option's WORDS as well
        #     as to its ids (round-9 R9-07). ``_validated_options`` above
        #     dry-runs an option that carries a binding and waves through
        #     one that does not — which is every option the classifier
        #     writes in free text. "Why did it go up?" was answered with the
        #     platform's own "Yes — re-group the figure F1 result by denial
        #     reason", and tapping it produced GRAIN_INCOMPATIBLE with the
        #     engine's internal predicate on screen.
        clarification = await self._grammatical_options(session, clarification)
        # 3. …then the subject: a clarification about rendering-provider
        #    denial rates must not offer payer options (R4-12 defect 3).
        clarification = await self._on_subject(session, clarification, state.question)
        # 3a. …and a question this session has already answered is not a
        #     question (R9-07): asking WHICH measure of a session holding
        #     exactly one is how "why did it go up" burned its second turn.
        clarification = await self._settled_measure(session, clarification)
        # 3b. …then the one thing a dialogue may never do: ask the question
        #     it has just asked, word for word, of an analyst who answered
        #     it (round-6 A-04). This branch covers the clarifications the
        #     VALIDATOR raises — the value-existence refusal — which reach
        #     this funnel from `_run_analysis` and never through
        #     `classified.clarification`, so the model-requested convergence
        #     counter below has never seen them.
        if self._repeats_pending(state, clarification):
            narrowed = options_named(state.utterance or state.question, clarification.options)
            if len(narrowed) == 1 and not state.applied_bindings:
                lone = clarification.binding_for(narrowed[0])
                if lone is not None:
                    state.applied_bindings.append(lone.option)
                    state.assumptions.append(
                        f"clarification_answer_applied: {lone.option} — I had already asked "
                        f"{clarification.question!r} once and your reply "
                        f"({state.question!r}) names exactly one of the values it offered, so "
                        "it was applied rather than asked about a second time. Say so if you "
                        "meant a different one and I will re-run it."
                    )
                    return await self._apply_binding(session, state, classified, lone)
            clarification = _no_replay(state, clarification, narrowed)
        # 4. …then convergence, counted across EVERY clarification the
        #    engine issues rather than only the interpreter's (defect 4).
        clarification = await self._converge_on_subject(session, state, clarification)
        clarification = self._bounded_clarification(state, clarification)
        # 5. …and finally: a question with one answer is not a question.
        #    ``_lone_binding`` was reachable only from the validator-derived
        #    refusal path, so a model-requested clarification whose options
        #    validation had reduced to exactly one still charged a turn to
        #    ask it (defect 5, measured at $0.146).
        reason_so_far = clarification.reason or ""
        reduced = (
            offered > len(clarification.options) or OPTIONS_DROPPED_MARKER in reason_so_far
        ) and not any(marker in reason_so_far for marker in _COMMITTED_REASONS)
        lone = self._lone_binding(clarification, reduced=reduced)
        if lone is not None and reduced:
            # …but a COLLAPSE is not an answer (round-9 R9-02, the exec's
            # demo blocker #2). "Give me a payer scorecard for July 2026"
            # mid-session came back as ``outcome: answer`` over a single
            # finding — "F5 | Atlas Commercial: 179.5 days in ar", a payer
            # the turn never named — with the refusal demoted into a
            # CLARIFICATION_ANSWER_APPLIED warning that the client's caution
            # fold then hid. The engine had asked a real question, watched
            # the data drop every option but one, and treated the survivor
            # as though the analyst had chosen it.
            #
            # Nobody chose it. A collapse to one surviving option is a fact
            # about this warehouse, and the honest move is to SAY it: the
            # refusal keeps the lead, the survivor is stated, and the
            # analyst decides whether it answers what they asked. Bindings
            # this platform DERIVED from governed content are untouched —
            # there the single answer is the pack's, not the data's, and
            # asking about it is the round-3 R3-07 defect.
            clarification = _state_the_survivor(clarification, lone)
        elif lone is not None and not state.applied_bindings:
            state.applied_bindings.append(lone.option)
            state.assumptions.append(
                f"clarification_answer_applied: {lone.option} — this was the only answer "
                f"available to '{clarification.question}', so it was applied rather than "
                "asked about, and your question was run with it. Say so if you wanted "
                "something else and I will re-run it."
            )
            return await self._apply_binding(session, state, classified, lone)
        clarification = _no_options_card(clarification)
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
        classified: ClassificationOutcome | None,
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
                    chart_sorts=_chart_sorts_from_trace(plan_context.get("chart_sorts")),
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
