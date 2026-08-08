"""The detected-anomaly population: emission, determinism, visibility, structure.

Small-scale checks run against the shared `small_result` fixture; the full-scale
population assertions are marked `reference` and read the generated `data/`
warehouse.
"""

from __future__ import annotations

import itertools
import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from revi_warehouse.anomalies import confidence_for, is_emitted, severity_for, spec_dimensions
from revi_warehouse.config import ANOMALY_SPECS, SELF_RESOLVING_IDS, SNAPSHOTS, GeneratorConfig
from revi_warehouse.generate import GenerationResult, run_generation

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_METRICS = REPO_ROOT / "packs" / "base-rcm" / "metrics"

SNAP_NAMES = ("snap_001", "snap_002", "snap_003")

ANOMALY_COLUMNS = (
    "anomaly_id",
    "detected_at",
    "category",
    "title",
    "description",
    "metric_id",
    "dimensions",
    "window_start",
    "window_end",
    "impact_cents",
    "severity",
    "confidence",
    "status",
    "evidence",
)


def _anomalies(result: GenerationResult, schema: str) -> list[dict]:
    return result.answer_key["anomalies"][schema]


# ---------------------------------------------------------------------------
# spec table


def test_spec_table_is_a_population_not_a_handful() -> None:
    assert 30 <= len(ANOMALY_SPECS) <= 40
    assert len({s.spec_id for s in ANOMALY_SPECS}) == len(ANOMALY_SPECS)
    assert len({s.title for s in ANOMALY_SPECS}) == len(ANOMALY_SPECS)


def test_every_spec_targets_exactly_one_payer_or_plan() -> None:
    for spec in ANOMALY_SPECS:
        assert (spec.payer is None) != (spec.plan is None), spec.spec_id


def test_every_metric_id_exists_in_the_base_pack() -> None:
    """metric_id must reference a real packs/base-rcm metric (read-only pack)."""
    available = {p.stem for p in PACK_METRICS.glob("*.yaml")}
    assert available, "base-rcm metric pack not found"
    for spec in ANOMALY_SPECS:
        assert spec.metric_id in available, f"{spec.spec_id} -> unknown metric {spec.metric_id}"


def test_spec_windows_are_ordered_and_within_the_simulated_period() -> None:
    for spec in ANOMALY_SPECS:
        assert spec.onset <= spec.window_end, spec.spec_id
        assert "2025-01-01" <= spec.onset <= "2026-08-02", spec.spec_id
        assert spec.window_end <= "2026-08-02", spec.spec_id


def test_onsets_are_staggered_across_many_distinct_weeks() -> None:
    """Onsets land weeks apart rather than all at once."""
    weeks = {date.fromisoformat(spec.onset).isocalendar()[:2] for spec in ANOMALY_SPECS}
    assert len(weeks) >= 12
    assert len({spec.onset for spec in ANOMALY_SPECS}) >= 25


# ---------------------------------------------------------------------------
# emission


def test_table_exists_in_every_snapshot_with_the_documented_columns(
    small_result: GenerationResult,
) -> None:
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        for schema in SNAP_NAMES:
            cols = [
                r[0]
                for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = ? AND table_name = 'detected_anomalies' "
                    "ORDER BY ordinal_position",
                    [schema],
                ).fetchall()
            ]
            assert tuple(cols) == ANOMALY_COLUMNS, schema
    finally:
        con.close()


def test_every_snapshot_emits_a_population(small_result: GenerationResult) -> None:
    for schema in SNAP_NAMES:
        assert len(_anomalies(small_result, schema)) >= 25, schema


def test_detected_at_equals_the_snapshot_loaded_at(small_result: GenerationResult) -> None:
    by_name = {s.schema_name: s for s in SNAPSHOTS}
    for schema in SNAP_NAMES:
        loaded_at = by_name[schema].loaded_at
        for row in _anomalies(small_result, schema):
            assert row["detected_at"] == loaded_at, (schema, row["anomaly_id"])


def test_rows_carry_spec_identity_and_scope(small_result: GenerationResult) -> None:
    specs = {s.spec_id: s for s in ANOMALY_SPECS}
    for schema in SNAP_NAMES:
        for row in _anomalies(small_result, schema):
            spec = specs[row["anomaly_id"]]
            assert row["category"] == spec.category
            assert row["title"] == spec.title
            assert row["metric_id"] == spec.metric_id
            assert row["window_start"] == spec.onset
            assert row["window_end"] == spec.window_end
            assert row["dimensions"] == spec_dimensions(spec)
            assert row["status"] == "open"


