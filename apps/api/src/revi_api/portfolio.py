"""The anomaly portfolio: governed prioritization over detected anomalies.

Priority formula ``anomaly_priority@3`` (versioned platform code, like an
operator — the pack supplies only bounded parameters):

    ranked_cents     = the RECONCILED figure when this platform's
                       re-derivation diverged from the detector's, else the
                       detector's (see "Ranking the disputed figure" below)
    impact_norm      = |ranked_cents| / max(|ranked_cents|) over the population
    age_days         = (watermark.loaded_at - onset).days, onset = the
                       evidence fact ``onset_date`` when present, else
                       ``detected_at``
    recency          = 0.5 ** (age_days / half_life_days)
    recoverable      = |ranked_cents| x recoverable_fraction  (governed
                       actionability rules over the record's evidence facts)
    recoverable_norm = recoverable / max(|ranked_cents|)
    priority         = (w_i·impact_norm + w_r·recency + w_a·recoverable_norm)
                       / (w_i + w_r + w_a)
    floor            = compliance-mandatory categories never score below
                       the MEDIAN score of the non-floored population

Weights, half-life, and the absolute fallback floor come from the pack's
bounded detector policies (``anomaly_priority_*``); recoverable fractions
come from the governed actionability rules. Items are sorted by priority
desc, ties by |impact| desc, then id — and every card carries the
decomposed components so the ranking is never a black box. Resolved /
self-resolved records are excluded (and absent-at-watermark anomalies
never appear, because the source reads per snapshot).

Ranking the disputed figure
===========================
A card publishes two figures and must say which one ordered it. Ranking on
``impact_cents`` — the DETECTOR's assertion — while the same payload calls
the re-derivation a divergence sets the worklist order by the side of the
platform's own disagreement it does not stand behind, and prices the
recoverable estimate off that same disputed figure.

Each card is therefore ranked on the figure the platform is prepared to
defend:

* ``impact_agreement == "diverged"`` and a re-derivation exists →
  ``reconciled_impact_cents`` ranks the card (``ranked_on="platform"``);
* ``impact_agreement == "not_comparable"`` → the DETECTOR's figure ranks
  it (``ranked_on="not_comparable"``), because the platform's number is a
  balance and the card's is a flow, and substituting one for the other
  would be a different claim rather than a better one;
* ``agreed`` / ``unavailable`` → the detector's figure ranks it
  (``ranked_on="detector"``).

Nothing is overwritten. ``impact_cents`` remains the detection system's
assertion — the card's provenance — and ``reconciled_impact_cents``
remains this platform's. Every card says WHICH of them ordered it
(``ranked_on``), what that figure was (``ranked_impact_cents``), why
(``ranked_on_note``), and what the population's normalizer was
(``priority.impact_normalizer_cents``). The recoverable estimate follows
the same figure, the normalizer is taken over the same figures, and the
relative compliance floor is the median of the resulting scores — so the
whole worklist is priced on one consistent basis instead of two.

The compliance floor
====================
A compliance-mandatory item must be worked regardless of size. Serving
that policy as a constant floor of ``0.60`` put an $824 credit balance
above a $178,217 critical DNFB card, so it is served three ways instead:

- **The floor is relative**: the median score of the non-floored
  population, computed at build time. A compliance item is lifted to "as
  important as the middle of the worklist", which is what "must do
  regardless of size" actually means, rather than to a constant that
  outranks every real finding on the list. The absolute governed value
  remains the fallback for a build with nothing left un-floored to take a
  median of, and the response says which basis was used. Detection
  provenance is untouched: this is a serving-time formula with a version
  string.
- **Compliance items are their own lane.** ``items`` stays one ranked
  array; each card names its ``lane`` and the response lists the lanes,
  so a UI renders "must-do regardless of size" as a section instead of
  letting it masquerade as the highest-value work in the building.
- **The arithmetic is published.** Every card carries the three
  normalized components, the three weighted terms, the normalizer, the
  score before the floor and the floor itself — so the ranking is
  checkable with a calculator instead of trusted.

Card/drill agreement
====================
A card's figure and its drill's answer are two different claims — the
detector's assertion in its own window, population and basis, against this
platform's governed contract re-derived at the pinned watermark — and both
are honest. The silence about the gap was not: a card published
``$178,217``, drilling it answered ``$195,873.92``, and nothing on either
screen said they disagreed.

So each drillable card's figure is **re-derived at build time**
(:mod:`revi_api.rederive`, the drillability pipeline continued two stages
further) and published beside the detector's as
``reconciled_impact_cents``, with the delta, the agreement verdict and
the reason the two may differ. A reader who checks can no longer find a
discrepancy the payload did not already state.

Cards carry **provenance instead of an evidence grade**: the underlying
anomaly is an external detection system's assertion, not a number this
platform computed from certified semantics, so each card is stamped
``provenance="external_detection"`` with the priority formula version and
the source watermark. See :class:`AnomalyCard` for the full rationale.

Ranking honesty
===============
The governed priority formula answers "what matters most". It does not
answer "what can I open", and the two can be almost perfectly
anti-correlated: one build ranked 33 cards of which 6 were drillable, put
the first drillable one at **rank 17**, and left ~90% of the ranked
dollars behind an error dialog. Two things address that, neither of which
touches the formula:

- **Drillability is decided before the card is published.** Each drill
  spec is run through the *real* planning and §6.6 validation pass — no
  warehouse query, no execution — and the card carries ``drillable`` plus
  the platform's own refusal text. Undrillable cards keep their score and
  their detected evidence, and sort below every card that can be opened,
  so the top of the worklist is work somebody can start today.
- **Drill handles may be repointed by governed content.** A record whose
  metric id cannot express the impact it published (a ratio contract
  reporting dollars) drills the contract that can, and says on the card
  which metric it substituted and why. See ``drill_repoints`` in
  ``packs/base-rcm/anomaly_actionability.yaml``.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from revi_api.actionability import ActionabilityRules, DimensionRepoint, assess
from revi_api.metric_display import MetricDisplayRules
from revi_api.rederive import ImpactComparison, ReDerivedImpact, compare_impact
from revi_api.warning_codes import structured_warnings
from revi_investigation.application.ports import AnomalyRecord
from revi_investigation_contracts.api import (
    AnomalyCard,
    AnomalyDimension,
    PortfolioLanePayload,
    PortfolioResponse,
    PriorityDecompositionPayload,
    TypedInvestigationSpec,
)
from revi_investigation_contracts.api import (
    DrillDimensionRepoint as DrillDimensionRepointPayload,
)
from revi_investigation_contracts.refinements import AbsoluteWindowModel, AddFilterModel
from revi_kernel.watermark import DataWatermark
from revi_pack.domain import PackSnapshot

PRIORITY_FORMULA_VERSION = "anomaly_priority@3"

_RESOLVED_STATUSES = frozenset({"resolved", "self_resolved", "closed", "dismissed"})

#: The two lanes of the worklist, in render order.
COMPLIANCE_LANE = "compliance"
VALUE_LANE = "value"

_LANE_LABELS: dict[str, tuple[str, str]] = {
    COMPLIANCE_LANE: (
        "Must do regardless of size",
        "Compliance-mandatory categories. These are worked because the rule "
        "says so, not because they are the largest thing on the list — a "
        "$824 credit balance and a $84,000 one carry the same obligation. "
        "Their score is floored to the median of the value-ranked work so "
        "they stay visible without displacing it.",
    ),
    VALUE_LANE: (
        "Ranked by value recoverable",
        "Ordered by a standard priority formula: normalized impact, "
        "recency, and the recoverable estimate that comes with it — with "
        "the cards this platform cannot yet investigate listed last, "
        "carrying its own refusal.",
    ),
}


@dataclass(frozen=True, slots=True)
class PriorityPolicy:
    """Bounded, pack-governed parameters of the priority formula.

    Actionability deliberately carries the dominant default weight: an
    un-fixable pile of dollars must not outrank fixable ones.

    ``compliance_floor`` is not the floor that is applied: it is the
    FALLBACK floor for a build where every detected anomaly is
    compliance-mandatory and there is no non-floored population to take a
    median of. It is governed, bounded and published in ``weights``; the
    floor actually applied is on the response as
    ``compliance_floor_value``/``_basis``.
    """

    impact_weight: Decimal = Decimal("0.25")
    recency_weight: Decimal = Decimal("0.15")
    actionability_weight: Decimal = Decimal("0.60")
    half_life_days: Decimal = Decimal(14)
    compliance_floor: Decimal = Decimal("0.60")

    def as_weights(self) -> dict[str, float]:
        return {
            "impact": float(self.impact_weight),
            "recency": float(self.recency_weight),
            "actionability": float(self.actionability_weight),
            "half_life_days": float(self.half_life_days),
            "compliance_floor": float(self.compliance_floor),
        }


def priority_policy_from_pack(snapshot: PackSnapshot) -> PriorityPolicy:
    """Read the governed parameters from the pack's detector policies."""
    values: dict[str, Decimal] = {}
    for policy in snapshot.detector_policies:
        values[policy.id] = policy.threshold
    defaults = PriorityPolicy()
    return PriorityPolicy(
        impact_weight=values.get("anomaly_priority_impact_weight", defaults.impact_weight),
        recency_weight=values.get("anomaly_priority_recency_weight", defaults.recency_weight),
        actionability_weight=values.get(
            "anomaly_priority_actionability_weight", defaults.actionability_weight
        ),
        half_life_days=values.get("anomaly_recency_half_life_days", defaults.half_life_days),
        compliance_floor=values.get("anomaly_compliance_floor", defaults.compliance_floor),
    )


