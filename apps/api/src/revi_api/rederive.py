"""Re-derive an anomaly card's headline figure from certified semantics.

A card's headline figure and the drill it hands the analyst can disagree
by several percent on two consecutive screens, with nothing saying they
disagreed or why. The turn's own reconciliation verdict does not cover it:
that verdict is about *investigation lineage*, and is silent about the two
numbers the reader actually compared.

The two figures are not a bug in either one. They are two different
claims:

* the **card** figure is an external detection system's assertion,
  computed by that system, in its window, on its population and its
  basis, at the time it fired;
* the **drill** figure is *this platform's* governed metric contract,
  re-derived at the pinned watermark over the population the card names.

A platform whose whole thesis is "explainable, reconciled, drillable"
cannot let those two sit next to each other unreconciled. So the number
is computed **at portfolio build time**, published on the card beside the
detector's own, and the divergence is stated in the payload rather than
left for a reader to notice.

Mechanism: the same pipeline the drillability probe already runs
(interpret the typed spec → plan → §6.6 validate), continued two stages
further (execute → calculate) and summed over the money column of the
resulting frame. No LLM call, no findings stage, no session, no stored
investigation: this is the contract's arithmetic, not an answer.

Cost is bounded three ways. The result is memoized per
``(watermark, plan hash)`` for the process's lifetime; every probe goes
through the ordinary evidence cache (§7.9), so a re-derivation and the
analyst's later drill of the same card read the warehouse once between
them; and a card whose spec will not plan is skipped before any query.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import Session
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_kernel.errors import ReviError
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import MetricRef
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

logger = logging.getLogger("revi.api.rederive")

#: Frame columns carrying money. The connector stamps the metric contract's
#: unit onto the column, so this reads the declared unit rather than
#: guessing from a name.
_MONEY_UNIT = "money_cents"


@dataclass(frozen=True, slots=True)
class ReDerivedImpact:
    """This platform's own figure for the cell an anomaly card names.

    ``cents`` is ``None`` when the platform could not re-derive it — and
    ``unavailable_reason`` then says which of the two honest reasons it
    was (the spec does not plan at this catalog version, or it planned and
    produced no money column to sum). Never zero standing in for unknown:
    a card reading ``$0.00 re-derived`` is a claim, and it would be false.
    """

    cents: int | None = None
    #: The governed contract the figure came from — which is not always the
    #: metric the detector named (see ``drill_repoints``).
    measure_id: str | None = None
    #: Rows the drill's frame returned; ``0`` with a ``cents`` of ``None``
    #: means the population is empty at this watermark, which is itself an
    #: answer worth seeing.
    rows: int = 0
    unavailable_reason: str | None = None


#: Re-derive one card's figure at a watermark. Async because it reads the
#: warehouse (through the evidence cache); returns a stated failure rather
#: than raising, because one un-derivable card must never fail a worklist.
ImpactReDeriver = Callable[[TypedInvestigationSpec, DataWatermark], Awaitable[ReDerivedImpact]]


#: How close this platform's figure must land to the detector's to be
#: called ``agreed``. Half a percent: below that the difference is
#: rounding and windowing noise nobody should be sent to chase; above it,
#: two systems are measuring different things and the payload says so. The
#: exact delta is published either way — this threshold picks a label, it
#: never hides a number.
AGREEMENT_TOLERANCE_FRACTION = Decimal("0.005")


@dataclass(frozen=True, slots=True)
class ImpactComparison:
    """Detector figure vs platform figure, with the sentence that explains it."""

    status: str  # agreed | diverged | not_comparable | unavailable
    detector_cents: int
    platform_cents: int | None = None
    delta_cents: int | None = None
    delta_fraction: float | None = None
    measure_id: str | None = None
    note: str = ""


def non_money_reason(metric_ids: Sequence[str], pack: object) -> str | None:
    """Why this drill's contracts cannot produce a dollar figure — or ``None``.

    :func:`money_total` reads the *frame*: it sums the first column stamped
    ``money_cents`` in the last money-bearing frame. That is blind to what
    the drill's metric contract actually declares, and a ratio contract
    whose numerator happens to be money — ``late_charge_pct`` is
    ``unit: ratio`` over ``{sum: late_charge_cents}`` — would leak its
    internal numerator out as the platform's headline re-derivation,
    publishing a near-total "divergence" against a card whose drill answers
    a ratio and says outright that it is not a measure of dollars at risk.

    So the *declared* unit decides. A drill that names no money contract
    has no comparable dollar figure, and the honest answer is that there is
    nothing to compare — not a coerced number with a fabricated divergence.
    A metric the pack cannot resolve is not evidence of anything and does
    not veto the others; the frame gate in :func:`money_total` still
    applies underneath this one.
    """
    declared: list[tuple[str, str]] = []
    for metric_id in metric_ids:
        contract = pack.metric(metric_id)  # type: ignore[attr-defined]
        unit = getattr(contract, "unit", None)
        if unit is None:
            continue
        declared.append((metric_id, str(unit)))
    if not declared or any(unit == _MONEY_UNIT for _, unit in declared):
        return None
    named = "; ".join(
        f"{metric_id.replace('_', ' ')} is measured in {unit.replace('_', ' ')}"
        for metric_id, unit in declared
    )
    return (
        f"{named} — a measure that is not in dollars produces no comparable dollar "
        "figure, so there is nothing to reconcile against the card's dollar impact. Any "
        "dollar column behind it is a numerator inside the calculation, not the measure "
        "itself."
    )


def compare_impact(
    *,
    detector_cents: int,
    window_start: date,
    window_end: date,
    rederived: ReDerivedImpact | None,
    unattempted_note: str,
    not_comparable_reason: str | None = None,
) -> ImpactComparison:
    """Reconcile the two figures, in one place, for card and answer alike.

    The card's strip and the drill answer's strip must never be able to
    describe the same pair of numbers differently, so both call this.

    ``not_comparable_reason`` guards the case where the two figures are not
    measuring over the same kind of period at all — a
    ``kind: snapshot`` contract is an as-of balance and applies no window,
    while the detector fired over a start..end range — a percentage delta
    is not a disagreement between two systems, it is an artifact of
    comparing a balance to a flow. The platform's figure is still
    published; only the *verdict* changes, because attributing that gap to
    the detector's population or valuation basis would be a claim the
    platform cannot support.
    """
    if rederived is None:
        return ImpactComparison(
            status="unavailable", detector_cents=detector_cents, note=unattempted_note
        )
    if rederived.cents is None:
        return ImpactComparison(
            status="unavailable",
            detector_cents=detector_cents,
            note=(
                "the platform could not re-derive this figure: "
                f"{rederived.unavailable_reason or 'no reason recorded'}"
            ),
        )
    delta = rederived.cents - detector_cents
    fraction = Decimal(delta) / Decimal(abs(detector_cents) or 1)
    agreed = abs(fraction) <= AGREEMENT_TOLERANCE_FRACTION
    if not_comparable_reason is not None and not agreed:
        return ImpactComparison(
            status="not_comparable",
            detector_cents=detector_cents,
            platform_cents=rederived.cents,
            delta_cents=delta,
            # Deliberately withheld: a percentage here would be read as a
            # divergence between two measurements of the same thing, and
            # these are measurements of two different things.
            delta_fraction=None,
            measure_id=rederived.measure_id,
            note=(
                f"The detection system reported ${detector_cents / 100:,.2f} for this cell "
                f"over {window_start}..{window_end}. This platform re-derived "
                f"${rederived.cents / 100:,.2f} from the standard definition of "
                f"{(rederived.measure_id or 'this measure').replace('_', ' ')}. The two are "
                f"not comparable: {not_comparable_reason} The difference is not attributed "
                "to the detection system, and this card is excluded from the divergence "
                "count."
            ),
        )
    basis = (
        f"The detection system reported ${detector_cents / 100:,.2f} for this cell in its "
        f"own period ({window_start} to {window_end}), on its own population and its own "
        f"valuation, when it fired. This platform re-derived ${rederived.cents / 100:,.2f} "
        f"from the standard definition of "
        f"{(rederived.measure_id or 'this measure').replace('_', ' ')}, read from the same "
        "data load, over the same named cell."
    )
    verdict = (
        "The two agree within half a percent."
        if agreed
        else (
            f"They differ by ${delta / 100:,.2f} ({float(fraction):+.1%}): the detector's "
            "window, population or valuation basis is not the contract's. The drill answers "
            "with the contract's figure and a real evidence grade; the card's figure remains "
            "the detection system's assertion, which is what it has always been."
        )
    )
    return ImpactComparison(
        status="agreed" if agreed else "diverged",
        detector_cents=detector_cents,
        platform_cents=rederived.cents,
        delta_cents=delta,
        delta_fraction=round(float(fraction), 6),
        measure_id=rederived.measure_id,
        note=f"{basis} {verdict}",
    )


def money_total(frames: tuple[tuple[str, EvidenceFrame], ...]) -> tuple[int | None, str | None, int]:
    """Sum the money column of the last frame that has one.

    "Last" is deliberate: :class:`CalculationResult` lists frames in
    creation order, so the final money-bearing frame is the one after every
    transform the plan applied — the same frame the findings stage reads.
    A frame with no money column (a ratio drill, a count) yields ``None``
    rather than a coerced number.
    """
    for frame_id, frame in reversed(frames):
        del frame_id
        for index, column in enumerate(frame.schema.columns):
            if column.unit != _MONEY_UNIT:
                continue
            total = 0
            for row in frame.rows:
                value = row[index]
                if value is None:
                    continue
                total += int(value)
            measure = column.ref.id if isinstance(column.ref, MetricRef) else column.name
            return total, measure, len(frame.rows)
    rows = len(frames[-1][1].rows) if frames else 0
    return None, None, rows


def build_rederiver(
    *,
    interpreter: object,
    planner: object,
    validator: object,
    executor: object,
    calculator: object,
    pack: object,
    pack_snapshot_id: str,
    pack_id: str,
    pack_version: str,
) -> ImpactReDeriver:
    """A memoizing re-deriver over the engine's own stages.

    Structurally typed on purpose (the parameters are the engine services
    the composition root already built): this module is a *use* of the
    pipeline, not a second implementation of it, and naming the concrete
    classes here would add an import edge for no checking value.
    """
    cache: dict[tuple[str, str], ReDerivedImpact] = {}

    async def rederive(
        spec: TypedInvestigationSpec, watermark: DataWatermark
    ) -> ReDerivedImpact:
        # Before any planning or query: does this drill's own contract even
        # produce dollars? A ratio contract does not, and summing whatever
        # money column its frame happens to carry publishes a fabricated
        # headline. Cheap, and it fails the same way every time.
        refusal = non_money_reason(tuple(spec.metric_ids), pack)
        if refusal is not None:
            return ReDerivedImpact(unavailable_reason=refusal)
        probe_session = Session(
            id="portfolio-rederivation-probe",
            tenant="portfolio",
            pack_version=PackVersionRef(pack_id, pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=watermark),),
            created_at=datetime.now(UTC),
        )
        try:
            interpreted = interpreter.from_typed_spec(  # type: ignore[attr-defined]
                spec, session=probe_session, turn_id="rederivation-probe"
            )
            plan = planner.build(  # type: ignore[attr-defined]
                interpreted.spec, playbook_id=None, window_explicit=True
            )
            validated = validator.validate(plan, interpreted.spec)  # type: ignore[attr-defined]
        except ReviError as exc:
            return ReDerivedImpact(unavailable_reason=f"{exc.code.value}: {exc.message}")

        key = (watermark.id, validated.plan.plan_hash)
        hit = cache.get(key)
        if hit is not None:
            return hit

        try:
            executed = await executor.execute(  # type: ignore[attr-defined]
                validated.plan,
                watermark=watermark,
                pack_snapshot_id=pack_snapshot_id,
                turn_id="rederivation-probe",
                grades=dict(validated.grades),
            )
            calculation = calculator.calculate(validated.plan, executed)  # type: ignore[attr-defined]
        except ReviError as exc:
            result = ReDerivedImpact(unavailable_reason=f"{exc.code.value}: {exc.message}")
            cache[key] = result
            return result
        except Exception:
            # Deliberately broad, and deliberately NOT cached: a worklist
            # must not fail because one card's re-derivation hit an
            # infrastructure fault, and the next build should try again.
            logger.exception("re-derivation failed for plan %s", validated.plan.plan_hash)
            return ReDerivedImpact(
                unavailable_reason="the platform could not re-derive this figure from "
                "this data load"
            )

        cents, measure, rows = money_total(calculation.frames)
        result = ReDerivedImpact(
            cents=cents,
            measure_id=measure,
            rows=rows,
            unavailable_reason=(
                None
                if cents is not None
                else "the measure behind this drill produces no dollar figure, so there "
                "is nothing to compare against the card's dollar impact"
            ),
        )
        cache[key] = result
        return result

    return rederive
