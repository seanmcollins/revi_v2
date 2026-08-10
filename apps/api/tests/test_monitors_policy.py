"""The governed Monitors gate: materiality, monitor thresholds, cash timing.

Alert fatigue is the death mode of a daily surface, and every number that
decides whether somebody is interrupted lives in ``packs/base-rcm/monitors.yaml``
rather than in engine code. These tests hold the gate to what that file
says — including the two properties that are easy to get subtly wrong and
impossible to notice in production:

* a RATE is gated in percentage points and never as a relative percentage
  ("up 3.2%" is ambiguous between 5.0→5.16 and 5.0→8.2, and the platform
  refuses that ambiguity everywhere else);
* MONEY needs a relative gate AND an absolute floor, conjoined. Relative
  alone briefs a $40 credit balance that doubled; absolute alone briefs a
  rounding error on a $12M receivable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from revi_api.monitors_policy import (
    MONITORS_FILENAME,
    assess_movement,
    assess_new_lead,
    assess_self_resolved,
    load_monitors_policy,
    time_to_impact_for,
    validate_monitor,
)
from revi_investigation.application.ports import AnomalyRecord, Monitor

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK = REPO_ROOT / "packs" / "base-rcm" / MONITORS_FILENAME


@pytest.fixture(scope="module")
def policy():  # type: ignore[no-untyped-def]
    return load_monitors_policy(PACK)


def _record(category: str, evidence: dict[str, object]) -> AnomalyRecord:
    return AnomalyRecord(
        anomaly_id="ANM-TEST",
        detected_at=datetime(2026, 8, 2, tzinfo=UTC),
        category=category,
        title="test",
        description="test",
        metric_id="denied_dollars",
        dimensions=(("payer", "Meridian Health"),),
        window_start=date(2026, 7, 1),
        window_end=date(2026, 8, 2),
        impact_cents=1_000_000,
        severity="high",
        confidence="0.9",
        status="open",
        evidence=evidence,
    )


class TestTheGovernedFileLoads:
    def test_the_pack_ships_monitors_content(self, policy) -> None:  # type: ignore[no-untyped-def]
        assert policy.enabled
        assert policy.content_hash
        assert policy.source.endswith(MONITORS_FILENAME)

    def test_a_missing_file_is_an_absence_not_an_error(self, tmp_path: Path) -> None:
        """A deployment whose pack ships no Monitors content still pins and
        still evaluates; it simply has no gate, and the brief says so."""
        empty = load_monitors_policy(tmp_path / "nope.yaml")
        assert not empty.enabled
        assert empty.materiality.unit_kinds == {}

    def test_a_time_to_impact_refusal_must_state_its_reason(self, tmp_path: Path) -> None:
        """The refusal arm is the one that must never be silent: a category
        published as ``unknown`` with no reason reads as a bug."""
        bad = tmp_path / "monitors.yaml"
        bad.write_text(
            "time_to_impact:\n  categories:\n    mystery:\n      method: unknown\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="without a reason"):
            load_monitors_policy(bad)


class TestMateriality:
    def test_a_rate_is_gated_in_points_not_percent(self, policy) -> None:  # type: ignore[no-untyped-def]
        """0.10 → 0.102 is +2% relative and 0.2 points. The pack gates at
        half a point, so this is NOT material — and a relative gate would
        have briefed it."""
        verdict = assess_movement(
            unit="ratio",
            prior=Decimal("0.10"),
            current=Decimal("0.102"),
            policy=policy.materiality,
        )
        assert not verdict.material
        assert verdict.rule == "ratio_points"
        assert "0.2 points" in verdict.note
        assert "%" not in verdict.note, "a rate's movement is never stated as a percentage"

    def test_a_rate_movement_over_the_gate_is_material(self, policy) -> None:  # type: ignore[no-untyped-def]
        verdict = assess_movement(
            unit="ratio",
            prior=Decimal("0.10"),
            current=Decimal("0.112"),
            policy=policy.materiality,
        )
        assert verdict.material and verdict.threshold_source == "governed"

    def test_money_needs_both_the_percentage_and_the_floor(self, policy) -> None:  # type: ignore[no-untyped-def]
        """$4,000 → $4,400 is +10%, over the relative gate, and $400, under
        the $1,000 floor. Conjoined, so it is not briefed."""
        under_floor = assess_movement(
            unit="money_cents",
            prior=Decimal(400_000),
            current=Decimal(440_000),
            policy=policy.materiality,
        )
        assert not under_floor.material

        # $1,000,000 → $1,020,000 is $20,000 (over the floor) and 2% (under
        # the relative gate). Also not briefed.
        under_relative = assess_movement(
            unit="money_cents",
            prior=Decimal(100_000_000),
            current=Decimal(102_000_000),
            policy=policy.materiality,
        )
        assert not under_relative.material

        both = assess_movement(
            unit="money_cents",
            prior=Decimal(100_000_000),
            current=Decimal(120_000_000),
            policy=policy.materiality,
        )
        assert both.material

    def test_an_ungoverned_unit_gates_HARDER_not_softer(self, policy) -> None:  # type: ignore[no-untyped-def]
        """When in doubt, hold: a brief that fires on movements it could not
        measure is the fatigue mode with extra steps."""
        verdict = assess_movement(
            unit="furlongs",
            prior=Decimal(1),
            current=Decimal(1000),
            policy=policy.materiality,
        )
        assert not verdict.material
        assert verdict.rule == "no_governed_threshold"
        assert "ungated alert" in verdict.note

    def test_a_first_evaluation_claims_no_movement(self, policy) -> None:  # type: ignore[no-untyped-def]
        verdict = assess_movement(
            unit="money_cents", prior=None, current=Decimal(1), policy=policy.materiality
        )
        assert not verdict.material and verdict.rule == "not_comparable"

    def test_a_compliance_lead_is_briefed_regardless_of_size(self, policy) -> None:  # type: ignore[no-untyped-def]
        """An $824 credit balance carries the same refund obligation as an
        $84,000 one — which is the whole reason the lane exists."""
        tiny = assess_new_lead(
            impact_cents=82_400, lane="compliance", policy=policy.materiality
        )
        assert tiny.material and tiny.rule == "always_material_lane"

        value_lane = assess_new_lead(
            impact_cents=82_400, lane="value", policy=policy.materiality
        )
        assert not value_lane.material and value_lane.rule == "new_lead_floor"

    def test_a_self_resolved_lead_below_the_floor_is_counted_not_briefed(
        self, policy
    ) -> None:  # type: ignore[no-untyped-def]
        assert not assess_self_resolved(
            impact_cents=5_000, policy=policy.materiality
        ).material
        assert assess_self_resolved(
            impact_cents=5_000_000, policy=policy.materiality
        ).material


class TestMonitorThresholds:
    def test_any_movement_briefs_a_move_the_governed_gate_would_not(
        self, policy
    ) -> None:  # type: ignore[no-untyped-def]
        """An analyst's threshold may LOOSEN the pack's — and when it does,
        the verdict records that so the brief can notice the pattern."""
        verdict = assess_movement(
            unit="ratio",
            prior=Decimal("0.10"),
            current=Decimal("0.1001"),
            policy=policy.materiality,
            monitor=Monitor(mode="any_movement"),
        )
        assert verdict.material
        assert verdict.threshold_source == "monitor"
        assert verdict.below_governed_gate, "the fatigue counter would never fire"

    def test_a_tighter_monitor_holds_a_move_the_governed_gate_would_brief(
        self, policy
    ) -> None:  # type: ignore[no-untyped-def]
        verdict = assess_movement(
            unit="ratio",
            prior=Decimal("0.10"),
            current=Decimal("0.112"),
            policy=policy.materiality,
            monitor=Monitor(mode="delta_gte", value=Decimal(5), unit="points"),
        )
        assert not verdict.material
        assert verdict.threshold_source == "monitor"
        assert not verdict.below_governed_gate

    def test_direction_applies_to_every_mode(self, policy) -> None:  # type: ignore[no-untyped-def]
        verdict = assess_movement(
            unit="ratio",
            prior=Decimal("0.20"),
            current=Decimal("0.10"),
            policy=policy.materiality,
            monitor=Monitor(mode="any_movement", direction="up"),
        )
        assert not verdict.material and verdict.rule == "monitor_direction"

    def test_crosses_fires_only_on_the_load_that_crosses(self, policy) -> None:  # type: ignore[no-untyped-def]
        monitor = Monitor(mode="crosses", value=Decimal(15), unit="points")
        approaching = assess_movement(
            unit="ratio",
            prior=Decimal("0.10"),
            current=Decimal("0.14"),
            policy=policy.materiality,
            monitor=monitor,
        )
        crossing = assess_movement(
            unit="ratio",
            prior=Decimal("0.14"),
            current=Decimal("0.16"),
            policy=policy.materiality,
            monitor=monitor,
        )
        back_down = assess_movement(
            unit="ratio",
            prior=Decimal("0.16"),
            current=Decimal("0.14"),
            policy=policy.materiality,
            monitor=monitor,
        )
        assert not approaching.material
        assert crossing.material and crossing.rule == "monitor_crosses"
        assert back_down.material, "crossing back down is a crossing"

    def test_a_dishonest_unit_pairing_is_refused_at_creation(self) -> None:
        assert validate_monitor(
            Monitor(mode="delta_gte", value=Decimal(1), unit="points"),
            units=["money_cents"],
        )
        assert validate_monitor(
            Monitor(mode="delta_gte", value=Decimal(1), unit="cents"),
            units=["ratio"],
        )
        assert (
            validate_monitor(
                Monitor(mode="delta_gte", value=Decimal(1), unit="points"),
                units=["ratio"],
            )
            is None
        )

    def test_a_days_threshold_is_legal_over_a_days_contract_and_nowhere_else(self) -> None:
        """The one contract whose own unit is also how a human states a
        threshold for it. Legal there; refused BY NAME everywhere else,
        because "2 days" on a denial rate has no meaning and coercing it
        into the metric's own unit would gate at 2 percentage points."""
        assert (
            validate_monitor(
                Monitor(mode="delta_gte", value=Decimal(2), unit="days"),
                units=["days"],
            )
            is None
        )
        for units in (["ratio"], ["money_cents"], ["count"]):
            refusal = validate_monitor(
                Monitor(mode="delta_gte", value=Decimal(2), unit="days"),
                units=units,
            )
            assert refusal is not None and "'days' contract" in refusal, units

    def test_a_days_threshold_gates_in_the_metrics_own_unit(self, policy) -> None:  # type: ignore[no-untyped-def]
        """Both directions of the gate, so "more than 2 days" cannot be
        read as anything but two days."""
        below = assess_movement(
            unit="days",
            prior=Decimal("2.5"),
            current=Decimal("4.0"),
            policy=policy.materiality,
            monitor=Monitor(mode="delta_gte", value=Decimal(2), unit="days"),
        )
        at_gate = assess_movement(
            unit="days",
            prior=Decimal("2.5"),
            current=Decimal("4.5"),
            policy=policy.materiality,
            monitor=Monitor(mode="delta_gte", value=Decimal(2), unit="days"),
        )
        assert not below.material
        assert at_gate.material and at_gate.rule == "monitor_delta_gte"
        assert "2.0 days" in at_gate.note

    def test_a_days_threshold_over_a_rate_cannot_evaluate_rather_than_coerce(
        self, policy
    ) -> None:  # type: ignore[no-untyped-def]
        """A stored monitor whose unit no longer fits its metric degrades to
        "cannot evaluate" and says so — it never gates on 2 points."""
        verdict = assess_movement(
            unit="ratio",
            prior=Decimal("0.10"),
            current=Decimal("0.90"),
            policy=policy.materiality,
            monitor=Monitor(mode="delta_gte", value=Decimal(2), unit="days"),
        )
        assert not verdict.material and verdict.rule == "monitor_unit_mismatch"

    def test_relative_pct_is_legal_against_any_unit(self) -> None:
        """A fraction of the reference value means the same thing in every
        unit, so it needs no agreement with the contract."""
        for units in (["ratio"], ["money_cents"], ["days"], ["money_cents", "ratio"]):
            assert (
                validate_monitor(
                    Monitor(mode="delta_gte", value=Decimal(10), unit="relative_pct"),
                    units=units,
                )
                is None
            )

    def test_a_multi_unit_spec_refuses_a_unit_specific_threshold(self) -> None:
        refusal = validate_monitor(
            Monitor(mode="delta_gte", value=Decimal(1), unit="points"),
            units=["ratio", "money_cents"],
        )
        assert refusal is not None and "more than one unit" in refusal

    def test_a_valueless_mode_refuses_a_value(self) -> None:
        refusal = validate_monitor(
            Monitor(mode="any_movement", value=Decimal(1)), units=["ratio"]
        )
        assert refusal is not None and "takes no threshold value" in refusal