def _age_days(record: AnomalyRecord, watermark: DataWatermark) -> int:
    onset = record.detected_at.date()
    raw = record.evidence.get("onset_date")
    if isinstance(raw, str):
        try:
            from datetime import date

            onset = date.fromisoformat(raw)
        except ValueError:
            pass
    return max(0, (watermark.loaded_at.date() - onset).days)


#: Decides whether a drill handle can be answered at a watermark. Returns
#: the platform's own refusal text, or ``None`` when the spec plans and
#: validates.
DrillabilityProbe = Callable[[TypedInvestigationSpec, DataWatermark], str | None]


#: Answers "which dimensions may this governed contract be cut by?" — the
#: pack's own ``scope_dimensions``, taken structurally so this module does
#: not import the pack adapter. Absent, no dimension repoint is applied:
#: substituting a cut without checking the contract accepts it would trade
#: one refusal for another.
ScopeDimensions = Callable[[str], frozenset[str]]


def _drill_spec(
    record: AnomalyRecord,
    metric_id: str | None = None,
    dimensions: Sequence[tuple[str, str]] | None = None,
) -> TypedInvestigationSpec:
    """The card's drill handle: a complete, executable typed investigation.

    The detector asserts a *level* about one cell — this metric, these
    dimension values, this observation window. The drill re-states exactly
    that in the platform's own vocabulary, so posting it re-derives the
    assertion from certified semantics and a versioned metric contract and
    the answer carries a real evidence grade (the card carries only
    provenance — see :class:`AnomalyCard`).

    The dimensions appear twice on purpose and mean two different things:
    as ``dimensions`` they are the breakdown the answer is cut by, and at
    their detected values as ``filters`` they are the scope that isolates
    the cell — so the header shows them as visible chips (§7.2) and a
    later ``remove_filter`` widens the drill back into its population
    without having to guess what the population was.

    No comparison is set. An anomaly card claims a level, not a movement;
    ``set_comparison`` is one ordinary refinement away, and — this being
    the point of the typed first turn — it now has a parent to land on.

    ``dimensions`` overrides the record's own cut, which is how a governed
    dimension repoint reaches the spec (see :func:`drill_spec_for`). The
    VALUES are carried across unchanged: the repointed dimension shares the
    source's value domain, and inventing a value would be a different claim
    rather than a wider one.
    """
    cut = record.dimensions if dimensions is None else tuple(dimensions)
    return TypedInvestigationSpec(
        metric_ids=[metric_id or record.metric_id],
        dimensions=[dimension for dimension, _ in cut],
        filters=[
            AddFilterModel(op="add_filter", dimension=dimension, predicate_op="eq", values=[value])
            for dimension, value in cut
        ],
        window=AbsoluteWindowModel(start=record.window_start, end=record.window_end),
    )


