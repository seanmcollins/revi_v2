"""Generator configuration: the single seed, scale knobs, snapshot specs, scenario constants.

Everything that controls the generated world lives here so that "same seed + same
config => byte-identical logical content" is auditable in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

REVI_SEED = 20260807
"""The single seed for the whole warehouse. Never derive per-table seeds."""


def make_rng() -> np.random.Generator:
    """The one generator threaded through every stage, exactly as specified."""
    return np.random.Generator(np.random.PCG64(REVI_SEED))


def day(iso: str) -> int:
    """ISO date string -> integer days since 1970-01-01 (the internal date unit)."""
    return int(np.datetime64(iso, "D").astype(int))


NEVER = 10**8
"""Sentinel day value meaning 'this event never happens / is unknown'."""

SERVICE_START = day("2025-01-01")
SERVICE_END = day("2026-08-02")

CALENDAR_START = day("2025-01-01")
CALENDAR_END = day("2026-12-31")


@dataclass(frozen=True)
class SnapshotSpec:
    """One simulated nightly load (design doc section 10.3: newest watermark 2026-08-03 04:10)."""

    watermark_id: str
    schema_name: str
    loaded_at: str  # 'YYYY-MM-DD HH:MM:SS'
    newest_data_date: str  # activity after this date is absent from the snapshot

    @property
    def cutoff_day(self) -> int:
        return day(self.newest_data_date)


SNAPSHOTS: tuple[SnapshotSpec, ...] = (
    SnapshotSpec("wm_001", "snap_001", "2026-08-01 04:05:00", "2026-07-31"),
    SnapshotSpec("wm_002", "snap_002", "2026-08-02 04:12:00", "2026-08-01"),
    SnapshotSpec("wm_003", "snap_003", "2026-08-03 04:10:00", "2026-08-02"),
)


@dataclass(frozen=True)
class ScenarioSpec:
    """Constants for the five planted scenarios. Dates are fixed regardless of scale."""

    # Scenario 1 — denial spike: Meridian Health x Imaging, CARC 197 (CO).
    s1_payer = "Meridian Health"
    s1_service_line = "Imaging"
    s1_break_day: int = day("2026-06-15")
    s1_pre_prob: float = 0.02
    s1_post_prob: float = 0.14

    # Scenario 2 — COB: Silverline Medicare Advantage, CARC 22 (OA) + delayed rebill.
    s2_payer = "Silverline Medicare Advantage"
    s2_service_start: int = day("2026-04-01")
    s2_service_end: int = day("2026-07-31")
    s2_frac: float = 0.08
    s2_rebill_min_days: int = 30
    s2_rebill_max_days: int = 45

    # Scenario 3 — cash decline week 2026-07-27..08-02 vs 2026-07-20..26.
    s3_week_prior_start: int = day("2026-07-20")
    s3_week_prior_end: int = day("2026-07-26")
    s3_week_decline_start: int = day("2026-07-27")
    s3_week_decline_end: int = day("2026-08-02")
    # (a) Atlas Commercial submission volume -20% starting ~2 weeks prior.
    s3a_payer = "Atlas Commercial"
    s3a_start_day: int = day("2026-07-13")
    s3a_drop_frac: float = 0.20
    s3a_defer_days: int = 21
    # (b) State Medicaid remit->post lag stretched ~+4 days in late July.
    s3b_payer = "State Medicaid"
    s3b_remit_start_day: int = day("2026-07-24")
    s3b_extra_lag_days: int = 4

    # Scenario 4 — underpayment: Northbridge Commercial ORTHO-SURG at 92% of expected.
    s4_payer = "Northbridge Commercial"
    s4_proc_group = "ORTHO-SURG"
    s4_start_day: int = day("2026-05-01")
    s4_factor: float = 0.92

    # Scenario 5 — timely filing: State Medicaid HMO (90 days from service), Eastside.
    s5_plan = "State Medicaid HMO"
    s5_facility = "Eastside Medical Center"
    s5_july_start: int = day("2026-07-01")
    s5_july_end: int = day("2026-07-31")
    # Late-filed claims must have service >90 days before submission for a CARC 29
    # denial to be mechanically correct, and early enough that the denial remit
    # lands before the snap_003 cutoff (2026-08-02).
    s5_carc29_service_start: int = day("2026-02-01")
    s5_carc29_service_end: int = day("2026-03-15")
    s5_carc29_late_min_days: int = 95
    s5_carc29_late_max_days: int = 110


SCENARIOS = ScenarioSpec()


@dataclass(frozen=True)
class GeneratorConfig:
    """Scale knobs. Defaults are full scale; use small() for fast tests (~6% scale)."""

    scale: str = "full"
    n_claims: int = 120_000
    n_patients: int = 20_000
    n_providers: int = 150
    extra_lines_lambda: float = 1.5  # lines per claim = 1 + Poisson(lambda), clipped to 6
    never_submitted_frac: float = 0.05
    appeal_frac: float = 0.35
    overturn_frac: float = 0.45  # among decided appeals
    patient_collect_frac: float = 0.72
    refund_frac: float = 0.004
    timely_cluster_size: int = 400  # scenario 5: July claims left unsubmitted at Eastside
    carc29_count: int = 15  # scenario 5: late-filed claims already denied CARC 29

    @staticmethod
    def small() -> GeneratorConfig:
        """Small preset for fast, deterministic tests.

        10% of full scale (not the nominal ~5%): the Meridian x Imaging post-break
        cell needs enough claims for the planted CARC 197 spike to be detectable.
        Generation stays comfortably under a couple of seconds.
        """
        return GeneratorConfig(
            scale="small",
            n_claims=12_000,
            n_patients=2_400,
            n_providers=150,
            timely_cluster_size=20,
            carc29_count=6,
        )
