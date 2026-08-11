"""Follow-up turns on a small generated warehouse: refinement mechanics,
zero-probe paths, gestures, pins, and watermark epochs (§7, §8.2-8.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.findings import claimed_rank
from revi_investigation.application.gestures import parse_gesture
from revi_investigation.application.llm.schemas import (
    AddFilterModel,
    DrillIntoModel,
    ExpandModel,
    RankByModel,
    SetDimensionsModel,
)
from revi_investigation.application.ports import RegisteredReferent
from revi_investigation.application.refinement_llm import (
    resolve_ordinal_referent,
    resolve_referent_tokens,
)
from revi_investigation.application.submit_turn import (
    NO_OPTIONS_REASON,
    PRESENTATION_PRODUCED_NOTHING_REASON,
    SubmitTurnRequest,
    TurnOutcome,
)
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import Finding, InvestigationStatus, Session
from revi_investigation.domain.refinements import SetDimensions
from revi_kernel.errors import ReferentNotFoundError
from revi_kernel.filters import iter_predicates
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef, ReferentId, ReferentKind
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

    async def test_the_same_plan_serves_the_same_warnings(
        self, small_warehouse_path: Path
    ) -> None:
        """Regression: a re-served plan published ``warnings=()``.

        Sessions saw 11 warnings become 0 and 7 become 1 across an
        IDENTICAL ``plan_hash`` and byte-identical finding titles, and the
        CSV export then printed "The platform attached no caveats to this
        answer" over the same numbers. The invariant: equal plan_hash ⇒ the
        parent's whole warning set, verbatim, plus the disclosure that this
        is a re-serve. Referents and chart ordering ride with it, because
        rows that cannot be drilled and a chart that has lost its sort are
        the same defect wearing different clothes.
        """
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        assert t1.warnings, "the fixture must carry caveats for this to mean anything"

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
        assert outcome.investigation.plan_hash == t1.investigation.plan_hash
        assert outcome.findings == t1.findings
        # every parent caveat, verbatim and in order…
        assert list(outcome.warnings[: len(t1.warnings)]) == list(t1.warnings)
        # …plus one line saying the plan was re-served rather than re-run
        assert outcome.warnings[-1].startswith("refinement_reused_plan:")
        # persisted too: a rehydrated turn must not lose them either
        assert outcome.investigation.warnings == outcome.warnings
        assert {e.referent.value for e in outcome.referents} == {
            e.referent.value for e in t1.referents
        }
        assert outcome.chart_sorts == t1.chart_sorts

    async def test_expanding_past_the_published_count_re_runs_the_builder(
        self, small_warehouse_path: Path
    ) -> None:
        """Regression: the Expand branch bailed only on ``frame.truncated``
        and then handed back ``parent.findings``, so "show me all twelve" was
        a no-op that cost a model call and changed nothing."""
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="(gesture)",
                session_id=t1.session.id,
                refinements=(ExpandModel(op="expand", limit=len(t1.findings) + 5),),
            )
        )
        trace = await engine.trace_store.get(outcome.trace_id)
        assert trace is not None
        # NOT the kernel path: a wider display scope is a re-selection
        assert "kernel_only" not in (trace.payload.get("refinement") or {})

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
        # A GUESS IS AN OPTION. The candidate the model named is offered as
        # something the analyst can tap: it shipped with `options: ()`, so
        # the funnel's last step stamped CLARIFICATION_NO_OPTIONS and the
        # card printed "There is no answerable option to offer here." one
        # line above a question that was offering exactly one.
        assert outcome.clarification.options, "the candidate referent must be offered"
        assert all("F1" in option for option in outcome.clarification.options)
        assert NO_OPTIONS_REASON not in (outcome.clarification.reason or "")
        # The option carries the HANDLE, which is what resolves it: a reply
        # naming one is claimed by `resolve_referent_tokens` at confidence
        # 1.0 before any model sees it (§7.6).
        entries = await engine.referent_registry.list_for_session(t1.session.id)
        resolved, unknown = resolve_referent_tokens(outcome.clarification.options[0], entries)
        assert unknown == ()
        assert [r.referent.value for r in resolved] == ["F1"]

    async def test_the_referent_guess_frame_carries_its_option(
        self, small_warehouse_path: Path
    ) -> None:
        """The SSE frame publishes the options, not only the question.

        The frame is what a client renders the card from the moment a turn
        resolves; it carried `{question, reason}` alone, so every option the
        funnel had just validated arrived only on the terminal frame.
        """
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
        await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="drill into that thing", session_id=t1.session.id
            )
        )
        [frame] = [e for e in engine.event_bus.events if e.kind == "clarification"]
        assert frame.payload["options"], frame.payload
        assert all("F1" in option for option in frame.payload["options"])


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

    async def test_a_re_presentation_that_produces_nothing_refuses(
        self, small_warehouse_path: Path
    ) -> None:
        """Regression: "Export this" came back ``outcome: answer``,
        ``turn_class: presentation_only``, narrative and findings
        byte-identical to the turn above it — while the same payload carried
        ``REFINEMENT_NOT_APPLIED``, the engine recording that the instruction
        changed nothing and shipping it as though it had.

        A re-presentation that produces no new artifact is a refusal.
        """
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {"turn_class": "presentation_only", "confidence": 0.95, "clarification_question": None},
            matcher=lambda p: "Export this" in p,
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="Export this", session_id=t1.session.id)
        )

        assert outcome.investigation.status is InvestigationStatus.CLARIFICATION_REQUIRED
        assert outcome.clarification is not None
        # …and it refuses BY NAME, pointing at the control that does exist.
        question = outcome.clarification.question
        assert "cannot hand you a file" in question
        assert "Copy answer" in question and "CSV" in question
        assert "nothing was exported by this turn" in question
        assert PRESENTATION_PRODUCED_NOTHING_REASON in (outcome.clarification.reason or "")
        # No second answer over the same rows, and no warning pretending the
        # re-served paragraph was a result.
        assert not outcome.findings
        assert not [w for w in outcome.warnings if w.startswith("refinement_not_applied")]

    async def test_a_re_presentation_that_DOES_something_still_answers(
        self, small_warehouse_path: Path
    ) -> None:
        """The refusal is scoped to turns that produce nothing. An ordering
        the served rows can actually be put in is applied, and applying it is
        the whole of what a presentation turn is for."""
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {"turn_class": "presentation_only", "confidence": 0.95, "clarification_question": None},
            matcher=lambda p: "sort them" in p,
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="sort them by payer alphabetically",
                session_id=t1.session.id,
            )
        )

        assert outcome.investigation.status is InvestigationStatus.COMPLETE
        assert outcome.clarification is None
        assert outcome.findings
        assert any(w.startswith("presentation_applied:") for w in outcome.warnings)

    async def test_an_option_dead_ending_in_a_playbook_refusal_is_never_offered(
        self, small_warehouse_path: Path
    ) -> None:
        """Regression: the hero chip advertising an answer that cannot run.

        "Who is my worst payer?" offered "Will my cash increase next
        month?", and asking for that elsewhere returns
        ``PLAYBOOK_TRANSFORM_UNAVAILABLE: cash_outlook answers by
        'project_lagged_realization'``.

        The options are free TEXT, which is the whole difficulty: an option
        carrying a binding is dry-run against the planner, while an option
        that is only a sentence was published unchecked against everything
        except the cut it names.

        The scorecard option is in this set on purpose and must SURVIVE: it
        was withheld by this same rule while the scorecard could not run,
        and it is the right offer now that it can. A guard that kept
        dropping it would cost the analyst the answer to the question they
        actually asked.
        """
        llm = MockLanguageModel()
        _canned_t1(llm)
        llm.respond(
            "classify_turn",
            {
                "turn_class": "new_investigation",
                "confidence": 0.5,
                "clarification_question": "Worst by what? Denial rate and days in A/R disagree.",
                "clarification_options": [
                    "Rank payers by denial rate",
                    "Run a full payer scorecard across all measures",
                    "Will my cash increase next month?",
                ],
            },
            matcher=lambda p: "worst payer" in p,
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="Who is my worst payer?", session_id=t1.session.id
            )
        )

        assert outcome.clarification is not None
        offered = list(outcome.clarification.options)
        assert "Will my cash increase next month?" not in offered
        # The scorecard runs, so the button that reaches it stands.
        assert "Run a full payer scorecard across all measures" in offered
        # The set is never emptied by this rule: an imperfect suggestion
        # beats a blank row of buttons.
        assert "Rank payers by denial rate" in offered
        reason = outcome.clarification.reason or ""
        assert "option(s) dropped before offer" in reason
        assert "cash_outlook" in reason and "project_lagged_realization" in reason

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

    Regression: "drill into F2" was sent to a language model on every
    follow-up turn. F2 is an identifier this platform printed and
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
        first.

        Asked here with a handle inside a *sentence*, because a bare
        "drill into F1" is one of the platform's own gestures and is now
        parsed without a model at all (see
        ``TestPlatformGesturesRoundTrip``). The emitter still compiles
        anything with language in it, and this is what it is told."""
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
                tenant="demo",
                question="drill into F1 and break it out by carc",
                session_id=t1.session.id,
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


