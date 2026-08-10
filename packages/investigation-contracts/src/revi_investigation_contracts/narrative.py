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
    #: How many caution-severity warnings the ANSWER publishes — which is
    #: not the same as how many of them this composer was handed. Round-5
    #: C-01: the prose asserted "No mandatory caveats were attached to
    #: these findings on this turn" on two turns rendering amber banners,
    #: because it was reporting the emptiness of its own prompt slot as a
    #: fact about the answer. Any affirmation of the absence of caveats is
    #: derived from this census, and a sentence that makes one anyway is
    #: redacted.
    published_cautions: int = 0
    #: True when findings were truncated, so superlative and spread claims
    #: describe the served slice rather than the population (R3-04).
    truncated: bool = False
    #: A deterministic sentence naming what the answer is about, prepended
    #: when redaction removes the narrative's opening and leaves a pronoun
    #: with no antecedent (R3-12).
    topic_sentence: str | None = None
    #: What this answer CAN certify about its own leader, put in the place
    #: of a superlative the guard removes (round-9 R9-09). "Who is my worst
    #: payer on denial rate right now" returned 402 words in which the words
    #: worst, highest and top never appeared: the guard was right to refuse
    #: a superlative over a truncated list, and deleting the answering
    #: sentence left a hole where the answer went. The substitute states the
    #: relation that IS certified — the leading finding is the highest
    #: MEASURED figure — and says out loud why that is not the same claim.
    #: ``None`` when the answer has no leader to name, in which case
    #: deletion stands.
    superlative_substitute: str | None = None
    #: The anomaly id at rank 1 of the worklist this answer carries, when
    #: the worklist IS the answer (round-3 R3-10). A prose sentence that
    #: names a DIFFERENT first action is a contradiction of the ranked list
    #: published on the same card — live, the narrative's closing
    #: instruction named a payer at $33,954.90 as the first thing to work
    #: while worklist rank 1 was a card at $178,216.82. ``None`` means no
    #: worklist routed and the prose owns the recommendation as before.
    worklist_first_action: str | None = None
    #: Size words the premise verdict has already ruled out (round-4
    #: R4-05). When the aggregate rose 72.6% against a question that
    #: assumed a doubling, the verdict published above the prose says "it
    #: did not double" — and a composer that then writes "denials roughly
    #: doubled" has contradicted the answer's own first claim in its second
    #: paragraph. Each entry is the question's own verb ("double",
    #: "triple", "halve"); a sentence asserting one is dropped unless it
    #: negates it. Empty on every turn that asserts no size, which is
    #: almost all of them.
    forbidden_magnitude_claims: list[str] = Field(default_factory=list)


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
