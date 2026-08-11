"""Turning certified estimates into the artifact a link points at.

Nothing in this module computes. Every number it publishes was produced by
``revi_statistics`` and arrives already labelled measured or refused; the
work here is deciding what a reader is shown, in what order, and in whose
words.

Three rules it enforces on the way out.

**A refusal is published, never dropped.** A population whose own history
could not support a rate keeps its count and its denied dollars and is
listed on its own, outside the total. The total says so beside itself.

**A population too small to NAME is not named.** The rate floor and the
naming floor are different rules doing different jobs: one protects the
reader from noise, the other protects the denials from being identified.
Populations under the naming floor are rolled into a single line — how
many, and what they hold.

**A chart never draws a claim it cannot support.** A line needs three
ordered points; below that it is a bar. One mark is a figure, not a trend.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from revi_investigation.application.deep_research import copy as words
from revi_investigation.application.deep_research.angles import AngleResult
from revi_investigation.application.deep_research.grammar import (
    AngleFamily,
    DeepResearchPlan,
    RateBasisChoice,
    TargetPopulation,
)
from revi_investigation.application.deep_research.policy import DeepResearchSettings
from revi_investigation.application.deep_research.rows import DenialRows
from revi_investigation_contracts.api import (
    ChartRow,
    ChartSort,
    ChartSpec,
    ChartType,
    FindingPayload,
    FindingValue,
)
from revi_investigation_contracts.deep_research import (
    AngleEvidencePayload,
    CensoringPayload,
    ContrastArmPayload,
    ContrastPayload,
    DeadlinePayload,
    DeadlineRowPayload,
    DeepResearchReport,
    DeepResearchSelector,
    ExpectedRecoveryRowPayload,
    HeadlinePayload,
    IntervalPayload,
    MoneyIntervalPayload,
    RateCellPayload,
    ResearchAnglePayload,
    ResearchPlanPayload,
    StratumPartPayload,
    ThinPopulationsPayload,
    TimelinessBandPayload,
    TimelinessCurvePayload,
)
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_statistics_contracts.contract import (
    CensoringDisclosure,
    Contrast,
    ContrastArm,
    DurationEstimate,
    EvidenceLabel,
    ExpectedRecovery,
    ExpectedRecoveryStratum,
    Interval,
    RateCell,
    RateEstimate,
    StratumKey,
)

#: Coded warning prefixes. The code is a handle a client branches on; the
#: sentence after the colon is what a reader reads.
WARN_UNPRICED = "deep_research_unpriced"
WARN_EXTREMES = "deep_research_extremes"
WARN_CENSORING = "deep_research_censoring"
WARN_ANGLE_REFUSED = "deep_research_angle_refused"
WARN_INDEPENDENCE = "deep_research_independence"
WARN_NO_PRIOR = "deep_research_no_prior"
WARN_THIN_ROLLUP = "deep_research_thin_rollup"

#: A line needs this many ordered points before it may be drawn as one.
_MIN_POINTS_FOR_A_LINE = 3


@dataclass(frozen=True, slots=True)
class ReportDraft:
    """The report as the engine leaves it, before prose is composed."""

    report: DeepResearchReport
    #: Coded warnings, for the API's own classification vocabulary.
    warnings: tuple[str, ...]
    #: The composer's context line — window, population, and the floor.
    header: ContextHeaderPayload
    #: Disclosures that must reach the reader whatever the prose does.
    disclosures: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------------------
# small conversions


def _interval(interval: Interval | None) -> IntervalPayload | None:
    if interval is None:
        return None
    return IntervalPayload(
        low=str(interval.low), high=str(interval.high), confidence=str(interval.confidence)
    )


def _money(low: int, high: int, confidence: Decimal) -> MoneyIntervalPayload:
    return MoneyIntervalPayload(low_cents=low, high_cents=high, confidence=str(confidence))


def _parts(
    stratum: StratumKey, settings: DeepResearchSettings
) -> list[StratumPartPayload]:
    return [
        StratumPartPayload(
            stratifier=name,  # type: ignore[arg-type]
            stratifier_label=settings.stratifier_label(name),
            value=value,
            value_label=settings.value_label(name, value),
        )
        for name, value in stratum.parts
    ]


def _label(stratum: StratumKey, settings: DeepResearchSettings) -> str:
    if not stratum.parts:
        return "everything in this population"
    return " / ".join(settings.value_label(name, value) for name, value in stratum.parts)


def _tier(evidence: EvidenceLabel) -> str:
    return "measured" if evidence is EvidenceLabel.MEASURED else "not_estimable"


def _cell(cell: RateCell, settings: DeepResearchSettings) -> RateCellPayload:
    return RateCellPayload(
        label=_label(cell.stratum, settings),
        parts=_parts(cell.stratum, settings),
        basis="decided" if str(cell.basis) == "decided" else "pursuit",
        n=cell.n,
        successes=cell.successes,
        evidence=_tier(cell.evidence),  # type: ignore[arg-type]
        rate=None if cell.rate is None else str(cell.rate),
        interval=_interval(cell.interval),
        floor=cell.min_cohort,
    )


def _arm(arm: ContrastArm, label: str | None = None) -> ContrastArmPayload:
    return ContrastArmPayload(
        label=label or arm.label,
        n=arm.n,
        successes=arm.successes,
        rate=None if arm.rate is None else str(arm.rate),
        interval=_interval(arm.interval),
    )


def _stratum_row(
    stratum: ExpectedRecoveryStratum, settings: DeepResearchSettings
) -> ExpectedRecoveryRowPayload:
    return ExpectedRecoveryRowPayload(
        label=_label(stratum.stratum, settings),
        parts=_parts(stratum.stratum, settings),
        evidence=_tier(stratum.evidence),  # type: ignore[arg-type]
        open_denials=stratum.open_denials,
        open_dollars_cents=stratum.open_dollars_cents,
        catchable_dollars_cents=stratum.catchable_dollars_cents,
        deadline_passed_dollars_cents=stratum.deadline_passed_dollars_cents,
        deadline_unknown_dollars_cents=stratum.deadline_unknown_dollars_cents,
        rate_cell=_cell(stratum.rate_cell, settings),
        expected_cents=stratum.expected_cents,
        expected_interval=(
            None
            if stratum.expected_interval is None
            else _money(
                stratum.expected_interval.low_cents,
                stratum.expected_interval.high_cents,
                stratum.expected_interval.confidence,
            )
        ),
    )


def _censoring(
    disclosure: CensoringDisclosure, settings: DeepResearchSettings
) -> CensoringPayload:
    statements = words.censoring_statements(
        considered=disclosure.rows_considered,
        in_denominator=disclosure.in_denominator,
        open_undecided=disclosure.excluded_open_undecided,
        not_pursued=disclosure.excluded_not_pursued,
        immature=disclosure.excluded_immature,
        data_edge=disclosure.data_edge_date.strftime("%b %-d, %Y"),
    )
    return CensoringPayload(
        basis="decided" if str(disclosure.basis) == "decided" else "pursuit",
        data_edge_date=disclosure.data_edge_date,
        rows_considered=disclosure.rows_considered,
        in_denominator=disclosure.in_denominator,
        excluded_immature=disclosure.excluded_immature,
        excluded_open_undecided=disclosure.excluded_open_undecided,
        excluded_not_pursued=disclosure.excluded_not_pursued,
        excluded_unclassifiable=disclosure.excluded_unclassifiable,
        open_undecided_in_input=disclosure.open_undecided_in_input,
        not_pursued_in_input=disclosure.not_pursued_in_input,
        statements=list(statements),
    )


def plan_payload_of(
    plan: DeepResearchPlan, settings: DeepResearchSettings
) -> ResearchPlanPayload:
    """The angles a run will look at, in the words a reader sees them in.

    Shared by the report and by the PLAN-ONLY preview, so a confirmation
    card and the run it starts describe the same angles in the same
    sentences — a preview composed separately is a second description that
    can drift from the thing it previews.
    """
    return ResearchPlanPayload(
        research_question=plan.research_question,
        angles=[
            ResearchAnglePayload(
                family=str(angle.family),  # type: ignore[arg-type]
                title=settings.angle(str(angle.family)).title,
                purpose=settings.angle(str(angle.family)).purpose,
                stratify_by=[str(s) for s in angle.stratify_by],  # type: ignore[misc]
                within=[str(s) for s in angle.within],  # type: ignore[misc]
                basis=str(angle.basis),  # type: ignore[arg-type]
            )
            for angle in plan.angles
        ],
        rationale=plan.rationale,
        authored_by=plan.authored_by,  # type: ignore[arg-type]
        added_by_revi=[str(family) for family in plan.added_by_revi],  # type: ignore[misc]
    )


# ---------------------------------------------------------------------------
# charts


def _bar(
    *,
    chart_id: str,
    title: str,
    x_label: str,
    value_label: str,
    rows: Sequence[tuple[str, float | int]],
    unit: str,
    annotations: Sequence[str] = (),
    axis_order: Sequence[str] | None = None,
    ranked: bool = False,
) -> ChartSpec | None:
    """A bar chart, or nothing. One mark is a figure, not a chart.

    ``ranked`` says these rows were ORDERED BY THE MEASURE, and it is
    published on the wire as the ordering it is. A ranking whose ordering
    stays inside this function is a ranking the renderer cannot recognise:
    it reads ``order.basis`` to decide that a league table of payer names
    is read down a column rather than across a 60px axis, and every chart
    here arrived with no ordering at all, so a report full of rankings drew
    them all as rotated-label column charts.
    """
    if len(rows) < 2:
        return None
    return ChartSpec(
        id=chart_id,
        chart_type="bar",
        title=title,
        frame_id=chart_id,
        x=x_label,
        value=value_label,
        unit=unit,
        grade="direct",
        rows=[ChartRow(x=name, value=value) for name, value in rows],
        annotations=list(annotations),
        axis_order=list(axis_order) if axis_order else None,
        sort=ChartSort(by=value_label, direction="desc") if ranked else None,
    )


def _ordered_series(
    *,
    chart_id: str,
    title: str,
    x_label: str,
    value_label: str,
    rows: Sequence[tuple[str, float]],
    annotations: Sequence[str] = (),
) -> ChartSpec | None:
    """An ordered series: a line at three points or more, a bar below that."""
    if len(rows) < 2:
        return None
    order = [name for name, _ in rows]
    chart_type: ChartType = "line" if len(order) >= _MIN_POINTS_FOR_A_LINE else "bar"
    return ChartSpec(
        id=chart_id,
        chart_type=chart_type,
        title=title,
        frame_id=chart_id,
        x=x_label,
        value=value_label,
        unit="ratio",
        grade="direct",
        rows=[ChartRow(x=name, value=value) for name, value in rows],
        annotations=list(annotations),
        axis_order=order,
    )


# ---------------------------------------------------------------------------
# the build


def _by_family(results: Sequence[AngleResult]) -> Mapping[AngleFamily, list[AngleResult]]:
    grouped: dict[AngleFamily, list[AngleResult]] = {}
    for result in results:
        grouped.setdefault(result.angle.family, []).append(result)
    return grouped


def _headline(priced: ExpectedRecovery) -> HeadlinePayload:
    every = (*priced.strata, *priced.refused_strata)
    return HeadlinePayload(
        total_open_denials=sum(stratum.open_denials for stratum in every),
        total_open_dollars_cents=priced.total_open_dollars_cents,
        total_expected_cents=priced.total_expected_cents,
        total_expected_interval=_money(
            priced.total_expected_interval.low_cents,
            priced.total_expected_interval.high_cents,
            priced.total_expected_interval.confidence,
        ),
        priced_open_dollars_cents=priced.priced_open_dollars_cents,
        unpriced_open_dollars_cents=priced.unpriced_open_dollars_cents,
        unpriced_share=str(priced.unpriced_share),
        catchable_dollars_cents=priced.catchable_dollars_cents,
        deadline_passed_dollars_cents=priced.deadline_passed_dollars_cents,
        deadline_unknown_dollars_cents=priced.deadline_unknown_dollars_cents,
        range_assumes_independence=priced.interval_assumes_independence,
    )


def _contrast_payload(
    result: AngleResult, settings: DeepResearchSettings
) -> ContrastPayload | None:
    contrast: Contrast | None = result.contrast
    if contrast is None:
        return None
    title = (
        words.TITLE_PAYER_GAP
        if result.angle.family is AngleFamily.PAYER_CONTRAST
        else words.TITLE_CLASS_GAP
    )
    if contrast.is_refused:
        return ContrastPayload(
            title=title,
            left=_arm(contrast.left),
            right=_arm(contrast.right),
            test="refused",
            refusal_reason=contrast.refusal_reason,
            implication=words.contrast_refused_statement(
                subject=result.contrast_subject, floor_sentence=settings.floor_sentence()
            ),
        )
    interval = contrast.risk_difference_interval
    implication = ""
    if interval is not None and contrast.p_value is not None:
        implication = words.separation_statement(
            p_value=contrast.p_value, low=interval.low, high=interval.high
        )
    # The estimator identifies a population by its own key; a reader is
    # shown the population's name.
    left_label = right_label = None
    if result.contrast_cells is not None:
        left_label = _label(result.contrast_cells[0].stratum, settings)
        right_label = _label(result.contrast_cells[1].stratum, settings)
    if result.contrast_note:
        implication = f"{result.contrast_note} {implication}".strip()
    return ContrastPayload(
        title=title,
        left=_arm(contrast.left, left_label),
        right=_arm(contrast.right, right_label),
        test="two_proportion_z" if contrast.z_statistic is not None else "fishers_exact",
        risk_difference=None if contrast.risk_difference is None else str(contrast.risk_difference),
        risk_difference_interval=_interval(interval),
        z_statistic=None if contrast.z_statistic is None else str(contrast.z_statistic),
        p_value=None if contrast.p_value is None else str(contrast.p_value),
        implication=implication,
    )


def _timeliness(
    result: AngleResult, settings: DeepResearchSettings
) -> tuple[TimelinessCurvePayload | None, list[str]]:
    curve: RateEstimate | None = result.curve
    if curve is None:
        return None, []
    bands = [
        TimelinessBandPayload(
            band=cell.stratum.value_of("delay_band") or cell.stratum.label,
            cell=_cell(cell, settings),
        )
        for cell in curve.cells
    ]
    measured = [band for band in bands if band.cell.rate is not None]
    implication = ""
    notes: list[str] = []
    if len(measured) >= 2:
        fast, slow = measured[0], measured[-1]
        fast_rate = Decimal(str(fast.cell.rate))
        slow_rate = Decimal(str(slow.cell.rate))
        implication = words.timeliness_statement(
            fast_band=fast.band,
            fast_rate=fast_rate,
            slow_band=slow.band,
            slow_rate=slow_rate,
        )
        notes.append(
            words.timeliness_implication(fast_band=fast.band, drop=fast_rate - slow_rate)
        )
    durations: DurationEstimate | None = result.durations
    if durations is not None:
        for cell in durations.cells:
            if cell.median_days is None:
                continue
            name = cell.stratum.value_of("recovery_class")
            if name is None:
                continue
            notes.append(
                words.median_delay_statement(
                    label=settings.value_label("recovery_class", name).capitalize(),
                    median=cell.median_days,
                )
            )
    return (
        TimelinessCurvePayload(
            bands=bands,
            within=[str(s) for s in result.angle.within],  # type: ignore[misc]
            implication=implication,
        ),
        notes,
    )


def _deadline(
    result: AngleResult, settings: DeepResearchSettings
) -> tuple[DeadlinePayload | None, list[str]]:
    estimate = result.rates
    if estimate is None:
        return None, []
    rows: list[DeadlineRowPayload] = []
    for cell in estimate.cells:
        position = cell.stratum.value_of("filing_position") or ""
        rule = cell.stratum.value_of("filing_rule") or ""
        rows.append(
            DeadlineRowPayload(
                position=position,
                position_label=settings.value_label("filing_position", position),
                rule=rule,
                rule_label=settings.value_label("filing_rule", rule),
                cell=_cell(cell, settings),
            )
        )
    notes: list[str] = [words.DEADLINE_AUTHORITY_NOTE]
    implication = ""
    within = [
        row for row in rows if row.position == "within_deadline" and row.cell.rate is not None
    ]
    past = [row for row in rows if row.position == "past_deadline" and row.cell.rate is not None]
    if within and past:
        within_n = sum(row.cell.n for row in within)
        within_wins = sum(row.cell.successes for row in within)
        past_n = sum(row.cell.n for row in past)
        past_wins = sum(row.cell.successes for row in past)
        if within_n and past_n:
            implication = words.deadline_statement(
                within_rate=Decimal(within_wins) / Decimal(within_n),
                within_n=within_n,
                past_rate=Decimal(past_wins) / Decimal(past_n),
                past_n=past_n,
            )
    for row in past:
        if row.rule == "confirmed" and row.cell.successes == 0 and row.cell.interval is not None:
            notes.append(
                words.zero_rate_bound_statement(
                    high=Decimal(row.cell.interval.high), n=row.cell.n
                )
            )
    return DeadlinePayload(rows=rows, implication=implication), notes


def build_report(
    *,
    run_id: str,
    plan: DeepResearchPlan,
    population: TargetPopulation,
    results: Sequence[AngleResult],
    rows: DenialRows,
    settings: DeepResearchSettings,
    created_at: datetime,
    completed_at: datetime,
    duration_ms: int,
) -> ReportDraft:
    """Assemble the artifact from the angles that ran."""
    grouped = _by_family(results)
    pricing = grouped.get(AngleFamily.EXPECTED_RECOVERY, [])
    if not pricing or pricing[0].expected is None:
        raise ValueError("a deep-research report needs a priced open population")
    priced = pricing[0].expected

    warnings: list[str] = []
    disclosures: list[str] = []
    findings: list[FindingPayload] = []
    charts: list[ChartSpec] = []
    context_notes: list[str] = []

    population_words = words.population_label(str(population.kind), population.values)
    load_label = words.data_load_label(rows.as_of.strftime("%b %-d, %Y"))

    # -- the headline ------------------------------------------------------
    headline = _headline(priced)
    priced_by = words.priced_by_statement(
        tuple(
            settings.stratifier_label(stratum).lower()
            for stratum in pricing[0].angle.stratify_by
        )
    )
    disclosures.append(priced_by)
    findings.append(
        FindingPayload(
            referent="F1",
            title=words.TITLE_HEADLINE,
            statement=" ".join(
                (
                    words.headline_statement(
                        expected=headline.total_expected_cents,
                        low=headline.total_expected_interval.low_cents,
                        high=headline.total_expected_interval.high_cents,
                        open_dollars=headline.total_open_dollars_cents,
                        open_denials=headline.total_open_denials,
                    ),
                    priced_by,
                )
            ),
            values=[
                FindingValue(
                    name="expected recoverable dollars",
                    value=words.dollar_value(headline.total_expected_cents),
                ),
                FindingValue(
                    name="lowest the recoverable dollars could be",
                    value=words.dollar_value(headline.total_expected_interval.low_cents),
                ),
                FindingValue(
                    name="highest the recoverable dollars could be",
                    value=words.dollar_value(headline.total_expected_interval.high_cents),
                ),
                FindingValue(
                    name="open denied dollars",
                    value=words.dollar_value(headline.total_open_dollars_cents),
                ),
                FindingValue(name="open denials", value=headline.total_open_denials),
            ],
            grade="direct",
            impact_cents=headline.total_expected_cents,
        )
    )
    findings.append(
        FindingPayload(
            referent="F2",
            title=words.TITLE_STILL_CATCHABLE,
            statement=words.split_statement(
                catchable=headline.catchable_dollars_cents,
                passed=headline.deadline_passed_dollars_cents,
                unknown=headline.deadline_unknown_dollars_cents,
            ),
            values=[
                FindingValue(
                    name="dollars inside the filing deadline",
                    value=words.dollar_value(headline.catchable_dollars_cents),
                ),
                FindingValue(
                    name="dollars past the filing deadline",
                    value=words.dollar_value(headline.deadline_passed_dollars_cents),
                ),
                FindingValue(
                    name="dollars on plans with no limit on file",
                    value=words.dollar_value(headline.deadline_unknown_dollars_cents),
                ),
            ],
            grade="direct",
            impact_cents=headline.catchable_dollars_cents,
        )
    )
    disclosures.append(words.NO_PRIOR_SUBSTITUTED)
    warnings.append(f"{WARN_NO_PRIOR}: {words.NO_PRIOR_SUBSTITUTED}")
    warnings.append(f"{WARN_INDEPENDENCE}: {words.INDEPENDENCE_CAVEAT}")
    if headline.deadline_unknown_dollars_cents:
        warnings.append(f"{WARN_UNPRICED}: {words.DEADLINE_UNKNOWN_NOTE}")

    # -- what could not be priced ------------------------------------------
    named: list[ExpectedRecoveryRowPayload] = []
    hidden: list[ExpectedRecoveryStratum] = []
    for stratum in priced.refused_strata:
        if stratum.open_denials >= settings.disclosure_floor:
            named.append(_stratum_row(stratum, settings))
        else:
            hidden.append(stratum)
    thin: ThinPopulationsPayload | None = None
    if hidden:
        thin = ThinPopulationsPayload(
            populations=len(hidden),
            open_denials=sum(stratum.open_denials for stratum in hidden),
            open_dollars_cents=sum(stratum.open_dollars_cents for stratum in hidden),
            floor=settings.disclosure_floor,
        )
        warnings.append(
            f"{WARN_THIN_ROLLUP}: "
            + words.thin_rollup_statement(
                populations=thin.populations,
                denials=thin.open_denials,
                cents=thin.open_dollars_cents,
                floor=settings.disclosure_floor,
            )
        )
    if priced.unpriced_open_dollars_cents:
        unpriced_sentence = words.unpriced_statement(
            unpriced=priced.unpriced_open_dollars_cents,
            share=priced.unpriced_share,
            populations=len(priced.refused_strata),
        )
        findings.append(
            FindingPayload(
                referent="F3",
                title=words.TITLE_NOT_ESTIMABLE,
                statement=unpriced_sentence,
                values=[
                    FindingValue(
                        name="dollars no rate could price",
                        value=words.dollar_value(priced.unpriced_open_dollars_cents),
                    ),
                    FindingValue(name="populations", value=len(priced.refused_strata)),
                ],
                grade="direct",
                impact_cents=priced.unpriced_open_dollars_cents,
            )
        )
        warnings.append(f"{WARN_UNPRICED}: {unpriced_sentence} {words.THIN_EXPLANATION}")
        disclosures.append(unpriced_sentence)

    strata_rows = [_stratum_row(stratum, settings) for stratum in priced.strata]
    top = sorted(
        (row for row in strata_rows if row.expected_cents is not None),
        key=lambda row: (-(row.expected_cents or 0), row.label),
    )[:12]
    chart = _bar(
        chart_id="deep-research-expected",
        title="Expected recoverable by population",
        x_label="population",
        value_label="expected recoverable",
        rows=[(row.label, row.expected_cents or 0) for row in top],
        unit="money_cents",
        annotations=[words.NO_PRIOR_SUBSTITUTED],
        # `top` is sorted by expected dollars, descending, ten lines above.
        ranked=True,
    )
    if chart is not None:
        charts.append(chart)

    # -- rates by population ------------------------------------------------
    rate_cells: list[RateCellPayload] = []
    next_referent = len(findings) + 1
    for result in grouped.get(AngleFamily.OUTCOME_BY_STRATUM, []):
        estimate = result.rates
        if estimate is None:
            continue
        cells = [_cell(cell, settings) for cell in estimate.cells]
        rate_cells.extend(cells)
        measured = [cell for cell in cells if cell.rate is not None]
        if len(measured) < 2:
            continue
        pursuit = result.angle.basis is RateBasisChoice.PURSUIT
        label = " and ".join(
            settings.stratifier_label(stratum).lower() for stratum in result.angle.stratify_by
        )
        chart = _bar(
            chart_id=f"deep-research-rate-{'-'.join(str(s) for s in result.angle.stratify_by)}"
            + ("-worked" if pursuit else ""),
            title=(
                f"How often denials are worked, by {label}"
                if pursuit
                else f"Recovery rate by {label}"
            ),
            x_label="population",
            value_label="recovery rate",
            rows=[
                (cell.label, float(cell.rate or 0))
                for cell in sorted(
                    measured, key=lambda c: (-float(c.rate or 0), c.label)
                )
            ],
            unit="ratio",
            annotations=[settings.floor_sentence()],
            # "Recovery rate by payer" is a league table: it is ordered by
            # the measure, and the ordering is published so the renderer
            # can read it down a column.
            ranked=True,
        )
        if chart is not None:
            charts.append(chart)
        if pursuit:
            best = max(measured, key=lambda cell: Decimal(str(cell.rate)))
            findings.append(
                FindingPayload(
                    referent=f"F{next_referent}",
                    title=words.TITLE_WORKED,
                    statement=words.pursuit_statement(
                        label=best.label.capitalize(),
                        rate=Decimal(str(best.rate)),
                        n=best.n,
                    ),
                    values=[
                        FindingValue(name="worked rate", value=float(Decimal(str(best.rate)))),
                        FindingValue(name="denials old enough to tell", value=best.n),
                    ],
                    grade="direct",
                )
            )
            next_referent += 1

    # -- contrasts ----------------------------------------------------------
    contrasts: list[ContrastPayload] = []
    for family in (AngleFamily.PAYER_CONTRAST, AngleFamily.CLASS_CONTRAST):
        for result in grouped.get(family, []):
            payload = _contrast_payload(result, settings)
            if payload is None:
                continue
            contrasts.append(payload)
            if payload.test == "refused":
                warnings.append(f"{WARN_ANGLE_REFUSED}: {payload.implication}")
                continue
            left, right = payload.left, payload.right
            statement = words.contrast_statement(
                subject=result.contrast_subject,
                strong_label=left.label,
                strong_rate=Decimal(str(left.rate)),
                strong_n=left.n,
                weak_label=right.label,
                weak_rate=Decimal(str(right.rate)),
                weak_n=right.n,
                difference=Decimal(str(payload.risk_difference)),
            )
            gap = payload.risk_difference_interval
            values = [
                FindingValue(name="stronger rate", value=float(Decimal(str(left.rate)))),
                FindingValue(name="stronger answered denials", value=left.n),
                FindingValue(name="weaker rate", value=float(Decimal(str(right.rate)))),
                FindingValue(name="weaker answered denials", value=right.n),
                FindingValue(name="gap", value=float(Decimal(str(payload.risk_difference)))),
            ]
            if gap is not None:
                values.extend(
                    [
                        FindingValue(name="smallest the gap could be", value=float(gap.low)),
                        FindingValue(name="largest the gap could be", value=float(gap.high)),
                    ]
                )
            findings.append(
                FindingPayload(
                    referent=f"F{next_referent}",
                    title=payload.title,
                    statement=f"{statement} {payload.implication}".strip(),
                    values=values,
                    grade="direct",
                )
            )
            next_referent += 1
            caveat = words.EXTREMES_CAVEAT
            if result.contrast_note:
                caveat = f"{result.contrast_note} {caveat}"
            warnings.append(f"{WARN_EXTREMES}: {caveat}")
            chart = _bar(
                chart_id=f"deep-research-contrast-{family}",
                title=payload.title,
                x_label="population",
                value_label="recovery rate",
                rows=[
                    (left.label, float(Decimal(str(left.rate)))),
                    (right.label, float(Decimal(str(right.rate)))),
                ],
                unit="ratio",
                annotations=[words.EXTREMES_CAVEAT],
            )
            if chart is not None:
                charts.append(chart)

    # -- timeliness ---------------------------------------------------------
    timeliness: TimelinessCurvePayload | None = None
    for result in grouped.get(AngleFamily.TIMELINESS_CURVE, []):
        timeliness, notes = _timeliness(result, settings)
        if timeliness is None:
            continue
        context_notes.extend(notes)
        measured_bands = [band for band in timeliness.bands if band.cell.rate is not None]
        if len(measured_bands) >= 2 and timeliness.implication:
            fast, slow = measured_bands[0], measured_bands[-1]
            findings.append(
                FindingPayload(
                    referent=f"F{next_referent}",
                    title=words.TITLE_TIMELINESS,
                    statement=timeliness.implication,
                    values=[
                        FindingValue(
                            name="rate when resubmitted fastest",
                            value=float(Decimal(str(fast.cell.rate))),
                        ),
                        FindingValue(
                            name="rate when resubmitted slowest",
                            value=float(Decimal(str(slow.cell.rate))),
                        ),
                    ],
                    grade="direct",
                )
            )
            next_referent += 1
        chart = _ordered_series(
            chart_id="deep-research-timeliness",
            title="Recovery rate by days to resubmission",
            x_label="days to resubmission",
            value_label="recovery rate",
            rows=[
                (band.band, float(Decimal(str(band.cell.rate))))
                for band in measured_bands
            ],
            annotations=[settings.floor_sentence()],
        )
        if chart is not None:
            charts.append(chart)

    # -- the filing deadline ------------------------------------------------
    deadline: DeadlinePayload | None = None
    for result in grouped.get(AngleFamily.DEADLINE_INTERACTION, []):
        deadline, notes = _deadline(result, settings)
        if deadline is None:
            continue
        context_notes.extend(notes)
        if deadline.implication:
            measured_rows = [row for row in deadline.rows if row.cell.rate is not None]
            findings.append(
                FindingPayload(
                    referent=f"F{next_referent}",
                    title=words.TITLE_DEADLINE,
                    statement=deadline.implication,
                    values=[
                        FindingValue(
                            name=f"{row.position_label}, {row.rule_label}",
                            value=float(Decimal(str(row.cell.rate))),
                        )
                        for row in measured_rows
                    ],
                    grade="direct",
                )
            )
            next_referent += 1
        chart = _bar(
            chart_id="deep-research-deadline",
            title="Recovery rate on each side of the filing deadline",
            x_label="filing deadline position",
            value_label="recovery rate",
            rows=[
                (f"{row.position_label} ({row.rule_label})", float(Decimal(str(row.cell.rate))))
                for row in deadline.rows
                if row.cell.rate is not None
            ],
            unit="ratio",
            annotations=[words.DEADLINE_AUTHORITY_NOTE],
        )
        if chart is not None:
            charts.append(chart)

    # -- what the data edge cost -------------------------------------------
    censoring = _censoring(priced.disclosure, settings)
    for statement in censoring.statements:
        warnings.append(f"{WARN_CENSORING}: {statement}")
    disclosures.extend(censoring.statements)

    # -- angles that could not run -----------------------------------------
    for result in results:
        if result.refusal:
            warnings.append(
                f"{WARN_ANGLE_REFUSED}: "
                + words.angle_refused_statement(title=result.title, reason=result.refusal)
            )

    # Context on what each kind of denial usually takes to fix. Mentioned so
    # a reader knows what the work is; never folded into a rate.
    classes = {
        value
        for stratum in priced.strata
        for name, value in stratum.stratum.parts
        if name == "recovery_class"
    }
    for name in sorted(classes):
        note = settings.class_context.get(name)
        if note:
            context_notes.append(note)

    evidence = [
        AngleEvidencePayload(
            family=str(result.angle.family),  # type: ignore[arg-type]
            title=result.title,
            estimator=result.estimator,
            read_fingerprint=rows.read_fingerprint,
            rows_read=rows.rows_read,
            rows_in_scope=(
                result.rates.disclosure.in_denominator if result.rates is not None else 0
            ),
            cells_published=result.cells_published,
            cells_refused=result.cells_refused,
            duration_ms=result.duration_ms,
        )
        for result in results
    ]

    selector = DeepResearchSelector(
        kind=str(population.kind),  # type: ignore[arg-type]
        values=list(population.values),
        label=population_words,
    )
    plan_payload = plan_payload_of(plan, settings)

    report = DeepResearchReport(
        id=run_id,
        research_question=plan.research_question,
        population=selector,
        data_load_label=load_label,
        data_edge_date=rows.as_of,
        created_at=created_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        plan=plan_payload,
        headline=headline,
        strata=strata_rows,
        not_estimable=named,
        thin_populations=thin,
        rates=rate_cells,
        contrasts=contrasts,
        timeliness=timeliness,
        deadline=deadline,
        censoring=censoring,
        findings=findings,
        charts=charts,
        warnings=[],
        narrative="",
        context_notes=list(dict.fromkeys(context_notes)),
        evidence=evidence,
    )
    header = _header(
        settings=settings,
        rows=rows,
        population_words=population_words,
        load_label=load_label,
        open_denials=headline.total_open_denials,
    )
    return ReportDraft(
        report=report,
        warnings=tuple(warnings),
        header=header,
        disclosures=tuple(dict.fromkeys(disclosures)),
    )


def _header(
    *,
    settings: DeepResearchSettings,
    rows: DenialRows,
    population_words: str,
    load_label: str,
    open_denials: int,
) -> ContextHeaderPayload:
    """The one line naming what was read, for the composer and the reader."""
    start: date = settings.earliest_service_date
    return ContextHeaderPayload(
        window_start=start,
        window_end=rows.as_of,
        basis="service",
        filters=[],
        filter_chips=[],
        cohort=population_words,
        cohort_size=open_denials,
        watermark_id=rows.watermark.id,
        display=words.header_display(
            population=population_words,
            floor_sentence=settings.floor_sentence(),
            load=load_label,
        ),
    )
