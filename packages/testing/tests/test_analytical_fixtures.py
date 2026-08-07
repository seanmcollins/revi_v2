"""Sanity tests for the fixture metric contracts and the contract suite shape."""

from __future__ import annotations

import inspect

from revi_calculation_contracts.contract import (
    MetricKind,
    MetricUnit,
    denominator_column,
    numerator_column,
)
from revi_kernel.refs import EntityGrain
from revi_testing.analytical_contract import AnalyticalRepositoryContract
from revi_testing.fixtures import FIXTURE_METRICS, fixture_metrics


def test_fixture_resolver_shape() -> None:
    assert fixture_metrics("cash_posted") is FIXTURE_METRICS["cash_posted"]
    assert fixture_metrics("no_such_metric") is None


def test_fixture_contract_kinds_and_grains() -> None:
    cash = FIXTURE_METRICS["cash_posted"]
    assert cash.kind is MetricKind.FLOW
    assert cash.entity_grain is EntityGrain.TRANSACTION
    assert not cash.is_ratio and cash.unit is MetricUnit.MONEY_CENTS

    rate = FIXTURE_METRICS["denial_rate"]
    assert rate.is_ratio and rate.unit is MetricUnit.RATIO
    assert rate.entity_grain is EntityGrain.DENIAL
    assert numerator_column(rate.id) == "denial_rate__num"
    assert denominator_column(rate.id) == "denial_rate__den"

    balance = FIXTURE_METRICS["ar_balance"]
    assert balance.kind is MetricKind.SNAPSHOT
    assert balance.entity_grain is EntityGrain.CLAIM


def test_fixture_fingerprints_are_distinct() -> None:
    fingerprints = {contract.fingerprint for contract in FIXTURE_METRICS.values()}
    assert len(fingerprints) == len(FIXTURE_METRICS)


def test_contract_suite_exposes_nine_behaviors() -> None:
    tests = [
        name
        for name, member in inspect.getmembers(AnalyticalRepositoryContract)
        if name.startswith("test_") and inspect.iscoroutinefunction(member)
    ]
    assert len(tests) == 9, tests
