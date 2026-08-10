"""The worklist as a set of referents, as THE answer when it routes, and the
platform's own dimension swap owned rather than blamed on the detector.

Three regressions. A worklist row was addressable by mouse and unaddressable by
name: "Show me ANM-021" answered "I can't open a worklist item by its id" while
the same turn's payload named that very card. A "what should we work first"
turn closed by recommending a payer at a fifth of the money the attached
worklist's first item carried. And the drill strip blamed the detector's window
or basis for a gap this platform had created by repointing the cut.
"""

from __future__ import annotations

from datetime import date

import pytest

from revi_api.actionability import DimensionRepoint
from revi_api.portfolio import dimension_repointed_warning, reconciliation_note
from revi_api.rederive import ImpactComparison
from revi_api.warning_codes import classify
from revi_api.worklist import resolve_worklist_reference
from revi_investigation.application.ports import AnomalyRecord
from revi_investigation_contracts.api import AnomalyCard, TypedInvestigationSpec
from revi_investigation_contracts.refinements import AbsoluteWindowModel
from revi_presentation.narrative import _first_action_conflict


def _card(anomaly_id: str, title: str, lane: str = "value") -> AnomalyCard:
    return AnomalyCard(
        anomaly_id=anomaly_id,
        title=title,
        category="dnfb",
        detected_at=date(2026, 7, 30),
        window_start=date(2026, 7, 3),
        window_end=date(2026, 7, 29),
        metric_id="dnfb_dollars",
        impact_cents=17821682,
        provenance="external_detection",
        description="A detection-feed card, as the rail publishes it.",
        severity="high",
        confidence="high",
        status="open",
        lane=lane,  # type: ignore[arg-type]
        drill_spec=TypedInvestigationSpec(
            metric_ids=["dnfb_dollars"],
            dimensions=["facility"],
            filters=[],
            window=AbsoluteWindowModel(start=date(2026, 7, 3), end=date(2026, 7, 29)),
        ),
        priority_formula_version="anomaly_priority@3",
        source_watermark_id="wm_003",
    )


CARDS = (
    _card("ANM-021", "DNFB accumulation: Northgate general-surgery discharges"),
    _card("ANM-013", "Contract-rate reset (working as designed)"),
    _card("ANM-024", "Credit balance aging past 60 days", lane="compliance"),
)


class TestWorklistRowsAreReferents:
    """The ids and the positions this platform PRINTED resolve the way every
    other handle it prints resolves: by lookup, before any model call."""

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            ("Show me ANM-021", "ANM-021"),
            ("show me anm-021", "ANM-021"),
            ("open ANM 21", "ANM-021"),
            ("what is behind ANM-013?", "ANM-013"),
        ],
    )
    def test_an_anomaly_id_resolves_however_it_is_typed(
        self, utterance: str, expected: str
    ) -> None:
        reference = resolve_worklist_reference(utterance, CARDS)
        assert reference is not None
        assert reference.card.anomaly_id == expected
        assert reference.basis == "anomaly_id"

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            ("Open the top item and show me what is behind the $178,217", "ANM-021"),
            ("open the first one", "ANM-021"),
            ("show me the second", "ANM-013"),
            ("number 3", "ANM-024"),
            ("item 2 please", "ANM-013"),
            ("#3", "ANM-024"),
        ],
    )
    def test_a_position_resolves_to_the_row_at_that_position(
        self, utterance: str, expected: str
    ) -> None:
        reference = resolve_worklist_reference(utterance, CARDS)
        assert reference is not None
        assert reference.card.anomaly_id == expected
        assert reference.basis == "ordinal"

    def test_the_resolved_card_routes_to_its_own_stored_drill(self) -> None:
        """The identical path the rail takes — so the reconciliation strip
        and the repoint disclosure fire for a typed reference exactly as
        they do for a click."""
        reference = resolve_worklist_reference("open the top item", CARDS)
        assert reference is not None
        assert reference.card.drill_spec is CARDS[0].drill_spec

    def test_an_id_the_list_does_not_hold_resolves_to_nothing(self) -> None:
        assert resolve_worklist_reference("show me ANM-999", CARDS) is None

    def test_a_position_past_the_end_resolves_to_nothing(self) -> None:
        """A claim about a row the analyst cannot see is worse than a
        question."""
        assert resolve_worklist_reference("number 9", CARDS) is None

    def test_nothing_resolves_without_a_list(self) -> None:
        assert resolve_worklist_reference("open the top item", ()) is None

    def test_a_finding_handle_belongs_to_the_engine(self) -> None:
        """F1 is the referent registry's, not this list's; two resolvers
        racing for the same words is how one of them wins wrongly."""
        assert resolve_worklist_reference("drill into F1 for the top payer", CARDS) is None

    def test_ordinary_language_is_left_alone(self) -> None:
        assert resolve_worklist_reference("why did denials rise in July?", CARDS) is None


