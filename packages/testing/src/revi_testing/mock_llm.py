"""Table-driven mock for the ``LanguageModelPort``.

Rules map ``(template_id, optional prompt matcher)`` to a canned structured
response (a plain dict, exactly what the schema-validated adapter would
return) or ``None`` to simulate the model failing the schema
(``structured_output=None``, a real SDK outcome). Every call is recorded for
assertions; an unmatched structured call fails the test loudly rather than
inventing an answer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from revi_investigation.application.ports import (
    LlmFailureKind,
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_testing.fakes import make_usage

PromptMatcher = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class CannedResponse:
    template_id: str
    response: Mapping[str, Any] | None
    matcher: PromptMatcher | None = None
    usage: LlmUsage | None = None
    #: Which kind of empty-handed a ``response=None`` rule simulates.
    #: ``None`` leaves the port's conservative reading ("the adapter did
    #: not say"), itself a real adapter outcome. "The model declined" and
    #: "the shape never validated" want different recoveries, so they need
    #: to be distinguishable here.
    failure: LlmFailureKind | None = None

    def __post_init__(self) -> None:
        if self.response is not None and self.failure is not None:
            raise ValueError(
                "a canned rule cannot both answer and fail: drop `failure`, or set "
                "`response=None` to simulate the model coming back empty-handed"
            )


@dataclass
class MockLanguageModel:
    """In-memory ``LanguageModelPort``: canned answers, full call recording.

    Every request is recorded whole, ``policy`` included, so a test can
    assert which model tier and which per-call budget a turn actually sent
    rather than trusting that it did.
    """

    rules: list[CannedResponse] = field(default_factory=list)
    structured_calls: list[StructuredLlmRequest] = field(default_factory=list)
    text_calls: list[TextLlmRequest] = field(default_factory=list)
    text_chunks: tuple[str, ...] = ()
    #: Stands in for an adapter that applies per-call policy, so wiring
    #: that reads this flag exercises the tier path. Set it False to
    #: reproduce a deployment whose model cannot honor a tier, where naming
    #: one is refused rather than silently ignored.
    applies_call_policy: bool = True

    def respond(
        self,
        template_id: str,
        response: Mapping[str, Any] | None,
        *,
        matcher: PromptMatcher | None = None,
        usage: LlmUsage | None = None,
        failure: LlmFailureKind | None = None,
    ) -> None:
        """Register a canned response.

        ``response=None`` simulates the model coming back with nothing
        usable; ``failure`` names which kind — ``SCHEMA`` (the answer never
        arrived in a readable shape, so asking again can work) versus
        ``DECLINED``/``OFF_SCRIPT`` (the model had no mapping and will not
        the next time either). Omit it to reproduce an adapter that did not
        say, which is the port's conservative path.
        """
        self.rules.append(
            CannedResponse(
                template_id=template_id,
                response=response,
                matcher=matcher,
                usage=usage,
                failure=failure,
            )
        )

    def calls_for(self, template_id: str) -> tuple[StructuredLlmRequest, ...]:
        return tuple(c for c in self.structured_calls if c.template_id == template_id)

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        self.structured_calls.append(request)
        for rule in self.rules:
            if rule.template_id != request.template_id:
                continue
            if rule.matcher is not None and not rule.matcher(request.rendered_prompt):
                continue
            output = dict(rule.response) if rule.response is not None else None
            return StructuredLlmResult(
                output=output,
                usage=rule.usage or make_usage(),
                failure=rule.failure if output is None else None,
            )
        raise AssertionError(
            f"MockLanguageModel has no canned response for template "
            f"{request.template_id!r} (prompt starts: {request.rendered_prompt[:80]!r})"
        )

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        self.text_calls.append(request)

        async def iterate() -> AsyncIterator[str]:
            for chunk in self.text_chunks:
                yield chunk

        return iterate()

    async def last_usage(self) -> LlmUsage | None:
        return make_usage() if self.text_calls else None
