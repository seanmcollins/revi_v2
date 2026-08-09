"""Semantic catalog YAML: parseable, complete, and internally consistent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"

VALID_PHI = {"none", "indirect", "direct"}
VALID_AGGS = {"sum", "count", "count_distinct"}
ENTITY_NAMES = {"claim", "claim_line", "transaction", "remit", "denial"}


def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((CATALOG_DIR / name).read_text())
    assert isinstance(data, dict) and data["version"] == 1
    return data


def test_entities_yaml() -> None:
    data = _load("entities.yaml")
    entities = data["entities"]
    assert set(entities) == ENTITY_NAMES
    for name, spec in entities.items():
        assert spec["base_view"].startswith("v_"), name
        assert spec["primary_key"]
        assert spec["grain"] in {"CLAIM", "LINE", "TRANSACTION", "REMIT"}
    for path in data["join_paths"]:
        assert path["from"] in ENTITY_NAMES and path["to"] in ENTITY_NAMES


def test_date_bases_yaml() -> None:
    data = _load("date_bases.yaml")
    bases = data["date_bases"]
    assert set(bases) == {"SERVICE", "POST", "SUBMISSION", "REMIT", "DISCHARGE"}
    for name, spec in bases.items():
        assert spec["columns"], name
        assert set(spec["columns"]) <= ENTITY_NAMES
    assert bases["POST"]["columns"]["transaction"] == "post_date"
    assert bases["SERVICE"]["columns"]["claim"] == "service_date"
    assert bases["DISCHARGE"]["columns"] == {"claim": "discharge_date"}


def test_dimensions_yaml() -> None:
    data = _load("dimensions.yaml")
    dims = data["dimensions"]
    certified = {k for k, v in dims.items() if v["certified"]}
    uncertified = {k for k, v in dims.items() if not v["certified"]}
    assert len(certified) == 26, sorted(certified)
    assert uncertified == {"rarc_synthetic", "revenue_code"}
    # Both derived buckets declare their labels; nothing else may claim the kind.
    derived = {k for k, v in dims.items() if v.get("kind") == "derived_bucket"}
    assert derived == {"ar_age_bucket", "filing_runway_bucket"}
    for name in derived:
        assert dims[name]["buckets"], name
        assert dims[name]["certified"], name
    assert {"expired", "filed"} <= set(dims["filing_runway_bucket"]["buckets"])
    # The three claim-level boolean flags: a filter predicate needs a
    # dimension, so `submission_date IS NULL` is only expressible once the
    # catalog certifies billed_flag. The dates stay date bases.
    flags = {"first_pass_paid", "billed_flag", "discharged_flag"}
    assert flags <= certified
    for flag in flags:
        assert dims[flag]["entities"].keys() == {"claim"}, flag
        assert dims[flag]["cardinality_estimate"] == 2, flag
        assert dims[flag]["description"].strip(), flag
    assert not flags & set(_load("date_bases.yaml")["date_bases"])
    for name, spec in dims.items():
        assert spec["synonyms"], f"{name} needs synonyms"
        assert isinstance(spec["cardinality_estimate"], int) and spec["cardinality_estimate"] > 0
        assert spec["phi"] in VALID_PHI, name
        assert set(spec.get("entities", {})) <= ENTITY_NAMES, name
        if not spec["certified"]:
            assert spec["uncertified_reason"], name
    # Epic/Athena-style aliases the interpreter depends on must be present.
    assert "DOS" in _load("date_bases.yaml")["date_bases"]["SERVICE"]["synonyms"]
    assert "fin class" in dims["financial_class"]["synonyms"]
    assert "svc line" in dims["service_line"]["synonyms"]
    assert "reason code" in dims["carc"]["synonyms"]
    assert "rmt payer" in dims["payer"]["synonyms"]


def test_measures_yaml() -> None:
    data = _load("measures.yaml")
    measures = data["measures"]
    required = {
        "billed_amount_cents",
        "expected_amount_cents",
        "allowed_amount_cents",
        "payment_cents",
        "patient_payment_cents",
        "contractual_adj_cents",
        "other_adj_cents",
        "refund_cents",
        "denied_amount_cents",
        "patient_responsibility_cents",
        "claim_count",
        "line_count",
        "denial_count",
        "appeal_count",
        "appeal_overturned_count",
        "appeal_upheld_count",
    }
    assert required <= set(measures), sorted(required - set(measures))
    for name, spec in measures.items():
        assert spec["entity"] in ENTITY_NAMES, name
        assert spec["aggregation"] in VALID_AGGS, name
        assert spec["unit"] in {"cents", "count"}, name
        assert isinstance(spec["additive"], bool), name
        assert spec["column"], name


def test_calendar_yaml() -> None:
    data = _load("calendar.yaml")
    cal = data["calendar"]
    assert cal["table"] == "dim_calendar"
    assert cal["date_column"] == "cal_date"
    assert cal["week_convention"].startswith("ISO")
    assert len(cal["holidays"]) >= 10
    assert {"CALENDAR_DAY", "BUSINESS_DAY"} <= set(cal["policies"])


def test_every_plan_resolves_against_the_packs_filing_rules() -> None:
    """The claim → plan → filing rule join, checked at the rule table.

    Two separate claims, both worth pinning:

    1. **Coverage.** Every one of the 30 plans matches some rule in the pack's
       ladder (the trailing ``*`` guarantees it, but a future edit could drop
       it), and every plan carries its own limit + basis in ``dim_plan`` — which
       is what the compiler actually reads, because a source adapter cannot see
       pack content and the plan record is the most specific rule available.
    2. **Agreement where the pack claims to mirror the plan record.** The
       ``plan_configuration`` tier is documented in filing_rules.yaml as
       mirroring dim_plan, so those rules must agree with it to the day. The
       coarser tiers are payer-pattern defaults and are allowed to differ; the
       ``requires_confirmation`` flag on them is exactly the statement that
       they might.
    """
    import fnmatch

    from revi_warehouse.dims import PLANS

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

    assert len(PLANS) == 30
    needs_confirmation = 0
    for payer_name, plan_name, _product, limit_days, basis, _weight in PLANS:
        assert limit_days > 0, plan_name
        assert basis in {"SERVICE", "SUBMISSION"}, plan_name
        rule = matched(payer_name, plan_name)
        if rule["authority"] == "plan_configuration":
            assert rule["filing_limit_days"] == limit_days, plan_name
            assert rule["date_basis"].upper() == basis, plan_name
            assert rule["requires_confirmation"] is False, rule["id"]
        if rule["requires_confirmation"]:
            needs_confirmation += 1
    # The number the contract's population caveat publishes. 23 of 30 plans fall
    # through to a tier the pack marks "confirm against the payer contract".
    assert needs_confirmation == 23


def test_calendar_yaml_matches_the_generated_calendar() -> None:
    """The published range and holiday list are the generator's, not a copy that drifts."""
    from revi_warehouse.config import CALENDAR_END, CALENDAR_START, day
    from revi_warehouse.dims import SYNTHETIC_HOLIDAYS

    cal = _load("calendar.yaml")["calendar"]
    assert day(cal["range"]["start"].isoformat()) == CALENDAR_START
    assert day(cal["range"]["end"].isoformat()) == CALENDAR_END
    assert [h.isoformat() for h in cal["holidays"]] == list(SYNTHETIC_HOLIDAYS)
