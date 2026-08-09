"""Reusable application-state store contract suite (design §15, §18.1).

Every implementation of the seven persistence ports in
``revi_investigation.application.ports`` — the in-memory fakes and the
Postgres adapters alike — must pass the same behavioral suite. Round-trip
equality is exact: a session, frame, or context that comes back different
is data corruption.

Usage::

    class TestMyStores(ApplicationStateStoreContract):
        @pytest.fixture
        def stores(self) -> ApplicationStores:
            return ApplicationStores(...)

The seven stores in one bundle must share backing state (``lineage`` reads
the session saved through ``SessionStore``). Tests generate unique ids per
invocation, so a bundle backed by a shared database stays rerun-safe; set
assertions are used wherever a shared backend may hold unrelated rows.

Datetime convention: fixtures use timezone-aware UTC datetimes — the
portable subset (typed ``timestamptz`` columns cannot represent "naive").
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from revi_investigation.application.ports import (
    EMPTY_SESSION_TITLE,
    CohortStore,
    EvidenceCache,
    FrameStore,
    InvestigationStore,
    ReferentRegistryStore,
    RegisteredReferent,
    SessionStore,
    TraceRecord,
    TraceStore,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
    ContextPin,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    InvestigationStatus,
    RefinementEdge,
    Session,
)
from revi_investigation.domain.refinements import AddFilter, DrillInto, RankBy
from revi_investigation.domain.settings import DEFAULT_SESSION_SETTINGS, SessionSettings
from revi_investigation.domain.turns import TurnClass
from revi_investigation_contracts.settings import EvidenceDepth, NarrativeDepth
from revi_kernel.cohort import CohortDefinition, CohortMaterialization, CohortRef
from revi_kernel.filters import And, Predicate, PredicateOp
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
    Comparison,
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    TimeWindow,
)
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

_T0 = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ApplicationStores:
    """The seven application-state ports over one shared backing state."""

    sessions: SessionStore
    referents: ReferentRegistryStore
    investigations: InvestigationStore
    traces: TraceStore
    frames: FrameStore
    cohorts: CohortStore
    evidence: EvidenceCache


# --- fixture-object builders (unique ids per call) --------------------------


def _token() -> str:
    return uuid4().hex[:12]


def _watermark(token: str, *, offset_days: int = 0) -> DataWatermark:
    return DataWatermark(
        id=f"wm_{token}_{offset_days}",
        loaded_at=_T0 + timedelta(days=offset_days),
        newest_data_date=date(2026, 8, 2) + timedelta(days=offset_days),
    )


def _session(
    token: str,
    *,
    tenant: str = "demo-tenant",
    suffix: str = "",
    created_at: datetime = _T0,
) -> Session:
    return Session(
        id=f"s_{token}{suffix}",
        tenant=tenant,
        pack_version=PackVersionRef(pack_id="rcm-base", version="1.2.0"),
        epochs=(WatermarkEpoch(index=0, watermark=_watermark(token)),),
        created_at=created_at,
    )


def _cohort(token: str, *, pinned: bool = True, created_at: datetime = _T0) -> CohortRef:
    cohort_id = f"c_{token}"
    materialization = (
        CohortMaterialization(
            cohort_id=cohort_id,
            watermark=_watermark(token),
            entity_ids_ref=f"cohort_ids_{token}",
            size=137,
            created_at=created_at,
            ttl_seconds=3600,
        )
        if pinned
        else None
    )
    return CohortRef(
        id=cohort_id,
        definition=CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",)),
            window=TimeWindow(basis=SERVICE, range=AbsoluteRange(date(2026, 1, 1), date(2026, 6, 30))),
        ),
        origin=ReferentId(f"F_{token}", ReferentKind.COHORT),
        size=137,
        pinned=materialization,
    )


def _spec(token: str) -> AnalysisSpec:
    window = TimeWindow(
        basis=SERVICE,
        range=AbsoluteRange(date(2026, 4, 26), date(2026, 8, 3)),
        requested=RelativeRange(Decimal("3.25"), TimeUnit.MONTH, RangeMode.TRAILING),
    )
    comparison = Comparison(
        kind=ComparisonKind.PRIOR_PERIOD,
        window=TimeWindow(basis=SERVICE, range=AbsoluteRange(date(2026, 1, 17), date(2026, 4, 25))),
    )
    context = InvestigationContext(
        window=window,
        comparison=comparison,
        scope=And(
            (
                Predicate(DimensionRef("payer"), PredicateOp.EQ, ("Meridian Health",), "t1"),
                Predicate(DimensionRef("carc"), PredicateOp.IN, ("197", "50"), "t2"),
            )
        ),
        cohort=_cohort(token),
        grain=Grain(EntityGrain.CLAIM, TimeBucket.MONTH),
        watermark=_watermark(token),
        pack_version=PackVersionRef(pack_id="rcm-base", version="1.2.0"),
        pins=(
            ContextPin(
                predicate=Predicate(DimensionRef("region"), PredicateOp.NEQ, ("Northeast",), "t1"),
                declared_at_turn="t1",
            ),
        ),
    )
    return AnalysisSpec(
        context=context,
        measures=(MetricRef("denial_rate"),),
        dimensions=(DimensionRef("payer"),),
        rank_by=MetricRef("denial_rate"),
        rank_descending=True,
        limit=10,
    )


def _finding(token: str) -> Finding:
    return Finding(
        referent=ReferentId(f"F1_{token}", ReferentKind.FINDING),
        title="Denial rate spike",
        statement="Denial rate rose to 12.34% for Meridian Health.",
        metric_refs=(MetricRef("denial_rate"),),
        values=(
            ("denial_rate", Decimal("0.1234")),
            ("window_start", date(2026, 4, 26)),
            ("prior_value", None),
            ("denied_claims", 412),
        ),
        grade=EvidenceGrade.DERIVED,
        impact_cents=1_234_500,
        confidence="high",
        suggested_refinements=("drill into CARC 197",),
    )


def _investigation(
    token: str,
    session_id: str,
    *,
    suffix: str,
    parent_id: str | None = None,
    created_at: datetime = _T0,
) -> Investigation:
    return Investigation(
        id=f"inv_{token}_{suffix}",
        session_id=session_id,
        parent_id=parent_id,
        turn_id=f"t_{suffix}",
        turn_class=TurnClass.NEW_INVESTIGATION if parent_id is None else TurnClass.REFINEMENT,
        question=f"why did denials spike ({suffix})?",
        spec=_spec(token),
        plan_hash=f"plan_{token}_{suffix}",
        status=InvestigationStatus.COMPLETE,
        findings=(_finding(token),),
        created_at=created_at,
        frame_refs=(f"frame_{token}_{suffix}",),
        warnings=("comparison window clipped by data start",),
    )


def _rich_frame(token: str) -> EvidenceFrame:
    """A frame exercising Decimal ratios, dates, None cells, suppression, and
    a TransformProvenance chain."""
    probe = ProbeProvenance(
        probe_id=f"p_{token}",
        probe_hash="a" * 64,
        repository_query_id=f"q_{token}",
        cache_hit=False,
    )
    aggregate = TransformProvenance(operator="aggregate", operator_version="1.0.0", inputs=(probe,))
    return EvidenceFrame(
        schema=FrameSchema(
            columns=(
                FrameColumn(name="payer", ref=DimensionRef("payer")),
                FrameColumn(name="denial_rate", ref=MetricRef("denial_rate"), contract_version=1),
                FrameColumn(name="first_seen", ref=FieldRef("first_seen"), unit="date"),
                FrameColumn(name="flagged", ref=FieldRef("flagged")),
            )
        ),
        rows=(
            ("Meridian Health", Decimal("0.1234"), date(2026, 1, 3), True),
            ("Cascade Care", Decimal("0.0500"), None, False),
            (None, Decimal("0"), date(2026, 2, 14), True),
        ),
        watermark=_watermark(token),
        provenance=TransformProvenance(
            operator="ratio",
            operator_version="2.1.0",
            inputs=(aggregate, probe),
        ),
        evidence_grade=EvidenceGrade.DERIVED,
        truncated=True,
        suppressed_cells=2,
    )


def _trace(token: str, session_id: str, investigation_id: str, *, suffix: str) -> TraceRecord:
    return TraceRecord(
        trace_id=f"tr_{token}_{suffix}",
        session_id=session_id,
        investigation_id=investigation_id,
        turn_id=f"t_{suffix}",
        created_at=_T0 + timedelta(minutes=int(suffix[-1] if suffix[-1].isdigit() else 0)),
        payload={
            "stage_timings_ms": {"classify": 120, "plan": 340},
            "llm_cost_usd": Decimal("0.0125"),
            "probe_dates": [date(2026, 4, 26), date(2026, 8, 3)],
            "notes": None,
            "cache_hits": [True, False],
        },
    )


# --- the contract -----------------------------------------------------------


class ApplicationStateStoreContract:
    """Port-semantics suite for the seven application-state stores.

    Subclass (with a ``Test``-prefixed name) and provide ``stores``.
    """

    @pytest.fixture
    def stores(self) -> ApplicationStores:
        raise NotImplementedError("contract subclasses must provide a stores fixture")

    # -------------------------------------------------------- 1. sessions

    async def test_session_round_trip_and_epoch_append(self, stores: ApplicationStores) -> None:
        token = _token()
        session = _session(token)
        await stores.sessions.save(session)
        assert await stores.sessions.get(session.id) == session

        refreshed = session.with_new_epoch(
            WatermarkEpoch(index=1, watermark=_watermark(token, offset_days=1), started_at_turn="t7")
        )
        await stores.sessions.save(refreshed)
        loaded = await stores.sessions.get(session.id)
        assert loaded == refreshed
        assert loaded is not None and len(loaded.epochs) == 2
        assert loaded.watermark == refreshed.epochs[-1].watermark

    async def test_session_settings_survive_the_store(
        self, stores: ApplicationStores
    ) -> None:
        """Settings persist with the session, and a decimal ceiling comes
        back a Decimal — a budget rounded through a float is not the budget
        the analyst set."""
        token = _token()
        session = _session(token).with_settings(
            SessionSettings(
                model_tier="claude-sonnet-5",
                max_turn_cost_usd=Decimal("0.25"),
                narrative_depth=NarrativeDepth.ANALYST,
                evidence_depth=EvidenceDepth.DEEP,
                debug=True,
            )
        )
        await stores.sessions.save(session)

        loaded = await stores.sessions.get(session.id)

        assert loaded == session
        assert loaded is not None
        assert loaded.settings.max_turn_cost_usd == Decimal("0.25")
        assert loaded.settings.narrative_depth is NarrativeDepth.ANALYST

    async def test_a_session_saved_without_settings_reads_as_defaults(
        self, stores: ApplicationStores
    ) -> None:
        token = _token()
        await stores.sessions.save(_session(token))

        loaded = await stores.sessions.get(f"s_{token}")

        assert loaded is not None
        assert loaded.settings == DEFAULT_SESSION_SETTINGS

    async def test_session_get_missing_returns_none(self, stores: ApplicationStores) -> None:
        assert await stores.sessions.get(f"s_missing_{_token()}") is None

    async def test_listing_is_scoped_to_one_tenant(self, stores: ApplicationStores) -> None:
        """Another tenant's sessions are not in the answer at all — not
        hidden by a caller-side filter, not counted in ``total``."""
        token = _token()
        mine = _session(token, tenant=f"t_mine_{token}")
        theirs = _session(token, tenant=f"t_theirs_{token}", suffix="_x")
        await stores.sessions.save(mine)
        await stores.sessions.save(theirs)

        page = await stores.sessions.list_for_tenant(mine.tenant, limit=50)

        assert [row.session_id for row in page.sessions] == [mine.id]
        assert page.total == 1

    async def test_a_session_with_no_turns_lists_with_its_own_timestamps(
        self, stores: ApplicationStores
    ) -> None:
        """A session exists the moment a client connects. It has no title
        to derive and no activity beyond being opened, and says so."""
        token = _token()
        session = _session(token, tenant=f"t_{token}")
        await stores.sessions.save(session)

        page = await stores.sessions.list_for_tenant(session.tenant, limit=50)

        assert len(page.sessions) == 1
        row = page.sessions[0]
        assert row.title == EMPTY_SESSION_TITLE
        assert row.turn_count == 0
        assert row.created_at == _T0
        assert row.last_activity == _T0

    async def test_a_row_is_titled_by_its_first_question_and_dated_by_its_last(
        self, stores: ApplicationStores
    ) -> None:
        token = _token()
        tenant = f"t_{token}"
        session = _session(token, tenant=tenant)
        await stores.sessions.save(session)
        first = _investigation(token, session.id, suffix="a")
        second = _investigation(
            token,
            session.id,
            suffix="b",
            parent_id=first.id,
            created_at=_T0 + timedelta(minutes=9),
        )
        # Saved out of chronological order: the title must come from the
        # FIRST question asked, not the first row written.
        await stores.investigations.save(second, None)
        await stores.investigations.save(first, None)

        page = await stores.sessions.list_for_tenant(tenant, limit=50)

        assert len(page.sessions) == 1
        row = page.sessions[0]
        assert row.title == first.question
        assert row.turn_count == 2
        assert row.created_at == _T0
        assert row.last_activity == _T0 + timedelta(minutes=9)

    async def test_listing_is_newest_activity_first_and_honors_the_limit(
        self, stores: ApplicationStores
    ) -> None:
        """A session that was answered five minutes ago outranks one opened
        later but never used — activity is what an analyst is looking for.
        ``total`` still counts every session, so a truncated page cannot be
        mistaken for the whole list."""
        token = _token()
        tenant = f"t_{token}"
        quiet = _session(token, tenant=tenant, suffix="_quiet", created_at=_T0 + timedelta(hours=1))
        busy = _session(token, tenant=tenant, suffix="_busy", created_at=_T0)
        stale = _session(token, tenant=tenant, suffix="_stale", created_at=_T0 - timedelta(days=1))
        for session in (quiet, busy, stale):
            await stores.sessions.save(session)
        await stores.investigations.save(
            _investigation(token, busy.id, suffix="busy", created_at=_T0 + timedelta(hours=2)),
            None,
        )

        page = await stores.sessions.list_for_tenant(tenant, limit=50)
        assert [row.session_id for row in page.sessions] == [busy.id, quiet.id, stale.id]
        assert page.total == 3

        capped = await stores.sessions.list_for_tenant(tenant, limit=2)
        assert [row.session_id for row in capped.sessions] == [busy.id, quiet.id]
        assert capped.total == 3, "total counts every session, not just the page"

    async def test_listing_an_unknown_tenant_is_empty_not_an_error(
        self, stores: ApplicationStores
    ) -> None:
        page = await stores.sessions.list_for_tenant(f"t_missing_{_token()}", limit=50)
        assert page.sessions == ()
        assert page.total == 0

    # -------------------------------------------------------- 2. referents

    async def test_referent_register_resolve_update_list(self, stores: ApplicationStores) -> None:
        token = _token()
        session_id = f"s_{token}"
        investigation_id = f"inv_{token}_a"
        finding_entry = RegisteredReferent(
            referent=ReferentId(f"F1_{token}", ReferentKind.FINDING),
            session_id=session_id,
            investigation_id=investigation_id,
            label="Denial rate spike",
            finding=_finding(token),
        )
        drillable = _cohort(token, pinned=False)
        cohort_entry = RegisteredReferent(
            referent=ReferentId(f"F2_{token}", ReferentKind.COHORT),
            session_id=session_id,
            investigation_id=investigation_id,
            label="Meridian denied claims",
            cohort_definition=drillable.definition,
            dimension_value=("payer", "Meridian Health"),
        )
        await stores.referents.register((finding_entry, cohort_entry))

        assert await stores.referents.resolve(session_id, finding_entry.referent) == finding_entry
        assert await stores.referents.resolve(session_id, cohort_entry.referent) == cohort_entry
        assert (
            await stores.referents.resolve(session_id, ReferentId("F99", ReferentKind.FINDING)) is None
        )
        assert await stores.referents.resolve(f"s_other_{token}", finding_entry.referent) is None

        pinned = _cohort(token)  # pinned when first drilled (design §7.6)
        updated = replace(cohort_entry, cohort=pinned)
        await stores.referents.update(updated)
        assert await stores.referents.resolve(session_id, cohort_entry.referent) == updated

        listed = await stores.referents.list_for_session(session_id)
        assert set(listed) == {finding_entry, updated}

    # --------------------------------------------------- 3. investigations

    async def test_investigation_save_get_round_trip(self, stores: ApplicationStores) -> None:
        token = _token()
        session = _session(token)
        await stores.sessions.save(session)
        investigation = _investigation(token, session.id, suffix="a")
        await stores.investigations.save(investigation, None)
        assert await stores.investigations.get(investigation.id) == investigation
        assert await stores.investigations.get(f"inv_missing_{token}") is None

    async def test_investigation_lineage_dag_with_edges(self, stores: ApplicationStores) -> None:
        token = _token()
        session = _session(token)
        await stores.sessions.save(session)

        root = _investigation(token, session.id, suffix="a")
        child_b = _investigation(
            token, session.id, suffix="b", parent_id=root.id, created_at=_T0 + timedelta(minutes=1)
        )
        child_c = _investigation(
            token, session.id, suffix="c", parent_id=root.id, created_at=_T0 + timedelta(minutes=2)
        )
        edge_b = RefinementEdge(
            parent_id=root.id,
            child_id=child_b.id,
            turn_id=child_b.turn_id,
            operators=(
                AddFilter(Predicate(DimensionRef("carc"), PredicateOp.EQ, ("197",))),
                RankBy(MetricRef("denied_amount"), descending=True),
            ),
        )
        edge_c = RefinementEdge(
            parent_id=root.id,
            child_id=child_c.id,
            turn_id=child_c.turn_id,
            operators=(DrillInto(ReferentId(f"F_{token}", ReferentKind.COHORT)),),
        )
        await stores.investigations.save(root, None)
        await stores.investigations.save(child_b, edge_b)
        await stores.investigations.save(child_c, edge_c)

        lineage = await stores.investigations.lineage(session.id)
        assert lineage is not None
        assert lineage.session == session
        assert set(lineage.investigations) == {root, child_b, child_c}
        assert set(lineage.edges) == {edge_b, edge_c}
        assert set(lineage.children_of(root.id)) == {child_b.id, child_c.id}
        assert lineage.children_of(child_b.id) == ()

        assert await stores.investigations.lineage(f"s_missing_{token}") is None

    # -------------------------------------------------------- 4. traces

    async def test_trace_save_get_and_for_investigation(self, stores: ApplicationStores) -> None:
        token = _token()
        session_id = f"s_{token}"
        inv_1 = f"inv_{token}_a"
        inv_2 = f"inv_{token}_b"
        first = _trace(token, session_id, inv_1, suffix="a1")
        second = _trace(token, session_id, inv_1, suffix="a2")
        other = _trace(token, session_id, inv_2, suffix="b1")
        for record in (second, first, other):  # insertion order is not chronological
            await stores.traces.save(record)

        assert await stores.traces.get(first.trace_id) == first
        assert await stores.traces.get(f"tr_missing_{token}") is None

        def by_id(records: tuple[TraceRecord, ...]) -> list[TraceRecord]:
            return sorted(records, key=lambda r: r.trace_id)  # order-agnostic (dicts unhashable)

        assert by_id(await stores.traces.for_investigation(inv_1)) == by_id((first, second))
        assert by_id(await stores.traces.for_investigation(inv_2)) == [other]
        assert await stores.traces.for_investigation(f"inv_missing_{token}") == ()

    # -------------------------------------------------------- 5. frames

    async def test_frame_round_trip_rich_types(self, stores: ApplicationStores) -> None:
        token = _token()
        key = f"trace_{token}/frame/0"
        frame = _rich_frame(token)
        await stores.frames.save(key, frame)
        loaded = await stores.frames.get(key)
        assert loaded == frame
        assert loaded is not None
        assert loaded.rows[0][1] == Decimal("0.1234")
        assert isinstance(loaded.rows[0][1], Decimal)
        assert loaded.suppressed_cells == 2 and loaded.truncated
        assert isinstance(loaded.provenance, TransformProvenance)
        assert isinstance(loaded.provenance.inputs[0], TransformProvenance)

        replacement = replace(frame, truncated=False, suppressed_cells=0)
        await stores.frames.save(key, replacement)
        assert await stores.frames.get(key) == replacement
        assert await stores.frames.get(f"missing_{token}") is None

    # -------------------------------------------------------- 6. cohorts

    async def test_cohort_save_get_and_expiry(self, stores: ApplicationStores) -> None:
        token_a, token_b = _token(), _token()
        session_id = f"s_{token_a}"
        pinned = _cohort(token_a, created_at=_T0)  # ttl 3600s → expires _T0 + 1h
        unpinned = _cohort(token_b, pinned=False)
        await stores.cohorts.save(pinned, tenant="demo-tenant", session_id=session_id)
        await stores.cohorts.save(unpinned, tenant="demo-tenant", session_id=session_id)

        assert await stores.cohorts.get(pinned.id) == pinned
        assert await stores.cohorts.get(unpinned.id) == unpinned
        assert await stores.cohorts.get(f"c_missing_{token_a}") is None

        expired_late = {c.id for c in await stores.cohorts.expired(_T0 + timedelta(hours=2))}
        assert pinned.id in expired_late
        assert unpinned.id not in expired_late  # no materialization → no TTL

        expired_early = {c.id for c in await stores.cohorts.expired(_T0 + timedelta(minutes=30))}
        assert pinned.id not in expired_early
        assert unpinned.id not in expired_early

    # --------------------------------------------------- 7. evidence cache

    async def test_evidence_cache_put_get_idempotent_and_key_isolated(
        self, stores: ApplicationStores
    ) -> None:
        token = _token()
        frame = _rich_frame(token)
        probe_hash, watermark_id, pack_id = f"ph_{token}", f"wm_{token}", f"pack_{token}"

        assert await stores.evidence.get(probe_hash, watermark_id, pack_id) is None
        await stores.evidence.put(probe_hash, watermark_id, pack_id, frame)
        assert await stores.evidence.get(probe_hash, watermark_id, pack_id) == frame

        # idempotent: a duplicate put must not clobber the cached frame
        await stores.evidence.put(probe_hash, watermark_id, pack_id, replace(frame, truncated=False))
        assert await stores.evidence.get(probe_hash, watermark_id, pack_id) == frame

        # key isolation: any differing component is a miss (design §7.9)
        assert await stores.evidence.get(probe_hash, f"wm_other_{token}", pack_id) is None
        assert await stores.evidence.get(probe_hash, watermark_id, f"pack_other_{token}") is None
        assert await stores.evidence.get(f"ph_other_{token}", watermark_id, pack_id) is None
