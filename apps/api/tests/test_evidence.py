"""The analyst-facing evidence projection.

The bundle is the drawer's whole contents, so the adversarial question is
not "does it render?" but "can it say something the turn did not do?".
Three ways it could: counting a probe that never ran as a query, reporting
a reconciliation the engine never reached, or filling a gap with a
reassuring default. Each has a test below.

It is also the *second* reader of one trace record (the first is
:mod:`revi_api.debug_trace`), so the fields they share are asserted to
come back identical rather than merely plausible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from revi_api.debug_trace import build_debug_trace
from revi_api.evidence import build_evidence, parse_reconciliation
from revi_investigation.application.ports import TraceRecord


def _record(**payload: Any) -> TraceRecord:
    base: dict[str, Any] = {
        "tenant": "demo",
        "question": "Why did cash decline last week?",
        "probes": [
            {
                "id": "cash_by_payer",
                "hash": "d94855a5",
                "purpose": "Decline week versus prior week by payer.",
                "kind": "aggregation",
                "metrics": [{"id": "cash_posted", "contract_version": 1}],
                "cache_hit": False,
                "rows": 12,
                "limit": 12,
                "truncated": False,
                "suppressed_cells": 2,
                "grade": "direct",
                "duration_ms": 31,
            },
            {
                "id": "cash_by_payer__prior",
                "hash": "ee0dfca3",
                "purpose": "comparison baseline",
                "kind": "aggregation",
                "metrics": [{"id": "cash_posted", "contract_version": 1}],
                "cache_hit": True,
                "rows": 12,
                "grade": "direct",
                "duration_ms": 0,
            },
        ],
        "grades": {"cash_by_payer": "direct"},
        "finding_grades": {"F1": "direct", "F2": "proxy"},
        "reconciliation": "status=passed",
    }
    base.update(payload)
    return TraceRecord(
        trace_id="tr_1",
        session_id="s_1",
        investigation_id="inv_1",
        turn_id="t_1",
        created_at=datetime(2026, 8, 3, 12, tzinfo=UTC),
        payload=base,
    )


class TestProbesAreReportedAsRecorded:
    def test_every_recorded_field_survives_the_projection(self) -> None:
        evidence = build_evidence(_record())
        probe = evidence.probes[0]
        assert probe.id == "cash_by_payer"
        assert probe.hash == "d94855a5"
        assert probe.purpose == "Decline week versus prior week by payer."
        assert probe.kind == "aggregation"
        assert [(m.id, m.contract_version) for m in probe.metrics] == [("cash_posted", 1)]
        assert probe.rows == 12
        assert probe.limit == 12
        assert probe.suppressed_cells == 2
        assert probe.grade == "direct"
        assert probe.duration_ms == 31

    def test_cache_hits_are_not_counted_as_warehouse_queries(self) -> None:
        evidence = build_evidence(_record())
        assert evidence.cache_hits == 1
        assert evidence.warehouse_queries == 1
        assert evidence.zero_probe_turn is False

    def test_a_turn_served_entirely_from_cache_claims_no_new_queries(self) -> None:
        record = _record(
            probes=[
                {"id": "a", "hash": "h", "cache_hit": True, "rows": 3},
                {"id": "b", "hash": "h2", "cache_hit": True, "rows": 4},
            ]
        )
        evidence = build_evidence(record)
        assert evidence.zero_probe_turn is True
        assert (evidence.cache_hits, evidence.warehouse_queries) == (2, 0)

    def test_a_planned_but_unexecuted_probe_is_not_a_query(self) -> None:
        """``rows: None`` is the recorded shape of "planned, never ran".

        Counting it would report a warehouse read that did not happen, and
        on a turn where *every* probe was pruned it would deny the answer
        its "no new queries" claim — wrong in both directions from one
        careless ``len()``."""
        record = _record(
            probes=[{"id": "a", "hash": "h", "cache_hit": False, "rows": None}],
        )
        evidence = build_evidence(record)
        assert evidence.probes[0].rows is None
        assert (evidence.warehouse_queries, evidence.cache_hits) == (0, 0)
        assert evidence.zero_probe_turn is True

    def test_a_turn_with_no_probes_at_all_is_a_zero_probe_turn(self) -> None:
        evidence = build_evidence(_record(probes=[], finding_grades={}))
        assert evidence.probes == []
        assert evidence.zero_probe_turn is True
        assert evidence.answer_grade is None


class TestReconciliationIsParsedNeverJudged:
    @pytest.mark.parametrize(
        ("summary", "status", "detail"),
        [
            ("status=passed", "passed", None),
            ("status=passed_with_suppression", "passed_with_suppression", None),
            (
                "status=failed; failed measures: cash_posted",
                "failed",
                "failed measures: cash_posted",
            ),
            (
                "status=not_applicable; reason=this is a first turn",
                "not_applicable",
                "this is a first turn",
            ),
        ],
    )
    def test_the_recorded_grammar_splits_into_status_and_detail(
        self, summary: str, status: str, detail: str | None
    ) -> None:
        parsed = parse_reconciliation(summary)
        assert parsed is not None
        assert (parsed.status, parsed.detail) == (status, detail)
        assert parsed.summary == summary, "the recorded string travels verbatim"

    def test_an_unrecognized_summary_is_reported_not_coerced(self) -> None:
        """A stored string this reader cannot parse must not become
        ``passed``. It is surfaced under ``unknown`` with the original
        text, because a wrong reassurance is worse than an odd label."""
        parsed = parse_reconciliation("reconciled ok")
        assert parsed is not None
        assert parsed.status == "unknown"
        assert parsed.detail == "reconciled ok"

    def test_no_recorded_verdict_is_absence_not_not_applicable(self) -> None:
        """Distinct facts: "the check was reached and declined, here is
        why" versus "no check was recorded at all" (a META citation, a
        kernel-only refinement). Collapsing them is the ambiguity the
        engine's ``_not_applicable`` was written to remove."""
        assert build_evidence(_record(reconciliation=None)).reconciliation is None
        assert parse_reconciliation(None) is None
        assert parse_reconciliation("   ") is None

    def test_a_trace_written_under_the_older_shape_still_reads(self) -> None:
        """``reconciliation`` used to live only under ``refinement``."""
        record = _record(reconciliation=None, refinement={"reconciliation": "status=passed"})
        parsed = build_evidence(record).reconciliation
        assert parsed is not None and parsed.status == "passed"


