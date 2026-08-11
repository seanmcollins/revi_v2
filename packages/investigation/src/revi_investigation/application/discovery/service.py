"""The discovery family: cheap, certified reads that choose an approach.

The agentic-resolution constitution names two closed tool families. Compute
operations answer questions; **discovery operations choose approaches** —
concept-to-path resolution, dimension value censuses, coverage and
population profiling, capability negotiation, subject presence. Before this
module those lived in four places, three of them private methods on the plan
validator, and none of them could be asked a question by a planner that had
a concept rather than a plan. Here they are one governed API with one cache,
one provenance shape, and one rule: *a discovery read never publishes a
figure a reader acts on.* It publishes what a figure could be made of.

Four properties are load-bearing.

**Cheap.** Every read is one grouped aggregation over a governed count
ruler, keyed into the ordinary evidence cache. A census of twelve payers
costs what any other twelve-row probe costs, and the second call inside a
run costs nothing at all — the service memoizes on the request key, so an
orient phase that resolves the same concept twice reads once.

**Governed.** The ruler a census counts in is read off the pack: the
additive, count-unit, unfiltered flow metric at that entity. Nothing here
names a measure, a column, or a threshold in code. A dimension no governed
measure declares as a cut is not censused by going around the declaration —
it is reported as what it is, and the concept resolver then looks for the
governed *measure* that carries the concept instead.

**Deterministic.** The request key is a canonical serialization of the
discovery question. Two runs asking the same question at the same load hit
the same key, the same probe hash, and the same bytes. Nothing here samples,
guesses, or consults a model.

**Disclosable.** Every result carries the sentence a report prints to
disclose the path it chose. The sentence is built beside the coverage figure
it quotes; a composer that had to phrase it from a payload would drop the
number, and a path disclosure without its coverage is decoration.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from revi_calculation_contracts.contract import (
    CountDistinct,
    Filtered,
    MeasureExpr,
    MetricKind,
)
from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.discovery.model import (
    CapabilityReport,
    ConceptExpression,
    ConceptResolution,
    DimensionCensus,
    DimensionValue,
    DiscoveryKind,
    DiscoveryNote,
    DiscoveryProvenance,
    MeasureAvailability,
    MeasureProfile,
    SubjectMatch,
    SubjectPresence,
)
from revi_investigation.application.ports import EvidenceCache
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.errors import ReviError
from revi_kernel.filters import FilterExpr, and_merge, dimensions_used
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import AggregationProbe, probe_hash
from revi_kernel.refs import DateBasisRef, DimensionRef, EntityGrain, Grain, MetricRef
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark

#: How many values a census names before it summarises. The cardinality is
#: always exact; the list is what a reader can hold.
MAX_CENSUS_VALUES = 40

#: What a NULL dimension value is called when it reaches a sentence. A claim
#: with no payer on it is not "null" — it is a claim that does not carry one.
UNPOPULATED = "(no value on the record)"

#: The largest cardinality a certified cut may declare before it stops being
#: somewhere a subject NAME could live. Read off the catalog's own estimate,
#: so widening it is content work, not a constant edited here.
MAX_SUBJECT_CARDINALITY = 64


class DiscoveryRefused(Exception):
    """A discovery read could not be made, so it was not made at all."""


@dataclass(frozen=True, slots=True)
class _Ruler:
    """The governed count metric a census is measured in, at one entity.

    ``basis`` is the contract's own primary date basis rather than one the
    caller chose. A census is a fact about a population, and which date
    puts a record in that population is a governed decision — asking for
    denials on a basis the denial entity does not bind is not a narrower
    census, it is a refusal the caller cannot act on.
    """

    metric_id: str
    entity: str
    grain: EntityGrain
    cuts: frozenset[str]
    basis: DateBasisRef


@dataclass(frozen=True, slots=True)
class _PopulationMeasure:
    """A governed measure whose own definition IS a population filter.

    ``cob_mismatch_claims`` counts claims billed primary while other
    insurance exists. The flag it filters on is a certified dimension that
    no measure declares as a *cut* — so it can never be censused, and a
    concept resolver that only knew how to census would report the
    warehouse's most direct reading of COB as absent.
    """

    metric_id: str
    grain: EntityGrain
    dimension_id: str


def _key(kind: DiscoveryKind, *parts: object) -> str:
    """The question-shape half of the cache key, canonically."""
    digest = hashlib.sha256()
    digest.update(str(kind).encode("utf-8"))
    for part in parts:
        digest.update(b"\x1f")
        digest.update(str(part).encode("utf-8"))
    return f"{kind}:{digest.hexdigest()[:24]}"


def _share(part: int, whole: int) -> Decimal:
    if whole <= 0:
        return Decimal(0)
    return (Decimal(part) / Decimal(whole)).quantize(Decimal("1E-6"))


def _pct(value: Decimal) -> str:
    """A share as a reader reads one. Never a raw ratio on a surface."""
    scaled = (value * 100).quantize(Decimal("0.1"))
    text = format(scaled, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text or '0'}%"


def _filter_dimensions(expr: MeasureExpr) -> frozenset[str]:
    """Dimensions a metric's own definition filters on.

    A contract-internal filter is the measure's *meaning*, not a scope the
    analyst chose, which is exactly why it can carry a concept a cut cannot.
    """
    if isinstance(expr, Filtered):
        return frozenset(ref.id for ref in dimensions_used(expr.where))
    return frozenset()


class DiscoveryService:
    """One governed discovery API over the catalog, the pack, and the data."""

    def __init__(
        self,
        repository: AnalyticalRepository,
        cache: EvidenceCache,
        catalog: CatalogSnapshot,
        pack: PackPort,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._catalog = catalog
        self._pack = pack
        self._memo: dict[str, object] = {}
        self._notes: list[DiscoveryNote] = []

    # -- what the run learned, in order ------------------------------------

    @property
    def notes(self) -> tuple[DiscoveryNote, ...]:
        """Every discovery finding this service produced, in the order asked.

        The orient phase hands these straight to the preview card and the
        trace. Recording them here rather than at each call site is what
        makes "the discovery findings that shaped the plan" a fact about the
        run instead of a list a caller remembered to keep.
        """
        return tuple(self._notes)

    def forget(self) -> None:
        self._memo.clear()
        self._notes.clear()

    def _record(self, provenance: DiscoveryProvenance, subject: str, statement: str) -> None:
        # One note per distinct discovery QUESTION, not per call. An orient
        # phase resolves the same census from three directions; recording it
        # three times would put the same sentence on the preview card three
        # times and make the walk a log of lookups instead of a record of
        # what was learned.
        if any(note.request_key == provenance.request_key for note in self._notes):
            return
        self._notes.append(
            DiscoveryNote(
                kind=provenance.kind,
                subject=subject,
                statement=statement,
                request_key=provenance.request_key,
                reads=provenance.reads,
                duration_ms=provenance.duration_ms,
                cache_hit=provenance.cache_hit,
            )
        )

    # -- rulers -------------------------------------------------------------

    def _rulers(self) -> tuple[_Ruler, ...]:
        """The governed count ruler at each entity grain this pack measures.

        A census counts *units*, and which unit it counts is a governed
        decision, not an arithmetic one: denials, claims, lines and remits
        are four populations and a coverage figure over the wrong one is a
        different claim. So the ruler is read off the pack — the count-unit,
        denominator-free, exclusion-free flow metric whose numerator is a
        plain distinct count, smallest id wins — rather than named here.

        A metric whose numerator is itself filtered is disqualified: it
        counts a sub-population, and a coverage figure taken over one would
        report how much of the *interesting* data carries a field.
        """
        cached = self._memo.get("__rulers__")
        if isinstance(cached, tuple):
            return cached
        by_grain: dict[EntityGrain, _Ruler] = {}
        for metric_id, _ in sorted(self._pack.metric_summaries()):
            contract = self._pack.metric(metric_id)
            if contract is None or contract.kind is not MetricKind.FLOW:
                continue
            if contract.denominator is not None or contract.exclusions is not None:
                continue
            if str(contract.unit) != "count" or not isinstance(contract.numerator, CountDistinct):
                continue
            entity = self._catalog.entity(contract.entity_grain)
            if entity is None or contract.entity_grain in by_grain:
                continue
            by_grain[contract.entity_grain] = _Ruler(
                metric_id=metric_id,
                entity=entity.name,
                grain=contract.entity_grain,
                cuts=frozenset(dim.id for dim in contract.scope_dimensions),
                basis=contract.primary_date_basis,
            )
        rulers = tuple(by_grain[grain] for grain in sorted(by_grain, key=str))
        self._memo["__rulers__"] = rulers
        return rulers

    def _ruler_for(self, dimension_id: str, *, prefer: EntityGrain | None = None) -> _Ruler | None:
        """The count ruler that may legally be cut by this dimension.

        ``prefer`` lets a caller ask for the grain its question is about; a
        dimension the preferred ruler cannot carry falls through to whichever
        governed ruler declares it, deterministically by grain name.
        """
        rulers = self._rulers()
        if prefer is not None:
            for ruler in rulers:
                if ruler.grain is prefer and dimension_id in ruler.cuts:
                    return ruler
        for ruler in rulers:
            if dimension_id in ruler.cuts:
                return ruler
        return None

    def _ruler_at(self, grain: EntityGrain) -> _Ruler | None:
        for ruler in self._rulers():
            if ruler.grain is grain:
                return ruler
        return None

    def _population_measure(self, dimension_id: str) -> _PopulationMeasure | None:
        """The governed count measure that defines itself BY this dimension.

        Smallest id wins, so the choice is stable; the measure must be a
        count so its share of the entity's ruler is a population share
        rather than a ratio of money to records.
        """
        memo_key = f"__popmeasure__{dimension_id}"
        if memo_key in self._memo:
            cached = self._memo[memo_key]
            return cached if isinstance(cached, _PopulationMeasure) else None
        found: _PopulationMeasure | None = None
        for metric_id, _ in sorted(self._pack.metric_summaries()):
            contract = self._pack.metric(metric_id)
            if contract is None or contract.kind is not MetricKind.FLOW:
                continue
            if contract.denominator is not None or str(contract.unit) != "count":
                continue
            if dimension_id not in _filter_dimensions(contract.numerator):
                continue
            found = _PopulationMeasure(
                metric_id=metric_id,
                grain=contract.entity_grain,
                dimension_id=dimension_id,
            )
            break
        self._memo[memo_key] = found
        return found

    # -- capability negotiation --------------------------------------------

    def capabilities(self, *, watermark: DataWatermark, pack_snapshot_id: str) -> CapabilityReport:
        """What this deployment can compute, before anything is asked of it.

        No warehouse read: every fact here is a declaration — the catalog's,
        the pack's, or the adapter's own. An adapter's silence advertises no
        capability and is never read as permission.
        """
        started = time.monotonic()
        request_key = _key(DiscoveryKind.CAPABILITIES, watermark.id, pack_snapshot_id)
        certified = tuple(sorted(d.id for d in self._catalog.dimensions if d.certified))
        uncertified = tuple(sorted(d.id for d in self._catalog.dimensions if not d.certified))
        metrics = tuple(sorted(metric_id for metric_id, _ in self._pack.metric_summaries()))
        grains = tuple(sorted({str(entity.grain) for entity in self._catalog.entities}))
        bases = tuple(sorted(basis.id for basis in self._catalog.date_bases))
        concepts = tuple(sorted(concept_id for concept_id, _ in self._pack.concept_summaries()))
        declared = self._repository.capabilities()
        statement = (
            f"Your definitions library holds {len(metrics)} measures over "
            f"{len(certified)} standard breakdowns, readable on "
            f"{len(bases)} dates."
        )
        provenance = DiscoveryProvenance(
            kind=DiscoveryKind.CAPABILITIES,
            request_key=request_key,
            watermark_id=watermark.id,
            pack_snapshot_id=pack_snapshot_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        report = CapabilityReport(
            certified_dimensions=certified,
            uncertified_dimensions=uncertified,
            metrics=metrics,
            entity_grains=grains,
            date_bases=bases,
            concepts=concepts,
            as_of_reads=declared.as_of_reads,
            server_side_top_n=declared.server_side_top_n,
            cross_entity_ratio_of_sums=declared.cross_entity_ratio_of_sums,
            suppression_threshold=self._catalog.suppression.threshold,
            statement=statement,
            provenance=provenance,
        )
        self._record(provenance, "what can be measured here", statement)
        return report

    # -- dimension value census --------------------------------------------

    async def dimension_census(
        self,
        dimension_id: str,
        *,
        window: AbsoluteRange,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        scope: FilterExpr | None = None,
        prefer: EntityGrain | None = None,
        limit: int = MAX_CENSUS_VALUES,
    ) -> DimensionCensus:
        """The values a dimension takes here, their sizes, and its coverage.

        One grouped read over the dimension's governed count ruler. The
        unpopulated group is not dropped — it *is* the coverage answer, and
        a census that discarded it would report a field as fully populated
        because the records lacking it fell out of a grouping it never
        printed.
        """
        started = time.monotonic()
        definition = self._catalog.dimension(dimension_id)
        if definition is None:
            raise DiscoveryRefused(f"{dimension_id!r} is not a breakdown this data defines")
        ruler = self._ruler_for(dimension_id, prefer=prefer)
        if ruler is None:
            raise DiscoveryRefused(
                f"no measure in your definitions library is broken out by "
                f"{definition.label.lower()}"
            )
        request_key = _key(
            DiscoveryKind.DIMENSION_CENSUS,
            dimension_id,
            ruler.metric_id,
            window,
            scope,
            limit,
            watermark.id,
            pack_snapshot_id,
        )
        memo = self._memo.get(request_key)
        if isinstance(memo, DimensionCensus):
            self._record(memo.provenance, definition.label, memo.statement)
            return memo

        probe = AggregationProbe(
            measures=(MetricRef(ruler.metric_id),),
            dimensions=(DimensionRef(dimension_id),),
            scope=scope if scope is not None else and_merge(),
            window=TimeWindow(basis=ruler.basis, range=window),
            grain=Grain(ruler.grain),
        )
        frame, cache_hit = await self._read(probe, watermark, pack_snapshot_id)

        rows = _named_counts(frame, dimension_id, ruler.metric_id)
        units = sum(count for _, count in rows)
        populated = sum(count for value, count in rows if value is not None)
        values = sorted(
            (
                DimensionValue(
                    value=value if value is not None else UNPOPULATED,
                    units=count,
                    share=_share(count, units),
                )
                for value, count in rows
            ),
            key=lambda entry: (-entry.units, entry.value),
        )
        cardinality = sum(1 for value, _ in rows if value is not None)
        truncated = len(values) > limit
        coverage = _share(populated, units)
        statement = _census_statement(
            label=definition.label,
            certified=definition.certified,
            cardinality=cardinality,
            coverage=coverage,
            values=values[:limit],
            units=units,
        )
        provenance = DiscoveryProvenance(
            kind=DiscoveryKind.DIMENSION_CENSUS,
            request_key=request_key,
            watermark_id=watermark.id,
            pack_snapshot_id=pack_snapshot_id,
            reads=(probe_hash(probe),),
            cache_hit=cache_hit,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        census = DimensionCensus(
            dimension_id=dimension_id,
            label=definition.label,
            certified=definition.certified,
            entity=ruler.entity,
            ruler=ruler.metric_id,
            values=tuple(values[:limit]),
            cardinality=cardinality,
            populated=populated,
            units=units,
            coverage=coverage,
            truncated=truncated,
            statement=statement,
            provenance=provenance,
        )
        self._memo[request_key] = census
        self._record(provenance, definition.label, statement)
        return census

    # -- concept → path -----------------------------------------------------

    async def concept_paths(
        self,
        term: str,
        *,
        window: AbsoluteRange,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        scope: FilterExpr | None = None,
    ) -> ConceptResolution:
        """A concept term → the certified expressions this warehouse populates.

        The pack declares which fields *could* express a concept and how
        strong each is as evidence for it. Only the data can say which of
        them this deployment carries, so every candidate gets a coverage
        figure and it travels onto the answer.

        Three shapes come back, because the warehouse really does carry
        concepts three ways: a certified **breakdown** (censused), a
        governed **population** whose definition is the field itself
        (measured as its share of the entity's ruler), and a **quantity**
        (present or not; a measure has no coverage of its own).

        The preferred path is the strongest one the data populates — never
        the strongest one declared. A certified DIRECT binding onto a column
        nobody fills is exactly the trap this operation exists to catch: the
        plan reads the proxy, and the report says it did.
        """
        started = time.monotonic()
        concept_id = self._pack.concept_for_alias(term) or (
            term if self._pack.has_concept(term) else None
        )
        request_key = _key(
            DiscoveryKind.CONCEPT_PATHS,
            term.strip().lower(),
            concept_id,
            window,
            scope,
            watermark.id,
            pack_snapshot_id,
        )
        memo = self._memo.get(request_key)
        if isinstance(memo, ConceptResolution):
            self._record(memo.provenance, term, memo.statement)
            return memo

        expressions: list[ConceptExpression] = []
        reads: list[str] = []
        if concept_id is not None:
            for binding in self._pack.concept_bindings(concept_id):
                expression, used = await self._express(
                    binding.field_id,
                    state=binding.state,
                    strength=binding.strength,
                    window=window,
                    watermark=watermark,
                    pack_snapshot_id=pack_snapshot_id,
                    scope=scope,
                )
                reads.extend(used)
                expressions.append(expression)

        preferred = _preferred(expressions)
        statement = _concept_statement(term=term, preferred=preferred, expressions=expressions)
        provenance = DiscoveryProvenance(
            kind=DiscoveryKind.CONCEPT_PATHS,
            request_key=request_key,
            watermark_id=watermark.id,
            pack_snapshot_id=pack_snapshot_id,
            reads=tuple(dict.fromkeys(reads)),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        resolution = ConceptResolution(
            term=term,
            concept_id=concept_id,
            expressions=tuple(expressions),
            preferred=preferred,
            statement=statement,
            provenance=provenance,
        )
        self._memo[request_key] = resolution
        self._record(provenance, term, statement)
        return resolution

    async def _express(
        self,
        field_id: str,
        *,
        state: str,
        strength: object,
        window: AbsoluteRange,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        scope: FilterExpr | None,
    ) -> tuple[ConceptExpression, tuple[str, ...]]:
        """One binding, resolved against the catalog and then the data."""
        from revi_kernel.grades import EvidenceGrade

        grade = strength if isinstance(strength, EvidenceGrade) else EvidenceGrade.UNAVAILABLE
        definition = self._catalog.dimension(field_id)
        measure = self._catalog.measure(field_id)
        if definition is not None:
            if self._ruler_for(field_id) is not None:
                try:
                    census = await self.dimension_census(
                        field_id,
                        window=window,
                        watermark=watermark,
                        pack_snapshot_id=pack_snapshot_id,
                        scope=scope,
                    )
                except (DiscoveryRefused, ReviError):
                    census = None
                if census is not None:
                    return (
                        ConceptExpression(
                            field_id=field_id,
                            label=definition.label,
                            kind="dimension",
                            binding_state=state,
                            strength=grade,
                            certified=definition.certified,
                            populated=census.populated,
                            units=census.units,
                            distinct_values=census.cardinality,
                            coverage=census.coverage,
                        ),
                        census.provenance.reads,
                    )
            population = self._population_measure(field_id)
            if population is not None:
                share = await self._population_share(
                    population,
                    window=window,
                    watermark=watermark,
                    pack_snapshot_id=pack_snapshot_id,
                    scope=scope,
                )
                if share is not None:
                    populated, units, read = share
                    return (
                        ConceptExpression(
                            field_id=field_id,
                            label=definition.label,
                            kind="population",
                            binding_state=state,
                            strength=grade,
                            certified=definition.certified,
                            populated=populated,
                            units=units,
                            distinct_values=1,
                            coverage=_share(populated, units),
                            measure_id=population.metric_id,
                        ),
                        (read,),
                    )
            return (
                ConceptExpression(
                    field_id=field_id,
                    label=definition.label,
                    kind="dimension",
                    binding_state=state,
                    strength=grade,
                    certified=definition.certified,
                ),
                (),
            )
        return (
            ConceptExpression(
                field_id=field_id,
                label=field_id.replace("_", " "),
                kind="measure" if measure is not None else "absent",
                binding_state=state,
                strength=grade,
                certified=measure is not None,
            ),
            (),
        )

    async def _population_share(
        self,
        population: _PopulationMeasure,
        *,
        window: AbsoluteRange,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        scope: FilterExpr | None,
    ) -> tuple[int, int, str] | None:
        """How much of an entity's records the governed population covers.

        One read for both numbers. Asking twice would be two reads and, at a
        moving data edge, two populations — the share is only a share if its
        halves came from the same query.
        """
        ruler = self._ruler_at(population.grain)
        if ruler is None or ruler.metric_id == population.metric_id:
            return None
        probe = AggregationProbe(
            measures=(MetricRef(population.metric_id), MetricRef(ruler.metric_id)),
            dimensions=(),
            scope=scope if scope is not None else and_merge(),
            window=TimeWindow(basis=ruler.basis, range=window),
            grain=Grain(ruler.grain),
        )
        try:
            frame, _ = await self._read(probe, watermark, pack_snapshot_id)
        except (DiscoveryRefused, ReviError):
            return None
        positions = {column.name: index for index, column in enumerate(frame.schema.columns)}
        part_at = positions.get(population.metric_id)
        whole_at = positions.get(ruler.metric_id)
        if part_at is None or whole_at is None or not frame.rows:
            return None
        row = frame.rows[0]
        part, whole = row[part_at], row[whole_at]
        if not isinstance(part, int) or not isinstance(whole, int):
            return None
        return part, whole, probe_hash(probe)

    # -- measure availability ----------------------------------------------

    def measure_availability(
        self,
        *,
        population: str,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        grain: EntityGrain | None = None,
        cuts: Sequence[str] = (),
    ) -> MeasureProfile:
        """Which governed measures can compute over a population and grain.

        Declaration-only, and deliberately so: the answer is a fact about
        contracts, not about rows, and paying for a read to learn that
        ``ar_over_90_pct`` is an as-of measure would make the cheapest
        question in the system the slowest. A measure is available when its
        contract's grain matches what was asked and its certified
        breakdowns cover every cut the question needs — the two ways a plan
        can propose an analysis this platform would refuse at validation.
        """
        started = time.monotonic()
        wanted = tuple(sorted(set(cuts)))
        request_key = _key(
            DiscoveryKind.MEASURE_AVAILABILITY,
            population,
            grain,
            wanted,
            watermark.id,
            pack_snapshot_id,
        )
        memo = self._memo.get(request_key)
        if isinstance(memo, MeasureProfile):
            self._record(memo.provenance, population, memo.statement)
            return memo

        measures: list[MeasureAvailability] = []
        for metric_id, _ in sorted(self._pack.metric_summaries()):
            contract = self._pack.metric(metric_id)
            if contract is None:
                continue
            declared = frozenset(dim.id for dim in contract.scope_dimensions)
            missing = [cut for cut in wanted if cut not in declared]
            available = True
            reason = ""
            if grain is not None and contract.entity_grain is not grain:
                available = False
                reason = (
                    f"measured one row per {contract.entity_grain}, "
                    f"not per {grain}"
                )
            elif missing:
                available = False
                reason = "not broken out by " + ", ".join(self._label_of(cut) for cut in missing)
            measures.append(
                MeasureAvailability(
                    metric_id=metric_id,
                    unit=str(contract.unit),
                    kind=str(contract.kind),
                    entity_grain=str(contract.entity_grain),
                    sign=str(contract.sign),
                    available=available,
                    reason=reason,
                    cuts=tuple(sorted(declared)),
                    date_bases=tuple(sorted(basis.id for basis in contract.allowed_date_bases)),
                )
            )
        usable = sum(1 for measure in measures if measure.available)
        cut_words = (
            f", broken out by {', '.join(self._label_of(cut) for cut in wanted)}" if wanted else ""
        )
        statement = (
            f"{usable} of the {len(measures)} measures in your definitions library can be "
            f"measured over {population}{cut_words}."
        )
        provenance = DiscoveryProvenance(
            kind=DiscoveryKind.MEASURE_AVAILABILITY,
            request_key=request_key,
            watermark_id=watermark.id,
            pack_snapshot_id=pack_snapshot_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        profile = MeasureProfile(
            population=population,
            grain=str(grain) if grain is not None else None,
            measures=tuple(measures),
            statement=statement,
            provenance=provenance,
        )
        self._memo[request_key] = profile
        self._record(provenance, population, statement)
        return profile

    # -- subject presence ---------------------------------------------------

    async def subject_presence(
        self,
        term: str,
        *,
        window: AbsoluteRange,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        dimensions: Sequence[str] = (),
    ) -> SubjectPresence:
        """Is a name the analyst used a value this data actually holds?

        Answered out of censuses, which is why it is cheap: the orient phase
        has usually already read the breakdown the subject lives in, and a
        presence check over a cached census is arithmetic. A subject the
        data does not hold is a refusal naming the gap — never an empty
        population quietly measured.
        """
        started = time.monotonic()
        candidates = tuple(dimensions) or self._subject_dimensions()
        request_key = _key(
            DiscoveryKind.SUBJECT_PRESENCE,
            term.strip().lower(),
            candidates,
            window,
            watermark.id,
            pack_snapshot_id,
        )
        memo = self._memo.get(request_key)
        if isinstance(memo, SubjectPresence):
            self._record(memo.provenance, term, memo.statement)
            return memo

        needle = term.strip().casefold()
        matches: list[SubjectMatch] = []
        reads: list[str] = []
        for dimension_id in candidates:
            try:
                census = await self.dimension_census(
                    dimension_id,
                    window=window,
                    watermark=watermark,
                    pack_snapshot_id=pack_snapshot_id,
                )
            except (DiscoveryRefused, ReviError):
                continue
            reads.extend(census.provenance.reads)
            for entry in census.values:
                if entry.value == UNPOPULATED:
                    continue
                folded = entry.value.casefold()
                if folded == needle or (len(needle) >= 3 and needle in folded):
                    matches.append(
                        SubjectMatch(
                            dimension_id=dimension_id,
                            dimension_label=census.label,
                            value=entry.value,
                            units=entry.units,
                        )
                    )
        matches.sort(key=lambda match: (-match.units, match.dimension_id, match.value))
        statement = _presence_statement(term, matches)
        provenance = DiscoveryProvenance(
            kind=DiscoveryKind.SUBJECT_PRESENCE,
            request_key=request_key,
            watermark_id=watermark.id,
            pack_snapshot_id=pack_snapshot_id,
            reads=tuple(dict.fromkeys(reads)),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        presence = SubjectPresence(
            term=term, matches=tuple(matches), statement=statement, provenance=provenance
        )
        self._memo[request_key] = presence
        self._record(provenance, term, statement)
        return presence

    # -- internals ----------------------------------------------------------

    def _subject_dimensions(self) -> tuple[str, ...]:
        """Certified, low-cardinality breakdowns a subject name could live in.

        Bounded on purpose: a presence check is meant to cost one or two
        cached reads, and sweeping every certified dimension — patient ids
        included — would be neither cheap nor safe. The bound is the
        catalog's own cardinality estimate and its own PHI class, both
        content rather than judgements made here.
        """
        cached = self._memo.get("__subject_dims__")
        if isinstance(cached, tuple):
            return cached
        rulers = self._rulers()
        allowed: frozenset[str] = frozenset()
        for ruler in rulers:
            allowed = allowed | ruler.cuts
        found = tuple(
            sorted(
                dimension.id
                for dimension in self._catalog.dimensions
                if dimension.certified
                and dimension.id in allowed
                and dimension.phi.value == "none"
                and 1 < dimension.cardinality_estimate <= MAX_SUBJECT_CARDINALITY
            )
        )
        self._memo["__subject_dims__"] = found
        return found

    def _label_of(self, dimension_id: str) -> str:
        definition = self._catalog.dimension(dimension_id)
        return (definition.label if definition is not None else dimension_id).lower()

    async def _read(
        self, probe: AggregationProbe, watermark: DataWatermark, pack_snapshot_id: str
    ) -> tuple[EvidenceFrame, bool]:
        """One cached read through the ordinary analytical port."""
        digest = probe_hash(probe)
        cached = await self._cache.get(digest, watermark.id, pack_snapshot_id)
        if cached is not None:
            return cached, True
        frame = await self._repository.execute(probe, watermark=watermark)
        await self._cache.put(digest, watermark.id, pack_snapshot_id, frame)
        return frame, False


# ---------------------------------------------------------------------------
# frame reading


def _named_counts(
    frame: EvidenceFrame, dimension_id: str, metric_id: str
) -> tuple[tuple[str | None, int], ...]:
    """``(value, units)`` pairs off a grouped census frame.

    Reads the column positions off the frame's own schema rather than
    assuming the order the probe asked for: the adapter stamps what it
    compiled, and a census that indexed by position would silently count
    the wrong column the first time a connector reordered its SELECT.
    """
    positions = {column.name: index for index, column in enumerate(frame.schema.columns)}
    value_at = positions.get(dimension_id)
    count_at = positions.get(metric_id)
    if value_at is None or count_at is None:
        raise DiscoveryRefused(
            "this data did not return the breakdown and the count this check needs"
        )
    out: list[tuple[str | None, int]] = []
    for row in frame.rows:
        raw, count = row[value_at], row[count_at]
        if not isinstance(count, int) or isinstance(count, bool):
            continue
        out.append((None if raw is None else str(raw), count))
    return tuple(out)


def _preferred(expressions: Sequence[ConceptExpression]) -> ConceptExpression | None:
    """The strongest expression the warehouse actually populates.

    Ordered by declared evidence strength first and coverage second — never
    coverage first. A well-populated proxy is still a proxy, and letting
    fill rate outrank the grade law is how a reason code becomes a fact
    about coverage.
    """
    populated = [
        expression
        for expression in expressions
        if expression.kind in ("dimension", "population")
        and expression.certified
        and expression.is_populated
    ]
    if not populated:
        return None
    populated.sort(key=lambda e: (-e.strength.strength, -(e.coverage or Decimal(0)), e.field_id))
    return populated[0]


# ---------------------------------------------------------------------------
# the sentences


def _census_statement(
    *,
    label: str,
    certified: bool,
    cardinality: int,
    coverage: Decimal,
    values: Sequence[DimensionValue],
    units: int,
) -> str:
    """One sentence a report can print about a breakdown, with its numbers."""
    named = ", ".join(entry.value for entry in values[:3] if entry.value != UNPOPULATED)
    standard = (
        ""
        if certified
        else " It is not standardized here, so anything read from it is exploratory."
    )
    thing = "value" if cardinality == 1 else "values"
    if coverage >= 1:
        return (
            f"{label} takes {cardinality:,} {thing} in your data"
            + (f" — {named} lead it" if named else "")
            + f" — and every one of the {units:,} records read carries one.{standard}"
        )
    if coverage <= 0:
        return (
            f"{label} is defined here and nothing in the {units:,} records read "
            f"carries it.{standard}"
        )
    return (
        f"{label} takes {cardinality:,} {thing} in your data"
        + (f" — {named} lead it" if named else "")
        + f" — and {_pct(coverage)} of the {units:,} records read carry one.{standard}"
    )


def _expression_words(expression: ConceptExpression) -> str:
    """How one candidate path is described in a path-choice sentence.

    A breakdown and a population marker are measured in different units and
    must never be worded as if they were the same figure. A breakdown's
    number is a **fill rate** — how much of the data carries a value at all.
    A marker's number is an **incidence** — how much of the data the marked
    population is. Printing "0.2%" beside "100%" as though the first were a
    thinner version of the second is how a reader concludes the direct
    reading is the weak one.
    """
    if expression.kind == "population":
        return (
            f"{expression.label.lower()} marks "
            f"{_pct(expression.coverage or Decimal(0))} of records outright"
        )
    if expression.kind == "measure":
        return f"{expression.label.lower()} is a quantity, not a breakdown"
    if expression.kind == "absent":
        return f"{expression.label.lower()} is not in your data"
    if expression.coverage is None:
        return f"{expression.label.lower()} cannot be broken out here"
    return f"{expression.label.lower()} is filled on {_pct(expression.coverage)} of records"


def _preferred_words(expression: ConceptExpression, term: str) -> str:
    if expression.kind == "population":
        return (
            f"Your data marks {term} outright — {expression.label.lower()} is set on "
            f"{_pct(expression.coverage or Decimal(0))} of the {expression.units:,} "
            "records read"
        )
    return (
        f"Your data carries {term} mainly in {expression.label.lower()}, filled on "
        f"{_pct(expression.coverage or Decimal(0))} of the {expression.units:,} records read"
    )


def _concept_statement(
    *, term: str, preferred: ConceptExpression | None, expressions: Sequence[ConceptExpression]
) -> str:
    """The path-choice disclosure the constitution requires by name.

    "Your data carries COB mainly in remit codes — the category field is
    sparsely populated here, so I read the codes." Two clauses: what was
    read, and what was passed over and why. The second clause is what makes
    the first checkable, so it is never dropped when there is one.
    """
    if not expressions:
        return f"Your definitions library carries no standard way to read {term} here."
    if preferred is None:
        return (
            f"Nothing in your data populates a standard reading of {term} — "
            + "; ".join(_expression_words(expression) for expression in expressions[:3])
            + "."
        )
    others = [
        expression for expression in expressions if expression.field_id != preferred.field_id
    ]
    lead = _preferred_words(preferred, term)
    if not others:
        return f"{lead}, so that is what I read."
    return (
        f"{lead}. The alternatives are weaker evidence for it — "
        + "; ".join(_expression_words(expression) for expression in others[:2])
        + f" — so I read {preferred.label.lower()}."
    )


def _presence_statement(term: str, matches: Sequence[SubjectMatch]) -> str:
    if not matches:
        return f"Nothing in your data is named {term}."
    first = matches[0]
    if len(matches) == 1:
        return (
            f"{first.value} is in your data as a {first.dimension_label.lower()}, "
            f"on {first.units:,} records."
        )
    return (
        f"{term} matches {len(matches)} things in your data; the largest is {first.value} "
        f"({first.dimension_label.lower()}, {first.units:,} records)."
    )
