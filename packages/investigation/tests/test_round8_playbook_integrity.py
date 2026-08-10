"""Playbook path integrity — the analyst's round-8 gate G3 (FIX-9).

Three live repros, one surface:

* **(1) silent family drops.** "Build me a payer scorecard for Pinnacle
  Health Plan for the last full quarter, I have a JOC with them next week"
  ran six probe families, got ``grade: direct`` rows from every one, and
  published ZERO findings. The whole narrative: "This turn published no
  finding, and here is why. 1 ranked row(s) on 'denied_dollars', and every
  one was zero or suppressed." Five families were read and never mentioned,
  because ``probe_families_empty`` returned early on a turn with no
  findings — the one warning whose entire job is to stop exactly this.
* **(2) the pivot dependency.** The same turn recorded
  ``TRANSFORM_NOT_EXECUTABLE: transform 'pivot' is not executable on this
  milestone's engine; recorded and skipped`` at severity INFO, and rendered
  four one-row charts beside "no finding". The pivot is what makes a
  scorecard a scorecard.
* **(3) the maturity guard does not travel.** "We have a denial spike.
  Investigate it." → ``PREMISE_FALSE``: "denied dollars fell $770,151.59
  (39.5%)", computed over 2026-06-08..2026-08-02 — the least settled window
  in the load. Minutes earlier, on the direct path, the same engine refused
  the equivalent comparison with "the two windows are not equally settled
  (1,544 adjudicated record(s) against 5,723, 27.0%) … Ask again once the
  thinner side matures."
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.findings import verify_premise
from revi_investigation.application.planning import (
    ANSWERING_TRANSFORMS,
    BuildInvestigationPlanService,
    InvestigationPlan,
    TransformPlan,
    TransformPlanStep,
)
from revi_investigation.application.submit_turn import probe_families_empty_warning
from revi_investigation.application.validation import PlanValidationService
from revi_investigation.application.window_maturity import WindowMaturity
from revi_investigation.domain.context import AskedDirection
from revi_kernel.errors import UnsupportedConceptError
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef
from revi_kernel.scope import AbsoluteRange
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import StubAnalyticalRepository

if TYPE_CHECKING:
    from tests.conftest import SpecFactory

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


@pytest.fixture
def planner(
    pack_port: PackSnapshotPort, catalog: CatalogSnapshot
) -> BuildInvestigationPlanService:
    return BuildInvestigationPlanService(pack_port, catalog)


class TestAnAnsweringTransformThisEngineCannotRun:
    """(2) Refuse the playbook by name, or answer it. Never half."""

    def test_the_payer_scorecard_refuses_and_names_the_transform(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(dimensions=("payer",), watermark=WATERMARK)

        with pytest.raises(UnsupportedConceptError) as raised:
            planner.build(spec, playbook_id="payer_scorecard", window_explicit=True)

        assert raised.value.details["transform"] == "pivot"
        assert raised.value.details["playbook"] == "payer_scorecard"
        assert "pivot" in str(raised.value)

    def test_the_cash_outlook_refuses_its_forecast_rather_than_answering_a_total(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        """"Will my cash increase next month?" handed over $6,355,211.10 of
        cash posted over the playbook's own window, with the words
        "forecast" and "cannot" nowhere in the response (FIX-12(c))."""
        spec = make_spec(watermark=WATERMARK)

        with pytest.raises(UnsupportedConceptError) as raised:
            planner.build(spec, playbook_id="cash_outlook", window_explicit=False)

        assert raised.value.details["transform"] == "project_lagged_realization"

    def test_an_enrichment_transform_is_still_only_a_note(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        """``decompose`` costs a column, not the question — cash_decline
        still answers, with the skip stated as a plan note."""
        spec = make_spec(dimensions=("payer",), watermark=WATERMARK)

        plan = planner.build(spec, playbook_id="cash_decline", window_explicit=True)

        assert plan.nodes
        assert any("decompose" in note for note in plan.notes)

    def test_the_refusal_offers_what_the_pack_can_answer(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: SpecFactory
    ) -> None:
        """A refusal with no way onward is the dead end this platform
        refuses everywhere else."""
        planner = BuildInvestigationPlanService(pack_port, catalog)
        repository = StubAnalyticalRepository(watermarks=(WATERMARK,))
        validator = PlanValidationService(catalog, pack_port, repository)
        spec = make_spec(dimensions=("payer",), watermark=WATERMARK)
        with pytest.raises(UnsupportedConceptError) as raised:
            planner.build(spec, playbook_id="payer_scorecard", window_explicit=True)

        clarification = validator.clarification_for(raised.value, spec)

        assert clarification is not None
        assert "pivot" in clarification.question
        assert len(clarification.options) >= 2
        assert all(binding.kind == "metric_cut" for binding in clarification.bindings)
        assert {b.option for b in clarification.bindings} == set(clarification.options)

    def test_the_two_transforms_are_named_once(self) -> None:
        """The list is a fact about this engine's operator set. When either
        is implemented it comes off the list and the playbooks answer."""
        assert frozenset({"pivot", "project_lagged_realization"}) == ANSWERING_TRANSFORMS


def _probe_frame(metric: str, rows: int) -> EvidenceFrame:
    columns = (FrameColumn(metric, MetricRef(metric), 1, "money_cents"),)
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=tuple((Decimal(100 + i),) for i in range(rows)),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="p" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestEveryFamilyThatWasReadIsAccountedFor:
    """(1) A family publishes a finding or is NAMED. No third outcome."""

    def _plan_and_probes(self, metrics: tuple[str, ...]) -> tuple[object, tuple[object, ...]]:
        from revi_investigation.application.execution import ExecutedProbe
        from revi_investigation.application.planning import ProbeNode
        from revi_kernel.filters import EMPTY_SCOPE
        from revi_kernel.probes import AggregationProbe
        from revi_kernel.refs import POST, EntityGrain, Grain
        from revi_kernel.scope import RangeMode, RelativeRange, TimeUnit, TimeWindow, resolve_window

        window = resolve_window(
            RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS),
            date(2026, 8, 3),
            basis=POST,
        )
        assert isinstance(window, TimeWindow)
        nodes = []
        executed = []
        for index, metric in enumerate(metrics):
            probe = AggregationProbe(
                measures=(MetricRef(metric),),
                dimensions=(),
                scope=EMPTY_SCOPE,
                window=window,
                grain=Grain(EntityGrain.CLAIM),
            )
            node = ProbeNode(id=f"probe_{index}", probe=probe, purpose="test")
            nodes.append(node)
            executed.append(
                ExecutedProbe(
                    node_id=node.id, frame=_probe_frame(metric, 1), cache_hit=False
                )
            )
        plan = InvestigationPlan(nodes=tuple(nodes), transforms=TransformPlan(steps=()))
        return plan, tuple(executed)

    @staticmethod
    def _validated(plan: object) -> object:
        from revi_investigation.application.validation import ValidatedPlan

        return ValidatedPlan(plan=plan, grades=(), warnings=())  # type: ignore[arg-type]

    def test_six_families_read_and_nothing_published_names_all_six(self) -> None:
        metrics = (
            "denied_dollars",
            "cash_posted",
            "ar_balance",
            "charges",
            "credit_balance_dollars",
            "patient_responsibility_dollars",
        )
        plan, executed = self._plan_and_probes(metrics)
        validated = self._validated(plan)

        warning = probe_families_empty_warning(validated, executed, ())  # type: ignore[arg-type]

        assert warning is not None
        for metric in metrics:
            assert metric in warning
        assert warning.startswith("probe_families_empty: 6 metric famil(ies)")

    def test_one_family_and_nothing_published_leaves_it_to_the_emptiness_fact(self) -> None:
        """Two statements of the same nothing is one too many — that half
        of the old rule was right and is kept."""
        plan, executed = self._plan_and_probes(("denied_dollars",))
        validated = self._validated(plan)

        assert probe_families_empty_warning(validated, executed, ()) is None  # type: ignore[arg-type]


def _money_compare_frame(current: int, prior: int) -> EvidenceFrame:
    """The premise probe's own frame: an ADDITIVE money measure, which is
    why the panel rule cannot see it — there is no denominator to count."""
    measure = "denied_dollars"
    columns = (
        FrameColumn(measure, MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 1, "money_cents"),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 1, "ratio"),
    )
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=((current, prior, current - prior, Decimal("-0.395")),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="premise", probe_hash="p" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestTheMaturityGuardTravels:
    """(3) The same refusal on both paths, or the product contradicts
    itself about the same metric three minutes apart."""

    def _spec(self, make_spec: SpecFactory) -> object:
        return replace(
            make_spec(measures=("denied_dollars",), watermark=WATERMARK),
            direction=AskedDirection.INCREASE,
            direction_asserted=True,
        )

    def _plan(self) -> InvestigationPlan:
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

    def test_an_unsettled_window_cannot_refute_a_premise(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        spec = self._spec(make_spec)
        calculation = CalculationResult(
            frames=(("main__compare", _money_compare_frame(118_052_308, 195_067_467)),),
            operations=(),
        )
        window = spec.context.window.range  # type: ignore[attr-defined]
        maturity = {
            window: WindowMaturity(
                yardstick="clean_claim_rate",
                population=1_544,
                expected=5_723,
                window=window,
                warning="adjudication_incomplete: …",
            )
        }

        premise = verify_premise(
            self._plan(),
            calculation,
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
            window_maturity=maturity,
        )

        assert premise is not None
        assert premise.window_immature is not None
        assert premise.unverifiable is True
        assert premise.holds is False

    def test_a_settled_window_still_refutes(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """The guard may only ever downgrade a verdict it has a reason to
        doubt — a settled window still gets a straight answer."""
        spec = self._spec(make_spec)
        calculation = CalculationResult(
            frames=(("main__compare", _money_compare_frame(118_052_308, 195_067_467)),),
            operations=(),
        )

        premise = verify_premise(
            self._plan(),
            calculation,
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
            window_maturity={},
        )

        assert premise is not None
        assert premise.window_immature is None
        assert premise.unverifiable is False

    def test_the_verdict_sentence_says_what_the_direct_path_says(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        from revi_investigation.application.findings import premise_verdict_sentence

        spec = self._spec(make_spec)
        calculation = CalculationResult(
            frames=(("main__compare", _money_compare_frame(118_052_308, 195_067_467)),),
            operations=(),
        )
        window = spec.context.window.range  # type: ignore[attr-defined]
        premise = verify_premise(
            self._plan(),
            calculation,
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
            window_maturity={
                window: WindowMaturity(
                    yardstick="clean_claim_rate",
                    population=1_544,
                    expected=5_723,
                    window=window,
                    warning="adjudication_incomplete: …",
                )
            },
        )
        assert premise is not None

        sentence = premise_verdict_sentence(premise, spec, comparison=None)  # type: ignore[arg-type]

        assert "cannot be checked yet" in sentence
        assert "1,544" in sentence and "5,723" in sentence
        assert "did not happen" not in sentence

    def test_the_window_judged_is_the_one_the_probe_read(
        self, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """A verdict keyed on the announced window is the defect: the
        playbook probe read 2026-06-08..2026-08-02 and the header said
        July against June."""
        spec = self._spec(make_spec)
        calculation = CalculationResult(
            frames=(("main__compare", _money_compare_frame(118_052_308, 195_067_467)),),
            operations=(),
        )
        elsewhere = AbsoluteRange(start=date(2026, 6, 8), end=date(2026, 8, 2))

        premise = verify_premise(
            self._plan(),
            calculation,
            spec,  # type: ignore[arg-type]
            pack_port,
            premise_prefix="premise",
            window_maturity={
                elsewhere: WindowMaturity(
                    yardstick="clean_claim_rate",
                    population=1,
                    expected=100,
                    window=elsewhere,
                    warning="adjudication_incomplete: …",
                )
            },
        )

        assert premise is not None
        # This plan's frame carries no window of its own, so the spec's is
        # what was read — and a verdict about a period nothing measured
        # must not touch it.
        assert premise.window_immature is None
