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
   grouped_bar); measure-only → bar on the synthetic PERIOD axis.

…and then, last and over all three, the **shape rule**: a chart kind that
the frame cannot support is corrected here rather than at the renderer
(see :func:`_shape_corrected_type`).

Every series row carries the referent id registered for its dimension
value, so a click compiles to a typed ``DrillInto`` — no natural language
in the gesture loop. Truncation and suppression are surfaced as
annotations, never hidden.

WHAT A CATEGORY AXIS MAY CONTAIN. A frame with no dimension and no time
bucket (one scalar, or one scalar against its baseline) has no column to
key marks by. Falling back to the MEASURE column — ``x = time_col or
(dims[0] if dims else value)`` — makes ``row[index_of(x)]`` the measured
number itself, so a denial-rate answer publishes a chart whose entire
category axis is the single tick ``0.127591``: the answer's own figure,
filed as though it were the name of a group. On a two-window comparison
that is worse than cosmetic, because the current value keys BOTH marks and
the two series collide on one bogus key.

The rule instead: when a frame has no dimension, the category axis is the
WINDOW. One labelled column for a scalar, two period labels ("Jul 2026"
against "Jun 2026") for a comparison — never a formatted value as a
category key, and never two series sharing a key derived from one of their
values. See :class:`ChartWindow`, :func:`period_label` and
``PERIOD_SERIES_COLUMN``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from revi_investigation_contracts.api import ChartRow, ChartSort, ChartSpec, ChartType
from revi_kernel.filters import Scalar
from revi_kernel.frame import (
    EvidenceFrame,
    primary_measure,
    published_measures,
    withheld_row_indices,
)
from revi_kernel.maturity import terminal_bucket_verdict
from revi_kernel.refs import DimensionRef

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

#: Chart kinds in English. The wire keeps the token on
#: :attr:`ChartSpec.chart_type`, where a client branches on it; a note a
#: reader is expected to read says the shape instead — "grouped bars", not
#: ``grouped_bar``. See docs/client-language.md.
_SHAPE_WORDS: dict[str, str] = {
    "bar": "bars",
    "grouped_bar": "grouped bars",
    "stacked_bar": "stacked bars",
    "line": "a line",
    "waterfall": "a waterfall",
    "table": "a table",
    "range_band": "a range band",
}


def _shape_words(chart_type: str) -> str:
    """``chart_type`` as a reader's phrase, or its own words if unknown."""
    return _SHAPE_WORDS.get(chart_type, chart_type.replace("_", " "))


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


def _coerce_type(raw: str) -> ChartType | None:
    mapped = _RECIPE_TYPE_ALIASES.get(raw, raw)
    if mapped in _ALLOWED_TYPES:
        return mapped  # type: ignore[return-value]
    return None


def _is_positive(value: Scalar) -> bool:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    return value > 0


def _cell(value: Scalar) -> str | int | float | None:
    if isinstance(value, Decimal):
        return float(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return int(value) if isinstance(value, bool) else value
    return str(value)


#: How the two halves of a comparison are labelled on a chart that draws
#: both. The comparison does NOT ride inside the compare output: charting
#: only the current column off a compare frame yields ``chart_main`` and
#: ``chart_main__compare`` with byte-identical rows, so the reader gets one
#: chart of July on an answer whose title, header chip and every warning
#: are about June against July.
CURRENT_SERIES = "current"
PRIOR_SERIES = "prior"
#: The synthetic axis those two labels sit on. Named on the spec so a
#: renderer can group by it; it is deliberately not a frame column, because
#: the distinction it draws is between two WINDOWS rather than between two
#: values of a dimension.
#:
#: It has a second job: on a frame with no dimension at all it is the ``x``
#: as well as the ``series``, and that is not a confusion — the identity of
#: such a mark IS its window, so the axis it sits on and the grouping that
#: colours it are the same one concept, published under the same one name
#: so a client has only that concept to implement.
PERIOD_SERIES_COLUMN = "period"
_PRIOR_SUFFIX = "__prior"

#: Month names spelled out rather than taken from ``strftime("%b")``: that
#: goes through the process locale, and an axis reading "juil. 2026" on one
#: deployment and "Jul 2026" on another is not a label a client can match
#: against the header chip or a reviewer against a stored spec.
_MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
)

#: What the period axis says when the caller passed no window. Prose, and
#: deliberately not empty: a turn with no context header still has to draw
#: an honest category, and the two things it must never be are a number
#: (the defect) and a blank (a tick a reader will read as missing data).
UNNAMED_CURRENT_LABEL = "this window"
UNNAMED_PRIOR_LABEL = "the window compared against"

