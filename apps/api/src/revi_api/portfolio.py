"""The anomaly portfolio: governed prioritization over detected anomalies.

Priority formula ``anomaly_priority@1`` (versioned platform code, like an
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
                       the governed ``anomaly_compliance_floor``

Weights, half-life, and the floor come from the pack's bounded detector
policies (``anomaly_priority_*``); recoverable fractions come from the
governed actionability rules. Items are sorted by priority desc, ties by
|impact| desc, then id — and every card carries the decomposed components
so the ranking is never a black box. Resolved / self-resolved records are
excluded (and absent-at-watermark anomalies never appear, because the
source reads per snapshot).

Cards carry **provenance instead of an evidence grade**: the underlying
anomaly is an external detection system's assertion, not a number this
platform computed from certified semantics, so each card is stamped
``provenance="external_detection"`` with the priority formula version and
the source watermark. See :class:`AnomalyCard` for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from revi_api.actionability import ActionabilityRules, assess
from revi_investigation.application.ports import AnomalyRecord
from revi_investigation_contracts.api import (
    AnomalyCard,
    AnomalyDimension,
    PortfolioResponse,
    TypedInvestigationSpec,
)
from revi_investigation_contracts.refinements import AbsoluteWindowModel, AddFilterModel
from revi_kernel.watermark import DataWatermark
from revi_pack.domain import PackSnapshot

PRIORITY_FORMULA_VERSION = "anomaly_priority@1"

_RESOLVED_STATUSES = frozenset({"resolved", "self_resolved", "closed", "dismissed"})


@dataclass(frozen=True, slots=True)
class PriorityPolicy:
    """Bounded, pack-governed parameters of the priority formula.

    Actionability deliberately carries the dominant default weight: an
    un-fixable pile of dollars must not outrank fixable ones."""

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


def _drill_spec(record: AnomalyRecord) -> TypedInvestigationSpec:
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
        metric_ids=[record.metric_id],
        dimensions=[dimension for dimension, _ in record.dimensions],
        filters=[
            AddFilterModel(op="add_filter", dimension=dimension, predicate_op="eq", values=[value])
            for dimension, value in record.dimensions
        ],
        window=AbsoluteWindowModel(start=record.window_start, end=record.window_end),
    )


def build_portfolio(
    records: tuple[AnomalyRecord, ...],
    *,
    watermark: DataWatermark,
    policy: PriorityPolicy,
    rules: ActionabilityRules,
    warnings: tuple[str, ...] = (),
) -> PortfolioResponse:
    """Rank the anomaly population by the governed priority formula."""
    active = [r for r in records if r.status.lower() not in _RESOLVED_STATUSES]
    all_warnings = list(warnings)
    if not active:
        if not records:
            all_warnings.append(
                "no detected anomalies at this watermark (the detection feed may not "
                "have landed yet)"
            )
        return PortfolioResponse(
            status="empty",
            watermark_id=watermark.id,
            formula_version=PRIORITY_FORMULA_VERSION,
            weights=policy.as_weights(),
            items=[],
            warnings=all_warnings,
        )

    max_impact = max(abs(r.impact_cents) for r in active) or 1
    weight_sum = policy.impact_weight + policy.recency_weight + policy.actionability_weight

    cards: list[AnomalyCard] = []
    for record in active:
        assessment = assess(rules.rule_for(record.category), record)
        age = _age_days(record, watermark)
        recency = Decimal(str(0.5 ** (age / float(policy.half_life_days))))
        impact_norm = Decimal(abs(record.impact_cents)) / Decimal(max_impact)
        recoverable_norm = Decimal(assessment.recoverable_cents) / Decimal(max_impact)
        score = (
            policy.impact_weight * impact_norm
            + policy.recency_weight * recency
            + policy.actionability_weight * recoverable_norm
        ) / weight_sum
        floored = False
        if assessment.compliance_floor and score < policy.compliance_floor:
            score = policy.compliance_floor
            floored = True
        cards.append(
            AnomalyCard(
                anomaly_id=record.anomaly_id,
                title=record.title,
                description=record.description,
                category=record.category,
                metric_id=record.metric_id,
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
                age_days=age,
                recoverable_cents_estimate=assessment.recoverable_cents,
                actionability_label=assessment.label,
                actionability_rationale=assessment.rationale,
                priority_score=round(float(score), 6),
                compliance_floor_applied=floored,
                drill_spec=_drill_spec(record),
                # honest provenance in place of an evidence grade: this row
                # is an external detector's assertion read at a watermark,
                # ordered by a versioned platform formula (see AnomalyCard)
                provenance="external_detection",
                priority_formula_version=PRIORITY_FORMULA_VERSION,
                source_watermark_id=watermark.id,
            )
        )
    cards.sort(key=lambda c: (-c.priority_score, -abs(c.impact_cents), c.anomaly_id))
    return PortfolioResponse(
        status="ok",
        watermark_id=watermark.id,
        formula_version=PRIORITY_FORMULA_VERSION,
        weights=policy.as_weights(),
        items=cards,
        warnings=all_warnings,
    )