class TestPlatformGesturesRoundTrip:
    """Regression: the product could not parse its own suggestion.

    Every finding publishes ``suggested_refinements`` and the unknown-handle
    clarification offers the same strings as options. "drill into F1" — a
    string this platform printed — came back
    ``clarification_required`` with ``referent_resolutions: []``, because the
    utterance went to the classifier first and the classifier asked a
    question. A button the product prints and cannot press is worse than no
    button.
    """

    async def test_every_suggestion_a_turn_emits_parses_back_with_no_model_call(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        suggestions = [s for f in t1.findings for s in f.suggested_refinements]
        assert suggestions, "the turn under test must actually suggest something"

        for suggestion in suggestions:
            calls_before = len(llm.structured_calls)
            # No canned classify/emit rules are registered for these
            # utterances: an unmatched structured call would raise here.
            outcome = await engine.submit.submit(
                SubmitTurnRequest(
                    tenant="demo", question=suggestion, session_id=t1.session.id
                )
            )
            assert len(llm.structured_calls) == calls_before, suggestion
            assert outcome.clarification is None, suggestion
            assert outcome.investigation.status is InvestigationStatus.COMPLETE

    async def test_the_gesture_is_case_and_punctuation_tolerant(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        calls_before = len(llm.structured_calls)

        outcome = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question="  Drill into f1.  ", session_id=t1.session.id
            )
        )

        assert len(llm.structured_calls) == calls_before
        assert outcome.investigation.status is InvestigationStatus.COMPLETE

    async def test_a_gesture_naming_an_unpublished_handle_answers_from_the_registry(
        self, small_warehouse_path: Path
    ) -> None:
        """Still zero model calls: the registry is the answer."""
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        calls_before = len(llm.structured_calls)

        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="drill into F97", session_id=t1.session.id)
        )

        assert len(llm.structured_calls) == calls_before
        assert outcome.clarification is not None
        assert "F97" in outcome.clarification.question
        # …and every option it offers is itself a parseable gesture
        for option in outcome.clarification.options:
            assert parse_gesture(option, ()) is not None, option

    async def test_a_sentence_containing_a_handle_is_still_language(
        self, small_warehouse_path: Path
    ) -> None:
        """The grammar is whole-utterance on purpose: matching loosely would
        silently drop the half of the request nobody parsed."""
        entries = ()
        assert parse_gesture("drill into F1 and break it out by carc", entries) is None
        assert parse_gesture("why did that happen?", entries) is None


