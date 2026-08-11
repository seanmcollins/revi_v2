"""What the answer's SHAPE changes about what gets published.

Four defects from the live corpus, all of them selection rather than
composition:

* **no total on a how-much question** — "how much did we write off last
  month?" came back with three payers and their shares, "22.8% of the
  total" three times, and never printed the total. It is the share
  denominator: computed, and simply not published.
* **no yes or no on a yes/no question** — six of six. "Are any payers
  paying us less than the contract says?" is answered *yes, three of them,
  $197.6K*, and that sentence did not exist.
* **impact-first on a deadline question** — ``timely_filing_watch``
  headlined the ``90+`` runway band, the one furthest from the deadline it
  asked about, with ``expired`` third.
* **a catch-all bucket as the #1 answer** — ``OTHER`` is the largest cell
  in a denial-category cut by construction, and impact-first therefore made
  it the answer to "what are we getting denied for most?".

Each is asserted with its own honesty bound intact: the total is the
VISIBLE total wherever a cell was withheld, the verdict is not published at
all where a zero stands over censored cells, and a demotion only ever moves
a row DOWN — nothing is dropped.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.capability_ports import PlaybookSpec
from revi_investigation.application.findings import EvaluateFindingsService
from revi_investigation.application.findings.bounds import (
    AGGREGATE_VALUE,
    RESIDUAL_LAST_POLICY,
    URGENCY_FIRST_POLICY,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
    TransformPlanStep,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    AnswerShape,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import Finding
from revi_kernel.filters import EMPTY_SCOPE
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import POST, DimensionRef, EntityGrain, Grain, MetricRef
from revi_kernel.scope import AbsoluteRange, RangeMode, RelativeRange, TimeUnit, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort, load_base_pack
from revi_testing.fakes import FakeReferentRegistryStore

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
JULY = TimeWindow(
    basis=POST,
    range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
    requested=RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS),
)
PACK_VERSION = PackVersionRef("base-rcm", "1.0.0")

MEASURE = "denial_write_off_dollars"
#: The live figures, to the cent: three payers of one write-off cut.
CELLS = (
    ("State Medicaid", 15317769),
    ("Atlas Commercial", 12365412),
    ("Halvern Health", 7538118),
)
TOTAL = sum(value for _, value in CELLS)


@pytest.fixture(scope="module")
def pack() -> PackSnapshotPort:
    return load_base_pack()


def _spec(shape: AnswerShape | None, subject: str | None = MEASURE) -> AnalysisSpec:
    return AnalysisSpec(
        context=InvestigationContext(
            window=JULY,
            comparison=None,
            scope=EMPTY_SCOPE,
            cohort=None,
            grain=Grain(EntityGrain.CLAIM),
            watermark=WATERMARK,
            pack_version=PACK_VERSION,
        ),
        measures=(MetricRef(MEASURE),),
        dimensions=(DimensionRef("payer"),),
        answer_shape=shape,
        subject_metric=None if subject is None else MetricRef(subject),
    )


def _ranked_frame(
    cells: tuple[tuple[str, int | None], ...] = CELLS,
    *,
    dimension: str = "payer",
    measure: str = MEASURE,
    unit: str = "money_cents",
) -> EvidenceFrame:
    """A rank output: one dimension, the measure, its share and its rank."""
    total = sum(v for _, v in cells if v is not None) or 1
    columns = (
        FrameColumn(dimension, DimensionRef(dimension), 1, None),
        FrameColumn(measure, MetricRef(measure), 1, unit),
        FrameColumn(f"{measure}__share", MetricRef(measure), 1, "ratio"),
        FrameColumn(f"{measure}__rank", MetricRef(measure), 1, None),
    )
    rows = tuple(
        (
            name,
            value,
            None if value is None else (Decimal(value) / Decimal(total)).quantize(
                Decimal("0.000001")
            ),
            index,
        )
        for index, (name, value) in enumerate(cells, start=1)
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="write_off_by_payer", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _rank_plan(
    dimension: str = "payer",
    measure: str = MEASURE,
    buckets: tuple[str, ...] = (),
) -> InvestigationPlan:
    probe = AggregationProbe(
        measures=(MetricRef(measure),),
        dimensions=(DimensionRef(dimension),),
        scope=EMPTY_SCOPE,
        window=JULY,
        grain=Grain(EntityGrain.CLAIM),
    )
    return InvestigationPlan(
        nodes=(ProbeNode(id="write_off_by_payer", probe=probe, purpose="playbook probe"),),
        transforms=TransformPlan(
            steps=(
                TransformPlanStep(
                    id="write_off_by_payer__rank",
                    operator="rank",
                    inputs=("write_off_by_payer",),
                    args=(("by", measure), ("descending", "true")),
                ),
            )
        ),
        bucket_orders=((dimension, buckets),) if buckets else (),
    )


def _playbook(policy: str) -> PlaybookSpec:
    return PlaybookSpec(
        id="test_playbook", description="", probes=(), ranking_policy=policy
    )


async def _evaluate(
    spec: AnalysisSpec,
    frame: EvidenceFrame,
    pack: PackSnapshotPort,
    *,
    plan: InvestigationPlan | None = None,
    playbook: PlaybookSpec | None = None,
) -> tuple[tuple[Finding, ...], tuple[str, ...]]:
    service = EvaluateFindingsService(FakeReferentRegistryStore())
    result = await service.evaluate(
        plan=plan or _rank_plan(),
        calculation=CalculationResult(
            frames=(("write_off_by_payer__rank", frame),), operations=()
        ),
        spec=spec,
        pack=pack,
        playbook=playbook,
        session_id="sess_test",
        investigation_id="inv_test",
    )
    return result.findings, result.warnings


class TestTheTotalIsPublishedOnAHowMuchQuestion:
    async def test_the_aggregate_is_the_first_finding(self, pack: PackSnapshotPort) -> None:
        findings, _ = await _evaluate(_spec(AnswerShape.SCALAR), _ranked_frame(), pack)

        assert findings[0].title.startswith("Total:")
        assert dict(findings[0].values)[MEASURE] == Decimal(TOTAL)
        assert dict(findings[0].values)[AGGREGATE_VALUE] is True
        # …and the concentration cells still follow it, all of them.
        assert [f.title.split(":")[0] for f in findings[1:]] == [name for name, _ in CELLS]

    async def test_the_total_is_the_denominator_the_shares_divide_by(
        self, pack: PackSnapshotPort
    ) -> None:
        """Two arithmetics for one whole is the defect this exists to avoid."""
        findings, _ = await _evaluate(_spec(AnswerShape.SCALAR), _ranked_frame(), pack)

        shares = [
            dict(f.values)["share_of_total"] for f in findings[1:] if "share_of_total" in dict(f.values)
        ]
        assert sum(shares) == pytest.approx(Decimal(1), abs=Decimal("0.000005"))

    async def test_a_withheld_cell_makes_it_a_visible_total_and_says_so(
        self, pack: PackSnapshotPort
    ) -> None:
        cells = (*CELLS, ("Northbridge Commercial", None))
        findings, _ = await _evaluate(_spec(AnswerShape.SCALAR), _ranked_frame(cells), pack)

        assert findings[0].title.startswith("Total measured here:")
        assert "at or above this figure" in findings[0].statement
        assert findings[0].confidence == "qualified"

    async def test_a_question_of_another_shape_publishes_no_total(
        self, pack: PackSnapshotPort
    ) -> None:
        """Nothing changes for the answers this was not written for."""
        findings, _ = await _evaluate(_spec(AnswerShape.ENTITY), _ranked_frame(), pack)

        assert not any(dict(f.values).get(AGGREGATE_VALUE) for f in findings)


class TestTheYesOrNoIsSaidFirst:
    async def test_a_measured_total_above_zero_is_a_yes_with_its_leader(
        self, pack: PackSnapshotPort
    ) -> None:
        _, warnings = await _evaluate(_spec(AnswerShape.VERDICT), _ranked_frame(), pack)

        verdict = next(w for w in warnings if w.startswith("verdict_lead:"))
        assert "Yes —" in verdict
        assert "$352,212.99" in verdict
        assert "State Medicaid" in verdict

    async def test_the_verdict_leads_the_turns_own_warnings(
        self, pack: PackSnapshotPort
    ) -> None:
        _, warnings = await _evaluate(_spec(AnswerShape.VERDICT), _ranked_frame(), pack)

        assert warnings[0].startswith("verdict_lead:")

    async def test_a_zero_over_censored_cells_is_not_a_no(
        self, pack: PackSnapshotPort
    ) -> None:
        """A verdict this platform cannot certify is not published as one."""
        cells = (("State Medicaid", 0), ("Atlas Commercial", None))
        _, warnings = await _evaluate(_spec(AnswerShape.VERDICT), _ranked_frame(cells), pack)

        assert not any(w.startswith("verdict_lead:") for w in warnings)


class TestUrgencyOutranksSize:
    #: The catalog's declared order for ``filing_runway_bucket``, most
    #: urgent first. ``90+`` is 90 days of runway REMAINING — the safest
    #: band, and impact-first made it the headline.
    RUNWAY = ("expired", "0-30", "31-60", "61-90", "90+")

    async def test_a_deadline_question_headlines_the_most_urgent_band(
        self, pack: PackSnapshotPort
    ) -> None:
        cells = (("90+", 122758375), ("expired", 101601504), ("31-60", 50000000))
        plan = _rank_plan("filing_runway_bucket", buckets=self.RUNWAY)
        frame = _ranked_frame(cells, dimension="filing_runway_bucket")

        findings, _ = await _evaluate(
            _spec(AnswerShape.ENTITY, subject=None),
            frame,
            pack,
            plan=plan,
            playbook=_playbook(URGENCY_FIRST_POLICY),
        )

        assert findings[0].title.startswith("expired:")
        assert "sequenced by urgency, not by size" in findings[0].statement

    async def test_the_bands_are_still_all_published(self, pack: PackSnapshotPort) -> None:
        cells = (("90+", 122758375), ("expired", 101601504), ("31-60", 50000000))
        findings, _ = await _evaluate(
            _spec(AnswerShape.ENTITY, subject=None),
            _ranked_frame(cells, dimension="filing_runway_bucket"),
            pack,
            plan=_rank_plan("filing_runway_bucket", buckets=self.RUNWAY),
            playbook=_playbook(URGENCY_FIRST_POLICY),
        )

        assert {f.title.split(":")[0] for f in findings} == {"expired", "90+", "31-60"}


class TestACatchAllBucketIsNeverTheAnswer:
    async def test_other_is_demoted_below_every_classified_cell(
        self, pack: PackSnapshotPort
    ) -> None:
        cells = (("OTHER", 61943456), ("MEDICAL_NECESSITY", 56391901), ("COB", 52289202))
        findings, _ = await _evaluate(
            _spec(AnswerShape.ENTITY, subject=None),
            _ranked_frame(cells, dimension="denial_category"),
            pack,
            plan=_rank_plan("denial_category"),
            playbook=_playbook(RESIDUAL_LAST_POLICY),
        )

        assert findings[0].title.startswith("MEDICAL_NECESSITY:")
        # Demoted, never dropped: the size is real.
        assert any(f.title.startswith("OTHER:") for f in findings)

    async def test_a_playbook_that_does_not_declare_it_orders_as_before(
        self, pack: PackSnapshotPort
    ) -> None:
        cells = (("OTHER", 61943456), ("MEDICAL_NECESSITY", 56391901))
        findings, _ = await _evaluate(
            _spec(AnswerShape.ENTITY, subject=None),
            _ranked_frame(cells, dimension="denial_category"),
            pack,
            plan=_rank_plan("denial_category"),
            playbook=_playbook("impact_first"),
        )

        assert findings[0].title.startswith("OTHER:")
