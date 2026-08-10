""""Right now" on a metric that only exists over a period (round-8 FIX-6).

The demo-room opener, live and verbatim: *"Who is my worst payer on denial
rate right now, and is that a change from last month?"* → ``outcome:
answer``, ``findings: []``, narrative "This turn published no finding…",
``context_header.display: "2026-08-01..2026-08-02 (service) · vs
2026-07-01..2026-07-02"``. "Right now" had resolved to the two days since
the month boundary and "last month" to a two-day slice of July, with no
window warning saying so. The identical question with the months named
returns three findings and good prose.

Two rules close it, both here in interpretation and both deterministic:

* a "now" phrase on a periodic metric anchors to the last FULL period —
  never to the days since a boundary — and says so in the ``window_assumed``
  shape the product already uses for a period nobody named;
* a baseline the utterance NAMES ("from last month") is honored as the
  comparison, and is not mistaken for the window.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.interpretation import InterpretQuestionService
from revi_investigation.application.ports import (
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import Session
from revi_kernel.watermark import DataWatermark, WatermarkEpoch
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import make_usage

# The reference load: it ran at 04:10 on 2026-08-03 over data through the
# 2nd — so the last FULL month is July 2026 and "right now", taken
# literally, is two days of an August that has barely started.
WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
SESSION = Session(
    id="sess-1",
    tenant="demo",
    pack_version=PackVersionRef("base-rcm", "1.0.0"),
    epochs=(WatermarkEpoch(index=0, watermark=WATERMARK),),
    created_at=datetime(2026, 8, 3, 4, 20),
)

JULY = (date(2026, 7, 1), date(2026, 7, 31))
JUNE = (date(2026, 6, 1), date(2026, 6, 30))

#: What the model actually returned for the opener: a month-to-date window
#: and a prior-period comparison, which derives a same-length slice.
MONTH_TO_DATE = {"quantity": "1", "unit": "month", "mode": "to_date"}


@dataclass
class _FixedLlm:
    output: dict[str, Any]

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        return StructuredLlmResult(output=self.output, usage=make_usage())

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        raise AssertionError("these tests never stream")

    async def last_usage(self) -> LlmUsage | None:
        return None


def _response(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent_summary": "test",
        "metric_ids": [],
        "dimension_ids": [],
        "concept_ids": [],
        "playbook_id": None,
        "window": None,
        "basis": None,
        "comparison": None,
        "scope": [],
        "direction": None,
        "magnitude": None,
        "clarification": None,
        "clarification_options": [],
        "definitional_terms": [],
    }
    base.update(overrides)
    return base


async def _interpret(
    pack_port: PackSnapshotPort, catalog: CatalogSnapshot, question: str, **overrides: Any
) -> Any:
    service = InterpretQuestionService(_FixedLlm(_response(**overrides)), pack_port, catalog)
    outcome = await service.interpret(question, session=SESSION, turn_id="t1")
    assert outcome.investigation is not None, outcome.clarification
    return outcome.investigation


def _window(interpreted: Any) -> tuple[date, date]:
    return (
        interpreted.spec.context.window.range.start,
        interpreted.spec.context.window.range.end,
    )


def _comparison(interpreted: Any) -> tuple[date, date] | None:
    comparison = interpreted.spec.context.comparison
    if comparison is None:
        return None
    return comparison.window.range.start, comparison.window.range.end


class TestTheDemoOpener:
    OPENER = "Who is my worst payer on denial rate right now, and is that a change from last month?"

    async def test_right_now_anchors_to_the_last_full_period(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """Two days of an open August is not a month, and ranking payers
        over it returns nothing at all."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            self.OPENER,
            metric_ids=["denial_rate"],
            dimension_ids=["payer"],
            window=MONTH_TO_DATE,
            comparison="prior_period",
        )

        assert _window(interpreted) == JULY

    async def test_the_comparison_is_the_whole_named_period(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """"From last month" is June in full — not 2026-07-01..2026-07-02,
        which is what a same-length prior period of a two-day window is."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            self.OPENER,
            metric_ids=["denial_rate"],
            dimension_ids=["payer"],
            window=MONTH_TO_DATE,
            comparison="prior_period",
        )

        assert _comparison(interpreted) == JUNE

    async def test_the_assumption_is_stated_in_the_analyst_s_own_words(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            self.OPENER,
            metric_ids=["denial_rate"],
            dimension_ids=["payer"],
            window=MONTH_TO_DATE,
            comparison="prior_period",
        )

        note = next(n for n in interpreted.notes if n.startswith("window_assumed"))
        assert '"right now"' in note
        assert "2026-07-01..2026-07-31" in note
        # …and what it would have been, so the reader can tell this rule
        # fired rather than wonder where July came from.
        assert "2026-08-01..2026-08-02" in note

    async def test_a_named_baseline_is_honored_when_the_model_drops_it(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """The utterance asks for a change. A level with no comparison
        answers the half of the question nobody cares about."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            self.OPENER,
            metric_ids=["denial_rate"],
            dimension_ids=["payer"],
        )

        assert _window(interpreted) == JULY
        assert _comparison(interpreted) == JUNE
        assert any(n.startswith("comparison_assumed") for n in interpreted.notes)

    async def test_last_month_in_a_baseline_clause_is_not_the_window(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """The relative vocabulary matches "last month" wherever it sits;
        read as the window it makes the answer a level over the baseline."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            self.OPENER,
            metric_ids=["denial_rate"],
            dimension_ids=["payer"],
        )

        assert not any(n.startswith("window_relative") for n in interpreted.notes)


class TestSiblingPhrasings:
    @pytest.mark.parametrize(
        "question",
        [
            "What is our denial rate at the moment?",
            "What is our denial rate currently?",
            "What is our denial rate today?",
            "What is our denial rate as we speak?",
            "What is our denial rate right now?",
        ],
    )
    async def test_every_now_phrase_anchors_the_same_way(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, question: str
    ) -> None:
        interpreted = await _interpret(
            pack_port, catalog, question, metric_ids=["denial_rate"], window=MONTH_TO_DATE
        )

        assert _window(interpreted) == JULY
        assert any(n.startswith("window_assumed") for n in interpreted.notes)

    async def test_today_is_anchored_rather_than_read_as_one_day(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """"Today" is in the relative vocabulary as a one-day to-date
        window. On a monthly metric that is the same defect with a shorter
        window — and this load's "today" holds no data at all."""
        interpreted = await _interpret(
            pack_port, catalog, "What is our denial rate today?", metric_ids=["denial_rate"]
        )

        assert _window(interpreted) == JULY


class TestWhatTheRuleMustNotTouch:
    async def test_a_snapshot_metric_still_means_now(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """An as-of balance IS a reading at an instant: rounding it back to
        a period would be the opposite error, and ``snapshot_as_of`` already
        says the window does not scope it."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            "Which payers have the most A/R over 90 days right now?",
            metric_ids=["ar_over_90_pct"],
            dimension_ids=["payer"],
            window=MONTH_TO_DATE,
        )

        assert _window(interpreted) == (date(2026, 8, 1), date(2026, 8, 2))
        assert any(n.startswith("snapshot_as_of") for n in interpreted.notes)

    async def test_a_period_the_analyst_named_wins(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """"Month to date" is a period somebody asked for on purpose. The
        rule is for utterances whose only time word is a "now"."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            "What is our denial rate month to date, currently?",
            metric_ids=["denial_rate"],
        )

        assert _window(interpreted) == (date(2026, 8, 1), date(2026, 8, 2))

    async def test_a_trailing_span_is_not_a_period_boundary(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """A trailing 90 days also ends inside the open month; it is a span
        the analyst asked for, not a boundary they fell over."""
        interpreted = await _interpret(
            pack_port,
            catalog,
            "What is our denial rate over the last 90 days right now?",
            metric_ids=["denial_rate"],
            window={"quantity": "90", "unit": "day", "mode": "trailing"},
        )

        assert _window(interpreted)[1] == date(2026, 8, 2)
        assert _window(interpreted)[0] == date(2026, 5, 5)

    async def test_a_named_month_is_never_re_anchored(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        interpreted = await _interpret(
            pack_port,
            catalog,
            "What was our denial rate in May 2026, and where does it stand right now?",
            metric_ids=["denial_rate"],
            window={"unit": "month", "year": 2026, "index": 5},
        )

        assert _window(interpreted) == (date(2026, 5, 1), date(2026, 5, 31))
