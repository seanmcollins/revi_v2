"""The scorecard's answer: a verdict that counts, and never a score.

"What is my top performing payer?" is the most natural question an
executive asks, and it dead-ended. The playbook routed correctly and the
step that turns its probe families into a card had never been built, so the
turn fell into the refusal machinery — six direct-grade result sets, zero
findings.

Building the step is only half of it. The other half is what the card is
allowed to SAY. There is no overall score, because averaging a denial rate
against posted cash needs weights nobody authored and no reader can
inspect; what a scorecard may state is an arithmetic fact about the
orderings it computed — who is first on how many of them, against a
majority the PACK declares. Both outcomes of that count are answers, and
the one that names nobody is not a failure to answer.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.capability_ports import (
    PlaybookSpec,
    ScorecardVerdictSpec,
)
from revi_investigation.application.findings import EvaluateFindingsService
from revi_investigation.application.planning import (
    InvestigationPlan,
    TransformPlan,
    TransformPlanStep,
)
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, TransformProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_testing.fakes import FakeReferentRegistryStore

if TYPE_CHECKING:
    from tests.conftest import SpecFactory

    from revi_testing.engine_wiring import PackSnapshotPort

WATERMARK_ID = "wm_003"
THRESHOLD = 11
PANEL_STEP = "payer_scorecard__panel"

WATERMARK = DataWatermark(
    id=WATERMARK_ID, loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)

#: Four rate measures with an improvement direction, plus one additive
#: dollar column that has one too and still must not vote. Northbridge
#: leads three of the four rates; Atlas leads one and posts the most cash.
_COLUMNS = (
    FrameColumn("payer", DimensionRef("payer")),
    FrameColumn("clean_claim_rate", MetricRef("clean_claim_rate"), 1, "ratio"),
    FrameColumn("initial_denial_rate", MetricRef("initial_denial_rate"), 1, "ratio"),
    FrameColumn("write_off_rate", MetricRef("write_off_rate"), 1, "ratio"),
    FrameColumn("avg_days_to_pay", MetricRef("avg_days_to_pay"), 1, "days"),
    FrameColumn("cash_posted", MetricRef("cash_posted"), 1, "money_cents"),
    FrameColumn("clean_claim_rate__rank", MetricRef("clean_claim_rate"), 1, "count"),
    FrameColumn("initial_denial_rate__rank", MetricRef("initial_denial_rate"), 1, "count"),
    FrameColumn("write_off_rate__rank", MetricRef("write_off_rate"), 1, "count"),
    FrameColumn("avg_days_to_pay__rank", MetricRef("avg_days_to_pay"), 1, "count"),
    FrameColumn("cash_posted__rank", MetricRef("cash_posted"), 1, "count"),
)

_ROWS: tuple[tuple[object, ...], ...] = (
    # payer, clean, initial denial, write-off, days, cash, then the ranks
    ("Northbridge", Decimal("0.95"), Decimal("0.03"), Decimal("0.01"), Decimal("40"), 500, 1, 1, 1, 2, 2),
    ("Atlas", Decimal("0.90"), Decimal("0.06"), Decimal("0.04"), Decimal("20"), 900, 2, 2, 2, 1, 1),
)


def _panel_frame(rows: tuple[tuple[object, ...], ...] = _ROWS) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(_COLUMNS),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=TransformProvenance(operator="panel", operator_version="1.0.0", inputs=()),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _plan() -> InvestigationPlan:
    return InvestigationPlan(
        nodes=(),
        transforms=TransformPlan(
            steps=(
                TransformPlanStep(
                    id=PANEL_STEP,
                    operator="panel",
                    inputs=("payer_flow",),
                    args=(("entity", "payer"),),
                ),
            )
        ),
    )


def _playbook(minimum: int, measures: tuple[str, ...]) -> PlaybookSpec:
    return PlaybookSpec(
        id="payer_scorecard",
        description="",
        probes=(),
        verdict=ScorecardVerdictSpec(leader_min_measures=minimum, measures=measures),
    )


_RATES = ("clean_claim_rate", "initial_denial_rate", "write_off_rate", "avg_days_to_pay")


def _evaluate(
    pack_port: PackSnapshotPort,
    make_spec: SpecFactory,
    *,
    minimum: int,
    measures: tuple[str, ...] = _RATES,
    frame: EvidenceFrame | None = None,
):  # type: ignore[no-untyped-def]
    service = EvaluateFindingsService(FakeReferentRegistryStore())
    spec = make_spec(dimensions=("payer",), watermark=WATERMARK)
    return asyncio.run(
        service.evaluate(
            plan=_plan(),
            calculation=CalculationResult(
                frames=((PANEL_STEP, frame if frame is not None else _panel_frame()),),
                operations=(),
            ),
            spec=spec,
            pack=pack_port,
            playbook=_playbook(minimum, measures),
            session_id="s1",
            investigation_id="i1",
            suppression_threshold=THRESHOLD,
        )
    )


class TestTheVerdictCountsAndNeverScores:
    def test_a_leader_is_named_when_it_clears_the_packs_majority(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        result = _evaluate(pack_port, make_spec, minimum=3)

        assert result.findings[0].title == "Northbridge leads on 3 of 4 measures"
        statement = result.findings[0].statement
        assert "clean claim rate" in statement
        assert "There is no combined score" in statement

    def test_no_leader_is_a_first_class_answer_naming_who_leads_what(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The second outcome of the same count. Northbridge is first on
        three of four and the pack asks for all four, so nobody leads — and
        that sentence is the answer, not a shrug."""
        result = _evaluate(pack_port, make_spec, minimum=4)

        assert result.findings[0].title == "No payer leads overall"
        statement = result.findings[0].statement
        assert "Different payers lead different measures" in statement
        # Grouped by payer, so one payer leading three measures reads as one
        # payer rather than as three.
        assert statement.count("Northbridge on") == 1
        assert "Atlas on avg days to pay" in statement

    def test_the_majority_is_stated_with_its_number_and_who_can_change_it(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """docs/client-language.md §2.1: a client-facing default states the
        concrete rule, the recommender, and that it can be changed. "Enough
        of the measures" would be the nonsense that sounds like governance."""
        statement = _evaluate(pack_port, make_spec, minimum=3).findings[0].statement

        assert "at least 3 of them" in statement
        assert "Revi's recommended majority" in statement
        assert "You can change this anytime." in statement

    def test_the_verdict_cites_every_measure_it_counted(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """An export or a rederivation that asks what this sentence rests on
        gets the panel, not one column of it."""
        verdict = _evaluate(pack_port, make_spec, minimum=3).findings[0]

        assert {ref.id for ref in verdict.metric_refs} == set(_RATES)

    def test_the_verdict_carries_no_dollar_impact(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """Counting first places is not dollars. An invented impact would
        sort this finding into a queue of recoverable money it is not in."""
        assert _evaluate(pack_port, make_spec, minimum=3).findings[0].impact_cents is None


class TestADollarColumnDoesNotVoteAndDoesNotClaimALeader:
    def test_an_uncounted_additive_column_says_biggest_rather_than_best(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """Atlas posts the most cash because Atlas sends the most business.
        "Atlas leads on posted cash" is the invented composite in miniature:
        it reads as a verdict and it measures size."""
        findings = _evaluate(pack_port, make_spec, minimum=3).findings[1:]
        cash = next(f for f in findings if f.metric_refs[0].id == "cash_posted")

        assert "first on" not in cash.statement
        assert "has the most cash posted" in cash.statement
        assert "not one of the measures the verdict above counts" in cash.statement
        assert "the largest figure names the biggest, not the best" in cash.statement

    def test_a_counted_column_does_claim_its_leader(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        findings = _evaluate(pack_port, make_spec, minimum=3).findings[1:]
        clean = next(f for f in findings if f.metric_refs[0].id == "clean_claim_rate")

        assert "is first on clean claim rate" in clean.statement

    def test_each_counted_measure_states_which_end_is_better(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The only thing that makes "first" mean anything. It comes off the
        metric contract's own sign, so the sentence and the ordering above
        it can never disagree."""
        findings = _evaluate(pack_port, make_spec, minimum=3).findings[1:]
        by_metric = {f.metric_refs[0].id: f.statement for f in findings}

        assert "Higher is better here" in by_metric["clean_claim_rate"]
        assert "Lower is better here" in by_metric["initial_denial_rate"]

    def test_every_measure_the_verdict_counted_is_published(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """Whatever the turn's finding limit says. The verdict is a claim
        over exactly those columns and a reader who cannot see them cannot
        check it; the default limit is this platform's judgement about how
        many rows of ONE ranking to show, and a scorecard is not one
        ranking."""
        findings = _evaluate(pack_port, make_spec, minimum=3).findings
        published = {f.metric_refs[0].id for f in findings[1:]}

        assert set(_RATES) <= published


class TestTheHonestyMachinerySurvivesPerColumn:
    def test_a_measure_whose_field_is_mostly_ceilings_is_not_ordered(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The bounded-ranking rule, asked once per COLUMN instead of once
        per frame. A column of ceilings nominates no leader and does not
        vote — the rest of the scorecard is unaffected."""
        columns = (
            *_COLUMNS,
            FrameColumn("appeal_overturn_rate__num", MetricRef("appeal_overturn_rate"), 1, "count"),
            FrameColumn("appeal_overturn_rate__den", MetricRef("appeal_overturn_rate"), 1, "count"),
            FrameColumn("appeal_overturn_rate", MetricRef("appeal_overturn_rate"), 1, "ratio"),
            FrameColumn("appeal_overturn_rate__rank", MetricRef("appeal_overturn_rate"), 1, "count"),
        )
        # Both cells bounded: numerator under the threshold, population over
        # it — the shape §15 publishes as a ceiling.
        rows = tuple(
            (*row, 4, 200, Decimal("0.05"), i + 1) for i, row in enumerate(_ROWS)
        )
        frame = EvidenceFrame(
            schema=FrameSchema(columns),
            rows=rows,  # type: ignore[arg-type]
            watermark=WATERMARK,
            provenance=TransformProvenance(
                operator="panel", operator_version="1.0.0", inputs=()
            ),
            evidence_grade=EvidenceGrade.DIRECT,
        )

        result = _evaluate(
            pack_port,
            make_spec,
            minimum=3,
            measures=(*_RATES, "appeal_overturn_rate"),
            frame=frame,
        )

        # Five measures were nominated and only four could be ordered.
        assert "3 of 4 measures" in result.findings[0].title
        assert not any(
            f.metric_refs[0].id == "appeal_overturn_rate" for f in result.findings[1:]
        )
        # A trailing note, not a leading refusal: the verdict above it is
        # unaffected and one column of a scorecard is not its answer.
        assert any(w.startswith("bounded_cells_unranked:") for w in result.warnings)
        assert any("appeal overturn rate could not be put in order" in w for w in result.warnings)

    def test_a_scorecard_no_column_of_which_can_be_ordered_publishes_nothing(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """And says which of the two nothings it is, as data. A card with a
        verdict over no ordering would be the invented score wearing the
        count's clothes."""
        rows = tuple((row[0], *(None,) * (len(_COLUMNS) - 1)) for row in _ROWS)
        result = _evaluate(pack_port, make_spec, minimum=3, frame=_panel_frame(rows))

        assert result.findings == ()
        assert result.emptiness is not None
        assert "not one measure could be put in order" in result.emptiness.detail
