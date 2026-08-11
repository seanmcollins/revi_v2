"""What a research study says about the edge of the data, and about a grid.

Two defects, both found by reading published studies back.

**A STUDY BYPASSED THE PLATFORM'S OWN CENSORING WARNINGS.** A reading of
claim resolution rate by month, on the service date, at a load ending
2026-08-02, published 84.5% for June, 20.4% for July and 0.0% for August —
every cell "measured", nothing bounded, nothing withheld, no note of any
kind — and the determination cited the fall as proof that something was
going wrong. A July service date has had about thirty days to be billed,
decided and resolved; an August one has had a day. The SAME question asked
conversationally came back with ``adjudication_incomplete`` naming the
share of July that had settled, plus the population and basis caveats
beside it. Every defect was already solved on the other path; deep research
did not route through it. And with no code-level mark, whether a reader was
warned was a coin flip — across four studies in one week the same defect
was used as proof of a crisis, hedged, caught, and explained correctly.

**A GRID WAS PUBLISHED AS A TREND.** A reading of appeal overturn rate by
payer AND by month settled with "rose from 47.2% in Atlas Commercial / Aug
2025 to 54.2% in State Medicaid / May 2026" — the reading's first cell
against its last, which on a payer-by-month grid are two different payers
in two different months.

What is asserted here is that both are now impossible: the settling verdict
is DATA on the reading and on the figures it applies to, folded into the
study's own warnings under the code the rest of the platform uses; and a
grid speaks for one named group at a time or says nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from revi_api.adapters import CalculationTransforms, PackSnapshotPort
from revi_api.deep_research_policy import (
    DEEP_RESEARCH_FILENAME,
    load_deep_research_settings,
)
from revi_api.warning_codes import structured_warnings
from revi_catalog import load_catalog
from revi_investigation.application.deep_research.general import (
    AngleShape,
    AngleVocabulary,
    MeasureAngle,
    PlannedAngle,
    ResearchWalk,
    TimeStep,
)
from revi_investigation.application.deep_research.general_report import (
    build_generalized_report,
)
from revi_investigation.application.deep_research.grammar import TargetPopulation
from revi_investigation.application.deep_research.knowledge import KnowledgeConsultation
from revi_investigation.application.deep_research.loop import Orientation
from revi_investigation.application.deep_research.measures import (
    MeasureAngleRunner,
    MeasureCell,
    MeasureResult,
)
from revi_investigation.application.window_maturity import WindowMaturityService
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import EvidenceProbe
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.scope import AbsoluteRange
from revi_kernel.watermark import DataWatermark
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG = REPO_ROOT / "warehouse" / "catalog"
PACK = REPO_ROOT / "packs" / "base-rcm"

#: The load the study ran against: data through August 2, 2026.
WATERMARK = DataWatermark(
    id="wm_003",
    loaded_at=datetime(2026, 8, 3, 4, 10),
    newest_data_date=date(2026, 8, 2),
)

#: The load's settling curve, as the mock warehouse holds it: eleven
#: settled months near 6,000 adjudicated claims and a July holding 1,544.
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

#: The published reading, verbatim: month, resolved claims, all claims.
RESOLUTION: tuple[tuple[date, int, int], ...] = (
    (date(2026, 1, 1), 5980, 6301),
    (date(2026, 2, 1), 5211, 5488),
    (date(2026, 3, 1), 5740, 6042),
    (date(2026, 6, 1), 5336, 6312),
    (date(2026, 7, 1), 1377, 6752),
    (date(2026, 8, 1), 0, 141),
)

MEASURE = "claim_resolution_rate"


@dataclass
class _Repository:
    """Serves the reading and the settling curve, and counts its reads."""

    reads: int = 0

    async def execute(
        self, probe: EvidenceProbe, *, watermark: DataWatermark
    ) -> EvidenceFrame:
        self.reads += 1
        measure = probe.measures[0].id  # type: ignore[union-attr]
        window = probe.window.range  # type: ignore[union-attr]
        if measure == MEASURE:
            rows = tuple(
                (bucket, resolved, total)
                for bucket, resolved, total in RESOLUTION
                if window.start <= bucket <= window.end
            )
        else:
            # Any other measure is the maturity guard's yardstick, read as
            # a monthly count of what the source has finished with.
            rows = tuple((bucket, count, count) for bucket, count in CURVE)
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
            rows=rows,
            watermark=watermark,
            provenance=ProbeProvenance(probe_id="reading", probe_hash="h"),
            evidence_grade=EvidenceGrade.DIRECT,
        )

    async def list_watermarks(self) -> tuple[DataWatermark, ...]:  # pragma: no cover
        return (WATERMARK,)


class _Cache:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], EvidenceFrame] = {}

    async def get(self, probe_hash: str, watermark_id: str, pack_snapshot_id: str):  # type: ignore[no-untyped-def]
        return self.entries.get((probe_hash, watermark_id, pack_snapshot_id))

    async def put(  # type: ignore[no-untyped-def]
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None:
        self.entries[(probe_hash, watermark_id, pack_snapshot_id)] = frame


@pytest.fixture(scope="module")
def catalog():  # type: ignore[no-untyped-def]
    return load_catalog(CATALOG)


@pytest.fixture(scope="module")
def snapshot():  # type: ignore[no-untyped-def]
    return build_snapshot([load_layer(PACK)])


@pytest.fixture(scope="module")
def settings():  # type: ignore[no-untyped-def]
    return load_deep_research_settings(PACK / DEEP_RESEARCH_FILENAME)


def _runner(catalog, snapshot, *, maturity: bool):  # type: ignore[no-untyped-def]
    pack = PackSnapshotPort(snapshot)
    repository = _Repository()
    return MeasureAngleRunner(
        repository,  # type: ignore[arg-type]
        _Cache(),
        catalog,
        pack,
        CalculationTransforms(snapshot.metric),
        WindowMaturityService(repository, pack) if maturity else None,  # type: ignore[arg-type]
    )


async def _read(catalog, snapshot, settings, *, window: AbsoluteRange, maturity: bool = True):  # type: ignore[no-untyped-def]
    """The published reading: claim resolution rate by month, on service."""
    runner = _runner(catalog, snapshot, maturity=maturity)
    return await runner.run(
        PlannedAngle(
            shape=AngleShape.TREND,
            reason="how much of the book is getting worked all the way to a resolution",
            measure=MeasureAngle(metric_id=MEASURE, step=TimeStep.MONTH, basis="service"),
        ),
        population=TargetPopulation(),
        window=window,
        as_of=WATERMARK.newest_data_date,
        watermark=WATERMARK,
        pack_snapshot_id=snapshot.id,
        policy=settings.estimation_policy(),
    )


def _study(results, catalog, settings, *, window: AbsoluteRange, run_id="dr_censor"):  # type: ignore[no-untyped-def]
    return build_generalized_report(
        run_id=run_id,
        walk=ResearchWalk(
            question="why has our resolution rate been falling",
            population=TargetPopulation(),
            angles=tuple(result.angle for result in results),
        ),
        results=tuple(results),
        orientation=Orientation(
            question="why has our resolution rate been falling",
            population=TargetPopulation(),
            window=window,
            vocabulary=AngleVocabulary(measures={}, bases={}, kinds={}, units={}),
            notes=(),
            concepts=(),
            measures=(),
            cut_for={},
            knowledge=KnowledgeConsultation(
                question="why has our resolution rate been falling",
                terms=(),
                entries=(),
                corpus_size=0,
                statement="Nothing spoke to this.",
            ),
            policy=settings.research,
        ),
        settings=settings,
        catalog=catalog,
        watermark=WATERMARK,
        population_label="everything in your data",
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
        completed_at=datetime(2026, 8, 11, tzinfo=UTC),
        duration_ms=1,
    )


def _figure_by_label(reading, label: str):  # type: ignore[no-untyped-def]
    return next(figure for figure in reading.figures if figure.label == label)


# ---------------------------------------------------------------------------
# F2 — the study raises what the conversational answer raises


class TestTheEdgeOfTheDataIsPublishedAsData:
    async def test_the_settling_caveat_reaches_the_reading_and_the_study(
        self, catalog, snapshot, settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The exact series that published "fell to 0.0%" with nothing on it."""
        window = AbsoluteRange(start=date(2026, 6, 1), end=date(2026, 8, 2))

        result = await _read(catalog, snapshot, settings, window=window)
        draft = _study([result], catalog, settings, window=window)
        reading = draft.report.readings[0]

        assert result.maturity, "the guard the conversational path runs never ran"
        settling = [
            sentence
            for sentence in reading.warnings
            if sentence.startswith("adjudication_incomplete:")
        ]
        assert settling, f"the reading carries no settling caveat: {reading.warnings}"
        assert any("July 2026" in sentence for sentence in settling), (
            "the caveat names no period, so a reader cannot tell which figure it governs"
        )
        # …and the study's own list is the FOLD of the readings' lists.
        for sentence in reading.warnings:
            assert sentence in draft.warnings
        codes = {warning.code for warning in structured_warnings(draft.warnings)}
        assert "ADJUDICATION_INCOMPLETE" in codes, codes

    async def test_the_censored_months_carry_the_mark_on_the_figure(
        self, catalog, snapshot, settings
    ) -> None:  # type: ignore[no-untyped-def]
        """A caveat only in prose is one an exporter drops and a chart ignores."""
        window = AbsoluteRange(start=date(2026, 6, 1), end=date(2026, 8, 2))

        result = await _read(catalog, snapshot, settings, window=window)
        draft = _study([result], catalog, settings, window=window)
        reading = draft.report.readings[0]

        assert _figure_by_label(reading, "Jul 2026").censored, (
            "the month that was a quarter settled is published as a measurement"
        )
        assert _figure_by_label(reading, "Aug 2026").censored, (
            "two days of a month, published as a month"
        )
        assert not _figure_by_label(reading, "Jun 2026").censored, (
            "a settled month must not be caveated: this is a guard, not a blanket"
        )
        chart = next(chart for chart in draft.report.charts if chart.id == reading.chart_id)
        marked = {row.x for row in chart.rows if row.provisional}
        assert marked == {"Jul 2026", "Aug 2026"}, marked

    async def test_a_window_clear_of_the_edge_earns_no_caveat(
        self, catalog, snapshot, settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The guard is a verdict about a window, not a caveat on every study."""
        window = AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 3, 31))

        result = await _read(catalog, snapshot, settings, window=window)
        draft = _study([result], catalog, settings, window=window)
        reading = draft.report.readings[0]

        assert result.maturity == ()
        assert not any(
            sentence.startswith("adjudication_incomplete:") for sentence in reading.warnings
        ), reading.warnings
        assert not any(
            sentence.startswith("adjudication_incomplete:") for sentence in draft.warnings
        )
        assert not any(figure.censored for figure in reading.figures)

    async def test_with_no_maturity_service_the_study_still_runs(
        self, catalog, snapshot, settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The guard is a dependency of the composition root, not of a reading.

        A deployment or a test that wires no maturity service publishes
        everything it published before: the same figures, the same verdict
        sentence, the same findings. What it loses is the CURVE — the
        judgement that July holds a fraction of a settled month — because
        that judgement needs the load's own settling curve and inventing
        one would be worse than silence.

        The calendar-partial mark survives, deliberately. That August
        covers two days of a month is arithmetic over the load's own edge:
        no curve, no service and no judgement is involved, and withholding
        it because an optional dependency is missing would reintroduce the
        coin flip this whole change exists to remove.
        """
        window = AbsoluteRange(start=date(2026, 6, 1), end=date(2026, 8, 2))

        result = await _read(catalog, snapshot, settings, window=window, maturity=False)
        draft = _study([result], catalog, settings, window=window)
        reading = draft.report.readings[0]

        assert result.maturity == ()
        assert result.refusal is None
        assert [figure.label for figure in reading.figures] == [
            "Jun 2026",
            "Jul 2026",
            "Aug 2026",
        ]
        assert reading.settled
        assert draft.report.findings
        assert not _figure_by_label(reading, "Jul 2026").censored, (
            "a settling verdict without a settling curve is a guess"
        )
        assert _figure_by_label(reading, "Aug 2026").censored


# ---------------------------------------------------------------------------
# F4 (deterministic half) — a grid is not a series


def _grid_result() -> MeasureResult:
    """The published payer-by-month reading, as it came back.

    Cells sorted by month, which is what the runner does to a trend — and
    what made the first and last cells two different payers.
    """
    cells = tuple(
        MeasureCell(
            label=f"{payer} / {period}",
            parts=(("payer", payer), ("month", bucket)),
            value=value,
            population=population,
            numerator=int(value * population),
            period_label=period,
            group_label=payer,
        )
        for payer, period, bucket, value, population in (
            ("Atlas Commercial", "Aug 2025", "2025-08-01", Decimal("0.472"), 900),
            ("State Medicaid", "Aug 2025", "2025-08-01", Decimal("0.610"), 400),
            ("Atlas Commercial", "May 2026", "2026-05-01", Decimal("0.489"), 950),
            ("State Medicaid", "May 2026", "2026-05-01", Decimal("0.542"), 380),
        )
    )
    return MeasureResult(
        angle=PlannedAngle(
            shape=AngleShape.TREND,
            reason="whether the overturn rate moved, and whether it moved everywhere",
            measure=MeasureAngle(
                metric_id="appeal_overturn_rate", cut_by=("payer",), step=TimeStep.MONTH
            ),
        ),
        metric_id="appeal_overturn_rate",
        title="Appeal overturn rate by month, by payer",
        unit="ratio",
        grade="direct",
        cells=cells,
        window=AbsoluteRange(start=date(2025, 8, 1), end=date(2026, 5, 31)),
        basis="remit",
    )


class TestAGridIsNotASeries:
    def test_a_payer_by_month_reading_never_publishes_a_cross_group_trend(
        self, catalog, settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The R5 failure, asserted by its exact shape."""
        window = AbsoluteRange(start=date(2025, 8, 1), end=date(2026, 5, 31))

        draft = _study([_grid_result()], catalog, settings, window=window)
        settled = draft.report.readings[0].settled

        assert " / " not in settled, (
            f"a composite payer-and-month label reached a trend sentence: {settled}"
        )
        assert "Atlas Commercial / Aug 2025" not in settled
        assert "State Medicaid / May 2026" not in settled
        # One group, named, and only its own two periods.
        assert settled.startswith("Within Atlas Commercial"), settled
        assert "State Medicaid" not in settled, settled
        assert "Aug 2025" in settled and "May 2026" in settled

    def test_the_group_it_speaks_for_is_the_one_with_the_records_behind_it(
        self, catalog, settings
    ) -> None:  # type: ignore[no-untyped-def]
        window = AbsoluteRange(start=date(2025, 8, 1), end=date(2026, 5, 31))

        settled = _study([_grid_result()], catalog, settings, window=window).report.readings[
            0
        ].settled

        # Atlas Commercial carries 1,850 records against State Medicaid's
        # 780, and 47.2% → 48.9% is Atlas Commercial's own movement.
        assert "rose from 47.2% in Aug 2025 to 48.9% in May 2026" in settled, settled
        assert "The one other group on this reading moved separately" in settled, settled

    def test_the_chart_draws_one_line_per_group(self, catalog, settings) -> None:  # type: ignore[no-untyped-def]
        """The same defect in pixels: one line through every cell joined
        Atlas Commercial's August to State Medicaid's."""
        window = AbsoluteRange(start=date(2025, 8, 1), end=date(2026, 5, 31))

        draft = _study([_grid_result()], catalog, settings, window=window)
        chart = draft.report.charts[0]

        assert chart.series, "a grid drawn with no series is one line through two payers"
        assert {row.series for row in chart.rows} == {"Atlas Commercial", "State Medicaid"}
        assert {row.x for row in chart.rows} == {"Aug 2025", "May 2026"}
        assert chart.axis_order == ["Aug 2025", "May 2026"]

    def test_a_grid_where_no_group_has_two_periods_states_no_movement(
        self, catalog, settings
    ) -> None:  # type: ignore[no-untyped-def]
        window = AbsoluteRange(start=date(2025, 8, 1), end=date(2026, 5, 31))
        result = _grid_result()
        one_period = tuple(cell for cell in result.cells if cell.period_label == "Aug 2025")

        draft = _study(
            [
                MeasureResult(
                    angle=result.angle,
                    metric_id=result.metric_id,
                    title=result.title,
                    unit=result.unit,
                    grade=result.grade,
                    cells=one_period,
                    window=result.window,
                    basis=result.basis,
                )
            ],
            catalog,
            settings,
            window=window,
        )
        settled = draft.report.readings[0].settled

        assert "rose" not in settled and "fell" not in settled, settled
        assert "no single group has two published periods" in settled, settled


# ---------------------------------------------------------------------------
# item 3 — the signals sit on the reading that produced them


def _flagged_result() -> MeasureResult:
    """A reading that substituted a basis, bounded half its field and
    refused to order what was left."""
    cells = (
        MeasureCell(
            label="Atlas Commercial",
            parts=(("payer", "Atlas Commercial"),),
            value=Decimal("0.121"),
            population=900,
            numerator=109,
            group_label="Atlas Commercial",
        ),
        MeasureCell(
            label="Cedar Ridge Health",
            parts=(("payer", "Cedar Ridge Health"),),
            value=Decimal("0.240"),
            population=15,
            numerator=4,
            bounded=True,
            group_label="Cedar Ridge Health",
        ),
    )
    return MeasureResult(
        angle=PlannedAngle(
            shape=AngleShape.STRATIFIED_RATES,
            reason="where the denial rate sits, payer by payer",
            measure=MeasureAngle(metric_id="denial_rate", cut_by=("payer",)),
        ),
        metric_id="denial_rate",
        title="Denial rate by payer",
        unit="ratio",
        grade="direct",
        cells=cells,
        ranking_refused=(
            "1 of the 2 groups here show a ceiling rather than a figure, so ordering them "
            "would rank ceilings against measurements."
        ),
        notes=(
            "alternate_basis_used: denial rate is defined on the remittance date and this "
            "data binds that date only on the remit views, so it was read on the service "
            "date instead.",
        ),
        window=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 3, 31)),
        basis="service",
    )


def _clean_result() -> MeasureResult:
    return MeasureResult(
        angle=PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="what the book looks like overall",
            measure=MeasureAngle(metric_id="denial_rate"),
        ),
        metric_id="denial_rate",
        title="Denial rate",
        unit="ratio",
        grade="direct",
        cells=(
            MeasureCell(
                label="everything in this population",
                parts=(),
                value=Decimal("0.118"),
                population=6000,
                numerator=708,
            ),
        ),
        window=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 3, 31)),
        basis="service",
    )


class TestASignalSitsOnTheReadingThatProducedIt:
    def test_the_basis_substitution_the_ceilings_and_the_refusal_are_on_it(
        self, catalog, settings
    ) -> None:  # type: ignore[no-untyped-def]
        window = AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 3, 31))

        draft = _study([_flagged_result(), _clean_result()], catalog, settings, window=window)
        flagged, clean = draft.report.readings

        prefixes = [sentence.split(":")[0] for sentence in flagged.warnings]
        assert "alternate_basis_used" in prefixes, flagged.warnings
        assert "suppression_bounded" in prefixes, flagged.warnings
        assert "ranking_refused" in prefixes, flagged.warnings
        # …and only on the reading that produced them.
        assert clean.warnings == []
        for sentence in flagged.warnings:
            assert sentence in draft.warnings
        codes = {warning.code for warning in structured_warnings(draft.warnings)}
        assert {"ALTERNATE_BASIS_USED", "SUPPRESSION_BOUNDED", "RANKING_REFUSED"} <= codes


# ---------------------------------------------------------------------------
# F10 — a bound must not ship the arithmetic that undoes it


def _leaky_result() -> MeasureResult:
    """A reading whose non-measured cells arrive CARRYING their counts.

    The review's evidence, verbatim: ``display: "≤ 37.0%" alongside
    population: 27, successes: 10 → 10/27 = 37.037% exactly``. The engine's
    own path nulls a withheld row's counts upstream, so the withheld cell
    here is built the hostile way on purpose: the wire rule under test is
    about what a figure payload may carry, whatever constructed the cell.
    """
    return MeasureResult(
        angle=PlannedAngle(
            shape=AngleShape.STRATIFIED_RATES,
            reason="where the appeal overturn rate sits, payer by payer",
            measure=MeasureAngle(metric_id="appeal_overturn_rate", cut_by=("payer",)),
        ),
        metric_id="appeal_overturn_rate",
        title="Appeal overturn rate by payer",
        unit="ratio",
        grade="direct",
        cells=(
            MeasureCell(
                label="Atlas Commercial",
                parts=(("payer", "Atlas Commercial"),),
                value=Decimal("0.472"),
                population=900,
                numerator=425,
                interval_low=Decimal("0.44"),
                interval_high=Decimal("0.50"),
                group_label="Atlas Commercial",
            ),
            MeasureCell(
                label="Cedar Ridge Health",
                parts=(("payer", "Cedar Ridge Health"),),
                value=Decimal("0.37037"),
                population=27,
                numerator=10,
                bounded=True,
                group_label="Cedar Ridge Health",
            ),
            MeasureCell(
                label="Harbor Point Mutual",
                parts=(("payer", "Harbor Point Mutual"),),
                value=None,
                population=4,
                numerator=1,
                withheld=True,
                group_label="Harbor Point Mutual",
            ),
        ),
        window=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 3, 31)),
        basis="remit",
    )


class TestABoundKeepsItsSecret:
    def test_no_bounded_or_withheld_figure_lets_a_reader_recompute_the_value(
        self, catalog, settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The R-review evidence: ≤ 37.0% beside 10/27 IS 37.037%."""
        window = AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 3, 31))

        draft = _study([_leaky_result()], catalog, settings, window=window)
        reading = draft.report.readings[0]

        for figure in reading.figures:
            if figure.bounded or figure.withheld:
                assert figure.population is None, (
                    f"{figure.label}: a non-measured figure ships its denominator"
                )
                assert figure.successes is None, (
                    f"{figure.label}: a non-measured figure ships its numerator"
                )
                assert figure.interval is None, (
                    f"{figure.label}: an interval is arithmetic over the same two counts"
                )
        # …while a measured figure keeps its provenance whole.
        measured = _figure_by_label(reading, "Atlas Commercial")
        assert measured.population == 900 and measured.successes == 425
        assert measured.interval is not None

    def test_the_cohort_size_survives_where_it_is_a_reason_not_a_value(
        self, catalog, settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The census still counts the groups; the refusal still counts the
        field. Neither can be inverted into a suppressed figure."""
        window = AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 3, 31))

        draft = _study([_flagged_result()], catalog, settings, window=window)
        reading = draft.report.readings[0]

        assert any(s.startswith("suppression_bounded:") for s in reading.warnings)
        assert reading.ranking_refused


# ---------------------------------------------------------------------------
# F10 — the study's censoring copy is the study's, and it agrees in number


class TestTheCensoringCopyIsTheStudys:
    def test_no_borrowed_recovery_sentence_about_payers_answering(self) -> None:
        """"Records the payer has already answered" is true of the denial
        review and false of a resolution study; neither wording family may
        appear here."""
        from revi_investigation.application.deep_research.general_report import (
            censoring_words,
        )

        for statement in censoring_words(
            readings=3, measured=28, bounded=4, withheld=2,
            population=5398, data_edge="Aug 2, 2026",
        ):
            assert "payer has already answered" not in statement, statement
            assert "settled far enough" not in statement, statement

    def test_the_one_item_case_agrees_in_number(self) -> None:
        """"1 reading here measure" and "1 group publish" read as machine
        output, which is the opposite of what this surface is for."""
        from revi_investigation.application.deep_research.general_report import (
            censoring_words,
        )

        singular = censoring_words(
            readings=1, measured=1, bounded=1, withheld=1,
            population=141, data_edge="Aug 2, 2026",
        )
        text = " ".join(singular)
        assert "1 reading here measures" in text, text
        assert "1 group publishes a ceiling" in text, text
        assert "1 group publishes nothing at all" in text, text
        assert "is left out of every ordering" in text, text
        assert "1 group carries a measurement" in text, text
        for broken in (
            "1 reading here measure ",
            "1 group publish ",
            "1 group carry ",
            "and are left out",
        ):
            assert broken not in text, text

    def test_the_many_item_case_still_reads_plural(self) -> None:
        from revi_investigation.application.deep_research.general_report import (
            censoring_words,
        )

        text = " ".join(
            censoring_words(
                readings=3, measured=28, bounded=4, withheld=2,
                population=5398, data_edge="Aug 2, 2026",
            )
        )
        assert "3 readings here measure " in text, text
        assert "4 groups publish a ceiling" in text, text
        assert "2 groups publish nothing at all" in text, text
        assert "are left out of every ordering" in text, text
        assert "28 groups carry a measurement" in text, text


def _trend_result(points: list[tuple[str, str, bool]]) -> MeasureResult:
    """A plain month series, with the settling verdict already on its cells."""
    cells = tuple(
        MeasureCell(
            label=label,
            parts=(("month", label),),
            value=Decimal(value),
            population=1000,
            numerator=int(Decimal(value) * 1000),
            period_label=label,
            censored=censored,
        )
        for label, value, censored in points
    )
    return MeasureResult(
        angle=PlannedAngle(
            shape=AngleShape.TREND,
            reason="how claim resolution rate has moved over the period asked about",
            measure=MeasureAngle(metric_id="claim_resolution_rate", step=TimeStep.MONTH),
        ),
        metric_id="claim_resolution_rate",
        title="Claim resolution rate by month",
        unit="ratio",
        grade="direct",
        cells=cells,
        window=AbsoluteRange(start=date(2025, 8, 1), end=date(2026, 8, 2)),
        basis="service",
    )


class TestADirectionNeverEndsOnAnUnsettledPeriod:
    """The review's headline artifact, as a sentence rather than a warning.

    "Claim resolution rate fell from 94.8% in Aug 2025 to 0.0% in Aug 2026"
    is arithmetically true of the published cells and says nothing about
    performance: an August service date has had a day to bill, adjudicate
    and resolve. The determination used it as proof of a crisis.
    """

    def test_the_movement_is_read_to_the_last_settled_period(self) -> None:
        from revi_investigation.application.deep_research.general_report import _settled

        result = _trend_result(
            [
                ("Aug 2025", "0.948", False),
                ("Jun 2026", "0.845", False),
                ("Jul 2026", "0.204", True),
                ("Aug 2026", "0.000", True),
            ]
        )
        said = _settled(result)
        assert "0.0% in Aug 2026" not in said
        assert "Jun 2026" in said
        assert "Jul 2026 and Aug 2026" in said
        assert "not finished settling" in said

    def test_a_series_clear_of_the_edge_still_states_its_direction(self) -> None:
        from revi_investigation.application.deep_research.general_report import _settled

        result = _trend_result(
            [("Aug 2025", "0.948", False), ("Jan 2026", "0.912", False)]
        )
        said = _settled(result)
        assert "fell from" in said
        assert "not finished settling" not in said

    def test_a_reading_that_is_all_edge_states_no_movement_at_all(self) -> None:
        from revi_investigation.application.deep_research.general_report import _settled

        result = _trend_result(
            [("Jul 2026", "0.204", True), ("Aug 2026", "0.000", True)]
        )
        said = _settled(result)
        assert "no movement is stated" in said
        assert "fell" not in said

