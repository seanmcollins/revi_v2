"""The anomaly portfolio: governed prioritization over detected anomalies.

Priority formula ``anomaly_priority@2`` (versioned platform code, like an
operator — the pack supplies only bounded parameters):

    impact_norm      = |impact_cents| / max(|impact_cents|) over the population
    age_days         = (watermark.loaded_at - onset).days, onset = the
                       evidence fact ``onset_date`` when present, else
                       ``detected_at``
    recency          = 0.5 ** (age_days / half_life_days)
    recoverable      = |impact_cents| x recoverable_fraction  (governed
                       actionability rules over the record's evidence facts)
    recoverable_norm = recoverable / max(|impact_cents|)
    priority         = (w_i·impact_norm + w_r·recency + w_a·recoverable_norm)
                       / (w_i + w_r + w_a)
    floor            = compliance-mandatory categories never score below
                       the MEDIAN score of the non-floored population

Weights, half-life, and the absolute fallback floor come from the pack's
bounded detector policies (``anomaly_priority_*``); recoverable fractions
come from the governed actionability rules. Items are sorted by priority
desc, ties by |impact| desc, then id — and every card carries the
decomposed components so the ranking is never a black box. Resolved /
self-resolved records are excluded (and absent-at-watermark anomalies
never appear, because the source reads per snapshot).

What changed in ``@2`` (round-1 review F17)
===========================================
The compliance floor was the constant ``0.60``, and the top of the
worklist read: an $824 credit balance at rank 1 and rank 2, then a
$178,217 critical DNFB card at rank 3. The policy behind that — a
compliance-mandatory item must be worked regardless of size — is right
and is kept. The *serving* of it was wrong twice over.

- **The floor is now relative**: the median score of the non-floored
  population, computed at build time. A compliance item is lifted to "as
  important as the middle of the worklist", which is what "must do
  regardless of size" actually means, rather than to a constant that
  outranked every real finding on the list. The absolute governed value
  remains the fallback for a build with nothing left un-floored to take a
  median of, and the response says which basis was used. Detection
  provenance is untouched: this is a serving-time formula with a version
  string, and the string is bumped.
- **Compliance items are their own lane.** ``items`` stays one ranked
  array; each card names its ``lane`` and the response lists the lanes,
  so a UI renders "must-do regardless of size" as a section instead of
  letting it masquerade as the highest-value work in the building.
- **The arithmetic is published.** Every card carries the three
  normalized components, the three weighted terms, the normalizer, the
  score before the floor and the floor itself — so the ranking is
  checkable with a calculator instead of trusted.

Card/drill agreement (round-1 review F1)
========================================
A card published ``$178,217``; drilling it answered ``$195,873.92``;
nothing on either screen said they disagreed. They are two different
claims — the detector's assertion in its own window, population and
basis, against this platform's governed contract re-derived at the pinned
watermark — and both are honest. What was not honest was the silence.

So each drillable card's figure is now **re-derived at build time**
(:mod:`revi_api.rederive`, the drillability pipeline continued two stages
further) and published beside the detector's as
``reconciled_impact_cents``, with the delta, the agreement verdict and
the reason the two may differ. A reader who checks can no longer find a
discrepancy the payload did not already state.

Cards carry **provenance instead of an evidence grade**: the underlying
anomaly is an external detection system's assertion, not a number this
platform computed from certified semantics, so each card is stamped
``provenance="external_detection"`` with the priority formula version and
the source watermark. See :class:`AnomalyCard` for the full rationale.

Ranking honesty (round-1 review D5)
===================================
The governed priority formula answers "what matters most". It does not
answer "what can I open", and the two were anti-correlated almost
perfectly: 33 cards, 6 drillable, first drillable card at **rank 17**,
~90% of the ranked dollars behind an error dialog. Ranks 1 and 2 were
``UNSUPPORTED_CONCEPT``; rank 3 was ``DATE_BASIS_INVALID``.

Two things changed, neither of which touches the formula:

- **Drillability is decided before the card is published.** Each drill
  spec is run through the *real* planning and §6.6 validation pass — no
  warehouse query, no execution — and the card carries ``drillable`` plus
  the platform's own refusal text. Undrillable cards keep their score and
  their detected evidence, and sort below every card that can be opened,
  so the top of the worklist is work somebody can start today.
- **Drill handles may be repointed by governed content.** A record whose
  metric id cannot express the impact it published (a ratio contract
  reporting dollars) drills the contract that can, and says on the card
  which metric it substituted and why. See ``drill_repoints`` in
  ``packs/base-rcm/anomaly_actionability.yaml``.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from revi_api.actionability import ActionabilityRules, assess
from revi_api.metric_display import MetricDisplayRules
from revi_api.rederive import ReDerivedImpact, compare_impact
from revi_api.warning_codes import structured_warnings
from revi_investigation.application.ports import AnomalyRecord
from revi_investigation_contracts.api import (
    AnomalyCard,
    AnomalyDimension,
    PortfolioLanePayload,
    PortfolioResponse,
    PriorityDecompositionPayload,
    TypedInvestigationSpec,
)
from revi_investigation_contracts.refinements import AbsoluteWindowModel, AddFilterModel
from revi_kernel.watermark import DataWatermark
from revi_pack.domain import PackSnapshot

PRIORITY_FORMULA_VERSION = "anomaly_priority@2"

_RESOLVED_STATUSES = frozenset({"resolved", "self_resolved", "closed", "dismissed"})

#: The two lanes of the worklist, in render order.
COMPLIANCE_LANE = "compliance"
VALUE_LANE = "value"

_LANE_LABELS: dict[str, tuple[str, str]] = {
    COMPLIANCE_LANE: (
        "Must do regardless of size",
        "Compliance-mandatory categories. These are worked because the rule "
        "says so, not because they are the largest thing on the list — a "
        "$824 credit balance and a $84,000 one carry the same obligation. "
        "Their score is floored to the median of the value-ranked work so "
        "they stay visible without displacing it.",
    ),
    VALUE_LANE: (
        "Ranked by value recoverable",
        "Ordered by the governed priority formula: normalized impact, "
        "recency, and the governed recoverable estimate — with the cards "
        "this platform cannot yet investigate listed last, carrying its "
        "own refusal.",
    ),
}


@dataclass(frozen=True, slots=True)
class PriorityPolicy:
    """Bounded, pack-governed parameters of the priority formula.

    Actionability deliberately carries the dominant default weight: an
    un-fixable pile of dollars must not outrank fixable ones.

    ``compliance_floor`` changed role in ``anomaly_priority@2``: it is no
    longer the floor that is applied, it is the FALLBACK floor for a build
    where every detected anomaly is compliance-mandatory and there is no
    non-floored population to take a median of. It is still governed, still
    bounded, and still published in ``weights``; the floor actually applied
    is on the response as ``compliance_floor_value``/``_basis``."""

    impact_weight: Decimal = Decimal("0.25")
    recency_weight: Decimal = Decimal("0.15")
    actionability_weight: Decimal = Decimal("0.60")
    half_life_days: Decimal = Decimal(14)
    compliance_floor: Decimal = Decimal("0.60")

    def as_weights(self) -> dict[str, float]:
        return {
            "impact": float(self.impact_weight),
            "recency": float(self.recency_weight),
            "actionability": float(self.actionability_weight),
            "half_life_days": float(self.half_life_days),
            "compliance_floor": float(self.compliance_floor),
        }


def priority_policy_from_pack(snapshot: PackSnapshot) -> PriorityPolicy:
    """Read the governed parameters from the pack's detector policies."""
    values: dict[str, Decimal] = {}
    for policy in snapshot.detector_policies:
        values[policy.id] = policy.threshold
    defaults = PriorityPolicy()
    return PriorityPolicy(
        impact_weight=values.get("anomaly_priority_impact_weight", defaults.impact_weight),
        recency_weight=values.get("anomaly_priority_recency_weight", defaults.recency_weight),
        actionability_weight=values.get(
            "anomaly_priority_actionability_weight", defaults.actionability_weight
        ),
        half_life_days=values.get("anomaly_recency_half_life_days", defaults.half_life_days),
        compliance_floor=values.get("anomaly_compliance_floor", defaults.compliance_floor),
    )


