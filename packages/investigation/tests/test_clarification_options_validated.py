"""Every clarification option this platform offers must be answerable.

Regression: model-authored options skipped the value validation applied to user-supplied
predicates. ``_option_resolves`` checked a scope value against a dimension's DECLARED
``value_domain`` and continued when there was none — which is every OPEN dimension
(``payer``, ``plan``, ``facility``) — and nothing dry-ran an option against the planner, so
an option naming a legal metric and an illegal cut for it survived to be tapped. Options are
now resolved the way the turn that accepts them will run them, at this watermark.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest
from revi_testing.engine_wiring import WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not WAREHOUSE.is_file(),
        reason="generated warehouse missing — run: "
        "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
    ),
]

QUESTION = "Walk me through the medical necessity denial spike at Summit Peak"

#: The six facilities this warehouse actually holds. Named here so the test
#: fails loudly if the fixture data changes under it rather than passing for
#: the wrong reason.
REAL_FACILITY = "Northgate Regional Hospital"
PHANTOM_FACILITY = "Summit Peak"


def _clarification(*options: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent_summary": "medical-necessity denials at a facility",
        "metric_ids": [],
        "dimension_ids": [],
        "concept_ids": [],
        "playbook_id": None,
        "window": None,
        "basis": None,
        "comparison": None,
        "scope": [],
        "clarification": "Which facility did you mean?",
        "clarification_options": list(options),
        "definitional_terms": [],
    }


def _facility_option(label: str, value: str) -> dict[str, Any]:
    """A grounded option: a real metric, a real dimension, and a VALUE."""
    return {
        "label": label,
        "metric_ids": ["denied_dollars"],
        "dimension_ids": ["facility"],
        "playbook_id": None,
        "scope": [{"dimension": "facility", "op": "eq", "values": [value]}],
    }


def _engine(*options: dict[str, Any]) -> WiredEngine:
    llm = MockLanguageModel()
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.99},
    )
    llm.respond("interpret_question", _clarification(*options))
    return build_duckdb_engine(warehouse_path=WAREHOUSE, llm=llm)


async def _turn(engine: WiredEngine) -> Any:
    return await engine.submit.submit(
        SubmitTurnRequest(tenant="demo", question=QUESTION)
    )


class TestPhantomValuesAreDroppedBeforeTheyAreOffered:
    async def test_an_option_naming_a_facility_this_data_lacks_is_dropped(self) -> None:
        """A real metric, a real dimension, and a facility that does not
        exist. ``facility`` is an OPEN dimension — no declared
        ``value_domain`` — which is precisely the case the old guard
        skipped."""
        outcome = await _turn(
            _engine(
                _facility_option(
                    f"{PHANTOM_FACILITY} is a facility — walk through the spike there",
                    PHANTOM_FACILITY,
                ),
                _facility_option(
                    f"Walk through the spike at {REAL_FACILITY}", REAL_FACILITY
                ),
            )
        )
        # The drop is the point: the phantom is gone before anything renders
        # it. The lone survivor is stated, not answered on the analyst's
        # behalf — a collapse to one option is a fact about this warehouse,
        # not a choice the analyst made, and treating it as one is how "Give
        # me a payer scorecard for July 2026" came back as one payer's A/R
        # with the refusal demoted into a warning.
        assert outcome.clarification is not None
        assert outcome.clarification.options == (
            f"Walk through the spike at {REAL_FACILITY}",
        )
        assert PHANTOM_FACILITY not in outcome.clarification.question.split("survives")[-1]
        assert "CLARIFICATION_SOLE_SURVIVOR" in (outcome.clarification.reason or "")
        assert not [
            w for w in outcome.warnings if w.startswith("clarification_answer_applied:")
        ]

    async def test_a_real_value_survives_untouched(self) -> None:
        """The check must not cost the analyst the options that are right:
        dropping the twelve real payers to catch one phantom would be a
        worse product than the defect."""
        outcome = await _turn(
            _engine(
                _facility_option(
                    f"Walk through the spike at {REAL_FACILITY}", REAL_FACILITY
                )
            )
        )
        assert outcome.clarification is not None
        assert any(REAL_FACILITY in option for option in outcome.clarification.options)

    async def test_dropping_every_option_says_so_rather_than_shipping_a_blank_row(
        self,
    ) -> None:
        """``ClarificationPrompt`` maps over the options array; an empty one
        renders as a question above a blank row of buttons. The question
        states the impasse instead."""
        outcome = await _turn(
            _engine(
                _facility_option("Try Summit Peak", PHANTOM_FACILITY),
                _facility_option("Or Summit Peak Campus", "Summit Peak Campus"),
            )
        )
        assert outcome.clarification is not None
        assert outcome.clarification.options == ()
        assert "dropped all 2 of them" in outcome.clarification.question
        assert "CLARIFICATION_OPTIONS_UNANSWERABLE" in (
            outcome.clarification.reason or ""
        )

    async def test_an_option_whose_cut_the_contract_refuses_is_dropped(self) -> None:
        """A guard on the half that already worked. ``denial_rate`` is a
        ratio and the pack does not declare ``carc_code`` as a legal cut for
        it; the ratio-grain rule in ``_option_resolves`` catches that today,
        and the dry planner run is the net beneath it for the cuts that rule
        cannot see. Pinned because the two checks now run in sequence and a
        regression in either would be invisible from the other's tests."""
        outcome = await _turn(
            _engine(
                {
                    "label": "Break denial rate down by CARC code",
                    "metric_ids": ["denial_rate"],
                    "dimension_ids": ["carc_code"],
                    "playbook_id": None,
                    "scope": [],
                },
                _facility_option(
                    f"Walk through the spike at {REAL_FACILITY}", REAL_FACILITY
                ),
            )
        )
        # The illegal cut is dropped (the point of this test) and the
        # survivor is STATED rather than run on the analyst's behalf (see the
        # sibling test above).
        assert outcome.clarification is not None
        assert outcome.clarification.options == (
            f"Walk through the spike at {REAL_FACILITY}",
        )
        assert "CARC code" not in " ".join(outcome.clarification.options)
        assert "CLARIFICATION_SOLE_SURVIVOR" in (outcome.clarification.reason or "")