#: Points a line needs before it is a trend rather than a decoration. Two
#: marks joined by a segment assert a direction between them and nothing
#: else; one mark with a line through it asserts a direction that has no
#: second point at all — which is exactly what the ``denial_rate_trend``
#: recipe forced onto the live one-point denial-rate frame.
_MIN_POINTS_FOR_A_LINE = 3

#: The ``windows`` key meaning "every frame on this turn". A turn has ONE
#: effective window (it is on the context header, and every frame was
#: measured under it), so the common call passes one entry under this key;
#: a per-frame entry, should a caller ever have one, still wins over it.
DEFAULT_WINDOW_KEY = ""


@dataclass(frozen=True, slots=True)
class ChartWindow:
    """The period labels a dimensionless frame is charted against.

    Already rendered, because the window a turn ran over is the caller's
    fact, not this module's: the API has it on the turn's context header
    (``window_start``/``window_end`` and, for a comparison,
    ``comparison_start``/``comparison_end``) and hands it over as text via
    :func:`period_label`. ``prior_label`` is ``None`` on a turn that
    compared nothing — a baseline label invented where no baseline was
    measured would be the same class of lie as the value-as-category axis
    this type exists to end.
    """

    current_label: str
    prior_label: str | None = None


class WindowFacts(Protocol):
    """Anything carrying the four window dates — the context header, live
    or restored.

    Structural on purpose. ``build_chart_specs``' two call sites in the API
    are held to one added argument each, so they pass the header payload
    they already hold rather than importing :class:`ChartWindow` and
    composing it; and taking it structurally keeps this module's inputs
    plain dates, so nothing about a domain header type leaks into the
    presentation layer.
    """

    @property
    def window_start(self) -> date: ...

    @property
    def window_end(self) -> date: ...

    @property
    def comparison_start(self) -> date | None: ...

    @property
    def comparison_end(self) -> date | None: ...


def _ends_a_month(day: date) -> bool:
    return (day + timedelta(days=1)).day == 1


def period_label(start: date, end: date) -> str:
    """Name an INCLUSIVE ``start..end`` range the way a reader says it.

    ``2026-07-01..2026-07-31`` → ``"Jul 2026"``; a whole quarter → ``"Q2
    2026"``; a whole calendar year → ``"2026"``; a single day → ``"Aug 2,
    2026"``; anything else keeps its edges: ``"Jul 1 — Aug 2, 2026"``, or
    ``"Dec 15, 2025 — Jan 14, 2026"`` across a year boundary.

    Whole-period forms are not cosmetic. A chart tick reading "Jul 2026"
    beside a header chip reading ``2026-07-01..2026-07-31`` is recognisably
    the same window; ``"2026-07-01 — 2026-07-31"`` on a 90px axis slot is
    two truncated ISO strings. The exact edges are never lost — the header
    states them, and this label is only ever the axis.

    Inclusive because every window in the product is: the header composes
    ``start.isoformat()..end.isoformat()`` from the same
    :class:`~revi_kernel.filters.TimeWindow`, and treating July 31 as an
    exclusive bound here would silently retitle July as "Jul 1 — Jul 30".
    """
    if start > end:  # nothing to say about an inverted range; state both edges
        return _explicit_range(start, end)
    if start.day == 1 and _ends_a_month(end):
        if start.month == 1 and end.month == 12 and start.year == end.year:
            return str(start.year)
        if (
            start.year == end.year
            and start.month in (1, 4, 7, 10)
            and end.month == start.month + 2
        ):
            return f"Q{(start.month - 1) // 3 + 1} {start.year}"
        if start.year == end.year and start.month == end.month:
            return f"{_MONTH_ABBR[start.month - 1]} {start.year}"
    if start == end:
        return f"{_MONTH_ABBR[start.month - 1]} {start.day}, {start.year}"
    return _explicit_range(start, end)


def _explicit_range(start: date, end: date) -> str:
    left = f"{_MONTH_ABBR[start.month - 1]} {start.day}"
    right = f"{_MONTH_ABBR[end.month - 1]} {end.day}, {end.year}"
    if start.year != end.year:
        left = f"{left}, {start.year}"
    return f"{left} — {right}"


def window_from_facts(facts: WindowFacts | None) -> ChartWindow | None:
    """A :class:`ChartWindow` off a turn's context header, or ``None``.

    ``None`` in, ``None`` out: a turn that published no header (a
    clarification, a context-control turn) has no window to name, and its
    dimensionless charts fall back to :data:`UNNAMED_CURRENT_LABEL` rather
    than to a date this function would have had to guess.
    """
    if facts is None:
        return None
    prior = (
        period_label(facts.comparison_start, facts.comparison_end)
        if facts.comparison_start is not None and facts.comparison_end is not None
        else None
    )
    return ChartWindow(
        current_label=period_label(facts.window_start, facts.window_end), prior_label=prior
    )


