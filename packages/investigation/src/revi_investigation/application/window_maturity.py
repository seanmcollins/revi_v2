"""Adjudication maturity of a WINDOW (design §2.8; round-6 E-01).

Two guards already existed and both are *relative to a sibling*:
:func:`revi_kernel.maturity.terminal_bucket_verdict` compares a series'
last bucket against the median of the buckets before it, and
:func:`revi_investigation.application.comparison.comparison_maturity`
compares the two panels of a comparison. Neither can see a **single-window
aggregate**, because it has no sibling bucket and no prior panel — and that
is the shape most questions actually take.

Live, that hole published two answers to one question 6.7x apart. "Silverline
Medicare Advantage: $23,749.29 denied dollars" over July on the *service*
basis, and "$158,122.42" over the same July on the *remit* basis, both at
``confidence: high``, ``grade: direct``, with nothing on either card saying
that service-dated July was about a quarter adjudicated. ``WINDOW_ASSUMED``
steers every undated question straight into that month.

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
from datetime import date
from decimal import Decimal

from revi_calculation_contracts.contract import CountDistinct
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.domain.context import AnalysisSpec
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import ReviError
from revi_kernel.filters import EMPTY_SCOPE
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

__all__ = [
    "WindowMaturity",
    "WindowMaturityService",
    "adjudication_yardstick",
]


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
    July", the exact shape E-01 was raised about — carries no ratio of its
    own to be judged by. A yardstick drawn from the turn would decline
    exactly there.

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


def _month_start(day: date) -> date:
    return date(day.year, day.month, 1)


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
        window = spec.context.window
        yardstick = adjudication_yardstick(
            self._pack, grain=spec.context.grain.entity, basis=window.basis
        )
        if yardstick is None:
            return None
        curve = await self._curve(
            spec.context.watermark, window.basis, spec.context.grain.entity, yardstick
        )
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
            warning=(
                "adjudication_incomplete: this answer's window "
                f"({window.range.start.isoformat()}..{window.range.end.isoformat()}, "
                f"{window.basis.id} basis) has not finished settling. Across this whole load "
                f"it holds {population:,} settled record(s) where a window of this length "
                f"normally holds about {expected:,} — {_share_text(population, expected)} of "
                "it. What HAS settled is not a random sample of what has not: the fastest "
                "cases reach a decision first, so a total measured here is understated and a "
                f"rate measured here is skewed. The count is the one {yardstick!r} declares, "
                "and the norm is the median month of this load. Ask again over the last "
                "settled period, or on a basis whose events are already recorded, to read "
                "this without the caveat."
            ),
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
