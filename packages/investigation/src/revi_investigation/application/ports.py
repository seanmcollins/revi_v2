"""Application-layer ports (Protocols) for the investigation capability.

Infrastructure adapters implement these; application services depend only
on the Protocols. In-memory fakes for every port live in ``revi_testing``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from revi_investigation.domain.records import (
    Finding,
    Investigation,
    RefinementEdge,
    Session,
    SessionLineage,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import ReferentId
from revi_kernel.watermark import DataWatermark

# --- language model ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LlmCallPolicy:
    """Per-turn overrides an adapter must apply to one call.

    Both fields are bounded before they get here (a deployment allowlist
    for the model, an admin ceiling for the money), so an adapter applies
    them rather than re-judging them. ``None`` on either means "the
    deployment's own default", which is what every call did before session
    settings existed.
    """

    #: Model id for this call; ``None`` keeps the adapter's pin.
    model: str | None = None
    #: Ceiling for THIS call, derived from what is left of the turn's
    #: budget. An adapter never widens its own cap with it.
    max_cost_usd: Decimal | None = None


DEFAULT_LLM_CALL_POLICY = LlmCallPolicy()


@dataclass(frozen=True, slots=True)
class StructuredLlmRequest:
    """A stateless, schema-constrained call. The prompt is already
    payload-guarded and PHI-masked by the caller; the schema is a JSON
    Schema derived from a closed Pydantic response model."""

    template_id: str
    template_version: str
    rendered_prompt: str
    schema: Mapping[str, Any]
    system_prompt: str | None = None
    #: Session-scoped model/budget overrides for this call (§7.1).
    policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY


@dataclass(frozen=True, slots=True)
class TextLlmRequest:
    """Streaming free-text call — narrative composition only."""

    template_id: str
    template_version: str
    rendered_prompt: str
    system_prompt: str | None = None
    #: Session-scoped model/budget overrides for this call (§7.1).
    policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY
    #: Called once with this call's usage when the stream completes.
    #:
    #: A streamed call cannot return its usage — the generator yields text —
    #: so the port has always exposed :meth:`LanguageModelPort.last_usage`
    #: instead. That is a *process-wide* slot: two turns narrating at once
    #: overwrite each other, and a caller reading it back can attribute
    #: another session's tokens to this one. The narrative was therefore
    #: left out of the turn envelope entirely, which published a turn cost
    #: missing its single largest generation. This sink is per request, so
    #: the usage lands on the turn that spent it and nowhere else.
    usage_sink: Callable[[LlmUsage], None] | None = None


@dataclass(frozen=True, slots=True)
class LlmUsage:
    model: str
    cost_usd: Decimal
    #: **Every** prompt token the call read, cached or not — the sum of the
    #: uncached remainder, the tokens written to the prompt cache, and the
    #: tokens served from it.
    #:
    #: The provider reports those three separately, and its ``input_tokens``
    #: is only the *uncached remainder*. Copying that field straight across
    #: published turns reading ``input_tokens: 4`` beside
    #: ``output_tokens: 953`` — a governed pipeline that sends a multi-
    #: thousand-token vocabulary in every prompt, reporting four. The split
    #: is not lost: it rides on the two fields below, so a reader can still
    #: see what was cached and what it cost.
    input_tokens: int
    output_tokens: int
    #: Turns the *model* burned failing to satisfy the output schema. A model
    #: problem: never retried by the adapter, only counted.
    schema_retries: int
    duration_ms: int
    #: Transport attempts the *adapter* made, including the first. 1 is the
    #: healthy case; >1 means a transient provider failure was retried and
    #: recovered. Kept apart from ``schema_retries`` because the two have
    #: different causes and different fixes, and averaging them would hide
    #: a degrading provider behind a well-behaved model (or the reverse).
    attempts: int = 1
    #: Prompt tokens served from the provider's cache (billed at a fraction
    #: of the base rate). Included in :attr:`input_tokens`.
    cache_read_tokens: int = 0
    #: Prompt tokens written to the provider's cache on this call (billed at
    #: a premium). Included in :attr:`input_tokens`.
    cache_creation_tokens: int = 0


class LlmFailureKind(StrEnum):
    """Why a structured call produced no usable output.

    The distinction call sites need is *what the analyst can do about it*.
    ``DECLINED`` and ``OFF_SCRIPT`` mean the model was asked and had no
    mapping to give — rephrasing is the recovery. ``SCHEMA`` means an
    answer never arrived in a readable shape; the question may have been
    perfectly good and simply asking again can work. Until this existed the
    port collapsed both into a bare ``output=None``, so a plumbing failure
    told the analyst to reword a question that was never the problem.
    """

    #: The model ran to completion and delivered nothing to parse.
    DECLINED = "declined"
    #: What came back did not satisfy the response schema — including the
    #: provider exhausting its own structured-output retries.
    SCHEMA = "schema"
    #: The scripted demo model has no entry for this call. Not a model
    #: judgement at all: the script simply stops there.
    OFF_SCRIPT = "off_script"


def retry_may_help(failure: LlmFailureKind | None) -> bool:
    """Is repeating the identical call a plausible recovery?

    True only for ``SCHEMA``: a well-formed question whose answer was mangled
    on the way back can come back clean. A model that declined, or a script
    with no entry, will decline identically forever — and an adapter that did
    not say which it was gets the conservative reading, because telling an
    analyst "try again" on a question the model will never answer is the more
    expensive of the two mistakes.
    """
    return failure is LlmFailureKind.SCHEMA


def failure_note(failure: LlmFailureKind | None) -> str:
    """Trace suffix naming why a structured call came back empty-handed.

    Lives beside the enum so every clarification reason spells the kind the
    same way, and so a trace query can grep one token.
    """
    return f" (llm_failure={failure.value if failure is not None else 'unspecified'})"


@dataclass(frozen=True, slots=True)
class StructuredLlmResult:
    output: Mapping[str, Any] | None  # None = no usable structured output
    usage: LlmUsage
    #: Why ``output`` is None. ``None`` alongside a ``None`` output means the
    #: adapter did not say; call sites then assume the conservative reading
    #: (the model declined) rather than blaming infrastructure they cannot see.
    failure: LlmFailureKind | None = None

    def __post_init__(self) -> None:
        if self.output is not None and self.failure is not None:
            raise ValueError("a structured result cannot carry both an output and a failure")


class LanguageModelPort(Protocol):
    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult: ...

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]: ...

    async def last_usage(self) -> LlmUsage | None:
        """Usage of the most recent stream_text call (structured calls carry
        usage inline)."""
        ...


# --- persistence ------------------------------------------------------------


#: Title for a session that exists but has answered no turn yet — a
#: session is created the moment a client connects, so the list must be
#: able to name one that has nothing in it. Spelled once here because both
#: store adapters derive it and a client compares against it.
EMPTY_SESSION_TITLE = "New session"


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """One row of a tenant's session list (design §7.1).

    ``title`` is the session's FIRST question, verbatim — the session
    record itself has no name, and inventing one would be a label the
    analyst never wrote. A session with no turns yet gets
    :data:`EMPTY_SESSION_TITLE`.

    ``last_activity`` is the newest investigation's ``created_at``, falling
    back to the session's own ``created_at`` when nothing has been asked.
    It is derived rather than stored: a column would be a second copy of a
    fact the investigations already carry, and the two would drift the
    first time a turn was written without touching the session row.
    """

    session_id: str
    title: str
    created_at: datetime
    last_activity: datetime
    turn_count: int


@dataclass(frozen=True, slots=True)
class SessionPage:
    """One page of :meth:`SessionStore.list_for_tenant`.

    ``total`` counts every session the tenant owns, not just the page, so a
    client can say "showing 50 of 214" instead of implying the list is
    complete when it is truncated.
    """

    sessions: tuple[SessionSummary, ...]
    total: int


class SessionStore(Protocol):
    async def get(self, session_id: str) -> Session | None: ...

    async def save(self, session: Session) -> None: ...

    async def list_for_tenant(self, tenant: str, *, limit: int) -> SessionPage:
        """The tenant's sessions, newest activity first.

        Tenant-scoped by signature, not by a filter a caller may forget:
        there is no "list all sessions" on this port, because the only
        caller is an authenticated request and the only correct answer is
        the caller's own sessions.
        """
        ...


@dataclass(frozen=True, slots=True)
class RegisteredReferent:
    """What a referent id stands for, resolvable across turns (design §7.6)."""

    referent: ReferentId
    session_id: str
    investigation_id: str
    label: str
    cohort_definition: CohortDefinition | None = None  # drillable referents
    cohort: CohortRef | None = None  # pinned when first drilled
    finding: Finding | None = None
    dimension_value: tuple[str, str] | None = None  # (dimension id, value)


class ReferentRegistryStore(Protocol):
    async def register(self, entries: tuple[RegisteredReferent, ...]) -> None: ...

    async def resolve(self, session_id: str, referent: ReferentId) -> RegisteredReferent | None: ...

    async def update(self, entry: RegisteredReferent) -> None: ...

    async def list_for_session(self, session_id: str) -> tuple[RegisteredReferent, ...]: ...


class InvestigationStore(Protocol):
    async def save(self, investigation: Investigation, edge: RefinementEdge | None) -> None: ...

    async def get(self, investigation_id: str) -> Investigation | None: ...

    async def lineage(self, session_id: str) -> SessionLineage | None: ...


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """One turn's full observability record (design §14). ``payload`` is a
    serializable mapping assembled by the engine; typed fields cover what
    queries need."""

    trace_id: str
    session_id: str
    investigation_id: str
    turn_id: str
    created_at: datetime
    payload: Mapping[str, Any]


class TraceStore(Protocol):
    async def save(self, record: TraceRecord) -> None: ...

    async def get(self, trace_id: str) -> TraceRecord | None: ...

    async def for_investigation(self, investigation_id: str) -> tuple[TraceRecord, ...]: ...


class FrameStore(Protocol):
    """Persisted evidence frames, addressed by trace-scoped keys."""

    async def save(self, key: str, frame: EvidenceFrame) -> None: ...

    async def get(self, key: str) -> EvidenceFrame | None: ...


class CohortStore(Protocol):
    """Cohort *metadata* (definitions, origins, TTLs). The entity-id sets
    live in the analytical repository's cohort store."""

    async def save(self, cohort: CohortRef, *, tenant: str, session_id: str) -> None: ...

    async def get(self, cohort_id: str) -> CohortRef | None: ...

    async def expired(self, now: datetime) -> tuple[CohortRef, ...]: ...


