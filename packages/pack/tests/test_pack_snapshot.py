"""Snapshot tests: content hashing, definitional lookup, integrity invariants."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from revi_calculation_contracts.contract import SignConvention
from revi_pack.domain import (
    CodeSystem,
    Concept,
    KnowledgeCard,
    PackLayer,
    PackLayerKind,
    PackSnapshot,
    PackVersion,
    Playbook,
    ProbeTemplate,
    ReviewStatus,
    normalize_term,
)
from revi_pack.errors import PackIntegrityError
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot, validate_contract_fingerprints

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def base() -> PackLayer:
    return load_layer(FIXTURES / "base")


@pytest.fixture
def tenant() -> PackLayer:
    return load_layer(FIXTURES / "tenant")


@pytest.fixture
def snapshot(base: PackLayer, tenant: PackLayer) -> PackSnapshot:
    return build_snapshot([base, tenant])


def make_snapshot(**kwargs: object) -> PackSnapshot:
    return PackSnapshot(id="test-id", version=PackVersion("p", "1"), layers=(), **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# hashing


def test_snapshot_id_stable_across_reloads(base: PackLayer, tenant: PackLayer) -> None:
    first = build_snapshot([load_layer(FIXTURES / "base"), load_layer(FIXTURES / "tenant")])
    second = build_snapshot([base, tenant])
    assert first.id == second.id
    assert len(first.id) == 64  # sha256 hex


def test_snapshot_id_ignores_nonsemantic_order(base: PackLayer) -> None:
    reordered_concepts = replace(base, concepts=tuple(reversed(base.concepts)))
    cob = base.concepts[0]
    realiased = replace(
        base,
        concepts=(replace(cob, aliases=tuple(reversed(cob.aliases))), *base.concepts[1:]),
    )
    assert build_snapshot([base]).id == build_snapshot([reordered_concepts]).id
    assert build_snapshot([base]).id == build_snapshot([realiased]).id


def test_snapshot_id_changes_on_any_content_change(base: PackLayer, tenant: PackLayer) -> None:
    base_only = build_snapshot([base])
    with_tenant = build_snapshot([base, tenant])
    assert base_only.id != with_tenant.id

    cob = base.concepts[0]
    edited = replace(
        base, concepts=(replace(cob, definition=cob.definition + " (edited)"), *base.concepts[1:])
    )
    assert build_snapshot([edited]).id != base_only.id


def test_layer_refs_recorded(snapshot: PackSnapshot) -> None:
    assert [(ref.kind, ref.name) for ref in snapshot.layers] == [
        (PackLayerKind.BASE, "test-rcm"),
        (PackLayerKind.TENANT, "acme-health-tenant"),
    ]
    assert all(len(ref.content_hash) == 64 for ref in snapshot.layers)
    assert snapshot.version == PackVersion("test-rcm", "1.0.0")


# ---------------------------------------------------------------------------
# alias normalization and definitional lookup


def test_normalize_term() -> None:
    assert normalize_term("  Coordination--of  Benefits! ") == "coordination_of_benefits"
    assert normalize_term("PR-3") == "pr_3"
    assert normalize_term("pr 3") == "pr_3"
    assert normalize_term("pr3") == "pr3"


def test_concept_for_alias_normalization(snapshot: PackSnapshot) -> None:
    cob = snapshot.concept("cob")
    assert cob is not None
    assert snapshot.concept_for_alias("COB") is cob
    assert snapshot.concept_for_alias("  other   INSURANCE ") is cob
    assert snapshot.concept_for_alias("Coordination of Benefits") is cob  # name is implicit alias
    assert snapshot.concept_for_alias("dnl") is snapshot.concept("denial")  # tenant-added
    assert snapshot.concept_for_alias("no such thing") is None


@pytest.mark.parametrize("spelling", ["pr3", "PR-3", "pr 3", "PR 3", "Pr_3"])
def test_resolve_term_pr3_returns_group_code_and_carc(
    snapshot: PackSnapshot, spelling: str
) -> None:
    group = snapshot.code(CodeSystem.GROUP_CODE, "PR")
    carc = snapshot.code(CodeSystem.CARC, "3")
    assert group is not None and carc is not None
    assert snapshot.resolve_term(spelling) == (group, carc)


def test_resolve_term_exact_codes(snapshot: PackSnapshot) -> None:
    group = snapshot.code(CodeSystem.GROUP_CODE, "PR")
    carc = snapshot.code(CodeSystem.CARC, "3")
    assert snapshot.resolve_term("PR") == (group,)
    assert snapshot.resolve_term("carc 3") == (carc,)
    assert snapshot.resolve_term("3") == (carc,)


def test_resolve_term_concept_and_metric(snapshot: PackSnapshot) -> None:
    assert snapshot.resolve_term("denied claim") == (snapshot.concept("denial"),)
    assert snapshot.resolve_term("Denial Rate") == (snapshot.metric("denial_rate"),)
    assert snapshot.resolve_term("") == ()
    assert snapshot.resolve_term("complete mystery") == ()


def test_lookup_helpers(snapshot: PackSnapshot) -> None:
    assert snapshot.metric("denial_rate") is not None
    assert snapshot.metric("nope") is None
    assert snapshot.concept("cob") is not None
    assert snapshot.concept("nope") is None
    assert snapshot.code(CodeSystem.CARC, "3") is not None
    assert snapshot.code(CodeSystem.RARC, "3") is None


def test_card_and_benchmark_lookups(snapshot: PackSnapshot) -> None:
    card = snapshot.card("cob_denial_workup")
    assert card is not None
    assert card.review_status is ReviewStatus.MACHINE_RESEARCHED
    assert snapshot.card("nope") is None

    figures = snapshot.benchmarks_for_metric("denial_rate")
    assert [b.id for b in figures] == ["denial_rate_industry"]
    assert (figures[0].value_low, figures[0].value_high) == ("0.06", "0.13")
    assert snapshot.benchmarks_for_metric("denied_amount") == ()
    assert snapshot.benchmarks_for_metric("nope") == ()


def test_resolve_term_returns_knowledge_card(snapshot: PackSnapshot) -> None:
    card = snapshot.card("cob_denial_workup")
    cob = snapshot.concept("cob")
    assert card is not None and cob is not None
    assert snapshot.resolve_term("cob workup") == (card,)
    assert snapshot.resolve_term("Working COB denials") == (card,)  # title is implicit alias
    # A shared alias returns the concept first, then the card that elaborates it.
    assert snapshot.resolve_term("COB") == (cob, card)


# ---------------------------------------------------------------------------
# integrity invariants


def _concept(concept_id: str, *aliases: str) -> Concept:
    return Concept(
        id=concept_id, name=concept_id.title(), description="", definition="", aliases=aliases
    )


def _card(card_id: str, *aliases: str) -> KnowledgeCard:
    return KnowledgeCard(
        id=card_id,
        title=card_id.title(),
        domains=(),
        aliases=aliases,
        summary="s",
        key_points=(),
        cautions=(),
        authored_by="machine-researched (KB wave 1, 2026-08-07)",
        review_status=ReviewStatus.MACHINE_RESEARCHED,
    )


def test_duplicate_alias_across_concepts_rejected() -> None:
    with pytest.raises(PackIntegrityError, match="alias 'shared' is owned by two concepts"):
        make_snapshot(concepts=(_concept("a", "shared"), _concept("b", "Shared!")))


def test_duplicate_alias_across_cards_rejected() -> None:
    with pytest.raises(PackIntegrityError, match="alias 'shared' is owned by two knowledge cards"):
        make_snapshot(knowledge_cards=(_card("k1", "shared"), _card("k2", "Shared!")))


def test_card_may_share_alias_with_concept() -> None:
    concept = _concept("a", "shared")
    card = _card("a_card", "shared")  # a card elaborates a concept: legal
    snapshot = make_snapshot(concepts=(concept,), knowledge_cards=(card,))
    assert snapshot.resolve_term("shared") == (concept, card)


def test_duplicate_card_id_rejected() -> None:
    card = _card("k1")
    with pytest.raises(PackIntegrityError, match="duplicate knowledge card id 'k1'"):
        make_snapshot(knowledge_cards=(card, replace(card, title="Other")))


def test_duplicate_metric_id_rejected(base: PackLayer) -> None:
    contract = base.metric_contracts[0]
    with pytest.raises(PackIntegrityError, match="duplicate metric id"):
        make_snapshot(metric_contracts=(contract, contract))


def test_playbook_references_must_resolve(base: PackLayer) -> None:
    probe = ProbeTemplate(id="p1", metric_ids=("ghost_metric",))
    with pytest.raises(PackIntegrityError, match="unknown metric 'ghost_metric'"):
        make_snapshot(playbooks=(Playbook(id="pb", description="d", probes=(probe,)),))

    ok_probe = ProbeTemplate(id="p1", metric_ids=(base.metric_contracts[0].id,))
    with pytest.raises(PackIntegrityError, match="unknown conclusion policy"):
        make_snapshot(
            metric_contracts=(base.metric_contracts[0],),
            playbooks=(
                Playbook(
                    id="pb", description="d", probes=(ok_probe,), conclusion_policies=("ghost",)
                ),
            ),
        )
    with pytest.raises(PackIntegrityError, match="unknown ranking policy"):
        make_snapshot(
            metric_contracts=(base.metric_contracts[0],),
            playbooks=(
                Playbook(id="pb", description="d", probes=(ok_probe,), ranking_policy="ghost"),
            ),
        )


def test_fingerprint_rules(base: PackLayer) -> None:
    contract = next(m for m in base.metric_contracts if m.id == "denial_rate")

    # same id+version, same meaning, different prose: fine (prose is not meaning)
    validate_contract_fingerprints([contract, replace(contract, description="other prose")])

    # same id, new version, identical meaning: a new version must change meaning
    with pytest.raises(PackIntegrityError, match="share a fingerprint"):
        validate_contract_fingerprints([contract, replace(contract, version=2)])

    # same id+version, different meaning: meaning is never silently overwritten
    with pytest.raises(PackIntegrityError, match="differ in fingerprint"):
        validate_contract_fingerprints(
            [contract, replace(contract, sign=SignConvention.NEUTRAL)]
        )

    # same id, new version, changed meaning: the legal promotion-path shape
    validate_contract_fingerprints(
        [contract, replace(contract, version=2, sign=SignConvention.NEUTRAL)]
    )


def test_threshold_tune_survives_into_snapshot(snapshot: PackSnapshot) -> None:
    detector = next(p for p in snapshot.detector_policies if p.id == "denial_spike")
    assert detector.threshold == Decimal("0.12")


def test_benchmark_metric_must_resolve(base: PackLayer) -> None:
    ghost = replace(base.benchmarks[0], metric_id="ghost_metric")
    with pytest.raises(PackIntegrityError, match="unknown metric 'ghost_metric'"):
        make_snapshot(benchmarks=(ghost,))


def test_duplicate_benchmark_id_rejected(base: PackLayer) -> None:
    benchmark = base.benchmarks[0]
    with pytest.raises(PackIntegrityError, match="duplicate benchmark id"):
        make_snapshot(metric_contracts=base.metric_contracts, benchmarks=(benchmark, benchmark))


def test_snapshot_id_covers_cards_and_benchmarks(base: PackLayer) -> None:
    base_id = build_snapshot([base]).id

    card = base.knowledge_cards[0]
    edited_card = replace(base, knowledge_cards=(replace(card, summary=card.summary + " (edited)"),))
    assert build_snapshot([edited_card]).id != base_id

    benchmark = base.benchmarks[0]
    edited_benchmark = replace(base, benchmarks=(replace(benchmark, value_high="0.99"),))
    assert build_snapshot([edited_benchmark]).id != base_id

    # card alias order is not semantic
    realiased = replace(base, knowledge_cards=(replace(card, aliases=tuple(reversed(card.aliases))),))
    assert build_snapshot([realiased]).id == base_id
