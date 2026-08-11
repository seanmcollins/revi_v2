"""Reading the denial history a run estimates over — once, at the pinned load.

Deep research is the first analysis in this platform that needs denials one
at a time rather than pre-aggregated. A median is not a sum, a Wilson
interval is not a ratio of two totals, and pricing an open population means
walking its rows against their own filing deadlines. So this module reads
row evidence through the ordinary analytical port — the same port every
probe uses, the same pinned load, the same fingerprint recorded on the
result — and turns it into the estimator's typed input.

Three properties are load-bearing.

**One read serves every angle.** Angles differ in how they cut the rows,
not in which rows they see. Reading once and cutting in memory is what
makes an eight-angle run cost one query, and it is also what makes the
angles consistent with each other: two angles disagreeing about the size of
the population would be a defect no reader could diagnose.

**A truncated read is refused, never published.** Row evidence is sampled
by construction. A sample is the right shape for showing examples and the
wrong shape for a measurement, so the read asks for a ceiling well above
any real population and refuses outright if the ceiling binds. An estimate
over an unstated sample is the one number in a report nobody could catch.

**The filing rule is an input.** Whether a plan's filing limit is stated
without a confirmation caveat or is only a planning default changes what
crossing the deadline means. The estimator will not guess it and neither
will this module: the caller supplies the judgement, from governed content.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from revi_investigation.application.deep_research.grammar import TargetPopulation
from revi_investigation.application.deep_research.policy import DeepResearchSettings
from revi_investigation.application.ports import EvidenceCache
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import QueryBudgetExceededError
from revi_kernel.filters import FilterExpr, Predicate, PredicateOp, and_merge
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import RowEvidenceProbe, SamplePolicy, probe_hash
from revi_kernel.refs import DateBasisRef, DimensionRef, FieldRef
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_statistics_contracts.contract import DenialRow, RecoveryStatus

#: The columns a recovery estimate needs, in a fixed order so the read's
#: fingerprint is stable across runs.
DENIAL_COLUMNS: tuple[str, ...] = (
    "denial_id",
    "denial_date",
    "service_date",
    "payer_name",
    "plan_name",
    "facility_name",
    "denial_recovery_class",
    "recovery_status",
    "denied_amount_cents",
    "recovered_amount_cents",
    "days_to_resubmission",
    "resubmission_date",
    "recovery_outcome_date",
    "timely_filing_days",
)

#: Recorded on the read and shown in the evidence rail. Row evidence
#: requires a stated purpose; this is it.
READ_PURPOSE = (
    "deep research: the resubmission history of each denial, at the denial grain, "
    "for own-cohort recovery estimation"
)

_APPEAL_STATUS = DimensionRef("appeal_status")
_NO_APPEAL = "NONE"
_SERVICE_BASIS = DateBasisRef("service")

#: The one status that is not a real recovery state — a value the estimator
#: has no member for means the data changed shape underneath us, and a run
#: that quietly dropped those rows would publish a population that is not
#: the one it read.
_KNOWN_STATUSES = frozenset(str(status) for status in RecoveryStatus)


class DeepResearchReadRefused(Exception):
    """The read could not be made honestly, so it was not made at all."""


@dataclass(frozen=True, slots=True)
class DenialRows:
    """One read, and everything a reader needs to audit it."""

    rows: tuple[DenialRow, ...]
    watermark: DataWatermark
    #: The read's content fingerprint — the same handle the evidence rail
    #: and the trace carry for every other read this platform makes.
    read_fingerprint: str
    rows_read: int
    cache_hit: bool
    duration_ms: int
    #: The data edge every maturity and deadline judgement is relative to.
    as_of: date

    @property
    def open_rows(self) -> tuple[DenialRow, ...]:
        """Denials whose story has not ended — the pricing target."""
        return tuple(row for row in self.rows if not row.recovery_status.is_decided)


def build_probe(
    *,
    population: TargetPopulation,
    settings: DeepResearchSettings,
    watermark: DataWatermark,
) -> RowEvidenceProbe:
    """The read, as a typed probe. No SQL is written anywhere in this mode."""
    clauses: list[FilterExpr] = []
    if settings.exclude_appealed:
        clauses.append(
            Predicate(dimension=_APPEAL_STATUS, op=PredicateOp.EQ, values=(_NO_APPEAL,))
        )
    dimension = population.dimension
    if dimension is not None:
        clauses.append(
            Predicate(
                dimension=DimensionRef(dimension),
                op=PredicateOp.IN,
                values=tuple(population.values),
            )
        )
    scope = and_merge(*clauses) if clauses else and_merge()
    window = TimeWindow(
        basis=_SERVICE_BASIS,
        range=AbsoluteRange(
            start=settings.earliest_service_date, end=watermark.newest_data_date
        ),
    )
    return RowEvidenceProbe(
        columns=tuple(FieldRef(name) for name in DENIAL_COLUMNS),
        scope=scope,
        sample=SamplePolicy(n=settings.max_rows),
        purpose=READ_PURPOSE,
        window=window,
    )


def _index(frame: EvidenceFrame) -> Mapping[str, int]:
    return {column.name: position for position, column in enumerate(frame.schema.columns)}


def _to_denial_rows(
    frame: EvidenceFrame,
    *,
    filing_rule_confirmed: Callable[[str, str], bool],
) -> tuple[DenialRow, ...]:
    """Frame rows → the estimator's typed input, refusing anything unreadable.

    Every conversion here is total: a row the estimator's own invariants
    reject is a defect in the read, not a row to skip. Skipping would change
    the population underneath a published total without saying so.
    """
    position = _index(frame)
    missing = [name for name in DENIAL_COLUMNS if name not in position]
    if missing:
        raise DeepResearchReadRefused(
            f"the recovery history is missing {len(missing)} column(s) this analysis needs"
        )

    def cell(row: Sequence[object], name: str) -> object:
        return row[position[name]]

    def whole(value: object, *, column: str) -> int | None:
        """A column that must be a whole number, or a refusal naming it."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise DeepResearchReadRefused(
                f"the recovery history holds a {column} this analysis cannot read as a number"
            )
        return int(value)

    rows: list[DenialRow] = []
    for raw in frame.rows:
        status_text = str(cell(raw, "recovery_status"))
        if status_text not in _KNOWN_STATUSES:
            raise DeepResearchReadRefused(
                "the recovery history holds a follow-up state this analysis does not know"
            )
        denial_date = cell(raw, "denial_date")
        service_date = cell(raw, "service_date")
        if not isinstance(denial_date, date) or not isinstance(service_date, date):
            raise DeepResearchReadRefused("a denial arrived without its dates")
        payer = str(cell(raw, "payer_name"))
        plan = str(cell(raw, "plan_name"))
        resubmission_date = cell(raw, "resubmission_date")
        outcome_date = cell(raw, "recovery_outcome_date")
        delay = cell(raw, "days_to_resubmission")
        limit = cell(raw, "timely_filing_days")
        rows.append(
            DenialRow(
                denial_id=str(cell(raw, "denial_id")),
                denial_date=denial_date,
                service_date=service_date,
                payer_name=payer,
                plan_name=plan,
                recovery_class=str(cell(raw, "denial_recovery_class")),
                recovery_status=RecoveryStatus(status_text),
                denied_amount_cents=whole(cell(raw, "denied_amount_cents"), column="denied amount") or 0,
                recovered_amount_cents=whole(
                    cell(raw, "recovered_amount_cents"), column="recovered amount"
                )
                or 0,
                days_to_resubmission=whole(delay, column="days to resubmission"),
                resubmission_date=resubmission_date if isinstance(resubmission_date, date) else None,
                recovery_outcome_date=outcome_date if isinstance(outcome_date, date) else None,
                timely_filing_days=whole(limit, column="filing limit"),
                filing_rule_confirmed=filing_rule_confirmed(payer, plan),
            )
        )
    # Sorted by the denial's own id so the estimator sees the same order on
    # every run regardless of what the source returned.
    return tuple(sorted(rows, key=lambda row: row.denial_id))


