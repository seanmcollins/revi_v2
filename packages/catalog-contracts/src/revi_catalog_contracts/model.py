"""Semantic-catalog model (design §4.1, §6.1).

Frozen dataclasses mirroring the catalog YAML vocabulary exactly:

- entities: entity grain → curated base view, primary key, date-basis column
  bindings, and certified join paths between grains;
- dimensions: certified (and deliberately uncertified) group-by columns with
  synonyms, cardinality estimates, and PHI classes (``none|indirect|direct``,
  the YAML's vocabulary);
- measures: additive columns with aggregation, unit, and optional row filter;
- date bases: a window is a range *on a basis* — each basis maps to the column
  carrying it per entity (absent ⇒ not answerable at that grain);
- calendar: the business-day calendar binding;
- suppression: the small-cell suppression policy threshold.

``CatalogSnapshot`` follows the frozen-without-slots + ``__post_init__`` index
pattern of ``revi_pack.domain.PackSnapshot``: equality and hashing stay
content-based while lookups are O(1), and integrity invariants are enforced at
construction so an invalid snapshot is unrepresentable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from revi_kernel.refs import DateBasisRef, EntityGrain

# ---------------------------------------------------------------------------
# term normalization (same rules as pack aliases: lowercase, non-alphanumeric
# runs collapse to a single underscore, leading/trailing stripped)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_synonym(text: str) -> str:
    """``"Payor  Type"`` → ``"payor_type"``, ``"CO-45"`` → ``"co_45"``."""
    return _NON_ALNUM.sub("_", text.lower()).strip("_")


# ---------------------------------------------------------------------------
# vocabulary enums (mirroring the YAML exactly)


class PhiClass(StrEnum):
    """PHI classification per the catalog YAML (``phi: none|indirect|direct``).

    ``NONE`` is safe to expose; ``INDIRECT`` narrows re-identification and is
    masked (deterministic token); ``DIRECT`` identifies a person and is
    suppressed outright. Masking happens before values leave the boundary
    toward models or UI (design §4.1) — see :mod:`revi_catalog_contracts.masking`.
    """

    NONE = "none"
    INDIRECT = "indirect"
    DIRECT = "direct"


class MeasureAggregation(StrEnum):
    SUM = "sum"
    COUNT_DISTINCT = "count_distinct"


class DimensionKind(StrEnum):
    COLUMN = "column"
    DERIVED_BUCKET = "derived_bucket"  # computed at probe time (e.g. ar_age_bucket)


# ---------------------------------------------------------------------------
# definitions


@dataclass(frozen=True, slots=True)
class EntityDef:
    """One entity grain → its curated pre-joined base view.

    ``grain`` is the declared physical row grain from the YAML (the denial
    entity declares ``LINE`` — one row per denial record is a line-level
    record). Probes address entities through :attr:`addressing_grain`: the
    ``EntityGrain`` whose value equals the entity *name* when one exists
    (``denial`` → ``EntityGrain.DENIAL``), else the declared grain
    (``claim_line`` → ``EntityGrain.LINE``). This keeps grain → entity lookup
    a bijection even when two entities share a physical grain.
    """

    name: str
    grain: EntityGrain
    base_view: str
    primary_key: str
    description: str = ""
    date_basis_columns: tuple[tuple[str, str], ...] = ()  # (basis id, column), sorted

    def __post_init__(self) -> None:
        named = (("name", self.name), ("base_view", self.base_view), ("primary_key", self.primary_key))
        for label, value in named:
            if not value:
                raise ValueError(f"EntityDef.{label} must be non-empty")

    @property
    def addressing_grain(self) -> EntityGrain:
        try:
            return EntityGrain(self.name)
        except ValueError:
            return self.grain

    def date_basis_column(self, basis: DateBasisRef) -> str | None:
        wanted = basis.id.lower()
        for basis_id, column in self.date_basis_columns:
            if basis_id == wanted:
                return column
        return None


@dataclass(frozen=True, slots=True)
class JoinPath:
    """A certified inter-entity path (pre-joined inside the base views; listed
    so planners and adapters can validate cohort flows between grains)."""

    from_entity: str
    to_entity: str
    on: str
    kind: str
    nullable: bool = False

    def __post_init__(self) -> None:
        if not self.from_entity or not self.to_entity or not self.on:
            raise ValueError("JoinPath requires from_entity, to_entity and on")


@dataclass(frozen=True, slots=True)
class DimensionDef:
    id: str
    label: str
    certified: bool
    entities: tuple[tuple[str, str], ...]  # (entity name, column)
    synonyms: tuple[str, ...] = ()
    cardinality_estimate: int = 1
    phi: PhiClass = PhiClass.NONE
    kind: DimensionKind = DimensionKind.COLUMN
    value_domain: tuple[str, ...] | None = None
    buckets: tuple[str, ...] | None = None
    description: str = ""
    uncertified_reason: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DimensionDef.id must be non-empty")
        if not self.entities:
            raise ValueError(f"dimension {self.id!r}: entities must be non-empty")
        if self.cardinality_estimate < 1:
            raise ValueError(f"dimension {self.id!r}: cardinality_estimate must be >= 1")
        if self.kind is DimensionKind.DERIVED_BUCKET and not self.buckets:
            raise ValueError(f"dimension {self.id!r}: derived_bucket dimensions must declare buckets")

    def column_for(self, entity_name: str) -> str | None:
        for name, column in self.entities:
            if name == entity_name:
                return column
        return None

    @property
    def lookup_terms(self) -> frozenset[str]:
        terms = {normalize_synonym(self.id), normalize_synonym(self.label)}
        terms.update(normalize_synonym(s) for s in self.synonyms)
        terms.discard("")
        return frozenset(terms)


@dataclass(frozen=True, slots=True)
class MeasureDef:
    """An additive measure: source column, optional row filter, aggregation.

    ``filter_sql`` is a catalog-governed SQL predicate over the entity's base
    view (e.g. ``txn_type = 'PAYMENT'``) — certified configuration, applied by
    adapters as a ``FILTER (WHERE …)`` clause.
    """

    id: str
    entity: str
    column: str
    aggregation: MeasureAggregation
    additive: bool
    unit: str
    synonyms: tuple[str, ...] = ()
    filter_sql: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("MeasureDef.id must be non-empty")
        if not self.entity or not self.column:
            raise ValueError(f"measure {self.id!r}: entity and column must be non-empty")
        if not self.unit:
            raise ValueError(f"measure {self.id!r}: unit must be non-empty")


@dataclass(frozen=True, slots=True)
class DateBasisDef:
    id: str  # lowercase, matching DateBasisRef ids ("service", "post", …)
    description: str = ""
    synonyms: tuple[str, ...] = ()
    columns: tuple[tuple[str, str], ...] = ()  # (entity name, column)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DateBasisDef.id must be non-empty")

    def column_for(self, entity_name: str) -> str | None:
        for name, column in self.columns:
            if name == entity_name:
                return column
        return None

    @property
    def lookup_terms(self) -> frozenset[str]:
        terms = {normalize_synonym(self.id)}
        terms.update(normalize_synonym(s) for s in self.synonyms)
        terms.discard("")
        return frozenset(terms)


@dataclass(frozen=True, slots=True)
class CalendarDef:
    table: str
    date_column: str
    range_start: date
    range_end: date
    columns: tuple[tuple[str, str], ...] = ()  # (role, column)
    week_convention: str = ""
    business_day_rule: str = ""
    holidays: tuple[date, ...] = ()
    policies: tuple[tuple[str, str], ...] = ()  # (policy name, description)

    def __post_init__(self) -> None:
        if not self.table or not self.date_column:
            raise ValueError("CalendarDef.table and date_column must be non-empty")
        if self.range_start > self.range_end:
            raise ValueError("CalendarDef.range_start must not be after range_end")


DEFAULT_SUPPRESSION_THRESHOLD = 11


@dataclass(frozen=True, slots=True)
class SuppressionPolicy:
    """Small-cell suppression: cells counting fewer than ``threshold`` entities
    are suppressed at the frame boundary (by the execution service, not the
    repository adapter)."""

    threshold: int = DEFAULT_SUPPRESSION_THRESHOLD

    def __post_init__(self) -> None:
        if self.threshold < 1:
            raise ValueError("SuppressionPolicy.threshold must be >= 1")


# ---------------------------------------------------------------------------
# snapshot


@dataclass(frozen=True)
class CatalogSnapshot:
    """The immutable loaded catalog.

    Frozen *without* ``slots`` on purpose: lookup indexes are built once in
    ``__post_init__`` (via ``object.__setattr__``) into non-init, non-compare
    fields, so equality and hashing stay content-based while lookups are O(1).
    """

    entities: tuple[EntityDef, ...]
    dimensions: tuple[DimensionDef, ...]
    measures: tuple[MeasureDef, ...]
    date_bases: tuple[DateBasisDef, ...]
    calendar: CalendarDef
    join_paths: tuple[JoinPath, ...] = ()
    suppression: SuppressionPolicy = SuppressionPolicy()

    _entities_by_name: dict[str, EntityDef] = field(init=False, repr=False, compare=False)
    _entities_by_grain: dict[EntityGrain, EntityDef] = field(init=False, repr=False, compare=False)
    _dimensions_by_id: dict[str, DimensionDef] = field(init=False, repr=False, compare=False)
    _measures_by_id: dict[str, MeasureDef] = field(init=False, repr=False, compare=False)
    _bases_by_id: dict[str, DateBasisDef] = field(init=False, repr=False, compare=False)
    _synonym_index: dict[str, tuple[DimensionDef, ...]] = field(init=False, repr=False, compare=False)
    _join_index: dict[tuple[str, str], JoinPath] = field(init=False, repr=False, compare=False)
    _declared_columns: dict[str, frozenset[str]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_entities_by_name", self._build_entity_names())
        object.__setattr__(self, "_entities_by_grain", self._build_entity_grains())
        object.__setattr__(self, "_dimensions_by_id", self._build_unique("dimension", self.dimensions))
        object.__setattr__(self, "_measures_by_id", self._build_unique("measure", self.measures))
        object.__setattr__(self, "_bases_by_id", self._build_unique("date basis", self.date_bases))
        object.__setattr__(self, "_synonym_index", self._build_synonym_index())
        object.__setattr__(self, "_join_index", self._build_join_index())
        object.__setattr__(self, "_declared_columns", self._build_declared_columns())
        self._check_references()

    # -- index construction ------------------------------------------------

    def _build_entity_names(self) -> dict[str, EntityDef]:
        index: dict[str, EntityDef] = {}
        for entity in self.entities:
            if entity.name in index:
                raise ValueError(f"duplicate entity name {entity.name!r} in catalog")
            index[entity.name] = entity
        return index

    def _build_entity_grains(self) -> dict[EntityGrain, EntityDef]:
        index: dict[EntityGrain, EntityDef] = {}
        for entity in self.entities:
            grain = entity.addressing_grain
            if grain in index:
                raise ValueError(
                    f"entities {index[grain].name!r} and {entity.name!r} both address grain {grain.value!r}"
                )
            index[grain] = entity
        return index

    def _build_unique[T: (DimensionDef, MeasureDef, DateBasisDef)](
        self, label: str, items: tuple[T, ...]
    ) -> dict[str, T]:
        index: dict[str, T] = {}
        for item in items:
            if item.id in index:
                raise ValueError(f"duplicate {label} id {item.id!r} in catalog")
            index[item.id] = item
        return index

    def _build_synonym_index(self) -> dict[str, tuple[DimensionDef, ...]]:
        index: dict[str, tuple[DimensionDef, ...]] = {}
        for dim in self.dimensions:
            for term in sorted(dim.lookup_terms):
                existing = index.get(term, ())
                if dim not in existing:
                    index[term] = (*existing, dim)
        return index

    def _build_join_index(self) -> dict[tuple[str, str], JoinPath]:
        index: dict[tuple[str, str], JoinPath] = {}
        for path in self.join_paths:
            key = (path.from_entity, path.to_entity)
            if key in index:
                raise ValueError(f"duplicate join path {path.from_entity!r} -> {path.to_entity!r}")
            index[key] = path
        return index

    def _build_declared_columns(self) -> dict[str, frozenset[str]]:
        """Every column the catalog binds per entity: primary key, dimension
        columns, measure columns, date-basis columns, and join-path columns.
        Adapters may reference base-view columns only from this set."""
        declared: dict[str, set[str]] = {e.name: {e.primary_key} for e in self.entities}
        for dim in self.dimensions:
            for entity_name, column in dim.entities:
                declared.setdefault(entity_name, set()).add(column)
        for measure in self.measures:
            declared.setdefault(measure.entity, set()).add(measure.column)
        for entity in self.entities:
            for _, column in entity.date_basis_columns:
                declared[entity.name].add(column)
        for path in self.join_paths:
            declared.setdefault(path.from_entity, set()).add(path.on)
        return {name: frozenset(columns) for name, columns in declared.items()}

    # -- integrity ---------------------------------------------------------

    def _check_references(self) -> None:
        for dim in self.dimensions:
            for entity_name, _ in dim.entities:
                if entity_name not in self._entities_by_name:
                    raise ValueError(f"dimension {dim.id!r} references unknown entity {entity_name!r}")
        for measure in self.measures:
            if measure.entity not in self._entities_by_name:
                raise ValueError(f"measure {measure.id!r} references unknown entity {measure.entity!r}")
        for basis in self.date_bases:
            for entity_name, _ in basis.columns:
                if entity_name not in self._entities_by_name:
                    raise ValueError(f"date basis {basis.id!r} references unknown entity {entity_name!r}")
        for path in self.join_paths:
            for entity_name in (path.from_entity, path.to_entity):
                if entity_name not in self._entities_by_name:
                    raise ValueError(f"join path references unknown entity {entity_name!r}")

    # -- lookups -----------------------------------------------------------

    def entity(self, grain: EntityGrain) -> EntityDef | None:
        """The entity addressed by this grain (see ``EntityDef.addressing_grain``)."""
        return self._entities_by_grain.get(grain)

    def entity_named(self, name: str) -> EntityDef | None:
        return self._entities_by_name.get(name)

    def dimension(self, dimension_id: str) -> DimensionDef | None:
        return self._dimensions_by_id.get(dimension_id)

    def measure(self, measure_id: str) -> MeasureDef | None:
        return self._measures_by_id.get(measure_id)

    def date_basis(self, basis_id: str) -> DateBasisDef | None:
        return self._bases_by_id.get(basis_id.lower())

    def dimension_for_synonym(self, text: str) -> DimensionDef | None:
        """Resolve analyst language to a dimension via normalized lookup.

        Returns ``None`` when the term is unknown **or ambiguous** (e.g.
        "service area" is a synonym of both facility and service_line);
        ambiguity is a planner concern, surfaced via
        :meth:`dimensions_for_synonym`.
        """
        matches = self._synonym_index.get(normalize_synonym(text), ())
        return matches[0] if len(matches) == 1 else None

    def dimensions_for_synonym(self, text: str) -> tuple[DimensionDef, ...]:
        return self._synonym_index.get(normalize_synonym(text), ())

    def is_certified(self, dimension_id: str) -> bool:
        """False for uncertified *and* unknown dimensions (fail closed)."""
        dim = self._dimensions_by_id.get(dimension_id)
        return dim is not None and dim.certified

    def phi_class(self, dimension_id: str) -> PhiClass:
        dim = self._dimensions_by_id.get(dimension_id)
        return dim.phi if dim is not None else PhiClass.NONE

    def date_basis_column(self, entity: EntityGrain | str, basis: DateBasisRef) -> str | None:
        """The column carrying ``basis`` on the entity's base view, or ``None``
        when the basis is not answerable at that grain."""
        entity_def = (
            self._entities_by_grain.get(entity)
            if isinstance(entity, EntityGrain)
            else self._entities_by_name.get(entity)
        )
        return entity_def.date_basis_column(basis) if entity_def is not None else None

    def join_column(self, from_entity: str, to_entity: str) -> str | None:
        """The certified join column from one entity's view to another entity
        (e.g. ``denial`` → ``claim`` on ``claim_id``), or ``None``."""
        path = self._join_index.get((from_entity, to_entity))
        return path.on if path is not None else None

    def declared_columns(self, entity_name: str) -> frozenset[str]:
        return self._declared_columns.get(entity_name, frozenset())
