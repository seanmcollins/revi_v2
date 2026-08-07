"""PHI masking helper tests (design §4.1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from revi_catalog import load_catalog, mask_value, masked_columns
from revi_catalog_contracts import SUPPRESSED_TOKEN, CatalogSnapshot, PhiClass, field_phi_class
from revi_kernel.refs import FieldRef

CATALOG_DIR = Path(__file__).resolve().parents[3] / "warehouse" / "catalog"
_TOKEN = re.compile(r"^MASKED-[0-9a-f]{10}$")


@pytest.fixture(scope="module")
def catalog() -> CatalogSnapshot:
    return load_catalog(CATALOG_DIR)


def test_none_class_passes_through() -> None:
    assert mask_value("Meridian Health", PhiClass.NONE) == "Meridian Health"
    assert mask_value(42, PhiClass.NONE) == 42


def test_indirect_masks_deterministically() -> None:
    first = mask_value("Dr Alice Wong", PhiClass.INDIRECT)
    second = mask_value("Dr Alice Wong", PhiClass.INDIRECT)
    other = mask_value("Dr Bob Reyes", PhiClass.INDIRECT)
    assert isinstance(first, str) and _TOKEN.match(first)
    assert first == second, "equal values must mask equally (grouping survives)"
    assert first != other
    assert "Alice" not in first


def test_direct_suppresses_entirely() -> None:
    assert mask_value("111-22-3333", PhiClass.DIRECT) == SUPPRESSED_TOKEN
    assert mask_value("anything else", PhiClass.DIRECT) == SUPPRESSED_TOKEN


def test_null_is_not_phi() -> None:
    assert mask_value(None, PhiClass.INDIRECT) is None
    assert mask_value(None, PhiClass.DIRECT) is None


def test_masked_columns_selects_phi_fields(catalog: CatalogSnapshot) -> None:
    fields = ("claim_id", "payer", "provider", "service_line")
    assert masked_columns(catalog, fields) == ("provider",)
    assert masked_columns(catalog, (FieldRef("provider"), FieldRef("payer"))) == ("provider",)


def test_field_phi_class_strips_entity_qualifier(catalog: CatalogSnapshot) -> None:
    assert field_phi_class(catalog, "claim.provider") is PhiClass.INDIRECT
    assert field_phi_class(catalog, "provider") is PhiClass.INDIRECT
    assert field_phi_class(catalog, "claim.payer") is PhiClass.NONE
    assert field_phi_class(catalog, "not_a_dimension") is PhiClass.NONE
