"""Workspace smoke test: every package skeleton is importable."""

import importlib

import pytest

MODULES = [
    "revi_kernel",
    "revi_investigation_contracts",
    "revi_investigation",
    "revi_catalog_contracts",
    "revi_catalog",
    "revi_calculation_contracts",
    "revi_calculation",
    "revi_pack_contracts",
    "revi_pack",
    "revi_pack_learning",
    "revi_presentation",
    "revi_connector_duckdb",
    "revi_store_postgres",
    "revi_adapter_claude",
    "revi_testing",
    "revi_warehouse",
    "revi_api",
    "revi_scheduler",
]


@pytest.mark.parametrize("module", MODULES)
def test_importable(module: str) -> None:
    importlib.import_module(module)
