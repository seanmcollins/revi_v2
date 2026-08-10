"""Comparability as the third leg of the premise read.

``verify_premise`` learned to read bounded endpoints and adjudicated panel
share — both signals measured off the frame. Whether two windows may be
differenced *at all* is a third question, and the answer was already in the
metric contract's governed ``Population caveat:`` on a surface the verdict
never read: a net-collection-rate premise published "Premise confirmed …
72.5% → 18.5%" at high confidence beside its own caveat saying two windows
of unequal maturity are not comparable as levels. The panel guard cannot
see it — that metric's denominator is dollars, not adjudicated records.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

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
from revi_investigation.application.submit_turn import SubmitTurnService
from revi_investigation.domain.context import AnalysisSpec, AskedDirection
from revi_investigation.domain.records import Finding
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef, ReferentId, ReferentKind
from revi_kernel.scope import AbsoluteRange, Comparison, ComparisonKind, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import FakeReferentRegistryStore

WATERMARK = DataWatermark(
    id="wm_007", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)

#: The live figures the defect was found on.
NCR_CURRENT = Decimal("0.185")
NCR_PRIOR = Decimal("0.725")


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
        # architectural point of this suite.
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
        list would go stale silently, which is this defect one layer up."""
        declared = {
            contract.id
            for contract in pack_port.snapshot.metric_contracts
            if declared_non_comparability(pack_port, contract.id) is not None
        }

        assert declared == {"net_collection_rate", "first_pass_yield"}
        # The metric where the panel guard fires correctly and loudly must
        # be unaffected.
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
    """The same spec, differenced against a quarter — the length-mismatch
    shape."""
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
    """The invariant, stated as a property: no finding whose warning set
    contains a caution-severity non-comparability caveat over its own
    comparison windows may serialize with confidence high. On the live
    payload every such finding was ``direct``/``high``.
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
