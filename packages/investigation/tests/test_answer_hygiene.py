"""First-five-minutes answer hygiene: four defects from an opening sitting.

"What is my denial rate?" led with a PROVISIONAL July figure and paired it
with "sits below the benchmark range of 19-20 percent" — a favourable
verdict on a provisional number, in a clause that omits the benchmark's own
cohort caution. "Export this" produced a paraphrase of the same finding and
no file, download or sentence saying where export lives. The hero chip "Will
my cash increase next month?" never declined the forecast and handed over a
cash-posted total instead (closed in ``planning.ANSWERING_TRANSFORMS`` — see
``test_playbook_integrity.py``). And clarifications collided: a new question
read as an answer to the pending one, and an option that is itself a
question.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from revi_investigation.application.submit_turn import (
    SubmitTurnRequest,
    TurnOutcome,
    _drop_interrogative_options,
    _export_request_refusal,
    _period_phrase,
)
from revi_investigation.application.window_maturity import WindowMaturityService
from revi_investigation.domain.turns import ClarificationBinding, ClarificationRequest
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DateBasisRef, MetricRef
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort, WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

if TYPE_CHECKING:
    from conftest import SeedPriorTurn

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


class TestTheSettledReading:
    """(a) Lead with the figure that has settled, not the one that has not."""

    def _frame(self, num: int, den: int) -> EvidenceFrame:
        """What a repository actually returns for a ratio: its ANATOMY.

        The ratio itself is the kernel's arithmetic, which is why reading a
        ``denial_rate`` column found nothing on every ratio in the pack.
        """
        columns = (
            FrameColumn("denial_rate__num", MetricRef("denial_rate"), 2, "count"),
            FrameColumn("denial_rate__den", MetricRef("denial_rate"), 2, "count"),
        )
        return EvidenceFrame(
            schema=FrameSchema(columns),
            rows=((num, den),),
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="settled", probe_hash="p" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )

    def _service(self, pack_port: PackSnapshotPort) -> WindowMaturityService:
        return WindowMaturityService(None, pack_port)  # type: ignore[arg-type]

    def _june(self) -> TimeWindow:
        return TimeWindow(
            basis=DateBasisRef("service"),
            range=AbsoluteRange(start=date(2026, 6, 1), end=date(2026, 6, 30)),
        )

    def test_a_ratio_is_composed_from_its_numerator_and_denominator(
        self, pack_port: PackSnapshotPort
    ) -> None:
        reading = self._service(pack_port)._reading_from(
            self._frame(523, 5_723), "denial_rate", self._june(), 11
        )

        assert reading is not None
        assert reading.unit == "ratio"
        assert round(Decimal(reading.value), 4) == round(Decimal(523) / Decimal(5723), 4)

    def test_a_small_cell_is_withheld_from_a_caveat_too(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The §15 policy does not stop at findings: a settled reading over
        nine claims is the small cell the rest of the engine withholds."""
        assert (
            self._service(pack_port)._reading_from(
                self._frame(3, 9), "denial_rate", self._june(), 11
            )
            is None
        )

    def test_a_zero_denominator_publishes_nothing(
        self, pack_port: PackSnapshotPort
    ) -> None:
        assert (
            self._service(pack_port)._reading_from(
                self._frame(0, 0), "denial_rate", self._june(), 11
            )
            is None
        )

    def test_the_period_is_named_the_way_a_reader_says_it(self) -> None:
        assert _period_phrase(AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))) == "June 2026"

    def test_a_period_that_is_not_a_month_is_never_rounded_to_one(self) -> None:
        """The playbook window 2026-06-08..2026-08-02 read as "June" would
        be the misattribution every warning here exists to avoid."""
        phrase = _period_phrase(AbsoluteRange(date(2026, 6, 8), date(2026, 8, 2)))
        assert phrase == "2026-06-08..2026-08-02"


class TestTheExportDeadEnd:
    """(b) Refuse it by name and point at the control, or do it."""

    @pytest.mark.parametrize(
        "utterance",
        [
            "Export this",
            "export this to a csv",
            "Can you download this for me",
            "send me this",
            "give me a spreadsheet",
        ],
    )
    def test_an_export_asked_for_in_words_is_refused_by_name(self, utterance: str) -> None:
        refusal = _export_request_refusal(utterance)

        assert refusal is not None
        assert refusal.startswith("refinement_not_applied:")
        assert "Copy answer" in refusal and "CSV" in refusal
        assert "nothing was exported by this turn" in refusal

    def test_an_ordinary_presentation_request_is_untouched(self) -> None:
        assert _export_request_refusal("sort them by percent change, largest first") is None


