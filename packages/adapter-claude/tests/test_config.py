"""Tests for environment-driven adapter construction (config.from_env).

``from_env`` never reads ``.env`` itself — it consumes the process
environment (or an explicit mapping, used here for hermeticity)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_adapter_claude.config import (
    MAX_BUDGET_ENV,
    MAX_CONCURRENCY_ENV,
    MAX_RETRIES_ENV,
    MODEL_PIN_ENV,
    TIMEOUT_ENV,
    envelope_from_env,
    from_env,
)


def test_missing_model_pin_raises_with_actionable_message() -> None:
    with pytest.raises(ValueError, match=MODEL_PIN_ENV):
        from_env({})


def test_empty_or_whitespace_model_pin_raises() -> None:
    with pytest.raises(ValueError, match=MODEL_PIN_ENV):
        from_env({MODEL_PIN_ENV: ""})
    with pytest.raises(ValueError, match=MODEL_PIN_ENV):
        from_env({MODEL_PIN_ENV: "   "})


def test_pin_set_uses_default_budget() -> None:
    adapter = from_env({MODEL_PIN_ENV: "claude-sonnet-5"})
    assert adapter._model_pin == "claude-sonnet-5"
    assert adapter._max_budget_usd == Decimal("0.50")


def test_budget_env_overrides_default() -> None:
    adapter = from_env({MODEL_PIN_ENV: "claude-sonnet-5", MAX_BUDGET_ENV: "0.25"})
    assert adapter._max_budget_usd == Decimal("0.25")


def test_invalid_budget_raises() -> None:
    with pytest.raises(ValueError, match=MAX_BUDGET_ENV):
        from_env({MODEL_PIN_ENV: "claude-sonnet-5", MAX_BUDGET_ENV: "not-a-number"})
    with pytest.raises(ValueError, match=MAX_BUDGET_ENV):
        from_env({MODEL_PIN_ENV: "claude-sonnet-5", MAX_BUDGET_ENV: "-0.10"})
    with pytest.raises(ValueError, match=MAX_BUDGET_ENV):
        from_env({MODEL_PIN_ENV: "claude-sonnet-5", MAX_BUDGET_ENV: "0"})


def test_defaults_to_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODEL_PIN_ENV, "claude-sonnet-5")
    monkeypatch.setenv(MAX_BUDGET_ENV, "0.75")
    adapter = from_env()
    assert adapter._model_pin == "claude-sonnet-5"
    assert adapter._max_budget_usd == Decimal("0.75")


# -- the operational envelope (review finding D10) ----------------------------
#
# Validated at construction rather than at first use: a deployment that
# mistypes its timeout should fail to start with a message naming the
# variable, not discover the problem during a customer's turn.


def test_envelope_defaults_when_nothing_is_set() -> None:
    envelope = envelope_from_env({})
    assert envelope.timeout_seconds == 120.0
    assert envelope.max_retries == 2
    assert envelope.max_concurrency == 4


def test_envelope_environment_overrides() -> None:
    envelope = envelope_from_env(
        {TIMEOUT_ENV: "45", MAX_RETRIES_ENV: "1", MAX_CONCURRENCY_ENV: "8"}
    )
    assert envelope.timeout_seconds == 45.0
    assert envelope.max_retries == 1
    assert envelope.max_concurrency == 8


def test_retries_can_be_switched_off_entirely() -> None:
    assert envelope_from_env({MAX_RETRIES_ENV: "0"}).max_attempts == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (TIMEOUT_ENV, "two minutes"),
        (TIMEOUT_ENV, "0"),
        (TIMEOUT_ENV, "-1"),
        (MAX_RETRIES_ENV, "lots"),
        (MAX_RETRIES_ENV, "-1"),
        (MAX_CONCURRENCY_ENV, "0"),
        (MAX_CONCURRENCY_ENV, "unbounded"),
    ],
)
def test_invalid_envelope_values_are_rejected_at_construction(name: str, value: str) -> None:
    with pytest.raises(ValueError, match="LLM envelope"):
        envelope_from_env({name: value})


def test_from_env_applies_the_envelope() -> None:
    adapter = from_env({MODEL_PIN_ENV: "claude-sonnet-5", TIMEOUT_ENV: "30", MAX_RETRIES_ENV: "1"})
    assert adapter.envelope.timeout_seconds == 30.0
    assert adapter.envelope.max_retries == 1
