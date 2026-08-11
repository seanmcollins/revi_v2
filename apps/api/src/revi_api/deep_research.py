"""Deep research at the transport boundary: launch, watch, read, list.

A run is a long-running investigation, so it needs three things an ordinary
turn does not: an id that outlives the request that started it, a way to
watch it while it works, and a rule for what survives if it never finishes.

**The id is an investigation id.** A finished run is written to the ordinary
investigation store, in the caller's own conversation, with the report on a
trace beside it. That is what makes it permalinkable, tenant-scoped and
listable without a second storage system — deep research is a mode of
investigation, not a parallel one, and the storage says so.

**Watching is a stream per run, not a stream per request.** The turn stream
binds its queue to the request that started it, which is correct for
something that finishes inside one response and wrong for something that
outlives one. So a run owns a fan-out registry: any number of watchers may
attach, a watcher that arrives late is caught up from the frames already
emitted, and a watcher that leaves does not disturb the run.

**A killed run persists its trace and nothing else.** The trace is written
when the run starts — its identity, its plan, its population — and rewritten
with the report when it finishes. In between, nothing partial is stored: a
process that dies mid-run leaves a record saying a run started and never
completed, which is exactly what happened, and no half-priced total.

**A run can be stopped, and stopping is a different fact from dying.** A
run is about a minute of real work and a real model call, so the person
who started one may end it: the loop yields between readings, the stop
lands on one of those boundaries, the task carrying the model call is
cancelled and the spend stops with it. What the run had got to is written
to its own trace — ``cancelled``, when, how far — because "somebody
stopped this" is a thing that happened and the record should say so.
``interrupted`` keeps its older, weaker meaning: nobody asked, the process
died, and all we can honestly claim is that the run started.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from revi_api.auth import Principal
from revi_api.deep_research_preview import generalized_preview_payload
from revi_api.warning_codes import structured_warnings
from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.deep_research import (
    DeepResearchProgress,
    DeepResearchService,
    GeneralizedReportDraft,
    GeneralizedResearchLoop,
    PopulationKind,
    ResearchProgressUpdate,
    TargetPopulation,
    build_generalized_report,
    planned_reading_payloads,
)
from revi_investigation.application.deep_research import copy as words
from revi_investigation.application.deep_research.general import walk_fingerprint
from revi_investigation.application.deep_research.loop import ResearchPreview, population_words
from revi_investigation.application.deep_research.report import plan_payload_of
from revi_investigation.application.deep_research.rows import DeepResearchReadRefused
from revi_investigation.application.ports import (
    InvestigationStore,
    LlmCallPolicy,
    TraceRecord,
    TraceStore,
)
from revi_investigation.domain.context import AnalysisSpec, PackVersionRef, empty_context
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    InvestigationStatus,
    Session,
)
from revi_investigation.domain.turns import TurnClass
from revi_investigation_contracts.api import FindingPayload, WarningPayload
from revi_investigation_contracts.deep_research import (
    DeepResearchListResponse,
    DeepResearchPreviewPayload,
    DeepResearchProgressPayload,
    DeepResearchReport,
    DeepResearchRunResponse,
    DeepResearchScopePayload,
    DeepResearchSelector,
    DeepResearchStatusLiteral,
    DeepResearchSummary,
    GeneralizedResearchPreviewPayload,
    GeneralizedResearchReport,
    StartDeepResearchRequest,
)
from revi_kernel.errors import (
    ReferentNotFoundError,
    ReviError,
    UnsupportedConceptError,
)
from revi_kernel.filters import Scalar
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import (
    DateBasisRef,
    EntityGrain,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
)
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark

logger = logging.getLogger("revi.api.deep_research")

#: Deep-research runs carry a typed id. It is an investigation id like any
#: other — this prefix is what lets a tenant's runs be listed out of their
#: conversations without a second index, and what stops an ordinary turn's
#: permalink from ever being mistaken for one.
RUN_ID_PREFIX = "dr_"

#: Prefix on the id a confirmation card carries back. Distinct from a run's
#: so the two can never be mistaken for one another on the wire.
PLAN_ID_PREFIX = "pl_"

#: How many previewed plans are remembered at once. A preview is cheap and a
#: reader may open several cards before confirming one; past this the oldest
#: are dropped and the run simply re-plans, which is what it did before.
_PLAN_CACHE_LIMIT = 256

#: The report rides on a supplementary trace beside the run's investigation,
#: exactly as the narrative validator's record and the worklist page do.
DEEP_RESEARCH_TRACE_SUFFIX = ":deep_research"

#: How many frames a late watcher is caught up with. A run emits a few dozen;
#: this is generous enough that nobody joining mid-run misses the plan.
_REPLAY_LIMIT = 256

#: What a run that nobody stopped, and that never finished, has to say for
#: itself. The process died holding it; that is the whole of what is known.
_INTERRUPTED_MESSAGE = "This run was stopped before it finished, so nothing was published."

#: And what a run somebody stopped has to say. A different sentence for a
#: different fact: nothing went wrong here, so nothing in it reads as an
#: apology or as a fault, and it says what survives rather than only what
#: does not. "On request" rather than "you stopped it" because a second
#: reader in the same organization may have been the one who pressed it.
_STOPPED_MESSAGE = (
    "This run was stopped on request, so nothing was published. "
    "What it had got through is kept."
)

#: Every state a run cannot leave. A watcher attaching to one is caught up
#: from the frames already emitted and released rather than left waiting
#: for a frame that will never come.
_SETTLED = ("complete", "failed", "interrupted", "cancelled")

#: And every state a run can still be stopped from. Narrower than "not
#: settled" on purpose: a run whose report has been composed and stored is
#: ``complete`` a moment before its task returns, and a stop landing in
#: that window must not withdraw a report that was already published.
_STOPPABLE = ("planning", "running")


def is_run_id(candidate: str) -> bool:
    return candidate.startswith(RUN_ID_PREFIX)


def new_run_id() -> str:
    return f"{RUN_ID_PREFIX}{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# in-flight state


@dataclass
class _Watcher:
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None]


@dataclass
class RunState:
    """One run, while the process that started it is still alive."""

    id: str
    session_id: str
    tenant: str
    created_at: datetime
    population: DeepResearchSelector
    data_load_label: str
    #: What this run is answering, known before anything runs. Empty on the
    #: standing review, which is a question nobody typed.
    research_question: str = ""
    status: str = "planning"
    progress: DeepResearchProgressPayload = field(
        default_factory=lambda: DeepResearchProgressPayload(phase="plan")
    )
    report: DeepResearchReport | None = None
    #: A research study's report. At most one of the two is ever set; a run
    #: that produced neither has produced nothing, which is what a failed or
    #: in-flight run has.
    research_report: GeneralizedResearchReport | None = None
    report_kind: str | None = None
    error: str | None = None
    frames: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    watchers: list[_Watcher] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    #: Somebody asked for this run to stop. Set before the task is
    #: cancelled and read when the cancellation lands, because that is the
    #: only thing that distinguishes the two ways a run ends without
    #: finishing: a request, or a process that died holding it.
    stop_requested: bool = False

    def response(self) -> DeepResearchRunResponse:
        return DeepResearchRunResponse(
            id=self.id,
            session_id=self.session_id,
            status=self.status,  # type: ignore[arg-type]
            created_at=self.created_at,
            population=self.population,
            data_load_label=self.data_load_label,
            research_question=self.research_question,
            progress=self.progress,
            report=self.report,
            research_report=self.research_report,
            report_kind=self.report_kind,  # type: ignore[arg-type]
            error=self.error,
        )

    async def emit(self, kind: str, data: Mapping[str, Any]) -> None:
        frame = (kind, dict(data))
        self.frames.append(frame)
        del self.frames[:-_REPLAY_LIMIT]
        for watcher in list(self.watchers):
            watcher.queue.put_nowait(frame)

    def close(self) -> None:
        for watcher in list(self.watchers):
            watcher.queue.put_nowait(None)


# ---------------------------------------------------------------------------
# the service


def _selector(request: StartDeepResearchRequest) -> TargetPopulation:
    values = tuple(value.strip() for value in request.population.values if value.strip())
    try:
        kind = PopulationKind(request.population.kind)
    except ValueError as exc:  # pragma: no cover - the wire literal prevents it
        raise UnsupportedConceptError(f"unknown population selector {request.population.kind!r}") from exc
    try:
        return TargetPopulation(kind=kind, values=values)
    except ValueError as exc:
        raise UnsupportedConceptError(str(exc)) from exc


def _other_populations(
    selector: DeepResearchSelector,
    narrowings: Sequence[Any] = (),
) -> list[DeepResearchSelector]:
    """The other populations this same offer could honestly run over.

    Two directions, and until now only one of them existed. WIDER comes
    from the selector itself: every open denial, and each named value on
    its own where the offer names several. NARROWER comes from the census
    the preview already read — where the open money actually is — because
    a reader looking at every open denial wants to cut it down, and being
    offered only "every open denial" when that is what they are already
    looking at is an empty affordance on the most ambiguous question the
    surface accepts.

    Nothing is invented: every narrowing names a population read off the
    same rows the preview counted, and only ones large enough to name.
    """
    options: list[DeepResearchSelector] = []
    if selector.kind != "all_open":
        options.append(DeepResearchSelector(kind="all_open", label="every open denial"))
    if len(selector.values) > 1:
        options.extend(
            DeepResearchSelector(kind=selector.kind, values=[value], label=value)
            for value in selector.values
        )
    if selector.kind == "all_open":
        options.extend(
            DeepResearchSelector(
                kind=option.kind,  # type: ignore[arg-type]
                values=[option.value],
                label=words.population_label(option.kind, (option.value,)),
            )
            for option in narrowings
        )
    return options


def _scalar(value: str | int | float | bool | None) -> Scalar:
    """A published figure, as the store's own value type.

    Floats are not a kernel scalar — money and rates travel as exact
    decimals everywhere else, and a stored finding must not be the one
    place a value arrives as a binary fraction.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _finding(payload: FindingPayload) -> Finding:
    """A published finding, as the investigation store holds one."""
    return Finding(
        referent=ReferentId(value=payload.referent, kind=ReferentKind.FINDING),
        title=payload.title,
        statement=payload.statement,
        metric_refs=tuple(MetricRef(metric_id) for metric_id in payload.metric_ids),
        values=tuple(
            (value.name, _scalar(value.value))
            for value in payload.values
            if value.value is None or isinstance(value.value, (str, int, float, bool))
        ),
        grade=EvidenceGrade.DIRECT,
        impact_cents=payload.impact_cents,
    )


