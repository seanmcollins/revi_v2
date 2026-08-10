"""Denial recovery chains: shape, causality, conservation, censoring, effects.

The structural half (foreign keys, ordering, amount conservation, population)
runs inside `--verify` and is asserted here through `_recovery_checks` rather
than restated. What this file adds is the part a check-suite cannot phrase: that
the authored effects are actually *in* the generated data, in the right
direction, at small scale — and that the feed's population rule matches the
governed filing ladder it claims to follow.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from revi_warehouse.config import (
    GOVERNED_CONFIRMED_PLANS,
    RECOVERY,
    RECOVERY_CLASS_BY_CARC,
    RECOVERY_CLASS_SPECS,
    RECOVERY_CLASSES,
    RECOVERY_PAYER_OVERTURN_FACTOR,
    RECOVERY_STRONG_PAYER,
    RECOVERY_WEAK_PAYER,
    RESUBMISSION_TYPES,
)
from revi_warehouse.dims import DENIAL_CODES, PAYERS, PLANS, recovery_class_rows
from revi_warehouse.generate import GenerationResult, run_generation
from revi_warehouse.verify import _recovery_checks

NEWEST = "snap_003"
COHORT = "service_date >= DATE '2025-01-01' AND appeal_status = 'NONE'"
DECIDED = "recovery_status IN ('RECOVERED_FULL', 'RECOVERED_PARTIAL', 'DENIED_AGAIN')"
RECOVERED = "recovery_status IN ('RECOVERED_FULL', 'RECOVERED_PARTIAL')"


@pytest.fixture(scope="module")
def con(small_result: GenerationResult) -> Any:
    connection = duckdb.connect(str(small_result.db_path), read_only=True)
    yield connection
    connection.close()


def _rate(con: Any, predicate: str) -> tuple[int, int, float]:
    """(recovered, decided, rate) over the recovery cohort matching `predicate`."""
    row = con.execute(
        f"SELECT count(*) FILTER (WHERE {RECOVERED}), count(*) FILTER (WHERE {DECIDED}) "
        f"FROM {NEWEST}.v_denial WHERE {COHORT} AND {predicate}"
    ).fetchone()
    recovered, decided = int(row[0]), int(row[1])
    return recovered, decided, (recovered / decided if decided else 0.0)


# --- config is config, not per-claim scripting --------------------------------


def test_every_carc_has_exactly_one_recovery_class() -> None:
    """The mapping is total over the code system and closed over the class list."""
    codes = {code for code, _desc, _cat in DENIAL_CODES}
    assert set(RECOVERY_CLASS_BY_CARC) == codes
    assert set(RECOVERY_CLASS_BY_CARC.values()) == set(RECOVERY_CLASSES)
    assert {spec.name for spec in RECOVERY_CLASS_SPECS} == set(RECOVERY_CLASSES)
    rows = recovery_class_rows()
    assert rows["carc_code"] == sorted(codes)
    assert all(desc.strip() for desc in rows["class_description"])


def test_authored_effect_table_is_ordered_the_way_the_story_claims() -> None:
    """Fixable classes resubmit more, faster, and win more. Every rung, in order."""
    by_name = {spec.name: spec for spec in RECOVERY_CLASS_SPECS}
    ladder = ["CODING", "REGISTRATION", "ROUTING", "CLINICAL", "FINAL"]
    resubmit = [by_name[name].resubmit_prob for name in ladder]
    overturn = [by_name[name].base_overturn for name in ladder]
    delay = [by_name[name].delay_median_days for name in ladder]
    backlog = [by_name[name].backlog_share for name in ladder]
    assert resubmit == sorted(resubmit, reverse=True)
    assert overturn == sorted(overturn, reverse=True)
    assert delay == sorted(delay)  # slower the less fixable it is
    assert backlog == sorted(backlog)  # and it sits in the queue longer
    assert by_name["CODING"].base_overturn > 3 * by_name["CLINICAL"].base_overturn


def test_payer_factors_name_one_strong_and_one_weak_payer() -> None:
    factors = dict(zip([p.name for p in PAYERS], RECOVERY_PAYER_OVERTURN_FACTOR, strict=True))
    assert len(factors) == len(PAYERS)
    assert max(factors, key=lambda name: factors[name]) == RECOVERY_STRONG_PAYER
    assert min(factors, key=lambda name: factors[name]) == RECOVERY_WEAK_PAYER
    assert factors[RECOVERY_STRONG_PAYER] / factors[RECOVERY_WEAK_PAYER] > 2.0


def test_confirmed_filing_plans_match_the_governed_pack_ladder() -> None:
    """`GOVERNED_CONFIRMED_PLANS` is a claim about pack content — so check it there.

    The deadline collapse is authored to bite hardest exactly where the pack's
    filing ladder states a limit WITHOUT `requires_confirmation`. If the pack
    changes tiers, this constant has to move with it or the interaction is
    asserting something the governed rules no longer say.
    """
    rules = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "packs" / "base-rcm" / "filing_rules.yaml").read_text()
    )["filing_rules"]

    def matched(payer_name: str, plan_name: str) -> dict[str, Any]:
        for rule in rules:  # first match wins, most specific first
            if not fnmatch.fnmatchcase(payer_name, rule["payer_pattern"]):
                continue
            pattern = rule.get("plan_pattern")
            if pattern is not None and not fnmatch.fnmatchcase(plan_name, pattern):
                continue
            return rule
        raise AssertionError(f"no filing rule matches {payer_name!r} / {plan_name!r}")

    confirmed = {
        plan_name
        for payer_name, plan_name, _product, _limit, _basis, _weight in PLANS
        if not matched(payer_name, plan_name)["requires_confirmation"]
    }
    assert confirmed == GOVERNED_CONFIRMED_PLANS
    assert RECOVERY.past_deadline_factor_confirmed < RECOVERY.past_deadline_factor_default


# --- shape of the written feed ------------------------------------------------


def test_structural_recovery_checks_pass_in_every_snapshot(small_result: GenerationResult) -> None:
    """Causality, conservation, linkage, population — the whole `--verify` block."""
    connection = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        checks = [
            check
            for snap in ("snap_001", "snap_002", "snap_003")
            for check in _recovery_checks(connection, snap, "2026-08-02")
        ]
    finally:
        connection.close()
    assert checks
    failures = [c for c in checks if not c.ok]
    assert not failures, [f"{c.name}: {c.detail}" for c in failures]


def test_chains_are_ordered_and_linked(con: Any) -> None:
    """One resubmission, then its answer — never the other way round, never orphaned."""
    bad_order, orphans, self_parented = con.execute(
        f"""
        SELECT
          (SELECT count(*) FROM {NEWEST}.fact_recovery_event e
             JOIN {NEWEST}.fact_recovery_event p ON p.recovery_event_id = e.parent_event_id
            WHERE e.event_date <= p.event_date OR p.event_type <> 'RESUBMISSION'
              OR p.cycle_num <> e.cycle_num OR p.denial_id <> e.denial_id),
          (SELECT count(*) FROM {NEWEST}.fact_recovery_event
            WHERE event_type = 'OUTCOME' AND parent_event_id IS NULL),
          (SELECT count(*) FROM {NEWEST}.fact_recovery_event
            WHERE parent_event_id = recovery_event_id)
        """
    ).fetchone()
    assert (bad_order, orphans, self_parented) == (0, 0, 0)


def test_amounts_conserve(con: Any) -> None:
    over, negative, silent_win = con.execute(
        f"""
        SELECT count(*) FILTER (WHERE recovered_amount_cents > denied_amount_cents),
               count(*) FILTER (WHERE recovered_amount_cents < 0),
               count(*) FILTER (WHERE outcome = 'PAID_FULL' AND recovered_amount_cents = 0)
        FROM {NEWEST}.fact_recovery_event
        """
    ).fetchone()
    assert (over, negative, silent_win) == (0, 0, 0)
    partial_under_full = con.execute(
        f"""
        SELECT count(*) FROM {NEWEST}.fact_recovery_event
        WHERE outcome = 'PAID_PARTIAL'
          AND recovered_amount_cents::DOUBLE / NULLIF(denied_amount_cents, 0) > 1.0
        """
    ).fetchone()[0]
    assert partial_under_full == 0


def test_second_denials_reuse_the_existing_code_system(con: Any) -> None:
    """No new real-code exposure: redenial CARCs and RARCs come from what exists."""
    unknown_carc, bad_group, bad_rarc = con.execute(
        f"""
        SELECT
          (SELECT count(*) FROM {NEWEST}.fact_recovery_event e
             LEFT JOIN {NEWEST}.dim_denial_code k USING (carc_code)
            WHERE e.carc_code IS NOT NULL AND k.carc_code IS NULL),
          (SELECT count(*) FROM {NEWEST}.fact_recovery_event
            WHERE group_code IS NOT NULL AND group_code NOT IN ('CO', 'PR', 'OA', 'PI')),
          (SELECT count(*) FROM {NEWEST}.fact_recovery_event
            WHERE rarc_synthetic IS NOT NULL
              AND NOT regexp_matches(rarc_synthetic, '^RZ[0-9]{{2}}$'))
        """
    ).fetchone()
    assert (unknown_carc, bad_group, bad_rarc) == (0, 0, 0)
    actions = {
        row[0]
        for row in con.execute(
            f"SELECT DISTINCT resubmission_type FROM {NEWEST}.fact_recovery_event "
            "WHERE resubmission_type IS NOT NULL"
        ).fetchall()
    }
    assert actions <= set(RESUBMISSION_TYPES)


def test_the_feed_covers_only_its_stated_population(con: Any) -> None:
    """Organic era, no formal appeal, and every chain's denial visible with it."""
    appealed, backfill = con.execute(
        f"""
        SELECT count(*) FILTER (WHERE d.appeal_status <> 'NONE'),
               count(*) FILTER (WHERE d.service_date < DATE '2025-01-01')
        FROM {NEWEST}.fact_recovery_event e JOIN {NEWEST}.v_denial d USING (denial_id)
        """
    ).fetchone()
    assert (appealed, backfill) == (0, 0)


