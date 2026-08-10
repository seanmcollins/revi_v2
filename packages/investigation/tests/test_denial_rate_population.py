"""denial_rate v2: the population is adjudicated claims, and it says so.

v1 counted every un-adjudicated claim as a denial: the numerator reads
``clean_claim = false`` and ``clean_claim`` is ``paid AND NOT denied``, so a
never-remitted claim reads false for want of evidence. All 11,319 OPEN claims
here carry **zero** denial records, yet State Medicaid published at 49.94% —
nearly ten times the true incidence — graded ``direct``/``high``, with the
contract's own population caveat unread in a description. Both halves are pinned
here: ``status`` is a certified dimension, so ``exclusions: {status eq OPEN}``
removes OPEN from both sides of the ratio, and any contract declaring a
``Population caveat:`` publishes it as a warning on every answer that reads the
metric. Expected numbers are cross-checked against direct SQL over
``snap_003.v_claim``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import AbsoluteWindowModel
from revi_testing.engine_wiring import build_duckdb_engine
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

WINDOW_START, WINDOW_END = "2026-05-01", "2026-08-02"

#: SELECT payer_name,
#:        count(*) FILTER (WHERE status <> 'OPEN' AND NOT clean_claim),
#:        count(*) FILTER (WHERE status <> 'OPEN')
#:   FROM snap_003.v_claim
#:  WHERE service_date BETWEEN '2026-05-01' AND '2026-08-02'
#:  GROUP BY 1;
EXPECTED: dict[str, tuple[int, int]] = {
    "Silverline Medicare Advantage": (159, 1080),
    "Summit Peak Medicare Advantage": (74, 594),
    "Lakewood Medicaid MCO": (63, 528),
    "Pinnacle Health Plan": (72, 672),
    "Bluestone Mutual": (94, 893),
    "State Medicaid MCO": (68, 674),
    "Meridian Health": (138, 1382),
    "Veritas Comp Fund": (25, 258),
    "State Medicaid": (175, 1864),
    "Northbridge Commercial": (83, 1103),
    "Atlas Commercial": (180, 2684),
    "Federal Medicare": (81, 1668),
}


@pytest.fixture(scope="module")
async def outcome() -> TurnOutcome:
    engine = build_duckdb_engine(warehouse_path=WAREHOUSE, llm=MockLanguageModel())
    return await engine.submit.submit(
        SubmitTurnRequest(
            tenant="demo",
            question="denial rate by payer",
            spec=TypedInvestigationSpec(
                metric_ids=["denial_rate"],
                dimensions=["payer"],
                window=AbsoluteWindowModel(start=WINDOW_START, end=WINDOW_END),
                basis="service",
            ),
        )
    )


def _by_payer(outcome: TurnOutcome) -> dict[str, tuple[int, int, Decimal]]:
    frame = next(f for fid, f in outcome.frames if fid == "main")
    payer = frame.schema.index_of("payer")
    num = frame.schema.index_of("denial_rate__num")
    den = frame.schema.index_of("denial_rate__den")
    rate = frame.schema.index_of("denial_rate")
    return {str(row[payer]): (row[num], row[den], row[rate]) for row in frame.rows}  # type: ignore[misc]


class TestAdjudicatedPopulation:
    async def test_every_payer_matches_direct_sql(self, outcome: TurnOutcome) -> None:
        actual = {payer: (n, d) for payer, (n, d, _) in _by_payer(outcome).items()}
        assert actual == EXPECTED

    async def test_the_headline_number_the_review_caught(self, outcome: TurnOutcome) -> None:
        """State Medicaid: published 49.94%, true 9.39% (175/1,864)."""
        num, den, rate = _by_payer(outcome)["State Medicaid"]
        assert (num, den) == (175, 1864)
        assert round(float(rate), 4) == 0.0939
        assert float(rate) < 0.10, "v1 published 0.499407 over the same window"

    async def test_the_payer_ranking_no_longer_inverts(self, outcome: TurnOutcome) -> None:
        """v1 ranked State Medicaid the single worst payer. On the corrected
        population it is 9th of 12 — better than the median — and Silverline
        Medicare Advantage, which v1 ranked mid-table, is genuinely worst."""
        ordered = sorted(_by_payer(outcome).items(), key=lambda kv: -float(kv[1][2]))
        names = [payer for payer, _ in ordered]
        assert names[0] == "Silverline Medicare Advantage"
        assert names.index("State Medicaid") >= 8

    async def test_open_claims_are_out_of_both_sides(self, outcome: TurnOutcome) -> None:
        """Symmetry is the whole point of an exclusion: 13,400 adjudicated
        claims against 18,410 in the window, and the 5,010 removed carry no
        denial records at all, so the numerator loses nothing real."""
        totals = _by_payer(outcome)
        assert sum(den for _, den, _ in totals.values()) == 13_400
        assert sum(num for num, _, _ in totals.values()) == 1_212


class TestPopulationCaveatIsPublished:
    async def test_the_contracts_caveat_reaches_the_answer(self, outcome: TurnOutcome) -> None:
        """The structural rule. v1's caveat existed, was correct, and never
        left the pack — the response's warnings carried only basis and
        suppression notes."""
        caveats = [w for w in outcome.warnings if w.startswith("population_caveat: denial_rate")]
        assert len(caveats) == 1, outcome.warnings
        assert "status OPEN" in caveats[0]
        assert "excluded from both sides" in caveats[0]

    async def test_the_caveat_is_not_duplicated_per_probe(self, outcome: TurnOutcome) -> None:
        assert len([w for w in outcome.warnings if w.startswith("population_caveat:")]) == 1
