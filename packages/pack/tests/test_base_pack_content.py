"""Wire-up tests for the committed base RCM pack content (packs/base-rcm)
plus the demo-tenant overlay, loaded through the real loader and composed by
the real snapshot builder. Content-level invariants live here; structural
loader/merge/snapshot behavior is covered by the sibling test modules."""

from decimal import Decimal, InvalidOperation
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
from revi_kernel.filters import And, Not, Predicate, PredicateOp, iter_predicates
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DateBasisRef, EntityGrain
from revi_pack.domain import (
    CodeDefinition,
    CodeSystem,
    Concept,
    KnowledgeCard,
    PackSnapshot,
    ReviewStatus,
)
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
    assert len(snapshot.concepts) >= 100


def test_metric_breadth(snapshot: PackSnapshot) -> None:
    assert len(snapshot.metric_contracts) >= 45


def test_knowledge_and_benchmark_breadth(snapshot: PackSnapshot) -> None:
    assert len(snapshot.knowledge_cards) >= 40
    assert len(snapshot.benchmarks) >= 20


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


def test_numerator_filter_dimensions_are_certified_catalog_dimensions(
    snapshot: PackSnapshot, catalog_dimension_ids: frozenset[str]
) -> None:
    """The gap that kept `dnfb_dollars` and `timely_filing_at_risk_dollars`
    dark for four milestones.

    ``validate_pack_catalog_conformance`` walks a contract's ``exclusions:``
    only, and the two tests above walk measure *fields* and scope dimensions.
    Nothing walked the predicates inside a ``filtered:`` numerator — so both
    contracts could name `submission_date` / `discharge_date`, which are date
    BASES and not dimensions, and every content test stayed green while the
    probe raised UNSUPPORTED_CONCEPT the first time anyone asked. This closes
    that hole for every shipped contract, not only the two that had it.
    """
    document = yaml.safe_load((CATALOG_DIR / "dimensions.yaml").read_text(encoding="utf-8"))
    certified = {name for name, spec in document["dimensions"].items() if spec["certified"]}
    seen = 0
    for contract in snapshot.metric_contracts:
        for expr in (contract.numerator, contract.denominator):
            if not isinstance(expr, Filtered):
                continue
            for predicate in iter_predicates(expr.where):
                seen += 1
                dimension_id = predicate.dimension.id
                assert dimension_id in catalog_dimension_ids, (
                    f"{contract.id}: filter dimension {dimension_id!r} is not a catalog "
                    "dimension (a date basis is not a dimension)"
                )
                assert dimension_id in certified, (
                    f"{contract.id}: filter dimension {dimension_id!r} is uncertified, "
                    "which would downgrade every answer built on this contract"
                )
    # Guards the guard: a walk that examined nothing would also report nothing.
    assert seen >= 25


def test_unbilled_inventory_contracts_filter_on_the_certified_flags(
    snapshot: PackSnapshot,
) -> None:
    """Closed-set pin, spelled out with polarity.

    A ``filtered:`` predicate names the population to KEEP (the opposite of
    ``exclusions:``), so DNFB is discharged AND NOT billed, and getting either
    polarity backwards is structurally invisible — both forms compile, and both
    return a plausible dollar figure. `billed_flag` / `discharged_flag` are the
    certified boolean dimensions standing in for `submission_date IS NULL` /
    `discharge_date IS NOT NULL`; the dates stay date bases (a window rides on
    them) and are not dimensions.
    """
    expected = {
        "dnfb_dollars": {
            ("discharged_flag", PredicateOp.EQ, (True,)),
            ("billed_flag", PredicateOp.EQ, (False,)),
        },
        "timely_filing_at_risk_dollars": {
            ("billed_flag", PredicateOp.EQ, (False,)),
            ("status", PredicateOp.EQ, ("OPEN",)),
        },
    }
    for metric_id, predicates in expected.items():
        contract = snapshot.metric(metric_id)
        assert contract is not None, metric_id
        numerator = contract.numerator
        assert isinstance(numerator, Filtered), metric_id
        assert isinstance(numerator.where, And), metric_id
        actual = {
            (p.dimension.id, p.op, p.values)
            for p in numerator.where.clauses
            if isinstance(p, Predicate)
        }
        assert actual == predicates, metric_id
        # No `not:` wrapper survives: the flags express both halves directly.
        assert not any(isinstance(clause, Not) for clause in numerator.where.clauses), metric_id


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
    # ...but it is a legal cut on denial-entity denied dollars (v2 rebound the
    # contract from `line` to `denial` so its measure and code dimensions all
    # bind on v_denial and claim cohorts semi-join through denial → claim).
    denied = snapshot.metric("denied_dollars")
    assert denied is not None
    assert denied.entity_grain is EntityGrain.DENIAL
    assert denied.version == 2
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


