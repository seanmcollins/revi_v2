"""The discovery family against the generated warehouse.

Discovery reads are the loop's orientation: they decide *how* a question is
answered here, and a wrong one sends every angle after it down a path the
data does not support. So they are tested against the real catalog, the real
pack and the real warehouse — a fake would only prove the fake agrees with
itself, and the whole point of a coverage figure is that nobody can predict
it from content.

What is asserted, in the order it matters:

* a census counts in a GOVERNED ruler and never invents one;
* the unpopulated group is counted, because it is the coverage answer;
* concept-to-path prefers the strongest EVIDENCE, not the fullest column;
* a path with no certified expression refuses and names the gap;
* every answer is deterministic, cached, and provenance-complete.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from revi_api.adapters import PackSnapshotPort
from revi_catalog import load_catalog
from revi_connector_duckdb import DuckDbAnalyticalRepository
from revi_investigation.application.discovery import (
    UNPOPULATED,
    DiscoveryKind,
    DiscoveryRefused,
    DiscoveryService,
)
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import EntityGrain
from revi_kernel.scope import AbsoluteRange
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"
CATALOG = REPO_ROOT / "warehouse" / "catalog"
PACK = REPO_ROOT / "packs" / "base-rcm"

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not WAREHOUSE.is_file(),
        reason="generated warehouse missing — run: make warehouse",
    ),
]


class _Cache:
    """The ordinary evidence cache, in process, counting its own hits."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], EvidenceFrame] = {}
        self.hits = 0
        self.puts = 0

    async def get(self, probe_hash: str, watermark_id: str, pack_snapshot_id: str):
        found = self.entries.get((probe_hash, watermark_id, pack_snapshot_id))
        if found is not None:
            self.hits += 1
        return found

    async def put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None:
        self.entries[(probe_hash, watermark_id, pack_snapshot_id)] = frame
        self.puts += 1


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(str(CATALOG))


@pytest.fixture(scope="module")
def snapshot():
    return build_snapshot([load_layer(PACK)])


@pytest.fixture(scope="module")
def pack(snapshot):
    return PackSnapshotPort(snapshot)


@pytest.fixture(scope="module")
def repository(catalog, snapshot):
    return DuckDbAnalyticalRepository(str(WAREHOUSE), catalog, snapshot.metric)


@pytest.fixture(scope="module")
async def watermark(repository):
    return (await repository.list_watermarks())[-1]


@pytest.fixture
def cache() -> _Cache:
    return _Cache()


@pytest.fixture
def discovery(repository, cache, catalog, pack) -> DiscoveryService:
    return DiscoveryService(repository, cache, catalog, pack)


@pytest.fixture
def window(watermark) -> AbsoluteRange:
    return AbsoluteRange(start=date(2025, 1, 1), end=watermark.newest_data_date)


def _run(discovery, snapshot, watermark, window, dimension: str):
    return discovery.dimension_census(
        dimension, window=window, watermark=watermark, pack_snapshot_id=snapshot.id
    )


class TestCapabilityNegotiation:
    def test_it_reports_what_the_catalog_and_pack_declare(
        self, discovery, snapshot, watermark, catalog
    ) -> None:
        report = discovery.capabilities(
            watermark=watermark, pack_snapshot_id=snapshot.id
        )
        assert set(report.certified_dimensions) == {
            d.id for d in catalog.dimensions if d.certified
        }
        assert set(report.uncertified_dimensions) == {
            d.id for d in catalog.dimensions if not d.certified
        }
        assert len(report.metrics) == len(snapshot.metric_contracts)
        assert report.suppression_threshold == catalog.suppression.threshold

    def test_it_costs_no_warehouse_read(
        self, discovery, snapshot, watermark, cache
    ) -> None:
        """Declarations are declarations. Paying for a query to learn what a
        contract says would make the cheapest question the slowest."""
        discovery.capabilities(watermark=watermark, pack_snapshot_id=snapshot.id)
        assert cache.puts == 0
        assert cache.hits == 0


