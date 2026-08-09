"""What "now" means to a relative window (design §2.6, §6.1).

Every relative range — "last 90 days", "last full month", "month to date" —
resolves against an anchor date exactly once. The anchor is not today. A
governed answer is as-of a watermark, and the only defensible "now" inside
a watermark is the newest activity that load can *see*
(``DataWatermark.newest_data_date``): wall-clock today would make the same
question return different windows on two consecutive mornings against
identical data, and the load's own clock is a fact about the ETL, not about
the business.

Load date is not the newest data date, and using it silently is the bug
this module exists to name. The nightly load that produced the reference
warehouse ran at 04:10 on 2026-08-03 over data through 2026-08-02, so
"the last 90 days" anchored on the load date ended on a day with no rows in
it — a window one day longer than the data, diluting every rate computed
over it by a day of structural zeroes.

The correction is per mode, because the two families mean different things
by "now":

- **TRAILING** and **TO_DATE** windows *end at* the anchor, so they anchor
  at ``newest_data_date``. The window ends on the last day that has data.
- **FULL_PERIODS** windows end at the last period completed *before* the
  anchor's own period. Anchoring those at ``newest_data_date`` would throw
  away a complete period whenever the load happens to land on that
  period's last day: data through Sunday 2026-08-02 would answer "last
  full week" with 07-20..07-26 and silently discard the complete week
  07-27..08-02 that is right there. They therefore anchor at the day
  *after* the newest data date — the first day the newest period is
  behind us — which includes every period the data completed and no period
  it did not.

One helper, used by interpretation, planning and re-anchoring alike, so a
playbook default and an analyst's own window can never sit on two
different "now"s.
"""

from __future__ import annotations

from datetime import date, timedelta

from revi_kernel.scope import RangeMode
from revi_kernel.watermark import DataWatermark


def window_anchor(watermark: DataWatermark, mode: RangeMode) -> date:
    """The anchor a relative range of this mode resolves against."""
    if mode is RangeMode.FULL_PERIODS:
        return watermark.newest_data_date + timedelta(days=1)
    return watermark.newest_data_date