# ---------------------------------------------------------------------------
# coverage wave 2 (2026-08-08) — the widened governed surface
#
# Closed sets, per packs/base-rcm/NOTES.md: growing the pack means growing
# these, and a removal has to be argued rather than noticed later.

WAVE_2_METRICS = frozenset(
    {
        # dollars and volume rulers the catalog could always compute
        "allowed_dollars",
        "expected_reimbursement",
        "line_charges",
        "line_volume",
        "patient_responsibility_dollars",
        "contractual_adjustment_dollars",
        "refund_dollars",
        "denied_ar_dollars",
        "denied_claims",
        "denial_volume",
        "appeal_volume",
        "overturned_denied_dollars",
        "remit_volume",
        # ratios
        "net_to_gross_rate",
        "patient_responsibility_rate",
        "write_off_rate",
        "refund_rate",
        "claim_resolution_rate",
        "ar_over_120_pct",
        "appeal_overturn_dollar_rate",
        "appeals_pending_pct",
        "denials_unworked_dollar_pct",
    }
)

WAVE_2_PLAYBOOKS = frozenset(
    {
        "ar_aging_review",
        "appeals_effectiveness",
        "credit_balance_review",
        "clean_claim_review",
        "patient_responsibility_review",
        "charge_capture_review",
        "payer_scorecard",
        "denial_category_drilldown",
        "volume_mix_shift",
        "write_off_review",
    }
)

# Named scopes: each resolves to a predicate over ONE certified catalog
# dimension, named in the concept's definition. `high_dollar_claims` is the
# deliberate exception — it binds `unavailable` because claim value lives in
# measures, never in a dimension, so no legal predicate exists (NOTES.md).
WAVE_2_COHORT_CONCEPTS: dict[str, str] = {
    "government_payers": "payer_type",
    "commercial_payers": "payer_type",
    "managed_care": "product_type",
    "medicare_advantage": "payer_type",
    "traditional_medicare": "payer_type",
    "medicaid_coverage": "payer_type",
    "surgical_services": "service_line",
}


def test_wave_2_metrics_present_and_versioned_from_one(snapshot: PackSnapshot) -> None:
    present = {m.id for m in snapshot.metric_contracts}
    assert present >= WAVE_2_METRICS, f"missing: {sorted(WAVE_2_METRICS - present)}"
    for metric_id in WAVE_2_METRICS:
        contract = snapshot.metric(metric_id)
        assert contract is not None
        # New contracts, never a revision of an existing meaning: a v1 here
        # is the evidence that nothing shipped was redefined underneath.
        assert contract.version == 1, metric_id
        assert contract.description.strip(), f"{metric_id}: a contract without prose is not governed"


