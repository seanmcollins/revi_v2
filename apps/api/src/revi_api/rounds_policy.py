"""Governed Rounds content: materiality, resolution, time-to-impact.

Every number that decides whether a human is interrupted lives in
``packs/base-rcm/rounds.yaml`` with its authoring rationale beside it. This
module reads that file and applies it; it holds no threshold of its own.

That placement is the point. Alert fatigue is the death mode of a daily
surface, and the difference between a brief somebody opens every morning
and one they mute is a handful of constants. Constants in engine code are
somebody's guess, unversioned, unattributable and un-tunable per
deployment; constants in the pack are governed content with a content hash
that rides on every brief they gated.

Three policies, three shapes:

* **materiality** — thresholds per UNIT KIND, because "is this a big move?"
  has a different shape in each unit. A rate moves in percentage points; a
  dollar figure needs a relative gate *and* an absolute floor to be right
  at both ends of a portfolio spanning four orders of magnitude. See
  :func:`assess_movement`.
* **resolution** — how many consecutive loads confirm a claimed fix, and
  what "back to baseline" means in measurable terms. See
  :class:`ResolutionPolicy`.
* **time_to_impact** — per category, how (and whether) this platform can
  honestly date the cash effect. The refusal arm is load-bearing: a guessed
  "14 days" is indistinguishable on screen from the real filing deadline
  beside it. See :func:`time_to_impact_for`.

A missing file is not an error — a deployment whose pack ships no Rounds
content gets no gate and no time-to-impact, stated rather than defaulted
(:attr:`RoundsPolicy.enabled`). A MALFORMED one is: a gate the platform
silently failed to load is the failure this file exists to prevent, wearing
a different mask.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from revi_investigation.application.ports import (
    WATCH_MODES,
    WATCH_THRESHOLD_UNITS,
    AnomalyRecord,
    RoundsWatch,
)
from revi_investigation.application.rendering import (
    COUNT_UNIT,
    DAYS_UNIT,
    MONEY_UNIT,
    RATIO_UNIT,
    magnitude,
)
from revi_investigation_contracts.api import TimeToImpactPayload
from revi_investigation_contracts.rounds import RoundsMaterialityPayload

#: Where the governed Rounds content lives, relative to the pack directory.
ROUNDS_FILENAME = "rounds.yaml"


# ---------------------------------------------------------------------------
# the policy objects


@dataclass(frozen=True, slots=True)
class UnitThreshold:
    """The gate for one unit kind. Every field is optional because the
    shapes genuinely differ — a rate has no relative gate and days have no
    floor-plus-percent pair."""

    min_points: Decimal | None = None
    min_relative: Decimal | None = None
    min_absolute: Decimal | None = None

    def as_payload(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if self.min_points is not None:
            out["min_points"] = float(self.min_points)
        if self.min_relative is not None:
            out["min_relative"] = float(self.min_relative)
        if self.min_absolute is not None:
            out["min_absolute"] = float(self.min_absolute)
        return out


@dataclass(frozen=True, slots=True)
class FatiguePolicy:
    """When the brief tells somebody their own thresholds are too loose.

    ``message`` is governed wording with ``{count}`` and ``{ordinal}``
    substituted. Deliberately not composed per deployment and never by a
    model: an advisory that reads differently every morning reads as a bug.
    """

    consecutive_loads: int = 0
    message: str = ""

    @property
    def enabled(self) -> bool:
        return self.consecutive_loads > 0 and bool(self.message)


@dataclass(frozen=True, slots=True)
class MaterialityPolicy:
    unit_kinds: Mapping[str, UnitThreshold] = field(default_factory=dict)
    new_lead_min_impact_cents: int = 0
    always_material_lanes: frozenset[str] = frozenset()
    self_resolved_min_impact_cents: int = 0
    max_entries: int = 12
    max_entries_per_kind: int = 5
    #: Which kinds the cap drops LAST, worst-to-lose first. Governed rather
    #: than implicit in assembly order: the cap used to truncate the tail of
    #: whatever order the engine happened to build entries in, which put the
    #: platform's verdicts on the team's own work first in the queue to be
    #: deleted (round-7 FN-11).
    priority_order: tuple[str, ...] = ()
    #: Kinds the overall cap may never drop.
    never_capped: frozenset[str] = frozenset()
    fatigue: FatiguePolicy = field(default_factory=FatiguePolicy)

    def rank_of(self, kind: str) -> int:
        """Where ``kind`` sits in the drop order; unlisted kinds sort last.

        Unlisted rather than refused: a pack that has not been updated for a
        new entry kind should keep briefing, with the new kind at the back
        of the queue, rather than crashing the surface it governs.
        """
        try:
            return self.priority_order.index(kind)
        except ValueError:
            return len(self.priority_order)


@dataclass(frozen=True, slots=True)
class ResolutionPolicy:
    """When a claimed fix becomes a confirmed one.

    ``consecutive_loads_required`` is why this is a verification and not a
    checkbox: one load is a coincidence, and confirming on it would publish
    "confirmed" for a card that returns tomorrow.
    """

    consecutive_loads_required: int = 2
    measured_reduction_fraction: Decimal = Decimal("0.80")
    regression_increase_fraction: Decimal = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class TimeToImpactRule:
    """How one anomaly category's cash timing is derived — or why it cannot be."""

    method: str  # filing_deadline | aging_projection | appeal_window | unknown
    lane: str  # already_hit | pre_cash | unknown
    note: str = ""
    reason: str = ""
    deadline_fact: str = ""
    deadline_aggregate: str = "min"
    age_fact: str = ""
    age_aggregate: str = "median"
    recovery_fact: str = ""
    recovery_aggregate: str = "min"
    recovery_label: str = ""


