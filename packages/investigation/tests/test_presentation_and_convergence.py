"""Four dialogue defects, as invariants: the engine losing what the analyst
was looking at.

A re-presentation request ("sort them by percent change, largest first")
classified below threshold and ended as a clarification asking whether
percent change was already a column; answering that clarification with its
own first option re-planned the sentence as a FIRST TURN, collapsing twelve
published rows to three; the value-existence refusal replayed
byte-identically when answered with anything but a verbatim value; and a
reply naming the platform's own referent handle ("Yes, F1 — Summit Peak
Medicare Advantage") reached the value-existence guard as a facility name.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from revi_investigation.application.interpretation import (
    PendingClarification,
    benchmark_comparison_request,
    presentation_order_request,
)
from revi_investigation.application.ports import RegisteredReferent
from revi_investigation.application.submit_turn import (
    _chart_sorts_for,
    _reordered,
    claim_referent_predicates,
    options_named,
    presentation_ordering,
)
from revi_investigation.domain.records import Finding
from revi_kernel.filters import Predicate, PredicateOp, and_merge, iter_predicates
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef, ReferentId, ReferentKind
from revi_kernel.watermark import DataWatermark


def _finding(referent: str, title: str, pct: str) -> Finding:
    return Finding(
        referent=ReferentId(value=referent, kind=ReferentKind.FINDING),
        title=title,
        statement=f"{title}.",
        metric_refs=(MetricRef("denial_rate"),),
        values=(("denial_rate", Decimal("0.1")), ("pct_change", Decimal(pct))),
        grade=EvidenceGrade.DIRECT,
        impact_cents=None,
        confidence="qualified",
    )


TWELVE = (
    _finding("F4", "Veritas Comp Fund denial rate at most", "7.538473"),
    _finding("F5", "State Medicaid MCO denial rate up 22.0 points", "2.925894"),
    _finding("F6", "Pinnacle Health Plan denial rate up 11.2 points", "0.958341"),
    _finding("F7", "Bluestone Mutual denial rate up 7.0 points", "0.748842"),
)


class TestThePresentationOpIsRecognisedWithoutAModel:
    """The recognizer is closed-vocabulary, like ``display_scope_limit``:
    an ordering verb plus words that say nothing about WHAT to measure."""

    @pytest.mark.parametrize(
        "utterance",
        [
            "sort them by percent change, largest first",
            "Just re-sort the rows already shown by their percent change column, descending",
            "order them by dollars",
            "reverse that order",
        ],
    )
    def test_an_ordering_over_rows_on_screen_is_recognised(self, utterance: str) -> None:
        assert presentation_order_request(utterance) is True

    @pytest.mark.parametrize(
        "utterance",
        [
            # names a metric and a cut: a real investigation, not a re-order
            "rank our providers by denial rate, worst first",
            # names a value: a filter, and filters change the population
            "exclude the Medicaid payers",
            # names a period
            "sort by date",
            # no ordering verb at all
            "show me all twelve",
        ],
    )
    def test_anything_naming_new_content_falls_through_to_the_model(
        self, utterance: str
    ) -> None:
        assert presentation_order_request(utterance) is False


class TestTheOrderingIsAppliedToTheServedRows:
    """The rows the analyst is looking at, in the order asked for."""

    def test_percent_change_resolves_to_the_column_the_rows_carry(self) -> None:
        assert presentation_ordering(
            "sort them by percent change, largest first", TWELVE
        ) == ("pct_change", True)

    def test_the_direction_word_is_read(self) -> None:
        assert presentation_ordering("sort them by percent change, smallest first", TWELVE) == (
            "pct_change",
            False,
        )

    def test_alphabetically_orders_by_the_row_label(self) -> None:
        assert presentation_ordering("sort them alphabetically", TWELVE) == ("", False)

    def test_a_column_the_rows_do_not_carry_resolves_to_nothing(self) -> None:
        """…and the caller then owes ``refinement_not_applied``, which is
        what that sentence is for."""
        assert presentation_ordering("sort them by name of the thing", ()) is None
        assert presentation_ordering("sort them", TWELVE) is None

    def test_ambiguity_resolves_to_nothing_rather_than_to_a_guess(self) -> None:
        """"change" alone names both ``pct_change`` and ``delta_cents``; the
        engine says it did not apply the request rather than picking one."""
        rows = tuple(
            replace(f, values=(*f.values, ("delta_cents", 100), ("current_cents", 100)))
            for f in TWELVE
        )
        assert presentation_ordering("sort them by change", rows) is None

    def test_the_rows_come_back_in_that_order_and_all_of_them_do(self) -> None:
        served = _reordered(TWELVE, "pct_change", descending=True)
        assert [f.referent.value for f in served] == ["F4", "F5", "F6", "F7"]
        assert len(served) == len(TWELVE), "a re-order publishes every row it was given"
        assert [f.referent.value for f in _reordered(TWELVE, "pct_change", False)] == [
            "F7",
            "F6",
            "F5",
            "F4",
        ]


def _frame(*names: str) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            columns=tuple(
                FrameColumn(name=n, ref=MetricRef(n.split("__")[0]), unit="ratio")
                for n in names
            )
        ),
        rows=(),
        watermark=DataWatermark(id="wm", loaded_at=None, newest_data_date=None),  # type: ignore[arg-type]
        provenance=ProbeProvenance(probe_id="p", probe_hash="h"),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestTheChartFollowsTheRowsItDrawsFor:
    def test_the_published_measure_wins_over_its_own_numerator(self) -> None:
        """Kept honest: a ratio frame carries BOTH
        ``denial_rate__pct_change`` and ``denial_rate__num__pct_change`` —
        the second is the numerator's movement, a different number, and it
        sorts first alphabetically."""
        frame = _frame(
            "denial_rate", "denial_rate__num__pct_change", "denial_rate__pct_change"
        )
        assert _chart_sorts_for((("chart_main", frame),), "pct_change", True) == (
            ("chart_main", "denial_rate__pct_change", True),
        )

    def test_a_frame_without_the_column_publishes_no_sort(self) -> None:
        assert _chart_sorts_for((("chart_main", _frame("denial_rate")),), "pct_change", True) == ()


class TestTheValueRefusalNeverReplaysItself:
    """The twelve-payer refusal, asked twice, byte for byte."""

    OPTIONS = (
        "Atlas Commercial",
        "Bluestone Mutual",
        "Federal Medicare",
        "Northbridge Commercial",
        "Summit Peak Medicare Advantage",
    )

    def test_a_reply_that_narrows_the_set_is_read_as_narrowing_it(self) -> None:
        assert options_named("the two biggest commercial ones", self.OPTIONS) == (
            "Atlas Commercial",
            "Northbridge Commercial",
        )

    def test_counting_and_superlative_words_name_no_value(self) -> None:
        """"the two biggest ones" describes a choice without making one, and
        the engine must not invent one from it."""
        assert options_named("the two biggest ones", self.OPTIONS) == ()

    def test_a_reply_naming_one_value_names_exactly_it(self) -> None:
        assert options_named("federal medicare please", self.OPTIONS) == ("Federal Medicare",)


class TestAHandleIsNeverADimensionValue:
    """``F1`` is an identifier this platform minted, and the §6.6
    value-existence guard has no business refusing it as a facility."""

    def _entries(self) -> tuple[RegisteredReferent, ...]:
        return (
            RegisteredReferent(
                referent=ReferentId(value="F1", kind=ReferentKind.FINDING),
                session_id="sess",
                investigation_id="inv",
                label="Summit Peak Medicare Advantage: $176,112.25 denied dollars",
                dimension_value=("payer", "Summit Peak Medicare Advantage"),
            ),
        )

    def test_the_handle_is_rewritten_to_what_the_registry_says_it_stood_for(
        self, make_spec: object
    ) -> None:
        spec = make_spec(measures=("denied_dollars",))  # type: ignore[operator]
        spec = spec.with_context(
            replace(
                spec.context,
                scope=Predicate(
                    dimension=DimensionRef("facility"), op=PredicateOp.EQ, values=("F1",)
                ),
            )
        )

        claimed, notes = claim_referent_predicates(spec, self._entries())

        predicates = list(iter_predicates(claimed.context.scope))
        assert [(p.dimension.id, p.values) for p in predicates] == [
            ("payer", ("Summit Peak Medicare Advantage",))
        ]
        assert notes and notes[0].startswith("referent_claimed: F1")
        assert "facility" in notes[0], "the substitution names what it replaced"

    def test_a_scope_naming_no_handle_is_left_exactly_alone(
        self, make_spec: object
    ) -> None:
        spec = make_spec(measures=("denied_dollars",))  # type: ignore[operator]
        spec = spec.with_context(
            replace(
                spec.context,
                scope=and_merge(
                    Predicate(
                        dimension=DimensionRef("payer"),
                        op=PredicateOp.EQ,
                        values=("Atlas Commercial",),
                    )
                ),
            )
        )

        claimed, notes = claim_referent_predicates(spec, self._entries())

        assert notes == []
        assert claimed is spec


class TestPendingClarificationIsUnchanged:
    """A guard on the fixture the funnel keys off: a repeat is decided on
    the QUESTION and the OPTIONS, so both have to survive the round trip."""

    def test_a_pending_clarification_carries_what_a_repeat_is_compared_on(self) -> None:
        pending = PendingClarification(
            question="Which did you mean?", options=("a", "b"), streak=1
        )
        assert pending.question and pending.options


class TestABenchmarkComparisonIsRecognisedWithoutAModel:
    """"Compare that to the industry benchmark" is a statement about ranges
    this platform has already harvested for the answer on screen.

    Benchmark attachment is fully structural — the guard walks the published
    findings' metrics and takes what the definitions library holds for them
    — so there is no analyst intent in the path and no field on the
    interpretation schema to carry one. The model had nowhere to put the
    utterance but a clarification, and three live conversations spent a turn
    being asked *which* measure to compare, in sessions that had measured
    exactly one, whose previous answer had already printed the range.
    """

    @pytest.mark.parametrize(
        "utterance",
        [
            "compare that to the industry benchmark",
            "how does that compare to the benchmark",
            "how does that compare to benchmark",
            "vs benchmark?",
            "vs the industry benchmark",
            # …and the same question asked without the word. The only
            # judgement this platform will pass on a level is the peer
            # range its definitions library publishes for that measure.
            "is that a lot?",
            "is that good?",
            "is that normal?",
        ],
    )
    def test_the_utterance_is_read_here(self, utterance: str) -> None:
        assert benchmark_comparison_request(utterance)

    @pytest.mark.parametrize(
        "utterance",
        [
            "what is the benchmark for denial rate",
            "compare the two",
            "and the dollars?",
            # Names a population: a question to be measured, not judged.
            "is that a lot for Atlas Commercial?",
        ],
    )
    def test_a_question_that_needs_measuring_is_not(self, utterance: str) -> None:
        assert not benchmark_comparison_request(utterance)
