"""Lossless, versioned serialization of frozen domain/kernel dataclasses ↔ JSONB.

This is the same *spirit* as :func:`revi_kernel.probes.canonicalize` (typed,
tagged, deterministic) but built to be **round-trippable**: every stored value
decodes back to an object that compares equal to the original. A frame or
context that does not round-trip exactly is data corruption, so the tag scheme
is explicit and closed:

======================  =====================================================
Python value            JSON encoding
======================  =====================================================
``None/bool/int/str``   as-is (``float`` also passes through for payloads)
``Decimal``             ``{"__decimal__": "3.25"}``
``date``                ``{"__date__": "2026-08-07"}``
``datetime``            ``{"__datetime__": "2026-08-07T12:00:00+00:00"}``
registered ``Enum``     ``{"__enum__": "TurnClass", "value": "refinement"}``
``tuple``               plain JSON array (domain collections are tuples)
``list``                ``{"__list__": [...]}`` (payload mappings only)
``Mapping``             ``{"__map__": {...}}`` (string keys only)
registered dataclass    ``{"__type__": "Predicate", <field>: <encoded>, ...}``
======================  =====================================================

Tagged unions (``FilterExpr``, ``Refinement``, ``ProvenanceRef``) fall out of
the dataclass rule for free — the ``__type__`` tag discriminates the variant.

Every stored envelope carries ``serde_version`` (see :data:`SERDE_VERSION`);
:func:`from_stored` refuses versions it does not understand instead of
guessing. Decoding an unknown ``__type__`` or a malformed shape raises
:class:`SerdeError` rather than returning partial data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from revi_investigation.application.ports import RegisteredReferent, TraceRecord
from revi_investigation.domain.context import (
    AnalysisSpec,
    AskedDirection,
    AskedMagnitude,
    AskedOrder,
    ContextPin,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    InvestigationStatus,
    RefinementEdge,
    Session,
)
from revi_investigation.domain.refinements import (
    AddFilter,
    DrillInto,
    Expand,
    Explain,
    Pivot,
    RankBy,
    RemoveFilter,
    ResetContext,
    SetComparison,
    SetDimensions,
    SetGrain,
    SetWindow,
)
from revi_investigation.domain.settings import SessionSettings
from revi_investigation.domain.turns import TurnClass
from revi_investigation_contracts.settings import EvidenceDepth, NarrativeDepth
from revi_kernel.cohort import CohortDefinition, CohortMaterialization, CohortRef
from revi_kernel.filters import And, InCohort, Not, Or, Predicate, PredicateOp
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
    TransformProvenance,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import (
    AggregationProbe,
    MeasurePredicate,
    Ordering,
    RowEvidenceProbe,
    SampleMethod,
    SamplePolicy,
    SnapshotProbe,
)
from revi_kernel.refs import (
    DateBasisRef,
    DimensionRef,
    EntityGrain,
    FieldRef,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
    TimeBucket,
)
from revi_kernel.scope import (
    AbsoluteRange,
    CalendarPolicy,
    CalendarRef,
    Comparison,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    TimeWindow,
)
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

SERDE_VERSION = 1

Json = None | bool | int | float | str | list["Json"] | dict[str, "Json"]


class SerdeError(Exception):
    """Raised when a value cannot be encoded or a stored payload cannot be
    decoded losslessly."""


_DATACLASSES: tuple[type, ...] = (
    # kernel — refs
    DimensionRef,
    MetricRef,
    FieldRef,
    DateBasisRef,
    Grain,
    ReferentId,
    # kernel — filters
    Predicate,
    InCohort,
    And,
    Or,
    Not,
    # kernel — scope
    CalendarRef,
    AbsoluteRange,
    RelativeRange,
    TimeWindow,
    Comparison,
    # kernel — cohort / watermark
    CohortDefinition,
    CohortMaterialization,
    CohortRef,
    DataWatermark,
    WatermarkEpoch,
    # kernel — frames
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
    TransformProvenance,
    EvidenceFrame,
    # kernel — probes (may appear in trace payloads)
    MeasurePredicate,
    Ordering,
    SamplePolicy,
    AggregationProbe,
    SnapshotProbe,
    RowEvidenceProbe,
    # investigation — context + records
    PackVersionRef,
    ContextPin,
    InvestigationContext,
    AnalysisSpec,
    Session,
    SessionSettings,
    Finding,
    RefinementEdge,
    Investigation,
    # investigation — the closed refinement-operator union
    SetDimensions,
    AddFilter,
    RemoveFilter,
    SetWindow,
    SetComparison,
    SetGrain,
    DrillInto,
    Pivot,
    Explain,
    RankBy,
    Expand,
    ResetContext,
    # application-port records
    RegisteredReferent,
    TraceRecord,
)

_ENUMS: tuple[type[Enum], ...] = (
    PredicateOp,
    EntityGrain,
    TimeBucket,
    ReferentKind,
    CalendarPolicy,
    TimeUnit,
    RangeMode,
    ComparisonKind,
    EvidenceGrade,
    SampleMethod,
    InvestigationStatus,
    TurnClass,
    NarrativeDepth,
    EvidenceDepth,
    # the movement a question asked about, carried on the AnalysisSpec so a
    # rehydrated turn selects rows the same way the live one did
    AskedDirection,
    AskedMagnitude,
    # …and the order it asked a ranking to arrive in, for the same reason:
    # a rehydrated turn must narrate "ranks first" the way the live one did
    AskedOrder,
)

_DATACLASS_REGISTRY: dict[str, type] = {cls.__name__: cls for cls in _DATACLASSES}
_ENUM_REGISTRY: dict[str, type[Enum]] = {cls.__name__: cls for cls in _ENUMS}

if len(_DATACLASS_REGISTRY) != len(_DATACLASSES):  # pragma: no cover - registry sanity
    raise AssertionError("duplicate dataclass names in serde registry")
if len(_ENUM_REGISTRY) != len(_ENUMS):  # pragma: no cover - registry sanity
    raise AssertionError("duplicate enum names in serde registry")

_TAGS = ("__type__", "__enum__", "__decimal__", "__date__", "__datetime__", "__list__", "__map__")


# --- encoding ---------------------------------------------------------------


def encode(value: object) -> Json:
    """Encode a supported value to a JSON-safe structure (no envelope)."""
    # Enums FIRST. A ``StrEnum`` member is a ``str`` and an ``IntEnum``
    # member is an ``int``, so the scalar branch below used to swallow them
    # and emit a bare "complete" where the tag scheme promises
    # ``{"__enum__": "InvestigationStatus", ...}``. In memory that was
    # invisible (``encode`` returned the member itself); through a JSONB
    # column it decoded back as a plain string, and every ``is`` comparison
    # against an enum member silently went False — a Postgres deployment
    # answered "there is no prior answer to refine" on turns whose parent
    # was sitting right there, because ``status is COMPLETE`` was False.
    if isinstance(value, Enum):
        name = type(value).__name__
        if name not in _ENUM_REGISTRY:
            raise SerdeError(f"enum {name} is not registered for serialization")
        return {"__enum__": name, "value": encode(value.value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if name not in _DATACLASS_REGISTRY:
            raise SerdeError(f"dataclass {name} is not registered for serialization")
        payload: dict[str, Json] = {"__type__": name}
        for f in fields(value):
            payload[f.name] = encode(getattr(value, f.name))
        return payload
    if isinstance(value, tuple):
        return [encode(item) for item in value]
    if isinstance(value, list):
        return {"__list__": [encode(item) for item in value]}
    if isinstance(value, Mapping):
        encoded: dict[str, Json] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SerdeError(f"mapping keys must be str, got {type(key).__name__}")
            encoded[key] = encode(item)
        return {"__map__": encoded}
    raise SerdeError(f"cannot serialize value of type {type(value).__name__}")


# --- decoding ---------------------------------------------------------------


def decode(data: Json) -> object:
    """Decode a structure produced by :func:`encode`."""
    if data is None or isinstance(data, (bool, int, float, str)):
        return data
    if isinstance(data, list):
        return tuple(decode(item) for item in data)
    if isinstance(data, dict):
        return _decode_dict(data)
    raise SerdeError(f"cannot decode value of type {type(data).__name__}")  # pragma: no cover


def _decode_dict(data: dict[str, Json]) -> object:
    if "__type__" in data:
        return _decode_dataclass(data)
    if "__enum__" in data:
        name = data["__enum__"]
        if not isinstance(name, str) or name not in _ENUM_REGISTRY:
            raise SerdeError(f"unknown enum tag {name!r}")
        return _ENUM_REGISTRY[name](decode(data.get("value")))
    if "__decimal__" in data:
        raw = data["__decimal__"]
        if not isinstance(raw, str):
            raise SerdeError("__decimal__ tag requires a string value")
        return Decimal(raw)
    if "__date__" in data:
        raw = data["__date__"]
        if not isinstance(raw, str):
            raise SerdeError("__date__ tag requires a string value")
        return date.fromisoformat(raw)
    if "__datetime__" in data:
        raw = data["__datetime__"]
        if not isinstance(raw, str):
            raise SerdeError("__datetime__ tag requires a string value")
        return datetime.fromisoformat(raw)
    if "__list__" in data:
        items = data["__list__"]
        if not isinstance(items, list):
            raise SerdeError("__list__ tag requires an array value")
        return [decode(item) for item in items]
    if "__map__" in data:
        mapping = data["__map__"]
        if not isinstance(mapping, dict):
            raise SerdeError("__map__ tag requires an object value")
        return {key: decode(item) for key, item in mapping.items()}
    raise SerdeError(f"object without a recognized serde tag: keys={sorted(data)!r}")


def _decode_dataclass(data: dict[str, Json]) -> object:
    name = data["__type__"]
    if not isinstance(name, str) or name not in _DATACLASS_REGISTRY:
        raise SerdeError(f"unknown dataclass tag {name!r}")
    cls = _DATACLASS_REGISTRY[name]
    known = {f.name for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, item in data.items():
        if key == "__type__":
            continue
        if key not in known:
            raise SerdeError(f"{name} has no field {key!r}")
        kwargs[key] = decode(item)
    try:
        return cls(**kwargs)
    except (TypeError, ValueError) as exc:
        raise SerdeError(f"cannot reconstruct {name}: {exc}") from exc


# --- versioned envelope -----------------------------------------------------


def to_stored(value: object) -> dict[str, Json]:
    """Wrap a value in the versioned envelope stored in every JSONB column."""
    return {"serde_version": SERDE_VERSION, "value": encode(value)}


def from_stored(envelope: Mapping[str, Any]) -> object:
    """Decode a stored envelope, refusing versions this code does not know."""
    version = envelope.get("serde_version")
    if version != SERDE_VERSION:
        raise SerdeError(f"unsupported serde_version {version!r} (expected {SERDE_VERSION})")
    if "value" not in envelope:
        raise SerdeError("stored envelope has no 'value' key")
    return decode(envelope["value"])


def from_stored_as[T](cls: type[T], envelope: Mapping[str, Any]) -> T:
    """Decode an envelope and assert the reconstructed value's type."""
    value = from_stored(envelope)
    if not isinstance(value, cls):
        raise SerdeError(f"expected {cls.__name__}, decoded {type(value).__name__}")
    return value
