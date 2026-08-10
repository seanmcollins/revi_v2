"""Adjudication maturity is a property of the WINDOW, not only of panels.

One load answered the same question two ways at ``confidence: high`` —
$23,749.29 on a service basis and $158,122.42 on a remit basis for the same
July, 6.7x apart, with nothing on either card reconciling them and nothing
saying that service-dated July was about a quarter adjudicated. Maturity was
bound to panels and series; the single-window aggregate that every undated
question steers into sailed through. These tests pin what the guard measures
its share against, that the yardstick is read from GOVERNED content rather
than from a metric id written into the engine, and that it stays silent
wherever another guard has already spoken.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from revi_investigation.application.window_maturity import (
    WindowMaturityService,
    adjudication_yardstick,
)
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import EvidenceProbe
from revi_kernel.refs import DateBasisRef, DimensionRef, EntityGrain, MetricRef
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort, load_base_pack

REPO_ROOT = Path(__file__).resolve().parents[3]

WATERMARK = DataWatermark(
    id="wm_003",
    loaded_at=datetime(2026, 8, 3, 4, 10),
    newest_data_date=date(2026, 8, 2),
)

#: The load's real settling curve, read off the mock warehouse: eleven
#: settled months around 6,000 adjudicated claims and a July holding 1,544.
CURVE: tuple[tuple[date, int], ...] = (
    (date(2025, 9, 1), 6044),
    (date(2025, 10, 1), 6239),
    (date(2025, 11, 1), 5660),
    (date(2025, 12, 1), 6326),
    (date(2026, 1, 1), 6049),
    (date(2026, 2, 1), 5534),
    (date(2026, 3, 1), 6051),
    (date(2026, 4, 1), 6074),
    (date(2026, 5, 1), 6133),
    (date(2026, 6, 1), 5723),
    (date(2026, 7, 1), 1544),
)


@dataclass
class _CurveRepository:
    """A repository that serves the curve above and counts the reads."""

    reads: int = 0

    async def execute(
        self, probe: EvidenceProbe, *, watermark: DataWatermark
    ) -> EvidenceFrame:
        self.reads += 1
        measure = probe.measures[0].id  # type: ignore[union-attr]
        return EvidenceFrame(
            schema=FrameSchema(
                columns=(
                    FrameColumn(name="month", ref=DimensionRef("time_bucket:month")),
                    FrameColumn(
                        name=f"{measure}__num", ref=MetricRef(f"{measure}__num"), unit="count"
                    ),
                    FrameColumn(
                        name=f"{measure}__den", ref=MetricRef(f"{measure}__den"), unit="count"
                    ),
                )
            ),
            rows=tuple((bucket, count, count) for bucket, count in CURVE),
            watermark=watermark,
            provenance=ProbeProvenance(probe_id="curve", probe_hash="h"),
            evidence_grade=EvidenceGrade.DIRECT,
        )

    async def list_watermarks(self) -> tuple[DataWatermark, ...]:  # pragma: no cover
        return (WATERMARK,)


@pytest.fixture(scope="module")
def pack() -> PackSnapshotPort:
    return PackSnapshotPort(load_base_pack(REPO_ROOT / "packs" / "base-rcm"))


def _spec(make_spec, start: date, end: date, basis: str = "service"):  # type: ignore[no-untyped-def]
    spec = make_spec(measures=("denied_dollars",), watermark=WATERMARK)
    window = TimeWindow(
        basis=DateBasisRef(basis), range=AbsoluteRange(start=start, end=end)
    )
    return spec.with_context(replace(spec.context, window=window, watermark=WATERMARK))


class TestTheYardstickIsGovernedContent:
    """No metric id is written into the engine: the contract is chosen by a
    stated rule and NAMED in the warning, so a reader can check it."""

    def test_it_is_a_ratio_whose_denominator_counts_what_has_settled(
        self, pack: PackSnapshotPort
    ) -> None:
        chosen = adjudication_yardstick(
            pack, grain=EntityGrain.CLAIM, basis=DateBasisRef("service")
        )

        assert chosen is not None
        contract = pack.metric(chosen)
        assert contract is not None
        assert contract.is_ratio, "a share is what makes the denominator a population"
        assert contract.exclusions is not None, (
            "the exclusion is what makes the denominator a count of what the "
            "source has FINISHED with"
        )

    def test_a_grain_the_pack_declares_no_such_contract_for_yields_nothing(
        self, pack: PackSnapshotPort
    ) -> None:
        """The honest outcome: a population whose completeness cannot be
        measured is not one this guard may make claims about."""
        assert (
            adjudication_yardstick(
                pack, grain=EntityGrain.REMIT, basis=DateBasisRef("remit")
            )
            is None
        )

    def test_a_basis_the_contract_cannot_be_read_on_yields_nothing(
        self, pack: PackSnapshotPort
    ) -> None:
        assert (
            adjudication_yardstick(
                pack, grain=EntityGrain.CLAIM, basis=DateBasisRef("post")
            )
            is None
        )


class TestTheWindowIsJudgedAgainstTheLoadsOwnCurve:
    async def test_the_immature_month_is_flagged_with_its_own_share(
        self, pack: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        service = WindowMaturityService(_CurveRepository(), pack)  # type: ignore[arg-type]

        verdict = await service.verdict(
            _spec(make_spec, date(2026, 7, 1), date(2026, 7, 31))
        )

        assert verdict is not None
        assert verdict.population == 1544
        assert verdict.share < Decimal("0.3")
        assert "adjudication_incomplete:" in verdict.warning
        assert "1,544 settled record(s)" in verdict.warning
        assert "2026-07-01..2026-07-31" in verdict.warning
        assert verdict.yardstick in verdict.warning, "the reader can check the yardstick"

    async def test_a_settled_month_is_left_alone(
        self, pack: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        service = WindowMaturityService(_CurveRepository(), pack)  # type: ignore[arg-type]

        assert (
            await service.verdict(_spec(make_spec, date(2026, 6, 1), date(2026, 6, 30)))
            is None
        )

    async def test_a_long_window_holding_one_thin_month_is_left_alone(
        self, pack: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """The guard fires on windows that are MATERIALLY unsettled. Six
        settled months and one thin one is 88% of a settled window, and
        qualifying every year-to-date answer would make the caveat noise."""
        assert (
            await service_verdict(
                pack, _spec(make_spec, date(2026, 1, 1), date(2026, 7, 31))
            )
            is None
        )

    async def test_the_curve_is_read_once_per_watermark_basis_and_grain(
        self, pack: PackSnapshotPort, make_spec
    ) -> None:  # type: ignore[no-untyped-def]
        """One extra warehouse read per session, not one per turn — and none
        at all on the plan, whose hash belongs to the analyst's question."""
        repository = _CurveRepository()
        service = WindowMaturityService(repository, pack)  # type: ignore[arg-type]

        for _ in range(3):
            await service.verdict(_spec(make_spec, date(2026, 7, 1), date(2026, 7, 31)))

        assert repository.reads == 1


async def service_verdict(pack: PackSnapshotPort, spec):  # type: ignore[no-untyped-def]
    service = WindowMaturityService(_CurveRepository(), pack)  # type: ignore[arg-type]
    return await service.verdict(spec)
