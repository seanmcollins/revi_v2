"""Round-trip property tests for the versioned serde (no database needed).

The property is the module's whole point: for every representative object
graph, ``from_stored(json.loads(json.dumps(to_stored(x)))) == x`` — the
JSON hop included, exactly as the value travels to and from a JSONB column.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from revi_investigation.application.ports import RegisteredReferent, TraceRecord
from revi_investigation.domain.context import (
    AnalysisSpec,
    ContextPin,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import Finding, Investigation, InvestigationStatus, Session
from revi_investigation.domain.refinements import (
    AddFilter,
    DrillInto,
    Expand,
    Explain,
    Pivot,
    RankBy,
    Refinement,
    RemoveFilter,
    ResetContext,
    SetComparison,
    SetDimensions,
    SetGrain,
    SetWindow,
)
from revi_investigation.domain.turns import TurnClass
from revi_kernel.cohort import CohortDefinition, CohortMaterialization, CohortRef
from revi_kernel.filters import And, FilterExpr, InCohort, Not, Or, Predicate, PredicateOp, Scalar
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
    TransformProvenance,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import (
    SERVICE,
    DateBasisRef,
    DimensionRef,
    EntityGrain,
    FieldRef,
    Grain,
    MetricRef,
    ReferentId,
    ReferentKind,
    TimeBucket,
)
from revi_kernel.scope import (
    AbsoluteRange,
    CalendarRef,
    Comparison,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    TimeWindow,
)
from revi_kernel.watermark import DataWatermark, WatermarkEpoch
from revi_store_postgres.serde import SERDE_VERSION, SerdeError, from_stored, to_stored


def round_trip(value: object) -> object:
    """Envelope → JSON text → envelope → value, exactly like a JSONB hop."""
    return from_stored(json.loads(json.dumps(to_stored(value))))


# --- strategies -------------------------------------------------------------

_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=10)
_decimals = st.decimals(allow_nan=False, allow_infinity=False, places=6)
_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31))
_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 12, 31),
    timezones=st.none() | st.just(UTC),
)
_scalars: st.SearchStrategy[Scalar] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**12), max_value=10**12),
    st.text(max_size=20),
    _decimals,
    _dates,
)

_dimension_refs = st.builds(DimensionRef, _names)
_metric_refs = st.builds(MetricRef, _names)
_referent_ids = st.builds(ReferentId, _names, st.sampled_from(ReferentKind))
_grains = st.builds(Grain, st.sampled_from(EntityGrain), st.none() | st.sampled_from(TimeBucket))
_watermarks = st.builds(DataWatermark, _names, _datetimes, _dates)


@st.composite
def _predicates(draw: st.DrawFn) -> Predicate:
    op = draw(st.sampled_from(PredicateOp))
    if op is PredicateOp.IS_NULL:
        values: tuple[Scalar, ...] = ()
    elif op is PredicateOp.CONTAINS:
        values = (draw(st.text(min_size=1, max_size=10)),)
    elif op is PredicateOp.RANGE:
        values = (draw(_scalars), draw(_scalars))
    elif op in (PredicateOp.IN, PredicateOp.NOT_IN):
        values = tuple(draw(st.lists(_scalars, min_size=1, max_size=3)))
    else:  # EQ / NEQ
        values = (draw(_scalars),)
    return Predicate(
        dimension=draw(_dimension_refs),
        op=op,
        values=values,
        origin_turn=draw(st.none() | _names),
    )


_absolute_ranges = st.builds(
    lambda start, days: AbsoluteRange(start, start + timedelta(days=days)),
    _dates,
    st.integers(min_value=0, max_value=400),
)


@st.composite
def _relative_ranges(draw: st.DrawFn) -> RelativeRange:
    mode = draw(st.sampled_from(RangeMode))
    if mode is RangeMode.TO_DATE:
        quantity = Decimal(1)
    else:
        quantity = draw(
            st.decimals(min_value=Decimal("0.25"), max_value=Decimal("24"), places=2)
        )
    return RelativeRange(quantity=quantity, unit=draw(st.sampled_from(TimeUnit)), mode=mode)


_windows = st.builds(
    TimeWindow,
    basis=st.builds(DateBasisRef, _names),
    range=_absolute_ranges,
    requested=st.none() | _relative_ranges(),
    calendar=st.builds(CalendarRef, _names),
)


@st.composite
def _cohort_refs(draw: st.DrawFn, *, pinned: bool | None = None) -> CohortRef:
    cohort_id = draw(_names)
    definition = CohortDefinition(
        entity=draw(st.sampled_from(EntityGrain)),
        scope=draw(_predicates()),
        window=draw(st.none() | _windows),
    )
    with_pin = draw(st.booleans()) if pinned is None else pinned
    materialization = (
        CohortMaterialization(
            cohort_id=cohort_id,
            watermark=draw(_watermarks),
            entity_ids_ref=draw(_names),
            size=draw(st.integers(min_value=0, max_value=10**6)),
            created_at=draw(_datetimes),
            ttl_seconds=draw(st.integers(min_value=1, max_value=10**6)),
        )
        if with_pin
        else None
    )
    return CohortRef(
        id=cohort_id,
        definition=definition,
        origin=draw(_referent_ids),
        size=draw(st.integers(min_value=0, max_value=10**6)),
        pinned=materialization,
    )


_filter_leaves: st.SearchStrategy[FilterExpr] = st.one_of(
    _predicates(),
    st.builds(InCohort, cohort=_cohort_refs(), origin_turn=st.none() | _names),
)
_filter_exprs: st.SearchStrategy[FilterExpr] = st.recursive(
    _filter_leaves,
    lambda children: st.one_of(
        st.builds(Not, children),
        st.lists(children, min_size=0, max_size=3).map(lambda cs: And(tuple(cs))),
        st.lists(children, min_size=1, max_size=3).map(lambda cs: Or(tuple(cs))),
    ),
    max_leaves=6,
)

_refinements: st.SearchStrategy[Refinement] = st.one_of(
    st.builds(SetDimensions, st.lists(_dimension_refs, max_size=3).map(tuple)),
    st.builds(AddFilter, _predicates()),
    st.builds(RemoveFilter, _dimension_refs),
    st.builds(
        SetWindow,
        window=st.one_of(_relative_ranges(), _absolute_ranges),
        basis=st.none() | st.builds(DateBasisRef, _names),
    ),
    st.builds(
        SetComparison,
        kind=st.none() | st.sampled_from(ComparisonKind),
        custom=st.none() | _absolute_ranges,
    ),
    st.builds(SetGrain, _grains),
    st.builds(DrillInto, _referent_ids),
    st.builds(Pivot, st.lists(_metric_refs, max_size=3).map(tuple)),
    st.builds(Explain, _referent_ids),
    st.builds(RankBy, _metric_refs, st.booleans()),
    st.builds(Expand, st.integers(min_value=1, max_value=500)),
    st.builds(ResetContext, st.booleans()),
)


@st.composite
def _contexts(draw: st.DrawFn) -> InvestigationContext:
    """InvestigationContext with a nested (pinned) cohort and session pins."""
    comparison = draw(
        st.none() | st.builds(Comparison, st.sampled_from(ComparisonKind), _windows)
    )
    pins = tuple(
        draw(
            st.lists(
                st.builds(ContextPin, predicate=_predicates(), declared_at_turn=_names),
                min_size=1,
                max_size=2,
            )
        )
    )
    return InvestigationContext(
        window=draw(_windows),
        comparison=comparison,
        scope=draw(_filter_exprs),
        cohort=draw(_cohort_refs(pinned=True)),
        grain=draw(_grains),
        watermark=draw(_watermarks),
        pack_version=PackVersionRef(pack_id=draw(_names), version=draw(_names)),
        pins=pins,
    )


_specs = st.builds(
    AnalysisSpec,
    context=_contexts(),
    measures=st.lists(_metric_refs, max_size=3).map(tuple),
    dimensions=st.lists(_dimension_refs, max_size=3).map(tuple),
    rank_by=st.none() | _metric_refs,
    rank_descending=st.booleans(),
    limit=st.none() | st.integers(min_value=1, max_value=100),
)

_provenances = st.recursive(
    st.builds(
        ProbeProvenance,
        probe_id=_names,
        probe_hash=_names,
        repository_query_id=st.none() | _names,
        cache_hit=st.booleans(),
    ),
    lambda children: st.builds(
        TransformProvenance,
        operator=_names,
        operator_version=_names,
        inputs=st.lists(children, min_size=1, max_size=3).map(tuple),
    ),
    max_leaves=5,
)


@st.composite
def _frames(draw: st.DrawFn) -> EvidenceFrame:
    column_names = draw(st.lists(_names, min_size=1, max_size=4, unique=True))
    columns = tuple(
        FrameColumn(
            name=name,
            ref=draw(st.one_of(_dimension_refs, _metric_refs, st.builds(FieldRef, _names))),
            contract_version=draw(st.none() | st.integers(min_value=1, max_value=9)),
            unit=draw(st.none() | _names),
        )
        for name in column_names
    )
    width = len(columns)
    rows = tuple(
        tuple(row)
        for row in draw(
            st.lists(st.lists(_scalars, min_size=width, max_size=width), max_size=5)
        )
    )
    return EvidenceFrame(
        schema=FrameSchema(columns=columns),
        rows=rows,
        watermark=draw(_watermarks),
        provenance=draw(_provenances),
        evidence_grade=draw(st.sampled_from(EvidenceGrade)),
        truncated=draw(st.booleans()),
        suppressed_cells=draw(st.integers(min_value=0, max_value=10)),
    )


_json_payload_values = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(10**12), max_value=10**12),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(max_size=15),
        _decimals,
        _dates,
        _datetimes,
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=8), children, max_size=3),
    ),
    max_leaves=8,
)

_trace_records = st.builds(
    TraceRecord,
    trace_id=_names,
    session_id=_names,
    investigation_id=_names,
    turn_id=_names,
    created_at=_datetimes,
    payload=st.dictionaries(st.text(max_size=10), _json_payload_values, max_size=4),
)


# --- round-trip properties --------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(_filter_exprs)
def test_filter_expr_round_trip(expr: FilterExpr) -> None:
    assert round_trip(expr) == expr


@settings(max_examples=100, deadline=None)
@given(_refinements)
def test_refinement_round_trip(refinement: Refinement) -> None:
    result = round_trip(refinement)
    assert type(result) is type(refinement)  # tagged union keeps the variant
    assert result == refinement


@settings(max_examples=25, deadline=None)
@given(_contexts())
def test_investigation_context_round_trip(context: InvestigationContext) -> None:
    assert round_trip(context) == context


@settings(max_examples=25, deadline=None)
@given(_specs)
def test_analysis_spec_round_trip(spec: AnalysisSpec) -> None:
    assert round_trip(spec) == spec


@settings(max_examples=50, deadline=None)
@given(_frames())
def test_evidence_frame_round_trip(frame: EvidenceFrame) -> None:
    result = round_trip(frame)
    assert result == frame
    assert isinstance(result, EvidenceFrame)
    for row, original_row in zip(result.rows, frame.rows, strict=True):
        for value, original in zip(row, original_row, strict=True):
            assert type(value) is type(original)  # Decimal stays Decimal, bool stays bool


@settings(max_examples=50, deadline=None)
@given(_trace_records)
def test_trace_record_round_trip(record: TraceRecord) -> None:
    assert round_trip(record) == record


@settings(max_examples=25, deadline=None)
@given(_cohort_refs(pinned=True))
def test_pinned_cohort_round_trip(cohort: CohortRef) -> None:
    assert round_trip(cohort) == cohort


# --- explicit coverage of every refinement variant --------------------------

_ALL_TWELVE: tuple[Refinement, ...] = (
    SetDimensions((DimensionRef("payer"), DimensionRef("carc"))),
    AddFilter(Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",), "t3")),
    RemoveFilter(DimensionRef("payer")),
    SetWindow(RelativeRange(Decimal("3.25"), TimeUnit.MONTH), SERVICE),
    SetComparison(ComparisonKind.CUSTOM, AbsoluteRange(date(2026, 1, 1), date(2026, 3, 31))),
    SetGrain(Grain(EntityGrain.CLAIM, TimeBucket.WEEK)),
    DrillInto(ReferentId("F2", ReferentKind.COHORT)),
    Pivot((MetricRef("denied_amount"), MetricRef("denial_count"))),
    Explain(ReferentId("F1", ReferentKind.FINDING)),
    RankBy(MetricRef("denied_amount"), descending=False),
    Expand(50),
    ResetContext(keep_pins=False),
)


@pytest.mark.parametrize("refinement", _ALL_TWELVE, ids=lambda r: type(r).__name__)
def test_each_refinement_variant_round_trips(refinement: Refinement) -> None:
    result = round_trip(refinement)
    assert type(result) is type(refinement)
    assert result == refinement


# --- deterministic composite graphs -----------------------------------------


def test_session_and_investigation_round_trip() -> None:
    watermark = DataWatermark("wm_1", datetime(2026, 8, 7, 3, 0, tzinfo=UTC), date(2026, 8, 2))
    session = Session(
        id="s_1",
        tenant="demo-tenant",
        pack_version=PackVersionRef("rcm-base", "1.2.0"),
        epochs=(
            WatermarkEpoch(0, watermark),
            WatermarkEpoch(
                1, DataWatermark("wm_2", datetime(2026, 8, 8, 3, 0, tzinfo=UTC), date(2026, 8, 3)), "t4"
            ),
        ),
        created_at=datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
    )
    assert round_trip(session) == session

    finding = Finding(
        referent=ReferentId("F1", ReferentKind.FINDING),
        title="Spike",
        statement="Denial rate rose.",
        metric_refs=(MetricRef("denial_rate"),),
        values=(("rate", Decimal("0.1234")), ("as_of", date(2026, 8, 2)), ("prior", None)),
        grade=EvidenceGrade.DERIVED,
        impact_cents=123_45,
    )
    investigation = Investigation(
        id="inv_1",
        session_id="s_1",
        parent_id=None,
        turn_id="t1",
        turn_class=TurnClass.NEW_INVESTIGATION,
        question="why did denials spike?",
        spec=AnalysisSpec(
            context=InvestigationContext(
                window=TimeWindow(basis=SERVICE, range=AbsoluteRange(date(2026, 5, 1), date(2026, 7, 31))),
                comparison=None,
                scope=And(()),
                cohort=None,
                grain=Grain(EntityGrain.CLAIM),
                watermark=watermark,
                pack_version=PackVersionRef("rcm-base", "1.2.0"),
            ),
            measures=(MetricRef("denial_rate"),),
        ),
        plan_hash="ph_1",
        status=InvestigationStatus.COMPLETE,
        findings=(finding,),
        created_at=datetime(2026, 8, 7, 12, 5, tzinfo=UTC),
        frame_refs=("tr_1/frame/0",),
        warnings=("truncated to top 20",),
    )
    assert round_trip(investigation) == investigation


def test_registered_referent_round_trip() -> None:
    entry = RegisteredReferent(
        referent=ReferentId("F2", ReferentKind.COHORT),
        session_id="s_1",
        investigation_id="inv_1",
        label="Meridian denied claims",
        cohort_definition=CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",)),
        ),
        dimension_value=("payer", "Meridian Health"),
    )
    assert round_trip(entry) == entry


# --- envelope + error behavior ----------------------------------------------


def test_envelope_carries_serde_version() -> None:
    envelope = to_stored(DimensionRef("payer"))
    assert envelope["serde_version"] == SERDE_VERSION


def test_unknown_serde_version_is_refused() -> None:
    envelope = to_stored(DimensionRef("payer"))
    envelope["serde_version"] = SERDE_VERSION + 1
    with pytest.raises(SerdeError, match="serde_version"):
        from_stored(envelope)


def test_unknown_type_tag_is_refused() -> None:
    with pytest.raises(SerdeError, match="unknown dataclass tag"):
        from_stored({"serde_version": SERDE_VERSION, "value": {"__type__": "NotAType"}})


def test_unregistered_dataclass_is_refused() -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Rogue:
        x: int

    with pytest.raises(SerdeError, match="not registered"):
        to_stored(Rogue(1))


def test_unknown_field_is_refused() -> None:
    with pytest.raises(SerdeError, match="no field"):
        from_stored(
            {
                "serde_version": SERDE_VERSION,
                "value": {"__type__": "DimensionRef", "id": "payer", "bogus": 1},
            }
        )
