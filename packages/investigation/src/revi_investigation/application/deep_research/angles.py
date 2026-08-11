"""Angle executors — the deterministic half of a deep-research run.

One function per family. Each takes the rows read once at the pinned load,
hands them to ``revi_statistics``, and returns typed results. No function
here interprets language, and nothing that interprets language reaches
these functions: the control plane chose the angle, and that is the whole
of its involvement in the number.

**Extremes are named as extremes.** A contrast between the strongest and
the weakest population is a comparison chosen *after* looking at the
rates, so its p-value is a screen rather than a confirmation. That is
stated on the result and travels onto the report rather than being left
for the reader to work out. The alternative — quietly reporting the
max-versus-min gap as a pre-specified test — is the most common way an
honest estimator produces a dishonest paragraph.

**Selection never reads the outcome.** Where an angle must choose a
population to hold fixed, it chooses the LARGEST one, by size, before any
rate is consulted. Choosing the most interesting one would be fishing.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from revi_investigation.application.deep_research import copy as words
from revi_investigation.application.deep_research.grammar import (
    AngleFamily,
    RateBasisChoice,
    ResearchAngle,
    Stratum,
)
from revi_investigation.application.deep_research.policy import (
    DeepResearchSettings,
    drop_unsupported,
    stratifier_of,
)
from revi_investigation.application.deep_research.rows import DenialRows
from revi_statistics import (
    compare_rate_cells,
    delay_effect_curve,
    estimate_durations,
    estimate_rates,
    expected_recovery,
)
from revi_statistics_contracts.contract import (
    Contrast,
    ContrastArm,
    ContrastTest,
    DurationEstimate,
    DurationMeasure,
    EstimationPolicy,
    EvidenceLabel,
    ExpectedRecovery,
    RateBasis,
    RateCell,
    RateEstimate,
    Stratifier,
    StratumKey,
)

_BASES = {
    RateBasisChoice.DECIDED: RateBasis.DECIDED,
    RateBasisChoice.PURSUIT: RateBasis.PURSUIT,
}

#: Said on every extremum contrast, so the reader is never handed a
#: max-versus-min p-value dressed as a pre-specified test. One wording,
#: from the report's own copy — the same fact worded two ways in two places
#: is how a reader concludes they are two facts.
EXTREMES_CAVEAT = words.EXTREMES_CAVEAT


class AngleRefused(Exception):
    """The angle could not be run honestly over this population."""


@dataclass(frozen=True, slots=True)
class AngleResult:
    """One angle's certified output, plus what it took to produce it."""

    angle: ResearchAngle
    title: str
    #: The estimator this angle called, named for the evidence rail.
    estimator: str
    duration_ms: int = 0
    rates: RateEstimate | None = None
    contrast: Contrast | None = None
    #: What the two sides of a contrast are, in the reader's words.
    contrast_subject: str = ""
    contrast_note: str = ""
    #: The two cells a contrast was taken between, kept so the report can
    #: name them in the reader's words rather than in the key the estimator
    #: identifies a population by.
    contrast_cells: tuple[RateCell, RateCell] | None = None
    curve: RateEstimate | None = None
    durations: DurationEstimate | None = None
    expected: ExpectedRecovery | None = None
    #: A population the angle could not run over at all, stated.
    refusal: str | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def cells(self) -> tuple[RateCell, ...]:
        source = self.curve if self.curve is not None else self.rates
        return () if source is None else source.cells

    @property
    def cells_published(self) -> int:
        return sum(1 for cell in self.cells if cell.is_measured)

    @property
    def cells_refused(self) -> int:
        return sum(1 for cell in self.cells if not cell.is_measured)


# ---------------------------------------------------------------------------
# helpers


def _strata(angle: ResearchAngle, settings: DeepResearchSettings) -> tuple[Stratifier, ...]:
    kept = drop_unsupported(angle.stratify_by, settings)
    return tuple(stratifier_of(stratum) for stratum in kept)


def _within(angle: ResearchAngle, settings: DeepResearchSettings) -> tuple[Stratifier, ...]:
    kept = drop_unsupported(angle.within, settings)
    return tuple(stratifier_of(stratum) for stratum in kept)


def _measured(cells: Sequence[RateCell]) -> list[RateCell]:
    """Measured cells, ordered strongest first, deterministically.

    Ties break on the larger population, then on the label: two payers at
    the same rate must land in the same order on every run, and the one
    measured over more denials is the more informative end of the range.
    """
    ranked = [cell for cell in cells if cell.is_measured and cell.rate is not None]
    ranked.sort(key=lambda cell: (-(cell.rate or 0), -cell.n, cell.stratum.label))
    return ranked


