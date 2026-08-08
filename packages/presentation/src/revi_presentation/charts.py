"""ChartSpec generation (design §4.6): governed recipes first, LLM
suggestion only as a tie-breaker, deterministic heuristics last.

Inputs are neutral: kernel evidence frames, contract DTOs, and plain
recipe tuples passed through from the pack — this package never imports
capability implementations. Selection order per frame:

1. a pack **recipe** whose ``applies_to`` matches a measure in the frame or
   the active playbook id (recipe chart types map into the closed DTO set;
   the pack's ``table_bars`` renders as ``table``);
2. the **LLM suggestion**, only when no recipe matched and the suggestion's
   columns exist in the frame;
3. **heuristics**: time-bucketed frames → line; one dimension → bar (two →
   grouped_bar); measure-only → table.

Every series row carries the referent id registered for its dimension
value, so a click compiles to a typed ``DrillInto`` — no natural language
in the gesture loop. Truncation and suppression are surfaced as
annotations, never hidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from revi_investigation_contracts.api import ChartRow, ChartSpec, ChartType
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import DimensionRef, MetricRef

_TIME_BUCKET_PREFIX = "time_bucket:"
_ALLOWED_TYPES: frozenset[str] = frozenset(
    {"bar", "grouped_bar", "stacked_bar", "line", "waterfall", "table", "range_band"}
)
_RECIPE_TYPE_ALIASES = {"table_bars": "table"}


@dataclass(frozen=True, slots=True)
class RecipeSpec:
    """A pack presentation recipe, passed through as neutral data."""

    id: str
    applies_to: str
    chart_type: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ChartSuggestion:
    """An LLM chart suggestion (already schema-validated upstream)."""

    chart_type: str
    x: str
    value: str
    series: str | None = None


def _dimension_columns(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, DimensionRef) and not col.ref.id.startswith(_TIME_BUCKET_PREFIX)
    )


def _time_column(frame: EvidenceFrame) -> str | None:
    for col in frame.schema.columns:
        if isinstance(col.ref, DimensionRef) and col.ref.id.startswith(_TIME_BUCKET_PREFIX):
            return col.name
    return None


def _measure_columns(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, MetricRef) and "__" not in col.name
    )


def _primary_measure(frame: EvidenceFrame) -> str | None:
    measures = _measure_columns(frame)
    for name in measures:
        col = frame.schema.columns[frame.schema.index_of(name)]
        if col.unit == "money_cents":
            return name
    return measures[0] if measures else None


def _coerce_type(raw: str) -> ChartType | None:
    mapped = _RECIPE_TYPE_ALIASES.get(raw, raw)
    if mapped in _ALLOWED_TYPES:
        return mapped  # type: ignore[return-value]
    return None


def _cell(value: Scalar) -> str | int | float | None:
    if isinstance(value, Decimal):
        return float(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return int(value) if isinstance(value, bool) else value
    return str(value)


def _pick_recipe(
    frame: EvidenceFrame, recipes: Sequence[RecipeSpec], playbook_id: str | None
) -> RecipeSpec | None:
    targets = set(_measure_columns(frame))
    if playbook_id is not None:
        targets.add(playbook_id)
    for recipe in recipes:
        if recipe.applies_to in targets and _coerce_type(recipe.chart_type) is not None:
            return recipe
    return None


def _heuristic_type(frame: EvidenceFrame) -> ChartType:
    dims = _dimension_columns(frame)
    if _time_column(frame) is not None:
        return "line"
    if len(dims) >= 2:
        return "grouped_bar"
    if len(dims) == 1:
        return "bar"
    return "table"


def build_chart_spec(
    frame_id: str,
    frame: EvidenceFrame,
    *,
    recipes: Sequence[RecipeSpec] = (),
    playbook_id: str | None = None,
    suggestion: ChartSuggestion | None = None,
    row_referents: Mapping[tuple[str, str], str] | None = None,
) -> ChartSpec | None:
    """Build one chart spec for a frame, or None when nothing is chartable."""
    value = _primary_measure(frame)
    if value is None:
        return None
    dims = _dimension_columns(frame)
    time_col = _time_column(frame)

    recipe = _pick_recipe(frame, recipes, playbook_id)
    chart_type: ChartType
    if recipe is not None:
        coerced = _coerce_type(recipe.chart_type)
        assert coerced is not None  # _pick_recipe only returns coercible recipes
        chart_type = coerced
    elif (
        suggestion is not None
        and _coerce_type(suggestion.chart_type) is not None
        and suggestion.value in frame.schema.names
        and suggestion.x in frame.schema.names
    ):
        coerced = _coerce_type(suggestion.chart_type)
        assert coerced is not None
        chart_type = coerced
        value = suggestion.value
    else:
        chart_type = _heuristic_type(frame)

    x = time_col or (dims[0] if dims else value)
    series = dims[0] if time_col is not None and dims else (dims[1] if len(dims) > 1 else None)

    referents = row_referents or {}
    x_is_dim = x in dims
    rows: list[ChartRow] = []
    for row in frame.rows:
        x_value = row[frame.schema.index_of(x)] if x in frame.schema.names else None
        series_value = row[frame.schema.index_of(series)] if series is not None else None
        referent_id: str | None = None
        if x_is_dim:
            referent_id = referents.get((x, str(x_value)))
        if referent_id is None and series is not None and series in dims:
            referent_id = referents.get((series, str(series_value)))
        rows.append(
            ChartRow(
                x=str(x_value),
                series=str(series_value) if series_value is not None else None,
                value=_cell(row[frame.schema.index_of(value)]),
                referent_id=referent_id,
            )
        )

    annotations: list[str] = []
    if frame.truncated:
        annotations.append("truncated: not all cells are shown (top-N applied at the source)")
    if frame.suppressed_cells > 0:
        annotations.append(
            f"suppression: {frame.suppressed_cells} small cells withheld per policy"
        )

    unit_col = frame.schema.columns[frame.schema.index_of(value)]
    return ChartSpec(
        id=f"chart_{frame_id}",
        chart_type=chart_type,
        title=f"{value.replace('_', ' ')} — {frame_id.replace('_', ' ')}",
        frame_id=frame_id,
        x=x,
        series=series,
        value=value,
        unit=unit_col.unit,
        grade=frame.evidence_grade.value,
        rows=rows,
        annotations=annotations,
        recipe_id=recipe.id if recipe is not None else None,
    )


def build_chart_specs(
    frames: Sequence[tuple[str, EvidenceFrame]],
    *,
    recipes: Sequence[RecipeSpec] = (),
    playbook_id: str | None = None,
    suggestion: ChartSuggestion | None = None,
    row_referents: Mapping[tuple[str, str], str] | None = None,
    limit: int = 4,
) -> tuple[ChartSpec, ...]:
    """Chart the displayable frames, derived (compare/share) outputs first."""
    ranked = sorted(
        frames,
        key=lambda item: (
            "__compare" not in item[0],  # compare outputs first
            not _dimension_columns(item[1]),  # dimensional frames before totals
        ),
    )
    specs: list[ChartSpec] = []
    for frame_id, frame in ranked:
        if len(specs) >= limit:
            break
        if frame_id.endswith("__prior"):
            continue  # the comparison rides inside the compare output
        if frame_id.endswith("__rank"):
            continue  # rank columns are presentation metadata, not a new view
        spec = build_chart_spec(
            frame_id,
            frame,
            recipes=recipes,
            playbook_id=playbook_id,
            suggestion=suggestion,
            row_referents=row_referents,
        )
        if spec is not None and spec.rows:
            specs.append(spec)
    return tuple(specs)
