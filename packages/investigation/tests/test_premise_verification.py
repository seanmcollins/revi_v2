"""The premise verdict reads what the integrity layer already published.

``verify_premise`` read the measure and its ``__prior`` straight out of the
frame and consulted nothing — not the bounded-cell index, not the panel
share, not whether the SIZE the question asserted had been parsed at all —
so it published confident verdicts over three classes of quantity the rest
of the engine had already marked unmeasurable: a "+157.1%" that was the
ratio of two clamped denominators, a "-72.7%" across panels of unequal
maturity, and an "It happened" beside ``premise_magnitude: unverifiable``.
The property at the bottom: no premise finding may publish a
non-UNVERIFIABLE magnitude when either endpoint is bounded or either panel
is immature.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar

import pytest

from revi_investigation.application.calculation_glue import CalculationResult
from revi_investigation.application.findings import (
    EvaluateFindingsService,
    MagnitudeVerdict,
    verify_premise,
)
from revi_investigation.application.interpretation import (
    asserted_multiple,
    size_asserted_unparsed,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
    TransformPlan,
    TransformPlanStep,
)
from revi_investigation.domain.context import AskedDirection
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort
from revi_testing.fakes import FakeReferentRegistryStore

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
THRESHOLD = 11


def _rate_premise_frame(
    *,
    numerator: int,
    denominator: int,
    prior_numerator: int,
    prior_denominator: int,
) -> EvidenceFrame:
    """The ungrouped compared RATE a premise probe produces, with panels.

    Both sides carry their numerator and denominator, which is what makes a
    bound recognisable — and the prior side's columns end in
    ``__num__prior``/``__den__prior``, which is why ``bound_index`` could
    never see them.
    """
    measure = "denial_rate"
    current = Decimal(numerator) / Decimal(denominator)
    prior = Decimal(prior_numerator) / Decimal(prior_denominator)
    columns = (
        FrameColumn(measure, MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__prior", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__delta", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__pct_change", MetricRef(measure), 2, "ratio"),
        FrameColumn(f"{measure}__num", MetricRef(measure), 2, "count"),
        FrameColumn(f"{measure}__den", MetricRef(measure), 2, "count"),
        FrameColumn(f"{measure}__num__prior", MetricRef(measure), 2, "count"),
        FrameColumn(f"{measure}__den__prior", MetricRef(measure), 2, "count"),
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


def _plan() -> InvestigationPlan:
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


def _check(frame: EvidenceFrame, spec: object, pack: PackSnapshotPort):  # type: ignore[no-untyped-def]
    return verify_premise(
        _plan(),
        CalculationResult(frames=(("premise__compare", frame),), operations=()),
        spec,  # type: ignore[arg-type]
        pack,
        premise_prefix="premise",
        suppression_threshold=THRESHOLD,
    )


def _doubling(make_spec):  # type: ignore[no-untyped-def]
    spec = make_spec(measures=("denial_rate",), watermark=WATERMARK)
    return replace(
        spec,
        direction=AskedDirection.INCREASE,
        direction_asserted=True,
        asserted_multiple=Decimal(2),
    )


class TestAMovementBetweenTwoCeilingsIsNotAMovement:
    """A competitor-exec reviewer's exact figures: 10/72 → 10/28,
    published as +157.1%."""

    BOUNDED: ClassVar[dict[str, int]] = {
        "numerator": 10,
        "denominator": 28,
        "prior_numerator": 10,
        "prior_denominator": 72,
    }

    def test_the_verdict_refuses_rather_than_confirming(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        premise = _check(_rate_premise_frame(**self.BOUNDED), _doubling(make_spec), pack_port)

        assert premise is not None
        assert premise.unverifiable is True
        assert premise.bounded is True
        assert premise.holds is False
        assert premise.magnitude is MagnitudeVerdict.UNVERIFIABLE

    def test_both_endpoints_are_recognised_including_the_prior(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """``bound_index`` sees ``__num``/``__den`` and therefore only the
        CURRENT side; a bounded PRIOR is exactly as unmeasurable."""
        premise = _check(_rate_premise_frame(**self.BOUNDED), _doubling(make_spec), pack_port)

        assert premise is not None
        assert premise.current_bound is not None
        assert premise.prior_bound is not None
        assert premise.prior_bound.population == 72
        assert premise.current_bound.population == 28

    def test_a_measured_pair_still_gets_a_real_verdict(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """The guard must not swallow the verdicts that are correct: this
        is the same shape with numerators the policy never touched."""
        premise = _check(
            _rate_premise_frame(
                numerator=40, denominator=200, prior_numerator=20, prior_denominator=200
            ),
            _doubling(make_spec),
            pack_port,
        )

        assert premise is not None
        assert premise.unverifiable is False
        assert premise.magnitude is MagnitudeVerdict.WITHIN
        assert premise.holds is True


class TestTwoUnequallySettledPanelsCannotBeCompared:
    """An investor reviewer's turn, at the panel sizes it measured."""

    def test_an_immature_panel_makes_the_verdict_unverifiable(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        premise = _check(
            _rate_premise_frame(
                numerator=198, denominator=1_544, prior_numerator=520, prior_denominator=5_723
            ),
            _doubling(make_spec),
            pack_port,
        )

        assert premise is not None
        assert premise.immature is not None
        assert premise.immature.current_panel == 1_544
        assert premise.immature.prior_panel == 5_723
        assert premise.unverifiable is True
        assert premise.holds is False
        assert premise.magnitude is MagnitudeVerdict.UNVERIFIABLE

    def test_a_settled_pair_is_left_alone(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        premise = _check(
            _rate_premise_frame(
                numerator=520, denominator=5_500, prior_numerator=260, prior_denominator=5_723
            ),
            _doubling(make_spec),
            pack_port,
        )

        assert premise is not None
        assert premise.immature is None
        assert premise.unverifiable is False


class TestASizeThatCannotBeParsedIsNeverAConfirmedDirection:
    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("why did cash collections halve in July?", Decimal("0.5")),
            ("why did denials halved last month?", Decimal("0.5")),
            ("why did denials quadruple in July?", Decimal(4)),
            ("why did denials go up 4x?", Decimal(4)),
            ("why did denials jump 300%?", Decimal(4)),
            ("why did denials rise threefold?", Decimal(3)),
            ("why are denied dollars up by 40%?", Decimal("1.4")),
            ("why did cash fall by 30 percent?", Decimal("0.7")),
        ],
    )
    def test_the_vocabulary_covers_what_analysts_actually_type(
        self, question: str, expected: Decimal
    ) -> None:
        """The closed table held "halved" and not "halve", "quadrupled" and
        not "quadruple", "2x"/"3x" and no numeric form at all."""
        assert asserted_multiple(question, True) == expected

    def test_a_size_nothing_can_read_is_flagged_rather_than_dropped(self) -> None:
        assert size_asserted_unparsed("why did denials fall off a cliff?", True) is False
        assert size_asserted_unparsed("why did denials shrink by an order of magnitude?", True)

    def test_a_direction_only_question_asserts_no_size(self) -> None:
        assert asserted_multiple("why are denials up?", True) is None
        assert size_asserted_unparsed("why are denials up?", True) is False

    def test_the_model_fills_the_tail_the_table_cannot_hold(self) -> None:
        """The closed table is a deterministic OVERRIDE, not the sole
        source: a word it holds is never asked of a model, and a phrasing
        it does not hold falls through to the interpretation's own reading."""
        assert asserted_multiple("why did denials double?", True, proposed=7.0) == Decimal(2)
        assert asserted_multiple("why did denials balloon?", True, proposed=2.5) == Decimal("2.5")

    def test_an_unparsed_size_can_never_publish_as_confirmed(
        self, pack_port: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=("denial_rate",), watermark=WATERMARK)
        spec = replace(
            spec,
            direction=AskedDirection.INCREASE,
            direction_asserted=True,
            asserted_multiple=None,
            size_asserted_unparsed=True,
        )
        premise = _check(
            _rate_premise_frame(
                numerator=600, denominator=5_500, prior_numerator=500, prior_denominator=5_723
            ),
            spec,
            pack_port,
        )

        assert premise is not None
        assert premise.directional is True  # the movement really did happen
        assert premise.holds is False  # …and the CLAIM was never checked
        assert premise.unverifiable is True