def test_emission_floors_are_respected(small_result: GenerationResult) -> None:
    specs = {s.spec_id: s for s in ANOMALY_SPECS}
    for schema in SNAP_NAMES:
        for row in _anomalies(small_result, schema):
            spec = specs[row["anomaly_id"]]
            n_events = row["evidence"]["n_events"]
            assert is_emitted(spec, n_events, row["impact_cents"]), row["anomaly_id"]


def test_no_spec_is_emitted_before_its_onset_is_visible(small_result: GenerationResult) -> None:
    specs = {s.spec_id: s for s in ANOMALY_SPECS}
    for snap in SNAPSHOTS:
        for row in _anomalies(small_result, snap.schema_name):
            assert specs[row["anomaly_id"]].onset <= snap.newest_data_date


def test_impact_is_recomputed_from_the_snapshot_not_copied_from_the_spec(
    small_result: GenerationResult,
) -> None:
    """Impacts must come from SQL over that snapshot, so they differ from the
    spec's planted billed totals and (for windows still filling in) across
    snapshots."""
    planted = {s.spec_id: s.billed_total_cents for s in ANOMALY_SPECS}
    rows = {r["anomaly_id"]: r for r in _anomalies(small_result, "snap_003")}
    assert all(r["impact_cents"] != planted[a] for a, r in rows.items())
    first = {r["anomaly_id"]: r["impact_cents"] for r in _anomalies(small_result, "snap_001")}
    moved = [a for a, v in first.items() if a in rows and rows[a]["impact_cents"] != v]
    assert moved, "no anomaly impact evolved across watermarks"


# ---------------------------------------------------------------------------
# severity + confidence rules


@pytest.mark.parametrize(
    ("impact_cents", "expected"),
    [
        (10_000_000, "critical"),
        (9_999_999, "high"),
        (2_500_000, "high"),
        (2_499_999, "medium"),
        (500_000, "medium"),
        (499_999, "low"),
        (0, "low"),
    ],
)
def test_severity_thresholds(impact_cents: int, expected: str) -> None:
    assert severity_for(impact_cents) == expected


@pytest.mark.parametrize(
    ("n_events", "expected"),
    [(40, 0.95), (20, 0.90), (10, 0.80), (5, 0.70), (4, 0.60), (0, 0.60)],
)
def test_confidence_rule(n_events: int, expected: float) -> None:
    assert confidence_for(n_events) == expected


def test_persisted_severity_and_confidence_follow_the_rules(
    small_result: GenerationResult,
) -> None:
    for schema in SNAP_NAMES:
        for row in _anomalies(small_result, schema):
            assert row["severity"] == severity_for(row["impact_cents"])
            assert row["confidence"] == confidence_for(row["evidence"]["n_events"])


def test_population_spans_the_magnitude_range(small_result: GenerationResult) -> None:
    impacts = [r["impact_cents"] for r in _anomalies(small_result, "snap_003")]
    sev = {r["severity"] for r in _anomalies(small_result, "snap_003")}
    assert sev == {"critical", "high", "medium", "low"}
    assert max(impacts) >= 10_000_000  # at least one $100k+ signal
    assert min(impacts) < 100_000  # ... and sub-$1k noise


# ---------------------------------------------------------------------------
# evidence: facts, never judgments


def test_evidence_is_fact_shaped(small_result: GenerationResult) -> None:
    """Evidence carries counts/dollars/day-statistics only — no verdict fields."""
    banned = {"fixable", "actionable", "recommendation", "root_cause", "verdict", "should"}
    for schema in SNAP_NAMES:
        for row in _anomalies(small_result, schema):
            evidence = row["evidence"]
            assert evidence["n_events"] >= 1
            assert evidence["cutoff"] <= "2026-08-02"
            for field in evidence:
                assert not any(b in field.lower() for b in banned), (row["anomaly_id"], field)


def test_denial_anomalies_carry_mixed_fixability_facts(small_result: GenerationResult) -> None:
    """Open-vs-expired appeal deadline counts and day stats let a reader decide."""
    rows = [
        r
        for r in _anomalies(small_result, "snap_003")
        if r["category"] in ("denial_spike", "eligibility_cluster", "unworked_denials", "duplicate")
    ]
    assert rows
    for row in rows:
        evidence = row["evidence"]
        assert evidence["appealable_claims"] + evidence["appeal_window_expired_claims"] >= 1
        assert set(evidence["days_to_appeal_deadline"]) == {"min", "max"}
        assert set(evidence["days_since_denial"]) == {"min", "median", "max"}
        assert evidence["appeal_status_counts"]["NONE"] >= 0
        assert evidence["claim_status_counts"]


