"""Turning a recorded walk into the artifact a research link points at.

The recovery report answers one standing question, so its shape is a priced
headline with the populations behind it. A research question has no such
headline — "why has A/R over 90 been climbing and what will it take to
bring it down" is not answered by a dollar figure, and inventing one would
be the first dishonest number in the document. So this builds a different
artifact, from the same discipline:

**A DETERMINATION, not a total.** The answer to the question, composed one
layer up under the grounding validator, standing where the recovery
report's headline stands. What this module produces is everything that
determination is allowed to be made of.

**A READING IS A FIGURE WITH AN ARGUMENT AROUND IT.** Every reading carries
the reason it was taken (the planner's own sentence, or the deterministic
reader's) and what it settled — one sentence composed *from published
figures only*. A research report that published tables without saying why
each was read would be a data dump wearing a report's clothes.

**THE WALK IS PUBLISHED.** "The recorded path is the plan"
(``docs/agentic-resolution.md``): the rounds, their chases and the reasons
for them are the "how I got here" a consultant shows, and a chase that
appeared without a cause is indistinguishable from an extra table.

Three rules it enforces on the way out, the same three the recovery report
enforces because there is one honesty machine and not two.

**A refusal is published, never dropped.** A reading that could not be
taken keeps its title and its reason and is named in the warnings.

**A bound is a mark, never a number.** A ceiling is published as a ceiling,
is never counted as a measurement, and never enters a superlative. Where
too much of a reading's field is bounded, no ordering is published at all.

**A chart never draws a claim it cannot support.** A line needs three
ordered points; below that it is a bar. One mark is a figure, not a chart.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.deep_research import copy as words
from revi_investigation.application.deep_research.general import (
    AngleShape,
    PlannedAngle,
    ResearchWalk,
    WalkStep,
)
from revi_investigation.application.deep_research.loop import Orientation
from revi_investigation.application.deep_research.measures import (
    MeasureCell,
    MeasureResult,
)
from revi_investigation.application.deep_research.policy import DeepResearchSettings
from revi_investigation.application.rendering import date_phrase, format_value, metric_label
from revi_investigation_contracts.api import (
    ChartRow,
    ChartSort,
    ChartSpec,
    ChartType,
    FindingPayload,
    FindingValue,
)
from revi_investigation_contracts.deep_research import (
    ConsultedNotePayload,
    ContrastArmPayload,
    ContrastPayload,
    DeepResearchSelector,
    DeterminationPayload,
    GeneralizedResearchReport,
    IntervalPayload,
    ResearchCensoringPayload,
    ResearchFigurePartPayload,
    ResearchFigurePayload,
    ResearchPathChoicePayload,
    ResearchReadingPayload,
    ResearchRoundPayload,
    ResearchWalkPayload,
    ResearchWalkStepPayload,
)
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_kernel.scope import AbsoluteRange
from revi_kernel.watermark import DataWatermark
from revi_statistics_contracts.contract import Contrast, Interval

#: Coded warning prefixes. The code is a handle a client branches on; the
#: sentence after the colon is what a reader reads. Every one of these is
#: already in the API's warning table — a research study raises the same
#: families a conversational answer does, because they are the same facts.
WARN_READING_REFUSED = "deep_research_angle_refused"
WARN_CENSORING = "deep_research_censoring"
WARN_NO_PRIOR = "deep_research_no_prior"
WARN_BOUNDED = "suppression_bounded"
WARN_RANKING_REFUSED = "ranking_refused"
WARN_CHASE_GATED = "deep_research_chase_gated"

#: A line needs this many ordered points before it may be drawn as one.
_MIN_POINTS_FOR_A_LINE = 3

#: How many figures of one reading reach a finding. A finding is what the
#: composer reads and what the grounding validator admits values from; past
#: a dozen it stops being the reading's shape and becomes the reading.
MAX_FIGURES_PER_FINDING = 12

#: How many figures one chart draws. The same ceiling the reading's own
#: ``top_n`` applies, restated here because a trend is not capped there.
MAX_CHART_ROWS = 24


@dataclass(frozen=True, slots=True)
class GeneralizedReportDraft:
    """The study as the engine leaves it, before the determination is written."""

    report: GeneralizedResearchReport
    #: Coded warnings, for the API's own classification vocabulary.
    warnings: tuple[str, ...]
    #: The composer's context line — the period, the population, the load.
    header: ContextHeaderPayload
    #: Sentences that must reach the reader whatever the prose does, and
    #: that LEAD the determination. Kept to what bounds whether the answer
    #: is an answer at all, so a reader who stops after one sentence has
    #: the claim and the one limit on it.
    disclosures: tuple[str, ...] = field(default=())
    #: Sentences that must reach the reader and that bound HOW THE FIGURES
    #: READ rather than whether they answer the question. They trail: a
    #: determination whose first five sentences are censoring arithmetic
    #: has buried the answer it was asked for, which is the same failure as
    #: dropping the arithmetic, pointed the other way.
    trailing: tuple[str, ...] = field(default=())
    #: The walk's own reasons, as the composer is shown them. Context for
    #: the so-what framing; never a source of a figure.
    walk_reasons: tuple[str, ...] = field(default=())
    #: The consulted background notes, as quotable prose.
    knowledge_context: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------------------
# the sentences this artifact says out loud
#
# Kept together and pure, for the same two reasons ``copy.py`` is: the
# report must read as one document, and the client-language guard invokes
# every one of them over representative figures. A template that reads
# cleanly and interpolates an internal identifier only fails once it is
# called.


NO_PRIOR_SUBSTITUTED = (
    "Every figure in this study is measured on your own data. The background notes "
    "shaped which readings were taken and never what any number says."
)

NOTHING_SETTLED = (
    "Every group this reading produced was too small to publish, so nothing here "
    "speaks for it."
)


def trend_words(*, measure: str, first_label: str, first: str, last_label: str, last: str,
                rising: bool | None) -> str:
    """What a series did between its ends, in the reader's own terms."""
    lead = measure[:1].upper() + measure[1:]
    if rising is None:
        return f"{lead} held at {first} from {first_label} through {last_label}."
    verb = "rose" if rising else "fell"
    return f"{lead} {verb} from {first} in {first_label} to {last} in {last_label}."