def bounded_rows(frame: EvidenceFrame, measure: str, threshold: int | None) -> dict[int, int]:
    """``{row index: population}`` for cells whose value is a ceiling.

    Structural, from the frame's own ``__num``/``__den`` columns — the same
    rule the executor applied, read back where the mark is drawn. Without
    it a bounded cell reaches the chart as ``{x, series, value,
    referent_id}`` with no marker of any kind: a hatched-vs-solid
    distinction is impossible for a renderer to make and every reader sees
    a measurement.
    """
    return _bounded_rows(frame, measure, threshold, suffix="")


def _bounded_rows(
    frame: EvidenceFrame, measure: str, threshold: int | None, *, suffix: str
) -> dict[int, int]:
    """``bounded_rows`` for one SIDE of a comparison.

    The prior side's panel columns are ``<m>__num__prior``/``<m>__den__prior``
    and end in neither suffix the current-side reader matches on, so a
    prior mark drawn from them had no way to be recognised as a ceiling.
    """
    if threshold is None:
        return {}
    numerator = f"{measure}{_NUMERATOR_SUFFIX}{suffix}"
    denominator = f"{measure}{_DENOMINATOR_SUFFIX}{suffix}"
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
    mark is drawn. Derived here rather than threaded in as a parameter,
    because a parameter with no production call site leaves ``provisional``
    ``false`` on every chart row while the finding title says the terminal
    point is provisional and the line is drawn solid straight through it.

    Deriving it here also fixes the *restored* turn: a re-opened answer
    rebuilds its charts from persisted frames with no findings in hand, and
    a provisional point that disappeared on reload would be a second way
    for the line and the sentence to disagree.
    """
    bucket = _time_column(frame)
    if bucket is None:
        return None
    verdict = terminal_bucket_verdict(frame, bucket_column=bucket, measure=measure)
    return None if verdict is None else str(verdict.bucket)


def _pick_recipe(
    frame: EvidenceFrame, recipes: Sequence[RecipeSpec], playbook_id: str | None
) -> RecipeSpec | None:
    targets = set(published_measures(frame))
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
    # No dimension: the period axis. One labelled column for a scalar — a
    # stat figure, which the closed ``ChartType`` set spells "bar" with a
    # single category — and two paired columns for a window against its
    # baseline. Not ``table``: a fair rendering of one number, but it tells
    # the client nothing about the shape, so a one-row frame arrives looking
    # like every other table.
    return "bar"


def _shape_corrected_type(
    chart_type: ChartType, rows: Sequence[ChartRow], *, ordered_axis: bool
) -> ChartType:
    """The last word on chart kind, decided from what will be DRAWN.

    A line asserts a trend: that the marks are in an order, and that the
    segment between two of them is a movement. Both claims are checkable
    here and nowhere else — only at this point are the rows built, so only
    here is the number of distinct categories known — and both are easy to
    make falsely:

    * a spec declaring ``line`` over two categories or fewer, which the
      pack's ``denial_rate_trend`` recipe asks for on a one-point frame. A
      client that quietly draws bars instead makes the wire and the screen
      disagree about what the answer was;
    * a line across a ``payer`` or ``facility`` axis, where the order of
      the categories is whatever the sort left them in and the slope
      between two of them is not a rate of change of anything.

    This runs AFTER the recipe, the LLM suggestion and the heuristic, and
    it overrides all three — a governed pack recipe included. A recipe
    states a preference about a metric ("chart denial rate as a trend"); it
    cannot know that this particular frame came back with one point on no
    time axis, and honouring it there publishes a trend nobody measured.
    The demotion is stated in an annotation when it overrules a recipe, so
    the ``recipe_id`` on the wire and the ``chart_type`` beside it do not
    read as a contradiction.
    """
    if chart_type != "line":
        return chart_type
    if not ordered_axis:
        return "bar"
    if len({row.x for row in rows}) < _MIN_POINTS_FOR_A_LINE:
        return "bar"
    return chart_type


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
    window: ChartWindow | None = None,
    metric_display: Mapping[str, str] | None = None,
) -> ChartSpec | None:
    """Build one chart spec for a frame, or None when nothing is chartable.

    ``suppression_threshold`` is the §15 threshold the frame was policed
    under. Without it a bounded cell is indistinguishable from a measured
    one: the ceiling exists as structured data on the executed probe and
    reaches the chart as a point value. ``provisional_x`` names a bucket
    that has not settled.

    ``sort`` is ``(column, descending)`` as the PLAN resolved it, published
    on the spec so the renderer orders the marks the way the findings above
    them were ordered. It is passed through, never derived: a chart with no
    plan ordering says so rather than implying one.

    ``metric_display`` maps metric ids to the governed display names that
    say what each number actually measures. A chart title names a metric to
    a human, so it is a surface the display overlay owns: without it the
    title read ``timely filing at risk dollars — main  compare`` — an
    overclaiming id, de-snaked, beside a FRAME ID, which is engine
    vocabulary a reader has no use for (docs/client-language.md §3).

    ``window`` names the period(s) this frame was measured over, and is
    read only when the frame has no dimension and no time bucket — the case
    that has no category to key marks by (see the module docstring).
    Omitted there, the axis says :data:`UNNAMED_CURRENT_LABEL` rather than
    falling back to anything derived from the numbers themselves.
    """
    value = primary_measure(frame)
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

    # With neither a time bucket nor a dimension there is no column that
    # names a category. Falling back to ``value`` — the MEASURE column,
    # whose cells are the numbers being charted — files every mark under its
    # own figure. The axis is the window instead; it is synthetic, so it is
    # never looked up in ``frame.schema``.
    period_axis = time_col is None and not dims
    x = time_col or (dims[0] if dims else PERIOD_SERIES_COLUMN)
    series = dims[0] if time_col is not None and dims else (dims[1] if len(dims) > 1 else None)

    # A compare frame draws BOTH windows — but only when the frame has no
    # other series to spend: a chart already grouping by a
    # second dimension cannot also group by period without inventing a
    # third axis, and the honest thing there is to keep drawing the current
    # window and let the caveats say the rest.
    prior_column = f"{value}{_PRIOR_SUFFIX}"
    two_sided = series is None and prior_column in frame.schema.names
    if two_sided:
        series = PERIOD_SERIES_COLUMN

    referents = row_referents or {}
    x_is_dim = x in dims
    bounds = bounded_rows(frame, value, suppression_threshold)
    prior_bounds = (
        _bounded_rows(frame, value, suppression_threshold, suffix=_PRIOR_SUFFIX)
        if two_sided
        else {}
    )
    # Derived when the caller did not name one. A caller that DID name a
    # bucket wins: it knows which finding it is drawing under.
    censored_x = provisional_x if provisional_x is not None else provisional_bucket(frame, value)
    # The two ticks of the period axis, composed once. Never read out of a
    # cell, and never a bare number: they are the only thing standing
    # between this chart and an axis labelled with its own measurement.
    current_period = window.current_label if window is not None else UNNAMED_CURRENT_LABEL
    prior_period = (window.prior_label if window is not None else None) or UNNAMED_PRIOR_LABEL
    rows: list[ChartRow] = []
    for row_index, row in enumerate(frame.rows):
        if period_axis:
            x_value: object | None = current_period
        else:
            x_value = row[frame.schema.index_of(x)] if x in frame.schema.names else None
        series_value: object | None = None
        if two_sided:
            series_value = CURRENT_SERIES
        elif series is not None:
            series_value = row[frame.schema.index_of(series)]
        referent_id: str | None = None
        # A period label is not a dimension value, so nothing is registered
        # against it and nothing is looked up: a drill handle here would
        # compile to a DrillInto on a group that does not exist.
        if x_is_dim:
            referent_id = referents.get((x, str(x_value)))
        if referent_id is None and series is not None and series in dims:
            referent_id = referents.get((series, str(series_value)))
        current_mark = ChartRow(
            x=str(x_value),
            series=str(series_value) if series_value is not None else None,
            value=_cell(row[frame.schema.index_of(value)]),
            referent_id=referent_id,
            is_bound=row_index in bounds,
            bound_population=bounds.get(row_index),
            provisional=censored_x is not None and str(x_value) == censored_x,
        )
        prior_mark = (
            ChartRow(
                # On a dimension axis both marks share the category and are
                # told apart by their series. On the PERIOD axis the mark's
                # category IS its window, so the baseline takes its own
                # tick — the alternative keys both windows by one of the two
                # values.
                x=prior_period if period_axis else str(x_value),
                series=PRIOR_SERIES,
                value=_cell(row[frame.schema.index_of(prior_column)]),
                referent_id=referent_id,
                is_bound=row_index in prior_bounds,
                bound_population=prior_bounds.get(row_index),
                # A settlement caveat is about the CURRENT bucket; the
                # baseline it is compared against has already settled.
                provisional=False,
            )
            if two_sided
            else None
        )
        if prior_mark is not None and period_axis:
            # Two ticks on one axis are read left to right as time, so the
            # baseline is drawn first. (On a dimension axis the pair sits
            # inside one category and this ordering would say nothing.)
            rows.extend((prior_mark, current_mark))
        elif prior_mark is not None:
            rows.extend((current_mark, prior_mark))
        else:
            rows.append(current_mark)

    # Defensive: on the period axis every tick was composed above from
    # dates. If one ever equals the cell it labels, the measurement has
    # leaked back onto the category axis — better a loud failure here than
    # stored specs keyed by their own figures.
    assert not period_axis or all(mark.x != str(mark.value) for mark in rows)

    annotations: list[str] = []
    if two_sided and period_axis:
        # The dimension-axis wording below would be false here: these two
        # marks are not two series on ONE category, they are one measure on
        # two windows, and the axis says which is which.
        annotations.append(
            f"comparison: one measure on two windows — {current_period} is "
            f"{UNNAMED_CURRENT_LABEL} and {prior_period} is {UNNAMED_PRIOR_LABEL}. "
            "They are not summed."
        )
    elif two_sided:
        annotations.append(
            f"comparison: two series per category — one is {UNNAMED_CURRENT_LABEL} and the "
            f"other is {UNNAMED_PRIOR_LABEL}. They are not summed."
        )
    if two_sided and not period_axis:
        # The mirror case: the compare operator outer-joins and zero-fills
        # additive units, so a key present only on the prior
        # side arrives with a current value of 0. Drawn as such it reads as
        # a collapse to nothing; counted here it reads as what it is. Only
        # a keyed frame can have this: with no dimension there is no join
        # key to be missing, so a current 0 there is a measured 0 and
        # calling it an absence would be the opposite error.
        prior_only = sum(
            1
            for row in frame.rows
            if row[frame.schema.index_of(value)] in (0, None)
            and _is_positive(row[frame.schema.index_of(prior_column)])
        )
        if prior_only:
            annotations.append(
                f"prior-only categories: {prior_only} of {len(frame.rows)} categories carry a "
                "figure on the comparison window and none on this one — their current mark is "
                "an absence, not a measured zero."
            )
    if frame.truncated:
        annotations.append(
            "truncated: not all cells are shown — only the leading ones were kept when this "
            "figure was measured"
        )
    if bounds:
        # Counted from the marks actually drawn, so the chart caption, the
        # narrative and the probe metadata cannot state three different
        # numbers for one control.
        annotations.append(
            f"upper bounds: {len(bounds)} of {len(frame.rows)} marks are ceilings, not "
            "measurements — their numerator was suppressed and they cannot be ranked "
            "against the measured marks"
        )
    # The census rule lives in the kernel and is asked, never re-derived,
    # so this annotation and the engine's own `Of N cell(s) on this answer…`
    # sentence cannot count a null row two different ways.
    withheld = len(withheld_row_indices(frame, value))
    # An answer that refuses to rank these cells sits above a chart that
    # will sort them by value anyway — "ordered by denial_rate, high to
    # low" over a column that is mostly ceilings orders by panel size. The
    # refusal is restated on the figure so a renderer can suppress the
    # ordering, and a reader who only sees the chart is told.
    drawn = len(frame.rows) - withheld
    if bounds and drawn and len(bounds) / drawn > _MAX_BOUNDED_SHARE_FOR_RANKING:
        annotations.append(
            "These marks are not ranked, whatever order they are drawn in: "
            f"{len(bounds)} of the {drawn} marks with a figure are ceilings, "
            f"leaving {drawn - len(bounds)} measured — too few for an order to mean anything. "
            "Putting ceilings in order beside measured figures sorts by how big each group is."
        )
    if withheld:
        annotations.append(
            f"withheld: {withheld} of {len(frame.rows)} groups are too small to publish at all "
            "and are drawn with no value"
        )
    elif frame.suppressed_cells > 0 and not bounds:
        annotations.append(
            f"suppression: {frame.suppressed_cells} cells were withheld because their "
            "groups were too small to publish"
        )

    # Last, over everything above it — see ``_shape_corrected_type``. The
    # rows exist by now, so this is the first point at which the number of
    # categories a line would be drawn through is known.
    drawn_type = _shape_corrected_type(chart_type, rows, ordered_axis=time_col is not None)
    if drawn_type != chart_type and recipe is not None:
        points = len({mark.x for mark in rows})
        axis = (
            "the period axis"
            if period_axis
            else ("its time axis" if time_col is not None else "an axis that is in no order")
        )
        annotations.append(
            f"chart type: drawn as {_shape_words(drawn_type)} rather than "
            f"{_shape_words(chart_type)} — a line asserts a movement between ordered points, "
            f"and this figure draws {points} point{'' if points == 1 else 's'} on {axis}."
        )
    chart_type = drawn_type

    unit_col = frame.schema.columns[frame.schema.index_of(value)]
    return ChartSpec(
        id=f"chart_{frame_id}",
        chart_type=chart_type,
        title=_chart_title(value, dims, time_col, metric_display),
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


#: The operator whose output is a scorecard. Read off the frame's own
#: transform provenance rather than off its id: a chart builder that
#: sniffed ``__panel`` would draw the right figure for the wrong reason and
#: silently stop the day a planner renamed a step.
PANEL_OPERATOR = "panel"


#: Units whose cells sum, so that the head of the column is the biggest
#: rather than the best. Mirrors the findings stage's own ``_is_additive``.
_ADDITIVE_UNITS = frozenset({"money_cents", "count"})


def _is_additive_unit(unit: str | None) -> bool:
    return unit in _ADDITIVE_UNITS


def _unit_of(frame: EvidenceFrame, name: str) -> str | None:
    return frame.schema.columns[frame.schema.index_of(name)].unit


def is_panel(frame: EvidenceFrame) -> bool:
    """Did the ``panel`` operator produce this frame?"""
    return getattr(frame.provenance, "operator", None) == PANEL_OPERATOR


def build_panel_chart_specs(
    frame_id: str,
    frame: EvidenceFrame,
    *,
    row_referents: Mapping[tuple[str, str], str] | None = None,
    suppression_threshold: int | None = None,
    metric_display: Mapping[str, str] | None = None,
    per_measure_limit: int = 3,
) -> tuple[ChartSpec, ...]:
    """A scorecard, table first, then one small ranking per measure.

    THE TABLE IS NOT A FALLBACK HERE, IT IS THE FORM. Every other figure
    this product draws is one measure over a set of categories, and a
    scorecard is N measures over one set of entities — there is no bar
    chart of it, because there is no axis a denial rate and a dollar total
    can share. The table is the shape whose natural reading is "one row per
    payer, one column per measure", which is what the reader asked for.

    Rows are long-format — one mark per (entity, measure) — because that is
    the shape the wire keys marks by (``x``, ``series``) and the shape the
    row-key integrity check reads. Each mark carries its own ceiling flag
    and its own population, so a bounded cell in one column does not mark
    the whole row.

    The per-measure rankings follow, and only for measures the panel put in
    an order: they are the columns where "first" means something, each with
    its own unit, drawn as an ordinary bar chart with the ordering the
    panel already computed. A neutral measure gets no small ranking for the
    same reason it got no rank column.
    """
    entity = next(
        (col.name for col in frame.schema.columns if isinstance(col.ref, DimensionRef)),
        None,
    )
    measures = published_measures(frame)
    if entity is None or not measures:
        return ()
    display = metric_display or {}
    referents = row_referents or {}
    entity_index = frame.schema.index_of(entity)

    def label_for(name: str) -> str:
        return display.get(name) or _humanized(name)

    rows: list[ChartRow] = []
    bounds_by_measure = {
        name: bounded_rows(frame, name, suppression_threshold) for name in measures
    }
    for row_index, row in enumerate(frame.rows):
        key = str(row[entity_index])
        referent_id = referents.get((entity, key))
        for name in measures:
            bounds = bounds_by_measure[name]
            rows.append(
                ChartRow(
                    x=key,
                    series=label_for(name),
                    value=_cell(row[frame.schema.index_of(name)]),
                    referent_id=referent_id,
                    is_bound=row_index in bounds,
                    bound_population=bounds.get(row_index),
                )
            )
    annotations = [
        "one row per "
        f"{_humanized(entity)}, one column per measure. The columns are different "
        "measurements over different periods and are never added together or divided "
        "into each other."
    ]
    withheld = sum(len(withheld_row_indices(frame, name)) for name in measures)
    if withheld:
        annotations.append(
            f"withheld: {withheld} cells are too small to publish at all and are drawn with "
            "no value"
        )
    total_bounds = sum(len(found) for found in bounds_by_measure.values())
    if total_bounds:
        annotations.append(
            f"upper bounds: {total_bounds} cells are ceilings, not measurements — their "
            "numerator was suppressed, and they are outside the ordering for their column"
        )
    if frame.truncated:
        annotations.append(
            "truncated: not all cells are shown — only the leading ones were kept when this "
            "figure was measured"
        )
    specs: list[ChartSpec] = [
        ChartSpec(
            id=f"chart_{frame_id}",
            chart_type="table",
            title=f"Scorecard by {_humanized(entity)}",
            frame_id=frame_id,
            x=entity,
            # The measure the marks are keyed by. There is no single value
            # column on a panel, so the spec names the first one it draws
            # and the series carries the rest — the wire's `value` is the
            # column a one-measure renderer would fall back to.
            series="metric",
            value=measures[0],
            unit=None,
            grade=frame.evidence_grade.value,
            rows=rows,
            annotations=annotations,
        )
    ]
    # RATES BEFORE DOLLARS. Every ordered column can be drawn, and only
    # some of them are worth drawing small: "first on posted cash" is the
    # biggest payer and "first on the write-off rate" is the best one, so
    # when there is room for three the three are rates. Decided from the
    # unit, which is the same additive/relative split the findings stage
    # uses to decide whether a column may claim a leader at all.
    ordered = [name for name in measures if f"{name}__rank" in frame.schema.names]
    ranked = [
        name for name in ordered if not _is_additive_unit(_unit_of(frame, name))
    ] + [name for name in ordered if _is_additive_unit(_unit_of(frame, name))]
    for name in ranked[:per_measure_limit]:
        bounds = bounds_by_measure[name]
        index = frame.schema.index_of(name)
        marks = [
            ChartRow(
                x=str(row[entity_index]),
                series=None,
                value=_cell(row[index]),
                referent_id=referents.get((entity, str(row[entity_index]))),
                is_bound=row_index in bounds,
                bound_population=bounds.get(row_index),
            )
            for row_index, row in enumerate(frame.rows)
        ]
        column = frame.schema.columns[index]
        specs.append(
            ChartSpec(
                id=f"chart_{frame_id}_{name}",
                chart_type="bar",
                title=_sentence_case(f"{label_for(name)} by {_humanized(entity)}"),
                frame_id=frame_id,
                x=entity,
                series=None,
                value=name,
                unit=column.unit,
                grade=frame.evidence_grade.value,
                rows=marks,
                # Ordered by the panel's OWN ordering column, which already
                # holds the improvement direction the metric contract
                # declares. Sorting by value here would put the worst
                # denial rate first and call it the head of the list.
                sort=ChartSort(by=f"{name}__rank", direction="asc"),
                annotations=[
                    "ordered best first, in the direction this measure improves"
                ],
            )
        )
    return tuple(specs)


#: Time-bucket columns say which grain they are in their own id
#: (``time_bucket:month``); the axis phrase is the grain, not the token.
_BUCKET_PHRASE = {"day": "day", "week": "week", "month": "month", "quarter": "quarter"}


def _sentence_case(text: str) -> str:
    """Sentence case, per docs/client-language.md §4."""
    return text[:1].upper() + text[1:] if text else text


def _humanized(column: str) -> str:
    """A frame column as a reader reads it: ``service_line`` → "service line"."""
    return column.replace("_", " ").strip()


def _and_list(items: Sequence[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _chart_title(
    value: str,
    dims: Sequence[str],
    time_col: str | None,
    metric_display: Mapping[str, str] | None,
) -> str:
    """What this figure is, in the reader's words.

    Sentence case, the metric's governed display name, and the cut it is
    drawn across — never the frame id. The old composition put the storage
    unit in the title (``denied dollars — denial code mix  compare``): a
    frame id is engine vocabulary with no client rendering at all
    (docs/client-language.md §3), and the double space was the ``__compare``
    suffix wearing a costume.
    """
    display = (metric_display or {}).get(value) or _humanized(value)
    cut = ""
    if dims:
        cut = f" by {_and_list([_humanized(d) for d in dims])}"
    elif time_col is not None:
        grain = time_col.replace(_TIME_BUCKET_PREFIX, "").replace("_", " ")
        cut = f" by {_BUCKET_PHRASE.get(grain, grain)}"
    title = f"{display}{cut}".strip()
    return title[:1].upper() + title[1:] if title else display


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
    windows: Mapping[str, ChartWindow] | WindowFacts | None = None,
    metric_display: Mapping[str, str] | None = None,
    subject_metric: str | None = None,
) -> tuple[ChartSpec, ...]:
    """Chart the displayable frames, derived (compare/share) outputs first.

    ``sorts`` maps a frame id to the ``(column, descending)`` the plan
    resolved for it (see ``resolved_orderings``); frames absent from it
    were not ordered by the plan and publish no sort.

    ``subject_metric`` is the metric the question is about, when the
    interpretation resolved one. Frames that publish it are charted first;
    nothing is dropped and nothing is added, so an answer that used to ship
    four charts still ships four.

    ``windows`` names the period(s) the frames were measured over, for the
    frames that have no dimension to key their marks by. Either form is
    accepted, and they mean the same thing:

    * the turn's **context header** — anything carrying the four window
      dates (:class:`WindowFacts`). One turn has one effective window, so
      this is the ordinary call, and it lets the API pass the payload it
      already holds instead of importing :class:`ChartWindow`;
    * a **mapping** of frame id → :class:`ChartWindow`, with an optional
      :data:`DEFAULT_WINDOW_KEY` entry standing for the rest. For a caller
      that really does have per-frame windows.

    Absent, a dimensionless frame still charts — its axis says
    :data:`UNNAMED_CURRENT_LABEL`. What it must never do is fall back to
    the measured value.

    Two rules keep one figure per fact:

    * a frame whose comparison was computed is SUPERSEDED by that
      comparison — charting ``main`` and ``main__compare`` identically
      leaves the reader with the current window twice;
    * whatever survives that is deduplicated on CONTENT rather than on a
      suffix list. A hard-coded list like ``["__compare", "__prior"]``
      misses a ``__compare__share`` the planner invented later, and two
      byte-identical stacked bars render under one composed title. A
      content key cannot be outgrown by the next suffix.
    """
    # Only frames that will actually be DRAWN can supersede another: a
    # ``main__compare__rank`` is skipped as presentation metadata, and
    # letting it retire ``main`` would leave the answer with no figure.
    ids = {
        frame_id
        for frame_id, _ in frames
        if not frame_id.endswith(("__prior", "__rank"))
    }
    # Normalised once: a header is one window for every frame on the turn.
    by_frame: Mapping[str, ChartWindow]
    if isinstance(windows, Mapping):
        by_frame = windows
    else:
        turn_window = window_from_facts(windows)
        by_frame = {} if turn_window is None else {DEFAULT_WINDOW_KEY: turn_window}
    ranked = sorted(
        frames,
        key=lambda item: (
            # THE SCORECARD FIRST, ahead of even the subject metric. A
            # panel is the whole answer and the frames beside it are the
            # columns it was assembled from, so a limit spent on them
            # leaves the reader every part of the figure except the figure.
            not is_panel(item[1]),
            # The metric the QUESTION is about, first. A playbook charts its
            # probe set, not the answer: "do we owe any refunds?" shipped
            # cash-posted and expected-reimbursement charts, and in 18 of 26
            # live answers at least one charted metric had no finding
            # anywhere on the response. The others still ship — the client
            # already folds "N more charts" — they simply stop going first.
            subject_metric is not None and subject_metric not in published_measures(item[1]),
            "__compare" not in item[0],  # compare outputs first
            not _dimension_columns(item[1]),  # dimensional frames before totals
        ),
    )
    specs: list[ChartSpec] = []
    seen: dict[tuple[object, ...], ChartSpec] = {}
    for frame_id, frame in ranked:
        if len(specs) >= limit:
            break
        if frame_id.endswith("__prior"):
            continue  # drawn as the baseline series of the compare output
        if frame_id.endswith("__rank"):
            continue  # rank columns are presentation metadata, not a new view
        if _superseded_by_comparison(frame_id, ids):
            continue
        if is_panel(frame):
            # A scorecard is not one measure over categories, so the
            # single-value builder below has nothing to draw of it: it
            # would pick the money column and publish a bar chart of one
            # column of a table the reader asked for whole.
            for panel_spec in build_panel_chart_specs(
                frame_id,
                frame,
                row_referents=row_referents,
                suppression_threshold=suppression_threshold,
                metric_display=metric_display,
            ):
                if len(specs) >= limit:
                    break
                seen[_content_key(panel_spec)] = panel_spec
                specs.append(panel_spec)
            continue
        spec = build_chart_spec(
            frame_id,
            frame,
            recipes=recipes,
            playbook_id=playbook_id,
            suggestion=suggestion,
            row_referents=row_referents,
            suppression_threshold=suppression_threshold,
            sort=(sorts or {}).get(frame_id),
            # A frame's own window wins over the turn's, when a caller has
            # one; neither is invented when the caller passed nothing.
            window=by_frame.get(frame_id, by_frame.get(DEFAULT_WINDOW_KEY)),
            metric_display=metric_display,
        )
        if spec is None or not spec.rows:
            continue
        key = _content_key(spec)
        twin = seen.get(key)
        if twin is not None:
            # Named, never silently dropped: a figure that was computed and
            # not drawn is a fact about the answer.
            twin.annotations.append(
                "identical to a figure this answer already drew, which is "
                "therefore not drawn twice"
            )
            continue
        seen[key] = spec
        specs.append(spec)
    return tuple(specs)


def _superseded_by_comparison(frame_id: str, ids: set[str]) -> bool:
    """Is this frame's whole content already inside a comparison frame?

    ``main`` against ``main__compare`` is the case: the compare frame holds
    the same rows plus the baseline, and drawing both publishes the current
    window twice on an answer that is about two windows.
    """
    prefix = f"{frame_id}__compare"
    return any(other != frame_id and other.startswith(prefix) for other in ids)


def _content_key(spec: ChartSpec) -> tuple[object, ...]:
    """What a chart DRAWS, as a comparable value."""
    return (
        spec.chart_type,
        spec.x,
        spec.series,
        spec.value,
        spec.unit,
        tuple((row.x, row.series, row.value) for row in spec.rows),
    )
