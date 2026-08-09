"""Dimension tables: fixed fictional entities plus seeded synthetic patients/providers.

All payer/plan/facility names are invented. CARC descriptions are paraphrased in our
own words — the official X12 text is licensed and is never copied here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from revi_warehouse.config import CALENDAR_END, CALENDAR_START, GeneratorConfig

# --- Payers -------------------------------------------------------------------
# (name, payer_type, financial_class, claim_weight, contract_rate,
#  adj_lag_mean, adj_lag_sd, post_lag_mean, post_lag_sd, patient_resp_frac, base_denial_prob)


@dataclass(frozen=True)
class PayerSpec:
    name: str
    payer_type: str
    financial_class: str
    weight: float
    contract_rate: float
    adj_lag_mean: float
    adj_lag_sd: float
    post_lag_mean: float
    post_lag_sd: float
    patient_resp_frac: float
    denial_prob: float


PAYERS: tuple[PayerSpec, ...] = (
    PayerSpec("Atlas Commercial", "COMMERCIAL", "Commercial", 0.20, 0.55, 12.0, 2.5, 2.2, 0.9, 0.18, 0.07),
    PayerSpec("Meridian Health", "COMMERCIAL", "Commercial", 0.10, 0.54, 12.0, 4.0, 2.5, 1.0, 0.17, 0.07),
    PayerSpec(
        "Silverline Medicare Advantage",
        "MEDICARE_ADVANTAGE",
        "Medicare Advantage",
        0.08,
        0.45,
        16.0,
        5.0,
        3.0,
        1.2,
        0.10,
        0.08,
    ),
    PayerSpec(
        "Northbridge Commercial", "COMMERCIAL", "Commercial", 0.08, 0.56, 14.0, 4.0, 2.5, 1.0, 0.18, 0.06
    ),
    PayerSpec("State Medicaid", "MEDICAID", "Medicaid", 0.16, 0.34, 18.0, 5.0, 4.0, 0.8, 0.01, 0.08),
    PayerSpec("State Medicaid MCO", "MEDICAID_MCO", "Medicaid", 0.05, 0.36, 17.0, 5.0, 3.5, 1.2, 0.01, 0.08),
    PayerSpec("Federal Medicare", "MEDICARE", "Medicare", 0.12, 0.42, 14.0, 3.0, 2.0, 0.8, 0.16, 0.05),
    PayerSpec("Bluestone Mutual", "BCBS", "Blue Cross", 0.06, 0.50, 13.0, 4.0, 2.5, 1.0, 0.15, 0.06),
    PayerSpec(
        "Pinnacle Health Plan", "COMMERCIAL", "Commercial", 0.05, 0.52, 15.0, 5.0, 3.0, 1.2, 0.16, 0.07
    ),
    PayerSpec("Veritas Comp Fund", "OTHER", "Workers Comp", 0.02, 0.60, 20.0, 7.0, 4.0, 1.5, 0.00, 0.07),
    PayerSpec(
        "Lakewood Medicaid MCO", "MEDICAID_MCO", "Medicaid", 0.04, 0.35, 17.0, 5.0, 3.5, 1.2, 0.01, 0.08
    ),
    PayerSpec(
        "Summit Peak Medicare Advantage",
        "MEDICARE_ADVANTAGE",
        "Medicare Advantage",
        0.04,
        0.44,
        16.0,
        5.0,
        3.0,
        1.2,
        0.10,
        0.08,
    ),
)

# --- Plans --------------------------------------------------------------------
# (payer_name, plan_name, product_type, timely_filing_days, timely_filing_basis, weight_within_payer)

PLANS: tuple[tuple[str, str, str, int, str, float], ...] = (
    ("Atlas Commercial", "Atlas PPO Select", "PPO", 180, "SUBMISSION", 0.40),
    ("Atlas Commercial", "Atlas HMO Complete", "HMO", 180, "SUBMISSION", 0.25),
    ("Atlas Commercial", "Atlas POS Flex", "POS", 180, "SUBMISSION", 0.20),
    ("Atlas Commercial", "Atlas National PPO", "PPO", 365, "SUBMISSION", 0.15),
    ("Meridian Health", "Meridian PPO Prime", "PPO", 180, "SERVICE", 0.40),
    ("Meridian Health", "Meridian HMO Care", "HMO", 180, "SERVICE", 0.30),
    ("Meridian Health", "Meridian POS Choice", "POS", 180, "SERVICE", 0.20),
    ("Meridian Health", "Meridian Exchange PPO", "PPO", 90, "SERVICE", 0.10),
    ("Silverline Medicare Advantage", "Silverline MA Classic", "MA", 365, "SERVICE", 0.60),
    ("Silverline Medicare Advantage", "Silverline MA Plus", "MA", 365, "SERVICE", 0.40),
    ("Northbridge Commercial", "Northbridge PPO", "PPO", 120, "SUBMISSION", 0.50),
    ("Northbridge Commercial", "Northbridge HMO", "HMO", 120, "SUBMISSION", 0.30),
    ("Northbridge Commercial", "Northbridge POS", "POS", 120, "SUBMISSION", 0.20),
    ("State Medicaid", "State Medicaid FFS", "FFS", 365, "SERVICE", 0.62),
    ("State Medicaid", "State Medicaid HMO", "HMO", 90, "SERVICE", 0.38),
    ("State Medicaid MCO", "State MCO Standard", "MCO", 95, "SERVICE", 0.70),
    ("State Medicaid MCO", "State MCO Expansion", "MCO", 95, "SERVICE", 0.30),
    ("Federal Medicare", "Federal Medicare Part A", "FFS", 365, "SERVICE", 0.35),
    ("Federal Medicare", "Federal Medicare Part B", "FFS", 365, "SERVICE", 0.65),
    ("Bluestone Mutual", "Bluestone PPO Blue", "PPO", 180, "SERVICE", 0.50),
    ("Bluestone Mutual", "Bluestone HMO Blue", "HMO", 180, "SERVICE", 0.30),
    ("Bluestone Mutual", "Bluestone Federal PPO", "PPO", 365, "SERVICE", 0.20),
    ("Pinnacle Health Plan", "Pinnacle PPO", "PPO", 90, "SUBMISSION", 0.50),
    ("Pinnacle Health Plan", "Pinnacle HMO", "HMO", 90, "SUBMISSION", 0.30),
    ("Pinnacle Health Plan", "Pinnacle POS", "POS", 90, "SUBMISSION", 0.20),
    ("Veritas Comp Fund", "Veritas Comp Standard", "FFS", 365, "SERVICE", 1.00),
    ("Lakewood Medicaid MCO", "Lakewood MCO Core", "MCO", 95, "SERVICE", 0.70),
    ("Lakewood Medicaid MCO", "Lakewood MCO Plus", "MCO", 95, "SERVICE", 0.30),
    ("Summit Peak Medicare Advantage", "Summit Peak MA", "MA", 365, "SERVICE", 0.60),
    ("Summit Peak Medicare Advantage", "Summit Peak MA Choice", "MA", 365, "SERVICE", 0.40),
)

# --- Facilities ---------------------------------------------------------------

FACILITIES: tuple[tuple[str, str, float], ...] = (
    ("Eastside Medical Center", "East", 0.22),
    ("Northgate Regional Hospital", "North", 0.20),
    ("Westpark Surgical Center", "West", 0.13),
    ("Southfield Community Hospital", "South", 0.18),
    ("Central Physicians Plaza", "Central", 0.15),
    ("Riverbend Outpatient Campus", "East", 0.12),
)

# --- Service lines ------------------------------------------------------------
# (name, weight, specialty, institutional_prob, ((proc_group, weight), ...))

SERVICE_LINES: tuple[tuple[str, float, str, float, tuple[tuple[str, float], ...]], ...] = (
    (
        "Imaging",
        0.14,
        "Radiology",
        0.10,
        (("IMG-CT", 0.30), ("IMG-MR", 0.20), ("IMG-XR", 0.40), ("IMG-US", 0.10)),
    ),
    (
        "Orthopedic Surgery",
        0.10,
        "Orthopedic Surgery",
        0.55,
        (("ORTHO-SURG", 0.60), ("EM-VISIT", 0.25), ("IMG-XR", 0.15)),
    ),
    (
        "Cardiology",
        0.10,
        "Cardiology",
        0.35,
        (("CARD-PROC", 0.50), ("EM-VISIT", 0.30), ("IMG-US", 0.20)),
    ),
    (
        "Emergency",
        0.16,
        "Emergency Medicine",
        0.60,
        (("ED-VISIT", 0.55), ("IMG-CT", 0.20), ("LAB", 0.25)),
    ),
    ("Primary Care", 0.18, "Family Medicine", 0.02, (("EM-VISIT", 0.70), ("LAB", 0.30))),
    (
        "General Surgery",
        0.08,
        "General Surgery",
        0.55,
        (("SURG-GEN", 0.60), ("EM-VISIT", 0.25), ("LAB", 0.15)),
    ),
    ("Oncology", 0.06, "Oncology", 0.30, (("ONC-INF", 0.55), ("EM-VISIT", 0.25), ("LAB", 0.20))),
    ("Laboratory", 0.10, "Pathology", 0.02, (("LAB", 1.00),)),
    (
        "Obstetrics",
        0.04,
        "Obstetrics & Gynecology",
        0.50,
        (("OB-DEL", 0.40), ("EM-VISIT", 0.40), ("IMG-US", 0.20)),
    ),
    ("Behavioral Health", 0.04, "Psychiatry", 0.05, (("BH-VISIT", 1.00),)),
)

# --- Procedure groups ---------------------------------------------------------
# proc_code prefixes are two letters + three digits: deliberately NOT a real CPT/HCPCS shape.
# (group, code_prefix, revenue_code, median_billed_cents, lognormal_sigma)

PROC_GROUPS: tuple[tuple[str, str, str, int, float], ...] = (
    ("ORTHO-SURG", "XS", "0360", 450_000, 0.45),
    ("IMG-CT", "XC", "0350", 120_000, 0.40),
    ("IMG-MR", "XM", "0610", 180_000, 0.40),
    ("IMG-XR", "XR", "0320", 35_000, 0.40),
    ("IMG-US", "XU", "0402", 45_000, 0.40),
    ("CARD-PROC", "XK", "0480", 250_000, 0.45),
    ("ED-VISIT", "XE", "0450", 85_000, 0.50),
    ("EM-VISIT", "XV", "0510", 22_000, 0.35),
    ("LAB", "XL", "0300", 12_000, 0.50),
    ("SURG-GEN", "XG", "0361", 320_000, 0.45),
    ("ONC-INF", "XO", "0335", 280_000, 0.45),
    ("OB-DEL", "XB", "0720", 350_000, 0.35),
    ("BH-VISIT", "XH", "0900", 25_000, 0.35),
)

PROC_CODES_PER_GROUP = 6

# --- Denial codes -------------------------------------------------------------
# Paraphrased descriptions (our own words — never the licensed X12 text).

DENIAL_CODES: tuple[tuple[int, str, str], ...] = (
    (1, "Amount was applied to the member's deductible.", "PATIENT_RESP"),
    (2, "Amount was applied as member coinsurance.", "PATIENT_RESP"),
    (3, "Amount was applied as the member's copay.", "PATIENT_RESP"),
    (4, "The procedure code conflicts with the reported modifier, or a needed modifier is absent.", "CODING"),
    (11, "The diagnosis on the claim does not support the procedure billed.", "CODING"),
    (16, "The claim is missing information or contains a submission error and cannot be processed.", "OTHER"),
    (18, "This appears to duplicate a claim or service already on file.", "DUPLICATE"),
    (22, "Coordination of benefits: another insurer may be responsible for this care.", "COB"),
    (23, "A prior payer's adjudication affected what this payer will allow.", "COB"),
    (27, "The charges were incurred after the member's coverage had ended.", "ELIGIBILITY"),
    (29, "The claim arrived after the filing deadline had already passed.", "TIMELY_FILING"),
    (45, "The billed charge exceeds the contracted or fee-schedule amount.", "CONTRACTUAL"),
    (
        50,
        "The service was judged not medically necessary under the plan's coverage rules.",
        "MEDICAL_NECESSITY",
    ),
    (96, "The charges are for a service the plan does not cover.", "OTHER"),
    (
        97,
        "Payment for this service is already included in the allowance for another adjudicated service.",
        "CODING",
    ),
    (
        109,
        "This payer is not responsible for the claim; it must go to the correct payer or contractor.",
        "COB",
    ),
    (
        151,
        "Payment was reduced because documentation does not support this quantity or frequency.",
        "MEDICAL_NECESSITY",
    ),
    (197, "Required precertification or prior authorization was not obtained before the service.", "AUTH"),
    (204, "The service is not a covered benefit under the member's current plan.", "ELIGIBILITY"),
    (253, "A federal sequestration reduction was applied to the payment.", "CONTRACTUAL"),
)

# Fixed CARC -> claim adjustment group code. PR = patient responsibility, OA = other adjustment.
CARC_GROUP: dict[int, str] = {1: "PR", 2: "PR", 3: "PR", 22: "OA", 23: "OA", 109: "OA"}

# Generic denial CARC mix (normalized at draw time). Scenario CARCs (197 for the
# Meridian x Imaging cell, forced 22 and 29) are drawn separately.
GENERIC_CARC_MIX: tuple[tuple[int, float], ...] = (
    (16, 0.14),
    (18, 0.10),
    (50, 0.10),
    (45, 0.08),
    (96, 0.08),
    (27, 0.07),
    (97, 0.07),
    (204, 0.07),
    (151, 0.06),
    (197, 0.06),
    (22, 0.05),
    (4, 0.04),
    (11, 0.04),
    (109, 0.03),
    (23, 0.03),
    (1, 0.02),
    (2, 0.02),
    (3, 0.02),
    (29, 0.01),
    (253, 0.01),
)

FIRST_NAMES = (
    "Avery", "Blake", "Casey", "Dana", "Ellis", "Frankie", "Gray", "Harper", "Indigo", "Jules",
    "Kendall", "Lane", "Morgan", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Sage", "Tatum",
    "Uma", "Vale", "Winter", "Xen", "Yael", "Zion", "Arden", "Briar", "Cameron", "Devon",
    "Emerson", "Finley", "Greer", "Hollis", "Ira", "Jamie", "Kai", "Lennon", "Marlow", "Nico",
)
LAST_NAMES = (
    "Stonebrook", "Fairweather", "Ashgrove", "Winterbourne", "Maplestone", "Corwin", "Delacroix",
    "Everhart", "Fenwick", "Gablewood", "Hollowell", "Iversen", "Jettison", "Kingsworth",
    "Larkspur", "Mossberg", "Nightingale", "Oakhurst", "Pemberly", "Quillfeather", "Ravenscroft",
    "Silverton", "Thornfield", "Umberland", "Vandermeer", "Westbrook", "Yarrow", "Zellwood",
    "Amberline", "Birchall", "Cresswell", "Dunmore", "Eastvale", "Foxglove", "Greenhollow",
    "Hartwell", "Ironwood", "Juniper", "Kestrel", "Lockridge", "Meadowfair", "Northwind",
    "Overbrook", "Pinehurst", "Quarry", "Riverstone", "Summerfield", "Thistledown", "Underhill",
    "Willowmere",
)
ZIP_CODES = (
    "98901", "98902", "98903", "98904", "98905", "98906", "98907", "98908", "98909", "98910",
    "98911", "98912", "98913", "98914", "98915", "98916", "98917", "98918", "98919", "98920",
)

SYNTHETIC_HOLIDAYS = (
    "2024-01-01", "2024-05-27", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    "2025-01-01", "2025-05-26", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-05-25", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
)


@dataclass
class Dims:
    """Generated dimension content plus lookup indices used by the world builder."""

    payer_ids: list[str] = field(default_factory=list)
    plan_ids: list[str] = field(default_factory=list)
    plan_payer_idx: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    plans_by_payer: dict[int, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
    provider_ids: list[str] = field(default_factory=list)
    provider_specialty: list[str] = field(default_factory=list)
    providers_by_service_line: dict[int, np.ndarray] = field(default_factory=dict)
    facility_ids: list[str] = field(default_factory=list)
    service_line_ids: list[str] = field(default_factory=list)
    proc_codes: dict[str, list[str]] = field(default_factory=dict)
    payer_index: dict[str, int] = field(default_factory=dict)
    plan_index: dict[str, int] = field(default_factory=dict)
    facility_index: dict[str, int] = field(default_factory=dict)
    service_line_index: dict[str, int] = field(default_factory=dict)
    patient_cols: dict[str, np.ndarray] = field(default_factory=dict)
    provider_rows: dict[str, list[str]] = field(default_factory=dict)


def build_dims(config: GeneratorConfig, rng: np.random.Generator) -> Dims:
    """Build all dimension content. rng call order here is part of the determinism contract."""
    dims = Dims()

    dims.payer_ids = [f"PAY-{i + 1:02d}" for i in range(len(PAYERS))]
    dims.payer_index = {p.name: i for i, p in enumerate(PAYERS)}

    dims.plan_ids = [f"PLN-{i + 1:02d}" for i in range(len(PLANS))]
    dims.plan_index = {p[1]: i for i, p in enumerate(PLANS)}
    plan_payer = np.array([dims.payer_index[p[0]] for p in PLANS], dtype=np.int64)
    dims.plan_payer_idx = plan_payer
    for payer_i in range(len(PAYERS)):
        idx = np.flatnonzero(plan_payer == payer_i)
        weights = np.array([PLANS[j][5] for j in idx], dtype=np.float64)
        dims.plans_by_payer[payer_i] = (idx, weights / weights.sum())

    dims.facility_ids = [f"FAC-{i + 1:d}" for i in range(len(FACILITIES))]
    dims.facility_index = {f[0]: i for i, f in enumerate(FACILITIES)}

    dims.service_line_ids = [f"SVC-{i + 1:02d}" for i in range(len(SERVICE_LINES))]
    dims.service_line_index = {s[0]: i for i, s in enumerate(SERVICE_LINES)}

    # Providers: allocate across specialties proportionally to service-line weight (min 3 each).
    counts = np.maximum(3, np.floor(np.array([s[1] for s in SERVICE_LINES]) * config.n_providers)).astype(int)
    while counts.sum() > config.n_providers:
        counts[int(np.argmax(counts))] -= 1
    while counts.sum() < config.n_providers:
        counts[int(np.argmin(counts))] += 1
    first_idx = rng.integers(0, len(FIRST_NAMES), size=config.n_providers)
    last_idx = rng.integers(0, len(LAST_NAMES), size=config.n_providers)
    provider_line = np.repeat(np.arange(len(SERVICE_LINES)), counts)
    for i in range(config.n_providers):
        line = SERVICE_LINES[provider_line[i]]
        dims.provider_ids.append(f"PRV-{i + 1:03d}")
        dims.provider_specialty.append(line[2])
    provider_names = [
        f"Dr. {FIRST_NAMES[first_idx[i]]} {LAST_NAMES[last_idx[i]]} ({i + 1:03d})"
        for i in range(config.n_providers)
    ]
    npis = [f"99{10_000_000 + i * 13:08d}" for i in range(config.n_providers)]
    for line_i in range(len(SERVICE_LINES)):
        dims.providers_by_service_line[line_i] = np.flatnonzero(provider_line == line_i)
    dims.provider_rows = {
        "provider_id": dims.provider_ids,
        "provider_name": provider_names,
        "npi_synthetic": npis,
        "specialty": dims.provider_specialty,
    }

    # Patients: clearly synthetic but shaped like PHI (for masking tests).
    # Stored as numeric codes; names/ids are materialized in SQL (registering
    # large object-dtype arrays into DuckDB is pathologically slow).
    n = config.n_patients
    pf = rng.integers(0, len(FIRST_NAMES), size=n)
    pl = rng.integers(0, len(LAST_NAMES), size=n)
    dob_days = rng.integers(
        int(np.datetime64("1940-01-01").astype(int)),
        int(np.datetime64("2010-12-31").astype(int)),
        size=n,
    )
    member_perm = rng.permutation(n)
    zips = rng.integers(0, len(ZIP_CODES), size=n)
    dims.patient_cols = {
        "idx": np.arange(n, dtype=np.int64),
        "first_i": pf.astype(np.int64),
        "last_i": pl.astype(np.int64),
        "dob": dob_days.astype("datetime64[D]").astype("datetime64[us]"),
        "member_i": member_perm.astype(np.int64),
        "zip_i": zips.astype(np.int64),
    }

    for group, prefix, _rev, _median, _sigma in PROC_GROUPS:
        dims.proc_codes[group] = [f"{prefix}{100 + 7 * k:03d}" for k in range(PROC_CODES_PER_GROUP)]

    return dims


def calendar_rows() -> dict[str, list[object]]:
    """dim_calendar content 2024-01-01..2026-12-31 with business-day flags.

    Spans the 2024 backfill year as well as the organic era so that
    business-day period alignment works on either side of a year-over-year
    comparison.
    """
    holidays = {dt.date.fromisoformat(h) for h in SYNTHETIC_HOLIDAYS}
    dates: list[object] = []
    business: list[object] = []
    iso_week: list[object] = []
    iso_year: list[object] = []
    month: list[object] = []
    quarter: list[object] = []
    d = dt.date(1970, 1, 1) + dt.timedelta(days=CALENDAR_START)
    end = dt.date(1970, 1, 1) + dt.timedelta(days=CALENDAR_END)
    while d <= end:
        iso = d.isocalendar()
        dates.append(d.isoformat())
        business.append(d.weekday() < 5 and d not in holidays)
        iso_week.append(iso.week)
        iso_year.append(iso.year)
        month.append(d.month)
        quarter.append((d.month - 1) // 3 + 1)
        d += dt.timedelta(days=1)
    return {
        "cal_date": dates,
        "is_business_day": business,
        "iso_week": iso_week,
        "iso_year": iso_year,
        "month": month,
        "quarter": quarter,
    }
