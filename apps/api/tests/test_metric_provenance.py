"""The governed-provenance projection behind the "Governed" badge.

The badge is a trust surface: it asserts that a HUMAN authored and
versioned the definition behind the number on screen. So the adversarial
question is not "does it populate?" but "can it name a contract the turn
did not use, at a version the turn did not read, from a pack the turn was
not pinned to?" — and, the subtler one, "can it elect a headline metric
on a turn that ran four?".

It is also the *third* reader of one trace record (:mod:`revi_api.debug_trace`
and :mod:`revi_api.evidence` are the others), so the facts they share are
asserted identical rather than merely plausible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from revi_api.debug_trace import build_debug_trace
from revi_api.evidence import build_evidence
from revi_api.metric_provenance import build_metric_provenance
from revi_investigation.application.ports import TraceRecord

PACK = {"id": "base-rcm", "version": "1.0.0", "snapshot_id": "snap_003"}


def _record(**payload: Any) -> TraceRecord:
    base: dict[str, Any] = {
        "tenant": "demo",
        "question": "Why did cash decline last week?",
        "pack": dict(PACK),
        "interpretation": {
            "intent_summary": "last week's posted-cash decline by payer",
            "metric_ids": ["cash_posted"],
            "dimension_ids": ["payer"],
            "concept_ids": [],
            "playbook_id": None,
            "window": {"start": "2026-07-27", "end": "2026-08-02", "basis": "post"},
        },
        "plan_context": {"playbook_id": None, "window_explicit": True},
        "probes": [
            {
                "id": "cash_by_payer",
                "hash": "d94855a5",
                "purpose": "Decline week versus prior week by payer.",
                "kind": "aggregation",
                "metrics": [{"id": "cash_posted", "contract_version": 3}],
                "cache_hit": False,
                "rows": 12,
                "grade": "direct",
                "duration_ms": 31,
            }
        ],
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


class TestTheGoverningMetric:
    def test_the_interpretation_s_first_metric_is_the_primary(self) -> None:
        """``metric_ids[0]`` is the engine's own ``governing[0]`` — the
        contract it took the entity grain and the default date basis
        from. The badge must name that one, not whichever probe happened
        to be planned first."""
        provenance = build_metric_provenance(_record())
        assert provenance.primary is not None
        assert (provenance.primary.id, provenance.primary.contract_version) == ("cash_posted", 3)

    def test_the_version_is_the_one_stamped_on_the_executed_frame(self) -> None:
        """Not the version the plan asked for: those differ exactly when a
        pack promotion lands mid-session, which is the case worth seeing."""
        record = _record(
            probes=[
                {
                    "id": "cash_by_payer",
                    "hash": "h",
                    "metrics": [{"id": "cash_posted", "contract_version": 4}],
                    "rows": 12,
                }
            ]
        )
        primary = build_metric_provenance(record).primary
        assert primary is not None and primary.contract_version == 4

    def test_an_interpreted_metric_no_probe_read_is_named_without_a_version(self) -> None:
        """A turn that stopped before executing still resolved a governing
        contract. Naming it with a ``None`` version is honest; inventing a
        version, or dropping the metric, is not."""
        record = _record(probes=[])
        provenance = build_metric_provenance(record)
        assert provenance.primary is not None
        assert provenance.primary.id == "cash_posted"
        assert provenance.primary.contract_version is None
        assert provenance.metrics == []

    def test_a_sole_probe_metric_is_the_primary_when_nothing_was_interpreted(self) -> None:
        """A refinement records no interpretation — it inherits its
        parent's spec. With exactly one metric read all turn, naming it is
        a reading of the record, not a choice among candidates."""
        record = _record(interpretation=None)
        provenance = build_metric_provenance(record)
        assert provenance.primary is not None
        assert (provenance.primary.id, provenance.primary.contract_version) == ("cash_posted", 3)


class TestAPlaybookTurnIsNotCollapsedIntoOneMetric:
    """The reference first turn runs the ``cash_decline`` playbook, which
    names no governing metric and reads four. Electing one of them the
    headline would be the badge asserting a contract the turn never
    designated — the exact overclaim it exists to prevent."""

    def _playbook_record(self) -> TraceRecord:
        return _record(
            interpretation={
                "intent_summary": "posted-cash decline",
                "metric_ids": [],
                "dimension_ids": ["payer"],
                "concept_ids": [],
                "playbook_id": "cash_decline",
                "window": {"start": "2026-07-27", "end": "2026-08-02", "basis": "post"},
            },
            plan_context={"playbook_id": "cash_decline", "window_explicit": True},
            probes=[
                {
                    "id": "weekly_cash_trend",
                    "hash": "h1",
                    "metrics": [{"id": "cash_posted", "contract_version": 3}],
                    "rows": 8,
                },
                {
                    "id": "cash_by_payer",
                    "hash": "h2",
                    "metrics": [{"id": "cash_posted", "contract_version": 3}],
                    "rows": 12,
                },
                {
                    "id": "submission_volume_by_payer",
                    "hash": "h3",
                    "metrics": [{"id": "claim_volume", "contract_version": 1}],
                    "rows": 12,
                },
                {
                    "id": "lag_distribution_compare",
                    "hash": "h4",
                    "metrics": [{"id": "avg_days_to_pay", "contract_version": 2}],
                    "rows": 12,
                },
            ],
        )

    def test_no_primary_is_elected(self) -> None:
        assert build_metric_provenance(self._playbook_record()).primary is None

    def test_every_metric_it_read_is_published_in_plan_order(self) -> None:
        provenance = build_metric_provenance(self._playbook_record())
        assert [(m.id, m.contract_version) for m in provenance.metrics] == [
            ("cash_posted", 3),
            ("claim_volume", 1),
            ("avg_days_to_pay", 2),
        ]

    def test_the_playbook_that_chose_them_is_named(self) -> None:
        assert build_metric_provenance(self._playbook_record()).playbook_id == "cash_decline"


class TestPackFactsAreTheTurnSOwn:
    def test_the_recorded_pack_version_and_snapshot_travel(self) -> None:
        """Read off the trace, never off the pack as it stands now: a pack
        promoted since the turn ran must not relabel it."""
        provenance = build_metric_provenance(_record())
        assert provenance.pack_id == "base-rcm"
        assert provenance.pack_version == "1.0.0"
        assert provenance.pack_snapshot_id == "snap_003"

    def test_a_trace_with_no_pack_block_claims_no_pack(self) -> None:
        provenance = build_metric_provenance(_record(pack={}))
        assert (provenance.pack_id, provenance.pack_version, provenance.pack_snapshot_id) == (
            "",
            "",
            "",
        )


class TestATurnThatMeasuredNothingSaysSo:
    def test_a_definitional_turn_publishes_an_empty_block(self) -> None:
        """No metrics, no primary, no playbook — and the pack facts still
        travel, because which pack was pinned is a fact about the turn even
        when the turn read no metric. The badge renders nothing from this;
        it must not render "governed by cash_posted"."""
        record = _record(interpretation=None, plan_context={}, probes=[])
        provenance = build_metric_provenance(record)
        assert provenance.primary is None
        assert provenance.metrics == []
        assert provenance.playbook_id is None
        assert provenance.pack_version == "1.0.0"

    def test_a_metric_with_no_id_is_dropped_rather_than_published_blank(self) -> None:
        record = _record(probes=[{"id": "p", "hash": "h", "metrics": [{"id": ""}], "rows": 1}])
        assert build_metric_provenance(record).metrics == []


class TestVersionsAreNotLostToDeduplication:
    def test_a_pruned_probe_does_not_erase_the_version_an_executed_one_stamped(self) -> None:
        """Same metric, named by a node that never ran and read by one that
        did. Taking the first entry blindly would report the metric as
        version-less on a turn that read it at version 3."""
        record = _record(
            probes=[
                {"id": "planned", "hash": "h1", "metrics": [{"id": "cash_posted"}], "rows": None},
                {
                    "id": "executed",
                    "hash": "h2",
                    "metrics": [{"id": "cash_posted", "contract_version": 3}],
                    "rows": 12,
                },
            ]
        )
        metrics = build_metric_provenance(record).metrics
        assert [(m.id, m.contract_version) for m in metrics] == [("cash_posted", 3)]


class TestOneRecordThreeProjections:
    """Built beside the evidence bundle and the debug trace rather than out
    of the frames, so the badge cannot disagree with the drawer or the
    trace. Asserted on the fields they share."""

    def test_the_metrics_match_the_evidence_bundle_s_probe_metrics(self) -> None:
        record = _record()
        provenance = build_metric_provenance(record)
        from_evidence = [
            (metric.id, metric.contract_version)
            for probe in build_evidence(record).probes
            for metric in probe.metrics
        ]
        assert [(m.id, m.contract_version) for m in provenance.metrics] == from_evidence

    def test_the_pack_facts_match_the_debug_trace(self) -> None:
        record = _record()
        provenance = build_metric_provenance(record)
        debug = build_debug_trace(record)
        assert (provenance.pack_id, provenance.pack_version, provenance.pack_snapshot_id) == (
            debug.pack_id,
            debug.pack_version,
            debug.pack_snapshot_id,
        )

    def test_the_primary_is_the_interpretation_the_debug_trace_publishes(self) -> None:
        record = _record()
        provenance = build_metric_provenance(record)
        debug = build_debug_trace(record)
        assert debug.interpretation is not None
        assert provenance.primary is not None
        assert provenance.primary.id == debug.interpretation.metric_ids[0]