@dataclass(frozen=True, slots=True)
class TimeToImpactPolicy:
    bill_days: int = 0
    payment_lag_days: int = 0
    categories: Mapping[str, TimeToImpactRule] = field(default_factory=dict)

    def rule_for(self, category: str) -> TimeToImpactRule | None:
        """The governed rule for one detector category, or ``None``.

        ``None`` is not "no timing" — it is "this pack has never been asked
        about this category", which the caller publishes as an ``unknown``
        verdict naming the gap rather than as silence.
        """
        return self.categories.get(category.lower())


@dataclass(frozen=True, slots=True)
class RoundsPolicy:
    """The whole governed Rounds content, plus where it came from."""

    materiality: MaterialityPolicy = field(default_factory=MaterialityPolicy)
    resolution: ResolutionPolicy = field(default_factory=ResolutionPolicy)
    time_to_impact: TimeToImpactPolicy = field(default_factory=TimeToImpactPolicy)
    content_hash: str = ""
    source: str = ""

    @property
    def enabled(self) -> bool:
        """False when the pack ships no Rounds content.

        A deployment without it still pins and still evaluates; it simply
        has no governed gate, and the brief says so rather than applying a
        threshold nobody agreed to.
        """
        return bool(self.content_hash)

    def payload(self) -> RoundsMaterialityPayload:
        """The gate that was applied, for publication on the brief."""
        return RoundsMaterialityPayload(
            unit_kinds={
                unit: threshold.as_payload()
                for unit, threshold in self.materiality.unit_kinds.items()
            },
            new_lead_min_impact_cents=self.materiality.new_lead_min_impact_cents,
            always_material_lanes=sorted(self.materiality.always_material_lanes),
            max_entries=self.materiality.max_entries,
            priority_order=list(self.materiality.priority_order),
            never_capped=sorted(self.materiality.never_capped),
            content_hash=self.content_hash,
            source=self.source,
        )


# ---------------------------------------------------------------------------
# loading


def _decimal(value: Any, where: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"{where}: {value!r} is not a number") from exc


