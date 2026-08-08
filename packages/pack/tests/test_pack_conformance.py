"""Pack ↔ semantic-catalog conformance (``revi_pack.conformance``).

Two layers:

1. synthetic snapshots against a hand-built catalog — the guard's contract
   (which conditions fail, which deliberately do not, and that *every*
   offender is named rather than the first);
2. the real composed ``packs/base-rcm`` + ``packs/overlays/demo-tenant``,
   exactly as ``revi_api.wiring`` composes it, against the real
   ``warehouse/catalog`` — the evidence that no shipped content trips the
   guard.
"""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from revi_calculation_contracts.contract import (
    CountDistinct,
    MetricContract,
    MetricKind,
    MetricUnit,
    SignConvention,
)
from revi_catalog_contracts import (
    CalendarDef,
    CatalogSnapshot,
    DateBasisDef,
    DimensionDef,
    EntityDef,
    MeasureAggregation,
    MeasureDef,
)
from revi_kernel.errors import ErrorCode
from revi_kernel.filters import And, Not, Predicate, PredicateOp
from revi_kernel.refs import DateBasisRef, DimensionRef, EntityGrain, FieldRef
from revi_pack.conformance import (
    PackCatalogConformanceError,
    unresolved_exclusion_dimensions,
    validate_pack_catalog_conformance,
)
from revi_pack.domain import PackSnapshot, PackVersion
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_PACK_DIR = REPO_ROOT / "packs" / "base-rcm"
OVERLAY_DIR = REPO_ROOT / "packs" / "overlays" / "demo-tenant"
CATALOG_DIR = REPO_ROOT / "warehouse" / "catalog"

SERVICE = DateBasisRef("service")


# ---------------------------------------------------------------------------
# synthetic fixtures


def _catalog() -> CatalogSnapshot:
    """A two-dimension catalog: one certified, one deliberately not.

    ``status`` is absent on purpose — it is the base-view column that the
    base pack's exclusions used to name.
    """
    claim = EntityDef(
        name="claim",
        grain=EntityGrain.CLAIM,
        base_view="v_claim",
        primary_key="claim_id",
        date_basis_columns=(("service", "service_date"),),
    )
    return CatalogSnapshot(
        entities=(claim,),
        dimensions=(
            DimensionDef(id="payer", label="Payer", certified=True, entities=(("claim", "payer_name"),)),
            DimensionDef(
                id="scratch_flag",
                label="Scratch flag",
                certified=False,
                entities=(("claim", "scratch_flag"),),
                uncertified_reason="exercises the discovery-grade downgrade path",
            ),
        ),
        measures=(
            MeasureDef(
                id="claim_count",
                entity="claim",
                column="claim_id",
                aggregation=MeasureAggregation.COUNT_DISTINCT,
                additive=True,
                unit="count",
            ),
        ),
        date_bases=(DateBasisDef(id="service", columns=(("claim", "service_date"),)),),
        calendar=CalendarDef(
            table="dim_date",
            date_column="date_day",
            range_start=date(2025, 1, 1),
            range_end=date(2026, 12, 31),
        ),
    )


def _contract(metric_id: str, exclusions: object | None) -> MetricContract:
    return MetricContract(
        id=metric_id,
        version=1,
        kind=MetricKind.FLOW,
        entity_grain=EntityGrain.CLAIM,
        numerator=CountDistinct(FieldRef("claim_id")),
        denominator=None,
        primary_date_basis=SERVICE,
        allowed_date_bases=(SERVICE,),
        scope_dimensions=(DimensionRef("payer"),),
        sign=SignConvention.NEUTRAL,
        unit=MetricUnit.COUNT,
        exclusions=exclusions,  # type: ignore[arg-type]
    )


def _pack(*contracts: MetricContract) -> PackSnapshot:
    return PackSnapshot(
        id="conformance-test-snapshot",
        version=PackVersion("test-pack", "1.0.0"),
        layers=(),
        metric_contracts=contracts,
    )


@pytest.fixture(scope="module")
def catalog() -> CatalogSnapshot:
    return _catalog()


# ---------------------------------------------------------------------------
# 1. the guard's contract


def test_exclusion_on_a_nonexistent_dimension_fails_the_guard(catalog: CatalogSnapshot) -> None:
    pack = _pack(_contract("scratch_metric", Predicate(DimensionRef("status"), PredicateOp.NEQ, ("OPEN",))))

    with pytest.raises(PackCatalogConformanceError) as excinfo:
        validate_pack_catalog_conformance(pack, catalog)

    error = excinfo.value
    assert error.code is ErrorCode.UNSUPPORTED_CONCEPT
    # the message must name both halves of the problem, not just "a dimension"
    assert "scratch_metric" in error.message
    assert "status" in error.message
    assert error.pairs == (("scratch_metric", "status"),)
    assert error.details["pairs"] == [{"metric": "scratch_metric", "dimension": "status"}]


def test_every_offending_pair_is_named_not_only_the_first(catalog: CatalogSnapshot) -> None:
    """Systematic authoring errors come in batches; stopping at the first
    offender turns one review into N."""
    pack = _pack(
        _contract("alpha", Predicate(DimensionRef("status"), PredicateOp.NEQ, ("OPEN",))),
        _contract("beta", Not(Predicate(DimensionRef("submission_date"), PredicateOp.IS_NULL))),
        _contract(
            "gamma",
            And(
                (
                    Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Atlas",)),
                    Predicate(DimensionRef("txn_type"), PredicateOp.EQ, ("PAYMENT",)),
                )
            ),
        ),
    )

    with pytest.raises(PackCatalogConformanceError) as excinfo:
        validate_pack_catalog_conformance(pack, catalog)

    assert excinfo.value.pairs == (
        ("alpha", "status"),
        ("beta", "submission_date"),
        ("gamma", "txn_type"),
    )
    for token in ("alpha", "beta", "gamma", "status", "submission_date", "txn_type"):
        assert token in excinfo.value.message


