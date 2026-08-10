"""Is this divergence a bug, or a fossil?

The corpus is a HISTORICAL record. Every stored investigation was published
by whatever engine was running the day it was written, and the disclosure
contract this audit holds answers to has changed since, twice:

* published ceilings gained their ``__is_bound`` marker and stopped being
  ranked against measured cells;
* findings whose probe declared its own window started stating THAT window
  rather than the investigation window.

An audit that cannot tell "the engine publishes this today" from "the engine
published this in August" hands over a fix queue nobody can work: divergences
that re-run correctly on the live engine are fossils, not bugs.

So every divergence is dated. ``created_at`` on the stored investigation is
compared against :data:`DISCLOSURE_CONTRACT_SINCE`, and the verdict FAILS on
live divergences only. Archaeological ones are counted, reported, and never
silently dropped — a fossil is still a record of something that reached a
human, and a growing pile of them is a reason to re-run the corpus, not a
reason to look away.

Two rules, so this cannot become a way of not looking:

* the boundary moves ONLY when a disclosure fix actually lands, and every
  entry below names the fix and its commit. A divergence dated after the
  newest entry is live, full stop;
* an investigation with no ``created_at`` is LIVE. Undatable is not the same
  as old, and the safe reading of "I do not know when this was written" is
  the one that fails the build.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class DisclosureFix:
    """One landed change to what an answer is obliged to say about itself."""

    #: Short handle, used in the report.
    code: str
    #: When it landed, UTC. Commit timestamps are recorded in local time;
    #: these are converted, because ``created_at`` on the corpus is UTC and
    #: comparing the two in different zones is a four-hour lie.
    landed: dt.datetime
    #: The commit, so the boundary is checkable rather than asserted.
    commit: str
    what: str


#: In landing order. The NEWEST entry is the boundary.
DISCLOSURE_FIXES: tuple[DisclosureFix, ...] = (
    DisclosureFix(
        code="bound_markers",
        landed=dt.datetime(2026, 8, 9, 17, 25, 3, tzinfo=dt.UTC),
        commit="f51d511",
        what=(
            "a suppressed numerator publishes __is_bound / __bound / "
            "__bound_population and a '≤' in its own title; a later change "
            "(6a42ad7) then pulled bounded cells out of the ranking entirely"
        ),
    ),
    DisclosureFix(
        code="probe_windows",
        # Bracketed by corpus evidence, not by a guess: the last answer
        # WITHOUT the disclosure is 2026-08-10T05:10:10Z, the first WITH it
        # is 2026-08-10T05:34:42Z (inv_d4cb71aa91fb, whose header carries the
        # own-period note and whose findings state 2026-07-06..2026-08-02).
        # 05:30 sits inside that gap.
        landed=dt.datetime(2026, 8, 10, 5, 30, 0, tzinfo=dt.UTC),
        # A boundary that excuses divergences by DATE while naming no commit
        # anybody can check is the one thing this field exists to prevent.
        # ``test_every_disclosure_fix_names_a_real_commit`` resolves every
        # hash here through ``git cat-file``, so it cannot go back to prose.
        commit="5ad1774",
        what=(
            "a finding whose probe declared its own window states THAT window "
            "in its title and statement, publishes it as "
            "<metric>__window_start / __window_end (and __prior_window_start / "
            "__prior_window_end when the comparison moved with it), and the "
            "context header says some checks ran their own periods"
        ),
    ),
)

#: Anything published before this was written under a different contract.
DISCLOSURE_CONTRACT_SINCE: dt.datetime = max(fix.landed for fix in DISCLOSURE_FIXES)

ARCHAEOLOGY = "archaeology"
LIVE = "live"


def classify(created_at: dt.datetime | None) -> str:
    """``live`` or ``archaeology`` for one stored investigation.

    ``None`` is ``live`` on purpose: see the module docstring. An undated
    record must not be able to excuse itself.
    """
    if created_at is None:
        return LIVE
    moment = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=dt.UTC)
    return ARCHAEOLOGY if moment < DISCLOSURE_CONTRACT_SINCE else LIVE