class TestNamedEntityBackReference:
    """Regression: one turn after publishing "Summit Peak Medicare Advantage
    … " as F1, the session asked whether "Summit Peak" was a facility, a
    payer or a provider."""

    async def test_a_payer_this_session_named_resolves_without_a_model(
        self, small_warehouse_path: Path
    ) -> None:
        llm = MockLanguageModel()
        _canned_t1(llm)
        engine = _engine(small_warehouse_path, llm)
        t1 = await _run_t1(engine)
        entry = await engine.referent_registry.resolve(t1.session.id, t1.findings[0].referent)
        assert entry is not None and entry.dimension_value is not None
        payer = entry.dimension_value[1]

        llm.respond(
            "classify_turn",
            {"turn_class": "refinement", "confidence": 0.93, "clarification_question": None},
        )
        llm.respond(
            "emit_refinements",
            {"operators": [{"op": "drill_into", "target": "F1"}], "rationale": "named payer"},
        )

        await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question=f"what is driving {payer}?",
                session_id=t1.session.id,
            )
        )

        assert llm.calls_for("resolve_referents") == (), (
            "a name this platform printed one turn ago is a lookup, not a guess"
        )
        [emit] = llm.calls_for("emit_refinements")
        assert payer in emit.rendered_prompt

    async def test_the_top_one_resolves_against_the_ranking_just_published(
        self, small_warehouse_path: Path
    ) -> None:
        """"Denial rate by facility last quarter" answered with a ranking —
        "ranks #1 of 6", a chart ordered high to low — and the next turn,
        "drill into the top one", refused to choose on the grounds that
        picking a row would invent an ordering the context had not
        established. The context had established it: the ordering is in the
        answer's own findings, which is where this now reads it."""
        ranked_question = "denial rate by payer last quarter"
        llm = MockLanguageModel()
        llm.respond(
            "classify_turn",
            {"turn_class": "new_investigation", "confidence": 0.94,
             "clarification_question": None},
            matcher=lambda p: ranked_question in p,
        )
        llm.respond(
            "interpret_question",
            {
                "intent_summary": "denial rate by payer",
                "metric_ids": ["denial_rate"],
                "dimension_ids": ["payer"],
                "concept_ids": [],
                "playbook_id": None,
                "window": {"quantity": "1", "unit": "quarter", "mode": "full_periods"},
                "basis": None,
                "comparison": None,
                "scope": [],
                "clarification": None,
                "definitional_terms": [],
            },
        )
        engine = _engine(small_warehouse_path, llm)
        t1 = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=ranked_question)
        )
        assert t1.findings, "the fixture turn published nothing"
        first = t1.findings[0]
        assert claimed_rank(first) == 1, "the fixture turn published no ranking"

        llm.respond(
            "classify_turn",
            {"turn_class": "refinement", "confidence": 0.93, "clarification_question": None},
        )
        llm.respond(
            "emit_refinements",
            {"operators": [{"op": "drill_into", "target": first.referent.value}],
             "rationale": "the published #1"},
        )

        await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="drill into the top one",
                session_id=t1.session.id,
            )
        )

        assert llm.calls_for("resolve_referents") == (), (
            "a position this platform published one turn ago is a lookup, not a guess"
        )
        [emit] = llm.calls_for("emit_refinements")
        _, _, tail = emit.rendered_prompt.partition("Resolved mentions for this utterance:")
        assert first.referent.value in tail
        assert "the top one" in tail


