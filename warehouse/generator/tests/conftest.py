"""Shared fixtures: one small-scale generation per test session."""

from __future__ import annotations

import pytest

from revi_warehouse.config import GeneratorConfig
from revi_warehouse.generate import GenerationResult, run_generation


@pytest.fixture(scope="session")
def small_config() -> GeneratorConfig:
    return GeneratorConfig.small()


@pytest.fixture(scope="session")
def small_result(
    small_config: GeneratorConfig, tmp_path_factory: pytest.TempPathFactory
) -> GenerationResult:
    out = tmp_path_factory.mktemp("warehouse") / "revi_small.duckdb"
    return run_generation(small_config, out)
