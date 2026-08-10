"""Bounded values, the selection census, and the warnings a selection earns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from revi_investigation.application.calculation_glue import (
    EmptinessFact,
)
from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.execution import (
    TOO_SMALL_TO_MEASURE,
    BoundedCell,
)
from revi_investigation.application.findings.shapes import ConcentrationShape, MovementShape
from revi_investigation.application.planning import InvestigationPlan
from revi_investigation.application.ports import RegisteredReferent
from revi_investigation.application.rendering import (
    COUNT_UNIT as _COUNT_UNIT,
)
from revi_investigation.application.rendering import (
    MONEY_UNIT as _MONEY_UNIT,
)
from revi_investigation.application.rendering import (
    format_value,
    metric_label,
    render_row_label,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import Finding
from revi_kernel.filters import Scalar
from revi_kernel.grades import EvidenceGrade

_QUALIFIED_GRADES = (EvidenceGrade.PROXY, EvidenceGrade.DISCOVERY, EvidenceGrade.UNAVAILABLE)


#: Share of a ranking frame's published cells that may carry an upper bound
#: before the ranking itself stops meaning anything.
#:
#: A bound is exactly ``(threshold - 1) / n``, so a frame that is mostly
#: bounds sorts by inverted panel size while presenting itself as a ranking
#: ("Dr. Casey Quarry ranks #1 by denial rate (worst first, as asked)" over
#: 147 ceilings in 150 values). Past this share there is no measured
#: population left to rank, and the honest answer is the population
#: arithmetic rather than an order.
MAX_BOUNDED_SHARE_FOR_RANKING = 0.5


def _bound_values(measure: str, bound: BoundedCell | None) -> list[tuple[str, Scalar]]:
    """The bound, as named values on the finding rather than as prose.

    Three additive names, so every consumer — card, chart, CSV, replay —
    can ask the same question the title now answers: is this a measurement
    (``__is_bound`` absent or false), what is the ceiling, and how big was
    the population it was taken over.
    """
    if bound is None:
        return []
    return [
        (f"{measure}__is_bound", True),
        (f"{measure}__bound", bound.bound),
        (f"{measure}__bound_population", bound.population),
    ]


def bound_text(value: Scalar, unit: str | None, *, bounded: bool) -> str:
    """A published figure, with the ``≤`` a suppressed numerator earns it.

    The single place a bound becomes visible prose. Rendering a ceiling
    through the ordinary value formatter makes every title, statement, chart
    label and export cell say "45.5% denial rate" about 10/22, with the
    ``≤`` surviving only inside a warning string.
    """
    text = format_value(value, unit)
    return f"≤ {text}" if bounded else text


#: Units whose totals scale with the length of the window they are measured
#: over. A length-mismatched comparison distorts these and leaves a rate
#: alone, which is why the mismatch caveat is applied per unit rather than
#: per turn.
_ADDITIVE_UNITS = (_MONEY_UNIT, _COUNT_UNIT)


def _is_additive(unit: str | None) -> bool:
    return unit in _ADDITIVE_UNITS


@dataclass(frozen=True, slots=True)
class FindingsResult:
    findings: tuple[Finding, ...]
    referents: tuple[RegisteredReferent, ...]
    #: What the analyst has to be told about the *selection* before they
    #: read the rows — chiefly that nothing moved the way they asked about.
    #: These lead the turn's warnings: a caveat published under the findings
    #: it contradicts is a caveat nobody reads.
    warnings: tuple[str, ...] = ()
    #: Set when frames had rows and no finding survived selection. The
    #: other half of :class:`EmptinessFact`: "there is nothing here" and
    #: "there is plenty here and none of it is notable" are different
    #: answers, and publishing both as silence made them the same one.
    emptiness: EmptinessFact | None = None


def _with_premise(
    result: FindingsResult, premise: tuple[Finding, RegisteredReferent, str] | None
) -> FindingsResult:
    """Put the premise verdict at the head of a non-movement result.

    The verdict is registered before the branch runs, so the branch's own
    findings are already numbered from F2 — this only splices the published
    objects back into the order the reader sees them in.
    """
    if premise is None:
        return result
    finding, referent, warning = premise
    return FindingsResult(
        findings=(finding, *result.findings),
        referents=(referent, *result.referents),
        warnings=(warning, *result.warnings),
        # A premise verdict IS a finding, so a turn carrying one is never
        # empty however little the rest of the plan found.
        emptiness=None,
    )


def _declared_bucket_order(
    plan: InvestigationPlan | None, shape: ConcentrationShape
) -> tuple[str, ...] | None:
    """The catalog's declared order for this cut, when it is an ordinal one."""
    if plan is None or len(shape.dimension_columns) != 1:
        return None
    return plan.bucket_order(shape.dimension_columns[0])


