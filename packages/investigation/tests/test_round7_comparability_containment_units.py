"""Round-7 FN-4, FN-10 and FN-14: the three defects the sold round gated on.

**FN-4 (P0) — comparability is the third leg of the integrity read.**
``verify_premise`` learned to read bounded endpoints (round-5 A-02a) and
adjudicated panel share (A-02b). Both are *signals measured off the frame*.
Whether two windows may be differenced at all is a third question, and the
payload was already answering it — in the metric contract's own governed
``Population caveat:`` — on a surface the verdict never read. Live, on the
vc's own stored data:

    "Premise confirmed: You asked about a decrease in net collection rate.
     It happened: 72.5% → 18.5% vs prior month, fell 53.9 points, a 74.4%
     relative change" — grade ``direct``, confidence ``high``

beside the SAME payload's ``POPULATION_CAVEAT``: *"a recent service-date
cohort has not finished adjudicating or posting … two windows of unequal
maturity are not comparable as levels."* No panel guard fired and none
should have: ``net_collection_rate``'s denominator is contract-expected
DOLLARS, so there is no adjudicated-record asymmetry to see. The round-6
analyst tested ``denial_rate``, where the guard fires loudly, and concluded
the answer surface was clean; it was clean only on the metrics whose
immaturity is expressed as record counts.

**FN-10 (P1, exec gate G6) — rate breakdowns never reconciled.** "For July
2026 on a service basis, the denial rate came in at 12.8% (F1)" → "Break
that down by payer." → *"this turn produced no compared money frame to
reconcile against the parent, so reconciliation is not applicable"*, then
29.5% / 22.9% / 18.8% and four ceilings, and the 12.8% never restated. "The
seam is closed on money and open on rates, which is the drill an RCM
director performs every single day."

**FN-14 (P1) — "179.5 days days in ar".** Reported at round 5, shipped
through M22 and M23, and promoted onto a Rounds tile headline a user reads
every morning. The ungrouped scalar path renders correctly, so the defect
is the *juxtaposition*: a rendered value carries its unit and the measure's
own display name already is "days in ar".
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.comparison import (
    declared_non_comparabilities,
    declared_non_comparability,
    declares_non_comparability,
)
from revi_investigation.application.findings import (
    EvaluateFindingsService,
    FindingsResult,
    premise_verdict_sentence,
    verify_premise,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
    TransformPlan,
    TransformPlanStep,
)
from revi_investigation.application.rendering import (
    format_value,
    measure_phrase,
    metric_label,
    unit_word,
)
from revi_investigation.application.submit_turn import (
    SubmitTurnService,
    containment_reconciliation,
)
from revi_investigation.domain.context import AnalysisSpec, AskedDirection
from revi_investigation.domain.records import Finding, Investigation, InvestigationStatus
from revi_investigation.domain.refinements import DrillInto, SetDimensions
from revi_investigation.domain.turns import TurnClass
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef, ReferentId, ReferentKind
from revi_kernel.scope import AbsoluteRange, Comparison, ComparisonKind, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import FakeReferentRegistryStore

WATERMARK = DataWatermark(
    id="wm_007", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)

#: The vc's own figures, off /tmp/vc7/V10.json.
NCR_CURRENT = Decimal("0.185")
NCR_PRIOR = Decimal("0.725")


# ---------------------------------------------------------------------------
# FN-4 — a verdict over a comparison the payload declares non-comparable


def _compared_scalar_frame(
    measure: str,
    *,
    current: Decimal,
    prior: Decimal,
    numerator: int,
    denominator: int,
    prior_numerator: int,
    prior_denominator: int,
    component_unit: str,
) -> EvidenceFrame:
    """The ungrouped compared rate a premise probe produces, with panels.

    ``component_unit`` is the point of the fixture: ``net_collection_rate``
    counts DOLLARS in its denominator, which is why the adjudicated-record
    panel guard has nothing to see on it.
    """
    columns = (
        FrameColumn(measure, MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__num", MetricRef(measure), 2, component_unit),
        FrameColumn(f"{measure}__den", MetricRef(measure), 2, component_unit),
        FrameColumn(f"{measure}__num__prior", MetricRef(measure), 2, component_unit),
        FrameColumn(f"{measure}__den__prior", MetricRef(measure), 2, component_unit),
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=(
            (
                current,
                prior,
                current - prior,
                (current - prior) / prior,
                numerator,
                denominator,
                prior_numerator,
                prior_denominator,
            ),
        ),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="premise", probe_hash="p" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _money_premise_frame(measure: str, *, current: int, prior: int) -> EvidenceFrame:
    columns = (
        FrameColumn(measure, MetricRef(measure), 2, "money_cents"),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 2, "money_cents"),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 2, "money_cents"),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 2, "ratio"),
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=((current, prior, current - prior, Decimal(current - prior) / Decimal(prior)),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="premise", probe_hash="p" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _premise_plan() -> InvestigationPlan:
    return InvestigationPlan(
        nodes=(),
        transforms=TransformPlan(
            steps=(
                TransformPlanStep(
                    id="premise__compare",
                    operator="compare",
                    inputs=("premise", "premise__prior"),
                ),
            )
        ),
    )


def _asserting(spec: AnalysisSpec, direction: AskedDirection) -> AnalysisSpec:
    return replace(spec, direction=direction, direction_asserted=True)


def _check(frame: EvidenceFrame, spec: AnalysisSpec, pack: PackSnapshotPort):  # type: ignore[no-untyped-def]
    return verify_premise(
        _premise_plan(),
        CalculationResult(frames=(("premise__compare", frame),), operations=()),
        spec,
        pack,
        premise_prefix="premise",
        suppression_threshold=11,
    )


def _ncr_frame() -> EvidenceFrame:
    return _compared_scalar_frame(
        "net_collection_rate",
        current=NCR_CURRENT,
        prior=NCR_PRIOR,
        # Both denominators are contract-expected dollars and near enough to
        # equal: the panel guard has nothing to fire on, which is the whole
        # architectural point of FN-4.
        numerator=1_850_000,
        denominator=10_000_000,
        prior_numerator=7_250_000,
        prior_denominator=10_000_000,
        component_unit="money_cents",
    )


class TestThePackDeclaresWhichComparisonsAreNotComparisons:
    def test_the_assertion_is_read_out_of_the_governed_caveat(self) -> None:
        caveat = declares_non_comparability(
            "Something. Population caveat: a recent service-date cohort has not finished "
            "adjudicating or posting, so two windows of unequal maturity are not comparable "
            "as levels. Benchmark context: 95-99 percent."
        )

        assert caveat is not None
        assert caveat.startswith("a recent service-date cohort")
        # Lifted verbatim and terminated at the next governed section: a
        # paraphrase composed here would be a second, ungoverned statement.
        assert "Benchmark context" not in caveat

    def test_prose_outside_the_caveat_declares_nothing(self) -> None:
        assert declares_non_comparability("These two things are not comparable.") is None
        assert declares_non_comparability("Population caveat: patient cash excluded.") is None

    def test_the_base_pack_declares_it_on_exactly_the_metrics_that_mean_it(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """Matched on the ASSERTION, never on a metric list: a hard-coded
        list would go stale silently, which is FN-4 one layer up."""
        declared = {
            contract.id
            for contract in pack_port.snapshot.metric_contracts
            if declared_non_comparability(pack_port, contract.id) is not None
        }

        assert declared == {"net_collection_rate", "first_pass_yield"}
        # The metric the round-6 analyst tested, where the panel guard fires
        # correctly and loudly, must be unaffected.
        assert declared_non_comparability(pack_port, "denial_rate") is None


class TestThePremiseVerdictConsultsComparability:
    def test_the_vc_repro_is_unverifiable_rather_than_confirmed(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        spec = _asserting(
            make_spec(
                measures=("net_collection_rate",),
                comparison=ComparisonKind.PRIOR_PERIOD,
                watermark=WATERMARK,
            ),
            AskedDirection.DECREASE,
        )

        premise = _check(_ncr_frame(), spec, pack_port)

        assert premise is not None
        assert premise.holds is False
        assert premise.unverifiable is True
        assert premise.not_comparable is not None
        assert premise.not_comparable.measure == "net_collection_rate"
        # The two signal legs are correctly silent: this is the class they
        # are structurally blind to.
        assert premise.bounded is False
        assert premise.immature is None

    def test_the_sentence_names_the_reason_in_the_packs_own_words(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        spec = _asserting(
            make_spec(
                measures=("net_collection_rate",),
                comparison=ComparisonKind.PRIOR_PERIOD,
                watermark=WATERMARK,
            ),
            AskedDirection.DECREASE,
        )
        premise = _check(_ncr_frame(), spec, pack_port)
        assert premise is not None

        sentence = premise_verdict_sentence(premise, spec, comparison=None)

        assert "It cannot be checked here" in sentence
        assert "not comparable as levels" in sentence
        # The arithmetic OUT LOUD, like every other unverifiable arm: both
        # figures are real, and what cannot be done is DIFFERENCE them.
        assert "72.5% → 18.5%" in sentence
        assert "confirmed" not in sentence.casefold()

    async def test_the_published_finding_is_qualified_and_drops_the_magnitude(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        spec = _asserting(
            make_spec(
                measures=("net_collection_rate",),
                comparison=ComparisonKind.PRIOR_PERIOD,
                watermark=WATERMARK,
            ),
            AskedDirection.DECREASE,
        )
        service = EvaluateFindingsService(FakeReferentRegistryStore())

        result = await service.evaluate(
            plan=_premise_plan(),
            calculation=CalculationResult(
                frames=(("premise__compare", _ncr_frame()),), operations=()
            ),
            spec=spec,
            pack=pack_port,
            playbook=None,
            session_id="sess_fn4",
            investigation_id="inv_fn4",
            suppression_threshold=11,
        )

        lead = result.findings[0]
        assert lead.title.startswith("Premise cannot be verified:")
        assert lead.confidence == "qualified"
        assert ("premise_holds", False) in lead.values
        assert ("premise_unverifiable", True) in lead.values
        assert ("premise_unverifiable_reason", "contract_not_comparable") in lead.values
        assert ("premise_not_comparable_metric", "net_collection_rate") in lead.values
        assert any(w.startswith("premise_unverifiable:") for w in result.warnings)

    def test_a_metric_without_the_declaration_still_gets_a_real_verdict(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """The guard must not swallow the verdicts that are correct."""
        spec = _asserting(
            make_spec(
                measures=("denial_rate",),
                comparison=ComparisonKind.PRIOR_PERIOD,
                watermark=WATERMARK,
            ),
            AskedDirection.INCREASE,
        )
        frame = _compared_scalar_frame(
            "denial_rate",
            current=Decimal("0.128"),
            prior=Decimal("0.091"),
            numerator=1280,
            denominator=10000,
            prior_numerator=910,
            prior_denominator=10000,
            component_unit="count",
        )

        premise = _check(frame, spec, pack_port)

        assert premise is not None
        assert premise.unverifiable is False
        assert premise.holds is True


def _mismatched(spec: AnalysisSpec) -> AnalysisSpec:
    """The same spec, differenced against a quarter — the R4-16 shape."""
    quarter = AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 3, 31))
    comparison = Comparison(
        kind=ComparisonKind.CUSTOM,
        window=TimeWindow(basis=spec.context.window.basis, range=quarter),
    )
    return spec.with_context(replace(spec.context, comparison=comparison))


class TestThePremiseVerdictConsultsWindowLength:
    def test_an_additive_premise_over_unequal_windows_cannot_be_checked(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """``comparison.py`` already withholds the impact and qualifies
        every finding for this. The premise verdict was the one surface
        still saying "It happened" over a length ratio."""
        spec = _mismatched(
            _asserting(
                make_spec(measures=("cash_posted",), watermark=WATERMARK),
                AskedDirection.DECREASE,
            )
        )

        premise = _check(
            _money_premise_frame("cash_posted", current=8_812_843, prior=18_722_151),
            spec,
            pack_port,
        )

        assert premise is not None
        assert premise.unverifiable is True
        assert premise.length_mismatched is not None
        sentence = premise_verdict_sentence(premise, spec, comparison=None)
        assert "not the same length" in sentence

    def test_a_rate_premise_over_unequal_windows_is_unaffected(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """A ratio over a window does not scale with the window's length —
        the same per-unit rule the finding paths already apply. Qualifying
        it here would state a caution that is not true."""
        spec = _mismatched(
            _asserting(
                make_spec(measures=("denial_rate",), watermark=WATERMARK),
                AskedDirection.INCREASE,
            )
        )
        frame = _compared_scalar_frame(
            "denial_rate",
            current=Decimal("0.128"),
            prior=Decimal("0.091"),
            numerator=1280,
            denominator=10000,
            prior_numerator=910,
            prior_denominator=10000,
            component_unit="count",
        )

        premise = _check(frame, spec, pack_port)

        assert premise is not None
        assert premise.length_mismatched is None
        assert premise.unverifiable is False


class TestNothingSurvivesAtHighConfidenceOverANonComparableComparison:
    """The invariant the review asked for, stated as a property.

    "No finding whose warning set contains a caution-severity
    non-comparability caveat over its own comparison windows may serialize
    with confidence high." F2/F3/F4 of the live payload — State Medicaid
    83.2% → 11.2%, Veritas 77.9% → 9.2% — were all ``direct``/``high``.
    """

    @staticmethod
    def _guard(pack: PackSnapshotPort, frames, findings):  # type: ignore[no-untyped-def]
        warnings: list[str] = []
        result = SubmitTurnService._guard_declared_comparability(
            SimpleNamespace(_pack=pack),  # type: ignore[arg-type]
            FindingsResult(findings=findings, referents=()),
            CalculationResult(frames=frames, operations=()),
            warnings,
        )
        return result, warnings

    @staticmethod
    def _finding(referent: str, measure: str, confidence: str) -> Finding:
        return Finding(
            referent=ReferentId(value=referent, kind=ReferentKind.FINDING),
            title=f"{measure} cell",
            statement="cell",
            metric_refs=(MetricRef(measure),),
            values=(),
            grade=EvidenceGrade.DIRECT,
            confidence=confidence,
        )

    def test_every_finding_drops_out_of_high_and_the_reason_is_stated(
        self, pack_port: PackSnapshotPort
    ) -> None:
        findings = tuple(
            self._finding(f"F{i}", "net_collection_rate", "high") for i in range(1, 5)
        )

        result, warnings = self._guard(
            pack_port, (("main__compare", _ncr_frame()),), findings
        )

        assert [f.confidence for f in result.findings] == ["qualified"] * 4
        assert any(w.startswith("not_comparable_windows:") for w in warnings)
        assert "net_collection_rate" in warnings[0]

    def test_a_stronger_caveat_is_never_raised(self, pack_port: PackSnapshotPort) -> None:
        findings = (self._finding("F1", "net_collection_rate", "low"),)

        result, _ = self._guard(pack_port, (("main__compare", _ncr_frame()),), findings)

        assert result.findings[0].confidence == "low"

    def test_reading_one_window_is_not_a_comparison_and_is_untouched(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """A caveat about COMPARING two windows says nothing about reading
        one, and demoting an uncompared level for it would be a caution
        that is not true."""
        level = EvidenceFrame(
            schema=FrameSchema(
                (
                    FrameColumn(
                        "net_collection_rate", MetricRef("net_collection_rate"), 2, "ratio"
                    ),
                )
            ),
            rows=((NCR_CURRENT,),),
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )
        findings = (self._finding("F1", "net_collection_rate", "high"),)

        result, warnings = self._guard(pack_port, (("main", level),), findings)

        assert result.findings[0].confidence == "high"
        assert warnings == []

    def test_the_compared_measure_is_read_off_the_frame_not_the_components(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """``<m>__num`` also carries a ``__prior`` sibling and is not a
        metric anybody wrote a caveat about."""
        assert [v.measure for v in declared_non_comparabilities(
            pack_port, (("main__compare", _ncr_frame()),)
        )] == ["net_collection_rate"]


# ---------------------------------------------------------------------------
# FN-10 — a rate breakdown recomposes to the rate it was cut out of


def _investigation(
    spec: AnalysisSpec, findings: tuple[Finding, ...], *, dimensions: tuple[str, ...] = ()
) -> Investigation:
    return Investigation(
        id="inv_parent",
        session_id="sess",
        parent_id=None,
        turn_id="turn_parent",
        turn_class=TurnClass.NEW_INVESTIGATION,
        question="What was our denial rate in July 2026?",
        spec=spec,
        plan_hash="hash",
        status=InvestigationStatus.COMPLETE,
        findings=findings,
        created_at=datetime.now(UTC),
    )


def _rate_whole(rate: str = "0.128", referent: str = "F1") -> Finding:
    return Finding(
        referent=ReferentId(value=referent, kind=ReferentKind.FINDING),
        title=f"denial rate: {float(rate):.1%} (2026-07-01..2026-07-31)",
        statement="denial rate over July 2026.",
        metric_refs=(MetricRef("denial_rate"),),
        values=(("denial_rate", Decimal(rate)),),
        grade=EvidenceGrade.DIRECT,
        impact_cents=None,
        confidence="high",
    )


def _rate_cells(*cells: tuple[str, int | None, int]) -> EvidenceFrame:
    """One row per payer: ``(label, numerator or None, denominator)``.

    ``None`` is a numerator the §15 policy withheld — the cell keeps its
    population and loses its contribution, which is the case the interval
    exists for.
    """
    columns = (
        FrameColumn("payer", DimensionRef("payer")),
        FrameColumn("denial_rate", MetricRef("denial_rate"), 2, "ratio"),
        FrameColumn("denial_rate__num", MetricRef("denial_rate"), 2, "count"),
        FrameColumn("denial_rate__den", MetricRef("denial_rate"), 2, "count"),
    )
    rows = tuple(
        (label, None if num is None else Decimal(num) / Decimal(den), num, den)
        for label, num, den in cells
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestARateBreakdownRecomposesToItsParent:
    def test_the_cells_recombine_through_their_denominators_and_agree(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """1,280 denied over 10,000 adjudicated across four payers IS the
        parent's 12.8% — weighted by each cell's own population, which is
        what a rate breakdown means and what summing three percentages
        never could."""
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(
                (
                    "main",
                    _rate_cells(
                        ("Atlas Commercial", 295, 1000),
                        ("Veritas Health", 229, 1000),
                        ("Pinnacle", 188, 1000),
                        ("State Medicaid MCO", 568, 7000),
                    ),
                ),
            ),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None
        assert result.passed is True
        assert result.summary.startswith("status=passed; scope=breakdown (rate recomposition);")
        assert "parent F1=12.8%" in result.summary
        assert "child recomposed=12.8%" in result.summary
        assert "1,280/10,000" in result.summary

    def test_cells_that_do_not_recompose_to_the_parent_fail_loudly(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _rate_cells(("Atlas", 295, 1000), ("Veritas", 229, 1000))),),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None
        assert result.passed is False
        assert result.summary.startswith("status=failed;")
        assert "delta=+" in result.summary  # signed: the direction of the gap

    def test_a_withheld_numerator_is_an_interval_not_a_disagreement(
        self, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """A cell the small-cell policy silenced keeps its population and
        loses its contribution. The parent still sits inside what those
        cells could be, so nothing disagrees — and calling that ``failed``
        would publish a conflict between two correct figures."""
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole("0.135"),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(
                (
                    "main",
                    _rate_cells(
                        ("Atlas Commercial", 1280, 10000),
                        ("Tiny Plan", None, 500),
                    ),
                ),
            ),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None
        assert result.passed is True
        # The grammar's own third state: not a point tie-out, not a
        # disagreement — the §15 policy standing between the two figures.
        assert result.summary.startswith("status=passed_with_suppression;")
        assert "withheld=1 cell(s)" in result.summary
        assert "12.2%..17.0%" in result.summary
        assert "the gap is the suppression and not a disagreement" in result.summary

    def test_a_clamped_ceiling_is_not_summed_as_a_numerator(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """The round-5 A-02a rule, one layer up — and a live regression the
        first cut of this fix shipped.

        The §15 policy publishes a small numerator as ``threshold - 1``
        rather than dropping the cell, so a bounded cell arrives carrying
        the integer 10 and reads exactly like a measurement. Live, 12 payer
        cells summed to 208/1,544 = 13.5% against a parent of 12.8% and the
        seam reported ``RECONCILIATION_FAILED`` about a gap that was
        entirely the suppression policy's.
        """
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        cells = _rate_cells(
            ("Atlas Commercial", 29, 355),
            ("Bluestone Mutual", 21, 129),
            ("Meridian Health", 29, 223),
            ("Northbridge Commercial", 12, 140),
            ("Pinnacle Health Plan", 22, 96),
            ("Silverline Medicare Advantage", 22, 117),
            ("State Medicaid", 15, 95),
            ("State Medicaid MCO", 18, 61),
            # The four the live payload published as ceilings: a numerator
            # under the threshold, clamped to threshold - 1.
            ("Federal Medicare", 10, 214),
            ("Lakewood Medicaid MCO", 10, 48),
            ("Summit Peak Medicare Advantage", 10, 53),
            ("Veritas Comp Fund", 10, 13),
        )
        calculation = CalculationResult(frames=(("main", cells),), operations=())

        summed = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )
        guarded = containment_reconciliation(
            parent,
            calculation,
            (SetDimensions((DimensionRef("payer"),)),),
            child,
            suppression_threshold=11,
        )

        # Without the threshold the ceilings look like measurements…
        assert summed is not None and summed.passed is False
        assert "208/1,544" in summed.summary
        # …and with it, the eight measured cells recompose and the four
        # ceilings contribute their POPULATION and a cap the policy itself
        # supplies, which the parent's 12.8% sits inside.
        assert guarded is not None and guarded.passed is True
        assert guarded.summary.startswith("status=passed_with_suppression;")
        assert "168/1,216" in guarded.summary
        assert "withheld=4 cell(s) over 328 population" in guarded.summary
        assert "10.9%..13.5%" in guarded.summary

    def test_a_parent_outside_that_interval_still_fails(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole("0.400"),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(
                (
                    "main",
                    _rate_cells(
                        ("Atlas Commercial", 1280, 10000),
                        ("Tiny Plan", None, 500),
                    ),
                ),
            ),
            operations=(),
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None and result.passed is False

    def test_a_rate_child_with_no_parent_rate_claims_nothing(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """Silence is the honest outcome, not a fabricated tie-out."""
        parent = _investigation(make_spec(measures=("denial_rate",)), ())
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _rate_cells(("Atlas", 295, 1000))),), operations=()
        )

        assert (
            containment_reconciliation(
                parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
            )
            is None
        )

    def test_a_drill_of_a_named_rate_finding_reconciles_too(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("carc",))
        calculation = CalculationResult(
            frames=(("main", _rate_cells(("CO / 16", 1280, 10000))),), operations=()
        )

        result = containment_reconciliation(
            parent,
            calculation,
            (DrillInto(ReferentId(value="F1", kind=ReferentKind.FINDING)),),
            child,
        )

        assert result is not None and result.passed is True
        assert "scope=containment (rate recomposition)" in result.summary


class TestTheChildAnswerCarriesTheParentLevel:
    def test_a_rate_breakdown_states_the_whole_it_descends_from(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        """"A reader who lands on the breakdown comes away believing denial
        rates run 19-29%." The 12.8% is now on the child, as a mandatory
        disclosure rather than as a line in a verdict."""
        parent = _investigation(make_spec(measures=("denial_rate",)), (_rate_whole(),))
        child = make_spec(measures=("denial_rate",), dimensions=("payer",))
        calculation = CalculationResult(
            frames=(("main", _rate_cells(("Atlas", 1280, 10000))),), operations=()
        )

        result = containment_reconciliation(
            parent, calculation, (SetDimensions((DimensionRef("payer"),)),), child
        )

        assert result is not None and result.anchor is not None
        assert result.anchor.startswith("parent_level:")
        assert "12.8% (F1)" in result.anchor
        assert "denial rate" in result.anchor
        # How they recombine, said explicitly: three percentages do not add.
        assert "through their own denominators, not by addition" in result.anchor

    def test_a_money_breakdown_carries_the_same_anchor(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        money_whole = Finding(
            referent=ReferentId(value="F1", kind=ReferentKind.FINDING),
            title="denied dollars: $1,193,126.92",
            statement="denied dollars over July 2026.",
            metric_refs=(MetricRef("denied_dollars"),),
            values=(("denied_dollars", 119312692),),
            grade=EvidenceGrade.DIRECT,
            impact_cents=119312692,
            confidence="high",
        )
        parent = _investigation(make_spec(measures=("denied_dollars",)), (money_whole,))
        child = make_spec(measures=("denied_dollars",), dimensions=("payer",))
        frame = EvidenceFrame(
            schema=FrameSchema(
                (
                    FrameColumn("payer", DimensionRef("payer")),
                    FrameColumn(
                        "denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"
                    ),
                )
            ),
            rows=(("Atlas", 119312692),),
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )

        result = containment_reconciliation(
            parent,
            CalculationResult(frames=(("main", frame),), operations=()),
            (SetDimensions((DimensionRef("payer"),)),),
            child,
        )

        assert result is not None and result.anchor is not None
        assert "$1,193,126.92 (F1)" in result.anchor
        assert "by addition" in result.anchor

    def test_the_disclosure_code_is_one_the_narrative_may_not_drop(self) -> None:
        from revi_presentation.narrative import MANDATORY_DISCLOSURE_CODES, recovered_code

        assert "PARENT_LEVEL" in MANDATORY_DISCLOSURE_CODES
        assert "NOT_COMPARABLE_WINDOWS" in MANDATORY_DISCLOSURE_CODES
        # The engine's prefix convention carries a brand-new family through
        # an API warning table that has not learned its name yet.
        assert recovered_code("UNCLASSIFIED", "parent_level: …") == "PARENT_LEVEL"
        assert (
            recovered_code("UNCLASSIFIED", "not_comparable_windows: …")
            == "NOT_COMPARABLE_WINDOWS"
        )


# ---------------------------------------------------------------------------
# FN-14 — the unit token is said once


def _token_count(text: str, token: str) -> int:
    return len(re.findall(rf"(?<!\w){re.escape(token)}(?!\w)", text.casefold()))


class TestAUnitIsSaidOnce:
    @pytest.mark.parametrize(
        ("unit", "value", "label", "expected"),
        [
            # The defect, verbatim, in all four display-name shapes the base
            # pack actually contains.
            ("days", Decimal("179.5"), "days in ar", "179.5 days in ar"),
            ("days", Decimal("179.5"), "bill lag days", "179.5 bill lag days"),
            ("days", Decimal("179.5"), "avg days to pay", "179.5 avg days to pay"),
            ("days", Decimal("179.5"), "days", "179.5 days"),
            # A label that never mentions the unit keeps the suffix.
            ("days", Decimal("179.5"), "ar aging", "179.5 days ar aging"),
            # Money, ratio and count attach their unit to the digits, so
            # there is nothing to collide and nothing is touched.
            ("money_cents", 419942121, "denied dollars", "$4,199,421.21 denied dollars"),
            ("ratio", Decimal("0.128"), "denial rate", "12.8% denial rate"),
            ("count", 1204, "appeal volume", "1,204 appeal volume"),
            (None, Decimal("1.5"), "unknown thing", "1.5 unknown thing"),
        ],
    )
    def test_every_unit_kind_by_every_display_name_shape(
        self, unit: str | None, value: object, label: str, expected: str
    ) -> None:
        assert measure_phrase(format_value(value, unit), label, unit) == expected  # type: ignore[arg-type]

    def test_the_unit_word_is_derived_from_the_renderer_not_tabulated(self) -> None:
        """So the two can never drift: whatever ``format_value`` appends
        after a space IS the token a value of that unit carries."""
        assert unit_word("days") == "days"
        assert unit_word("money_cents") is None
        assert unit_word("ratio") is None
        assert unit_word("count") is None
        assert unit_word(None) is None

    def test_no_metric_in_the_base_pack_says_its_unit_twice(
        self, pack_port: PackSnapshotPort
    ) -> None:
        """The contract-level assertion the review asked for, swept over
        every metric against its own declared unit."""
        for contract in pack_port.snapshot.metric_contracts:
            unit = contract.unit.value
            token = unit_word(unit)
            if token is None:
                continue
            label = metric_label(contract.id)
            phrase = measure_phrase(format_value(Decimal("179.5"), unit), label, unit)
            assert _token_count(phrase, token) == 1, f"{contract.id}: {phrase!r}"


class TestTheGroupedTitleIsTheSurfaceThatBroke:
    """The ungrouped scalar path renders correctly ("days in ar: 159.4
    days"), so the defect is specific to grouped titles — where the figure
    and the measure name are juxtaposed."""

    @staticmethod
    def _ranked(measure: str, unit: str, value: Decimal | int) -> EvidenceFrame:
        return EvidenceFrame(
            schema=FrameSchema(
                (
                    FrameColumn("payer", DimensionRef("payer")),
                    FrameColumn(measure, MetricRef(measure), 1, unit),
                    FrameColumn(f"{measure}__rank", MetricRef(measure), 1, "count"),
                )
            ),
            rows=(("Atlas Commercial", value, 1),),
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="main", probe_hash="h" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )

    @staticmethod
    def _plan(measure: str) -> InvestigationPlan:
        return InvestigationPlan(
            nodes=(),
            transforms=TransformPlan(
                steps=(
                    TransformPlanStep(
                        id="r", operator="rank", inputs=("main",), args=(("by", measure),)
                    ),
                )
            ),
        )

    @pytest.mark.parametrize(
        "measure", ["days_in_ar", "bill_lag_days", "charge_lag_days", "avg_days_to_pay"]
    )
    async def test_a_grouped_days_title_says_days_once(
        self, measure: str, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=(measure,), dimensions=("payer",), watermark=WATERMARK)
        service = EvaluateFindingsService(FakeReferentRegistryStore())

        result = await service.evaluate(
            plan=self._plan(measure),
            calculation=CalculationResult(
                frames=(("r", self._ranked(measure, "days", Decimal("179.5"))),), operations=()
            ),
            spec=spec,
            pack=pack_port,
            playbook=None,
            session_id="sess_fn14",
            investigation_id="inv_fn14",
        )

        [finding] = result.findings
        assert "days days" not in finding.title
        assert _token_count(finding.title, "days") == 1, finding.title
        assert "179.5" in finding.title
        # The registered referent's label is the title, and it is what a
        # Rounds tile headline is built from.
        assert result.referents[0].label == finding.title
