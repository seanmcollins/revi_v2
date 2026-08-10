"""Round-9 engine fixes: the guards, predicates and formatters that
contradicted themselves on the demo's opening question.

Every case here was reproduced live against the demo tenant before it was
written down, and each one reaches an answer surface verbatim:

* **R9-03** — the product's own "drill into F1" chip on a 29.5% denial-rate
  finding published ``RECONCILIATION_FAILED … parent F1=$0.00;
  child=$31,174.49; delta=$31,174.49 (+311744900.0%)`` in a red alert.
  ``int(Decimal("0.295082"))`` is ``0``, and the immediate-parent branch of
  ``_parent_finding`` matched on the referent alone, so a ratio was read
  back as a dollar figure of nothing and differenced against a pile of
  dollars.
* **R9-06** — ``SUPPRESSION_BOUNDED`` said "4 of 12 groups", listed five
  rows, named one payer twice (the second row being the COMPARISON
  window's cell) and stated "fewer than 11 things sit behind each" over a
  row of 214 entities — beside a ``SUPPRESSION_APPLIED`` caution stating
  the real rule correctly.
* **R9-10** — ``WINDOW_ASSUMED``'s literal-reading clause reported the whole
  literal window as open: "31 day(s) of the period that is still open" over
  2026-07-03..2026-08-02, of which exactly two days (Aug 1-2) are in the
  open month.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.execution import (
    BoundedCell,
    SuppressionCensus,
    bounded_cells_warning,
)
from revi_investigation.application.interpretation import (
    InterpretQuestionService,
    _open_period_clause,
)
from revi_investigation.application.ports import (
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_investigation.application.submit_turn import (
    _ASKS_WHICH_MEASURE,
    CLARIFICATION_SOLE_SURVIVOR_REASON,
    _state_the_survivor,
    _with_resumed_context,
    containment_reconciliation,
    measure_mismatch_reason,
)
from revi_investigation.application.validation import PlanValidationService
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    InvestigationStatus,
    Session,
)
from revi_investigation.domain.refinements import DrillInto
from revi_investigation.domain.turns import (
    ClarificationBinding,
    ClarificationRequest,
    TurnClass,
)
from revi_kernel.filters import Predicate, PredicateOp, iter_predicates
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef, ReferentId, ReferentKind
from revi_kernel.scope import AbsoluteRange
from revi_kernel.watermark import DataWatermark, WatermarkEpoch
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import make_usage

WATERMARK = DataWatermark(
    id="wm_003",
    loaded_at=datetime(2026, 8, 3, 4, 10, tzinfo=UTC).replace(tzinfo=None),
    newest_data_date=date(2026, 8, 2),
)

#: The pinned pack's units, as the turn service reads them.
UNITS = {"denial_rate": "ratio", "denied_dollars": "money_cents"}

_SESSION = Session(
    id="sess-1",
    tenant="demo",
    pack_version=PackVersionRef("base-rcm", "1.0.0"),
    epochs=(WatermarkEpoch(index=0, watermark=WATERMARK),),
    created_at=datetime(2026, 8, 3, 4, 20),
)


@dataclass
class _FixedInterpretation:
    output: dict[str, Any]

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        return StructuredLlmResult(output=self.output, usage=make_usage())

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        raise AssertionError("these tests never stream")

    async def last_usage(self) -> LlmUsage | None:
        return None


def _rate_finding() -> Finding:
    """F1 exactly as the demo opener publishes it: a rate and a rank, and
    not one dollar anywhere on it."""
    return Finding(
        referent=ReferentId(value="F1", kind=ReferentKind.FINDING),
        title="State Medicaid MCO: 29.5% denial rate",
        statement="State Medicaid MCO leads on denial rate at 29.5%.",
        metric_refs=(MetricRef("denial_rate"),),
        values=(("denial_rate", Decimal("0.295082")), ("rank", 1)),
        grade=EvidenceGrade.DIRECT,
        confidence="high",
    )


def _money_child_frame(cents: int) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            columns=(
                FrameColumn(name="carc", ref=DimensionRef("carc")),
                FrameColumn(
                    name="denied_dollars", ref=MetricRef("denied_dollars"), unit="money_cents"
                ),
            )
        ),
        rows=(("16", cents),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h"),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _parent(make_spec, findings: tuple[Finding, ...]):  # type: ignore[no-untyped-def]
    return Investigation(
        id="inv_parent",
        session_id="sess",
        parent_id=None,
        turn_id="turn_parent",
        turn_class=TurnClass.NEW_INVESTIGATION,
        question="Who is my worst payer on denial rate right now?",
        spec=make_spec(measures=("denial_rate",), dimensions=("payer",)),
        plan_hash="hash",
        status=InvestigationStatus.COMPLETE,
        findings=findings,
        created_at=datetime.now(UTC),
    )


class TestDrillingARateFindingNeverPublishesADisagreement:
    """R9-03, the rcm-analyst's live repro on the product's own chip."""

    def test_a_rate_parent_is_never_read_back_as_zero_dollars(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _parent(make_spec, (_rate_finding(),))
        child = make_spec(measures=("denied_dollars",), dimensions=("carc",))

        result = containment_reconciliation(
            parent,
            CalculationResult(frames=(("main", _money_child_frame(3117449)),), operations=()),
            (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
            child,
            metric_unit=UNITS.get,
        )

        # The one thing that may never happen: a red banner claiming two
        # figures for one cell disagree by 311,744,900%.
        assert result is None or "status=failed" not in result.summary
        assert result is None or "$0.00" not in result.summary

    def test_it_holds_without_a_pack_to_ask(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """Money is cents-as-int everywhere in this system, so a fraction of
        a cent is not money whether or not the contract is reachable."""
        parent = _parent(make_spec, (_rate_finding(),))
        child = make_spec(measures=("denied_dollars",), dimensions=("carc",))

        result = containment_reconciliation(
            parent,
            CalculationResult(frames=(("main", _money_child_frame(3117449)),), operations=()),
            (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
            child,
        )

        assert result is None or "status=failed" not in result.summary

    def test_the_mismatch_is_said_rather_than_left_to_a_generic_reason(self) -> None:
        reason = measure_mismatch_reason(
            (_rate_finding(),),
            (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
            "denied_dollars",
        )
        assert reason is not None
        assert "F1" in reason and "denial_rate" in reason and "denied_dollars" in reason

    def test_a_handle_that_does_publish_the_measure_keeps_its_tie_out(self) -> None:
        money = Finding(
            referent=ReferentId(value="F1", kind=ReferentKind.FINDING),
            title="denied dollars: $31,174.49",
            statement="denied dollars.",
            metric_refs=(MetricRef("denied_dollars"),),
            values=(("denied_dollars", 3117449),),
            grade=EvidenceGrade.DIRECT,
            impact_cents=3117449,
        )
        assert (
            measure_mismatch_reason(
                (money,),
                (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
                "denied_dollars",
            )
            is None
        )


class TestTheSuppressionDisclosureAgreesWithItself:
    """R9-06, on the demo opener (inv_d2df8d75b298).

    "The disclosure says four groups, lists five rows; Veritas Comp Fund
    appears twice (the second row is the COMPARISON window's cell); and it
    states 'fewer than 11 things sit behind each' over a row with 214
    entities." — product-designer P0, uiux P1(4).
    """

    CURRENT = (
        BoundedCell("Veritas Comp Fund", "denial_rate", 214, Decimal("0.0467"), 0),
        BoundedCell("Harborline Health Plan", "denial_rate", 48, Decimal("0.2083"), 1),
        BoundedCell("Cascade Select", "denial_rate", 53, Decimal("0.1887"), 2),
        BoundedCell("Northgate Choice", "denial_rate", 13, Decimal("0.7692"), 3),
    )
    #: The same payer, one window earlier: ≤9.0% over 111.
    PRIOR = (BoundedCell("Veritas Comp Fund", "denial_rate", 111, Decimal("0.0901"), 0),)

    def test_the_stated_count_is_the_length_of_the_list_it_prints(self) -> None:
        sentence = bounded_cells_warning(
            self.CURRENT,
            11,
            census=SuppressionCensus(total=12, bounded=4, withheld=0),
            comparison_cells=self.PRIOR,
        )
        assert sentence is not None
        assert "4 of 12 groups" in sentence
        # One row per named cell, and the count says four because four are
        # named. The live sentence said four and printed five.
        answer, _, _ = sentence.partition("In the comparison window")
        assert answer.count(" entities)") == 4

    def test_the_count_never_disagrees_with_the_list_even_when_the_census_does(self) -> None:
        """The census counted the widest frame; the list came from every
        probe the plan ran. Whichever is stale, the sentence stays true to
        the rows it prints."""
        sentence = bounded_cells_warning(
            self.CURRENT, 11, census=SuppressionCensus(total=2, bounded=9, withheld=0)
        )
        assert sentence is not None
        assert "4 of 4 groups" in sentence
        assert sentence.count(" entities)") == 4

    def test_no_cell_is_named_twice(self) -> None:
        sentence = bounded_cells_warning(
            (*self.CURRENT, *self.CURRENT),
            11,
            census=SuppressionCensus(total=12, bounded=4, withheld=0),
        )
        assert sentence is not None
        assert sentence.count("Veritas Comp Fund") == 1

    def test_the_comparison_window_gets_its_own_labelled_clause(self) -> None:
        sentence = bounded_cells_warning(
            self.CURRENT,
            11,
            census=SuppressionCensus(total=12, bounded=4, withheld=0),
            comparison_cells=self.PRIOR,
        )
        assert sentence is not None
        before, marker, after = sentence.partition("comparison window")
        assert marker, "the prior window's ceilings must be labelled as such"
        # …and the current window's list does not carry the prior cell.
        assert "111 entities" not in before
        assert "111 entities" in after

    def test_the_rule_stated_is_the_rule_applied(self) -> None:
        """A ceiling is a suppressed NUMERATOR over a published population.
        'Fewer than 11 things sit behind each of those numbers' printed over
        a row of 214 entities is refuted by the row beside it."""
        sentence = bounded_cells_warning(
            self.CURRENT, 11, census=SuppressionCensus(total=12, bounded=4, withheld=0)
        )
        assert sentence is not None
        assert "fewer than 11 things sit behind each" not in sentence
        assert "fewer than 11" in sentence
        assert "214 entities" in sentence

    def test_a_comparison_only_bound_still_gets_said(self) -> None:
        sentence = bounded_cells_warning(
            (), 11, census=SuppressionCensus(total=12, bounded=0, withheld=0),
            comparison_cells=self.PRIOR,
        )
        assert sentence is not None
        assert "comparison window" in sentence
        assert "0 of 12" not in sentence


class TestTheLiteralWindowClauseCountsTheOpenDaysOnly:
    """R9-10, uiux P1(5).

    "Literal window 2026-07-03..2026-08-02 is 31 days, of which exactly two
    (Aug 1-2) fall in the still-open month; the sentence claims all 31,
    making the clause meaningless." The signature is using the window's
    LENGTH where the intersection was meant — so the current value equals
    the window length for every trailing-N-day case, which is what this
    rejects.
    """

    JULY = AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 31))

    def test_a_trailing_window_straddling_a_month_boundary(self) -> None:
        literal = AbsoluteRange(start=date(2026, 7, 3), end=date(2026, 8, 2))
        clause = _open_period_clause(literal, self.JULY, WATERMARK)
        assert clause == "2 of its 31 day(s) inside the period that is still open"
        assert "31 day(s) of the period" not in clause

    def test_a_window_entirely_inside_the_open_period_says_all_of_it(self) -> None:
        """The product-designer's differently-phrased run: literal window
        2026-08-01..2026-08-02, and "2 day(s)" was already right there."""
        literal = AbsoluteRange(start=date(2026, 8, 1), end=date(2026, 8, 2))
        clause = _open_period_clause(literal, self.JULY, WATERMARK)
        assert clause == "all 2 day(s) of it inside the period that is still open"

    def test_the_count_is_never_just_the_window_length(self) -> None:
        for days in (7, 14, 30, 31, 60, 90):
            literal = AbsoluteRange(
                start=date(2026, 8, 2) - timedelta(days=days - 1), end=date(2026, 8, 2)
            )
            clause = _open_period_clause(literal, self.JULY, WATERMARK)
            assert f"all {days} day(s)" not in clause or days <= 2
            assert "2 " in clause

    async def test_the_note_the_analyst_reads_says_two(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot
    ) -> None:
        """End to end, on the phrasing that produced the live defect: a
        trailing 31 days resolves to 2026-07-03..2026-08-02 and the
        assumption note must not call all 31 of them unsettled."""
        service = InterpretQuestionService(
            _FixedInterpretation(
                {
                    "intent_summary": "worst payer on denial rate",
                    "metric_ids": ["denial_rate"],
                    "dimension_ids": ["payer"],
                    "concept_ids": [],
                    "playbook_id": None,
                    "window": {"quantity": "31", "unit": "day", "mode": "trailing"},
                    "basis": None,
                    "comparison": None,
                    "scope": [],
                    "direction": None,
                    "magnitude": None,
                    "clarification": None,
                    "clarification_options": [],
                    "definitional_terms": [],
                }
            ),
            pack_port,
            catalog,
        )
        outcome = await service.interpret(
            "Who is my worst payer on denial rate right now?",
            session=_SESSION,
            turn_id="t1",
        )
        assert outcome.investigation is not None, outcome.clarification
        note = next(
            n for n in outcome.investigation.notes if n.startswith("window_assumed")
        )
        assert "2026-07-03..2026-08-02" in note
        assert "2 of its 31 day(s) inside the period that is still open" in note
        assert "31 day(s) of the period that is still open" not in note



@pytest.fixture(name="validator")
def validator_fixture(
    catalog: CatalogSnapshot, pack_port: PackSnapshotPort
) -> PlanValidationService:
    """The §6.6 validator, for the checks that read no warehouse.

    ``unexecutable_cut`` is pure catalog + pack — it answers "can this
    metric be cut that way" from two snapshots — so the repository is never
    reached and is not wired.
    """
    return PlanValidationService(catalog, pack_port, repository=cast(Any, None))


#: The option the live session collapsed to and then RAN, unasked.
SURVIVOR = "Show days in A/R for July 2026"


def _scorecard_refusal() -> ClarificationRequest:
    return ClarificationRequest(
        question=(
            "I can't build a payer scorecard: this pack has no playbook that composes "
            "denials, collections and A/R into one view."
        ),
        options=(SURVIVOR,),
        reason="PLAYBOOK_TRANSFORM_UNAVAILABLE: scorecard",
        bindings=(
            ClarificationBinding(
                option=SURVIVOR, kind="grounded_option", metric_ids=("days_in_ar",)
            ),
        ),
    )


class TestACollapseToOneOptionIsStatedNeverSelected:
    """R9-02, the exec's demo blocker #2.

    Clean session ``sess_765ad30357c7``: "Give me a payer scorecard for July
    2026" → the correct ``clarification_required`` with a good refusal and
    four bound options. The same utterance in a session with prior context
    (``sess_8e7a46512fd9``) → ``outcome: answer``, sole finding "F5 | Atlas
    Commercial: 179.5 days in ar" — a payer the turn never named — with the
    refusal demoted into ``CLARIFICATION_ANSWER_APPLIED`` ("this was the
    only answer left… so it was applied rather than asked about").
    """

    def test_the_refusal_keeps_the_lead(self) -> None:
        clarification = _scorecard_refusal()
        stated = _state_the_survivor(clarification, clarification.bindings[0])
        assert stated.question.startswith("I can't build a payer scorecard")

    def test_the_survivor_is_named_and_not_run(self) -> None:
        clarification = _scorecard_refusal()
        stated = _state_the_survivor(clarification, clarification.bindings[0])
        assert SURVIVOR in stated.question
        assert "I have not run it on your behalf" in stated.question
        assert stated.options == (SURVIVOR,)
        assert CLARIFICATION_SOLE_SURVIVOR_REASON in (stated.reason or "")

    def test_the_original_reason_survives_for_every_other_reader(self) -> None:
        clarification = _scorecard_refusal()
        stated = _state_the_survivor(clarification, clarification.bindings[0])
        assert (stated.reason or "").startswith("PLAYBOOK_TRANSFORM_UNAVAILABLE")


class TestAThreadFilterIsNeverCarriedOntoTheCutItAsksAbout:
    """R9-02's other half: RESUMED_CONTEXT carried ``payer eq [Atlas
    Commercial]`` onto a turn that asked for a scorecard ACROSS payers."""

    def test_a_filter_on_the_dimension_being_cut_by_is_not_inherited(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        thread = make_spec(
            measures=("days_in_ar",),
            scope=Predicate(
                dimension=DimensionRef("payer"),
                op=PredicateOp.EQ,
                values=("Atlas Commercial",),
            ),
        )
        asked = make_spec(measures=("days_in_ar",), dimensions=("payer",))

        resumed, _, notes = _with_resumed_context(asked, thread, True)

        assert not [
            p for p in iter_predicates(resumed.context.scope) if p.dimension.id == "payer"
        ]
        assert any("is NOT carried" in note for note in notes)

    def test_a_filter_on_another_dimension_is_still_inherited(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        thread = make_spec(
            measures=("denial_rate",),
            scope=Predicate(
                dimension=DimensionRef("service_line"),
                op=PredicateOp.EQ,
                values=("Imaging",),
            ),
        )
        asked = make_spec(measures=("denial_rate",), dimensions=("payer",))

        resumed, _, notes = _with_resumed_context(asked, thread, True)

        assert [p.dimension.id for p in iter_predicates(resumed.context.scope)] == [
            "service_line"
        ]
        assert any("are carried onto" in note for note in notes)


class TestEveryOfferedOptionIsOneTheEngineCanRun:
    """R9-07: "Why did it go up?" burned three turns and fired the circuit
    breaker on the product's own suggestion.

    "GRAIN_INCOMPATIBLE_RECOVERABLE: denial_category is not a scope
    dimension of denial_rate — the product offered a breakdown it knew it
    cannot run." — fresh-eyes P0.
    """

    def test_a_cut_the_metric_does_not_declare_is_refused_before_offer(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        assert (
            validator.unexecutable_cut(
                "Yes — re-group the figure F1 result by denial reason", ("denial_rate",)
            )
            is not None
        )

    def test_a_legal_cut_survives(self, validator) -> None:  # type: ignore[no-untyped-def]
        assert (
            validator.unexecutable_cut("Break denial rate down by payer", ("denial_rate",))
            is None
        )

    def test_a_platform_recovery_chip_is_not_a_query(self, validator) -> None:  # type: ignore[no-untyped-def]
        assert (
            validator.unexecutable_cut("Raise the per-turn cost ceiling", ("denial_rate",))
            is None
        )

    def test_an_option_naming_both_a_legal_and_an_illegal_cut_survives(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        """One-sided on purpose: the option is answerable, the composer was
        imprecise, and dropping it would cost the analyst a real route."""
        assert (
            validator.unexecutable_cut(
                "Break denial rate down by payer and denial category", ("denial_rate",)
            )
            is None
        )

    def test_a_playbook_this_engine_cannot_answer_is_refused_before_offer(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        """Round-10 R10-6, the live option verbatim. "Who is my worst
        payer?" offered "Run a full payer scorecard across all measures";
        asking for that elsewhere returns ``PLAYBOOK_TRANSFORM_UNAVAILABLE:
        payer_scorecard answers by 'pivot'``."""
        assert validator.unanswerable_playbook(
            "Run a full payer scorecard across all measures"
        ) == ("payer_scorecard", "pivot")

    def test_the_hero_chip_advertising_an_unimplemented_forecast_is_caught(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        """Guide chip 5 at the source: ``cash_outlook`` answers by
        ``project_lagged_realization``, which this engine does not
        implement, and the chip is on the hero."""
        assert validator.unanswerable_playbook("Will my cash increase next month?") == (
            "cash_outlook",
            "project_lagged_realization",
        )

    def test_a_playbook_this_engine_CAN_answer_survives(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        assert validator.unanswerable_playbook("Show me AR aging") is None
        assert validator.unanswerable_playbook("Break denial rate down by payer") is None

    def test_an_option_naming_a_measure_is_a_direct_query_however_it_is_phrased(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        """One-sided, exactly like ``unexecutable_cut``. ``payer_scorecard``
        declares the trigger "rank payers", and "Rank payers by denial rate"
        is a question this engine answers in one probe — dropping it would
        cost the analyst a real route to keep a rule tidy."""
        for option in (
            "Rank payers by denial rate",
            "Score each payer on days_in_ar",
            "Payer scorecard: just the denial rate column",
        ):
            assert validator.unanswerable_playbook(option) is None, option

    def test_an_option_naming_no_playbook_at_all_survives(
        self, validator
    ) -> None:  # type: ignore[no-untyped-def]
        assert validator.unanswerable_playbook("Raise the per-turn cost ceiling") is None
        assert validator.unanswerable_playbook("") is None

    def test_which_measure_is_recognised_however_it_is_phrased(self) -> None:
        for question in (
            "Which metric are you asking about?",
            "Which measure did you mean — the last figure you charted?",
            "What metric are you asking about?",
        ):
            assert _ASKS_WHICH_MEASURE.search(question), question

    def test_an_ordinary_question_is_not_mistaken_for_it(self) -> None:
        for question in (
            "Which payer did you mean?",
            "This pack defines no metric called 'foo'. Did you mean one of these?",
        ):
            assert not _ASKS_WHICH_MEASURE.search(question), question


class TestOneSetOfRowsGetsOneNoun:
    """R9-06's smaller twin: the census sentence said "4 of 12 groups" and
    the ranking's own sentence said "4 of 12 payers" about the same four
    rows, on the same card."""

    def test_the_disclosure_uses_the_analyst_s_word_for_its_rows(self) -> None:
        sentence = bounded_cells_warning(
            TestTheSuppressionDisclosureAgreesWithItself.CURRENT,
            11,
            census=SuppressionCensus(total=12, bounded=4, withheld=0),
            noun="payers",
        )
        assert sentence is not None
        assert "4 of 12 payers here are" in sentence

    def test_a_single_row_is_singular(self) -> None:
        sentence = bounded_cells_warning(
            TestTheSuppressionDisclosureAgreesWithItself.CURRENT[:1],
            11,
            census=SuppressionCensus(total=1, bounded=1, withheld=0),
            noun="payers",
        )
        assert sentence is not None
        assert "1 of 1 payer here is" in sentence or "1 of 1 payer here are" in sentence