class TestTheWorklistLeadsWhenItRoutes:
    """Two orderings on one card, and when the question was "what should we
    work first" only one of them was asked for."""

    @pytest.mark.parametrize(
        "sentence",
        [
            "Start with Atlas Commercial at $33,954.90 (F1).",
            "The first action is to work Atlas Commercial (F1).",
            "Prioritise the Meridian Health backlog (F2).",
            "Your next step should be the Silverline queue (F3).",
        ],
    )
    def test_a_contradicting_first_action_is_refused(self, sentence: str) -> None:
        assert _first_action_conflict(sentence, "ANM-021") is not None

    def test_naming_rank_one_is_allowed(self) -> None:
        assert (
            _first_action_conflict("Start with ANM-021, the largest card.", "ANM-021")
            is None
        )

    def test_a_sentence_that_recommends_nothing_is_allowed(self) -> None:
        """The findings may still say which figure is largest — they rank a
        different thing over a different population, and saying so is not
        an instruction."""
        assert (
            _first_action_conflict(
                "Atlas Commercial shows the largest denied dollars at $33,954.90 (F1).",
                "ANM-021",
            )
            is None
        )

    def test_no_worklist_leaves_the_prose_owning_the_recommendation(self) -> None:
        assert _first_action_conflict("Start with Atlas Commercial (F1).", "") is None


def _comparison(status: str) -> ImpactComparison:
    return ImpactComparison(
        status=status,  # type: ignore[arg-type]
        detector_cents=25667,
        platform_cents=0,
        delta_cents=-25667,
        delta_fraction=-1.0,
        measure_id="denied_dollars",
        note=(
            "The detection system reported $256.67 for this cell. This platform "
            "re-derived $0.00 from the governed contract. The two diverge: the "
            "detector's window, population or valuation basis is not the contract's."
        ),
    )


REPOINT = DimensionRepoint(
    from_dimension="proc_group",
    to_dimension="primary_proc_group",
    rationale="Procedures bind at claim_line, so a claim-grain contract cannot be cut "
    "by `proc_group` at all.",
)


class TestRepointsAreOwnedNotBlamed:
    def test_the_drill_says_what_the_card_says(self) -> None:
        """``service.py`` set ``detail=comparison.note`` straight off the raw
        comparison and shipped the shared sentence on a gap this platform
        created. One function, both surfaces."""
        note = reconciliation_note(_comparison("diverged"), (REPOINT,))
        assert "this platform's own doing, not the detector's" in note
        assert "proc_group → primary_proc_group" in note
        assert "related — not identical — populations" in note

    def test_an_unrepointed_card_keeps_the_plain_note(self) -> None:
        comparison = _comparison("diverged")
        assert reconciliation_note(comparison, ()) == comparison.note

    def test_an_agreeing_card_says_nothing_about_the_swap(self) -> None:
        """A repoint that produced no gap is not an explanation of a gap."""
        comparison = _comparison("agreed")
        assert reconciliation_note(comparison, (REPOINT,)) == comparison.note

    def test_the_turn_warning_carries_the_packs_own_rationale(self) -> None:
        record = AnomalyRecord(
            anomaly_id="ANM-013",
            title="Contract-rate reset",
            category="underpayment",
            detected_at=date(2026, 7, 30),
            window_start=date(2026, 5, 1),
            window_end=date(2026, 6, 25),
            metric_id="gross_collection_rate",
            impact_cents=25667,
            dimensions=(("proc_group", "ORTHO-SURG"),),
            status="open",
            description="Contract-rate reset, working as designed.",
            severity="low",
            confidence="high",
            evidence=(),
        )
        warning = dimension_repointed_warning(record, (REPOINT,))
        assert warning.startswith("dimension_repointed:")
        assert "ANM-013" in warning
        assert "proc_group → primary_proc_group" in warning
        # verbatim, never re-worded: a governed decision explained in the
        # platform's own words would be a second, ungoverned explanation
        assert REPOINT.rationale in warning

    def test_the_warning_classifies_as_its_own_family(self) -> None:
        code, severity = classify("dimension_repointed: this drill of ANM-013 …")
        assert (code, severity) == ("DIMENSION_REPOINTED", "caution")
