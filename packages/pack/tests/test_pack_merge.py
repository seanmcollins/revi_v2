"""Merge tests: §5.4 overlay legality in both directions."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from revi_kernel.errors import PolicyDeniedError
from revi_kernel.grades import EvidenceGrade
from revi_pack.domain import (
    AliasOverride,
    BindingCandidate,
    BindingState,
    Concept,
    DetectorOverride,
    DetectorPolicy,
    PackLayer,
    PackLayerKind,
    Playbook,
    PresentationRecipe,
    RankingPolicy,
)
from revi_pack.errors import PackCompositionError
from revi_pack.loader import load_layer
from revi_pack.merge import compose

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def base() -> PackLayer:
    return load_layer(FIXTURES / "base")


@pytest.fixture
def tenant() -> PackLayer:
    return load_layer(FIXTURES / "tenant")


def overlay(**kwargs: object) -> PackLayer:
    return PackLayer(kind=PackLayerKind.TENANT, name="t-overlay", version="1", **kwargs)  # type: ignore[arg-type]


def test_tenant_alias_added_and_threshold_tuned(base: PackLayer, tenant: PackLayer) -> None:
    composed = compose([base, tenant])
    assert composed.pack_id == "test-rcm"
    assert composed.version == "1.0.0"

    denial = next(c for c in composed.concepts if c.id == "denial")
    assert denial.aliases == ("denied claim", "claim denial", "dnl", "hard denial")
    # everything but aliases is untouched
    assert denial.definition == next(c for c in base.concepts if c.id == "denial").definition

    detector = next(p for p in composed.detector_policies if p.id == "denial_spike")
    assert detector.threshold == Decimal("0.12")
    assert detector.threshold_min == Decimal("0.05")  # range comes from the base declaration
    assert detector.threshold_max == Decimal("0.25")
    assert detector.description == base.detector_policies[0].description

    # base artifacts carried through
    assert {m.id for m in composed.metric_contracts} == {"denial_rate", "denied_amount"}
    assert [p.id for p in composed.playbooks] == ["denial_overview"]


def test_alias_replace_semantics(base: PackLayer) -> None:
    layer = overlay(
        alias_overrides=(AliasOverride(concept_id="denial", replace_aliases=("only alias",)),)
    )
    composed = compose([base, layer])
    denial = next(c for c in composed.concepts if c.id == "denial")
    assert denial.aliases == ("only alias",)


def test_metric_redefinition_denied(base: PackLayer) -> None:
    bad = load_layer(FIXTURES / "tenant_bad_metric")
    with pytest.raises(PolicyDeniedError, match="may not redefine metric 'denial_rate'"):
        compose([base, bad])


def test_threshold_outside_range_denied(base: PackLayer) -> None:
    for outside in (Decimal("0.30"), Decimal("0.01")):
        layer = overlay(
            detector_overrides=(DetectorOverride(id="denial_spike", threshold=outside),)
        )
        with pytest.raises(PolicyDeniedError, match="outside the declared range"):
            compose([base, layer])


def test_full_redeclaration_within_range_is_a_tune(base: PackLayer) -> None:
    declared = base.detector_policies[0]
    layer = overlay(detector_policies=(replace(declared, threshold=Decimal("0.2")),))
    composed = compose([base, layer])
    assert composed.detector_policies[0].threshold == Decimal("0.2")


def test_range_redeclaration_denied(base: PackLayer) -> None:
    declared = base.detector_policies[0]
    widened = replace(declared, threshold_max=Decimal("0.9"))
    layer = overlay(detector_policies=(widened,))
    with pytest.raises(PolicyDeniedError, match="tune the threshold only"):
        compose([base, layer])


def test_base_layer_must_be_present(tenant: PackLayer) -> None:
    with pytest.raises(PackCompositionError, match="first layer must be the base layer"):
        compose([tenant])
    with pytest.raises(PackCompositionError, match="empty layer stack"):
        compose([])


def test_layer_order_enforced(base: PackLayer, tenant: PackLayer) -> None:
    org = PackLayer(kind=PackLayerKind.ORGANIZATION, name="org", version="1")
    with pytest.raises(PackCompositionError, match="base -> organization -> tenant"):
        compose([base, tenant, org])
    with pytest.raises(PackCompositionError, match="base -> organization -> tenant"):
        compose([base, replace(base, name="base-2")])


def test_ranking_and_presentation_overrides_permitted(base: PackLayer) -> None:
    layer = overlay(
        ranking_policies=(
            RankingPolicy(
                id="impact_first",
                weights=(("impact", Decimal("0.9")), ("recency", Decimal("0.1"))),
                description="Tenant prefers impact even harder.",
            ),
        ),
        presentation_recipes=(
            PresentationRecipe(
                id="denial_rate_trend", applies_to="denial_rate", chart_type="bar", notes=""
            ),
        ),
    )
    composed = compose([base, layer])
    assert composed.ranking_policies[0].weights[0] == ("impact", Decimal("0.9"))
    assert composed.presentation_recipes[0].chart_type == "bar"


def test_playbook_redefinition_denied(base: PackLayer) -> None:
    layer = overlay(playbooks=(Playbook(id="denial_overview", description="hijacked"),))
    with pytest.raises(PolicyDeniedError, match="may not redefine playbook"):
        compose([base, layer])


def test_binding_override_tenant_wins(base: PackLayer) -> None:
    tenant_binding = BindingCandidate(
        concept_id="denial",
        dimension_or_measure_id="carc",
        state=BindingState.VALIDATED,
        strength=EvidenceGrade.PROXY,
        rationale="Tenant remit feed lacks the definitive flag.",
    )
    composed = compose([base, overlay(bindings=(tenant_binding,))])
    assert composed.bindings == (tenant_binding,)


def test_new_artifacts_in_overlay_are_additive(base: PackLayer) -> None:
    new_metric = replace(base.metric_contracts[0], id="tenant_specific_rate")
    new_concept = Concept(
        id="secondary_billing",
        name="Secondary Billing",
        description="",
        definition="Billing the secondary payer after primary adjudication.",
        aliases=("secondary claims",),
    )
    composed = compose([base, overlay(metric_contracts=(new_metric,), concepts=(new_concept,))])
    assert "tenant_specific_rate" in {m.id for m in composed.metric_contracts}
    assert "secondary_billing" in {c.id for c in composed.concepts}


def test_concept_redefinition_denied(base: PackLayer) -> None:
    clone = next(c for c in base.concepts if c.id == "denial")
    with pytest.raises(PolicyDeniedError, match="may not redefine concept 'denial'"):
        compose([base, overlay(concepts=(clone,))])


def test_alias_override_for_unknown_concept_rejected(base: PackLayer) -> None:
    layer = overlay(alias_overrides=(AliasOverride(concept_id="ghost", add_aliases=("x",)),))
    with pytest.raises(PackCompositionError, match="unknown concept 'ghost'"):
        compose([base, layer])


def test_detector_override_for_unknown_policy_rejected(base: PackLayer) -> None:
    layer = overlay(detector_overrides=(DetectorOverride(id="ghost", threshold=Decimal("0.1")),))
    with pytest.raises(PackCompositionError, match="unknown policy 'ghost'"):
        compose([base, layer])


def test_new_detector_policy_in_overlay_is_additive(base: PackLayer) -> None:
    new_policy = DetectorPolicy(
        id="tenant_lag_watch",
        description="Tenant-specific remit lag detector.",
        threshold=Decimal("5"),
        threshold_min=Decimal("1"),
        threshold_max=Decimal("30"),
    )
    composed = compose([base, overlay(detector_policies=(new_policy,))])
    assert {p.id for p in composed.detector_policies} == {"denial_spike", "tenant_lag_watch"}
