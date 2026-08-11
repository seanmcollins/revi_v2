"""The generalized research REPORT, over the generated warehouse.

The loop's own reference suite proves that a research question is
oriented, planned, executed and iterated. This one is about what a reader
finally receives: that the artifact is the walk that produced it, that
every figure on it is provenance-complete and rederivable, that the marks
survive the trip to the wire, and that the parts a study earns are the
parts it gets — a censoring disclosure only where outcome-like data was
read, an ordering only where one can be published honestly.

What is asserted is what a reader depends on.

**The report IS the walk.** Every reading on the report is an angle the
walk records, in the order it ran, with the reason the walk recorded for
it. A report whose readings and record disagree has no provenance at all.

**Every figure carries its read.** A published figure names the read that
produced it, the period it covers and the date it was measured on. A
figure without those three is a number with a story rather than a source.

**A value on the wire equals the value in the data.** One published
figure per study is recomputed by independent SQL straight against DuckDB
— the discipline ``make warehouse-diff`` applies to the conversational
surface — and must match to the digit.

**A bound is a mark, never a number.** A ceiling is published as a ceiling,
never counted as a measurement, never entered into an ordering, and never
put in a finding the composer can cite.

**The recorded walk replays with no planner calls.** A permalink, a replay
and the harness re-run what was DECIDED and may not re-decide it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from revi_api.adapters import CalculationTransforms, PackSnapshotPort
from revi_api.deep_research_policy import (
    DEEP_RESEARCH_FILENAME,
    load_deep_research_settings,
)
from revi_api.warning_codes import structured_warnings
from revi_catalog import load_catalog
from revi_connector_duckdb import DuckDbAnalyticalRepository
from revi_investigation.application.deep_research.general import (
    AngleShape,
    MeasureAngle,
    PlannedAngle,
    ResearchWalk,
    TimeStep,
    walk_fingerprint,
)
from revi_investigation.application.deep_research.general_report import (
    build_generalized_report,
)
from revi_investigation.application.deep_research.grammar import TargetPopulation
from revi_investigation.application.deep_research.loop import (
    GeneralizedResearchLoop,
    ResearchOrienter,
    default_window,
    leads_of,
    population_words,
)
from revi_investigation.application.deep_research.measures import MeasureAngleRunner
from revi_investigation.application.discovery import DiscoveryService
from revi_kernel.frame import EvidenceFrame
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"
CATALOG = REPO_ROOT / "warehouse" / "catalog"
PACK = REPO_ROOT / "packs" / "base-rcm"

#: The live acceptance question, verbatim.
AR_QUESTION = (
    "research why our A/R over 90 has been climbing and what it will take to bring it down"
)
#: A question whose readings are rates over a counted population — the one
#: shape a censoring disclosure applies to.
PAYER_QUESTION = (
    "which payers have the worst denial rate, and is the gap real or just their volume"
)

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not WAREHOUSE.is_file(),
        reason="generated warehouse missing — run: make warehouse",
    ),
]


class _Cache:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], EvidenceFrame] = {}

    async def get(self, probe_hash: str, watermark_id: str, pack_snapshot_id: str):
        return self.entries.get((probe_hash, watermark_id, pack_snapshot_id))

    async def put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None:
        self.entries[(probe_hash, watermark_id, pack_snapshot_id)] = frame


class _ScriptedPlanner:
    """A control plane that chooses a plan the fallback would not.

    The same discipline the loop's own suite applies: if the report's
    invariants only held over the deterministic set, this file would be
    testing the deterministic set. It also counts its own calls, which is
    how replay proves it re-executes rather than re-decides.
    """

    def __init__(self) -> None:
        self.opened = 0
        self.continued = 0

    async def open(self, orientation, *, budget):
        self.opened += 1
        return (
            [
                PlannedAngle(
                    shape=AngleShape.STRATIFIED_RATES,
                    reason="where the denial rate sits, payer by payer",
                    measure=MeasureAngle(metric_id="denial_rate", cut_by=("payer",)),
                ),
                PlannedAngle(
                    shape=AngleShape.CONTRAST,
                    reason="whether the payer gap is real or the size of the groups behind it",
                    measure=MeasureAngle(metric_id="denial_rate", cut_by=("payer",)),
                ),
                PlannedAngle(
                    shape=AngleShape.TREND,
                    reason="how the denial rate has moved over the period asked about",
                    measure=MeasureAngle(metric_id="denial_rate", step=TimeStep.MONTH),
                ),
            ],
            "Read the rate by payer, test the widest gap, then look at how it moved.",
        )

    async def next_round(self, orientation, rounds, *, index, remaining):
        self.continued += 1
        proposed = []
        for lead in leads_of(rounds, orientation.policy)[:1]:
            proposed.append(
                PlannedAngle(
                    shape=AngleShape.MEASURE_PROFILE,
                    round=index,
                    chases=lead.title,
                    reason=f"{lead.why} — cutting inside {lead.shown} by claim type next",
                    measure=MeasureAngle(
                        metric_id="denial_rate",
                        cut_by=("claim_type",),
                        within=((lead.dimension, lead.value),),
                    ),
                )
            )
        # One proposal into a population nothing separated, every round. The
        # thresholds must drop it, and the DROP must reach the report.
        proposed.append(
            PlannedAngle(
                shape=AngleShape.MEASURE_PROFILE,
                round=index,
                chases="a reading that did not separate",
                reason="this one looked interesting to me",
                measure=MeasureAngle(
                    metric_id="denial_rate",
                    cut_by=("facility",),
                    within=(("payer", "Atlas Commercial"),),
                ),
            )
        )
        return proposed


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(str(CATALOG))


@pytest.fixture(scope="module")
def snapshot():
    return build_snapshot([load_layer(PACK)])


@pytest.fixture(scope="module")
def settings():
    return load_deep_research_settings(PACK / DEEP_RESEARCH_FILENAME)


@pytest.fixture(scope="module")
def repository(catalog, snapshot):
    return DuckDbAnalyticalRepository(str(WAREHOUSE), catalog, snapshot.metric)


@pytest.fixture(scope="module")
async def watermark(repository):
    return (await repository.list_watermarks())[-1]


@pytest.fixture(scope="module")
def cache() -> _Cache:
    return _Cache()


def _loop(repository, cache, catalog, snapshot, planner=None):
    pack = PackSnapshotPort(snapshot)
    return GeneralizedResearchLoop(
        ResearchOrienter(DiscoveryService(repository, cache, catalog, pack), catalog, pack),
        MeasureAngleRunner(
            repository, cache, catalog, pack, CalculationTransforms(snapshot.metric)
        ),
        planner=planner,
    )


async def _study(loop, *, question, settings, watermark, snapshot, catalog, run_id="dr_ref"):
    walk, results, orientation = await loop.run(
        question=question,
        population=TargetPopulation(),
        settings=settings,
        watermark=watermark,
        pack_snapshot_id=snapshot.id,
    )
    draft = build_generalized_report(
        run_id=run_id,
        walk=walk,
        results=results,
        orientation=orientation,
        settings=settings,
        catalog=catalog,
        watermark=watermark,
        population_label=population_words(orientation.population),
        created_at=datetime(2026, 8, 11, 4, tzinfo=UTC),
        completed_at=datetime(2026, 8, 11, 4, 1, tzinfo=UTC),
        duration_ms=61_000,
    )
    return walk, results, orientation, draft


@pytest.fixture(scope="module")
async def ar_study(repository, cache, catalog, snapshot, settings, watermark):
    """The A/R question, planned by nobody — the deterministic fallback."""
    loop = _loop(repository, cache, catalog, snapshot)
    return await _study(
        loop,
        question=AR_QUESTION,
        settings=settings,
        watermark=watermark,
        snapshot=snapshot,
        catalog=catalog,
    )


@pytest.fixture(scope="module")
def planner() -> _ScriptedPlanner:
    return _ScriptedPlanner()


@pytest.fixture(scope="module")
async def payer_study(repository, cache, catalog, snapshot, settings, watermark, planner):
    """The payer question, planned by something outside the loop."""
    loop = _loop(repository, cache, catalog, snapshot, planner)
    walk, results, orientation, draft = await _study(
        loop,
        question=PAYER_QUESTION,
        settings=settings,
        watermark=watermark,
        snapshot=snapshot,
        catalog=catalog,
        run_id="dr_ref_payer",
    )
    return loop, walk, results, orientation, draft


@pytest.fixture(scope="module")
def studies(ar_study, payer_study):
    return (
        (AR_QUESTION, ar_study[0], ar_study[1], ar_study[3]),
        (PAYER_QUESTION, payer_study[1], payer_study[2], payer_study[4]),
    )


# ---------------------------------------------------------------------------
# structure


class TestTheShapeOfAStudy:
    async def test_it_is_a_determination_and_not_a_priced_headline(self, ar_study) -> None:
        """A study's answer is prose, not a total.

        The recoverability review's report opens on an expected-recovery
        figure because that IS its answer. "Why has A/R over 90 been
        climbing" has no such figure, and the report shape must not have a
        slot inviting one.
        """
        _, _, _, draft = ar_study
        report = draft.report
        assert report.kind == "generalized_research"
        assert report.determination.question == AR_QUESTION
        assert report.determination.rests_on
        assert not hasattr(report, "headline")

    async def test_every_reading_names_why_it_was_read(self, studies) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                assert reading.reason, f"{question}: {reading.title} has no stated cause"

    async def test_every_reading_that_published_something_says_what_it_settled(
        self, studies
    ) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                if reading.refusal:
                    continue
                assert reading.settled, f"{question}: {reading.title} settled nothing and says nothing"

    async def test_the_report_carries_what_was_established_before_it_chose(
        self, ar_study
    ) -> None:
        _, _, _, draft = ar_study
        assert draft.report.path_choices, "a study built without orientation is a guess"
        for choice in draft.report.path_choices:
            assert choice.statement

    async def test_the_consultation_reaches_the_report_as_titles_only(self, ar_study) -> None:
        """A note shapes which reading runs and can never shape a number.

        Its key points are deliberately not on the wire: publishing them
        beside a measured figure would put an industry number next to a
        measured one on the same screen.
        """
        _, _, orientation, draft = ar_study
        report = draft.report
        assert report.knowledge_statement
        assert len(report.knowledge_consulted) == len(orientation.knowledge.entries)
        for note in report.knowledge_consulted:
            assert note.title
            assert not hasattr(note, "key_points")

    async def test_the_disclosure_that_no_note_priced_anything_is_published(
        self, studies
    ) -> None:
        for question, _, _, draft in studies:
            codes = {warning.code for warning in structured_warnings(draft.warnings)}
            assert "DEEP_RESEARCH_NO_PRIOR" in codes, question
            assert any("measured on your own data" in line for line in draft.disclosures)


# ---------------------------------------------------------------------------
# the walk is the plan


class TestTheReportIsTheWalk:
    async def test_the_readings_are_exactly_the_angles_the_walk_records(
        self, studies
    ) -> None:
        for question, walk, results, draft in studies:
            assert [reading.title for reading in draft.report.readings] == [
                result.title for result in results
            ], question
            assert [reading.reason for reading in draft.report.readings] == [
                angle.reason for angle in walk.angles
            ], question
            assert [reading.round for reading in draft.report.readings] == [
                angle.round for angle in walk.angles
            ], question

    async def test_every_round_the_walk_took_appears_once(self, studies) -> None:
        for question, walk, _, draft in studies:
            rounds = draft.report.walk.rounds
            assert [round_.index for round_ in rounds] == list(range(walk.rounds)), question
            assert draft.report.walk.rounds_taken == walk.rounds
            assert draft.report.walk.rounds_allowed == walk.budget

    async def test_every_reading_is_filed_under_the_round_that_chose_it(
        self, studies
    ) -> None:
        for question, _, _, draft in studies:
            by_id = {reading.id: reading for reading in draft.report.readings}
            for round_ in draft.report.walk.rounds:
                for reading_id in round_.readings:
                    assert by_id[reading_id].round == round_.index, question

    async def test_every_walk_step_states_its_cause(self, studies) -> None:
        for question, _, _, draft in studies:
            for round_ in draft.report.walk.rounds:
                for step in round_.steps:
                    assert step.reason, f"{question}: {step.action} recorded with no cause"

    async def test_a_later_round_says_why_it_exists(self, ar_study) -> None:
        _, _, _, draft = ar_study
        later = [round_ for round_ in draft.report.walk.rounds if round_.index > 0]
        assert later, "a why-and-what-will-it-take question earns more than one pass"
        assert all(round_.reason for round_ in later)

    async def test_who_chose_the_readings_is_on_the_report(self, ar_study, payer_study) -> None:
        """A fallback presented as a choice is a small lie about how the
        analysis was decided."""
        _, _, _, fallback = ar_study
        _, _, _, _, planned = payer_study
        assert fallback.report.walk.authored_by == "revi"
        assert planned.report.walk.authored_by == "model"
        assert planned.report.walk.rationale.startswith("Read the rate by payer")

    async def test_a_gated_chase_is_recorded_and_counted(self, payer_study) -> None:
        """The model proposes; the content decides what is significant — and
        a run that quietly declined to chase looks identical to one that
        never thought of it."""
        _, _, _, _, draft = payer_study
        dropped = [
            step
            for round_ in draft.report.walk.rounds
            for step in round_.steps
            if step.action == "drop" and "I did not go inside" in step.reason
        ]
        assert dropped, "the out-of-bounds proposal was neither run nor recorded"
        assert "You can change this anytime." in dropped[0].reason
        codes = {warning.code for warning in structured_warnings(draft.warnings)}
        assert "DEEP_RESEARCH_CHASE_GATED" in codes


# ---------------------------------------------------------------------------
# provenance


class TestEveryFigureIsProvenanceComplete:
    async def test_every_reading_names_the_read_behind_it(self, studies) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                if reading.refusal:
                    continue
                assert len(reading.read_fingerprint) == 64, question
                assert reading.window_label
                assert reading.basis_label
                assert reading.metric_id
                assert reading.measure_label

    async def test_the_report_is_pinned_to_the_load_it_read(self, studies, watermark) -> None:
        for question, _, _, draft in studies:
            assert draft.report.data_edge_date == watermark.newest_data_date, question
            assert draft.report.data_load_label
            assert draft.header.watermark_id == watermark.id

    async def test_every_figure_carries_the_breakdown_it_is_over(self, studies) -> None:
        for question, _, results, draft in studies:
            for reading, result in zip(draft.report.readings, results, strict=True):
                for figure, cell in zip(reading.figures, result.cells, strict=True):
                    assert figure.label == cell.label, question
                    assert [(part.dimension, part.value) for part in figure.parts] == list(
                        cell.parts
                    )
                    # Both spellings: the raw value a chase narrows on, and
                    # the words the reader saw.
                    for part in figure.parts:
                        assert part.dimension_label
                        assert part.value_label

    async def test_the_counts_on_a_reading_agree_with_its_figures(self, studies) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                measured = [f for f in reading.figures if f.evidence == "measured"]
                assert reading.figures_published == len(measured), question
                assert reading.figures_withheld == len(reading.figures) - len(measured)


# ---------------------------------------------------------------------------
# marks


class TestTheMarksSurviveTheWire:
    async def test_a_measured_figure_carries_a_value_and_no_mark(self, studies) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                for figure in reading.figures:
                    if figure.evidence != "measured":
                        continue
                    assert figure.value is not None, question
                    assert not figure.bounded and not figure.withheld
                    assert figure.display and "≤" not in figure.display

    async def test_a_ceiling_reads_as_a_ceiling_and_is_never_measured(self, studies) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                for figure in reading.figures:
                    if not figure.bounded:
                        continue
                    assert figure.evidence == "not_estimable", question
                    assert figure.display.startswith("≤")

    async def test_a_withheld_figure_publishes_nothing(self, studies) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                for figure in reading.figures:
                    if not figure.withheld:
                        continue
                    assert figure.value is None, question
                    assert figure.evidence == "not_estimable"

    async def test_an_interval_exists_only_where_a_population_counts_it(
        self, studies
    ) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                for figure in reading.figures:
                    if figure.interval is None:
                        continue
                    assert figure.population is not None and figure.population > 0, question
                    assert figure.successes is not None

    async def test_only_a_measured_figure_reaches_a_finding(self, studies) -> None:
        """The composer may cite a finding; a ceiling is not one.

        A finding's values are what the grounding validator admits, so a
        bounded figure in one would licence a sentence stating a ceiling as
        a measurement.
        """
        for question, _, _, draft in studies:
            by_title = {reading.title: reading for reading in draft.report.readings}
            for finding in draft.report.findings:
                reading = by_title[finding.title]
                published = {
                    figure.label
                    for figure in reading.figures
                    if figure.evidence == "measured"
                }
                for value in finding.values:
                    name = value.name.split(" — records behind it")[0]
                    assert name in published, f"{question}: {name} is not a measured figure"

    async def test_an_ordering_is_published_only_where_it_can_be_honest(
        self, studies
    ) -> None:
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                if reading.ranking_refused:
                    assert not reading.ranked, question
                    assert "ceiling" in reading.ranking_refused

    async def test_a_chart_is_drawn_only_where_two_marks_support_one(
        self, studies
    ) -> None:
        for question, _, _, draft in studies:
            charts = {chart.id: chart for chart in draft.report.charts}
            for reading in draft.report.readings:
                measured = [f for f in reading.figures if f.evidence == "measured"]
                if reading.chart_id == "":
                    assert len(measured) < 2, question
                    continue
                chart = charts[reading.chart_id]
                assert len(chart.rows) >= 2
                # One mark is a figure; a line needs three ordered points.
                if chart.chart_type == "line":
                    assert len(chart.rows) >= 3
                assert chart.unit == reading.unit


# ---------------------------------------------------------------------------
# censoring, only where it applies


class TestCensoringWhereItApplies:
    async def test_a_study_over_rates_publishes_what_the_edge_cost(
        self, payer_study
    ) -> None:
        _, _, results, _, draft = payer_study
        assert any(
            cell.population is not None for result in results for cell in result.cells
        ), "the payer question read no rate over a counted population"
        censoring = draft.report.censoring
        assert censoring is not None, "outcome-like data was read and nothing disclosed it"
        assert censoring.statements
        assert censoring.data_edge_date == draft.report.data_edge_date
        assert censoring.figures_measured > 0
        assert censoring.readings_over_outcomes > 0
        codes = {warning.code for warning in structured_warnings(draft.warnings)}
        assert "DEEP_RESEARCH_CENSORING" in codes

    async def test_a_study_over_levels_publishes_no_censoring_at_all(
        self, ar_study
    ) -> None:
        """A dollars or days figure has no population to be censored out of,
        so a censoring block beside one would be a disclosure about
        nothing."""
        _, results, _, draft = ar_study[0], ar_study[1], ar_study[2], ar_study[3]
        assert not any(
            cell.population is not None for result in results for cell in result.cells
        )
        assert draft.report.censoring is None

    async def test_the_counts_in_the_disclosure_are_the_figures_it_describes(
        self, payer_study
    ) -> None:
        _, _, _, _, draft = payer_study
        censoring = draft.report.censoring
        assert censoring is not None
        counted = [
            reading
            for reading in draft.report.readings
            if any(figure.population is not None for figure in reading.figures)
        ]
        assert censoring.readings_over_outcomes == len(counted)
        assert censoring.figures_measured == sum(
            1
            for reading in counted
            for figure in reading.figures
            if figure.evidence == "measured"
        )


# ---------------------------------------------------------------------------
# the numbers


class TestTheValuesAreTheData:
    @pytest.fixture
    def connection(self, watermark):
        con = duckdb.connect(str(WAREHOUSE), read_only=True)
        con.execute(f"SET search_path = '{watermark.id.replace('wm_', 'snap_')}'")
        try:
            yield con
        finally:
            con.close()

    async def test_a_published_rate_matches_naive_sql(
        self, payer_study, connection, watermark, settings
    ) -> None:
        _, _, results, _, draft = payer_study
        reading = next(
            reading
            for reading in draft.report.readings
            if "denial rate by payer" in reading.title
        )
        result = next(r for r in results if r.title == reading.title)
        figure = next(
            figure for figure in reading.figures if figure.label == "Atlas Commercial"
        )
        assert figure.evidence == "measured" and figure.value is not None
        window = default_window(watermark, settings.research)
        assert result.basis in {"service", "submission"}
        numerator, denominator = connection.execute(
            f"""
            SELECT
                COUNT(DISTINCT CASE WHEN NOT clean_claim THEN claim_id END) AS num,
                COUNT(DISTINCT claim_id) AS den
            FROM v_claim
            WHERE payer_name = ?
              AND status <> 'OPEN'
              AND {result.basis}_date BETWEEN ? AND ?
            """,
            ["Atlas Commercial", window.start, window.end],
        ).fetchall()[0]
        published = Decimal(figure.value)
        assert figure.successes == numerator
        assert figure.population == denominator
        assert published == (Decimal(numerator) / Decimal(denominator)).quantize(published)

    async def test_a_finding_publishes_dollars_as_dollars(self, ar_study) -> None:
        """Money travels in cents and is NAMED in dollars.

        Handing a composer a bare integer of cents beside a label reading
        "denied dollars" invites a hundredfold overstatement that every
        downstream check passes, because the digits are real and only the
        unit is wrong.
        """
        _, _, _, draft = ar_study
        by_title = {reading.title: reading for reading in draft.report.readings}
        for finding in draft.report.findings:
            reading = by_title[finding.title]
            if reading.unit != "money_cents":
                continue
            for value in finding.values:
                if value.name.endswith("records behind it"):
                    continue
                figure = next(f for f in reading.figures if f.label == value.name)
                assert figure.value is not None
                assert Decimal(str(value.value)) == Decimal(figure.value) / Decimal(100)

    async def test_the_display_string_is_the_value_in_its_own_unit(
        self, studies
    ) -> None:
        """No client re-derives dollars from cents or a percent from a ratio."""
        for question, _, _, draft in studies:
            for reading in draft.report.readings:
                for figure in reading.figures:
                    if figure.evidence != "measured":
                        continue
                    if reading.unit == "money_cents":
                        assert figure.display.startswith("$"), question
                    elif reading.unit == "ratio":
                        assert figure.display.endswith("%"), question


# ---------------------------------------------------------------------------
# determinism


#: What a report says about ITSELF rather than about the data: how long it
#: took, and whether a read was already in the cache. Two runs of one walk
#: at one load must agree on every published FIGURE; requiring them to
#: agree on their own wall clock would be requiring the machine to be
#: idle, and a test nobody can keep green is a test that gets deleted.
_VOLATILE = ("duration_ms", "cache_hit")


def _orientation_for(watermark, settings):
    """The thinnest orientation a report needs: the window it was read over.

    Everything else on the report under test comes from the reading itself,
    so building a real one here would be running a whole study to assert a
    property of one measure.
    """
    from revi_investigation.application.deep_research.general import AngleVocabulary
    from revi_investigation.application.deep_research.knowledge import (
        KnowledgeConsultation,
    )
    from revi_investigation.application.deep_research.loop import Orientation

    return Orientation(
        question="how has bill lag moved",
        population=TargetPopulation(),
        window=default_window(watermark, settings.research),
        vocabulary=AngleVocabulary(measures={}, bases={}, kinds={}, units={}),
        notes=(),
        concepts=(),
        measures=(),
        cut_for={},
        knowledge=KnowledgeConsultation(
            question="how has bill lag moved",
            terms=(),
            entries=(),
            corpus_size=0,
            statement="Nothing spoke to this.",
        ),
        policy=settings.research,
    )


def _stable(report) -> dict:
    """One report with its own timings normalized away."""
    payload = report.model_dump(mode="json")

    def scrub(node: object) -> object:
        if isinstance(node, dict):
            return {
                key: (0 if key in _VOLATILE else scrub(value))
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [scrub(item) for item in node]
        return node

    return scrub(payload)  # type: ignore[return-value]


class TestDeterminism:
    async def test_the_same_walk_at_the_same_load_publishes_the_same_report(
        self, repository, cache, catalog, snapshot, settings, watermark, ar_study
    ) -> None:
        walk, _, _, draft = ar_study
        loop = _loop(repository, cache, catalog, snapshot)
        again_walk, _, _, again = await _study(
            loop,
            question=AR_QUESTION,
            settings=settings,
            watermark=watermark,
            snapshot=snapshot,
            catalog=catalog,
        )
        assert walk_fingerprint(again_walk) == walk_fingerprint(walk)
        assert _stable(again.report) == _stable(draft.report)
        assert again.warnings == draft.warnings
        assert again.disclosures == draft.disclosures

    async def test_the_recorded_walk_replays_with_no_planner_calls(
        self, payer_study, settings, watermark, snapshot, catalog, planner
    ) -> None:
        """"The recorded path is the plan." A permalink, a replay and the
        harness re-run what was DECIDED and may not re-decide it."""
        loop, walk, _results, orientation, draft = payer_study
        before = (planner.opened, planner.continued)
        again = await loop.replay(
            walk,
            settings=settings,
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
            window=default_window(watermark, settings.research),
        )
        assert (planner.opened, planner.continued) == before
        replayed = build_generalized_report(
            run_id=draft.report.id,
            walk=walk,
            results=again,
            orientation=orientation,
            settings=settings,
            catalog=catalog,
            watermark=watermark,
            population_label=population_words(orientation.population),
            created_at=draft.report.created_at,
            completed_at=draft.report.completed_at,
            duration_ms=draft.report.duration_ms,
        )
        assert [
            (reading.title, [(f.label, f.value) for f in reading.figures])
            for reading in replayed.report.readings
        ] == [
            (reading.title, [(f.label, f.value) for f in reading.figures])
            for reading in draft.report.readings
        ]

    async def test_the_walk_fingerprint_ignores_the_words_and_keeps_the_reads(
        self, ar_study
    ) -> None:
        """Two runs sharing a fingerprint must publish byte-identical
        numbers. The REASONS are excluded deliberately: they are what was
        SAID, and a report whose numbers changed because a sentence was
        worded differently would have a reproducibility claim it could not
        keep."""
        from dataclasses import replace

        walk, _, _, _ = ar_study
        reworded = replace(
            walk,
            angles=tuple(
                replace(angle, reason=f"{angle.reason} (said another way)")
                for angle in walk.angles
            ),
        )
        assert walk_fingerprint(reworded) == walk_fingerprint(walk)


class TestARateIsOnlyARateWhereBothHalvesSaySo:
    """A mean is not a proportion, and no interval may be drawn around one.

    The live A/R study found this: ``bill_lag_days`` sums DAYS over a count
    of claims, its denominator is a ``count_distinct``, and a
    denominator-only test admitted it as outcome-like data. The estimator
    was handed "48,377 successes out of 6,011" and raised. Had the mean
    been under 1 it would have published a confidence interval around an
    average instead — a confidence statement about nothing, counted into
    the censoring disclosure as though a population stood behind it.
    """

    def test_a_proportion_is_a_counted_subset_over_a_counted_whole(
        self, snapshot
    ) -> None:
        from revi_investigation.application.deep_research.measures import is_proportion

        denial_rate = snapshot.metric("denial_rate")
        assert denial_rate is not None
        assert is_proportion(denial_rate), "denied claims over adjudicated claims IS a rate"

    def test_a_mean_over_a_counted_population_is_not_a_rate(self, snapshot) -> None:
        from revi_investigation.application.deep_research.measures import is_proportion

        mean = snapshot.metric("bill_lag_days")
        assert mean is not None
        # The shape that fooled a denominator-only rule.
        assert mean.denominator is not None
        assert not is_proportion(mean)

    def test_an_additive_measure_is_not_a_rate(self, snapshot) -> None:
        from revi_investigation.application.deep_research.measures import is_proportion

        dollars = snapshot.metric("denied_dollars")
        assert dollars is not None and dollars.denominator is None
        assert not is_proportion(dollars)

    async def test_no_reading_over_a_mean_publishes_a_population_or_an_interval(
        self, repository, cache, catalog, snapshot, settings, watermark
    ) -> None:
        """End to end: the reading that raised, run and published."""
        from revi_investigation.application.deep_research.measures import (
            MeasureAngleRunner,
        )

        runner = MeasureAngleRunner(
            repository,
            cache,
            catalog,
            PackSnapshotPort(snapshot),
            CalculationTransforms(snapshot.metric),
        )
        angle = PlannedAngle(
            shape=AngleShape.TREND,
            reason="how bill lag has moved, broken out by claim type",
            measure=MeasureAngle(
                metric_id="bill_lag_days", cut_by=("claim_type",), step=TimeStep.MONTH
            ),
        )
        result = await runner.run(
            angle,
            population=TargetPopulation(),
            window=default_window(watermark, settings.research),
            as_of=watermark.newest_data_date,
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
            policy=settings.estimation_policy(),
        )
        assert result.refusal is None
        assert result.cells, "the reading published nothing at all"
        for cell in result.cells:
            assert cell.population is None
            assert cell.numerator is None
            assert cell.interval_low is None and cell.interval_high is None
        # …and it therefore earns no censoring disclosure, because there is
        # no population it could have been censored out of.
        draft = build_generalized_report(
            run_id="dr_mean",
            walk=ResearchWalk(
                question="how has bill lag moved",
                population=TargetPopulation(),
                angles=(result.angle,),
            ),
            results=(result,),
            orientation=_orientation_for(watermark, settings),
            settings=settings,
            catalog=catalog,
            watermark=watermark,
            population_label="everything in your data",
            created_at=datetime(2026, 8, 11, tzinfo=UTC),
            completed_at=datetime(2026, 8, 11, tzinfo=UTC),
            duration_ms=1,
        )
        assert draft.report.censoring is None
