"""Neutral shapes for narrative grounding (design §2.2: the narrative is
composed from certified findings and validated against them).

``NarrativeFacts`` is everything the validator may trust: the certified
numbers (finding values plus header figures), the referent handles a claim
may cite, the closed name vocabulary, and date tokens. The validator in
``revi_presentation`` consumes exactly this; the composition call site
builds it from the turn outcome.

``ReadingSeries`` is the one channel that carries more than certified
values: the ordered figures of a reading WITH the ranges around them, so a
claim about which way something moved, or which row is best, can be
checked rather than trusted. See its docstring for the determination that
made it necessary.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SeriesPoint(BaseModel):
    """One published figure of one reading, with the range around it.

    ``display`` is the figure as its own reading already formatted it —
    "47.2%", "$99,093" — so nothing downstream re-derives a percentage from
    a ratio or dollars from cents. ``value`` and the two bounds are the
    exact decimals the same figure carries, and they exist for arithmetic
    the reader never sees: whether two figures are far enough apart to be
    called a direction, and whether a spread is wider than the uncertainty
    around it. A withheld figure has no value and no bounds; a bounded one
    carries a ceiling rather than a measurement, and neither can bear a
    claim about which way something moved.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    label: str
    display: str = ""
    value: Decimal | None = None
    interval_low: Decimal | None = None
    interval_high: Decimal | None = None
    bounded: bool = False
    withheld: bool = False


class ReadingSeries(BaseModel):
    """The ordered figures one reading published, and what they can bear.

    The grounding validator could always check that a number was measured;
    it could never check that a SENTENCE about those numbers was. So a
    study published "it is getting worse on appeals: 47.2% Aug … 45.2% Jan,
    ending below where it started" over six months whose confidence
    intervals every one of them overlapped every other — point estimates
    spanning 11.3 points inside ranges 28 points wide — and truncated the
    series at January, without saying so, because the four months it left
    out are what made "ending below where it started" false. Every figure
    in that sentence was certified. The direction was not, and nothing on
    the wire reached the validator that could have said so.

    This is that channel. One entry per reading, carrying the reading's own
    ordered figures WITH their ranges, so a direction, a ranking or a
    truncated window is a deterministic comparison rather than a
    plausible-sounding paragraph.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    #: The reading this series came from, so a redaction can be traced back
    #: to the table it was about.
    reading_id: str
    #: What the figures measure, in the reader's words — never a metric id.
    measure_label: str = ""
    #: The window the reading itself was taken over. A claim's window is
    #: this window; a claim that quotes less of the series than the reading
    #: published is describing a slice it chose.
    window_label: str = ""
    #: True when the points are periods in order. "Rose", "fell" and
    #: "ending below where it started" mean something over a trend and
    #: nothing over a league table, where the first and last rows are an
    #: ordering rather than a start and an end.
    over_time: bool = False
    #: The figures in the order the reading published them.
    points: list[SeriesPoint] = Field(default_factory=list)
    #: The width of the widest range this study demonstrated for figures of
    #: this kind — this reading's own where it published ranges, and
    #: otherwise the middle width its sibling readings of the same unit
    #: published. A facility study that names a best and a worst 1.1 points
    #: apart, while the study's own measurements of that kind of rate carry
    #: ranges 30 points wide, is ordering noise. ``None`` where the study
    #: published no ranges at all, in which case no claim is refused on
    #: this ground — precision is never invented.
    comparable_interval_width: Decimal | None = None
    #: What goes in the place of a direction the ranges do not support,
    #: composed here rather than by the validator: the sentence names two
    #: figures, and the module that composes it is the one holding the
    #: figures. Empty where the reading has no two measured endpoints.
    direction_substitute: str = ""
    #: What goes in the place of a best-or-worst the spread does not
    #: support — the same discipline a ranking already follows when too
    #: much of its field is a ceiling. Empty where there is nothing to
    #: order.
    ranking_refusal: str = ""


class NarrativeFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    referent_ids: list[str] = Field(default_factory=list)
    numeric_values: list[Decimal] = Field(default_factory=list)
    allowed_names: list[str] = Field(default_factory=list)
    date_tokens: list[str] = Field(default_factory=list)
    #: Cell/population counts the answer certified — the integers a
    #: suppression disclosure may quote. Small integers get a free pass from
    #: the numeric check (they read as counts and years), which lets "only
    #: part of a fifteen-cell set in which several cells were withheld"
    #: validate over 12 cells of which none were withheld. A population
    #: claim is checked against this list rather than waved through. Empty
    #: means "no population count was certified", and a sentence that makes
    #: one is dropped.
    population_counts: list[int] = Field(default_factory=list)
    #: True when this turn carries a caution-severity disclosure. Bans the
    #: "can be taken at face value" family of sentences: prose has otherwise
    #: called a comparison's magnitude and direction reliable "for the
    #: period and basis stated" on a turn where the engine warned that a
    #: 92-day comparison held 33 days of data.
    cautioned: bool = False
    #: How many caution-severity warnings the ANSWER publishes — which is
    #: not the same as how many of them this composer was handed. Prose that
    #: reads its own empty prompt slot as a fact about the answer asserts
    #: "No mandatory caveats were attached to these findings on this turn"
    #: on turns rendering caution banners. Any affirmation of the absence of
    #: caveats is derived from this census, and a sentence that makes one
    #: anyway is redacted.
    published_cautions: int = 0
    #: True when findings were truncated, so superlative and spread claims
    #: describe the served slice rather than the population.
    truncated: bool = False
    #: A deterministic sentence naming what the answer is about, prepended
    #: when redaction removes the narrative's opening and leaves a pronoun
    #: with no antecedent.
    topic_sentence: str | None = None
    #: What this answer CAN certify about its own leader, put in the place
    #: of a superlative the guard removes. Refusing a superlative over a
    #: truncated list is right, but plain deletion leaves a hole where the
    #: answer went — "who is my worst payer on denial rate right now" comes
    #: back as hundreds of words in which worst, highest and top never
    #: appear. The substitute states the relation that IS certified — the
    #: leading finding is the highest MEASURED figure — and says out loud
    #: why that is not the same claim. ``None`` when the answer has no
    #: leader to name, in which case deletion stands.
    superlative_substitute: str | None = None
    #: The anomaly id at rank 1 of the worklist this answer carries, when
    #: the worklist IS the answer. A prose sentence that names a DIFFERENT
    #: first action contradicts the ranked list published on the same card —
    #: a closing instruction has named a payer at $33,954.90 as the first
    #: thing to work while worklist rank 1 was a card at $178,216.82.
    #: ``None`` means no worklist routed and the prose owns the
    #: recommendation as before.
    worklist_first_action: str | None = None
    #: Size words the premise verdict has already ruled out. When the
    #: aggregate rose 72.6% against a question that assumed a doubling, the
    #: verdict published above the prose says "it did not double" — and a
    #: composer that then writes "denials roughly doubled" contradicts the
    #: answer's own first claim in its second paragraph. Each entry is the
    #: question's own verb ("double", "triple", "halve"); a sentence
    #: asserting one is dropped unless it negates it. Empty on every turn
    #: that asserts no size, which is almost all of them.
    forbidden_magnitude_claims: list[str] = Field(default_factory=list)
    #: The interval-carrying series this answer rests on, one per reading —
    #: see :class:`ReadingSeries` for what a study published without it.
    #: Empty on the quick path, which measures no intervals; a guard that
    #: has no ranges to check against never fires, and none are invented to
    #: make it fire.
    interval_series: list[ReadingSeries] = Field(default_factory=list)


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