class TestTheDimensionCensus:
    async def test_it_counts_in_a_governed_ruler(
        self, discovery, snapshot, watermark, window, pack
    ) -> None:
        census = await _run(discovery, snapshot, watermark, window, "payer")
        contract = pack.metric(census.ruler)
        assert contract is not None, "the ruler must be a governed measure"
        assert str(contract.unit) == "count"
        assert contract.denominator is None
        assert "payer" in {dim.id for dim in contract.scope_dimensions}

    async def test_it_reproduces_the_warehouses_own_payer_list(
        self, discovery, snapshot, watermark, window, repository
    ) -> None:
        census = await _run(discovery, snapshot, watermark, window, "payer")
        assert census.cardinality == 12
        assert census.coverage == Decimal(1).quantize(Decimal("1E-6"))
        assert {value.value for value in census.values} >= {
            "Atlas Commercial",
            "Federal Medicare",
            "Northbridge Commercial",
        }
        assert sum(value.units for value in census.values) == census.units

    async def test_the_values_are_ordered_by_size_then_name(
        self, discovery, snapshot, watermark, window
    ) -> None:
        census = await _run(discovery, snapshot, watermark, window, "payer")
        ordered = sorted(census.values, key=lambda v: (-v.units, v.value))
        assert list(census.values) == ordered

    async def test_the_unpopulated_group_is_counted_not_dropped(
        self, discovery, snapshot, watermark, window
    ) -> None:
        """Coverage IS the unpopulated group. A census that discarded it
        would report every field as fully populated, because the records
        lacking it fall out of a grouping the census never printed."""
        census = await _run(discovery, snapshot, watermark, window, "payer")
        listed = sum(value.units for value in census.values if value.value != UNPOPULATED)
        assert census.populated == listed
        assert census.units >= census.populated

    async def test_a_dimension_no_measure_declares_is_refused_by_name(
        self, discovery, snapshot, watermark, window
    ) -> None:
        with pytest.raises(DiscoveryRefused) as refusal:
            await _run(discovery, snapshot, watermark, window, "cob_mismatch_flag")
        assert "definitions library" in str(refusal.value)

    async def test_a_dimension_the_catalog_does_not_define_is_refused(
        self, discovery, snapshot, watermark, window
    ) -> None:
        with pytest.raises(DiscoveryRefused):
            await _run(discovery, snapshot, watermark, window, "not_a_dimension")

    async def test_the_statement_quotes_its_own_numbers(
        self, discovery, snapshot, watermark, window
    ) -> None:
        census = await _run(discovery, snapshot, watermark, window, "payer")
        assert f"{census.cardinality:,}" in census.statement
        assert f"{census.units:,}" in census.statement

    async def test_a_second_ask_is_free_and_identical(
        self, discovery, snapshot, watermark, window, cache
    ) -> None:
        first = await _run(discovery, snapshot, watermark, window, "payer")
        reads_before = cache.puts
        second = await _run(discovery, snapshot, watermark, window, "payer")
        assert cache.puts == reads_before
        assert second.values == first.values
        assert second.provenance.request_key == first.provenance.request_key

    async def test_it_is_provenance_complete(
        self, discovery, snapshot, watermark, window
    ) -> None:
        census = await _run(discovery, snapshot, watermark, window, "payer")
        provenance = census.provenance
        assert provenance.kind is DiscoveryKind.DIMENSION_CENSUS
        assert provenance.watermark_id == watermark.id
        assert provenance.pack_snapshot_id == snapshot.id
        assert len(provenance.reads) == 1
        assert len(provenance.reads[0]) == 64

    async def test_a_preferred_grain_is_honoured_when_it_can_be(
        self, discovery, snapshot, watermark, window
    ) -> None:
        at_denial = await discovery.dimension_census(
            "payer",
            window=window,
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
            prefer=EntityGrain.DENIAL,
        )
        assert at_denial.entity == "denial"
        assert at_denial.units < 200_000, "denials are a smaller population than claims"


