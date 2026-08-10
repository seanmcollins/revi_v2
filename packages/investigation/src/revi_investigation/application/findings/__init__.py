"""Findings evaluation (design §8.1 steps 12-13): certified, referent-
addressable results built from the final frames, with drillable cohorts.

Five finding shapes, tried in order — all generic, none keyed to any
question or playbook id:

**Movement** (preferred). The primary findings frame is the first
``compare`` output that carries at least one dimension column and a money
measure with its delta. Rows are ranked by delta **ascending** (the biggest
declines of a higher-is-good measure first) and the top N become findings
F1, F2, ... Each carries current/prior/delta cents and pct change, and
``impact_cents`` equal to the delta. That ascending default holds only
while the question asserted no direction: when the spec carries one
(``AskedDirection``, resolved against the metric's sign convention), rows
moving the other way are not eligible to be the answer, and an empty
direction-matched set says so before the opposite is offered as context —
see ``_select_directional``.

**Concentration** (fallback). Plenty of real questions have no comparison
at all — "do I have a COB problem?", "score my facilities", "what's aging
out of timely filing?". Their playbooks rank a population instead of
comparing two windows, so when no compare shape exists the first ``rank``
output carrying a dimension column and a measure supplies the findings, in
rank order, with ``impact_cents`` set only when the ranked measure is money
(a claim count is not dollars, and pretending otherwise would invent an
impact). Share-of-total columns ride along when the playbook computed them.

**Scalar** (the ungrouped answer). The plainest question there is — "what
is our net collection rate over the last 90 days?" — plans one probe, no
dimensions, no comparison, and produces one frame with one row and one
cell. It has no dimension column, so neither shape above can see it:
``find_primary_compare`` and ``find_primary_concentration`` both require
``_dimension_columns(frame)`` to be non-empty. Without this shape the probe
executes, the number is computed, the chart draws it, and ``findings`` comes
back empty — which also short-circuits the narrative stage and leaves the
answer silent, so a computed number reaches nobody. A frame with no
dimension columns and exactly one row therefore publishes its metric cells as
findings: the level, the window, the grade, and — when the turn carried a
comparison, so the frame also holds ``__prior``/``__delta``/``__pct_change``
— the movement. Both sides are rendered in the metric contract's own unit,
so a ratio reads as a percentage and money as dollars; ``impact_cents`` is
set only for money, exactly as in the concentration path. A suppressed cell
publishes no finding: "suppressed" is not a level.

**Trend** (the series). An ungrouped frame with a *time bucket* column and
more than one row is neither a scalar nor a breakdown — it is a series, and
the scalar path refuses it by construction ("a frame with more than one row
is not a scalar"). Without this shape, "denial rate by month for the last 6
months" either collapses into one six-month number (grain dropped, silently)
or publishes nothing at all (grain honored, nothing to say). One
finding per measure states it as a series: where it started, where it
ended, and its extremes with the bucket each fell in. ``impact_cents``
stays unset — the end-to-end movement of a series is a description, not a
recoverable figure.

**Premise** (before all of them). A question that *states* a movement
("why did denials double") is answered honestly only once that movement has
been measured: the planner adds an ungrouped premise probe
(``BuildInvestigationPlanService.PREMISE_PREFIX``) and :func:`verify_premise`
checks the asserted direction against the aggregate. When the aggregate
moved the other way the correction leads — a ``premise_false`` warning
first, and F1 is the correction itself with the aggregate figures behind it
— and the direction-matched cells follow as context. Unchecked, "why did
denials at Federal Medicare double in July" is answered with the three CARC
cells that rose, totalling $3,204, inside a fall from $58,983.54 to
$10,915.24 that no sentence mentions.

Whichever shape applies, each finding gets — via the referent registry — a
drillable :class:`CohortDefinition` at the CLAIM entity scoped to the
finding's dimension values plus the analysis window, so a later ``DrillInto``
refinement can pin the exact population shown.

Conclusion policies gate confidence: when a playbook's policies demand a
stronger grade than the frame provides (proxy or discovery evidence, or a
policy requiring DIRECT), the finding's confidence drops to "qualified" —
weak evidence can surface, but never in certified language. A comparison
whose two windows are different lengths qualifies a finding for the same
reason and additionally withholds ``impact_cents``
(:mod:`revi_investigation.application.comparison` documents why).

Every value that reaches a title or a statement is rendered through
:mod:`revi_investigation.application.rendering`, in the unit the metric
contract declares — never ``repr``, never floor-divided dollars beside raw
cents, and never a bare CARC integer without its group code and title.

Every compare row is also registered as a dimension-value referent
(D1, D2, ...) so table rows are addressable in follow-up turns.
**Reading order.** :mod:`~revi_investigation.application.findings.windows`
says what window a probe actually measured;
:mod:`~revi_investigation.application.findings.shapes` recognises the shape a
findings set has; :mod:`~revi_investigation.application.findings.bounds`
holds bounded values and the census of what was selected;
:mod:`~revi_investigation.application.findings.premise` verifies the premise a
question asserts; and
:mod:`~revi_investigation.application.findings.service` is the stage itself.
Each module depends only on the ones before it.
"""

from __future__ import annotations

from revi_investigation.application.findings.bounds import (
    MAX_BOUNDED_SHARE_FOR_RANKING,
    FindingsResult,
    SelectionCensus,
    bound_text,
    row_noun,
)
from revi_investigation.application.findings.bounds import _bound_values as _bound_values
from revi_investigation.application.findings.builders import claimed_rank
from revi_investigation.application.findings.premise import (
    PREMISE_MAGNITUDE_BAND,
    MagnitudeVerdict,
    PremiseCheck,
    movement_forms,
    premise_verdict_sentence,
    verify_premise,
)
from revi_investigation.application.findings.service import EvaluateFindingsService
from revi_investigation.application.findings.shapes import (
    CompareShape,
    ConcentrationShape,
    MovementShape,
    ScalarShape,
    TerminalCensoring,
    TrendShape,
    as_number,
    find_primary_compare,
    find_primary_concentration,
    find_primary_movement,
    find_scalar_shapes,
    find_trend_shapes,
    terminal_bucket_censoring,
)
from revi_investigation.application.findings.windows import (
    PRIOR_WINDOW_END_SUFFIX,
    PRIOR_WINDOW_START_SUFFIX,
    WINDOW_END_SUFFIX,
    WINDOW_START_SUFFIX,
    probe_window_disclosure,
    published_window_note,
)

__all__ = [
    "MAX_BOUNDED_SHARE_FOR_RANKING",
    "PREMISE_MAGNITUDE_BAND",
    "PRIOR_WINDOW_END_SUFFIX",
    "PRIOR_WINDOW_START_SUFFIX",
    "WINDOW_END_SUFFIX",
    "WINDOW_START_SUFFIX",
    "CompareShape",
    "ConcentrationShape",
    "EvaluateFindingsService",
    "FindingsResult",
    "MagnitudeVerdict",
    "MovementShape",
    "PremiseCheck",
    "ScalarShape",
    "SelectionCensus",
    "TerminalCensoring",
    "TrendShape",
    "as_number",
    "bound_text",
    "claimed_rank",
    "find_primary_compare",
    "find_primary_concentration",
    "find_primary_movement",
    "find_scalar_shapes",
    "find_trend_shapes",
    "movement_forms",
    "premise_verdict_sentence",
    "probe_window_disclosure",
    "published_window_note",
    "row_noun",
    "terminal_bucket_censoring",
    "verify_premise",
]
