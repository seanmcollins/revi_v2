"""Public wire shapes for deep research — the recoverability mode.

Deep research is a long-running investigation that answers one question
over a tenant's own denial history: of the denials still open, how much is
realistically coming back, and where. It runs in three phases — a plan
chosen from a closed catalogue of research angles, deterministic execution
of each angle against the statistics capability, and a written report.

Two rules shape every model below and are worth stating before the code.

**Every published number carries its evidence tier.** ``measured`` means
this population's own history supported a rate, with the interval and the
size of the population behind it. ``not_estimable`` means it did not: the
count and the dollars are still published, the rate is not, and the
expected-recovery total excludes it. No industry average, no pooled rate
and no neighbouring population's rate is ever substituted — a total that
quietly borrows a rate looks complete and cannot be checked.

**Rates travel as exact decimal text, money as whole cents.** A rate
published as a float would differ in its last digits between machines, and
two runs over the same data load must agree byte for byte.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from revi_investigation_contracts.api import ChartSpec, FindingPayload, WarningPayload
from revi_investigation_contracts.deep_research_offer import (
    DeepResearchAffordance,
    DeepResearchSelector,
    SelectorKindLiteral,
)
from revi_investigation_contracts.refinements import ClosedModel

__all__ = [
    "DEEP_RESEARCH_EVENT_PAYLOADS",
    "AngleEvidencePayload",
    "AngleFamilyLiteral",
    "CensoringPayload",
    "ConsultedNotePayload",
    "ContrastArmPayload",
    "ContrastPayload",
    "ContrastTestLiteral",
    "DeadlinePayload",
    "DeadlineRowPayload",
    "DeepResearchAffordance",
    "DeepResearchEventKind",
    "DeepResearchListResponse",
    "DeepResearchPhaseLiteral",
    "DeepResearchPreviewPayload",
    "DeepResearchProgressPayload",
    "DeepResearchReport",
    "DeepResearchRunResponse",
    "DeepResearchScopePayload",
    "DeepResearchSelector",
    "DeepResearchStatusLiteral",
    "DeepResearchStreamEvent",
    "DeepResearchSummary",
    "DeterminationPayload",
    "EvidenceTierLiteral",
    "ExpectedRecoveryRowPayload",
    "GeneralizedResearchPreviewPayload",
    "GeneralizedResearchReport",
    "HeadlinePayload",
    "IntervalPayload",
    "MoneyIntervalPayload",
    "PlannedReadingPayload",
    "RateBasisLiteral",
    "RateCellPayload",
    "ReportKindLiteral",
    "ResearchAnglePayload",
    "ResearchCensoringPayload",
    "ResearchFigurePartPayload",
    "ResearchFigurePayload",
    "ResearchPathChoicePayload",
    "ResearchPlanPayload",
    "ResearchReadingPayload",
    "ResearchRoundPayload",
    "ResearchShapeLiteral",
    "ResearchWalkActionLiteral",
    "ResearchWalkPayload",
    "ResearchWalkStepPayload",
    "SelectorKindLiteral",
    "StartDeepResearchRequest",
    "StratumLiteral",
    "StratumPartPayload",
    "ThinPopulationsPayload",
    "TimelinessBandPayload",
    "TimelinessCurvePayload",
]

# ---------------------------------------------------------------------------
# closed vocabularies

#: The research angles a run may contain. Closed: the control plane picks
#: from this list and cannot invent an entry, and each angle is executed by
#: deterministic code that the control plane never touches.
AngleFamilyLiteral = Literal[
    "outcome_by_stratum",
    "payer_contrast",
    "class_contrast",
    "timeliness_curve",
    "deadline_interaction",
    "expected_recovery",
]

#: The populations an angle may cut by. Each one is a column read straight
#: off a denial; nothing else can be named.
StratumLiteral = Literal[
    "payer",
    "plan",
    "recovery_class",
    "age_band",
    "dollar_band",
    "delay_band",
    "filing_position",
    "filing_rule",
]

#: Which denominator a rate is over. ``decided`` is "given we resubmitted
#: and the payer answered, how often did we win" — open chains are in
#: neither the numerator nor the denominator, never counted as losses.
#: ``pursuit`` is "was this worked at all", over denials old enough that a
#: resubmission would have been seen by now.
RateBasisLiteral = Literal["decided", "pursuit"]

#: ``measured`` — this population's own history supported a rate.
#: ``not_estimable`` — it did not, and nothing was substituted.
EvidenceTierLiteral = Literal["measured", "not_estimable"]

#: How two populations were compared, or that they were not.
ContrastTestLiteral = Literal["two_proportion_z", "fishers_exact", "refused"]

#: ``preview`` is the one state that is not a run: a plan-only request
#: resolved what a run would do and started nothing.
#:
#: ``cancelled`` and ``interrupted`` are deliberately two words for two
#: different facts. Somebody asked for this run to stop and it stopped —
#: that is ``cancelled``, and the record says how far it got. Nobody asked,
#: and the process carrying it died — that is ``interrupted``, and all the
#: record can honestly say is that the run started and never finished.
#: Flattening them would tell a reader who pressed Stop that something went
#: wrong, and a reader whose run was lost that they did it themselves.
DeepResearchStatusLiteral = Literal[
    "preview", "planning", "running", "complete", "failed", "interrupted", "cancelled"
]

#: The phases a run passes through, in the order it passes through them.
#: The first three are the generalized loop's own — orienting on the data,
#: consulting the definitions library's background notes, and reading a
#: round's results before deciding what to chase. A surface that only knew
#: ``plan | execute | synthesize`` would render a minute of orienting and
#: deciding as "still going", which is exactly the part of a research run a
#: reader most wants to watch.
DeepResearchPhaseLiteral = Literal[
    "orient", "consult", "plan", "execute", "read", "round", "synthesize"
]

#: Frames a run emits while it is in flight, plus the terminal frame.
DeepResearchEventKind = Literal[
    "research_started",
    "research_plan",
    "research_readings",
    "research_progress",
    "research_finding",
    "research_warning",
    "narrative_delta",
    "error",
    "research_cancelled",
    "research_complete",
]

#: What each frame carries, published so a client can branch without
#: reading the server.
DEEP_RESEARCH_EVENT_PAYLOADS: dict[str, str] = {
    "research_started": "the run's id, the data load it is pinned to, and the population it targets",
    "research_plan": "the angles this run will look at, in the order it will look at them",
    "research_readings": (
        "a research study's opening readings, each with the reason it is in the run — "
        "the generalized twin of the plan frame, sent as soon as the readings are chosen"
    ),
    "research_progress": "which angle is running, how far along, and how long it has taken",
    "research_finding": "one certified result, the moment it is measured",
    "research_warning": "a qualification a reader needs before reading the numbers",
    "narrative_delta": "one chunk of the written report as it is composed",
    "error": "the run stopped; nothing partial is published",
    "research_cancelled": (
        "somebody stopped this run — it ended at the next safe point, nothing partial "
        "is published, and the record keeps how far it got"
    ),
    "research_complete": "the finished report",
}

#: Which of the two report shapes a run produced. The recoverability review
#: answers one standing question about open denials and publishes
#: :class:`DeepResearchReport`; a research question is a study and publishes
#: :class:`GeneralizedResearchReport`. They are different artifacts because
#: they answer different kinds of question, and flattening them into one
#: shape would mean either a study carrying an expected-recovery headline it
#: never computed or a review losing the one it exists for.
ReportKindLiteral = Literal["recovery", "generalized"]


# ---------------------------------------------------------------------------
# small shared shapes


class IntervalPayload(ClosedModel):
    """A confidence interval on a rate, as exact decimal text."""

    low: str
    high: str
    confidence: str


class MoneyIntervalPayload(ClosedModel):
    """A confidence interval on dollars, in whole cents."""

    low_cents: int
    high_cents: int
    confidence: str


class StratumPartPayload(ClosedModel):
    """One ``population = value`` pair, with the words a reader sees."""

    stratifier: StratumLiteral
    stratifier_label: str
    value: str
    value_label: str


class RateCellPayload(ClosedModel):
    """One population's recovery rate, or its refusal to publish one.

    A cell is either a measurement — ``rate`` and ``interval`` both present
    — or a refusal, with both absent. There is no third state. ``n`` and
    ``successes`` are published either way, because the size of the
    population is the reason for the refusal and hiding it would make the
    refusal impossible to check.
    """

    label: str
    parts: list[StratumPartPayload] = Field(default_factory=list)
    basis: RateBasisLiteral
    n: int
    successes: int
    evidence: EvidenceTierLiteral
    rate: str | None = None
    interval: IntervalPayload | None = None
    #: The number of answered denials a rate needs before it is published.
    floor: int


# ---------------------------------------------------------------------------
# the run's own description of itself


class ResearchAnglePayload(ClosedModel):
    """One angle a run looked at, and how it was cut."""

    family: AngleFamilyLiteral
    title: str
    purpose: str
    stratify_by: list[StratumLiteral] = Field(default_factory=list)
    within: list[StratumLiteral] = Field(default_factory=list)
    basis: RateBasisLiteral = "decided"


class ResearchPlanPayload(ClosedModel):
    """The angles a run will look at, and who chose them.

    ``authored_by`` is ``model`` when the control plane selected the angles
    and ``revi`` when the run fell back to the standing set — a run whose
    plan came from a fallback says so rather than presenting it as a
    choice. Either way every angle is one of the closed set, and none of
    them computes anything: the numbers come from deterministic code.
    """

    research_question: str
    angles: list[ResearchAnglePayload] = Field(default_factory=list)
    rationale: str = ""
    authored_by: Literal["model", "revi"] = "revi"
    #: Angles Revi added because the report cannot be written without them.
    added_by_revi: list[AngleFamilyLiteral] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# angle results


class PricedPositionPayload(ClosedModel):
    """One side of the filing deadline inside one population, and its price.

    The unit the total is actually built from. ``scope`` says whose
    evidence set the rate: this population's own answered denials, or the
    whole read's answer for that side of the deadline when this
    population's own cohort was too thin. A reader deciding where to put
    people is entitled to know which of the two they are reading, per line,
    rather than being told once at the bottom of the page.
    """

    #: ``within_deadline``, ``past_deadline`` or ``unknown``.
    position: str
    position_label: str
    dollars_cents: int
    #: ``own``, ``population`` or ``none``.
    scope: Literal["own", "population", "none"]
    rate: str | None = None
    interval: IntervalPayload | None = None
    #: Answered denials the rate rests on.
    n: int = 0
    #: The share of a denied dollar a win returns, applied on top.
    severity: str | None = None
    expected_cents: int | None = None
    expected_interval: MoneyIntervalPayload | None = None


class ExpectedRecoveryRowPayload(ClosedModel):
    """One population of open denials, priced or explicitly not priced."""

    label: str
    parts: list[StratumPartPayload] = Field(default_factory=list)
    evidence: EvidenceTierLiteral
    open_denials: int
    open_dollars_cents: int
    catchable_dollars_cents: int
    deadline_passed_dollars_cents: int
    deadline_unknown_dollars_cents: int
    rate_cell: RateCellPayload
    #: The filing-deadline buckets this population's dollars were priced in.
    positions: list[PricedPositionPayload] = Field(default_factory=list)
    #: The share of a denied dollar a win returns, and whose denials it was
    #: measured over.
    severity: str | None = None
    severity_scope: Literal["own", "population", "none"] = "none"
    severity_wins: int = 0
    expected_cents: int | None = None
    expected_interval: MoneyIntervalPayload | None = None


class ThinPopulationsPayload(ClosedModel):
    """Populations too small to name individually, counted together.

    Naming a population of four denials and printing its dollars discloses
    those four denials. So populations under the disclosure level are
    rolled into this one line: how many there were and what they hold, and
    nothing that identifies any of them.
    """

    populations: int
    open_denials: int
    open_dollars_cents: int
    #: The number of denials a population needs before it is named.
    floor: int


class HeadlinePayload(ClosedModel):
    """What the open denials are worth, and how sure that figure is.

    ``total_expected_cents`` covers measured populations only. Everything
    that could not be measured is in ``unpriced_open_dollars_cents`` and
    listed separately, at full value, so a reader can see exactly how much
    of the inventory went unpriced instead of finding a total that quietly
    assumed zero.

    The range is the sum of each population's own range — the widest way to
    add ranges up, wider than independence would give, and therefore a
    spread indication rather than a calibrated band.
    ``range_is_summed_endpoints`` says which arithmetic produced it, and
    ``amounts_treated_as_known`` says what it leaves out: only the recovery
    rate carries variance, while the denied amounts and the share of a
    denied dollar a win returns enter as constants.

    ``construction`` states, in one sentence, how the figure was built —
    the filing-deadline split, the rate on each side, and what a win is
    actually worth. A headline whose construction is not on the page beside
    it is a headline a reader cannot check.
    """

    total_open_denials: int
    total_open_dollars_cents: int
    total_expected_cents: int
    total_expected_interval: MoneyIntervalPayload
    priced_open_dollars_cents: int
    unpriced_open_dollars_cents: int
    unpriced_share: str
    catchable_dollars_cents: int
    deadline_passed_dollars_cents: int
    deadline_unknown_dollars_cents: int
    #: Dollars inside a priced population that no side-of-the-deadline rate
    #: could price — an unrecorded filing limit, or a side too thin to read.
    unpriced_position_dollars_cents: int = 0
    construction: str = ""
    #: The two population-level rates the construction quotes.
    within_deadline_rate: str | None = None
    within_deadline_n: int = 0
    past_deadline_rate: str | None = None
    past_deadline_n: int = 0
    #: What a win returns on the denied dollar, over the whole read.
    severity: str | None = None
    severity_wins: int = 0
    severity_recovered_cents: int = 0
    severity_denied_cents: int = 0
    range_is_summed_endpoints: bool = True
    amounts_treated_as_known: bool = True


class ContrastArmPayload(ClosedModel):
    label: str
    n: int
    successes: int
    rate: str | None = None
    interval: IntervalPayload | None = None


class ContrastPayload(ClosedModel):
    """Two populations compared, with the effect size and the test.

    A refused contrast publishes both sides' sizes and nothing else: a
    reader needs to know how thin the comparison was, and must not be
    handed a probability the disclosure rules say is not publishable.
    """

    title: str
    left: ContrastArmPayload
    right: ContrastArmPayload
    test: ContrastTestLiteral
    #: The difference between the two rates — the size of the effect,
    #: reported beside the test because significance is not size.
    risk_difference: str | None = None
    risk_difference_interval: IntervalPayload | None = None
    z_statistic: str | None = None
    p_value: str | None = None
    refusal_reason: str | None = None
    #: The one sentence a reader takes away.
    implication: str = ""


class TimelinessBandPayload(ClosedModel):
    band: str
    cell: RateCellPayload


class TimelinessCurvePayload(ClosedModel):
    """Recovery rate by how long the denial waited before going back out."""

    bands: list[TimelinessBandPayload] = Field(default_factory=list)
    within: list[StratumLiteral] = Field(default_factory=list)
    #: What the curve means for the work queue, in one sentence.
    implication: str = ""


class DeadlineRowPayload(ClosedModel):
    position: str
    position_label: str
    rule: str
    rule_label: str
    cell: RateCellPayload


class DeadlinePayload(ClosedModel):
    """What crossing a filing deadline costs, split by the limit's standing.

    A limit stated without a confirmation caveat and a limit that is only a
    planning default are not the same fact, and pooling them over-predicts
    the drop on every plan whose limit nobody has confirmed.
    """

    rows: list[DeadlineRowPayload] = Field(default_factory=list)
    implication: str = ""


class CensoringPayload(ClosedModel):
    """What the edge of the data cost this analysis, stated in counts.

    Nothing here is modelled or extrapolated. Every denial left out of a
    denominator is counted on one of these lines, so the numbers above are
    readable against exactly what they exclude.
    """

    basis: RateBasisLiteral
    data_edge_date: date
    rows_considered: int
    in_denominator: int
    excluded_immature: int = 0
    excluded_open_undecided: int = 0
    excluded_not_pursued: int = 0
    excluded_unclassifiable: int = 0
    open_undecided_in_input: int = 0
    not_pursued_in_input: int = 0
    #: The disclosure in the words a reader gets, one sentence per line.
    statements: list[str] = Field(default_factory=list)


class AngleEvidencePayload(ClosedModel):
    """How one angle got its numbers, for the evidence rail and exports.

    Internal identifiers live here and only here: the read's fingerprint,
    the estimator it called, how long it took. The default surface never
    shows them and this never loses them.
    """

    family: AngleFamilyLiteral
    title: str
    estimator: str
    read_fingerprint: str
    rows_read: int
    rows_in_scope: int
    cells_published: int
    cells_refused: int
    duration_ms: int


# ---------------------------------------------------------------------------
# the report


class DeepResearchReport(ClosedModel):
    """A finished deep-research report — the artifact a link points at.

    Everything a reader needs to check the headline is here: the rate
    behind every dollar, the size of every population, what was left out
    and why, and the words that explain it. Nothing is summarized away.
    """

    id: str
    #: The question the report answers, first sentence first.
    research_question: str
    population: DeepResearchSelector
    #: The load these numbers were read at, in words a reader can use.
    data_load_label: str
    data_edge_date: date
    created_at: datetime
    completed_at: datetime | None = None
    duration_ms: int = 0

    plan: ResearchPlanPayload
    headline: HeadlinePayload
    strata: list[ExpectedRecoveryRowPayload] = Field(default_factory=list)
    not_estimable: list[ExpectedRecoveryRowPayload] = Field(default_factory=list)
    thin_populations: ThinPopulationsPayload | None = None
    rates: list[RateCellPayload] = Field(default_factory=list)
    contrasts: list[ContrastPayload] = Field(default_factory=list)
    timeliness: TimelinessCurvePayload | None = None
    deadline: DeadlinePayload | None = None
    censoring: CensoringPayload

    findings: list[FindingPayload] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    warnings: list[WarningPayload] = Field(default_factory=list)
    narrative: str = ""
    #: Context on what each kind of denial usually takes to fix. Never
    #: blended into a number — it explains the work, it does not price it.
    context_notes: list[str] = Field(default_factory=list)
    evidence: list[AngleEvidencePayload] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# requests, progress and responses


class DeepResearchScopePayload(ClosedModel):
    """How big the population a run would cover is.

    The same two quantities ``ExpectedRecoveryRowPayload`` publishes per
    population, measured over the whole of it, so a surface offering the
    run can say "565 of them, worth $1,153,302.17" instead of naming the
    population and inventing nothing about its size.
    """

    open_denials: int
    open_dollars_cents: int


#: The closed operation shapes a generalized reading may take. Mirrors
#: :class:`revi_investigation.application.deep_research.general.AngleShape`.
#: Closed for the same reason the recovery families are: a control plane
#: picks from this list and cannot invent an entry, and each shape is
#: executed by deterministic code the control plane never touches. What is
#: NOT closed here is which measure a shape is applied to — that is one
#: deployment's own definitions library, resolved against it before a
#: single read is built.
ResearchShapeLiteral = Literal[
    "measure_profile", "stratified_rates", "contrast", "trend", "composition"
]


class ResearchPathChoicePayload(ClosedModel):
    """One thing a run established about the data before it chose anything.

    "Your data carries COB mainly in remit codes — the category field is
    sparsely populated here, so I read the codes." The statement arrives
    already composed, beside the coverage figure it quotes, so no surface
    can re-word it into something true, useless and unfalsifiable.
    """

    #: What the finding is about — a breakdown, a measure, the deployment.
    subject: str
    #: The finding, as one plain sentence a report can print verbatim.
    statement: str


class ConsultedNotePayload(ClosedModel):
    """One background note the run read before deciding what to check.

    The title only. A note's content shapes *which reading runs* and can
    never shape *what a number says*, and publishing its key points beside
    a preview would put an industry figure next to a measured one on the
    same card.
    """

    title: str
    #: What matched it to this question — an alias, a subject area, or the
    #: question's own words. Answers "why was this in front of the
    #: planner" without re-running the matcher.
    matched_on: list[str] = Field(default_factory=list)


class PlannedReadingPayload(ClosedModel):
    """One reading a run intends to take, and the reason it is there.

    The reason is the whole point of showing this before spending a minute
    of work: a confirmation that lists what will be read without saying why
    is a progress bar in advance.
    """

    shape: ResearchShapeLiteral
    #: What the reading is called, in the words the report will use.
    title: str
    #: Why this reading is in the run, in the analyst's own language.
    reason: str
    #: Which round chose it. Round 0 is the opening read; anything above it
    #: is chosen after certified results come back, so a preview only ever
    #: shows round 0 and the report shows the rest.
    round: int = 0
    #: The reading whose result sent the run here, when one did.
    chases: str = ""


class GeneralizedResearchPreviewPayload(ClosedModel):
    """What a research run learned, and what it therefore intends to read.

    Resolved WITHOUT executing any of it: the orientation reads are cheap
    and cached, the background notes are a lookup, and the readings are
    chosen but not run. So the card in front of a minute of work costs a
    fraction of a second and still says something a reader can correct.

    Nothing here is a measurement. The path choices quote coverage — how
    much of the data carries a path — and coverage is a fact about the data
    rather than an answer to the question.
    """

    #: What this run will answer, in one sentence.
    research_question: str
    #: WHAT THIS RUN READS, in the words a research report can use — and
    #: deliberately not the recoverability review's own population noun.
    #: The review is about open denials and says so; a research question
    #: about A/R aging reads claims, remits and balances, and a card that
    #: called that "every open denial" would be describing a population the
    #: run never opens. The two live side by side on the same payload, so
    #: the difference has to be on the wire rather than in a comment.
    population_label: str = ""
    #: The period it will read, in a reader's words.
    window_label: str
    #: The handle for THIS plan. Sent back on the launch request, it makes
    #: the run's opening readings the ones on this card rather than a
    #: fresh draw from the same question.
    plan_id: str = ""
    #: What was established about the data before anything was chosen.
    #: Negative statements lead — what the question wanted that this data
    #: does not carry is the half of the card a reader most needs before
    #: spending a minute.
    path_choices: list[ResearchPathChoicePayload] = Field(default_factory=list)
    #: One sentence naming what was consulted, or that nothing spoke to it.
    knowledge_statement: str = ""
    knowledge_consulted: list[ConsultedNotePayload] = Field(default_factory=list)
    #: The opening readings, each with its reason.
    readings: list[PlannedReadingPayload] = Field(default_factory=list)
    #: Why this set and not another.
    rationale: str = ""
    #: ``model`` when the control plane chose these readings, ``revi`` when
    #: the run fell back to its standing set. A fallback presented as a
    #: choice is a small lie about how the analysis was decided.
    authored_by: Literal["model", "revi"] = "revi"
    #: How many read-and-decide rounds this question earned. Budgets scale
    #: with the question's composition depth, so a reader can see that a
    #: three-part question bought more work than a one-part one.
    rounds_planned: int = 1
    #: Set when nothing in the definitions library can speak to the
    #: question — a refusal naming the data gap, never a thin run.
    refusal: str = ""


# ---------------------------------------------------------------------------
# the generalized research report
#
# A study is not a recoverability review, and this is where that stops being
# a sentence in a design document. The review answers one standing question
# — of what is still open, how much is coming back — so its report is a
# priced headline with the populations behind it. A research question
# ("why has our A/R over 90 been climbing, and what will it take to bring it
# down") has no headline dollar figure to be the answer, and inventing one
# would be the first dishonest number in the artifact. What it has instead
# is a DETERMINATION, the readings that support it, and the record of how
# the run got from one to the other.
#
# The two shapes are additive on the wire and discriminated by
# ``ReportKindLiteral``. Nothing about the recovery shape moves.


#: What a walk step DID. Mirrors the loop's own vocabulary, closed so a
#: client can render each action in its own words rather than printing a
#: token it does not recognise.
ResearchWalkActionLiteral = Literal[
    "orient", "consult", "plan", "execute", "chase", "broaden", "drop", "refuse", "synthesize"
]


class ResearchFigurePartPayload(ClosedModel):
    """One ``breakdown = value`` pair behind a figure, in both spellings.

    The raw value is what the data holds and what a later round narrowed
    on; the label is what the reader saw. A denial reason cell reads
    ``16 — Missing or invalid information`` over a column holding ``16``,
    and a client that had only one of the two could either render a bare
    code or lose the ability to say which column it came from.
    """

    dimension: str
    dimension_label: str
    value: str
    value_label: str


class ResearchFigurePayload(ClosedModel):
    """One certified figure of one reading, with its marks already on it.

    The same two-state rule the recovery report's rate cells obey, applied
    to any governed measure: a figure is either a measurement — ``value``
    present, ``evidence`` ``measured`` — or it is not, and there is no
    third state. A ceiling carries ``bounded`` and its ``value`` is the
    largest it could have been rather than what it is; a withheld cell
    carries no value at all. ``display`` is the figure already formatted in
    its measure's own unit, so no client re-derives dollars from cents or
    a percentage from a ratio.
    """

    label: str
    parts: list[ResearchFigurePartPayload] = Field(default_factory=list)
    evidence: EvidenceTierLiteral
    #: Exact decimal text, never a float. ``None`` where nothing was
    #: published — the small-population rule withheld the row.
    value: str | None = None
    #: The figure in its own unit, or the words that stand in for one.
    display: str
    #: True when the true value was withheld and this is a ceiling.
    bounded: bool = False
    #: True when the small-population rule nulled the row outright.
    withheld: bool = False
    #: The population this figure is a rate OVER, where the measure is a
    #: ratio whose denominator counts one. Absent for an additive measure,
    #: where "the population" is not a thing the number has — and absent,
    #: with ``successes`` and ``interval``, on a bounded or withheld
    #: figure: a ceiling beside its own numerator and denominator is one
    #: division away from the value it exists to withhold, and a withheld
    #: cell's population is the small cohort the disclosure rule refused
    #: to name.
    population: int | None = None
    successes: int | None = None
    interval: IntervalPayload | None = None
    #: True when this figure's own period has NOT finished settling — the
    #: reading reaches the edge of the data, and what has settled there is
    #: not a random sample of what has not. A mark rather than a sentence,
    #: for the same reason ``bounded`` is one: a caveat that lives only in
    #: prose is dropped by every exporter and drawn by no renderer, and a
    #: point like this one grounded a published "fell to 0.0%".
    censored: bool = False


class ResearchReadingPayload(ClosedModel):
    """One reading a study took: what it read, why, and what it settled.

    The ``reason`` is the sentence that put this reading in the run — the
    planner's own, or the deterministic reader's. The ``settled`` sentence
    is what came back, composed from published figures only. A reading
    whose reason and verdict are both absent is a table with no argument
    around it, which is what a research report must never be.
    """

    #: Stable within one report, so the walk and the determination can
    #: point at a reading without repeating its title.
    id: str
    shape: ResearchShapeLiteral
    title: str
    #: The measure this reading is of, by its display name.
    measure_label: str
    #: The measure's contract id. Provenance, not copy — the same role
    #: ``FindingPayload.metric_ids`` plays.
    metric_id: str
    #: The declared unit its figures are in (``money_cents``, ``ratio``, …).
    unit: str
    reason: str
    #: What this reading settled, in one sentence over published figures.
    #: Empty where it settled nothing, which is itself a fact the walk says.
    settled: str = ""
    #: Which read-and-decide round chose it. 0 is the opening read.
    round: int = 0
    #: The reading whose result sent the run here, when one did.
    chases: str = ""
    figures: list[ResearchFigurePayload] = Field(default_factory=list)
    #: Present on a comparison over outcome-like data, with its test.
    contrast: ContrastPayload | None = None
    #: The chart drawn for this reading, when one could be drawn honestly.
    chart_id: str = ""
    #: These figures were ORDERED BY THE MEASURE — published as the
    #: ordering it is, so a renderer reads a league table down a column.
    ranked: bool = False
    #: Set when the reading could not be ordered honestly.
    ranking_refused: str = ""
    notes: list[str] = Field(default_factory=list)
    #: The coded warnings THIS reading raised, in the same
    #: ``<code>: <sentence>`` spelling the conversational surface uses —
    #: the settling verdict on its window, the date basis it had to
    #: substitute, the ceilings in its field, the ordering it refused.
    #:
    #: Published beside the figures as well as folded into the study's own
    #: warning list, because the study-level fold is the bottom of a long
    #: page: a reader looking at "0.0% in Aug 2026" needs the caveat that
    #: governs THAT number next to it, and a client that can only render
    #: one list renders it in the wrong place.
    warnings: list[str] = Field(default_factory=list)
    #: Why this reading could not be taken, when it could not.
    refusal: str = ""
    # -- provenance, complete, per reading -------------------------------
    window_label: str = ""
    #: Which date the reading was measured on, in a reader's words.
    basis_label: str = ""
    read_fingerprint: str = ""
    rows_read: int = 0
    figures_published: int = 0
    figures_withheld: int = 0
    cache_hit: bool = False
    duration_ms: int = 0


class ResearchWalkStepPayload(ClosedModel):
    """One decision the run made, with its stated reason."""

    round: int
    action: ResearchWalkActionLiteral
    subject: str
    reason: str
    detail: str = ""


class ResearchRoundPayload(ClosedModel):
    """One read-and-decide round: what it did, and why it exists."""

    index: int
    #: Why this round happened at all — the sentence that decided it. Empty
    #: on the opening round, which needs no cause beyond the question.
    reason: str = ""
    steps: list[ResearchWalkStepPayload] = Field(default_factory=list)
    #: The readings this round took, by id.
    readings: list[str] = Field(default_factory=list)


class ResearchWalkPayload(ClosedModel):
    """How the run got here — the "how I got there" a consultant shows.

    The recorded walk IS the plan (``docs/agentic-resolution.md``): what a
    permalink restores, what replay re-executes, what the harness audits.
    Publishing it is what lets a reader see that a chase was a decision
    with a cause rather than an extra table that appeared.
    """

    rounds_taken: int = 1
    #: How many rounds the question's composition depth earned.
    rounds_allowed: int = 1
    #: ``model`` when the control plane chose the readings, ``revi`` when
    #: the run fell back to its standing set.
    authored_by: Literal["model", "revi"] = "revi"
    rationale: str = ""
    rounds: list[ResearchRoundPayload] = Field(default_factory=list)
    #: ``True`` when the opening round is the plan a reader saw on the
    #: confirmation card and approved. ``False`` when the run planned its
    #: own opening, in which case ``plan_variance`` says in one sentence
    #: what that means — the same question can legitimately open on
    #: different readings, and "chosen for this question" must not be read
    #: as one deliberation per run when it means one sample per run.
    plan_confirmed: bool = False
    plan_variance: str = ""


class ResearchCensoringPayload(ClosedModel):
    """What the edge of the data cost a study's outcome-like readings.

    Published only where outcome-like data was involved — a rate whose
    denominator counts the population its numerator is drawn from. Over
    dollars or days there is no population to be censored out of, and a
    censoring block beside those figures would be a disclosure about
    nothing.

    Nothing here is modelled. Every count is read off the certified
    figures, and the statements are composed beside the counts they quote.
    """

    data_edge_date: date
    window_label: str
    #: Readings whose figures are rates over a counted population.
    readings_over_outcomes: int
    #: Figures those readings published as measurements.
    figures_measured: int
    #: …as ceilings, because the true value was withheld.
    figures_bounded: int
    #: …not at all, because the population was too small to publish.
    figures_withheld: int
    #: The size of the populations the measured figures are over, summed.
    population_measured: int
    #: The disclosure in the words a reader gets, one sentence per line.
    statements: list[str] = Field(default_factory=list)


class DeterminationPayload(ClosedModel):
    """The answer to the research question — the artifact's first claim.

    Composed under the same discipline every other answer's prose is: the
    composer is shown the question and told its first sentence must answer
    it, it may cite only certified readings, and every figure it writes is
    checked against a value some estimator actually produced. What is
    different is what else it is shown — the walk's own reasons, and the
    background notes as QUOTABLE CONTEXT. A note may inform how the answer
    is framed; it can never be a number, and the grounding validator is
    what makes that true rather than requested.
    """

    question: str
    #: The determination, disclosures first. Empty only where nothing could
    #: be composed, in which case the disclosures stand alone.
    statement: str = ""
    #: True when a composer wrote the body. False means the disclosures are
    #: the whole of it — an honest state, and a different one.
    composed: bool = False
    #: The readings this determination rests on, by id.
    rests_on: list[str] = Field(default_factory=list)


class GeneralizedResearchReport(ClosedModel):
    """A finished research study — the artifact a link points at.

    Everything a reader needs to check the determination is here: every
    reading with the reason it was taken and what it settled, every figure
    with its marks and the read behind it, the walk with its chases, what
    was established about the data before anything was chosen, and which
    background notes were consulted.
    """

    #: The discriminator. A recovery report carries no ``kind`` at all, so
    #: its bytes are unchanged; a client reads this field's presence.
    kind: Literal["generalized_research"] = "generalized_research"
    id: str
    #: The question the report answers, first sentence first.
    research_question: str
    population: DeepResearchSelector
    #: WHAT THIS RUN READ, in the words a study can use — not the
    #: recoverability review's own population noun.
    population_label: str = ""
    #: The period it read, in a reader's words.
    window_label: str = ""
    #: The load these numbers were read at, in words a reader can use.
    data_load_label: str = ""
    data_edge_date: date
    created_at: datetime
    completed_at: datetime | None = None
    duration_ms: int = 0

    determination: DeterminationPayload
    readings: list[ResearchReadingPayload] = Field(default_factory=list)
    walk: ResearchWalkPayload
    #: What was established about the data before anything was chosen.
    path_choices: list[ResearchPathChoicePayload] = Field(default_factory=list)
    #: One sentence naming what was consulted, or that nothing spoke to it.
    knowledge_statement: str = ""
    knowledge_consulted: list[ConsultedNotePayload] = Field(default_factory=list)
    #: Present when outcome-like data was involved, absent when it was not.
    censoring: ResearchCensoringPayload | None = None

    findings: list[FindingPayload] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    warnings: list[WarningPayload] = Field(default_factory=list)


class DeepResearchPreviewPayload(ClosedModel):
    """What a run WOULD do, resolved without doing any of it.

    A run is about a minute of work and a real model call, so the surface
    that offers one confirms intent first — and a confirmation is only
    worth reading if it says what will actually be looked at. This is that
    payload: the population, its size, the angles the run would take in the
    words the reader will see them in, and the other populations the same
    offer could run over.

    Nothing here is executed and nothing is stored. The one read it makes
    is the run's own denial read, through the same cache the run uses, so
    a preview followed by a run costs one read rather than two.
    """

    population: DeepResearchSelector
    scope: DeepResearchScopePayload
    plan: ResearchPlanPayload
    #: Other populations this offer could run over, as closed selectors —
    #: what the reader taps is exactly what would be posted, and no client
    #: has to parse a sentence back into a request.
    options: list[DeepResearchSelector] = Field(default_factory=list)
    #: The load every figure above was read at, in a reader's words.
    data_load_label: str = ""
    #: What a research question — as opposed to the standing recoverability
    #: review — would look at, and why. Present when the request carried a
    #: question the generalized loop can research; absent when the run is
    #: the standing recoverability review, which describes itself through
    #: ``plan`` above.
    generalized: GeneralizedResearchPreviewPayload | None = None


class StartDeepResearchRequest(ClosedModel):
    """Launch a run over a target population."""

    population: DeepResearchSelector = Field(default_factory=DeepResearchSelector)
    #: What the reader wants to know, in their own words. Left empty, the
    #: run answers the standing question: what is most likely to be
    #: recovered out of what is still open.
    question: str | None = None
    #: Attach the run to an existing conversation. Omitted, one is opened.
    session_id: str | None = None
    #: Resolve what the run WOULD do and return it, without starting
    #: anything. Answers 200 with :class:`DeepResearchPreviewPayload` on the
    #: response's ``preview`` field rather than 202 with a run.
    plan_only: bool = False
    #: The plan a reader confirmed, exactly as the card handed it back.
    #: With it, the run's opening readings ARE the ones on the card and the
    #: planner is re-entered only for the rounds beyond it — the ones
    #: nobody previewed. Without it the run plans its own opening, which is
    #: legitimate and is disclosed on the walk rather than left to read as
    #: a deliberation.
    plan_id: str | None = None


class DeepResearchProgressPayload(ClosedModel):
    """Where a run has got to."""

    phase: DeepResearchPhaseLiteral
    angle_index: int = 0
    angle_total: int = 0
    #: What the run is doing right now, in one short phrase.
    message: str = ""
    elapsed_ms: int = 0
    #: Which read-and-decide round this is, and how many the question
    #: earned. Zero on a run that takes one pass. A reader watching a
    #: minute of work is entitled to know whether the run is still on its
    #: opening read or has read something and gone after it, and "still
    #: going" cannot say which.
    round_index: int = 0
    round_total: int = 0


class DeepResearchRunResponse(ClosedModel):
    """A run: its status, how far it has got, and its report when done."""

    id: str
    session_id: str
    status: DeepResearchStatusLiteral
    created_at: datetime
    population: DeepResearchSelector
    data_load_label: str
    #: What this run is answering, from the moment it starts.
    #:
    #: Present on a run that carries a research question, empty on the
    #: standing recoverability review — which is a question nobody typed
    #: and is described by its population instead. A waiting room that had
    #: only the population to go on called a study of A/R aging "every open
    #: denial" for its whole minute, which is a population it never opens.
    research_question: str = ""
    progress: DeepResearchProgressPayload
    #: The recoverability review's report. Unchanged, byte for byte.
    report: DeepResearchReport | None = None
    #: A research study's report. At most one of these two is ever set, and
    #: ``report_kind`` says which — the run response is the discriminated
    #: payload, so a client branches on a field rather than on the shape it
    #: happens to receive.
    research_report: GeneralizedResearchReport | None = None
    report_kind: ReportKindLiteral | None = None
    #: Why a run stopped, when it did not finish.
    error: str | None = None
    #: Set only on a ``plan_only`` request: what a run WOULD do, with
    #: nothing started. ``status`` is ``preview`` and there is no run to
    #: poll or stream.
    preview: DeepResearchPreviewPayload | None = None


class DeepResearchSummary(ClosedModel):
    """One line in the list of a tenant's runs."""

    id: str
    session_id: str
    status: DeepResearchStatusLiteral
    created_at: datetime
    research_question: str
    population: DeepResearchSelector
    data_load_label: str
    total_expected_cents: int | None = None
    #: Which artifact this run produced, so a list can label a study as a
    #: study rather than showing it an empty expected-recovery column.
    report_kind: ReportKindLiteral | None = None


class DeepResearchListResponse(ClosedModel):
    runs: list[DeepResearchSummary] = Field(default_factory=list)


class DeepResearchStreamEvent(ClosedModel):
    """One frame on a run's progress stream."""

    event: DeepResearchEventKind
    data: dict[str, Any]
