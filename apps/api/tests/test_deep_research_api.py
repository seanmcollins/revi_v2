"""Deep research at the boundary: launching, watching, reading back, listing.

A run outlives the request that started it, which is the one thing about
this surface that is genuinely new. So these tests are mostly about what
happens either side of that: who may see a run, what a watcher who arrives
late is shown, what a killed run leaves behind, and whether a finished run
still reads correctly out of storage after its process forgot it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from revi_api.auth import Principal
from revi_api.deep_research import (
    DEEP_RESEARCH_TRACE_SUFFIX,
    RUN_ID_PREFIX,
    is_run_id,
)
from revi_api.scripted_llm import demo_language_model
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation_contracts.deep_research import (
    DeepResearchSelector,
    StartDeepResearchRequest,
)
from revi_investigation_contracts.monitors import CreateMonitorsPinRequest
from revi_kernel.errors import (
    PolicyDeniedError,
    ReferentNotFoundError,
    UnsupportedConceptError,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

CALLER = Principal(tenant="demo", subject="tests")
OTHER = Principal(tenant="other-tenant", subject="tests")

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.is_file(),
    reason="generated warehouse missing — run: make warehouse",
)


def service() -> ApiService:
    env = {"REVI_WAREHOUSE_PATH": str(WAREHOUSE), "REVI_LLM_MOCK": "1"}
    return ApiService(build_components(env, llm=demo_language_model()))


async def _finish(api: ApiService, run_id: str) -> None:
    state = api.research._runs[run_id]
    if state.task is not None:
        await state.task


class TestLaunching:
    async def test_a_run_starts_immediately_and_finishes_in_the_background(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        assert is_run_id(started.id)
        assert started.id.startswith(RUN_ID_PREFIX)
        assert started.status in ("planning", "running")
        assert started.report is None
        await _finish(api, started.id)
        finished = await api.get_deep_research(CALLER, started.id)
        assert finished.status == "complete"
        assert finished.report is not None
        assert finished.report.headline.total_expected_cents > 0

    async def test_it_is_pinned_to_the_load_it_started_at(self) -> None:
        api = service()
        newest = await api.components.open_session.newest_watermark()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        finished = await api.get_deep_research(CALLER, started.id)
        assert finished.report is not None
        assert finished.report.data_edge_date == newest.newest_data_date

    async def test_a_narrowed_population_reaches_the_report(self) -> None:
        api = service()
        started = await api.start_deep_research(
            CALLER,
            StartDeepResearchRequest(
                population=DeepResearchSelector(
                    kind="payer", values=["Northbridge Commercial"]
                ),
                question="What can we recover from Northbridge?",
            ),
        )
        await _finish(api, started.id)
        finished = await api.get_deep_research(CALLER, started.id)
        assert finished.report is not None
        assert finished.report.population.kind == "payer"
        assert finished.report.population.values == ["Northbridge Commercial"]
        assert finished.report.research_question.startswith("What can we recover")

    async def test_a_selector_that_names_nothing_is_refused_not_widened(self) -> None:
        api = service()
        with pytest.raises(UnsupportedConceptError, match="at least one value"):
            await api.start_deep_research(
                CALLER,
                StartDeepResearchRequest(
                    population=DeepResearchSelector(kind="payer", values=[])
                ),
            )

    async def test_naming_values_for_every_open_denial_is_refused(self) -> None:
        api = service()
        with pytest.raises(UnsupportedConceptError, match="no values"):
            await api.start_deep_research(
                CALLER,
                StartDeepResearchRequest(
                    population=DeepResearchSelector(kind="all_open", values=["Anything"])
                ),
            )

    async def test_a_run_lands_in_a_conversation_the_caller_owns(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        session = await api.components.sessions.get(started.session_id)
        assert session is not None
        assert session.tenant == CALLER.tenant


class TestTheReport:
    @pytest.fixture(scope="class")
    async def finished(self):
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        return api, await api.get_deep_research(CALLER, started.id)

    async def test_the_headline_is_the_recoverable_determination(self, finished) -> None:
        _, run = finished
        report = run.report
        assert report is not None
        headline = report.headline
        assert headline.total_expected_cents > 0
        assert (
            headline.total_expected_interval.low_cents
            <= headline.total_expected_cents
            <= headline.total_expected_interval.high_cents
        )
        assert (
            headline.catchable_dollars_cents
            + headline.deadline_passed_dollars_cents
            + headline.deadline_unknown_dollars_cents
            == headline.total_open_dollars_cents
        )

    async def test_every_published_rate_carries_its_tier(self, finished) -> None:
        _, run = finished
        report = run.report
        assert report is not None
        for cell in report.rates:
            assert cell.evidence in ("measured", "not_estimable")
            assert (cell.rate is None) == (cell.evidence == "not_estimable")
            assert (cell.interval is None) == (cell.evidence == "not_estimable")

    async def test_the_warnings_are_coded_for_a_client_to_branch_on(
        self, finished
    ) -> None:
        _, run = finished
        report = run.report
        assert report is not None
        codes = {warning.code for warning in report.warnings}
        assert "DEEP_RESEARCH_UNPRICED" in codes
        assert "DEEP_RESEARCH_INDEPENDENCE" in codes
        assert "DEEP_RESEARCH_CENSORING" in codes
        assert all(warning.message for warning in report.warnings)

    async def test_the_report_is_written_up_and_leads_with_its_limits(
        self, finished
    ) -> None:
        _, run = finished
        report = run.report
        assert report is not None
        assert report.narrative
        assert "industry average" in report.narrative

    async def test_charts_never_draw_a_line_through_fewer_than_three_points(
        self, finished
    ) -> None:
        _, run = finished
        report = run.report
        assert report is not None
        assert report.charts
        for chart in report.charts:
            assert len(chart.rows) >= 2, chart.id
            if chart.chart_type == "line":
                assert len({row.x for row in chart.rows}) >= 3, chart.id
                assert chart.axis_order

    async def test_the_evidence_names_the_read_behind_every_angle(
        self, finished
    ) -> None:
        _, run = finished
        report = run.report
        assert report is not None
        fingerprints = {entry.read_fingerprint for entry in report.evidence}
        assert len(fingerprints) == 1
        assert all(entry.rows_read == 5398 for entry in report.evidence)


class TestTenancy:
    async def test_another_tenant_cannot_read_a_run(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        with pytest.raises(ReferentNotFoundError):
            await api.get_deep_research(OTHER, started.id)

    async def test_another_tenant_cannot_watch_a_run(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        with pytest.raises(ReferentNotFoundError):
            async for _ in api.stream_deep_research(OTHER, started.id):
                pass
        await _finish(api, started.id)

    async def test_another_tenant_sees_none_of_this_tenants_runs(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        mine = await api.list_deep_research(CALLER)
        theirs = await api.list_deep_research(OTHER)
        assert started.id in {run.id for run in mine.runs}
        assert theirs.runs == []

    async def test_an_unknown_run_is_a_miss_not_a_blank_report(self) -> None:
        api = service()
        with pytest.raises(ReferentNotFoundError):
            await api.get_deep_research(CALLER, "dr_nothing")


class TestWatching:
    async def test_the_frames_carry_the_run_from_start_to_report(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        kinds: list[str] = []
        async for frame in api.stream_deep_research(CALLER, started.id):
            kind = frame.split("\n", 1)[0].removeprefix("event: ")
            kinds.append(kind)
        assert kinds[0] == "research_started"
        assert kinds[-1] == "research_complete"
        assert "research_progress" in kinds
        assert "research_plan" in kinds
        assert "research_finding" in kinds
        assert "research_warning" in kinds

    async def test_progress_names_each_angle_as_it_runs(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        messages: list[str] = []
        async for frame in api.stream_deep_research(CALLER, started.id):
            head, _, body = frame.partition("\n")
            if head == "event: research_progress":
                messages.append(body)
        assert any("Pricing the open denials" in message for message in messages)
        assert any("Checking filing deadlines" in message for message in messages)

    async def test_a_watcher_that_arrives_after_the_run_is_caught_up_not_cut_off(
        self,
    ) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        kinds = [
            frame.split("\n", 1)[0].removeprefix("event: ")
            async for frame in api.stream_deep_research(CALLER, started.id)
        ]
        assert kinds[0] == "research_started"
        assert kinds[-1] == "research_complete"

    async def test_two_watchers_both_see_the_whole_run(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())

        async def watch() -> list[str]:
            return [
                frame.split("\n", 1)[0].removeprefix("event: ")
                async for frame in api.stream_deep_research(CALLER, started.id)
            ]

        first, second = await asyncio.gather(watch(), watch())
        assert first[-1] == second[-1] == "research_complete"


class TestPersistenceAndCancellation:
    async def test_a_finished_run_reads_back_out_of_storage(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        live = await api.get_deep_research(CALLER, started.id)
        # Forget the in-flight registry: the permalink must survive it.
        api.research._runs.clear()
        stored = await api.get_deep_research(CALLER, started.id)
        assert stored.status == "complete"
        assert stored.report is not None
        assert live.report is not None
        assert (
            stored.report.headline.total_expected_cents
            == live.report.headline.total_expected_cents
        )

    async def test_a_finished_run_is_an_investigation_like_any_other(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        investigation = await api.components.investigations.get(started.id)
        assert investigation is not None
        assert investigation.session_id == started.session_id
        assert investigation.findings
        assert investigation.narrative
        assert investigation.plan_hash

    async def test_a_run_that_was_stopped_persists_its_trace_and_nothing_else(
        self,
    ) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await api.research.cancel(CALLER, started.id)
        assert await api.components.investigations.get(started.id) is None
        record = await api.components.traces.get(
            f"{started.id}{DEEP_RESEARCH_TRACE_SUFFIX}"
        )
        assert record is not None
        assert record.payload["status"] == "running"
        assert "report" not in record.payload

    async def test_a_stopped_run_reads_back_as_stopped_rather_than_empty(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await api.research.cancel(CALLER, started.id)
        api.research._runs.clear()
        stored = await api.get_deep_research(CALLER, started.id)
        assert stored.status == "interrupted"
        assert stored.report is None
        assert stored.error


class TestTheTrace:
    """Full provenance for every number, recorded and readable.

    Deep research publishes dollars that no probe returned — a rate applied
    to an inventory. So the trace has to carry more than "a query ran": for
    each figure it must be possible to get back to the denials behind it,
    the rate that priced them, how many answered denials that rate rests
    on, and the rule that let it be published at all.
    """

    @pytest.fixture(scope="class")
    async def traced(self):
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        record = await api.components.traces.get(
            f"{started.id}{DEEP_RESEARCH_TRACE_SUFFIX}"
        )
        return api, started.id, record

    async def test_the_trace_names_the_load_the_read_and_the_plan(self, traced) -> None:
        _, run_id, record = traced
        assert record is not None
        payload = record.payload
        assert payload["mode"] == "deep_research"
        assert payload["status"] == "complete"
        assert payload["watermark"] == "wm_003"
        assert payload["rows_read"] == 5398
        assert len(payload["read_fingerprint"]) == 64
        assert len(payload["plan_fingerprint"]) == 64
        assert record.investigation_id == run_id

    async def test_every_dollar_figure_traces_to_a_rate_and_its_population(
        self, traced
    ) -> None:
        _, _, record = traced
        assert record is not None
        report = record.payload["report"]
        for row in report["strata"]:
            cell = row["rate_cell"]
            assert cell["rate"] is not None
            assert cell["n"] >= cell["floor"]
            assert cell["successes"] <= cell["n"]
            assert cell["interval"] is not None
            assert row["expected_cents"] is not None
            assert row["parts"], "a priced population names the cut it came from"

    async def test_a_refused_population_traces_to_the_rule_that_refused_it(
        self, traced
    ) -> None:
        _, _, record = traced
        assert record is not None
        report = record.payload["report"]
        assert report["not_estimable"]
        for row in report["not_estimable"]:
            cell = row["rate_cell"]
            assert cell["rate"] is None
            assert cell["n"] < cell["floor"]
            assert row["expected_cents"] is None

    async def test_every_angle_records_what_it_called_and_what_it_cost(
        self, traced
    ) -> None:
        _, _, record = traced
        assert record is not None
        report = record.payload["report"]
        assert report["evidence"]
        for entry in report["evidence"]:
            assert entry["estimator"]
            assert entry["read_fingerprint"] == record.payload["read_fingerprint"]
            assert entry["duration_ms"] >= 0

    async def test_the_stored_report_is_the_published_one(self, traced) -> None:
        api, run_id, record = traced
        assert record is not None
        served = await api.get_deep_research(CALLER, run_id)
        assert served.report is not None
        assert served.report.model_dump(mode="json") == record.payload["report"]


class TestListing:
    async def test_runs_come_back_newest_first_with_their_headline(self) -> None:
        api = service()
        first = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, first.id)
        second = await api.start_deep_research(
            CALLER,
            StartDeepResearchRequest(
                population=DeepResearchSelector(kind="payer", values=["Atlas Commercial"])
            ),
        )
        await _finish(api, second.id)
        listing = await api.list_deep_research(CALLER)
        ids = [run.id for run in listing.runs]
        assert set(ids) == {first.id, second.id}
        assert listing.runs[0].created_at >= listing.runs[-1].created_at
        assert all(run.total_expected_cents is not None for run in listing.runs)

    async def test_a_listing_survives_the_registry_being_forgotten(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        api.research._runs.clear()
        listing = await api.list_deep_research(CALLER)
        assert [run.id for run in listing.runs] == [started.id]
        assert listing.runs[0].total_expected_cents is not None


class TestOrdinaryTurnsAreUnaffected:
    async def test_a_normal_question_still_answers_and_offers_nothing(self) -> None:
        from revi_investigation_contracts.api import TurnRequest

        api = service()
        opened = await api.components.open_session.open(tenant="demo", session_id=None)
        answer = await api.submit_turn(
            CALLER,
            opened.id,
            TurnRequest(utterance="what were denied dollars by payer last month?"),
        )
        assert answer.outcome in ("answer", "clarification_required")
        if answer.outcome == "answer":
            assert answer.deep_research is None


class TestThePlanOnlyDryRun:
    """The confirmation in front of a minute of work, answered from facts.

    A run is about sixty seconds and a real model call, and this product's
    rule for a real consequence is that it is stated BEFORE the click. The
    surface that offers one needed three things it cannot compose without
    asserting facts it has not read: how big the population is, which angles
    the run would take, and which other populations the same offer could
    run over. All three come back here, and nothing starts.
    """

    async def test_it_starts_nothing_and_says_what_a_run_would_do(self) -> None:
        api = service()
        answer = await api.start_deep_research(
            CALLER, StartDeepResearchRequest(plan_only=True)
        )
        assert answer.status == "preview"
        assert answer.id == ""
        assert api.research._runs == {}, "a preview must not create a run"
        assert answer.report is None
        preview = answer.preview
        assert preview is not None
        assert preview.scope.open_denials > 0
        assert preview.scope.open_dollars_cents > 0
        assert preview.plan.angles, "the angles a run would take"
        assert all(angle.title and angle.purpose for angle in preview.plan.angles)

    async def test_the_size_it_reports_is_the_size_the_run_prices(self) -> None:
        api = service()
        preview = (
            await api.start_deep_research(CALLER, StartDeepResearchRequest(plan_only=True))
        ).preview
        assert preview is not None
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        finished = await api.get_deep_research(CALLER, started.id)
        assert finished.report is not None
        assert (
            preview.scope.open_denials == finished.report.headline.total_open_denials
        )
        assert (
            preview.scope.open_dollars_cents
            == finished.report.headline.total_open_dollars_cents
        )

    async def test_the_other_populations_are_closed_selectors_not_sentences(self) -> None:
        api = service()
        answer = await api.start_deep_research(
            CALLER,
            StartDeepResearchRequest(
                population=DeepResearchSelector(kind="payer", values=["Atlas Commercial"]),
                plan_only=True,
            ),
        )
        preview = answer.preview
        assert preview is not None
        assert [option.kind for option in preview.options] == ["all_open"]
        assert preview.population.kind == "payer"


class TestTheResearchQuestionGetsItsOwnPreview:
    """A research question is not the standing recoverability review.

    The review answers one question over open denials and can describe
    itself from its closed catalogue. A research question can be about
    anything the semantic layer measures, so the only honest description is
    what the run LEARNED about the data and what it therefore intends to
    read — resolved by really orienting, really consulting the definitions
    library, and really planning, with nothing executed.
    """

    QUESTION = (
        "research why our A/R over 90 has been climbing and what it will take "
        "to bring it down"
    )

    async def _preview(self, question: str | None):
        api = service()
        answer = await api.start_deep_research(
            CALLER, StartDeepResearchRequest(question=question, plan_only=True)
        )
        assert api.research._runs == {}, "a preview must not create a run"
        assert answer.preview is not None
        return answer.preview

    async def test_the_review_with_no_question_gets_no_research_preview(self) -> None:
        """Nothing to research is not the same as nothing to say."""
        preview = await self._preview(None)
        assert preview.generalized is None
        assert preview.plan.angles, "the review still describes itself"

    async def test_a_question_gets_the_path_choices_it_will_read_through(self) -> None:
        preview = await self._preview(self.QUESTION)
        general = preview.generalized
        assert general is not None
        assert general.path_choices, "a plan built without orientation is a guess"
        for choice in general.path_choices:
            assert choice.subject and choice.statement
            assert choice.statement.rstrip().endswith((".", "%"))

    async def test_the_background_notes_it_consulted_are_named(self) -> None:
        preview = await self._preview(self.QUESTION)
        general = preview.generalized
        assert general is not None
        assert general.knowledge_statement
        titles = [note.title for note in general.knowledge_consulted]
        assert titles, "an A/R aging question has background notes that bear on it"
        assert all(note.matched_on for note in general.knowledge_consulted)

    async def test_every_reading_carries_the_reason_it_is_there(self) -> None:
        preview = await self._preview(self.QUESTION)
        general = preview.generalized
        assert general is not None
        assert general.readings
        for reading in general.readings:
            assert reading.title and reading.reason
            assert reading.round == 0, "a preview shows the opening read only"

    async def test_it_says_who_chose_the_readings(self) -> None:
        preview = await self._preview(self.QUESTION)
        general = preview.generalized
        assert general is not None
        assert general.authored_by in ("model", "revi")

    async def test_the_budget_it_publishes_scales_with_the_question(self) -> None:
        deep = await self._preview(self.QUESTION)
        shallow = await self._preview("what is our denial rate")
        assert deep.generalized is not None and shallow.generalized is not None
        assert deep.generalized.rounds_planned > shallow.generalized.rounds_planned

    async def test_a_question_no_measure_can_answer_refuses_by_naming_the_gap(
        self,
    ) -> None:
        """The first of the two honest non-answers: a statement about the
        data, never about the engine."""
        preview = await self._preview("how is the cafeteria doing")
        general = preview.generalized
        assert general is not None
        assert general.readings == []
        assert "definitions library" in general.refusal

    async def test_the_period_it_will_read_is_stated_in_a_readers_words(self) -> None:
        preview = await self._preview(self.QUESTION)
        general = preview.generalized
        assert general is not None
        assert "through" in general.window_label
        assert "-" not in general.window_label.replace("A/R", "")


class TestARunIsNotSomethingAMonitorCanReRun:
    """A monitor measures ONE thing at every load and compares it to the
    last one. A recoverability review is a whole analysis over a population
    — dozens of rates, an expected-recovery total, its own refusals — and it
    names no single measure. Pinning one reached the typed-spec builder with
    an empty measure list and came back a 500."""

    async def test_pinning_one_is_refused_in_the_reader_s_own_words(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        with pytest.raises(PolicyDeniedError) as refused:
            await api.monitors.create_pin(
                CALLER, CreateMonitorsPinRequest(investigation_id=started.id)
            )
        message = str(refused.value)
        assert "monitor" in message
        assert "data load" in message
        # …and it says what CAN be watched instead, rather than only "no".
        assert "instead" in message


class TestAResearchQuestionRunsAsAStudy:
    """The run branch: a question is executed, not merely previewed.

    Until this milestone the confirm card resolved a research plan and the
    button started the recoverability review. Both runs now exist, the
    server branches on the question, and the two artifacts are
    discriminated on the wire rather than by shape-sniffing.

    These run against the scripted control plane, so the planner call fails
    and the loop falls back to its standing opening read — which is the
    deterministic-fallback path, end to end, through the real API.
    """

    QUESTION = (
        "research why our A/R over 90 has been climbing and what it will take "
        "to bring it down"
    )

    async def _study(self, question: str = QUESTION):
        api = service()
        started = await api.start_deep_research(
            CALLER, StartDeepResearchRequest(question=question)
        )
        await _finish(api, started.id)
        return api, await api.get_deep_research(CALLER, started.id)

    async def test_a_question_produces_a_study_and_says_which_it_is(self) -> None:
        _, finished = await self._study()
        assert finished.status == "complete"
        assert finished.report_kind == "generalized"
        assert finished.report is None, "a study must not carry the review's shape"
        assert finished.research_report is not None
        assert finished.research_report.kind == "generalized_research"
        assert finished.research_report.research_question == self.QUESTION

    async def test_the_study_publishes_readings_with_their_reasons(self) -> None:
        _, finished = await self._study()
        study = finished.research_report
        assert study is not None
        assert study.readings, "a study that read nothing is not a study"
        for reading in study.readings:
            assert reading.title and reading.reason
            assert reading.read_fingerprint or reading.refusal

    async def test_the_walk_reaches_the_wire_with_its_rounds(self) -> None:
        _, finished = await self._study()
        study = finished.research_report
        assert study is not None
        assert study.walk.rounds_taken >= 2, (
            "a why-and-what-will-it-take question earns more than one pass"
        )
        assert [round_.index for round_ in study.walk.rounds] == list(
            range(study.walk.rounds_taken)
        )
        for round_ in study.walk.rounds:
            assert all(step.reason for step in round_.steps)

    async def test_the_determination_is_composed_and_leads_with_its_disclosures(
        self,
    ) -> None:
        _, finished = await self._study()
        study = finished.research_report
        assert study is not None
        assert study.determination.question == self.QUESTION
        assert study.determination.statement
        # The disclosures are published in front of the prose, verbatim, so
        # a reader who stops after the first sentence has the limits too.
        assert study.determination.statement.startswith("Every figure in this study")

    async def test_the_no_question_run_is_still_the_review_byte_for_byte(self) -> None:
        api = service()
        started = await api.start_deep_research(CALLER, StartDeepResearchRequest())
        await _finish(api, started.id)
        finished = await api.get_deep_research(CALLER, started.id)
        assert finished.report_kind == "recovery"
        assert finished.research_report is None
        assert finished.report is not None
        assert finished.report.headline.total_expected_cents > 0

    async def test_a_question_no_measure_can_answer_falls_back_to_the_review(
        self,
    ) -> None:
        """The card says the button measures the review instead. So does the
        server, and they have to agree or the confirmation was a promise
        about a different run."""
        _, finished = await self._study("how is the cafeteria doing")
        assert finished.report_kind == "recovery"
        assert finished.research_report is None
        assert finished.report is not None

    async def test_the_study_survives_the_trace_round_trip(self) -> None:
        api, finished = await self._study()
        run_id = finished.id
        # Forget the in-flight state, exactly as a restarted process would.
        api.research._runs.clear()
        restored = await api.get_deep_research(CALLER, run_id)
        assert restored.report_kind == "generalized"
        assert restored.research_report is not None
        assert restored.research_report.model_dump(
            mode="json"
        ) == finished.research_report.model_dump(mode="json")

    async def test_the_stored_analysis_is_keyed_on_the_walk(self) -> None:
        """"The recorded path is the plan." The stored investigation's own
        handle is the walk's fingerprint, so a permalink restores what was
        decided rather than a plan nobody took."""
        api, finished = await self._study()
        record = await api.components.traces.get(
            f"{finished.id}{DEEP_RESEARCH_TRACE_SUFFIX}"
        )
        assert record is not None
        assert record.payload["report_kind"] == "generalized"
        assert len(str(record.payload["plan_fingerprint"])) == 64
        lineage = await api.components.investigations.lineage(finished.session_id)
        assert lineage is not None
        stored = next(i for i in lineage.investigations if i.id == finished.id)
        assert stored.plan_hash == record.payload["plan_fingerprint"]
        assert stored.question == self.QUESTION

    async def test_a_study_is_listed_as_a_study(self) -> None:
        api, finished = await self._study()
        listed = await api.list_deep_research(CALLER)
        row = next(run for run in listed.runs if run.id == finished.id)
        assert row.report_kind == "generalized"
        # A study prices nothing, so the expected-recovery column is empty
        # rather than zero: zero is a measurement it never made.
        assert row.total_expected_cents is None

    async def test_a_watcher_sees_the_readings_and_the_rounds_while_it_works(
        self,
    ) -> None:
        """The iteration rows light up from real frames.

        `round_index`/`round_total` were on the wire and always zero; the
        readings were named only once the report arrived. Both are now
        emitted as the run takes them, which is the difference between a
        record of reasoning and a spinner with words on it.
        """
        api = service()
        started = await api.start_deep_research(
            CALLER, StartDeepResearchRequest(question=self.QUESTION)
        )
        await _finish(api, started.id)
        frames = api.research._runs[started.id].frames
        kinds = [kind for kind, _ in frames]
        assert "research_readings" in kinds
        assert kinds[-1] == "research_complete"

        progress = [data for kind, data in frames if kind == "research_progress"]
        assert {frame["phase"] for frame in progress} >= {
            "orient",
            "consult",
            "plan",
            "execute",
            "read",
            "round",
            "synthesize",
        }
        assert max(frame["round_index"] for frame in progress) >= 1
        assert all(frame["round_total"] >= 1 for frame in progress)
        # The readings frame arrives BEFORE the report, carrying the reason
        # each reading is in the run.
        first_readings = kinds.index("research_readings")
        assert first_readings < kinds.index("research_complete")
        readings = next(data for kind, data in frames if kind == "research_readings")
        assert readings["readings"]
        assert all(entry["reason"] for entry in readings["readings"])

    async def test_a_study_stopped_MID_READING_publishes_nothing(self) -> None:
        """Cancellation-safe as the review is, at the boundary it added.

        The loop yields between readings so a stopped run lands on a
        reading boundary rather than mid-estimate. This stops one after it
        has really started measuring — the state a run is in for most of
        its minute — and requires that nothing partial reaches storage.
        """
        api = service()
        started = await api.start_deep_research(
            CALLER, StartDeepResearchRequest(question=self.QUESTION)
        )
        state = api.research._runs[started.id]
        for _ in range(6_000):
            if state.progress.phase == "execute" and state.progress.angle_index > 0:
                break
            await asyncio.sleep(0.005)
        assert state.progress.phase == "execute", (
            f"the run never began measuring — it reached {state.progress.phase}"
        )
        await api.research.cancel(CALLER, started.id)
        assert state.status == "interrupted"
        assert state.research_report is None
        assert state.report is None
        assert await api.components.investigations.get(started.id) is None
        record = await api.components.traces.get(
            f"{started.id}{DEEP_RESEARCH_TRACE_SUFFIX}"
        )
        assert record is not None
        assert record.payload["status"] == "running"
        assert "report" not in record.payload


class TestAStudyThatRaisesEndsAsAStudyThatFailed:
    """One outcome envelope, around both kinds of run.

    The generalized branch shipped without one for exactly as long as it
    took to run it against real data: a reading that raised left the run
    reading "running" forever with a frozen progress line, a stream nobody
    closed, and an exception nobody retrieved. A background job that can
    end in silence is worse than one that fails loudly, because the surface
    watching it never stops waiting.
    """

    QUESTION = "research why our A/R over 90 has been climbing"

    async def test_a_raising_study_fails_loudly_and_releases_its_watchers(
        self,
    ) -> None:
        api = service()

        class _Exploding:
            async def run(self, **kwargs):
                raise RuntimeError("a reading blew up")

            async def preview(self, **kwargs):  # pragma: no cover - not reached
                raise RuntimeError("a reading blew up")

        research = api.research
        research._research = _Exploding()  # type: ignore[assignment]
        started = await api.start_deep_research(
            CALLER, StartDeepResearchRequest(question=self.QUESTION)
        )
        await _finish(api, started.id)
        state = research._runs[started.id]
        assert state.status == "failed"
        assert state.error == "This run stopped before it could finish."
        assert state.research_report is None and state.report is None
        # The stream is closed rather than left open on a run that will
        # never emit again.
        kinds = [kind for kind, _ in state.frames]
        assert kinds[-1] == "error"
        assert not state.watchers
        # And nothing partial was written: the trace still says it started.
        assert await api.components.investigations.get(started.id) is None
        record = await api.components.traces.get(
            f"{started.id}{DEEP_RESEARCH_TRACE_SUFFIX}"
        )
        assert record is not None and "report" not in record.payload

    async def test_a_refused_read_names_the_gap_rather_than_stopping_silently(
        self,
    ) -> None:
        api = service()

        class _Refusing:
            async def run(self, **kwargs):
                raise UnsupportedConceptError("this data carries no such measure")

        api.research._research = _Refusing()  # type: ignore[assignment]
        started = await api.start_deep_research(
            CALLER, StartDeepResearchRequest(question=self.QUESTION)
        )
        await _finish(api, started.id)
        finished = await api.get_deep_research(CALLER, started.id)
        assert finished.status == "failed"
        assert "no such measure" in (finished.error or "")