def test_population_contains_both_appealable_and_expired_denial_cohorts(
    small_result: GenerationResult,
) -> None:
    rows = [r for r in _anomalies(small_result, "snap_003") if "appealable_claims" in r["evidence"]]
    assert any(r["evidence"]["appealable_claims"] > 0 for r in rows)
    assert any(r["evidence"]["appeal_window_expired_claims"] > 0 for r in rows)


def test_timely_filing_anomalies_carry_open_and_expired_deadline_facts(
    small_result: GenerationResult,
) -> None:
    rows = [r for r in _anomalies(small_result, "snap_003") if r["category"] == "timely_filing"]
    assert len(rows) >= 2
    for row in rows:
        evidence = row["evidence"]
        assert evidence["open_claims"] + evidence["expired_claims"] == evidence["unsubmitted_claims"]
        assert evidence["open_billed_cents"] + evidence["expired_billed_cents"] == evidence["billed_cents"]
        assert set(evidence["days_to_deadline"]) == {"min", "median", "max"}
    assert any(r["evidence"]["open_claims"] > 0 for r in rows)
    assert any(r["evidence"]["expired_claims"] > 0 for r in rows)


# ---------------------------------------------------------------------------
# per-snapshot visibility


def test_some_anomalies_appear_only_at_later_snapshots(small_result: GenerationResult) -> None:
    seen = [{r["anomaly_id"] for r in _anomalies(small_result, s)} for s in SNAP_NAMES]
    assert seen[1] - seen[0], "nothing new became visible at snap_002"
    assert seen[2] - seen[1], "nothing new became visible at snap_003"


def test_visibility_is_monotonic_except_documented_self_resolvers(
    small_result: GenerationResult,
) -> None:
    seen = [{r["anomaly_id"] for r in _anomalies(small_result, s)} for s in SNAP_NAMES]
    for earlier, later in itertools.pairwise(seen):
        regressed = {a for a in earlier - later if a not in SELF_RESOLVING_IDS}
        assert not regressed, f"non-self-resolving anomalies disappeared: {sorted(regressed)}"


def test_self_resolvers_are_visible_at_snap_002_and_gone_by_snap_003(
    small_result: GenerationResult,
) -> None:
    assert 2 <= len(SELF_RESOLVING_IDS) <= 3
    at_002 = {r["anomaly_id"] for r in _anomalies(small_result, "snap_002")}
    at_003 = {r["anomaly_id"] for r in _anomalies(small_result, "snap_003")}
    assert at_002 >= SELF_RESOLVING_IDS
    assert not (SELF_RESOLVING_IDS & at_003)


def test_final_snapshot_holds_the_complete_population(small_result: GenerationResult) -> None:
    at_003 = {r["anomaly_id"] for r in _anomalies(small_result, "snap_003")}
    assert at_003 == {s.spec_id for s in ANOMALY_SPECS} - SELF_RESOLVING_IDS


# ---------------------------------------------------------------------------
# answer key + determinism


def test_answer_key_records_the_population_per_snapshot(small_result: GenerationResult) -> None:
    key = small_result.answer_key
    assert set(key["anomalies"]) == set(SNAP_NAMES)
    meta = key["anomalies_meta"]
    assert meta["spec_count"] == len(ANOMALY_SPECS)
    assert sorted(meta["self_resolving_ids"]) == sorted(SELF_RESOLVING_IDS)
    for schema in SNAP_NAMES:
        rows = key["anomalies"][schema]
        assert meta["per_snapshot_counts"][schema] == len(rows)
        assert [r["anomaly_id"] for r in rows] == sorted(r["anomaly_id"] for r in rows)
        for row in rows:
            assert set(row) == set(ANOMALY_COLUMNS)


def test_answer_key_mirrors_the_persisted_table(small_result: GenerationResult) -> None:
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        for schema in SNAP_NAMES:
            stored = {
                r[0]: (int(r[1]), r[2], json.loads(r[3]))
                for r in con.execute(
                    f"SELECT anomaly_id, impact_cents, severity, CAST(evidence AS VARCHAR) "
                    f"FROM {schema}.detected_anomalies"
                ).fetchall()
            }
            recorded = {
                r["anomaly_id"]: (r["impact_cents"], r["severity"], r["evidence"])
                for r in _anomalies(small_result, schema)
            }
            assert stored == recorded, schema
    finally:
        con.close()