# --- right-censoring ----------------------------------------------------------


def test_censoring_exists_at_the_edge(con: Any, small_result: GenerationResult) -> None:
    """Both open shapes must be present, and the world must know more than the edge."""
    pending, not_resubmitted = con.execute(
        f"""
        SELECT count(*) FILTER (WHERE recovery_status = 'RESUBMITTED_PENDING'),
               count(*) FILTER (WHERE recovery_status = 'NOT_RESUBMITTED')
        FROM {NEWEST}.v_denial WHERE {COHORT}
        """
    ).fetchone()
    assert pending > 0, "no chain is awaiting an answer at the newest watermark"
    assert not_resubmitted > 0
    truth = small_result.answer_key["recovery"]["world_truth"]
    # Chains the world will resubmit after the edge are invisible today and
    # indistinguishable from denials nobody will ever work. That is the point.
    assert truth["resubmission_after_newest_watermark"] > 0
    assert truth["resubmitted_undecided_at_newest_watermark"] > 0
    assert truth["chains"] > truth["settled_by_newest_watermark"]


def test_the_feed_fills_in_as_loads_arrive(con: Any) -> None:
    counts = [
        con.execute(f"SELECT count(*) FROM {snap}.fact_recovery_event").fetchone()[0]
        for snap in ("snap_001", "snap_002", "snap_003")
    ]
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]
    late = con.execute(
        f"SELECT count(*) FROM {NEWEST}.fact_recovery_event WHERE event_date > DATE '2026-07-31'"
    ).fetchone()[0]
    early = con.execute(
        "SELECT count(*) FROM snap_001.fact_recovery_event WHERE event_date > DATE '2026-07-31'"
    ).fetchone()[0]
    assert late > 0 and early == 0


