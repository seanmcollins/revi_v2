"""The schema-constrained LLM boundary: schemas, guard, templates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.llm.render import load_template, render_template
from revi_investigation.application.llm.schemas import (
    InterpretationResponse,
    RefinementEmissionResponse,
    TurnClassificationResponse,
    sanitize_json_schema,
)
from revi_kernel.errors import PolicyDeniedError


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(_contains_key(v, key) for v in value)
    return False


class TestSchemas:
    def test_refinement_union_schema_has_no_discriminator_keyword(self) -> None:
        raw = RefinementEmissionResponse.model_json_schema()
        assert _contains_key(raw, "discriminator")  # pydantic emits it...
        clean = sanitize_json_schema(raw)
        assert not _contains_key(clean, "discriminator")  # ...and we strip it
        # lossless: the union variants are still pinned
        assert _contains_key(clean, "oneOf") or _contains_key(clean, "anyOf")

    def test_sanitize_strips_recursively_and_preserves_everything_else(self) -> None:
        nested = {
            "discriminator": {"propertyName": "op"},
            "oneOf": [{"properties": {"discriminator": {"type": "string"}}}],
            "items": [{"discriminator": "x", "keep": True}],
        }
        clean = sanitize_json_schema(nested)
        assert not _contains_key(clean, "discriminator")
        assert clean["items"][0]["keep"] is True

    def test_refinement_union_parses_all_twelve_ops(self) -> None:
        payload = {
            "rationale": "drill into the two payers and cut by code monthly",
            "operators": [
                {"op": "set_dimensions", "dimensions": ["carc"]},
                {"op": "add_filter", "dimension": "payer", "predicate_op": "eq", "values": ["A"]},
                {"op": "remove_filter", "dimension": "payer"},
                {
                    "op": "set_window",
                    "window": {"quantity": "3.25", "unit": "month", "mode": "trailing"},
                    "basis": "post",
                },
                {"op": "set_comparison", "kind": "prior_period"},
                {"op": "set_grain", "entity": "claim", "time_bucket": "month"},
                {"op": "drill_into", "target": "F1"},
                {"op": "pivot", "measures": ["denied_dollars"]},
                {"op": "explain", "target": "F2"},
                {"op": "rank_by", "by": "impact_cents", "descending": True},
                {"op": "expand", "limit": 10},
                {"op": "reset_context", "keep_pins": True},
            ],
        }
        parsed = RefinementEmissionResponse.model_validate(payload)
        assert len(parsed.operators) == 12

    def test_unknown_operator_and_extra_keys_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RefinementEmissionResponse.model_validate(
                {"rationale": "x", "operators": [{"op": "delete_everything"}]}
            )
        with pytest.raises(ValidationError):
            TurnClassificationResponse.model_validate(
                {"turn_class": "new_investigation", "confidence": 0.9, "bonus": 1}
            )

    def test_interpretation_response_defaults(self) -> None:
        parsed = InterpretationResponse.model_validate({"intent_summary": "x"})
        assert parsed.metric_ids == [] and parsed.playbook_id is None
        assert parsed.window is None and parsed.scope == []


class TestGuard:
    def test_vocabulary_prompt_passes(self) -> None:
        assert_safe_payload(
            "Metrics:\n- cash_posted: payer payment dollars posted in the window\n"
            "Dimensions:\n- payer: Payer\nQuestion:\nWhy did cash decline last week?"
        )

    @pytest.mark.parametrize(
        "payload",
        [
            "postgresql://revi:hunter2@db.internal:5432/revi",
            "see duckdb:///warehouse.db for the data",
            "password=hunter2",
            "api_key: sk-abc123",
            "read /Users/dev/revi_v2/data/revi_warehouse.duckdb",
            "member 123-45-6789 was denied",
            "creds AKIAABCDEFGHIJKLMNOP here",
        ],
    )
    def test_sensitive_patterns_rejected(self, payload: str) -> None:
        with pytest.raises(PolicyDeniedError):
            assert_safe_payload(payload)

    def test_raw_tabular_payload_rejected(self) -> None:
        table = "\n".join("A | B | C | D | E | F" for _ in range(10))
        with pytest.raises(PolicyDeniedError) as excinfo:
            assert_safe_payload(table)
        assert excinfo.value.details["rule"] == "tabular_payload"

    def test_serialized_row_array_rejected(self) -> None:
        rows = "[" + ",".join('{"payer": "P", "cents": 1}' for _ in range(12)) + "]"
        with pytest.raises(PolicyDeniedError) as excinfo:
            assert_safe_payload(rows)
        assert excinfo.value.details["rule"] == "row_payload"


class TestTemplates:
    def test_both_templates_load_with_stable_hashes(self) -> None:
        classify = load_template("classify_turn", "v1")
        interpret = load_template("interpret_question", "v1")
        assert classify.sha256 == load_template("classify_turn", "v1").sha256
        assert "{question}" in classify.text
        for placeholder in ("{metrics}", "{dimensions}", "{playbooks}", "{question}"):
            assert placeholder in interpret.text

    def test_missing_template_is_loud(self) -> None:
        with pytest.raises(LookupError):
            load_template("classify_turn", "v999")

    def test_render_is_strict_both_ways(self) -> None:
        assert render_template("Q: {question}", {"question": "hi"}) == "Q: hi"
        with pytest.raises(LookupError):
            render_template("Q: {question}", {})
        with pytest.raises(LookupError):
            render_template("Q: {question}", {"question": "hi", "unused": "x"})

    def test_non_placeholder_braces_pass_through(self) -> None:
        assert render_template('{"json": 1} and {q}', {"q": "ok"}) == '{"json": 1} and ok'