class DenialRowSource:
    """Reads the recovery history for one run, through the ordinary port.

    The evidence cache is the same one every probe uses, keyed the same way
    — the read's fingerprint, the data load, and the definitions library's
    snapshot. Two runs over the same population at the same load therefore
    share one query, which is most of why a second run is fast.
    """

    def __init__(
        self,
        repository: AnalyticalRepository,
        cache: EvidenceCache,
        *,
        filing_rule_confirmed: Callable[[str, str], bool],
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._confirmed = filing_rule_confirmed

    async def fetch(
        self,
        *,
        population: TargetPopulation,
        settings: DeepResearchSettings,
        watermark: DataWatermark,
        pack_snapshot_id: str,
    ) -> DenialRows:
        probe = build_probe(
            population=population, settings=settings, watermark=watermark
        )
        digest = probe_hash(probe)
        started = time.monotonic()
        cached = await self._cache.get(digest, watermark.id, pack_snapshot_id)
        cache_hit = cached is not None
        if cached is not None:
            frame = cached
        else:
            try:
                frame = await self._repository.execute(probe, watermark=watermark)
            except QueryBudgetExceededError as exc:
                raise DeepResearchReadRefused(
                    "this population is larger than one read of this analysis may take"
                ) from exc
            await self._cache.put(digest, watermark.id, pack_snapshot_id, frame)

        if frame.truncated:
            raise DeepResearchReadRefused(
                "this population is larger than one read of this analysis may take, and "
                "an estimate over part of it would not be a measurement"
            )
        rows = _to_denial_rows(frame, filing_rule_confirmed=self._confirmed)
        return DenialRows(
            rows=rows,
            watermark=watermark,
            read_fingerprint=digest,
            rows_read=len(rows),
            cache_hit=cache_hit,
            duration_ms=int((time.monotonic() - started) * 1000),
            as_of=watermark.newest_data_date,
        )
