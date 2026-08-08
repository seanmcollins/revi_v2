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
    assert len(certified) == 20, sorted(certified)
    assert uncertified == {"rarc_synthetic", "revenue_code"}
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
