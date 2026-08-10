"""Deterministic statistical estimators over caller-supplied rows.

Pure functions, kernel discipline: no warehouse access, no I/O, no clock, no
randomness, no third-party dependency. The caller reads rows and hands them
over already typed; this package turns them into estimates that state their
own limits.

Start with :mod:`revi_statistics.rates` — the choice of denominator is the
part of a recoverability estimate that is usually wrong, and its module
docstring is the argument for the two this package will publish.
"""

from revi_statistics.composition import expected_recovery
from revi_statistics.contrasts import (
    compare_cohorts,
    compare_rate_cells,
    contrast_counts,
    two_proportion_z,
)
from revi_statistics.exact import (
    fishers_exact_two_sided,
    min_expected_cell_count,
    needs_exact_test,
)
from revi_statistics.intervals import (
    newcombe_risk_difference_interval,
    normal_quantile,
    proportion,
    two_sided_p_from_z,
    wilson_interval,
    z_for_confidence,
)
from revi_statistics.rates import denominator_rows, estimate_rates
from revi_statistics.strata import filing_position, group_rows, stratum_key, validate_stratifiers
from revi_statistics.timing import delay_effect_curve, estimate_durations, quantile

__all__ = [
    "compare_cohorts",
    "compare_rate_cells",
    "contrast_counts",
    "delay_effect_curve",
    "denominator_rows",
    "estimate_durations",
    "estimate_rates",
    "expected_recovery",
    "filing_position",
    "fishers_exact_two_sided",
    "group_rows",
    "min_expected_cell_count",
    "needs_exact_test",
    "newcombe_risk_difference_interval",
    "normal_quantile",
    "proportion",
    "quantile",
    "stratum_key",
    "two_proportion_z",
    "two_sided_p_from_z",
    "validate_stratifiers",
    "wilson_interval",
    "z_for_confidence",
]