def is_active(record: AnomalyRecord) -> bool:
    """Will this record appear on the worklist at all?

    Exported so a caller doing per-card work *before* the build (notably
    re-derivation, which reads the warehouse) skips exactly the records
    the build will skip, rather than paying for anomalies nobody will see.
    """
    return record.status.lower() not in _RESOLVED_STATUSES


def dimension_repoints_for(
    record: AnomalyRecord,
    rules: ActionabilityRules,
    metric_id: str,
    scope_dimensions: ScopeDimensions | None,
) -> tuple[DimensionRepoint, ...]:
    """Governed cut substitutions this card's drill needs — and may make.

    Both conditions are required, and both are checked against the pack
    rather than assumed:

    * **needed** — the drilled contract does not accept the detector's own
      dimension (a denial-grain or line-grain contract that already accepts
      ``proc_group`` keeps it, and nothing is substituted);
    * **legal** — the contract does accept the replacement.

    Without ``scope_dimensions`` nothing is substituted at all: swapping a
    cut without checking the contract takes it would trade one
    ``GRAIN_INCOMPATIBLE`` for another, and the card would say the drill
    was repointed while still refusing to open.
    """
    if scope_dimensions is None:
        return ()
    allowed = scope_dimensions(metric_id)
    if not allowed:
        return ()
    out: list[DimensionRepoint] = []
    for dimension, _ in record.dimensions:
        if dimension in allowed:
            continue
        repoint = rules.dimension_repoint_for(dimension)
        if repoint is not None and repoint.to_dimension in allowed:
            out.append(repoint)
    return tuple(out)