def load_rounds_policy(path: str | Path) -> RoundsPolicy:
    """Read the governed Rounds content, or an empty policy when absent."""
    file = Path(path)
    if not file.is_file():
        return RoundsPolicy()
    raw = file.read_text(encoding="utf-8")
    document: Any = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a mapping document")

    materiality_doc = document.get("materiality") or {}
    if not isinstance(materiality_doc, dict):
        raise ValueError(f"{path}: 'materiality' must be a mapping")
    unit_kinds: dict[str, UnitThreshold] = {}
    for unit, entry in (materiality_doc.get("unit_kinds") or {}).items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: materiality.unit_kinds.{unit} must be a mapping")
        # ``min_points`` is stated in percentage POINTS and compared against
        # a ratio difference, so it is divided here — once, at the boundary
        # — rather than at the two comparison sites.
        points = entry.get("min_points")
        absolute = entry.get("min_absolute_cents")
        if absolute is None:
            absolute = entry.get("min_absolute_days")
        if absolute is None:
            absolute = entry.get("min_absolute")
        unit_kinds[str(unit)] = UnitThreshold(
            min_points=(
                _decimal(points, f"{path}: min_points") / 100 if points is not None else None
            ),
            min_relative=(
                _decimal(entry["min_relative"], f"{path}: min_relative")
                if entry.get("min_relative") is not None
                else None
            ),
            min_absolute=(
                _decimal(absolute, f"{path}: min_absolute") if absolute is not None else None
            ),
        )
    new_lead = materiality_doc.get("new_lead") or {}
    self_resolved = materiality_doc.get("self_resolved") or {}
    brief = materiality_doc.get("brief") or {}
    fatigue_doc = materiality_doc.get("fatigue") or {}
    materiality = MaterialityPolicy(
        unit_kinds=unit_kinds,
        new_lead_min_impact_cents=int(new_lead.get("min_impact_cents", 0)),
        always_material_lanes=frozenset(
            str(lane) for lane in (new_lead.get("always_material_lanes") or [])
        ),
        self_resolved_min_impact_cents=int(self_resolved.get("min_impact_cents", 0)),
        max_entries=int(brief.get("max_entries", 12)),
        max_entries_per_kind=int(brief.get("max_entries_per_kind", 5)),
        priority_order=tuple(str(kind) for kind in (brief.get("priority_order") or [])),
        never_capped=frozenset(str(kind) for kind in (brief.get("never_capped") or [])),
        fatigue=FatiguePolicy(
            consecutive_loads=int(fatigue_doc.get("consecutive_loads", 0)),
            message=" ".join(str(fatigue_doc.get("message", "")).split()),
        ),
    )

    resolution_doc = document.get("resolution") or {}
    required = int(resolution_doc.get("consecutive_loads_required", 2))
    if required < 1:
        raise ValueError(f"{path}: resolution.consecutive_loads_required must be >= 1")
    resolution = ResolutionPolicy(
        consecutive_loads_required=required,
        measured_reduction_fraction=_decimal(
            resolution_doc.get("measured_reduction_fraction", "0.80"), f"{path}: resolution"
        ),
        regression_increase_fraction=_decimal(
            resolution_doc.get("regression_increase_fraction", "0.10"), f"{path}: resolution"
        ),
    )

    tti_doc = document.get("time_to_impact") or {}
    projection = tti_doc.get("projection") or {}
    categories: dict[str, TimeToImpactRule] = {}
    for category, entry in (tti_doc.get("categories") or {}).items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: time_to_impact.categories.{category} must be a mapping")
        method = str(entry.get("method", "unknown"))
        if method == "unknown" and not str(entry.get("reason", "")).strip():
            # The refusal arm is the one that must never be silent.
            raise ValueError(
                f"{path}: time_to_impact.categories.{category} declares method 'unknown' "
                "without a reason — a category the platform cannot date must say why"
            )
        categories[str(category).lower()] = TimeToImpactRule(
            method=method,
            lane=str(entry.get("lane", "unknown")),
            note=" ".join(str(entry.get("note", "")).split()),
            reason=" ".join(str(entry.get("reason", "")).split()),
            deadline_fact=str(entry.get("deadline_fact", "")),
            deadline_aggregate=str(entry.get("deadline_aggregate", "min")),
            age_fact=str(entry.get("age_fact", "")),
            age_aggregate=str(entry.get("age_aggregate", "median")),
            recovery_fact=str(entry.get("recovery_fact", "")),
            recovery_aggregate=str(entry.get("recovery_aggregate", "min")),
            recovery_label=str(entry.get("recovery_label", "")),
        )
    time_to_impact = TimeToImpactPolicy(
        bill_days=int(projection.get("bill_days", 0)),
        payment_lag_days=int(projection.get("payment_lag_days", 0)),
        categories=categories,
    )

    return RoundsPolicy(
        materiality=materiality,
        resolution=resolution,
        time_to_impact=time_to_impact,
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        source=str(file),
    )