def _refused_contrast(reason: str, policy: EstimationPolicy) -> Contrast:
    empty = ContrastArm(label="", n=0, successes=0, rate=None, interval=None)
    return Contrast(
        left=empty,
        right=empty,
        test=ContrastTest.REFUSED,
        min_cohort=policy.min_cohort,
        refusal_reason=reason,
    )


def _largest_value(cells: Sequence[RateCell], stratifier: Stratifier) -> str | None:
    """The value of ``stratifier`` holding the most answered denials.

    Chosen on size alone, before any rate is read — the population an
    extremum contrast is held inside must not be chosen for its effect.
    """
    totals: dict[str, int] = {}
    for cell in cells:
        value = cell.stratum.value_of(stratifier)
        if value is None:
            continue
        totals[value] = totals.get(value, 0) + cell.n
    if not totals:
        return None
    return max(sorted(totals), key=lambda value: totals[value])


# ---------------------------------------------------------------------------
# executors


def run_outcome_by_stratum(
    angle: ResearchAngle,
    rows: DenialRows,
    *,
    settings: DeepResearchSettings,
    policy: EstimationPolicy,
) -> AngleResult:
    """Recovery rate by population, with an interval and a size per cell."""
    started = time.monotonic()
    strata = _strata(angle, settings)
    if not strata:
        raise AngleRefused("this cut needs band edges the content does not define")
    basis = _BASES[angle.basis]
    estimate = estimate_rates(
        rows.rows, basis=basis, stratify_by=strata, policy=policy, as_of=rows.as_of
    )
    return AngleResult(
        angle=angle,
        title=settings.angle(str(angle.family)).title,
        estimator=f"estimate_rates[{basis}]",
        duration_ms=int((time.monotonic() - started) * 1000),
        rates=estimate,
    )


def _contrast_over(
    angle: ResearchAngle,
    rows: DenialRows,
    *,
    axis: Stratifier,
    held: Stratifier | None,
    settings: DeepResearchSettings,
    policy: EstimationPolicy,
    subject: str,
) -> AngleResult:
    started = time.monotonic()
    stratifiers: tuple[Stratifier, ...] = (axis,) if held is None else (held, axis)
    estimate = estimate_rates(
        rows.rows,
        basis=RateBasis.DECIDED,
        stratify_by=stratifiers,
        policy=policy,
        as_of=rows.as_of,
    )
    cells = list(estimate.cells)
    note = ""
    if held is not None:
        chosen = _largest_value(cells, held)
        if chosen is None:
            cells = []
        else:
            cells = [cell for cell in cells if cell.stratum.value_of(held) == chosen]
            note = words.held_inside_statement(
                label=settings.value_label(str(held), chosen),
                population=settings.stratifier_label(str(held)),
            )
    ranked = _measured(cells)
    duration = int((time.monotonic() - started) * 1000)
    if len(ranked) < 2:
        floor = settings.floor_sentence()
        return AngleResult(
            angle=angle,
            title=settings.angle(str(angle.family)).title,
            estimator="compare_rate_cells",
            duration_ms=duration,
            rates=estimate,
            contrast=_refused_contrast(
                f"Fewer than two of these populations have enough answered denials "
                f"to compare. {floor}",
                policy,
            ),
            contrast_subject=subject,
            contrast_note=note,
        )
    strong, weak = ranked[0], ranked[-1]
    return AngleResult(
        angle=angle,
        title=settings.angle(str(angle.family)).title,
        estimator="compare_rate_cells",
        duration_ms=duration,
        rates=estimate,
        contrast=compare_rate_cells(strong, weak, policy=policy),
        contrast_cells=(strong, weak),
        contrast_subject=subject,
        contrast_note=note,
        notes=(EXTREMES_CAVEAT,),
    )


def run_payer_contrast(
    angle: ResearchAngle,
    rows: DenialRows,
    *,
    settings: DeepResearchSettings,
    policy: EstimationPolicy,
) -> AngleResult:
    held = _within(angle, settings)
    return _contrast_over(
        angle,
        rows,
        axis=Stratifier.PAYER,
        held=held[0] if held else None,
        settings=settings,
        policy=policy,
        subject=settings.stratifier_label(Stratum.PAYER),
    )


def run_class_contrast(
    angle: ResearchAngle,
    rows: DenialRows,
    *,
    settings: DeepResearchSettings,
    policy: EstimationPolicy,
) -> AngleResult:
    held = _within(angle, settings)
    return _contrast_over(
        angle,
        rows,
        axis=Stratifier.RECOVERY_CLASS,
        held=held[0] if held else None,
        settings=settings,
        policy=policy,
        subject=settings.stratifier_label(Stratum.RECOVERY_CLASS),
    )


