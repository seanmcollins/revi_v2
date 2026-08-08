"""The anomaly portfolio: governed priority formula, actionability rules,
and the DuckDB anomaly source (fixture table + graceful absence)."""

from __future__ import annotations

import json
from dataclasses import replace
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
from revi_kernel.errors import WatermarkStaleError
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


def replace_onsets(
    records: tuple[AnomalyRecord, ...], onset: str
) -> tuple[AnomalyRecord, ...]:
    """The same population with every onset flattened to one date — what
    the portfolio saw before the onset was wired through the source."""
    return tuple(
        replace(r, evidence={**r.evidence, "onset_date": onset}) for r in records
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
        spec = card.drill_spec
        assert spec.metric_ids == ["cash_posted"] and spec.dimensions == ["payer"]
        [drill] = spec.filters
        assert drill.op == "add_filter" and drill.dimension == "payer"
        assert drill.values == ["State Medicaid"]
        assert spec.window.start == date(2026, 7, 1)

    def test_empty_population_is_an_empty_portfolio_with_warning(
        self, rules, policy: PriorityPolicy
    ) -> None:
        portfolio = build_portfolio((), watermark=WATERMARK, policy=policy, rules=rules)
        assert portfolio.status == "empty" and portfolio.items == []
        assert any("detection feed" in w for w in portfolio.warnings)


    def test_onset_separates_records_the_detection_stamp_cannot(
        self, rules, policy: PriorityPolicy
    ) -> None:
        """The regression this file exists to prevent.

        A detection feed stamps ``detected_at`` when it *ran*, so every row
        of a snapshot carries the same stamp; only the onset says when the
        problem began. These two records are identical — same category, same
        impact, same detection stamp — and differ only in onset, so the
        recency term is the entire difference between their scores."""
        fresh = _record("fresh", "UNDERPAYMENT", 1_000_000,
                        evidence={"onset_date": "2026-07-27"})
        old = _record("old", "UNDERPAYMENT", 1_000_000,
                      evidence={"onset_date": "2026-05-04"})
        assert fresh.detected_at == old.detected_at  # the stamp separates nothing
        portfolio = build_portfolio(
            (old, fresh), watermark=WATERMARK, policy=policy, rules=rules
        )
        assert [c.anomaly_id for c in portfolio.items] == ["fresh", "old"]
        assert [c.age_days for c in portfolio.items] == [7, 91]  # 0.5 and 6.5 half-lives
        gap = portfolio.items[0].priority_score - portfolio.items[1].priority_score
        weight_sum = policy.impact_weight + policy.recency_weight + policy.actionability_weight
        expected = float(policy.recency_weight / weight_sum) * (0.5**0.5 - 0.5**6.5)
        assert gap == pytest.approx(expected, abs=1e-6)
        assert gap > 0.10  # a real separation, not a rounding artefact


@pytest.fixture(scope="module")
def anomaly_fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A two-snapshot warehouse in the ``detected_anomalies`` row shape.

    Hand-built rather than generated: these tests are about the source's
    row → record mapping, and the generated population is covered by the
    reference-marked tests below."""
    path = tmp_path_factory.mktemp("anomalies") / "fixture.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE main.watermarks (watermark_id VARCHAR, schema_name VARCHAR, "
            "loaded_at TIMESTAMP, newest_data_date DATE)"
        )
        con.execute(
            "INSERT INTO main.watermarks VALUES (?,?,?,?), (?,?,?,?)",
            [
                "wm_001", "snap_001", datetime(2026, 8, 1, 4, 5), date(2026, 7, 31),
                "wm_003", "snap_003", datetime(2026, 8, 3, 4, 10), date(2026, 8, 2),
            ],
        )
        con.execute("CREATE SCHEMA snap_001")
        con.execute("CREATE SCHEMA snap_003")
        con.execute(
            """
            CREATE TABLE snap_003.detected_anomalies (
                anomaly_id VARCHAR, detected_at TIMESTAMP, category VARCHAR,
                title VARCHAR, description VARCHAR, metric_id VARCHAR,
                dimensions VARCHAR, window_start DATE, window_end DATE,
                impact_cents BIGINT, severity VARCHAR, confidence VARCHAR,
                status VARCHAR, evidence VARCHAR
            )
            """
        )
        rows = [
            # the ordinary case: the feed states no onset, so window_start is it
            (
                "anom_derived", "Northbridge paying below contract",
                json.dumps({"payer": "Northbridge Commercial"}),
                date(2026, 6, 10), date(2026, 7, 31),
                json.dumps({"denied_cents": 2_500_000}),
            ),
            # a feed that knows its own onset: ours must not overwrite it
            (
                "anom_stated", "Northbridge underpaying laboratory",
                json.dumps({"payer": "Northbridge Commercial"}),
                date(2026, 5, 1), date(2026, 7, 31),
                json.dumps({"onset_date": "2026-04-15"}),
            ),
        ]
        for anomaly_id, title, dimensions, window_start, window_end, evidence in rows:
            con.execute(
                "INSERT INTO snap_003.detected_anomalies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    anomaly_id,
                    # every row of a snapshot carries the same load stamp
                    datetime(2026, 8, 3, 4, 10),
                    "UNDERPAYMENT",
                    title,
                    "planted",
                    "underpayment_variance",
                    dimensions,
                    window_start,
                    window_end,
                    2_500_000,
                    "high",
                    "high",
                    "open",
                    evidence,
                ],
            )
    finally:
        con.close()
    return path


class TestDuckDbAnomalySource:
    async def test_window_start_is_published_as_the_onset(
        self, anomaly_fixture_db: Path
    ) -> None:
        records = await DuckDbAnomalySource(anomaly_fixture_db).list_anomalies(WATERMARK)
        by_id = {r.anomaly_id: r for r in records}
        assert sorted(by_id) == ["anom_derived", "anom_stated"]
        derived = by_id["anom_derived"]
        assert derived.dimensions == (("payer", "Northbridge Commercial"),)
        assert derived.window_start == date(2026, 6, 10)
        # the row had no onset fact; the source derives it from the window
        assert derived.evidence["onset_date"] == "2026-06-10"
        assert derived.evidence["denied_cents"] == 2_500_000  # other facts survive

    async def test_a_feed_supplied_onset_is_never_clobbered(
        self, anomaly_fixture_db: Path
    ) -> None:
        records = await DuckDbAnomalySource(anomaly_fixture_db).list_anomalies(WATERMARK)
        stated = next(r for r in records if r.anomaly_id == "anom_stated")
        assert stated.window_start == date(2026, 5, 1)
        assert stated.evidence["onset_date"] == "2026-04-15"  # the feed's own onset wins

    async def test_missing_table_is_an_empty_population_not_an_error(
        self, anomaly_fixture_db: Path
    ) -> None:
        earlier = DataWatermark(
            id="wm_001", loaded_at=datetime(2026, 8, 1, 4, 5), newest_data_date=date(2026, 7, 31)
        )
        assert await DuckDbAnomalySource(anomaly_fixture_db).list_anomalies(earlier) == ()

    async def test_unknown_watermark_is_stale_not_empty(
        self, anomaly_fixture_db: Path
    ) -> None:
        unknown = DataWatermark(
            id="wm_999", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
        )
        with pytest.raises(WatermarkStaleError):
            await DuckDbAnomalySource(anomaly_fixture_db).list_anomalies(unknown)

    async def test_the_onset_reaches_the_card_age(self, anomaly_fixture_db: Path, rules) -> None:
        """Source → formula, end to end: the age of each card is the
        watermark date minus that record's onset, derived or stated."""
        records = await DuckDbAnomalySource(anomaly_fixture_db).list_anomalies(WATERMARK)
        portfolio = build_portfolio(
            records,
            watermark=WATERMARK,
            policy=priority_policy_from_pack(load_base_pack()),
            rules=rules,
        )
        ages = {c.anomaly_id: c.age_days for c in portfolio.items}
        assert ages == {"anom_derived": 54, "anom_stated": 110}


@pytest.mark.reference
class TestGeneratedPopulation:
    """The real 33-anomaly population at ``wm_003``."""

    @staticmethod
    def _portfolio(records, watermark):
        return build_portfolio(
            records,
            watermark=watermark,
            policy=priority_policy_from_pack(load_base_pack()),
            rules=load_actionability_rules(RULES_PATH),
        )

    @staticmethod
    async def _load():
        if not REFERENCE_DB.is_file():
            pytest.skip("generated warehouse missing")
        con = duckdb.connect(str(REFERENCE_DB), read_only=True)
        try:
            rows = con.execute(
                "SELECT watermark_id, loaded_at, newest_data_date FROM main.watermarks "
                "ORDER BY loaded_at"
            ).fetchall()
        finally:
            con.close()
        newest = DataWatermark(
            id=rows[-1][0], loaded_at=rows[-1][1], newest_data_date=rows[-1][2]
        )
        records = await DuckDbAnomalySource(REFERENCE_DB).list_anomalies(newest)
        assert records, "detected_anomalies is empty at the newest watermark"
        return records, newest

    async def test_real_population_ranks_by_the_governed_formula(self) -> None:
        records, newest = await self._load()
        portfolio = self._portfolio(records, newest)
        assert portfolio.status == "ok"
        assert len(portfolio.items) >= 10  # the ~30-40 population, minus resolved
        scores = [c.priority_score for c in portfolio.items]
        assert scores == sorted(scores, reverse=True)
        assert all(c.actionability_rationale for c in portfolio.items)

    async def test_age_days_is_varied_and_anchored_to_each_onset(self) -> None:
        """The recency term must separate the population, not decorate it.

        Before the onset was wired through the anomaly source every card
        reported ``age_days: 0`` (the generator stamps ``detected_at`` at
        the watermark for every row), so the recency term contributed the
        same constant to all 33 scores."""
        records, newest = await self._load()
        portfolio = self._portfolio(records, newest)
        ages = sorted(c.age_days for c in portfolio.items)
        assert len(portfolio.items) == 33
        assert len(set(ages)) == 28  # 28 distinct onsets across 33 cards
        assert ages[0] == 1 and ages[-1] == 155
        assert ages[len(ages) // 2] == 38  # median
        assert ages[-1] - ages[0] >= 150  # ~22 weeks of spread, ~11 half-lives

        loaded_on = newest.loaded_at.date()
        for card in portfolio.items:
            assert card.age_days == (loaded_on - card.window_start).days
        oldest = max(portfolio.items, key=lambda c: c.age_days)
        assert oldest.window_start == date(2026, 3, 1)
        assert oldest.age_days == (loaded_on - date(2026, 3, 1)).days == 155

    async def test_recency_moves_the_ranking_on_the_real_population(self) -> None:
        """Not just varied — load-bearing. Re-ranking the same records with
        every onset flattened to the watermark reorders the portfolio."""
        records, newest = await self._load()
        governed = [c.anomaly_id for c in self._portfolio(records, newest).items]
        flattened = replace_onsets(records, newest.loaded_at.date().isoformat())
        assert governed != [c.anomaly_id for c in self._portfolio(flattened, newest).items]