class TestAnOptionIsSomethingYouCanSay:
    """(d) An option phrased as a question resolves nothing."""

    def _clarification(self, *options: str) -> ClarificationRequest:
        return ClarificationRequest(
            question="Which metric did you mean?",
            options=options,
            reason="model requested clarification",
            bindings=tuple(
                ClarificationBinding(option=option, kind="metric_cut", metric_ids=("denial_rate",))
                for option in options
            ),
        )

    def test_the_platform_s_own_question_is_not_an_option(self) -> None:
        """Live, on "Why did it go up?": option 3 read "Which metric are you
        asking about? — I mean the last figure you charted." """
        cleaned = _drop_interrogative_options(
            self._clarification(
                "Denial rate",
                "Which metric are you asking about? — I mean the last figure you charted.",
            )
        )

        assert cleaned.options == ("Denial rate",)
        assert {b.option for b in cleaned.bindings} == {"Denial rate"}

    def test_an_optionless_card_beats_a_row_of_questions(self) -> None:
        cleaned = _drop_interrogative_options(
            self._clarification("What did you mean?", "Which one?")
        )

        assert cleaned.options == ()
        assert cleaned.bindings == ()
        assert "itself a question" in (cleaned.reason or "")

    def test_a_statement_that_merely_contains_a_question_mark_survives(self) -> None:
        cleaned = _drop_interrogative_options(
            self._clarification("Denial rate, excluding Medicare?", "Denied dollars")
        )

        assert len(cleaned.options) == 2


# --------------------------------------------------------------------- (d)
# The maze, end to end. These drive the real engine against the generated
# warehouse, because what is being tested is a ROUTING decision: which
# branch a turn takes when a question of ours is already on screen.

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

_PENDING_CLARIFICATION = {
    "turn_class": "new_investigation",
    "confidence": 0.2,
    "clarification_question": "Ranked by what — denial rate, or days in A/R?",
    "clarification_options": ["Highest denial rate", "Most days in A/R"],
}

_INTERPRETATION = {
    "intent_summary": "A/R aging",
    "metric_ids": ["denial_rate"],
    "dimension_ids": [],
    "concept_ids": [],
    "playbook_id": None,
    "window": {"quantity": "1", "unit": "month", "mode": "full_periods"},
    "basis": None,
    "comparison": None,
    "scope": [],
    "clarification": None,
    "definitional_terms": [],
}


@pytest.mark.reference
@pytest.mark.skipif(not WAREHOUSE.is_file(), reason="generated warehouse missing")
class TestOnePendingQuestionAtATime:
    """A new question supersedes the pending one and says so.

    Live: "Who is my worst payer?" clarified; the director asked their next
    real question, "Show me AR aging", and the platform replied "Is this
    answering the 'worst payer' question by picking the days-in-A/R
    measure, or a new A/R aging request?" — asking the reader to adjudicate
    the relationship between their own two sentences, with "Drop the worst
    payer question entirely" as an option.
    """

    @pytest.fixture
    def engine(self) -> WiredEngine:
        return build_duckdb_engine(warehouse_path=WAREHOUSE, llm=MockLanguageModel())

    async def _turn(self, engine: WiredEngine, session_id: str | None, question: str) -> TurnOutcome:
        return await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=question, session_id=session_id)
        )

    async def test_a_new_question_replaces_the_pending_one(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        engine.llm.respond("classify_turn", _PENDING_CLARIFICATION)
        engine.llm.respond("interpret_question", _INTERPRETATION)
        session_id: str = await seed_prior_turn(engine)

        first = await self._turn(engine, session_id, "Who is my worst payer?")
        assert first.clarification is not None, "the fixture's own premise"

        second = await self._turn(engine, first.session.id, "Show me AR aging")

        assert second.clarification is None, (
            "a second question about which question we are answering is the maze"
        )
        assert any("replaces the one I had open" in w for w in second.warnings)

    async def test_a_real_answer_is_still_read_as_an_answer(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """Superseding a genuine reply would throw away the answer this
        platform asked for — the opposite defect, one turn later."""
        engine.llm.respond("classify_turn", _PENDING_CLARIFICATION)
        engine.llm.respond("interpret_question", _INTERPRETATION)
        session_id: str = await seed_prior_turn(engine)

        first = await self._turn(engine, session_id, "Who is my worst payer?")
        second = await self._turn(engine, first.session.id, "Highest denial rate")

        assert not any("replaces the one I had open" in w for w in second.warnings)