# --- the authored effects, realized -------------------------------------------


def test_fixable_classes_recover_better_than_clinical_ones(con: Any) -> None:
    rates = {
        name: _rate(con, f"denial_recovery_class = '{name}'") for name in RECOVERY_CLASSES
    }
    for name, (_rec, decided, _r) in rates.items():
        assert decided > 0, f"{name} has no decided chain to measure"
    assert rates["CODING"][2] > rates["ROUTING"][2] > rates["CLINICAL"][2] > rates["FINAL"][2]
    assert rates["REGISTRATION"][2] > rates["CLINICAL"][2]
    # And they are worked more often, not only won more often.
    resubmission = dict(
        con.execute(
            f"""
            SELECT denial_recovery_class,
                   count(*) FILTER (WHERE recovery_status <> 'NOT_RESUBMITTED')::DOUBLE / count(*)
            FROM {NEWEST}.v_denial WHERE {COHORT} GROUP BY 1
            """
        ).fetchall()
    )
    assert resubmission["CODING"] > resubmission["ROUTING"] > resubmission["CLINICAL"]
    assert resubmission["CLINICAL"] > resubmission["FINAL"]


def test_the_strong_payer_recovers_better_than_the_weak_one(con: Any) -> None:
    strong = _rate(con, f"payer_name = '{RECOVERY_STRONG_PAYER}'")
    weak = _rate(con, f"payer_name = '{RECOVERY_WEAK_PAYER}'")
    assert strong[1] > 0 and weak[1] > 0
    assert strong[2] > weak[2], (strong, weak)
    # The pair is thin at small scale, so also compare the halves of the payer
    # field the factors define — that contrast is what a cohort-sized study sees.
    factors = dict(zip([p.name for p in PAYERS], RECOVERY_PAYER_OVERTURN_FACTOR, strict=True))
    favourable = ", ".join(f"'{n}'" for n, f in factors.items() if f >= 1.05)
    unfavourable = ", ".join(f"'{n}'" for n, f in factors.items() if f <= 0.80)
    top = _rate(con, f"payer_name IN ({favourable})")
    bottom = _rate(con, f"payer_name IN ({unfavourable})")
    assert top[1] >= 30 and bottom[1] >= 30
    assert top[2] - bottom[2] > 0.10, (top, bottom)


