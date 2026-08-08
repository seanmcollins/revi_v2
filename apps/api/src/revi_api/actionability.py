"""Governed anomaly-actionability rules: "can this actually be fixed?"

Loads ``packs/base-rcm/anomaly_actionability.yaml`` (pack-adjacent
governed content, content-hashed for the trace) and applies its
per-category recoverable-fraction rules to an anomaly's EVIDENCE FACTS —
deterministically, never per-anomaly code. See the YAML header for the
rule language (modes: fraction / open_share / flag_share).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

from revi_investigation.application.ports import AnomalyRecord


@dataclass(frozen=True, slots=True)
class ActionabilityRule:
    category: str
    mode: str  # fraction | open_share | flag_share
    fraction: Decimal
    rationale: str
    open_fact: str | None = None
    expired_fact: str | None = None
    numerator_fact: str | None = None
    denominator_fact: str | None = None
    compliance_floor: bool = False


@dataclass(frozen=True, slots=True)
class DrillRepoint:
    """A governed substitution for a detector record's metric id.

    A card publishes ``impact_cents``; its drill must be able to produce
    that figure. When the record's own metric cannot — a ratio contract
    reporting dollars, or one whose primary basis is unbound at its
    grain — this names the contract that can, and the rationale travels
    with it so the substitution is visible rather than silent."""

    from_metric_id: str
    to_metric_id: str
    rationale: str


@dataclass(frozen=True, slots=True)
class ActionabilityRules:
    default: ActionabilityRule
    by_category: Mapping[str, ActionabilityRule]
    content_hash: str
    drill_repoints: Mapping[str, DrillRepoint] = field(default_factory=dict)

    def rule_for(self, category: str) -> ActionabilityRule:
        return self.by_category.get(category.upper(), self.default)

    def repoint_for(self, metric_id: str) -> DrillRepoint | None:
        return self.drill_repoints.get(metric_id)


@dataclass(frozen=True, slots=True)
class ActionabilityAssessment:
    recoverable_cents: int
    recoverable_fraction: Decimal
    label: str
    rationale: str
    compliance_floor: bool


def _parse_rule(category: str, node: Mapping[str, Any]) -> ActionabilityRule:
    mode = str(node.get("mode", "fraction"))
    if mode not in ("fraction", "open_share", "flag_share"):
        raise ValueError(f"anomaly_actionability: unknown mode {mode!r} for {category!r}")
    return ActionabilityRule(
        category=category,
        mode=mode,
        fraction=Decimal(str(node.get("fraction", "0.5"))),
        rationale=" ".join(str(node.get("rationale", "")).split()),
        open_fact=node.get("open_fact"),
        expired_fact=node.get("expired_fact"),
        numerator_fact=node.get("numerator_fact"),
        denominator_fact=node.get("denominator_fact"),
        compliance_floor=bool(node.get("compliance_floor", False)),
    )


def load_actionability_rules(path: str | Path) -> ActionabilityRules:
    raw = Path(path).read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a mapping document")
    default = _parse_rule("default", document.get("default", {}))
    categories = document.get("categories", {})
    if not isinstance(categories, dict):
        raise ValueError(f"{path}: 'categories' must be a mapping")
    by_category = {
        str(category).upper(): _parse_rule(str(category).upper(), node)
        for category, node in categories.items()
    }
    repoints_node = document.get("drill_repoints", {})
    if not isinstance(repoints_node, dict):
        raise ValueError(f"{path}: 'drill_repoints' must be a mapping")
    repoints: dict[str, DrillRepoint] = {}
    for source, node in repoints_node.items():
        if not isinstance(node, dict) or not node.get("metric_id"):
            raise ValueError(f"{path}: drill_repoints[{source!r}] needs a metric_id")
        repoints[str(source)] = DrillRepoint(
            from_metric_id=str(source),
            to_metric_id=str(node["metric_id"]),
            rationale=" ".join(str(node.get("rationale", "")).split()),
        )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return ActionabilityRules(
        default=default,
        by_category=by_category,
        content_hash=digest,
        drill_repoints=repoints,
    )


def _fact(evidence: Mapping[str, Any], name: str | None) -> Decimal | None:
    if name is None:
        return None
    value = evidence.get(name)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    return None


def _label(fraction: Decimal, compliance: bool) -> str:
    if compliance:
        return "compliance-mandatory"
    if fraction >= Decimal("0.75"):
        return "highly recoverable"
    if fraction >= Decimal("0.25"):
        return "partially recoverable"
    if fraction > 0:
        return "marginally recoverable"
    return "not recoverable"


def assess(rule: ActionabilityRule, record: AnomalyRecord) -> ActionabilityAssessment:
    """Deterministic recoverable-fraction assessment from evidence facts."""
    fraction = rule.fraction
    if rule.mode == "open_share":
        open_count = _fact(record.evidence, rule.open_fact)
        expired_count = _fact(record.evidence, rule.expired_fact)
        if open_count is not None and expired_count is not None:
            total = open_count + expired_count
            fraction = (open_count / total) if total > 0 else Decimal(0)
    elif rule.mode == "flag_share":
        numerator = _fact(record.evidence, rule.numerator_fact)
        denominator = _fact(record.evidence, rule.denominator_fact)
        if numerator is not None and denominator is not None and denominator > 0:
            fraction = numerator / denominator
    fraction = max(Decimal(0), min(Decimal(1), fraction))
    recoverable = int(
        (Decimal(abs(record.impact_cents)) * fraction).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )
    return ActionabilityAssessment(
        recoverable_cents=recoverable,
        recoverable_fraction=fraction,
        label=_label(fraction, rule.compliance_floor),
        rationale=rule.rationale,
        compliance_floor=rule.compliance_floor,
    )
