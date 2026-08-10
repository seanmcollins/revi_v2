"""The shapes a findings set can take: compare, movement, concentration, scalar, trend."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from revi_investigation.application.calculation_glue import (
    CalculationResult,
)
from revi_investigation.application.findings.windows import _PRIOR_SUFFIX, _TIME_BUCKET_PREFIX
from revi_investigation.application.planning import InvestigationPlan, frame_window
from revi_investigation.application.rendering import (
    MONEY_UNIT as _MONEY_UNIT,
)
from revi_investigation.application.rendering import (
    ratio_pct,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame
from revi_kernel.maturity import (
    TERMINAL_BUCKET_MIN_SHARE as _TERMINAL_BUCKET_MIN_SHARE,
)
from revi_kernel.maturity import (
    CensoringKind,
    terminal_bucket_verdict,
)
from revi_kernel.refs import (
    DimensionRef,
    MetricRef,
)
from revi_kernel.scope import TimeWindow


@dataclass(frozen=True, slots=True)
class CompareShape:
    """A compare frame suitable for findings/reconciliation: at least one
    dimension column plus a money measure with its delta."""

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    money_measure: str
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None


def find_primary_compare(
    plan: InvestigationPlan, calculation: CalculationResult
) -> CompareShape | None:
    """The first compare output carrying dimensions and a money measure —
    the findings frame, and the child side of the reconciliation invariant."""
    for step in plan.transforms.steps:
        if step.operator != "compare":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        money = _money_measure(frame)
        if dims and money is not None:
            return CompareShape(
                frame_id=step.id,
                frame=frame,
                dimension_columns=dims,
                money_measure=money,
                window=frame_window(plan, step.id),
            )
    return None


@dataclass(frozen=True, slots=True)
class MovementShape:
    """A compare frame the movement path can publish findings from.

    The generalization of :class:`CompareShape`, which requires a **money**
    measure with a delta. That requirement is a filter on which questions
    can be answered at all: "denial rate by payer for the last 90 days
    compared to the prior 90 days" plans two probes, compares them
    correctly, produces a frame with a payer column, a rate, a prior rate, a
    delta and a percentage change, and publishes **zero findings and a null
    narrative** — no column is money, so the shape is ``None``, and
    ``evaluate`` only falls through to the concentration path when there is
    no compare shape at all.

    A movement is a movement in whatever unit the contract declares.
    ``impact_cents`` is still money-only (a rate is not dollars), and
    reconciliation still requires money (children of a rate do not sum),
    which is why :func:`find_primary_compare` stays as it was.
    """

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    measure: str
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


def find_primary_movement(
    plan: InvestigationPlan, calculation: CalculationResult
) -> MovementShape | None:
    """The first compare output carrying dimensions and any measure delta.

    Money wins when a frame holds several compared measures — a dollar
    movement is what a worklist is built from, and preferring it keeps
    every answer this engine already gave byte-identical. Otherwise the
    first compared metric column is the answer, which is the case that used
    to publish nothing.
    """
    for step in plan.transforms.steps:
        if step.operator != "compare":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        if not dims:
            continue
        compared = _compared_measures(frame)
        if not compared:
            continue
        best = next((name for name in compared if _unit_of(frame, name) == _MONEY_UNIT), None)
        measure = best if best is not None else compared[0]
        return MovementShape(
            frame_id=step.id,
            frame=frame,
            dimension_columns=dims,
            measure=measure,
            unit=_unit_of(frame, measure),
            window=frame_window(plan, step.id),
        )
    return None


def _compared_measures(frame: EvidenceFrame) -> tuple[str, ...]:
    """Metric columns in this frame that carry a ``__delta`` sibling."""
    names = set(frame.schema.names)
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, MetricRef)
        and "__" not in col.name
        and f"{col.name}__delta" in names
    )


@dataclass(frozen=True, slots=True)
class ConcentrationShape:
    """A ranked frame suitable for findings when nothing was compared: at
    least one dimension column plus the measure the rank was taken on."""

    frame_id: str
    frame: EvidenceFrame
    dimension_columns: tuple[str, ...]
    measure: str
    rank_column: str
    share_column: str | None
    is_money: bool
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None


def find_primary_concentration(
    plan: InvestigationPlan, calculation: CalculationResult
) -> ConcentrationShape | None:
    """The first ``rank`` output carrying dimensions and a base measure —
    the findings frame for playbooks that rank rather than compare."""
    for step in plan.transforms.steps:
        if step.operator != "rank":
            continue
        try:
            frame = calculation.frame(step.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        dims = _dimension_columns(frame)
        if not dims:
            continue
        ranked_by = step.arg("by")
        if ranked_by is None:
            continue
        measure = _base_measure(frame, ranked_by)
        if measure is None:
            continue
        rank_column = f"{ranked_by}__rank"
        if rank_column not in frame.schema.names:
            continue
        share_column = f"{measure}__share"
        unit = _unit_of(frame, measure)
        return ConcentrationShape(
            frame_id=step.id,
            frame=frame,
            dimension_columns=dims,
            measure=measure,
            rank_column=rank_column,
            share_column=share_column if share_column in frame.schema.names else None,
            is_money=unit == _MONEY_UNIT,
            unit=unit,
            window=frame_window(plan, step.id),
        )
    return None


@dataclass(frozen=True, slots=True)
class ScalarShape:
    """One ungrouped metric cell: the whole answer to a direct question.

    ``prior_column``/``delta_column``/``pct_column`` are set only when the
    turn carried a comparison and the ``compare`` operator produced them,
    which is what separates "the rate is 5.2%" from "the rate is 5.2%, up
    from 4.9%".
    """

    frame_id: str
    frame: EvidenceFrame
    measure: str
    #: the metric contract's declared unit, as stamped on the frame column
    unit: str | None
    prior_column: str | None
    delta_column: str | None
    pct_column: str | None
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT

    @property
    def compared(self) -> bool:
        return self.prior_column is not None and self.delta_column is not None


def find_scalar_shapes(
    plan: InvestigationPlan, calculation: CalculationResult
) -> tuple[ScalarShape, ...]:
    """Every ungrouped single-row metric cell this plan produced.

    Reads the *final* logical frame for each probe node — the node's
    ``compare`` output when it has one, else the probe frame itself — so a
    scalar with a comparison is described by its movement rather than twice
    by its level. Prior-window twins are skipped: they are an input to the
    comparison, never an answer.

    A frame with more than one row is not a scalar. That is deliberate:
    an ungrouped frame with several rows is a time-bucketed series, which
    is a trend and wants a trend's treatment, not N headline levels.
    """
    compare_of: dict[str, str] = {}
    for step in plan.transforms.steps:
        if step.operator == "compare" and step.inputs:
            compare_of[step.inputs[0]] = step.id

    shapes: list[ScalarShape] = []
    for node in plan.nodes:
        if node.id.endswith(_PRIOR_SUFFIX):
            continue
        frame_id = compare_of.get(node.id, node.id)
        try:
            frame = calculation.frame(frame_id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        if _dimension_columns(frame) or len(frame.rows) != 1:
            continue
        names = set(frame.schema.names)
        for column in frame.schema.columns:
            if not isinstance(column.ref, MetricRef) or "__" in column.name:
                continue
            measure = column.name
            prior = f"{measure}__prior"
            delta = f"{measure}__delta"
            pct = f"{measure}__pct_change"
            compared = prior in names and delta in names
            shapes.append(
                ScalarShape(
                    frame_id=frame_id,
                    frame=frame,
                    measure=measure,
                    unit=column.unit,
                    prior_column=prior if compared else None,
                    delta_column=delta if compared else None,
                    pct_column=pct if pct in names else None,
                    window=frame_window(plan, frame_id),
                )
            )
    return tuple(shapes)


def _dimension_columns(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, DimensionRef) and not col.ref.id.startswith(_TIME_BUCKET_PREFIX)
    )


def _time_bucket_column(frame: EvidenceFrame) -> str | None:
    """The frame's time axis, when it has one (``time_bucket:month``)."""
    for col in frame.schema.columns:
        if isinstance(col.ref, DimensionRef) and col.ref.id.startswith(_TIME_BUCKET_PREFIX):
            return col.name
    return None


