"""Load one pack layer from a directory of YAML files.

Layout (everything except ``pack.yaml`` is optional per layer):

.. code-block:: text

    pack.yaml            # manifest: pack_id, version, kind, description
    concepts.yaml        # concepts: [...]        (overlays: alias patches too)
    codes.yaml           # codes: [...]
    bindings.yaml        # bindings: [...]
    metrics/*.yaml       # one MetricContract per file
    playbooks/*.yaml     # one Playbook per file
    policies.yaml        # conclusion_policies / ranking_policies / detector_policies
    presentation.yaml    # recipes: [...]
    filing_rules.yaml    # filing_rules: [...]

Validation is strict: unknown keys are rejected, every error names the file
and entry it came from. The loader parses and type-checks a *single* layer;
overlay legality (what an overlay may override) is merge policy
(:mod:`revi_pack.merge`), except for shapes a base layer can never contain
(alias patches, detector threshold overrides), which are rejected here.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

import yaml

from revi_calculation_contracts.contract import (
    Count,
    CountDistinct,
    Filtered,
    MeasureExpr,
    MetricContract,
    MetricKind,
    MetricUnit,
    SignConvention,
    Sum,
)
from revi_kernel.filters import And, FilterExpr, Not, Or, Predicate, PredicateOp, Scalar
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DateBasisRef, DimensionRef, EntityGrain, FieldRef
from revi_kernel.scope import RangeMode, RelativeRange, TimeUnit
from revi_pack.domain import (
    AliasOverride,
    BindingCandidate,
    BindingState,
    CodeDefinition,
    CodeSystem,
    Concept,
    ConclusionPolicy,
    DetectorOverride,
    DetectorPolicy,
    FilingRule,
    PackLayer,
    PackLayerKind,
    Playbook,
    PresentationRecipe,
    ProbeTemplate,
    RankingPolicy,
    SourceRef,
    TransformStep,
)
from revi_pack.errors import PackLoadError

MANIFEST_FILE = "pack.yaml"


# ---------------------------------------------------------------------------
# strict-parsing primitives


@contextmanager
def _located(ctx: str) -> Iterator[None]:
    """Convert a domain-constructor ValueError into a located PackLoadError;
    already-located PackLoadErrors pass through unwrapped."""
    try:
        yield
    except PackLoadError:
        raise
    except ValueError as exc:
        raise PackLoadError(f"{ctx}: {exc}") from exc


def _mapping(value: object, ctx: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PackLoadError(f"{ctx}: expected a mapping, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise PackLoadError(f"{ctx}: mapping keys must be strings, got {key!r}")
    return dict(value)


def _sequence(value: object, ctx: str) -> list[object]:
    if not isinstance(value, list):
        raise PackLoadError(f"{ctx}: expected a list, got {type(value).__name__}")
    return list(value)


def _check_keys(
    mapping: dict[str, object], *, required: frozenset[str], optional: frozenset[str], ctx: str
) -> None:
    missing = sorted(required - mapping.keys())
    if missing:
        raise PackLoadError(f"{ctx}: missing required key(s) {missing}")
    unknown = sorted(mapping.keys() - required - optional)
    if unknown:
        allowed = sorted(required | optional)
        raise PackLoadError(f"{ctx}: unknown key(s) {unknown}; allowed keys are {allowed}")


def _str_value(value: object, ctx: str) -> str:
    if not isinstance(value, str):
        raise PackLoadError(f"{ctx}: expected a string, got {type(value).__name__}")
    return value


def _get_str(mapping: dict[str, object], key: str, ctx: str) -> str:
    return _str_value(mapping[key], f"{ctx}.{key}")


def _get_opt_str(mapping: dict[str, object], key: str, ctx: str) -> str | None:
    if key not in mapping or mapping[key] is None:
        return None
    return _str_value(mapping[key], f"{ctx}.{key}")


def _get_str_default(mapping: dict[str, object], key: str, ctx: str, default: str) -> str:
    value = _get_opt_str(mapping, key, ctx)
    return default if value is None else value


def _get_bool(mapping: dict[str, object], key: str, ctx: str, default: bool) -> bool:
    if key not in mapping:
        return default
    value = mapping[key]
    if not isinstance(value, bool):
        raise PackLoadError(f"{ctx}.{key}: expected a boolean, got {type(value).__name__}")
    return value


def _get_int(mapping: dict[str, object], key: str, ctx: str) -> int:
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise PackLoadError(f"{ctx}.{key}: expected an integer, got {type(value).__name__}")
    return value


def _get_opt_int(mapping: dict[str, object], key: str, ctx: str) -> int | None:
    if key not in mapping or mapping[key] is None:
        return None
    return _get_int(mapping, key, ctx)


def _decimal_value(value: object, ctx: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PackLoadError(f"{ctx}: expected a number, got {type(value).__name__}")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise PackLoadError(f"{ctx}: {value!r} is not a valid number") from exc


def _get_decimal(mapping: dict[str, object], key: str, ctx: str) -> Decimal:
    return _decimal_value(mapping[key], f"{ctx}.{key}")


def _str_tuple(mapping: dict[str, object], key: str, ctx: str) -> tuple[str, ...]:
    if key not in mapping or mapping[key] is None:
        return ()
    items = _sequence(mapping[key], f"{ctx}.{key}")
    return tuple(_str_value(item, f"{ctx}.{key}[{i}]") for i, item in enumerate(items))


def _parse_enum[E: StrEnum](enum_cls: type[E], value: object, ctx: str, *, name: str) -> E:
    text = _str_value(value, ctx)
    try:
        return enum_cls(text.lower())
    except ValueError as exc:
        raise PackLoadError(f"{ctx}: {text!r} is not a valid {name}") from exc


def _reject_duplicates(ids: Iterable[object], *, label: str, ctx: str) -> None:
    seen: set[object] = set()
    for value in ids:
        if value in seen:
            raise PackLoadError(f"{ctx}: duplicate {label} {value!r}")
        seen.add(value)


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackLoadError(f"{path.name}: invalid YAML: {exc}") from exc
    if raw is None:
        return {}
    return _mapping(raw, path.name)


# ---------------------------------------------------------------------------
# filters and measures


def _scalar(value: object, ctx: str) -> Scalar:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, datetime):
        raise PackLoadError(f"{ctx}: timestamps are not valid predicate values (use a date)")
    if isinstance(value, date):
        return value
    raise PackLoadError(f"{ctx}: {value!r} is not a valid scalar value")


def _parse_predicate(node: object, ctx: str) -> Predicate:
    mapping = _mapping(node, ctx)
    _check_keys(
        mapping,
        required=frozenset({"dimension", "op"}),
        optional=frozenset({"value", "values"}),
        ctx=ctx,
    )
    if "value" in mapping and "values" in mapping:
        raise PackLoadError(f"{ctx}: provide either 'value' or 'values', not both")
    op = _parse_enum(PredicateOp, mapping["op"], f"{ctx}.op", name="predicate op")
    values: tuple[Scalar, ...] = ()
    if "values" in mapping:
        items = _sequence(mapping["values"], f"{ctx}.values")
        values = tuple(_scalar(item, f"{ctx}.values[{i}]") for i, item in enumerate(items))
    elif "value" in mapping:
        values = (_scalar(mapping["value"], f"{ctx}.value"),)
    with _located(ctx):
        return Predicate(dimension=DimensionRef(_get_str(mapping, "dimension", ctx)), op=op, values=values)


def _parse_filter(node: object, ctx: str) -> FilterExpr:
    mapping = _mapping(node, ctx)
    keys = set(mapping)
    if keys == {"and"}:
        items = _sequence(mapping["and"], f"{ctx}.and")
        return And(tuple(_parse_filter(item, f"{ctx}.and[{i}]") for i, item in enumerate(items)))
    if keys == {"or"}:
        items = _sequence(mapping["or"], f"{ctx}.or")
        clauses = tuple(_parse_filter(item, f"{ctx}.or[{i}]") for i, item in enumerate(items))
        with _located(ctx):
            return Or(clauses)
    if keys == {"not"}:
        return Not(_parse_filter(mapping["not"], f"{ctx}.not"))
    if keys == {"predicate"}:
        return _parse_predicate(mapping["predicate"], f"{ctx}.predicate")
    if "dimension" in keys:
        return _parse_predicate(mapping, ctx)
    raise PackLoadError(
        f"{ctx}: unknown filter form {sorted(keys)}; expected one of "
        "'and', 'or', 'not', 'predicate', or a bare predicate mapping"
    )


def _parse_simple_measure(node: object, ctx: str) -> Sum | Count | CountDistinct:
    mapping = _mapping(node, ctx)
    if len(mapping) != 1:
        raise PackLoadError(f"{ctx}: a measure must have exactly one key, got {sorted(mapping)}")
    key, value = next(iter(mapping.items()))
    if key == "sum":
        return Sum(FieldRef(_str_value(value, f"{ctx}.sum")))
    if key == "count":
        if value not in (None, {}):
            raise PackLoadError(f"{ctx}.count: expected an empty mapping, got {value!r}")
        return Count()
    if key == "count_distinct":
        return CountDistinct(FieldRef(_str_value(value, f"{ctx}.count_distinct")))
    if key == "filtered":
        raise PackLoadError(f"{ctx}: 'filtered' cannot be nested inside another 'filtered' measure")
    raise PackLoadError(
        f"{ctx}: unknown measure {key!r}; expected 'sum', 'count', 'count_distinct', or 'filtered'"
    )


def _parse_measure(node: object, ctx: str) -> MeasureExpr:
    mapping = _mapping(node, ctx)
    if len(mapping) == 1 and "filtered" in mapping:
        inner_ctx = f"{ctx}.filtered"
        body = _mapping(mapping["filtered"], inner_ctx)
        _check_keys(body, required=frozenset({"inner", "where"}), optional=frozenset(), ctx=inner_ctx)
        return Filtered(
            inner=_parse_simple_measure(body["inner"], f"{inner_ctx}.inner"),
            where=_parse_filter(body["where"], f"{inner_ctx}.where"),
        )
    return _parse_simple_measure(node, ctx)


# ---------------------------------------------------------------------------
# metric contracts


_METRIC_KEYS_REQUIRED = frozenset(
    {
        "id",
        "version",
        "kind",
        "entity_grain",
        "numerator",
        "primary_date_basis",
        "allowed_date_bases",
        "scope_dimensions",
        "sign",
        "unit",
    }
)
_METRIC_KEYS_OPTIONAL = frozenset({"denominator", "exclusions", "description"})


def _parse_metric(mapping: dict[str, object], ctx: str) -> MetricContract:
    _check_keys(mapping, required=_METRIC_KEYS_REQUIRED, optional=_METRIC_KEYS_OPTIONAL, ctx=ctx)
    metric_id = _get_str(mapping, "id", ctx)
    ctx = f"{ctx} (metric {metric_id!r})"
    denominator: MeasureExpr | None = None
    if mapping.get("denominator") is not None:
        denominator = _parse_measure(mapping["denominator"], f"{ctx}.denominator")
    exclusions: FilterExpr | None = None
    if mapping.get("exclusions") is not None:
        exclusions = _parse_filter(mapping["exclusions"], f"{ctx}.exclusions")
    allowed = _str_tuple(mapping, "allowed_date_bases", ctx)
    if not allowed:
        raise PackLoadError(f"{ctx}.allowed_date_bases: must list at least one date basis")
    with _located(ctx):
        return MetricContract(
            id=metric_id,
            version=_get_int(mapping, "version", ctx),
            kind=_parse_enum(MetricKind, mapping["kind"], f"{ctx}.kind", name="metric kind"),
            entity_grain=_parse_enum(
                EntityGrain, mapping["entity_grain"], f"{ctx}.entity_grain", name="entity grain"
            ),
            numerator=_parse_measure(mapping["numerator"], f"{ctx}.numerator"),
            denominator=denominator,
            primary_date_basis=DateBasisRef(_get_str(mapping, "primary_date_basis", ctx)),
            allowed_date_bases=tuple(DateBasisRef(b) for b in allowed),
            scope_dimensions=tuple(
                DimensionRef(d) for d in _str_tuple(mapping, "scope_dimensions", ctx)
            ),
            sign=_parse_enum(SignConvention, mapping["sign"], f"{ctx}.sign", name="sign convention"),
            unit=_parse_enum(MetricUnit, mapping["unit"], f"{ctx}.unit", name="metric unit"),
            exclusions=exclusions,
            description=_get_str_default(mapping, "description", ctx, ""),
        )


# ---------------------------------------------------------------------------
# concepts, codes, bindings


def _parse_sources(mapping: dict[str, object], ctx: str) -> tuple[SourceRef, ...]:
    if "sources" not in mapping or mapping["sources"] is None:
        return ()
    items = _sequence(mapping["sources"], f"{ctx}.sources")
    sources: list[SourceRef] = []
    for i, item in enumerate(items):
        entry_ctx = f"{ctx}.sources[{i}]"
        entry = _mapping(item, entry_ctx)
        _check_keys(
            entry,
            required=frozenset({"id", "title", "publisher", "authority"}),
            optional=frozenset({"url"}),
            ctx=entry_ctx,
        )
        with _located(entry_ctx):
            sources.append(
                SourceRef(
                    id=_get_str(entry, "id", entry_ctx),
                    title=_get_str(entry, "title", entry_ctx),
                    publisher=_get_str(entry, "publisher", entry_ctx),
                    url=_get_opt_str(entry, "url", entry_ctx),
                    authority=_get_str(entry, "authority", entry_ctx),
                )
            )
    return tuple(sources)


_ALIAS_PATCH_KEYS = frozenset({"id", "aliases", "add_aliases"})
_FULL_CONCEPT_MARKERS = frozenset({"name", "description", "definition", "sources", "related"})


def _parse_concepts(
    path: Path, kind: PackLayerKind
) -> tuple[tuple[Concept, ...], tuple[AliasOverride, ...]]:
    document = _load_yaml_mapping(path)
    _check_keys(document, required=frozenset({"concepts"}), optional=frozenset(), ctx=path.name)
    concepts: list[Concept] = []
    overrides: list[AliasOverride] = []
    for i, item in enumerate(_sequence(document["concepts"], f"{path.name}.concepts")):
        ctx = f"{path.name}.concepts[{i}]"
        entry = _mapping(item, ctx)
        if not (entry.keys() & _FULL_CONCEPT_MARKERS):  # alias patch, not a full concept
            _check_keys(entry, required=frozenset({"id"}), optional=_ALIAS_PATCH_KEYS, ctx=ctx)
            if kind is PackLayerKind.BASE:
                raise PackLoadError(
                    f"{ctx}: base-layer concepts must be full definitions "
                    "(alias patches belong to overlays)"
                )
            replace_aliases = (
                _str_tuple(entry, "aliases", ctx) if entry.get("aliases") is not None else None
            )
            with _located(ctx):
                overrides.append(
                    AliasOverride(
                        concept_id=_get_str(entry, "id", ctx),
                        add_aliases=_str_tuple(entry, "add_aliases", ctx),
                        replace_aliases=replace_aliases,
                    )
                )
            continue
        _check_keys(
            entry,
            required=frozenset({"id", "name", "description", "definition"}),
            optional=frozenset({"aliases", "related", "sources"}),
            ctx=ctx,
        )
        with _located(ctx):
            concepts.append(
                Concept(
                    id=_get_str(entry, "id", ctx),
                    name=_get_str(entry, "name", ctx),
                    description=_get_str(entry, "description", ctx),
                    definition=_get_str(entry, "definition", ctx),
                    aliases=_str_tuple(entry, "aliases", ctx),
                    sources=_parse_sources(entry, ctx),
                    related=_str_tuple(entry, "related", ctx),
                )
            )
    _reject_duplicates((c.id for c in concepts), label="concept id", ctx=path.name)
    return tuple(concepts), tuple(overrides)


def _code_str(value: object, ctx: str) -> str:
    if isinstance(value, bool):
        raise PackLoadError(f"{ctx}: expected a code string, got a boolean")
    if isinstance(value, int):
        return str(value)
    return _str_value(value, ctx)


def _parse_codes(path: Path) -> tuple[CodeDefinition, ...]:
    document = _load_yaml_mapping(path)
    _check_keys(document, required=frozenset({"codes"}), optional=frozenset(), ctx=path.name)
    codes: list[CodeDefinition] = []
    for i, item in enumerate(_sequence(document["codes"], f"{path.name}.codes")):
        ctx = f"{path.name}.codes[{i}]"
        entry = _mapping(item, ctx)
        _check_keys(
            entry,
            required=frozenset({"code_system", "code", "title", "definition_paraphrase"}),
            optional=frozenset({"category", "sources"}),
            ctx=ctx,
        )
        with _located(ctx):
            codes.append(
                CodeDefinition(
                    code_system=_parse_enum(
                        CodeSystem, entry["code_system"], f"{ctx}.code_system", name="code system"
                    ),
                    code=_code_str(entry["code"], f"{ctx}.code"),
                    title=_get_str(entry, "title", ctx),
                    definition_paraphrase=_get_str(entry, "definition_paraphrase", ctx),
                    category=_get_opt_str(entry, "category", ctx),
                    sources=_parse_sources(entry, ctx),
                )
            )
    _reject_duplicates(((c.code_system.value, c.code) for c in codes), label="code", ctx=path.name)
    return tuple(codes)


def _parse_bindings(path: Path) -> tuple[BindingCandidate, ...]:
    document = _load_yaml_mapping(path)
    _check_keys(document, required=frozenset({"bindings"}), optional=frozenset(), ctx=path.name)
    bindings: list[BindingCandidate] = []
    for i, item in enumerate(_sequence(document["bindings"], f"{path.name}.bindings")):
        ctx = f"{path.name}.bindings[{i}]"
        entry = _mapping(item, ctx)
        _check_keys(
            entry,
            required=frozenset({"concept_id", "dimension_or_measure_id", "state", "strength"}),
            optional=frozenset({"rationale"}),
            ctx=ctx,
        )
        with _located(ctx):
            bindings.append(
                BindingCandidate(
                    concept_id=_get_str(entry, "concept_id", ctx),
                    dimension_or_measure_id=_get_str(entry, "dimension_or_measure_id", ctx),
                    state=_parse_enum(
                        BindingState, entry["state"], f"{ctx}.state", name="binding state"
                    ),
                    strength=_parse_enum(
                        EvidenceGrade, entry["strength"], f"{ctx}.strength", name="evidence grade"
                    ),
                    rationale=_get_str_default(entry, "rationale", ctx, ""),
                )
            )
    _reject_duplicates(
        ((b.concept_id, b.dimension_or_measure_id) for b in bindings), label="binding", ctx=path.name
    )
    return tuple(bindings)


# ---------------------------------------------------------------------------
# playbooks


def _parse_window(node: object, ctx: str) -> RelativeRange:
    mapping = _mapping(node, ctx)
    _check_keys(
        mapping, required=frozenset({"quantity", "unit"}), optional=frozenset({"mode"}), ctx=ctx
    )
    mode = (
        _parse_enum(RangeMode, mapping["mode"], f"{ctx}.mode", name="range mode")
        if "mode" in mapping
        else RangeMode.TRAILING
    )
    with _located(ctx):
        return RelativeRange(
            quantity=_get_decimal(mapping, "quantity", ctx),
            unit=_parse_enum(TimeUnit, mapping["unit"], f"{ctx}.unit", name="time unit"),
            mode=mode,
        )


def _parse_probe(node: object, ctx: str) -> ProbeTemplate:
    mapping = _mapping(node, ctx)
    _check_keys(
        mapping,
        required=frozenset({"id", "metric_ids"}),
        optional=frozenset({"dimensions", "window", "basis_override", "top_n", "scope_note"}),
        ctx=ctx,
    )
    window = (
        _parse_window(mapping["window"], f"{ctx}.window")
        if mapping.get("window") is not None
        else None
    )
    with _located(ctx):
        return ProbeTemplate(
            id=_get_str(mapping, "id", ctx),
            metric_ids=_str_tuple(mapping, "metric_ids", ctx),
            dimensions=_str_tuple(mapping, "dimensions", ctx),
            window=window,
            basis_override=_get_opt_str(mapping, "basis_override", ctx),
            top_n=_get_opt_int(mapping, "top_n", ctx),
            scope_note=_get_str_default(mapping, "scope_note", ctx, ""),
        )


def _parse_transform(node: object, ctx: str) -> TransformStep:
    mapping = _mapping(node, ctx)
    _check_keys(mapping, required=frozenset({"operator"}), optional=frozenset({"args"}), ctx=ctx)
    pairs: list[tuple[str, str]] = []
    if mapping.get("args") is not None:
        for key, value in _mapping(mapping["args"], f"{ctx}.args").items():
            if isinstance(value, bool) or value is None:
                pairs.append((key, str(value).lower()))
            elif isinstance(value, (str, int, float)):
                pairs.append((key, str(value)))
            else:
                raise PackLoadError(f"{ctx}.args.{key}: expected a scalar, got {type(value).__name__}")
    with _located(ctx):
        return TransformStep(operator=_get_str(mapping, "operator", ctx), args=tuple(pairs))


def _parse_playbook(mapping: dict[str, object], ctx: str) -> Playbook:
    _check_keys(
        mapping,
        required=frozenset({"id", "description"}),
        optional=frozenset(
            {"triggers", "params", "probes", "transforms", "conclusion_policies", "ranking_policy"}
        ),
        ctx=ctx,
    )
    playbook_id = _get_str(mapping, "id", ctx)
    ctx = f"{ctx} (playbook {playbook_id!r})"
    probes = tuple(
        _parse_probe(item, f"{ctx}.probes[{i}]")
        for i, item in enumerate(_sequence(mapping.get("probes") or [], f"{ctx}.probes"))
    )
    transforms = tuple(
        _parse_transform(item, f"{ctx}.transforms[{i}]")
        for i, item in enumerate(_sequence(mapping.get("transforms") or [], f"{ctx}.transforms"))
    )
    with _located(ctx):
        return Playbook(
            id=playbook_id,
            description=_get_str(mapping, "description", ctx),
            triggers=_str_tuple(mapping, "triggers", ctx),
            params=_str_tuple(mapping, "params", ctx),
            probes=probes,
            transforms=transforms,
            conclusion_policies=_str_tuple(mapping, "conclusion_policies", ctx),
            ranking_policy=_get_opt_str(mapping, "ranking_policy", ctx),
        )


# ---------------------------------------------------------------------------
# policies, presentation, filing rules


_DETECTOR_OVERRIDE_KEYS = frozenset({"id", "threshold"})
_DETECTOR_FULL_KEYS = frozenset({"id", "description", "threshold", "threshold_min", "threshold_max"})


def _parse_policies(
    path: Path, kind: PackLayerKind
) -> tuple[
    tuple[ConclusionPolicy, ...],
    tuple[RankingPolicy, ...],
    tuple[DetectorPolicy, ...],
    tuple[DetectorOverride, ...],
]:
    document = _load_yaml_mapping(path)
    _check_keys(
        document,
        required=frozenset(),
        optional=frozenset({"conclusion_policies", "ranking_policies", "detector_policies"}),
        ctx=path.name,
    )

    conclusions: list[ConclusionPolicy] = []
    for i, item in enumerate(
        _sequence(document.get("conclusion_policies") or [], f"{path.name}.conclusion_policies")
    ):
        ctx = f"{path.name}.conclusion_policies[{i}]"
        entry = _mapping(item, ctx)
        _check_keys(
            entry,
            required=frozenset({"id", "claim", "required_grade", "required_evidence"}),
            optional=frozenset({"estimate_label_required"}),
            ctx=ctx,
        )
        with _located(ctx):
            conclusions.append(
                ConclusionPolicy(
                    id=_get_str(entry, "id", ctx),
                    claim=_get_str(entry, "claim", ctx),
                    required_grade=_parse_enum(
                        EvidenceGrade,
                        entry["required_grade"],
                        f"{ctx}.required_grade",
                        name="evidence grade",
                    ),
                    required_evidence=_str_tuple(entry, "required_evidence", ctx),
                    estimate_label_required=_get_bool(entry, "estimate_label_required", ctx, False),
                )
            )

    rankings: list[RankingPolicy] = []
    for i, item in enumerate(
        _sequence(document.get("ranking_policies") or [], f"{path.name}.ranking_policies")
    ):
        ctx = f"{path.name}.ranking_policies[{i}]"
        entry = _mapping(item, ctx)
        _check_keys(
            entry,
            required=frozenset({"id", "description", "weights"}),
            optional=frozenset(),
            ctx=ctx,
        )
        weights = tuple(
            (key, _decimal_value(value, f"{ctx}.weights.{key}"))
            for key, value in _mapping(entry["weights"], f"{ctx}.weights").items()
        )
        with _located(ctx):
            rankings.append(
                RankingPolicy(
                    id=_get_str(entry, "id", ctx),
                    weights=weights,
                    description=_get_str(entry, "description", ctx),
                )
            )

    detectors: list[DetectorPolicy] = []
    overrides: list[DetectorOverride] = []
    for i, item in enumerate(
        _sequence(document.get("detector_policies") or [], f"{path.name}.detector_policies")
    ):
        ctx = f"{path.name}.detector_policies[{i}]"
        entry = _mapping(item, ctx)
        if entry.keys() <= _DETECTOR_OVERRIDE_KEYS:  # threshold tune, not a full policy
            _check_keys(entry, required=_DETECTOR_OVERRIDE_KEYS, optional=frozenset(), ctx=ctx)
            if kind is PackLayerKind.BASE:
                raise PackLoadError(
                    f"{ctx}: base-layer detector policies must declare description and "
                    "[threshold_min, threshold_max] (threshold overrides belong to overlays)"
                )
            with _located(ctx):
                overrides.append(
                    DetectorOverride(
                        id=_get_str(entry, "id", ctx),
                        threshold=_get_decimal(entry, "threshold", ctx),
                    )
                )
            continue
        _check_keys(entry, required=_DETECTOR_FULL_KEYS, optional=frozenset(), ctx=ctx)
        with _located(ctx):
            detectors.append(
                DetectorPolicy(
                    id=_get_str(entry, "id", ctx),
                    description=_get_str(entry, "description", ctx),
                    threshold=_get_decimal(entry, "threshold", ctx),
                    threshold_min=_get_decimal(entry, "threshold_min", ctx),
                    threshold_max=_get_decimal(entry, "threshold_max", ctx),
                )
            )

    _reject_duplicates((p.id for p in conclusions), label="conclusion policy id", ctx=path.name)
    _reject_duplicates((p.id for p in rankings), label="ranking policy id", ctx=path.name)
    _reject_duplicates(
        [p.id for p in detectors] + [o.id for o in overrides],
        label="detector policy id",
        ctx=path.name,
    )
    return tuple(conclusions), tuple(rankings), tuple(detectors), tuple(overrides)


def _parse_presentation(path: Path) -> tuple[PresentationRecipe, ...]:
    document = _load_yaml_mapping(path)
    _check_keys(document, required=frozenset({"recipes"}), optional=frozenset(), ctx=path.name)
    recipes: list[PresentationRecipe] = []
    for i, item in enumerate(_sequence(document["recipes"], f"{path.name}.recipes")):
        ctx = f"{path.name}.recipes[{i}]"
        entry = _mapping(item, ctx)
        _check_keys(
            entry,
            required=frozenset({"id", "applies_to", "chart_type"}),
            optional=frozenset({"notes"}),
            ctx=ctx,
        )
        with _located(ctx):
            recipes.append(
                PresentationRecipe(
                    id=_get_str(entry, "id", ctx),
                    applies_to=_get_str(entry, "applies_to", ctx),
                    chart_type=_get_str(entry, "chart_type", ctx),
                    notes=_get_str_default(entry, "notes", ctx, ""),
                )
            )
    _reject_duplicates((r.id for r in recipes), label="presentation recipe id", ctx=path.name)
    return tuple(recipes)


def _parse_filing_rules(path: Path) -> tuple[FilingRule, ...]:
    document = _load_yaml_mapping(path)
    _check_keys(document, required=frozenset({"filing_rules"}), optional=frozenset(), ctx=path.name)
    rules: list[FilingRule] = []
    for i, item in enumerate(_sequence(document["filing_rules"], f"{path.name}.filing_rules")):
        ctx = f"{path.name}.filing_rules[{i}]"
        entry = _mapping(item, ctx)
        _check_keys(
            entry,
            required=frozenset(
                {"id", "payer_pattern", "filing_limit_days", "date_basis", "authority"}
            ),
            optional=frozenset({"plan_pattern", "requires_confirmation"}),
            ctx=ctx,
        )
        with _located(ctx):
            rules.append(
                FilingRule(
                    id=_get_str(entry, "id", ctx),
                    payer_pattern=_get_str(entry, "payer_pattern", ctx),
                    plan_pattern=_get_opt_str(entry, "plan_pattern", ctx),
                    filing_limit_days=_get_int(entry, "filing_limit_days", ctx),
                    date_basis=DateBasisRef(_get_str(entry, "date_basis", ctx)),
                    authority=_get_str(entry, "authority", ctx),
                    requires_confirmation=_get_bool(entry, "requires_confirmation", ctx, False),
                )
            )
    _reject_duplicates((r.id for r in rules), label="filing rule id", ctx=path.name)
    return tuple(rules)


# ---------------------------------------------------------------------------
# layer assembly


def _yaml_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file())


def load_layer(directory: Path | str) -> PackLayer:
    """Load and strictly validate one pack layer from ``directory``."""
    root = Path(directory)
    if not root.is_dir():
        raise PackLoadError(f"pack layer directory not found: {root}")
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        raise PackLoadError(f"{root}: missing required manifest {MANIFEST_FILE!r}")
    manifest = _load_yaml_mapping(manifest_path)
    _check_keys(
        manifest,
        required=frozenset({"pack_id", "version", "kind"}),
        optional=frozenset({"description"}),
        ctx=MANIFEST_FILE,
    )
    kind = _parse_enum(PackLayerKind, manifest["kind"], f"{MANIFEST_FILE}.kind", name="layer kind")
    version_raw = manifest["version"]
    if isinstance(version_raw, bool) or not isinstance(version_raw, (str, int, float)):
        raise PackLoadError(f"{MANIFEST_FILE}.version: expected a string")
    version = str(version_raw)

    concepts: tuple[Concept, ...] = ()
    alias_overrides: tuple[AliasOverride, ...] = ()
    if (root / "concepts.yaml").is_file():
        concepts, alias_overrides = _parse_concepts(root / "concepts.yaml", kind)

    codes = _parse_codes(root / "codes.yaml") if (root / "codes.yaml").is_file() else ()
    bindings = _parse_bindings(root / "bindings.yaml") if (root / "bindings.yaml").is_file() else ()

    metrics: list[MetricContract] = []
    metrics_dir = root / "metrics"
    if metrics_dir.is_dir():
        for path in _yaml_files(metrics_dir):
            metrics.append(_parse_metric(_load_yaml_mapping(path), f"metrics/{path.name}"))
    _reject_duplicates((m.id for m in metrics), label="metric id", ctx=str(root))

    playbooks: list[Playbook] = []
    playbooks_dir = root / "playbooks"
    if playbooks_dir.is_dir():
        for path in _yaml_files(playbooks_dir):
            playbooks.append(_parse_playbook(_load_yaml_mapping(path), f"playbooks/{path.name}"))
    _reject_duplicates((p.id for p in playbooks), label="playbook id", ctx=str(root))

    conclusions: tuple[ConclusionPolicy, ...] = ()
    rankings: tuple[RankingPolicy, ...] = ()
    detectors: tuple[DetectorPolicy, ...] = ()
    detector_overrides: tuple[DetectorOverride, ...] = ()
    if (root / "policies.yaml").is_file():
        conclusions, rankings, detectors, detector_overrides = _parse_policies(
            root / "policies.yaml", kind
        )

    presentation = (
        _parse_presentation(root / "presentation.yaml")
        if (root / "presentation.yaml").is_file()
        else ()
    )
    filing_rules = (
        _parse_filing_rules(root / "filing_rules.yaml")
        if (root / "filing_rules.yaml").is_file()
        else ()
    )

    return PackLayer(
        kind=kind,
        name=_get_str(manifest, "pack_id", MANIFEST_FILE),
        version=version,
        description=_get_str_default(manifest, "description", MANIFEST_FILE, ""),
        concepts=concepts,
        alias_overrides=alias_overrides,
        code_definitions=codes,
        metric_contracts=tuple(metrics),
        bindings=bindings,
        playbooks=tuple(playbooks),
        conclusion_policies=conclusions,
        ranking_policies=rankings,
        detector_policies=detectors,
        detector_overrides=detector_overrides,
        presentation_recipes=presentation,
        filing_rules=filing_rules,
    )
