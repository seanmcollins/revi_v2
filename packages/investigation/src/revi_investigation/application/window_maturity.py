"""Adjudication maturity of a WINDOW (design §2.8).

The other two guards are *relative to a sibling*:
:func:`revi_kernel.maturity.terminal_bucket_verdict` compares a series'
last bucket against the median of the buckets before it, and
:func:`revi_investigation.application.comparison.comparison_maturity`
compares the two panels of a comparison. Neither can see a **single-window
aggregate**, because it has no sibling bucket and no prior panel — and that
is the shape most questions actually take.

That hole publishes two answers to one question 6.7x apart: "$23,749.29
denied dollars" over a month on the *service* basis and "$158,122.42" over
the same month on the *remit* basis, both at ``confidence: high``,
``grade: direct``, with nothing on either card saying that the service-dated
month was about a quarter adjudicated. ``WINDOW_ASSUMED`` steers every
undated question straight into that month.

So maturity is asked of the window itself, against the load's own settling
curve:

* the yardstick is GOVERNED content — a ratio contract whose denominator is
  a distinct count of the entity and which declares an exclusion, i.e. a
  denominator that counts only records the source has finished with. In the
  base pack that exclusion is ``status = OPEN``, "not adjudicated yet";
* the curve is ONE aggregation read, bucketed by month over the whole load,
  cached per (watermark, basis, entity) — a session pays for it once;
* the verdict compares what the window HOLDS against what a settled window
  of that length holds (the median month, pro-rated for partial months).
  Below :data:`revi_kernel.maturity.TERMINAL_BUCKET_MIN_SHARE` of it, the
  window is still settling and the answer says so.

The read is deliberately outside the investigation plan: a probe added to
the plan would change every ``plan_hash`` in the product to ask a question
about data maturity, and the plan is the analyst's, not the guard's. This
is the same shape ``PlanValidationService.resolve_predicate_values`` already
uses for value existence.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from revi_calculation_contracts.contract import CountDistinct
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.rendering import metric_label
from revi_investigation.domain.context import AnalysisSpec
from revi_investigation_contracts.header import basis_phrase
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import ReviError
from revi_kernel.filters import EMPTY_SCOPE, Scalar
from revi_kernel.frame import EvidenceFrame
from revi_kernel.maturity import (
    DENOMINATOR_SUFFIX,
    MIN_SERIES_POINTS,
    TERMINAL_BUCKET_MIN_SHARE,
    median,
    time_bucket_column,
)
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import DateBasisRef, EntityGrain, Grain, MetricRef, TimeBucket
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark

#: How far back the settling curve is read when the load does not publish an
#: oldest data date. Wide on purpose: the curve's job is to establish what a
#: SETTLED month looks like, and a short lookback would take its norm from
#: months that are themselves still settling.
_CURVE_YEARS = 3

#: A trailing month the load reaches only this far into is a stub, not a
#: settling one — excluded from the curve so it cannot drag the norm down.
_STUB_MONTH_DAYS = 3

#: The anatomy column a repository returns for a measure's numerator.
#: Its twin, ``__den``, is :data:`revi_kernel.maturity.DENOMINATOR_SUFFIX`.
_NUMERATOR_SUFFIX = "__num"

#: How far back :meth:`WindowMaturityService.settled_reading` will walk to
#: find a period it can speak for. Three months: past that the "settled
#: reading" is old enough that leading with it would answer a different
#: question, and saying nothing is the better failure.
_SETTLED_LOOKBACK = 3


def _measured(value: Scalar) -> Decimal | int | None:
    """One cell as a MEASUREMENT, or ``None`` when it is not one.

    A frame column carries the whole ``Scalar`` vocabulary — text, dates
    and flags included — and a settled reading is arithmetic over it. A
    date in a measure column is not a small number, it is not a number at
    all, and reading one as a value published a ``date`` where every
    caller formats a quantity. ``bool`` is refused ahead of ``int``
    because Python says ``True`` is 1 while a flag column is still not a
    measurement.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return value

