"""Follow-up turns on a small generated warehouse: refinement mechanics,
zero-probe paths, gestures, pins, and watermark epochs (§7, §8.2-8.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.llm.schemas import (
    AddFilterModel,
    DrillIntoModel,
    RankByModel,
    SetDimensionsModel,
)
from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import InvestigationStatus, Session
from revi_investigation.domain.refinements import SetDimensions
from revi_kernel.errors import ReferentNotFoundError
from revi_kernel.filters import iter_predicates
from revi_kernel.watermark import WatermarkEpoch
from revi_testing.engine_wiring import WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

T1_QUESTION = "Why did cash decline last week?"


@pytest.fixture(scope="module")
def small_warehouse_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from revi_warehouse.config import GeneratorConfig
    from revi_warehouse.generate import run_generation

    out = tmp_path_factory.mktemp("warehouse") / "revi_small.duckdb"
    return run_generation(GeneratorConfig.small(), out).db_path


def _canned_t1(llm: MockLanguageModel) -> None:
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
        matcher=lambda p: T1_QUESTION in p,
    )
    llm.respond(
        "interpret_question",
        {
            "intent_summary": "cash decline",
            "metric_ids": [],
            "dimension_ids": ["payer"],
            "concept_ids": [],
            "playbook_id": "cash_decline",
            "window": {"quantity": "1", "unit": "week", "mode": "full_periods"},
            "basis": "post",
            "comparison": "prior_period",
            "scope": [],
            "clarification": None,
            "definitional_terms": [],
        },
    )


def _engine(small_warehouse_path: Path, llm: MockLanguageModel) -> WiredEngine:
    return build_duckdb_engine(warehouse_path=small_warehouse_path, llm=llm)


async def _run_t1(engine: WiredEngine) -> TurnOutcome:
    outcome = await engine.submit.submit(SubmitTurnRequest(tenant="demo", question=T1_QUESTION))
    assert outcome.investigation.status is InvestigationStatus.COMPLETE
    assert outcome.findings
    return outcome


class TestGestureAndKernelOnly:
    async def test_typed_gesture_skips_the_llm_entirely(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        calls_before = len(llm.structured_calls)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="(gesture)",
                session_id=t1.session.id,
                refinements=(SetDimensionsModel(op="set_dimensions", dimensions=["payer"]),),
            )
        )
        assert outcome.investigation.status is InvestigationStatus.COMPLETE
        assert len(llm.structured_calls) == calls_before  # no classify/resolve/emit
        assert outcome.investigation.parent_id == t1.investigation.id
        lineage = await engine.investigation_store.lineage(t1.session.id)
        assert lineage is not None
        [edge] = lineage.edges
        assert edge.operators == (SetDimensions((next(iter(t1.investigation.spec.dimensions)),)),)

    async def test_unchanged_probes_come_from_the_cache(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        executes_before = engine.repository.execute_count
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="(gesture)",
                session_id=t1.session.id,
                refinements=(SetDimensionsModel(op="set_dimensions", dimensions=["payer"]),),
            )
        )
        # identical probe set — the diff is empty and nothing re-executes
        assert engine.repository.execute_count == executes_before
        assert outcome.diff is not None
        assert outcome.diff.added == () and outcome.diff.removed == ()
        assert outcome.reconciliation is not None and "passed" in outcome.reconciliation

    async def test_kernel_only_rank_turn_executes_zero_probes(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        executes_before = engine.repository.execute_count
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="(gesture)",
                session_id=t1.session.id,
                refinements=(
                    RankByModel(op="rank_by", by="cash_posted__delta", descending=False),
                ),
            )
        )
        assert outcome.investigation.status is InvestigationStatus.COMPLETE
        assert engine.repository.execute_count == executes_before  # zero probes
        trace = await engine.trace_store.get(outcome.trace_id)
        assert trace is not None
        assert trace.payload["refinement"]["kernel_only"] is True
        assert any(fid.endswith("__rank") for fid, _ in outcome.frames)

    async def test_unknown_referent_in_gesture_is_typed_error(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        with pytest.raises(ReferentNotFoundError):
            await engine.submit.submit(
                SubmitTurnRequest(
                    tenant="demo",
                    question="(gesture)",
                    session_id=t1.session.id,
                    refinements=(DrillIntoModel(op="drill_into", target="F99"),),
                )
            )


class TestConflictAndClarification:
    async def test_context_conflict_is_a_clarification_not_an_error(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        # drill into F1 → the context now carries that payer's cohort
        drill = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="(gesture)",
                session_id=t1.session.id,
                refinements=(DrillIntoModel(op="drill_into", target="F1"),),
            )
        )
        assert drill.investigation.spec.context.cohort is not None
        entry = await engine.referent_registry.resolve(t1.session.id, t1.findings[0].referent)
        assert entry is not None and entry.dimension_value is not None
        payer = entry.dimension_value[1]
        executes_before = engine.repository.execute_count
        # excluding the drilled payer inside its own cohort is certainly empty
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="(gesture)",
                session_id=t1.session.id,
                refinements=(
                    AddFilterModel(
                        op="add_filter", dimension="payer", predicate_op="neq", values=[payer]
                    ),
                ),
            )
        )
        assert outcome.clarification is not None
        assert outcome.clarification.reason is not None
        assert outcome.clarification.reason.startswith("CONTEXT_CONFLICT")
        assert payer in outcome.clarification.question
        assert outcome.investigation.status is InvestigationStatus.CLARIFICATION_REQUIRED
        # detected BEFORE execution: no probe ran
        assert engine.repository.execute_count == executes_before

    async def test_refinement_without_parent_clarifies(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        engine = _engine(small_warehouse_path, llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="(gesture)",
                refinements=(SetDimensionsModel(op="set_dimensions", dimensions=["payer"]),),
            )
        )
        assert outcome.clarification is not None
        assert engine.repository.execute_count == 0

    async def test_ambiguous_emission_clarifies(self, small_warehouse_path: Path) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {"turn_class": "refinement", "confidence": 0.9, "clarification_question": None},
            matcher=lambda p: "do something clever" in p,
        )
        llm.respond(
            "resolve_referents", {"resolutions": []}, matcher=lambda p: "do something clever" in p
        )
        llm.respond("emit_refinements", None, matcher=lambda p: "do something clever" in p)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="do something clever", session_id=t1.session.id
            )
        )
        assert outcome.clarification is not None
        assert outcome.clarification.reason is not None
        assert "AMBIGUOUS_REFINEMENT" in outcome.clarification.reason

    async def test_low_confidence_referent_resolution_clarifies(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {"turn_class": "refinement", "confidence": 0.9, "clarification_question": None},
            matcher=lambda p: "that thing" in p,
        )
        llm.respond(
            "resolve_referents",
            {"resolutions": [{"mention": "that thing", "referent_id": "F1", "confidence": 0.2}]},
            matcher=lambda p: "that thing" in p,
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="drill into that thing", session_id=t1.session.id
            )
        )
        assert outcome.clarification is not None
        assert "F1" in outcome.clarification.question


class TestZeroProbeTurns:
    async def test_presentation_only_reserves_frames_with_zero_probes(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {"turn_class": "presentation_only", "confidence": 0.95, "clarification_question": None},
            matcher=lambda p: "as a chart" in p,
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        executes_before = engine.repository.execute_count
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="show that as a chart", session_id=t1.session.id
            )
        )
        assert engine.repository.execute_count == executes_before  # zero probes
        assert outcome.investigation.status is InvestigationStatus.COMPLETE
        assert [fid for fid, _ in outcome.frames] == [
            key.partition(":")[2] for key in t1.investigation.frame_refs
        ]
        assert outcome.findings == t1.findings

    async def test_meta_turn_cites_recorded_provenance_with_zero_probes(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {"turn_class": "meta", "confidence": 0.95, "clarification_question": None},
            matcher=lambda p: "Why do you say" in p,
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        executes_before = engine.repository.execute_count
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="Why do you say F1?", session_id=t1.session.id)
        )
        assert engine.repository.execute_count == executes_before  # zero probes
        assert outcome.meta is not None
        assert outcome.meta.referent == "F1"
        assert outcome.meta.investigation_id == t1.investigation.id
        t1_trace = await engine.trace_store.get(t1.trace_id)
        assert t1_trace is not None
        assert [p["hash"] for p in outcome.meta.probes] == [
            p["hash"] for p in t1_trace.payload["probes"]
        ]
        assert outcome.meta.operators  # operator applications with versions
        assert outcome.meta.finding_values  # the cited finding's numbers

    async def test_meta_without_referent_clarifies(self, small_warehouse_path: Path) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {"turn_class": "meta", "confidence": 0.95, "clarification_question": None},
            matcher=lambda p: "how did you" in p,
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="how did you work all this out", session_id=t1.session.id
            )
        )
        assert outcome.clarification is not None

    async def test_context_control_pins_persist_into_the_next_question(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {"turn_class": "context_control", "confidence": 0.95, "clarification_question": None},
            matcher=lambda p: "keep State Medicaid excluded" in p,
        )
        llm.respond(
            "emit_refinements",
            {
                "operators": [
                    {
                        "op": "add_filter",
                        "dimension": "payer",
                        "predicate_op": "neq",
                        "values": ["State Medicaid"],
                    }
                ],
                "rationale": "sticky exclusion",
            },
            matcher=lambda p: "keep State Medicaid excluded" in p,
        )
        llm.respond(
            "classify_turn",
            {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
            matcher=lambda p: "cash posted last week" in p,
        )
        llm.respond(
            "interpret_question",
            {
                "intent_summary": "cash",
                "metric_ids": ["cash_posted"],
                "dimension_ids": [],
                "concept_ids": [],
                "playbook_id": None,
                "window": {"quantity": "1", "unit": "week", "mode": "full_periods"},
                "basis": "post",
                "comparison": None,
                "scope": [],
                "clarification": None,
                "definitional_terms": [],
            },
            matcher=lambda p: "cash posted last week" in p,
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        executes_before = engine.repository.execute_count

        pinned = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="keep State Medicaid excluded for this session",
                session_id=t1.session.id,
            )
        )
        assert pinned.investigation.status is InvestigationStatus.COMPLETE
        assert engine.repository.execute_count == executes_before  # zero probes
        assert len(pinned.investigation.spec.context.pins) == 1
        assert pinned.header is not None
        assert any("State Medicaid" in f for f in pinned.header.filters)

        # the next investigative turn inherits the pin (carryover law 5)
        followup = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="cash posted last week", session_id=t1.session.id
            )
        )
        assert followup.investigation.status is InvestigationStatus.COMPLETE
        assert len(followup.investigation.spec.context.pins) == 1
        executed = engine.repository.executed_probes[executes_before:]
        assert executed  # the follow-up did query
        for probe in executed:
            predicates = list(iter_predicates(probe.scope))
            assert any(
                p.dimension.id == "payer" and p.values == ("State Medicaid",) for p in predicates
            )


class TestWatermarkEpochs:
    async def _pinned_session(self, engine: WiredEngine, watermark_index: int) -> Session:
        watermarks = await engine.repository.list_watermarks()
        session = Session(
            id="sess_pinned",
            tenant="demo",
            pack_version=PackVersionRef(engine.pack_port.pack_id, engine.pack_port.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=watermarks[watermark_index]),),
            created_at=datetime.now(UTC),
        )
        await engine.session_store.save(session)
        return session

    async def test_stale_watermark_is_surfaced_and_continuation_stays_pinned(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        session = await self._pinned_session(engine, -2)  # wm_002; wm_003 is newer
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=T1_QUESTION, session_id=session.id)
        )
        assert outcome.watermark_stale is True
        assert outcome.session.watermark.id == "wm_002"
        assert outcome.header is not None and outcome.header.watermark_id == "wm_002"
        warning_events = [e for e in engine.event_bus.events if e.kind == "warning"]
        assert any(e.payload.get("code") == "WATERMARK_STALE" for e in warning_events)
        # pinned continuation is byte-stable: same plan, same watermark, cache-served
        executes_before = engine.repository.execute_count
        rerun = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=T1_QUESTION, session_id=session.id)
        )
        assert rerun.investigation.plan_hash == outcome.investigation.plan_hash
        assert rerun.session.watermark.id == "wm_002"
        assert engine.repository.execute_count == executes_before

    async def test_re_anchor_starts_a_new_epoch(self, small_warehouse_path: Path) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        session = await self._pinned_session(engine, -2)
        stale = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=T1_QUESTION, session_id=session.id)
        )
        assert stale.watermark_stale is True
        re_anchored = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question=T1_QUESTION, session_id=session.id, re_anchor=True
            )
        )
        assert re_anchored.watermark_stale is False
        assert re_anchored.session.watermark.id == "wm_003"
        assert len(re_anchored.session.epochs) == 2
        assert re_anchored.session.epochs[1].index == 1
        trace = await engine.trace_store.get(re_anchored.trace_id)
        assert trace is not None
        epoch_payload: dict[str, Any] = dict(trace.payload["epoch"])
        assert epoch_payload == {"index": 1, "watermark": "wm_003", "re_anchored": True}
        # the re-anchored window re-resolved against the new load's anchor
        assert re_anchored.investigation.plan_hash != stale.investigation.plan_hash


class TestDeterministicReferents:
    """A handle the platform minted is resolved by lookup, not by a model.

    Round-1 live finding F11: "drill into F2" was sent to a language model
    on every follow-up turn. F2 is an identifier this platform printed and
    stored — matching it is a dictionary lookup, and routing it through a
    model buys a call, a latency, and a probability that the turn asking to
    drill into F2 comes back asking which F2 was meant.
    """

    async def test_a_typed_handle_resolves_with_no_model_call(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        llm.respond(
            "classify_turn",
            {"turn_class": "refinement", "confidence": 0.93, "clarification_question": None},
            matcher=lambda p: "F1" in p,
        )
        llm.respond(
            "emit_refinements",
            {
                "operators": [{"op": "drill_into", "target": "F1"}],
                "rationale": "drill into the named finding",
            },
        )

        await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="drill into F1", session_id=t1.session.id
            )
        )

        assert llm.calls_for("resolve_referents") == (), (
            "an identifier this platform minted needs no model to recognize"
        )

    async def test_the_emitter_is_told_what_the_handle_stands_for(
        self, small_warehouse_path: Path
    ) -> None:
        """Resolved referents ride in as structure: the label the analyst
        saw and, for a single-dimension row, the (dimension, value) pair —
        so compiling the operator is not a second guess on top of the
        first."""
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        llm.respond(
            "classify_turn",
            {"turn_class": "refinement", "confidence": 0.93, "clarification_question": None},
            matcher=lambda p: "F1" in p,
        )
        llm.respond(
            "emit_refinements",
            {
                "operators": [{"op": "drill_into", "target": "F1"}],
                "rationale": "drill",
            },
        )

        await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="drill into F1", session_id=t1.session.id
            )
        )

        [emit] = llm.calls_for("emit_refinements")
        _, _, tail = emit.rendered_prompt.partition("Resolved mentions for this utterance:")
        resolutions, _, _ = tail.partition("Dimensions you may reference")
        assert "'F1' -> F1 (confidence 1.00)" in resolutions
        assert "[payer = " in resolutions, resolutions

    async def test_a_handle_this_session_never_published_is_a_question(
        self, small_warehouse_path: Path
    ) -> None:
        """Not a 500, and not a model call hoping to find something close:
        the registry is the answer, so the registry is what gets shown."""
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        llm.respond(
            "classify_turn",
            {"turn_class": "refinement", "confidence": 0.93, "clarification_question": None},
            matcher=lambda p: "F97" in p,
        )

        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="drill into F97", session_id=t1.session.id
            )
        )

        assert outcome.clarification is not None
        assert "F97" in outcome.clarification.question
        assert "F1" in outcome.clarification.question  # what WAS shown
        assert outcome.clarification.reason is not None
        assert outcome.clarification.reason.startswith("REFERENT_NOT_FOUND")
        assert llm.calls_for("resolve_referents") == ()
        assert llm.calls_for("emit_refinements") == ()