def spread_words(*, high_label: str, high: str, low_label: str, low: str) -> str:
    """The two ends of a breakdown, where the ordering may be published."""
    return f"{high_label} is the highest at {high}; {low_label} the lowest at {low}."


def flat_words(*, measure: str, value: str, groups: int) -> str:
    """No spread at all — said as the finding it is, not as a fake ranking.

    Two groups whose figures round to the same printed number are not a
    highest and a lowest. "12.0 days is the highest; 12.0 days the lowest"
    is arithmetically defensible and reads as a rendering fault, and the
    reader's real question — is there a difference here — is answered by
    saying there is not one worth printing.
    """
    lead = measure[:1].upper() + measure[1:]
    return (
        f"{lead} reads {value} across all {groups} groups here, so nothing on this "
        "breakdown separates them."
    )


def single_figure_words(*, measure: str, label: str, value: str) -> str:
    lead = measure[:1].upper() + measure[1:]
    return f"{lead} is {value} over {label}."


def gap_words(*, left_label: str, left: str, right_label: str, right: str,
              difference: Decimal) -> str:
    """Two arms of a comparison and the size of the gap between them."""
    return (
        f"{left_label} runs {left} against {right_label} at {right} — a gap of "
        f"{words.points(difference)}."
    )


def ceiling_census_words(*, bounded: int, total: int, title: str) -> str:
    """How much of one reading is a ceiling rather than a measurement."""
    noun = "group" if bounded == 1 else "groups"
    return (
        f"{bounded} of the {total} {noun} on {title} show a ceiling rather than a figure, "
        "so a mark there is the most it could be and not what it is."
    )


