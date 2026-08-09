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

ORGANIC_ERA_START = SERVICE_START
"""First service date of the organic world.

Everything the five scenarios and the anomaly population describe happens in the
organic era. The 2024 comparison backfill (backfill.py) sits entirely before it,
so scenario and anomaly *baseline* windows are bounded below by this date: a
prior-year cohort must not retroactively redefine "the period before the break".
No pre-existing row carries a service, remit or denial date before it, so the
bound is value-preserving for every published number.
"""

CALENDAR_START = day("2024-01-01")
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

ANOMALY_STREAM = 0xA110
"""Seed-sequence tag for the anomaly injection streams (independent of base draws)."""


@dataclass(frozen=True)
class AnomalySpec:
    """One declaratively planted, detectable anomaly.

    The injection engine (anomalies.py) appends spec-generated claims (plus
    their lines/remits/transactions/denials) to the base world, and the
    detector re-derives every published number per snapshot with SQL. Records
    carry facts only; `onset`/`window_end` bound the observation window the
    detector filters on (denial dates, remit dates, service dates, discharge
    dates, charge-entry dates or payment post dates depending on category).
    """

    spec_id: str
    category: str
    metric_id: str  # a real packs/base-rcm metric id
    title: str
    service_line: str
    onset: str  # observation window start (ISO date)
    window_end: str  # observation window end (ISO date)
    n_claims: int
    billed_total_cents: int
    payer: str | None = None  # exactly one of payer/plan must be set
    plan: str | None = None
    facility: str | None = None
    proc_group: str | None = None
    carc: int | None = None
    appeal_window_days: int = 60
    appealed_frac: float = 0.0
    allowed_factor: float | None = None  # underpayment: allowed = factor * expected
    rate_override: float | None = None  # contractual shift: expected = allowed = billed * rate
    post_lag_days: int | None = None  # posting lag: payment posts remit + lag (+0..6)
    charge_lag_min: int = 0  # charge-entry lag from service, days
    charge_lag_max: int = 4
    overpay_frac: float | None = None  # credit balance: duplicate payment fraction
    resolve_submit_on: str | None = None  # self-resolver: submission entered on this day
    prior_onset: str | None = None  # resolved-then-recurred: prior episode window
    prior_end: str | None = None
    prior_n: int = 0
    min_events: int = 3  # emission floor: qualifying events visible in the snapshot
    min_impact_cents: int = 1  # emission floor: recomputed impact (0 = impact-free signal)

    @property
    def self_resolving(self) -> bool:
        return self.resolve_submit_on is not None


def _a(**kw: object) -> AnomalySpec:
    return AnomalySpec(**kw)  # type: ignore[arg-type]


