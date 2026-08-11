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
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from revi_api.auth import Principal
from revi_api.warning_codes import structured_warnings
from revi_investigation.application.deep_research import (
    DeepResearchProgress,
    DeepResearchService,
    PopulationKind,
    TargetPopulation,
)
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
    DeepResearchSummary,
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

#: The report rides on a supplementary trace beside the run's investigation,
#: exactly as the narrative validator's record and the worklist page do.
DEEP_RESEARCH_TRACE_SUFFIX = ":deep_research"

#: How many frames a late watcher is caught up with. A run emits a few dozen;
#: this is generous enough that nobody joining mid-run misses the plan.
_REPLAY_LIMIT = 256


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
    status: str = "planning"
    progress: DeepResearchProgressPayload = field(
        default_factory=lambda: DeepResearchProgressPayload(phase="plan")
    )
    report: DeepResearchReport | None = None
    error: str | None = None
    frames: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    watchers: list[_Watcher] = field(default_factory=list)
    task: asyncio.Task[None] | None = None

    def response(self) -> DeepResearchRunResponse:
        return DeepResearchRunResponse(
            id=self.id,
            session_id=self.session_id,
            status=self.status,  # type: ignore[arg-type]
            created_at=self.created_at,
            population=self.population,
            data_load_label=self.data_load_label,
            progress=self.progress,
            report=self.report,
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


def _other_populations(selector: DeepResearchSelector) -> list[DeepResearchSelector]:
    """The other populations this same offer could honestly run over.

    Derived from the selector itself and from nothing else — every open
    denial, and each named value on its own where the offer names several.
    A client that had to invent alternatives would be widening a scope
    nobody asked it to widen; a client offered a closed selector posts
    exactly what it was given.
    """
    options: list[DeepResearchSelector] = []
    if selector.kind != "all_open":
        options.append(DeepResearchSelector(kind="all_open", label="every open denial"))
    if len(selector.values) > 1:
        options.extend(
            DeepResearchSelector(kind=selector.kind, values=[value], label=value)
            for value in selector.values
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
    ) -> None:
        self._service = service
        self._investigations = investigations
        self._traces = traces
        self._pack_id = pack_id
        self._pack_version = pack_version
        self._pack_snapshot_id = pack_snapshot_id
        self._compose = compose
        self._runs: dict[str, RunState] = {}

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
                options=_other_populations(selector),
                data_load_label=(
                    f"the load through {watermark.newest_data_date.strftime('%b %-d, %Y')}"
                ),
            ),
        )

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
        )
        self._runs[run_id] = state
        await self._write_start_trace(state, request, watermark)
        await state.emit(
            "research_started",
            {
                "id": run_id,
                "session_id": session.id,
                "data_load": label,
                "population": state.population.model_dump(mode="json"),
            },
        )
        state.task = asyncio.create_task(
            self._run(state, request, session, watermark, settings, llm_policy)
        )
        return state.response()

    async def _run(
        self,
        state: RunState,
        request: StartDeepResearchRequest,
        session: Session,
        watermark: DataWatermark,
        settings: Any,
        llm_policy: LlmCallPolicy | None,
    ) -> None:
        state.status = "running"

        async def progress(update: DeepResearchProgress) -> None:
            state.progress = DeepResearchProgressPayload(
                phase=update.phase,  # type: ignore[arg-type]
                angle_index=update.angle_index,
                angle_total=update.angle_total,
                message=update.message,
                elapsed_ms=update.elapsed_ms,
            )
            await state.emit("research_progress", state.progress.model_dump(mode="json"))

        try:
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
            report = report.model_copy(
                update={"warnings": warnings, "narrative": narrative}
            )
            state.report = report
            state.status = "complete"
            await self._persist(state, session, result, report)
            await state.emit("research_complete", report.model_dump(mode="json"))
        except asyncio.CancelledError:
            state.status = "interrupted"
            state.error = "This run was stopped before it finished, so nothing was published."
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
            # A run whose process died mid-flight. The trace says it started
            # and never finished, which is the whole truth about it.
            return DeepResearchRunResponse(
                id=run_id,
                session_id=record.session_id,
                status="interrupted",
                created_at=record.created_at,
                population=DeepResearchSelector.model_validate(
                    payload.get("population") or {}
                ),
                data_load_label="",
                progress=DeepResearchProgressPayload(phase="execute"),
                error="This run was stopped before it finished, so nothing was published.",
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
            summaries.append(
                DeepResearchSummary(
                    id=state.id,
                    session_id=state.session_id,
                    status=state.status,  # type: ignore[arg-type]
                    created_at=state.created_at,
                    research_question=(
                        state.report.research_question if state.report else ""
                    ),
                    population=state.population,
                    data_load_label=state.data_load_label,
                    total_expected_cents=(
                        state.report.headline.total_expected_cents
                        if state.report
                        else None
                    ),
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
                report = (
                    DeepResearchReport.model_validate(stored)
                    if isinstance(stored, dict) and stored
                    else None
                )
                summaries.append(
                    DeepResearchSummary(
                        id=investigation.id,
                        session_id=session.id,
                        status="complete" if report else "interrupted",
                        created_at=investigation.created_at,
                        research_question=investigation.question or "",
                        population=(
                            report.population if report else DeepResearchSelector()
                        ),
                        data_load_label=report.data_load_label if report else "",
                        total_expected_cents=(
                            report.headline.total_expected_cents if report else None
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
        finished = state.status in ("complete", "failed", "interrupted")
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

    async def cancel(self, principal: Principal, run_id: str) -> None:
        state = self._runs.get(run_id)
        if state is None or state.tenant != principal.tenant:
            raise ReferentNotFoundError(f"no deep research run {run_id!r}")
        if state.task is not None and not state.task.done():
            state.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.task


def _sse(kind: str, data: Mapping[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n"
