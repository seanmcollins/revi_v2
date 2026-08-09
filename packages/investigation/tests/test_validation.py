"""The §6.6 validation pass: every error code, warning, and grade path."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING

import pytest

from revi_calculation_contracts.contract import MetricContract, MetricKind
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
    population_caveat,
)
from revi_kernel.capabilities import DerivedMeasure, RepositoryCapabilities
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
from revi_kernel.probes import AggregationProbe, ProbeShape
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
from revi_testing.fakes import MINIMAL_CAPABILITIES, StubAnalyticalRepository

if TYPE_CHECKING:
    from conftest import SpecFactory

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _capabilities(**overrides: object) -> RepositoryCapabilities:
    """A source with the retrieval mechanics and **no** advertised
    computation of its own — the §6.3 default, and the pre-negotiation
    behaviour every existing expectation in this module was written
    against. Tests that want a computing source say so."""
    return replace(MINIMAL_CAPABILITIES, **overrides)  # type: ignore[arg-type]


def _derived(field: str, entity: str, *shapes: ProbeShape) -> DerivedMeasure:
    return DerivedMeasure(field=field, entity=entity, shapes=frozenset(shapes))


class _PackWithOverrides:
    """The real pack with one or two contracts replaced.

    Used to state plan-time refusals that the shipped pack cannot express
    — a contract mis-authored at the wrong metric kind, or one whose
    allowed bases reach a date basis only one of its two entities binds.
    Delegates everything else, so nothing about the pack under test is a
    fixture of its own.
    """

    def __init__(self, inner: PackSnapshotPort, overrides: dict[str, MetricContract]) -> None:
        self._inner = inner
        self._overrides = overrides

    def metric(self, metric_id: str) -> MetricContract | None:
        override = self._overrides.get(metric_id)
        return override if override is not None else self._inner.metric(metric_id)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


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
def planner(
    pack_port: PackSnapshotPort, catalog: CatalogSnapshot
) -> BuildInvestigationPlanService:
    return BuildInvestigationPlanService(pack_port, catalog)


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
        # cash_decline's lag probe sums payment_lag_days, which is neither a
        # catalog measure nor a declared column — so it is answerable only
        # by a source that says it computes it, and this one advertises
        # nothing (``_capabilities``). Honest degradation, not assumption.
        spec = make_spec(dimensions=("payer",), comparison=ComparisonKind.PRIOR_PERIOD)
        plan = planner.build(spec, playbook_id="cash_decline", window_explicit=True)
        validated = validator.validate(plan, spec)
        ids = [node.id for node in validated.plan.nodes]
        assert "lag_distribution_compare" not in ids
        assert "lag_distribution_compare__prior" not in ids
        assert "cash_by_payer" in ids
        assert any("lag_distribution_compare" in w and "omitted" in w for w in validated.warnings)
        # the warning names the field and why, not a category
        assert any("payment_lag_days" in w for w in validated.warnings)
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

    def test_binding_strength_downgrades_per_concept(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        """The same certified field is direct evidence for one concept and
        only proxy evidence for another (design §5.5). The pack binds
        cob→carc at PROXY strength and denial→carc at DIRECT, so an
        identical probe must grade differently depending on what is being
        asked — otherwise a payer's reason code could certify a conclusion
        about coverage."""
        denial = make_spec(
            measures=("denied_dollars",), dimensions=("carc",), concepts=("denial",)
        )
        assert (
            validator.validate(planner.build(denial), denial).grade_of("main")
            is EvidenceGrade.DIRECT
        )

        cob = make_spec(measures=("denied_dollars",), dimensions=("carc",), concepts=("cob",))
        assert (
            validator.validate(planner.build(cob), cob).grade_of("main") is EvidenceGrade.PROXY
        )

    def test_unbound_fields_do_not_downgrade(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        # the pack declares no cob binding for `facility`: silence is not a
        # downgrade, only a declared weaker strength is
        spec = make_spec(measures=("claim_volume",), dimensions=("facility",), concepts=("cob",))
        validated = validator.validate(planner.build(spec), spec)
        assert validated.grade_of("main") is EvidenceGrade.DIRECT

    def test_binding_strength_reaches_the_contracts_own_fields(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        """Bindings name catalog *fields*, which a probe often touches only
        through a metric contract's internals — the cob_mismatch_claims
        contract filters on cob_mismatch_flag without ever exposing it as a
        dimension. Grading has to look inside the contract or the direct
        binding would never be found."""
        spec = make_spec(
            measures=("cob_mismatch_claims",), dimensions=("payer",), concepts=("cob",)
        )
        validated = validator.validate(planner.build(spec), spec)
        assert validated.grade_of("main") is EvidenceGrade.DIRECT


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

    def test_population_caveats_are_published_on_every_answer(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        """The structural rule: a contract that declares a population caveat
        in its description emits it as a warning whenever it is read. The
        live API published denial_rate at 49.94% with its own caveat
        nowhere in the response."""
        spec = make_spec(measures=("denial_rate",), basis=SERVICE)
        validated = validator.validate(planner.build(spec), spec)
        caveats = [w for w in validated.warnings if w.startswith("population_caveat:")]
        assert len(caveats) == 1, validated.warnings
        assert "denial_rate" in caveats[0] and "status OPEN" in caveats[0]

    def test_a_contract_without_a_caveat_emits_nothing(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        spec = make_spec(measures=("cash_posted",))
        validated = validator.validate(planner.build(spec), spec)
        assert not [w for w in validated.warnings if w.startswith("population_caveat:")]

    def test_caveat_extraction_stops_at_the_next_governed_heading(self) -> None:
        text = (
            "Share of adjudicated claims. Population caveat: OPEN claims are "
            "excluded from both sides. Primary basis is remittance date. "
            "Benchmark context: sub-5 percent."
        )
        caveat = population_caveat(text)
        assert caveat == "OPEN claims are excluded from both sides."
        assert population_caveat("no caveat here") is None

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


class TestCapabilityNegotiation:
    """§6.3: answerability is negotiated with the source, not guessed.

    Before this, ``_field_resolves`` consulted the catalog plus one
    hardcoded field name, so a source that had learned to compute seven
    more was told it could not. These tests hold the negotiation from both
    sides: what a source advertises, it gets asked for; what it does not,
    it is not — with the refusal naming the field and the reason.
    """

    def test_an_advertised_derived_measure_becomes_answerable(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        planner: BuildInvestigationPlanService,
        make_spec: SpecFactory,
    ) -> None:
        """The same plan the pruning test uses, against a source that says
        it computes the field."""
        validator = _validator(
            catalog,
            pack_port,
            _capabilities(
                derived_measures=(
                    _derived("payment_lag_days", "transaction", ProbeShape.AGGREGATION),
                )
            ),
        )
        spec = make_spec(dimensions=("payer",), comparison=ComparisonKind.PRIOR_PERIOD)
        plan = planner.build(spec, playbook_id="cash_decline", window_explicit=True)
        validated = validator.validate(plan, spec)
        ids = [node.id for node in validated.plan.nodes]
        assert "lag_distribution_compare" in ids
        assert "lag_distribution_compare__prior" in ids
        assert not any("lag_distribution_compare" in w for w in validated.warnings)

    def test_an_unadvertised_derived_measure_refuses_with_the_reason(
        self,
        planner: BuildInvestigationPlanService,
        validator: PlanValidationService,
        make_spec: SpecFactory,
    ) -> None:
        """Silence is not permission — and the refusal says what is
        missing, so nobody has to guess whether it is the catalog, the
        pack or the source."""
        spec = make_spec(measures=("avg_days_to_pay",), entity=EntityGrain.TRANSACTION)
        with pytest.raises(UnsupportedConceptError) as excinfo:
            validator.validate(planner.build(spec), spec)
        assert "no probe in the plan is answerable at the source" in str(excinfo.value)
        [reason] = excinfo.value.details["reasons"]
        assert "payment_lag_days" in reason
        assert "computes" in reason or "neither a catalog measure" in reason

    def test_a_derived_measure_advertised_at_another_entity_does_not_resolve(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        planner: BuildInvestigationPlanService,
        make_spec: SpecFactory,
    ) -> None:
        """An advertisement is per entity: computing ``payment_lag_days``
        over transactions says nothing about computing it over claims, and
        a source that cannot cross grains at all cannot borrow it."""
        validator = _validator(
            catalog,
            pack_port,
            _capabilities(
                derived_measures=(
                    _derived("submission_lag_days", "transaction", ProbeShape.AGGREGATION),
                )
            ),
        )
        spec = make_spec(measures=("bill_lag_days",))  # claim-grain contract
        with pytest.raises(UnsupportedConceptError):
            validator.validate(planner.build(spec), spec)

    def test_a_shape_the_source_cannot_compute_is_refused_at_plan_time(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        make_spec: SpecFactory,
    ) -> None:
        """The verdicts cannot disagree.

        ``credit_balance_cents`` is a snapshot-time quantity: the adapter
        refuses it inside a flow aggregation rather than inventing an
        as-of. Given a contract mis-authored at the flow kind, §6.6 must
        refuse the same probe for the same reason *before* it is executed —
        otherwise plan time promises what execute time denies.
        """
        authored = pack_port.metric("credit_balance_dollars")
        assert authored is not None
        pack = _PackWithOverrides(
            pack_port, {"credit_balance_dollars": replace(authored, kind=MetricKind.FLOW)}
        )
        validator = _validator(
            catalog,
            pack,  # type: ignore[arg-type]
            _capabilities(
                derived_measures=(
                    _derived("credit_balance_cents", "claim", ProbeShape.SNAPSHOT),
                )
            ),
        )
        planner = BuildInvestigationPlanService(pack, catalog)  # type: ignore[arg-type]
        spec = make_spec(measures=("credit_balance_dollars",), basis=SERVICE)
        plan = planner.build(spec)
        assert isinstance(plan.nodes[0].probe, AggregationProbe)  # the mis-authoring
        with pytest.raises(UnsupportedConceptError) as excinfo:
            validator.validate(plan, spec)
        assert excinfo.value.details["dropped"] == ["main"]

    def test_cross_entity_components_need_the_advertised_construction(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        planner: BuildInvestigationPlanService,
        make_spec: SpecFactory,
    ) -> None:
        """``net_collection_rate`` sums transaction cash over claim
        expected. A source that cannot aggregate across grains refuses it;
        a source that advertises the construction answers it."""
        spec = make_spec(measures=("net_collection_rate",), basis=SERVICE)
        blind = _validator(catalog, pack_port)
        with pytest.raises(UnsupportedConceptError):
            blind.validate(planner.build(spec), spec)

        capable = _validator(catalog, pack_port, _capabilities(cross_entity_ratio_of_sums=True))
        validated = capable.validate(planner.build(spec), spec)
        assert [node.id for node in validated.plan.nodes] == ["main"]

    def test_cross_entity_is_refused_on_a_snapshot_probe(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        make_spec: SpecFactory,
    ) -> None:
        """A snapshot aggregates one entity as-of a date; there is no
        second block to join. Advertising the construction does not make a
        snapshot eligible for it — which is also what the adapter does."""
        authored = pack_port.metric("net_collection_rate")
        assert authored is not None
        pack = _PackWithOverrides(
            pack_port, {"net_collection_rate": replace(authored, kind=MetricKind.SNAPSHOT)}
        )
        validator = _validator(
            catalog,
            pack,  # type: ignore[arg-type]
            _capabilities(cross_entity_ratio_of_sums=True),
        )
        planner = BuildInvestigationPlanService(pack, catalog)  # type: ignore[arg-type]
        spec = make_spec(measures=("net_collection_rate",), basis=SERVICE)
        plan = planner.build(spec)
        with pytest.raises(UnsupportedConceptError):
            validator.validate(plan, spec)

    def test_cross_entity_cuts_must_bind_at_both_entities(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        planner: BuildInvestigationPlanService,
        make_spec: SpecFactory,
    ) -> None:
        """Each side aggregates the identical keys against its own base
        view, so a scope the second entity cannot express would silently
        give the two sides different populations. ``status`` binds at claim
        and nowhere else, so the probe is refused rather than executed
        half-scoped."""
        validator = _validator(catalog, pack_port, _capabilities(cross_entity_ratio_of_sums=True))
        scope = Predicate(DimensionRef("status"), PredicateOp.EQ, ("OPEN",))
        spec = make_spec(measures=("net_collection_rate",), basis=SERVICE, scope=scope)
        with pytest.raises(GrainIncompatibleError) as excinfo:
            validator.validate(planner.build(spec), spec)
        assert excinfo.value.details["entity"] == "transaction"
        assert excinfo.value.details["dimension"] == "status"

    def test_cross_entity_windows_must_read_the_same_basis_at_both_entities(
        self,
        catalog: CatalogSnapshot,
        pack_port: PackSnapshotPort,
        make_spec: SpecFactory,
    ) -> None:
        """The other half of the same law: a window basis bound at one
        entity only would date the two sides differently."""
        authored = pack_port.metric("net_collection_rate")
        assert authored is not None
        pack = _PackWithOverrides(
            pack_port,
            {
                "net_collection_rate": replace(
                    authored,
                    primary_date_basis=DISCHARGE,  # bound at claim, not at transaction
                    allowed_date_bases=(DISCHARGE,),
                )
            },
        )
        validator = _validator(
            catalog,
            pack,  # type: ignore[arg-type]
            _capabilities(cross_entity_ratio_of_sums=True),
        )
        planner = BuildInvestigationPlanService(pack, catalog)  # type: ignore[arg-type]
        spec = make_spec(measures=("net_collection_rate",), basis=DISCHARGE)
        with pytest.raises(DateBasisInvalidError) as excinfo:
            validator.validate(planner.build(spec), spec)
        assert excinfo.value.details["entity"] == "transaction"