__all__ = [
    "SettledReading",
    "WindowMaturity",
    "WindowMaturityService",
    "adjudication_yardstick",
]


@dataclass(frozen=True, slots=True)
class SettledReading:
    """One measure, over the latest period that has finished adjudicating."""

    window: AbsoluteRange
    measure: str
    value: Decimal | int
    unit: str | None


@dataclass(frozen=True, slots=True)
class WindowMaturity:
    """One window that has not finished adjudicating."""

    #: The governed contract whose denominator counted the population.
    yardstick: str
    #: Records in this window the source has finished with.
    population: int
    #: What a settled window of this length holds, from the load's own curve.
    expected: int
    #: ``start..end`` of the window judged.
    window: AbsoluteRange
    warning: str

    @property
    def share(self) -> Decimal:
        """How much of a settled window's population this one holds."""
        if self.expected <= 0:  # pragma: no cover - guarded at construction
            return Decimal(1)
        return Decimal(self.population) / Decimal(self.expected)


def adjudication_yardstick(
    pack: PackPort, *, grain: EntityGrain, basis: DateBasisRef
) -> str | None:
    """The governed contract whose denominator counts settled records.

    Chosen by a stated rule rather than named in code: a ratio contract at
    this grain, readable on this basis, whose denominator is a distinct
    count of the entity and which declares an exclusion — the exclusion is
    what makes the denominator a count of what the source has FINISHED with
    rather than of everything that exists. Deterministic (lowest id) so two
    runs of one question judge maturity the same way, and named in the
    warning so a reader can check what the share was measured with.

    Searched across the whole PACK, never across the turn's own measures:
    settling is a property of the load, and a question that reads one
    additive money contract — "how many dollars did we lose to denials in
    July" — carries no ratio of its own to be judged by, and a yardstick
    drawn from the turn would decline exactly there.

    ``None`` when the pack declares no such contract, which is the honest
    outcome: a load whose completeness cannot be measured is not one this
    guard may make claims about.
    """
    candidates: list[str] = []
    for metric_id, _description in pack.metric_summaries():
        contract = pack.metric(metric_id)
        if contract is None:
            continue
        if contract.entity_grain is not grain or not contract.is_ratio:
            continue
        if contract.exclusions is None:
            continue
        if not isinstance(contract.denominator, CountDistinct):
            continue
        if not contract.allows_date_basis(basis):
            continue
        candidates.append(metric_id)
    return sorted(candidates)[0] if candidates else None


def _share_text(population: int, expected: int) -> str:
    return f"{Decimal(population) / Decimal(expected):.1%}"


#: Month names spelled out rather than taken through the process locale, for
#: the same reason the chart axis spells them: a caveat reading "juil. 2026"
#: on one deployment is not a sentence a reader can match against the header.
_MONTH_NAME = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _readable_range(window: AbsoluteRange) -> str:
    """A window as a reader says it — "July 2026", or its two edges.

    Never an ISO pair on a default surface (docs/client-language.md §4), and
    never a month NAME for a range that is not one whole month: rounding
    2026-06-08..2026-08-02 to "June" would be the misattribution this
    module's warnings exist to prevent.
    """
    last = monthrange(window.end.year, window.end.month)[1]
    same_month = (window.start.year, window.start.month) == (
        window.end.year,
        window.end.month,
    )
    if same_month and window.start.day == 1 and window.end.day == last:
        return f"{_MONTH_NAME[window.start.month - 1]} {window.start.year}"
    left = f"{_MONTH_NAME[window.start.month - 1][:3]} {window.start.day}"
    if window.start.year != window.end.year:
        left = f"{left}, {window.start.year}"
    right = f"{_MONTH_NAME[window.end.month - 1][:3]} {window.end.day}, {window.end.year}"
    return f"{left} — {right}"


def _month_start(day: date) -> date:
    return date(day.year, day.month, 1)