def test_wave_2_playbooks_resolve_their_policies_and_metrics(snapshot: PackSnapshot) -> None:
    playbooks = {p.id: p for p in snapshot.playbooks}
    assert playbooks.keys() >= WAVE_2_PLAYBOOKS, f"missing: {sorted(WAVE_2_PLAYBOOKS - playbooks.keys())}"
    policy_ids = {p.id for p in snapshot.conclusion_policies}
    metric_ids = {m.id for m in snapshot.metric_contracts}
    for playbook_id in sorted(WAVE_2_PLAYBOOKS):
        playbook = playbooks[playbook_id]
        assert playbook.triggers, f"{playbook_id}: a playbook with no triggers is unreachable"
        assert playbook.probes, playbook_id
        assert playbook.conclusion_policies, f"{playbook_id}: no conclusion policy"
        assert set(playbook.conclusion_policies) <= policy_ids, playbook_id
        for probe in playbook.probes:
            assert set(probe.metric_ids) <= metric_ids, f"{playbook_id}/{probe.id}"
            assert probe.scope_note.strip(), f"{playbook_id}/{probe.id}: probes state their scope"


def test_wave_2_conclusion_policies_require_evidence_their_playbook_produces(
    snapshot: PackSnapshot,
) -> None:
    """A required-evidence id that no probe template emits is a conclusion
    that can never be reached — and nothing else would report it."""
    policies = {p.id: p for p in snapshot.conclusion_policies}
    for playbook in snapshot.playbooks:
        if playbook.id not in WAVE_2_PLAYBOOKS:
            continue
        probe_ids = {probe.id for probe in playbook.probes}
        for policy_id in playbook.conclusion_policies:
            required = set(policies[policy_id].required_evidence)
            assert required, f"{policy_id}: a conclusion policy must require evidence"
            missing = required - probe_ids
            assert not missing, f"{playbook.id}/{policy_id}: no probe emits {sorted(missing)}"


def test_wave_2_cohort_concepts_bind_to_certified_dimensions(
    snapshot: PackSnapshot, catalog_dimension_ids: frozenset[str]
) -> None:
    concept_ids = {c.id for c in snapshot.concepts}
    bindings: dict[str, set[str]] = {}
    for binding in snapshot.bindings:
        bindings.setdefault(binding.concept_id, set()).add(binding.dimension_or_measure_id)
    for concept_id, dimension_id in WAVE_2_COHORT_CONCEPTS.items():
        assert concept_id in concept_ids, concept_id
        assert dimension_id in catalog_dimension_ids, dimension_id
        assert dimension_id in bindings.get(concept_id, set()), (
            f"{concept_id}: no binding to {dimension_id!r} — a named scope without a "
            "binding is a phrase, not a governed filter"
        )


def test_high_dollar_cohort_is_declared_unavailable_rather_than_approximated(
    snapshot: PackSnapshot,
) -> None:
    """Claim value is a measure, not a dimension, so no dollar-threshold
    predicate is legal. The concept exists so the term resolves and the
    answer can say that, instead of quietly filtering on something else."""
    concept = snapshot.concept("high_dollar_claims")
    assert concept is not None
    binding = next(b for b in snapshot.bindings if b.concept_id == "high_dollar_claims")
    assert binding.strength is EvidenceGrade.UNAVAILABLE
    assert binding.dimension_or_measure_id == "billed_amount_cents"


def test_anomaly_actionability_covers_every_emitted_category() -> None:
    """Pack-adjacent governed content (read by the API composition root, not
    by the pack loader) and therefore otherwise untested here. Every category
    the warehouse's detection feed emits must have an argued rule: falling to
    the blanket 50% default sets the worklist order by accident."""
    document = yaml.safe_load((BASE_PACK_DIR / "anomaly_actionability.yaml").read_text(encoding="utf-8"))
    rules = document["categories"]
    emitted = {
        "DENIAL_SPIKE",
        "UNWORKED_DENIALS",
        "ELIGIBILITY_CLUSTER",
        "DUPLICATE",
        "UNDERPAYMENT",
        "CONTRACTUAL",
        "POSTING_LAG",
        "SUBMISSION_GAP",
        "TIMELY_FILING",
        "DNFB",
        "CREDIT_BALANCE",
        "CHARGE_ENTRY_LAG",
        "CHARGE_HOLD",
    }
    assert emitted <= rules.keys(), f"no governed rule for {sorted(emitted - rules.keys())}"
    for category, rule in rules.items():
        assert rule["mode"] in ("fraction", "open_share", "flag_share"), category
        assert Decimal(0) <= Decimal(str(rule["fraction"])) <= Decimal(1), category
        assert rule.get("rationale", "").strip(), f"{category}: a fraction without an argument"
        if rule["mode"] == "open_share":
            assert rule.get("open_fact") and rule.get("expired_fact"), category
        if rule["mode"] == "flag_share":
            assert rule.get("numerator_fact") and rule.get("denominator_fact"), category