class TestThePropertyTheReviewAskedFor:
    """*No premise finding may publish a non-UNVERIFIABLE magnitude when
    either endpoint is bounded or when either panel is immature.*"""

    @pytest.mark.parametrize(
        ("numerator", "denominator", "prior_numerator", "prior_denominator"),
        [
            (10, 28, 10, 72),  # both sides bounded
            (10, 400, 500, 5_000),  # current bounded only
            (500, 5_000, 10, 400),  # prior bounded only
            (198, 1_544, 520, 5_723),  # immature panel
            (520, 5_723, 198, 1_544),  # immature panel, the other way round
            (2, 12, 3, 13),  # bounded on a tiny panel
        ],
    )
    async def test_no_verdict_survives_an_unmeasurable_endpoint(
        self,
        pack_port: PackSnapshotPort,
        make_spec,  # type: ignore[no-untyped-def]
        numerator: int,
        denominator: int,
        prior_numerator: int,
        prior_denominator: int,
    ) -> None:
        frame = _rate_premise_frame(
            numerator=numerator,
            denominator=denominator,
            prior_numerator=prior_numerator,
            prior_denominator=prior_denominator,
        )
        spec = _doubling(make_spec)

        premise = _check(frame, spec, pack_port)
        assert premise is not None
        assert premise.magnitude is MagnitudeVerdict.UNVERIFIABLE
        assert premise.holds is False

        # …and the published FINDING says so in the field a screenshot
        # carries, not only in a structured flag.
        service = EvaluateFindingsService(FakeReferentRegistryStore())
        result = await service.evaluate(
            plan=_plan(),
            calculation=CalculationResult(
                frames=(("premise__compare", frame),), operations=()
            ),
            spec=spec,
            pack=pack_port,
            playbook=None,
            session_id="sess",
            investigation_id="inv",
            suppression_threshold=THRESHOLD,
        )
        assert result.findings
        assert result.findings[0].title.startswith("Premise cannot be verified:")
        assert result.findings[0].confidence == "qualified"
        assert result.warnings[0].startswith("premise_unverifiable:")
        assert dict(result.findings[0].values)["premise_unverifiable"] is True