def covered_months(window: AbsoluteRange) -> tuple[AbsoluteRange, ...]:
    """Every whole calendar month this range touches, as its own range.

    A window wider than a month blends settled and settling data, and the
    blend hides the part that matters: 2026-06-08..2026-08-02 holds a fully
    settled June, a July that is a quarter adjudicated and two days of
    August, and judged as one span it passes. Judged month by month, the
    July inside it is what makes a delta across that window a settlement
    artifact.
    """
    out: list[AbsoluteRange] = []
    cursor = _month_start(window.start)
    while cursor <= window.end:
        last = monthrange(cursor.year, cursor.month)[1]
        out.append(AbsoluteRange(start=cursor, end=date(cursor.year, cursor.month, last)))
        cursor = _month_start(date(cursor.year, cursor.month, last) + timedelta(days=1))
    return tuple(out)


def _covered_share(bucket: date, window: AbsoluteRange) -> Decimal:
    """How much of one month the window covers, as a fraction of its days.

    Pro-rated rather than counted whole, so "the last 30 days" is judged
    against 30 days of a settled month rather than against two whole ones.
    """
    length = monthrange(bucket.year, bucket.month)[1]
    start = max(bucket, window.start)
    end = min(date(bucket.year, bucket.month, length), window.end)
    if end < start:
        return Decimal(0)
    return Decimal((end - start).days + 1) / Decimal(length)