# ---------------------------------------------------------------------------
# audit response (2026-08-08) — the wave-2 corrections, pinned
#
# Each of these existed as a claim in prose that turned out to be false or
# as a binding whose blast radius nobody had measured. Prose cannot fail a
# test; these can. See packs/base-rcm/NOTES.md, "Audit response".


def test_unworked_denials_prices_zero_appealable_at_zero() -> None:
    """The flat 0.45 could not see zero: ANM-004 (14 denials, all past the
    appeal window, appealable_claims = 0) was published as $27,916
    "partially recoverable" — money no appeal can reach. flag_share over
    the record's own appealable share is exactly right at that end."""
    document = yaml.safe_load((BASE_PACK_DIR / "anomaly_actionability.yaml").read_text(encoding="utf-8"))
    rule = document["categories"]["UNWORKED_DENIALS"]
    assert rule["mode"] == "flag_share"
    assert rule["numerator_fact"] == "appealable_claims"
    assert rule["denominator_fact"] == "denied_claims"
    # the same fact pair and mode DENIAL_SPIKE already uses on this evidence
    spike = document["categories"]["DENIAL_SPIKE"]
    assert (rule["numerator_fact"], rule["denominator_fact"]) == (
        spike["numerator_fact"],
        spike["denominator_fact"],
    )
    # arithmetic of the mode, over the answer-key facts for ANM-004/027/028
    for appealable, denied, expected in ((0, 14, 0.0), (17, 29, 17 / 29), (17, 17, 1.0)):
        assert appealable / denied == pytest.approx(expected)


def test_no_recipe_binds_a_shared_measure_that_would_hijack_bare_frames(
    snapshot: PackSnapshot,
) -> None:
    """`_pick_recipe` matches on measure name with first match winning, so a
    recipe bound to a measure several playbooks report captures every frame
    carrying it — `volume_mix_share` on `charges` turned bare charge trends
    into single-series stacked bars. Mix is a property of the investigation,
    so the binding is the playbook id."""
    playbook_ids = {p.id for p in snapshot.playbooks}
    mix = next(r for r in snapshot.presentation_recipes if r.id == "volume_mix_share")
    assert mix.applies_to == "volume_mix_shift"
    assert mix.applies_to in playbook_ids
    assert not [r.id for r in snapshot.presentation_recipes if r.applies_to == "charges"]