# ---------------------------------------------------------------------------
# materiality


@dataclass(frozen=True, slots=True)
class MaterialityVerdict:
    """Did this movement earn a line in the brief, and by whose rule?"""

    material: bool
    rule: str
    note: str
    #: ``governed`` (the pack's threshold) or ``watch`` (the analyst's own).
    threshold_source: str = "governed"
    #: True when the analyst's own threshold briefed a movement the
    #: GOVERNED gate calls normal variation. Counted across loads to decide
    #: the fatigue advisory.
    below_governed_gate: bool = False


#: Returned when the gate cannot be evaluated at all. Deliberately NOT
#: material: when in doubt, gate harder — a brief that fires on movements it
#: could not measure is the fatigue mode with extra steps.
_UNGATED = "no_governed_threshold"


def assess_movement(
    *,
    unit: str | None,
    prior: Decimal | None,
    current: Decimal | None,
    policy: MaterialityPolicy,
    watch: RoundsWatch | None = None,
) -> MaterialityVerdict:
    """Is this load-over-load movement worth interrupting somebody for?

    Two gates, and the answer says which one decided:

    * the PACK's, per unit kind, which is what every watch gets by default;
    * the ANALYST's, when the watch declares its own mode. It may be
      looser than the pack's — somebody watching one cell knows things a
      blanket threshold does not — and when it briefs a movement the
      governed gate calls normal, the verdict records that
      (:attr:`MaterialityVerdict.below_governed_gate`) so the brief can
      notice the pattern and say so once.

    Every non-material outcome carries the sentence that explains it, so
    the counted-and-withheld line on the brief can say WHAT was withheld
    and why rather than reporting a bare number.
    """
    if prior is None or current is None:
        return MaterialityVerdict(
            False,
            "not_comparable",
            "there is no prior measurement of this tile to compare against, so no movement "
            "is claimed",
        )
    delta = current - prior
    magnitude_text = magnitude(delta, unit)

    # Direction applies to every mode, including the governed default: a
    # watch that says "tell me when it gets worse" has said something about
    # every threshold beneath it.
    if watch is not None and watch.direction != "any" and delta != 0:
        wanted_up = watch.direction == "up"
        if (delta > 0) != wanted_up:
            return MaterialityVerdict(
                False,
                "watch_direction",
                f"this watch is set to movements {watch.direction} only, and it moved "
                f"{'up' if delta > 0 else 'down'} by {magnitude_text}",
                threshold_source="watch",
            )

    governed = _governed_verdict(unit, delta, prior, policy, magnitude_text)
    if watch is None or watch.mode == "governed_default":
        return governed

    own = _watch_verdict(watch, unit, delta, prior, current, magnitude_text)
    return MaterialityVerdict(
        material=own.material,
        rule=own.rule,
        note=own.note,
        threshold_source="watch",
        below_governed_gate=own.material and not governed.material,
    )


def _governed_verdict(
    unit: str | None,
    delta: Decimal,
    prior: Decimal,
    policy: MaterialityPolicy,
    magnitude_text: str,
) -> MaterialityVerdict:
    threshold = policy.unit_kinds.get(unit or "")
    if threshold is None:
        return MaterialityVerdict(
            False,
            _UNGATED,
            f"the governed materiality content declares no threshold for unit "
            f"{unit or 'unknown'!r}, so this movement is counted but not briefed — an "
            "ungated alert is the failure mode this gate exists to prevent",
        )
    passed, rule, note = _pack_gate(unit, delta, prior, threshold, magnitude_text)
    return MaterialityVerdict(passed, rule, note)