class TestAnswerGrade:
    def test_the_grade_law_is_applied_to_the_recorded_finding_grades(self) -> None:
        # §5.3: the weakest link caps the claim — proxy beats direct down.
        assert build_evidence(_record()).answer_grade == "proxy"

    def test_a_grade_this_build_does_not_know_is_skipped_not_fatal(self) -> None:
        record = _record(finding_grades={"F1": "direct", "F2": "telepathic"})
        assert build_evidence(record).answer_grade == "direct"


class TestOneRecordTwoProjections:
    """The point of building this beside the debug view rather than out of
    the frames: the numbers cannot disagree, because there is only one
    source. Asserted field by field on the fields they share."""

    def test_probe_facts_are_identical_in_both_projections(self) -> None:
        record = _record()
        evidence = build_evidence(record)
        debug = build_debug_trace(record)
        assert len(evidence.probes) == len(debug.probes)
        for shown, traced in zip(evidence.probes, debug.probes, strict=True):
            assert shown.id == traced.id
            assert shown.hash == traced.hash
            assert shown.purpose == traced.purpose
            assert shown.kind == traced.kind
            assert shown.cache_hit == traced.cache_hit
            assert shown.rows == traced.rows
            assert shown.limit == traced.limit
            assert shown.truncated == traced.truncated
            assert shown.suppressed_cells == traced.suppressed_cells
            assert shown.grade == traced.grade
            assert shown.duration_ms == traced.duration_ms

    def test_the_reconciliation_summary_is_the_same_string(self) -> None:
        record = _record()
        evidence = build_evidence(record)
        assert evidence.reconciliation is not None
        assert evidence.reconciliation.summary == build_debug_trace(record).reconciliation
