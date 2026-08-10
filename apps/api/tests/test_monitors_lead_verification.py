"""A confirmation is evidence FROM AFTER THE CLAIM, and it expires.

Regression: a card could render "new lead, already confirmed fixed" in one
eyebrow. The loads that "verified" the fix ran BEFORE anybody claimed it — the
walk banked them when older loads were re-evaluated after the claim — and a lead
that reached ``resolved_confirmed`` was never looked at again, so the detector
firing on it at a later load could not take the badge back.

Three rules close it, each proved twice below: as the pure function that decides
it, and over the three real loads of the generated warehouse, the only place the
claim → load → load walk exists.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from revi_api.auth import Principal
from revi_api.monitors import (
    _assert_no_confirmed_lead_in_feed,
    _is_strictly_after,
    _merged_verifications,
    _publishable_lead_status,
    _regressed_on_reappearance,
    _repaired_lead,
)
from revi_api.scripted_llm import demo_language_model
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation.application.ports import MonitorsLead, MonitorsLoad
from revi_investigation_contracts.api import AnomalyCard, TypedInvestigationSpec
from revi_investigation_contracts.monitors import MonitorsLeadPatchRequest
from revi_investigation_contracts.refinements import WindowSpecModel
from revi_kernel.errors import ReviError
from revi_kernel.watermark import DataWatermark

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

TENANT = "demo"
CALLER = Principal(tenant=TENANT, subject="lead-verification-suite")

_T0 = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
#: Ids that sort the WRONG way as strings, so a lexicographic comparison
#: cannot pass this file: wm_010 landed after wm_002.
ORDER = {
    "wm_001": _T0,
    "wm_002": _T0 + timedelta(days=1),
    "wm_010": _T0 + timedelta(days=2),
}


def _lead(**overrides: object) -> MonitorsLead:
    base: dict[str, object] = {
        "tenant": TENANT,
        "anomaly_id": "ANM-029",
        "status": "resolved_claimed",
        "updated_at": _T0,
        "note": "rebilled the 14 accounts",
        "claimed_at_watermark": "wm_010",
        "baseline_cents": 1767733,
        "baseline_basis": "this platform's own re-derivation at the claim load wm_010",
        "confirming_watermarks": (),
        "verification_note": "",
    }
    base.update(overrides)
    return MonitorsLead(**base)  # type: ignore[arg-type]


def _card(anomaly_id: str = "ANM-029") -> AnomalyCard:
    return AnomalyCard(
        anomaly_id=anomaly_id,
        provenance="external_detection",
        priority_formula_version="anomaly_priority@3",
        source_watermark_id="wm_011",
        title="Non-covered denial burst: Bluestone PPO Imaging",
        description="Non-covered denials on imaging at Bluestone PPO Blue",
        category="denial_spike",
        metric_id="denied_dollars",
        severity="medium",
        confidence="high",
        status="open",
        detected_at=datetime(2026, 8, 2, tzinfo=UTC),
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 31),
        drill_spec=TypedInvestigationSpec(
            metric_ids=["denied_dollars"],
            dimensions=[],
            window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
        ),
        lane="value",
        impact_cents=1767733,
        ranked_impact_cents=1767733,
    )


class TestStrictlyAfter:
    """Loads are ordered by their own clock, never by their id."""

    def test_a_later_load_is_after_and_an_earlier_one_is_not(self) -> None:
        assert _is_strictly_after("wm_010", "wm_002", ORDER) is True
        assert _is_strictly_after("wm_001", "wm_010", ORDER) is False

    def test_the_claim_load_is_not_after_itself(self) -> None:
        """The load a fix was claimed at is not evidence for the claim."""
        assert _is_strictly_after("wm_002", "wm_002", ORDER) is False

    def test_an_unorderable_pair_is_neither(self) -> None:
        """``None`` is not "no", and every caller treats it as "not
        evidence" rather than guessing which load came first."""
        assert _is_strictly_after("wm_999", "wm_002", ORDER) is None
        assert _is_strictly_after("wm_002", "wm_999", ORDER) is None


