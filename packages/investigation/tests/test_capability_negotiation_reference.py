"""Capability negotiation on the product path, against the real warehouse.

The unit tests in ``test_validation.py`` hold the negotiation itself: what
a source advertises is asked for, what it does not is refused. This module
holds the thing the negotiation was built to fix — the gap between what
the DuckDB source can execute and what a *question* can reach.

Nine shipped metric contracts executed correctly at the source and were
refused on the product path with ``UNSUPPORTED_CONCEPT: no probe in the
plan is answerable at the source``, because §6.6 decided answerability
from the catalog plus one hardcoded field name. So these tests take the
whole route a user takes — typed first turn → interpretation → planning →
§6.6 validation → execution against ``data/revi_warehouse.duckdb`` — and
require a number to come back.

Two refusals are pinned as *still* refused, because neither is a
capability gap and neither should be routed around by this work:

- ``denial_rate`` on its primary ``remit`` basis, which is unbound at the
  claim entity: a catalog modelling decision (rebind to denial grain, or
  bind REMIT on claim), recorded in ``packs/base-rcm/NOTES.md``.
- Anything a source does not advertise, which keeps the old refusal text.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import AbsoluteWindowModel
from revi_kernel.errors import DateBasisInvalidError
from revi_testing.engine_wiring import WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

if TYPE_CHECKING:
    from conftest import SpecFactory

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

#: The nine §6.6 refused and the source executed, by what unblocks them.
DERIVED_MEASURE_CONTRACTS = (
    "avg_days_to_pay",  # payment_lag_days
    "bill_lag_days",  # submission_lag_days
    "charge_lag_days",  # charge_entry_lag_days
    "late_charge_pct",  # late_charge_cents
    "underpayment_variance",  # underpayment_cents
    "days_in_ar",  # ar_age_days_billed_cents (snapshot)
    "credit_balance_dollars",  # credit_balance_cents (snapshot)
)
CROSS_ENTITY_CONTRACTS = ("net_collection_rate", "gross_collection_rate")

#: The three wave-2 playbook probe groups §6.6 pruned, and their playbooks.
PRUNED_PROBE_GROUPS = (
    ("charge_capture_review", "charge_timing"),
    ("clean_claim_review", "bill_timing"),
    ("credit_balance_review", "credit_standing"),
)


@pytest.fixture(scope="module")
def engine() -> WiredEngine:
    return build_duckdb_engine(warehouse_path=WAREHOUSE, llm=MockLanguageModel())


async def _typed_turn(engine: WiredEngine, metric_id: str, *, basis: str | None) -> TurnOutcome:
    """One metric, asked the way a portfolio card or a chart click asks:
    typed in, typed out, zero model calls."""
    watermark = (await engine.repository.list_watermarks())[-1]
    end = watermark.newest_data_date
    return await engine.submit.submit(
        SubmitTurnRequest(
            tenant="demo",
            question="",
            spec=TypedInvestigationSpec(
                metric_ids=[metric_id],
                window=AbsoluteWindowModel(start=end - timedelta(days=93), end=end),
                basis=basis,
            ),
        )
    )


class TestPreviouslyUnreachableContracts:
    @pytest.mark.parametrize("metric_id", DERIVED_MEASURE_CONTRACTS)
    async def test_a_derived_measure_contract_answers_end_to_end(
        self, engine: WiredEngine, metric_id: str
    ) -> None:
        """Executed, not merely validated: the frame carries the number."""
        outcome = await _typed_turn(engine, metric_id, basis=None)
        assert outcome.clarification is None
        assert outcome.frames, f"{metric_id} planned and validated but retrieved nothing"
        [(_, frame)] = [(name, f) for name, f in outcome.frames if name == "main"]
        assert frame.rows, f"{metric_id} executed and returned no rows"
        assert not any("not answerable at the" in w for w in outcome.warnings)

    @pytest.mark.parametrize(
        ("metric_id", "expected"),
        [
            # Appendix B of packs/base-rcm/NOTES.md, measured at the SOURCE.
            # Reaching the identical pair through a question is the point:
            # the product path was not returning a different number, it was
            # returning no number at all.
            ("net_collection_rate", (1_494_532_901, 2_623_183_106)),
            ("gross_collection_rate", (1_494_532_901, 5_642_309_382)),
        ],
    )
    async def test_a_cross_entity_ratio_answers_end_to_end(
        self, engine: WiredEngine, metric_id: str, expected: tuple[int, int]
    ) -> None:
        """Numerator at the transaction grain, denominator at the claim
        grain, one probe — and the kernel, not the adapter, divides."""
        outcome = await _typed_turn(engine, metric_id, basis="service")
        assert outcome.clarification is None
        [(_, frame)] = [(name, f) for name, f in outcome.frames if name == "main"]
        assert frame.schema.names == (f"{metric_id}__num", f"{metric_id}__den", metric_id)
        numerator, denominator, ratio = frame.rows[0]
        assert (numerator, denominator) == expected
        assert ratio == round(Decimal(numerator) / Decimal(denominator), 6)

    async def test_the_three_pruned_probe_groups_survive_planning(
        self, engine: WiredEngine, make_spec: SpecFactory
    ) -> None:
        """``charge_timing``, ``bill_timing`` and ``credit_standing`` each
        sum a derived measure; each executed at the source and never
        reached it through a question."""
        spec = make_spec(dimensions=("payer",))
        for playbook_id, probe_id in PRUNED_PROBE_GROUPS:
            plan = engine.planner.build(spec, playbook_id=playbook_id, window_explicit=True)
            assert probe_id in {node.id for node in plan.nodes}, playbook_id
            validated = engine.validator.validate(plan, spec)
            assert probe_id in {node.id for node in validated.plan.nodes}, (
                f"{playbook_id}/{probe_id} was pruned by §6.6"
            )


class TestStillRefused:
    async def test_denial_rate_on_its_primary_basis_is_still_refused(
        self, engine: WiredEngine
    ) -> None:
        """The one true holdout, and a capability declaration must not
        launder it: ``remit`` is not bound at the claim entity, so the
        probe cannot be dated. A catalog gap, refused as one."""
        with pytest.raises(DateBasisInvalidError) as excinfo:
            await _typed_turn(engine, "denial_rate", basis="remit")
        assert "remit" in str(excinfo.value)

    async def test_denial_rate_still_answers_on_an_allowed_basis(
        self, engine: WiredEngine
    ) -> None:
        """...and the refusal is basis-specific, not contract-wide."""
        outcome = await _typed_turn(engine, "denial_rate", basis="service")
        assert outcome.clarification is None
        [(_, frame)] = [(name, f) for name, f in outcome.frames if name == "main"]
        assert frame.rows
