"""Table-driven mock for the ``LanguageModelPort``.

Rules map ``(template_id, optional prompt matcher)`` to a canned structured
response (a plain dict, exactly what the schema-validated adapter would
return) or ``None`` to simulate the model failing the schema
(``structured_output=None`` — SDK spike trap #4). Every call is recorded
for assertions; an unmatched structured call fails the test loudly rather
than inventing an answer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from revi_investigation.application.ports import (
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


@dataclass
class MockLanguageModel:
    """In-memory ``LanguageModelPort``: canned answers, full call recording."""

    rules: list[CannedResponse] = field(default_factory=list)
    structured_calls: list[StructuredLlmRequest] = field(default_factory=list)
    text_calls: list[TextLlmRequest] = field(default_factory=list)
    text_chunks: tuple[str, ...] = ()

    def respond(
        self,
        template_id: str,
        response: Mapping[str, Any] | None,
        *,
        matcher: PromptMatcher | None = None,
        usage: LlmUsage | None = None,
    ) -> None:
        """Register a canned response; ``None`` simulates schema failure."""
        self.rules.append(
            CannedResponse(template_id=template_id, response=response, matcher=matcher, usage=usage)
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
            return StructuredLlmResult(output=output, usage=rule.usage or make_usage())
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
