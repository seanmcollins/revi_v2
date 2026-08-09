"""Every option this platform offers is answerable (round-3 R3-17).

Four personas. vc-investor T11 was offered *"Summit Peak is a facility —
walk through the medical-necessity denial spike in cardiology at that
facility"*; ``snap_003.dim_facility`` holds exactly six facilities and Summit
Peak is not one of them, so the option was an $0.1428 invitation to a
refusal, plus another turn to discover it was empty. rcm-analyst replayed a
product-offered option verbatim and got ``outcome: answer``, 0 findings, and
*"8 row(s) were retrieved across 6 frame(s) and no finding could be
published from them"*.

The review noted the shape of the leak precisely: **the value-validation
path is excellent** — "There is no payer named UnitedHealthcare in this
data", all twelve real values enumerated, ``PREDICATE_VALUE_UNMATCHED`` —
and simply was not applied to model-authored options. Two holes, both
closed here:

* ``_option_resolves`` checked a scope value against a dimension's
  DECLARED ``value_domain`` and ``continue``\\ d when there was none, which
  is every OPEN dimension: ``payer``, ``plan``, ``facility``. Summit Peak
  walked through that ``continue``.
* nothing dry-ran an option against the planner, so an option naming a
  legal metric and an illegal cut for it survived to be tapped.

Both are closed by running the option the way the turn that accepts it will
run it, against this warehouse, at this watermark.
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
        """The exact vc-investor T11 shape: a real metric, a real dimension,
        and a facility that does not exist. ``facility`` is an OPEN
        dimension — no declared ``value_domain`` — which is precisely the
        case the old guard skipped."""
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
        assert outcome.clarification is not None
        offered = outcome.clarification.options
        assert not any(PHANTOM_FACILITY in option for option in offered)
        assert any(REAL_FACILITY in option for option in offered)

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
        renders as a question above a blank row of buttons, which is what
        rcm-analyst saw. The question states the impasse instead."""
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
        assert outcome.clarification is not None
        assert not any("CARC code" in o for o in outcome.clarification.options)
        assert any(REAL_FACILITY in o for o in outcome.clarification.options)
