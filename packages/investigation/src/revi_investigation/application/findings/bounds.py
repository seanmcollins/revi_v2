"""Bounded values, the selection census, and the warnings a selection earns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from revi_investigation.application.calculation_glue import (
    EmptinessFact,
)
from revi_investigation.application.capability_ports import PackPort, PlaybookSpec
from revi_investigation.application.execution import (
    TOO_SMALL_TO_MEASURE,
    BoundedCell,
)
from revi_investigation.application.findings.shapes import (
    ConcentrationShape,
    MovementShape,
    as_number,
)
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
    measure_phrase,
    metric_label,
    plural,
    ratio_pct,
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


#: The named value that says a finding is the WHOLE rather than a row of
#: it. Read by :func:`revi_presentation.narrative._measured_leader`, which
#: may not import this package — the literal is pinned on both sides by a
#: test, exactly as the premise verdict's own value names are.
AGGREGATE_VALUE = "aggregate_total"


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


#: The pack ranking policy that says a deadline question is ordered by the
#: clock. Declared in ``packs/base-rcm/policies.yaml``; held as a literal
#: here because the id is pack content and this module is the only reader,
#: and pinned on both sides by a test.
URGENCY_FIRST_POLICY = "urgency_first"

#: …and the one that says a catch-all bucket is never the answer.
RESIDUAL_LAST_POLICY = "residual_last"

#: Catch-all dimension values: the cell every row that did not classify
#: falls into. They are legitimately the LARGEST cell in several cuts and
#: they are never the ANSWER to "what are we getting denied for most" —
#: "OTHER accounts for $619,434.56, or 19.0% of the total" tells a reader
#: nothing they can work.
#:
#: Matched case-insensitively on the leading dimension's value, and held
#: here rather than read off the catalog because the catalog's value
#: domains live outside this repository's pack territory. The set is closed
#: and small on purpose: a wide list would demote a real category.
RESIDUAL_VALUES: frozenset[str] = frozenset(
    {"other", "unclassified", "unknown", "unspecified", "n/a", "none"}
)


def _declared_bucket_order(
    plan: InvestigationPlan | None,
    shape: ConcentrationShape,
    playbook: PlaybookSpec | None = None,
) -> tuple[str, ...] | None:
    """The catalog's declared order for this cut, when it is an ordinal one.

    A single-dimension ordinal cut always carries its own direction — that
    is what an ordinal bucket IS — and sequencing it by size tells a team to
    work the least urgent band first.

    A cut with a SECOND dimension is the case that mattered and the case
    this used to refuse: ``timely_filing_watch`` cuts by
    ``filing_runway_bucket`` **and** ``plan``, so the declared order was
    never read and "is anything about to miss a filing deadline?" headlined
    the ``90+`` band — the one furthest from the deadline — with ``expired``
    third. There the pack decides: a playbook whose whole point is a clock
    declares ``urgency_first``, and its leading dimension's declared order
    is the ranking whatever else it is cut by.
    """
    if plan is None or not shape.dimension_columns:
        return None
    if len(shape.dimension_columns) > 1 and (
        playbook is None or playbook.ranking_policy != URGENCY_FIRST_POLICY
    ):
        return None
    return plan.bucket_order(shape.dimension_columns[0])


def is_residual_row(
    row: tuple[Scalar, ...],
    shape: ConcentrationShape,
    playbook: PlaybookSpec | None,
) -> bool:
    """Is this row the cut's catch-all bucket, on a cut that says so?

    Governed rather than global: a pack that wants ``OTHER`` demoted says
    so on the playbook (:data:`RESIDUAL_LAST_POLICY`), and every other
    ranking in the product orders exactly as it did.
    """
    if playbook is None or playbook.ranking_policy != RESIDUAL_LAST_POLICY:
        return False
    index = shape.frame.schema.index_of(shape.dimension_columns[0])
    return str(row[index]).strip().casefold() in RESIDUAL_VALUES


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


def group_noun(dimension_columns: Sequence[str]) -> str:
    """What ONE ROW of a multi-dimension cut is, in the reader's words.

    :func:`row_noun` names the rows by their first cut, which is right for a
    single-dimension frame and false for two: ``filing_runway_bucket`` by
    ``plan`` has 144 rows and five buckets, so "across 144 filing runway
    buckets" is a count of one thing attached to the name of another.
    A row of that frame is a COMBINATION, and it is said as one.
    """
    if len(dimension_columns) < 2:
        return row_noun(dimension_columns)
    named = " and ".join(column.replace("_", " ") for column in dimension_columns)
    return f"{named} combinations"


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
    # Through the shared pluralizer, so "facility" comes back "facilities"
    # rather than "facilitys" on every surface that counts these rows.
    return label if label.endswith("s") else plural(2, label)


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


def panel_column_bounds_warning(
    *,
    census: SelectionCensus,
    measure: str,
    ordered: bool,
    noun: str = "rows",
) -> str:
    """What ONE column of a scorecard owes a reader once some of it is ceilings.

    The same rule as :func:`_unranked_bounds_warning` and deliberately not
    the same sentence. That one is written for an answer that IS a ranking,
    so it opens "most of these payers are too small to measure exactly, so
    no ranking is published" — true of the column and false of the card.
    Published on a scorecard it reads as the whole scorecard refusing, and
    the panel that carried it went out under a paragraph announcing that
    zero payers could be measured above a card that had measured six.

    So the column leads the sentence. What follows is the same fact and the
    same arithmetic: how many cells are ceilings, why a ceiling cannot take
    a place in an order, and what the rest of the card still says.

    **It is a trailing note, not a leading refusal**, which is the other
    half of the same correction. ``ranking_refused`` leads the answer
    because on a ranking it IS the answer — "a refusal cannot sit under the
    rows it refused to order". On a scorecard it is one column of eighteen,
    the verdict above it is unaffected, and leading with it opens a card
    that measured six payers on a paragraph about the one it could not.
    ``bounded_cells_unranked`` trails, which is where a bound on part of an
    answer belongs.
    """
    label = metric_label(measure)
    singular = noun[:-1] if noun.endswith("s") else noun
    counted = plural(census.publishable, noun[:-1] if noun.endswith("s") else noun)
    if not ordered:
        return (
            f"bounded_cells_unranked: {label} could not be put in order, so no {singular} is "
            f"named first on it. {census.bounded} of the {census.publishable} {counted} with a "
            f"figure for it {plural(census.bounded, 'is', 'are')} {TOO_SMALL_TO_MEASURE} — for "
            "those only a ceiling is known, and putting ceilings in order beside measured "
            f"figures sorts by how big each group is rather than by {label}. Every other measure "
            "on this scorecard is unaffected, and the column itself is still shown."
        )
    return (
        f"bounded_cells_unranked: {census.bounded} of the {census.publishable} {counted} with a "
        f"{label} figure {plural(census.bounded, 'is', 'are')} {TOO_SMALL_TO_MEASURE} — for those "
        "only a ceiling is known, so they sit outside the order for that column. It covers the "
        f"{census.measured} that could be measured: a ceiling has no place in an order it was "
        "never measured for."
    )


def verdict_warning(
    *,
    measure: str,
    total: Scalar,
    unit: str | None,
    census: SelectionCensus,
    noun: str,
    leader_label: str | None,
    leader_text: str | None,
    leader_share: Scalar | None,
    period_text: str,
    bands: Sequence[tuple[str, Scalar]] = (),
) -> str | None:
    """The yes or the no a yes/no question came for, said first.

    Six yes/no questions in the live corpus were answered without a yes or
    a no. "Do we owe any refunds right now?" opened on five sentences of
    settling caveat and never stated the total owed — which its own shares
    divide by. "Are any payers paying us less than the contract says?" — the
    answer is *yes, three of them, $197.6K*, and that sentence did not
    exist.

    Composed here, deterministically, from the same certified figures the
    findings beside it publish: the visible total, the census that says what
    is not in it, and the leading cell in whatever order this answer put its
    rows in — which on an ordinal cut is the most urgent BAND, not the
    biggest one.

    Three states, and only three:

    * a measured total above zero → **Yes**, with the figure and where it
      sits;
    * a measured total of exactly zero, over a population where nothing was
      withheld and nothing is a ceiling → **No**; a zero standing over
      censored cells is not a no, and returns ``None``;
    * anything else → ``None``. A verdict this platform cannot certify is
      not published as one, and the answer reads exactly as it did before.
    """
    amount = as_number(total)
    if amount is None:
        return None
    label = metric_label(measure)
    # ``measure_phrase`` rather than an f-string, for the reason it exists:
    # "153" + "cob mismatch claims" must read "153 cob mismatch claims", and
    # "179.5 days" + "days in ar" must not say the unit twice.
    figure = measure_phrase(format_value(total, unit), label, unit)
    counted = plural(census.measured, _singular(noun))
    if amount == 0:
        if census.withheld or census.bounded:
            return None
        return (
            f"verdict_lead: No — this answer measures no {label} {period_text}, across the "
            f"{census.measured} {counted} it read."
        )
    # An ordinal cut carries its own direction, and on a deadline question
    # that direction IS the answer: "$X is already past its filing deadline
    # and $Y expires inside 30 days" is what was asked, and the grand total
    # over every band — a claim with a year of runway and a claim already
    # lost in one figure — is the population it sits in.
    banded = ""
    stated = [
        f"{format_value(amount_of, unit)} of it in {name}"
        for name, amount_of in bands
        if as_number(amount_of) is not None
    ]
    if stated:
        banded = f" That is {', and '.join(stated)}."
    where = ""
    if leader_label is not None and leader_text is not None:
        share = (
            f", {ratio_pct(leader_share)} of it"
            if isinstance(leader_share, Decimal)
            else ""
        )
        where = (
            f" The largest single {_singular(noun)} is {leader_label}: {leader_text}{share}."
        )
    unaccounted = census.withheld + census.bounded
    unseen = (
        f" {unaccounted} further {plural(unaccounted, _singular(noun))} "
        f"{'was' if unaccounted == 1 else 'were'} withheld or carry only a ceiling, so the "
        "whole is at or above this."
        if unaccounted
        else ""
    )
    return (
        f"verdict_lead: Yes — {figure} {period_text}, across "
        f"{census.measured} {counted}.{banded}{where}{unseen}"
    )


def _singular(noun: str) -> str:
    """The plural noun this module is handed, in the number a sentence needs."""
    return noun[:-1] if noun.endswith("s") else noun


def benchmark_verdict_warning(
    *,
    measure: str,
    value: Scalar,
    unit: str | None,
    benchmark: object,
    period_text: str,
) -> str | None:
    """A yes/no against a governed peer RANGE, stated as a range.

    The best benchmark rendering in the live corpus states the range with
    its cohort and never as a pass/fail target, and this keeps that exactly:
    the verdict is the reader's own question answered ("are we at risk?"),
    the range is quoted whole, and the population it came from is named in
    the same breath. ``None`` whenever the comparison cannot be made — an
    unparseable range, a value that is not a number — because a verdict over
    a range nobody could read is worse than no verdict.
    """
    amount = as_number(value)
    low = _decimal_or_none(getattr(benchmark, "value_low", None))
    high = _decimal_or_none(getattr(benchmark, "value_high", None))
    cohort = getattr(benchmark, "cohort_label", None)
    if amount is None or low is None or high is None or not isinstance(cohort, str):
        return None
    label = metric_label(measure)
    figure = format_value(value, unit)
    band = getattr(benchmark, "range_text", f"{low}-{high}")
    if amount > high:
        return (
            f"verdict_lead: Yes — {label} is {figure} {period_text}, above the top of the "
            f"{band} range published for {cohort}."
        )
    if amount < low:
        return (
            f"verdict_lead: No — {label} is {figure} {period_text}, below the bottom of the "
            f"{band} range published for {cohort}."
        )
    return (
        f"verdict_lead: No — {label} is {figure} {period_text}, inside the {band} range "
        f"published for {cohort}."
    )


def _decimal_or_none(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


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
        f"the remaining {computed - served} are on the chart and in the evidence but carry "
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
