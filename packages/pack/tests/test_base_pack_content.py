"""Wire-up tests for the committed base RCM pack content (packs/base-rcm)
plus the demo-tenant overlay, loaded through the real loader and composed by
the real snapshot builder. Content-level invariants live here; structural
loader/merge/snapshot behavior is covered by the sibling test modules."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from revi_calculation_contracts.contract import (
    Count,
    CountDistinct,
    Filtered,
    MeasureExpr,
    MetricKind,
    Sum,
)
from revi_kernel.refs import DateBasisRef, EntityGrain
from revi_pack.domain import CodeDefinition, CodeSystem, Concept, PackSnapshot
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_PACK_DIR = REPO_ROOT / "packs" / "base-rcm"
OVERLAY_DIR = REPO_ROOT / "packs" / "overlays" / "demo-tenant"
CATALOG_DIR = REPO_ROOT / "warehouse" / "catalog"

# Probe-time derived measures documented in packs/base-rcm/NOTES.md. A sum
# field must be a catalog measure id or appear here; growing this set means
# growing the NOTES.md registry too.
DERIVED_SUM_MEASURES = frozenset(
    {
        "ar_age_days_billed_cents",
        "underpayment_cents",
        "credit_balance_cents",
        "payment_lag_days",
        "charge_entry_lag_days",
        "submission_lag_days",
        "late_charge_cents",
    }
)


@pytest.fixture(scope="module")
def snapshot() -> PackSnapshot:
    base = load_layer(BASE_PACK_DIR)
    tenant = load_layer(OVERLAY_DIR)
    return build_snapshot([base, tenant])


@pytest.fixture(scope="module")
def catalog_dimension_ids() -> frozenset[str]:
    document = yaml.safe_load((CATALOG_DIR / "dimensions.yaml").read_text(encoding="utf-8"))
    return frozenset(document["dimensions"])


@pytest.fixture(scope="module")
def catalog_measures() -> dict[str, dict[str, object]]:
    document = yaml.safe_load((CATALOG_DIR / "measures.yaml").read_text(encoding="utf-8"))
    return dict(document["measures"])


def _iter_fields(expr: MeasureExpr | None) -> list[tuple[str, str]]:
    """Yield (shape, field_id) references from a measure expression."""
    if expr is None:
        return []
    if isinstance(expr, Sum):
        return [("sum", expr.field.id)]
    if isinstance(expr, CountDistinct):
        return [("count_distinct", expr.field.id)]
    if isinstance(expr, Count):
        return []
    if isinstance(expr, Filtered):
        return _iter_fields(expr.inner)
    raise AssertionError(f"unknown measure expression {expr!r}")


# ---------------------------------------------------------------------------
# composition and volume


def test_snapshot_composes_base_plus_tenant(snapshot: PackSnapshot) -> None:
    assert snapshot.version.pack_id == "base-rcm"
    assert [layer.kind.value for layer in snapshot.layers] == ["base", "tenant"]
    assert all(layer.content_hash for layer in snapshot.layers)


def test_concept_breadth(snapshot: PackSnapshot) -> None:
    assert len(snapshot.concepts) >= 60


def test_metric_breadth(snapshot: PackSnapshot) -> None:
    assert len(snapshot.metric_contracts) >= 22


def test_playbooks_policies_rules_present(snapshot: PackSnapshot) -> None:
    playbook_ids = {p.id for p in snapshot.playbooks}
    assert {
        "cash_decline",
        "denial_spike",
        "cob_investigation",
        "underpayment_review",
        "timely_filing_watch",
        "dimension_scorecard",
        "cash_outlook",
        "daily_portfolio",
    } <= playbook_ids
    assert snapshot.conclusion_policies
    assert snapshot.ranking_policies
    assert snapshot.detector_policies
    assert snapshot.presentation_recipes
    assert len(snapshot.filing_rules) >= 5


# ---------------------------------------------------------------------------
# metric contracts vs the semantic catalog


def test_scope_dimensions_exist_in_catalog(
    snapshot: PackSnapshot, catalog_dimension_ids: frozenset[str]
) -> None:
    for contract in snapshot.metric_contracts:
        unknown = {d.id for d in contract.scope_dimensions} - catalog_dimension_ids
        assert not unknown, f"{contract.id}: scope dimensions not in catalog: {sorted(unknown)}"


def test_measure_fields_exist_in_catalog_or_derived_set(
    snapshot: PackSnapshot, catalog_measures: dict[str, dict[str, object]]
) -> None:
    measure_ids = frozenset(catalog_measures)
    count_columns = frozenset(
        str(spec["column"])
        for spec in catalog_measures.values()
        if spec.get("aggregation") == "count_distinct"
    )
    for contract in snapshot.metric_contracts:
        for expr in (contract.numerator, contract.denominator):
            for shape, field_id in _iter_fields(expr):
                if shape == "sum":
                    assert field_id in measure_ids | DERIVED_SUM_MEASURES, (
                        f"{contract.id}: sum field {field_id!r} is neither a catalog "
                        "measure nor a documented derived measure"
                    )
                else:
                    assert field_id in count_columns, (
                        f"{contract.id}: count_distinct field {field_id!r} is not a "
                        "catalog count-measure column"
                    )


def test_metric_date_bases_exist_in_catalog(snapshot: PackSnapshot) -> None:
    document = yaml.safe_load((CATALOG_DIR / "date_bases.yaml").read_text(encoding="utf-8"))
    catalog_bases = {name.lower() for name in document["date_bases"]}
    for contract in snapshot.metric_contracts:
        for basis in contract.allowed_date_bases:
            assert basis.id in catalog_bases, f"{contract.id}: unknown date basis {basis.id!r}"


def test_denial_rate_contract_shape(snapshot: PackSnapshot) -> None:
    contract = snapshot.metric(denial_rate_id := "denial_rate")
    assert contract is not None, denial_rate_id
    assert contract.kind is MetricKind.FLOW
    assert contract.entity_grain is EntityGrain.CLAIM
    assert contract.primary_date_basis == DateBasisRef("remit")
    assert contract.allows_date_basis(DateBasisRef("service"))
    assert contract.allows_date_basis(DateBasisRef("submission"))
    # The GRAIN_INCOMPATIBLE demo: carc is not a legal cut on the claim-grain rate.
    assert "carc" not in {d.id for d in contract.scope_dimensions}
    # ...but it is a legal cut on line-grain denied dollars.
    denied = snapshot.metric("denied_dollars")
    assert denied is not None
    assert "carc" in {d.id for d in denied.scope_dimensions}


def test_planned_metric_set_present(snapshot: PackSnapshot) -> None:
    expected = {
        "denial_rate",
        "initial_denial_rate",
        "denied_dollars",
        "clean_claim_rate",
        "first_pass_yield",
        "cash_posted",
        "patient_cash_posted",
        "charges",
        "claim_volume",
        "days_in_ar",
        "ar_balance",
        "ar_over_90_pct",
        "net_collection_rate",
        "gross_collection_rate",
        "underpayment_variance",
        "denial_write_off_dollars",
        "credit_balance_dollars",
        "appeal_overturn_rate",
        "denials_unworked_pct",
        "timely_filing_at_risk_dollars",
        "avg_days_to_pay",
        "charge_lag_days",
        "bill_lag_days",
        "dnfb_dollars",
        "late_charge_pct",
    }
    present = {m.id for m in snapshot.metric_contracts}
    assert expected <= present, f"missing metrics: {sorted(expected - present)}"


def test_snapshot_metrics_are_snapshot_kind(snapshot: PackSnapshot) -> None:
    for metric_id in (
        "days_in_ar",
        "ar_balance",
        "ar_over_90_pct",
        "credit_balance_dollars",
        "timely_filing_at_risk_dollars",
        "dnfb_dollars",
    ):
        contract = snapshot.metric(metric_id)
        assert contract is not None, metric_id
        assert contract.kind is MetricKind.SNAPSHOT, metric_id


# ---------------------------------------------------------------------------
# definitional resolution (the DEFINITIONAL turn path)


def test_resolve_pr3_returns_group_code_and_carc(snapshot: PackSnapshot) -> None:
    matches = snapshot.resolve_term("pr3")
    codes = [m for m in matches if isinstance(m, CodeDefinition)]
    assert (CodeSystem.GROUP_CODE, "PR") in {(c.code_system, c.code) for c in codes}
    assert (CodeSystem.CARC, "3") in {(c.code_system, c.code) for c in codes}
    # Spelling variants normalize to the same pair.
    assert snapshot.resolve_term("PR-3") == matches


def test_resolve_dnfb_hits_concept(snapshot: PackSnapshot) -> None:
    matches = snapshot.resolve_term("dnfb")
    assert any(isinstance(m, Concept) and m.id == "dnfb" for m in matches)
    # EHR-native vocabulary reaches the same concept.
    epic = snapshot.concept_for_alias("DNB")
    assert epic is not None and epic.id == "dnfb"


def test_resolve_clean_claim_rate_hits_metric(snapshot: PackSnapshot) -> None:
    matches = snapshot.resolve_term("clean claim rate")
    assert any(getattr(m, "id", None) == "clean_claim_rate" for m in matches)


def test_group_codes_and_carcs_governed(snapshot: PackSnapshot) -> None:
    for group in ("CO", "PR", "OA", "PI", "CR"):
        assert snapshot.code(CodeSystem.GROUP_CODE, group) is not None, group
    carcs = [c for c in snapshot.code_definitions if c.code_system is CodeSystem.CARC]
    assert len(carcs) == 20
    rarcs = [c for c in snapshot.code_definitions if c.code_system is CodeSystem.RARC]
    assert len(rarcs) >= 8
    # CR is honest about never appearing in the mock data.
    cr = snapshot.code(CodeSystem.GROUP_CODE, "CR")
    assert cr is not None and "never emits" in cr.definition_paraphrase


# ---------------------------------------------------------------------------
# overlay effects (permitted override classes only)


def test_tenant_alias_patch_applied(snapshot: PackSnapshot) -> None:
    facility = snapshot.concept_for_alias("the university hospital")
    assert facility is not None and facility.id == "facility"


def test_tenant_detector_tune_applied_within_range(snapshot: PackSnapshot) -> None:
    detector = next(p for p in snapshot.detector_policies if p.id == "denial_rate_spike")
    assert detector.threshold == Decimal("0.20")
    assert detector.threshold_min <= detector.threshold <= detector.threshold_max


def test_tenant_presentation_preference_applied(snapshot: PackSnapshot) -> None:
    recipe = next(r for r in snapshot.presentation_recipes if r.id == "cash_trend")
    assert recipe.chart_type == "area"
