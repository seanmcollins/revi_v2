"""Tests for environment-driven adapter construction (config.from_env).

``from_env`` never reads ``.env`` itself — it consumes the process
environment (or an explicit mapping, used here for hermeticity)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_adapter_claude.config import MAX_BUDGET_ENV, MODEL_PIN_ENV, from_env


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