def drill_spec_for(
    record: AnomalyRecord,
    rules: ActionabilityRules,
    scope_dimensions: ScopeDimensions | None = None,
) -> TypedInvestigationSpec:
    """The drill handle a card will publish, including any governed repoint.

    Exported so a caller can re-derive a card's figure BEFORE the portfolio
    is built (re-derivation reads the warehouse and is therefore async,
    while this builder stays synchronous and testable). Both paths call
    this one function, so the spec that gets re-derived is always the spec
    the card publishes — including its dimension repoints, or the
    re-derived figure would belong to a different population from the one
    the card offers to open.
    """
    repoint = rules.repoint_for(record.metric_id)
    metric_id = repoint.to_metric_id if repoint else record.metric_id
    swaps = {
        r.from_dimension: r.to_dimension
        for r in dimension_repoints_for(record, rules, metric_id, scope_dimensions)
    }
    cut = (
        tuple((swaps.get(dimension, dimension), value) for dimension, value in record.dimensions)
        if swaps
        else None
    )
    return _drill_spec(record, repoint.to_metric_id if repoint else None, cut)


#: What the platform can honestly say when it never attempted a
#: re-derivation for a card.
_NOT_ATTEMPTED = (
    "this card's figure was not re-derived by the platform in this build, so the "
    "detector's number stands alone"
)


#: Why a ``kind: snapshot`` contract and a detector's windowed figure are
#: not two measurements of the same thing. A snapshot is a balance as of
#: the watermark; it applies no start..end range at all, so
#: the gap between it and a card fired over a window is a difference of
#: *kind*, not a disagreement this platform can lay at the detector's door.
SNAPSHOT_NOT_COMPARABLE = (
    "the standard definition reads a balance standing as of this data load and applies "
    "no period at all, while the card's figure was computed over one — so the gap "
    "between them is a difference in what is being measured, not in the population, "
    "the period, or how the dollars were valued."
)


def _comparison_for(
    record: AnomalyRecord,
    rederived: ReDerivedImpact | None,
    *,
    drillable: bool,
    snapshot_drill: bool = False,
) -> ImpactComparison:
    """"Does the platform's own number agree with this card's?"

    Always answered. A card that says nothing about the relationship
    between its figure and the drill's is the exact defect this closes.
    The comparison itself lives in :func:`revi_api.rederive.compare_impact`,
    shared with the drill answer's strip so the two cannot word the same
    pair of numbers differently. Computed once per card, BEFORE the score,
    because under ``@3`` the verdict decides which figure ranks it.
    """
    return compare_impact(
        detector_cents=record.impact_cents,
        window_start=record.window_start,
        window_end=record.window_end,
        rederived=rederived,
        unattempted_note=(
            _NOT_ATTEMPTED
            if drillable
            else "this card cannot be opened with the definitions available today, so "
            "there is no standard figure to reconcile the detection system's against"
        ),
        not_comparable_reason=SNAPSHOT_NOT_COMPARABLE if snapshot_drill else None,
    )


def reconciliation_note(
    comparison: ImpactComparison,
    dimension_repoints: Sequence[DimensionRepoint] = (),
) -> str:
    """The sentence a card — or its drill — owes about a repointed cut.

    Exported because the drill strip must say what the card says: reading
    ``comparison.note`` straight off the raw :class:`ImpactComparison`
    shipped the shared "the detector's window, population or valuation
    basis is not the contract's" on a gap this platform had created itself.
    One function, both surfaces.
    """
    note = comparison.note
    if dimension_repoints and comparison.status in ("diverged", "unavailable"):
        swaps = "; ".join(
            f"{r.from_dimension} → {r.to_dimension}" for r in dimension_repoints
        )
        note = (
            f"{note} Part of this gap is this platform's own doing, not the "
            f"detector's: the drill was repointed onto a different cut ({swaps}) "
            "because the standard definition cannot be broken out the way the "
            "detection system broke it out, so the two figures are measured over "
            "related — not identical — populations."
        ).strip()
    return note


def dimension_repointed_warning(
    record: AnomalyRecord, dimension_repoints: Sequence[DimensionRepoint]
) -> str:
    """The turn-level disclosure a repointed drill owes its reader.

    The card carries its ``drill_dimension_repoints`` and its rationale;
    the drill answer carried neither. This is that fact as a coded warning,
    so it reaches the narrative's mandatory disclosures and the structured
    warning table rather than living only on a card the analyst has
    navigated away from.

    The rationale is the pack's own, verbatim — a substitution stated in
    the platform's words and not the pack author's would be a second,
    ungoverned explanation of a governed decision.
    """
    swaps = "; ".join(f"{r.from_dimension} → {r.to_dimension}" for r in dimension_repoints)
    rationales = "; ".join(
        dict.fromkeys(r.rationale for r in dimension_repoints if r.rationale)
    )
    tail = f" {rationales}" if rationales else ""
    return (
        f"dimension_repointed: this drill of {record.anomaly_id} does not read the cut the "
        f"detection system fired on. The standard definition cannot be broken out by "
        f"{', '.join(sorted({r.from_dimension.replace('_', ' ') for r in dimension_repoints}))}, so the "
        f"platform repointed it ({swaps}). The figures below are measured over a related "
        f"— not identical — population, and any gap against the card's figure is partly "
        f"this substitution rather than the detector's error.{tail}"
    )