class TestPreClaimRepair:
    """Banked pre-claim evidence is taken back off leads already on disk."""

    def test_the_shipped_state_is_repaired_back_to_claimed(self) -> None:
        lead = _lead(
            status="resolved_confirmed",
            confirming_watermarks=("wm_001", "wm_002"),
            verification_note=(
                "Confirmed: ANM-029 is no longer in the detection feed at wm_002: the "
                "detector's own rule has stopped firing for this cell, for 2 consecutive "
                "loads, wm_001-wm_002."
            ),
        )

        repaired = _repaired_lead(lead, ORDER, 2)

        assert repaired.status == "resolved_claimed"
        assert repaired.confirming_watermarks == ()
        assert "ran BEFORE it" in repaired.verification_note
        assert "not evidence the fix worked" in repaired.verification_note
        assert "wm_010" in repaired.verification_note  # the claim load, named
        assert "Confirmed" not in repaired.verification_note

    def test_the_repair_runs_once_and_then_leaves_the_lead_alone(self) -> None:
        """The repaired sentence must not read, to the scan that wrote it,
        like the pre-claim verdict it replaced — or every load would rewrite
        it forever."""
        once = _repaired_lead(
            _lead(status="resolved_confirmed", confirming_watermarks=("wm_001", "wm_002")),
            ORDER,
            2,
        )
        assert _repaired_lead(once, ORDER, 2) is once

    def test_a_post_claim_streak_is_kept(self) -> None:
        lead = _lead(
            claimed_at_watermark="wm_001",
            confirming_watermarks=("wm_002", "wm_010"),
            verification_note="1 of the 2 consecutive loads",
        )

        assert _repaired_lead(lead, ORDER, 2) is lead

    def test_a_confirmation_earned_after_the_claim_survives_the_repair(self) -> None:
        lead = _lead(
            status="resolved_confirmed",
            claimed_at_watermark="wm_001",
            confirming_watermarks=("wm_001", "wm_002", "wm_010"),
            verification_note="Confirmed: for 2 consecutive loads, wm_002-wm_010.",
        )

        repaired = _repaired_lead(lead, ORDER, 2)

        assert repaired.status == "resolved_confirmed"
        assert repaired.confirming_watermarks == ("wm_002", "wm_010")
        assert "ran BEFORE it" in repaired.verification_note
        assert "not evidence the fix worked" in repaired.verification_note

    def test_a_verdict_reached_at_a_pre_claim_load_is_withdrawn(self) -> None:
        """The companion symptom: ANM-001 rendered "still detected at wm_002" —
        a verdict from the load BEFORE the claim — on a page walking wm_010."""
        lead = _lead(
            anomaly_id="ANM-001",
            verification_note=(
                "still detected at wm_002: this platform's re-derived exposure is "
                "$177,202.87 against $177,202.87 at the claim load (0% down, short of the "
                "governed 80%). Not confirmed, and the streak restarts."
            ),
        )

        repaired = _repaired_lead(lead, ORDER, 2)

        assert repaired.status == "resolved_claimed"
        assert "still detected at wm_002" not in repaired.verification_note
        assert "no load since has verified it" in repaired.verification_note

    def test_a_load_this_platform_cannot_place_in_time_is_left_alone(self) -> None:
        """Only DEMONSTRABLY pre-claim evidence is dropped. A rotated-out
        load is not deleted on a guess."""
        lead = _lead(confirming_watermarks=("wm_gone",), claimed_at_watermark="wm_002")

        assert _repaired_lead(lead, ORDER, 2) is lead


