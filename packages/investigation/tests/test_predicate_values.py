"""Filter values are resolved against values that exist (§6.6 step 4b),
and refusals that a pack can answer become questions instead of dead ends.

Two regressions. "denial rate for UnitedHealthcare and Aetna" compiled against
a warehouse holding neither name, matched nothing, and published an empty
answer caveated only about small-cell suppression — every **id** had been
validated against governed content, and nothing had ever validated a **value**.
Separately, a ``GRAIN_INCOMPATIBLE`` refusal named an error code and offered
nothing while the pack held metrics declaring exactly the refused cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.planning import BuildInvestigationPlanService
from revi_investigation.application.validation import (
    PlanClarificationNeeded,
    PlanValidationService,
    contract_pinned_values,
)
from revi_kernel.capabilities import RepositoryCapabilities
from revi_kernel.cohort import CohortDefinition, CohortMaterialization
from revi_kernel.errors import GrainIncompatibleError, SourceUnavailableError
from revi_kernel.filters import Predicate, PredicateOp
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import AggregationProbe, EvidenceProbe, SnapshotProbe
from revi_kernel.refs import DimensionRef
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import MINIMAL_CAPABILITIES

if TYPE_CHECKING:
    from conftest import SpecFactory

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)

PAYERS = ("Atlas Commercial", "Bluestone Mutual", "Meridian Health", "State Medicaid")


@dataclass
class _ValueRepository:
    """Serves exactly the distinct-value read and refuses everything else.

    A validation read must be *cheap and narrow*: one dimension, no scope,
    the probe's own window. Refusing anything else is how this fixture
    asserts that shape without asserting on SQL.
    """

    values: tuple[str, ...] = PAYERS
    dimension: str = "payer"
    reads: int = 0
    seen: list[EvidenceProbe] = field(default_factory=list)

    def capabilities(self) -> RepositoryCapabilities:
        return MINIMAL_CAPABILITIES

    async def list_watermarks(self) -> tuple[DataWatermark, ...]:
        return (WATERMARK,)

    async def execute(self, probe: EvidenceProbe, *, watermark: DataWatermark) -> EvidenceFrame:
        self.seen.append(probe)
        ref = DimensionRef(self.dimension)
        if not isinstance(probe, (AggregationProbe, SnapshotProbe)) or probe.dimensions != (ref,):
            raise SourceUnavailableError("this fixture serves only the distinct-value read")
        self.reads += 1
        return EvidenceFrame(
            schema=FrameSchema((FrameColumn(name=self.dimension, ref=ref),)),
            rows=tuple((value,) for value in self.values),
            watermark=watermark,
            provenance=ProbeProvenance(probe_id="values", probe_hash="0" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )

    async def materialize_cohort(
        self, definition: CohortDefinition, *, watermark: DataWatermark
    ) -> CohortMaterialization:
        raise SourceUnavailableError("not used")


@dataclass
class _BlindRepository(_ValueRepository):
    """A source that cannot serve the validation read at all."""

    async def execute(self, probe: EvidenceProbe, *, watermark: DataWatermark) -> EvidenceFrame:
        raise SourceUnavailableError("offline")


def _predicate(dimension: str, *values: str, op: PredicateOp = PredicateOp.IN) -> Predicate:
    return Predicate(dimension=DimensionRef(dimension), op=op, values=values)


async def _resolved(
    catalog: CatalogSnapshot,
    pack_port: PackSnapshotPort,
    make_spec: SpecFactory,
    repository: _ValueRepository,
    predicate: Predicate,
    *,
    measures: tuple[str, ...] = ("denial_rate",),
) -> tuple[PlanValidationService, object]:
    spec = make_spec(measures=measures, scope=predicate, watermark=WATERMARK)
    planner = BuildInvestigationPlanService(pack_port, catalog)
    validator = PlanValidationService(catalog, pack_port, repository)
    validated = validator.validate(planner.build(spec), spec)
    return validator, await validator.resolve_predicate_values(validated, watermark=WATERMARK)


class TestOpenDimensionValues:
    async def test_a_payer_that_does_not_exist_clarifies_instead_of_querying_the_void(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        repository = _ValueRepository()
        with pytest.raises(PlanClarificationNeeded) as raised:
            await _resolved(
                catalog,
                pack_port,
                make_spec,
                repository,
                _predicate("payer", "UnitedHealthcare", "Aetna"),
            )

        clarification = raised.value.clarification
        assert "UnitedHealthcare" in clarification.question
        assert "Aetna" in clarification.question
        # how much exists, so "which did you mean" is answerable
        assert str(len(PAYERS)) in clarification.question
        assert clarification.options  # real names, tappable
        assert set(clarification.options) <= set(PAYERS)
        assert clarification.reason is not None
        assert clarification.reason.startswith("PREDICATE_VALUE_UNMATCHED")

    async def test_a_referent_handle_in_a_filter_is_caught_by_the_same_check(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """A handle is not a payer. It compiled before because nothing ever
        asked whether the value existed."""
        with pytest.raises(PlanClarificationNeeded):
            await _resolved(
                catalog, pack_port, make_spec, _ValueRepository(), _predicate("payer", "F2")
            )

    async def test_case_variants_are_corrected_and_the_correction_is_stated(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        _, validated = await _resolved(
            catalog, pack_port, make_spec, _ValueRepository(), _predicate("payer", "atlas commercial")
        )

        assert any("value_corrected" in warning for warning in validated.warnings)  # type: ignore[attr-defined]
        values = {
            value
            for node in validated.plan.nodes  # type: ignore[attr-defined]
            for predicate in _top_level(node.probe.scope)
            for value in predicate.values
        }
        assert "Atlas Commercial" in values
        assert "atlas commercial" not in values

    async def test_an_existing_value_passes_untouched(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        _, validated = await _resolved(
            catalog, pack_port, make_spec, _ValueRepository(), _predicate("payer", "Atlas Commercial")
        )
        assert not [w for w in validated.warnings if "value_corrected" in w]  # type: ignore[attr-defined]

    async def test_the_domain_read_is_cached_per_watermark(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """A dimension's values cannot change inside a load, so the second
        question about payers in the same window costs nothing."""
        repository = _ValueRepository()
        validator, _ = await _resolved(
            catalog, pack_port, make_spec, repository, _predicate("payer", "Atlas Commercial")
        )
        first = repository.reads

        spec = make_spec(
            measures=("denial_rate",),
            scope=_predicate("payer", "Bluestone Mutual"),
            watermark=WATERMARK,
        )
        planner = BuildInvestigationPlanService(pack_port, catalog)
        await validator.resolve_predicate_values(
            validator.validate(planner.build(spec), spec), watermark=WATERMARK
        )

        assert repository.reads == first, "the second question re-read the domain"

    async def test_a_source_that_cannot_enumerate_never_accuses(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """Refusing an answer because a *validation* read failed would turn
        an unavailable source into a wrong-value accusation."""
        _, validated = await _resolved(
            catalog,
            pack_port,
            make_spec,
            _BlindRepository(),
            _predicate("payer", "UnitedHealthcare"),
        )
        assert validated is not None


class TestDeclaredValueDomains:
    async def test_a_declared_enum_is_checked_without_touching_the_source(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        repository = _ValueRepository()
        with pytest.raises(PlanClarificationNeeded) as raised:
            await _resolved(
                catalog, pack_port, make_spec, repository, _predicate("payer_type", "PLATINUM")
            )
        assert repository.reads == 0, "a catalog-declared domain needs no warehouse read"
        assert "PLATINUM" in raised.value.clarification.question

    async def test_analyst_spelling_of_an_enum_value_is_auto_corrected(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort, make_spec: SpecFactory
    ) -> None:
        """"Medicare Advantage" is how a person writes ``MEDICARE_ADVANTAGE``
        and is exactly the shape that used to compile into an empty
        population."""
        _, validated = await _resolved(
            catalog,
            pack_port,
            make_spec,
            _ValueRepository(),
            _predicate("payer_type", "Medicare Advantage", op=PredicateOp.EQ),
        )
        values = {
            value
            for node in validated.plan.nodes  # type: ignore[attr-defined]
            for predicate in _top_level(node.probe.scope)
            for value in predicate.values
        }
        assert "MEDICARE_ADVANTAGE" in values
        assert any("value_corrected" in w for w in validated.warnings)  # type: ignore[attr-defined]


class TestRefusalsWithAWayOut:
    def test_a_grain_refusal_offers_metrics_that_declare_the_cut(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort
    ) -> None:
        validator = PlanValidationService(catalog, pack_port, _ValueRepository())

        clarification = validator.clarification_for(
            GrainIncompatibleError(
                "dimension 'ar_age_bucket' is not a legal scope dimension for ratio metric "
                "'ar_over_90_pct'",
                details={"dimension": "ar_age_bucket", "metric": "ar_over_90_pct", "probe": "main"},
            )
        )

        assert clarification is not None
        assert clarification.options
        assert any("ar balance" in option for option in clarification.options)
        assert clarification.reason is not None
        assert "GRAIN_INCOMPATIBLE_RECOVERABLE" in clarification.reason

    def test_a_refusal_the_pack_cannot_answer_stays_a_refusal(
        self, catalog: CatalogSnapshot, pack_port: PackSnapshotPort
    ) -> None:
        """A clarification with no way forward is worse than an error that
        says what happened."""
        validator = PlanValidationService(catalog, pack_port, _ValueRepository())
        assert (
            validator.clarification_for(
                GrainIncompatibleError("nothing to go on", details={})
            )
            is None
        )


class TestContractPinnedValues:
    def test_a_contract_reports_the_population_it_pins(
        self, pack_port: PackSnapshotPort
    ) -> None:
        contract = pack_port.metric("ar_over_90_pct")
        assert contract is not None
        pinned = contract_pinned_values(contract)
        assert pinned["ar_age_bucket"] == frozenset({"91_120", "120"})
        assert "status" in pinned


def _top_level(expr: object) -> tuple[Predicate, ...]:
    """The conjunctive predicates of a scope, without importing internals."""
    if isinstance(expr, Predicate):
        return (expr,)
    clauses = getattr(expr, "clauses", ())
    return tuple(p for clause in clauses for p in _top_level(clause))