# The detected-anomaly population. Magnitude tiers: a few $100k+, many $5-50k,
# several sub-$1k noise. Onsets are staggered; ANM-007/029 first become visible
# at snap_003, ANM-034 at snap_002; ANM-031/032/033 self-resolve before snap_003.
# Cells never intersect the five scenario cells/windows (verify.py proves it).
ANOMALY_SPECS: tuple[AnomalySpec, ...] = (
    _a(spec_id="ANM-001", category="denial_spike", metric_id="denial_rate",
       title="Medical-necessity denial spike: Summit Peak MA Cardiology",
       payer="Summit Peak Medicare Advantage", service_line="Cardiology", carc=50,
       onset="2026-06-20", window_end="2026-07-28", n_claims=26,
       billed_total_cents=18_500_000, appealed_frac=0.15, min_events=10),
    _a(spec_id="ANM-002", category="denial_spike", metric_id="denial_rate",
       title="Missing-information denial wave: Bluestone Laboratory",
       payer="Bluestone Mutual", service_line="Laboratory", carc=16,
       onset="2026-06-01", window_end="2026-07-15", n_claims=30,
       billed_total_cents=1_950_000, min_events=10),
    _a(spec_id="ANM-003", category="denial_spike", metric_id="denial_rate",
       title="Prior-auth denials recurred: Lakewood MCO Emergency",
       payer="Lakewood Medicaid MCO", service_line="Emergency", carc=197,
       onset="2026-07-05", window_end="2026-07-31", n_claims=18,
       billed_total_cents=9_600_000, prior_onset="2026-02-10", prior_end="2026-03-10",
       prior_n=12, min_events=8),
    _a(spec_id="ANM-004", category="unworked_denials", metric_id="denials_unworked_pct",
       title="Aged unworked bundling denials: Federal Medicare General Surgery",
       payer="Federal Medicare", service_line="General Surgery", carc=97,
       onset="2026-03-01", window_end="2026-04-15", n_claims=14,
       billed_total_cents=5_800_000, min_events=6),
    _a(spec_id="ANM-005", category="denial_spike", metric_id="denial_rate",
       title="Fee-schedule denials: Veritas Comp Behavioral Health",
       payer="Veritas Comp Fund", service_line="Behavioral Health", carc=45,
       onset="2026-07-10", window_end="2026-07-25", n_claims=3,
       billed_total_cents=85_000, min_events=2),
    _a(spec_id="ANM-006", category="eligibility_cluster", metric_id="denial_rate",
       title="Coverage-terminated denial cluster: Pinnacle HMO Primary Care",
       plan="Pinnacle HMO", service_line="Primary Care", carc=27,
       onset="2026-07-18", window_end="2026-08-01", n_claims=22,
       billed_total_cents=980_000, min_events=8),
    _a(spec_id="ANM-007", category="eligibility_cluster", metric_id="denial_rate",
       title="Eligibility denial burst: State MCO Standard Emergency",
       plan="State MCO Standard", service_line="Emergency", carc=27,
       onset="2026-08-02", window_end="2026-08-02", n_claims=12,
       billed_total_cents=1_450_000, min_events=6),
    _a(spec_id="ANM-008", category="eligibility_cluster", metric_id="denial_rate",
       title="Small eligibility pocket: Bluestone HMO Primary Care",
       plan="Bluestone HMO Blue", service_line="Primary Care", carc=27,
       onset="2026-07-08", window_end="2026-07-16", n_claims=3,
       billed_total_cents=42_000, min_events=2),
    _a(spec_id="ANM-009", category="duplicate", metric_id="denied_dollars",
       title="Duplicate-claim denials: Federal Medicare Imaging",
       payer="Federal Medicare", service_line="Imaging", carc=18,
       onset="2026-06-25", window_end="2026-07-25", n_claims=12,
       billed_total_cents=2_300_000, min_events=6),
    _a(spec_id="ANM-010", category="duplicate", metric_id="denied_dollars",
       title="Duplicate lab rebills: Pinnacle PPO Laboratory",
       plan="Pinnacle PPO", service_line="Laboratory", carc=18,
       onset="2026-07-05", window_end="2026-07-12", n_claims=2,
       billed_total_cents=36_000, min_events=2),
    _a(spec_id="ANM-011", category="underpayment", metric_id="underpayment_variance",
       title="Severe underpayment: Bluestone general-surgery lines at 72% of expected",
       payer="Bluestone Mutual", service_line="General Surgery", proc_group="SURG-GEN",
       allowed_factor=0.72, onset="2026-05-05", window_end="2026-07-05", n_claims=25,
       billed_total_cents=92_000_000, min_events=10),
    _a(spec_id="ANM-012", category="underpayment", metric_id="underpayment_variance",
       title="Mild underpayment: Summit Peak MA cardiology procedures at 94%",
       payer="Summit Peak Medicare Advantage", service_line="Cardiology",
       proc_group="CARD-PROC", allowed_factor=0.94, onset="2026-05-20",
       window_end="2026-07-03", n_claims=18, billed_total_cents=27_000_000, min_events=8),
    _a(spec_id="ANM-013", category="contractual", metric_id="gross_collection_rate",
       title="Contract-rate reset (working as designed): Veritas Comp orthopedics",
       payer="Veritas Comp Fund", service_line="Orthopedic Surgery", proc_group="ORTHO-SURG",
       rate_override=0.45, onset="2026-05-01", window_end="2026-06-25", n_claims=15,
       billed_total_cents=61_000_000, min_events=6),
    _a(spec_id="ANM-014", category="posting_lag", metric_id="avg_days_to_pay",
       title="Remitted-not-posted backlog: Bluestone Cardiology",
       payer="Bluestone Mutual", service_line="Cardiology", post_lag_days=30,
       onset="2026-07-08", window_end="2026-07-24", n_claims=16,
       billed_total_cents=15_000_000, min_events=8),
    _a(spec_id="ANM-015", category="posting_lag", metric_id="avg_days_to_pay",
       title="Posting stall on late-July remits: Pinnacle Oncology",
       payer="Pinnacle Health Plan", service_line="Oncology", post_lag_days=25,
       onset="2026-07-22", window_end="2026-08-01", n_claims=10,
       billed_total_cents=10_500_000, min_events=5),
    _a(spec_id="ANM-016", category="submission_gap", metric_id="bill_lag_days",
       title="Unsubmitted primary-care backlog: Meridian Health",
       payer="Meridian Health", service_line="Primary Care",
       onset="2026-06-18", window_end="2026-07-08", n_claims=24,
       billed_total_cents=1_050_000, min_events=10),
    _a(spec_id="ANM-017", category="submission_gap", metric_id="bill_lag_days",
       title="Small submission gap: Veritas Comp Primary Care",
       payer="Veritas Comp Fund", service_line="Primary Care",
       onset="2026-07-02", window_end="2026-07-09", n_claims=3,
       billed_total_cents=54_000, min_events=2),
    _a(spec_id="ANM-018", category="timely_filing", metric_id="timely_filing_at_risk_dollars",
       title="Timely-filing risk (window open): Meridian Exchange PPO at Southfield",
       plan="Meridian Exchange PPO", facility="Southfield Community Hospital",
       service_line="Primary Care", onset="2026-06-02", window_end="2026-06-24",
       n_claims=16, billed_total_cents=2_950_000, min_events=8),
    _a(spec_id="ANM-019", category="timely_filing", metric_id="timely_filing_at_risk_dollars",
       title="Timely-filing deadlines already passed: State Medicaid HMO at Northgate",
       plan="State Medicaid HMO", facility="Northgate Regional Hospital",
       service_line="Emergency", onset="2026-03-18", window_end="2026-04-08",
       n_claims=14, billed_total_cents=1_320_000, min_events=6),
    _a(spec_id="ANM-020", category="timely_filing", metric_id="timely_filing_at_risk_dollars",
       title="Timely-filing deadlines imminent: Lakewood MCO Core at Riverbend",
       plan="Lakewood MCO Core", facility="Riverbend Outpatient Campus",
       service_line="Primary Care", onset="2026-05-04", window_end="2026-05-18",
       n_claims=12, billed_total_cents=1_780_000, min_events=6),
    _a(spec_id="ANM-021", category="dnfb", metric_id="dnfb_dollars",
       title="DNFB accumulation: Northgate general-surgery discharges",
       payer="Federal Medicare", facility="Northgate Regional Hospital",
       service_line="General Surgery", onset="2026-07-03", window_end="2026-07-29",
       n_claims=20, billed_total_cents=16_000_000, min_events=8),
    _a(spec_id="ANM-022", category="dnfb", metric_id="dnfb_dollars",
       title="DNFB pocket: Southfield obstetrics discharges",
       payer="Northbridge Commercial", facility="Southfield Community Hospital",
       service_line="Obstetrics", onset="2026-06-26", window_end="2026-07-18",
       n_claims=10, billed_total_cents=6_200_000, min_events=5),
    _a(spec_id="ANM-023", category="credit_balance", metric_id="credit_balance_dollars",
       title="Double-posted payments awaiting refund: Atlas PPO Imaging",
       plan="Atlas PPO Select", service_line="Imaging", overpay_frac=1.0,
       onset="2026-06-05", window_end="2026-07-10", n_claims=9,
       billed_total_cents=8_000_000, min_events=4),
    _a(spec_id="ANM-024", category="credit_balance", metric_id="credit_balance_dollars",
       title="Small overpayment credits: Federal Medicare Primary Care",
       payer="Federal Medicare", service_line="Primary Care", overpay_frac=0.35,
       onset="2026-06-14", window_end="2026-07-16", n_claims=6,
       billed_total_cents=520_000, min_events=3),
    _a(spec_id="ANM-025", category="charge_entry_lag", metric_id="charge_lag_days",
       title="Late charge entry burst: Riverbend oncology infusions",
       payer="Federal Medicare", facility="Riverbend Outpatient Campus",
       service_line="Oncology", charge_lag_min=22, charge_lag_max=36,
       onset="2026-07-04", window_end="2026-08-01", n_claims=14,
       billed_total_cents=8_600_000, min_events=5),
    _a(spec_id="ANM-026", category="charge_entry_lag", metric_id="late_charge_pct",
       title="Late charges: Eastside cardiology",
       payer="Bluestone Mutual", facility="Eastside Medical Center",
       service_line="Cardiology", charge_lag_min=18, charge_lag_max=30,
       onset="2026-07-18", window_end="2026-08-02", n_claims=12,
       billed_total_cents=4_100_000, min_events=5),
    _a(spec_id="ANM-027", category="unworked_denials", metric_id="denials_unworked_pct",
       title="Unworked non-covered denials aging: State Medicaid FFS Emergency",
       plan="State Medicaid FFS", service_line="Emergency", carc=96,
       onset="2026-05-01", window_end="2026-07-20", n_claims=28,
       billed_total_cents=5_400_000, min_events=10),
    _a(spec_id="ANM-028", category="unworked_denials", metric_id="denials_unworked_pct",
       title="Fresh unworked medical-necessity denials: Northbridge Emergency",
       payer="Northbridge Commercial", service_line="Emergency", carc=50,
       onset="2026-06-22", window_end="2026-07-26", n_claims=16,
       billed_total_cents=3_100_000, min_events=8),
    _a(spec_id="ANM-029", category="denial_spike", metric_id="denial_rate",
       title="Non-covered denial burst: Bluestone PPO Imaging",
       plan="Bluestone PPO Blue", service_line="Imaging", carc=204,
       onset="2026-08-02", window_end="2026-08-02", n_claims=10,
       billed_total_cents=1_750_000, min_events=6),
    _a(spec_id="ANM-030", category="underpayment", metric_id="underpayment_variance",
       title="Rounding-scale lab underpayment: Lakewood MCO Plus",
       plan="Lakewood MCO Plus", service_line="Laboratory", proc_group="LAB",
       allowed_factor=0.90, onset="2026-06-08", window_end="2026-06-30", n_claims=4,
       billed_total_cents=740_000, min_events=3),
    _a(spec_id="ANM-031", category="dnfb", metric_id="dnfb_dollars",
       title="DNFB blip (clears on 2026-08-02): Westpark general surgery",
       payer="Pinnacle Health Plan", facility="Westpark Surgical Center",
       service_line="General Surgery", onset="2026-07-22", window_end="2026-07-26",
       n_claims=12, billed_total_cents=9_800_000, min_events=8,
       resolve_submit_on="2026-08-02"),
    _a(spec_id="ANM-032", category="submission_gap", metric_id="bill_lag_days",
       title="Submission hold (released 2026-08-02): Central Plaza behavioral health",
       payer="Summit Peak Medicare Advantage", facility="Central Physicians Plaza",
       service_line="Behavioral Health", onset="2026-07-14", window_end="2026-07-21",
       n_claims=15, billed_total_cents=640_000, min_events=8,
       resolve_submit_on="2026-08-02"),
    _a(spec_id="ANM-033", category="charge_hold", metric_id="charge_lag_days",
       title="Charges not entered (posted 2026-08-02): Riverbend laboratory",
       payer="Federal Medicare", facility="Riverbend Outpatient Campus",
       service_line="Laboratory", onset="2026-07-09", window_end="2026-07-15",
       n_claims=10, billed_total_cents=130_000, min_events=5,
       resolve_submit_on="2026-08-02", min_impact_cents=0),
    _a(spec_id="ANM-034", category="eligibility_cluster", metric_id="denial_rate",
       title="Eligibility denials: Meridian HMO Care Emergency",
       plan="Meridian HMO Care", service_line="Emergency", carc=27,
       onset="2026-08-01", window_end="2026-08-01", n_claims=9,
       billed_total_cents=760_000, min_events=9),
    _a(spec_id="ANM-035", category="duplicate", metric_id="denied_dollars",
       title="Duplicate lab claims: State MCO Expansion",
       plan="State MCO Expansion", service_line="Laboratory", carc=18,
       onset="2026-07-06", window_end="2026-07-22", n_claims=6,
       billed_total_cents=290_000, min_events=3),
    _a(spec_id="ANM-036", category="dnfb", metric_id="dnfb_dollars",
       title="Tiny DNFB tail: Central Plaza primary care",
       payer="Meridian Health", facility="Central Physicians Plaza",
       service_line="Primary Care", onset="2026-07-12", window_end="2026-07-24",
       n_claims=3, billed_total_cents=88_000, min_events=3),
)