def test_bare_charges_trend_falls_back_to_the_line_heuristic(snapshot: PackSnapshot) -> None:
    """The behavior the rebinding restores, driven through the real
    presentation layer with the real pack recipes: a weekly charges frame
    outside the mix playbook takes no recipe and charts as a line."""
    from datetime import date, datetime

    from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
    from revi_kernel.grades import EvidenceGrade
    from revi_kernel.refs import DimensionRef, MetricRef
    from revi_kernel.watermark import DataWatermark
    from revi_presentation import RecipeSpec, build_chart_spec

    recipes = tuple(
        RecipeSpec(id=r.id, applies_to=r.applies_to, chart_type=r.chart_type, notes=r.notes)
        for r in snapshot.presentation_recipes
    )
    frame = EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("week", DimensionRef("time_bucket:week")),
                FrameColumn("charges", MetricRef("charges"), 1, "money_cents"),
            )
        ),
        rows=((date(2026, 7, 27), 100), (date(2026, 8, 3), 120)),
        watermark=DataWatermark(
            id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
        ),
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )
    bare = build_chart_spec("charge_trend", frame, recipes=recipes)
    assert bare is not None
    assert bare.recipe_id is None, f"a recipe captured a bare charges frame: {bare.recipe_id}"
    assert bare.chart_type == "line"
    # ...and inside the mix playbook the recipe still applies, by playbook id
    in_playbook = build_chart_spec(
        "charge_trend", frame, recipes=recipes, playbook_id="volume_mix_shift"
    )
    assert in_playbook is not None
    assert in_playbook.recipe_id == "volume_mix_share"
    assert in_playbook.chart_type == "stacked_bar"


def test_denied_inventory_aging_probe_materializes_the_stacked_recipe(
    snapshot: PackSnapshot, catalog_dimension_ids: frozenset[str]
) -> None:
    """The `denied_inventory_aging` recipe describes buckets stacked per
    payer. A recipe whose shape no probe emits is decoration, so the probe
    that produces it is pinned here: two certified dimensions, both legal
    cuts of the contract, which the renderer turns into x=payer with the
    age bucket as the series."""
    playbook = next(p for p in snapshot.playbooks if p.id == "ar_aging_review")
    probe = next(p for p in playbook.probes if p.id == "denied_inventory_aging")
    assert probe.metric_ids == ("denied_ar_dollars",)
    assert probe.dimensions == ("payer", "ar_age_bucket")
    assert set(probe.dimensions) <= catalog_dimension_ids
    contract = snapshot.metric("denied_ar_dollars")
    assert contract is not None
    scope = {d.id for d in contract.scope_dimensions}
    assert set(probe.dimensions) <= scope, "the probe cuts a dimension the contract does not allow"
    recipe = next(r for r in snapshot.presentation_recipes if r.id == "denied_inventory_aging")
    assert recipe.applies_to == "denied_ar_dollars" and recipe.chart_type == "stacked_bar"


def test_playbook_descriptions_front_load_within_the_selection_clip(
    snapshot: PackSnapshot,
) -> None:
    """Playbook selection reads `playbook_summaries()` clipped to 160 chars
    and nothing else — `triggers:` has no runtime consumer. So a
    disambiguation past the clip is a disambiguation nobody reads:
    `payer_scorecard`'s tiebreak against the pre-existing generic
    `dimension_scorecard` has to land inside it."""
    clip = 160
    payer = next(p for p in snapshot.playbooks if p.id == "payer_scorecard")
    head = " ".join(payer.description.split())[:clip]
    assert "dimension_scorecard" in head
    assert "payer" in head.lower()
    # the pre-existing generic playbook is untouched by this pass
    generic = next(p for p in snapshot.playbooks if p.id == "dimension_scorecard")
    assert generic.description.startswith("Generic multi-metric assessment across ANY certified dimension")


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
# governed knowledge: cards and benchmark figures (KB wave 1)

KNOWLEDGE_NAMESPACES = frozenset({"benchmark", "payer", "reg", "ops"})


def test_card_ids_use_the_declared_namespaces(snapshot: PackSnapshot) -> None:
    for card in snapshot.knowledge_cards:
        namespace = card.id.split(".")[0]
        assert namespace in KNOWLEDGE_NAMESPACES, f"{card.id}: unknown namespace {namespace!r}"
        assert card.domains, f"{card.id}: needs at least one domain"
        assert card.key_points, f"{card.id}: a card must elaborate, not just summarize"


def test_every_card_carries_a_sourced_url(snapshot: PackSnapshot) -> None:
    for card in snapshot.knowledge_cards:
        assert card.sources, f"{card.id}: knowledge cards must cite at least one source"
        assert any(s.url for s in card.sources), f"{card.id}: no source carries a url"
        for source in card.sources:
            assert source.authority, f"{card.id}: source {source.id!r} declares no authority"