def _reconciliation_fields(
    comparison: ImpactComparison,
    dimension_repoints: Sequence[DimensionRepoint] = (),
) -> dict[str, object]:
    """The card's reconciliation block, from the comparison already made.

    A repointed CUT is named in the note (see :func:`reconciliation_note`).
    """
    note = reconciliation_note(comparison, dimension_repoints)
    return {
        "reconciled_impact_cents": comparison.platform_cents,
        "reconciled_impact_metric_id": comparison.measure_id,
        "impact_agreement": comparison.status,
        "impact_delta_cents": comparison.delta_cents,
        "impact_delta_fraction": comparison.delta_fraction,
        "impact_reconciliation_note": note,
    }


@dataclass(frozen=True, slots=True)
class RankedFigure:
    """Which of a card's two figures the ranking is computed from, and why.

    See the module docstring ("Ranking the disputed figure"). ``basis`` is
    the value published as :attr:`AnomalyCard.ranked_on`.
    """

    cents: int
    basis: str  # detector | platform | not_comparable
    note: str


def ranked_figure(record: AnomalyRecord, comparison: ImpactComparison) -> RankedFigure:
    """The figure that will order this card — the reconciled one when the
    platform disputes the detector's, the detector's otherwise, and never
    silently either way."""
    if comparison.status == "diverged" and comparison.platform_cents is not None:
        return RankedFigure(
            cents=comparison.platform_cents,
            basis="platform",
            note=(
                "ranked on this platform's re-derived figure "
                f"(${comparison.platform_cents / 100:,.2f} from the standard definition of "
                f"{(comparison.measure_id or 'this measure').replace('_', ' ')}), not the detection system's "
                f"${record.impact_cents / 100:,.2f}: the two diverge, and ordering the "
                "worklist by a number this payload disputes would rank the work on the "
                "side of the disagreement the platform does not stand behind. The "
                "detector's figure is kept above as its own assertion."
            ),
        )
    if comparison.status == "not_comparable":
        return RankedFigure(
            cents=record.impact_cents,
            basis="not_comparable",
            note=(
                "ranked on the detection system's figure "
                f"(${record.impact_cents / 100:,.2f}): this platform's re-derivation is "
                "not a comparable quantity (an as-of balance against a windowed flow), so "
                "substituting it would change the claim rather than correct it."
            ),
        )
    if comparison.status == "agreed":
        return RankedFigure(
            cents=record.impact_cents,
            basis="detector",
            note=(
                "ranked on the detection system's figure "
                f"(${record.impact_cents / 100:,.2f}); this platform re-derived the same "
                "cell within half a percent, so the two figures rank it identically."
            ),
        )
    return RankedFigure(
        cents=record.impact_cents,
        basis="detector",
        note=(
            "ranked on the detection system's figure "
            f"(${record.impact_cents / 100:,.2f}): this platform has no re-derived figure "
            "for this card, so there is no reconciled number to rank it on instead."
        ),
    )


