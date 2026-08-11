"""What a discovery read returns — typed, provenance-carrying, quotable.

Discovery answers *how could this be measured here*, never *what is the
number*. Every shape in this module therefore carries two things a compute
result does not need: a **coverage** figure (how much of the population the
path can actually speak for) and a **statement** — one plain sentence a
report can print verbatim to disclose the path it chose.

The sentence is built here, beside the numbers it quotes, rather than in the
composer. A disclosure composed somewhere else from a payload that lost the
coverage figure is how "your data carries COB mainly in remit codes" becomes
"COB was read from the available fields" — true, useless, and unfalsifiable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from revi_kernel.grades import EvidenceGrade


class DiscoveryKind(StrEnum):
    """The closed discovery family (the constitution's second tool family)."""

    #: What this deployment can compute at all — catalog, pack, adapter.
    CAPABILITIES = "capabilities"
    #: A concept term → the certified expressions this warehouse populates.
    CONCEPT_PATHS = "concept_paths"
    #: A dimension's values, cardinality and coverage over a population.
    DIMENSION_CENSUS = "dimension_census"
    #: Which governed measures can compute over a population and grain.
    MEASURE_AVAILABILITY = "measure_availability"
    #: Whether a named subject (a payer, a facility) is in the data at all.
    SUBJECT_PRESENCE = "subject_presence"


@dataclass(frozen=True, slots=True)
class DiscoveryProvenance:
    """Where a discovery answer came from, and what it cost.

    ``request_key`` is the question-shape half of the cache key: two calls
    asking the same discovery question at the same load must be the same
    bytes, and the key is what makes that checkable rather than asserted.
    """

    kind: DiscoveryKind
    request_key: str
    watermark_id: str
    pack_snapshot_id: str
    #: Probe fingerprints behind this answer — the same handles the evidence
    #: rail carries for every other read this platform makes.
    reads: tuple[str, ...] = ()
    cache_hit: bool = False
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# capabilities


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    """What this deployment can compute, before anything is asked of it."""

    certified_dimensions: tuple[str, ...]
    uncertified_dimensions: tuple[str, ...]
    metrics: tuple[str, ...]
    entity_grains: tuple[str, ...]
    date_bases: tuple[str, ...]
    concepts: tuple[str, ...]
    as_of_reads: bool
    server_side_top_n: bool
    cross_entity_ratio_of_sums: bool
    suppression_threshold: int
    statement: str
    provenance: DiscoveryProvenance


# ---------------------------------------------------------------------------
# concept → path


@dataclass(frozen=True, slots=True)
class ConceptExpression:
    """One certified way this warehouse could express a concept.

    ``strength`` is the pack's declared evidence strength of this field *as
    evidence for this concept* — a CARC is the direct representation of a
    denial and only a proxy for coordination of benefits. ``coverage`` is
    what the warehouse does with it: a certified proxy nobody populates is
    not a path, and only the coverage figure can say so.
    """

    field_id: str
    label: str
    #: ``"dimension"`` — a certified breakdown, censused; ``"population"`` —
    #: a governed measure whose own definition IS this field, measured as a
    #: share of its entity; ``"measure"`` — a quantity, which has no coverage
    #: of its own; ``"absent"`` — the catalog does not carry it at all.
    kind: str
    binding_state: str
    strength: EvidenceGrade
    certified: bool = False
    #: Units carrying a value for this field, out of the units read.
    populated: int = 0
    units: int = 0
    distinct_values: int = 0
    coverage: Decimal | None = None
    #: The governed measure this path runs through, when the concept is
    #: carried by a measure's own definition rather than by a breakdown.
    measure_id: str = ""
    #: Governed measures that DECLARE this breakdown, when no census could
    #: be taken over it. Coverage and availability are different facts and
    #: conflating them produced a wrong negative: a dimension every A/R
    #: measure is cut by, but which no count metric declares, was reported
    #: to the reader as one that "cannot be broken out here" — while the
    #: same run broke it out. What is true is narrower and is said instead:
    #: nothing here can COUNT how much of the data carries it.
    declared_by: tuple[str, ...] = ()

    @property
    def is_populated(self) -> bool:
        return self.coverage is not None and self.coverage > 0


@dataclass(frozen=True, slots=True)
class ConceptResolution:
    """A concept term, and where this warehouse actually carries it."""

    term: str
    concept_id: str | None
    expressions: tuple[ConceptExpression, ...]
    #: The expression a plan should read this concept through: the best
    #: evidence strength among the ones the warehouse populates, ties broken
    #: on coverage and then on the field id. ``None`` when nothing certified
    #: is populated — which is a refusal with a named data gap, not a
    #: weaker path.
    preferred: ConceptExpression | None
    statement: str
    provenance: DiscoveryProvenance

    @property
    def has_path(self) -> bool:
        return self.preferred is not None


# ---------------------------------------------------------------------------
# dimension value census


@dataclass(frozen=True, slots=True)
class DimensionValue:
    value: str
    units: int
    share: Decimal


@dataclass(frozen=True, slots=True)
class DimensionCensus:
    """The values a dimension takes here, and how much of the data has one."""

    dimension_id: str
    label: str
    certified: bool
    entity: str
    ruler: str
    values: tuple[DimensionValue, ...]
    cardinality: int
    populated: int
    units: int
    coverage: Decimal
    #: True when the census listed only the largest values — the count is
    #: still exact, the list is not the whole set.
    truncated: bool
    statement: str
    provenance: DiscoveryProvenance

    def holds(self, value: str) -> bool:
        return any(entry.value == value for entry in self.values)


# ---------------------------------------------------------------------------
# measure availability


@dataclass(frozen=True, slots=True)
class MeasureAvailability:
    """One governed measure, judged against a population and a grain."""

    metric_id: str
    unit: str
    kind: str
    entity_grain: str
    sign: str
    available: bool
    #: Why not, in the reader's terms, when ``available`` is False.
    reason: str = ""
    #: Certified cuts this measure declares — the closed set of ways a plan
    #: may slice it.
    cuts: tuple[str, ...] = ()
    date_bases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MeasureProfile:
    """Which governed measures can compute over a named population."""

    population: str
    grain: str | None
    measures: tuple[MeasureAvailability, ...]
    statement: str
    provenance: DiscoveryProvenance

    @property
    def available(self) -> tuple[MeasureAvailability, ...]:
        return tuple(m for m in self.measures if m.available)

    def for_metric(self, metric_id: str) -> MeasureAvailability | None:
        for measure in self.measures:
            if measure.metric_id == metric_id:
                return measure
        return None


# ---------------------------------------------------------------------------
# subject presence


@dataclass(frozen=True, slots=True)
class SubjectMatch:
    dimension_id: str
    dimension_label: str
    value: str
    units: int


@dataclass(frozen=True, slots=True)
class SubjectPresence:
    """Whether a name the analyst used is a value this data holds."""

    term: str
    matches: tuple[SubjectMatch, ...]
    statement: str
    provenance: DiscoveryProvenance

    @property
    def found(self) -> bool:
        return bool(self.matches)


# ---------------------------------------------------------------------------
# the walk's own record of what it learned


@dataclass(frozen=True, slots=True)
class DiscoveryNote:
    """One discovery finding, as one plain sentence plus its provenance.

    This is the shape that reaches a preview card and a trace. It is
    deliberately the *only* shape that does: a surface that reached into a
    census to phrase its own sentence would be a second place the same fact
    is worded.
    """

    kind: DiscoveryKind
    subject: str
    statement: str
    request_key: str
    reads: tuple[str, ...] = field(default=())
    duration_ms: int = 0
    cache_hit: bool = False