def chase_gated_words(*, dropped: int) -> str:
    """Chases the thresholds did not admit, counted for the reader."""
    noun = "reading" if dropped == 1 else "readings"
    return (
        f"{dropped} further {noun} were proposed and not taken, because nothing in the "
        "round before had separated the population they would have gone inside. Each one "
        "is named with its reason under how Revi got here."
    )


def censoring_words(
    *,
    readings: int,
    measured: int,
    bounded: int,
    withheld: int,
    population: int,
    data_edge: str,
) -> tuple[str, ...]:
    """What the edge of the data cost the rate readings in this study."""
    noun = "reading" if readings == 1 else "readings"
    lines = [
        f"{readings} {noun} here measure a rate over a counted population, across "
        f"{words.count(population, 'record')} the payer has already answered."
    ]
    if bounded:
        lines.append(
            f"{words.count(bounded, 'group')} publish a ceiling rather than a figure, "
            "because the population behind them is too small to name."
        )
    if withheld:
        lines.append(
            f"{words.count(withheld, 'group')} publish nothing at all, for the same "
            "reason, and are left out of every ordering above."
        )
    lines.append(
        f"{words.count(measured, 'group')} carry a measurement. Everything above is as "
        f"the data stood on {data_edge}."
    )
    return tuple(lines)


def window_words(window: AbsoluteRange) -> str:
    """A period as a reader writes one, never as a range literal."""
    return f"{window.start:%b %-d, %Y} through {window.end:%b %-d, %Y}"


def header_words(*, population: str, window: str, load: str) -> str:
    return f"{population.capitalize()}, over {window}, read at {load}."


def round_reason_words(*, index: int, reason: str) -> str:
    """Why a later round exists, quoting the sentence that decided it."""
    return f"Round {index}: {reason}"


# ---------------------------------------------------------------------------
# figures


def _dimension_label(dimension_id: str, catalog: CatalogSnapshot) -> str:
    definition = catalog.dimension(dimension_id)
    if definition is not None:
        return definition.label.lower()
    if dimension_id in ("day", "week", "month"):
        return dimension_id
    return dimension_id.replace("_", " ")


def _display(cell: MeasureCell, unit: str) -> str:
    """One figure, already formatted — or the words that stand in for one.

    A ceiling reads as a ceiling. Prose beside it does not repeat the mark
    (``docs/client-language.md`` §4); the mark carries it.
    """
    if cell.withheld or cell.value is None:
        return "too small to publish"
    rendered = format_value(cell.value, unit)
    return f"≤ {rendered}" if cell.bounded else rendered


def _interval(low: Decimal | None, high: Decimal | None, confidence: Decimal) -> IntervalPayload | None:
    if low is None or high is None:
        return None
    return IntervalPayload(low=str(low), high=str(high), confidence=str(confidence))


def _figure(
    cell: MeasureCell, *, unit: str, catalog: CatalogSnapshot, confidence: Decimal
) -> ResearchFigurePayload:
    return ResearchFigurePayload(
        label=cell.label,
        parts=[
            ResearchFigurePartPayload(
                dimension=name,
                dimension_label=_dimension_label(name, catalog),
                value=value,
                value_label=value or "(no value on the record)",
            )
            for name, value in cell.parts
        ],
        evidence="measured" if cell.is_measured else "not_estimable",
        value=None if cell.value is None else str(cell.value),
        display=_display(cell, unit),
        bounded=cell.bounded,
        withheld=cell.withheld,
        population=cell.population,
        successes=cell.numerator,
        interval=_interval(cell.interval_low, cell.interval_high, confidence),
    )