class TestOrdinalsReadThePublishedOrdering:
    """The ordinal resolver in isolation: what it will and will not bind."""

    @staticmethod
    def _finding(referent: str, rank: int | None) -> Finding:
        statement = (
            f"Eastmere ranks #{rank} of 6 measured by denial rate over 2026-04-01: 8.3%."
            if rank is not None
            else (
                "Eastmere: 8.3% denial rate. No position is claimed for it — too much of "
                "this population carries suppressed numerators for an order to mean anything."
            )
        )
        return Finding(
            referent=ReferentId(value=referent, kind=ReferentKind.FINDING),
            title=f"{referent} title",
            statement=statement,
            metric_refs=(MetricRef(id="denial_rate"),),
            values=(("denial_rate", Decimal("0.083")), ("rank", rank or 1)),
            grade=EvidenceGrade.DIRECT,
        )

    @staticmethod
    def _entries(*findings: Finding) -> tuple[RegisteredReferent, ...]:
        return tuple(
            RegisteredReferent(
                session_id="s",
                investigation_id="i",
                referent=f.referent,
                label=f.title,
                cohort=None,
            )
            for f in findings
        )

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("drill into the top one", "F1"),
            ("drill into the first one", "F1"),
            ("show me the second row", "F2"),
            ("open #3", "F3"),
            ("drill into number 2", "F2"),
            ("what about the last one", "F3"),
            ("break out the bottom one", "F3"),
        ],
    )
    def test_a_position_the_answer_claimed_binds(
        self, question: str, expected: str
    ) -> None:
        findings = tuple(self._finding(f"F{i}", i) for i in (1, 2, 3))
        [resolution] = resolve_ordinal_referent(question, findings, self._entries(*findings))
        assert resolution.referent.value == expected
        assert resolution.confidence == 1.0

    def test_an_answer_that_claimed_no_position_binds_nothing(self) -> None:
        """A population too bounded to order does not acquire an order
        because somebody asked for its top row."""
        findings = tuple(self._finding(f"F{i}", None) for i in (1, 2, 3))
        assert resolve_ordinal_referent(
            "drill into the top one", findings, self._entries(*findings)
        ) == ()

    @pytest.mark.parametrize(
        "question",
        [
            "show me the top 3 payers",  # a LIMIT, not a referent
            "compare the first quarter to the second",  # a period, not a row
            "drill into the worst one",  # polarity: which end depends on the sign
            "drill into the best one",
            "drill into the fourth one",  # no such published position
            "why did denials rise",
        ],
    )
    def test_what_is_left_to_the_model(self, question: str) -> None:
        findings = tuple(self._finding(f"F{i}", i) for i in (1, 2, 3))
        assert resolve_ordinal_referent(question, findings, self._entries(*findings)) == ()

    def test_ambiguity_is_left_to_the_model(self) -> None:
        """Two payers on screen and "that payer" means somebody has to say
        which — a deterministic guess there is the confident-wrong answer
        the rule exists to prevent."""
        from datetime import date

        from revi_investigation.application.ports import RegisteredReferent
        from revi_investigation.application.refinement_llm import resolve_named_referents
        from revi_kernel.cohort import CohortDefinition
        from revi_kernel.filters import EMPTY_SCOPE
        from revi_kernel.refs import DateBasisRef, EntityGrain, ReferentId, ReferentKind
        from revi_kernel.scope import AbsoluteRange, TimeWindow

        def entry(handle: str, value: str) -> RegisteredReferent:
            return RegisteredReferent(
                referent=ReferentId(value=handle, kind=ReferentKind.DIMENSION_VALUE),
                session_id="s",
                investigation_id="i",
                label=value,
                cohort_definition=CohortDefinition(
                    entity=EntityGrain.CLAIM,
                    scope=EMPTY_SCOPE,
                    window=TimeWindow(
                        basis=DateBasisRef("post"),
                        range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
                    ),
                ),
                dimension_value=("payer", value),
            )

        one = (entry("D1", "Summit Peak Medicare Advantage"),)
        two = (*one, entry("D2", "Atlas Commercial"))

        assert resolve_named_referents("that payer again", one)
        assert resolve_named_referents("that payer again", two) == ()
        # …but naming one of the two is never ambiguous
        [resolved] = resolve_named_referents("how about Atlas Commercial", two)
        assert resolved.referent.value == "D2"