@dataclass(frozen=True, slots=True)
class SelectionCensus:
    """Every cell of a ranking frame, counted once and accounted for.

    Two censuses of one frame can contradict each other on one card — "52
    of the 52 publishable denial rate cells … leaving 0 measured, so no
    ranking is published" beside "Of 150 cell(s) on this answer, 52 carry an
    upper bound, 85 were withheld outright and 13 are measured". That
    refusal is manufactured by discarding the 13 measured cells as padding:
    they are providers with a **0% denial rate**, dropped by a rule written
    for counts ("Payer X: 0 mismatched claims") and applied to rates. Zero
    is a measurement, and in a denial-rate ranking it is the most
    informative one there is.

    The four buckets partition the frame by construction
    (``total == bounded + measured + empty + withheld``), so no two
    sentences on one answer can state different arithmetic about it.
    """

    #: Rows in the ranking frame.
    total: int
    #: Publishable rows whose value is a ceiling, not a measurement.
    bounded: int
    #: Publishable rows carrying a measurement — zeros included.
    measured: int
    #: Rows dropped as empty. Only ever additive units: a 0 in a count or a
    #: dollar column is a ranking's tail, a 0 in a rate column is a result.
    empty: int

    @property
    def publishable(self) -> int:
        return self.bounded + self.measured

    @property
    def withheld(self) -> int:
        """Rows the small-cell policy nulled outright — the remainder."""
        return max(self.total - self.publishable - self.empty, 0)

    @property
    def bounded_share(self) -> float:
        return self.bounded / self.publishable if self.publishable else 0.0

    def as_payload(self) -> dict[str, int]:
        """The full census, for the trace.

        Recorded here rather than appended to the answer's own disclosure,
        which would state the same arithmetic twice — once in words and once
        in the machine's ("Of 30 cell(s) on this answer, 24 carry an upper
        bound…"). The page says the count once, in words; an auditor checks
        the complete partition here.
        """
        return {
            "total_rows": self.total,
            "bounded_rows": self.bounded,
            "measured_rows": self.measured,
            "empty_rows": self.empty,
            "withheld_rows": self.withheld,
        }


def row_noun(dimension_columns: Sequence[str]) -> str:
    """What the rows of this answer ARE, in the analyst's vocabulary.

    "24 of 30 plans are too small to measure exactly" is actionable;
    "24 of the 30 publishable denial rate cells" is the engine talking to
    itself. The cut's own id is the closest thing the engine has to the
    reader's word for its rows, so it is the word used.
    """
    if not dimension_columns:
        return "rows"
    label = dimension_columns[0].replace("_", " ")
    return label if label.endswith("s") else f"{label}s"