def _arm(label: str, arm: object) -> ContrastArmPayload:
    n = int(getattr(arm, "n", 0))
    successes = int(getattr(arm, "successes", 0))
    rate = getattr(arm, "rate", None)
    interval: Interval | None = getattr(arm, "interval", None)
    return ContrastArmPayload(
        label=label,
        n=n,
        successes=successes,
        rate=None if rate is None else str(rate),
        interval=(
            None
            if interval is None
            else IntervalPayload(
                low=str(interval.low), high=str(interval.high), confidence=str(interval.confidence)
            )
        ),
    )


def _contrast_payload(result: MeasureResult) -> ContrastPayload | None:
    contrast: Contrast | None = result.contrast
    if contrast is None:
        return None
    if contrast.is_refused:
        return ContrastPayload(
            title=result.title,
            left=_arm(contrast.left.label, contrast.left),
            right=_arm(contrast.right.label, contrast.right),
            test="refused",
            refusal_reason=contrast.refusal_reason,
            implication=(
                "Too few populations here carry enough answered records to compare, so no "
                "comparison was made."
            ),
        )
    interval = contrast.risk_difference_interval
    implication = ""
    if interval is not None and contrast.p_value is not None:
        implication = words.separation_statement(
            p_value=contrast.p_value, low=interval.low, high=interval.high
        )
    return ContrastPayload(
        title=result.title,
        left=_arm(contrast.left.label, contrast.left),
        right=_arm(contrast.right.label, contrast.right),
        test="two_proportion_z" if contrast.z_statistic is not None else "fishers_exact",
        risk_difference=None if contrast.risk_difference is None else str(contrast.risk_difference),
        risk_difference_interval=(
            None
            if interval is None
            else IntervalPayload(
                low=str(interval.low), high=str(interval.high), confidence=str(interval.confidence)
            )
        ),
        z_statistic=None if contrast.z_statistic is None else str(contrast.z_statistic),
        p_value=None if contrast.p_value is None else str(contrast.p_value),
        implication=implication,
    )


# ---------------------------------------------------------------------------
# what a reading settled


def _settled(result: MeasureResult) -> str:
    """One sentence saying what this reading settled, over published figures.

    Composed rather than requested, for the reason every mandatory sentence
    in this codebase is composed: a verdict a model may decline to write is
    not a verdict. Every figure in it came back from an estimator, and a
    reading that measured nothing says exactly that.
    """
    if result.refusal:
        return ""
    measured = [cell for cell in result.cells if cell.is_measured and cell.value is not None]
    if not measured:
        return NOTHING_SETTLED
    measure = metric_label(result.metric_id)

    contrast = _contrast_payload(result)
    if contrast is not None and contrast.test != "refused" and contrast.risk_difference is not None:
        left, right = contrast.left, contrast.right
        gap = gap_words(
            left_label=left.label,
            left=format_value(Decimal(str(left.rate)), result.unit) if left.rate else "",
            right_label=right.label,
            right=format_value(Decimal(str(right.rate)), result.unit) if right.rate else "",
            difference=Decimal(str(contrast.risk_difference)),
        )
        return f"{gap} {contrast.implication}".strip()

    if result.angle.shape is AngleShape.TREND and len(measured) >= 2:
        first, last = measured[0], measured[-1]
        assert first.value is not None and last.value is not None
        rising: bool | None = None
        if last.value > first.value:
            rising = True
        elif last.value < first.value:
            rising = False
        return trend_words(
            measure=measure,
            first_label=first.label,
            first=_display(first, result.unit),
            last_label=last.label,
            last=_display(last, result.unit),
            rising=rising,
        )

    if len(measured) == 1:
        return single_figure_words(
            measure=measure, label=measured[0].label, value=_display(measured[0], result.unit)
        )

    if not result.ranked:
        # Too much of the field is a ceiling for an ordering to be honest,
        # and a superlative over it would rank ceilings against
        # measurements. The refusal is the reading's verdict.
        return result.ranking_refused or NOTHING_SETTLED

    ordered = sorted(measured, key=lambda cell: (cell.value or Decimal(0), cell.label))
    low, high = ordered[0], ordered[-1]
    top, bottom = _display(high, result.unit), _display(low, result.unit)
    if top == bottom:
        return flat_words(measure=measure, value=top, groups=len(measured))
    return spread_words(
        high_label=high.label, high=top, low_label=low.label, low=bottom
    )


