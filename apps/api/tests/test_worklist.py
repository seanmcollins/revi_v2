"""The conversation's read path onto the ranked worklist (deferred P1).

"What should my denial team work first this week to recover the most
cash?" returned a clarification offering four ranking bases, none of which
was the 33-card worklist the platform had already ranked, reconciled and
priced. These tests pin the two halves of the fix: the routing is GOVERNED
CONTENT (a playbook id and a concept id in the pack, never a question
string), and the cards published are the portfolio's own — same order,
same decomposition, same reconciliation state, same warnings.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from revi_api.actionability import load_actionability_rules
from revi_api.portfolio import PriorityPolicy, build_portfolio
from revi_api.rederive import ReDerivedImpact
from revi_api.warning_codes import classify
from revi_api.worklist import (
    WORKLIST_FILENAME,
    build_worklist,
    load_worklist_routing,
    worklist_warning,
)
from revi_investigation.application.ports import AnomalyRecord
from revi_investigation_contracts.api import WorklistQuery
from revi_kernel.watermark import DataWatermark

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_DIR = REPO_ROOT / "packs" / "base-rcm"
RULES_PATH = PACK_DIR / "anomaly_actionability.yaml"

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


@pytest.fixture(scope="module")
def routing():
    return load_worklist_routing(PACK_DIR / WORKLIST_FILENAME)


@pytest.fixture(scope="module")
def rules():
    return load_actionability_rules(RULES_PATH)


def _record(anomaly_id: str, category: str, cents: int) -> AnomalyRecord:
    return AnomalyRecord(
        anomaly_id=anomaly_id,
        detected_at=datetime(2026, 8, 2, 6, 0),
        category=category,
        title=f"{category} {anomaly_id}",
        description="planted",
        metric_id="denied_dollars",
        dimensions=(("payer", "Atlas Commercial"),),
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 31),
        impact_cents=cents,
        severity="high",
        confidence="high",
        status="OPEN",
        evidence={},
    )


def _portfolio(rules, records, rederived=None):
    return build_portfolio(
        records,
        watermark=WATERMARK,
        policy=PriorityPolicy(),
        rules=rules,
        tenant="demo",
        rederived=rederived,
    )


class TestGovernedRouting:
    def test_the_pack_declares_the_routing_and_nothing_else_does(self, routing) -> None:
        """The mapping is content. Not a question string, anywhere."""
        assert routing.enabled
        assert "daily_portfolio" in routing.playbook_ids
        assert "work_prioritization" in routing.concept_ids
        assert routing.content_hash  # governed content is hashed like the rest
        source = (PACK_DIR / WORKLIST_FILENAME).read_text(encoding="utf-8")
        assert "what should" not in source.lower().split("#")[0]

    def test_a_matching_playbook_routes_the_worklist(self, routing) -> None:
        assert routing.match(playbook_id="daily_portfolio", concepts=()) == (
            "playbook",
            "daily_portfolio",
        )

    def test_a_matching_concept_routes_the_worklist(self, routing) -> None:
        assert routing.match(
            playbook_id="denial_spike", concepts=("denial", "work_prioritization")
        ) == ("concept", "work_prioritization")

    def test_an_ordinary_turn_routes_nothing(self, routing) -> None:
        assert routing.match(playbook_id="denial_spike", concepts=("denial",)) is None
        assert routing.match(playbook_id=None, concepts=()) is None

    def test_a_pack_without_the_file_attaches_no_worklist(self, tmp_path: Path) -> None:
        empty = load_worklist_routing(tmp_path / "absent.yaml")
        assert not empty.enabled
        assert empty.match(playbook_id="daily_portfolio", concepts=()) is None

    def test_a_malformed_file_is_refused(self, tmp_path: Path) -> None:
        bad = tmp_path / "worklist.yaml"
        bad.write_text("playbook_ids: 3\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be lists"):
            load_worklist_routing(bad)

    def test_limits_are_bounded_by_the_pack(self, routing) -> None:
        assert routing.bounded_limit(None) == routing.default_limit
        assert routing.bounded_limit(3) == 3
        assert routing.bounded_limit(10_000) == routing.max_limit


class TestWorklistPayload:
    def test_the_cards_are_the_portfolios_own(self, routing, rules) -> None:
        records = tuple(
            _record(f"ANM-{i:03d}", "DENIAL_SPIKE", 1_000_000 - i * 10_000) for i in range(12)
        )
        portfolio = _portfolio(rules, records)
        payload = build_worklist(
            portfolio, routing, matched_on="playbook", matched_id="daily_portfolio"
        )
        # Same objects, same order — never re-ranked here.
        assert [c.anomaly_id for c in payload.items] == [
            c.anomaly_id for c in portfolio.items[: routing.default_limit]
        ]
        assert payload.total_items == len(portfolio.items)
        assert payload.limit == routing.default_limit
        assert payload.formula_version == portfolio.formula_version
        assert payload.watermark_id == "wm_003"
        assert payload.tenant == "demo"
        # The decomposition the rail shows travels with the answer.
        assert payload.items[0].priority.impact_normalizer_cents > 0
        assert payload.items[0].ranked_on in ("detector", "platform", "not_comparable")
        # …and the recoverable total covers the WHOLE population, not the page.
        assert payload.total_recoverable_cents_estimate == sum(
            c.recoverable_cents_estimate for c in portfolio.items
        )
        assert str(len(payload.items)) in payload.statement
        assert portfolio.formula_version in payload.statement

    def test_the_builds_warnings_travel_verbatim(self, routing, rules) -> None:
        """A worklist read into a conversation must not shed disclosures."""
        records = (_record("a", "DENIAL_SPIKE", 2_000_000),)
        portfolio = _portfolio(
            rules,
            records,
            rederived={"a": ReDerivedImpact(cents=3_000_000, measure_id="denied_dollars", rows=2)},
        )
        payload = build_worklist(
            portfolio, routing, matched_on="concept", matched_id="work_prioritization"
        )
        assert payload.warnings == list(portfolio.warnings)
        assert any("re-derived figure" in w for w in payload.warnings)
        assert "ranked on the platform's figure" in payload.statement or any(
            "re-derived figure" in w for w in payload.warnings
        )

    def test_a_typed_query_can_narrow_the_lane_and_the_page(self, routing, rules) -> None:
        records = (
            _record("credit", "CREDIT_BALANCE", 82_437),
            _record("denial", "DENIAL_SPIKE", 1_000_000),
        )
        portfolio = _portfolio(rules, records)
        payload = build_worklist(
            portfolio,
            routing,
            matched_on="typed_query",
            matched_id="",
            query=WorklistQuery(limit=1, lane="compliance"),
        )
        assert [c.anomaly_id for c in payload.items] == ["credit"]
        assert payload.total_items == 1  # the lane's population, not the whole list
        assert "compliance lane" in payload.statement

    def test_an_empty_feed_says_so_rather_than_showing_nothing(
        self, routing, rules
    ) -> None:
        portfolio = _portfolio(rules, ())
        payload = build_worklist(
            portfolio, routing, matched_on="playbook", matched_id="daily_portfolio"
        )
        assert payload.items == []
        assert "no ranked work at this watermark" in payload.statement

    def test_the_attachment_is_disclosed_as_a_classified_warning(
        self, routing, rules
    ) -> None:
        portfolio = _portfolio(rules, (_record("a", "DENIAL_SPIKE", 100_000),))
        payload = build_worklist(
            portfolio, routing, matched_on="playbook", matched_id="daily_portfolio"
        )
        warning = worklist_warning(payload)
        assert classify(warning) == ("WORKLIST_ATTACHED", "info")
        assert "daily_portfolio" in warning
        assert "not findings this turn computed" in warning
