"""Executing generalized angles over the governed measure plane.

The recovery domain reads denial rows once and cuts them in memory. Every
other domain — A/R aging, payer behavior, revenue quality, cash — is
already measured by governed metric contracts, so its angles run through
the ordinary probe path: one aggregation or one as-of read per angle,
cache-first, small-cell policy applied, ratios folded by the kernel.

Nothing in this module computes a published figure by hand. Ratios are
folded by the transform operator that owns the contract's declared unit;
intervals and tests come from ``revi_statistics``; suppression is the
executor's own §15 policy, read back off the policed frame so a cache hit
discloses exactly what a cache miss discloses. What this module decides is
*which cells a reader is shown, in what order, and with which marks* — the
same job ``report.py`` does for the recovery domain.

**A bound is a mark, never a number.** A ratio whose numerator was withheld
publishes a ceiling, and the cell says so. A ranking is refused outright
when too much of its field is bounded, by the same rule the conversational
surface uses — one honesty machine, not two.

**A rate is only a rate where BOTH HALVES say so.** A stratified-rate
reading, an interval and a two-proportion test are honest over a
PROPORTION — a counted subset over a counted whole. A mean (days summed
over claims counted) has the same division and no population behind the
numerator, so the shape is refused rather than approximated: see
:func:`is_proportion`, whose second half is the one a live run had to
teach this module.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from revi_calculation_contracts.contract import (
    Count,
    CountDistinct,
    MetricContract,
    MetricKind,
    denominator_column,
    numerator_column,
)
from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.capability_ports import PackPort, TransformPort
from revi_investigation.application.date_basis import (
    BasisResolution,
    resolve_answerable_basis,
    substitution_warning,
)
from revi_investigation.application.deep_research.general import (
    AngleShape,
    MeasureAngle,
    PlannedAngle,
    TimeStep,
)
from revi_investigation.application.deep_research.grammar import TargetPopulation
from revi_investigation.application.execution import (
    apply_small_cell_suppression,
    bound_index,
)
from revi_investigation.application.ports import EvidenceCache
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import ReviError
from revi_kernel.filters import FilterExpr, Predicate, PredicateOp, and_merge
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import AggregationProbe, Ordering, SnapshotProbe, probe_hash
from revi_kernel.refs import DateBasisRef, DimensionRef, Grain, MetricRef, TimeBucket
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_statistics import contrast_counts, wilson_interval
from revi_statistics_contracts.contract import Contrast, EstimationPolicy

#: Past this share of a broken-out reading being bounded, no ordering is
#: published. The same rule the conversational ranking path applies: a
#: league table that silently mixes ceilings with measurements is exactly as
#: misleading as one that drops the ceilings.
MAX_BOUNDED_SHARE = Decimal("0.5")

_BUCKETS: Mapping[TimeStep, TimeBucket] = {
    TimeStep.DAY: TimeBucket.DAY,
    TimeStep.WEEK: TimeBucket.WEEK,
    TimeStep.MONTH: TimeBucket.MONTH,
}


class MeasureAngleRefused(Exception):
    """The angle could not be run honestly over this population."""


@dataclass(frozen=True, slots=True)
class MeasureCell:
    """One cell of a measure-plane reading, with its marks already on it."""

    #: What the reader sees — a code carries the title the pack holds for
    #: it, because a bare ``16`` is not a denial reason.
    label: str
    #: The raw ``(dimension, value)`` pairs, exactly as the data holds them.
    #: A later round narrows on THESE: filtering on the label would send
    #: ``carc = "16 — Missing or invalid information"`` to a column holding
    #: ``16``, and the chase would fail at the source rather than land.
    parts: tuple[tuple[str, str], ...]
    value: Decimal | None
    #: The population the value is over, when the measure is a ratio and the
    #: denominator counts one. ``None`` for an additive measure, where "the
    #: population" is not a thing the number has.
    population: int | None = None
    numerator: int | None = None
    #: True when the true value was withheld and this is the largest it
    #: could have been.
    bounded: bool = False
    #: True when the small-population rule nulled every measure on the row.
    withheld: bool = False
    #: The confidence interval, where the cell is a rate over a counted
    #: population. Never invented for an additive measure.
    interval_low: Decimal | None = None
    interval_high: Decimal | None = None

    @property
    def is_measured(self) -> bool:
        return self.value is not None and not self.bounded and not self.withheld


@dataclass(frozen=True, slots=True)
class MeasureResult:
    """One generalized angle's certified output."""

    angle: PlannedAngle
    metric_id: str
    title: str
    unit: str
    grade: str
    cells: tuple[MeasureCell, ...] = field(default=())
    #: Set on a CONTRAST over rate-like data.
    contrast: Contrast | None = None
    contrast_subject: str = ""
    read_fingerprint: str = ""
    rows_read: int = 0
    cache_hit: bool = False
    duration_ms: int = 0
    #: Ordered by the measure — published so a renderer can read a league
    #: table down a column instead of across a 60px axis.
    ranked: bool = False
    #: Set when the reading could not be ordered honestly.
    ranking_refused: str = ""
    refusal: str | None = None
    notes: tuple[str, ...] = field(default=())
    window: AbsoluteRange | None = None
    basis: str = ""

    @property
    def cells_published(self) -> int:
        return sum(1 for cell in self.cells if cell.is_measured)

    @property
    def cells_refused(self) -> int:
        return sum(1 for cell in self.cells if not cell.is_measured)


