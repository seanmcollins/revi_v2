"""Loader tests: strict YAML -> typed layer, precise rejection messages."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from revi_calculation_contracts.contract import Count, CountDistinct, Filtered, MetricUnit, Sum
from revi_kernel.filters import Predicate, PredicateOp
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DateBasisRef, EntityGrain, FieldRef
from revi_kernel.scope import RangeMode, RelativeRange, TimeUnit
from revi_pack.domain import (
    AliasOverride,
    BindingState,
    CodeSystem,
    DetectorOverride,
    PackLayerKind,
    ReviewStatus,
)
from revi_pack.errors import PackLoadError
from revi_pack.loader import load_layer

FIXTURES = Path(__file__).parent / "fixtures"

_MANIFEST = 'pack_id: tmp-pack\nversion: "1"\nkind: {kind}\n'

_METRIC_STUB = """\
id: {metric_id}
version: 1
kind: flow
entity_grain: claim
numerator: {numerator}
primary_date_basis: post
allowed_date_bases: [post]
scope_dimensions: [payer]
sign: neutral
unit: count
"""


def write_layer(root: Path, files: dict[str, str], kind: str = "base") -> Path:
    (root / "pack.yaml").write_text(_MANIFEST.format(kind=kind), encoding="utf-8")
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_load_base_layer() -> None:
    layer = load_layer(FIXTURES / "base")
    assert layer.kind is PackLayerKind.BASE
    assert layer.name == "test-rcm"
    assert layer.version == "1.0.0"

    assert [c.id for c in layer.concepts] == ["cob", "denial"]
    cob = layer.concepts[0]
    assert cob.aliases == ("COB", "other insurance")
    assert cob.related == ("denial",)
    assert cob.sources[0].publisher == "Example Clearinghouse Press"
    assert "primary" in cob.definition

    assert [(c.code_system, c.code) for c in layer.code_definitions] == [
        (CodeSystem.GROUP_CODE, "PR"),
        (CodeSystem.CARC, "3"),
    ]

    denial_rate = next(m for m in layer.metric_contracts if m.id == "denial_rate")
    assert denial_rate.unit is MetricUnit.RATIO
    assert denial_rate.entity_grain is EntityGrain.CLAIM
    numerator = denial_rate.numerator
    assert isinstance(numerator, Filtered)
    assert numerator.inner == Count()
    where = numerator.where
    assert isinstance(where, Predicate)
    assert where.op is PredicateOp.EQ
    assert where.values == ("DENIED",)
    assert denial_rate.denominator == Count()
    assert denial_rate.primary_date_basis == DateBasisRef("submission")

    denied_amount = next(m for m in layer.metric_contracts if m.id == "denied_amount")
    assert denied_amount.numerator == Sum(FieldRef("denied_amount_cents"))
    assert isinstance(denied_amount.exclusions, Predicate)
    assert denied_amount.exclusions.op is PredicateOp.NEQ

    playbook = layer.playbooks[0]
    assert playbook.id == "denial_overview"
    assert playbook.probes[0].window == RelativeRange(
        quantity=Decimal(3), unit=TimeUnit.MONTH, mode=RangeMode.FULL_PERIODS
    )
    assert playbook.probes[0].top_n == 10
    assert playbook.probes[1].basis_override == "remit"
    assert playbook.transforms[0].operator == "rank"
    assert playbook.transforms[0].args == (("by", "denied_amount"), ("descending", "true"))
    assert playbook.conclusion_policies == ("denial_pressure_claim",)
    assert playbook.ranking_policy == "impact_first"

    detector = layer.detector_policies[0]
    assert detector.threshold == Decimal("0.1")
    assert detector.threshold_min == Decimal("0.05")
    assert detector.threshold_max == Decimal("0.25")

    binding = layer.bindings[0]
    assert binding.state is BindingState.CERTIFIED
    assert binding.strength is EvidenceGrade.DIRECT

    assert layer.presentation_recipes[0].chart_type == "line"
    assert layer.filing_rules[0].filing_limit_days == 90
    assert layer.filing_rules[0].requires_confirmation is True
    assert layer.conclusion_policies[0].required_grade is EvidenceGrade.DERIVED
    assert layer.ranking_policies[0].weights == (
        ("impact", Decimal("0.7")),
        ("recency", Decimal("0.3")),
    )


def test_load_knowledge_cards() -> None:
    layer = load_layer(FIXTURES / "base")
    assert [c.id for c in layer.knowledge_cards] == ["cob_denial_workup"]
    card = layer.knowledge_cards[0]
    assert card.title == "Working COB denials"
    assert card.domains == ("denials", "eligibility")
    assert card.aliases == ("COB", "cob workup")
    assert len(card.key_points) == 3
    assert card.cautions == ("Payer-specific COB rules must be confirmed against the contract.",)
    assert card.authored_by == "machine-researched (KB wave 1, 2026-08-07)"
    assert card.review_status is ReviewStatus.MACHINE_RESEARCHED
    source = card.sources[0]
    assert source.effective_from == date(2024, 1, 1)
    assert source.effective_to is None
    assert source.last_verified_at == date(2026, 8, 7)


def test_load_benchmarks() -> None:
    layer = load_layer(FIXTURES / "base")
    assert [b.id for b in layer.benchmarks] == ["denial_rate_industry"]
    benchmark = layer.benchmarks[0]
    assert benchmark.metric_id == "denial_rate"
    assert benchmark.cohort_label == "US hospitals, all payers"
    assert (benchmark.value_low, benchmark.value_high) == ("0.06", "0.13")
    assert benchmark.unit == "ratio"
    assert benchmark.period == "CY2025"
    assert benchmark.review_status is ReviewStatus.MACHINE_RESEARCHED
    assert benchmark.cautions == (
        "Survey-based range; denominator definitions vary by respondent.",
    )
    assert benchmark.sources[0].last_verified_at == date(2026, 8, 7)


def test_load_tenant_overlay() -> None:
    layer = load_layer(FIXTURES / "tenant")
    assert layer.kind is PackLayerKind.TENANT
    assert layer.concepts == ()
    assert layer.alias_overrides == (
        AliasOverride(concept_id="denial", add_aliases=("dnl", "hard denial"), replace_aliases=None),
    )
    assert layer.detector_overrides == (
        DetectorOverride(id="denial_spike", threshold=Decimal("0.12")),
    )


def test_missing_manifest_rejected(tmp_path: Path) -> None:
    with pytest.raises(PackLoadError, match="missing required manifest"):
        load_layer(tmp_path)


def test_unknown_key_rejected(tmp_path: Path) -> None:
    write_layer(
        tmp_path,
        {
            "concepts.yaml": (
                "concepts:\n"
                "  - id: c1\n"
                "    name: C One\n"
                "    description: d\n"
                "    definition: d\n"
                "    alliases: [oops]\n"
            )
        },
    )
    with pytest.raises(PackLoadError, match=r"concepts\.yaml\.concepts\[0\].*unknown key.*alliases"):
        load_layer(tmp_path)


def test_base_alias_patch_rejected(tmp_path: Path) -> None:
    write_layer(tmp_path, {"concepts.yaml": "concepts:\n  - id: c1\n    add_aliases: [x]\n"})
    with pytest.raises(PackLoadError, match="full definitions"):
        load_layer(tmp_path)


def test_base_detector_override_rejected(tmp_path: Path) -> None:
    write_layer(
        tmp_path,
        {"policies.yaml": "detector_policies:\n  - id: d1\n    threshold: 0.5\n"},
    )
    with pytest.raises(PackLoadError, match="threshold_min"):
        load_layer(tmp_path)


def test_count_distinct_and_filter_tree_forms(tmp_path: Path) -> None:
    metric = _METRIC_STUB.format(metric_id="m1", numerator="{count_distinct: claim_id}")
    metric += (
        "exclusions:\n"
        "  and:\n"
        "    - {dimension: payer, op: in, values: [A, B]}\n"
        "    - not: {dimension: status, op: is_null}\n"
    )
    write_layer(tmp_path, {"metrics/m1.yaml": metric})
    contract = load_layer(tmp_path).metric_contracts[0]
    assert contract.numerator == CountDistinct(FieldRef("claim_id"))
    assert contract.exclusions is not None


def test_nested_filtered_measure_rejected(tmp_path: Path) -> None:
    metric = _METRIC_STUB.format(
        metric_id="m2",
        numerator=(
            "{filtered: {inner: {filtered: {inner: {count: {}}, "
            "where: {dimension: a, op: is_null}}}, where: {dimension: b, op: is_null}}}"
        ),
    )
    write_layer(tmp_path, {"metrics/m2.yaml": metric})
    with pytest.raises(PackLoadError, match="cannot be nested"):
        load_layer(tmp_path)


def test_duplicate_metric_id_within_layer_rejected(tmp_path: Path) -> None:
    stub = _METRIC_STUB.format(metric_id="m1", numerator="{count: {}}")
    write_layer(tmp_path, {"metrics/a.yaml": stub, "metrics/b.yaml": stub})
    with pytest.raises(PackLoadError, match="duplicate metric id 'm1'"):
        load_layer(tmp_path)


def test_invalid_enum_value_named_precisely(tmp_path: Path) -> None:
    metric = _METRIC_STUB.format(metric_id="m3", numerator="{count: {}}").replace(
        "kind: flow", "kind: cumulative"
    )
    write_layer(tmp_path, {"metrics/m3.yaml": metric})
    with pytest.raises(PackLoadError, match="'cumulative' is not a valid metric kind"):
        load_layer(tmp_path)


def test_predicate_value_and_values_conflict(tmp_path: Path) -> None:
    metric = _METRIC_STUB.format(metric_id="m4", numerator="{count: {}}")
    metric += "exclusions: {dimension: a, op: eq, value: x, values: [y]}\n"
    write_layer(tmp_path, {"metrics/m4.yaml": metric})
    with pytest.raises(PackLoadError, match="either 'value' or 'values'"):
        load_layer(tmp_path)


_CARD_STUB = """\
knowledge_cards:
  - id: k1
    title: T1
    summary: S1
    authored_by: machine-researched (KB wave 1, 2026-08-07)
    review_status: machine_researched
