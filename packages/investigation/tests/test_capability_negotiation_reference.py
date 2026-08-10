"""Capability negotiation on the product path, against the real warehouse.

``test_validation.py`` holds the negotiation itself; this module holds the gap it
was built to fix. Nine shipped metric contracts executed correctly at the source
yet were refused on the product path with ``UNSUPPORTED_CONCEPT``, because §6.6
decided answerability from the catalog plus one hardcoded field name. These tests
take the whole route a user takes — typed turn → interpretation → planning → §6.6
validation → execution — and require a number to come back; anything a source
does not advertise keeps the old refusal text. ``denial_rate`` on its unbound
primary ``remit`` basis is a labeled substitution rather than a refusal (§5.3);
see ``TestBasisFallback``.
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

#: The three playbook probe groups §6.6 pruned, and their playbooks.
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


class TestBasisFallback:
    async def test_denial_rate_falls_back_from_its_unbound_primary_basis(
        self, engine: WiredEngine
    ) -> None:
        """The catalog gap is answered on a declared alternate, and said so.

        ``remit`` is denial_rate's primary basis and this warehouse binds
        it on the remit/transaction/denial views only — not on claim, the
        contract's own grain. That used to be a refusal raised by the SQL
        compiler, one layer past §6.6. §5.3 already provides for it: an
        allowed alternate may be used, *labeled in output and provenance*.
        So the probe reads ``service``, the header says ``service``, and
        the warning names both bases and the grain that forced the swap.
        """
        outcome = await _typed_turn(engine, "denial_rate", basis="remit")
        assert outcome.clarification is None
        assert outcome.header is not None
        assert outcome.header.basis == "service"
        [(_, frame)] = [(name, f) for name, f in outcome.frames if name == "main"]
        assert frame.rows
        [warning] = [w for w in outcome.warnings if w.startswith("alternate_basis_used:")]
        assert "'service'" in warning
        assert "'remit'" in warning
        assert "'claim'" in warning

    async def test_the_fallback_never_leaves_the_contract(
        self, engine: WiredEngine
    ) -> None:
        """A basis the contract forbids is still ``DATE_BASIS_INVALID``.

        The fallback chooses only among ``allowed_date_bases``; it is a
        binding decision, not a licence to date a metric however the
        warehouse finds convenient. ``post`` is not an allowed basis for
        denial_rate, and asking for it is refused as it always was."""
        with pytest.raises(DateBasisInvalidError) as excinfo:
            await _typed_turn(engine, "denial_rate", basis="post")
        assert "post" in str(excinfo.value)

    async def test_denial_rate_answers_on_an_explicitly_allowed_basis(
        self, engine: WiredEngine
    ) -> None:
        """Asking for the alternate outright reads it, and is labeled the same.

        The label is a property of the *answer*, not of who picked the
        basis: a reader comparing this number against a published denial
        rate needs to know it is not the MAP AR-5 remittance-dated one,
        whether the substitution happened silently upstream or because
        they typed ``service`` themselves.
        """
        outcome = await _typed_turn(engine, "denial_rate", basis="service")
        assert outcome.clarification is None
        [(_, frame)] = [(name, f) for name, f in outcome.frames if name == "main"]
        assert frame.rows
        assert any(w.startswith("alternate_basis_used:") for w in outcome.warnings)
