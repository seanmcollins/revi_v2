"""The generalized research loop: orient, consult, plan, execute, read, iterate.

v1 planning was one model call before one batch of angles. This is the
iterative loop the agentic-resolution addendum specifies, and the phases are
separated because they fail differently and because each one is a decision a
reader is entitled to see:

**ORIENT** runs the discovery family over the question's concepts and its
candidate populations. What comes back is not evidence — it is what evidence
could be made of here: which certified paths this warehouse populates, what
values a cut takes, which governed measures can compute over the population.
Every finding lands as one plain sentence on the walk.

**CONSULT** retrieves the pack's RCM knowledge for the question and puts it
in the planner's context as quotable prose. It changes *what deserves
checking*; it can never change what a number says.

**PLAN** chooses angles over the full catalog in the generalized grammar,
each with the reason it is there.

**EXECUTE** runs them deterministically. Recovery angles go through the M48
executors unchanged; measure angles go through the probe path.

**READ AND ITERATE** is the part that makes this a loop rather than a batch.
The planner sees its own certified results — not the prose, the results —
and may chase a separated contrast into its strata, broaden when nothing
separated, or drop an angle that published nothing. Each decision is a walk
step with its stated reason, inside a budget that scales with the question's
composition depth.

**SYNTHESIZE** hands the composer a report whose first sentence owes the
research question an answer.

Determinism is per step, not per session. Two runs of the *same recorded
walk* at the same load agree byte for byte; two cold runs of the same
question may route differently once their context differs, which is the
named trade the addendum accepts. The fingerprint attaches to the walk,
which is what provenance has always promised.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Protocol

from revi_calculation_contracts.contract import MetricKind
from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.deep_research.general import (
    AngleShape,
    AngleVocabulary,
    MeasureAngle,
    PlannedAngle,
    ResearchWalk,
    TimeStep,
    WalkStep,
    dedupe,
    normalize_measure_angle,
)
from revi_investigation.application.deep_research.grammar import TargetPopulation
from revi_investigation.application.deep_research.knowledge import (
    KnowledgeConsultation,
    consult,
)
from revi_investigation.application.deep_research.measures import (
    MeasureAngleRunner,
    MeasureCell,
    MeasureResult,
    is_proportion,
)
from revi_investigation.application.deep_research.policy import (
    DeepResearchSettings,
    ResearchPolicy,
)
from revi_investigation.application.discovery import (
    DiscoveryKind,
    DiscoveryNote,
    DiscoveryRefused,
    DiscoveryService,
)
from revi_kernel.errors import ReviError
from revi_kernel.refs import EntityGrain
from revi_kernel.scope import AbsoluteRange
from revi_kernel.watermark import DataWatermark

#: Everything this loop decides with a number — how many rounds a question
#: earns, how big a gap has to be before it is chased, how wide a breakdown
#: may be — arrives as :class:`ResearchPolicy`, loaded from governed content.
#: Nothing in this module picks one, because none of them is a fact about
#: arithmetic and a threshold chosen inside a library is a policy nobody can
#: inspect.
_WORD = re.compile(r"[a-z0-9]+")

#: Words in a research question that mean it is asking about change over
#: time, a comparison, or a cause. Each earns the run another round, because
#: each is a question one reading cannot close.
_DEPTH_WORDS = {
    "trend": ("trend", "trending", "climbing", "rising", "falling", "growing", "declining",
              "over time", "month", "months", "quarter", "worsening", "improving", "changed"),
    "compare": ("versus", "vs", "compare", "compared", "against", "worse", "better", "best",
                "worst", "gap", "differ", "different", "outlier", "outliers"),
    "cause": ("why", "cause", "causes", "driving", "drivers", "because", "reason", "reasons",
              "explain", "root"),
    "action": ("what will it take", "how do we", "fix", "reduce", "improve", "bring it down",
               "recover", "recoverable", "worth"),
}


@dataclass(frozen=True, slots=True)
class ResearchRound:
    """One round's certified output, as the planner sees it before deciding."""

    index: int
    results: tuple[MeasureResult, ...]


@dataclass(frozen=True, slots=True)
class Orientation:
    """What the run learned about its own data before planning anything."""

    question: str
    population: TargetPopulation
    window: AbsoluteRange
    vocabulary: AngleVocabulary
    notes: tuple[DiscoveryNote, ...]
    #: Concepts the question named that resolved to a governed path here.
    concepts: tuple[str, ...]
    #: Governed measures the question points at, strongest match first.
    measures: tuple[str, ...]
    #: The certified cut each measure is best broken out by here, chosen on
    #: coverage and cardinality — never on the values it would produce.
    cut_for: dict[str, str]
    knowledge: KnowledgeConsultation
    policy: ResearchPolicy
    #: Subjects the question named that nothing in this deployment carries.
    #: Empty for almost every question, and load-bearing for the ones it is
    #: not: a question about something never loaded must be told so before
    #: a minute is spent, not after.
    gaps: tuple[str, ...] = ()


class GeneralPlanner(Protocol):
    """What a control plane must provide to drive the loop.

    Two calls, both selection-only: choose the opening angles, and — having
    seen its own certified results — choose what to do next. Neither can
    compute; both are re-validated against the vocabulary before anything
    runs.
    """

    async def open(
        self, orientation: Orientation, *, budget: int
    ) -> tuple[Sequence[PlannedAngle], str]: ...

    async def next_round(
        self,
        orientation: Orientation,
        rounds: Sequence[ResearchRound],
        *,
        index: int,
        remaining: int,
    ) -> Sequence[PlannedAngle]: ...


@dataclass(frozen=True, slots=True)
class ResearchProgressUpdate:
    """Where a run has got to, as a reader waiting on it would say it.

    The round counters are on every frame and not only on the round ones,
    because "which pass is this" is the fact a reader watching a
    minute-long run most wants and the one a per-angle frame most easily
    loses: a run back on its second pass is emitting ``execute`` frames
    again, and an ``execute`` frame that did not say which round it was in
    would render as the opening read starting over.
    """

    phase: str
    message: str
    #: Which read-and-decide round this is, and how many the question
    #: earned. Zero on a run that takes one pass.
    round_index: int = 0
    round_total: int = 1
    #: Which reading of this round is running, and how many it holds.
    reading_index: int = 0
    reading_total: int = 0
    #: The reading being taken, named as the report will name it, with the
    #: reason it is in the run. Present on an ``execute`` frame and empty
    #: elsewhere. A watcher accumulates these into the list of what is
    #: being read — which is a fact from the first measurement, where a
    #: count is a fact about nothing until the report arrives.
    reading_title: str = ""
    reading_reason: str = ""
    #: Set when this reading exists to go inside an earlier finding.
    reading_chases: str = ""