class TestAValueExistenceRefusalKeepsItsCandidates:
    """The twelve real payers are the whole answer to "there is no payer
    named UnitedHealthcare". They are read out of the warehouse's own
    domain one step earlier and bound as ``predicate_value`` options — and
    every step of the offer funnel runs over them afterwards. An option the
    validator itself derived from live data is not a guess to be re-checked
    away: dropping them leaves a question that enumerates twelve values in
    its prose above a blank row of buttons."""

    async def test_the_candidate_values_reach_the_wire_as_options(self) -> None:
        llm = MockLanguageModel()
        llm.respond(
            "classify_turn",
            {"turn_class": "new_investigation", "confidence": 0.95,
             "clarification_question": None},
        )
        llm.respond(
            "interpret_question",
            {
                "intent_summary": "denial rate for one payer",
                "metric_ids": ["denial_rate"],
                "dimension_ids": [],
                "concept_ids": [],
                "playbook_id": None,
                "window": {"quantity": "1", "unit": "month", "mode": "full_periods"},
                "basis": None,
                "comparison": None,
                "scope": [
                    {"dimension": "payer", "op": "eq", "values": ["UnitedHealthcare"]}
                ],
                "clarification": None,
                "definitional_terms": [],
            },
        )
        engine = build_duckdb_engine(warehouse_path=WAREHOUSE, llm=llm)

        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="what is the denial rate for UnitedHealthcare?"
            )
        )

        clarification = outcome.clarification
        assert clarification is not None
        assert clarification.options, "the candidate values were computed and discarded"
        # Every offered option is a real payer, and each carries the binding
        # that makes it tappable rather than re-readable as free text.
        assert len(clarification.options) >= 2
        for option in clarification.options:
            assert option in clarification.question
            binding = clarification.binding_for(option)
            assert binding is not None and binding.kind == "predicate_value"
            assert binding.scope == (("payer", (option,)),)