@dataclass(frozen=True, slots=True)
class TrendShape:
    """An ungrouped series over time: the answer to "…by month".

    ``find_scalar_shapes`` deliberately refuses a frame with more than one
    row — "an ungrouped frame with several rows is a time-bucketed series,
    which is a trend and wants a trend's treatment". Without a trend shape a
    monthly breakdown either collapses into a single scalar (grain dropped)
    or publishes nothing at all (grain honored). One finding per measure
    states the series as a series: where it started, where it ended, and its
    extremes, each with the bucket it fell in.
    """

    frame_id: str
    frame: EvidenceFrame
    bucket_column: str
    measure: str
    unit: str | None
    #: The window the probe behind this frame actually read, when it
    #: declared one of its own. ``None`` is the investigation window.
    window: TimeWindow | None = None

    @property
    def is_money(self) -> bool:
        return self.unit == _MONEY_UNIT


#: See :data:`revi_kernel.maturity.TERMINAL_BUCKET_MIN_SHARE` — re-exported
#: here because this module's callers have always read it from here.
TERMINAL_BUCKET_MIN_SHARE = _TERMINAL_BUCKET_MIN_SHARE


@dataclass(frozen=True, slots=True)
class TerminalCensoring:
    """A trend's last bucket, and why it cannot close the series."""

    bucket: str
    #: The bucket's own population, and the series median, when the frame
    #: carries a denominator to read them off.
    population: int | None
    median_population: int | None
    reason: str
    warning: str
    #: The bucket key exactly as the frame holds it (``"2026-07-20"``,
    #: not ``"week of 2026-07-20"``). A chart row's ``x`` is built from the
    #: raw key, so publishing only the human label leaves the prose naming a
    #: provisional point while the chart draws a solid line through it.
    bucket_key: str = ""


