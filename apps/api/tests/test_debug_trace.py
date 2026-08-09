"""The debug projection: a faithful read of the trace, guarded on the way
out.

Two properties matter here and both are adversarial. First, the payload
must report what the turn *recorded* — a debug view that recomputed
anything could disagree with the answer it claims to explain. Second,
nothing the outbound-payload guard considers sensitive may leave the
process through this door, and anything withheld must be visible as a
redaction rather than as an innocent-looking gap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from revi_api.debug_trace import REDACTED, build_debug_trace
from revi_investigation.application.ports import TraceRecord


def _record(**payload: Any) -> TraceRecord:
    base: dict[str, Any] = {
        "tenant": "demo",
        "question": "Why did cash decline last week?",
        "settings": {
            "model_tier": "claude-sonnet-5",
            "max_turn_cost_usd": "0.25",
            "narrative_depth": "analyst",
            "evidence_depth": "deep",
            "debug": True,
        },
        "pack": {"id": "base-rcm", "version": "1.0.0", "snapshot_id": "snap_abc"},
        "watermark": {
            "id": "wm_003",
            "loaded_at": "2026-08-03T04:10:00",
            "newest_data_date": "2026-08-02",
        },
        "watermark_stale": False,
        "epoch": {"index": 1, "watermark": "wm_003", "re_anchored": True},
        "classification": {"turn_class": "new_investigation", "confidence": 0.94},
        "interpretation": {
            "intent_summary": "cash decline by payer",
            "metric_ids": ["cash_posted"],
            "dimension_ids": ["payer"],
            "concept_ids": [],
            "playbook_id": "cash_decline",
            "window": {"start": "2026-07-27", "end": "2026-08-02", "basis": "post"},
        },
        "plan_hash": "abc123",
        "plan_context": {
            "playbook_id": "cash_decline",
            "window_explicit": True,
            "evidence_depth": "deep",
        },
        "probes": [
            {
                "id": "main",
                "hash": "h1",
                "purpose": "playbook probe main",
                "cache_hit": False,
                "rows": 12,
                "limit": 48,
                "truncated": False,
                "suppressed_cells": 2,
                "grade": "direct",
                "duration_ms": 31,
            }
        ],
        "grades": {"main": "direct", "context": "proxy"},
        "finding_grades": {"F1": "direct"},
        "operators": [{"operator": "compare", "version": 1, "inputs": ["main"], "output": "c"}],
        "warnings": ["alternate basis used"],
        "clarification": None,
        "clarification_reason": None,
        "llm": [
            {
                "template": "classify_turn",
                "model": "claude-sonnet-5",
                "cost_usd": "0.02",
                "input_tokens": 900,
                "output_tokens": 40,
                "schema_retries": 1,
                "attempts": 2,
                "duration_ms": 1200,
                "failure": "schema",
            }
        ],
        "template_hashes": {"classify_turn@v1": "deadbeef"},
        "timings_ms": {"classify": 1200, "plan": 3},
    }
    base.update(payload)
    return TraceRecord(
        trace_id="trace_1",
        session_id="sess_1",
        investigation_id="inv_1",
        turn_id="turn_1",
        created_at=datetime(2026, 8, 8, tzinfo=UTC),
        payload=base,
    )


class TestProjection:
    def test_it_reports_what_the_turn_recorded(self) -> None:
        debug = build_debug_trace(_record())

        assert debug.trace_id == "trace_1" and debug.investigation_id == "inv_1"
        assert debug.turn_class == "new_investigation"
        assert debug.classification_confidence == pytest.approx(0.94)
        assert debug.plan_hash == "abc123"
        assert debug.playbook_id == "cash_decline"
        assert debug.pack_version == "1.0.0" and debug.pack_snapshot_id == "snap_abc"
        assert debug.watermark_id == "wm_003"
        assert debug.epoch == 1 and debug.re_anchored is True
        assert debug.timings_ms == {"classify": 1200, "plan": 3}
        assert debug.template_hashes == {"classify_turn@v1": "deadbeef"}

    def test_settings_in_force_travel_with_the_turn(self) -> None:
        debug = build_debug_trace(_record())

        assert debug.settings.model_tier == "claude-sonnet-5"
        assert debug.settings.max_turn_cost_usd == "0.25"
        assert debug.settings.narrative_depth == "analyst"
        assert debug.settings.evidence_depth == "deep"
        assert debug.settings.debug is True

    def test_probes_carry_what_they_actually_read(self) -> None:
        [probe] = build_debug_trace(_record()).probes

        assert probe.id == "main" and probe.hash == "h1"
        assert probe.rows == 12 and probe.limit == 48
        assert probe.suppressed_cells == 2 and probe.duration_ms == 31
        assert probe.grade == "direct" and probe.cache_hit is False

    def test_llm_calls_carry_model_cost_and_failure_kind(self) -> None:
        [call] = build_debug_trace(_record()).llm_calls

        assert call.template == "classify_turn"
        assert call.model == "claude-sonnet-5"
        assert call.cost_usd == "0.02"
        assert call.schema_retries == 1 and call.attempts == 2
        assert call.failure == "schema"

    def test_the_weakest_node_grade_is_stated_not_implied(self) -> None:
        """The grade law (§5.3) applied to the recorded node grades: this
        is the ceiling on what the answer may claim, and a reader should
        not have to derive it from a dict."""
        assert build_debug_trace(_record()).weakest_grade == "proxy"

    def test_a_trace_with_no_grades_claims_none(self) -> None:
        assert build_debug_trace(_record(grades={})).weakest_grade is None

    def test_an_older_trace_without_settings_reads_as_defaults(self) -> None:
        """A record written before the settings field existed ran under the
        defaults — which is exactly what reporting the defaults says."""
        debug = build_debug_trace(_record(settings={}))

        assert debug.settings.model_tier is None
        assert debug.settings.debug is False


class TestGuard:
    def test_a_question_the_guard_rejects_never_leaves(self) -> None:
        debug = build_debug_trace(
            _record(question="check /Users/dev/secrets/patients.csv for the decline")
        )

        assert debug.question == REDACTED
        assert any(field.startswith("question") for field in debug.redactions)

    def test_a_warning_carrying_a_connection_string_is_withheld(self) -> None:
        debug = build_debug_trace(
            _record(warnings=["ok", "source postgresql://user:pw@host/db unreachable"])
        )

        assert debug.warnings[0] == "ok"
        assert debug.warnings[1] == REDACTED
        assert debug.redactions == ["warnings[1] (credentialed_url)"]

    def test_model_written_prose_is_guarded_too(self) -> None:
        debug = build_debug_trace(
            _record(
                interpretation={
                    "intent_summary": "api_key: sk-live-not-a-real-key",
                    "metric_ids": ["cash_posted"],
                    "dimension_ids": [],
                    "concept_ids": [],
                    "playbook_id": None,
                    "window": {},
                }
            )
        )

        assert debug.interpretation is not None
        assert debug.interpretation.intent_summary == REDACTED
        # the ids beside it are platform vocabulary and travel intact
        assert debug.interpretation.metric_ids == ["cash_posted"]
        assert debug.redactions == ["interpretation.intent_summary (secret_assignment)"]

    def test_clean_text_is_not_touched(self) -> None:
        debug = build_debug_trace(_record())

        assert debug.question == "Why did cash decline last week?"
        assert debug.redactions == []
