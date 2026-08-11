"""Turn orchestration: the §8.1 compile path, the §8.2 refinement path, and
the §8.3 zero-probe paths, over one explicit typed context.

Turn dispatch (after the session watermark check):

- **Typed first turn** — a request carrying a ``TypedInvestigationSpec``
  is a NEW_INVESTIGATION by construction and needs no parent: the spec
  states the metrics, dimensions, scope and window outright, so
  classification and interpretation are simply *known*, not guessed
  (zero model calls). Everything after the spec is built is the ordinary
  pipeline, §6.6 validation included. This is the anchor typed
  refinements always needed: a portfolio card's drill handle, or a chart
  click in a session with no prior answer, opens an investigation
  instead of returning "nothing to refine yet".
- **Typed gesture** — a request carrying refinement DTOs skips NL entirely
  and enters the refinement pipeline at the operator converter. Unlike
  the typed first turn, this one *edits* the session's latest
  investigation and therefore does require a parent.
- **NEW_INVESTIGATION** — classify → interpret → plan → §6.6 validation →
  cache-first execution → deterministic calculation → findings/referents.
- **DEFINITIONAL** — governed pack content with provenance; zero probes.
- **REFINEMENT** — resolve referents against the live registry → emit
  operators from the closed set → convert → ``apply_refinements`` (context
  conflicts surface *before* execution as clarification outcomes, never
  500s) → DrillInto targets pin ONE cohort at the session watermark →
  replan → plan diff vs the deterministically rebuilt parent plan →
  cache-first execution (unchanged probes never touch the warehouse) →
  auto-reconciliation against the parent totals on splits/drills
  (RECONCILIATION_FAILED is a surfaced warning + event, never silent,
  never fatal) → child Investigation + RefinementEdge.
- **PRESENTATION_ONLY / META / CONTEXT_CONTROL / kernel-only refinements**
  — answered from persisted frames, traces, and the context object with
  ZERO repository calls (spy-asserted per §18.1-14).

Watermark epochs (§7.1): every turn compares the session pin against the
newest completed load; staleness is surfaced (``watermark_stale``, a
warning event) and the analyst chooses — ``re_anchor=True`` starts a new
epoch, re-resolves relative windows against the new anchor, and records
the transition in the trace. Pinned continuation stays byte-stable.

Clarifications are successful outcomes: they cross this boundary as data
on the :class:`TurnOutcome`, never as exceptions.

**Reading order.** :mod:`~revi_investigation.application.submit_turn.service`
holds :class:`SubmitTurnService` — its constructor and :meth:`submit`, which
dispatches to exactly one turn path. Each phase is a base class in its own
module, and each calls only into the ones below it: ``recording`` →
``containment`` → ``guards`` → ``clarifying`` → ``core`` → ``refinement``.
The pure decisions those phases make live in modules of plain functions with
no engine state: ``types``, ``header``, ``census``, ``clarification``,
``presentation``, ``open_session``.
"""

from __future__ import annotations

from revi_investigation.application.submit_turn.census import (
    _same_findings as _same_findings,
)
from revi_investigation.application.submit_turn.census import (
    probe_families_empty_warning as probe_families_empty_warning,
)
from revi_investigation.application.submit_turn.clarification import (
    _ASKS_WHICH_MEASURE as _ASKS_WHICH_MEASURE,
)
from revi_investigation.application.submit_turn.clarification import (
    ASKS_WHETHER_TO_PIN as ASKS_WHETHER_TO_PIN,
)
from revi_investigation.application.submit_turn.clarification import (
    CLARIFICATION_SOLE_SURVIVOR_REASON as CLARIFICATION_SOLE_SURVIVOR_REASON,
)
from revi_investigation.application.submit_turn.clarification import (
    ENTITY_SUPERLATIVE as ENTITY_SUPERLATIVE,
)
from revi_investigation.application.submit_turn.clarification import (
    NO_OPTIONS_REASON as NO_OPTIONS_REASON,
)
from revi_investigation.application.submit_turn.clarification import (
    _answers_pending as _answers_pending,
)
from revi_investigation.application.submit_turn.clarification import (
    _bindings_from_trace as _bindings_from_trace,
)
from revi_investigation.application.submit_turn.clarification import (
    _drop_interrogative_options as _drop_interrogative_options,
)
from revi_investigation.application.submit_turn.clarification import (
    _no_options_card as _no_options_card,
)
from revi_investigation.application.submit_turn.clarification import (
    _state_the_survivor as _state_the_survivor,
)
from revi_investigation.application.submit_turn.clarification import (
    _with_binding as _with_binding,
)
from revi_investigation.application.submit_turn.clarification import (
    _with_chosen_values as _with_chosen_values,
)
from revi_investigation.application.submit_turn.clarification import (
    _with_resumed_context as _with_resumed_context,
)
from revi_investigation.application.submit_turn.clarification import (
    claim_referent_predicates as claim_referent_predicates,
)
from revi_investigation.application.submit_turn.clarification import (
    cuts_an_entity_axis as cuts_an_entity_axis,
)
from revi_investigation.application.submit_turn.clarification import (
    drop_refuted_options as drop_refuted_options,
)
from revi_investigation.application.submit_turn.clarification import (
    options_named as options_named,
)
from revi_investigation.application.submit_turn.containment import (
    _CONTAINMENT_TOLERANCE as _CONTAINMENT_TOLERANCE,
)
from revi_investigation.application.submit_turn.containment import (
    containment_reconciliation as containment_reconciliation,
)
from revi_investigation.application.submit_turn.containment import (
    measure_mismatch_reason as measure_mismatch_reason,
)
from revi_investigation.application.submit_turn.open_session import OpenSessionService
from revi_investigation.application.submit_turn.presentation import (
    PRESENTATION_PRODUCED_NOTHING_REASON as PRESENTATION_PRODUCED_NOTHING_REASON,
)
from revi_investigation.application.submit_turn.presentation import (
    _chart_sorts_for as _chart_sorts_for,
)
from revi_investigation.application.submit_turn.presentation import (
    _export_request_refusal as _export_request_refusal,
)
from revi_investigation.application.submit_turn.presentation import (
    _reordered as _reordered,
)
from revi_investigation.application.submit_turn.presentation import (
    _unapplied_presentation_request as _unapplied_presentation_request,
)
from revi_investigation.application.submit_turn.presentation import (
    presentation_ordering as presentation_ordering,
)
from revi_investigation.application.submit_turn.service import SubmitTurnService
from revi_investigation.application.submit_turn.types import (
    MAX_CONSECUTIVE_CLARIFICATIONS as MAX_CONSECUTIVE_CLARIFICATIONS,
)
from revi_investigation.application.submit_turn.types import (
    MetaAnswer,
    SubmitTurnRequest,
    TurnOutcome,
)
from revi_investigation.application.submit_turn.types import (
    _period_phrase as _period_phrase,
)

__all__ = [
    "MAX_CONSECUTIVE_CLARIFICATIONS",
    "NO_OPTIONS_REASON",
    "PRESENTATION_PRODUCED_NOTHING_REASON",
    "MetaAnswer",
    "OpenSessionService",
    "SubmitTurnRequest",
    "SubmitTurnService",
    "TurnOutcome",
    "claim_referent_predicates",
    "containment_reconciliation",
    "drop_refuted_options",
    "measure_mismatch_reason",
    "options_named",
    "presentation_ordering",
    "probe_families_empty_warning",
]