def _age_days(record: AnomalyRecord, watermark: DataWatermark) -> int:
    onset = record.detected_at.date()
    raw = record.evidence.get("onset_date")
    if isinstance(raw, str):
        try:
            from datetime import date

            onset = date.fromisoformat(raw)
        except ValueError:
            pass
    return max(0, (watermark.loaded_at.date() - onset).days)


#: Decides whether a drill handle can be answered at a watermark. Returns
#: the platform's own refusal text, or ``None`` when the spec plans and
#: validates.
DrillabilityProbe = Callable[[TypedInvestigationSpec, DataWatermark], str | None]


def _drill_spec(record: AnomalyRecord, metric_id: str | None = None) -> TypedInvestigationSpec:
    """The card's drill handle: a complete, executable typed investigation.

    The detector asserts a *level* about one cell — this metric, these
    dimension values, this observation window. The drill re-states exactly
    that in the platform's own vocabulary, so posting it re-derives the
    assertion from certified semantics and a versioned metric contract and
    the answer carries a real evidence grade (the card carries only
    provenance — see :class:`AnomalyCard`).

    The dimensions appear twice on purpose and mean two different things:
    as ``dimensions`` they are the breakdown the answer is cut by, and at
    their detected values as ``filters`` they are the scope that isolates
    the cell — so the header shows them as visible chips (§7.2) and a
    later ``remove_filter`` widens the drill back into its population
    without having to guess what the population was.

    No comparison is set. An anomaly card claims a level, not a movement;
    ``set_comparison`` is one ordinary refinement away, and — this being
    the point of the typed first turn — it now has a parent to land on.
    """
    return TypedInvestigationSpec(
        metric_ids=[metric_id or record.metric_id],
        dimensions=[dimension for dimension, _ in record.dimensions],
        filters=[
            AddFilterModel(op="add_filter", dimension=dimension, predicate_op="eq", values=[value])
            for dimension, value in record.dimensions
        ],
        window=AbsoluteWindowModel(start=record.window_start, end=record.window_end),
    )


