"""Neutral shapes for narrative grounding (design §2.2: the narrative is
composed from certified findings and validated against them).

``NarrativeFacts`` is everything the validator may trust: the certified
numbers (finding values plus header figures), the referent handles a claim
may cite, the closed name vocabulary, and date tokens. The validator in
``revi_presentation`` consumes exactly this; the composition call site
builds it from the turn outcome.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class NarrativeFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    referent_ids: list[str] = Field(default_factory=list)
    numeric_values: list[Decimal] = Field(default_factory=list)
    allowed_names: list[str] = Field(default_factory=list)
    date_tokens: list[str] = Field(default_factory=list)


class NarrativeRedaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence: str
    reason: str


class NarrativeValidation(BaseModel):
    """The authoritative post-validation text plus what was removed."""

    model_config = ConfigDict(extra="forbid")

    text: str
    redactions: list[NarrativeRedaction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.redactions