ProgressSink = Callable[[ResearchProgressUpdate], Awaitable[None]]


# ---------------------------------------------------------------------------
# depth and window


def _fold(word: str) -> str:
    """A word reduced to the form two spellings of it share.

    Plurals only. "denials" and "denial" are one word to a reader and must
    be one word here; a stemmer aggressive enough to also join "aging" to
    "age" would start matching charge-capture measures to questions about
    bills, which is a worse failure than missing a match.
    """
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> frozenset[str]:
    return frozenset(_fold(word) for word in _WORD.findall(text.lower()))


#: English endings that make a word a thing rather than an action. Used for
#: one job only: deciding whether a word this deployment has never heard of
#: is a SUBJECT the question wants measured ("satisfaction") or a verb
#: connecting two subjects it already has ("affect"). Morphology, not
#: governed content — the same register as :func:`_fold`'s plural rule
#: directly above, and kept as small as that one. Erring towards silence is
#: deliberate: a missed gap degrades to today's behaviour, while a false one
#: would refuse a question this data can answer.
_SUBJECT_SUFFIXES: tuple[str, ...] = (
    "tion",
    "sion",
    "ment",
    "ness",
    "ity",
    "ance",
    "ence",
    "ship",
    "score",
    "rating",
)

#: Below this a word is too short to be carrying a subject nobody has heard
#: of; it is far more likely to be ordinary English the pack happens not to
#: use.
_MIN_SUBJECT_LENGTH = 5


def deployment_vocabulary(pack: PackPort, catalog: CatalogSnapshot) -> frozenset[str]:
    """Every word this deployment's governed content actually uses.

    Built from the pack and the catalog rather than from a list kept beside
    the code, so a tenant that loads a patient-experience feed stops being
    told it has none the moment the content lands. Nothing here is a
    judgement about English: it is the set of words the definitions library
    and the semantic layer are written in.
    """
    words: set[str] = set()
    for metric_id, description in pack.metric_summaries():
        words |= _words(metric_id.replace("_", " "))
        words |= _words(description)
    for concept_id, name in pack.concept_summaries():
        words |= _words(concept_id.replace("_", " "))
        words |= _words(name)
    for playbook_id, description in pack.playbook_summaries():
        words |= _words(playbook_id.replace("_", " "))
        words |= _words(description)
    for measure in catalog.measures:
        words |= _words(measure.id.replace("_", " "))
        for synonym in measure.synonyms:
            words |= _words(synonym)
    for dimension in catalog.dimensions:
        words |= _words(dimension.id.replace("_", " "))
        for synonym in getattr(dimension, "synonyms", ()):
            words |= _words(synonym)
    for entity in catalog.entities:
        words |= _words(entity.name.replace("_", " "))
    return frozenset(words)


def unreached_subjects(
    question: str, vocabulary: frozenset[str], pack: PackPort
) -> tuple[str, ...]:
    """Subjects this question names that the definitions library has never heard of.

    The gap this closes is a specific one: a question asking about two
    things, one of which this deployment measures and one of which it does
    not, currently reaches the planner as a question about the first. The
    model then plans confident readings of the half it can see, the reader
    confirms a card full of cheerful availability statements, and a minute
    later the determination explains that the other half was never
    answerable. The reader is owed that sentence before the minute, not
    after it.

    Deliberately conservative — see :data:`_SUBJECT_SUFFIXES`.
    """
    found: list[str] = []
    for raw in _WORD.findall(question.lower()):
        word = _fold(raw)
        if len(word) < _MIN_SUBJECT_LENGTH or word in vocabulary:
            continue
        if pack.concept_for_alias(raw) is not None or pack.concept_for_alias(word) is not None:
            continue
        if not word.endswith(_SUBJECT_SUFFIXES):
            continue
        if word not in found:
            found.append(word)
    return tuple(found)


def iteration_budget(question: str, policy: ResearchPolicy) -> int:
    """How many rounds this question's composition depth earns.

    Budgets scale with depth, not with a cap on intelligence: one reading
    answers "what is our denial rate"; "why has over-90 been climbing and
    what will it take to bring it down" is a trend, a cause and an action in
    one sentence, and closing it in one round would mean not closing it.
    """
    lowered = question.lower()
    words = _words(question)
    earned = 1
    for markers in _DEPTH_WORDS.values():
        if any(
            (marker in words) if " " not in marker else (marker in lowered)
            for marker in markers
        ):
            earned += 1
    return max(1, min(policy.max_rounds, earned))


def earned_rounds(opening: Sequence[PlannedAngle], ceiling: int) -> int:
    """How many read-and-decide passes the OPENING PLAN says this needs.

    Read off the plan rather than off the question's words. The keyword
    bags this replaces asked whether a question contained "why" or "trend"
    or "compare", which made the vaguest question the shallowest study:
    "Research denials." matched no bag and earned a single round, so the
    least-specified question got the least work, which is backwards — the
    first pass is exactly what narrows a vague question.

    A plan's SHAPE is the question's composition depth, and the planner has
    already expressed it: opening with a trend, a breakdown and a contrast
    is a statement that three things must be established before they can be
    put together. No number crosses the control-plane boundary to produce
    this — the model returns shapes and ids, and the count is arithmetic
    over them, here, in the deterministic plane.
    """
    if not opening:
        return 1
    shapes = {angle.shape for angle in opening}
    measures = {
        angle.measure.metric_id for angle in opening if angle.measure is not None
    }
    depth = max(len(shapes), 1) + (1 if len(measures) > 1 else 0)
    return max(1, min(ceiling, depth))


def default_window(watermark: DataWatermark, policy: ResearchPolicy) -> AbsoluteRange:
    """The window a research run reads when the question named no period."""
    end = watermark.newest_data_date
    year = end.year
    month = end.month - policy.window_months
    while month <= 0:
        month += 12
        year -= 1
    return AbsoluteRange(start=date(year, month, 1), end=end)


# ---------------------------------------------------------------------------
# orientation


