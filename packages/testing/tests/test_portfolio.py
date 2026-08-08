"""The anomaly portfolio: governed priority formula, actionability rules,
and the DuckDB anomaly source (fixture table + graceful absence)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from revi_api.actionability import assess, load_actionability_rules
from revi_api.portfolio import (
    PRIORITY_FORMULA_VERSION,
    PriorityPolicy,
    build_portfolio,
    priority_policy_from_pack,
)
from revi_connector_duckdb import DuckDbAnomalySource
from revi_investigation.application.ports import AnomalyRecord
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import load_base_pack

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "packs" / "base-rcm" / "anomaly_actionability.yaml"
REFERENCE_DB = REPO_ROOT / "data" / "revi_warehouse.duckdb"

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _record(
    anomaly_id: str,
    category: str,
    impact_cents: int,
    *,
    detected_at: datetime = datetime(2026, 8, 1, 6, 0),
    evidence: dict[str, Any] | None = None,
    status: str = "OPEN",
) -> AnomalyRecord:
    return AnomalyRecord(
        anomaly_id=anomaly_id,
        detected_at=detected_at,
        category=category,
        title=f"{category} anomaly {anomaly_id}",
        description="planted",
        metric_id="cash_posted",
        dimensions=(("payer", "State Medicaid"),),
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 31),
        impact_cents=impact_cents,
        severity="high",
        confidence="high",
        status=status,
        evidence=evidence or {},
    )


@pytest.fixture(scope="module")
def rules():
    return load_actionability_rules(RULES_PATH)


@pytest.fixture(scope="module")
def policy() -> PriorityPolicy:
    return priority_policy_from_pack(load_base_pack())


class TestActionabilityRules:
    def test_governed_params_come_from_the_pack(self, policy: PriorityPolicy) -> None:
        assert policy.impact_weight == Decimal("0.25")
        assert policy.actionability_weight == Decimal("0.60")
        assert policy.half_life_days == Decimal(14)
        assert policy.compliance_floor == Decimal("0.60")

    def test_timely_filing_recoverable_follows_open_share(self, rules) -> None:
        rule = rules.rule_for("TIMELY_FILING")
        all_expired = assess(rule, _record("a", "TIMELY_FILING", 1_000_000,
                                           evidence={"open_claims": 0, "expired_claims": 40}))
        assert all_expired.recoverable_cents == 0
        assert all_expired.label == "not recoverable"
        half_open = assess(rule, _record("b", "TIMELY_FILING", 1_000_000,
                                         evidence={"open_claims": 20, "expired_claims": 20}))
        assert half_open.recoverable_cents == 500_000

    def test_unknown_category_uses_default_rule(self, rules) -> None:
        assessment = assess(rules.rule_for("SOMETHING_NEW"), _record("c", "SOMETHING_NEW", 100))
        assert assessment.recoverable_cents == 50
        assert assessment.rationale  # every rule carries a citable rationale


class TestPriorityFormula:
    def test_dead_timely_filing_ranks_below_recoverable_underpayment(
        self, rules, policy: PriorityPolicy
    ) -> None:
        big_but_dead = _record(
            "tf", "TIMELY_FILING", 5_000_000,
            evidence={"open_claims": 0, "expired_claims": 100},
        )
        smaller_but_live = _record("up", "UNDERPAYMENT", 2_000_000)
        portfolio = build_portfolio(
            (big_but_dead, smaller_but_live), watermark=WATERMARK, policy=policy, rules=rules
        )
        assert [c.anomaly_id for c in portfolio.items] == ["up", "tf"]
        up_card, tf_card = portfolio.items
        assert up_card.recoverable_cents_estimate == 1_700_000  # 85% dispute path
        assert tf_card.recoverable_cents_estimate == 0
        assert tf_card.actionability_label == "not recoverable"
        assert portfolio.formula_version == PRIORITY_FORMULA_VERSION

    def test_tiny_credit_balance_floors_above_noise(
        self, rules, policy: PriorityPolicy
    ) -> None:
        tiny_compliance = _record("cb", "CREDIT_BALANCE", 40_000)
        big_noise = _record(
            "noise", "CONTRACTUAL", 9_000_000,
            detected_at=datetime(2026, 6, 1, 6, 0),  # old, and working-as-designed
        )
        portfolio = build_portfolio(
            (tiny_compliance, big_noise), watermark=WATERMARK, policy=policy, rules=rules
        )
        assert portfolio.items[0].anomaly_id == "cb"
        assert portfolio.items[0].compliance_floor_applied is True
        assert portfolio.items[0].priority_score >= float(policy.compliance_floor)

    def test_recency_decay_separates_twin_impacts(
        self, rules, policy: PriorityPolicy
    ) -> None:
        fresh = _record("fresh", "UNDERPAYMENT", 1_000_000,
                        detected_at=datetime(2026, 8, 2, 6, 0))
        stale = _record("stale", "UNDERPAYMENT", 1_000_000,
                        detected_at=datetime(2026, 6, 1, 6, 0))
        portfolio = build_portfolio(
            (fresh, stale), watermark=WATERMARK, policy=policy, rules=rules
        )
        assert [c.anomaly_id for c in portfolio.items] == ["fresh", "stale"]
        assert portfolio.items[0].age_days < portfolio.items[1].age_days

    def test_onset_from_evidence_overrides_detected_at(
        self, rules, policy: PriorityPolicy
    ) -> None:
        record = _record("o", "UNDERPAYMENT", 100, evidence={"onset_date": "2026-07-04"})
        portfolio = build_portfolio((record,), watermark=WATERMARK, policy=policy, rules=rules)
        assert portfolio.items[0].age_days == 30

    def test_resolved_records_are_excluded(self, rules, policy: PriorityPolicy) -> None:
        records = (
            _record("open", "UNDERPAYMENT", 100),
            _record("done", "UNDERPAYMENT", 100, status="SELF_RESOLVED"),
        )
        portfolio = build_portfolio(records, watermark=WATERMARK, policy=policy, rules=rules)
        assert [c.anomaly_id for c in portfolio.items] == ["open"]

    def test_cards_carry_decomposed_components_and_drill_handles(
        self, rules, policy: PriorityPolicy
    ) -> None:
        portfolio = build_portfolio(
            (_record("a", "UNDERPAYMENT", 1_000_000),),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
        )
        [card] = portfolio.items
        assert card.impact_cents == 1_000_000
        assert card.recoverable_cents_estimate == 850_000
        assert card.actionability_rationale
        assert card.priority_score > 0
        [drill] = card.drill_filters
        assert drill.op == "add_filter" and drill.dimension == "payer"
        assert drill.values == ["State Medicaid"]
        assert card.drill_window is not None
        assert card.drill_window.start == date(2026, 7, 1)

    def test_empty_population_is_an_empty_portfolio_with_warning(
        self, rules, policy: PriorityPolicy
    ) -> None:
        portfolio = build_portfolio((), watermark=WATERMARK, policy=policy, rules=rules)
        assert portfolio.status == "empty" and portfolio.items == []
        assert any("detection feed" in w for w in portfolio.warnings)


@pytest.fixture(scope="module")
def warehouse_with_anomalies(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from revi_warehouse.config import GeneratorConfig
    from revi_warehouse.generate import run_generation

    out = tmp_path_factory.mktemp("wh") / "anomalies.duckdb"
    path = run_generation(GeneratorConfig.small(), out).db_path
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS snap_003.detected_anomalies (
                anomaly_id VARCHAR, detected_at TIMESTAMP, category VARCHAR,
                title VARCHAR, description VARCHAR, metric_id VARCHAR,
                dimensions VARCHAR, window_start DATE, window_end DATE,
                impact_cents BIGINT, severity VARCHAR, confidence VARCHAR,
                status VARCHAR, evidence VARCHAR
            )
            """
        )
        con.execute(
            "INSERT INTO snap_003.detected_anomalies VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                "anom_001",
                datetime(2026, 8, 1, 5, 0),
                "UNDERPAYMENT",
                "Northbridge paying below contract",
                "planted",
                "underpayment_variance",
                json.dumps({"payer": "Northbridge Commercial"}),
                date(2026, 5, 1),
                date(2026, 7, 31),
                2_500_000,
                "high",
                "high",
                "OPEN",
                json.dumps({"onset_date": "2026-05-01"}),
            ],
        )
    finally:
        con.close()
    return path

    async def test_reads_fixture_rows(self, warehouse_with_anomalies: Path) -> None:
        source = DuckDbAnomalySource(warehouse_with_anomalies)
        repo_watermarks = duckdb.connect(str(warehouse_with_anomalies), read_only=True)
        try:
            rows = repo_watermarks.execute(
                "SELECT watermark_id, loaded_at, newest_data_date FROM main.watermarks "
                "ORDER BY loaded_at"
            ).fetchall()
        finally:
            repo_watermarks.close()
        newest = DataWatermark(id=rows[-1][0], loaded_at=rows[-1][1], newest_data_date=rows[-1][2])
        records = await source.list_anomalies(newest)
        assert len(records) == 1
        [record] = records
        assert record.anomaly_id == "anom_001"
        assert record.dimensions == (("payer", "Northbridge Commercial"),)
        assert record.evidence["onset_date"] == "2026-05-01"
        # earlier snapshots have no table → empty, not an error
        earlier = DataWatermark(id=rows[0][0], loaded_at=rows[0][1], newest_data_date=rows[0][2])
        assert await source.list_anomalies(earlier) == ()

    @pytest.mark.reference
    async def test_real_population_when_the_generator_change_lands(self) -> None:
        if not REFERENCE_DB.is_file():
            pytest.skip("generated warehouse missing")
        source = DuckDbAnomalySource(REFERENCE_DB)
        con = duckdb.connect(str(REFERENCE_DB), read_only=True)
        try:
            rows = con.execute(
                "SELECT watermark_id, loaded_at, newest_data_date FROM main.watermarks "
                "ORDER BY loaded_at"
            ).fetchall()
        finally:
            con.close()
        newest = DataWatermark(id=rows[-1][0], loaded_at=rows[-1][1], newest_data_date=rows[-1][2])
        records = await source.list_anomalies(newest)
        if not records:
            pytest.skip("detected_anomalies not yet generated — concurrent workstream")
        portfolio = build_portfolio(
            records,
            watermark=newest,
            policy=priority_policy_from_pack(load_base_pack()),
            rules=load_actionability_rules(RULES_PATH),
        )
        assert portfolio.status == "ok"
        assert len(portfolio.items) >= 10  # the ~30-40 population, minus resolved
        scores = [c.priority_score for c in portfolio.items]
        assert scores == sorted(scores, reverse=True)
        assert all(c.actionability_rationale for c in portfolio.items)