def is_active(record: AnomalyRecord) -> bool:
    """Will this record appear on the worklist at all?

    Exported so a caller doing per-card work *before* the build (notably
    re-derivation, which reads the warehouse) skips exactly the records
    the build will skip, rather than paying for anomalies nobody will see.
    """
    return record.status.lower() not in _RESOLVED_STATUSES


def drill_spec_for(record: AnomalyRecord, rules: ActionabilityRules) -> TypedInvestigationSpec:
    """The drill handle a card will publish, including any governed repoint.

    Exported so a caller can re-derive a card's figure BEFORE the portfolio
    is built (re-derivation reads the warehouse and is therefore async,
    while this builder stays synchronous and testable). Both paths call
    this one function, so the spec that gets re-derived is always the spec
    the card publishes.
    """
    repoint = rules.repoint_for(record.metric_id)
    return _drill_spec(record, repoint.to_metric_id if repoint else None)


#: What the platform can honestly say when it never attempted a
#: re-derivation for a card.
_NOT_ATTEMPTED = (
    "this card's figure was not re-derived by the platform in this build, so the "
    "detector's number stands alone"
)


#: Why a ``kind: snapshot`` contract and a detector's windowed figure are
#: not two measurements of the same thing (round-2 FN-2). A snapshot is a
#: balance as of the watermark; it applies no start..end range at all, so
#: the gap between it and a card fired over a window is a difference of
#: *kind*, not a disagreement this platform can lay at the detector's door.
SNAPSHOT_NOT_COMPARABLE = (
    "the governed contract is a snapshot (an as-of balance at the watermark) and applies "
    "no start..end window, while the card's figure was computed over one, so the gap "
    "between them is a difference of measurement kind rather than of population, window "
    "or valuation basis."
)


