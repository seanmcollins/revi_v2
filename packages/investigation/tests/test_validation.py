"""The §6.6 validation pass: every error code, warning, and grade path."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
)
from revi_investigation.application.validation import (
    PlanValidationService,
    ValidationLimits,
)
from revi_kernel.capabilities import RepositoryCapabilities
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.errors import (
    DateBasisInvalidError,
    GrainIncompatibleError,
    QueryBudgetExceededError,
    SourceCapabilityUnsupportedError,
    UnsupportedConceptError,
)
from revi_kernel.filters import EMPTY_SCOPE, InCohort, Predicate, PredicateOp
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import (
    DISCHARGE,
    SERVICE,
    DimensionRef,
    EntityGrain,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
)
from revi_kernel.scope import ComparisonKind
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import StubAnalyticalRepository

if TYPE_CHECKING:
    from conftest import SpecFactory

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _capabilities(**overrides: bool) -> RepositoryCapabilities:
    values: dict[str, object] = {
        "as_of_reads": True,
        "cohort_semijoin": True,
        "max_cohort_size": 100_000,
        "having_pushdown": True,
        "server_side_top_n": True,
    }
    values.update(overrides)
    return RepositoryCapabilities(**values)  # type: ignore[arg-type]


def _validator(
    catalog: CatalogSnapshot,
    pack_port: PackSnapshotPort,
    caps: RepositoryCapabilities | None = None,
    limits: ValidationLimits | None = None,
) -> PlanValidationService:
    repository = StubAnalyticalRepository(
        watermarks=(WATERMARK,),
        repository_capabilities=caps if caps is not None else _capabilities(),
    )
    return PlanValidationService(
        catalog, pack_port, repository, limits if limits is not None else ValidationLimits()
    )


@pytest.fixture
def planner(pack_port: PackSnapshotPort) -> BuildInvestigationPlanService:
    return BuildInvestigationPlanService(pack_port)


@pytest.fixture
def validator(catalog: CatalogSnapshot, pack_port: PackSnapshotPort) -> PlanValidationService:
    return _validator(catalog, pack_port)


class TestResolution:
    def test_unknown_dimension_is_unsupported_concept(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        spec = make_spec(measures=("cash_posted",), dimensions=("no_such_dimension",))
        with pytest.raises(UnsupportedConceptError):
            validator.validate(planner.build(spec), spec)

    def test_unanswerable_measures_prune_with_warning(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        # cash_decline's lag probe sums the probe-time-derived
        # payment_lag_days field, which this catalog cannot answer yet
        spec = make_spec(dimensions=("payer",), comparison=ComparisonKind.PRIOR_PERIOD)
        plan = planner.build(spec, playbook_id="cash_decline", window_explicit=True)
        validated = validator.validate(plan, spec)
        ids = [node.id for node in validated.plan.nodes]
        assert "lag_distribution_compare" not in ids
        assert "lag_distribution_compare__prior" not in ids
        assert "cash_by_payer" in ids
        assert any("lag_distribution_compare" in w and "omitted" in w for w in validated.warnings)
        for step in validated.plan.transforms.steps:
            assert all(not inp.startswith("lag_distribution_compare") for inp in step.inputs)

    def test_fully_unanswerable_plan_is_unsupported(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        spec = make_spec(measures=("avg_days_to_pay",))
        with pytest.raises(UnsupportedConceptError):
            validator.validate(planner.build(spec), spec)

    def test_certified_chain_grades_direct(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        spec = make_spec(measures=("claim_volume",), dimensions=("payer",))
        validated = validator.validate(planner.build(spec), spec)
        assert validated.grade_of("main") is EvidenceGrade.DIRECT

    def test_uncertified_scope_dimension_downgrades_to_discovery(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        # rarc_synthetic is deliberately uncertified (design §2.3): scoping
        # over it downgrades the whole chain to discovery grade
        scope = Predicate(DimensionRef("rarc_synthetic"), PredicateOp.EQ, ("N1",))
        spec = make_spec(measures=("appeal_overturn_rate",), scope=scope)
        validated = validator.validate(planner.build(spec), spec)
        assert validated.grade_of("main") is EvidenceGrade.DISCOVERY


class TestGrainAndBasis:
    def test_denial_rate_by_carc_is_grain_incompatible(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        # the pack deliberately excludes carc from denial_rate's scope
        # dimensions: a claim-grain rate cut by a line-level code lies
        spec = make_spec(measures=("denial_rate",), dimensions=("carc",))
        with pytest.raises(GrainIncompatibleError) as excinfo:
            validator.validate(planner.build(spec), spec)
        assert excinfo.value.details["dimension"] == "carc"

    def test_denial_rate_by_payer_is_legal(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        spec = make_spec(measures=("denial_rate",), dimensions=("payer",))
        validated = validator.validate(planner.build(spec), spec)
        assert validated.grade_of("main") is EvidenceGrade.DIRECT

    def test_probe_grain_must_match_contract_grain(
        self, validator: PlanValidationService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("claim_volume",))
        probe = AggregationProbe(
            measures=(MetricRef("claim_volume"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            window=spec.context.window,
            grain=Grain(EntityGrain.LINE),  # claim_volume is CLAIM-grain
        )
        plan = InvestigationPlan(
            nodes=(ProbeNode(id="main", probe=probe, purpose="test"),),
            transforms=TransformPlan(),
        )
        with pytest.raises(GrainIncompatibleError):
            validator.validate(plan, spec)

    def test_illegal_basis_is_date_basis_invalid(
        self, validator: PlanValidationService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("cash_posted",))
        probe = AggregationProbe(
            measures=(MetricRef("cash_posted"),),
            dimensions=(),
            scope=EMPTY_SCOPE,
            window=replace(spec.context.window, basis=DISCHARGE),
            grain=Grain(EntityGrain.TRANSACTION),
        )
        plan = InvestigationPlan(
            nodes=(ProbeNode(id="main", probe=probe, purpose="test"),),
            transforms=TransformPlan(),
        )
        with pytest.raises(DateBasisInvalidError):
            validator.validate(plan, spec)

    def test_alternate_basis_warns(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        spec = make_spec(measures=("cash_posted",), basis=SERVICE)
        validated = validator.validate(planner.build(spec), spec)
        assert any(w.startswith("alternate_basis_used") for w in validated.warnings)


class TestBudgetsAndWarnings:
    def test_high_cardinality_without_limit_exceeds_budget(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        # payer(12) x plan(30) x facility(6) x service_line(10) = 21,600 cells
        spec = make_spec(
            measures=("cash_posted",),
            dimensions=("payer", "plan", "facility", "service_line"),
        )
        with pytest.raises(QueryBudgetExceededError):
            validator.validate(planner.build(spec), spec)

    def test_limit_turns_budget_error_into_truncation_warning(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        spec = make_spec(
            measures=("cash_posted",),
            dimensions=("payer", "plan", "facility", "service_line"),
            limit=25,
        )
        validated = validator.validate(planner.build(spec), spec)
        assert any("truncated to the top 25" in w for w in validated.warnings)

    def test_exclusion_intersection_warns(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        # denial_rate's numerator is Filtered on clean_claim — scoping by it
        # is the "denial rate for denied claims" confusion (§6.6 step 5)
        scope = Predicate(DimensionRef("clean_claim"), PredicateOp.EQ, (False,))
        spec = make_spec(measures=("denial_rate",), scope=scope)
        validated = validator.validate(planner.build(spec), spec)
        assert any("clean_claim" in w and "denial_rate" in w for w in validated.warnings)

    def test_suppression_threshold_noted(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        spec = make_spec(measures=("cash_posted",), dimensions=("payer",))
        validated = validator.validate(planner.build(spec), spec)
        assert any(w.startswith("suppression:") for w in validated.warnings)

    def test_probe_count_budget(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        planner: BuildInvestigationPlanService,
        make_spec: SpecFactory,
    ) -> None:
        tight = _validator(catalog, pack_port, limits=ValidationLimits(max_probes=1))
        spec = make_spec(measures=("cash_posted",), comparison=ComparisonKind.PRIOR_PERIOD)
        with pytest.raises(QueryBudgetExceededError):
            tight.validate(planner.build(spec), spec)


class TestCapabilities:
    def test_limit_needs_server_side_top_n(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        planner: BuildInvestigationPlanService,
        make_spec: SpecFactory,
    ) -> None:
        validator = _validator(catalog, pack_port, _capabilities(server_side_top_n=False))
        spec = make_spec(measures=("cash_posted",), dimensions=("payer",), limit=5)
        with pytest.raises(SourceCapabilityUnsupportedError) as excinfo:
            validator.validate(planner.build(spec), spec)
        assert excinfo.value.details["capability"] == "server_side_top_n"

    def test_cohort_scope_needs_cohort_semijoin(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        planner: BuildInvestigationPlanService,
        make_spec: SpecFactory,
    ) -> None:
        cohort = CohortRef(
            id="cohort_test",
            definition=CohortDefinition(entity=EntityGrain.CLAIM, scope=EMPTY_SCOPE),
            origin=ReferentId(value="F1", kind=ReferentKind.FINDING),
            size=10,
        )
        spec = make_spec(measures=("cash_posted",), scope=InCohort(cohort=cohort))
        validator = _validator(catalog, pack_port, _capabilities(cohort_semijoin=False))
        with pytest.raises(SourceCapabilityUnsupportedError) as excinfo:
            validator.validate(planner.build(spec), spec)
        assert excinfo.value.details["capability"] == "cohort_semijoin"
