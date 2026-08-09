"""Strict YAML → :class:`CatalogSnapshot` loader.

``load_catalog(path)`` reads the catalog directory (``entities.yaml``,
``date_bases.yaml``, ``dimensions.yaml``, ``measures.yaml``, ``calendar.yaml``
plus an optional ``suppression.yaml``) and builds an immutable snapshot.

Strictness rules:

- unknown keys are rejected, with the file and object context in the error;
- every value is type-checked (this is governed configuration — a typo must
  fail the load, not silently misbind a probe);
- referential integrity (dimensions/measures/date bases naming unknown
  entities, duplicate ids, grain-addressing collisions) is enforced by
  ``CatalogSnapshot`` itself and re-raised with directory context.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

import yaml

from revi_catalog_contracts.model import (
    CalendarDef,
    CatalogSnapshot,
    DateBasisDef,
    DimensionDef,
    DimensionKind,
    EntityDef,
    JoinPath,
    MeasureAggregation,
    MeasureDef,
    PhiClass,
    SuppressionPolicy,
)
from revi_kernel.refs import EntityGrain

REQUIRED_FILES = ("entities.yaml", "date_bases.yaml", "dimensions.yaml", "measures.yaml", "calendar.yaml")
OPTIONAL_SUPPRESSION_FILE = "suppression.yaml"


class CatalogLoadError(ValueError):
    """A catalog file failed strict validation. The message always names the
    offending file and object."""


# ---------------------------------------------------------------------------
# strict primitives


def _mapping(value: object, ctx: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogLoadError(f"{ctx}: expected a mapping, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise CatalogLoadError(f"{ctx}: mapping keys must be strings, got {key!r}")
    return cast(dict[str, object], value)


def _check_keys(mapping: dict[str, object], required: set[str], optional: set[str], ctx: str) -> None:
    unknown = sorted(set(mapping) - required - optional)
    if unknown:
        raise CatalogLoadError(f"{ctx}: unknown key(s) {unknown}")
    missing = sorted(required - set(mapping))
    if missing:
        raise CatalogLoadError(f"{ctx}: missing required key(s) {missing}")


def _str(value: object, ctx: str) -> str:
    if not isinstance(value, str):
        raise CatalogLoadError(f"{ctx}: expected a string, got {type(value).__name__}")
    return value


def _bool(value: object, ctx: str) -> bool:
    if not isinstance(value, bool):
        raise CatalogLoadError(f"{ctx}: expected a boolean, got {type(value).__name__}")
    return value


def _int(value: object, ctx: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogLoadError(f"{ctx}: expected an integer, got {type(value).__name__}")
    return value


def _date(value: object, ctx: str) -> date:
    if not isinstance(value, date):
        raise CatalogLoadError(f"{ctx}: expected an ISO date, got {type(value).__name__}")
    return value


def _str_tuple(value: object, ctx: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CatalogLoadError(f"{ctx}: expected a list, got {type(value).__name__}")
    return tuple(_str(item, f"{ctx}[{i}]") for i, item in enumerate(value))


def _str_pairs(value: object, ctx: str) -> tuple[tuple[str, str], ...]:
    mapping = _mapping(value, ctx)
    return tuple((key, _str(val, f"{ctx}.{key}")) for key, val in mapping.items())


def _load_file(directory: Path, name: str) -> dict[str, object]:
    path = directory / name
    if not path.is_file():
        raise CatalogLoadError(f"{path}: missing catalog file")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - malformed YAML
        raise CatalogLoadError(f"{path}: invalid YAML ({exc})") from exc
    return _mapping(raw, str(path))


# ---------------------------------------------------------------------------
# per-file parsers


def _parse_entities(directory: Path) -> tuple[list[EntityDef], tuple[JoinPath, ...]]:
    ctx = str(directory / "entities.yaml")
    doc = _load_file(directory, "entities.yaml")
    _check_keys(doc, {"version", "entities"}, {"join_paths"}, ctx)
    entities: list[EntityDef] = []
    for name, raw in _mapping(doc["entities"], f"{ctx}: entities").items():
        ectx = f"{ctx}: entity {name!r}"
        body = _mapping(raw, ectx)
        _check_keys(
            body, {"grain", "base_view", "primary_key"}, {"description", "declared_columns"}, ectx
        )
        grain_name = _str(body["grain"], f"{ectx}.grain")
        try:
            grain = EntityGrain[grain_name]
        except KeyError:
            raise CatalogLoadError(f"{ectx}: unknown grain {grain_name!r}") from None
        entities.append(
            EntityDef(
                name=name,
                grain=grain,
                base_view=_str(body["base_view"], f"{ectx}.base_view"),
                primary_key=_str(body["primary_key"], f"{ectx}.primary_key"),
                description=_str(body.get("description", ""), f"{ectx}.description"),
                extra_columns=_str_tuple(
                    body.get("declared_columns", []), f"{ectx}.declared_columns"
                ),
            )
        )
    join_paths: list[JoinPath] = []
    raw_paths = doc.get("join_paths", [])
    if not isinstance(raw_paths, list):
        raise CatalogLoadError(f"{ctx}: join_paths must be a list")
    for i, raw in enumerate(raw_paths):
        jctx = f"{ctx}: join_paths[{i}]"
        if isinstance(raw, dict):
            # YAML 1.1 parses the bare key ``on:`` as boolean True; restore it.
            raw = {("on" if key is True else key): value for key, value in raw.items()}
        body = _mapping(raw, jctx)
        _check_keys(body, {"from", "to", "on", "kind"}, {"nullable"}, jctx)
        join_paths.append(
            JoinPath(
                from_entity=_str(body["from"], f"{jctx}.from"),
                to_entity=_str(body["to"], f"{jctx}.to"),
                on=_str(body["on"], f"{jctx}.on"),
                kind=_str(body["kind"], f"{jctx}.kind"),
                nullable=_bool(body.get("nullable", False), f"{jctx}.nullable"),
            )
        )
    return entities, tuple(join_paths)


def _parse_date_bases(directory: Path) -> tuple[DateBasisDef, ...]:
    ctx = str(directory / "date_bases.yaml")
    doc = _load_file(directory, "date_bases.yaml")
    _check_keys(doc, {"version", "date_bases"}, set(), ctx)
    bases: list[DateBasisDef] = []
    for name, raw in _mapping(doc["date_bases"], f"{ctx}: date_bases").items():
        bctx = f"{ctx}: date basis {name!r}"
        body = _mapping(raw, bctx)
        _check_keys(body, {"columns"}, {"description", "synonyms"}, bctx)
        bases.append(
            DateBasisDef(
                id=name.lower(),
                description=_str(body.get("description", ""), f"{bctx}.description"),
                synonyms=_str_tuple(body.get("synonyms", []), f"{bctx}.synonyms"),
                columns=_str_pairs(body["columns"], f"{bctx}.columns"),
            )
        )
    return tuple(bases)


def _parse_dimensions(directory: Path) -> tuple[DimensionDef, ...]:
    ctx = str(directory / "dimensions.yaml")
    doc = _load_file(directory, "dimensions.yaml")
    _check_keys(doc, {"version", "dimensions"}, set(), ctx)
    dims: list[DimensionDef] = []
    for dim_id, raw in _mapping(doc["dimensions"], f"{ctx}: dimensions").items():
        dctx = f"{ctx}: dimension {dim_id!r}"
        body = _mapping(raw, dctx)
        _check_keys(
            body,
            {"label", "certified", "entities", "phi"},
            {
                "synonyms",
                "cardinality_estimate",
                "value_domain",
                "kind",
                "description",
                "buckets",
                "companion_dimensions",
                "uncertified_reason",
            },
            dctx,
        )
        phi_name = _str(body["phi"], f"{dctx}.phi")
        try:
            phi = PhiClass(phi_name)
        except ValueError:
            raise CatalogLoadError(f"{dctx}: unknown phi class {phi_name!r}") from None
        kind_name = _str(body.get("kind", DimensionKind.COLUMN.value), f"{dctx}.kind")
        try:
            kind = DimensionKind(kind_name)
        except ValueError:
            raise CatalogLoadError(f"{dctx}: unknown kind {kind_name!r}") from None
        raw_domain = body.get("value_domain")
        raw_buckets = body.get("buckets")
        dims.append(
            DimensionDef(
                id=dim_id,
                label=_str(body["label"], f"{dctx}.label"),
                certified=_bool(body["certified"], f"{dctx}.certified"),
                entities=_str_pairs(body["entities"], f"{dctx}.entities"),
                synonyms=_str_tuple(body.get("synonyms", []), f"{dctx}.synonyms"),
                cardinality_estimate=_int(
                    body.get("cardinality_estimate", 1), f"{dctx}.cardinality_estimate"
                ),
                phi=phi,
                kind=kind,
                value_domain=None if raw_domain is None else _str_tuple(raw_domain, f"{dctx}.value_domain"),
                buckets=None if raw_buckets is None else _str_tuple(raw_buckets, f"{dctx}.buckets"),
                companion_dimensions=_str_tuple(
                    body.get("companion_dimensions", []), f"{dctx}.companion_dimensions"
                ),
                description=_str(body.get("description", ""), f"{dctx}.description"),
                uncertified_reason=_str(body.get("uncertified_reason", ""), f"{dctx}.uncertified_reason"),
            )
        )
    return tuple(dims)


def _parse_measures(directory: Path) -> tuple[MeasureDef, ...]:
    ctx = str(directory / "measures.yaml")
    doc = _load_file(directory, "measures.yaml")
    _check_keys(doc, {"version", "measures"}, set(), ctx)
    measures: list[MeasureDef] = []
    for measure_id, raw in _mapping(doc["measures"], f"{ctx}: measures").items():
        mctx = f"{ctx}: measure {measure_id!r}"
        body = _mapping(raw, mctx)
        _check_keys(
            body, {"entity", "column", "aggregation", "additive", "unit"}, {"synonyms", "filter"}, mctx
        )
        agg_name = _str(body["aggregation"], f"{mctx}.aggregation")
        try:
            aggregation = MeasureAggregation(agg_name)
        except ValueError:
            raise CatalogLoadError(f"{mctx}: unknown aggregation {agg_name!r}") from None
        raw_filter = body.get("filter")
        measures.append(
            MeasureDef(
                id=measure_id,
                entity=_str(body["entity"], f"{mctx}.entity"),
                column=_str(body["column"], f"{mctx}.column"),
                aggregation=aggregation,
                additive=_bool(body["additive"], f"{mctx}.additive"),
                unit=_str(body["unit"], f"{mctx}.unit"),
                synonyms=_str_tuple(body.get("synonyms", []), f"{mctx}.synonyms"),
                filter_sql=None if raw_filter is None else _str(raw_filter, f"{mctx}.filter"),
            )
        )
    return tuple(measures)


def _parse_calendar(directory: Path) -> CalendarDef:
    ctx = str(directory / "calendar.yaml")
    doc = _load_file(directory, "calendar.yaml")
    _check_keys(doc, {"version", "calendar"}, set(), ctx)
    body = _mapping(doc["calendar"], f"{ctx}: calendar")
    _check_keys(
        body,
        {"table", "date_column", "range", "columns"},
        {"week_convention", "business_day_rule", "holidays", "policies"},
        f"{ctx}: calendar",
    )
    range_body = _mapping(body["range"], f"{ctx}: calendar.range")
    _check_keys(range_body, {"start", "end"}, set(), f"{ctx}: calendar.range")
    raw_holidays = body.get("holidays", [])
    if not isinstance(raw_holidays, list):
        raise CatalogLoadError(f"{ctx}: calendar.holidays must be a list")
    policies: list[tuple[str, str]] = []
    for name, raw in _mapping(body.get("policies", {}), f"{ctx}: calendar.policies").items():
        pctx = f"{ctx}: calendar policy {name!r}"
        policy_body = _mapping(raw, pctx)
        _check_keys(policy_body, set(), {"description"}, pctx)
        policies.append((name, _str(policy_body.get("description", ""), f"{pctx}.description")))
    return CalendarDef(
        table=_str(body["table"], f"{ctx}: calendar.table"),
        date_column=_str(body["date_column"], f"{ctx}: calendar.date_column"),
        range_start=_date(range_body["start"], f"{ctx}: calendar.range.start"),
        range_end=_date(range_body["end"], f"{ctx}: calendar.range.end"),
        columns=_str_pairs(body["columns"], f"{ctx}: calendar.columns"),
        week_convention=_str(body.get("week_convention", ""), f"{ctx}: calendar.week_convention"),
        business_day_rule=_str(body.get("business_day_rule", ""), f"{ctx}: calendar.business_day_rule"),
        holidays=tuple(_date(d, f"{ctx}: calendar.holidays[{i}]") for i, d in enumerate(raw_holidays)),
        policies=tuple(policies),
    )


def _parse_suppression(directory: Path) -> SuppressionPolicy:
    path = directory / OPTIONAL_SUPPRESSION_FILE
    if not path.is_file():
        return SuppressionPolicy()
    ctx = str(path)
    doc = _load_file(directory, OPTIONAL_SUPPRESSION_FILE)
    _check_keys(doc, {"threshold"}, {"version"}, ctx)
    return SuppressionPolicy(threshold=_int(doc["threshold"], f"{ctx}.threshold"))


# ---------------------------------------------------------------------------
# entry point


def load_catalog(path: str | Path) -> CatalogSnapshot:
    """Load a catalog directory into an immutable, integrity-checked snapshot."""
    directory = Path(path)
    if not directory.is_dir():
        raise CatalogLoadError(f"{directory}: catalog directory does not exist")
    entities, join_paths = _parse_entities(directory)
    date_bases = _parse_date_bases(directory)
    dimensions = _parse_dimensions(directory)
    measures = _parse_measures(directory)
    calendar = _parse_calendar(directory)
    suppression = _parse_suppression(directory)

    # Invert basis → entity → column into per-entity bindings.
    by_entity: dict[str, list[tuple[str, str]]] = {e.name: [] for e in entities}
    for basis in date_bases:
        for entity_name, column in basis.columns:
            if entity_name not in by_entity:
                raise CatalogLoadError(
                    f"{directory / 'date_bases.yaml'}: date basis {basis.id!r} "
                    f"references unknown entity {entity_name!r}"
                )
            by_entity[entity_name].append((basis.id, column))
    bound_entities = tuple(
        EntityDef(
            name=e.name,
            grain=e.grain,
            base_view=e.base_view,
            primary_key=e.primary_key,
            description=e.description,
            date_basis_columns=tuple(sorted(by_entity[e.name])),
            extra_columns=e.extra_columns,
        )
        for e in entities
    )
    try:
        return CatalogSnapshot(
            entities=bound_entities,
            dimensions=dimensions,
            measures=measures,
            date_bases=date_bases,
            calendar=calendar,
            join_paths=join_paths,
            suppression=suppression,
        )
    except ValueError as exc:
        raise CatalogLoadError(f"{directory}: {exc}") from exc