def build_portfolio(
    records: tuple[AnomalyRecord, ...],
    *,
    watermark: DataWatermark,
    policy: PriorityPolicy,
    rules: ActionabilityRules,
    tenant: str = "",
    warnings: tuple[str, ...] = (),
    drillability: DrillabilityProbe | None = None,
    rederived: Mapping[str, ReDerivedImpact] | None = None,
    metric_display: MetricDisplayRules | None = None,
    snapshot_metric_ids: frozenset[str] = frozenset(),
    scope_dimensions: ScopeDimensions | None = None,
) -> PortfolioResponse:
    """Rank the anomaly population by the governed priority formula, then
    float the cards the platform can actually investigate to the top.

    ``rederived`` maps anomaly id → this platform's own figure for that
    card's cell, computed by the caller (it reads the warehouse; this
    function does not). Absent, every card reports its impact as
    un-reconciled and says so — never silently as agreed.
    """
    active = [r for r in records if is_active(r)]
    all_warnings = list(warnings)
    if not active:
        if not records:
            all_warnings.append(
                "no detected anomalies in this data load (the detection feed may not "
                "have landed yet)"
            )
        return PortfolioResponse(
            status="empty",
            tenant=tenant,
            watermark_id=watermark.id,
            formula_version=PRIORITY_FORMULA_VERSION,
            weights=policy.as_weights(),
            items=[],
            warnings=all_warnings,
            warnings_v2=structured_warnings(all_warnings),
        )

    weight_sum = policy.impact_weight + policy.recency_weight + policy.actionability_weight

    # ---- pass 1: which figure ranks each card ------------------------------
    #
    # Reconciliation and drillability both run before any arithmetic, and
    # neither reads the warehouse here: the re-derivations were computed by
    # the caller and the drill probe is a planning + §6.6 pass. The verdict
    # decides the figure (``@3``), the figure decides the normalizer, and
    # the normalizer cannot be known until every card has one.
    @dataclass(frozen=True, slots=True)
    class _Ranked:
        record: AnomalyRecord
        comparison: ImpactComparison
        figure: RankedFigure
        drill_spec: TypedInvestigationSpec
        drill_reason: str | None
        dimension_repoints: tuple[DimensionRepoint, ...]
        recoverable_cents: int
        label: str
        rationale: str
        compliance: bool
        age: int

    ranked: list[_Ranked] = []
    for record in active:
        drill_spec = drill_spec_for(record, rules, scope_dimensions)
        drill_reason = drillability(drill_spec, watermark) if drillability is not None else None
        repointed_dimensions = dimension_repoints_for(
            record, rules, drill_spec.metric_ids[0], scope_dimensions
        )
        comparison = _comparison_for(
            record,
            (rederived or {}).get(record.anomaly_id),
            drillable=drill_reason is None,
            snapshot_drill=any(mid in snapshot_metric_ids for mid in drill_spec.metric_ids),
        )
        figure = ranked_figure(record, comparison)
        # The recoverable estimate is a governed fraction OF a figure, so it
        # follows the figure that ranked the card: pricing the work off a
        # number the same payload disputes is how a card came to read "this
        # platform: $151" beside "~$3,750 recoverable".
        assessment = assess(
            rules.rule_for(record.category), record, impact_cents=figure.cents
        )
        ranked.append(
            _Ranked(
                record=record,
                comparison=comparison,
                figure=figure,
                drill_spec=drill_spec,
                drill_reason=drill_reason,
                dimension_repoints=repointed_dimensions,
                recoverable_cents=assessment.recoverable_cents,
                label=assessment.label,
                rationale=assessment.rationale,
                compliance=assessment.compliance_floor,
                age=_age_days(record, watermark),
            )
        )

    # The normalizer is taken over the SAME figures the scores are computed
    # from — a max mixing detector and platform figures would normalize each
    # card against a denominator no card was measured on.
    max_impact = max(abs(entry.figure.cents) for entry in ranked) or 1

    # ---- pass 2: the formula, before any floor -----------------------------
    @dataclass(frozen=True, slots=True)
    class _Scored:
        ranked: _Ranked
        raw: Decimal
        impact_norm: Decimal
        recency: Decimal
        recoverable_norm: Decimal

    scored: list[_Scored] = []
    for entry in ranked:
        recency = Decimal(str(0.5 ** (entry.age / float(policy.half_life_days))))
        impact_norm = Decimal(abs(entry.figure.cents)) / Decimal(max_impact)
        recoverable_norm = Decimal(entry.recoverable_cents) / Decimal(max_impact)
        raw = (
            policy.impact_weight * impact_norm
            + policy.recency_weight * recency
            + policy.actionability_weight * recoverable_norm
        ) / weight_sum
        scored.append(
            _Scored(
                ranked=entry,
                raw=raw,
                impact_norm=impact_norm,
                recency=recency,
                recoverable_norm=recoverable_norm,
            )
        )

    # ---- the relative floor ------------------------------------------------
    value_scores = [s.raw for s in scored if not s.ranked.compliance]
    if value_scores:
        floor = Decimal(str(statistics.median(float(s) for s in value_scores)))
        floor_basis = "relative_median"
    else:
        # Nothing to take a median of — every detected anomaly is
        # compliance-mandatory. The governed absolute is the honest
        # fallback, and the response says that is what happened.
        floor = policy.compliance_floor
        floor_basis = "governed_absolute"

    # ---- pass 3: cards -----------------------------------------------------
    cards: list[AnomalyCard] = []
    for entry in scored:
        source = entry.ranked
        record = source.record
        repoint = rules.repoint_for(record.metric_id)
        drill_spec = source.drill_spec
        reason = source.drill_reason
        drillable = reason is None
        score = entry.raw
        floored = False
        if source.compliance and score < floor:
            score = floor
            floored = True
        cards.append(
            AnomalyCard(
                anomaly_id=record.anomaly_id,
                title=record.title,
                description=record.description,
                category=record.category,
                metric_id=record.metric_id,
                metric_display_name=(
                    metric_display.name_for(record.metric_id)
                    if metric_display is not None
                    else None
                ),
                severity=record.severity,
                confidence=record.confidence,
                status=record.status,
                detected_at=record.detected_at,
                window_start=record.window_start,
                window_end=record.window_end,
                dimensions=[
                    AnomalyDimension(dimension=d, value=v) for d, v in record.dimensions
                ],
                impact_cents=record.impact_cents,
                # Which figure ordered this card, what it was, and why —
                # published on every card, agreed or not (``@3``).
                ranked_on=source.figure.basis,  # type: ignore[arg-type]
                ranked_impact_cents=source.figure.cents,
                ranked_on_note=source.figure.note,
                age_days=source.age,
                recoverable_cents_estimate=source.recoverable_cents,
                actionability_label=source.label,
                actionability_rationale=source.rationale,
                priority_score=round(float(score), 6),
                compliance_floor_applied=floored,
                # The whole computation, publishable because it is trivially
                # available here and unverifiable anywhere else.
                priority=PriorityDecompositionPayload(
                    impact_norm=round(float(entry.impact_norm), 6),
                    recency=round(float(entry.recency), 6),
                    recoverable_norm=round(float(entry.recoverable_norm), 6),
                    impact_term=round(float(policy.impact_weight * entry.impact_norm), 6),
                    recency_term=round(float(policy.recency_weight * entry.recency), 6),
                    actionability_term=round(
                        float(policy.actionability_weight * entry.recoverable_norm), 6
                    ),
                    weight_sum=round(float(weight_sum), 6),
                    score_before_floor=round(float(entry.raw), 6),
                    floor_applied=floored,
                    floor_value=round(float(floor), 6),
                    floor_basis=floor_basis,
                    # The two facts that make ``impact_norm`` checkable with
                    # a calculator: which figure it came from, and what the
                    # population's denominator was.
                    ranked_on=source.figure.basis,  # type: ignore[arg-type]
                    ranked_impact_cents=source.figure.cents,
                    impact_normalizer_cents=max_impact,
                ),
                # Lane membership is the CATEGORY, not the mechanism: a
                # compliance-mandatory card that already scored above the
                # floor is still worked because the rule says so, and
                # belongs in the same section as its floored siblings.
                lane=COMPLIANCE_LANE if source.compliance else VALUE_LANE,
                drill_spec=drill_spec,
                drillable=drillable,
                drill_unavailable_reason=reason,
                drill_repointed_from=repoint.from_metric_id if repoint else None,
                drill_repoint_rationale=repoint.rationale if repoint else None,
                # The detector's CUT, where the drilled contract has no
                # legal cut at the detector's dimension. Published for the
                # same reason as the metric repoint: a repointed drill
                # measures a related population, not the same rows.
                drill_dimension_repoints=[
                    DrillDimensionRepointPayload(
                        from_dimension=r.from_dimension,
                        to_dimension=r.to_dimension,
                        rationale=r.rationale,
                    )
                    for r in source.dimension_repoints
                ],
                # honest provenance in place of an evidence grade: this row
                # is an external detector's assertion read at a watermark,
                # ordered by a versioned platform formula (see AnomalyCard)
                provenance="external_detection",
                priority_formula_version=PRIORITY_FORMULA_VERSION,
                source_watermark_id=watermark.id,
                **_reconciliation_fields(source.comparison, source.dimension_repoints),
            )
        )
    # Governed priority still decides the order; drillability decides which
    # half of the list you are in. A worklist whose first sixteen rows all
    # open an error dialog is not a worklist. The tiebreak reads the same
    # figure the score did (``@3``), so two cards on equal scores are not
    # separated by a number neither was ranked on.
    cards.sort(
        key=lambda c: (
            not c.drillable,
            -c.priority_score,
            -abs(c.ranked_impact_cents),
            c.anomaly_id,
        )
    )
    blocked = [c for c in cards if not c.drillable]
    if blocked:
        blocked_cents = sum(abs(c.impact_cents) for c in blocked)
        total_cents = sum(abs(c.impact_cents) for c in cards) or 1
        all_warnings.append(
            f"{len(blocked)} of {len(cards)} detected anomalies "
            f"({blocked_cents / total_cents:.0%} of ranked impact) are not investigable "
            "with the definitions available today. They are detected, ranked, and listed "
            "after the cards that can be opened, each carrying its own refusal."
        )
    all_warnings.extend(_reconciliation_warnings(cards))
    return PortfolioResponse(
        status="ok",
        tenant=tenant,
        watermark_id=watermark.id,
        formula_version=PRIORITY_FORMULA_VERSION,
        weights=policy.as_weights(),
        items=cards,
        lanes=_lanes(cards),
        compliance_floor_value=round(float(floor), 6),
        compliance_floor_basis=floor_basis,
        warnings=all_warnings,
        warnings_v2=structured_warnings(all_warnings),
    )