def _watch_verdict(
    watch: RoundsWatch,
    unit: str | None,
    delta: Decimal,
    prior: Decimal,
    current: Decimal,
    magnitude_text: str,
) -> MaterialityVerdict:
    """The analyst's own threshold, applied. Never raises on a bad watch:
    validation happens at CREATION (:func:`validate_watch`), so a stored
    watch whose unit no longer fits its metric — a pack that re-declared a
    contract's unit — degrades to "cannot evaluate" and says so, rather
    than briefing on a comparison that means nothing."""
    if watch.mode == "any_movement":
        material = delta != 0
        return MaterialityVerdict(
            material,
            "watch_any_movement",
            f"this watch is set to brief on any movement at all, and it moved "
            f"{magnitude_text}"
            if material
            else "this watch is set to brief on any movement at all, and it did not move",
        )
    if watch.value is None:
        return MaterialityVerdict(
            False,
            "watch_incomplete",
            f"this watch declares mode {watch.mode!r} with no threshold value, so it cannot "
            "be evaluated and nothing is briefed",
        )
    if watch.mode == "crosses":
        level = _threshold_in_metric_unit(watch, unit, reference=None)
        if level is None:
            return MaterialityVerdict(False, "watch_unit_mismatch", _unit_mismatch_note(watch, unit))
        crossed = (prior < level <= current) or (current <= level < prior)
        return MaterialityVerdict(
            crossed,
            "watch_crosses",
            (
                f"the value crossed the watched level of {format_threshold(watch, unit)}: "
                f"{magnitude(prior, unit)} to {magnitude(current, unit)}"
            )
            if crossed
            else (
                f"the value did not cross the watched level of "
                f"{format_threshold(watch, unit)} at this load"
            ),
        )
    # delta_gte
    gate = _threshold_in_metric_unit(watch, unit, reference=prior)
    if gate is None:
        return MaterialityVerdict(False, "watch_unit_mismatch", _unit_mismatch_note(watch, unit))
    material = abs(delta) >= gate
    return MaterialityVerdict(
        material,
        "watch_delta_gte",
        f"this watch briefs at {format_threshold(watch, unit)} and it moved "
        f"{magnitude_text}"
        + (f" ({watch.note})" if watch.note else ""),
    )


def _threshold_in_metric_unit(
    watch: RoundsWatch, unit: str | None, *, reference: Decimal | None
) -> Decimal | None:
    """The watch's threshold expressed in the metric's own unit.

    ``None`` means the pairing is dishonest (points on money, cents on a
    rate) or unevaluable — never a coerced fallback, because a coerced
    threshold is a watch that fires for a reason nobody can see.
    """
    if watch.value is None:
        return None
    if watch.unit == "points":
        return watch.value / 100 if unit == RATIO_UNIT else None
    if watch.unit == "cents":
        return watch.value if unit == MONEY_UNIT else None
    if watch.unit == "days":
        return watch.value if unit == DAYS_UNIT else None
    if watch.unit == "relative_pct":
        if reference is None or not reference:
            return None
        return abs(reference) * watch.value / 100
    # No unit stated: read the threshold in the metric's own unit, which is
    # the only reading that cannot be wrong.
    return watch.value


def _unit_mismatch_note(watch: RoundsWatch, unit: str | None) -> str:
    return (
        f"this watch states its threshold in {watch.unit!r}, which is not an honest unit for "
        f"a {unit or 'unknown'!r} contract, so it cannot be evaluated and nothing is briefed "
        "(the pairing was legal when the watch was created — the metric's declared unit has "
        "changed since)"
    )


def format_threshold(watch: RoundsWatch, unit: str | None) -> str:
    """The watch's threshold, said the way its unit should be said.

    Including its number's own grammar: exactly one point is "1 point". The
    platform read "1 points" on the first screen, which is the kind of seam
    a reader files under "software wrote this" — on a surface whose whole
    claim is that a person could have written every sentence on it.
    """
    if watch.value is None:
        return "no threshold"
    if watch.unit == "points":
        # ``.00`` is dropped on whole numbers and kept on fractional ones
        # ("0.50 points" is how a threshold that precise should read), and
        # exactly one point is singular.
        rendered = f"{float(watch.value):.2f}".replace(".00", "")
        return f"{rendered} point" + ("" if float(watch.value) == 1 else "s")
    if watch.unit == "cents":
        return magnitude(int(watch.value), MONEY_UNIT)
    if watch.unit == "days":
        return magnitude(watch.value, DAYS_UNIT)
    if watch.unit == "relative_pct":
        return f"{float(watch.value):.1f}% of the reference value"
    return magnitude(watch.value, unit)


