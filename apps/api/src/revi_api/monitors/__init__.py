"""Monitors: the proactive surface. Revi walks it every load and briefs it.

Four capabilities, one module each — none of them a new pipeline.

**PIN = MONITOR** (:mod:`~revi_api.monitors.pins`). A pin stores the
``TypedInvestigationSpec`` behind an artifact, never the artifact.
Evaluating it re-runs that spec through ``SubmitTurnService`` as an ordinary
TYPED first turn: zero model calls, the §6.6 validation pass, the real
findings stage, the real warnings. That choice is the load-bearing one here.
The obvious alternative — a lightweight evaluator that sums a frame, in the
shape of :mod:`revi_api.rederive` — would be a SECOND implementation of the
honesty rules, and every caveat the answer path publishes (bounded cells,
provisional buckets, population caveats, alternate bases, grade demotion)
would have to be re-earned there or silently lost. A tile is an answer, so a
tile runs the answer path. The consequence is deliberate: every tile IS a
real ``Investigation``, with a real trace and a real permalink, so tapping a
tile opens the full investigation rather than a number computed off to the
side.

**PER-LOAD EVALUATION** (:mod:`~revi_api.monitors.tiles`).
:meth:`MonitorsService.evaluate_load` is idempotent per (pin, watermark) and
has two callers: the scheduled tick (:mod:`revi_api.monitors_sweep`) keeps
an idle deployment current, and the brief route calls it too, so a brief for
a load nobody swept is computed rather than empty. One primitive, two
callers, no drift.

**MATERIALITY** (:mod:`~revi_api.monitors.brief`). Every gate is governed
content (``packs/base-rcm/monitors.yaml`` via :mod:`revi_api.monitors_policy`);
this package holds no threshold. Alert fatigue is the death mode, so when in
doubt the gate holds: an unmeasurable movement is counted, not briefed.
Everything withheld is counted on the response — withheld visibly, never
silently.

**LEAD LIFECYCLE** (:mod:`~revi_api.monitors.leads`). A human may claim a
lead is resolved; only the platform may confirm it, by re-running the lead's
own drill across consecutive loads. Three rules keep that asymmetry from
becoming decoration:

* **A confirmation is evidence FROM AFTER THE CLAIM.** Only a load strictly
  after ``claimed_at_watermark`` may count toward the streak. Loads that ran
  before it are discarded, on sight, wherever they were banked: absence
  before anybody claimed a fix is not evidence the fix worked.
* **A confirmed lead stays under verification.** ``resolved_confirmed`` is a
  verdict about data, and data changes. A confirmed lead that reappears in
  the detection feed REGRESSES, in a sentence that states both facts.
* **A lead in the feed is not a fixed lead.** Never confirmed while the
  detector is still firing for its cell, and never PUBLISHED as confirmed on
  a card that is in the load's own feed — asserted at payload build, because
  no other stage is in a position to notice.

:class:`MonitorsService` in :mod:`~revi_api.monitors.service` assembles the
four into one object; :mod:`~revi_api.monitors.spec` turns a stored
investigation into the re-runnable spec a pin holds, and
:mod:`~revi_api.monitors.cards` puts lead state onto worklist cards.
"""

from __future__ import annotations

# Internals re-exported so the tests that pin these rules keep importing them
# from one place. Not part of the package's contract — read them in the module
# that defines them.
from revi_api.monitors.brief import _cap as _cap
from revi_api.monitors.brief import _headline_sentence as _headline_sentence
from revi_api.monitors.brief import _immaterial_note as _immaterial_note
from revi_api.monitors.cards import _cash_timing_lanes as _cash_timing_lanes
from revi_api.monitors.cards import annotate_time_to_impact
from revi_api.monitors.common import MonitorsNotFoundError
from revi_api.monitors.leads import (
    _assert_no_confirmed_lead_in_feed as _assert_no_confirmed_lead_in_feed,
)
from revi_api.monitors.leads import _is_strictly_after as _is_strictly_after
from revi_api.monitors.leads import _merged_verifications as _merged_verifications
from revi_api.monitors.leads import _publishable_lead_status as _publishable_lead_status
from revi_api.monitors.leads import _regressed_on_reappearance as _regressed_on_reappearance
from revi_api.monitors.leads import _repaired_lead as _repaired_lead
from revi_api.monitors.leads import lead_payload
from revi_api.monitors.pins import _monitor_confirmation as _monitor_confirmation
from revi_api.monitors.pins import pin_payload
from revi_api.monitors.service import MonitorsService
from revi_api.monitors.spec import _eq_filters_of as _eq_filters_of
from revi_api.monitors.spec import _narrowed_to_cell as _narrowed_to_cell
from revi_api.monitors.spec import spec_hash as spec_hash
from revi_api.monitors.spec import typed_spec_from_analysis
from revi_api.monitors.tiles import (
    _assert_subject_matches_label as _assert_subject_matches_label,
)
from revi_api.monitors.tiles import _Headline as _Headline
from revi_api.monitors.tiles import _not_comparable_reason as _not_comparable_reason
from revi_api.monitors.tiles import _spec_names_one_cell as _spec_names_one_cell
from revi_api.monitors.tiles import _stale_result_reason as _stale_result_reason
from revi_api.monitors.tiles import _subject_mismatch as _subject_mismatch

__all__ = [
    "MonitorsNotFoundError",
    "MonitorsService",
    "annotate_time_to_impact",
    "lead_payload",
    "pin_payload",
    "spec_hash",
    "typed_spec_from_analysis",
]
