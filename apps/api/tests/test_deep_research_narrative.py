"""The determination's own write-up seam, with the ranges on the wire.

``compose_determination`` is the only place a research study's prose is
made. What is pinned here is that the study's READINGS reach the fact set
the prose is validated against — not just the findings' digits. Without
them the validator can confirm that 47.2% was measured and cannot confirm
that six months of overlapping intervals are "getting worse", which is how
that sentence was published.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest

from revi_api.deep_research_narrative import compose_determination
from revi_investigation.application.deep_research.general_report import (
    GeneralizedReportDraft,
)
from revi_investigation.application.ports import (
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_investigation_contracts.api import FindingPayload, FindingValue
from revi_investigation_contracts.deep_research import (
    DeterminationPayload,
    GeneralizedResearchReport,
    IntervalPayload,
    ResearchFigurePayload,
    ResearchReadingPayload,
    ResearchWalkPayload,
)
from revi_investigation_contracts.deep_research_offer import DeepResearchSelector
from revi_investigation_contracts.header import ContextHeaderPayload

#: The published series, and the intervals that were in the same payload.
MONTHS = (
    ("Aug 2025", "0.472", "47.2%", "0.320", "0.630"),
    ("Sep 2025", "0.407", "40.7%", "0.245", "0.593"),
    ("Oct 2025", "0.480", "48.0%", "0.300", "0.665"),
    ("Nov 2025", "0.367", "36.7%", "0.219", "0.545"),
    ("Dec 2025", "0.432", "43.2%", "0.297", "0.578"),
    ("Jan 2026", "0.452", "45.2%", "0.292", "0.622"),
)

#: The determination as it was written, with a referent citation so the
#: sentence reaches the rule this file is about.
WRITTEN = (
    "Only Atlas Commercial has a readable direction, and it is getting worse on appeals "
    "(F1): 47.2% Aug, 40.7% Sep, 48.0% Oct, 36.7% Nov, 43.2% Dec, 45.2% Jan 2026, ending "
    "below where it started."
)


class ScriptedLlm:
    """One composer, one paragraph — the paragraph the review found."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def structured(
        self, request: StructuredLlmRequest
    ) -> StructuredLlmResult:  # pragma: no cover - unused on this seam
        raise NotImplementedError

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        async def stream() -> AsyncIterator[str]:
            yield self._text

        return stream()

    async def last_usage(self) -> LlmUsage | None:
        return None


def _reading() -> ResearchReadingPayload:
    return ResearchReadingPayload(
        id="R1",
        shape="trend",
        title="Appeal overturn rate by month within Atlas Commercial",
        measure_label="Appeal overturn rate",
        metric_id="appeal_overturn_rate",
        unit="ratio",
        reason="Atlas Commercial carried the largest appealed balance.",
        window_label="Aug 2025..Jan 2026",
        figures=[
            ResearchFigurePayload(
                label=label,
                evidence="measured",
                value=value,
                display=display,
                population=30,
                interval=IntervalPayload(low=low, high=high, confidence="0.95"),
            )
            for label, value, display, low, high in MONTHS
        ],
    )


def _draft() -> GeneralizedReportDraft:
    report = GeneralizedResearchReport(
        id="dr_atlas",
        research_question="How is Atlas Commercial behaving on appeals?",
        population=DeepResearchSelector(label="Atlas Commercial"),
        data_edge_date=date(2026, 1, 31),
        created_at=datetime(2026, 2, 2, tzinfo=UTC),
        determination=DeterminationPayload(question="How is Atlas Commercial behaving?"),
        walk=ResearchWalkPayload(),
        readings=[_reading()],
        findings=[
            FindingPayload(
                referent="F1",
                title="Atlas Commercial appeal overturn rate by month",
                statement="Atlas Commercial appeal overturn rate by month.",
                values=[
                    FindingValue(name=label, value=float(value))
                    for label, value, _display, _low, _high in MONTHS
                ],
                grade="direct",
            )
        ],
    )
    return GeneralizedReportDraft(
        report=report,
        warnings=(),
        header=ContextHeaderPayload(
            window_start=date(2025, 8, 1),
            window_end=date(2026, 1, 31),
            basis="post",
            watermark_id="wm_014",
            display="2025-08-01..2026-01-31 (post) · watermark wm_014",
        ),
    )


class TestTheRangesReachTheValidator:
    @pytest.mark.asyncio
    async def test_a_direction_over_overlapping_ranges_is_corrected_on_the_wire(
        self,
    ) -> None:
        text, composed, redactions = await compose_determination(
            llm=ScriptedLlm(WRITTEN), draft=_draft(), warnings=[]
        )

        assert composed
        assert "getting worse" not in text
        assert "ending below where it started" not in text
        # The reader keeps both figures and gains what the ranges support.
        assert "47.2%" in text and "45.2%" in text
        assert "noise-compatible" in text
        assert redactions == (WRITTEN,)

    @pytest.mark.asyncio
    async def test_a_claim_the_ranges_support_is_published_untouched(self) -> None:
        stated = (
            "Atlas Commercial appeal overturn reads 47.2% in Aug 2025 and 45.2% in "
            "Jan 2026 (F1)."
        )
        text, composed, redactions = await compose_determination(
            llm=ScriptedLlm(stated), draft=_draft(), warnings=[]
        )

        assert composed
        assert redactions == ()
        assert stated in text