class TestReappearance:
    """A confirmed lead back in the feed regresses, and says both facts."""

    def test_the_sentence_states_the_confirmation_and_the_firing(self) -> None:
        lead = _lead(
            status="resolved_confirmed",
            claimed_at_watermark="wm_001",
            confirming_watermarks=("wm_002", "wm_010"),
        )
        watermark = DataWatermark(
            id="wm_011", loaded_at=_T0 + timedelta(days=3), newest_data_date=_T0.date()
        )

        outcome = _regressed_on_reappearance(lead, _card(), watermark)

        assert outcome.lead.status == "regressed"
        assert outcome.lead.confirming_watermarks == ()
        assert outcome.lead.verification_note == (
            "Regressed: ANM-029 was confirmed fixed at wm_010 on 2 loads wm_002, wm_010; the "
            "detector fired again at wm_011 — the confirmation is withdrawn, because a lead in "
            "this load's own detection feed is not a fixed lead."
        )
        assert outcome.entry is not None
        # `resolution_regressed` is first in the governed priority order and
        # is never capped, so this takes its slot in the brief.
        assert outcome.entry["status"] == "regressed"
        assert outcome.entry["impact_cents"] == 1767733


class TestPayloadBuild:
    """No card in a load's feed leaves the building marked confirmed."""

    def test_a_confirmed_lead_in_the_feed_is_published_as_regressed(self) -> None:
        lead = _lead(status="resolved_confirmed", confirming_watermarks=("wm_002", "wm_010"))

        status, note = _publishable_lead_status(lead, tenant=TENANT, watermark_id="wm_011")

        assert status == "regressed"
        assert "the detector fired again at wm_011" in note

    def test_every_other_status_is_published_as_stored(self) -> None:
        lead = _lead(status="working", verification_note="", note="Sarah has it")

        assert _publishable_lead_status(lead, tenant=TENANT, watermark_id="wm_011") == (
            "working",
            "Sarah has it",
        )

    def test_the_assertion_refuses_the_contradiction_rather_than_rendering_it(self) -> None:
        with pytest.raises(ReviError) as refused:
            _assert_no_confirmed_lead_in_feed(
                TENANT, "wm_011", {"ANM-029": "resolved_confirmed", "ANM-001": "working"}
            )

        assert "ANM-029" in refused.value.message
        assert "not a fixed lead" in refused.value.message

    def test_it_passes_on_the_statuses_a_feed_may_carry(self) -> None:
        _assert_no_confirmed_lead_in_feed(
            TENANT, "wm_011", {"ANM-029": "regressed", "ANM-001": "resolved_claimed"}
        )


class TestVerdictsSurviveARewalk:
    """One load is walked many times; its verdicts are stated once."""

    def _load(self, verifications: list[dict[str, object]]) -> MonitorsLoad:
        return MonitorsLoad(
            tenant=TENANT,
            watermark_id="wm_010",
            watermark_loaded_at=ORDER["wm_010"],
            evaluated_at=_T0,
            payload={"verifications": verifications},
        )

    def test_a_stored_verdict_survives_a_walk_that_reached_none(self) -> None:
        stored = self._load([{"anomaly_id": "ANM-031", "status": "resolved_confirmed"}])
        leads = {"ANM-031": _lead(anomaly_id="ANM-031", status="resolved_confirmed")}

        assert _merged_verifications(stored, [], leads) == [
            {"anomaly_id": "ANM-031", "status": "resolved_confirmed"}
        ]

    def test_a_withdrawn_verdict_does_not(self) -> None:
        """A confirmation the lead no longer holds is not a record of this
        load, it is the contradiction this file exists to kill."""
        stored = self._load([{"anomaly_id": "ANM-029", "status": "resolved_confirmed"}])
        leads = {"ANM-029": _lead(anomaly_id="ANM-029", status="resolved_claimed")}

        assert _merged_verifications(stored, [], leads) == []

    def test_this_walk_wins_over_the_stored_one(self) -> None:
        stored = self._load([{"anomaly_id": "ANM-029", "status": "resolved_confirmed"}])
        leads = {"ANM-029": _lead(anomaly_id="ANM-029", status="regressed")}
        fresh = [{"anomaly_id": "ANM-029", "status": "regressed", "note": "the fix did not hold"}]

        assert _merged_verifications(stored, fresh, leads) == fresh


# ---------------------------------------------------------------------------
# the same three rules, over the real loads