class EvidenceCache(Protocol):
    """Keyed on (probe hash, watermark, pack snapshot) — design §7.9."""

    async def get(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str
    ) -> EvidenceFrame | None: ...

    async def put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None: ...


# --- turn progress events ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """A typed progress event, bridged to SSE by the API layer.

    ``kind`` ∈ {stage, context_header, finding, chart_spec, narrative_delta,
    clarification, warning, turn_complete, error}; ``payload`` is a small
    serializable mapping shaped per kind.
    """

    kind: str
    turn_id: str
    payload: Mapping[str, Any]


class TurnEventBus(Protocol):
    async def publish(self, event: TurnEvent) -> None: ...


# --- detected anomalies (the portfolio surface) -----------------------------


@dataclass(frozen=True, slots=True)
class AnomalyRecord:
    """One detected anomaly as persisted at a warehouse watermark.

    Mirrors the warehouse ``detected_anomalies`` shape: an external
    detection system writes these; the platform only reads and ranks them.
    ``dimensions`` is (dimension id, value) pairs — together with the
    window they form the drillable handle an anomaly card carries, so the
    UI can start an ordinary investigation turn from it.
    """

    anomaly_id: str
    detected_at: datetime
    category: str
    title: str
    description: str
    metric_id: str
    dimensions: tuple[tuple[str, str], ...]
    window_start: date
    window_end: date
    impact_cents: int
    severity: str
    confidence: str
    status: str
    evidence: Mapping[str, Any]


class AnomalySource(Protocol):
    """Read-only access to detected anomalies as-of a watermark. A
    prebuilt external detection system implements this port; locally the
    DuckDB connector serves the generator's planted scenarios."""

    async def list_anomalies(self, watermark: DataWatermark) -> tuple[AnomalyRecord, ...]: ...


