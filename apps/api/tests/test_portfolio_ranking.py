"""``anomaly_priority@3``: the worklist ranks on the figure it defends.

Regression in ``@2``: two figures were published per card — the detector's
assertion and this platform's re-derivation — and the ranking used the
detector's even where the payload itself called the two a divergence.

Four cases are pinned: a diverged card ranks (and prices its recoverable
estimate) on the reconciled figure; a ``not_comparable`` one keeps the
detector's and says so; agreed and un-derivable cards are unchanged; and the
normalizer is taken over the same figures the scores are.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from revi_api.actionability import assess, load_actionability_rules
from revi_api.portfolio import PriorityPolicy, build_portfolio
from revi_api.rederive import ReDerivedImpact
from revi_investigation.application.ports import AnomalyRecord
from revi_kernel.watermark import DataWatermark

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "packs" / "base-rcm" / "anomaly_actionability.yaml"

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


@pytest.fixture(scope="module")
def rules():
    return load_actionability_rules(RULES_PATH)


@pytest.fixture(scope="module")
def policy() -> PriorityPolicy:
    return PriorityPolicy()


def _record(
    anomaly_id: str,
    category: str,
    impact_cents: int,
    *,
    metric_id: str = "denied_dollars",
    evidence: dict[str, Any] | None = None,
) -> AnomalyRecord:
    return AnomalyRecord(
        anomaly_id=anomaly_id,
        detected_at=datetime(2026, 8, 2, 6, 0),
        category=category,
        title=f"{category} {anomaly_id}",
        description="planted",
        metric_id=metric_id,
        dimensions=(("payer", "Atlas Commercial"),),
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 31),
        impact_cents=impact_cents,
        severity="high",
        confidence="high",
        status="OPEN",
        evidence=evidence or {},
    )


def _card(portfolio, anomaly_id: str):
    return next(c for c in portfolio.items if c.anomaly_id == anomaly_id)


def _with_dimension(record: AnomalyRecord, dimension: tuple[str, str]) -> AnomalyRecord:
    """The same record, cut the way the detection feed cuts it."""
    return replace(record, dimensions=(*record.dimensions, dimension))


class TestRankedOn:
    def test_a_diverged_card_ranks_and_prices_on_the_platform_figure(
        self, rules, policy: PriorityPolicy
    ) -> None:
        """The reconciled figure ranks; the detector's stays as provenance."""
        record = _record("div", "DENIAL_SPIKE", 2_549_370)
        portfolio = build_portfolio(
            (record,),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            rederived={
                "div": ReDerivedImpact(cents=3_551_530, measure_id="denied_dollars", rows=4)
            },
        )
        card = _card(portfolio, "div")
        assert card.impact_agreement == "diverged"
        # provenance untouched
        assert card.impact_cents == 2_549_370
        assert card.reconciled_impact_cents == 3_551_530
        # ranked on the figure the platform defends, and it says so
        assert card.ranked_on == "platform"
        assert card.ranked_impact_cents == 3_551_530
        assert "not the detection system's" in card.ranked_on_note
        # the recoverable estimate follows the same figure
        fraction = assess(rules.rule_for("DENIAL_SPIKE"), record).recoverable_fraction
        assert card.recoverable_cents_estimate == int(
            (Decimal(3_551_530) * fraction).to_integral_value()
        )
        assert card.priority.ranked_impact_cents == 3_551_530
        assert card.priority.impact_normalizer_cents == 3_551_530
        assert card.priority.impact_norm == 1.0

    def test_a_not_comparable_card_keeps_the_detector_figure_and_states_it(
        self, rules, policy: PriorityPolicy
    ) -> None:
        """A snapshot balance is not a better measurement of a windowed flow."""
        record = _record("snap", "DNFB", 17_821_682, metric_id="dnfb_dollars")
        portfolio = build_portfolio(
            (record,),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            rederived={
                "snap": ReDerivedImpact(cents=19_587_392, measure_id="dnfb_dollars", rows=6)
            },
            snapshot_metric_ids=frozenset({"dnfb_dollars"}),
        )
        card = _card(portfolio, "snap")
        assert card.impact_agreement == "not_comparable"
        assert card.ranked_on == "not_comparable"
        assert card.ranked_impact_cents == 17_821_682
        assert "not a comparable quantity" in card.ranked_on_note
        assert any("not a comparable quantity" in w for w in portfolio.warnings)

    def test_agreed_and_unavailable_cards_rank_on_the_detector(
        self, rules, policy: PriorityPolicy
    ) -> None:
        agreed = _record("agr", "DENIAL_SPIKE", 1_767_733)
        unknown = _record("unk", "DENIAL_SPIKE", 6_355_160)
        portfolio = build_portfolio(
            (agreed, unknown),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            rederived={
                "agr": ReDerivedImpact(cents=1_767_733, measure_id="denied_dollars", rows=2),
                "unk": ReDerivedImpact(unavailable_reason="no money column"),
            },
        )
        assert _card(portfolio, "agr").ranked_on == "detector"
        assert _card(portfolio, "agr").ranked_impact_cents == 1_767_733
        assert _card(portfolio, "unk").ranked_on == "detector"
        assert _card(portfolio, "unk").ranked_impact_cents == 6_355_160
        assert "no re-derived figure" in _card(portfolio, "unk").ranked_on_note

    def test_a_re_derivation_can_change_the_order(
        self, rules, policy: PriorityPolicy
    ) -> None:
        """The whole point: two cards swap when the disputed figure is dropped."""
        # Same category on purpose: identical recoverable fraction and
        # identical age, so the ONLY thing that can reorder them is which
        # figure the score was computed from.
        smaller_detected = _record("up", "DENIAL_SPIKE", 1_000_000)
        larger_detected = _record("dn", "DENIAL_SPIKE", 1_400_000)
        on_detector = build_portfolio(
            (smaller_detected, larger_detected),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
        )
        assert [c.anomaly_id for c in on_detector.items] == ["dn", "up"]
        re_ranked = build_portfolio(
            (smaller_detected, larger_detected),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            rederived={
                # the platform re-derives the smaller card far higher, and
                # the larger one materially lower
                "up": ReDerivedImpact(cents=3_000_000, measure_id="underpayment_dollars", rows=3),
                "dn": ReDerivedImpact(cents=700_000, measure_id="denied_dollars", rows=3),
            },
        )
        assert [c.anomaly_id for c in re_ranked.items] == ["up", "dn"]
        assert _card(re_ranked, "up").ranked_on == "platform"
        assert _card(re_ranked, "dn").ranked_on == "platform"
        # the normalizer is the max of the RANKED figures, not the detected
        assert _card(re_ranked, "up").priority.impact_normalizer_cents == 3_000_000

    def test_lane_totals_carry_both_bases(self, rules, policy: PriorityPolicy) -> None:
        record = _record("div", "DENIAL_SPIKE", 1_000_000)
        portfolio = build_portfolio(
            (record,),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            rederived={
                "div": ReDerivedImpact(cents=1_500_000, measure_id="denied_dollars", rows=2)
            },
        )
        lane = next(lane for lane in portfolio.lanes if lane.id == "value")
        assert lane.impact_cents == 1_000_000
        assert lane.ranked_impact_cents == 1_500_000
        assert lane.recoverable_cents_estimate == _card(
            portfolio, "div"
        ).recoverable_cents_estimate
        assert any("ranked on this platform's re-derived figure" in w for w in portfolio.warnings)

    def test_the_formula_version_is_stamped_on_every_card(
        self, rules, policy: PriorityPolicy
    ) -> None:
        portfolio = build_portfolio(
            (_record("a", "DENIAL_SPIKE", 100_000),),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
        )
        assert portfolio.formula_version == "anomaly_priority@3"
        assert all(c.priority_formula_version == "anomaly_priority@3" for c in portfolio.items)