def test_every_benchmark_resolves_to_a_pack_metric(snapshot: PackSnapshot) -> None:
    for benchmark in snapshot.benchmarks:
        contract = snapshot.metric(benchmark.metric_id)
        assert contract is not None, f"{benchmark.id}: unknown metric {benchmark.metric_id!r}"
        assert benchmark in snapshot.benchmarks_for_metric(benchmark.metric_id)
        assert benchmark.cohort_label, f"{benchmark.id}: cohort_label must be surfaced"
        assert benchmark.sources, f"{benchmark.id}: benchmark figures must cite a source"


def test_benchmark_ranges_are_ordered_where_numeric(snapshot: PackSnapshot) -> None:
    for benchmark in snapshot.benchmarks:
        try:
            low = Decimal(benchmark.value_low)
            high = Decimal(benchmark.value_high)
        except InvalidOperation:
            continue  # qualified figures like '<1' or '+2.2' stay as authored text
        assert low <= high, f"{benchmark.id}: value_low {low} exceeds value_high {high}"


def test_benchmark_values_are_yaml_strings(snapshot: PackSnapshot) -> None:
    """Authored as strings so published figures never round-trip through float."""
    document = yaml.safe_load((BASE_PACK_DIR / "benchmarks.yaml").read_text(encoding="utf-8"))
    for entry in document["benchmarks"]:
        for key in ("value_low", "value_high"):
            assert isinstance(entry[key], str), f"{entry['id']}: {key} must be authored as a string"


def test_nothing_self_certifies(snapshot: PackSnapshot) -> None:
    """Machine-researched content stays machine-researched until a human
    promotes it; promotion machinery is Phase 4 (see packs/base-rcm/NOTES.md)."""
    for card in snapshot.knowledge_cards:
        assert card.review_status is ReviewStatus.MACHINE_RESEARCHED, card.id
        assert card.authored_by.startswith("machine-researched"), card.id
    for benchmark in snapshot.benchmarks:
        assert benchmark.review_status is ReviewStatus.MACHINE_RESEARCHED, benchmark.id


def test_resolve_ma_denial_rate_surfaces_a_card(snapshot: PackSnapshot) -> None:
    matches = snapshot.resolve_term("MA denial rate")
    cards = [m for m in matches if isinstance(m, KnowledgeCard)]
    assert cards, "expected a knowledge card for 'MA denial rate'"
    card = cards[0]
    assert card.id == "payer.ma.denial_patterns"
    assert "medicare_advantage" in card.domains


@pytest.mark.parametrize(
    ("term", "concept_id", "card_id"),
    [
        ("soft denial", "soft_denial", "ops.denial_taxonomy"),
        ("coordination of benefits", "cob", "ops.cob_ordering_msp"),
        ("map keys", "map_keys", "ops.map_keys_measurement"),
        ("denial write-off", "denial_write_off", "ops.write_off_governance"),
        ("unworked denials", "unworked_denials", "benchmark.denials_never_worked"),
    ],
)
def test_cards_elaborate_concepts_rather_than_replacing_them(
    snapshot: PackSnapshot, term: str, concept_id: str, card_id: str
) -> None:
    """A card may share an alias with a concept: the governed definition
    resolves first, the elaborating card follows."""
    matches = snapshot.resolve_term(term)
    assert isinstance(matches[0], Concept) and matches[0].id == concept_id
    assert any(isinstance(m, KnowledgeCard) and m.id == card_id for m in matches)


def test_card_ids_never_collide_with_concepts_or_metrics(snapshot: PackSnapshot) -> None:
    card_ids = {c.id for c in snapshot.knowledge_cards}
    assert not card_ids & {c.id for c in snapshot.concepts}
    assert not card_ids & {m.id for m in snapshot.metric_contracts}


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
