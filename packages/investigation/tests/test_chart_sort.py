"""The plan's resolved ordering, published on the chart (round-3 R3-13).

Four personas: the findings obey "best to worst" and the chart directly
beneath them is drawn alphabetically — Atlas, Bluestone, Federal Medicare,
Lakewood — because the ordering existed only inside the plan and nothing
carried it to the renderer. It exists in three places there (an ``Ordering``
on the probe, ``by``/``descending`` args on a rank step, and the
``{by}__rank`` column the rank operator appends) and the frame the chart
builder actually draws has none of them: the rank operator appends a rank
column rather than reordering rows, and the frame it outputs is skipped by
the chart builder as presentation metadata.

``resolved_orderings`` is the one function that reads all three, keyed by
the frame id a chart is built for.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from revi_investigation.application.planning import (
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
    TransformPlanStep,
    resolved_orderings,
)
from revi_kernel.filters import EMPTY_SCOPE
from revi_kernel.probes import AggregationProbe, Ordering
from revi_kernel.refs import DateBasisRef, DimensionRef, EntityGrain, Grain, MetricRef
from revi_kernel.scope import RangeMode, RelativeRange, TimeUnit, resolve_window

_WINDOW = resolve_window(
    RelativeRange(Decimal(1), TimeUnit.MONTH, RangeMode.FULL_PERIODS),
    date(2026, 8, 2),
    basis=DateBasisRef("service"),
)


def _node(node_id: str, *, order_by: tuple[Ordering, ...] = ()) -> ProbeNode:
    return ProbeNode(
        id=node_id,
        probe=AggregationProbe(
            measures=(MetricRef("denied_dollars"),),
            dimensions=(DimensionRef("payer"),),
            scope=EMPTY_SCOPE,
            window=_WINDOW,
            grain=Grain(EntityGrain.CLAIM),
            order_by=order_by,
        ),
        purpose="denied dollars by payer",
    )


def _plan(
    nodes: tuple[ProbeNode, ...], steps: tuple[TransformPlanStep, ...] = ()
) -> InvestigationPlan:
    return InvestigationPlan(nodes=nodes, transforms=TransformPlan(steps=steps))


class TestTheProbesOwnOrdering:
    def test_an_ordered_probe_publishes_its_ordering(self) -> None:
        plan = _plan(
            (
                _node(
                    "main",
                    order_by=(Ordering(by=MetricRef("denied_dollars"), descending=True),),
                ),
            )
        )
        assert resolved_orderings(plan) == (("main", "denied_dollars", True),)

    def test_an_unordered_probe_publishes_nothing(self) -> None:
        """A chart with no plan ordering says so rather than implying one a
        renderer would then sort by."""
        assert resolved_orderings(_plan((_node("main"),))) == ()

    def test_an_ascending_ordering_survives_as_ascending(self) -> None:
        """"Rank payers best to worst" on a higher-is-bad rate sorts
        ASCENDING; publishing it descending is how the worst payer got
        narrated as the best."""
        plan = _plan(
            (
                _node(
                    "main",
                    order_by=(Ordering(by=MetricRef("denial_rate"), descending=False),),
                ),
            )
        )
        assert resolved_orderings(plan) == (("main", "denial_rate", False),)


class TestTheRankStepsResolution:
    def test_a_rank_step_keys_the_frame_that_gets_charted(self) -> None:
        """The rank operator appends a ``{by}__rank`` column and leaves the
        rows in place, and the chart builder skips ``*__rank`` frames — so
        the ordering has to land on the step's INPUT as well as its output,
        or it reaches no chart at all."""
        plan = _plan(
            (_node("main"),),
            (
                TransformPlanStep(
                    id="main__rank",
                    operator="rank",
                    inputs=("main",),
                    args=(("by", "denied_dollars"), ("descending", "true")),
                ),
            ),
        )
        assert {
            frame: (column, desc) for frame, column, desc in resolved_orderings(plan)
        } == {
            "main": ("denied_dollars", True),
            "main__rank": ("denied_dollars", True),
        }

    def test_a_rank_step_wins_over_the_probes_own_order_by(self) -> None:
        """A rank step is the later and more specific decision, and it is
        the one the findings layer reads."""
        plan = _plan(
            (
                _node(
                    "main",
                    order_by=(Ordering(by=MetricRef("denied_dollars"), descending=True),),
                ),
            ),
            (
                TransformPlanStep(
                    id="main__rank",
                    operator="rank",
                    inputs=("main",),
                    args=(("by", "denial_rate"), ("descending", "false")),
                ),
            ),
        )
        assert ("main", "denial_rate", False) in resolved_orderings(plan)

    def test_top_k_is_descending_by_construction(self) -> None:
        """The operator only knows how to take the largest; publishing an
        ascending hint over it would describe a computation nobody ran."""
        plan = _plan(
            (_node("main"),),
            (
                TransformPlanStep(
                    id="main__top",
                    operator="top_k",
                    inputs=("main",),
                    args=(("by", "denied_dollars"), ("k", "5")),
                ),
            ),
        )
        assert ("main", "denied_dollars", True) in resolved_orderings(plan)

    def test_a_step_naming_no_column_publishes_nothing(self) -> None:
        plan = _plan(
            (_node("main"),),
            (
                TransformPlanStep(
                    id="main__share", operator="share_of_total", inputs=("main",)
                ),
            ),
        )
        assert resolved_orderings(plan) == ()
