"""Knowledge consultation: governed retrieval, and the wall it keeps.

Two things are under test and only one of them is retrieval quality.

The first is that consultation **selects**. A retriever that returns the
same six cards for every question has consulted nothing while looking
exactly like it consulted something, and that failure is silent — the plan
still runs, the report still reads well, and the judgement it claimed to
apply was never applied. So the assertions are comparative: the A/R question
must reach the A/R card and the payer-denial question must not.

The second is the wall. The pack's knowledge is quotable context and never
a source of numbers, and the renderer is where that stops being a promise:
it emits summaries, key points and cautions, and there is no path through it
for anything a planner could mistake for a measurement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from revi_api.adapters import PackSnapshotPort
from revi_investigation.application.capability_ports import KnowledgeEntry
from revi_investigation.application.deep_research.knowledge import (
    MAX_CONSULTED,
    as_prompt_context,
    consult,
)
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK = REPO_ROOT / "packs" / "base-rcm"


@pytest.fixture(scope="module")
def pack():
    return PackSnapshotPort(build_snapshot([load_layer(PACK)]))


class _EmptyPack:
    """A deployment whose definitions library carries no background notes."""

    def knowledge_entries(self) -> tuple[KnowledgeEntry, ...]:
        return ()


class TestItSelects:
    def test_the_ar_question_reaches_the_ar_card_first(self, pack) -> None:
        result = consult(
            pack,
            question="why has our A/R over 90 been climbing and what will it take to bring it down",
            metric_ids=("ar_over_90_pct", "days_in_ar"),
        )
        assert result.consulted
        assert result.entries[0].id == "benchmark.aged_ar_over_90"

    def test_the_payer_denial_question_reaches_the_payer_denial_card_first(
        self, pack
    ) -> None:
        result = consult(
            pack,
            question="which payers deny us most and why",
            concepts=("denial",),
            metric_ids=("denial_rate",),
        )
        assert result.consulted
        assert result.entries[0].id == "benchmark.denial_rate_by_payer_type"

    def test_two_different_questions_do_not_get_the_same_context(self, pack) -> None:
        """The failure this whole test file exists for. A retriever that
        returns its highest-scoring cards regardless of the question has
        consulted nothing, and nothing about the run would say so."""
        ar = consult(
            pack,
            question="why has our A/R over 90 been climbing",
            metric_ids=("ar_over_90_pct",),
        )
        denials = consult(
            pack,
            question="which payers deny us most",
            concepts=("denial",),
            metric_ids=("denial_rate",),
        )
        assert {entry.id for entry in ar.entries} != {
            entry.id for entry in denials.entries
        }
        assert ar.entries[0].id not in {entry.id for entry in denials.entries}

    def test_a_card_that_matches_nothing_is_not_consulted(self, pack) -> None:
        result = consult(pack, question="zzzz qqqq wwww")
        assert not result.consulted
        assert result.corpus_size > 0
        assert "None of the" in result.statement

    def test_it_never_pads_to_the_limit(self, pack) -> None:
        result = consult(pack, question="timely filing", limit=MAX_CONSULTED)
        assert 0 < len(result.entries) <= MAX_CONSULTED
        assert all(entry.score > 0 for entry in result.entries)


class TestItIsDeterministic:
    def test_the_same_question_consults_the_same_cards_in_the_same_order(
        self, pack
    ) -> None:
        first = consult(pack, question="denial write-offs by payer", concepts=("denial",))
        second = consult(pack, question="denial write-offs by payer", concepts=("denial",))
        assert [entry.id for entry in first.entries] == [
            entry.id for entry in second.entries
        ]

    def test_ties_break_on_the_card_id(self, pack) -> None:
        result = consult(pack, question="denials", concepts=("denial",))
        scores = [(entry.score, entry.id) for entry in result.entries]
        assert scores == sorted(scores, key=lambda pair: (-pair[0], pair[1]))

    def test_every_match_records_what_matched(self, pack) -> None:
        result = consult(
            pack, question="aged A/R over 90 days", metric_ids=("ar_over_90_pct",)
        )
        assert result.entries
        for entry in result.entries:
            assert entry.matched_on, f"{entry.id} was consulted with no stated cause"


class TestWordMatching:
    def test_plurals_are_one_term(self, pack) -> None:
        singular = consult(pack, question="denial rate benchmark")
        plural = consult(pack, question="denials rate benchmarks")
        assert {e.id for e in singular.entries} & {e.id for e in plural.entries}

    def test_a_short_word_does_not_match_inside_a_longer_one(self, pack) -> None:
        """``cob`` must not match ``cobra``. A substring retriever routes a
        COBRA question to the coordination-of-benefits cards and nobody
        finds out until the plan is wrong."""
        result = consult(pack, question="how much cobra coverage do we bill")
        assert "cob" not in {term for e in result.entries for term in e.matched_on}


class TestTheWall:
    def test_the_prompt_context_carries_prose_and_provenance_only(self, pack) -> None:
        result = consult(
            pack, question="aged A/R over 90 days", metric_ids=("ar_over_90_pct",)
        )
        rendered = as_prompt_context(result)
        for entry in result.entries:
            assert entry.title in rendered
            assert entry.summary in rendered
            assert "machine researched" in rendered

    def test_a_consultation_that_found_nothing_says_to_plan_without_it(
        self, pack
    ) -> None:
        rendered = as_prompt_context(consult(pack, question="zzzz qqqq"))
        assert "Plan from the catalogue" in rendered

    def test_the_statement_quotes_no_figure_from_any_card(self, pack) -> None:
        """The step contributes judgement about what to check. A sentence
        that led with an industry number would be the first place the line
        between context and computation blurred."""
        result = consult(
            pack, question="aged A/R over 90 days", metric_ids=("ar_over_90_pct",)
        )
        assert "%" not in result.statement
        assert "$" not in result.statement
        assert "They shaped the questions, never the figures." in result.statement

    def test_a_deployment_with_no_notes_says_so_rather_than_inventing_any(self) -> None:
        result = consult(_EmptyPack(), question="anything at all")
        assert result.corpus_size == 0
        assert not result.consulted
        assert "no background notes" in result.statement