class DeepResearchApi:
    """Launches runs, streams them, serves them back."""

    def __init__(
        self,
        *,
        service: DeepResearchService,
        investigations: InvestigationStore,
        traces: TraceStore,
        pack_id: str,
        pack_version: str,
        pack_snapshot_id: str,
        compose: Any = None,
        compose_research: Any = None,
        research: GeneralizedResearchLoop | None = None,
        catalog: CatalogSnapshot | None = None,
    ) -> None:
        self._service = service
        self._investigations = investigations
        self._traces = traces
        self._pack_id = pack_id
        self._pack_version = pack_version
        self._pack_snapshot_id = pack_snapshot_id
        self._compose = compose
        #: The determination composer. Separate from ``compose`` because the
        #: two write different artifacts from different material — a study's
        #: composer is shown the walk and the background notes, and an
        #: answer's is shown neither.
        self._compose_research = compose_research
        #: The generalized loop, for a request that carries a research
        #: QUESTION rather than the standing recoverability review. Optional
        #: so a deployment with no catalog wired still previews the review.
        self._research = research
        self._catalog = catalog
        self._runs: dict[str, RunState] = {}
        #: Plans a reader has been shown, by the id the card carries. A
        #: confirmation that re-planned from the question would be a
        #: confirmation of a SAMPLE — the reader approves one set of
        #: readings and the run takes another, both legitimately drawn from
        #: the same question. Keeping the resolved plan here is what makes
        #: "the plan you approved is the plan that ran" a fact rather than a
        #: hope. Bounded, because an unbounded one is a leak.
        self._plans: dict[str, tuple[str, ResearchPreview]] = {}

    # -- the dry run ---------------------------------------------------------

    async def preview(
        self,
        request: StartDeepResearchRequest,
        session: Session,
        watermark: DataWatermark,
        settings: Any,
    ) -> DeepResearchRunResponse:
        """What a run WOULD do — nothing started, nothing stored.

        The confirmation in front of a minute of work needs three things a
        surface cannot compose for itself without asserting facts it has
        not read: how big the population is, which angles the run would
        take, and which other populations the same offer could run over.
        All three come from here, and the run this previews is the same
        run: the plan is the standing set the run resolves, and the size is
        read through the run's own cache, so confirming and then running
        costs one read.
        """
        population = _selector(request)
        resolved = await self._service.preview(
            question=request.question,
            population=population,
            settings=settings,
            watermark=watermark,
            pack_snapshot_id=self._pack_snapshot_id,
        )
        selector = DeepResearchSelector(
            kind=request.population.kind,
            values=list(population.values),
            label=request.population.label,
        )
        return DeepResearchRunResponse(
            id="",
            session_id=session.id,
            status="preview",
            created_at=datetime.now(UTC),
            population=selector,
            data_load_label=(
                f"the load through {watermark.newest_data_date.strftime('%b %-d, %Y')}"
            ),
            progress=DeepResearchProgressPayload(phase="plan"),
            preview=DeepResearchPreviewPayload(
                population=selector,
                scope=DeepResearchScopePayload(
                    open_denials=resolved.open_denials,
                    open_dollars_cents=resolved.open_dollars_cents,
                ),
                plan=plan_payload_of(resolved.plan, settings),
                options=_other_populations(selector, resolved.narrowings),
                data_load_label=(
                    f"the load through {watermark.newest_data_date.strftime('%b %-d, %Y')}"
                ),
                generalized=await self._generalized_preview(
                    request, population, watermark, settings
                ),
            ),
        )

    async def _generalized_preview(
        self,
        request: StartDeepResearchRequest,
        population: TargetPopulation,
        watermark: DataWatermark,
        settings: Any,
    ) -> GeneralizedResearchPreviewPayload | None:
        """What a research QUESTION would look at — orient, consult, plan.

        Only for a request that carries a question. Without one there is no
        research question to research, and the standing recoverability
        review already describes itself through the closed catalogue.

        A failure here degrades to the review's own description rather than
        failing the confirmation: the reader still gets a card that says
        what will be looked at, and the run they confirm is unaffected —
        the loop re-orients for itself and never reads this payload.
        """
        question = (request.question or "").strip()
        if not question or self._research is None or self._catalog is None:
            return None
        try:
            resolved = await self._research.preview(
                question=question,
                population=population,
                settings=settings,
                watermark=watermark,
                pack_snapshot_id=self._pack_snapshot_id,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("generalized research preview failed")
            return None
        plan_id = self._remember_plan(question, population, watermark, resolved)
        return generalized_preview_payload(resolved, self._catalog, plan_id=plan_id)

    def _plan_key(
        self, question: str, population: TargetPopulation, watermark: DataWatermark
    ) -> str:
        """What a confirmed plan is only valid for.

        The same readings over a different population, a different period
        of data, or a different question are a different analysis. A plan
        id that outlived any of those would let a confirmation authorise a
        run the reader never saw."""
        parts = (question, str(population.kind), *population.values, watermark.id)
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def _remember_plan(
        self,
        question: str,
        population: TargetPopulation,
        watermark: DataWatermark,
        resolved: ResearchPreview,
    ) -> str:
        plan_id = PLAN_ID_PREFIX + uuid.uuid4().hex[:16]
        if len(self._plans) >= _PLAN_CACHE_LIMIT:
            for stale in list(self._plans)[: len(self._plans) - _PLAN_CACHE_LIMIT + 1]:
                self._plans.pop(stale, None)
        self._plans[plan_id] = (self._plan_key(question, population, watermark), resolved)
        return plan_id

    def _confirmed_plan(
        self,
        plan_id: str | None,
        question: str,
        population: TargetPopulation,
        watermark: DataWatermark,
    ) -> ResearchPreview | None:
        """The plan this request confirmed, if it confirmed one that still fits."""
        if not plan_id:
            return None
        remembered = self._plans.get(plan_id)
        if remembered is None:
            return None
        key, resolved = remembered
        if key != self._plan_key(question, population, watermark):
            return None
        return resolved

    # -- launch --------------------------------------------------------------

    async def start(
        self,
        principal: Principal,
        request: StartDeepResearchRequest,
        session: Session,
        watermark: DataWatermark,
        settings: Any,
        *,
        llm_policy: LlmCallPolicy | None = None,
    ) -> DeepResearchRunResponse:
        population = _selector(request)
        run_id = new_run_id()
        label = f"the load through {watermark.newest_data_date.strftime('%b %-d, %Y')}"
        state = RunState(
            id=run_id,
            session_id=session.id,
            tenant=principal.tenant,
            created_at=datetime.now(UTC),
            population=DeepResearchSelector(
                kind=request.population.kind,
                values=list(population.values),
                label=request.population.label,
            ),
            data_load_label=label,
            research_question=(request.question or "").strip(),
        )
        self._runs[run_id] = state
        await self._write_start_trace(state, request, watermark)
        await state.emit(
            "research_started",
            {
                "id": run_id,
                "session_id": session.id,
                "data_load": label,
                "research_question": state.research_question,
                "population": state.population.model_dump(mode="json"),
            },
        )
        state.task = asyncio.create_task(
            self._run(state, request, session, watermark, settings, llm_policy)
        )
        return state.response()

    def _studies(self, request: StartDeepResearchRequest) -> bool:
        """Is this request a research QUESTION rather than the standing review?

        One branch, decided once. A run carrying a question the semantic
        layer can research is a study and publishes a study's report; a run
        carrying none is the recoverability review and publishes the
        review's, byte for byte as it has since M48. The question is the
        whole of the condition because it is the whole of the difference:
        the review answers a question nobody typed.
        """
        question = (request.question or "").strip()
        return bool(question) and self._research is not None and self._catalog is not None

    async def _run(
        self,
        state: RunState,
        request: StartDeepResearchRequest,
        session: Session,
        watermark: DataWatermark,
        settings: Any,
        llm_policy: LlmCallPolicy | None,
    ) -> None:
        """One run, whichever kind it is, inside ONE outcome envelope.

        The envelope is here rather than inside each branch, and that is
        the whole point of its being here: a run that raises anywhere must
        end as ``failed`` with its watchers released, and a branch that
        carried its own try/except would be a second place that promise is
        made — which is exactly how the generalized branch shipped without
        one and left a raising study reading "running" forever, with a
        frozen progress line, a stream nobody closed and an exception
        nobody retrieved.
        """
        state.status = "running"
        try:
            if self._studies(request):
                if await self._run_generalized(
                    state, request, session, watermark, settings, llm_policy
                ):
                    return
                # The question reached no measure this deployment carries.
                # The study refused, and the run on offer — the
                # recoverability review — measures something that refusal
                # is not about, so it is what runs. The card said exactly
                # this before the click.
                logger.info(
                    "deep research run %s: no measure reached this question; "
                    "running the review",
                    state.id,
                )
            await self._run_recovery(
                state, request, session, watermark, settings, llm_policy
            )
        except asyncio.CancelledError:
            # TWO WAYS A RUN ENDS WITHOUT FINISHING, AND THEY ARE NOT THE
            # SAME EVENT. Somebody pressed Stop, or the process carrying
            # this run was torn down under it. Only the first is a thing a
            # reader did, and telling them the second in the same words
            # would report an outage as their own decision.
            if state.stop_requested:
                await self._settle_stopped(state)
            else:
                state.status = "interrupted"
                state.error = _INTERRUPTED_MESSAGE
                await state.emit("error", {"message": state.error})
            raise
        except (DeepResearchReadRefused, ReviError) as refusal:
            state.status = "failed"
            state.error = str(refusal)
            logger.warning("deep research run %s refused: %s", state.id, refusal)
            await state.emit("error", {"message": state.error})
        except Exception:
            state.status = "failed"
            state.error = "This run stopped before it could finish."
            logger.exception("deep research run %s failed", state.id)
            await state.emit("error", {"message": state.error})
        finally:
            state.close()

    # -- the generalized study ------------------------------------------------

    async def _run_generalized(
        self,
        state: RunState,
        request: StartDeepResearchRequest,
        session: Session,
        watermark: DataWatermark,
        settings: Any,
        llm_policy: LlmCallPolicy | None,
    ) -> bool:
        """Execute the planned generalized walk. ``False`` if nothing ran.

        The loop owns the walk; this owns the wire. Every round the loop
        takes becomes progress frames naming the round, every reading
        becomes part of the report, and the recorded walk is persisted as
        the plan — which is what makes a permalink restore the study and
        replay re-execute it without re-deciding anything.
        """
        assert self._research is not None and self._catalog is not None
        started = time.monotonic()
        #: What the run has been seen reading, in the order it read it. The
        #: readings frame is re-emitted as the list grows, so a watcher that
        #: attaches at any point holds the whole list and one that has been
        #: here since the start sees each reading appear as it is taken.
        seen: list[dict[str, object]] = []

        async def progress(update: ResearchProgressUpdate) -> None:
            state.progress = DeepResearchProgressPayload(
                phase=update.phase,  # type: ignore[arg-type]
                angle_index=update.reading_index,
                angle_total=update.reading_total,
                message=update.message,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                round_index=update.round_index,
                round_total=update.round_total,
            )
            await state.emit("research_progress", state.progress.model_dump(mode="json"))
            if update.reading_title and all(
                entry["title"] != update.reading_title for entry in seen
            ):
                seen.append(
                    {
                        "title": update.reading_title,
                        "reason": update.reading_reason,
                        "round": update.round_index,
                        "chases": update.reading_chases,
                    }
                )
                await state.emit("research_readings", {"readings": list(seen)})

        question = (request.question or "").strip()
        population = _selector(request)
        walk, results, orientation = await self._research.run(
            question=question,
            population=population,
            settings=settings,
            watermark=watermark,
            pack_snapshot_id=self._pack_snapshot_id,
            progress=progress,
            # The plan the reader confirmed, when they confirmed one. The
            # loop re-plans only the rounds beyond it — the ones nobody
            # previewed, because nobody could have.
            confirmed=self._confirmed_plan(
                request.plan_id, question, population, watermark
            ),
        )
        if not results:
            return False

        # The whole list once, now that every reading is known and titled
        # exactly as the report titles it. A watcher that joined late gets
        # this frame from the replay buffer and never sees a partial list.
        await state.emit(
            "research_readings",
            {
                "research_question": walk.question,
                "authored_by": walk.authored_by,
                "rationale": walk.rationale,
                "rounds_planned": walk.budget,
                "readings": planned_reading_payloads(walk.angles, self._catalog),
            },
        )

        completed_at = datetime.now(UTC)
        duration_ms = int((time.monotonic() - started) * 1000)
        draft = build_generalized_report(
            run_id=state.id,
            walk=walk,
            results=results,
            orientation=orientation,
            settings=settings,
            catalog=self._catalog,
            watermark=watermark,
            population_label=population_words(orientation.population),
            created_at=state.created_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        report = draft.report
        for finding in report.findings:
            await state.emit("research_finding", finding.model_dump(mode="json"))
        warnings: list[WarningPayload] = structured_warnings(draft.warnings)
        for warning in warnings:
            await state.emit("research_warning", warning.model_dump(mode="json"))

        determination = ""
        composed = False
        if self._compose_research is not None:
            determination, composed = await self._compose_research(
                draft=draft,
                warnings=warnings,
                emit=lambda delta: state.emit("narrative_delta", {"delta": delta}),
            )
        else:  # pragma: no cover - a deployment with no composer wired
            determination, _ = " ".join(draft.disclosures), False
        report = report.model_copy(
            update={
                "warnings": warnings,
                "determination": report.determination.model_copy(
                    update={"statement": determination, "composed": composed}
                ),
            }
        )
        state.research_report = report
        state.report_kind = "generalized"
        state.status = "complete"
        await self._persist_generalized(state, session, walk, draft, report, watermark)
        await state.emit("research_complete", report.model_dump(mode="json"))
        return True

    # -- the recoverability review -------------------------------------------

    async def _run_recovery(
        self,
        state: RunState,
        request: StartDeepResearchRequest,
        session: Session,
        watermark: DataWatermark,
        settings: Any,
        llm_policy: LlmCallPolicy | None,
    ) -> None:
        async def progress(update: DeepResearchProgress) -> None:
            state.progress = DeepResearchProgressPayload(
                phase=update.phase,  # type: ignore[arg-type]
                angle_index=update.angle_index,
                angle_total=update.angle_total,
                message=update.message,
                elapsed_ms=update.elapsed_ms,
            )
            await state.emit("research_progress", state.progress.model_dump(mode="json"))

        result = await self._service.run(
            run_id=state.id,
            question=request.question,
            population=_selector(request),
            settings=settings,
            watermark=watermark,
            pack_snapshot_id=self._pack_snapshot_id,
            progress=progress,
            llm_policy=llm_policy or LlmCallPolicy(),
            created_at=state.created_at,
        )
        draft = result.draft
        report = draft.report
        await state.emit("research_plan", report.plan.model_dump(mode="json"))
        for finding in report.findings:
            await state.emit("research_finding", finding.model_dump(mode="json"))

        warnings: list[WarningPayload] = structured_warnings(draft.warnings)
        for warning in warnings:
            await state.emit("research_warning", warning.model_dump(mode="json"))

        narrative = ""
        if self._compose is not None:
            narrative = await self._compose(
                draft=draft,
                warnings=warnings,
                emit=lambda delta: state.emit("narrative_delta", {"delta": delta}),
            )
        report = report.model_copy(update={"warnings": warnings, "narrative": narrative})
        state.report = report
        state.report_kind = "recovery"
        state.status = "complete"
        await self._persist(state, session, result, report)
        await state.emit("research_complete", report.model_dump(mode="json"))

    # -- persistence ---------------------------------------------------------

    def _spec(self, watermark: DataWatermark, earliest: date) -> AnalysisSpec:
        """The stored shape of a run, in the vocabulary every investigation
        is stored in: the window it read, the grain it read at, the load it
        was pinned to, and the definitions library it ran under."""
        window = TimeWindow(
            basis=DateBasisRef("service"),
            range=AbsoluteRange(start=earliest, end=watermark.newest_data_date),
        )
        return AnalysisSpec(
            context=empty_context(
                window=window,
                grain=Grain(entity=EntityGrain.CLAIM),
                watermark=watermark,
                pack_version=PackVersionRef(
                    pack_id=self._pack_id, version=self._pack_version
                ),
            ),
            measures=(),
        )

    async def _write_start_trace(
        self,
        state: RunState,
        request: StartDeepResearchRequest,
        watermark: DataWatermark,
    ) -> None:
        await self._traces.save(
            TraceRecord(
                trace_id=f"{state.id}{DEEP_RESEARCH_TRACE_SUFFIX}",
                session_id=state.session_id,
                investigation_id=state.id,
                turn_id=state.id,
                created_at=state.created_at,
                payload={
                    "mode": "deep_research",
                    "status": "running",
                    "tenant": state.tenant,
                    "watermark": watermark.id,
                    "question": request.question or "",
                    "population": state.population.model_dump(mode="json"),
                },
            )
        )

    async def _write_stopped_trace(self, state: RunState) -> None:
        """What a stopped run leaves behind: that it was stopped, and how far.

        The row the start wrote is UPDATED rather than replaced, so the
        run's identity, its question and its population survive exactly as
        they were recorded and only the outcome is written over it. That is
        what makes a stopped run readable after the process holding it is
        gone: without this the trace would still read "running", and a
        permalink opened tomorrow would show a run that has been about to
        finish since yesterday.

        No report, no findings and no investigation are written, because
        none were produced. What is added is the account of the stop — when
        it happened, which phase it landed in, how many rounds had closed
        and which readings had been taken. Truth relocates, never deletes:
        a run that measured four of nine readings measured four, and the
        record says four rather than nothing.
        """
        trace_id = f"{state.id}{DEEP_RESEARCH_TRACE_SUFFIX}"
        existing = await self._traces.get(trace_id)
        payload: dict[str, Any] = (
            dict(existing.payload)
            if existing is not None
            else {
                "mode": "deep_research",
                "tenant": state.tenant,
                "question": state.research_question,
                "population": state.population.model_dump(mode="json"),
            }
        )
        payload.update(
            {
                "status": "cancelled",
                "stopped_at": datetime.now(UTC).isoformat(),
                "data_load_label": state.data_load_label,
                "progress": state.progress.model_dump(mode="json"),
                "rounds_completed": state.progress.round_index or 0,
                "readings_taken": _readings_taken(state),
            }
        )
        await self._traces.save(
            TraceRecord(
                trace_id=trace_id,
                session_id=state.session_id,
                investigation_id=state.id,
                turn_id=state.id,
                created_at=state.created_at,
                payload=payload,
            )
        )

    async def _persist(
        self,
        state: RunState,
        session: Session,
        result: Any,
        report: DeepResearchReport,
    ) -> None:
        investigation = Investigation(
            id=state.id,
            session_id=session.id,
            parent_id=None,
            turn_id=state.id,
            turn_class=TurnClass.NEW_INVESTIGATION,
            question=report.research_question,
            spec=self._spec(result.rows.watermark, result.draft.header.window_start),
            plan_hash=result.fingerprint,
            status=InvestigationStatus.COMPLETE,
            findings=tuple(_finding(payload) for payload in report.findings),
            created_at=state.created_at,
            warnings=tuple(warning.message for warning in report.warnings),
            narrative=report.narrative or None,
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            TraceRecord(
                trace_id=f"{state.id}{DEEP_RESEARCH_TRACE_SUFFIX}",
                session_id=session.id,
                investigation_id=state.id,
                turn_id=state.id,
                created_at=state.created_at,
                payload={
                    "mode": "deep_research",
                    "status": "complete",
                    "tenant": state.tenant,
                    "watermark": result.rows.watermark.id,
                    "plan_fingerprint": result.fingerprint,
                    "read_fingerprint": result.rows.read_fingerprint,
                    "rows_read": result.rows.rows_read,
                    "cache_hit": result.rows.cache_hit,
                    "duration_ms": result.duration_ms,
                    "report": report.model_dump(mode="json"),
                },
            )
        )

    async def _persist_generalized(
        self,
        state: RunState,
        session: Session,
        walk: Any,
        draft: GeneralizedReportDraft,
        report: GeneralizedResearchReport,
        watermark: DataWatermark,
    ) -> None:
        """Store the study, and store the WALK as the thing that produced it.

        ``plan_hash`` is the walk's fingerprint rather than the recovery
        plan's, which is the whole of "the recorded path is the plan": two
        runs sharing it at one data load must publish byte-identical
        figures, and the harness, a permalink and a replay all re-execute
        what was decided without re-deciding it.
        """
        investigation = Investigation(
            id=state.id,
            session_id=session.id,
            parent_id=None,
            turn_id=state.id,
            turn_class=TurnClass.NEW_INVESTIGATION,
            question=report.research_question,
            spec=self._spec(watermark, draft.header.window_start),
            plan_hash=walk_fingerprint(walk),
            status=InvestigationStatus.COMPLETE,
            findings=tuple(_finding(payload) for payload in report.findings),
            created_at=state.created_at,
            warnings=tuple(warning.message for warning in report.warnings),
            narrative=report.determination.statement or None,
        )
        await self._investigations.save(investigation, None)
        await self._traces.save(
            TraceRecord(
                trace_id=f"{state.id}{DEEP_RESEARCH_TRACE_SUFFIX}",
                session_id=session.id,
                investigation_id=state.id,
                turn_id=state.id,
                created_at=state.created_at,
                payload={
                    "mode": "deep_research",
                    "status": "complete",
                    "report_kind": "generalized",
                    "tenant": state.tenant,
                    "watermark": watermark.id,
                    "plan_fingerprint": walk_fingerprint(walk),
                    "rounds": report.walk.rounds_taken,
                    "authored_by": report.walk.authored_by,
                    "duration_ms": report.duration_ms,
                    "report": report.model_dump(mode="json"),
                },
            )
        )

    # -- read ----------------------------------------------------------------

    async def get(self, principal: Principal, run_id: str) -> DeepResearchRunResponse:
        state = self._runs.get(run_id)
        if state is not None:
            if state.tenant != principal.tenant:
                raise ReferentNotFoundError(f"no deep research run {run_id!r}")
            return state.response()
        record = await self._traces.get(f"{run_id}{DEEP_RESEARCH_TRACE_SUFFIX}")
        if record is None:
            raise ReferentNotFoundError(f"no deep research run {run_id!r}")
        payload = dict(record.payload)
        if str(payload.get("tenant", "")) != principal.tenant:
            raise ReferentNotFoundError(f"no deep research run {run_id!r}")
        stored = payload.get("report")
        if not isinstance(stored, dict):
            # A run that published no report. Either somebody stopped it,
            # and the stop recorded how far it had got, or its process died
            # holding it and the trace says only that it started. The row
            # is the authority on which of the two happened; neither is
            # inferred from the absence of a report.
            stopped = str(payload.get("status", "")) == "cancelled"
            return DeepResearchRunResponse(
                id=run_id,
                session_id=record.session_id,
                status="cancelled" if stopped else "interrupted",
                created_at=record.created_at,
                population=DeepResearchSelector.model_validate(
                    payload.get("population") or {}
                ),
                data_load_label=str(payload.get("data_load_label", "")),
                research_question=str(payload.get("question", "")),
                progress=_stored_progress(payload),
                error=_STOPPED_MESSAGE if stopped else _INTERRUPTED_MESSAGE,
            )
        if str(payload.get("report_kind", "")) == "generalized":
            study = GeneralizedResearchReport.model_validate(stored)
            return DeepResearchRunResponse(
                id=run_id,
                session_id=record.session_id,
                status="complete",
                created_at=record.created_at,
                population=study.population,
                data_load_label=study.data_load_label,
                research_question=study.research_question,
                progress=DeepResearchProgressPayload(
                    phase="synthesize",
                    message="Finished",
                    elapsed_ms=study.duration_ms,
                    round_index=study.walk.rounds_taken,
                    round_total=study.walk.rounds_allowed,
                ),
                research_report=study,
                report_kind="generalized",
            )
        report = DeepResearchReport.model_validate(stored)
        return DeepResearchRunResponse(
            id=run_id,
            session_id=record.session_id,
            status="complete",
            created_at=record.created_at,
            population=report.population,
            data_load_label=report.data_load_label,
            progress=DeepResearchProgressPayload(
                phase="synthesize", message="Finished", elapsed_ms=report.duration_ms
            ),
            report=report,
            report_kind="recovery",
        )

    async def list_runs(
        self, principal: Principal, sessions: Sequence[Session], *, limit: int
    ) -> DeepResearchListResponse:
        """Every run in this tenant's conversations, newest first."""
        summaries: list[DeepResearchSummary] = []
        seen: set[str] = set()
        for state in self._runs.values():
            if state.tenant != principal.tenant:
                continue
            seen.add(state.id)
            question = ""
            if state.report is not None:
                question = state.report.research_question
            elif state.research_report is not None:
                question = state.research_report.research_question
            summaries.append(
                DeepResearchSummary(
                    id=state.id,
                    session_id=state.session_id,
                    status=state.status,  # type: ignore[arg-type]
                    created_at=state.created_at,
                    research_question=question,
                    population=state.population,
                    data_load_label=state.data_load_label,
                    total_expected_cents=(
                        state.report.headline.total_expected_cents
                        if state.report
                        else None
                    ),
                    report_kind=state.report_kind,  # type: ignore[arg-type]
                )
            )
        for session in sessions:
            lineage = await self._investigations.lineage(session.id)
            if lineage is None:
                continue
            for investigation in lineage.investigations:
                if not is_run_id(investigation.id) or investigation.id in seen:
                    continue
                seen.add(investigation.id)
                record = await self._traces.get(
                    f"{investigation.id}{DEEP_RESEARCH_TRACE_SUFFIX}"
                )
                stored = (record.payload.get("report") if record else None) or {}
                generalized = (
                    record is not None
                    and str(record.payload.get("report_kind", "")) == "generalized"
                )
                report: DeepResearchReport | None = None
                study: GeneralizedResearchReport | None = None
                if isinstance(stored, dict) and stored:
                    if generalized:
                        study = GeneralizedResearchReport.model_validate(stored)
                    else:
                        report = DeepResearchReport.model_validate(stored)
                finished = report is not None or study is not None
                # A run with no report either was stopped or was lost, and
                # the row says which. A list that called every unfinished
                # run "interrupted" would report a reader's own decision
                # back to them as a failure.
                unfinished: DeepResearchStatusLiteral = (
                    "cancelled"
                    if record is not None
                    and str(record.payload.get("status", "")) == "cancelled"
                    else "interrupted"
                )
                summaries.append(
                    DeepResearchSummary(
                        id=investigation.id,
                        session_id=session.id,
                        status="complete" if finished else unfinished,
                        created_at=investigation.created_at,
                        research_question=investigation.question or "",
                        population=(
                            report.population
                            if report is not None
                            else study.population
                            if study is not None
                            else DeepResearchSelector()
                        ),
                        data_load_label=(
                            report.data_load_label
                            if report is not None
                            else study.data_load_label
                            if study is not None
                            else ""
                        ),
                        total_expected_cents=(
                            report.headline.total_expected_cents if report else None
                        ),
                        report_kind=(
                            "generalized"
                            if study is not None
                            else "recovery"
                            if report is not None
                            else None
                        ),
                    )
                )
        summaries.sort(key=lambda summary: summary.created_at, reverse=True)
        return DeepResearchListResponse(runs=summaries[:limit])

    # -- watch ---------------------------------------------------------------

    async def stream(self, principal: Principal, run_id: str) -> AsyncIterator[str]:
        """Server-sent frames for one run, from wherever it has got to."""
        state = self._runs.get(run_id)
        if state is None or state.tenant != principal.tenant:
            raise ReferentNotFoundError(f"no deep research run {run_id!r}")
        watcher = _Watcher(queue=asyncio.Queue())
        replay = list(state.frames)
        finished = state.status in _SETTLED
        if not finished:
            state.watchers.append(watcher)
        try:
            for kind, data in replay:
                yield _sse(kind, data)
            if finished:
                return
            while True:
                frame = await watcher.queue.get()
                if frame is None:
                    return
                yield _sse(frame[0], frame[1])
        finally:
            with contextlib.suppress(ValueError):
                state.watchers.remove(watcher)

    # -- stop ----------------------------------------------------------------

    async def cancel(self, principal: Principal, run_id: str) -> DeepResearchRunResponse:
        """Stop a run, and answer with the run it stopped.

        THE SPEND IS THE REASON THIS EXISTS. A run is about a minute of
        real work and a real model call, both awaited inside the task this
        cancels, so cancelling it unwinds the model call at its own await
        and the meter stops. Without this route a reader who changed their
        mind could only close the tab, which stops the watching and none of
        the spending.

        IT LANDS ON A BOUNDARY, NOT MID-ESTIMATE. Both loops yield between
        readings for exactly this reason, so a stopped run is stopped
        between two measurements rather than half way through one — the
        same discipline that keeps a partial figure from ever existing to
        be persisted.

        WHAT IS ANSWERED IS THE RUN. The same shape the run's own GET
        returns, with ``cancelled`` on it, so the surface that pressed Stop
        renders the outcome from the response rather than inferring it.

        Stopping something already finished is not an error and does not
        rewrite it: a run that has published its report keeps it, and the
        answer is that report. The only refusal here is the one every other
        read makes — an unknown run, or one belonging to somebody else, is
        a miss.
        """
        state = self._runs.get(run_id)
        if state is None:
            # Nothing in flight in this process. The stored run is the
            # honest answer, and its own read refuses an unknown run and a
            # foreign tenant's run in exactly the same words.
            return await self.get(principal, run_id)
        if state.tenant != principal.tenant:
            raise ReferentNotFoundError(f"no deep research run {run_id!r}")
        if state.task is None or state.task.done() or state.status not in _STOPPABLE:
            return state.response()
        state.stop_requested = True
        state.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.task
        # Belt and braces: a task cancelled before its first step never
        # reaches the handler inside ``_run``, and a run left "planning"
        # with watchers still attached is the hang this route exists to
        # prevent. ``_settle_stopped`` acts only on a run that is still
        # going, so the ordinary path — where the handler already ran —
        # passes through it untouched, and a run that finished inside the
        # same tick keeps the outcome it earned.
        await self._settle_stopped(state)
        if state.status == "cancelled":
            await self._write_stopped_trace(state)
        return state.response()

    async def _settle_stopped(self, state: RunState) -> None:
        """Mark a stopped run stopped, tell its watchers, release them.

        One place, called from both sides of the cancellation, because the
        alternative is two places that must agree about what a stopped run
        looks like.

        A run that is no longer going is left exactly as it is. That covers
        the second call on one stop (idempotent), and the narrower case
        that matters: a run that composed and stored its report in the tick
        the stop arrived is ``complete``, and stopping the work that had
        already produced a report must not withdraw the report.
        """
        if state.status not in _STOPPABLE:
            return
        state.status = "cancelled"
        state.error = _STOPPED_MESSAGE
        await state.emit(
            "research_cancelled",
            {
                "id": state.id,
                "message": state.error,
                "progress": state.progress.model_dump(mode="json"),
            },
        )
        state.close()


def _stored_progress(payload: Mapping[str, Any]) -> DeepResearchProgressPayload:
    """How far a run got, as its own record says — or the plainest default.

    A stopped run wrote its progress line down at the moment it stopped, so
    reading it back is how a permalink shows four readings of nine rather
    than a bare "stopped". A record with nothing to say gets the default,
    which asserts only that the run was executing when it ended.
    """
    stored = payload.get("progress")
    if isinstance(stored, Mapping):
        try:
            return DeepResearchProgressPayload.model_validate(dict(stored))
        except ValueError:  # pragma: no cover - a row written by an older build
            pass
    return DeepResearchProgressPayload(phase="execute")


def _readings_taken(state: RunState) -> list[str]:
    """The readings a run had been seen taking, in the order it took them.

    Read off the frames the run already emitted rather than off a second
    list kept for this purpose, so what a stopped run's record says it was
    doing is exactly what its watchers were shown it doing. The last entry
    is the reading the stop landed on: begun, and — because the stop lands
    on a boundary — not published.
    """
    titles: list[str] = []
    for kind, data in state.frames:
        if kind != "research_readings":
            continue
        entries = data.get("readings")
        if not isinstance(entries, list):
            continue
        titles = [
            str(entry["title"])
            for entry in entries
            if isinstance(entry, dict) and entry.get("title")
        ]
    return titles


def _sse(kind: str, data: Mapping[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n"