def run_timeliness_curve(
    angle: ResearchAngle,
    rows: DenialRows,
    *,
    settings: DeepResearchSettings,
    policy: EstimationPolicy,
) -> AngleResult:
    """Recovery rate by days-to-resubmission, in band order.

    The median time each denial type takes to go back out rides along: a
    curve says what delay costs, and the durations say where the delay
    currently is.
    """
    started = time.monotonic()
    if not policy.delay_bands:
        raise AngleRefused("the content defines no delay bands to read a curve along")
    within = _within(angle, settings)
    curve = delay_effect_curve(
        rows.rows, policy=policy, as_of=rows.as_of, within=within
    )
    durations = estimate_durations(
        rows.rows,
        measure=DurationMeasure.DAYS_TO_RESUBMISSION,
        stratify_by=(Stratifier.RECOVERY_CLASS,),
        policy=policy,
        as_of=rows.as_of,
    )
    return AngleResult(
        angle=angle,
        title=settings.angle(str(angle.family)).title,
        estimator="delay_effect_curve + estimate_durations",
        duration_ms=int((time.monotonic() - started) * 1000),
        curve=curve,
        durations=durations,
    )


def run_deadline_interaction(
    angle: ResearchAngle,
    rows: DenialRows,
    *,
    settings: DeepResearchSettings,
    policy: EstimationPolicy,
) -> AngleResult:
    """The rate on each side of the filing deadline, split by the limit's standing."""
    started = time.monotonic()
    estimate = estimate_rates(
        rows.rows,
        basis=RateBasis.DECIDED,
        stratify_by=(Stratifier.FILING_POSITION, Stratifier.FILING_RULE),
        policy=policy,
        as_of=rows.as_of,
    )
    return AngleResult(
        angle=angle,
        title=settings.angle(str(angle.family)).title,
        estimator="estimate_rates[filing position]",
        duration_ms=int((time.monotonic() - started) * 1000),
        rates=estimate,
    )


def run_expected_recovery(
    angle: ResearchAngle,
    rows: DenialRows,
    *,
    settings: DeepResearchSettings,
    policy: EstimationPolicy,
) -> AngleResult:
    """Price the open denials against each population's own measured rate.

    The rate evidence comes from the whole read; the dollars come from the
    denials still open. A population whose own history could not support a
    rate is priced at nothing and listed at full value — never at a pooled
    rate, a neighbour's rate, or an industry figure.
    """
    started = time.monotonic()
    strata = _strata(angle, settings)
    if not strata:
        raise AngleRefused("this cut needs band edges the content does not define")
    evidence = estimate_rates(
        rows.rows,
        basis=RateBasis.DECIDED,
        stratify_by=strata,
        policy=policy,
        as_of=rows.as_of,
    )
    priced = expected_recovery(
        rows.open_rows,
        rates=evidence,
        stratify_by=strata,
        policy=policy,
        as_of=rows.as_of,
    )
    return AngleResult(
        angle=angle,
        title=settings.angle(str(angle.family)).title,
        estimator="estimate_rates[decided] + expected_recovery",
        duration_ms=int((time.monotonic() - started) * 1000),
        rates=evidence,
        expected=priced,
    )


_EXECUTORS = {
    AngleFamily.OUTCOME_BY_STRATUM: run_outcome_by_stratum,
    AngleFamily.PAYER_CONTRAST: run_payer_contrast,
    AngleFamily.CLASS_CONTRAST: run_class_contrast,
    AngleFamily.TIMELINESS_CURVE: run_timeliness_curve,
    AngleFamily.DEADLINE_INTERACTION: run_deadline_interaction,
    AngleFamily.EXPECTED_RECOVERY: run_expected_recovery,
}


def run_angle(
    angle: ResearchAngle,
    rows: DenialRows,
    *,
    settings: DeepResearchSettings,
    policy: EstimationPolicy,
) -> AngleResult:
    """Execute one angle. A refusal comes back as a result, not an exception."""
    executor = _EXECUTORS[angle.family]
    try:
        return executor(angle, rows, settings=settings, policy=policy)
    except AngleRefused as refusal:
        return AngleResult(
            angle=angle,
            title=settings.angle(str(angle.family)).title,
            estimator="none",
            refusal=str(refusal),
        )


def unmeasured(estimate: RateEstimate | None) -> tuple[StratumKey, ...]:
    """Populations an estimate refused to publish a rate for."""
    if estimate is None:
        return ()
    return tuple(
        cell.stratum for cell in estimate.cells if cell.evidence is EvidenceLabel.REFUSED_THIN
    )