"""

_BENCHMARK_ENTRY = """\
  - id: {benchmark_id}
    metric_id: m1
    cohort_label: US hospitals, all payers
    value_low: "1"
    value_high: "2"
    unit: ratio
    period: CY2025
    authority: survey
    review_status: human_approved
"""


def test_knowledge_unknown_key_rejected(tmp_path: Path) -> None:
    write_layer(tmp_path, {"knowledge.yaml": _CARD_STUB + "    key_pointz: [oops]\n"})
    with pytest.raises(
        PackLoadError, match=r"knowledge\.yaml\.knowledge_cards\[0\].*unknown key.*key_pointz"
    ):
        load_layer(tmp_path)


def test_knowledge_invalid_review_status_named_precisely(tmp_path: Path) -> None:
    stub = _CARD_STUB.replace("review_status: machine_researched", "review_status: reviewed")
    write_layer(tmp_path, {"knowledge.yaml": stub})
    with pytest.raises(PackLoadError, match="'reviewed' is not a valid review status"):
        load_layer(tmp_path)


def test_source_timestamp_rejected_where_date_expected(tmp_path: Path) -> None:
    stub = _CARD_STUB + (
        "    sources:\n"
        "      - id: s1\n"
        "        title: T\n"
        "        publisher: P\n"
        "        authority: authoritative\n"
        "        last_verified_at: 2026-08-07 10:00:00\n"
    )
    write_layer(tmp_path, {"knowledge.yaml": stub})
    with pytest.raises(PackLoadError, match=r"last_verified_at: expected a date, got a timestamp"):
        load_layer(tmp_path)


def test_benchmark_unknown_key_rejected(tmp_path: Path) -> None:
    stub = "benchmarks:\n" + _BENCHMARK_ENTRY.format(benchmark_id="b1") + "    target: 0.05\n"
    write_layer(tmp_path, {"benchmarks.yaml": stub})
    with pytest.raises(PackLoadError, match=r"benchmarks\.yaml\.benchmarks\[0\].*unknown key.*target"):
        load_layer(tmp_path)


def test_duplicate_benchmark_id_within_layer_rejected(tmp_path: Path) -> None:
    stub = (
        "benchmarks:\n"
        + _BENCHMARK_ENTRY.format(benchmark_id="b1")
        + _BENCHMARK_ENTRY.format(benchmark_id="b1")
    )
    write_layer(tmp_path, {"benchmarks.yaml": stub})
    with pytest.raises(PackLoadError, match="duplicate benchmark id 'b1'"):
        load_layer(tmp_path)
