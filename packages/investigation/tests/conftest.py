"""Shared fixtures for the investigation application tests.

Test code may import the real pack/catalog/calculation implementations —
the package-independence contract binds ``src`` packages, not tests; the
wiring helpers live in ``revi_testing`` (which depends on the impls).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from revi_catalog import load_catalog
from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.domain.context import (
    AnalysisSpec,
    InvestigationContext,
    PackVersionRef,
)
from revi_investigation.domain.records import Investigation, InvestigationStatus
from revi_investigation.domain.turns import TurnClass
from revi_kernel.filters import EMPTY_SCOPE, FilterExpr
from revi_kernel.refs import (
    POST,
    DateBasisRef,
    DimensionRef,
    EntityGrain,
    Grain,
    MetricRef,
)
from revi_kernel.scope import (
    ComparisonKind,
    RangeMode,
    RelativeRange,
    TimeUnit,
    derive_comparison,
    resolve_window,
)
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import PackSnapshotPort, load_base_pack

REPO_ROOT = Path(__file__).resolve().parents[3]

TEST_WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
PACK_VERSION = PackVersionRef("base-rcm", "1.0.0")

LAST_FULL_WEEK = RelativeRange(quantity=Decimal(1), unit=TimeUnit.WEEK, mode=RangeMode.FULL_PERIODS)


@pytest.fixture(scope="session")
def catalog() -> CatalogSnapshot:
    return load_catalog(REPO_ROOT / "warehouse" / "catalog")


@pytest.fixture(scope="session")
def pack_port() -> PackSnapshotPort:
    return PackSnapshotPort(load_base_pack(REPO_ROOT / "packs" / "base-rcm"))


def _make_spec(
    *,
    measures: tuple[str, ...] = (),
    dimensions: tuple[str, ...] = (),
    basis: DateBasisRef = POST,
    window: RelativeRange = LAST_FULL_WEEK,
    comparison: ComparisonKind | None = None,
    scope: FilterExpr = EMPTY_SCOPE,
    entity: EntityGrain = EntityGrain.CLAIM,
    limit: int | None = None,
    watermark: DataWatermark = TEST_WATERMARK,
    concepts: tuple[str, ...] = (),
) -> AnalysisSpec:
    resolved = resolve_window(window, watermark.loaded_at.date(), basis=basis)
    context = InvestigationContext(
        window=resolved,
        comparison=derive_comparison(resolved, comparison) if comparison is not None else None,
        scope=scope,
        cohort=None,
        grain=Grain(entity),
        watermark=watermark,
        pack_version=PACK_VERSION,
    )
    return AnalysisSpec(
        context=context,
        measures=tuple(MetricRef(m) for m in measures),
        dimensions=tuple(DimensionRef(d) for d in dimensions),
        limit=limit,
        concepts=concepts,
    )


SpecFactory = Callable[..., AnalysisSpec]


@pytest.fixture(name="make_spec", scope="session")
def make_spec_fixture() -> SpecFactory:
    """The spec builder as a fixture (importlib test mode: no cross-module
    test imports)."""
    return _make_spec


async def _seed_prior_turn(engine: Any, *, session_id: str | None = None) -> str:
    """Give a session one completed turn, so the next one is not its first.

    The first utterance of a session is a NEW_INVESTIGATION *by
    construction* — nothing else in the §7.3 taxonomy has anything to point
    at — and the engine therefore classifies it with zero model calls (see
    ``SubmitTurnService._classification_by_construction``). Every test
    about what CLASSIFICATION does with an utterance consequently needs a
    session that has already answered something; this seeds exactly that
    and nothing else. The seeded turn carries no ``plan_hash``, so it is a
    prior *turn* without being a prior *answer* — a refinement still has
    nothing to refine.
    """
    session = await engine.open_session.open(tenant="demo", session_id=session_id)
    await engine.investigation_store.save(
        Investigation(
            id=f"inv_seed_{session.id}",
            session_id=session.id,
            parent_id=None,
            turn_id=f"turn_seed_{session.id}",
            turn_class=TurnClass.NEW_INVESTIGATION,
            question="(seeded prior turn)",
            spec=_make_spec(measures=("cash_posted",), watermark=session.watermark),
            plan_hash=None,
            status=InvestigationStatus.COMPLETE,
            findings=(),
            created_at=datetime.now(UTC),
        ),
        None,
    )
    return session.id


SeedPriorTurn = Callable[..., Awaitable[str]]


@pytest.fixture(name="seed_prior_turn", scope="session")
def seed_prior_turn_fixture() -> SeedPriorTurn:
    return _seed_prior_turn