def _reconciliation_fields(
    record: AnomalyRecord,
    rederived: ReDerivedImpact | None,
    *,
    drillable: bool,
    snapshot_drill: bool = False,
) -> dict[str, object]:
    """Card fields for "does the platform's own number agree with this?"

    Always populated. A card that says nothing about the relationship
    between its figure and the drill's is the exact defect this closes.
    The comparison itself lives in :func:`revi_api.rederive.compare_impact`,
    shared with the drill answer's strip so the two cannot word the same
    pair of numbers differently.
    """
    comparison = compare_impact(
        detector_cents=record.impact_cents,
        window_start=record.window_start,
        window_end=record.window_end,
        rederived=rederived,
        unattempted_note=(
            _NOT_ATTEMPTED
            if drillable
            else "this card cannot be investigated at this catalog and pack version, so "
            "there is no governed contract figure to reconcile the detector's against"
        ),
        not_comparable_reason=SNAPSHOT_NOT_COMPARABLE if snapshot_drill else None,
    )
    return {
        "reconciled_impact_cents": comparison.platform_cents,
        "reconciled_impact_metric_id": comparison.measure_id,
        "impact_agreement": comparison.status,
        "impact_delta_cents": comparison.delta_cents,
        "impact_delta_fraction": comparison.delta_fraction,
        "impact_reconciliation_note": comparison.note,
    }


