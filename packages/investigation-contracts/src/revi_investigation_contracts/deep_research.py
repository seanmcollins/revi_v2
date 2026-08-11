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
    "EvidenceTierLiteral",
    "ExpectedRecoveryRowPayload",
    "GeneralizedResearchPreviewPayload",
    "HeadlinePayload",
    "IntervalPayload",
    "MoneyIntervalPayload",
    "PlannedReadingPayload",
    "RateBasisLiteral",
    "RateCellPayload",
    "ResearchAnglePayload",
    "ResearchPathChoicePayload",
    "ResearchPlanPayload",
    "ResearchShapeLiteral",
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
DeepResearchStatusLiteral = Literal[
    "preview", "planning", "running", "complete", "failed", "interrupted"
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
    "research_progress",
    "research_finding",
    "research_warning",
    "narrative_delta",
    "error",
    "research_complete",
]

#: What each frame carries, published so a client can branch without
#: reading the server.
DEEP_RESEARCH_EVENT_PAYLOADS: dict[str, str] = {
    "research_started": "the run's id, the data load it is pinned to, and the population it targets",
    "research_plan": "the angles this run will look at, in the order it will look at them",
    "research_progress": "which angle is running, how far along, and how long it has taken",
    "research_finding": "one certified result, the moment it is measured",
    "research_warning": "a qualification a reader needs before reading the numbers",
    "narrative_delta": "one chunk of the written report as it is composed",
    "error": "the run stopped; nothing partial is published",
    "research_complete": "the finished report",
}


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

    The range is the sum of each population's own range. Populations that
    share payers, staffing and seasons move together, so it is a spread
    indication rather than a guarantee — ``range_assumes_independence``
    says so on the wire rather than in a comment.
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
    range_assumes_independence: bool = True


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
    #: What was established about the data before anything was chosen.
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
    progress: DeepResearchProgressPayload
    report: DeepResearchReport | None = None
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


class DeepResearchListResponse(ClosedModel):
    runs: list[DeepResearchSummary] = Field(default_factory=list)


class DeepResearchStreamEvent(ClosedModel):
    """One frame on a run's progress stream."""

    event: DeepResearchEventKind
    data: dict[str, Any]