def _lanes(cards: list[AnomalyCard]) -> list[PortfolioLanePayload]:
    """The worklist's sections, in render order, over the ranked array."""
    lanes: list[PortfolioLanePayload] = []
    for lane_id in (COMPLIANCE_LANE, VALUE_LANE):
        members = [c for c in cards if c.lane == lane_id]
        if not members:
            continue
        label, description = _LANE_LABELS[lane_id]
        lanes.append(
            PortfolioLanePayload(
                id=lane_id,
                label=label,
                description=description,
                anomaly_ids=[c.anomaly_id for c in members],
                item_count=len(members),
                impact_cents=sum(abs(c.impact_cents) for c in members),
                # Totalled on the same figures that ranked the members, so a
                # lane header and the cards under it cannot be priced on two
                # different bases (``@3``).
                ranked_impact_cents=sum(abs(c.ranked_impact_cents) for c in members),
                recoverable_cents_estimate=sum(
                    c.recoverable_cents_estimate for c in members
                ),
            )
        )
    return lanes


def _reconciliation_warnings(cards: list[AnomalyCard]) -> list[str]:
    """Worklist-level statements about card/contract agreement.

    Two separate facts, never merged: how many cards the platform could
    not re-derive at all, and how many it re-derived to a materially
    different number. Both are worth a reader's attention and they call
    for different responses.
    """
    out: list[str] = []
    drillable = [c for c in cards if c.drillable]
    if not drillable:
        return out
    unavailable = [c for c in drillable if c.impact_agreement == "unavailable"]
    # Counted, and counted apart. A card whose contract is a ratio or a
    # snapshot never had a comparable dollar figure to diverge FROM, and
    # folding it into the divergence statistic is how "-99.6% divergence"
    # got published about a percentage and how a balance-vs-flow gap got
    # attributed to the detector.
    all_diverged = [c for c in drillable if c.impact_agreement == "diverged"]
    # …and a THIRD class, for the same reason. A card whose drill this
    # platform repointed onto a different cut measures a related population,
    # not the same one, so the gap is partly ours. Publishing it as detector
    # divergence reports a self-inflicted population change as somebody
    # else's error, and it dominated the headline: the largest gap in one
    # build was a card this platform itself zeroed via that swap. The two
    # classes are counted apart and the headline gap is computed over the
    # detector's alone.
    repointed = [c for c in all_diverged if c.drill_dimension_repoints]
    diverged = [c for c in all_diverged if not c.drill_dimension_repoints]
    not_comparable = [c for c in drillable if c.impact_agreement == "not_comparable"]
    if not_comparable:
        out.append(
            f"{len(not_comparable)} of {len(drillable)} ranked cards name a governed "
            "contract that is not comparable to the detector's dollar figure (a snapshot "
            "balance against a windowed flow); both figures are published on each card "
            "and neither is stated as a divergence from the other"
        )
    if unavailable:
        out.append(
            f"{len(unavailable)} of {len(drillable)} ranked cards could not be re-derived "
            "against this platform's own standard definitions, so their impact figures "
            "are the detection system's assertion alone."
        )
    if diverged:
        worst = max(abs(c.impact_delta_fraction or 0.0) for c in diverged)
        out.append(
            f"{len(diverged)} ranked cards diverge from this platform's re-derivation of "
            f"the same cell (largest gap {worst:.1%}). Each card publishes both figures, "
            "the difference between them, and the reason the detection system's period, "
            "population or valuation is not the standard one."
        )
    if repointed:
        worst_repointed = max(abs(c.impact_delta_fraction or 0.0) for c in repointed)
        swaps = sorted(
            {
                f"{r.from_dimension} → {r.to_dimension}"
                for c in repointed
                for r in c.drill_dimension_repoints
            }
        )
        out.append(
            f"dimension_repointed: {len(repointed)} further ranked cards differ from this "
            "platform's re-derivation because this platform, not the detection system, "
            f"repointed their drill onto a different cut ({'; '.join(swaps)}) — the standard "
            "definition cannot be broken out the way the detection system broke it out, so "
            "the two figures are measured over related, "
            f"not identical, populations (largest gap {worst_repointed:.1%}). They are "
            "counted here and not in the divergence figure above: the gap is this "
            "platform's doing, not the detection system's"
        )
    # Which basis ordered the worklist, counted. A reader who is about to
    # work the top of this list is entitled to know that some of its order
    # and some of its recoverable estimates come from the platform's figure
    # rather than the detector's, without opening every card.
    on_platform = [c for c in cards if c.ranked_on == "platform"]
    if on_platform:
        out.append(
            f"{len(on_platform)} of {len(cards)} cards are ranked on this platform's "
            "re-derived figure rather than the detection system's, because the two "
            "diverge (anomaly_priority@3); their recoverable estimates follow the same "
            "figure, and each card publishes both numbers, which one ranked it, and why"
        )
    not_comparable_ranked = [c for c in cards if c.ranked_on == "not_comparable"]
    if not_comparable_ranked:
        out.append(
            f"{len(not_comparable_ranked)} of {len(cards)} cards are ranked on the "
            "detection system's figure because this platform's re-derivation of them is "
            "not a comparable quantity (an as-of balance against a windowed flow); the "
            "re-derived figure is published on each card and is not treated as a "
            "correction to the one that ranked it"
        )
    return out