class TestConceptToPath:
    async def test_it_returns_every_declared_binding_for_the_concept(
        self, discovery, snapshot, watermark, window, pack
    ) -> None:
        resolution = await discovery.concept_paths(
            "cob", window=window, watermark=watermark, pack_snapshot_id=snapshot.id
        )
        assert resolution.concept_id == "cob"
        declared = {binding.field_id for binding in pack.concept_bindings("cob")}
        assert {e.field_id for e in resolution.expressions} == declared

    async def test_the_preferred_path_is_the_strongest_evidence_not_the_fullest(
        self, discovery, snapshot, watermark, window
    ) -> None:
        """A well-populated proxy is still a proxy.

        Here ``carc`` is filled on every denial and is declared PROXY for
        coordination of benefits; the mismatch flag marks a fraction of a
        percent of claims and is DIRECT. Letting fill rate outrank the grade
        law is how a payer's reason code becomes a fact about coverage.
        """
        resolution = await discovery.concept_paths(
            "cob", window=window, watermark=watermark, pack_snapshot_id=snapshot.id
        )
        assert resolution.has_path
        preferred = resolution.preferred
        assert preferred is not None
        assert preferred.field_id == "cob_mismatch_flag"
        assert str(preferred.strength) == "direct"
        carc = next(e for e in resolution.expressions if e.field_id == "carc")
        assert str(carc.strength) == "proxy"
        assert (carc.coverage or Decimal(0)) > (preferred.coverage or Decimal(0))

    async def test_a_population_marker_is_measured_through_its_governed_measure(
        self, discovery, snapshot, watermark, window
    ) -> None:
        resolution = await discovery.concept_paths(
            "cob", window=window, watermark=watermark, pack_snapshot_id=snapshot.id
        )
        flag = next(e for e in resolution.expressions if e.field_id == "cob_mismatch_flag")
        assert flag.kind == "population"
        assert flag.measure_id == "cob_mismatch_claims"
        assert 0 < flag.populated < flag.units

    async def test_a_quantity_is_named_as_a_quantity_and_carries_no_coverage(
        self, discovery, snapshot, watermark, window
    ) -> None:
        resolution = await discovery.concept_paths(
            "denial", window=window, watermark=watermark, pack_snapshot_id=snapshot.id
        )
        money = next(
            e for e in resolution.expressions if e.field_id == "denied_amount_cents"
        )
        assert money.kind == "measure"
        assert money.coverage is None

    async def test_the_statement_names_what_was_read_and_what_was_passed_over(
        self, discovery, snapshot, watermark, window
    ) -> None:
        resolution = await discovery.concept_paths(
            "cob", window=window, watermark=watermark, pack_snapshot_id=snapshot.id
        )
        statement = resolution.statement
        assert "cob mismatch flag" in statement
        assert "carc" in statement or "payer sequence" in statement
        assert "%" in statement, "a path disclosure without its coverage is decoration"

    async def test_a_term_the_pack_does_not_know_resolves_to_nothing_and_says_so(
        self, discovery, snapshot, watermark, window
    ) -> None:
        resolution = await discovery.concept_paths(
            "warp core breach",
            window=window,
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
        )
        assert resolution.concept_id is None
        assert not resolution.has_path
        assert not resolution.expressions
        assert "no standard way" in resolution.statement