def _unranked_bounds_warning(
    *,
    census: SelectionCensus,
    measure: str,
    unrankable: bool,
    order: object | None,
    noun: str = "rows",
) -> str:
    """What a ranking owes a reader once some of its rows are ceilings.

    Written for the reader who has to act on it: the meaning leads, the
    count is stated ONCE and in words, and the full partition goes to the
    trace (:meth:`SelectionCensus.as_payload`) where an auditor can check
    it. Opening with a count, using machine vocabulary ("publishable …
    cells"), carrying three words for two ideas ("upper bound", "ceiling",
    "measurement") or restating the census as arithmetic all make the
    paragraph unreadable.
    """
    label = metric_label(measure)
    if unrankable:
        return (
            f"ranking_refused: most of these {noun} are {TOO_SMALL_TO_MEASURE}, so no ranking "
            f"is published. {census.bounded} of {census.total} are {TOO_SMALL_TO_MEASURE} — for "
            "those only a ceiling is known, and putting ceilings in order beside measured "
            f"figures sorts by how big each group is rather than by {label}. The "
            f"{census.measured} that could be measured are published above; the rest are listed "
            "separately, each with the size of the group its ceiling was taken over."
        )
    asked = " (the order you asked for applies to the measured rows only)" if order else ""
    return (
        f"bounded_cells_unranked: {census.bounded} of {census.total} {noun} are "
        f"{TOO_SMALL_TO_MEASURE} — for those only a ceiling is known, so they are listed below "
        f"the ranking rather than inside it{asked}. The ranking covers the {census.measured} "
        "that could be measured: a ceiling has no place in an order it was never measured for."
    )


def _truncation_warning(served: int, computed: int, spec: AnalysisSpec) -> str | None:
    """What a truncated finding list owes its reader.

    Without it, "show me all twelve payers, not just three" returns three
    findings with no omission notice over an evidence panel reading
    ``rows: 12, limit: null, truncated: false``, and the narrative calls a
    4.4% to 15.0% spread "roughly three percentage points … a tight band"
    over the three it can see.

    ``computed`` is the frame the CHART draws, not the selection that was
    kept: counting the direction-filtered set instead makes the census read
    "3 of 10" beside a twelve-row chart, and then vanish on the expand —
    suppressed because served (10) is no longer under a computed (10) that
    had already been narrowed.

    A count the analyst NAMED is also an obligation. When they asked for
    twelve and twelve is not what came back, this fires whether or not the
    frame had more rows to give, and says which of the two happened.
    """
    shortfall = spec.limit is not None and spec.limit > served
    if computed <= served and not shortfall:
        return None
    if computed <= served:
        return (
            f"findings_truncated: this answer publishes all {served} of the {computed} cell(s) "
            "this cut computed — the row count asked for is larger than the population, so "
            f"there are no further rows to show. Superlatives and spread statements describe "
            f"those {served}."
        )
    asked = (
        f" The {spec.limit} rows asked for could not be met: {served} are published."
        if shortfall
        else ""
    )
    return (
        f"findings_truncated: {served} of {computed} computed cells are published as findings; "
        f"the remaining {computed - served} are in the chart and the evidence frame but carry "
        "no finding. Superlatives and spread statements on this answer describe the published "
        f"slice, not the full population.{asked}"
    )


def _direction_omission_warning(
    counter: list[tuple[Scalar, ...]],
    published: int,
    shape: MovementShape,
    spec: AnalysisSpec,
    pack: PackPort,
) -> str | None:
    """Name the cells a direction filter removed.

    A question that asserts a rise narrows the finding population to the
    cells that rose, and every census on the card then counts the narrowed
    set: "show me all twelve" returns ten, the two missing are the only two
    that IMPROVED, and the card reads as "all twelve payers got worse" over
    a population in which two got better. A directional selection is
    legitimate; a silent one is not.
    """
    if not counter or spec.direction is None:
        return None
    names = ", ".join(
        render_row_label(
            pack,
            shape.dimension_columns,
            {
                dim: row[shape.frame.schema.index_of(dim)]
                for dim in shape.dimension_columns
            },
        )
        for row in counter[:8]
    )
    more = f" (and {len(counter) - 8} more)" if len(counter) > 8 else ""
    listed = (
        f"The first {published} of them are listed below the asked set, labelled."
        if published
        else "They carry no finding on this answer."
    )
    return (
        f"direction_omitted: {len(counter)} of {len(shape.frame.rows)} cell(s) moved the OTHER "
        f"way and are therefore not part of what {spec.direction.value!r} asked about: {names}"
        f"{more}. {listed} Any statement about how this whole population moved has to count "
        "them."
    )
