"""Round-4 R4-05 (premise verdict) and R4-10 (zero is a measurement).

**R4-05** was the highest-variance claim of the round — four different
outcomes across six reviewers from one subsystem:

(a) it never fired on an undimensioned plan (0 of 5 live probes), because
    the verdict only accepted a compare step whose first input started with
    ``premise`` and the plans produced were ``['main', 'main__prior']``;
(b) it bound to whichever compared column was MONEY, so a question about
    denial RATE was answered "You asked about a doubling in denied dollars.
    It did not happen" — as bolded F1, at high confidence, on a turn whose
    rate had risen;
(c) it CONFIRMED a doubling at +72.6%, with ``asserted_multiple: 2.0`` and
    ``pct_change: 0.726`` sitting in the same values array, because the
    magnitude test was a one-sided floor at half the asserted change.

**R4-10**: measured 0%-rate cells were dropped as "padding", which drove
the measured count to zero and manufactured a refusal to rank — beside a
census that said thirteen cells were measured.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.findings import (
    EvaluateFindingsService,
    MagnitudeVerdict,
    verify_premise,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
    TransformPlan,
    TransformPlanStep,
)
from revi_investigation.domain.context import AskedDirection
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import FakeReferentRegistryStore

if TYPE_CHECKING:
    from tests.conftest import SpecFactory

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


# ---------------------------------------------------------------- fixtures


def _scalar_rate_compare(
    current: str, prior: str, *, measure: str = "denial_rate"
) -> EvidenceFrame:
    """The ungrouped aggregate a rate question's premise is about."""
    columns = (
        FrameColumn(measure, MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 2, "ratio"),
    )
    now, was = Decimal(current), Decimal(prior)
    pct = ((now - was) / was).quantize(Decimal("0.0001"))
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=((now, was, now - was, pct),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="p" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _two_metric_scalar_compare() -> EvidenceFrame:
    """One frame, two compared metrics: denied dollars FELL, the rate ROSE.

    The adonis case. Whichever column the verdict binds to decides whether
    the answer is "it did not happen" or "it happened".
    """
    columns = (
        FrameColumn("denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"),
        FrameColumn("denied_dollars__prior", MetricRef("denied_dollars"), 1, "money_cents"),
        FrameColumn("denied_dollars__delta", MetricRef("denied_dollars"), 1, "money_cents"),
        FrameColumn("denied_dollars__pct_change", MetricRef("denied_dollars"), 1, "ratio"),
        FrameColumn("denial_rate", MetricRef("denial_rate"), 2, "ratio"),
        FrameColumn("denial_rate__prior", MetricRef("denial_rate"), 2, "ratio"),
        FrameColumn("denial_rate__delta", MetricRef("denial_rate"), 2, "ratio"),
        FrameColumn("denial_rate__pct_change", MetricRef("denial_rate"), 2, "ratio"),
    )
    rows = (
        (
            31_142_306,
            113_476_506,
            -82_334_200,
            Decimal("-0.7273"),
            Decimal("0.128"),
            Decimal("0.091"),
            Decimal("0.037"),
            Decimal("0.4066"),
        ),
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="p" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _undimensioned_plan() -> InvestigationPlan:
    """The plan four live probes actually produced: no premise node."""
    return InvestigationPlan(
        nodes=(),
        transforms=TransformPlan(
            steps=(
                TransformPlanStep(
                    id="main__compare", operator="compare", inputs=("main", "main__prior")
                ),
            )
        ),
    )


def _premise_plan() -> InvestigationPlan:
    return InvestigationPlan(
        nodes=(),
        transforms=TransformPlan(
            steps=(
                TransformPlanStep(
                    id="main__compare", operator="compare", inputs=("main", "main__prior")
                ),
                TransformPlanStep(
                    id="premise__compare",
                    operator="compare",
                    inputs=("premise", "premise__prior"),
                ),
            )
        ),
    )


def _asserted(spec: object, *, multiple: Decimal | None = None) -> object:
    return replace(
        spec,  # type: ignore[type-var]
        direction=AskedDirection.INCREASE,
        direction_asserted=True,
        asserted_multiple=multiple,
    )


# ----------------------------------------------------------------- R4-05a


class TestPremiseFiresOnAScalarPlan:
    """(a) 0 of 5 live probes fired. A scalar frame is a scalar frame
    whatever the step that produced it is called."""

    def test_an_undimensioned_compare_is_a_premise_frame(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        spec = _asserted(
            make_spec(measures=("denial_rate",), watermark=WATERMARK), multiple=Decimal(2)
        )
        calculation = CalculationResult(
            frames=(("main__compare", _scalar_rate_compare("0.128", "0.074")),), operations=()
        )

        premise = verify_premise(
            _undimensioned_plan(),
            calculation,
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
        )

        assert premise is not None
        assert premise.measure == "denial_rate"

    def test_a_dedicated_premise_probe_still_wins(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """Plans that DO carry a premise node are read exactly as before."""
        spec = _asserted(make_spec(measures=("denial_rate",), watermark=WATERMARK))
        calculation = CalculationResult(
            frames=(
                ("main__compare", _scalar_rate_compare("0.128", "0.074")),
                ("premise__compare", _scalar_rate_compare("0.200", "0.100")),
            ),
            operations=(),
        )

        premise = verify_premise(
            _premise_plan(),
            calculation,
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
        )

        assert premise is not None
        assert premise.frame_id == "premise__compare"
        assert premise.prior == Decimal("0.100")


# ----------------------------------------------------------------- R4-05b


class TestPremiseBindsTheNamedMetric:
    """(b) "You asked about a doubling in denied dollars" — over a question
    about denial RATE, on a turn where the rate rose and the dollars fell."""

    def test_the_spec_metric_wins_over_the_money_column(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        spec = _asserted(make_spec(measures=("denial_rate",), watermark=WATERMARK))
        calculation = CalculationResult(
            frames=(("main__compare", _two_metric_scalar_compare()),), operations=()
        )

        premise = verify_premise(
            _undimensioned_plan(),
            calculation,
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
        )

        assert premise is not None
        assert premise.measure == "denial_rate"
        assert premise.holds, "the rate rose; the question asserted a rise"

    def test_a_metric_the_frame_never_measured_is_never_substituted(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """"If the named metric is not probeable, say the premise could not
        be evaluated; never substitute a different one." """
        spec = _asserted(make_spec(measures=("cash_posted",), watermark=WATERMARK))
        calculation = CalculationResult(
            frames=(("main__compare", _two_metric_scalar_compare()),), operations=()
        )

        premise = verify_premise(
            _undimensioned_plan(),
            calculation,
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
        )

        assert premise is None


# ----------------------------------------------------------------- R4-05c


class TestMagnitudeBandIsTwoSided:
    """(c) "Premise confirmed … It happened: 7.4% → 12.8%, 72.6%" at high
    confidence, with asserted_multiple 2.0 in the same values array."""

    async def _verdict(
        self,
        pack_port: PackSnapshotPort,
        make_spec: SpecFactory,
        current: str,
        prior: str,
        multiple: Decimal = Decimal(2),
    ) -> tuple[tuple[str, ...], tuple[str, ...], object]:
        spec = _asserted(
            make_spec(measures=("denial_rate",), watermark=WATERMARK), multiple=multiple
        )
        service = EvaluateFindingsService(FakeReferentRegistryStore())
        result = await service.evaluate(
            plan=_undimensioned_plan(),
            calculation=CalculationResult(
                frames=(("main__compare", _scalar_rate_compare(current, prior)),), operations=()
            ),
            spec=spec,  # type: ignore[arg-type]
            pack=pack_port,
            playbook=None,
            session_id="sess",
            investigation_id="inv",
        )
        values = dict(result.findings[0].values) if result.findings else {}
        return (
            tuple(f.title for f in result.findings),
            result.warnings,
            values,
        )

    async def test_a_doubling_asserted_at_plus_72_6_percent_is_short_of(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The live case, exactly: 7.42% → 12.81%."""
        titles, warnings, values = await self._verdict(
            pack_port, make_spec, "0.1281", "0.0742"
        )

        assert titles[0].startswith("Premise partly supported:")
        assert "It did not double" in titles[0]
        assert "short of the 100.0% a doubling assumes" in titles[0]
        assert warnings[0].startswith("premise_partial:")
        # Neither confirmation nor refutation-of-direction.
        assert "It happened" not in titles[0]
        assert "It did not happen" not in titles[0]
        assert values["premise_holds"] is False  # type: ignore[index]
        assert values["premise_magnitude"] == "short"  # type: ignore[index]
        assert values["asserted_multiple"] == Decimal(2)  # type: ignore[index]
        assert values["premise_asserted_verb"] == "double"  # type: ignore[index]

    async def test_an_actual_doubling_confirms(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        titles, warnings, values = await self._verdict(
            pack_port, make_spec, "0.1500", "0.0750"
        )

        assert titles[0].startswith("Premise confirmed:")
        assert warnings[0].startswith("premise_verified:")
        assert values["premise_magnitude"] == "within"  # type: ignore[index]

    async def test_a_tenfold_move_is_not_a_doubling_either(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The band is two-sided: the old floor confirmed "doubled" for a
        10x move as readily as for +50%."""
        titles, _, values = await self._verdict(pack_port, make_spec, "0.7500", "0.0750")

        assert values["premise_magnitude"] == "beyond"  # type: ignore[index]
        assert "by more than that" in titles[0]
        assert values["premise_holds"] is True  # type: ignore[index]

    @pytest.mark.parametrize(
        ("current", "prior", "expected"),
        [
            ("0.1500", "0.0750", MagnitudeVerdict.WITHIN),  # 2.00x — exactly it
            ("0.1320", "0.0750", MagnitudeVerdict.WITHIN),  # 1.76x — inside the band
            ("0.1280", "0.0750", MagnitudeVerdict.SHORT),  # 1.71x — under it
            ("0.1740", "0.0750", MagnitudeVerdict.BEYOND),  # 2.32x — over it
        ],
    )
    def test_the_band_edges(
        self,
        pack_port: PackSnapshotPort,
        make_spec: SpecFactory,
        current: str,
        prior: str,
        expected: MagnitudeVerdict,
    ) -> None:
        spec = _asserted(
            make_spec(measures=("denial_rate",), watermark=WATERMARK), multiple=Decimal(2)
        )
        premise = verify_premise(
            _undimensioned_plan(),
            CalculationResult(
                frames=(("main__compare", _scalar_rate_compare(current, prior)),), operations=()
            ),
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
        )
        assert premise is not None
        assert premise.magnitude is expected

    def test_a_halving_reads_the_same_rule(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """"fell by half" asserts -50%; -10% is short of it, -48% is it."""
        spec = replace(
            make_spec(measures=("denial_rate",), watermark=WATERMARK),  # type: ignore[type-var]
            direction=AskedDirection.DECREASE,
            direction_asserted=True,
            asserted_multiple=Decimal("0.5"),
        )

        def verdict(current: str, prior: str) -> MagnitudeVerdict:
            premise = verify_premise(
                _undimensioned_plan(),
                CalculationResult(
                    frames=(("main__compare", _scalar_rate_compare(current, prior)),),
                    operations=(),
                ),
                spec,  # type: ignore[arg-type]
                pack_port,
                premise_prefix="premise",
            )
            assert premise is not None
            return premise.magnitude

        assert verdict("0.0520", "0.1000") is MagnitudeVerdict.WITHIN
        assert verdict("0.0900", "0.1000") is MagnitudeVerdict.SHORT
        assert verdict("0.0100", "0.1000") is MagnitudeVerdict.BEYOND


# ------------------------------------------------------------------ R4-10


def _provider_rank_frame(
    rows: tuple[tuple[str, object, int, int], ...],
) -> EvidenceFrame:
    """A ranked provider frame: label, rate, numerator, denominator."""
    columns = (
        FrameColumn("provider", DimensionRef("provider")),
        FrameColumn("denial_rate", MetricRef("denial_rate"), 2, "ratio"),
        FrameColumn("denial_rate__num", MetricRef("denial_rate"), 2, "count"),
        FrameColumn("denial_rate__den", MetricRef("denial_rate"), 2, "count"),
        FrameColumn("denial_rate__rank", MetricRef("denial_rate"), 2, "count"),
    )
    built = tuple(
        (label, value, num, den, i + 1) for i, (label, value, num, den) in enumerate(rows)
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=built,
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="p" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
        suppressed_cells=1,
    )


def _rank_plan() -> InvestigationPlan:
    return InvestigationPlan(
        nodes=(),
        transforms=TransformPlan(
            steps=(
                TransformPlanStep(
                    id="main__rank",
                    operator="rank",
                    inputs=("main",),
                    args=(("by", "denial_rate"),),
                ),
            )
        ),
    )


class TestZeroIsAMeasurement:
    """R4-10. Thirteen providers with a 0% denial rate — the perfect
    performers — were discarded by a rule written for counts, which drove
    the measured count to 0 and manufactured "leaving 0 measured, so no
    ranking is published" beside a census stating 13 are measured."""

    async def _evaluate(
        self,
        frame: EvidenceFrame,
        pack_port: PackSnapshotPort,
        make_spec: SpecFactory,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        service = EvaluateFindingsService(FakeReferentRegistryStore())
        result = await service.evaluate(
            plan=_rank_plan(),
            calculation=CalculationResult(frames=(("main__rank", frame),), operations=()),
            spec=make_spec(  # type: ignore[arg-type]
                measures=("denial_rate",), dimensions=("provider",), watermark=WATERMARK
            ),
            pack=pack_port,
            playbook=None,
            session_id="sess",
            investigation_id="inv",
            suppression_threshold=11,
        )
        return tuple(f.title for f in result.findings), result.warnings

    async def test_a_zero_rate_cell_is_published_and_counted(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        frame = _provider_rank_frame(
            (
                ("Dr. Alder", Decimal("0.250"), 25, 100),
                ("Dr. Birch", Decimal("0.100"), 15, 150),
                ("Dr. Cedar", Decimal("0.000"), 0, 100),
                ("Dr. Dogwood", Decimal("0.000"), 0, 90),
                ("Dr. Elm", Decimal("0.9090"), 10, 11),  # bounded: 10 < 11 <= 11
            )
        )

        titles, warnings = await self._evaluate(frame, pack_port, make_spec)

        assert any("Dr. Cedar" in title for title in titles), titles
        refusal = [w for w in warnings if w.startswith("ranking_refused:")]
        assert not refusal, "4 measured against 1 bound is a rankable population"
        unranked = [w for w in warnings if w.startswith("bounded_cells_unranked:")]
        assert unranked and "1 of 5" in unranked[0]

    async def test_the_refusal_only_fires_against_all_measured_cells(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """Zeros counted, the bounded share is 3/5 — a genuine majority, so
        the refusal fires honestly and its census reconciles."""
        frame = _provider_rank_frame(
            (
                ("Dr. Alder", Decimal("0.9090"), 10, 11),
                ("Dr. Birch", Decimal("0.9090"), 10, 11),
                ("Dr. Cedar", Decimal("0.5555"), 10, 18),
                ("Dr. Dogwood", Decimal("0.000"), 0, 100),
                ("Dr. Elm", Decimal("0.100"), 20, 200),
                ("Dr. Fir", None, None, None),  # withheld outright
            )
        )

        _, warnings = await self._evaluate(frame, pack_port, make_spec)

        refusal = next(w for w in warnings if w.startswith("ranking_refused:"))
        assert "3 of the 5 publishable" in refusal
        assert "leaving 2 measured" in refusal
        # …and the arithmetic reconciles to the frame, in the same words the
        # suppression disclosure uses: 3 + 1 + 2 == 6.
        assert "Of 6 cell(s) on this answer, 3 carry an upper bound, 1 were withheld" in refusal
        assert "and 2 are measured" in refusal

    async def test_an_additive_zero_is_still_padding(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The rule the zero-drop was written for is unchanged: "Payer X: 0
        mismatched claims" is a ranked list's tail, not a result."""
        columns = (
            FrameColumn("payer", DimensionRef("payer")),
            FrameColumn("denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"),
            FrameColumn("denied_dollars__rank", MetricRef("denied_dollars"), 1, "count"),
        )
        frame = EvidenceFrame(
            schema=FrameSchema(columns),
            rows=(("Meridian Health", 500_000, 1), ("Atlas Commercial", 0, 2)),
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="main", probe_hash="p" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )
        service = EvaluateFindingsService(FakeReferentRegistryStore())
        result = await service.evaluate(
            plan=InvestigationPlan(
                nodes=(),
                transforms=TransformPlan(
                    steps=(
                        TransformPlanStep(
                            id="main__rank",
                            operator="rank",
                            inputs=("main",),
                            args=(("by", "denied_dollars"),),
                        ),
                    )
                ),
            ),
            calculation=CalculationResult(frames=(("main__rank", frame),), operations=()),
            spec=make_spec(  # type: ignore[arg-type]
                measures=("denied_dollars",), dimensions=("payer",), watermark=WATERMARK
            ),
            pack=pack_port,
            playbook=None,
            session_id="sess",
            investigation_id="inv",
            suppression_threshold=11,
        )

        titles = [f.title for f in result.findings]
        assert len(titles) == 1 and titles[0].startswith("Meridian Health")


class TestBoundsAreOrderedByPopulation:
    """A ceiling is ``(threshold - 1) / n``, so ordering bounded cells by
    VALUE orders them by inverse panel size — and the three published were
    always the three loosest ceilings over the three smallest populations,
    while a far more useful "≤ 55.6% over 18 entities" sat in the same
    payload's census."""

    async def test_the_tightest_ceiling_is_published_first(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        frame = _provider_rank_frame(
            (
                ("Dr. Alder", Decimal("0.9090"), 10, 11),
                ("Dr. Birch", Decimal("0.9090"), 10, 11),
                ("Dr. Cedar", Decimal("0.5555"), 10, 18),
                ("Dr. Dogwood", Decimal("0.2000"), 10, 50),
            )
        )
        service = EvaluateFindingsService(FakeReferentRegistryStore())
        result = await service.evaluate(
            plan=_rank_plan(),
            calculation=CalculationResult(frames=(("main__rank", frame),), operations=()),
            spec=make_spec(  # type: ignore[arg-type]
                measures=("denial_rate",), dimensions=("provider",), watermark=WATERMARK
            ),
            pack=pack_port,
            playbook=None,
            session_id="sess",
            investigation_id="inv",
            suppression_threshold=11,
        )

        titles = [f.title for f in result.findings]
        assert titles, "every cell here is a bound; they still publish"
        assert "Dr. Dogwood" in titles[0], titles