class TestTimeToImpact:
    def test_a_filing_deadline_is_a_real_date_from_the_detectors_own_facts(
        self, policy
    ) -> None:  # type: ignore[no-untyped-def]
        record = _record(
            "timely_filing",
            {
                "cutoff": "2026-08-02",
                "days_to_deadline": {"min": 29, "median": 41, "max": 51},
                "timely_filing_days": 90,
            },
        )
        payload = time_to_impact_for(
            record, newest_data_date=date(2026, 8, 2), policy=policy.time_to_impact
        )
        assert payload is not None
        assert payload.kind == "deadline" and payload.lane == "pre_cash"
        assert payload.days == 29
        assert payload.deadline_date == date(2026, 8, 31)
        assert payload.provisional is False

    def test_a_projection_is_marked_provisional_and_publishes_no_date(
        self, policy
    ) -> None:  # type: ignore[no-untyped-def]
        record = _record(
            "dnfb",
            {"cutoff": "2026-08-02", "days_since_discharge": {"min": 4, "median": 15, "max": 30}},
        )
        payload = time_to_impact_for(
            record, newest_data_date=date(2026, 8, 2), policy=policy.time_to_impact
        )
        assert payload is not None
        assert payload.kind == "projected" and payload.provisional
        assert payload.deadline_date is None
        assert payload.days == policy.time_to_impact.bill_days + policy.time_to_impact.payment_lag_days
        assert "planning defaults" in payload.method

    def test_a_closed_appeal_window_publishes_its_negative_days(self, policy) -> None:  # type: ignore[no-untyped-def]
        """"The appeal window closed 49 days ago" is the fact that decides
        whether the money is reachable; a null there would read as "no
        deadline"."""
        record = _record(
            "unworked_denials",
            {"cutoff": "2026-08-02", "days_to_appeal_deadline": {"min": -94, "max": -49}},
        )
        payload = time_to_impact_for(
            record, newest_data_date=date(2026, 8, 2), policy=policy.time_to_impact
        )
        assert payload is not None
        assert payload.kind == "already_hit"
        assert payload.recovery_days == -94
        # The date the window actually closed, from the detector's own
        # cutoff — published rather than suppressed, because it decides
        # whether the money is reachable at all.
        assert payload.recovery_deadline_date == date(2026, 4, 30)

    def test_a_missing_evidence_fact_refuses_rather_than_guesses(self, policy) -> None:  # type: ignore[no-untyped-def]
        record = _record("timely_filing", {"cutoff": "2026-08-02"})
        payload = time_to_impact_for(
            record, newest_data_date=date(2026, 8, 2), policy=policy.time_to_impact
        )
        assert payload is not None
        assert payload.kind == "unknown"
        assert payload.reason and "days_to_deadline" in payload.reason

    def test_an_ungoverned_category_says_the_pack_has_no_rule(self, policy) -> None:  # type: ignore[no-untyped-def]
        record = _record("brand_new_category", {"cutoff": "2026-08-02"})
        payload = time_to_impact_for(
            record, newest_data_date=date(2026, 8, 2), policy=policy.time_to_impact
        )
        assert payload is not None
        assert payload.kind == "unknown" and payload.method_id == "ungoverned"
        assert "brand_new_category" in (payload.reason or "")