@pytest.fixture(scope="module")
def anomaly_rerun(
    small_config: GeneratorConfig, tmp_path_factory: pytest.TempPathFactory
) -> GenerationResult:
    out = tmp_path_factory.mktemp("warehouse-anomaly-rerun") / "revi_small.duckdb"
    return run_generation(small_config, out)


def test_injection_uses_independent_streams_and_reruns_identically(
    small_result: GenerationResult, anomaly_rerun: GenerationResult
) -> None:
    """A second generation reproduces the population exactly (rows and evidence)."""
    for schema in SNAP_NAMES:
        assert json.dumps(anomaly_rerun.answer_key["anomalies"][schema], sort_keys=True) == json.dumps(
            small_result.answer_key["anomalies"][schema], sort_keys=True
        )


def test_detected_anomalies_table_is_byte_stable_across_runs(
    small_result: GenerationResult, anomaly_rerun: GenerationResult
) -> None:
    def dump(path: Path, schema: str) -> list[tuple]:
        con = duckdb.connect(str(path), read_only=True)
        try:
            return con.execute(
                f"SELECT * FROM {schema}.detected_anomalies ORDER BY anomaly_id"
            ).fetchall()
        finally:
            con.close()

    for schema in SNAP_NAMES:
        assert dump(small_result.db_path, schema) == dump(anomaly_rerun.db_path, schema)


def test_injection_leaves_the_base_world_draw_sequence_untouched(
    small_result: GenerationResult,
) -> None:
    """Base claims keep contiguous low ids; injected claims are appended after
    them, so no existing draw sequence is consumed or shifted."""
    n_base = small_result.answer_key["anomalies_meta"]["base_n_claims"]
    first_injected = small_result.answer_key["anomalies_meta"]["first_injected_claim_id"]
    assert first_injected == f"CLM-{n_base + 1:07d}"
    con = duckdb.connect(str(small_result.db_path), read_only=True)
    try:
        base_max, total = con.execute(
            "SELECT COUNT(*) FILTER (WHERE claim_id < ?), COUNT(*) FROM snap_003.fact_claim",
            [first_injected],
        ).fetchone()  # type: ignore[misc]
    finally:
        con.close()
    assert base_max > 0
    assert total > base_max, "no injected claims were appended"


# ---------------------------------------------------------------------------
# full-scale population (reference dataset)


@pytest.fixture(scope="module")
def full_key() -> dict:
    db = REPO_ROOT / "data" / "revi_warehouse.duckdb"
    key_path = REPO_ROOT / "data" / "answer_key.json"
    if not db.exists() or not key_path.exists():
        pytest.skip("full-scale warehouse not generated; run `make warehouse` first")
    key = json.loads(key_path.read_text())
    if key["config"]["scale"] != "full":
        pytest.skip("data/ warehouse was generated at small scale")
    return key


@pytest.mark.reference
def test_full_scale_population_is_complete(full_key: dict) -> None:
    at_003 = {r["anomaly_id"] for r in full_key["anomalies"]["snap_003"]}
    assert at_003 == {s.spec_id for s in ANOMALY_SPECS} - SELF_RESOLVING_IDS


@pytest.mark.reference
def test_full_scale_magnitude_profile(full_key: dict) -> None:
    """$100k+ headliners down to sub-$1k noise, all four severities present."""
    rows = full_key["anomalies"]["snap_003"]
    impacts = sorted(r["impact_cents"] for r in rows)
    assert sum(1 for i in impacts if i >= 10_000_000) >= 3  # $100k+
    assert sum(1 for i in impacts if i < 100_000) >= 3  # sub-$1k
    assert {r["severity"] for r in rows} == {"critical", "high", "medium", "low"}


@pytest.mark.reference
def test_full_scale_visibility_profile(full_key: dict) -> None:
    seen = [{r["anomaly_id"] for r in full_key["anomalies"][s]} for s in SNAP_NAMES]
    assert seen[2] - seen[1], "no anomaly first became visible at snap_003"
    assert seen[1] - seen[0], "no anomaly first became visible at snap_002"
    for earlier, later in itertools.pairwise(seen):
        assert not {a for a in earlier - later if a not in SELF_RESOLVING_IDS}
    assert seen[1] >= SELF_RESOLVING_IDS
    assert not (SELF_RESOLVING_IDS & seen[2])


@pytest.mark.reference
def test_full_scale_five_scenarios_unchanged_by_injection(full_key: dict) -> None:
    """The reference-week cash totals are the load-bearing anchors."""
    s3 = full_key["scenarios"]["3_cash_decline"]["snap_003"]
    assert s3["week_prior"]["payer_cash_cents"] == 152_196_731
    assert s3["week_decline"]["payer_cash_cents"] == 132_844_152