class TestDimensionRepoints:
    """The detector's cut, repointed onto the one the contract accepts.

    Four reference cards — including the largest on the worklist, the anchor of
    impact normalisation — named a claim-grain contract beside a `proc_group`
    value and refused with GRAIN_INCOMPATIBLE, because procedures bind at
    claim_line. The catalog now certifies `primary_proc_group` at the claim
    grain and both contracts accept it (v2, scope only).
    """

    def test_a_needed_and_legal_substitution_is_made_and_published(
        self, rules, policy: PriorityPolicy
    ) -> None:
        record = _record("proc", "UNDERPAYMENT", 13_403_077, metric_id="underpayment_variance")
        record = _with_dimension(record, ("proc_group", "SURG-GEN"))
        portfolio = build_portfolio(
            (record,),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            # underpayment_variance@2: claim-grain, accepts the claim's
            # dominant procedure group and not the line-grain cut.
            scope_dimensions=lambda _mid: frozenset(
                {"payer", "service_line", "primary_proc_group"}
            ),
        )
        card = _card(portfolio, "proc")
        assert card.drill_spec.dimensions == ["payer", "primary_proc_group"]
        # the VALUE rides across unchanged — same domain, different key
        assert [f.values for f in card.drill_spec.filters] == [
            ["Atlas Commercial"], ["SURG-GEN"]
        ]
        [repoint] = card.drill_dimension_repoints
        assert (repoint.from_dimension, repoint.to_dimension) == (
            "proc_group",
            "primary_proc_group",
        )
        assert repoint.rationale

    def test_nothing_is_substituted_when_the_contract_takes_the_detectors_cut(
        self, rules, policy: PriorityPolicy
    ) -> None:
        """A denial-grain or line-grain contract keeps `proc_group`."""
        record = _with_dimension(
            _record("keep", "DENIAL_SPIKE", 100_000), ("proc_group", "SURG-GEN")
        )
        portfolio = build_portfolio(
            (record,),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            scope_dimensions=lambda _mid: frozenset(
                {"payer", "proc_group", "primary_proc_group"}
            ),
        )
        card = _card(portfolio, "keep")
        assert card.drill_dimension_repoints == []
        assert "proc_group" in card.drill_spec.dimensions

    def test_nothing_is_substituted_when_the_replacement_is_illegal_too(
        self, rules, policy: PriorityPolicy
    ) -> None:
        """Swapping a cut the contract also refuses would trade one
        GRAIN_INCOMPATIBLE for another while claiming a repoint."""
        record = _with_dimension(
            _record("neither", "DENIAL_SPIKE", 100_000), ("proc_group", "SURG-GEN")
        )
        portfolio = build_portfolio(
            (record,),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            scope_dimensions=lambda _mid: frozenset({"payer"}),
        )
        card = _card(portfolio, "neither")
        assert card.drill_dimension_repoints == []
        assert "proc_group" in card.drill_spec.dimensions

    def test_without_the_pack_nothing_is_substituted_at_all(
        self, rules, policy: PriorityPolicy
    ) -> None:
        record = _with_dimension(
            _record("blind", "DENIAL_SPIKE", 100_000), ("proc_group", "SURG-GEN")
        )
        portfolio = build_portfolio(
            (record,), watermark=WATERMARK, policy=policy, rules=rules
        )
        assert _card(portfolio, "blind").drill_dimension_repoints == []

    def test_a_repointed_cards_divergence_is_not_laid_at_the_detector(
        self, rules, policy: PriorityPolicy
    ) -> None:
        """The shared sentence blames the detector's window, population or
        basis. On a repointed card part of the gap is the platform's own
        substitution, and the note says so."""
        record = _with_dimension(
            _record("gap", "UNDERPAYMENT", 1_000_000, metric_id="underpayment_variance"),
            ("proc_group", "SURG-GEN"),
        )
        portfolio = build_portfolio(
            (record,),
            watermark=WATERMARK,
            policy=policy,
            rules=rules,
            rederived={
                "gap": ReDerivedImpact(cents=400_000, measure_id="underpayment_variance", rows=2)
            },
            scope_dimensions=lambda _mid: frozenset(
                {"payer", "service_line", "primary_proc_group"}
            ),
        )
        card = _card(portfolio, "gap")
        assert card.impact_agreement == "diverged"
        note = card.impact_reconciliation_note
        assert "this platform's own doing, not the detector's" in note
        assert "proc_group → primary_proc_group" in note
