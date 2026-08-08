"""Environment-driven construction of the Claude adapter.

The application composes its environment (loading ``.env`` is the app's job —
this module only reads the process environment or an explicitly passed
mapping, so tests never touch global state).

Conventions (``.env.example``):

- ``REVI_MODEL_PIN`` — **mandatory**. Construction fails without an explicit
  model pin: an unpinned Claude Agent SDK call inherits the local login's
  session default model (spike RESULTS.md §4 — ~4x cost, nondeterministic).
- ``REVI_LLM_MAX_BUDGET_USD`` — optional per-call budget ceiling in USD;
  defaults to 0.50. Enforced by the CLI *between* turns, so treat it as a
  soft cap.
- ``REVI_LLM_TIMEOUT_SECONDS`` — wall clock for a whole call *including its
  retries*; defaults to 120. Generous on purpose: it exists to catch a hang,
  not to trim a slow-but-working call.
- ``REVI_LLM_MAX_RETRIES`` — retries after the first attempt, for transient
  transport failures only; defaults to 2.
- ``REVI_LLM_MAX_CONCURRENCY`` — live SDK subprocesses this process will run
  at once; defaults to 4.

Every one of these is validated here rather than at first use. A deployment
that mistypes its timeout should fail to start with a message naming the
variable, not discover the problem during a customer's turn.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from revi_adapter_claude.adapter import DEFAULT_MAX_BUDGET_USD, ClaudeAgentSdkLanguageModel
from revi_adapter_claude.envelope import LlmEnvelope

MODEL_PIN_ENV = "REVI_MODEL_PIN"
MAX_BUDGET_ENV = "REVI_LLM_MAX_BUDGET_USD"
TIMEOUT_ENV = "REVI_LLM_TIMEOUT_SECONDS"
MAX_RETRIES_ENV = "REVI_LLM_MAX_RETRIES"
MAX_CONCURRENCY_ENV = "REVI_LLM_MAX_CONCURRENCY"


def _float_env(source: Mapping[str, str], name: str, default: float) -> float:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number in seconds (got {raw!r})") from None


def _int_env(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number (got {raw!r})") from None


def envelope_from_env(source: Mapping[str, str]) -> LlmEnvelope:
    """The operational envelope, or a ``ValueError`` naming the bad variable."""
    defaults = LlmEnvelope()
    try:
        return LlmEnvelope(
            timeout_seconds=_float_env(source, TIMEOUT_ENV, defaults.timeout_seconds),
            max_retries=_int_env(source, MAX_RETRIES_ENV, defaults.max_retries),
            max_concurrency=_int_env(source, MAX_CONCURRENCY_ENV, defaults.max_concurrency),
        )
    except ValueError as exc:
        raise ValueError(f"invalid LLM envelope configuration: {exc}") from None


def from_env(env: Mapping[str, str] | None = None) -> ClaudeAgentSdkLanguageModel:
    """Build a :class:`ClaudeAgentSdkLanguageModel` from environment variables.

    ``env`` defaults to ``os.environ``; pass a mapping for hermetic tests.
    Raises ``ValueError`` with an actionable message when ``REVI_MODEL_PIN``
    is unset/empty or the budget is not a positive decimal.
    """
    source: Mapping[str, str] = os.environ if env is None else env

    model_pin = source.get(MODEL_PIN_ENV, "").strip()
    if not model_pin:
        raise ValueError(
            f"{MODEL_PIN_ENV} is not set (or empty). Revi refuses to construct the "
            "Claude adapter without an explicit model pin: unpinned calls inherit "
            "the local Claude login's session default model, which is unpredictable "
            "and can cost ~4x. Set it in the environment, e.g. "
            f"{MODEL_PIN_ENV}=claude-opus-5 (see .env.example)."
        )

    raw_budget = source.get(MAX_BUDGET_ENV, "").strip()
    max_budget_usd = DEFAULT_MAX_BUDGET_USD
    if raw_budget:
        try:
            max_budget_usd = Decimal(raw_budget)
        except InvalidOperation as exc:
            raise ValueError(
                f"{MAX_BUDGET_ENV} must be a decimal amount in USD (got {raw_budget!r})"
            ) from exc
        if max_budget_usd <= 0:
            raise ValueError(f"{MAX_BUDGET_ENV} must be positive (got {raw_budget!r})")

    return ClaudeAgentSdkLanguageModel(
        model_pin, max_budget_usd=max_budget_usd, envelope=envelope_from_env(source)
    )
