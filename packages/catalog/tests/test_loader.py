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
    assert len(catalog.dimensions) == 28
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


def test_boolean_flag_dimensions_are_certified_and_bound_to_claim_columns(
    catalog: CatalogSnapshot,
) -> None:
    """The three claim-level flags a filter predicate needs. `submission_date`
    and `discharge_date` stay date bases (a window rides on them); the
    predicate form is a certified boolean dimension, which is what makes
    `submission_date IS NULL` expressible at all."""
    for dimension_id, column in (
        ("first_pass_paid", "first_pass_paid"),
        ("billed_flag", "billed_flag"),
        ("discharged_flag", "discharged_flag"),
    ):
        dim = catalog.dimension(dimension_id)
        assert dim is not None, dimension_id
        assert dim.certified and dim.column_for("claim") == column
        assert dim.cardinality_estimate == 2
        assert dim.description, dimension_id
    # the dates themselves are bases, never dimensions
    assert catalog.dimension("submission_date") is None
    assert catalog.dimension("discharge_date") is None


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


def test_filing_runway_bucket_is_a_certified_derived_bucket(catalog: CatalogSnapshot) -> None:
    """The second derived bucket, and the one with non-numeric arms.

    `expired` and `filed` are states, not day ranges, and the compiler names
    both as constants — so the catalog must declare them or the binding
    refuses. The claim binding is the filing-clock ANCHOR (service date); the
    limit half of the join is the declared `timely_filing_days` column.
    """
    bucket = catalog.dimension("filing_runway_bucket")
    assert bucket is not None
    assert bucket.certified
    assert bucket.kind is DimensionKind.DERIVED_BUCKET
    assert bucket.buckets == ("expired", "0-30", "31-60", "61-90", "90+", "filed")
    assert bucket.column_for("claim") == "service_date"
    assert bucket.column_for("claim_line") is None  # claim grain only


def test_primary_proc_group_is_certified_at_claim_grain_only(catalog: CatalogSnapshot) -> None:
    """Claim-grain procedure attribution, distinct from the line-grain
    `proc_group` it is derived from — and the two must not collide on a
    synonym, or every "procedure category" question becomes ambiguous."""
    primary = catalog.dimension("primary_proc_group")
    line = catalog.dimension("proc_group")
    assert primary is not None and line is not None
    assert primary.certified and primary.kind is DimensionKind.COLUMN
    assert primary.column_for("claim") == "primary_proc_group"
    assert primary.column_for("claim_line") is None
    assert line.column_for("claim") is None
    assert not set(primary.synonyms) & set(line.synonyms)
    assert catalog.dimension_for_synonym("procedure category") is line
    assert catalog.dimension_for_synonym("dominant procedure group") is primary


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


def test_declared_columns_carry_the_entitys_explicit_declarations(
    catalog: CatalogSnapshot,
) -> None:
    """`declared_columns:` in entities.yaml is the escape hatch for a base-view
    column no dimension, measure, date basis or join path names.

    `charge_entry_date` is the live case: the probe-time derived measures
    `charge_entry_lag_days` and `late_charge_cents` (summed by `charge_lag_days`
    and `late_charge_pct`) read it, and until it was declared here the DuckDB
    compiler reached it through a private module constant — a column dependency
    that existed in one adapter and nowhere in the catalog. It is deliberately
    NOT a dimension (nothing groups by it) and NOT a date basis (no window
    rides on it), so the declaration is the only place it can be visible.
    """
    line = catalog.entity_named("claim_line")
    assert line is not None
    assert line.extra_columns == ("charge_entry_date",)
    assert "charge_entry_date" in catalog.declared_columns("claim_line")
    # It stays out of the dimension and date-basis surfaces.
    assert catalog.dimension("charge_entry_date") is None
    assert line.date_basis_column(SERVICE) == "service_date"
    # The claim's own declaration is the same move for the filing-rule join:
    # `days_to_filing_deadline` and `filing_runway_bucket` read the plan's
    # `timely_filing_days` off the pre-joined claim view, and nothing else in
    # the catalog names that column (no window rides on it, nobody groups by
    # a limit in days).
    claim = catalog.entity_named("claim")
    assert claim is not None
    assert claim.extra_columns == ("timely_filing_days",)
    assert "timely_filing_days" in catalog.declared_columns("claim")
    assert catalog.dimension("timely_filing_days") is None


def test_declared_columns_must_be_a_list_of_strings(tmp_path: Path) -> None:
    target = _copy_catalog(tmp_path)
    entities = target / "entities.yaml"
    entities.write_text(
        entities.read_text().replace("      - charge_entry_date", "      - 17", 1)
    )
    with pytest.raises(CatalogLoadError, match=r"entities\.yaml.*claim_line.*declared_columns"):
        load_catalog(target)


def test_calendar_binding(catalog: CatalogSnapshot) -> None:
    assert catalog.calendar.table == "dim_calendar"
    # The calendar spans the closed 2024 comparison year as well as the organic
    # era, so business-day alignment works on both sides of a year-over-year cut.
    assert catalog.calendar.range_start == date(2024, 1, 1)
    assert catalog.calendar.range_end == date(2026, 12, 31)
    assert catalog.calendar.week_convention == "ISO-8601"
    assert date(2024, 12, 25) in catalog.calendar.holidays
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
