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
    #: Cell/population counts the answer certified — the integers a
    #: suppression disclosure may quote. Round-3 R3-18: small integers get
    #: a free pass from the numeric check (they read as counts and years),
    #: so "only part of a fifteen-cell set in which several cells were
    #: withheld" validated over 12 cells of which none were withheld. A
    #: population claim is now checked against this list rather than waved
    #: through. Empty means "no population count was certified", and a
    #: sentence that makes one is dropped.
    population_counts: list[int] = Field(default_factory=list)
    #: True when this turn carries a caution-severity disclosure. Bans the
    #: "can be taken at face value" family of sentences (round-3 R3-05):
    #: the engine warned that a 92-day comparison held 33 days of data and
    #: the prose said the magnitude and direction "can be taken at face
    #: value for the period and basis stated".
    cautioned: bool = False
    #: True when findings were truncated, so superlative and spread claims
    #: describe the served slice rather than the population (R3-04).
    truncated: bool = False
    #: A deterministic sentence naming what the answer is about, prepended
    #: when redaction removes the narrative's opening and leaves a pronoun
    #: with no antecedent (R3-12).
    topic_sentence: str | None = None
    #: The anomaly id at rank 1 of the worklist this answer carries, when
    #: the worklist IS the answer (round-3 R3-10). A prose sentence that
    #: names a DIFFERENT first action is a contradiction of the ranked list
    #: published on the same card — live, the narrative's closing
    #: instruction named a payer at $33,954.90 as the first thing to work
    #: while worklist rank 1 was a card at $178,216.82. ``None`` means no
    #: worklist routed and the prose owns the recommendation as before.
    worklist_first_action: str | None = None


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