def _pack_confirming_on_one_load(tmp_path: Path) -> Path:
    """The governed pack with the confirmation rule set to one load.

    Every lead that leaves this warehouse's feed does so at the last of its
    three loads, so the shipped two-load rule cannot reach a confirmation
    inside it. The knob is pack content precisely so a deployment can turn
    it, and it is turned here the way a deployment would.
    """
    pack = tmp_path / "base-rcm"
    shutil.copytree(REPO_ROOT / "packs" / "base-rcm", pack)
    monitors_yaml = pack / "monitors.yaml"
    monitors_yaml.write_text(
        monitors_yaml.read_text().replace(
            "consecutive_loads_required: 2", "consecutive_loads_required: 1"
        ),
        encoding="utf-8",
    )
    return pack


def _warehouse_with_a_fourth_load(tmp_path: Path) -> Path:
    """A copy of the generated warehouse plus ``wm_004``, whose feed is the
    one from ``wm_002`` — so leads that left at ``wm_003`` are detected
    again, which is the only shape this warehouse cannot otherwise show."""
    import duckdb

    path = tmp_path / "revi_warehouse.duckdb"
    shutil.copyfile(WAREHOUSE, path)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE SCHEMA snap_004")
        for (table,) in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'snap_002'"
        ).fetchall():
            con.execute(f"CREATE VIEW snap_004.{table} AS SELECT * FROM snap_002.{table}")
        con.execute(
            "INSERT INTO main.watermarks SELECT 'wm_004', 'snap_004', "
            "loaded_at + INTERVAL 1 DAY, newest_data_date FROM main.watermarks "
            "WHERE watermark_id = 'wm_003'"
        )
    finally:
        con.close()
    return path


