"""Planning: direct + playbook expansion, comparison pairing, plan hashing,
and the trivial plan diff."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.planning import (
    BuildInvestigationPlanService,
    DiffPlanService,
    InvestigationPlan,
)
from revi_kernel.errors import UnsupportedConceptError
from revi_kernel.probes import AggregationProbe, SnapshotProbe
from revi_kernel.refs import POST, REMIT
from revi_kernel.scope import ComparisonKind, RangeMode, RelativeRange, TimeUnit
from revi_testing.engine_wiring import PackSnapshotPort

if TYPE_CHECKING:
    from conftest import SpecFactory


@pytest.fixture
def planner(
    pack_port: PackSnapshotPort, catalog: CatalogSnapshot
) -> BuildInvestigationPlanService:
    return BuildInvestigationPlanService(pack_port, catalog)


def _node_ids(plan: InvestigationPlan) -> list[str]:
    return [node.id for node in plan.nodes]


class TestDirectMode:
    def test_single_flow_probe_with_comparison_pair(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(
            measures=("cash_posted",),
            dimensions=("payer",),
            comparison=ComparisonKind.PRIOR_PERIOD,
        )
        plan = planner.build(spec)
        assert _node_ids(plan) == ["main", "main__prior"]
        main = plan.node("main").probe
        prior = plan.node("main__prior").probe
        assert isinstance(main, AggregationProbe) and isinstance(prior, AggregationProbe)
        assert main.window.basis == POST
        assert main.window.range.start == date(2026, 7, 27)
        assert main.window.range.end == date(2026, 8, 2)
        assert prior.window.range.start == date(2026, 7, 20)
        assert prior.window.range.end == date(2026, 7, 26)
        [compare_step] = plan.transforms.steps
        assert compare_step.operator == "compare"
        assert compare_step.inputs == ("main", "main__prior")

    def test_snapshot_contract_builds_snapshot_probe(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("ar_balance",), dimensions=("payer",))
        plan = planner.build(spec)
        [node] = plan.nodes
        probe = node.probe
        assert isinstance(probe, SnapshotProbe)
        assert probe.as_of == date(2026, 8, 2)  # watermark newest data date
        # spec basis POST is not allowed for ar_balance → primary SERVICE aging
        assert probe.aging_basis is not None and probe.aging_basis.id == "service"

    def test_mixed_grains_split_into_groups(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("cash_posted", "claim_volume"), dimensions=("payer",))
        plan = planner.build(spec)
        assert _node_ids(plan) == ["main", "main_2"]
        cash, volume = plan.node("main").probe, plan.node("main_2").probe
        assert isinstance(cash, AggregationProbe) and isinstance(volume, AggregationProbe)
        assert cash.window.basis == POST
        assert volume.window.basis.id == "service"  # claim_volume rejects POST → primary

    def test_no_measures_is_unsupported(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        with pytest.raises(UnsupportedConceptError):
            planner.build(make_spec())


class TestPlaybookExpansion:
    def test_cash_decline_explicit_window_governs_probes(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(dimensions=("payer",), comparison=ComparisonKind.PRIOR_PERIOD)
        plan = planner.build(spec, playbook_id="cash_decline", window_explicit=True)
        ids = _node_ids(plan)
        for expected in (
            "weekly_cash_trend",
            "cash_by_payer",
            "submission_volume_by_payer",
            "lag_distribution_compare",
        ):
            assert expected in ids and f"{expected}__prior" in ids
        by_payer = plan.node("cash_by_payer").probe
        assert isinstance(by_payer, AggregationProbe)
        # the analyst's explicit window wins over the template's two weeks
        assert by_payer.window.range.start == date(2026, 7, 27)
        assert by_payer.window.range.end == date(2026, 8, 2)
        assert by_payer.limit == 12  # template top_n
        assert by_payer.order_by and by_payer.order_by[0].descending
        submissions = plan.node("submission_volume_by_payer").probe
        assert isinstance(submissions, AggregationProbe)
        assert submissions.window.basis.id == "submission"  # template basis_override
        # transforms: one compare per flow pair + impact ranks on money frames
        compare_steps = [s for s in plan.transforms.steps if s.operator == "compare"]
        assert {s.inputs[0] for s in compare_steps} == {
            "weekly_cash_trend",
            "cash_by_payer",
            "submission_volume_by_payer",
            "lag_distribution_compare",
        }
        rank_steps = [s for s in plan.transforms.steps if s.operator == "rank"]
        assert any(
            s.inputs == ("cash_by_payer__compare",) and s.arg("by") == "cash_posted__delta"
            for s in rank_steps
        )
        # impact ranking is ascending: biggest declines first
        assert all(s.arg("descending") == "false" for s in rank_steps)
        assert any("decompose" in note for note in plan.notes)

    def test_cash_decline_default_window_uses_templates(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(dimensions=("payer",))
        plan = planner.build(spec, playbook_id="cash_decline", window_explicit=False)
        by_payer = plan.node("cash_by_payer").probe
        assert isinstance(by_payer, AggregationProbe)
        # template window: last 2 full weeks before the 2026-08-03 anchor
        assert by_payer.window.range.start == date(2026, 7, 20)
        assert by_payer.window.range.end == date(2026, 8, 2)

    def test_denial_spike_expansion(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(basis=REMIT, comparison=ComparisonKind.PRIOR_PERIOD)
        plan = planner.build(spec, playbook_id="denial_spike", window_explicit=True)
        ids = _node_ids(plan)
        for expected in ("denial_trend", "denial_code_mix", "denial_concentration"):
            assert expected in ids
        code_mix = plan.node("denial_code_mix").probe
        assert isinstance(code_mix, AggregationProbe)
        assert code_mix.limit == 10
        assert [d.id for d in code_mix.dimensions] == ["group_code", "carc"]
        share_steps = [s for s in plan.transforms.steps if s.operator == "share_of_total"]
        assert share_steps and all(s.arg("measure") == "denied_dollars" for s in share_steps)
        rank_steps = [s for s in plan.transforms.steps if s.operator == "rank"]
        assert any(s.arg("by") == "denied_dollars" for s in rank_steps)

    def test_unknown_playbook_is_unsupported(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        with pytest.raises(UnsupportedConceptError):
            planner.build(make_spec(dimensions=("payer",)), playbook_id="does_not_exist")


class TestPlanHashAndDiff:
    def test_plan_hash_is_stable_and_window_sensitive(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("cash_posted",), dimensions=("payer",))
        assert planner.build(spec).plan_hash == planner.build(spec).plan_hash
        wider = make_spec(
            measures=("cash_posted",),
            dimensions=("payer",),
            window=RelativeRange(Decimal(2), TimeUnit.WEEK, RangeMode.FULL_PERIODS),
        )
        assert planner.build(spec).plan_hash != planner.build(wider).plan_hash

    def test_diff_by_probe_hash(
        self, planner: BuildInvestigationPlanService, make_spec: SpecFactory
    ) -> None:
        old = planner.build(make_spec(measures=("cash_posted",), dimensions=("payer",)))
        new = planner.build(
            make_spec(
                measures=("cash_posted",),
                dimensions=("payer",),
                comparison=ComparisonKind.PRIOR_PERIOD,
            )
        )
        diff = DiffPlanService().diff(old, new)
        assert [n.id for n in diff.unchanged] == ["main"]
        assert [n.id for n in diff.added] == ["main__prior"]
        assert diff.removed == ()
        null_diff = DiffPlanService().diff(new, new)
        assert null_diff.added == () and null_diff.removed == ()
        assert len(null_diff.unchanged) == 2


class TestCompanionDimensions:
    """A CARC is half an identity.

    CO-50 is a contractual write-off nobody can appeal; PI-50 is a
    payer-initiated reduction that is disputable money. A free-form
    breakdown "by CARC" cut by ``carc`` alone merged $21,234 of the first
    with $5,752 of the second under one label, while every pack playbook
    that cuts by ``carc`` conjoins ``group_code`` by hand — so the same
    product answered the same question two different ways.
    """

    def test_a_free_form_carc_cut_also_groups_by_the_adjustment_group(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("denied_dollars",), dimensions=("carc",))

        plan = BuildInvestigationPlanService(pack_port, catalog).build(spec)

        [node] = [n for n in plan.nodes if not n.id.endswith("__prior")]
        planned = [ref.id for ref in node.probe.dimensions]  # type: ignore[union-attr]
        assert planned == ["group_code", "carc"], planned

    def test_the_companionship_is_declared_by_the_catalog_not_the_engine(
        self, catalog: CatalogSnapshot
    ) -> None:
        carc = catalog.dimension("carc")
        assert carc is not None
        assert carc.companion_dimensions == ("group_code",)
        # …and a dimension that declares none is untouched
        payer = catalog.dimension("payer")
        assert payer is not None and payer.companion_dimensions == ()

    def test_a_metric_that_cannot_be_cut_by_the_companion_is_left_alone(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: SpecFactory
    ) -> None:
        """Adding a cut the contract forbids would turn an answerable
        question into GRAIN_INCOMPATIBLE; the renderer's "(all adjustment
        groups)" disclosure is the honest fallback."""
        from dataclasses import replace as _replace

        from revi_kernel.refs import DimensionRef as _DimensionRef

        contract = pack_port.metric("denied_dollars")
        assert contract is not None
        narrowed = _replace(
            contract,
            scope_dimensions=tuple(
                d for d in contract.scope_dimensions if d != _DimensionRef("group_code")
            ),
        )
        planner = BuildInvestigationPlanService(pack_port, catalog)
        assert planner._with_companions((_DimensionRef("carc"),), (narrowed,)) == (
            _DimensionRef("carc"),
        )

    def test_a_cut_that_already_names_both_is_unchanged(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: SpecFactory
    ) -> None:
        spec = make_spec(measures=("denied_dollars",), dimensions=("group_code", "carc"))

        plan = BuildInvestigationPlanService(pack_port, catalog).build(spec)

        [node] = [n for n in plan.nodes if not n.id.endswith("__prior")]
        planned = [ref.id for ref in node.probe.dimensions]  # type: ignore[union-attr]
        assert planned == ["group_code", "carc"]