class TestMeasureAvailability:
    def test_every_governed_measure_is_judged(
        self, discovery, snapshot, watermark
    ) -> None:
        profile = discovery.measure_availability(
            population="everything in your data",
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
        )
        assert len(profile.measures) == len(snapshot.metric_contracts)
        assert all(measure.available for measure in profile.measures)

    def test_a_grain_that_does_not_match_refuses_with_a_reason(
        self, discovery, snapshot, watermark
    ) -> None:
        profile = discovery.measure_availability(
            population="every denial",
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
            grain=EntityGrain.DENIAL,
        )
        claim_grain = profile.for_metric("denial_rate")
        assert claim_grain is not None
        assert not claim_grain.available
        assert "claim" in claim_grain.reason

    def test_a_cut_the_measure_does_not_declare_refuses_with_a_reason(
        self, discovery, snapshot, watermark
    ) -> None:
        profile = discovery.measure_availability(
            population="everything in your data",
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
            cuts=("carc",),
        )
        rate = profile.for_metric("denial_rate")
        assert rate is not None
        assert not rate.available
        assert "adjustment reason code" in rate.reason

    def test_it_publishes_the_certified_cuts_a_plan_may_use(
        self, discovery, snapshot, watermark, pack
    ) -> None:
        profile = discovery.measure_availability(
            population="everything in your data",
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
        )
        rate = profile.for_metric("denial_rate")
        contract = pack.metric("denial_rate")
        assert rate is not None and contract is not None
        assert set(rate.cuts) == {dim.id for dim in contract.scope_dimensions}


class TestSubjectPresence:
    async def test_a_name_the_data_holds_is_found_with_its_size(
        self, discovery, snapshot, watermark, window
    ) -> None:
        presence = await discovery.subject_presence(
            "Northbridge Commercial",
            window=window,
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
            dimensions=("payer",),
        )
        assert presence.found
        assert presence.matches[0].value == "Northbridge Commercial"
        assert presence.matches[0].units > 0
        assert "Northbridge Commercial" in presence.statement

    async def test_a_name_the_data_does_not_hold_is_a_refusal_naming_the_gap(
        self, discovery, snapshot, watermark, window
    ) -> None:
        presence = await discovery.subject_presence(
            "Summit Peak Health",
            window=window,
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
            dimensions=("facility",),
        )
        assert not presence.found
        assert "Nothing in your data is named Summit Peak Health" in presence.statement

    async def test_it_runs_off_the_censuses_the_orient_phase_already_paid_for(
        self, discovery, snapshot, watermark, window, cache
    ) -> None:
        await _run(discovery, snapshot, watermark, window, "payer")
        reads_before = cache.puts
        await discovery.subject_presence(
            "Atlas",
            window=window,
            watermark=watermark,
            pack_snapshot_id=snapshot.id,
            dimensions=("payer",),
        )
        assert cache.puts == reads_before


class TestTheWalkRecord:
    async def test_every_answer_lands_as_one_sentence_on_the_record(
        self, discovery, snapshot, watermark, window
    ) -> None:
        discovery.capabilities(watermark=watermark, pack_snapshot_id=snapshot.id)
        await _run(discovery, snapshot, watermark, window, "payer")
        await _run(discovery, snapshot, watermark, window, "facility")
        kinds = [note.kind for note in discovery.notes]
        assert DiscoveryKind.CAPABILITIES in kinds
        assert kinds.count(DiscoveryKind.DIMENSION_CENSUS) == 2
        assert all(note.statement.endswith((".", "%")) for note in discovery.notes)

    async def test_one_note_per_question_however_many_times_it_is_asked(
        self, discovery, snapshot, watermark, window
    ) -> None:
        """An orient phase resolves the same census from three directions.
        Recording it three times would put one sentence on a preview card
        three times, and make the walk a log of lookups."""
        await _run(discovery, snapshot, watermark, window, "payer")
        await _run(discovery, snapshot, watermark, window, "payer")
        await _run(discovery, snapshot, watermark, window, "payer")
        census_notes = [
            note for note in discovery.notes if note.kind is DiscoveryKind.DIMENSION_CENSUS
        ]
        assert len(census_notes) == 1

    async def test_forgetting_clears_the_record_and_the_memo(
        self, discovery, snapshot, watermark, window
    ) -> None:
        await _run(discovery, snapshot, watermark, window, "payer")
        discovery.forget()
        assert discovery.notes == ()