@pytest.mark.reference
@pytest.mark.skipif(
    not WAREHOUSE.is_file(),
    reason="generated warehouse missing — run: "
    "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
)
class TestOverRealLoads:
    """wm_001 → wm_002 → wm_003, through the shipped code path.

    ANM-029 is the lead this rule was written for: absent from the feed at
    wm_001 and wm_002 and first detected at wm_003, which is what made the
    shipped defect possible and what makes it the proof here.
    """

    def _service(self, pack_dir: Path | None = None, *, warehouse: Path | None = None) -> ApiService:
        env = {"REVI_WAREHOUSE_PATH": str(warehouse or WAREHOUSE), "REVI_LLM_MOCK": "1"}
        if pack_dir is not None:
            env["REVI_PACK_DIR"] = str(pack_dir)
        return ApiService(build_components(env, llm=demo_language_model()))

    async def test_loads_before_the_claim_never_verify_it(self) -> None:
        """The repro, end to end: claim at the newest load, then let the two
        older ones be walked. They used to be banked and confirm the fix; the
        lead's own claim load is the floor now."""
        service = self._service()
        first, second, third = await service.components.repository.list_watermarks()

        await service.monitors.brief_at(CALLER, third)
        claimed = await service.monitors.patch_lead(
            CALLER,
            "ANM-029",
            MonitorsLeadPatchRequest(status="resolved_claimed", note="rebilled the 14 accounts"),
            watermark=third,
        )
        assert claimed.claimed_at_watermark == third.id

        await service.monitors.brief_at(CALLER, first)
        await service.monitors.brief_at(CALLER, second)

        lead = await service.monitors.get_lead(CALLER, "ANM-029")
        assert lead.status == "resolved_claimed"
        assert lead.confirming_watermarks == []

    async def test_the_card_in_this_load_is_never_published_as_fixed(self) -> None:
        """The screen that shipped the contradiction: the brief's own census and
        the leads panel, at the load whose feed holds the card."""
        service = self._service()
        *_, third = await service.components.repository.list_watermarks()

        await service.monitors.brief_at(CALLER, third)
        await service.monitors.patch_lead(
            CALLER,
            "ANM-029",
            MonitorsLeadPatchRequest(status="resolved_claimed"),
            watermark=third,
        )
        brief = await service.monitors.brief_at(CALLER, third)
        portfolio = await service.get_portfolio(CALLER)

        card = next(c for c in portfolio.items if c.anomaly_id == "ANM-029")
        assert card.lead_status == "resolved_claimed"
        assert [e.lead_status for e in brief.entries if e.anomaly_id == "ANM-029"] != [
            "resolved_confirmed"
        ]
        assert not [
            e for e in brief.entries if e.kind == "new_lead" and e.lead_status == "resolved_confirmed"
        ]

    async def test_a_claim_is_confirmed_by_the_loads_that_follow_it(
        self, tmp_path: Path
    ) -> None:
        """The other half of the rule: post-claim loads DO verify.

        The governed knob is set to one load — as the shipped rule's own
        test does, because every lead that leaves this warehouse's feed does
        so at the last of three loads — and the pre-claim load is walked
        after the claim to prove it is discarded while the post-claim one
        counts.
        """
        service = self._service(_pack_confirming_on_one_load(tmp_path))
        first, second, third = await service.components.repository.list_watermarks()

        await service.monitors.brief_at(CALLER, second)
        await service.monitors.patch_lead(
            CALLER,
            "ANM-031",
            MonitorsLeadPatchRequest(status="resolved_claimed", note="rebilled"),
            watermark=second,
        )
        # A pre-claim load, walked after the claim, and then the post-claim
        # one. Only the second may count.
        await service.monitors.brief_at(CALLER, first)
        brief = await service.monitors.brief_at(CALLER, third)

        lead = await service.monitors.get_lead(CALLER, "ANM-031")
        assert lead.status == "resolved_confirmed"
        assert lead.confirming_watermarks == [third.id]
        assert first.id not in lead.verification_note
        assert lead.verification_note.startswith("Confirmed: ANM-031")
        assert f"for 1 load, {third.id}" in lead.verification_note
        assert "ANM-031" in {
            e.anomaly_id for e in brief.entries if e.kind == "resolution_confirmed"
        }

    async def test_a_confirmed_lead_that_reappears_regresses(self, tmp_path: Path) -> None:
        """ANM-031 leaves the feed at wm_003 and the platform confirms the
        fix. A FOURTH load lands with the detector firing on it again, and
        the badge comes off in one sentence carrying both facts.

        The fourth load is a real load — a schema in a copy of the generated
        warehouse and a row in its watermark table — because the whole point
        of this rule is that it fires on data arriving, and a stubbed feed
        would prove only that the stub was called.
        """
        warehouse = _warehouse_with_a_fourth_load(tmp_path)
        service = self._service(_pack_confirming_on_one_load(tmp_path), warehouse=warehouse)
        _, second, third, fourth = await service.components.repository.list_watermarks()
        assert fourth.id == "wm_004"

        await service.monitors.brief_at(CALLER, second)
        await service.monitors.patch_lead(
            CALLER,
            "ANM-031",
            MonitorsLeadPatchRequest(status="resolved_claimed"),
            watermark=second,
        )
        await service.monitors.brief_at(CALLER, third)
        assert (await service.monitors.get_lead(CALLER, "ANM-031")).status == "resolved_confirmed"

        brief = await service.monitors.brief_at(CALLER, fourth)

        lead = await service.monitors.get_lead(CALLER, "ANM-031")
        assert lead.status == "regressed"
        assert lead.confirming_watermarks == []
        assert lead.verification_note.startswith("Regressed: ANM-031 was confirmed fixed at wm_003")
        assert "the detector fired again at wm_004" in lead.verification_note
        [entry] = [e for e in brief.entries if e.kind == "resolution_regressed"]
        assert entry.anomaly_id == "ANM-031"
        assert entry.statement == lead.verification_note
        # A reader refreshes the page. The verdict this load reached is
        # stated once and does not vanish on the second read.
        again = await service.monitors.brief_at(CALLER, fourth)
        assert [e.anomaly_id for e in again.entries if e.kind == "resolution_regressed"] == [
            "ANM-031"
        ]
        assert (await service.monitors.get_lead(CALLER, "ANM-031")).verification_note == (
            lead.verification_note
        )
        # And the card carries the same verdict the brief does.
        card = next(
            c for c in (await service.get_portfolio(CALLER)).items if c.anomaly_id == "ANM-031"
        )
        assert card.lead_status == "regressed"
