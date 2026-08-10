"""§18.1-8 and §18.1-9: candidate pack changes are reviewable before they
are believed, and meaning changes are versioned rather than edited in place.

The scenario: a proposal to narrow ``cash_posted`` to exclude traditional
Medicaid — a few lines of YAML that silently move every dollar figure the metric
has ever produced. It cannot be applied as a tenant overlay at all; published as
a new base version it mints a new content hash rather than mutating one, so
rollback is a pin rather than a restore; and a recorded investigation replays
against the candidate so the delta is quantified before promotion.

Scope: this is the *mechanism*, exercised end to end. The offline promotion
harness that would run it over a corpus of historical investigations and gate a
workflow is design §20 / Phase 4 and is not built. See
docs/acceptance-walkthrough.md.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_kernel.errors import ErrorCode, PolicyDeniedError
from revi_pack.loader import load_layer
from revi_pack.snapshot import build_snapshot
from revi_testing.engine_wiring import build_duckdb_engine, load_base_pack
from revi_testing.mock_llm import MockLanguageModel

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"
BASE_PACK = REPO_ROOT / "packs" / "base-rcm"

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not WAREHOUSE.is_file(),
        reason="generated warehouse missing — run: "
        "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
    ),
]

QUESTION = "Why did cash decline last week?"

# The proposal: someone argues traditional Medicaid remits on a state cycle
# that distorts the weekly cash trend, and that `cash_posted` should exclude
# it. Same id, same probes, same window — a different number, for everyone
# who has ever read this metric.
_CANDIDATE_CASH_POSTED = """\
id: cash_posted
version: 2
kind: flow
entity_grain: transaction
numerator: {sum: payment_cents}
exclusions: {dimension: payer_type, op: eq, value: MEDICAID}
primary_date_basis: post
allowed_date_bases: [post, remit, service, submission]
scope_dimensions:
  [payer, payer_type, financial_class, plan, product_type, facility, region,
   service_line, claim_type]
sign: higher_is_good
unit: money_cents
description: >-
  CANDIDATE REVISION: payer cash excluding traditional Medicaid.
"""


def _interpretation() -> dict[str, Any]:
    return {
        "intent_summary": "Investigate last week's posted-cash decline by payer",
        "metric_ids": [],
        "dimension_ids": ["payer"],
        "concept_ids": [],
        "playbook_id": "cash_decline",
        "window": {"quantity": "1", "unit": "week", "mode": "full_periods"},
        "basis": "post",
        "comparison": "prior_period",
        "scope": [],
        "clarification": None,
        "definitional_terms": [],
    }


def _llm() -> MockLanguageModel:
    llm = MockLanguageModel()
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
    )
    llm.respond("interpret_question", _interpretation())
    return llm


@pytest.fixture(scope="module")
def candidate_pack_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The proposal as a new base-layer version, on disk."""
    target = tmp_path_factory.mktemp("candidate") / "base-rcm"
    shutil.copytree(BASE_PACK, target)
    manifest = target / "pack.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('version: "1.0.0"', 'version: "1.1.0"'),
        encoding="utf-8",
    )
    (target / "metrics" / "cash_posted.yaml").write_text(
        _CANDIDATE_CASH_POSTED, encoding="utf-8"
    )
    return target


@pytest.fixture(scope="module")
def baseline() -> TurnOutcome:
    engine = build_duckdb_engine(warehouse_path=WAREHOUSE, llm=_llm())
    return asyncio.run(engine.submit.submit(SubmitTurnRequest(tenant="demo", question=QUESTION)))


@pytest.fixture(scope="module")
def candidate(candidate_pack_dir: Path) -> TurnOutcome:
    engine = build_duckdb_engine(
        warehouse_path=WAREHOUSE, llm=_llm(), pack_dir=candidate_pack_dir
    )
    return asyncio.run(engine.submit.submit(SubmitTurnRequest(tenant="demo", question=QUESTION)))


class TestPackVersioningAndReplay:
    def test_overlay_cannot_redefine_meaning(self, tmp_path: Path) -> None:
        """§18.1-9, first half: a meaning change is not something a tenant
        can do quietly — it is refused at compose time."""
        overlay = tmp_path / "sneaky-tenant"
        shutil.copytree(REPO_ROOT / "packs" / "overlays" / "demo-tenant", overlay)
        metrics = overlay / "metrics"
        metrics.mkdir(exist_ok=True)
        (metrics / "cash_posted.yaml").write_text(_CANDIDATE_CASH_POSTED, encoding="utf-8")
        with pytest.raises(PolicyDeniedError) as excinfo:
            build_snapshot([load_layer(BASE_PACK), load_layer(overlay)])
        # a typed §12 refusal naming the metric, not a silent last-write-wins
        assert excinfo.value.code is ErrorCode.POLICY_DENIED
        assert excinfo.value.details["metric"] == "cash_posted"

    def test_meaning_change_mints_a_new_snapshot_id(self, candidate_pack_dir: Path) -> None:
        """§18.1-9, second half: versions, not edits. Rollback is pinning
        the old snapshot id, which still composes from its own layer."""
        base = load_base_pack()
        proposed = load_base_pack(candidate_pack_dir)
        assert proposed.id != base.id
        assert proposed.version.version == "1.1.0" != base.version.version
        assert base.metric("cash_posted") is not None
        assert base.metric("cash_posted").version == 1  # type: ignore[union-attr]
        assert proposed.metric("cash_posted").version == 2  # type: ignore[union-attr]
        # the old snapshot is reproducible from its layer — nothing was lost
        assert load_base_pack().id == base.id

    async def test_candidate_replay_quantifies_the_delta(
        self, baseline: TurnOutcome, candidate: TurnOutcome
    ) -> None:
        """§18.1-8: the same recorded question, at the same watermark,
        against two pack snapshots — and the difference is a number a
        reviewer can weigh, not a diff they have to imagine."""
        assert baseline.session.watermark.id == candidate.session.watermark.id == "wm_003"
        # the probes are identical: only the meaning of the measure moved,
        # so this is a fair A/B and not two different questions
        assert baseline.investigation.plan_hash == candidate.investigation.plan_hash

        before = {f.title.split(" cash")[0]: f.impact_cents for f in baseline.findings}
        after = {f.title.split(" cash")[0]: f.impact_cents for f in candidate.findings}
        assert before and after
        assert before != after, "a meaning change that moves no number is not a meaning change"

        # every number is attributable to the pack version that produced it
        assert baseline.investigation.spec.context.pack_version.version == "1.0.0"
        assert candidate.investigation.spec.context.pack_version.version == "1.1.0"