def validate_watch(watch: RoundsWatch, *, units: Sequence[str | None]) -> str | None:
    """Why this watch cannot be created, or ``None`` when it can.

    Unit honesty is checked HERE, at creation, against the declared units
    of the pinned spec's own metric contracts — not at fire time. A watch
    accepted with a dishonest unit is a watch that silently never fires (or
    always does), and the analyst finds out weeks later by not being told
    about something.

    A spec measuring several metrics with different units cannot carry a
    unit-specific threshold at all: "half a point" over a money contract
    and a rate contract together has no single meaning, and picking one is
    a guess. ``relative_pct`` stays legal there because a fraction of the
    reference value means the same thing in any unit.
    """
    if watch.mode not in WATCH_MODES:
        return (
            f"unknown watch mode {watch.mode!r} — the closed set is "
            f"{', '.join(WATCH_MODES)}"
        )
    if watch.mode in ("delta_gte", "crosses"):
        if watch.value is None:
            return f"a {watch.mode!r} watch needs a threshold value; none was given"
        if watch.unit is None:
            return (
                f"a {watch.mode!r} watch needs the unit its threshold is stated in "
                f"({', '.join(WATCH_THRESHOLD_UNITS)}) — an unlabelled number cannot be "
                "compared honestly against a metric whose unit it may not share"
            )
        if watch.unit not in WATCH_THRESHOLD_UNITS:
            return (
                f"unknown threshold unit {watch.unit!r} — the closed set is "
                f"{', '.join(WATCH_THRESHOLD_UNITS)}"
            )
    elif watch.value is not None:
        return (
            f"a {watch.mode!r} watch takes no threshold value, and one was given "
            f"({watch.value}) — it would be a number with nothing to compare against"
        )
    if watch.direction not in ("any", "up", "down"):
        return f"unknown watch direction {watch.direction!r} — expected any, up or down"
    if watch.unit is None or watch.unit == "relative_pct":
        # A fraction of the reference value means the same thing in every
        # unit, so it needs no agreement with the contract.
        return None
    distinct = {unit for unit in units if unit is not None}
    if not distinct:
        return (
            f"this watch states its threshold in {watch.unit!r}, and the pack declares no unit "
            "for the metrics this spec measures, so the two cannot be checked against each "
            "other"
        )
    if len(distinct) > 1:
        return (
            f"this spec measures metrics in more than one unit ({', '.join(sorted(distinct))}), "
            f"so a threshold stated in {watch.unit!r} has no single meaning over it — state it "
            "as relative_pct, or watch one metric at a time"
        )
    unit = next(iter(distinct))
    expected = {"points": RATIO_UNIT, "cents": MONEY_UNIT, "days": DAYS_UNIT}[watch.unit]
    if unit != expected:
        advice = {
            "points": (
                "Percentage points describe a rate's movement; for money state the threshold "
                "in cents, for a lag in days, or as relative_pct."
            ),
            "cents": (
                "Cents describe money; for a rate state the threshold in points, for a lag in "
                "days, or as relative_pct."
            ),
            "days": (
                "Days describe a lag metric; for a rate state the threshold in points, for "
                "money in cents, or as relative_pct."
            ),
        }[watch.unit]
        return (
            f"a threshold in {watch.unit!r} is only honest for a {expected!r} contract, and "
            f"this watch measures {unit!r}. " + advice
        )
    return None


