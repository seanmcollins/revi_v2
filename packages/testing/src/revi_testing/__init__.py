"""Test harness: in-memory fakes, MockLanguageModel, contract suites, golden-conversation runner, fixtures."""

from revi_testing.analytical_contract import AnalyticalRepositoryContract
from revi_testing.fixtures import FIXTURE_METRICS, fixture_metrics

__all__ = ["FIXTURE_METRICS", "AnalyticalRepositoryContract", "fixture_metrics"]