class ResearchOrienter:
    """The ORIENT and CONSULT phases, in one place.

    Kept separate from the loop because a preview needs exactly this and
    none of the rest: the plan-only card shows what the run learned about
    the data and what it therefore intends to check, and paying for the
    execution to produce that card would make the confirmation cost what it
    exists to let the reader avoid.
    """

    def __init__(
        self,
        discovery: DiscoveryService,
        catalog: CatalogSnapshot,
        pack: PackPort,
    ) -> None:
        self._discovery = discovery
        self._catalog = catalog
        self._pack = pack

    async def orient(
        self,
        *,
        question: str,
        population: TargetPopulation,
        window: AbsoluteRange,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        policy: ResearchPolicy,
    ) -> Orientation:
        self._discovery.forget()
        self._discovery.capabilities(watermark=watermark, pack_snapshot_id=pack_snapshot_id)

        measures = self._named_measures(question)
        concepts = await self._named_concepts(
            question, window=window, watermark=watermark, pack_snapshot_id=pack_snapshot_id
        )
        profile = self._discovery.measure_availability(
            population=population_words(population),
            watermark=watermark,
            pack_snapshot_id=pack_snapshot_id,
        )
        vocabulary = _vocabulary_of(profile, self._pack)
        # What the question asked for that nothing here carries. Computed
        # before the planner sees anything, so the gap reaches the
        # confirmation card rather than the determination.
        gaps = unreached_subjects(
            question, deployment_vocabulary(self._pack, self._catalog), self._pack
        )
        measures = tuple(metric_id for metric_id in measures if metric_id in vocabulary.measures)

        cut_for: dict[str, str] = {}
        for metric_id in measures[:3]:
            cut = await self._best_cut(
                metric_id,
                vocabulary,
                policy,
                window=window,
                watermark=watermark,
                pack_snapshot_id=pack_snapshot_id,
            )
            if cut is not None:
                cut_for[metric_id] = cut

        knowledge = consult(
            self._pack,
            question=question,
            concepts=concepts,
            metric_ids=measures,
        )
        return Orientation(
            question=question,
            population=population,
            window=window,
            vocabulary=vocabulary,
            # The negative statements lead. A card that lists what this data
            # can do and truncates what it cannot is a card that answers the
            # wrong half of the reader's decision.
            notes=(*_gap_notes(gaps), *self._discovery.notes),
            concepts=concepts,
            measures=measures,
            cut_for=cut_for,
            knowledge=knowledge,
            policy=policy,
            gaps=gaps,
        )

    # -- what the question points at ---------------------------------------

    def _named_measures(self, question: str) -> tuple[str, ...]:
        """Governed measures this question points at, best match first.

        Matched on the measure's own words and on the catalog's authored
        synonyms — the analyst vocabulary the catalog exists to carry. No
        model is involved and none needs to be: "A/R over 90" is a phrase
        the semantic layer already knows, and asking a model to guess it
        would be asking it to re-derive content we ship.
        """
        asked = _words(question)
        if not asked:
            return ()
        # Concepts the question's own words resolve to through the pack's
        # governed aliases. "denials" reaches the denial concept, and a
        # measure whose id carries that concept is what the question is
        # about — content doing work that would otherwise be a synonym list
        # kept beside the code.
        concepts = frozenset(
            resolved
            for word in _WORD.findall(question.lower())
            if (resolved := self._pack.concept_for_alias(word)) is not None
        )
        scored: list[tuple[int, str]] = []
        for metric_id, description in self._pack.metric_summaries():
            terms = _words(metric_id.replace("_", " "))
            score = 3 * len(asked & terms)
            score += 2 * sum(
                1 for concept in concepts if concept in metric_id or metric_id in concept
            )
            for measure in self._catalog.measures:
                if measure.id not in _measure_fields(self._pack, metric_id):
                    continue
                for synonym in measure.synonyms:
                    synonym_words = _words(synonym)
                    if synonym_words and synonym_words <= asked:
                        score += 2 * len(synonym_words)
            # A measure whose own description names the question's words is
            # weaker evidence than its id, and is only a tiebreak.
            score += len(asked & _words(description)) // 4
            if score > 0:
                scored.append((score, metric_id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return tuple(metric_id for _, metric_id in scored[:6])

    async def _named_concepts(
        self,
        question: str,
        *,
        window: AbsoluteRange,
        watermark: DataWatermark,
        pack_snapshot_id: str,
    ) -> tuple[str, ...]:
        """Pack concepts the question names, resolved to a path in this data.

        Every concept is put through concept-to-path resolution rather than
        merely recognised, because "the question mentions COB" and "this
        warehouse can express COB" are different facts and only the second
        one can shape a plan.
        """
        found: list[str] = []
        lowered = question.lower()
        for concept_id, name in self._pack.concept_summaries():
            probe = name.lower()
            if probe and re.search(rf"(?<![a-z0-9]){re.escape(probe)}(?![a-z0-9])", lowered):
                found.append(concept_id)
        for concept_id in found[:4]:
            try:
                await self._discovery.concept_paths(
                    concept_id,
                    window=window,
                    watermark=watermark,
                    pack_snapshot_id=pack_snapshot_id,
                )
            except (DiscoveryRefused, ReviError):
                continue
        return tuple(dict.fromkeys(found))

    async def _best_cut(
        self,
        metric_id: str,
        vocabulary: AngleVocabulary,
        policy: ResearchPolicy,
        *,
        window: AbsoluteRange,
        watermark: DataWatermark,
        pack_snapshot_id: str,
    ) -> str | None:
        """The certified cut this measure is most readable by, here.

        Chosen on **coverage and readable granularity** — never on the
        values it would produce. Choosing the breakdown that looks most
        interesting is fishing, and it is fishing a report would then
        present as the analysis it happened to run.

        Granularity is scored, not maximised. A two-value cut separates
        almost nothing (institutional against professional tells a reader
        very little about why over-90 is climbing); a thirty-value cut
        spends the whole reading on cells too thin to publish. So the best
        cut is the one with the most groups that still fits on a page, and
        the ceiling is content — the catalog's own cardinality estimate.
        """
        best: tuple[Decimal, int, str] | None = None
        for cut in sorted(vocabulary.cuts_for(metric_id)):
            definition = self._catalog.dimension(cut)
            if definition is None or not definition.certified:
                continue
            if definition.phi.value != "none" or definition.cardinality_estimate > policy.max_groups_per_cut:
                continue
            try:
                census = await self._discovery.dimension_census(
                    cut,
                    window=window,
                    watermark=watermark,
                    pack_snapshot_id=pack_snapshot_id,
                )
            except (DiscoveryRefused, ReviError):
                continue
            if census.cardinality < policy.min_groups_per_cut:
                continue
            readable = census.cardinality if census.cardinality <= policy.max_groups_per_cut else 0
            candidate = (census.coverage, readable, cut)
            if best is None or candidate > best:
                best = candidate
        return best[2] if best is not None else None


def _measure_fields(pack: PackPort, metric_id: str) -> frozenset[str]:
    contract = pack.metric(metric_id)
    if contract is None:
        return frozenset()
    fields: set[str] = set()
    for expr in (contract.numerator, contract.denominator):
        inner = getattr(expr, "inner", expr)
        field_ref = getattr(inner, "field", None)
        if field_ref is not None:
            fields.add(field_ref.id)
    return frozenset(fields)


def _vocabulary_of(profile: object, pack: PackPort) -> AngleVocabulary:
    """The legal angle vocabulary, from the measure-availability profile.

    **Availability is a filter here, not a field carried along.** The
    profile already decides which measures this deployment can compute over
    this population; copying the unavailable ones into the vocabulary put
    them in front of the planner with nothing marking them, so a refusal
    that should have been structural became an instruction in a prompt. The
    vocabulary's own docstring promised this filter; now it performs it.
    """
    measures: dict[str, frozenset[str]] = {}
    bases: dict[str, frozenset[str]] = {}
    kinds: dict[str, str] = {}
    units: dict[str, str] = {}
    rate_like: set[str] = set()
    descriptions = dict(pack.metric_summaries())
    for availability in getattr(profile, "measures", ()):
        if not getattr(availability, "available", True):
            continue
        measures[availability.metric_id] = frozenset(availability.cuts)
        bases[availability.metric_id] = frozenset(availability.date_bases)
        kinds[availability.metric_id] = availability.kind
        units[availability.metric_id] = availability.unit
        contract = pack.metric(availability.metric_id)
        # RATE-LIKE MEANS PROPORTION, and the test lives in one place. A
        # denominator-only rule called ``bill_lag_days`` a rate — a mean of
        # days over a count of claims — which would have offered the
        # planner a stratified-rate reading and a two-proportion test over
        # an average.
        if contract is not None and is_proportion(contract):
            rate_like.add(availability.metric_id)
    return AngleVocabulary(
        measures=measures,
        bases=bases,
        kinds=kinds,
        units=units,
        rate_like=frozenset(rate_like),
        descriptions={
            metric_id: description
            for metric_id, description in descriptions.items()
            if metric_id in measures
        },
    )


def gap_statement(gaps: Sequence[str]) -> str:
    """The gap, in one sentence, in the reader's words."""
    named = list(gaps)
    if len(named) == 1:
        subject = named[0]
    else:
        subject = f"{', '.join(named[:-1])} or {named[-1]}"
    return (
        f"Nothing in your definitions library measures {subject}, so no reading here "
        f"can speak to that part of the question. Answering it would mean loading a "
        f"feed that carries it."
    )


def _gap_notes(gaps: Sequence[str]) -> tuple[DiscoveryNote, ...]:
    if not gaps:
        return ()
    return (
        DiscoveryNote(
            kind=DiscoveryKind.CAPABILITIES,
            subject=gaps[0],
            statement=gap_statement(gaps),
            request_key="research_gap:" + ",".join(gaps),
        ),
    )


def population_words(population: TargetPopulation) -> str:
    """What this run is about, in words a generalized report can use.

    The recovery mode's own label says "every open denial", which is right
    there and wrong here: a research run over A/R aging is not about
    denials, and a preview card that said so would be describing a
    population the run never read.
    """
    from revi_investigation.application.deep_research import copy as words

    if population.dimension is None:
        return "everything in your data"
    return words.population_label(str(population.kind), population.values)


# ---------------------------------------------------------------------------
# the standing generalized plan


def standing_angles(orientation: Orientation) -> tuple[PlannedAngle, ...]:
    """The angles a run looks at when nobody chose for it.

    Not a fallback in the apologetic sense: this is a complete opening read
    of any measure the question points at — what it is, how it moves, and
    where it differs — and it is the plan a run publishes when the control
    plane has nothing to give, with the report saying which of the two
    chose it.
    """
    from revi_investigation.application.rendering import metric_label

    angles: list[PlannedAngle] = []
    for position, metric_id in enumerate(orientation.measures[:3]):
        cut = orientation.cut_for.get(metric_id)
        kind = orientation.vocabulary.kinds.get(metric_id, "flow")
        measure = metric_label(metric_id)
        reason_lead = (
            "the measure the question names"
            if position == 0
            else "a measure the question's words reach"
        )
        if kind == "flow":
            angles.append(
                PlannedAngle(
                    shape=AngleShape.TREND,
                    reason=f"{reason_lead} — how it has moved over the window asked about",
                    measure=MeasureAngle(metric_id=metric_id, step=TimeStep.MONTH),
                )
            )
        if cut is not None:
            angles.append(
                PlannedAngle(
                    shape=(
                        AngleShape.STRATIFIED_RATES
                        if metric_id in orientation.vocabulary.rate_like
                        else AngleShape.MEASURE_PROFILE
                    ),
                    reason=f"{reason_lead} — where it sits, broken out by {cut.replace('_', ' ')}",
                    measure=MeasureAngle(metric_id=metric_id, cut_by=(cut,)),
                )
            )
            if metric_id in orientation.vocabulary.rate_like:
                angles.append(
                    PlannedAngle(
                        shape=AngleShape.CONTRAST,
                        reason=(
                            f"whether the {cut.replace('_', ' ')} spread on {measure} is a "
                            "real gap or the size of the groups behind it"
                        ),
                        measure=MeasureAngle(metric_id=metric_id, cut_by=(cut,)),
                    )
                )
    return dedupe(angles)


# ---------------------------------------------------------------------------
# reading the results


def _separated(result: MeasureResult, policy: ResearchPolicy) -> bool:
    """Did this reading find a difference worth going inside?

    Two answers, because there are two kinds of measure. Over a rate the
    question is statistical and the estimator already answered it: a gap
    whose range still contains "no difference" is not a lead, whatever its
    point value. Over dollars or days there is no population to test, so
    the lead is the SPREAD itself — the widest group against the narrowest,
    against a ratio the content sets. Publishing a p-value about dollars
    would read as rigour and be arithmetic on the wrong object.
    """
    contrast = result.contrast
    if contrast is not None and not contrast.is_refused and contrast.p_value is not None:
        interval = contrast.risk_difference_interval
        return (
            contrast.p_value < policy.chase_p_value
            and interval is not None
            and interval.excludes_zero
        )
    return _spread(result, policy) is not None


def _spread(result: MeasureResult, policy: ResearchPolicy) -> tuple[MeasureCell, Decimal] | None:
    """The widest group and how many times the narrowest it is, or nothing.

    Read off published, certified cells only. A bounded cell is a ceiling,
    not a value, and letting one be the widest group would chase a number
    the report itself declined to publish.
    """
    if result.angle.shape is AngleShape.TREND:
        return None
    measured = [
        cell for cell in result.cells if cell.is_measured and cell.value is not None
    ]
    if len(measured) < policy.min_groups_for_spread:
        return None
    ordered = sorted(measured, key=lambda cell: (cell.value or Decimal(0), cell.label))
    low, high = ordered[0], ordered[-1]
    if low.value is None or high.value is None or low.value <= 0:
        return None
    ratio = (high.value / low.value).quantize(Decimal("0.01"))
    if ratio < policy.chase_spread_ratio:
        return None
    return high, ratio


@dataclass(frozen=True, slots=True)
class Lead:
    """A finding the research thresholds admit as worth going inside.

    The policy's own verdict, made into an object because two callers need
    it and they must not disagree. The deterministic planner turns leads
    into chases directly; the model planner is SHOWN them, proposes chases
    in its own words, and is then held to this same set — a chase into a
    population the thresholds never admitted is dropped, whatever reason
    was written for it. The model decides what is interesting; the content
    decides what is significant.
    """

    #: The reading this came off, by the title a reader saw.
    title: str
    #: The breakdown the lead sits on, and the value AS THE DATA HOLDS IT —
    #: a chase filters the column, and the column holds ``16`` where the
    #: reader saw ``16 — Missing or invalid information``.
    dimension: str
    value: str
    #: The same population in the words a reader saw.
    shown: str
    #: The clause that says why this is a lead, quoted by the walk step,
    #: the progress line and the planner prompt alike, so one decision is
    #: described once.
    why: str


def leads_of(rounds: Sequence[ResearchRound], policy: ResearchPolicy) -> tuple[Lead, ...]:
    """Everything in the newest round the thresholds say is worth chasing."""
    if not rounds:
        return ()
    found: list[Lead] = []
    for result in rounds[-1].results:
        angle = result.angle.measure
        if angle is None or result.refusal or not angle.cut_by:
            continue
        lead = _lead(result, policy)
        if lead is None:
            continue
        raw, shown, why = lead
        found.append(
            Lead(
                title=result.title,
                dimension=angle.cut_by[0],
                value=raw,
                shown=shown,
                why=why,
            )
        )
    return tuple(found)


def gate_sentence(policy: ResearchPolicy) -> str:
    """The chase levels, stated as the rule they are.

    Said whenever a proposal is dropped for going inside a difference the
    thresholds did not admit. It names the value, the unit, who recommends
    it and that it can be moved — never "below the threshold", which asks a
    reader to accept a number nobody showed them
    (``docs/client-language.md`` §2.1).
    """
    return (
        "A difference is worth going inside when a rate gap is beyond what the size of "
        f"the groups explains at the {policy.chase_p_value} level, or when a dollars or "
        f"days breakdown has a widest group at least {policy.chase_spread_ratio} times its "
        "narrowest — Revi's recommended levels for chasing a difference. You can change "
        "this anytime."
    )


def gate_chases(
    proposed: Sequence[PlannedAngle],
    leads: Sequence[Lead],
    policy: ResearchPolicy,
    *,
    round_index: int,
) -> tuple[tuple[PlannedAngle, ...], tuple[WalkStep, ...]]:
    """Hold proposed chases to the thresholds, and record what was dropped.

    A reading that narrows to a population — ``within`` is set — is a claim
    that the last round found something there. The content decides whether
    it did. A proposal narrowing into a population no reading separated is
    dropped rather than trimmed into a pooled reading: run as a broadening
    it would answer a question nobody asked and carry a reason about a
    finding that does not exist.

    Broadenings pass through. They assert nothing about the last round, so
    there is nothing for a threshold to hold them to.
    """
    admitted = {(lead.dimension, lead.value) for lead in leads}
    kept: list[PlannedAngle] = []
    dropped: list[WalkStep] = []
    for angle in proposed:
        measure = angle.measure
        if measure is None or not measure.within:
            kept.append(angle)
            continue
        outside = [pair for pair in measure.within if pair not in admitted]
        if not outside:
            kept.append(angle)
            continue
        dropped.append(
            WalkStep(
                round=round_index,
                action="drop",
                subject=angle.subject,
                reason=(
                    "I did not go inside "
                    + ", ".join(value for _, value in outside)
                    + " — nothing in the last reading separated it. "
                    + gate_sentence(policy)
                ),
                detail=angle.reason,
            )
        )
    return tuple(kept), tuple(dropped)


def chase_angles(
    orientation: Orientation, rounds: Sequence[ResearchRound], *, index: int
) -> tuple[PlannedAngle, ...]:
    """What to run next, read off this run's own certified results.

    Three moves, in the order a analyst would make them:

    * **Chase.** A contrast that separated names a population worth going
      inside. The next angle re-reads the same measure *within* the arm
      that won, broken out by a different certified cut — the payer
      contrast was decisive, so cut inside that payer next.
    * **Broaden.** Nothing separated, so the reading was not wrong but was
      not the cut that matters. Take the next certified cut the measure
      declares.
    * **Drop.** An angle that published no measured cell is not repeated
      and is recorded as dropped with the reason, so a reader sees that it
      was tried.
    """
    if not rounds:
        return ()
    latest = rounds[-1]
    seen_cuts = {
        cut
        for round_ in rounds
        for result in round_.results
        if result.angle.measure is not None
        for cut in result.angle.measure.cut_by
    }
    proposals: list[PlannedAngle] = []
    for result in latest.results:
        angle = result.angle.measure
        if angle is None or result.refusal:
            continue
        lead = _lead(result, orientation.policy)
        if lead is not None:
            raw, shown, why = lead
            cut = angle.cut_by[0] if angle.cut_by else ""
            inner = _next_cut(orientation, angle.metric_id, seen_cuts | {cut})
            if inner and cut:
                proposals.append(
                    PlannedAngle(
                        shape=AngleShape.MEASURE_PROFILE,
                        round=index,
                        chases=result.title,
                        reason=(
                            f"{why} — cutting inside {shown} by "
                            f"{inner.replace('_', ' ')} next"
                        ),
                        measure=MeasureAngle(
                            metric_id=angle.metric_id,
                            cut_by=(inner,),
                            within=((cut, raw),),
                        ),
                    )
                )
                continue
        if result.cells_published == 0:
            continue
        if angle.metric_id != (orientation.measures[0] if orientation.measures else ""):
            # Broadening every measure every round turns a research run into
            # a cross-product of the catalog. The question named one measure
            # first; the others are context, and context does not get its own
            # investigation.
            continue
        broader = _next_cut(orientation, angle.metric_id, seen_cuts)
        if broader and result.angle.shape is not AngleShape.TREND:
            proposals.append(
                PlannedAngle(
                    shape=(
                        AngleShape.STRATIFIED_RATES
                        if angle.metric_id in orientation.vocabulary.rate_like
                        else AngleShape.MEASURE_PROFILE
                    ),
                    round=index,
                    reason=(
                        "nothing separated on "
                        + (
                            ", ".join(cut.replace("_", " ") for cut in angle.cut_by)
                            or "the pooled reading"
                        )
                        + f" — trying {broader.replace('_', ' ')}"
                    ),
                    measure=MeasureAngle(metric_id=angle.metric_id, cut_by=(broader,)),
                )
            )
    return dedupe(proposals)[:3]


def _lead(
    result: MeasureResult, policy: ResearchPolicy
) -> tuple[str, str, str] | None:
    """The population this reading points at, and why — or nothing.

    Returns ``(the raw value to narrow on, what a reader is shown, the
    clause that says why)``. The first two are different strings on purpose:
    a code cell reads ``16 — Missing or invalid information`` and the column
    holds ``16``, so a chase built from the label narrows on a value the
    data does not have and fails at the source. The walk step and the
    progress line quote the same clause, so one decision is described once.
    """
    contrast = result.contrast
    cuts = result.angle.measure.cut_by if result.angle.measure else ()
    cut = ", ".join(part.replace("_", " ") for part in cuts)
    axis = cuts[0] if cuts else ""
    if contrast is not None and not contrast.is_refused and contrast.p_value is not None:
        if not _separated(result, policy):
            return None
        arm = _raw_of(result, contrast.left.label, axis)
        if arm is None:
            return None
        return arm, contrast.left.label, f"the {cut} gap was decisive"
    spread = _spread(result, policy)
    if spread is None:
        return None
    cell, ratio = spread
    raw = dict(cell.parts).get(axis)
    if not raw:
        return None
    return (
        raw,
        cell.label,
        f"the {cut} spread was wide — {cell.label} runs {ratio}x the narrowest group",
    )


def _raw_of(result: MeasureResult, label: str, axis: str) -> str | None:
    """The value the data holds for the cell a reader saw named ``label``."""
    for cell in result.cells:
        if cell.label == label:
            return dict(cell.parts).get(axis)
    return None


def _next_cut(orientation: Orientation, metric_id: str, used: set[str]) -> str:
    """The next certified cut this measure declares, deterministically.

    Alphabetical among what is left, so two runs of the same walk broaden
    the same way. The alternative — "the most interesting remaining cut" —
    would require reading the outcome to choose the analysis.
    """
    declared = orientation.vocabulary.cuts_for(metric_id)
    preferred = orientation.cut_for.get(metric_id)
    candidates = sorted(cut for cut in declared if cut not in used and cut != preferred)
    return candidates[0] if candidates else ""


def drop_steps(rounds: Sequence[ResearchRound]) -> tuple[WalkStep, ...]:
    """A walk step for every angle that ran and published nothing."""
    steps: list[WalkStep] = []
    for round_ in rounds:
        for result in round_.results:
            if result.refusal:
                steps.append(
                    WalkStep(
                        round=round_.index,
                        action="refuse",
                        subject=result.title,
                        reason=result.refusal,
                    )
                )
            elif result.cells_published == 0:
                steps.append(
                    WalkStep(
                        round=round_.index,
                        action="drop",
                        subject=result.title,
                        reason=(
                            "every group this reading produced was too small to publish, so "
                            "nothing here speaks for it"
                        ),
                    )
                )
    return tuple(steps)


# ---------------------------------------------------------------------------
# the loop


@dataclass(frozen=True, slots=True)
class ResearchPreview:
    """What a run WOULD look at, resolved without looking at any of it.

    A research run is a minute of work and a real model call, so the
    surface offering one confirms intent first — and a confirmation is only
    worth reading if it says what will actually be checked and why. That is
    ORIENT, CONSULT and PLAN — the three phases that cost cached reads and
    one model call — and none of EXECUTE, which is the minute. The
    confirmation is not free, and it is not meant to be: what it buys is a
    reader seeing the reasoning before the work rather than after it.

    The reader can correct it. Everything here is a decision — the period,
    the population, which measures were reached, which readings follow —
    and a preview that could not be argued with would be a progress bar
    shown in advance.
    """

    orientation: Orientation
    angles: tuple[PlannedAngle, ...]
    rationale: str
    #: ``model`` when the control plane chose; ``revi`` when the standing
    #: set did. Carried through to the card, because a fallback presented
    #: as a choice is a small lie about how the analysis was decided.
    authored_by: str
    #: Rounds this question's composition depth earned.
    budget: int

    @property
    def refusal(self) -> str:
        """Why there is nothing to run, when there is nothing to run.

        A statement about the data, never about the engine — the first of
        the two honest non-answers the completeness bar allows.

        A question that reaches no measure this deployment can compute is
        refused here, at the card, before the minute — and the refusal
        names what the question wanted rather than saying the engine is
        unable. That is the whole difference between a gate and an
        instruction in a prompt: the model never had the chance to plan
        confident readings of the half it could see.
        """
        if not self.angles or not self.orientation.vocabulary.measures:
            if self.orientation.gaps:
                return gap_statement(self.orientation.gaps)
            if not self.orientation.measures:
                return (
                    "Nothing in your definitions library matches the words in this "
                    "question, so there is no standard measure to research it through."
                )
            return (
                "The measures this question reaches cannot be read over this population, "
                "so there is no reading to take."
            )
        return ""

    @property
    def gap_note(self) -> str:
        """What the question asked for that this data does not carry.

        Distinct from :attr:`refusal`: a question with one answerable half
        and one unanswerable one still runs, and the reader is entitled to
        know which half they are buying BEFORE they buy it.
        """
        return gap_statement(self.orientation.gaps) if self.orientation.gaps else ""


class GeneralizedResearchLoop:
    """One research run, from a free-text question to a recorded walk."""

    def __init__(
        self,
        orienter: ResearchOrienter,
        runner: MeasureAngleRunner,
        *,
        planner: GeneralPlanner | None = None,
    ) -> None:
        self._orienter = orienter
        self._runner = runner
        self._planner = planner

    async def preview(
        self,
        *,
        question: str,
        population: TargetPopulation,
        settings: DeepResearchSettings,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        window: AbsoluteRange | None = None,
    ) -> ResearchPreview:
        """Orient, consult and plan. Read nothing.

        The same three phases :meth:`run` opens with, called by the surface
        that offers the run, so the card a reader confirms describes the
        run they are about to get rather than a generic description of the
        mode. The orientation reads go through the run's own cache, so a
        preview followed by a run costs one read rather than two.
        """
        policy = settings.research
        span = window or default_window(watermark, policy)
        budget = iteration_budget(question, policy)
        orientation = await self._orienter.orient(
            question=question,
            population=population,
            window=span,
            watermark=watermark,
            pack_snapshot_id=pack_snapshot_id,
            policy=policy,
        )
        angles, rationale, authored_by, earned = await self._open(orientation, budget)
        return ResearchPreview(
            orientation=orientation,
            angles=tuple(angles),
            rationale=rationale,
            authored_by=authored_by,
            budget=earned,
        )

    async def run(
        self,
        *,
        question: str,
        population: TargetPopulation,
        settings: DeepResearchSettings,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        window: AbsoluteRange | None = None,
        progress: ProgressSink | None = None,
        rounds_allowed: int | None = None,
        confirmed: ResearchPreview | None = None,
    ) -> tuple[ResearchWalk, tuple[MeasureResult, ...], Orientation]:
        """Execute a research run.

        ``confirmed`` is the plan the reader approved. When it is supplied
        the opening round IS that plan — the same angles, in the same
        order, with the same reasons and the same round budget — and the
        planner is re-entered only for the iteration rounds beyond it,
        which nobody previewed and nobody could have. Without it the
        opening round is planned afresh, and two runs of one question can
        legitimately open on different readings; the report says so rather
        than presenting a resample as a deliberation.
        """
        policy = settings.research
        span = window or default_window(watermark, policy)
        budget = (
            rounds_allowed
            or (confirmed.budget if confirmed is not None else 0)
            or iteration_budget(question, policy)
        )

        async def say(
            phase: str,
            message: str,
            *,
            round_index: int = 0,
            reading_index: int = 0,
            reading_total: int = 0,
            reading: PlannedAngle | None = None,
        ) -> None:
            if progress is not None:
                await progress(
                    ResearchProgressUpdate(
                        phase=phase,
                        message=message,
                        round_index=round_index,
                        round_total=budget,
                        reading_index=reading_index,
                        reading_total=reading_total,
                        reading_title=(
                            "" if reading is None else self._runner.title(reading)
                        ),
                        reading_reason="" if reading is None else reading.reason,
                        reading_chases="" if reading is None else reading.chases,
                    )
                )

        await say("orient", "Checking what your data can answer")
        orientation = await self._orienter.orient(
            question=question,
            population=population,
            window=span,
            watermark=watermark,
            pack_snapshot_id=pack_snapshot_id,
            policy=policy,
        )
        steps: list[WalkStep] = [
            WalkStep(round=0, action="orient", subject=note.subject, reason=note.statement)
            for note in orientation.notes
        ]

        await say("consult", "Reading the background notes that bear on this")
        steps.append(
            WalkStep(
                round=0,
                action="consult",
                subject="background notes",
                reason=orientation.knowledge.statement,
                detail=", ".join(entry.id for entry in orientation.knowledge.entries),
            )
        )

        if confirmed is not None:
            await say("plan", "Taking the readings you confirmed")
            opening: Sequence[PlannedAngle] = confirmed.angles
            rationale = confirmed.rationale
            authored_by = confirmed.authored_by
        else:
            await say("plan", "Choosing what to check")
            opening, rationale, authored_by, budget = await self._open(orientation, budget)
        planned = list(opening)
        for angle in planned:
            steps.append(
                WalkStep(round=0, action="plan", subject=angle.subject, reason=angle.reason)
            )

        results: list[MeasureResult] = []
        history: list[ResearchRound] = []
        estimation = settings.estimation_policy()
        index = 0
        while planned and index < budget:
            batch: list[MeasureResult] = []
            for position, angle in enumerate(planned, start=1):
                await say(
                    "execute",
                    _progress_words(angle, index, position, len(planned)),
                    round_index=index,
                    reading_index=position,
                    reading_total=len(planned),
                    reading=angle,
                )
                batch.append(
                    await self._runner.run(
                        angle,
                        population=population,
                        window=span,
                        as_of=watermark.newest_data_date,
                        watermark=watermark,
                        pack_snapshot_id=pack_snapshot_id,
                        policy=estimation,
                    )
                )
                # A yield between readings, so a cancelled run stops on a
                # reading boundary rather than mid-estimate — the same
                # boundary the recovery run stops on, for the same reason:
                # nothing partial is ever persisted, and a long run must
                # not starve the event loop it streams on.
                await asyncio.sleep(0)
            results.extend(batch)
            history.append(ResearchRound(index=index, results=tuple(batch)))
            index += 1
            if index >= budget:
                break
            await say(
                "read",
                "Reading what came back and deciding what to chase",
                round_index=index - 1,
            )
            chosen, gated = await self._next(orientation, history, index, budget - index)
            planned = list(chosen)
            steps.extend(gated)
            for angle in planned:
                steps.append(
                    WalkStep(
                        round=index,
                        # THE GATE DECIDES THIS, not the model. A chase is a
                        # reading narrowed into a population the gate
                        # admitted as a lead — anything else was dropped
                        # before it got here. Branching on the model's own
                        # ``chases`` sentence let a plain new cut be
                        # published as "Went after", which is a claim about
                        # causation the run never made.
                        action=_walk_action(angle),
                        subject=angle.subject,
                        reason=angle.reason,
                        detail=angle.chases,
                    )
                )
            if planned:
                # The reader is watching a minute-long run and is entitled to
                # know which state it is in. "Still going" and "the payer
                # spread was decisive — cutting inside Veritas Comp Fund
                # next" are different states, and only the second one says
                # that the run READ something and DECIDED.
                await say("round", round_words(index, planned), round_index=index)

        await say(
            "synthesize",
            "Writing the answer the question asked for",
            round_index=max(index - 1, 0),
        )
        steps.extend(drop_steps(history))
        steps.append(
            WalkStep(
                round=index,
                action="synthesize",
                subject=question,
                reason=(
                    f"{len(results)} readings over {index} "
                    + ("round" if index == 1 else "rounds")
                    + " — writing the answer the question asked for"
                ),
            )
        )
        walk = ResearchWalk(
            question=question,
            population=population,
            angles=tuple(result.angle for result in results),
            steps=tuple(steps),
            authored_by=authored_by,
            rationale=rationale,
            rounds=index,
            budget=budget,
            plan_confirmed=confirmed is not None,
        )
        return walk, tuple(results), orientation

    # -- planning -----------------------------------------------------------

    async def _open(
        self, orientation: Orientation, budget: int
    ) -> tuple[tuple[PlannedAngle, ...], str, str, int]:
        """The opening readings, who chose them, and how deep to go.

        The depth comes back WITH the plan because it is the same
        judgement: a planner that has just decided a question needs a
        trend, a breakdown and a contrast has already decided it will not
        close in one pass. Reading depth off keyword bags instead made the
        vaguest question the shallowest study — "Research denials." matched
        no bag and earned one round — which is exactly backwards.
        """
        standing = standing_angles(orientation)
        if self._planner is None:
            return standing, _standing_rationale(orientation), "revi", earned_rounds(standing, budget)
        try:
            proposed, rationale = await self._planner.open(orientation, budget=budget)
        except Exception:
            return standing, _standing_rationale(orientation), "revi", earned_rounds(standing, budget)
        legal = validate_angles(proposed, orientation)
        if not legal:
            return standing, _standing_rationale(orientation), "revi", earned_rounds(standing, budget)
        return legal, rationale, "model", earned_rounds(legal, budget)

    async def _next(
        self,
        orientation: Orientation,
        history: Sequence[ResearchRound],
        index: int,
        remaining: int,
    ) -> tuple[tuple[PlannedAngle, ...], tuple[WalkStep, ...]]:
        """What runs next, and the record of what was proposed and refused.

        The order is the whole design. The control plane proposes; the
        grammar decides what is *legal*; the research thresholds decide what
        is *significant*; and only what survives both runs. A proposal
        dropped at either gate is recorded rather than discarded, because a
        run that quietly declined to chase something looks identical to a
        run that never thought of it.
        """
        deterministic = chase_angles(orientation, history, index=index)
        if self._planner is None:
            return deterministic, ()
        try:
            proposed = await self._planner.next_round(
                orientation, history, index=index, remaining=remaining
            )
        except Exception:
            return deterministic, ()
        legal = validate_angles(proposed, orientation, round_index=index)
        if not legal:
            return deterministic, ()
        kept, dropped = gate_chases(
            legal,
            leads_of(history, orientation.policy),
            orientation.policy,
            round_index=index,
        )
        return (kept or deterministic), dropped

    # -- replay -------------------------------------------------------------

    async def replay(
        self,
        walk: ResearchWalk,
        *,
        settings: DeepResearchSettings,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        window: AbsoluteRange,
    ) -> tuple[MeasureResult, ...]:
        """Re-execute a recorded walk. No orientation, no model, no choices.

        "The recorded path is the plan" (addendum). This is the method that
        makes the sentence true: a permalink, a replay and the warehouse-diff
        harness all re-run *what was decided*, and none of them may re-decide
        it. Two runs of one walk at one load therefore publish byte-identical
        numbers whether or not a control plane was ever reachable — the
        reproducibility claim attaches to the walk, which is what provenance
        has always promised.
        """
        estimation = settings.estimation_policy()
        results: list[MeasureResult] = []
        for angle in walk.angles:
            if angle.measure is None:
                continue
            results.append(
                await self._runner.run(
                    angle,
                    population=walk.population,
                    window=window,
                    as_of=watermark.newest_data_date,
                    watermark=watermark,
                    pack_snapshot_id=pack_snapshot_id,
                    policy=estimation,
                )
            )
        return tuple(results)


def validate_angles(
    proposed: Sequence[PlannedAngle], orientation: Orientation, *, round_index: int = 0
) -> tuple[PlannedAngle, ...]:
    """Re-validate everything a control plane proposed, before anything runs.

    A model cannot invent an analysis: the shape is an enumeration, the
    measure must be one this deployment carries, and the cuts must be ones
    the measure's own contract declares. What survives is legal by
    construction; what does not is dropped rather than weakened.
    """
    kept: list[PlannedAngle] = []
    for angle in proposed:
        if angle.recovery is not None:
            kept.append(replace(angle, round=angle.round or round_index))
            continue
        if angle.measure is None:
            continue
        normalized, _ = normalize_measure_angle(
            angle.measure, angle.shape, orientation.vocabulary
        )
        if normalized is None:
            continue
        kept.append(
            replace(angle, measure=normalized, round=angle.round or round_index)
        )
    return dedupe(kept)


def _standing_rationale(orientation: Orientation) -> str:
    if not orientation.measures:
        return (
            "Nothing in your definitions library matches the words in this question, so "
            "there is no standard measure to read it through."
        )
    from revi_investigation.application.rendering import metric_label

    named = ", ".join(metric_label(metric_id) for metric_id in orientation.measures[:3])
    return (
        f"Revi's opening read of {named}: what it is, how it has moved, and where it "
        "differs — then whatever those readings point at."
    )


def round_words(index: int, planned: Sequence[PlannedAngle]) -> str:
    """What a new round is FOR, in the sentence that decided it.

    The reason a round exists was already written once — by the planner
    that chose the angle, or by the deterministic reader that found the
    lead. Quoting it is what makes the progress stream a record of
    reasoning rather than a spinner with words on it, and re-wording it
    here would put a second description of one decision on the wire.
    """
    lead = planned[0]
    verb = "chasing it" if _walk_action(lead) == "chase" else "trying another angle"
    return f"Round {index} — {verb}: {lead.reason}"


def _walk_action(angle: PlannedAngle) -> str:
    """Whether this reading is a chase or a broaden, decided by the gate.

    A chase is a reading NARROWED into a population the gate admitted as a
    lead. The gate is what admits it — ``gate_chases`` drops every
    narrowing into a population no lead named — so a surviving narrowing is
    a chase by construction and a reading with nothing to narrow into is
    not, whatever sentence the control plane wrote about it.
    """
    measure = angle.measure
    return "chase" if measure is not None and measure.within else "broaden"


def _progress_words(angle: PlannedAngle, round_index: int, position: int, total: int) -> str:
    """What a run says about itself while it works, honestly.

    Names the round when there is more than one, and names it CORRECTLY:
    the progress line follows the same gate decision the walk records, so a
    round that broadened is not announced as a round that chased. The two
    surfaces describing one decision differently is how a reader concludes
    the walk is decoration.
    """
    subject = angle.subject.replace("_", " ")
    tail = f" ({position} of {total})" if total > 1 else ""
    if round_index == 0:
        return f"Checking {subject}{tail}"
    if _walk_action(angle) == "chase":
        return f"Chasing what round {round_index} turned up: {subject}{tail}"
    return f"Widening after round {round_index}: {subject}{tail}"


def entity_grain_of(pack: PackPort, metric_id: str) -> EntityGrain | None:
    contract = pack.metric(metric_id)
    return contract.entity_grain if contract is not None else None


def is_flow(pack: PackPort, metric_id: str) -> bool:
    contract = pack.metric(metric_id)
    return contract is not None and contract.kind is MetricKind.FLOW