SELF_RESOLVING_IDS: frozenset[str] = frozenset(s.spec_id for s in ANOMALY_SPECS if s.self_resolving)

BACKFILL_STREAM = 0xB1FF
"""Seed-sequence tag for the 2024-backfill streams (independent of every other draw)."""


@dataclass(frozen=True)
class BackfillSpec:
    """Shape of the additive 2024 prior year (backfill.py).

    Volume, seasonality, payer mix and denial propensity are declared here so
    the "how did 2025 compare with 2024?" answer has one auditable source. The
    resolution deadline is the load-bearing constant: every backfill claim is
    closed before it, which is what keeps watermark-time point metrics (A/R,
    DNFB, credit balances, timely-filing risk) exactly where they were.
    """

    service_start: int = day("2024-01-01")
    service_end: int = day("2024-12-31")  # 2024 is a leap year: 366 days
    resolved_by: int = day("2025-06-30")
    volume_ratio: float = 0.86
    """2024 claims per day as a share of the 2025 rate — the growth story."""
    denial_factor: float = 0.88
    """2024 denial propensity as a share of each payer's 2025 rate."""
    # Per-month multiplier on the daily claim rate (Jan..Dec), mean ~1.0.
    seasonality: tuple[float, ...] = (
        1.04, 0.95, 1.05, 1.02, 1.03, 0.98, 0.94, 0.96, 1.01, 1.06, 0.97, 0.99,
    )
    # Multiplicative tilt on each payer's 2025 share, in PAYERS order, applied
    # before renormalisation. The story: commercial and Medicare Advantage
    # volume grew into 2025 while the Medicaid book receded.
    payer_mix_tilt: tuple[float, ...] = (
        0.90,  # Atlas Commercial
        1.05,  # Meridian Health
        0.85,  # Silverline Medicare Advantage
        1.00,  # Northbridge Commercial
        1.15,  # State Medicaid
        1.10,  # State Medicaid MCO
        1.05,  # Federal Medicare
        0.95,  # Bluestone Mutual
        1.00,  # Pinnacle Health Plan
        1.10,  # Veritas Comp Fund
        1.10,  # Lakewood Medicaid MCO
        0.80,  # Summit Peak Medicare Advantage
    )
    # Lifecycle clips, tightened from the organic world's so that the whole year
    # closes by `resolved_by`. Each bound sits far enough into the tail of the
    # organic distribution (>2.4 sigma) that mean cycle times are unchanged, so
    # 2024-vs-2025 lag comparisons stay honest.
    max_submission_lag: int = 22  # organic: 45
    max_adjudication_lag: int = 40  # organic: 60
    max_post_lag: int = 12  # organic: 15


BACKFILL = BackfillSpec()


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
    include_backfill: bool = True  # append the closed 2024 comparison year (backfill.py)

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
