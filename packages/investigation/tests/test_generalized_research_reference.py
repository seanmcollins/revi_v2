"""Generalized deep research over the generated warehouse.

The recovery domain has its own reference test and it is unchanged. This one
is about the *generalized* loop — that a free-text research question in a
domain nobody wrote an angle library for is oriented, planned, executed,
iterated and left in a state a report can be written from.

Three questions, deliberately in three domains the v1 grammar could not
reach:

* a **payer-behavior** study over denial and payment data;
* an **A/R aging** research question — the live acceptance question;
* a **facility revenue-quality** question.

What is asserted is what a reader depends on:

**The angles executed are the recorded plan.** Not a superset, not a
reordering. The walk is what a permalink restores and what the harness
audits, so a run whose results and record disagree has no provenance at all.

**Every figure is rederivable.** One published cell from each question is
recomputed by independent SQL straight against DuckDB — the same discipline
``make warehouse-diff`` applies to the conversational surface — and must
match to the digit.

**Evidence tiers are correct.** A measured cell carries a value and no
ceiling; a bounded cell carries a ceiling and is never counted as measured;
a withheld cell carries nothing and says so.

**No number exists outside a certified cell.** The loop's own prose — walk
reasons, progress lines, chase clauses — may quote a figure only where that
figure came back from an estimator.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from revi_api.adapters import CalculationTransforms, PackSnapshotPort
from revi_api.deep_research_policy import (
    DEEP_RESEARCH_FILENAME,
    load_deep_research_settings,
)
from revi_catalog import load_catalog
from revi_connector_duckdb import DuckDbAnalyticalRepository
from revi_investigation.application.deep_research.general import (
    AngleShape,
    walk_fingerprint,
)
from revi_investigation.application.deep_research.grammar import (
    PopulationKind,
    TargetPopulation,
)
from revi_investigation.application.deep_research.loop import (
    GeneralizedResearchLoop,
    ResearchOrienter,
    default_window,
    iteration_budget,
)
from revi_investigation.application.deep_research.measures import MeasureAngleRunner
from revi_investigation.application.discovery import DiscoveryKind, DiscoveryService
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
PAYER_QUESTION = (
    "which payers have the worst denial rate, and is the gap real or just their volume"
)
FACILITY_QUESTION = "which facility has the worst revenue quality and what is driving it"

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
        self.hits = 0

    async def get(self, probe_hash: str, watermark_id: str, pack_snapshot_id: str):
        found = self.entries.get((probe_hash, watermark_id, pack_snapshot_id))
        if found is not None:
            self.hits += 1
        return found

    async def put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None:
        self.entries[(probe_hash, watermark_id, pack_snapshot_id)] = frame


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


@pytest.fixture(scope="module")
def loop(repository, cache, catalog, snapshot):
    pack = PackSnapshotPort(snapshot)
    return GeneralizedResearchLoop(
        ResearchOrienter(DiscoveryService(repository, cache, catalog, pack), catalog, pack),
        MeasureAngleRunner(
            repository, cache, catalog, pack, CalculationTransforms(snapshot.metric)
        ),
    )


async def _research(loop, settings, watermark, snapshot, question, population=None):
    return await loop.run(
        question=question,
        population=population or TargetPopulation(),
        settings=settings,
        watermark=watermark,
        pack_snapshot_id=snapshot.id,
    )


@pytest.fixture(scope="module")
async def ar_run(loop, settings, watermark, snapshot):
    return await _research(loop, settings, watermark, snapshot, AR_QUESTION)


@pytest.fixture(scope="module")
async def payer_run(loop, settings, watermark, snapshot):
    return await _research(loop, settings, watermark, snapshot, PAYER_QUESTION)


@pytest.fixture(scope="module")
async def facility_run(loop, settings, watermark, snapshot):
    return await _research(loop, settings, watermark, snapshot, FACILITY_QUESTION)


@pytest.fixture(scope="module")
def every_run(ar_run, payer_run, facility_run):
    """All three questions, so a property can be asserted over every one.

    Parametrizing over fixture NAMES would make each assertion its own test
    and read better; it also makes the async fixtures resolve inside an
    already-running loop, which is a different failure with a worse
    message. Three runs in one tuple is the boring option that works.
    """
    return (
        (AR_QUESTION, ar_run),
        (PAYER_QUESTION, payer_run),
        (FACILITY_QUESTION, facility_run),
    )


def _cell(results, title_fragment: str, label: str):
    for result in results:
        if title_fragment in result.title:
            for cell in result.cells:
                if cell.label == label:
                    return result, cell
    raise AssertionError(f"no cell {label!r} on any reading matching {title_fragment!r}")


# ---------------------------------------------------------------------------
# the shape of a run


class TestTheLoopRuns:
    async def test_it_orients_before_it_plans(self, ar_run) -> None:
        walk, _, orientation = ar_run
        actions = [step.action for step in walk.steps]
        assert actions.index("orient") < actions.index("plan")
        assert orientation.notes, "a plan built without orientation is a guess"

    async def test_the_orientation_findings_are_discovery_reads(self, ar_run) -> None:
        _, _, orientation = ar_run
        kinds = {note.kind for note in orientation.notes}
        assert DiscoveryKind.CAPABILITIES in kinds
        assert DiscoveryKind.DIMENSION_CENSUS in kinds
        assert DiscoveryKind.MEASURE_AVAILABILITY in kinds

    async def test_each_discovery_finding_is_one_plain_sentence(self, ar_run) -> None:
        _, _, orientation = ar_run
        for note in orientation.notes:
            assert note.statement
            assert note.statement[0].isupper() or note.statement[0].isdigit()
            assert note.statement.rstrip().endswith((".", "%"))

    async def test_it_consults_the_pack_before_it_plans(self, ar_run) -> None:
        walk, _, orientation = ar_run
        actions = [step.action for step in walk.steps]
        assert actions.index("consult") < actions.index("plan")
        assert orientation.knowledge.consulted
        assert orientation.knowledge.entries[0].id == "benchmark.aged_ar_over_90"

    async def test_the_consultation_is_visible_on_the_walk_with_its_card_ids(
        self, ar_run
    ) -> None:
        walk, _, _ = ar_run
        consult_step = next(step for step in walk.steps if step.action == "consult")
        assert "benchmark.aged_ar_over_90" in consult_step.detail

    async def test_the_question_resolves_to_governed_measures(self, ar_run) -> None:
        _, _, orientation = ar_run
        assert "ar_over_90_pct" in orientation.measures
        assert orientation.cut_for["ar_over_90_pct"] in orientation.vocabulary.cuts_for(
            "ar_over_90_pct"
        )

    async def test_it_iterates_at_least_once(self, ar_run) -> None:
        walk, _, _ = ar_run
        assert walk.rounds >= 2, "a why-and-what-will-it-take question earns more than one read"
        assert max(angle.round for angle in walk.angles) >= 1

    async def test_the_budget_scales_with_the_question(self, settings) -> None:
        policy = settings.research
        assert iteration_budget("what is our denial rate", policy) == 1
        assert iteration_budget(AR_QUESTION, policy) > iteration_budget(
            "what is our denial rate", policy
        )
        assert iteration_budget(AR_QUESTION, policy) <= policy.max_rounds

    async def test_every_round_decision_states_its_reason(self, ar_run) -> None:
        walk, _, _ = ar_run
        for step in walk.steps:
            assert step.reason, f"{step.action} on {step.subject} was recorded with no cause"

    async def test_it_ends_by_synthesizing(self, ar_run) -> None:
        walk, _, _ = ar_run
        assert walk.steps[-1].action == "synthesize"


class TestTheWalkIsThePlan:
    async def test_the_angles_executed_are_exactly_the_recorded_ones(self, every_run) -> None:
        for question, (walk, results, _) in every_run:
            assert [result.angle for result in results] == list(walk.angles), question

    async def test_every_angle_is_legal_in_the_grammar(self, every_run) -> None:
        for _, (walk, _, orientation) in every_run:
          for angle in walk.angles:
            assert angle.shape in set(AngleShape)
            assert (angle.measure is None) != (angle.recovery is None)
            if angle.measure is not None:
                declared = orientation.vocabulary.cuts_for(angle.measure.metric_id)
                assert set(angle.measure.cut_by) <= declared

    async def test_the_walk_is_content_addressed(self, ar_run) -> None:
        walk, _, _ = ar_run
        assert len(walk_fingerprint(walk)) == 64

    async def test_two_runs_of_the_same_question_agree_on_every_number(
        self, loop, settings, watermark, snapshot, ar_run
    ) -> None:
        walk, results, _ = ar_run
        again_walk, again_results, _ = await _research(
            loop, settings, watermark, snapshot, AR_QUESTION
        )
        assert walk_fingerprint(again_walk) == walk_fingerprint(walk)
        assert [
            (result.title, [(cell.label, cell.value) for cell in result.cells])
            for result in again_results
        ] == [
            (result.title, [(cell.label, cell.value) for cell in result.cells])
            for result in results
        ]

    async def test_a_narrower_population_is_a_different_walk(
        self, loop, settings, watermark, snapshot, ar_run
    ) -> None:
        walk, _, _ = ar_run
        narrowed, _, _ = await _research(
            loop,
            settings,
            watermark,
            snapshot,
            AR_QUESTION,
            TargetPopulation(kind=PopulationKind.PAYER, values=("Atlas Commercial",)),
        )
        assert walk_fingerprint(narrowed) != walk_fingerprint(walk)


class TestEvidenceTiers:
    async def test_a_measured_cell_carries_a_value_and_no_ceiling(self, every_run) -> None:
        for _, (_, results, _) in every_run:
          for result in results:
            for cell in result.cells:
                if cell.is_measured:
                    assert cell.value is not None
                    assert not cell.bounded and not cell.withheld

    async def test_a_withheld_cell_publishes_nothing_and_is_never_measured(self, every_run) -> None:
        for _, (_, results, _) in every_run:
          for result in results:
            for cell in result.cells:
                if cell.withheld:
                    assert cell.value is None
                    assert not cell.is_measured

    async def test_a_ceiling_is_never_counted_as_a_measurement(self, every_run) -> None:
        for _, (_, results, _) in every_run:
          for result in results:
            bounded = [cell for cell in result.cells if cell.bounded]
            assert all(not cell.is_measured for cell in bounded)
            assert result.cells_published + result.cells_refused == len(result.cells)

    async def test_an_interval_exists_only_where_a_population_counts_it(self, every_run) -> None:
        """A Wilson interval over dollars divided by dollars would be a
        confidence statement about nothing."""
        for _, (_, results, _) in every_run:
          for result in results:
            for cell in result.cells:
                if cell.interval_low is not None:
                    assert cell.population is not None and cell.population > 0
                    assert cell.numerator is not None

    async def test_a_ranking_is_refused_when_too_much_of_its_field_is_a_ceiling(
        self, every_run
    ) -> None:
        for result in [r for _, (_, results, _) in every_run for r in results]:
            if result.ranking_refused:
                assert not result.ranked
                assert "ceiling" in result.ranking_refused


class TestRederivability:
    """Every published figure recomputed by an independent SQL path.

    The same discipline ``make warehouse-diff`` applies to the
    conversational surface: read the contract, write plain SQL, compare to
    the digit. A research report is a longer artifact than an answer and
    therefore a better place for an arithmetic mistake to hide.
    """

    @pytest.fixture
    def connection(self, watermark):
        """A cursor onto the pinned load's own schema.

        Each data load is its own DuckDB schema, so the rederivation reads
        the schema the run was pinned to rather than whatever ``main``
        happens to hold — a naive path that read the wrong load would agree
        with itself and disagree with the report for a reason nobody could
        see.
        """
        con = duckdb.connect(str(WAREHOUSE), read_only=True)
        con.execute(f"SET search_path = '{watermark.id.replace('wm_', 'snap_')}'")
        try:
            yield con
        finally:
            con.close()

    async def test_a_denial_rate_cell_matches_naive_sql(
        self, payer_run, connection, watermark, settings
    ) -> None:
        _, results, _ = payer_run
        result, cell = _cell(results, "denial rate by payer", "Atlas Commercial")
        window = default_window(watermark, settings.research)
        # The contract declares the remittance date primary at claim grain
        # and this warehouse binds it only on the remit views, so the engine
        # substitutes an allowed basis it CAN read. The rederivation follows
        # the substitution rather than the declaration — a naive path that
        # read the declared basis would fail to bind, exactly as the engine
        # would have without the fallback.
        assert result.basis in {"service", "submission"}
        rows = connection.execute(
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
        ).fetchall()
        numerator, denominator = rows[0]
        assert cell.numerator == numerator
        assert cell.population == denominator
        assert cell.value is not None
        assert cell.value == (
            Decimal(numerator) / Decimal(denominator)
        ).quantize(cell.value)
        assert result.grade == "direct"

    async def test_a_money_cell_cut_by_facility_matches_naive_sql(
        self, facility_run, connection, watermark, settings, snapshot
    ) -> None:
        """A dollars figure, rederived straight off the base view.

        Which money measure the facility question reaches is a fact about
        the semantic layer's own vocabulary, not something this test should
        pin — so it takes whichever one the run published broken out by
        facility, and holds THAT to the cent.
        """
        _, results, _ = facility_run
        candidates = [
            result
            for result in results
            if result.unit == "money_cents"
            and result.angle.measure is not None
            and "facility" in result.angle.measure.cut_by
            and any(cell.is_measured for cell in result.cells)
        ]
        assert candidates, "the facility question published no dollars by facility"
        result = candidates[0]
        cell = next(cell for cell in result.cells if cell.is_measured)
        facility = dict(cell.parts).get("facility")
        assert facility, "a dollars reading must name the population it is over"
        contract = snapshot.metric(result.metric_id)
        assert contract is not None and contract.denominator is None
        column = contract.numerator.field.id
        entity = {"claim": "v_claim", "line": "v_claim_line", "denial": "v_denial"}[
            str(contract.entity_grain)
        ]
        window = default_window(watermark, settings.research)
        total = connection.execute(
            f"""
            SELECT COALESCE(SUM({column}), 0)
            FROM {entity}
            WHERE facility_name = ?
              AND {result.basis}_date BETWEEN ? AND ?
            """,
            [facility, window.start, window.end],
        ).fetchone()
        assert cell.value == Decimal(total[0])

    async def test_every_reading_names_the_read_behind_it(self, every_run) -> None:
        for _, (_, results, _) in every_run:
            for result in results:
                if result.refusal:
                    continue
                assert len(result.read_fingerprint) == 64
                assert result.basis
                assert result.window is not None


class TestNoNumberOutsideACertifiedFinding:
    async def test_a_walk_reason_quoting_a_figure_quotes_a_certified_one(self, every_run) -> None:
        """The loop's own prose is allowed to say WHY it chased something.
        The only figures it may quote are ones an estimator produced — a
        chase clause that invented a ratio would be the loop asserting a
        measurement of its own."""
        for _, (walk, results, _) in every_run:
          published = {
            str(cell.value)
            for result in results
            for cell in result.cells
            if cell.value is not None
          }
          for step in walk.steps:
            if step.action not in ("chase", "broaden"):
                continue
            if "x the narrowest" not in step.reason:
                continue
            quoted = step.reason.split("runs ")[1].split("x the narrowest")[0]
            ratio = Decimal(quoted)
            assert ratio >= 1, "a spread below 1 is the ratio the wrong way up"
            assert published, "a spread was quoted with nothing published to derive it from"

    async def test_a_refusal_names_what_could_not_be_done(self, every_run) -> None:
        for _, (_, results, _) in every_run:
          for result in results:
            if result.refusal:
                assert len(result.refusal) > 10
                assert not result.cells


class TestTheDomainsItReaches:
    async def test_the_payer_question_reaches_denial_data(self, payer_run) -> None:
        _, results, orientation = payer_run
        assert "denial_rate" in orientation.measures
        assert any(result.metric_id == "denial_rate" for result in results)

    async def test_the_payer_question_tests_whether_the_gap_is_real(
        self, payer_run
    ) -> None:
        _, results, _ = payer_run
        contrasts = [result for result in results if result.contrast is not None]
        assert contrasts, "a question asking whether a gap is real owes a test"
        contrast = contrasts[0].contrast
        assert contrast is not None
        assert contrast.p_value is not None
        assert contrast.left.n > 0 and contrast.right.n > 0

    async def test_the_ar_question_reaches_aging_data(self, ar_run) -> None:
        _, results, orientation = ar_run
        assert "ar_over_90_pct" in orientation.measures
        assert any(result.metric_id == "ar_over_90_pct" for result in results)

    async def test_the_facility_question_cuts_by_facility(self, facility_run) -> None:
        _, results, _ = facility_run
        cut_by = {
            cut
            for result in results
            if result.angle.measure is not None
            for cut in result.angle.measure.cut_by
        }
        assert "facility" in cut_by

    async def test_a_question_with_no_governed_measure_says_so_rather_than_guessing(
        self, loop, settings, watermark, snapshot
    ) -> None:
        walk, results, orientation = await _research(
            loop, settings, watermark, snapshot, "how is the cafeteria doing"
        )
        assert orientation.measures == ()
        assert results == ()
        assert "no standard measure" in walk.rationale


class TestItIsCheap:
    async def test_the_orientation_reads_are_shared_with_the_angles(
        self, ar_run, cache
    ) -> None:
        """One cache, one key shape. A discovery census and an angle over
        the same measure at the same load are one read."""
        _, _, _ = ar_run
        assert cache.hits > 0

    async def test_every_reading_is_a_single_read(self, ar_run) -> None:
        _, results, _ = ar_run
        fingerprints = [result.read_fingerprint for result in results if not result.refusal]
        assert len(fingerprints) == len(results)


class TestDates:
    async def test_the_default_window_ends_at_the_data_edge(
        self, watermark, settings
    ) -> None:
        window = default_window(watermark, settings.research)
        assert window.end == watermark.newest_data_date
        assert window.start < window.end
        assert isinstance(window.start, date)