def build_portfolio(
    records: tuple[AnomalyRecord, ...],
    *,
    watermark: DataWatermark,
    policy: PriorityPolicy,
    rules: ActionabilityRules,
    tenant: str = "",
    warnings: tuple[str, ...] = (),
    drillability: DrillabilityProbe | None = None,
    rederived: Mapping[str, ReDerivedImpact] | None = None,
    metric_display: MetricDisplayRules | None = None,
    snapshot_metric_ids: frozenset[str] = frozenset(),
) -> PortfolioResponse:
    """Rank the anomaly population by the governed priority formula, then
    float the cards the platform can actually investigate to the top.

    ``rederived`` maps anomaly id → this platform's own figure for that
    card's cell, computed by the caller (it reads the warehouse; this
    function does not). Absent, every card reports its impact as
    un-reconciled and says so — never silently as agreed.
    """
    active = [r for r in records if is_active(r)]
    all_warnings = list(warnings)
    if not active:
        if not records:
            all_warnings.append(
                "no detected anomalies at this watermark (the detection feed may not "
                "have landed yet)"
            )
        return PortfolioResponse(
            status="empty",
            tenant=tenant,
            watermark_id=watermark.id,
            formula_version=PRIORITY_FORMULA_VERSION,
            weights=policy.as_weights(),
            items=[],
            warnings=all_warnings,
            warnings_v2=structured_warnings(all_warnings),
        )

    max_impact = max(abs(r.impact_cents) for r in active) or 1
    weight_sum = policy.impact_weight + policy.recency_weight + policy.actionability_weight

    # ---- pass 1: the formula, before any floor -----------------------------
    #
    # Two passes because the floor is now RELATIVE: it is the median of the
    # scores of everything that is not floored, which cannot be known until
    # every score exists. Nothing else about the arithmetic changed.
    @dataclass(frozen=True, slots=True)
    class _Scored:
        record: AnomalyRecord
        raw: Decimal
        impact_norm: Decimal
        recency: Decimal
        recoverable_norm: Decimal
        recoverable_cents: int
        label: str
        rationale: str
        compliance: bool
        age: int

    scored: list[_Scored] = []
    for record in active:
        assessment = assess(rules.rule_for(record.category), record)
        age = _age_days(record, watermark)
        recency = Decimal(str(0.5 ** (age / float(policy.half_life_days))))
        impact_norm = Decimal(abs(record.impact_cents)) / Decimal(max_impact)
        recoverable_norm = Decimal(assessment.recoverable_cents) / Decimal(max_impact)
        raw = (
            policy.impact_weight * impact_norm
            + policy.recency_weight * recency
            + policy.actionability_weight * recoverable_norm
        ) / weight_sum
        scored.append(
            _Scored(
                record=record,
                raw=raw,
                impact_norm=impact_norm,
                recency=recency,
                recoverable_norm=recoverable_norm,
                recoverable_cents=assessment.recoverable_cents,
                label=assessment.label,
                rationale=assessment.rationale,
                compliance=assessment.compliance_floor,
                age=age,
            )
        )

    # ---- the relative floor ------------------------------------------------
    value_scores = [s.raw for s in scored if not s.compliance]
    if value_scores:
        floor = Decimal(str(statistics.median(float(s) for s in value_scores)))
        floor_basis = "relative_median"
    else:
        # Nothing to take a median of — every detected anomaly is
        # compliance-mandatory. The governed absolute is the honest
        # fallback, and the response says that is what happened.
        floor = policy.compliance_floor
        floor_basis = "governed_absolute"

    # ---- pass 2: cards -----------------------------------------------------
    cards: list[AnomalyCard] = []
    for entry in scored:
        record = entry.record
        repoint = rules.repoint_for(record.metric_id)
        drill_spec = drill_spec_for(record, rules)
        reason = drillability(drill_spec, watermark) if drillability is not None else None
        drillable = reason is None
        score = entry.raw
        floored = False
        if entry.compliance and score < floor:
            score = floor
            floored = True
        cards.append(
            AnomalyCard(
                anomaly_id=record.anomaly_id,
                title=record.title,
                description=record.description,
                category=record.category,
                metric_id=record.metric_id,
                metric_display_name=(
                    metric_display.name_for(record.metric_id)
                    if metric_display is not None
                    else None
                ),
                severity=record.severity,
                confidence=record.confidence,
                status=record.status,
                detected_at=record.detected_at,
                window_start=record.window_start,
                window_end=record.window_end,
                dimensions=[
                    AnomalyDimension(dimension=d, value=v) for d, v in record.dimensions
                ],
                impact_cents=record.impact_cents,
                age_days=entry.age,
                recoverable_cents_estimate=entry.recoverable_cents,
                actionability_label=entry.label,
                actionability_rationale=entry.rationale,
                priority_score=round(float(score), 6),
                compliance_floor_applied=floored,
                # The whole computation, publishable because it is trivially
                # available here and unverifiable anywhere else.
                priority=PriorityDecompositionPayload(
                    impact_norm=round(float(entry.impact_norm), 6),
                    recency=round(float(entry.recency), 6),
                    recoverable_norm=round(float(entry.recoverable_norm), 6),
                    impact_term=round(float(policy.impact_weight * entry.impact_norm), 6),
                    recency_term=round(float(policy.recency_weight * entry.recency), 6),
                    actionability_term=round(
                        float(policy.actionability_weight * entry.recoverable_norm), 6
                    ),
                    weight_sum=round(float(weight_sum), 6),
                    score_before_floor=round(float(entry.raw), 6),
                    floor_applied=floored,
                    floor_value=round(float(floor), 6),
                    floor_basis=floor_basis,
                ),
                # Lane membership is the CATEGORY, not the mechanism: a
                # compliance-mandatory card that already scored above the
                # floor is still worked because the rule says so, and
                # belongs in the same section as its floored siblings.
                lane=COMPLIANCE_LANE if entry.compliance else VALUE_LANE,
                drill_spec=drill_spec,
                drillable=drillable,
                drill_unavailable_reason=reason,
                drill_repointed_from=repoint.from_metric_id if repoint else None,
                drill_repoint_rationale=repoint.rationale if repoint else None,
                # honest provenance in place of an evidence grade: this row
                # is an external detector's assertion read at a watermark,
                # ordered by a versioned platform formula (see AnomalyCard)
                provenance="external_detection",
                priority_formula_version=PRIORITY_FORMULA_VERSION,
                source_watermark_id=watermark.id,
                **_reconciliation_fields(
                    record,
                    (rederived or {}).get(record.anomaly_id),
                    drillable=drillable,
                    snapshot_drill=any(
                        mid in snapshot_metric_ids for mid in drill_spec.metric_ids
                    ),
                ),
            )
        )
    # Governed priority still decides the order; drillability decides which
    # half of the list you are in. A worklist whose first sixteen rows all
    # open an error dialog is not a worklist.
    cards.sort(
        key=lambda c: (not c.drillable, -c.priority_score, -abs(c.impact_cents), c.anomaly_id)
    )
    blocked = [c for c in cards if not c.drillable]
    if blocked:
        blocked_cents = sum(abs(c.impact_cents) for c in blocked)
        total_cents = sum(abs(c.impact_cents) for c in cards) or 1
        all_warnings.append(
            f"{len(blocked)} of {len(cards)} detected anomalies "
            f"({blocked_cents / total_cents:.0%} of ranked impact) are not investigable at "
            "this catalog and pack version; they are detected, ranked, and listed after the "
            "cards that can be opened, with the platform's refusal on each"
        )
    all_warnings.extend(_reconciliation_warnings(cards))
    return PortfolioResponse(
        status="ok",
        tenant=tenant,
        watermark_id=watermark.id,
        formula_version=PRIORITY_FORMULA_VERSION,
        weights=policy.as_weights(),
        items=cards,
        lanes=_lanes(cards),
        compliance_floor_value=round(float(floor), 6),
        compliance_floor_basis=floor_basis,
        warnings=all_warnings,
        warnings_v2=structured_warnings(all_warnings),
    )


