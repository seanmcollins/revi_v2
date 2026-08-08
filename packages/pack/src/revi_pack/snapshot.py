"""Assemble composed layers into an immutable :class:`PackSnapshot`.

The snapshot ``id`` is a SHA-256 content hash over the *composed* content
(design §5.4), built on :func:`revi_kernel.probes.canonicalize` — the same
canonical serializer used for probe hashes and contract fingerprints. The
hash is stable and order-independent where order is not semantic: artifact
collections are sorted by identity, concept aliases/related/sources and
knowledge-card aliases/domains/sources are sorted, while semantic order
(playbook probe sequence, ranking-weight order, card key points) is
preserved.

Integrity invariants (unique ids, no alias owned by two concepts, resolvable
playbook references) live in ``PackSnapshot.__post_init__`` itself, so they
hold no matter who constructs a snapshot; this module adds the cross-version
fingerprint rules and the hashing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import replace

from revi_calculation_contracts.contract import MetricContract
from revi_kernel.probes import canonicalize
from revi_pack.domain import (
    Concept,
    KnowledgeCard,
    PackLayer,
    PackLayerRef,
    PackSnapshot,
    PackVersion,
)
from revi_pack.errors import PackIntegrityError
from revi_pack.merge import ComposedPack, compose


def validate_contract_fingerprints(contracts: Iterable[MetricContract]) -> None:
    """Enforce the §5.2 fingerprint rules over a contract registry:

    - two contracts with the same id *and* version must not differ in
      fingerprint (meaning is never silently overwritten);
    - the same id at a *different* version must differ in fingerprint
      (a new version must change meaning).
    """
    by_id_version: dict[tuple[str, int], str] = {}
    by_id: dict[str, dict[int, str]] = {}
    for contract in contracts:
        fingerprint = contract.fingerprint
        key = (contract.id, contract.version)
        existing = by_id_version.get(key)
        if existing is not None and existing != fingerprint:
            raise PackIntegrityError(
                f"metric {contract.id!r} v{contract.version}: two contracts share id+version "
                "but differ in fingerprint — meaning is never silently overwritten"
            )
        by_id_version[key] = fingerprint
        versions = by_id.setdefault(contract.id, {})
        for other_version, other_fingerprint in versions.items():
            if other_version != contract.version and other_fingerprint == fingerprint:
                raise PackIntegrityError(
                    f"metric {contract.id!r}: versions {other_version} and {contract.version} "
                    "share a fingerprint — a new version must change meaning"
                )
        versions[contract.version] = fingerprint


def _sha256(payload: object) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _canonical_concept(concept: Concept) -> Concept:
    """Alias/related/source order is not semantic; sort it out of the hash."""
    return replace(
        concept,
        aliases=tuple(sorted(concept.aliases)),
        related=tuple(sorted(concept.related)),
        sources=tuple(sorted(concept.sources, key=lambda s: s.id)),
    )


def _canonical_card(card: KnowledgeCard) -> KnowledgeCard:
    """Alias/domain/source order is not semantic; key-point and caution
    order is (presentation order), so it stays in the hash."""
    return replace(
        card,
        aliases=tuple(sorted(card.aliases)),
        domains=tuple(sorted(card.domains)),
        sources=tuple(sorted(card.sources, key=lambda s: s.id)),
    )


def layer_content_hash(layer: PackLayer) -> str:
    """SHA-256 over one layer's canonical content (its identity in
    ``PackSnapshot.layers``)."""
    return _sha256(canonicalize(layer))


def snapshot_content_hash(composed: ComposedPack) -> str:
    """SHA-256 over the composed content — the ``PackSnapshot.id``."""
    payload: dict[str, object] = {
        "pack_id": composed.pack_id,
        "version": composed.version,
        "concepts": [
            canonicalize(_canonical_concept(c))
            for c in sorted(composed.concepts, key=lambda c: c.id)
        ],
        "code_definitions": [
            canonicalize(c)
            for c in sorted(composed.code_definitions, key=lambda c: (c.code_system.value, c.code))
        ],
        "knowledge_cards": [
            canonicalize(_canonical_card(k))
            for k in sorted(composed.knowledge_cards, key=lambda k: k.id)
        ],
        "benchmarks": [canonicalize(b) for b in sorted(composed.benchmarks, key=lambda b: b.id)],
        "metric_contracts": [
            canonicalize(m) for m in sorted(composed.metric_contracts, key=lambda m: m.id)
        ],
        "bindings": [
            canonicalize(b)
            for b in sorted(composed.bindings, key=lambda b: (b.concept_id, b.dimension_or_measure_id))
        ],
        "playbooks": [canonicalize(p) for p in sorted(composed.playbooks, key=lambda p: p.id)],
        "conclusion_policies": [
            canonicalize(p) for p in sorted(composed.conclusion_policies, key=lambda p: p.id)
        ],
        "ranking_policies": [
            canonicalize(p) for p in sorted(composed.ranking_policies, key=lambda p: p.id)
        ],
        "detector_policies": [
            canonicalize(p) for p in sorted(composed.detector_policies, key=lambda p: p.id)
        ],
        "presentation_recipes": [
            canonicalize(p) for p in sorted(composed.presentation_recipes, key=lambda p: p.id)
        ],
        "filing_rules": [canonicalize(r) for r in sorted(composed.filing_rules, key=lambda r: r.id)],
    }
    return _sha256(payload)


def build_snapshot(layers: Sequence[PackLayer]) -> PackSnapshot:
    """Compose a layer stack and assemble the immutable snapshot.

    Raises ``PolicyDeniedError`` for forbidden overlay overrides,
    ``PackCompositionError`` for structural layer problems, and
    ``PackIntegrityError`` for snapshot-integrity violations.
    """
    composed = compose(layers)
    validate_contract_fingerprints(composed.metric_contracts)
    return PackSnapshot(
        id=snapshot_content_hash(composed),
        version=PackVersion(pack_id=composed.pack_id, version=composed.version),
        layers=tuple(
            PackLayerRef(kind=layer.kind, name=layer.name, content_hash=layer_content_hash(layer))
            for layer in composed.layers
        ),
        concepts=composed.concepts,
        code_definitions=composed.code_definitions,
        knowledge_cards=composed.knowledge_cards,
        benchmarks=composed.benchmarks,
        metric_contracts=composed.metric_contracts,
        bindings=composed.bindings,
        playbooks=composed.playbooks,
        conclusion_policies=composed.conclusion_policies,
        ranking_policies=composed.ranking_policies,
        detector_policies=composed.detector_policies,
        presentation_recipes=composed.presentation_recipes,
        filing_rules=composed.filing_rules,
    )
