"""Rounds wire shapes: pins, tiles, the brief, and lead lifecycle.

Rounds is the proactive surface. Revi walks it every data load and briefs
what changed. Three rules shape every model in this module, and each of
them is a defect somebody could ship without it:

**A pin is a WATCH, not a snapshot.** :class:`RoundsPinPayload` carries the
``TypedInvestigationSpec`` that produced the artifact, never the artifact.
Each load re-runs that spec at the new watermark through the identical
typed pipeline — zero model calls, plan-hash cached — so
:class:`RoundsTilePayload` is a *current* answer with a current grade, not
a value frozen the day somebody pinned it. A tile that showed a stale
number under a fresh date would be worse than no tile.

**A tile carries its caveats or it is not shipped.**
:class:`RoundsTileIntegrity` is the M22 integrity-line atom as a payload:
the answer-level grade, the count of things to know, the caveat CODES
behind that count, and the checks that were run. Six adversarial rounds
went into making an answer say ``≤ 45.5%`` instead of ``45.5%`` and
"provisional" instead of a settled-looking point; a tile that renders the
number and drops the marks undoes all of it on the one surface a person
looks at every morning without reading.

**The brief gates on governed materiality.** Alert fatigue is the death
mode of a daily surface: a brief that cries wolf twice is a brief nobody
opens again. Every entry passes a threshold that lives in the pack
(``packs/base-rcm/rounds.yaml``), never in engine code, and the thresholds
that were applied ride on the response (:class:`RoundsMaterialityPayload`)
so a reader can check the gate rather than trust it. "Nothing material
changed" is a first-class, proud outcome — :attr:`RoundsBriefResponse.status`
``nothing_material`` — with the counts that back the claim.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from revi_investigation_contracts.api import (
    FindingPayload,
    RoundsWatchMode,
    RoundsWatchModel,
    RoundsWatchUnit,
    TimeToImpactPayload,
    TypedInvestigationSpec,
    WarningPayload,
    WatchDeclarationPayload,
)
from revi_investigation_contracts.refinements import ClosedModel

#: Re-exported so a Rounds client imports one module. These four shapes
#: live in :mod:`revi_investigation_contracts.api` because a portfolio card
#: and a turn answer carry them too, and one definition beats two that must
#: agree.
__all__ = [
    "CreateRoundsPinRequest",
    "RoundsBriefEntry",
    "RoundsBriefResponse",
    "RoundsDeltaPayload",
    "RoundsFatigueAdvisory",
    "RoundsImmaterialSummary",
    "RoundsLeadPatchRequest",
    "RoundsLeadPayload",
    "RoundsMaterialityPayload",
    "RoundsPinListResponse",
    "RoundsPinPayload",
    "RoundsPresentation",
    "RoundsProvenancePayload",
    "RoundsResponse",
    "RoundsTileIntegrity",
    "RoundsTilePayload",
    "RoundsWatchMode",
    "RoundsWatchModel",
    "RoundsWatchUnit",
    "RoundsWindowMode",
    "TimeToImpactPayload",
    "WatchDeclarationPayload",
]

# ---------------------------------------------------------------------------
# pins

#: How a pinned spec is meant to be rendered. The spec decides WHAT is
#: measured; this decides what the tile looks like, and it is the analyst's
#: choice at pin time rather than a re-inference per load.
RoundsPresentation = Literal["chart", "finding", "worklist_slice", "scalar"]

#: What "re-run this spec at the new load" means for this pin's window.
#: Published on every pin because the two answer different questions — see
#: :class:`~revi_investigation.application.ports.RoundsPin`.
RoundsWindowMode = Literal["relative", "absolute", "anchored"]


class CreateRoundsPinRequest(ClosedModel):
    """Add a watch to Rounds.

    Three ways in, all producing the same row — a pin created from an
    artifact and a watch created by intent differ only in provenance:

    * ``investigation_id`` (+ optional ``referent``) — watch what is on
      screen. The platform resolves that investigation's STORED spec
      server-side and pins it. No text is re-interpreted and no model is
      called: the spec already exists, and re-deriving it from the
      question would be a second, worse answer to a question already
      answered.
    * ``spec`` — a caller that already holds a typed spec (a portfolio
      card's ``drill_spec``, a saved view) watches it directly.
    * an utterance — "watch Silverline's denial rate" — which is not this
      request at all: it is an ordinary turn that compiles through
      interpretation and registers the watch from the answer it produced.
      See ``revi_api.watch_intent``.

    Exactly one of ``investigation_id`` / ``spec``. A body carrying both is
    refused rather than resolved, because either resolution order would be
    a guess about which the caller meant.
    """

    investigation_id: str | None = None
    #: The artifact within that investigation — a finding referent
    #: (``F1``) or a chart id. Recorded as provenance and used to title
    #: the tile; it never changes what is measured, because the spec is
    #: the investigation's own.
    referent: str | None = None
    spec: TypedInvestigationSpec | None = None
    presentation: RoundsPresentation = "finding"
    #: The tile's title. Empty takes the pinned finding's own title, or the
    #: investigation's question — never a generated label.
    label: str = ""
    watch: RoundsWatchModel | None = None


class RoundsPinPayload(ClosedModel):
    """One pinned spec, as stored.

    ``spec`` is published, not hidden: a watch whose definition a reader
    cannot see is a watch they cannot check, and this is the object that
    decides what the tile measures every morning.
    """

    pin_id: str
    tenant: str
    label: str
    presentation: RoundsPresentation
    spec: TypedInvestigationSpec
    window_mode: RoundsWindowMode
    #: What re-running THIS pin's window means, in one sentence — a moving
    #: period (a real movement) or fixed dates (late-arriving data).
    window_note: str = ""
    #: ``artifact`` | ``intent`` | ``spec`` — how this watch came to exist.
    #: Provenance only: all three evaluate identically.
    created_from_kind: Literal["artifact", "intent", "spec"] = "spec"
    created_from_investigation_id: str | None = None
    created_from_referent: str | None = None
    #: The stored spec in the reader's own nouns — "Denial rate, broken
    #: down by payer, filtered to Pinnacle Health Plan — last full month
    #: (service basis)". The panel headed "What this watch measures" is the
    #: one control that lets somebody catch a watch that is measuring the
    #: wrong cell, and it had every part of this on the wire and rendered
    #: none of it (round-7 FN-18).
    spec_summary: str = ""
    #: What could not be carried onto this watch, and anything the platform
    #: did to the spec at creation — the cell it narrowed to, a duplicate it
    #: returned instead of creating. Published as a list rather than folded
    #: into :attr:`window_note`, because a reader needs to tell "this is how
    #: the window works" from "here is what happened to your request".
    notes: list[str] = Field(default_factory=list)
    #: True when this create returned a watch that already existed on the
    #: same tenant with the same spec, rather than making a second one. A
    #: duplicate re-evaluates every load and can brief one movement N times,
    #: which is the alert fatigue the pack spends 300 lines preventing.
    already_existed: bool = False
    watch: RoundsWatchModel | None = None
    #: What this watch read at the load it was created on — the reference
    #: point a baseline delta is measured from. ``None`` until the first
    #: load evaluates it: a watch created between loads has no baseline
    #: yet, and taking the previous load's value would attribute a movement
    #: to a period nobody was watching.
    baseline_watermark_id: str | None = None
    baseline_value: float | None = None
    baseline_value_text: str = ""
    baseline_unit: str | None = None
    created_at: datetime
    archived_at: datetime | None = None
    #: Tenant-scoped, not user-scoped, in v1 — stated on the wire rather
    #: than discovered when a second analyst finds pins they did not make.
    #: See the AUTH DEBT note in the ports module.
    scope: Literal["tenant"] = "tenant"


class RoundsPinListResponse(ClosedModel):
    tenant: str = ""
    pins: list[RoundsPinPayload] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# tiles


class RoundsTileIntegrity(ClosedModel):
    """The M22 integrity line, as a payload (the tile's honesty contract).

    Every field here is a count of something the tile also carries, so a
    renderer states facts rather than inventing a score:

        ● Verified against your data · 3 things to know, 2 change how a
          number here should be read · 12 checks

    ``grade`` is the answer-level evidence grade, ``things_to_know`` is
    exactly ``len(caveat_codes_expanded)`` — the caveats the tile publishes
    — and ``checks`` is the probes the evaluation ran. A tile that dropped
    any of them would present a bounded, provisional, proxy-graded figure
    with the same weight as a measured one.
    """

    #: ``direct`` | ``derived`` | ``proxy`` — the weakest grade any finding
    #: on this tile carries, because an answer is only as certified as its
    #: least certified part.
    grade: str = "direct"
    #: How many caveats this tile publishes. The count the line renders.
    things_to_know: int = 0
    #: How many of them are ``caution`` — the ones that change how a number
    #: here should be READ, as opposed to being worth knowing.
    things_to_know_caution: int = 0
    #: The stable warning codes behind the count, deduplicated and ordered
    #: as published. A client branches on these; it never matches prose.
    caveat_codes: list[str] = Field(default_factory=list)
    #: Probes this evaluation executed (``0`` is a real answer: a fully
    #: cached re-run reads no warehouse).
    checks: int = 0
    #: True when the headline value is an upper BOUND rather than a
    #: measurement — a suppressed numerator replaced by the largest value
    #: it could have held. A renderer must not draw it like a measurement.
    is_bound: bool = False
    #: True when the headline value is not yet settled (a calendar-partial
    #: or still-adjudicating terminal bucket).
    provisional: bool = False


class RoundsDeltaPayload(ClosedModel):
    """This tile's movement since the prior load, in the metric's own unit.

    Unit honesty is the whole point of the shape. A rate's movement is
    stated in POINTS (``1.3 points``), never as a percentage that a reader
    cannot tell from a relative change; money keeps dollars; counts stay
    counts. ``delta_text`` is the rendered form and ``delta`` the raw
    number, so a client can format its own without re-deriving the unit.

    ``comparable`` is false — with a reason — whenever the two loads are
    not two measurements of one thing: a first evaluation, a pin whose
    metric or unit changed, a prior value that was suppressed. A percentage
    is withheld in that case rather than computed from mismatched sides.
    """

    prior_watermark_id: str = ""
    prior_value: float | None = None
    prior_value_text: str = ""
    value: float | None = None
    value_text: str = ""
    unit: str | None = None
    delta: float | None = None
    #: Unsigned magnitude in the contract's unit — "1.3 points", "$4,201.00".
    delta_text: str = ""
    #: Signed fraction of the prior value. ``None`` for a rate (points are
    #: the honest form) and whenever the prior value is zero or absent.
    delta_fraction: float | None = None
    direction: Literal["up", "down", "flat", "unknown"] = "unknown"
    comparable: bool = True
    not_comparable_reason: str | None = None
    #: What this delta is measured FROM: the prior load, or the watch's own
    #: creation-load baseline. Both are published when they differ
    #: materially — see :class:`RoundsWatchModel` for why.
    reference: Literal["prior_load", "baseline"] = "prior_load"
    #: WHICH CELL each side measured, in the reader's words. A watch over a
    #: ranked breakdown headlines whatever ranks first at that load, so two
    #: loads can be two measurements of two different payers — and a
    #: percentage between them looks exactly like a movement. When these
    #: disagree the delta is not comparable and no delta is published
    #: (round-7 FN-2: a brief reported a payer's denial rate "up 3.6 points"
    #: when that payer had FALLEN 6.6, and explained the phantom rise as
    #: adjudication run-out).
    subject_label: str = ""
    prior_subject_label: str = ""
    #: True when BOTH loads resolved to the same dates. The number is right
    #: either way; what changes is what it means. A same-window change is
    #: late-arriving data settling — adjudication run-out, back-dated
    #: charges — and reporting it as a movement in the business would be the
    #: claims run-out plotted as deterioration, which is a defect this
    #: platform has already fixed once on the trend path.
    #:
    #: Always false when :attr:`comparable` is false: this qualifies a
    #: movement, and a payload with no movement has nothing for it to
    #: qualify. It was true beside a withheld delta once, and a run-out
    #: sentence rode out on a rank flip that never moved anything.
    same_window: bool = False
    #: Did this movement clear the materiality gate?
    material: bool = False
    #: WHOSE threshold decided. ``governed`` is the pack's; ``watch`` is
    #: the analyst's own, and an entry briefed on a threshold looser than
    #: the pack's says so here rather than looking governed.
    threshold_source: Literal["governed", "watch"] = "governed"
    #: True when the analyst's threshold briefed a movement the GOVERNED
    #: gate calls normal variation. Counted across loads to decide the
    #: fatigue advisory — a setting that fires every morning is a setting
    #: worth revisiting, and the surface should say so once rather than
    #: keep quietly interrupting.
    below_governed_gate: bool = False
    #: Which rule decided, and what it compared against — so the gate is
    #: checkable rather than trusted.
    materiality_rule: str = ""
    materiality_note: str = ""


class RoundsTilePayload(ClosedModel):
    """One pin, evaluated at one load.

    ``status`` is honest about the three things that can happen when a
    stored spec meets a new load: it answered (``ok``), the platform
    refused it (``unavailable`` — a catalog or pack change can make
    yesterday's spec unanswerable today, and a tile that went blank
    without saying so would look like a zero), or it came back asking a
    question (``clarification``, which a typed spec should never do and
    which is reported rather than swallowed if it does).
    """

    pin_id: str
    label: str
    presentation: RoundsPresentation
    status: Literal["ok", "unavailable", "clarification"] = "ok"
    watermark_id: str = ""
    newest_data_date: date | None = None
    evaluated_at: datetime | None = None
    #: The dates this evaluation actually measured, after the watch's window
    #: resolved against THIS load. Published because they decide how a delta
    #: should be read: two loads that resolved to the same dates are two
    #: measurements of one period, so the change between them is
    #: late-arriving data (adjudication run-out, back-dated charges) rather
    #: than a movement in the business. A relative window usually moves and
    #: sometimes does not — two nightly loads inside one month resolve to
    #: the same month — so the answer cannot be inferred from
    #: :attr:`RoundsPinPayload.window_mode` alone.
    window_start: date | None = None
    window_end: date | None = None
    #: The investigation this evaluation created — the permalink a tap on
    #: the tile opens. Every tile IS a real investigation with a real
    #: trace, not a number computed off to the side.
    investigation_id: str | None = None
    #: The headline finding: its referent, title and statement verbatim
    #: from the finding the evaluation published.
    headline_referent: str | None = None
    headline_title: str = ""
    headline_statement: str = ""
    #: WHICH CELL this tile's number is about, resolved to dimension
    #: members (``{"payer": "Pinnacle Health Plan"}``) rather than read off
    #: a display title. Empty for a watch with no dimension at all.
    #:
    #: Load-bearing twice over. A tile whose LABEL names one payer and
    #: whose VALUE is another payer's is the defect that gated round 7, and
    #: this is the field that makes it checkable rather than eyeballed; and
    #: a load-over-load delta between two different subjects is not a
    #: movement at all, which is what :attr:`RoundsDeltaPayload.subject_label`
    #: guards on.
    headline_subject: dict[str, str] = Field(default_factory=dict)
    #: The same cell as one human phrase ("Pinnacle Health Plan"), rendered
    #: with the pack's own code titles where a dimension carries codes.
    headline_subject_label: str = ""
    #: The headline number, rendered in its contract unit (with the ``≤``
    #: a bounded cell earns), and raw for a client that formats its own.
    value_text: str = ""
    value: float | None = None
    unit: str | None = None
    metric_id: str | None = None
    #: The honesty marks. Never optional — see :class:`RoundsTileIntegrity`.
    integrity: RoundsTileIntegrity = Field(default_factory=RoundsTileIntegrity)
    #: The platform's own sentences, verbatim, and their classified twins.
    warnings: list[str] = Field(default_factory=list)
    warnings_v2: list[WarningPayload] = Field(default_factory=list)
    #: Every finding the evaluation published, so a chart or worklist-slice
    #: presentation has its rows without a second fetch.
    findings: list[FindingPayload] = Field(default_factory=list)
    #: Movement since the PRIOR load. Always present once a prior load
    #: exists, material or not — the tile shows its delta, and the brief
    #: decides separately whether to interrupt anybody about it.
    delta: RoundsDeltaPayload | None = None
    #: Movement since the watch's CREATION-LOAD baseline, published when a
    #: baseline exists and it says something the prior-load delta does not.
    #: See :class:`RoundsWatchModel` for the semantics.
    baseline_delta: RoundsDeltaPayload | None = None
    #: Why the tile has no value, when it has none — in the platform's own
    #: error vocabulary (``GRAIN_INCOMPATIBLE: ...``), never silence.
    unavailable_reason: str | None = None


class RoundsResponse(ClosedModel):
    """The Rounds surface at one load: every active pin, evaluated."""

    tenant: str = ""
    watermark_id: str = ""
    newest_data_date: date | None = None
    prior_watermark_id: str | None = None
    tiles: list[RoundsTilePayload] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    warnings_v2: list[WarningPayload] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# leads


class RoundsLeadPayload(ClosedModel):
    """One lead's lifecycle state.

    ``resolved_confirmed`` and ``regressed`` are verdicts the PLATFORM
    reaches from data across loads; a human can claim resolution and
    cannot assert it. That asymmetry is the feature: "mark as resolved" on
    every other tool in this category is a checkbox, and a checkbox is an
    opinion.
    """

    anomaly_id: str
    tenant: str = ""
    status: Literal[
        "open", "acknowledged", "working", "resolved_claimed", "resolved_confirmed", "regressed"
    ] = "open"
    note: str = ""
    updated_at: datetime | None = None
    claimed_at_watermark: str | None = None
    #: The platform's own re-derived exposure at the claim load — the
    #: baseline every later verification is measured against — and how it
    #: was obtained. ``None`` with a stated basis when the lead's drill
    #: cannot be re-derived, in which case the claim is never auto-confirmed.
    baseline_cents: int | None = None
    baseline_basis: str = ""
    #: Loads that have verified the claim so far, in order.
    confirming_watermarks: list[str] = Field(default_factory=list)
    #: How many consecutive verifying loads the governed rule requires.
    confirmations_required: int = 0
    #: What the last verification measured, in the platform's own words —
    #: including "could not verify", which is a result and not a silence.
    verification_note: str = ""
    #: Every transition: ``{at, watermark_id, from, to, by, note}``.
    history: list[dict[str, Any]] = Field(default_factory=list)


class RoundsLeadPatchRequest(ClosedModel):
    """Move one lead along its lifecycle.

    Only the four human-settable statuses are accepted. Asking for
    ``resolved_confirmed`` is refused with the reason: confirmation is a
    measurement across two loads, and a lead that could be confirmed by
    assertion would make the whole verification path decorative.
    """

    status: Literal["open", "acknowledged", "working", "resolved_claimed"]
    note: str = ""


# ---------------------------------------------------------------------------
# the brief


class RoundsProvenancePayload(ClosedModel):
    """Where one brief entry came from. On every entry, no exceptions.

    A brief is the surface furthest from the evidence — somebody reads it
    over coffee — so each line has to be able to say which system asserted
    it, at which load, and by what method.
    """

    #: ``detection_feed`` (an external detector's assertion, read as-of a
    #: watermark) or ``pinned_spec`` (this platform's own governed
    #: re-run of a stored spec).
    source: Literal["detection_feed", "pinned_spec"]
    watermark_id: str = ""
    prior_watermark_id: str | None = None
    evaluated_at: datetime | None = None
    #: The versioned formula behind a ranked figure, when one ranked it.
    formula_version: str | None = None
    #: How this entry was decided, in one sentence.
    method: str = ""


class RoundsBriefEntry(ClosedModel):
    """One line of the brief.

    ``kind`` is the closed set of things a load can change, and each one is
    a different reading:

    * ``new_lead`` — the detection feed fired something that was not there
      at the prior load;
    * ``pin_movement`` — a watched spec moved materially;
    * ``self_resolved`` — a lead the feed no longer reports, nobody
      claimed. Published because a problem that fixed itself is
      information, and because the alternative is an analyst chasing a
      card that is gone;
    * ``resolution_confirmed`` / ``resolution_regressed`` — the platform's
      verdict on a claimed resolution;
    * ``rank_flip`` — the cell a ranked watch headlines is not the cell it
      headlined last load. This is NOT a movement and never carries a
      delta: it is the fact that "your worst payer" is now a different
      payer, which is the headline the movement it replaced was pretending
      to be (round-7 FN-2).
    """

    kind: Literal[
        "new_lead",
        "pin_movement",
        "self_resolved",
        "resolution_confirmed",
        "resolution_regressed",
        "rank_flip",
    ]
    title: str
    #: The line itself, composed from the payload — never by a model.
    statement: str
    anomaly_id: str | None = None
    pin_id: str | None = None
    #: The permalink: the investigation behind a moved tile, when there is
    #: one. A brief entry a reader cannot open is a notification.
    investigation_id: str | None = None
    category: str | None = None
    lane: str | None = None
    impact_cents: int | None = None
    time_to_impact: TimeToImpactPayload | None = None
    delta: RoundsDeltaPayload | None = None
    #: The same movement measured from the watch's creation-load baseline,
    #: present only when it differs materially from :attr:`delta`. Two true
    #: stories, and the surface tells both rather than picking one.
    baseline_delta: RoundsDeltaPayload | None = None
    lead_status: str | None = None
    #: The honesty marks travel onto the brief with the number, for the
    #: same reason they travel onto a tile.
    integrity: RoundsTileIntegrity | None = None
    provenance: RoundsProvenancePayload


class RoundsMaterialityPayload(ClosedModel):
    """The governed gate that was actually applied, published.

    Thresholds live in ``packs/base-rcm/rounds.yaml`` — pack content, with
    an authoring rationale beside every number — never in engine code. They
    ride on the response so a reader can check the gate that produced a
    brief (or the absence of one) rather than take it on faith, and so two
    deployments running different packs can be told apart from the payload.
    """

    #: Threshold per unit KIND: points for a rate, percent-plus-floor for
    #: money, and so on. ``{unit_kind: {rule: value}}``.
    unit_kinds: dict[str, dict[str, float]] = Field(default_factory=dict)
    #: The floor a NEW lead must clear to be briefed, and the lanes that
    #: bypass it (compliance work is briefed regardless of size).
    new_lead_min_impact_cents: int = 0
    always_material_lanes: list[str] = Field(default_factory=list)
    max_entries: int = 0
    #: The order the cap drops entries in, worst-to-lose first. The cap used
    #: to bite in INSERTION order, which put verified regressions and
    #: confirmations — the verdicts about the team's own work — at the front
    #: of the queue to be deleted (round-7 FN-11).
    priority_order: list[str] = Field(default_factory=list)
    #: Kinds the overall cap may never drop. A regression an analyst does
    #: not see is an analyst who believes something is fixed.
    never_capped: list[str] = Field(default_factory=list)
    #: Content hash of the governed file, so a brief can be tied to the
    #: exact thresholds that produced it.
    content_hash: str = ""
    source: str = ""


class RoundsImmaterialSummary(ClosedModel):
    """Everything the gate held back, counted rather than hidden.

    The line a brief owes its reader: "4 watched items moved immaterially".
    Suppressing a movement silently and suppressing it visibly are
    different products — the first is a filter the analyst cannot audit.
    """

    pin_movements: int = 0
    new_leads: int = 0
    self_resolved: int = 0
    entries_withheld_by_cap: int = 0
    #: Watches that were evaluated and have nothing to compare against at
    #: the load this brief diffs from — a first reading, or a watch created
    #: after that load. Counted rather than dropped so the census CLOSES:
    #: ``pins_evaluated == briefed + pin_movements + not_yet_comparable +
    #: unavailable``. A total that does not reconcile to its parts is the
    #: one thing a surface whose whole claim is "withheld visibly, never
    #: silently" cannot publish (round-7 FN-12).
    not_yet_comparable: int = 0
    #: Watches whose stored spec could not be answered at this load. They
    #: are neither material nor immaterial; they are unmeasured, and the
    #: tile says why.
    unavailable: int = 0
    #: What the cap dropped, BY KIND. "12 further entries" does not tell a
    #: reader whether a confirmed fix was among them.
    entries_withheld_by_kind: dict[str, int] = Field(default_factory=dict)
    note: str = ""


class RoundsFatigueAdvisory(ClosedModel):
    """The brief noticing that somebody's own thresholds are too loose.

    A watch may set a threshold looser than the pack's governed gate, which
    is a real need and a real risk. When those settings brief movements the
    governed gate calls normal variation, on several consecutive loads, the
    surface says so — ONCE per load, in governed wording, with the counts
    that back it:

        3 of your watches moved within normal variation for the third load
        running — consider tightening them.

    Never more than once per load and never for a single load's worth of
    noise: an advisory that nagged would be the fatigue it is warning
    about. ``active`` false means the condition has not been met, and the
    counts are still published so the state is readable rather than
    inferred from an absent field.
    """

    active: bool = False
    #: Watches whose own threshold briefed a movement the governed gate
    #: calls normal variation, at THIS load.
    watches_below_governed_gate: int = 0
    #: How many consecutive loads that has now been true for.
    consecutive_loads: int = 0
    #: How many the governed content requires before saying anything.
    loads_required: int = 0
    #: The sentence itself, composed from the counts. Empty when inactive.
    message: str = ""


class RoundsBriefResponse(ClosedModel):
    """What changed at this load, gated, capped and provenanced.

    ``status`` has three values and the middle one is the point:

    * ``first_load`` — nothing to diff against yet, said plainly;
    * ``nothing_material`` — the loud, proud outcome. Revi walked the
      Rounds, measured everything, and there is nothing you need to do.
      That is the answer, with the counts that back it, and it is not an
      empty page;
    * ``material_changes`` — entries follow.
    """

    tenant: str = ""
    status: Literal["first_load", "nothing_material", "material_changes"] = "nothing_material"
    watermark_id: str = ""
    newest_data_date: date | None = None
    prior_watermark_id: str | None = None
    #: The data date of the load this brief diffs AGAINST. Published because
    #: the brief speaks in dates and not in warehouse ids: "since the Aug 1
    #: load" is a sentence a VP reads, and ``wm_002`` is one they forward to
    #: somebody else to explain (round-7 FN-8). The ids stay on
    #: :attr:`prior_watermark_id` and in provenance, where they belong.
    prior_newest_data_date: date | None = None
    #: One sentence, composed from the counts below. The thing a person
    #: reads before anything else.
    headline: str = ""
    entries: list[RoundsBriefEntry] = Field(default_factory=list)
    #: How many entries CLEARED the gate, before the cap. ``entries`` may
    #: be shorter; the difference is in ``immaterial.entries_withheld_by_cap``.
    entries_total: int = 0
    immaterial: RoundsImmaterialSummary = Field(default_factory=RoundsImmaterialSummary)
    #: The brief telling somebody their own thresholds are too loose —
    #: once, when it has been true for long enough to be a pattern.
    fatigue: RoundsFatigueAdvisory = Field(default_factory=RoundsFatigueAdvisory)
    materiality: RoundsMaterialityPayload = Field(default_factory=RoundsMaterialityPayload)
    #: Pins evaluated and leads verified at this load — the work behind the
    #: brief, so "nothing material changed" is visibly a measurement.
    pins_evaluated: int = 0
    leads_verified: int = 0
    generated_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    warnings_v2: list[WarningPayload] = Field(default_factory=list)
