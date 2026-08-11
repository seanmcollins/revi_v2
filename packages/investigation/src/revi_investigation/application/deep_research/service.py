"""Orchestration: plan, execute, hand back a report ready for prose.

The phases are separated because they fail differently. A plan that cannot
be chosen falls back to the standing set and the run continues. An angle
that cannot be run honestly is refused and named, and the run continues. A
read that cannot be made honestly stops the run — there is nothing to
continue with, and a partial estimate is the one output this mode must
never produce.

Cancellation is a first-class outcome. Angles run one at a time with a
yield between them, so a cancelled run stops at an angle boundary rather
than mid-estimate, and nothing partial is persisted: whoever is driving
this service keeps the progress trace and throws the rest away.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from revi_investigation.application.deep_research.angles import AngleResult, run_angle
from revi_investigation.application.deep_research.grammar import (
    DeepResearchPlan,
    TargetPopulation,
    plan_fingerprint,
    standing_plan,
)
from revi_investigation.application.deep_research.policy import DeepResearchSettings
from revi_investigation.application.deep_research.report import ReportDraft, build_report
from revi_investigation.application.deep_research.rows import (
    DeepResearchReadRefused,
    DenialRows,
    DenialRowSource,
)
from revi_investigation.application.ports import DEFAULT_LLM_CALL_POLICY, LlmCallPolicy
from revi_kernel.watermark import DataWatermark


@dataclass(frozen=True, slots=True)
class DeepResearchProgress:
    """Where a run has got to, as a reader would say it."""

    phase: str
    angle_index: int = 0
    angle_total: int = 0
    message: str = ""
    elapsed_ms: int = 0


ProgressSink = Callable[[DeepResearchProgress], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DeepResearchResult:
    """A finished run, before prose."""

    draft: ReportDraft
    plan: DeepResearchPlan
    rows: DenialRows
    results: tuple[AngleResult, ...]
    fingerprint: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class DeepResearchPreview:
    """A run that has not started: what it would look at, and over how much."""

    plan: DeepResearchPlan
    open_denials: int
    open_dollars_cents: int
    #: The data edge every figure above was read at.
    as_of: date


class DeepResearchPlanner(Protocol):
    """What the control plane must provide. Implemented by the model path."""

    async def plan(
        self,
        *,
        question: str,
        population: TargetPopulation,
        settings: DeepResearchSettings,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> DeepResearchPlan: ...


class DeepResearchService:
    """One run, end to end, up to the point prose is written."""

    def __init__(
        self,
        rows: DenialRowSource,
        *,
        planner: DeepResearchPlanner | None = None,
    ) -> None:
        self._rows = rows
        self._planner = planner

    async def preview(
        self,
        *,
        question: str | None,
        population: TargetPopulation,
        settings: DeepResearchSettings,
        watermark: DataWatermark,
        pack_snapshot_id: str,
    ) -> DeepResearchPreview:
        """What a run would do, resolved without doing any of it.

        A run is a minute of work and a real model call, so the surface
        offering one confirms intent first — and a confirmation is only
        worth reading if it says what will actually be looked at, over how
        much. Both come from facts, not from prose: the angles are the
        standing plan (the same set a run falls back to, and the one this
        resolves without spending a model call on a popover), and the size
        is read through the run's own cached read, so a preview followed by
        a run costs one read rather than two.
        """
        rows = await self._rows.fetch(
            population=population,
            settings=settings,
            watermark=watermark,
            pack_snapshot_id=pack_snapshot_id,
        )
        open_rows = rows.open_rows
        return DeepResearchPreview(
            plan=standing_plan(question),
            open_denials=len(open_rows),
            open_dollars_cents=sum(row.denied_amount_cents for row in open_rows),
            as_of=rows.as_of,
        )

    async def run(
        self,
        *,
        run_id: str,
        question: str | None,
        population: TargetPopulation,
        settings: DeepResearchSettings,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        progress: ProgressSink | None = None,
        llm_policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
        created_at: datetime | None = None,
    ) -> DeepResearchResult:
        started_at = created_at or datetime.now(UTC)
        clock = time.monotonic()

        async def say(
            phase: str, message: str, index: int = 0, total: int = 0
        ) -> None:
            if progress is None:
                return
            await progress(
                DeepResearchProgress(
                    phase=phase,
                    angle_index=index,
                    angle_total=total,
                    message=message,
                    elapsed_ms=int((time.monotonic() - clock) * 1000),
                )
            )

        # -- PLAN ------------------------------------------------------------
        await say("plan", "Choosing what to look at")
        plan = standing_plan(question)
        if self._planner is not None:
            try:
                plan = await self._planner.plan(
                    question=question or plan.research_question,
                    population=population,
                    settings=settings,
                    policy=llm_policy,
                )
            except Exception:
                plan = standing_plan(question)

        # -- EXECUTE ---------------------------------------------------------
        estimation = settings.estimation_policy()
        total = len(plan.angles)
        await say("execute", "Reading your denial history", 0, total)
        rows = await self._rows.fetch(
            population=population,
            settings=settings,
            watermark=watermark,
            pack_snapshot_id=pack_snapshot_id,
        )
        if not rows.rows:
            raise DeepResearchReadRefused(
                "no denials in this population have any resubmission history yet"
            )

        results: list[AngleResult] = []
        for index, angle in enumerate(plan.angles, start=1):
            await say(
                "execute",
                settings.angle(str(angle.family)).progress,
                index,
                total,
            )
            results.append(
                run_angle(angle, rows, settings=settings, policy=estimation)
            )
            # A yield between angles, so cancellation lands on a boundary
            # and a long run never starves the event loop it streams on.
            await asyncio.sleep(0)

        # -- SYNTHESIZE (structure; prose is composed above this layer) ------
        await say("synthesize", "Writing it up", total, total)
        completed_at = datetime.now(UTC)
        duration_ms = int((time.monotonic() - clock) * 1000)
        draft = build_report(
            run_id=run_id,
            plan=plan,
            population=population,
            results=results,
            rows=rows,
            settings=settings,
            created_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        return DeepResearchResult(
            draft=draft,
            plan=plan,
            rows=rows,
            results=tuple(results),
            fingerprint=plan_fingerprint(plan, population),
            duration_ms=duration_ms,
        )