def test_early_resubmissions_recover_better_than_late_ones(con: Any) -> None:
    early = _rate(con, "days_to_resubmission <= 14")
    late = _rate(con, "days_to_resubmission > 30")
    assert early[1] > 0 and late[1] > 0
    assert early[2] > late[2], (early, late)
    # Within one class, so the decay is not just slow classes being weak ones.
    early_coding = _rate(con, "denial_recovery_class = 'CODING' AND days_to_resubmission <= 14")
    late_coding = _rate(con, "denial_recovery_class = 'CODING' AND days_to_resubmission > 14")
    assert late_coding[1] > 0
    assert early_coding[2] > late_coding[2], (early_coding, late_coding)


def test_crossing_the_filing_deadline_collapses_recovery(con: Any) -> None:
    within = _rate(con, "resubmission_date <= service_date + timely_filing_days")
    past = _rate(con, "resubmission_date > service_date + timely_filing_days")
    assert past[1] > 0, "nothing was resubmitted past a filing deadline"
    assert past[2] < 0.5 * within[2], (past, within)


def test_larger_denials_are_pursued_more(con: Any) -> None:
    rows = dict(
        con.execute(
            f"""
            SELECT CASE WHEN denied_amount_cents < 50000 THEN 'small'
                        WHEN denied_amount_cents < 500000 THEN 'medium' ELSE 'large' END,
                   count(*) FILTER (WHERE recovery_status <> 'NOT_RESUBMITTED')::DOUBLE / count(*)
            FROM {NEWEST}.v_denial WHERE {COHORT} GROUP BY 1
            """
        ).fetchall()
    )
    assert rows["large"] > rows["small"]
    assert rows["large"] - rows["small"] < 0.25, "the dollar tilt is meant to be mild"


# --- the answer key records both the model and what it produced ---------------


def test_answer_key_records_parameters_and_realized_outcomes(
    small_result: GenerationResult,
) -> None:
    recovery = small_result.answer_key["recovery"]
    assert set(recovery) >= {"generating_model", "world_truth", "snap_001", "snap_002", "snap_003"}
    model = recovery["generating_model"]
    assert {spec["class"] for spec in model["classes"]} == set(RECOVERY_CLASSES)
    assert model["payer_overturn_factor"][RECOVERY_STRONG_PAYER] > (
        model["payer_overturn_factor"][RECOVERY_WEAK_PAYER]
    )
    assert sorted(model["timeliness"]["confirmed_plans"]) == sorted(GOVERNED_CONFIRMED_PLANS)

    newest = recovery["snap_003"]
    cells = newest["by_payer_class"]
    assert cells and {"payer_name", "denial_recovery_class", "denials", "resubmitted",
                      "recovered", "recovered_cents"} <= set(cells[0])
    overall = newest["overall"]
    assert overall["recovered"] <= overall["decided"] <= overall["resubmitted"] <= overall["denials"]
    assert overall["denials"] == (
        overall["resubmitted"] + overall["not_resubmitted"]
    )
    assert overall["recovered_cents"] < overall["denied_cents"]
    assert newest["censoring"]["resubmitted_no_outcome_yet"] > 0
    assert newest["detectability"]["strong_rate"] > newest["detectability"]["weak_rate"]


def test_answer_key_cells_reconcile_with_the_database(
    small_result: GenerationResult, con: Any
) -> None:
    """Every payer x class cell is a real count, not a remembered one."""
    for cell in small_result.answer_key["recovery"]["snap_003"]["by_payer_class"]:
        row = con.execute(
            f"""
            SELECT count(*), count(*) FILTER (WHERE {RECOVERED}),
                   COALESCE(SUM(recovered_amount_cents), 0)
            FROM {NEWEST}.v_denial
            WHERE {COHORT} AND payer_name = ?
              AND denial_recovery_class = ?
            """,
            [cell["payer_name"], cell["denial_recovery_class"]],
        ).fetchone()
        assert (cell["denials"], cell["recovered"], cell["recovered_cents"]) == (
            int(row[0]),
            int(row[1]),
            int(row[2]),
        )


# --- determinism --------------------------------------------------------------


def test_two_generations_produce_identical_chains(
    small_result: GenerationResult, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from revi_warehouse.config import GeneratorConfig

    out = tmp_path_factory.mktemp("warehouse-recovery") / "revi_small.duckdb"
    again = run_generation(GeneratorConfig.small(), out)

    def digest(path: Path) -> tuple[Any, ...]:
        connection = duckdb.connect(str(path), read_only=True)
        try:
            return tuple(
                connection.execute(
                    f"SELECT count(*), md5(string_agg(h, '' ORDER BY h)) FROM "
                    f"(SELECT md5(CAST(t AS VARCHAR)) AS h FROM {snap}.fact_recovery_event t)"
                ).fetchone()
                for snap in ("snap_001", "snap_002", "snap_003")
            )
        finally:
            connection.close()

    assert digest(small_result.db_path) == digest(again.db_path)
    assert small_result.answer_key["recovery"] == again.answer_key["recovery"]