def _lanes(cards: list[AnomalyCard]) -> list[PortfolioLanePayload]:
    """The worklist's sections, in render order, over the ranked array."""
    lanes: list[PortfolioLanePayload] = []
    for lane_id in (COMPLIANCE_LANE, VALUE_LANE):
        members = [c for c in cards if c.lane == lane_id]
        if not members:
            continue
        label, description = _LANE_LABELS[lane_id]
        lanes.append(
            PortfolioLanePayload(
                id=lane_id,
                label=label,
                description=description,
                anomaly_ids=[c.anomaly_id for c in members],
                item_count=len(members),
                impact_cents=sum(abs(c.impact_cents) for c in members),
            )
        )
    return lanes


def _reconciliation_warnings(cards: list[AnomalyCard]) -> list[str]:
    """Worklist-level statements about card/contract agreement.

    Two separate facts, never merged: how many cards the platform could
    not re-derive at all, and how many it re-derived to a materially
    different number. Both are worth a reader's attention and they call
    for different responses.
    """
    out: list[str] = []
    drillable = [c for c in cards if c.drillable]
    if not drillable:
        return out
    unavailable = [c for c in drillable if c.impact_agreement == "unavailable"]
    # Counted, and counted apart. A card whose contract is a ratio or a
    # snapshot never had a comparable dollar figure to diverge FROM, and
    # folding it into the divergence statistic is how "-99.6% divergence"
    # got published about a percentage (FN-1) and how a balance-vs-flow gap
    # got attributed to the detector (FN-2).
    diverged = [c for c in drillable if c.impact_agreement == "diverged"]
    not_comparable = [c for c in drillable if c.impact_agreement == "not_comparable"]
    if not_comparable:
        out.append(
            f"{len(not_comparable)} of {len(drillable)} ranked cards name a governed "
            "contract that is not comparable to the detector's dollar figure (a snapshot "
            "balance against a windowed flow); both figures are published on each card "
            "and neither is stated as a divergence from the other"
        )
    if unavailable:
        out.append(
            f"{len(unavailable)} of {len(drillable)} ranked cards could not be re-derived "
            "against this platform's governed contracts; their impact figures are the "
            "detection system's assertion alone"
        )
    if diverged:
        worst = max(abs(c.impact_delta_fraction or 0.0) for c in diverged)
        out.append(
            f"{len(diverged)} ranked cards diverge from this platform's re-derivation of "
            f"the same cell (largest gap {worst:.1%}); each card publishes both figures, "
            "the delta, and the reason the detector's window, population or valuation "
            "basis is not the contract's"
        )
    return out
