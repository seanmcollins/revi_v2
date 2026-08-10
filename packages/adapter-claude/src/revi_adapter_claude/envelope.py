"""The operational envelope around every LLM call.

Without a wall-clock bound, a retry and a subprocess cap, a hung subprocess
holds a turn open indefinitely, a transient spawn failure loses the turn
outright, and N concurrent turns mean N live CLI subprocesses — against a
provider measured at 5-15s per call, with outliers past 20s.

This module is the policy, kept apart from the adapter that applies it so the
classification can be read and tested on its own. Four decisions carry the
weight, and each is a decision *against* an easier alternative:

**One wall-clock envelope per port call, not per attempt.** The deadline is
set once, and retries spend the same budget. A retrying provider therefore
can never take longer than a non-retrying one — the alternative (a fresh
timeout per attempt) turns a 120s bound into a silent 360s one exactly when
the provider is already sick.

**Retry only genuinely transient transport conditions.** A schema-validation
failure is a *model* problem: the model saw the prompt and produced the wrong
shape, and asking again costs money to get the same answer. A budget error is
a *policy* problem, and retrying it is how a cap becomes a suggestion. A 4xx
is a request problem. None of them are retried. What is retried: a connection
that never opened, a subprocess that died, and a 5xx/408/429 from a provider
saying "not now".

**A timeout is terminal.** It is the one bound the caller asked for, so
spending it twice more would defeat it — and the SDK's own handshake timeout
is indistinguishable from ours at this layer, so treating both as terminal is
the answer that never guesses wrong in the expensive direction.

**Terminal beats transient on the exception hierarchy.** ``CLINotFoundError``
subclasses ``CLIConnectionError`` in the SDK, so a plain "is it a connection
error?" MRO walk would retry a missing CLI — a configuration failure that will
fail identically three times. Terminal names are matched first.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: HTTP-ish statuses that mean "not now" rather than "not ever".
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429})

#: Exception class names that indicate a connection, spawn, or subprocess
#: failure. Matched against the whole MRO so this module needs no imports from
#: the SDK or any HTTP library — it classifies what it is handed.
_TRANSIENT_EXCEPTION_NAMES = frozenset(
    {
        "APIConnectionError",
        "BrokenPipeError",
        "CLIConnectionError",  # SDK: the CLI subprocess would not talk to us
        "ConnectError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ProcessError",  # SDK: the CLI subprocess died
        "ReadError",
        "RemoteProtocolError",
        "TransportError",
        "WriteError",
    }
)

#: Checked *before* the transient set, because these are subclasses of
#: transient-looking exceptions that will fail identically on every attempt.
#: ``CLINotFoundError`` is a ``CLIConnectionError``; the CLI does not appear
#: on disk because we asked twice more.
_TERMINAL_EXCEPTION_NAMES = frozenset({"CLINotFoundError"})


@dataclass(frozen=True, slots=True)
class LlmEnvelope:
    """Timeout, retry budget, and concurrency cap for one adapter instance."""

    #: Wall clock for the WHOLE port call including retries. Generous by
    #: design: the measured p95 for a refinement call is well inside 30s, so
    #: 120s catches a hang without cutting off a slow-but-working call.
    timeout_seconds: float = 120.0
    #: Bounded hard. Three attempts against a sick provider is a diagnosis;
    #: more is a denial-of-service against the provider and the budget.
    max_retries: int = 2
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    #: Concurrent SDK subprocesses. Each is a real OS process with its own
    #: memory; unbounded, a burst of turns is a fork bomb with a credit card.
    max_concurrency: int = 4

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must not be negative")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must not be below initial_backoff_seconds")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1

    def backoff_for(self, attempt: int, *, jitter: float) -> float:
        """Full-jitter exponential backoff for the delay after ``attempt``.

        Full jitter (uniform over ``[0, span]``) rather than plain exponential:
        several turns that fail at the same instant must not all wake at the
        same instant and re-collide. ``jitter`` is a 0..1 fraction so tests can
        pin the delay exactly instead of sampling a random one.
        """
        growth = float(2 ** max(0, attempt - 1))
        span = min(self.max_backoff_seconds, self.initial_backoff_seconds * growth)
        return max(0.0, span * jitter)


def default_jitter() -> float:
    """The 0..1 fraction used in production. Tests inject a fixed value."""
    return random.random()  # backoff spread, not a security decision


def status_code_of(exc: BaseException) -> int | None:
    """Best-effort HTTP status for an exception, without HTTP-library imports."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    if response is None:
        return None
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    if isinstance(response, Mapping):
        metadata: Any = response.get("ResponseMetadata")
        if isinstance(metadata, Mapping):
            status = metadata.get("HTTPStatusCode")
            if isinstance(status, int):
                return status
    return None


def is_transient(exc: BaseException) -> bool:
    """Is this failure worth another attempt?

    ``False`` for anything that arrived and was rejected on its merits — a
    4xx, a missing CLI, a schema failure (which never reaches here: it is a
    non-exceptional result, not an exception).
    """
    names = {klass.__name__ for klass in type(exc).__mro__}
    if names & _TERMINAL_EXCEPTION_NAMES:
        return False
    status = status_code_of(exc)
    if status is not None:
        return status in _RETRYABLE_STATUS_CODES or 500 <= status < 600
    return bool(names & _TRANSIENT_EXCEPTION_NAMES)