def _pack_gate(
    unit: str | None,
    delta: Decimal,
    prior: Decimal,
    threshold: UnitThreshold,
    magnitude_text: str,
) -> tuple[bool, str, str]:
    size = abs(delta)
    if unit == RATIO_UNIT and threshold.min_points is not None:
        # A rate's movement is percentage POINTS and nothing else. A
        # relative gate on a rate is meaningless at both ends of the scale.
        material = size >= threshold.min_points
        gate = f"{float(threshold.min_points) * 100:.1f} points"
        return (
            material,
            "ratio_points",
            f"the rate moved {magnitude_text}, "
            + (f"at or above the governed gate of {gate}" if material else f"below {gate}"),
        )
    if unit in (MONEY_UNIT, COUNT_UNIT):
        # Conjoined on purpose: relative alone briefs a $40 balance that
        # doubled, absolute alone briefs a rounding error on $12M.
        floor = threshold.min_absolute
        relative = threshold.min_relative
        clears_floor = floor is None or size >= floor
        share = (size / abs(prior)) if prior else None
        clears_relative = relative is None or share is None or share >= relative
        material = clears_floor and clears_relative
        gate_bits: list[str] = []
        if relative is not None:
            gate_bits.append(f"{float(relative):.0%} of the prior value")
        if floor is not None:
            gate_bits.append(magnitude(floor, unit))
        gate = " and ".join(gate_bits) or "no gate"
        observed = magnitude_text + (f" ({float(share):.1%})" if share is not None else "")
        rule = "money_relative_and_floor" if unit == MONEY_UNIT else "count_relative_and_floor"
        return (
            material,
            rule,
            f"the value moved {observed}, "
            + (f"clearing the governed gate of {gate}" if material else f"short of {gate}"),
        )
    if unit == DAYS_UNIT and threshold.min_absolute is not None:
        material = size >= threshold.min_absolute
        gate = magnitude(threshold.min_absolute, unit)
        return (
            material,
            "days_absolute",
            f"the measure moved {magnitude_text}, "
            + (f"at or above the governed gate of {gate}" if material else f"below {gate}"),
        )
    return (
        False,
        _UNGATED,
        f"the governed thresholds for unit {unit or 'unknown'!r} do not describe a rule this "
        "movement can be judged by, so it is counted rather than briefed",
    )


def assess_new_lead(
    *, impact_cents: int, lane: str, policy: MaterialityPolicy
) -> MaterialityVerdict:
    """Does a newly detected lead earn a line in the brief?

    A card has no prior value to have moved from, so the gate is its size —
    with one governed exception: the lanes named ``always_material`` are
    briefed regardless, because compliance-mandatory work carries the same
    obligation at $824 as at $84,000, and lowering the floor for everybody
    to catch those would brief every duplicate-claim card in the building.
    """
    if lane in policy.always_material_lanes:
        return MaterialityVerdict(
            True,
            "always_material_lane",
            f"the {lane} lane is briefed regardless of size: this work is done because the "
            "rule says so, not because it is the largest thing on the list",
        )
    floor = policy.new_lead_min_impact_cents
    material = abs(impact_cents) >= floor
    return MaterialityVerdict(
        material,
        "new_lead_floor",
        f"the lead's ranked impact is {magnitude(impact_cents, MONEY_UNIT)}, "
        + (
            f"at or above the governed brief floor of {magnitude(floor, MONEY_UNIT)}"
            if material
            else f"below the governed brief floor of {magnitude(floor, MONEY_UNIT)}"
        ),
    )


def assess_self_resolved(*, impact_cents: int, policy: MaterialityPolicy) -> MaterialityVerdict:
    """Does a lead that left the feed unclaimed earn a line?"""
    floor = policy.self_resolved_min_impact_cents
    material = abs(impact_cents) >= floor
    return MaterialityVerdict(
        material,
        "self_resolved_floor",
        f"the lead carried {magnitude(impact_cents, MONEY_UNIT)} when it was last detected, "
        + (
            "at or above the governed brief floor"
            if material
            else f"below the governed brief floor of {magnitude(floor, MONEY_UNIT)}"
        ),
    )


# ---------------------------------------------------------------------------
# time to impact


def _fact(evidence: Mapping[str, Any], name: str, aggregate: str) -> Decimal | None:
    """One evidence fact, aggregated as the governed rule asks.

    The detector publishes some facts as scalars and some as
    ``{min, median, max}`` bundles; both shapes are read here so a rule can
    name a fact without also naming its shape.
    """
    if not name:
        return None
    raw = evidence.get(name)
    if isinstance(raw, Mapping):
        raw = raw.get(aggregate)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _cutoff(evidence: Mapping[str, Any], fallback: date) -> date:
    """The date the detector's day-counts are measured FROM.

    Every record in this feed publishes ``cutoff``; the watermark's newest
    data date is the fallback, because a day-count with no origin is not a
    date and quietly using today's would re-date the card on every read.
    """
    raw = evidence.get("cutoff")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return fallback
    if isinstance(raw, date):
        return raw
    return fallback