def test_predicates_are_found_at_any_polarity_and_depth(catalog: CatalogSnapshot) -> None:
    """A dimension that does not exist cannot compile wherever it sits, so
    the walk must not stop at the top of the expression tree."""
    buried = Not(And((And(()), Not(Predicate(DimensionRef("status"), PredicateOp.IS_NULL)))))
    pack = _pack(_contract("deep", buried))
    assert unresolved_exclusion_dimensions(pack, catalog) == (("deep", "status"),)


def test_conforming_and_exclusion_free_contracts_pass(catalog: CatalogSnapshot) -> None:
    pack = _pack(
        _contract("no_exclusions", None),
        _contract("certified_exclusion", Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Atlas",))),
    )
    assert unresolved_exclusion_dimensions(pack, catalog) == ()
    validate_pack_catalog_conformance(pack, catalog)  # does not raise


def test_uncertified_dimension_resolves_and_is_not_a_conformance_failure(
    catalog: CatalogSnapshot,
) -> None:
    """"Resolves nowhere" means absent, not uncertified.

    An uncertified dimension binds a real column and downgrades the answer to
    DISCOVERY grade — a deliberate, already-handled way to say "this evidence
    is weak". Failing composition on it would make honest weak-evidence
    content unauthorable.
    """
    pack = _pack(
        _contract("discovery_grade", Predicate(DimensionRef("scratch_flag"), PredicateOp.EQ, (True,)))
    )
    assert catalog.is_certified("scratch_flag") is False
    assert catalog.dimension("scratch_flag") is not None
    validate_pack_catalog_conformance(pack, catalog)  # does not raise


def test_empty_pack_conforms(catalog: CatalogSnapshot) -> None:
    validate_pack_catalog_conformance(_pack(), catalog)


# ---------------------------------------------------------------------------
# 2. the real shipped content


@pytest.fixture(scope="module")
def base_pack_snapshot() -> PackSnapshot:
    """Composed exactly as ``revi_api.wiring.build_components`` composes it."""
    return build_snapshot([load_layer(BASE_PACK_DIR), load_layer(OVERLAY_DIR)])


@pytest.fixture(scope="module")
def real_catalog() -> CatalogSnapshot:
    from revi_catalog import load_catalog

    return load_catalog(CATALOG_DIR)


def test_composed_base_pack_conforms_to_the_catalog(
    base_pack_snapshot: PackSnapshot, real_catalog: CatalogSnapshot
) -> None:
    """Every metric contract in the shipped pack, not only the ones a
    question happens to reach."""
    offenders = unresolved_exclusion_dimensions(base_pack_snapshot, real_catalog)
    assert offenders == (), (
        "base-rcm exclusions name dimensions the catalog does not define: "
        f"{[f'{m}.{d}' for m, d in offenders]}"
    )
    validate_pack_catalog_conformance(base_pack_snapshot, real_catalog)


def test_every_shipped_contract_was_examined(
    base_pack_snapshot: PackSnapshot, real_catalog: CatalogSnapshot
) -> None:
    """Guards the guard: a conformance pass that silently examined nothing
    would also report no offenders."""
    contracts = base_pack_snapshot.metric_contracts
    assert len(contracts) >= 22
    assert len({c.id for c in contracts}) == len(contracts)

    with_exclusions = {c.id for c in contracts if c.exclusions is not None}
    # One contract carries an exclusion, and it is the only one whose
    # dimension the catalog defines. The other six were removed in the
    # 2026-08-08 polarity correction (packs/base-rcm/NOTES.md).
    assert with_exclusions == {"denials_unworked_pct"}
    for metric_id in ("clean_claim_rate", "first_pass_yield", "denial_rate"):
        contract = base_pack_snapshot.metric(metric_id)
        assert contract is not None, metric_id
        assert contract.exclusions is None, metric_id

    unworked = base_pack_snapshot.metric("denials_unworked_pct")
    assert unworked is not None and unworked.exclusions is not None
    # The near miss: this exclusion resolves, so the guard passes it. Its
    # polarity was repaired by hand (v2) — a resolving exclusion is
    # structurally indistinguishable from a correct one.
    assert unworked.version == 2
    assert isinstance(unworked.exclusions, Predicate)
    assert unworked.exclusions.dimension == DimensionRef("denial_category")
    assert unworked.exclusions.op is PredicateOp.EQ
    assert unworked.exclusions.values == ("PATIENT_RESP",)
    assert real_catalog.is_certified("denial_category")


def test_a_regression_in_shipped_content_would_be_caught(
    base_pack_snapshot: PackSnapshot, real_catalog: CatalogSnapshot
) -> None:
    """Re-introducing the original defect into the real pack trips the guard
    — so the green result above is a fact about the content, not about the
    check being vacuous."""
    broken = base_pack_snapshot.metric("clean_claim_rate")
    assert broken is not None
    regressed = replace(
        broken, exclusions=Predicate(DimensionRef("status"), PredicateOp.NEQ, ("OPEN",))
    )
    others = tuple(c for c in base_pack_snapshot.metric_contracts if c.id != "clean_claim_rate")

    with pytest.raises(PackCatalogConformanceError) as excinfo:
        validate_pack_catalog_conformance(_pack(*others, regressed), real_catalog)
    assert ("clean_claim_rate", "status") in excinfo.value.pairs