# --- Rounds: the proactive surface ------------------------------------------
#
# A pin is a WATCH, never a snapshot: what persists is the TYPED SPEC, and
# every load re-runs it at the new watermark through the ordinary typed
# pipeline. The four ports below are the application state that makes that
# possible — the pins themselves, one evaluated result per (pin, load) so a
# load-over-load delta has something to diff against, a per-load census of
# the detection feed so "new lead" and "self-resolved" are decidable, and
# the lead lifecycle a human drives.
#
# They live beside :class:`AnomalySource` for the same reason it does: the
# engine's services do not use them, but they are application state of the
# investigation capability, and both the API and the Postgres adapters need
# one declaration of their semantics rather than two.
#
# AUTH DEBT (v1, deliberate): every Rounds record is TENANT-scoped, not
# user-scoped. Two analysts in one tenant share one set of Rounds. The
# design intends per-user pins; the credential this deployment issues
# carries a tenant and a subject, and nothing downstream of the token
# consumes the subject yet. Per-user scoping is therefore a column and a
# filter away — it is recorded here rather than implied by silence.


#: How a watch decides that a movement deserves a brief entry.
WATCH_MODES: tuple[str, ...] = ("governed_default", "any_movement", "delta_gte", "crosses")

#: The units a threshold may be STATED in. A rate threshold in dollars and
#: a money threshold in points are both nonsense; see :class:`RoundsWatch`.
WATCH_THRESHOLD_UNITS: tuple[str, ...] = ("points", "relative_pct", "cents")