class WindowMaturityService:
    """The load's settling curve, and what it says about one window."""

    def __init__(
        self,
        repository: AnalyticalRepository,
        pack: PackPort,
        *,
        min_share: Decimal = TERMINAL_BUCKET_MIN_SHARE,
    ) -> None:
        self._repository = repository
        self._pack = pack
        self._min_share = min_share
        self._curves: dict[
            tuple[str, str, str, str], tuple[tuple[date, int], ...] | None
        ] = {}

    async def verdict(self, spec: AnalysisSpec) -> WindowMaturity | None:
        """Is this spec's window still settling? ``None`` when it is not —
        or when this load gives no honest way to tell."""
        return await self.verdict_for(
            spec.context.window,
            grain=spec.context.grain.entity,
            watermark=spec.context.watermark,
        )

    async def verdict_for(
        self,
        window: TimeWindow,
        *,
        grain: EntityGrain,
        watermark: DataWatermark,
    ) -> WindowMaturity | None:
        """…asked of ANY window, not only the one the header announced.

        A playbook probe declares its own window, so a guard that reads only
        the spec judges a window nothing was computed over and stays silent
        on the one that was — the premise verdict checked over
        2026-06-08..2026-08-02 while the turn announces July against June.
        The caller collects the windows a plan actually reads and asks about
        each; the settling curve behind them is read once per (watermark,
        basis, entity, yardstick) whatever they are.
        """
        yardstick = adjudication_yardstick(self._pack, grain=grain, basis=window.basis)
        if yardstick is None:
            return None
        curve = await self._curve(watermark, window.basis, grain, yardstick)
        if curve is None:
            return None
        covered = [
            (count, covered_share)
            for bucket, count in curve
            if (covered_share := _covered_share(bucket, window.range)) > 0
        ]
        if not covered:
            return None
        first_covered = next(b for b, _ in curve if _covered_share(b, window.range) > 0)
        settled = [count for bucket, count in curve if bucket < first_covered]
        if len(settled) < MIN_SERIES_POINTS:
            return None
        norm = median(settled)
        if norm <= 0:
            return None
        population = sum(count for count, _ in covered)
        expected = int(sum(Decimal(norm) * share for _, share in covered))
        if expected <= 0:
            return None
        if Decimal(population) >= self._min_share * Decimal(expected):
            return None
        return WindowMaturity(
            yardstick=yardstick,
            population=population,
            expected=expected,
            window=window.range,
            # The FIRST sentence carries the whole caveat: the period, how
            # much of it has settled, and what that does to a total and to
            # a rate. It is written that way because the disclosure layer
            # publishes that sentence above the answer and leaves the rest
            # to the caution banners — this paragraph led twelve live
            # answers, and cost one of them five sentences before "12.8%".
            warning=(
                "adjudication_incomplete: only "
                f"{_share_text(population, expected)} of "
                f"{_readable_range(window.range)} has settled "
                f"{basis_phrase(window.basis.id)}, so a total here is understated and a rate "
                "here is skewed. What HAS settled is not a random sample of what has not: the "
                "fastest cases reach a decision first. This whole load holds "
                f"{population:,} settled record(s) over a window of this length where it "
                f"normally holds about {expected:,}; the count is the one "
                f"{metric_label(yardstick)} declares, and the norm is the median month of this "
                "load. Ask again over the last settled period, or on a basis whose events are "
                "already recorded, to read this without the caveat."
            ),
        )

    async def settled_reading(
        self,
        spec: AnalysisSpec,
        *,
        measure: str,
        suppression_threshold: int | None = None,
        max_steps: int = _SETTLED_LOOKBACK,
    ) -> SettledReading | None:
        """The same measure over the most recent period that HAS settled.

        An undated "what is my denial rate?" assumes the newest month and
        answers "12.8%" — the one figure this product's own trend answer
        excludes as provisional (1,544 records against a series median of
        6,051) — with every caveat underneath it, while the settled book
        runs 7.2-9.1%.

        Naming the caveat is not enough when the number itself is the
        wrong one to lead with, so the settled reading is MEASURED and put
        in front of it: "Through June, 9.1%; July reads 12.8% but only 26%
        of it has adjudicated." One aggregation, outside the plan for the
        same reason the curve is (the plan is the analyst's), over the
        analyst's own scope so the two figures describe one population.

        ``None`` whenever the honest answer is silence: no settled period
        within the lookback, a source that will not serve it, or a frame
        with no value in it.
        """
        window = spec.context.window
        entity = spec.context.grain.entity
        candidate = _month_start(window.range.start)
        for _ in range(max_steps):
            candidate = _month_start(candidate - timedelta(days=1))
            settled_window = TimeWindow(
                basis=window.basis,
                range=AbsoluteRange(
                    start=candidate,
                    end=date(
                        candidate.year,
                        candidate.month,
                        monthrange(candidate.year, candidate.month)[1],
                    ),
                ),
                calendar=window.calendar,
            )
            if settled_window.range.end > spec.context.watermark.newest_data_date:
                continue
            if (
                await self.verdict_for(
                    settled_window, grain=entity, watermark=spec.context.watermark
                )
                is not None
            ):
                continue  # that one is still settling too — keep walking back
            probe = AggregationProbe(
                measures=(MetricRef(measure),),
                dimensions=(),
                scope=spec.context.scope,
                window=settled_window,
                grain=Grain(entity),
            )
            try:
                frame = await self._repository.execute(
                    probe, watermark=spec.context.watermark
                )
            except ReviError:
                # Any refusal — the source is down, the grain does not take
                # this measure — is evidence of nothing, and this method
                # says nothing rather than guessing.
                return None
            return self._reading_from(frame, measure, settled_window, suppression_threshold)
        return None

    def _reading_from(
        self,
        frame: EvidenceFrame,
        measure: str,
        window: TimeWindow,
        suppression_threshold: int | None,
    ) -> SettledReading | None:
        """One measure's value off a repository frame, ratios composed.

        A repository returns a metric's ANATOMY — ``<m>__num`` and
        ``<m>__den`` — and the ratio itself is the kernel's arithmetic, not
        the warehouse's. Reading only a ``<m>`` column therefore found
        nothing on every ratio in the pack, which is every metric this
        sentence is worth saying about.

        The §15 policy is applied here too, on the same threshold the
        turn's own frames were policed under: a settled reading over a
        handful of claims is exactly the small cell the rest of the engine
        withholds, and it may not slip in through a caveat.
        """
        if not frame.rows:
            return None
        row = frame.rows[0]
        names = frame.schema.names
        contract = self._pack.metric(measure)
        unit = str(contract.unit) if contract is not None else None
        if measure in names:
            value = _measured(row[frame.schema.index_of(measure)])
            if value is None:
                return None
            column_unit = frame.schema.columns[frame.schema.index_of(measure)].unit
            return SettledReading(
                window=window.range, measure=measure, value=value, unit=column_unit or unit
            )
        numerator = f"{measure}{_NUMERATOR_SUFFIX}"
        denominator = f"{measure}{DENOMINATOR_SUFFIX}"
        if numerator not in names:
            return None
        num = _measured(row[frame.schema.index_of(numerator)])
        if num is None:
            return None
        if denominator not in names:  # an additive measure has no divisor
            return SettledReading(window=window.range, measure=measure, value=num, unit=unit)
        den = _measured(row[frame.schema.index_of(denominator)])
        if den is None or den <= 0:
            return None
        if suppression_threshold is not None and den < suppression_threshold:
            return None
        return SettledReading(
            window=window.range,
            measure=measure,
            value=Decimal(num) / Decimal(den),
            unit=unit,
        )

    async def _curve(
        self,
        watermark: DataWatermark,
        basis: DateBasisRef,
        entity: EntityGrain,
        yardstick: str,
    ) -> tuple[tuple[date, int], ...] | None:
        # The yardstick is part of the key because it is part of the READ:
        # two turns at one grain and basis can select different contracts,
        # and reusing the first curve under the second's name would publish
        # a share the named contract never produced.
        key = (watermark.id, basis.id, entity.value, yardstick)
        if key in self._curves:
            return self._curves[key]
        curve = await self._read_curve(watermark, basis, entity, yardstick)
        self._curves[key] = curve
        return curve

    async def _read_curve(
        self,
        watermark: DataWatermark,
        basis: DateBasisRef,
        entity: EntityGrain,
        yardstick: str,
    ) -> tuple[tuple[date, int], ...] | None:
        end = watermark.newest_data_date
        floor = watermark.oldest_data_date
        start = date(end.year - _CURVE_YEARS, 1, 1)
        probe = AggregationProbe(
            measures=(MetricRef(yardstick),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            window=TimeWindow(
                basis=basis,
                range=AbsoluteRange(
                    start=max(start, floor) if floor is not None else start, end=end
                ),
            ),
            grain=Grain(entity, TimeBucket.MONTH),
        )
        try:
            frame = await self._repository.execute(probe, watermark=watermark)
        except ReviError:
            # A source that cannot serve the curve is not evidence of an
            # immature window; it is evidence of nothing, and this guard
            # stays silent rather than guessing.
            return None
        column = f"{yardstick}{DENOMINATOR_SUFFIX}"
        # By REF, never by the alias a connector happens to choose: the
        # frame column's ``DimensionRef("time_bucket:month")`` is the
        # contract, and matching the literal "month" would disable this
        # guard silently against any other connector.
        bucket_column = time_bucket_column(frame)
        if bucket_column is None or column not in frame.schema.names:
            return None
        idx_bucket, idx_count = (
            frame.schema.index_of(bucket_column),
            frame.schema.index_of(column),
        )
        out: list[tuple[date, int]] = []
        for row in frame.rows:
            bucket, count = row[idx_bucket], row[idx_count]
            if isinstance(count, bool) or not isinstance(count, int):
                continue
            if isinstance(bucket, date):
                out.append((_month_start(bucket), count))
            elif isinstance(bucket, str):
                try:
                    out.append((_month_start(date.fromisoformat(bucket[:10])), count))
                except ValueError:
                    continue
        # A trailing bucket the load barely reaches is a calendar artifact,
        # not a settling one; the window judged against the curve is what
        # this guard is about, and a stub month would drag the norm down.
        if watermark.newest_data_date.day <= _STUB_MONTH_DAYS:
            cutoff = _month_start(watermark.newest_data_date)
            out = [entry for entry in out if entry[0] < cutoff]
        return tuple(sorted(out)) or None
