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

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Protocol

from revi_calculation_contracts.contract import CountDistinct, MetricKind
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
)
from revi_investigation.application.deep_research.policy import (
    DeepResearchSettings,
    ResearchPolicy,
)
from revi_investigation.application.discovery import (
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


ProgressSink = Callable[[str, str], Awaitable[None]]


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
            population=_population_words(population),
            watermark=watermark,
            pack_snapshot_id=pack_snapshot_id,
        )
        vocabulary = _vocabulary_of(profile, self._pack)

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
            notes=self._discovery.notes,
            concepts=concepts,
            measures=measures,
            cut_for=cut_for,
            knowledge=knowledge,
            policy=policy,
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
    """The legal angle vocabulary, from the measure-availability profile."""
    measures: dict[str, frozenset[str]] = {}
    bases: dict[str, frozenset[str]] = {}
    kinds: dict[str, str] = {}
    units: dict[str, str] = {}
    rate_like: set[str] = set()
    for availability in getattr(profile, "measures", ()):
        measures[availability.metric_id] = frozenset(availability.cuts)
        bases[availability.metric_id] = frozenset(availability.date_bases)
        kinds[availability.metric_id] = availability.kind
        units[availability.metric_id] = availability.unit
        contract = pack.metric(availability.metric_id)
        if contract is None or contract.denominator is None:
            continue
        inner = getattr(contract.denominator, "inner", contract.denominator)
        if isinstance(inner, CountDistinct):
            rate_like.add(availability.metric_id)
    return AngleVocabulary(
        measures=measures,
        bases=bases,
        kinds=kinds,
        units=units,
        rate_like=frozenset(rate_like),
    )


def _population_words(population: TargetPopulation) -> str:
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
    angles: list[PlannedAngle] = []
    for position, metric_id in enumerate(orientation.measures[:3]):
        cut = orientation.cut_for.get(metric_id)
        kind = orientation.vocabulary.kinds.get(metric_id, "flow")
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
                            f"whether the {cut.replace('_', ' ')} spread on {metric_id} is a "
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
                        f"nothing separated on {', '.join(angle.cut_by) or 'the pooled reading'}"
                        f" — trying {broader.replace('_', ' ')}"
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
    ) -> tuple[ResearchWalk, tuple[MeasureResult, ...], Orientation]:
        policy = settings.research
        span = window or default_window(watermark, policy)
        budget = rounds_allowed or iteration_budget(question, policy)

        async def say(phase: str, message: str) -> None:
            if progress is not None:
                await progress(phase, message)

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

        await say("plan", "Choosing what to check")
        opening, rationale, authored_by = await self._open(orientation, budget)
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
            results.extend(batch)
            history.append(ResearchRound(index=index, results=tuple(batch)))
            index += 1
            if index >= budget:
                break
            await say("read", "Reading what came back and deciding what to chase")
            planned = list(await self._next(orientation, history, index, budget - index))
            for angle in planned:
                steps.append(
                    WalkStep(
                        round=index,
                        action="chase" if angle.chases else "broaden",
                        subject=angle.subject,
                        reason=angle.reason,
                        detail=angle.chases,
                    )
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
        )
        return walk, tuple(results), orientation

    # -- planning -----------------------------------------------------------

    async def _open(
        self, orientation: Orientation, budget: int
    ) -> tuple[tuple[PlannedAngle, ...], str, str]:
        standing = standing_angles(orientation)
        if self._planner is None:
            return standing, _standing_rationale(orientation), "revi"
        try:
            proposed, rationale = await self._planner.open(orientation, budget=budget)
        except Exception:
            return standing, _standing_rationale(orientation), "revi"
        legal = validate_angles(proposed, orientation)
        if not legal:
            return standing, _standing_rationale(orientation), "revi"
        return legal, rationale, "model"

    async def _next(
        self,
        orientation: Orientation,
        history: Sequence[ResearchRound],
        index: int,
        remaining: int,
    ) -> tuple[PlannedAngle, ...]:
        deterministic = chase_angles(orientation, history, index=index)
        if self._planner is None:
            return deterministic
        try:
            proposed = await self._planner.next_round(
                orientation, history, index=index, remaining=remaining
            )
        except Exception:
            return deterministic
        legal = validate_angles(proposed, orientation, round_index=index)
        return legal or deterministic


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


def _progress_words(angle: PlannedAngle, round_index: int, position: int, total: int) -> str:
    """What a run says about itself while it works, honestly.

    Names the round when there is more than one, because "still going" and
    "chasing what the last round found" are different states and a reader
    watching a minute-long run is entitled to know which one they are in.
    """
    lead = "Checking" if round_index == 0 else "Chasing"
    subject = angle.subject.replace("_", " ")
    tail = f" ({position} of {total})" if total > 1 else ""
    if round_index == 0:
        return f"{lead} {subject}{tail}"
    return f"{lead} what round {round_index} turned up: {subject}{tail}"


def entity_grain_of(pack: PackPort, metric_id: str) -> EntityGrain | None:
    contract = pack.metric(metric_id)
    return contract.entity_grain if contract is not None else None


def is_flow(pack: PackPort, metric_id: str) -> bool:
    contract = pack.metric(metric_id)
    return contract is not None and contract.kind is MetricKind.FLOW