# ---------------------------------------------------------------------------
# probe construction


def _scope(population: TargetPopulation, within: Sequence[tuple[str, str]] = ()) -> FilterExpr:
    """The run's population, narrowed by anything this angle holds fixed.

    ``within`` is how a later round goes INSIDE an earlier finding: the
    contrast that separated payers is chased by re-reading the same measure
    with the payer pinned. It is a narrower population, not a different
    analysis, and it compiles as one more predicate rather than one more
    code path.
    """
    clauses: list[FilterExpr] = []
    dimension = population.dimension
    if dimension is not None:
        clauses.append(
            Predicate(
                dimension=DimensionRef(dimension),
                op=PredicateOp.IN,
                values=tuple(population.values),
            )
        )
    for name, value in within:
        clauses.append(
            Predicate(dimension=DimensionRef(name), op=PredicateOp.EQ, values=(value,))
        )
    return and_merge(*clauses) if clauses else and_merge()


def build_probe(
    angle: MeasureAngle,
    shape: AngleShape,
    contract: MetricContract,
    catalog: CatalogSnapshot,
    *,
    population: TargetPopulation,
    window: AbsoluteRange,
    as_of: date,
) -> tuple[AggregationProbe | SnapshotProbe, BasisResolution | None]:
    """The read this angle needs, as a typed probe. No SQL anywhere.

    An as-of measure reads as a snapshot at the data edge; a flow measure
    reads over the window. The choice is the contract's, never the
    planner's — a plan that asked for A/R "over July" would otherwise get a
    number that means nothing, because A/R is a level and July is not a
    level's window.

    The date basis goes through the same answerable-basis rule every other
    layer uses. ``denial_rate`` declares the remittance date primary at
    claim grain and this warehouse binds that date only on the remit views;
    resolving it here rather than trusting the declaration is the
    difference between a labelled substitution and a compiler error after
    the click.
    """
    measures = (MetricRef(angle.metric_id),)
    dimensions = tuple(DimensionRef(cut) for cut in angle.cut_by)
    scope = _scope(population, angle.within)
    if contract.kind is MetricKind.SNAPSHOT:
        return (
            SnapshotProbe(
                measures=measures,
                dimensions=dimensions,
                scope=scope,
                as_of=as_of,
                grain=Grain(contract.entity_grain),
            ),
            None,
        )
    resolution = resolve_answerable_basis(
        contract, DateBasisRef(angle.basis) if angle.basis else None, catalog
    )
    basis = resolution.basis
    bucket = _BUCKETS[angle.step] if angle.step is not None else None
    order_by: tuple[Ordering, ...] = ()
    # Server-side ordering on an additive measure only. A ratio's own column
    # does not exist until the kernel folds it, so ordering by it here would
    # order by a column the adapter never selected.
    if (
        shape in (AngleShape.MEASURE_PROFILE, AngleShape.COMPOSITION)
        and dimensions
        and not bucket
        and contract.denominator is None
    ):
        order_by = (Ordering(by=MetricRef(angle.metric_id), descending=True),)
    return (
        AggregationProbe(
            measures=measures,
            dimensions=dimensions,
            scope=scope,
            window=TimeWindow(basis=basis, range=window),
            grain=Grain(contract.entity_grain, bucket),
            order_by=order_by,
        ),
        resolution,
    )


# ---------------------------------------------------------------------------
# the executor