@dataclass(frozen=True, slots=True)
class RoundsWatch:
    """The analyst's own sensitivity for one watch, overriding the pack's.

    ``mode`` is one of :data:`WATCH_MODES`:

    * ``governed_default`` — the pack's threshold for the metric's unit
      kind. What every watch gets unless somebody says otherwise;
    * ``any_movement`` — brief me on any change at all. Deliberately
      available: a director watching one payer's denial rate may genuinely
      want every wobble, and refusing that would push them back to a
      spreadsheet. It is also the setting most likely to produce fatigue,
      which is why the brief counts what it produced and says so once;
    * ``delta_gte`` — brief me when the movement reaches ``value`` in
      ``unit``;
    * ``crosses`` — brief me when the value crosses ``value`` in either
      direction. The one mode whose reference point is a LEVEL rather than
      a prior value, which is why its baseline semantics differ.

    ``unit`` is how the threshold is *stated*, not the metric's own unit:
    ``points`` for a rate (0.5 = half a percentage point), ``cents`` for
    money, ``relative_pct`` for a fraction of the reference value (legal for
    any metric). Validated against the pinned spec's own contracts when the
    watch is created — a dishonest pairing is refused with the reason, never
    coerced into the nearest legal one, because a threshold in the wrong
    unit produces a watch that never fires (or always does) for a reason
    nobody can see on the screen.

    An analyst's threshold may LOOSEN the governed gate as well as tighten
    it. That is a deliberate reversal of the safer-looking rule: somebody
    watching one specific cell has knowledge the pack's blanket threshold
    does not. It is paid for rather than refused — every brief entry says
    which threshold briefed it, and a watch whose own threshold keeps firing
    on movements the governed gate calls normal is counted and named once
    (see ``fatigue`` in ``packs/base-rcm/rounds.yaml``).

    BASELINE SEMANTICS (stated here because it is the thing a reader of a
    delta most needs to know): a watch's reference point is its
    CREATION-LOAD value — what the metric read the moment somebody started
    watching — for every mode except ``crosses``, whose reference is the
    crossing level itself. The brief always states the movement since the
    PRIOR load, and additionally states the movement since the baseline
    whenever the two differ materially: a tile that has drifted four points
    since it was pinned while moving 0.2 points overnight is telling two
    different true stories, and a surface showing only the overnight one
    would hide the reason the watch exists.
    """

    mode: str = "governed_default"
    value: Decimal | None = None
    unit: str | None = None  # points | relative_pct | cents
    direction: str = "any"  # any | up | down
    note: str = ""