# ---------------------------------------------------------------------------
# charts


def _chart(result: MeasureResult, chart_id: str) -> ChartSpec | None:
    """The figure this reading draws, or nothing. One mark is not a chart."""
    measured = [cell for cell in result.cells if cell.is_measured and cell.value is not None]
    if len(measured) < 2:
        return None
    rows = measured[:MAX_CHART_ROWS]
    annotations = list(result.notes)
    bounded = sum(1 for cell in result.cells if cell.bounded)
    if bounded:
        annotations.append(
            ceiling_census_words(
                bounded=bounded,
                total=sum(1 for cell in result.cells if not cell.withheld),
                title=result.title,
            )
        )
    if result.angle.shape is AngleShape.TREND:
        chart_type: ChartType = "line" if len(rows) >= _MIN_POINTS_FOR_A_LINE else "bar"
        return ChartSpec(
            id=chart_id,
            chart_type=chart_type,
            title=result.title,
            frame_id=chart_id,
            x="period",
            value=metric_label(result.metric_id),
            unit=result.unit,
            grade=result.grade,
            rows=[ChartRow(x=cell.label, value=float(cell.value or 0)) for cell in rows],
            annotations=annotations,
            axis_order=[cell.label for cell in rows],
        )
    ordered = (
        sorted(rows, key=lambda cell: (-(cell.value or Decimal(0)), cell.label))
        if result.ranked
        else rows
    )
    return ChartSpec(
        id=chart_id,
        chart_type="bar",
        title=result.title,
        frame_id=chart_id,
        x="population",
        value=metric_label(result.metric_id),
        unit=result.unit,
        grade=result.grade,
        rows=[ChartRow(x=cell.label, value=float(cell.value or 0)) for cell in ordered],
        annotations=annotations,
        sort=(
            ChartSort(by=metric_label(result.metric_id), direction="desc")
            if result.ranked
            else None
        ),
    )


# ---------------------------------------------------------------------------
# findings — what the composer may cite, and what the validator admits


def _finding_value(cell: MeasureCell, unit: str) -> float | int:
    """One figure as the NUMBER a reader would say out loud.

    Money is published in dollars and named as dollars. Handing a composer
    a bare integer of cents beside a label reading "denied dollars" invites
    a hundredfold overstatement that every downstream check passes, because
    the digits are real and only the unit is wrong.
    """
    value = cell.value or Decimal(0)
    if unit == "money_cents":
        return words.dollar_value(int(value))
    return float(value)


