"""Assigning rows to strata, deterministically.

Every stratifier is a pure function of one :class:`DenialRow` plus the as-of
date. No stratifier consults the rest of the population, so a cell's identity
never depends on what else happened to be in the batch — the same row lands in
the same cell whether it arrives alone or among five thousand.

Two rules worth stating out loud:

* **Bands are caller-supplied and must cover the data.** A value that falls
  in no band raises rather than being swept into a catch-all, because a
  silently-created bucket is a population the reader never agreed to.
* **A missing value is not a band.** A denial that was never resubmitted has
  no days-to-resubmission, so it lands in :data:`UNBANDED_LABEL` rather than
  in the lowest numeric band. Putting it in "0-14 days" would invent a
  resubmission that has not happened.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from revi_statistics_contracts.contract import (
    BANDED_STRATIFIERS,
    UNBANDED_LABEL,
    Band,
    DenialRow,
    EstimationPolicy,
    Stratifier,
    StratumKey,
)

#: Value published for :attr:`Stratifier.FILING_POSITION` when the plan
#: carries no configured filing limit. "Unknown" is its own answer: absence
#: of a deadline is not evidence that a claim is still filable.
FILING_POSITION_UNKNOWN = "unknown"
FILING_POSITION_WITHIN = "within_deadline"
FILING_POSITION_PAST = "past_deadline"

FILING_RULE_CONFIRMED = "confirmed"
FILING_RULE_REQUIRES_CONFIRMATION = "requires_confirmation"


def validate_stratifiers(stratifiers: Sequence[Stratifier], policy: EstimationPolicy) -> None:
    """Reject a stratification the policy cannot actually carry out.

    Checked up front so the failure names the missing bands, rather than
    surfacing later as a row that fits no bucket.
    """
    seen: set[Stratifier] = set()
    for stratifier in stratifiers:
        if stratifier in seen:
            raise ValueError(f"stratifier {stratifier} requested twice")
        seen.add(stratifier)
        if stratifier in BANDED_STRATIFIERS and not policy.bands_for(stratifier):
            raise ValueError(
                f"stratifier {stratifier} requires caller-supplied bands; "
                f"EstimationPolicy carries none for it"
            )


def _band_label(value: int, bands: Sequence[Band], stratifier: Stratifier) -> str:
    for band in bands:
        if band.contains(value):
            return band.label
    raise ValueError(
        f"value {value} falls outside every band supplied for {stratifier}; "
        "extend the bands (an open-ended top band has upper=None) rather than "
        "leaving part of the population unbucketed"
    )


def filing_position(row: DenialRow, as_of: date) -> str:
    """Where this denial stands relative to its filing deadline.

    Evaluated at the date that actually matters for the row: the
    resubmission date if one went out (the deadline either was or was not
    met when it was filed), otherwise the as-of date (the deadline either
    has or has not passed while the denial sat). One rule, and it reads
    correctly for both a decided-rate cut and an open-inventory cut.
    """
    deadline = row.filing_deadline
    if deadline is None:
        return FILING_POSITION_UNKNOWN
    reference = row.resubmission_date if row.resubmission_date is not None else as_of
    return FILING_POSITION_PAST if reference > deadline else FILING_POSITION_WITHIN


def stratum_value(
    row: DenialRow, stratifier: Stratifier, policy: EstimationPolicy, as_of: date
) -> str:
    """This row's value for one stratifier."""
    match stratifier:
        case Stratifier.PAYER:
            return row.payer_name
        case Stratifier.PLAN:
            return row.plan_name
        case Stratifier.RECOVERY_CLASS:
            return row.recovery_class
        case Stratifier.FILING_RULE:
            return (
                FILING_RULE_CONFIRMED
                if row.filing_rule_confirmed
                else FILING_RULE_REQUIRES_CONFIRMATION
            )
        case Stratifier.FILING_POSITION:
            return filing_position(row, as_of)
        case Stratifier.AGE_BAND:
            return _band_label(row.age_days(as_of), policy.age_bands, stratifier)
        case Stratifier.DOLLAR_BAND:
            return _band_label(row.denied_amount_cents, policy.dollar_bands, stratifier)
        case Stratifier.DELAY_BAND:
            if row.days_to_resubmission is None:
                return UNBANDED_LABEL
            return _band_label(row.days_to_resubmission, policy.delay_bands, stratifier)


def stratum_key(
    row: DenialRow,
    stratifiers: Sequence[Stratifier],
    policy: EstimationPolicy,
    as_of: date,
) -> StratumKey:
    """The cell this row belongs to. Empty stratifiers means the total."""
    return StratumKey(tuple((str(s), stratum_value(row, s, policy, as_of)) for s in stratifiers))


def group_rows(
    rows: Iterable[DenialRow],
    stratifiers: Sequence[Stratifier],
    policy: EstimationPolicy,
    as_of: date,
) -> dict[StratumKey, list[DenialRow]]:
    """Partition rows into cells, insertion-independent and total."""
    grouped: dict[StratumKey, list[DenialRow]] = {}
    for row in rows:
        grouped.setdefault(stratum_key(row, stratifiers, policy, as_of), []).append(row)
    return grouped
