"""What the Rounds surface SAYS, and what it refuses to say.

Round 7 gated on two sentences a buyer read on their own screen against
their own stored data:

    "Pinnacle Health Plan: 22.9%"  — over State Medicaid MCO's 29.5%
    "29.5% at wm_003, up 3.6 points from 25.9% at wm_002 … this change is
     late-arriving data settling — adjudication run-out"

Every clause of the second is false: the payer named in the tile's title
had FALLEN 6.6 points, and the "movement" was the difference between two
different payers, gated material, counted, and then explained by a causal
mechanism. The pieces below are the ones that made that possible and the
ones that now make it impossible:

* subject identity on a comparison (:func:`_not_comparable_reason`), and
  the payload-build assertion that a label and a value may not name
  different subjects;
* the brief's own vocabulary — human nouns, load DATES, no ``wm_`` ids, no
  "(s)", each figure said once;
* the cap, which dropped the platform's verdicts on the team's own work
  first because it truncated in assembly order;
* the census, which must reconcile to its parts on a surface whose whole
  claim is "withheld visibly, never silently".

These are the pure functions. The end-to-end proof over three real loads
lives in ``packages/testing/tests/test_rounds_loads.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from revi_api.rounds import (
    _assert_subject_matches_label,
    _cap,
    _cash_timing_lanes,
    _eq_filters_of,
    _Headline,
    _headline_sentence,
    _immaterial_note,
    _narrowed_to_cell,
    _not_comparable_reason,
    _spec_names_one_cell,
    spec_hash,
)
from revi_api.rounds_policy import RoundsPolicy, load_rounds_policy
from revi_investigation.application.ports import RoundsPin
from revi_investigation_contracts.api import AnomalyCard, TimeToImpactPayload, TypedInvestigationSpec
from revi_investigation_contracts.refinements import WindowSpecModel
from revi_investigation_contracts.rounds import (
    RoundsBriefEntry,
    RoundsDeltaPayload,
    RoundsImmaterialSummary,
    RoundsProvenancePayload,
    RoundsTilePayload,
)
from revi_kernel.errors import ReviError

PACK_ROUNDS = "packs/base-rcm/rounds.yaml"


def _spec(**overrides: object) -> TypedInvestigationSpec:
    base: dict[str, object] = {
        "metric_ids": ["denial_rate"],
        "dimensions": ["payer"],
        "window": WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
        "basis": "service",
    }
    base.update(overrides)
    return TypedInvestigationSpec(**base)  # type: ignore[arg-type]


def _pin(spec: TypedInvestigationSpec, label: str = "watched") -> RoundsPin:
    return RoundsPin(
        id="pin_test",
        tenant="demo",
        label=label,
        spec=spec,
        presentation="finding",
        window_mode="relative",
        created_at=datetime.now(UTC),
    )


def _tile(**overrides: object) -> RoundsTilePayload:
    base: dict[str, object] = {
        "pin_id": "pin_test",
        "label": "watched",
        "presentation": "finding",
        "watermark_id": "wm_002",
        "value": 0.259,
        "value_text": "25.9%",
        "unit": "ratio",
        "metric_id": "denial_rate",
    }
    base.update(overrides)
    return RoundsTilePayload(**base)  # type: ignore[arg-type]


def _headline(**overrides: object) -> _Headline:
    base: dict[str, object] = {
        "referent": "F1",
        "title": "State Medicaid MCO: 29.5% denial rate",
        "statement": "State Medicaid MCO ranks #1 of 8",
        "metric_id": "denial_rate",
        "value": Decimal("0.295"),
        "unit": "ratio",
        "text": "29.5%",
        "is_bound": False,
    }
    base.update(overrides)
    return _Headline(**base)  # type: ignore[arg-type]


def _entry(kind: str, *, title: str = "", impact: int | None = None) -> RoundsBriefEntry:
    return RoundsBriefEntry(
        kind=kind,  # type: ignore[arg-type]
        title=title or kind,
        statement="",
        impact_cents=impact,
        provenance=RoundsProvenancePayload(source="detection_feed"),
    )


# ---------------------------------------------------------------------------
# FN-1 / FN-2: which cell is this number about?


class TestSubjectIdentity:
    def test_a_delta_between_two_different_cells_is_refused_with_both_named(self) -> None:
        """The exec's own repro. wm_002 headlined Pinnacle at 25.9%, wm_003
        headlines State Medicaid MCO at 29.5%, and the shipped code called
        that "up 3.6 points" — then explained the phantom as adjudication
        run-out on a same-window note."""
        pin = _pin(_spec())  # a ranked breakdown: the headline is a RANK
        prior = _tile(headline_subject_label="Pinnacle Health Plan")

        reason = _not_comparable_reason(
            pin, prior, _headline(subject_label="State Medicaid MCO")
        )

        assert reason is not None
        assert "Pinnacle Health Plan" in reason and "State Medicaid MCO" in reason
        assert "two different subjects" in reason

    def test_two_readings_of_one_cell_stay_comparable(self) -> None:
        pin = _pin(_spec())
        prior = _tile(headline_subject_label="Pinnacle Health Plan")

        assert (
            _not_comparable_reason(
                pin, prior, _headline(subject_label="Pinnacle Health Plan")
            )
            is None
        )

    def test_a_spec_narrowed_to_one_cell_needs_no_recorded_subject(self) -> None:
        """Tiles stored before subjects were recorded must not all become
        not-comparable: when the SPEC fixes the cell, both sides measured it
        whatever their payloads happen to carry."""
        narrowed = _narrowed_to_cell(_spec(), [("payer", "Pinnacle Health Plan")])
        pin = _pin(narrowed)
        prior = _tile(headline_subject_label="")

        assert (
            _not_comparable_reason(
                pin, prior, _headline(subject_label="Pinnacle Health Plan")
            )
            is None
        )

    def test_an_unrecorded_subject_on_a_ranking_is_not_assumed_to_match(self) -> None:
        pin = _pin(_spec())
        prior = _tile(headline_subject_label="")

        reason = _not_comparable_reason(
            pin, prior, _headline(subject_label="State Medicaid MCO")
        )

        assert reason is not None and "did not record which cell" in reason

    def test_narrowing_keeps_the_dimension_and_adds_one_equality(self) -> None:
        narrowed = _narrowed_to_cell(_spec(), [("payer", "Pinnacle Health Plan")])

        assert narrowed.dimensions == ["payer"], (
            "the dimension is kept so the evaluation's own finding names the cell — "
            "dropping it answers with a bare scalar and throws that away"
        )
        assert _eq_filters_of(narrowed) == (("payer", "Pinnacle Health Plan"),)
        assert _spec_names_one_cell(narrowed)
        assert not _spec_names_one_cell(_spec())

    def test_narrowing_twice_adds_nothing(self) -> None:
        once = _narrowed_to_cell(_spec(), [("payer", "Pinnacle Health Plan")])
        twice = _narrowed_to_cell(once, [("payer", "Pinnacle Health Plan")])
        assert len(twice.filters) == len(once.filters) == 1

    def test_a_tile_may_not_publish_a_cell_its_spec_excludes(self) -> None:
        """The assertion runs at payload build on every tile, because the
        shipped defect certified itself ``grade: direct`` and nothing in the
        pipeline was in a position to notice."""
        pin = _pin(_narrowed_to_cell(_spec(), [("payer", "Pinnacle Health Plan")]))

        with pytest.raises(ReviError, match="different subjects"):
            _assert_subject_matches_label(
                pin, _headline(subject=(("payer", "State Medicaid MCO"),))
            )

    def test_the_matching_cell_passes(self) -> None:
        pin = _pin(_narrowed_to_cell(_spec(), [("payer", "Pinnacle Health Plan")]))
        _assert_subject_matches_label(
            pin, _headline(subject=(("payer", "Pinnacle Health Plan"),))
        )


# ---------------------------------------------------------------------------
# FN-18: one spec, one watch


class TestDuplicateSpecs:
    def test_order_does_not_make_two_watches_out_of_one(self) -> None:
        """A department of eight directors pinning from their own answers
        converges on the same specs within a month, and every duplicate
        re-evaluates every load and can brief one movement N times."""
        a = _spec(metric_ids=["denial_rate", "denied_dollars"], dimensions=["payer", "carc"])
        b = _spec(metric_ids=["denied_dollars", "denial_rate"], dimensions=["carc", "payer"])

        assert spec_hash(a, "finding") == spec_hash(b, "finding")

    def test_a_different_cell_is_a_different_watch(self) -> None:
        one = _narrowed_to_cell(_spec(), [("payer", "Pinnacle Health Plan")])
        other = _narrowed_to_cell(_spec(), [("payer", "State Medicaid MCO")])

        assert spec_hash(one, "finding") != spec_hash(other, "finding")

    def test_presentation_is_part_of_the_identity(self) -> None:
        assert spec_hash(_spec(), "chart") != spec_hash(_spec(), "finding")


# ---------------------------------------------------------------------------
# FN-11: what the cap drops


class TestTheCapDropsByConsequence:
    @staticmethod
    def _policy() -> RoundsPolicy:
        return load_rounds_policy(PACK_ROUNDS)

    def test_the_governed_order_is_content_not_assembly_order(self) -> None:
        materiality = self._policy().materiality
        assert materiality.priority_order[0] == "resolution_regressed"
        assert materiality.rank_of("resolution_regressed") < materiality.rank_of("new_lead")
        assert materiality.rank_of("new_lead") < materiality.rank_of("self_resolved")
        assert "resolution_regressed" in materiality.never_capped

    def test_a_regression_survives_a_full_brief(self) -> None:
        """The pack's own words, a few lines below the cap it governs: "the
        cost of missing a regression (an analyst believes something is
        fixed) is higher than the cost of one extra line." The shipped cap
        deleted regressions FIRST."""
        policy = self._policy()
        entries = [_entry("new_lead", title=f"lead {i}", impact=10_000) for i in range(20)]
        entries += [_entry("self_resolved", title=f"gone {i}", impact=9_000) for i in range(20)]
        entries.append(_entry("resolution_regressed", title="the fix did not hold"))

        published, dropped = _cap(entries, policy)

        assert any(e.kind == "resolution_regressed" for e in published)
        assert dropped, "what the cap took is reported, not swallowed"

    def test_the_biggest_of_a_kind_survives_its_own_cap(self) -> None:
        policy = self._policy()
        entries = [
            _entry("new_lead", title=f"lead {i}", impact=i * 1_000) for i in range(12)
        ]

        published, dropped = _cap(entries, policy)

        titles = [e.title for e in published]
        assert "lead 11" in titles and "lead 0" not in titles
        assert dropped["new_lead"] == 12 - policy.materiality.max_entries_per_kind

    def test_what_was_dropped_is_reported_by_kind(self) -> None:
        policy = self._policy()
        entries = [_entry("new_lead", title=f"n{i}", impact=1) for i in range(9)]
        entries += [_entry("self_resolved", title=f"s{i}", impact=1) for i in range(9)]

        _, dropped = _cap(entries, policy)

        assert set(dropped) == {"new_lead", "self_resolved"}


# ---------------------------------------------------------------------------
# FN-8: the sentence a champion screenshots


class TestBriefCopy:
    def test_the_headline_speaks_dates_and_nouns(self) -> None:
        sentence = _headline_sentence(
            status="material_changes",
            newest_data_date=date(2026, 8, 2),
            prior_newest_data_date=date(2026, 8, 1),
            has_prior=True,
            entries=[
                _entry("new_lead"),
                _entry("new_lead"),
                _entry("pin_movement"),
                _entry("self_resolved"),
            ],
            pins_evaluated=12,
            leads=33,
        )

        assert sentence == (
            "Since the Aug 1 load: 2 new leads, 1 watch moved, 1 resolved on its own."
        )

    def test_the_proud_shape_stays_proud_and_stays_human(self) -> None:
        sentence = _headline_sentence(
            status="nothing_material",
            newest_data_date=date(2026, 8, 2),
            prior_newest_data_date=date(2026, 8, 1),
            has_prior=True,
            entries=[],
            pins_evaluated=1,
            leads=33,
        )

        assert "Nothing material has changed since the Aug 1 load" in sentence
        assert "1 watch " in sentence and "33 detected leads" in sentence

    def test_a_load_with_no_recorded_date_is_named_not_numbered(self) -> None:
        """Loads written before the census recorded a data date. The prose
        says "the previous load" rather than falling back to a warehouse
        handle — the id is still on the payload, in provenance."""
        sentence = _headline_sentence(
            status="material_changes",
            newest_data_date=date(2026, 8, 2),
            prior_newest_data_date=None,
            has_prior=True,
            entries=[_entry("rank_flip")],
            pins_evaluated=1,
            leads=1,
        )

        assert sentence == "Since the previous load: 1 new leader."

    @pytest.mark.parametrize(
        "sentence",
        [
            _headline_sentence(
                status="material_changes",
                newest_data_date=date(2026, 8, 2),
                prior_newest_data_date=date(2026, 8, 1),
                has_prior=True,
                entries=[_entry("pin_movement"), _entry("resolution_regressed")],
                pins_evaluated=12,
                leads=33,
            ),
            _headline_sentence(
                status="first_load",
                newest_data_date=date(2026, 7, 31),
                prior_newest_data_date=None,
                has_prior=False,
                entries=[],
                pins_evaluated=2,
                leads=30,
            ),
            _immaterial_note(
                RoundsImmaterialSummary(
                    pin_movements=1,
                    new_leads=2,
                    self_resolved=1,
                    not_yet_comparable=3,
                    unavailable=1,
                    entries_withheld_by_cap=4,
                    entries_withheld_by_kind={"new_lead": 4},
                )
            ),
        ],
    )
    def test_no_machine_grammar_reaches_the_brief_surface(self, sentence: str) -> None:
        assert "(s)" not in sentence and "(es)" not in sentence
        assert "wm_" not in sentence
        assert "pin" not in sentence.lower(), "the spec's own naming rule bans the word"
        assert "tile" not in sentence.lower()
        assert "_" not in sentence, "no raw enum ids"

    def test_the_headline_no_longer_prints_the_held_back_line_too(self) -> None:
        """It was printed twice on one screen, byte-identical — once inside
        the headline and once as its own line. ImmaterialLine owns it."""
        sentence = _headline_sentence(
            status="nothing_material",
            newest_data_date=date(2026, 8, 2),
            prior_newest_data_date=date(2026, 8, 1),
            has_prior=True,
            entries=[],
            pins_evaluated=3,
            leads=4,
        )

        assert "Held back" not in sentence

    def test_the_held_back_line_counts_every_bucket(self) -> None:
        note = _immaterial_note(
            RoundsImmaterialSummary(
                pin_movements=1,
                not_yet_comparable=16,
                unavailable=2,
            )
        )

        assert "1 watch moved by less than" in note
        assert "16 watches have nothing to compare against yet" in note
        assert "2 watches could not be measured" in note


# ---------------------------------------------------------------------------
# FN-16: the money still catchable


class TestCashTimingLanes:
    @staticmethod
    def _card(
        anomaly_id: str,
        lane: str | None,
        *,
        recoverable: int,
        deadline: date | None = None,
        days: int | None = None,
        recovery: date | None = None,
        timing: TimeToImpactPayload | None = None,
    ) -> AnomalyCard:
        if lane is not None and timing is None:
            timing = TimeToImpactPayload(
                kind="deadline" if deadline else "already_hit",
                lane=lane,  # type: ignore[arg-type]
                days=days,
                deadline_date=deadline,
                recovery_deadline_date=recovery,
                method="test",
            )
        return AnomalyCard(
            anomaly_id=anomaly_id,
            provenance="external_detection",
            priority_formula_version="anomaly_priority@3",
            source_watermark_id="wm_003",
            title=anomaly_id,
            description=anomaly_id,
            category="timely_filing",
            metric_id="denied_dollars",
            severity="high",
            confidence="high",
            status="open",
            detected_at=datetime(2026, 8, 2, tzinfo=UTC),
            window_start=date(2026, 7, 1),
            window_end=date(2026, 7, 31),
            drill_spec=_spec(dimensions=[]),
            lane="value",
            impact_cents=recoverable,
            ranked_impact_cents=recoverable,
            recoverable_cents_estimate=recoverable,
            time_to_impact=timing,
        )

    def test_the_split_totals_the_money_that_has_not_hit_cash(self) -> None:
        """The director's question — "how much money has not hit cash yet
        and when are the deadlines?" — was answered with one
        undifferentiated total, and the deadline half was dropped with no
        refusal. Every part of the answer was already on each card."""
        lanes = _cash_timing_lanes(
            [
                self._card("A", "pre_cash", recoverable=17_064_300,
                           deadline=date(2026, 8, 19), days=17),
                self._card("B", "pre_cash", recoverable=5_000_00),
                self._card("C", "already_hit", recoverable=9_000_00,
                           recovery=date(2026, 9, 1)),
            ]
        )

        by_id = {lane.id: lane for lane in lanes}
        assert by_id["pre_cash"].recoverable_cents_estimate == 17_064_300 + 5_000_00
        assert by_id["pre_cash"].item_count == 2
        assert by_id["pre_cash"].soonest_deadline_date == date(2026, 8, 19)
        assert by_id["pre_cash"].soonest_deadline_days == 17
        assert by_id["pre_cash"].dated_item_count == 1, (
            "a horizon computed from one of two cards is a fact about one card"
        )
        assert all(lane.kind == "cash_timing" for lane in lanes)

    def test_a_recovery_window_is_the_dated_limit_on_the_already_hit_lane(self) -> None:
        lanes = _cash_timing_lanes(
            [self._card("C", "already_hit", recoverable=1, recovery=date(2026, 9, 1))]
        )
        assert lanes[0].soonest_deadline_date == date(2026, 9, 1)

    def test_a_closed_window_is_counted_rather_than_rendered_as_a_countdown(self) -> None:
        """On the reference worklist the soonest limit in the already-hit
        lane closed 94 days ago. Sorting on it makes the header read "closes
        in -94 days", which is a passed deadline wearing a countdown's
        clothes — and hiding it would drop the fact that windows have
        closed. Both are published, each as what it is."""
        lanes = _cash_timing_lanes(
            [
                self._card(
                    "PAST", "pre_cash", recoverable=1, deadline=date(2026, 4, 30), days=-94
                ),
                self._card(
                    "OPEN", "pre_cash", recoverable=1, deadline=date(2026, 8, 19), days=17
                ),
            ]
        )

        assert lanes[0].soonest_deadline_date == date(2026, 8, 19)
        assert lanes[0].soonest_deadline_days == 17
        assert lanes[0].dated_item_count == 2
        assert lanes[0].passed_deadline_count == 1

    def test_a_lane_of_only_closed_windows_publishes_no_horizon(self) -> None:
        lanes = _cash_timing_lanes(
            [
                self._card(
                    "PAST", "pre_cash", recoverable=1, deadline=date(2026, 4, 30), days=-94
                )
            ]
        )

        assert lanes[0].soonest_deadline_date is None
        assert lanes[0].passed_deadline_count == 1

    def test_a_projection_never_becomes_a_horizon(self) -> None:
        """``TimeToImpactPayload`` refuses to put a projection in
        ``deadline_date`` for the same reason: an estimate rendered beside a
        filing limit is indistinguishable from one."""
        projected = self._card(
            "P",
            None,
            recoverable=1,
            timing=TimeToImpactPayload(
                kind="projected", lane="pre_cash", days=32, provisional=True, method="test"
            ),
        )

        lanes = _cash_timing_lanes([projected])

        assert lanes[0].soonest_deadline_date is None
        assert lanes[0].dated_item_count == 0

    def test_a_card_with_no_timing_lands_in_the_stated_unknown_lane(self) -> None:
        lanes = _cash_timing_lanes([self._card("U", None, recoverable=1)])
        assert [lane.id for lane in lanes] == ["unknown"]
        assert lanes[0].description


# ---------------------------------------------------------------------------
# FN-12: the census closes


def test_a_delta_payload_can_state_a_first_reading() -> None:
    """A tile with no prior sent ``null``, and a renderer draws nothing for
    nothing — so a watch that had never been compared looked exactly like
    one that had not moved. Absence is read as absence only if something
    says so."""
    payload = RoundsDeltaPayload(
        comparable=False,
        not_comparable_reason="first reading — baseline set at this load",
    )
    assert not payload.comparable and payload.not_comparable_reason