def _findings(readings: Sequence[tuple[ResearchReadingPayload, MeasureResult]]) -> list[FindingPayload]:
    findings: list[FindingPayload] = []
    for index, (payload, result) in enumerate(readings, start=1):
        measured = [cell for cell in result.cells if cell.is_measured and cell.value is not None]
        if not measured:
            continue
        values = [
            FindingValue(name=cell.label, value=_finding_value(cell, result.unit))
            for cell in measured[:MAX_FIGURES_PER_FINDING]
        ]
        for cell in measured[:MAX_FIGURES_PER_FINDING]:
            if cell.population is not None:
                values.append(
                    FindingValue(name=f"{cell.label} — records behind it", value=cell.population)
                )
        findings.append(
            FindingPayload(
                referent=f"F{index}",
                title=payload.title,
                statement=payload.settled or payload.reason,
                metric_ids=[result.metric_id],
                values=values,
                grade=result.grade if result.grade in ("direct", "derived") else "direct",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# censoring — only where outcome-like data was involved


def _censoring(
    readings: Sequence[tuple[ResearchReadingPayload, MeasureResult]],
    *,
    window: AbsoluteRange,
    data_edge: date,
) -> ResearchCensoringPayload | None:
    """The data edge's cost to this study's rate readings, or nothing.

    A rate over a counted population can be censored — records the payer
    has not answered are in neither the numerator nor the denominator. A
    dollars or days figure has no population to be censored out of, so a
    censoring block beside one would be a disclosure about nothing. That is
    why this is optional in the shape and required whenever outcome-like
    data is present.
    """
    outcome = [
        (payload, result)
        for payload, result in readings
        if any(cell.population is not None for cell in result.cells)
    ]
    if not outcome:
        return None
    measured = bounded = withheld = 0
    population = 0
    for _, result in outcome:
        for cell in result.cells:
            if cell.withheld:
                withheld += 1
            elif cell.bounded:
                bounded += 1
            elif cell.is_measured:
                measured += 1
                population += cell.population or 0
    edge = data_edge.strftime("%b %-d, %Y")
    return ResearchCensoringPayload(
        data_edge_date=data_edge,
        window_label=window_words(window),
        readings_over_outcomes=len(outcome),
        figures_measured=measured,
        figures_bounded=bounded,
        figures_withheld=withheld,
        population_measured=population,
        statements=list(
            censoring_words(
                readings=len(outcome),
                measured=measured,
                bounded=bounded,
                withheld=withheld,
                population=population,
                data_edge=edge,
            )
        ),
    )


# ---------------------------------------------------------------------------
# the walk


def _walk_payload(walk: ResearchWalk, readings: Sequence[ResearchReadingPayload]) -> ResearchWalkPayload:
    """The recorded walk, grouped into the rounds a reader can follow."""
    # A run's last steps — the drops it is recording and the synthesis it
    # is about to do — are stamped with the round the loop STOPPED at,
    # which is one past the last round it took. Left ungrouped they open a
    # round of their own with no readings in it, and a reader following the
    # walk is shown a pass that never happened.
    last = max(walk.rounds - 1, 0)
    by_round: dict[int, list[WalkStep]] = {}
    for step in walk.steps:
        by_round.setdefault(min(step.round, last), []).append(step)
    reading_ids: dict[int, list[str]] = {}
    for reading in readings:
        reading_ids.setdefault(reading.round, []).append(reading.id)

    rounds: list[ResearchRoundPayload] = []
    for index in sorted(set(by_round) | set(reading_ids)):
        steps = by_round.get(index, ())
        reason = ""
        if index > 0:
            decisive = next(
                (step for step in steps if step.action in ("chase", "broaden")), None
            )
            if decisive is not None:
                reason = round_reason_words(index=index, reason=decisive.reason)
        rounds.append(
            ResearchRoundPayload(
                index=index,
                reason=reason,
                steps=[
                    ResearchWalkStepPayload(
                        round=step.round,
                        action=step.action,  # type: ignore[arg-type]
                        subject=step.subject.replace("_", " "),
                        reason=step.reason,
                        detail=step.detail,
                    )
                    for step in steps
                ],
                readings=reading_ids.get(index, []),
            )
        )
    return ResearchWalkPayload(
        rounds_taken=walk.rounds,
        rounds_allowed=walk.budget,
        authored_by="model" if walk.authored_by == "model" else "revi",
        rationale=walk.rationale,
        rounds=rounds,
    )


def _reading_id(index: int) -> str:
    return f"R{index}"


def _basis_label(result: MeasureResult) -> str:
    """Which date this reading was measured on, in a reader's words."""
    if not result.basis or result.basis == "as of":
        return "as it stood at the data edge"
    return f"on the {date_phrase(result.basis)}"


# ---------------------------------------------------------------------------
# the build


def build_generalized_report(
    *,
    run_id: str,
    walk: ResearchWalk,
    results: Sequence[MeasureResult],
    orientation: Orientation,
    settings: DeepResearchSettings,
    catalog: CatalogSnapshot,
    watermark: DataWatermark,
    population_label: str,
    created_at: datetime,
    completed_at: datetime,
    duration_ms: int,
) -> GeneralizedReportDraft:
    """Assemble the study from the readings that ran."""
    window = orientation.window
    load_label = words.data_load_label(watermark.newest_data_date.strftime("%b %-d, %Y"))
    window_label = window_words(window)

    warnings: list[str] = []
    disclosures: list[str] = []
    charts: list[ChartSpec] = []
    paired: list[tuple[ResearchReadingPayload, MeasureResult]] = []

    for index, result in enumerate(results, start=1):
        reading_id = _reading_id(index)
        chart = _chart(result, f"research-{run_id}-{index}")
        if chart is not None:
            charts.append(chart)
        figures = [
            _figure(cell, unit=result.unit, catalog=catalog, confidence=settings.confidence)
            for cell in result.cells
        ]
        payload = ResearchReadingPayload(
            id=reading_id,
            shape=str(result.angle.shape),  # type: ignore[arg-type]
            title=result.title,
            measure_label=metric_label(result.metric_id),
            metric_id=result.metric_id,
            unit=result.unit,
            reason=result.angle.reason,
            settled=_settled(result),
            round=result.angle.round,
            chases=result.angle.chases,
            figures=figures,
            contrast=_contrast_payload(result),
            chart_id=chart.id if chart is not None else "",
            ranked=result.ranked,
            ranking_refused=result.ranking_refused,
            notes=list(result.notes),
            refusal=result.refusal or "",
            window_label=window_label,
            basis_label=_basis_label(result),
            read_fingerprint=result.read_fingerprint,
            rows_read=result.rows_read,
            figures_published=result.cells_published,
            figures_withheld=result.cells_refused,
            cache_hit=result.cache_hit,
            duration_ms=result.duration_ms,
        )
        paired.append((payload, result))

        if result.refusal:
            warnings.append(
                f"{WARN_READING_REFUSED}: "
                + words.angle_refused_statement(title=result.title, reason=result.refusal)
            )
        if result.ranking_refused:
            warnings.append(f"{WARN_RANKING_REFUSED}: {result.ranking_refused}")
        bounded = sum(1 for cell in result.cells if cell.bounded)
        if bounded:
            warnings.append(
                f"{WARN_BOUNDED}: "
                + ceiling_census_words(
                    bounded=bounded,
                    total=sum(1 for cell in result.cells if not cell.withheld),
                    title=result.title,
                )
            )
        # The basis substitution arrives from the executor already carrying
        # its own ``alternate_basis_used`` prefix, so it is passed through
        # rather than re-worded: one decision, described once.
        warnings.extend(result.notes)

    readings = [payload for payload, _ in paired]

    # -- what this study never did with the notes it read ------------------
    disclosures.append(NO_PRIOR_SUBSTITUTED)
    warnings.append(f"{WARN_NO_PRIOR}: {NO_PRIOR_SUBSTITUTED}")

    # -- chases the thresholds did not admit --------------------------------
    gated = [step for step in walk.steps if step.action == "drop" and step.detail]
    if gated:
        sentence = chase_gated_words(dropped=len(gated))
        warnings.append(f"{WARN_CHASE_GATED}: {sentence}")

    # -- what the data edge cost, where it could cost anything --------------
    censoring = _censoring(paired, window=window, data_edge=watermark.newest_data_date)
    trailing: list[str] = []
    if censoring is not None:
        for statement in censoring.statements:
            warnings.append(f"{WARN_CENSORING}: {statement}")
        trailing.extend(censoring.statements)

    findings = _findings(paired)
    selector = DeepResearchSelector(
        kind=str(orientation.population.kind),  # type: ignore[arg-type]
        values=list(orientation.population.values),
        label=population_label,
    )
    report = GeneralizedResearchReport(
        id=run_id,
        research_question=walk.question,
        population=selector,
        population_label=population_label,
        window_label=window_label,
        data_load_label=load_label,
        data_edge_date=watermark.newest_data_date,
        created_at=created_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        determination=DeterminationPayload(
            question=walk.question,
            statement="",
            composed=False,
            rests_on=[reading.id for reading in readings if reading.figures_published > 0],
        ),
        readings=readings,
        walk=_walk_payload(walk, readings),
        path_choices=[
            ResearchPathChoicePayload(subject=note.subject, statement=note.statement)
            for note in orientation.notes
        ],
        knowledge_statement=orientation.knowledge.statement,
        knowledge_consulted=[
            ConsultedNotePayload(title=entry.title, matched_on=list(entry.matched_on))
            for entry in orientation.knowledge.entries
        ],
        censoring=censoring,
        findings=findings,
        charts=charts,
        warnings=[],
    )
    header = ContextHeaderPayload(
        window_start=window.start,
        window_end=window.end,
        basis="service",
        filters=[],
        filter_chips=[],
        cohort=population_label,
        watermark_id=watermark.id,
        display=header_words(
            population=population_label, window=window_label, load=load_label
        ),
    )
    return GeneralizedReportDraft(
        report=report,
        warnings=tuple(dict.fromkeys(warnings)),
        header=header,
        disclosures=tuple(dict.fromkeys(disclosures)),
        trailing=tuple(dict.fromkeys(trailing)),
        walk_reasons=_walk_reasons(walk),
        knowledge_context=_knowledge_context(orientation),
    )


def _walk_reasons(walk: ResearchWalk) -> tuple[str, ...]:
    """The decisions the run made, as the composer is shown them.

    Reasons only, and never a figure to cite: a chase clause quoting a
    ratio is the loop explaining itself, not an estimator publishing a
    value, and the grounding validator will drop any sentence that lifts a
    number out of one. That is the intended behaviour and not a limitation.
    """
    return tuple(
        f"{step.action}: {step.reason}"
        for step in walk.steps
        if step.action in ("plan", "chase", "broaden", "drop", "refuse")
    )


def _knowledge_context(orientation: Orientation) -> tuple[str, ...]:
    """The background notes, as prose the determination may frame with.

    Summaries and cautions only, exactly as the planner sees them. A note
    may inform the so-what; it may never be a number, which is enforced by
    the grounding validator rather than asked for — none of these lines
    reaches the fact set's numeric values, so a sentence quoting an
    industry figure fails validation and is dropped.
    """
    lines: list[str] = []
    for entry in orientation.knowledge.entries:
        lines.append(f"{entry.title}: {entry.summary}")
        lines.extend(entry.cautions)
    return tuple(lines)


def planned_reading_payloads(
    angles: Sequence[PlannedAngle], catalog: CatalogSnapshot
) -> list[dict[str, object]]:
    """The opening readings, for the frame a watcher sees before results.

    The same three fields the preview card shows, so a reader who confirmed
    a run and then watched it sees the readings named identically.
    """
    from revi_investigation.application.deep_research.measures import title_of

    return [
        {
            "shape": str(angle.shape),
            "title": title_of(angle, catalog),
            "reason": angle.reason,
            "round": angle.round,
            "chases": angle.chases,
        }
        for angle in angles
    ]


__all__ = [
    "MAX_CHART_ROWS",
    "MAX_FIGURES_PER_FINDING",
    "NOTHING_SETTLED",
    "NO_PRIOR_SUBSTITUTED",
    "WARN_BOUNDED",
    "WARN_CENSORING",
    "WARN_CHASE_GATED",
    "WARN_NO_PRIOR",
    "WARN_RANKING_REFUSED",
    "WARN_READING_REFUSED",
    "GeneralizedReportDraft",
    "build_generalized_report",
    "ceiling_census_words",
    "censoring_words",
    "chase_gated_words",
    "gap_words",
    "header_words",
    "planned_reading_payloads",
    "round_reason_words",
    "single_figure_words",
    "spread_words",
    "trend_words",
    "window_words",
]