def terminal_bucket_censoring(
    shape: TrendShape, spec: AnalysisSpec
) -> TerminalCensoring | None:
    """Is this series' last point a measurement, or an artifact of maturity?

    The verdict itself is :func:`revi_kernel.maturity.terminal_bucket_verdict`
    — one rule, in the kernel, because the chart builder must break its line
    at exactly the bucket this sentence calls provisional and the two
    capabilities may not import each other. What is composed here is the
    prose: the reason and the mandatory disclosure that carry it.
    """
    verdict = terminal_bucket_verdict(
        shape.frame, bucket_column=shape.bucket_column, measure=shape.measure
    )
    if verdict is None:
        return None
    bucket_label = _bucket_text(verdict.bucket, verdict.noun)
    noun = verdict.noun
    if verdict.kind is CensoringKind.CALENDAR_PARTIAL:
        assert verdict.covered_through is not None
        reason = (
            f"the {noun} runs to {verdict.covered_through.isoformat()} and this load ends "
            f"{verdict.newest_data_date.isoformat()}, so the bucket holds only part of its "
            "own period."
        )
        return TerminalCensoring(
            bucket=bucket_label,
            population=None,
            median_population=None,
            reason=reason,
            warning=(
                f"adjudication_incomplete: the last point of this series ({bucket_label}) is a "
                f"PARTIAL {noun} — {reason} It is published as provisional and excluded from the "
                "first-to-last movement, the high and the low; a series that terminates on a "
                "partial bucket reports the calendar, not the business."
            ),
            bucket_key=str(verdict.bucket),
        )

    assert verdict.population is not None and verdict.median_population is not None
    share = Decimal(verdict.population) / Decimal(verdict.median_population)
    reason = (
        f"it was computed over {verdict.population:,} adjudicated records against a series "
        f"median of {verdict.median_population:,} ({ratio_pct(share)} of it), so the {noun} is "
        "still settling and the records that have settled are not a random sample of it."
    )
    return TerminalCensoring(
        bucket=bucket_label,
        population=verdict.population,
        median_population=verdict.median_population,
        reason=reason,
        warning=(
            f"adjudication_incomplete: the last point of this series ({bucket_label}) is "
            f"RIGHT-CENSORED — {reason} It is published as provisional and excluded from the "
            "first-to-last movement, the high and the low. A rise that terminates on an "
            "incompletely adjudicated bucket is a data-maturity artifact until that bucket "
            "matures."
        ),
        bucket_key=str(verdict.bucket),
    )


