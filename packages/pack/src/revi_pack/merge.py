"""Overlay composition (design §5.4). Composition is not free-form.

Permitted overlay overrides:

- concept **aliases** (add/replace via alias patches),
- **presentation recipes**,
- **ranking weights**,
- **tenant bindings** (conflict resolution tenant > organization > base),
- **detector thresholds** strictly within the base-declared [min, max] range.

Forbidden: metric formulas, denominators, grain, or date-basis changes —
redefining an existing metric id in an overlay raises ``PolicyDeniedError``;
new contract versions arrive only through the promotion path (§9.5). The
same applies to concepts, codes, playbooks, conclusion policies, and filing
rules: overlays may *add* new ids, never redefine existing ones.

The base layer must be present and first; layer order is base →
organization → tenant.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from revi_calculation_contracts.contract import MetricContract
from revi_kernel.errors import PolicyDeniedError
from revi_pack.domain import (
    BindingCandidate,
    CodeDefinition,
    CodeSystem,
    Concept,
    ConclusionPolicy,
    DetectorPolicy,
    FilingRule,
    PackLayer,
    PackLayerKind,
    Playbook,
    PresentationRecipe,
    RankingPolicy,
)
from revi_pack.errors import PackCompositionError

_KIND_ORDER: dict[PackLayerKind, int] = {
    PackLayerKind.BASE: 0,
    PackLayerKind.ORGANIZATION: 1,
    PackLayerKind.TENANT: 2,
}


@dataclass(frozen=True, slots=True)
class ComposedPack:
    """The merge result: fully resolved artifact collections plus the source
    layers, ready for snapshot hashing (:mod:`revi_pack.snapshot`)."""

    pack_id: str
    version: str
    layers: tuple[PackLayer, ...]
    concepts: tuple[Concept, ...]
    code_definitions: tuple[CodeDefinition, ...]
    metric_contracts: tuple[MetricContract, ...]
    bindings: tuple[BindingCandidate, ...]
    playbooks: tuple[Playbook, ...]
    conclusion_policies: tuple[ConclusionPolicy, ...]
    ranking_policies: tuple[RankingPolicy, ...]
    detector_policies: tuple[DetectorPolicy, ...]
    presentation_recipes: tuple[PresentationRecipe, ...]
    filing_rules: tuple[FilingRule, ...]


def _validate_stack(layers: Sequence[PackLayer]) -> None:
    if not layers:
        raise PackCompositionError("cannot compose an empty layer stack")
    if layers[0].kind is not PackLayerKind.BASE:
        raise PackCompositionError(
            f"the first layer must be the base layer, got {layers[0].kind.value!r} "
            f"({layers[0].name!r})"
        )
    for previous, current in itertools.pairwise(layers):
        if _KIND_ORDER[current.kind] <= _KIND_ORDER[previous.kind]:
            raise PackCompositionError(
                f"layer order must be base -> organization -> tenant with at most one of each; "
                f"{current.kind.value!r} ({current.name!r}) may not follow {previous.kind.value!r}"
            )


def _deny(message: str, **details: object) -> PolicyDeniedError:
    return PolicyDeniedError(message, details={k: str(v) for k, v in details.items()})


def _merge_concepts(layers: Sequence[PackLayer]) -> dict[str, Concept]:
    concepts: dict[str, Concept] = {}
    for layer in layers:
        for concept in layer.concepts:
            if concept.id in concepts and layer.kind is not PackLayerKind.BASE:
                raise _deny(
                    f"overlay {layer.name!r} may not redefine concept {concept.id!r}; "
                    "only aliases may be overridden (use an alias patch)",
                    layer=layer.name,
                    concept=concept.id,
                )
            concepts[concept.id] = concept
        if layer.alias_overrides and layer.kind is PackLayerKind.BASE:
            raise PackCompositionError(f"base layer {layer.name!r} may not carry alias overrides")
        for override in layer.alias_overrides:
            target = concepts.get(override.concept_id)
            if target is None:
                raise PackCompositionError(
                    f"layer {layer.name!r}: alias override references unknown concept "
                    f"{override.concept_id!r}"
                )
            aliases = list(target.aliases if override.replace_aliases is None else override.replace_aliases)
            for alias in override.add_aliases:
                if alias not in aliases:
                    aliases.append(alias)
            concepts[target.id] = replace(target, aliases=tuple(aliases))
    return concepts


def _merge_detectors(layers: Sequence[PackLayer]) -> dict[str, DetectorPolicy]:
    detectors: dict[str, DetectorPolicy] = {}

    def tune(existing: DetectorPolicy, threshold: Decimal, layer: PackLayer) -> DetectorPolicy:
        if not (existing.threshold_min <= threshold <= existing.threshold_max):
            raise _deny(
                f"layer {layer.name!r}: detector {existing.id!r} threshold {threshold} is outside "
                f"the declared range [{existing.threshold_min}, {existing.threshold_max}]",
                layer=layer.name,
                detector=existing.id,
                threshold=threshold,
                threshold_min=existing.threshold_min,
                threshold_max=existing.threshold_max,
            )
        return replace(existing, threshold=threshold)

    for layer in layers:
        for policy in layer.detector_policies:
            existing = detectors.get(policy.id)
            if existing is None:
                detectors[policy.id] = policy
                continue
            if (
                policy.description != existing.description
                or policy.threshold_min != existing.threshold_min
                or policy.threshold_max != existing.threshold_max
            ):
                raise _deny(
                    f"overlay {layer.name!r} may not redeclare detector {policy.id!r} "
                    "description or [threshold_min, threshold_max]; overlays tune the "
                    "threshold only",
                    layer=layer.name,
                    detector=policy.id,
                )
            detectors[policy.id] = tune(existing, policy.threshold, layer)
        if layer.detector_overrides and layer.kind is PackLayerKind.BASE:
            raise PackCompositionError(f"base layer {layer.name!r} may not carry detector overrides")
        for override in layer.detector_overrides:
            existing = detectors.get(override.id)
            if existing is None:
                raise PackCompositionError(
                    f"layer {layer.name!r}: detector override references unknown policy "
                    f"{override.id!r}"
                )
            detectors[override.id] = tune(existing, override.threshold, layer)
    return detectors


def compose(layers: Sequence[PackLayer]) -> ComposedPack:
    """Merge an ordered layer stack under the §5.4 override rules."""
    _validate_stack(layers)
    base = layers[0]

    concepts = _merge_concepts(layers)
    detectors = _merge_detectors(layers)

    codes: dict[tuple[CodeSystem, str], CodeDefinition] = {}
    metrics: dict[str, MetricContract] = {}
    bindings: dict[tuple[str, str], BindingCandidate] = {}
    playbooks: dict[str, Playbook] = {}
    conclusions: dict[str, ConclusionPolicy] = {}
    rankings: dict[str, RankingPolicy] = {}
    recipes: dict[str, PresentationRecipe] = {}
    filing_rules: dict[str, FilingRule] = {}

    for layer in layers:
        overlay = layer.kind is not PackLayerKind.BASE
        for code_def in layer.code_definitions:
            key = (code_def.code_system, code_def.code)
            if key in codes and overlay:
                raise _deny(
                    f"overlay {layer.name!r} may not redefine code "
                    f"{code_def.code_system.value.upper()} {code_def.code!r}",
                    layer=layer.name,
                    code=code_def.code,
                )
            codes[key] = code_def
        for contract in layer.metric_contracts:
            if contract.id in metrics and overlay:
                raise _deny(
                    f"overlay {layer.name!r} may not redefine metric {contract.id!r}: formula, "
                    "denominator, grain, and date basis are governed — new contract versions "
                    "arrive only through the promotion path",
                    layer=layer.name,
                    metric=contract.id,
                )
            metrics[contract.id] = contract
        for binding in layer.bindings:
            # permitted override class: tenant > organization > base
            bindings[(binding.concept_id, binding.dimension_or_measure_id)] = binding
        for playbook in layer.playbooks:
            if playbook.id in playbooks and overlay:
                raise _deny(
                    f"overlay {layer.name!r} may not redefine playbook {playbook.id!r}",
                    layer=layer.name,
                    playbook=playbook.id,
                )
            playbooks[playbook.id] = playbook
        for conclusion in layer.conclusion_policies:
            if conclusion.id in conclusions and overlay:
                raise _deny(
                    f"overlay {layer.name!r} may not redefine conclusion policy {conclusion.id!r}",
                    layer=layer.name,
                    policy=conclusion.id,
                )
            conclusions[conclusion.id] = conclusion
        for ranking in layer.ranking_policies:
            # permitted override class: ranking weights
            rankings[ranking.id] = ranking
        for recipe in layer.presentation_recipes:
            # permitted override class: presentation preferences
            recipes[recipe.id] = recipe
        for rule in layer.filing_rules:
            if rule.id in filing_rules and overlay:
                raise _deny(
                    f"overlay {layer.name!r} may not redefine filing rule {rule.id!r}",
                    layer=layer.name,
                    filing_rule=rule.id,
                )
            filing_rules[rule.id] = rule

    return ComposedPack(
        pack_id=base.name,
        version=base.version,
        layers=tuple(layers),
        concepts=tuple(concepts.values()),
        code_definitions=tuple(codes.values()),
        metric_contracts=tuple(metrics.values()),
        bindings=tuple(bindings.values()),
        playbooks=tuple(playbooks.values()),
        conclusion_policies=tuple(conclusions.values()),
        ranking_policies=tuple(rankings.values()),
        detector_policies=tuple(detectors.values()),
        presentation_recipes=tuple(recipes.values()),
        filing_rules=tuple(filing_rules.values()),
    )