def time_to_impact_for(
    record: AnomalyRecord, *, newest_data_date: date, policy: TimeToImpactPolicy
) -> TimeToImpactPayload | None:
    """When this card's dollars hit cash, per the governed rule for its category.

    ``None`` only when the deployment ships no governed content at all.
    Otherwise every card gets a verdict — including ``unknown`` WITH its
    reason, which is the arm that keeps the real dates beside it readable.
    """
    if not policy.categories:
        return None
    rule = policy.rule_for(record.category)
    if rule is None:
        return TimeToImpactPayload(
            kind="unknown",
            lane="unknown",
            method="no governed time-to-impact rule exists for this category in the "
            "deployment's pack, so this platform does not date its cash effect",
            method_id="ungoverned",
            reason=f"category {record.category!r} has no rule in the governed Rounds content",
        )
    cutoff = _cutoff(record.evidence, newest_data_date)
    recovery_days, recovery_date = _recovery(record, rule, cutoff)

    if rule.method == "filing_deadline":
        days = _fact(record.evidence, rule.deadline_fact, rule.deadline_aggregate)
        if days is None:
            return TimeToImpactPayload(
                kind="unknown",
                lane=rule.lane,  # type: ignore[arg-type]
                method=f"the governed rule reads the detector's {rule.deadline_fact!r} fact, "
                "which this record does not publish",
                method_id=rule.method,
                reason=f"the detection feed published no {rule.deadline_fact!r} for this card",
            )
        whole = int(days)
        return TimeToImpactPayload(
            kind="deadline",
            lane=rule.lane,  # type: ignore[arg-type]
            days=whole,
            deadline_date=cutoff + timedelta(days=whole),
            method=f"{rule.note} Soonest deadline in this cell: "
            f"{rule.deadline_fact}.{rule.deadline_aggregate} = {whole} days from the "
            f"detector's cutoff of {cutoff.isoformat()}.",
            method_id=rule.method,
            provisional=False,
            recovery_days=recovery_days,
            recovery_deadline_date=recovery_date,
            recovery_label=rule.recovery_label,
        )

    if rule.method == "aging_projection":
        age = _fact(record.evidence, rule.age_fact, rule.age_aggregate)
        projected = policy.bill_days + policy.payment_lag_days
        aged = (
            f", against a {rule.age_fact}.{rule.age_aggregate} of {int(age)} days"
            if age is not None
            else ""
        )
        return TimeToImpactPayload(
            kind="projected",
            lane=rule.lane,  # type: ignore[arg-type]
            days=projected,
            deadline_date=None,  # an estimate is not a date, and is never published as one
            method=f"{rule.note} PROJECTION, not a deadline: {policy.bill_days} days to bill "
            f"plus {policy.payment_lag_days} days of governed payment lag = {projected} "
            f"days{aged}. Both figures are planning defaults from the pack, not a payer "
            "contract, which is why this is marked provisional.",
            method_id=rule.method,
            provisional=True,
            recovery_days=recovery_days,
            recovery_deadline_date=recovery_date,
            recovery_label=rule.recovery_label,
        )

    if rule.method == "appeal_window":
        return TimeToImpactPayload(
            kind="already_hit",
            lane=rule.lane,  # type: ignore[arg-type]
            days=None,
            method=rule.note
            or "these claims were adjudicated and did not pay, so the cash effect is in the past",
            method_id=rule.method,
            provisional=False,
            recovery_days=recovery_days,
            recovery_deadline_date=recovery_date,
            recovery_label=rule.recovery_label,
        )

    return TimeToImpactPayload(
        kind="already_hit" if rule.lane == "already_hit" else "unknown",
        lane=rule.lane,  # type: ignore[arg-type]
        method=rule.note or rule.reason,
        method_id=rule.method,
        reason=rule.reason,
        recovery_days=recovery_days,
        recovery_deadline_date=recovery_date,
        recovery_label=rule.recovery_label,
    )


def _recovery(
    record: AnomalyRecord, rule: TimeToImpactRule, cutoff: date
) -> tuple[int | None, date | None]:
    """A dated recovery window, when the detector published one.

    Negative days are published rather than suppressed: "the appeal window
    closed 49 days ago" is the fact that decides whether the money is
    reachable at all, and a null there would read as "no deadline".
    """
    days = _fact(record.evidence, rule.recovery_fact, rule.recovery_aggregate)
    if days is None:
        return None, None
    whole = int(days)
    return whole, cutoff + timedelta(days=whole)