class MeasureAngleRunner:
    """Runs generalized angles through the ordinary probe path."""

    def __init__(
        self,
        repository: AnalyticalRepository,
        cache: EvidenceCache,
        catalog: CatalogSnapshot,
        pack: PackPort,
        transforms: TransformPort,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._catalog = catalog
        self._pack = pack
        self._transforms = transforms
        self._threshold = catalog.suppression.threshold

    def title(self, planned: PlannedAngle) -> str:
        """What this reading will be called, before it has run.

        Exposed so the loop can name a reading on the wire while it is
        being taken rather than when the report is assembled. A reader
        watching a minute of work is entitled to see the reading being
        measured, and "reading 2 of 4" is a counter where "A/R over 90 by
        payer" is a fact.
        """
        return title_of(planned, self._catalog)

    async def run(
        self,
        planned: PlannedAngle,
        *,
        population: TargetPopulation,
        window: AbsoluteRange,
        as_of: date,
        watermark: object,
        pack_snapshot_id: str,
        policy: EstimationPolicy,
    ) -> MeasureResult:
        """Execute one angle. A refusal comes back as a result, not a raise."""
        angle = planned.measure
        assert angle is not None
        started = time.monotonic()
        contract = self._pack.metric(angle.metric_id)
        title = _title(angle, planned.shape, self._catalog, contract)
        if contract is None:
            return MeasureResult(
                angle=planned,
                metric_id=angle.metric_id,
                title=title,
                unit="",
                grade="unavailable",
                refusal=f"{angle.metric_id} is not a measure in your definitions library",
            )
        try:
            probe, resolution = build_probe(
                angle,
                planned.shape,
                contract,
                self._catalog,
                population=population,
                window=window,
                as_of=as_of,
            )
            frame, cache_hit = await self._read(probe, watermark, pack_snapshot_id)
        except (ReviError, ValueError) as exc:
            return MeasureResult(
                angle=planned,
                metric_id=angle.metric_id,
                title=title,
                unit=str(contract.unit),
                grade="unavailable",
                duration_ms=int((time.monotonic() - started) * 1000),
                refusal=_refusal_words(exc),
            )

        folded = self._fold(frame, contract)
        cells = self._cells(folded, angle, contract)
        ranked, refused_reason = _ordering_verdict(cells, planned.shape)
        contrast, subject = self._contrast(cells, planned, contract, policy)
        notes: list[str] = []
        if resolution is not None:
            substitution = substitution_warning(contract, resolution)
            if substitution is not None:
                notes.append(substitution)
        return MeasureResult(
            angle=planned,
            metric_id=angle.metric_id,
            title=title,
            unit=str(contract.unit),
            grade=str(folded.evidence_grade),
            cells=cells,
            contrast=contrast,
            contrast_subject=subject,
            read_fingerprint=probe_hash(probe),
            rows_read=len(frame.rows),
            cache_hit=cache_hit,
            duration_ms=int((time.monotonic() - started) * 1000),
            ranked=ranked,
            ranking_refused=refused_reason,
            notes=tuple(notes),
            window=window,
            basis=(
                "as of"
                if resolution is None
                else resolution.basis.id
            ),
        )

    # -- internals ---------------------------------------------------------

    async def _read(
        self, probe: AggregationProbe | SnapshotProbe, watermark: object, pack_snapshot_id: str
    ) -> tuple[EvidenceFrame, bool]:
        digest = probe_hash(probe)
        watermark_id = getattr(watermark, "id", str(watermark))
        cached = await self._cache.get(digest, watermark_id, pack_snapshot_id)
        if cached is not None:
            return cached, True
        frame = await self._repository.execute(probe, watermark=watermark)  # type: ignore[arg-type]
        frame = apply_small_cell_suppression(frame, self._threshold)
        await self._cache.put(digest, watermark_id, pack_snapshot_id, frame)
        return frame, False

    def _fold(self, frame: EvidenceFrame, contract: MetricContract) -> EvidenceFrame:
        """Fold a ratio's components into the metric column, or pass through.

        The kernel owns the division and the declared unit; doing it here
        would put a second definition of the same metric in the application
        layer, which is the one place this codebase has repeatedly been
        wrong.
        """
        if contract.denominator is None:
            return frame
        return self._transforms.ratio(
            frame,
            numerator=numerator_column(contract.id),
            denominator=denominator_column(contract.id),
            out=contract.id,
            out_ref=MetricRef(contract.id),
            contract_version=contract.version,
            unit=contract.unit.value,
        )

    def _cells(
        self, frame: EvidenceFrame, angle: MeasureAngle, contract: MetricContract
    ) -> tuple[MeasureCell, ...]:
        positions = {column.name: index for index, column in enumerate(frame.schema.columns)}
        value_at = positions.get(contract.id)
        if value_at is None:
            return ()
        num_at = positions.get(numerator_column(contract.id))
        den_at = positions.get(denominator_column(contract.id))
        bucket_name = angle.step.value if angle.step is not None else None
        group_names = [*angle.cut_by] + ([bucket_name] if bucket_name else [])
        group_at = [(name, positions[name]) for name in group_names if name in positions]
        bounds = bound_index(frame, self._threshold)
        counted = is_proportion(contract)

        cells: list[MeasureCell] = []
        for index, row in enumerate(frame.rows):
            parts = tuple(
                (name, "" if row[at] is None else str(row[at])) for name, at in group_at
            )
            raw = row[value_at]
            value = _decimal(raw)
            numerator = _int(row[num_at]) if num_at is not None else None
            population = _int(row[den_at]) if den_at is not None else None
            bounded = contract.id in bounds.get(index, {})
            withheld = value is None
            low = high = None
            if (
                counted
                and not bounded
                and not withheld
                and numerator is not None
                and population is not None
                and population > 0
            ):
                interval = wilson_interval(numerator, population, Decimal("0.95"))
                low, high = interval.low, interval.high
            cells.append(
                MeasureCell(
                    label=_cell_label(parts, self._catalog, self._pack),
                    parts=parts,
                    value=value,
                    population=population if counted else None,
                    numerator=numerator if counted else None,
                    bounded=bounded,
                    withheld=withheld,
                    interval_low=low,
                    interval_high=high,
                )
            )
        if bucket_name is not None:
            # A trend is read along its axis, and the axis is the date. Any
            # other ordering makes a line that is not a line.
            cells.sort(key=lambda cell: dict(cell.parts).get(bucket_name, ""))
        return tuple(cells[: angle.top_n]) if bucket_name is None else tuple(cells)

    def _contrast(
        self,
        cells: Sequence[MeasureCell],
        planned: PlannedAngle,
        contract: MetricContract,
        policy: EstimationPolicy,
    ) -> tuple[Contrast | None, str]:
        """The strongest cell against the weakest, where the data supports it.

        Only over a ratio whose denominator counts a population: that is
        what makes a two-proportion test meaningful. Over an additive
        measure the gap is real and the test is not, so no test is
        published — the alternative is a p-value about dollars, which reads
        as rigour and is arithmetic on the wrong object.
        """
        if planned.shape is not AngleShape.CONTRAST:
            return None, ""
        if not is_proportion(contract):
            return None, ""
        measured = [cell for cell in cells if cell.is_measured and cell.numerator is not None]
        if len(measured) < 2:
            return None, ""
        measured.sort(key=lambda cell: (-(cell.value or Decimal(0)), -(cell.population or 0), cell.label))
        strong, weak = measured[0], measured[-1]
        assert strong.numerator is not None and strong.population is not None
        assert weak.numerator is not None and weak.population is not None
        subject = ", ".join(
            _dimension_label(name, self._catalog) for name in planned.measure.cut_by  # type: ignore[union-attr]
        )
        return (
            contrast_counts(
                left_label=strong.label,
                left_successes=strong.numerator,
                left_n=strong.population,
                right_label=weak.label,
                right_successes=weak.numerator,
                right_n=weak.population,
                policy=policy,
            ),
            subject,
        )


# ---------------------------------------------------------------------------
# helpers


def is_proportion(contract: MetricContract) -> bool:
    """Is this ratio a PROPORTION — a counted subset over a counted whole?

    The test a stratified-rate, an interval or a two-proportion reading is
    only honest over. ``denial_rate`` counts denied claims over adjudicated
    claims: every numerator is one of the denominator's own members, which
    is what makes "how often" a meaningful question and a Wilson interval a
    statement about something.

    BOTH HALVES ARE CHECKED, and the second half is the one that was
    missing. ``days_in_ar`` divides dollars by dollars-per-day and fails on
    the denominator, which is how it was excluded. ``bill_lag_days`` sums
    DAYS over a count of claims — a MEAN — and its denominator is a
    ``count_distinct``, so a denominator-only rule admitted it: a mean of
    8.0 days over 6,011 claims was handed to the interval estimator as
    "48,377 successes out of 6,011", which raised on the live A/R study
    and, had the mean been under 1, would instead have published a
    confidence interval around an average and counted the reading as
    outcome-like data in the censoring disclosure.
    """
    denominator = contract.denominator
    if denominator is None:
        return False
    under = getattr(denominator, "inner", denominator)
    over = getattr(contract.numerator, "inner", contract.numerator)
    return isinstance(under, (Count, CountDistinct)) and isinstance(
        over, (Count, CountDistinct)
    )


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    return None


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return int(value)


def _dimension_label(dimension_id: str, catalog: CatalogSnapshot) -> str:
    definition = catalog.dimension(dimension_id)
    if definition is not None:
        return definition.label.lower()
    if dimension_id in ("day", "week", "month"):
        return dimension_id
    return dimension_id.replace("_", " ")


#: How each time bucket reads to somebody who is not a database. A default
#: surface never shows ``2026-07-01`` (``docs/client-language.md`` §4), and
#: a trend's own axis is the one place an ISO date is guaranteed to reach
#: one: the bucket column holds the period's first date and nothing else
#: renders it.
_BUCKET_FORMATS: Mapping[str, str] = {
    "month": "%b %Y",
    "week": "week of %b %-d, %Y",
    "day": "%b %-d, %Y",
}


def _period_label(bucket: str, value: str) -> str:
    """One time bucket, as a reader writes a period."""
    try:
        moment = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return moment.strftime(_BUCKET_FORMATS[bucket])


def _cell_label(
    parts: Sequence[tuple[str, str]], catalog: CatalogSnapshot, pack: PackPort
) -> str:
    """A cell named the way the domain names it.

    A bare ``16`` is not a denial reason; ``16 — the title the pack carries``
    is. The pack already holds those titles for the definitional path, and
    reaching for them here is what stops a research report publishing raw
    code values a reader has to look up.

    A time bucket gets the same treatment for the same reason. The column
    holds ``2026-07-01`` because that is what a month IS in the data; a
    reader reads "Jul 2026", and a chart axis or a trend sentence spelling
    the ISO date is this platform's storage format on a default surface.
    """
    if not parts:
        return "everything in this population"
    rendered: list[str] = []
    for name, value in parts:
        if not value:
            rendered.append("(no value on the record)")
            continue
        if name in _BUCKET_FORMATS:
            rendered.append(_period_label(name, value))
            continue
        title = None
        if name in ("carc", "rarc", "group_code"):
            title = pack.code_title(name, value)
        rendered.append(f"{value} — {title}" if title else value)
    return " / ".join(rendered)


def _ordering_verdict(
    cells: Sequence[MeasureCell], shape: AngleShape
) -> tuple[bool, str]:
    """May this reading publish an ordering, and if not, why not."""
    if shape is AngleShape.TREND or len(cells) < 2:
        return False, ""
    publishable = [cell for cell in cells if not cell.withheld]
    if not publishable:
        return False, ""
    bounded = sum(1 for cell in publishable if cell.bounded)
    share = Decimal(bounded) / Decimal(len(publishable))
    if share > MAX_BOUNDED_SHARE:
        return False, (
            f"{bounded} of the {len(publishable)} groups here show a ceiling rather than a "
            "figure, so ordering them would rank ceilings against measurements."
        )
    return True, ""


def _refusal_words(exc: Exception) -> str:
    """A refusal a reader can act on: what could not be done, in their terms."""
    text = " ".join(str(exc).split())
    return text or "this reading could not be taken over this population"


def title_of(planned: PlannedAngle, catalog: CatalogSnapshot) -> str:
    """What a planned reading will be called, before it has run.

    The preview and the report must name a reading identically — a
    confirmation card that said "A/R over 90 by payer" over a report
    section called something else would leave a reader unable to tell
    whether what they approved is what ran. So both go through one
    formatter, and the preview reaches it here rather than composing a
    second name of its own.
    """
    if planned.measure is None:
        return planned.subject.replace("_", " ")
    return _title(planned.measure, planned.shape, catalog, None)


def _title(
    angle: MeasureAngle,
    shape: AngleShape,
    catalog: CatalogSnapshot,
    contract: MetricContract | None,
) -> str:
    """What this angle is called on the report, in sentence case."""
    from revi_investigation.application.rendering import metric_label

    measure = metric_label(angle.metric_id)
    cuts = " and ".join(_dimension_label(cut, catalog) for cut in angle.cut_by)
    # A reading taken INSIDE a population is a different reading, and its
    # title has to say so. Two angles called "days to pay by claim type",
    # one of them inside a single payer, is one title for two populations —
    # the exact ambiguity a chase creates and a report must not inherit.
    inside = (
        " within " + ", ".join(value for _, value in angle.within) if angle.within else ""
    )

    if shape is AngleShape.TREND:
        step = (angle.step or TimeStep.MONTH).value
        return f"{measure} by {step}" + (f", by {cuts}" if cuts else "") + inside
    if shape is AngleShape.CONTRAST:
        head = f"{measure}: the widest gap by {cuts}" if cuts else f"{measure} compared"
        return head + inside
    if shape is AngleShape.COMPOSITION:
        head = f"What makes up {measure.lower()}, by {cuts}" if cuts else measure
        return head + inside
    return (f"{measure} by {cuts}" if cuts else measure) + inside
