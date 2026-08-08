"""Loader tests against the real warehouse catalog plus strictness checks."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from revi_catalog import CatalogLoadError, load_catalog
from revi_catalog_contracts import CatalogSnapshot, DimensionKind, PhiClass
from revi_kernel.refs import DISCHARGE, POST, REMIT, SERVICE, EntityGrain

CATALOG_DIR = Path(__file__).resolve().parents[3] / "warehouse" / "catalog"


@pytest.fixture(scope="module")
def catalog() -> CatalogSnapshot:
    return load_catalog(CATALOG_DIR)


def test_loads_all_artifacts(catalog: CatalogSnapshot) -> None:
    assert len(catalog.entities) == 5
    assert len(catalog.dimensions) == 23
    assert len(catalog.measures) == 20
    assert len(catalog.date_bases) == 5
    assert len(catalog.join_paths) == 7


def test_entity_grain_addressing_is_bijective(catalog: CatalogSnapshot) -> None:
    claim = catalog.entity(EntityGrain.CLAIM)
    line = catalog.entity(EntityGrain.LINE)
    denial = catalog.entity(EntityGrain.DENIAL)
    assert claim is not None and claim.base_view == "v_claim" and claim.primary_key == "claim_id"
    assert line is not None and line.name == "claim_line"
    # The denial entity declares physical grain LINE in the YAML but is
    # addressed as EntityGrain.DENIAL because its name matches.
    assert denial is not None and denial.base_view == "v_denial" and denial.grain is EntityGrain.LINE
    assert catalog.entity(EntityGrain.ENCOUNTER) is None


def test_date_basis_bindings(catalog: CatalogSnapshot) -> None:
    assert catalog.date_basis_column(EntityGrain.TRANSACTION, POST) == "post_date"
    assert catalog.date_basis_column(EntityGrain.CLAIM, POST) is None
    assert catalog.date_basis_column(EntityGrain.CLAIM, SERVICE) == "service_date"
    assert catalog.date_basis_column(EntityGrain.CLAIM, DISCHARGE) == "discharge_date"
    assert catalog.date_basis_column(EntityGrain.DENIAL, REMIT) == "denial_date"
    assert catalog.date_basis_column("remit", REMIT) == "remit_date"


def test_synonym_resolution(catalog: CatalogSnapshot) -> None:
    payer = catalog.dimension_for_synonym("Payor")
    assert payer is not None and payer.id == "payer"
    ins = catalog.dimension_for_synonym("ins")
    assert ins is not None and ins.id == "payer"
    carc = catalog.dimension_for_synonym("reason code")
    assert carc is not None and carc.id == "carc"
    # "service area" is a synonym of both facility and service_line: the
    # unique-resolution helper returns None; the plural form returns both.
    assert catalog.dimension_for_synonym("service area") is None
    both = catalog.dimensions_for_synonym("service area")
    assert {d.id for d in both} == {"facility", "service_line"}
    assert catalog.dimension_for_synonym("no such thing") is None


def test_certification_awareness(catalog: CatalogSnapshot) -> None:
    assert catalog.is_certified("payer")
    assert not catalog.is_certified("rarc_synthetic")
    assert not catalog.is_certified("revenue_code")
    assert not catalog.is_certified("unknown_dimension")  # fail closed
    rarc = catalog.dimension("rarc_synthetic")
    assert rarc is not None and rarc.uncertified_reason


def test_phi_classes(catalog: CatalogSnapshot) -> None:
    assert catalog.phi_class("provider") is PhiClass.INDIRECT
    assert catalog.phi_class("payer") is PhiClass.NONE
    assert catalog.phi_class("nonexistent") is PhiClass.NONE


def test_derived_bucket_dimension(catalog: CatalogSnapshot) -> None:
    bucket = catalog.dimension("ar_age_bucket")
    assert bucket is not None
    assert bucket.kind is DimensionKind.DERIVED_BUCKET
    assert bucket.buckets == ("0-30", "31-60", "61-90", "91-120", "120+")


def test_measures_carry_governed_filters(catalog: CatalogSnapshot) -> None:
    payment = catalog.measure("payment_cents")
    assert payment is not None
    assert payment.entity == "transaction"
    assert payment.column == "amount_cents"
    assert payment.filter_sql == "txn_type = 'PAYMENT'"
    billed = catalog.measure("billed_amount_cents")
    assert billed is not None and billed.filter_sql is None


def test_join_paths_index(catalog: CatalogSnapshot) -> None:
    assert catalog.join_column("denial", "claim") == "claim_id"
    assert catalog.join_column("transaction", "remit") == "remit_id"
    assert catalog.join_column("claim", "denial") is None
    assert "claim_id" in catalog.declared_columns("denial")
    assert "remit_id" in catalog.declared_columns("denial")


def test_calendar_binding(catalog: CatalogSnapshot) -> None:
    assert catalog.calendar.table == "dim_calendar"
    assert catalog.calendar.range_start == date(2025, 1, 1)
    assert catalog.calendar.range_end == date(2026, 12, 31)
    assert catalog.calendar.week_convention == "ISO-8601"
    assert date(2026, 12, 25) in catalog.calendar.holidays
    assert dict(catalog.calendar.policies).keys() == {"CALENDAR_DAY", "BUSINESS_DAY"}


def test_suppression_defaults_to_eleven(catalog: CatalogSnapshot) -> None:
    assert catalog.suppression.threshold == 11


# ---------------------------------------------------------------- strictness


def _copy_catalog(tmp_path: Path) -> Path:
    target = tmp_path / "catalog"
    shutil.copytree(CATALOG_DIR, target)
    return target


def test_unknown_key_rejected_with_file_context(tmp_path: Path) -> None:
    target = _copy_catalog(tmp_path)
    dims = target / "dimensions.yaml"
    dims.write_text(dims.read_text().replace("  payer:\n", "  payer:\n    surprising_key: 1\n"))
    with pytest.raises(CatalogLoadError, match=r"dimensions\.yaml.*payer.*surprising_key"):
        load_catalog(target)


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    target = _copy_catalog(tmp_path)
    entities = target / "entities.yaml"
    entities.write_text(entities.read_text() + "\nextra_section: {}\n")
    with pytest.raises(CatalogLoadError, match=r"entities\.yaml.*extra_section"):
        load_catalog(target)


def test_invalid_phi_class_rejected(tmp_path: Path) -> None:
    target = _copy_catalog(tmp_path)
    dims = target / "dimensions.yaml"
    dims.write_text(dims.read_text().replace("phi: indirect", "phi: mystery", 1))
    with pytest.raises(CatalogLoadError, match="phi"):
        load_catalog(target)


def test_missing_file_rejected(tmp_path: Path) -> None:
    target = _copy_catalog(tmp_path)
    (target / "measures.yaml").unlink()
    with pytest.raises(CatalogLoadError, match=r"measures\.yaml"):
        load_catalog(target)


def test_optional_suppression_file(tmp_path: Path) -> None:
    target = _copy_catalog(tmp_path)
    (target / "suppression.yaml").write_text("version: 1\nthreshold: 25\n")
    assert load_catalog(target).suppression.threshold == 25