@dataclass(frozen=True, slots=True)
class RoundsPin:
    """One watch: a typed spec, how to render it, and how sensitive it is.

    A pin created from an artifact ("add this chart to Rounds") and a watch
    created by intent ("watch Silverline's denial rate") are the SAME row
    with different :attr:`created_from_kind` provenance. One store, one
    model, one evaluation path — because they are the same object: a typed
    spec somebody wants re-run every load.

    ``spec`` is the whole point. A pinned chart is not an image and a
    pinned finding is not a number — both are the ``TypedInvestigationSpec``
    that produced them, re-run per watermark through the identical typed
    path (zero model calls, plan-hash cached), so the tile shows the
    CURRENT value with the same grades, bounds and caveats a live answer
    would carry.

    ``window_mode`` says what "re-run" means for this pin, because the two
    kinds of window answer different questions and a tile that did not say
    which would be lying by omission:

    * ``relative`` — the window re-anchors to each load's newest data date,
      so the tile tracks a moving period ("the last full week") and a
      load-over-load delta is a real movement in the business;
    * ``absolute`` / ``anchored`` — the window is fixed dates, so the tile
      re-measures the SAME period every load and a delta is late-arriving
      data (adjudication run-out, back-dated charges), not a movement.
    """

    id: str
    tenant: str
    label: str
    spec: TypedInvestigationSpec
    presentation: str  # chart | finding | worklist_slice | scalar
    window_mode: str  # relative | absolute | anchored
    created_at: datetime
    #: How this watch came to exist: ``artifact`` (pinned from something on
    #: screen), ``intent`` ("watch X", compiled through interpretation), or
    #: ``spec`` (a caller that already held a typed spec). Provenance, not
    #: behaviour — all three evaluate identically.
    created_from_kind: str = "spec"
    created_from_investigation_id: str | None = None
    created_from_referent: str | None = None
    watch: RoundsWatch | None = None
    archived_at: datetime | None = None
    #: The token subject that created it — recorded, never enforced (see
    #: the AUTH DEBT note above).
    created_by: str = ""
    #: The BASELINE: what this watch read at the load it was created on,
    #: captured once at first evaluation and never rewritten.
    #:
    #: Stored on the pin rather than derived from the oldest stored result
    #: because the baseline is a fact about the WATCH ("this is where you
    #: started watching from") and must survive result history being
    #: trimmed. ``None`` until the first load evaluates it — a watch created
    #: between loads has no baseline yet, and inventing one from the
    #: previous load's value would attribute a movement to a period nobody
    #: was watching.
    baseline_watermark_id: str | None = None
    baseline_value: Decimal | None = None
    baseline_unit: str | None = None
    baseline_captured_at: datetime | None = None


class RoundsPinStore(Protocol):
    """Pinned specs, tenant-scoped."""

    async def save(self, pin: RoundsPin) -> None: ...

    async def get(self, pin_id: str) -> RoundsPin | None: ...

    async def list_for_tenant(
        self, tenant: str, *, include_archived: bool = False
    ) -> tuple[RoundsPin, ...]:
        """The tenant's pins, oldest first. Archived ones are excluded by
        default: un-pinning is soft, so the evaluated history a brief already
        published stays readable."""
        ...

    async def archive(self, pin_id: str) -> None:
        """Un-pin. Idempotent, and it deletes nothing."""
        ...

    async def tenants_with_pins(self) -> tuple[str, ...]:
        """Every tenant holding at least one ACTIVE pin — what a sweep
        iterates. There is no principal on a scheduled run to take a tenant
        from, and evaluating a tenant with nothing pinned costs a query for
        no tile."""
        ...


