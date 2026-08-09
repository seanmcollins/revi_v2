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

from revi_investigation_contracts.api import ChartRow, ChartSort, ChartSpec, ChartType
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame
from revi_kernel.maturity import terminal_bucket_verdict
from revi_kernel.refs import DimensionRef, MetricRef

_TIME_BUCKET_PREFIX = "time_bucket:"

#: Share of a chart's publishable marks that may be ceilings before an
#: ordering over them means nothing (mirrors the findings evaluator's
#: ``MAX_BOUNDED_SHARE_FOR_RANKING`` — one threshold, two surfaces).
_MAX_BOUNDED_SHARE_FOR_RANKING = 0.5
_NUMERATOR_SUFFIX = "__num"
_DENOMINATOR_SUFFIX = "__den"
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


def bounded_rows(frame: EvidenceFrame, measure: str, threshold: int | None) -> dict[int, int]:
    """``{row index: population}`` for cells whose value is a ceiling.

    Structural, from the frame's own ``__num``/``__den`` columns — the same
    rule the executor applied, read back where the mark is drawn. Round-3
    R3-01: a bounded cell reached the chart as ``{x, series, value,
    referent_id}`` with no marker of any kind, so a hatched-vs-solid
    distinction was impossible for a renderer to make and every reader saw
    a measurement.
    """
    if threshold is None:
        return {}
    numerator, denominator = f"{measure}{_NUMERATOR_SUFFIX}", f"{measure}{_DENOMINATOR_SUFFIX}"
    names = frame.schema.names
    if numerator not in names or denominator not in names:
        return {}
    n_idx, d_idx = frame.schema.index_of(numerator), frame.schema.index_of(denominator)
    out: dict[int, int] = {}
    for i, row in enumerate(frame.rows):
        num, den = row[n_idx], row[d_idx]
        if isinstance(num, bool) or isinstance(den, bool):
            continue
        if not isinstance(num, int) or not isinstance(den, int):
            continue
        if 0 < num < threshold <= den:
            out[i] = den
    return out


def provisional_bucket(frame: EvidenceFrame, measure: str) -> str | None:
    """The x value of a terminal bucket that has not settled, or ``None``.

    Structural, from the frame's own time axis, denominator and watermark —
    the same rule the findings evaluator states in prose, applied where the
    mark is drawn. Round-4 R4-03: ``provisional_x`` existed as a parameter
    with no production call site, so ``provisional`` was ``false`` on all
    1,046 chart rows a review scanned across fifteen turns. The finding
    title said "the week of 2026-07-20 point (66.7%) is PROVISIONAL and is
    excluded from that movement" and the SVG drew an unbroken solid line
    straight up to 66.7% as its terminus — the strongest thing in the build,
    invisible everywhere except the prose.

    Deriving it here rather than threading it in also fixes the *restored*
    turn: a re-opened answer rebuilds its charts from persisted frames with
    no findings in hand, and a provisional point that disappeared on reload
    would be a second way for the line and the sentence to disagree.
    """
    bucket = _time_column(frame)
    if bucket is None:
        return None
    verdict = terminal_bucket_verdict(frame, bucket_column=bucket, measure=measure)
    return None if verdict is None else str(verdict.bucket)


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
    suppression_threshold: int | None = None,
    provisional_x: str | None = None,
    sort: tuple[str, bool] | None = None,
) -> ChartSpec | None:
    """Build one chart spec for a frame, or None when nothing is chartable.

    ``suppression_threshold`` is the §15 threshold the frame was policed
    under. Without it a bounded cell is indistinguishable from a measured
    one, which is exactly the state R3-01 found: the ceiling existed as
    structured data on the executed probe and reached the chart as a point
    value. ``provisional_x`` names a bucket that has not settled (R3-06).

    ``sort`` is ``(column, descending)`` as the PLAN resolved it (R3-13),
    published on the spec so the renderer orders the marks the way the
    findings above them were ordered. It is passed through, never derived:
    a chart with no plan ordering says so rather than implying one.
    """
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
    bounds = bounded_rows(frame, value, suppression_threshold)
    # Derived when the caller did not name one. A caller that DID name a
    # bucket wins: it knows which finding it is drawing under.
    censored_x = provisional_x if provisional_x is not None else provisional_bucket(frame, value)
    rows: list[ChartRow] = []
    for row_index, row in enumerate(frame.rows):
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
                is_bound=row_index in bounds,
                bound_population=bounds.get(row_index),
                provisional=censored_x is not None and str(x_value) == censored_x,
            )
        )

    annotations: list[str] = []
    if frame.truncated:
        annotations.append("truncated: not all cells are shown (top-N applied at the source)")
    if bounds:
        # Counted from the marks actually drawn, so the chart caption, the
        # narrative and the probe metadata cannot state three different
        # numbers for one control (round-3 R3-18).
        annotations.append(
            f"upper bounds: {len(bounds)} of {len(frame.rows)} marks are ceilings, not "
            "measurements — their numerator was suppressed and they cannot be ranked "
            "against the measured marks"
        )
    withheld = sum(
        1
        for row in frame.rows
        if row[frame.schema.index_of(value)] is None
    )
    # Round-4 R4-02: the answer refused to rank these cells and the chart
    # 400px below it sorted them by value anyway — "ordered by denial_rate,
    # high to low" over a column that is mostly ceilings, which orders by
    # panel size. The refusal is restated on the figure so a renderer can
    # suppress the ordering, and a reader who only sees the chart is told.
    publishable = len(frame.rows) - withheld
    if bounds and publishable and len(bounds) / publishable > _MAX_BOUNDED_SHARE_FOR_RANKING:
        annotations.append(
            f"ranking_refused: {len(bounds)} of the {publishable} publishable marks are upper "
            f"bounds, leaving {publishable - len(bounds)} measured — too few for an order to "
            "mean anything. These marks are NOT ranked, whatever order they are drawn in; "
            "sorting ceilings against measurements sorts by population size."
        )
    if withheld:
        annotations.append(
            f"withheld: {withheld} of {len(frame.rows)} cells were withheld outright per the "
            "small-cell policy and are drawn with no value"
        )
    elif frame.suppressed_cells > 0 and not bounds:
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
        # Only ever an ordering over a column this chart actually draws:
        # a sort naming a column the renderer cannot see is a hint it must
        # either ignore or obey wrongly, and both are worse than none.
        sort=(
            ChartSort(by=sort[0], direction="desc" if sort[1] else "asc")
            if sort is not None and sort[0] in frame.schema.names
            else None
        ),
    )


def build_chart_specs(
    frames: Sequence[tuple[str, EvidenceFrame]],
    *,
    recipes: Sequence[RecipeSpec] = (),
    playbook_id: str | None = None,
    suggestion: ChartSuggestion | None = None,
    row_referents: Mapping[tuple[str, str], str] | None = None,
    limit: int = 4,
    suppression_threshold: int | None = None,
    sorts: Mapping[str, tuple[str, bool]] | None = None,
) -> tuple[ChartSpec, ...]:
    """Chart the displayable frames, derived (compare/share) outputs first.

    ``sorts`` maps a frame id to the ``(column, descending)`` the plan
    resolved for it (see ``resolved_orderings``); frames absent from it
    were not ordered by the plan and publish no sort.
    """
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
            suppression_threshold=suppression_threshold,
            sort=(sorts or {}).get(frame_id),
        )
        if spec is not None and spec.rows:
            specs.append(spec)
    return tuple(specs)