def find_trend_shapes(
    plan: InvestigationPlan, calculation: CalculationResult
) -> tuple[TrendShape, ...]:
    """Every ungrouped multi-row series this plan produced, in plan order."""
    shapes: list[TrendShape] = []
    for node in plan.nodes:
        if node.id.endswith(_PRIOR_SUFFIX):
            continue
        try:
            frame = calculation.frame(node.id)
        except KeyError:  # pragma: no cover - pruned steps never execute
            continue
        bucket = _time_bucket_column(frame)
        if bucket is None or _dimension_columns(frame) or len(frame.rows) < 2:
            continue
        for column in frame.schema.columns:
            if not isinstance(column.ref, MetricRef) or "__" in column.name:
                continue
            shapes.append(
                TrendShape(
                    frame_id=node.id,
                    frame=frame,
                    bucket_column=bucket,
                    measure=column.name,
                    unit=column.unit,
                    window=frame_window(plan, node.id),
                )
            )
    return tuple(shapes)


def _unit_of(frame: EvidenceFrame, name: str) -> str | None:
    for col in frame.schema.columns:
        if col.name == name:
            return col.unit
    return None


def _base_measure(frame: EvidenceFrame, ranked_by: str) -> str | None:
    """The undecorated metric column behind a rank arg (``x__delta`` → ``x``)."""
    names = set(frame.schema.names)
    candidate = ranked_by.split("__", 1)[0]
    if candidate not in names:
        return None
    for col in frame.schema.columns:
        if col.name == candidate and isinstance(col.ref, MetricRef):
            return candidate
    return None


def _money_measure(frame: EvidenceFrame) -> str | None:
    names = set(frame.schema.names)
    for col in frame.schema.columns:
        if (
            isinstance(col.ref, MetricRef)
            and col.unit == _MONEY_UNIT
            and "__" not in col.name
            and f"{col.name}__delta" in names
        ):
            return col.name
    return None


def _as_int(value: Scalar) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def as_number(value: Scalar) -> Decimal | None:
    """Any numeric cell as a Decimal, or ``None`` when there is no number.

    Reading deltas through :func:`_as_int` instead is correct for money
    (integer cents) and silently wrong for everything else: a ratio delta is
    a ``Decimal``, so every row of a compared *rate* reads as "no movement"
    and sorts into the NULL bucket, and the comparison comes back with
    nothing to say.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return Decimal(value)


def _direction(delta: Scalar) -> str | None:
    """"up from" / "down from" / "unchanged from", or ``None`` when the
    delta is not a number and there is therefore no movement to name.

    Read off the delta in whatever numeric type the operator produced —
    money deltas are integer cents, ratio deltas are ``Decimal``. Deciding
    direction from an int-only coercion publishes a *rate* that rose 1.0
    point as "unchanged".
    """
    if delta is None or isinstance(delta, bool) or not isinstance(delta, (int, Decimal)):
        return None
    if delta == 0:
        return "unchanged from"
    return "down from" if delta < 0 else "up from"


def _bucket_noun(frame: EvidenceFrame, column: str) -> str:
    """"month" / "week" / "day", read off the frame's own time-axis ref."""
    for col in frame.schema.columns:
        if col.name == column and isinstance(col.ref, DimensionRef):
            return col.ref.id.removeprefix(_TIME_BUCKET_PREFIX)
    return "period"


def _bucket_text(value: Scalar, noun: str) -> str:
    """One bucket, named the way the bucket is ("2026-02", "week of …").

    A monthly series whose points read "2026-02-01" is stating a day where
    it means a month, which is the same class of imprecision the rest of
    this module exists to avoid — and the first day of a month is exactly
    the value a reader would misread as the measurement date.
    """
    text = str(value)
    if noun == "month" and len(text) >= 7 and text[4] == "-":
        return text[:7]
    if noun == "week":
        return f"week of {text}"
    return text