@dataclass(frozen=True, slots=True)
class RoundsPinResult:
    """One pin, evaluated at one load.

    ``payload`` is the serialized tile. Kept generic on purpose (like the
    turn receipt): the tile shape is a wire contract that will grow, and a
    typed column per field would make every addition a migration.

    ``watermark_loaded_at`` is stored beside the id because "the prior
    load" must be answerable by the load's own clock — watermark ids are
    opaque strings, and ordering a history lexically is a guess that
    happens to work on ``wm_001``.
    """

    pin_id: str
    tenant: str
    watermark_id: str
    watermark_loaded_at: datetime
    evaluated_at: datetime
    payload: Mapping[str, Any]


class RoundsPinResultStore(Protocol):
    async def put(self, result: RoundsPinResult) -> None: ...

    async def get(self, pin_id: str, watermark_id: str) -> RoundsPinResult | None: ...

    async def history(self, pin_id: str, *, limit: int = 12) -> tuple[RoundsPinResult, ...]:
        """This pin's evaluated loads, NEWEST FIRST."""
        ...


@dataclass(frozen=True, slots=True)
class RoundsLoad:
    """What the detection feed said for one tenant at one load.

    The census a brief diffs against: without it "new lead" and "self-
    resolved" are unanswerable, because the feed is read per snapshot and a
    lead that left it simply stops appearing.
    """

    tenant: str
    watermark_id: str
    watermark_loaded_at: datetime
    evaluated_at: datetime
    payload: Mapping[str, Any]


class RoundsLoadStore(Protocol):
    async def put(self, load: RoundsLoad) -> None: ...

    async def get(self, tenant: str, watermark_id: str) -> RoundsLoad | None: ...

    async def list_for_tenant(self, tenant: str, *, limit: int = 12) -> tuple[RoundsLoad, ...]:
        """The tenant's evaluated loads, NEWEST FIRST (by the load's own
        ``loaded_at``, never by id order)."""
        ...


#: The lead lifecycle, in order. ``resolved_confirmed`` and ``regressed``
#: are the only two the PLATFORM may set: they are verdicts about data, and
#: a human asserting either would be asserting a measurement.
LEAD_STATUSES: tuple[str, ...] = (
    "open",
    "acknowledged",
    "working",
    "resolved_claimed",
    "resolved_confirmed",
    "regressed",
)

#: What a human may PATCH a lead to. ``resolved_claimed`` is the strongest
#: claim a person can make — "I fixed it" — and the platform confirms or
#: refuses it against the next loads rather than taking it as fact.
LEAD_STATUSES_HUMAN_SETTABLE: frozenset[str] = frozenset(
    {"open", "acknowledged", "working", "resolved_claimed"}
)


@dataclass(frozen=True, slots=True)
class RoundsLead:
    """One detected lead's lifecycle state, tenant-scoped.

    Keyed by ``anomaly_id`` — the detection feed's own handle — so a lead
    keeps its status across loads even though the feed is re-read per
    snapshot and the card object is rebuilt every time.
    """

    tenant: str
    anomaly_id: str
    status: str
    updated_at: datetime
    note: str = ""
    #: The load at which a human claimed resolution, and the platform's own
    #: re-derived exposure for the lead's drill at that load — the baseline
    #: every subsequent verification is measured against. ``None`` on a
    #: lead nobody has claimed, and ``baseline_cents=None`` with a stated
    #: basis on a lead whose drill this platform cannot re-derive.
    claimed_at_watermark: str | None = None
    baseline_cents: int | None = None
    baseline_basis: str = ""
    #: Loads that have so far verified the claim, in order. The governed
    #: rule requires N consecutive ones before ``resolved_confirmed``.
    confirming_watermarks: tuple[str, ...] = ()
    #: What the last verification actually measured, in the platform's own
    #: words — including "could not verify", which is a result.
    verification_note: str = ""
    #: Every transition, append-only: ``{at, watermark_id, from, to, by,
    #: note}``. A lifecycle with no history cannot answer "who said this
    #: was fixed, and when".
    history: tuple[Mapping[str, Any], ...] = ()


class RoundsLeadStore(Protocol):
    async def put(self, lead: RoundsLead) -> None: ...

    async def get(self, tenant: str, anomaly_id: str) -> RoundsLead | None: ...

    async def list_for_tenant(self, tenant: str) -> tuple[RoundsLead, ...]:
        """The tenant's leads, by anomaly id."""
        ...
