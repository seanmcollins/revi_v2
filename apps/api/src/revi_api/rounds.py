"""Rounds: the proactive surface. Revi walks it every load and briefs it.

Four capabilities, one seam each — none of them a new pipeline:

**PIN = WATCH.** A pin stores the ``TypedInvestigationSpec`` behind an
artifact, never the artifact. Evaluating it re-runs that spec through
``SubmitTurnService`` as an ordinary TYPED first turn: zero model calls,
the §6.6 validation pass, the real findings stage, the real warnings. That
choice is the load-bearing one in this module. The obvious alternative —
a lightweight evaluator that sums a frame, in the shape of
:mod:`revi_api.rederive` — would be a SECOND implementation of the honesty
rules, and every caveat the answer path has learned to publish (bounded
cells, provisional buckets, population caveats, alternate bases, grade
demotion) would have to be re-earned there or silently lost. Six
adversarial rounds went into those rules. A tile is an answer, so a tile
runs the answer path.

The consequence is deliberate and good: every tile IS a real
``Investigation``, with a real trace and a real permalink, so tapping a
tile opens the full investigation rather than a number computed off to the
side.

**PER-LOAD EVALUATION.** :meth:`RoundsService.evaluate_load` is idempotent
per (pin, watermark) and is called from two places for the same reason the
cohort sweep is: the scheduled tick (``revi_api.rounds_sweep``) keeps an
idle deployment current, and the brief route calls it too, so a brief for
a load nobody swept is computed rather than empty. One primitive, two
callers, no drift.

**MATERIALITY.** Every gate is governed content
(``packs/base-rcm/rounds.yaml`` via :mod:`revi_api.rounds_policy`); this
module holds no threshold. Alert fatigue is the death mode, so when in
doubt the gate holds: an unmeasurable movement is counted, not briefed.
Everything withheld is counted on the response — withheld visibly, never
silently.

**LEAD LIFECYCLE.** A human may claim a lead is resolved; only the
platform may confirm it, by re-running the lead's own drill across
consecutive loads. That asymmetry is the product.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from revi_api.assembly import finding_payload
from revi_api.auth import AuthorizationError, Principal
from revi_api.evidence import build_evidence
from revi_api.portfolio import PRIORITY_FORMULA_VERSION
from revi_api.rounds_policy import (
    MaterialityVerdict,
    ResolutionPolicy,
    RoundsPolicy,
    assess_movement,
    assess_new_lead,
    assess_self_resolved,
    format_threshold,
    time_to_impact_for,
    validate_watch,
)
from revi_api.warning_codes import CAUTION, structured_warnings
from revi_investigation.application.dto_mapping import refinement_to_dto
from revi_investigation.application.ports import (
    LEAD_STATUSES_HUMAN_SETTABLE,
    AnomalyRecord,
    RegisteredReferent,
    RoundsLead,
    RoundsLoad,
    RoundsPin,
    RoundsPinResult,
    RoundsWatch,
)
from revi_investigation.application.rendering import (
    format_value,
    magnitude,
    metric_label,
    render_row_label,
)
from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.context import AnalysisSpec, PackVersionRef
from revi_investigation.domain.records import Finding, Investigation, Session
from revi_investigation.domain.refinements import AddFilter
from revi_investigation_contracts.api import (
    AnomalyCard,
    PortfolioLanePayload,
    PortfolioResponse,
    RoundsWatchModel,
    TimeToImpactPayload,
    TypedInvestigationSpec,
    WatchDeclarationPayload,
)
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    WindowSpecModel,
)
from revi_investigation_contracts.rounds import (
    CreateRoundsPinRequest,
    RoundsBriefEntry,
    RoundsBriefResponse,
    RoundsDeltaPayload,
    RoundsFatigueAdvisory,
    RoundsImmaterialSummary,
    RoundsLeadPatchRequest,
    RoundsLeadPayload,
    RoundsPinListResponse,
    RoundsPinPayload,
    RoundsProvenancePayload,
    RoundsResponse,
    RoundsTileIntegrity,
    RoundsTilePayload,
)
from revi_kernel.errors import ErrorCode, PolicyDeniedError, ReviError
from revi_kernel.filters import PredicateOp, iter_predicates
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.scope import ComparisonKind
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    from revi_api.wiring import ApiComponents

logger = logging.getLogger("revi.api.rounds")

#: Builds the ranked portfolio for one tenant at one watermark. Supplied by
#: :class:`~revi_api.service.ApiService` rather than re-implemented here, so
#: a brief's "new lead" and the rail's card are the same object from the
#: same build — the rule the conversational worklist already follows.
PortfolioFor = Callable[[str, DataWatermark], Awaitable[PortfolioResponse]]

#: What a pin evaluation's turn records as its question. Never shown as an
#: analyst's words, because it is not one: it names the watch it re-ran.
_EVALUATION_QUESTION = "(Rounds: re-running a watched spec at this load)"


class RoundsNotFoundError(ReviError):
    """A pin, lead or load the caller named does not exist (HTTP 404).

    ``REFERENT_NOT_FOUND`` for the same reason
    :class:`~revi_api.service.NotFoundError` uses it: "the thing you named
    does not exist here" is one failure whether the handle is a pin id or a
    watermark, and ``UNSUPPORTED_CONCEPT`` would say something different and
    false.
    """

    code = ErrorCode.REFERENT_NOT_FOUND


# ---------------------------------------------------------------------------
# resolving a stored investigation into a re-runnable typed spec


#: Comparison kinds a typed spec can express. ``CUSTOM`` is a pair of
#: literal dates that meant something on the turn that set it; carrying it
#: onto a watch would freeze a comparison window while the primary window
#: moved, which is a different measurement every load.
_COMPARISON_LITERALS = {
    ComparisonKind.PRIOR_PERIOD: "prior_period",
    ComparisonKind.PRIOR_YEAR: "prior_year",
}


def typed_spec_from_analysis(spec: AnalysisSpec) -> tuple[TypedInvestigationSpec, str, list[str]]:
    """The stored ``AnalysisSpec`` as a re-runnable typed spec.

    This is what "pin-from-investigation resolves the stored spec
    server-side" means concretely: the investigation already holds the
    disposed, validated spec its answer was computed from, so the pin is
    built from THAT and no text is re-interpreted and no model is called.
    Re-deriving the spec from the question would be a second, worse answer
    to a question already answered — and it would drift the day the
    interpreter improved.

    Returns the spec, its ``window_mode``, and any notes about what could
    not be carried across. The notes matter: a watch that silently dropped
    a scope clause would measure a different population from the answer the
    analyst was looking at when they pinned it.
    """
    notes: list[str] = []
    window = spec.context.window
    # A RELATIVE window re-anchors per load, so the watch tracks a moving
    # period and a delta is a real movement. An absolute one re-measures
    # fixed dates, so a delta is late-arriving data. Both are legitimate
    # watches; only one of them is a movement, which is why the mode is
    # published rather than inferred by a reader.
    if window.requested is not None:
        window_model: WindowSpecModel | AbsoluteWindowModel = WindowSpecModel(
            quantity=str(window.requested.quantity),
            unit=window.requested.unit.value,
            mode=window.requested.mode.value,
        )
        window_mode = "relative"
    else:
        window_model = AbsoluteWindowModel(start=window.range.start, end=window.range.end)
        window_mode = "absolute"

    filters: list[AddFilterModel] = []
    for predicate in iter_predicates(spec.context.scope):
        dto = refinement_to_dto(AddFilter(predicate))
        assert isinstance(dto, AddFilterModel)  # AddFilter maps to exactly this
        filters.append(dto)
    # Pins (session-sticky scope) are deliberately NOT carried: they belong
    # to the session that declared them, and a watch that inherited another
    # conversation's sticky filter would narrow every future load by a
    # decision nobody made about this watch.
    if spec.context.pins:
        notes.append(
            f"{len(spec.context.pins)} session-pinned scope clause(s) were not carried onto "
            "this watch: a pin belongs to the conversation that declared it, and inheriting "
            "one here would narrow every future load by a decision nobody made about the watch"
        )
    if spec.context.cohort is not None:
        notes.append(
            "the answer this watch was created from was computed over a pinned cohort "
            f"({spec.context.cohort.id}), which is an extensional set materialized at one "
            "watermark; the watch carries the scope predicates instead, so it re-selects the "
            "population at every load rather than re-reading a frozen one"
        )

    comparison: str | None = None
    if spec.context.comparison is not None:
        comparison = _COMPARISON_LITERALS.get(spec.context.comparison.kind)
        if comparison is None:
            notes.append(
                "the answer's custom comparison window was not carried onto this watch: it is "
                "a pair of literal dates, and holding it fixed while the primary window moves "
                "would make every load a different measurement"
            )

    typed = TypedInvestigationSpec(
        metric_ids=[measure.id for measure in spec.measures],
        dimensions=[dimension.id for dimension in spec.dimensions],
        filters=filters,
        window=window_model,
        basis=window.basis.id,
        comparison=comparison,  # type: ignore[arg-type]
    )
    return typed, window_mode, notes


def _eq_filters_of(spec: TypedInvestigationSpec) -> tuple[tuple[str, str], ...]:
    """The single-value equality filters on a spec, as ``(dimension, value)``.

    These are what makes a spec name ONE cell rather than a ranking, so they
    are what the label, the spec summary and the subject-identity guard all
    read.
    """
    return tuple(
        (f.dimension, str(f.values[0]))
        for f in spec.filters
        if f.predicate_op == "eq" and len(f.values) == 1
    )


def _narrowed_to_cell(
    spec: TypedInvestigationSpec, cell: Sequence[tuple[str, str]]
) -> TypedInvestigationSpec:
    """The same spec, restricted to one cell of its own breakdown.

    The dimensions are KEPT. A watch narrowed to ``payer = Pinnacle Health
    Plan`` and still broken out by payer evaluates to a one-row breakdown
    whose finding names that payer — so the tile's own headline states the
    subject, and label and value cannot come apart. Dropping the dimension
    would answer with a bare scalar ("denial rate is 22.9%") and throw away
    the very fact this fix exists to keep.
    """
    existing = {(f.dimension, str(f.values[0])) for f in _iter_eq(spec)}
    additions = [
        AddFilterModel(
            op="add_filter", dimension=dimension, predicate_op="eq", values=[value]
        )
        for dimension, value in cell
        if (dimension, value) not in existing
    ]
    if not additions:
        return spec
    return spec.model_copy(update={"filters": [*spec.filters, *additions]})


def _iter_eq(spec: TypedInvestigationSpec) -> list[AddFilterModel]:
    return [f for f in spec.filters if f.predicate_op == "eq" and len(f.values) == 1]


def _cell_phrase(cell: Sequence[tuple[str, str]], pack: Any) -> str:
    """A cell in the reader's words, with codes rendered as codes.

    ``payer`` + ``group_code`` + ``carc`` reads "Bluestone Mutual / CO / 16
    — Claim/service lacks information" rather than three raw values, using
    the same governed renderer the findings themselves use — so a watch
    label and the finding it was pinned from name the cell identically.
    """
    if not cell:
        return ""
    dimensions = [dimension for dimension, _ in cell]
    values: dict[str, Any] = dict(cell)
    return render_row_label(pack, dimensions, values)


def _window_phrase(spec: TypedInvestigationSpec, window_mode: str) -> str:
    """The watch's window, said the way somebody would say it."""
    window = spec.window
    if isinstance(window, WindowSpecModel):
        unit = str(window.unit)
        quantity = str(window.quantity)
        period = unit if quantity in ("1", "1.0") else f"{quantity} {unit}s"
        moving = "the last full " if window.mode == "full_periods" else "the last "
        return f"{moving}{period}, re-anchored at every load"
    if isinstance(window, AbsoluteWindowModel):
        return f"the fixed dates {window.start}..{window.end}, re-measured at every load"
    return "the window stored with this watch"


def spec_hash(spec: TypedInvestigationSpec, presentation: str) -> str:
    """A stable identity for "what this watch measures, and how it renders".

    Normalised so two specs that differ only in the ORDER somebody named
    their metrics, dimensions or filters hash the same: they are one
    measurement, and treating them as two is how a department of eight
    directors ends up with six copies of the same watch briefing the same
    movement six times (round-7 FN-18).
    """
    payload = {
        "metric_ids": sorted(spec.metric_ids),
        "dimensions": sorted(spec.dimensions),
        "filters": sorted(
            f"{f.dimension}|{f.predicate_op}|{'|'.join(sorted(str(v) for v in f.values))}"
            for f in spec.filters
        ),
        "window": spec.window.model_dump(mode="json"),
        "basis": spec.basis or "",
        "comparison": spec.comparison or "",
        "presentation": presentation,
    }
    return hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()[:16]


_WINDOW_NOTES = {
    "relative": (
        "This watch re-anchors its window to each load's newest data date, so it usually "
        "tracks a moving period. Where two loads land inside the same period it resolves to "
        "the same dates, and the change between them is late-arriving data rather than a "
        "movement — each tile publishes the dates it actually measured, so a reader never "
        "has to guess which of the two they are looking at."
    ),
    "absolute": (
        "This watch re-measures the SAME fixed dates every load, so a load-over-load change "
        "is always late-arriving data (adjudication run-out, back-dated charges) rather than "
        "a movement in the period itself."
    ),
}
_WINDOW_NOTES["anchored"] = _WINDOW_NOTES["absolute"]

#: The sentence a movement earns when both loads measured the SAME dates.
#: Not a caveat about the number — the number is right — but about what the
#: change MEANS, which is the thing a delta on a daily surface is read for.
SAME_WINDOW_NOTE = (
    "Both loads measured the same dates ({start}..{end}), so this change is late-arriving "
    "data settling — adjudication run-out, back-dated charges — rather than a movement in "
    "the period itself."
)


# ---------------------------------------------------------------------------
# the service


class RoundsService:
    """Pins, per-load evaluation, the brief, and the lead lifecycle."""

    def __init__(
        self,
        components: ApiComponents,
        *,
        portfolio_for: PortfolioFor,
    ) -> None:
        self._components = components
        self._portfolio_for = portfolio_for

    @property
    def policy(self) -> RoundsPolicy:
        return self._components.rounds_policy

    # ------------------------------------------------------------------ pins

    async def create_pin(
        self, principal: Principal, request: CreateRoundsPinRequest
    ) -> RoundsPinPayload:
        """Add a watch, from an investigation on screen or from a typed spec."""
        if (request.investigation_id is None) == (request.spec is None):
            raise PolicyDeniedError(
                "a pin names exactly one of `investigation_id` (watch what is on screen) or "
                "`spec` (watch a typed spec you already hold) — a body carrying both, or "
                "neither, would leave the platform guessing which was meant",
                details={"tenant": principal.tenant},
            )
        notes: list[str] = []
        label = request.label.strip()
        created_from_kind = "spec"
        if request.investigation_id is not None:
            investigation = await self._authorized_investigation(
                principal, request.investigation_id
            )
            spec, window_mode, notes = typed_spec_from_analysis(investigation.spec)
            created_from_kind = "artifact"
            # THE CELL, not the ranking it was drawn from. A finding on a
            # ranked breakdown names ONE payer; the investigation's spec
            # names all of them, and pinning the spec unnarrowed produced a
            # tile titled "Pinnacle Health Plan: 22.9%" whose number was
            # State Medicaid MCO's 29.5% — wrong on the day it was created,
            # not merely after a rank flip (round-7 FN-1).
            cell = await self._referent_cell(investigation, request.referent, spec)
            if cell:
                spec = _narrowed_to_cell(spec, cell)
                notes.append(
                    "this watch was narrowed at creation to the cell you pinned "
                    f"({_cell_phrase(cell, self._components.pack_port)}), so it measures that "
                    "cell at every load rather than whatever ranks first"
                )
            elif self._referent_is_a_cell(investigation, request.referent) and spec.dimensions:
                raise PolicyDeniedError(
                    f"this watch was pinned from {request.referent!r}, which names one cell of "
                    "a ranked breakdown, and that cell can no longer be resolved from the "
                    "investigation it came from — so the only thing left to pin is the whole "
                    "ranking, which would put one cell's name over whatever ranks first at "
                    "every future load. Re-run the answer and pin it again, or pin the "
                    "breakdown itself with a label that says so",
                    details={
                        "tenant": principal.tenant,
                        "investigation_id": request.investigation_id,
                        "referent": request.referent,
                    },
                )
            if not label:
                label = self._composed_label(spec)
        else:
            assert request.spec is not None
            spec = request.spec
            window_mode = (
                "relative" if isinstance(spec.window, WindowSpecModel) else "absolute"
            )
            if not label:
                label = self._composed_label(spec)
        # The threshold is checked BEFORE the duplicate lookup, so a request
        # carrying an illegal one is refused on its own terms rather than
        # answered with somebody else's watch.
        watch = _watch_from_model(request.watch)
        if watch is not None:
            refusal = validate_watch(watch, units=self._units_for(spec.metric_ids))
            if refusal is not None:
                raise PolicyDeniedError(
                    f"this watch's threshold cannot be applied honestly: {refusal}",
                    details={"tenant": principal.tenant, "watch": request.watch.model_dump()
                             if request.watch is not None else None},
                )
        # Already watching this? Return THAT watch rather than a second copy
        # of it. Every duplicate is re-evaluated every load and can brief one
        # movement N times, which is the alert fatigue the pack spends 300
        # lines preventing — and the client already told the analyst this
        # check existed (round-7 FN-18).
        existing_pin = await self._pin_with_same_spec(
            principal.tenant, spec, request.presentation
        )
        if existing_pin is not None:
            if watch is not None and watch != existing_pin.watch:
                # A DIFFERENT sensitivity over the same spec is a different
                # instruction, and quietly answering it with the existing
                # watch's threshold would be the silent substitution this
                # round is fixing everywhere else (FN-6). Refused, naming the
                # watch to adjust — creating a second one would brief the
                # same movement twice every morning.
                raise PolicyDeniedError(
                    f"you are already watching this spec as {existing_pin.label!r}, and this "
                    "request states a different sensitivity for it. A second watch over the "
                    "same spec would brief the same movement twice every morning, and "
                    "quietly keeping the existing threshold would apply a number you did not "
                    "ask for — change that watch's sensitivity instead",
                    details={"tenant": principal.tenant, "pin_id": existing_pin.id},
                )
            logger.info(
                "rounds pin create for tenant %s returned existing pin %s (same spec)",
                principal.tenant,
                existing_pin.id,
            )
            return self._pin_payload(
                existing_pin,
                notes=[
                    f"you are already watching this — {existing_pin.label!r} measures the same "
                    "spec, so this returned that watch instead of creating a second one that "
                    "would brief the same movement twice every morning"
                ],
                already_existed=True,
            )
        pin = RoundsPin(
            id=f"pin_{uuid.uuid4().hex[:12]}",
            tenant=principal.tenant,
            label=label or "Watched spec",
            spec=spec,
            presentation=request.presentation,
            window_mode=window_mode,
            created_at=datetime.now(UTC),
            created_from_kind=created_from_kind,
            created_from_investigation_id=request.investigation_id,
            created_from_referent=request.referent,
            watch=watch,
            created_by=principal.subject,
        )
        await self._components.rounds_pins.save(pin)
        logger.info(
            "rounds pin %s created for tenant %s from %s (%s window)",
            pin.id,
            pin.tenant,
            created_from_kind,
            window_mode,
        )
        return self._pin_payload(pin, notes=notes)

    async def register_intent_pin(
        self,
        principal: Principal,
        outcome: TurnOutcome,
        *,
        label: str,
        watch: RoundsWatch | None,
        matched_phrase: str,
    ) -> WatchDeclarationPayload:
        """Register a watch declared in words, from the turn that answered it.

        The declaration turn has already run as an ordinary investigation —
        same interpretation, same planning, same §6.6 validation — so the
        spec pinned here is the one the analyst just saw answered, and that
        answer doubles as the baseline. Nothing is re-interpreted: this is
        the same ``typed_spec_from_analysis`` resolution the on-screen pin
        path uses, over the same stored spec.
        """
        investigation = outcome.investigation
        watermark = outcome.session.watermark
        watermark_id = watermark.id
        spec, window_mode, _ = typed_spec_from_analysis(investigation.spec)
        baseline = self._headline(outcome, spec)
        if watch is not None:
            refusal = validate_watch(watch, units=self._units_for(spec.metric_ids))
            if refusal is not None:
                raise PolicyDeniedError(
                    f"this watch's threshold cannot be applied honestly: {refusal}",
                    details={"tenant": principal.tenant},
                )
        pin = RoundsPin(
            id=f"pin_{uuid.uuid4().hex[:12]}",
            tenant=principal.tenant,
            label=label,
            spec=spec,
            presentation="scalar",
            window_mode=window_mode,
            created_at=datetime.now(UTC),
            created_from_kind="intent",
            created_from_investigation_id=investigation.id,
            watch=watch,
            created_by=principal.subject,
            # The declaration turn IS the baseline load: the analyst saw
            # this number and said "watch that". Capturing it here rather
            # than waiting for the next evaluation means the first brief can
            # already say how far it has moved since they asked.
            baseline_watermark_id=watermark_id if baseline is not None else None,
            baseline_value=baseline.value if baseline is not None else None,
            baseline_unit=baseline.unit if baseline is not None else None,
            baseline_captured_at=datetime.now(UTC) if baseline is not None else None,
        )
        await self._components.rounds_pins.save(pin)
        # The declaration turn IS an evaluation of this watch at this load,
        # so it is stored as one. Without it the baseline is a bare number
        # with no recorded cell and no recorded window, and every later
        # baseline delta has to refuse for want of the two facts that decide
        # whether it is a like-for-like comparison (round-7 FN-2, FN-9).
        if baseline is not None:
            try:
                await self._store_tile(
                    pin, watermark, await self._tile_from_outcome(pin, outcome, watermark, None)
                )
            except Exception:  # pragma: no cover - defensive; the watch still stands
                logger.warning(
                    "rounds: baseline evaluation for watch %s could not be stored", pin.id,
                    exc_info=True,
                )
        logger.info("rounds watch %s declared by intent for tenant %s", pin.id, pin.tenant)
        threshold_statement = _threshold_statement(watch, baseline.unit if baseline else None)
        alternative = _threshold_alternative(
            watch, baseline.unit if baseline else None, baseline.value if baseline else None
        )
        value_text = baseline.text if baseline is not None else ""
        statement = _watch_confirmation(
            label, value_text, threshold_statement, alternative
        )
        return WatchDeclarationPayload(
            pin_id=pin.id,
            label=label,
            statement=statement,
            spec=spec,
            watch=_watch_model(watch),
            threshold_statement=threshold_statement,
            threshold_alternative=alternative,
            baseline_value_text=value_text,
            baseline_watermark_id=pin.baseline_watermark_id or "",
            matched_phrase=matched_phrase,
        )

    async def list_pins(self, principal: Principal) -> RoundsPinListResponse:
        pins = await self._components.rounds_pins.list_for_tenant(principal.tenant)
        return RoundsPinListResponse(
            tenant=principal.tenant,
            pins=[self._pin_payload(pin) for pin in pins],
            total=len(pins),
        )

    async def archive_pin(self, principal: Principal, pin_id: str) -> None:
        """Un-pin. SOFT, like every other dismissal on this platform: the
        evaluated history a brief already published stays readable, and a
        permalink into a tile's investigation does not 404 because somebody
        tidied their Rounds."""
        pin = await self._authorized_pin(principal, pin_id)
        await self._components.rounds_pins.archive(pin.id)
        logger.info("rounds pin %s archived by tenant %s", pin_id, principal.tenant)

    async def repair_pins(self, tenant: str) -> dict[str, list[str]]:
        """Bring watches created before round 7 onto the narrowed-cell rule.

        Every watch pinned from one cell of a ranked breakdown stored the
        WHOLE ranking and titled itself with that cell's finding, so its
        tile has been showing another subject's number since the day it was
        created (round-7 FN-1). Fixing the create path does not fix those
        rows, and leaving them is leaving the defect in production under a
        fix's name.

        Two outcomes, both stated:

        * the investigation it was pinned from still resolves the cell —
          the spec is narrowed to it, the label is recomposed from the
          narrowed spec, and the baseline is CLEARED so the next load
          captures it from the right cell. Keeping the old baseline would
          measure this cell against another cell's number, which is the
          same defect with a longer half-life;
        * it does not — the watch is archived (softly, like every other
          dismissal here) and its last tile says why, because a watch that
          silently kept publishing the wrong subject is worse than one that
          stopped and explained itself.

        Idempotent: a watch already narrowed to its cell is left alone.
        """
        repaired: list[str] = []
        archived: list[str] = []
        for pin in await self._components.rounds_pins.list_for_tenant(tenant):
            if not pin.created_from_referent or _spec_names_one_cell(pin.spec):
                continue
            investigation = (
                await self._components.investigations.get(pin.created_from_investigation_id)
                if pin.created_from_investigation_id
                else None
            )
            cell = (
                await self._referent_cell(investigation, pin.created_from_referent, pin.spec)
                if investigation is not None
                else ()
            )
            if not cell:
                await self._archive_unrepairable(pin)
                archived.append(pin.id)
                continue
            narrowed = _narrowed_to_cell(pin.spec, cell)
            await self._components.rounds_pins.save(
                replace(
                    pin,
                    spec=narrowed,
                    label=self._composed_label(narrowed),
                    baseline_watermark_id=None,
                    baseline_value=None,
                    baseline_unit=None,
                    baseline_captured_at=None,
                )
            )
            repaired.append(pin.id)
            logger.info(
                "rounds: pin %s narrowed to its pinned cell (%s) and its baseline reset",
                pin.id,
                _cell_phrase(cell, self._components.pack_port),
            )
        return {"repaired": repaired, "archived": archived}

    async def _archive_unrepairable(self, pin: RoundsPin) -> None:
        """Stop a watch that cannot be told which cell it is about, and say so
        where its number used to be."""
        newest = await self._components.rounds_results.history(pin.id, limit=1)
        note = (
            "this watch was pinned from one cell of a ranked breakdown and stored the whole "
            "ranking, so its title named one subject and its number was whichever subject "
            "ranked first at each load. The answer it was created from can no longer resolve "
            "that cell, so the watch has been stopped rather than left publishing a number "
            "under somebody else's name. Pin it again from a current answer to resume it."
        )
        for result in newest:
            tile = RoundsTilePayload.model_validate(result.payload)
            await self._components.rounds_results.put(
                RoundsPinResult(
                    pin_id=pin.id,
                    tenant=pin.tenant,
                    watermark_id=result.watermark_id,
                    watermark_loaded_at=result.watermark_loaded_at,
                    evaluated_at=datetime.now(UTC),
                    payload=tile.model_copy(
                        update={
                            "status": "unavailable",
                            "unavailable_reason": note,
                            "delta": None,
                            "baseline_delta": None,
                        }
                    ).model_dump(mode="json"),
                )
            )
        await self._components.rounds_pins.archive(pin.id)
        logger.warning("rounds: pin %s archived — its pinned cell cannot be resolved", pin.id)

    async def _authorized_pin(self, principal: Principal, pin_id: str) -> RoundsPin:
        pin = await self._components.rounds_pins.get(pin_id)
        if pin is None:
            raise RoundsNotFoundError(
                f"pin {pin_id!r} does not exist", details={"pin_id": pin_id}
            )
        if pin.tenant != principal.tenant:
            # Refused, not disguised as a 404: pin ids are not secrets, and
            # the same rule the session reads follow applies here.
            raise AuthorizationError(
                f"pin {pin_id!r} belongs to another tenant",
                details={"pin_id": pin_id, "tenant": principal.tenant},
            )
        return pin

    async def _authorized_investigation(
        self, principal: Principal, investigation_id: str
    ) -> Investigation:
        investigation = await self._components.investigations.get(investigation_id)
        if investigation is None:
            raise RoundsNotFoundError(
                f"investigation {investigation_id!r} does not exist",
                details={"investigation_id": investigation_id},
            )
        session = await self._components.sessions.get(investigation.session_id)
        if session is None or session.tenant != principal.tenant:
            raise AuthorizationError(
                f"investigation {investigation_id!r} belongs to another tenant",
                details={"investigation_id": investigation_id, "tenant": principal.tenant},
            )
        return investigation

    # ------------------------------------------------- what a pin measures

    async def _referent_cell(
        self,
        investigation: Investigation,
        referent: str | None,
        spec: TypedInvestigationSpec,
    ) -> tuple[tuple[str, str], ...]:
        """The dimension members the pinned artifact stands for.

        This is the fix for the round-7 signature defect. A finding on a
        ranked breakdown IS a cell — the referent registry has held its
        dimension members since §7.6, because that is how "drill into F1"
        works — and the pin path stored the parent spec and used the
        finding's TITLE as the tile's label. The tile then headlined
        whatever ranked first at each load under a title naming a different
        payer, and certified the result ``grade: direct``.

        Only members on dimensions this SPEC actually breaks out are
        returned: narrowing by a dimension the watch does not measure would
        change the population without changing what the tile says it is.

        Empty when the referent names no cell (a scalar answer, a chart of
        the whole breakdown) or when its registry entry no longer belongs to
        this investigation — a referent id is session-scoped and a later
        turn reuses it, so the entry is only evidence about THIS
        investigation when it says so.
        """
        if not referent or not spec.dimensions:
            return ()
        entry = await self._referent_entry(investigation, referent)
        if entry is None:
            return ()
        wanted = list(spec.dimensions)
        members: dict[str, str] = {}
        # The cohort definition is the row's own scope: the spec's scope
        # AND one eq-predicate per dimension column. It carries every
        # dimension of a multi-dimension row, where ``dimension_value``
        # carries one and is None above a single column.
        if entry.cohort_definition is not None:
            for predicate in iter_predicates(entry.cohort_definition.scope):
                if predicate.op is not PredicateOp.EQ:
                    continue
                if predicate.dimension.id not in wanted:
                    continue
                members[predicate.dimension.id] = str(predicate.values[0])
        if not members and entry.dimension_value is not None:
            dimension, value = entry.dimension_value
            if dimension in wanted:
                members[dimension] = str(value)
        # All or nothing. A partial narrowing still leaves a ranking behind
        # the label, which is the defect with fewer dimensions.
        if set(members) != set(wanted):
            return ()
        return tuple((dimension, members[dimension]) for dimension in wanted)

    async def _referent_entry(
        self, investigation: Investigation, referent: str
    ) -> RegisteredReferent | None:
        for entry in await self._components.referents.list_for_session(
            investigation.session_id
        ):
            if entry.referent.value == referent and entry.investigation_id == investigation.id:
                return entry
        return None

    @staticmethod
    def _referent_is_a_cell(investigation: Investigation, referent: str | None) -> bool:
        """Does this referent name a FINDING on the pinned investigation?

        A finding referent on a breakdown is a cell and must resolve to one.
        A chart id is not: pinning a chart is a legitimate watch of the
        whole ranking, and it gets a label that says so.
        """
        if not referent:
            return False
        return any(finding.referent.value == referent for finding in investigation.findings)

    def _composed_label(self, spec: TypedInvestigationSpec) -> str:
        """The tile's title, composed from what the watch MEASURES.

        Never a finding title. A finding title carries the value it had on
        the load it was written ("Pinnacle Health Plan: 22.9% denial rate"),
        so it goes stale on the most prominent line of a surface whose whole
        job is to be current — and it names a subject the spec may not have
        been narrowed to. Composed from the spec, the label and the number
        cannot come from different loads or different cells.
        """
        pack = self._components.pack_port
        metrics = " and ".join(
            self._components.metric_display.name_for(metric_id) or metric_label(metric_id)
            for metric_id in spec.metric_ids
        ) or "Watched spec"
        cell = _cell_phrase(_eq_filters_of(spec), pack)
        if cell:
            return f"{cell} — {metrics}"
        if spec.dimensions:
            dimensions = " and ".join(metric_label(d) for d in spec.dimensions)
            return f"{metrics} by {dimensions}"
        return metrics[:1].upper() + metrics[1:]

    def _spec_summary(self, spec: TypedInvestigationSpec, window_mode: str) -> str:
        """The stored spec in the reader's own nouns (round-7 FN-18).

        The panel headed "What this watch measures" rendered a window note
        and the analyst's own note, and not the metric, the breakdown or the
        filters — every one of which was already on the wire. It is the one
        control that lets somebody catch a watch that is measuring the wrong
        thing, and it was the one place scope was omitted.
        """
        pack = self._components.pack_port
        metrics = " and ".join(
            self._components.metric_display.name_for(metric_id) or metric_label(metric_id)
            for metric_id in spec.metric_ids
        )
        parts = [metrics[:1].upper() + metrics[1:] if metrics else "A typed spec"]
        if spec.dimensions:
            parts.append(
                "broken down by " + " and ".join(metric_label(d) for d in spec.dimensions)
            )
        filters = _eq_filters_of(spec)
        if filters:
            parts.append("filtered to " + _cell_phrase(filters, pack))
        other = [
            f"{metric_label(f.dimension)} {f.predicate_op} "
            f"{', '.join(str(v) for v in f.values)}"
            for f in spec.filters
            if f.predicate_op != "eq" or len(f.values) != 1
        ]
        if other:
            parts.append("filtered where " + "; ".join(other))
        window = _window_phrase(spec, window_mode)
        summary = ", ".join(parts)
        basis = f" on the {spec.basis} basis" if spec.basis else ""
        return f"{summary} — {window}{basis}."

    async def _pin_with_same_spec(
        self, tenant: str, spec: TypedInvestigationSpec, presentation: str
    ) -> RoundsPin | None:
        """An ACTIVE watch on this tenant already measuring exactly this."""
        digest = spec_hash(spec, presentation)
        for pin in await self._components.rounds_pins.list_for_tenant(tenant):
            if pin.archived_at is not None:
                continue
            if spec_hash(pin.spec, pin.presentation) == digest:
                return pin
        return None

    def _pin_payload(
        self,
        pin: RoundsPin,
        *,
        notes: Sequence[str] = (),
        already_existed: bool = False,
    ) -> RoundsPinPayload:
        return pin_payload(
            pin,
            notes=notes,
            spec_summary=self._spec_summary(pin.spec, pin.window_mode),
            already_existed=already_existed,
        )

    def _units_for(self, metric_ids: Sequence[str]) -> tuple[str | None, ...]:
        pack = self._components.pack_port
        units: list[str | None] = []
        for metric_id in metric_ids:
            contract = pack.metric(metric_id)
            unit = getattr(contract, "unit", None)
            units.append(None if unit is None else str(unit))
        return tuple(units)

    # ------------------------------------------------------- per-load evaluation

    async def evaluate_load(
        self, tenant: str, watermark: DataWatermark, *, force: bool = False
    ) -> RoundsLoad:
        """Re-run every active pin at this load, verify claimed resolutions,
        and record the detection-feed census.

        Idempotent per (pin, watermark): a stored result is reused rather
        than recomputed, so calling this from the scheduled sweep and from
        the brief route costs one evaluation between them. ``force``
        re-evaluates — for a redeployed pack, or a repaired snapshot.
        """
        # Watches created before the narrowed-cell rule are brought onto it
        # (or stopped) BEFORE they are evaluated, so no load re-publishes a
        # tile whose label and value name different subjects. Runs on every
        # load and does nothing after the first: a repaired watch names one
        # cell, and this only looks at watches that do not.
        try:
            await self.repair_pins(tenant)
        except Exception:  # pragma: no cover - a repair must not cost a load
            logger.exception("rounds: pin repair pass failed for tenant %s", tenant)
        pins = await self._components.rounds_pins.list_for_tenant(tenant)
        evaluated = 0
        for pin in pins:
            existing = await self._components.rounds_results.get(pin.id, watermark.id)
            if existing is not None and not force:
                continue
            await self._evaluate_pin(pin, watermark)
            evaluated += 1

        portfolio = await self._portfolio_for(tenant, watermark)
        verifications = await self._verify_claimed_leads(tenant, watermark, portfolio)
        census = await self._census(tenant, watermark, portfolio, pins, verifications)
        load = RoundsLoad(
            tenant=tenant,
            watermark_id=watermark.id,
            watermark_loaded_at=watermark.loaded_at,
            evaluated_at=datetime.now(UTC),
            payload=census,
        )
        await self._components.rounds_loads.put(load)
        logger.info(
            "rounds: evaluated %d of %d pin(s) and verified %d claimed lead(s) for tenant %s "
            "at %s",
            evaluated,
            len(pins),
            len(verifications),
            tenant,
            watermark.id,
        )
        return load

    async def _evaluate_pin(self, pin: RoundsPin, watermark: DataWatermark) -> RoundsTilePayload:
        """Run one pin's stored spec at one load, and store the tile.

        The evaluation is an ordinary TYPED first turn — see the module
        docstring for why this is the answer path and not a lighter one.
        """
        prior = await self._prior_result(pin, watermark)
        session = await self._rounds_session(pin.tenant, watermark)
        try:
            outcome = await self._components.submit.submit(
                SubmitTurnRequest(
                    tenant=pin.tenant,
                    question=_EVALUATION_QUESTION,
                    session_id=session.id,
                    spec=pin.spec,
                )
            )
        except ReviError as exc:
            # A stored spec can stop being answerable — a catalog change, a
            # pack that retired a metric. The tile says so in the platform's
            # own error vocabulary rather than going blank, which would read
            # as a zero.
            tile = RoundsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="unavailable",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                unavailable_reason=f"{exc.code.value}: {exc.message}",
            )
            await self._store_tile(pin, watermark, tile)
            return tile
        except Exception:
            logger.exception("rounds: pin %s could not be evaluated at %s", pin.id, watermark.id)
            tile = RoundsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="unavailable",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                unavailable_reason="this watch could not be evaluated at this load (the "
                "attempt is recorded in the API log); no value is published rather than a "
                "stale one",
            )
            await self._store_tile(pin, watermark, tile)
            return tile

        tile = await self._tile_from_outcome(pin, outcome, watermark, prior)
        # The baseline is captured ONCE, at the first load that produces a
        # value. A watch created between loads has no baseline until then,
        # and taking the previous load's value would attribute a movement to
        # a period nobody was watching.
        if pin.baseline_value is None and tile.value is not None:
            pin = replace(
                pin,
                baseline_watermark_id=watermark.id,
                baseline_value=Decimal(str(tile.value)),
                baseline_unit=tile.unit,
                baseline_captured_at=datetime.now(UTC),
            )
            await self._components.rounds_pins.save(pin)
        tile = tile.model_copy(
            update={"baseline_delta": await self._baseline_delta(pin, tile)}
        )
        await self._store_tile(pin, watermark, tile)
        return tile

    async def _store_tile(
        self, pin: RoundsPin, watermark: DataWatermark, tile: RoundsTilePayload
    ) -> None:
        await self._components.rounds_results.put(
            RoundsPinResult(
                pin_id=pin.id,
                tenant=pin.tenant,
                watermark_id=watermark.id,
                watermark_loaded_at=watermark.loaded_at,
                evaluated_at=datetime.now(UTC),
                payload=tile.model_dump(mode="json"),
            )
        )

    async def _prior_result(
        self, pin: RoundsPin, watermark: DataWatermark
    ) -> RoundsTilePayload | None:
        """This pin's newest evaluation STRICTLY BEFORE this load.

        Ordered by the load's own clock, never by watermark id: that
        ``wm_001`` sorts before ``wm_002`` is a coincidence of one
        warehouse's naming, and diffing the wrong pair of loads is worse
        than diffing none.
        """
        for result in await self._components.rounds_results.history(pin.id, limit=12):
            if _utc(result.watermark_loaded_at) < _utc(watermark.loaded_at):
                return RoundsTilePayload.model_validate(result.payload)
        return None

    async def _rounds_session(self, tenant: str, watermark: DataWatermark) -> Session:
        """The session a load's evaluations run in, pinned AT that load.

        Deterministic per (tenant, watermark) so re-evaluating a load reuses
        one session instead of minting one per tile, and created ARCHIVED so
        it never appears in the analyst's rail — a soft archive keeps every
        investigation inside it fetchable by id, which is what makes a tile's
        permalink work.

        Pinned at the requested watermark rather than the newest, because
        that is what evaluating a load MEANS. Re-running a historical load
        therefore produces an honest ``watermark_stale`` on the turn: the
        session really is behind the newest data, and the tile names the
        load it was measured at.
        """
        digest = hashlib.sha256(f"{tenant}|{watermark.id}".encode()).hexdigest()[:16]
        session_id = f"rounds_{digest}"
        existing = await self._components.sessions.get(session_id)
        if existing is not None:
            return existing
        pack = self._components.pack_port
        session = Session(
            id=session_id,
            tenant=tenant,
            pack_version=PackVersionRef(pack.pack_id, pack.pack_version),
            epochs=(WatermarkEpoch(index=0, watermark=watermark),),
            created_at=datetime.now(UTC),
        )
        await self._components.sessions.save(session)
        await self._components.sessions.archive(session_id)
        return session

    async def _tile_from_outcome(
        self,
        pin: RoundsPin,
        outcome: TurnOutcome,
        watermark: DataWatermark,
        prior: RoundsTilePayload | None,
    ) -> RoundsTilePayload:
        if outcome.clarification is not None:
            # A typed spec should never clarify. If one does, that is
            # reported rather than swallowed: it means the stored spec has
            # become ambiguous against the current pack, which is a fact
            # about the watch and not a blank tile.
            return RoundsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="clarification",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                investigation_id=outcome.investigation.id,
                unavailable_reason=(
                    "re-running this watch's stored spec at this load asked a question rather "
                    f"than answering: {outcome.clarification.question}"
                ),
            )
        trace = await self._components.traces.get(outcome.trace_id)
        evidence = build_evidence(trace) if trace is not None else None
        warnings = list(outcome.warnings)
        classified = structured_warnings(warnings)
        headline = self._headline(outcome, pin.spec)
        grade = evidence.answer_grade if evidence is not None else None
        if grade is None and outcome.findings:
            grade = min_grade(*(f.grade for f in outcome.findings)).value
        integrity = RoundsTileIntegrity(
            grade=grade or EvidenceGrade.UNAVAILABLE.value,
            things_to_know=len(classified),
            things_to_know_caution=sum(1 for w in classified if w.severity == CAUTION),
            caveat_codes=list(dict.fromkeys(w.code for w in classified)),
            checks=len(evidence.probes) if evidence is not None else 0,
            is_bound=headline.is_bound if headline is not None else False,
            # "adjudication_incomplete" is the engine's own statement that a
            # terminal bucket is not yet a settled measurement. Read off the
            # warning rather than re-derived, so the tile and the answer
            # cannot disagree about whether a number has settled.
            provisional=any(w.code == "ADJUDICATION_INCOMPLETE" for w in classified),
        )
        # A tile whose LABEL names one subject and whose VALUE is another
        # subject's must be impossible by construction, not merely unlikely.
        # It shipped, it was certified `grade: direct`, and it is what gated
        # round 7 — so the check runs on every payload build rather than in
        # a test that only covers the paths somebody thought of.
        _assert_subject_matches_label(pin, headline)
        return RoundsTilePayload(
            pin_id=pin.id,
            label=pin.label,
            presentation=pin.presentation,  # type: ignore[arg-type]
            status="ok",
            watermark_id=watermark.id,
            newest_data_date=watermark.newest_data_date,
            evaluated_at=datetime.now(UTC),
            # The dates this load actually measured, off the turn's own §7.2
            # header — so a reader can tell a moving period from a re-measured
            # one without re-resolving the window themselves.
            window_start=outcome.header.window_start if outcome.header is not None else None,
            window_end=outcome.header.window_end if outcome.header is not None else None,
            investigation_id=outcome.investigation.id,
            headline_referent=headline.referent if headline is not None else None,
            headline_title=headline.title if headline is not None else "",
            headline_statement=headline.statement if headline is not None else "",
            headline_subject=(dict(headline.subject) if headline is not None else {}),
            headline_subject_label=headline.subject_label if headline is not None else "",
            value_text=headline.text if headline is not None else "",
            value=float(headline.value) if headline is not None else None,
            unit=headline.unit if headline is not None else None,
            metric_id=headline.metric_id if headline is not None else None,
            integrity=integrity,
            warnings=warnings,
            warnings_v2=classified,
            findings=[
                finding_payload(f, outcome.benchmarks, self._components.metric_display)
                for f in outcome.findings
            ],
            delta=self._delta(
                pin,
                headline,
                prior,
                (
                    (outcome.header.window_start, outcome.header.window_end)
                    if outcome.header is not None
                    else None
                ),
            ),
        )

    def _headline(self, outcome: TurnOutcome, spec: TypedInvestigationSpec) -> _Headline | None:
        """The tile's number: the first finding's value for the watched metric.

        Read off the FINDING rather than the frame, so a tile shows exactly
        what the answer published — including the ``≤`` a suppressed
        numerator earned it (:func:`bound_text`'s rule, applied here through
        the finding's own ``__is_bound`` value rather than re-derived).

        "The first finding" is a RANK on a breakdown, so the headline also
        carries WHICH CELL it came from. Every caller that compares two
        headlines needs it: without it, "up 3.6 points" was published for a
        payer that had fallen 6.6, because the two loads' first findings
        were two different payers (round-7 FN-2).
        """
        pack = self._components.pack_port
        for finding in outcome.findings:
            candidates = [ref.id for ref in finding.metric_refs]
            preferred = [m for m in spec.metric_ids if m in candidates] + candidates
            for metric_id in preferred:
                value = _named_value(finding, metric_id)
                if value is None:
                    continue
                contract = pack.metric(metric_id)
                unit = getattr(contract, "unit", None)
                unit_str = None if unit is None else str(unit)
                bounded = _named_value(finding, f"{metric_id}__is_bound") is not None
                text = format_value(value, unit_str)
                subject = _subject_of(
                    outcome.referents, finding.referent.value, spec.dimensions
                )
                return _Headline(
                    referent=finding.referent.value,
                    title=finding.title,
                    statement=finding.statement,
                    metric_id=metric_id,
                    value=value,
                    unit=unit_str,
                    text=f"≤ {text}" if bounded else text,
                    is_bound=bounded,
                    subject=subject,
                    subject_label=_cell_phrase(subject, pack),
                )
        return None

    def _delta(
        self,
        pin: RoundsPin,
        headline: _Headline | None,
        prior: RoundsTilePayload | None,
        window: tuple[date, date] | None = None,
    ) -> RoundsDeltaPayload:
        """Movement since the PRIOR load, gated by the governed materiality
        content and by this watch's own threshold.

        Always a payload, never ``None``. A tile with no prior used to send
        nothing, and the renderer draws nothing for nothing — so a watch
        that had never been compared looked exactly like a watch that had
        not moved, on nine tiles out of twelve (round-7 FN-12). Absence is
        read as absence only if something says so.
        """
        subject_label = headline.subject_label if headline is not None else ""
        if prior is None:
            return _delta_payload(
                prior_watermark_id="",
                prior_value=None,
                current=headline.value if headline is not None else None,
                unit=headline.unit if headline is not None else None,
                verdict=MaterialityVerdict(
                    False,
                    "first_reading",
                    "first reading — this load set the baseline and there is nothing behind "
                    "it to compare against yet",
                ),
                comparable=False,
                not_comparable_reason="first reading — baseline set at this load, with no "
                "earlier evaluation of this watch to compare against",
                reference="prior_load",
                subject_label=subject_label,
            )
        prior_value = _decimal(prior.value)
        current = headline.value if headline is not None else None
        unit = headline.unit if headline is not None else prior.unit
        reason = _not_comparable_reason(pin, prior, headline)
        verdict = (
            assess_movement(
                unit=unit,
                prior=prior_value,
                current=current,
                policy=self.policy.materiality,
                watch=pin.watch,
            )
            if reason is None
            else MaterialityVerdict(False, "not_comparable", reason)
        )
        return _delta_payload(
            prior_watermark_id=prior.watermark_id,
            prior_value=prior_value,
            current=current,
            unit=unit,
            verdict=verdict,
            comparable=reason is None,
            not_comparable_reason=reason,
            reference="prior_load",
            # Did the window actually move? A relative window usually does
            # and sometimes does not (two nightly loads inside one month),
            # and the difference decides whether this delta is a movement or
            # data settling.
            same_window=(
                window is not None
                and prior.window_start is not None
                and (prior.window_start, prior.window_end) == window
            ),
            subject_label=subject_label,
            prior_subject_label=prior.headline_subject_label,
        )

    async def _baseline_delta(
        self, pin: RoundsPin, tile: RoundsTilePayload
    ) -> RoundsDeltaPayload | None:
        """Movement since the watch's CREATION-LOAD baseline.

        Published only when it says something the prior-load delta does not:
        a tile that has drifted four points since it was created while
        moving 0.2 overnight is telling two true stories, and a surface
        showing only the overnight one would hide the reason the watch
        exists. When the baseline IS the load being evaluated there is
        nothing to say, and nothing is published.

        Held to the SAME two tests the prior-load delta is held to, because
        it was held to neither (round-7 FN-2, FN-9): the baseline load's own
        stored tile says which cell it measured and which dates it resolved,
        so a baseline delta across a rank flip is refused with the reason,
        and one across two different windows says so instead of presenting
        window movement as drift. The baseline load's tile is the authority
        on both — the pin stores only a number, a unit and a watermark id.
        """
        if pin.baseline_value is None or tile.value is None:
            return None
        if pin.baseline_watermark_id == tile.watermark_id:
            return None
        if pin.baseline_unit != tile.unit:
            was = pin.baseline_unit or "an unknown unit"
            now = tile.unit or "an unknown unit"
            return _delta_payload(
                prior_watermark_id=pin.baseline_watermark_id or "",
                prior_value=pin.baseline_value,
                current=_decimal(tile.value),
                unit=tile.unit,
                verdict=MaterialityVerdict(
                    False,
                    "not_comparable",
                    f"this watch's baseline was measured in {was} and it now measures {now}, "
                    "so the two are not two measurements of one thing",
                ),
                comparable=False,
                not_comparable_reason="the metric's declared unit changed since the baseline "
                "was captured",
                reference="baseline",
                subject_label=tile.headline_subject_label,
            )
        baseline_tile = await self._baseline_tile(pin)
        reason = _baseline_not_comparable_reason(pin, baseline_tile, tile)
        verdict = (
            assess_movement(
                unit=tile.unit,
                prior=pin.baseline_value,
                current=_decimal(tile.value),
                policy=self.policy.materiality,
                watch=pin.watch,
            )
            if reason is None
            else MaterialityVerdict(False, "not_comparable", reason)
        )
        return _delta_payload(
            prior_watermark_id=pin.baseline_watermark_id or "",
            prior_value=pin.baseline_value,
            current=_decimal(tile.value),
            unit=tile.unit,
            verdict=verdict,
            comparable=reason is None,
            not_comparable_reason=reason,
            reference="baseline",
            # Measured, exactly as the prior-load delta measures it. It
            # defaulted to False and was never computed, while the sentence
            # it produced sat immediately above a run-out note gated on the
            # OTHER delta's window equality — so a reader attached the
            # equality claim to the wrong sentence (round-7 FN-9).
            same_window=(
                baseline_tile is not None
                and baseline_tile.window_start is not None
                and (baseline_tile.window_start, baseline_tile.window_end)
                == (tile.window_start, tile.window_end)
            ),
            subject_label=tile.headline_subject_label,
            prior_subject_label=(
                baseline_tile.headline_subject_label if baseline_tile is not None else ""
            ),
        )

    async def _baseline_tile(self, pin: RoundsPin) -> RoundsTilePayload | None:
        """This watch's stored evaluation at its baseline load, if there is one."""
        if not pin.baseline_watermark_id:
            return None
        stored = await self._components.rounds_results.get(
            pin.id, pin.baseline_watermark_id
        )
        if stored is None:
            return None
        return RoundsTilePayload.model_validate(stored.payload)

    # ------------------------------------------------------------ the surface

    async def rounds(self, principal: Principal) -> RoundsResponse:
        """Every active watch, evaluated at the newest load."""
        return await self.rounds_at(
            principal, await self._components.open_session.newest_watermark()
        )

    async def rounds_at(
        self, principal: Principal, watermark: DataWatermark
    ) -> RoundsResponse:
        """The surface AT a named load.

        The explicit-watermark form exists because a load-over-load product
        has to be testable across loads: the simulated-load suite drives
        wm_001 → wm_002 → wm_003 through this, which is the same code the
        newest-load route runs. A seam that tests exercise and production
        does not is a seam that proves nothing.
        """
        await self.evaluate_load(principal.tenant, watermark)
        pins = await self._components.rounds_pins.list_for_tenant(principal.tenant)
        tiles: list[RoundsTilePayload] = []
        for pin in pins:
            stored = await self._components.rounds_results.get(pin.id, watermark.id)
            if stored is not None:
                tiles.append(RoundsTilePayload.model_validate(stored.payload))
        prior = await self._prior_load(principal.tenant, watermark)
        warnings = _rounds_warnings(self.policy)
        return RoundsResponse(
            tenant=principal.tenant,
            watermark_id=watermark.id,
            newest_data_date=watermark.newest_data_date,
            prior_watermark_id=prior.watermark_id if prior is not None else None,
            tiles=tiles,
            warnings=warnings,
            warnings_v2=structured_warnings(warnings),
        )

    async def _prior_load(
        self, tenant: str, watermark: DataWatermark
    ) -> RoundsLoad | None:
        for load in await self._components.rounds_loads.list_for_tenant(tenant, limit=12):
            if _utc(load.watermark_loaded_at) < _utc(watermark.loaded_at):
                return load
        return None

    # ------------------------------------------------------------- the brief

    async def brief(
        self, principal: Principal, *, since: str | None = None
    ) -> RoundsBriefResponse:
        """What changed at this load: gated, capped, counted and provenanced."""
        return await self.brief_at(
            principal,
            await self._components.open_session.newest_watermark(),
            since=since,
        )

    async def brief_at(
        self,
        principal: Principal,
        watermark: DataWatermark,
        *,
        since: str | None = None,
    ) -> RoundsBriefResponse:
        """The brief FOR a named load. See :meth:`rounds_at` for why this
        seam exists: the simulated-load suite drives every watermark
        transition through the same code the newest-load route runs."""
        tenant = principal.tenant
        load = await self.evaluate_load(tenant, watermark)
        prior = await self._named_prior_load(tenant, watermark, since)

        pins = {
            pin.id: pin
            for pin in await self._components.rounds_pins.list_for_tenant(
                tenant, include_archived=True
            )
        }
        current_leads = _leads_of(load)
        prior_leads = _leads_of(prior) if prior is not None else {}

        entries: list[RoundsBriefEntry] = []
        new_lead_skipped = 0
        self_resolved_skipped = 0
        if prior is not None:
            new_entries, new_lead_skipped = self._new_lead_entries(
                load, prior_leads, current_leads
            )
            resolved_entries, self_resolved_skipped = self._self_resolved_entries(
                load,
                prior_leads,
                current_leads,
                frozenset(
                    lead.anomaly_id
                    for lead in (await self._components.rounds_leads.list_for_tenant(tenant))
                    if lead.status in ("resolved_claimed", "resolved_confirmed", "regressed")
                ),
            )
            entries.extend(new_entries)
            entries.extend(resolved_entries)
        # ONE reference frame for the whole brief. The lead census diffed
        # against the load the caller named and the watch movements diffed
        # against last night's, so `since=wm_001` produced a headline about
        # wm_001..wm_003 containing an entry measured wm_002..wm_003 — and
        # "I was away for a week" is the highest-value read of a proactive
        # surface (round-7 FN-9).
        census = await self._movement_entries(tenant, watermark, pins, prior)
        entries.extend(census.entries)
        entries.extend(self._verification_entries(load, watermark))

        total = len(entries)
        published, dropped_by_kind = _cap(entries, self.policy)
        immaterial = RoundsImmaterialSummary(
            pin_movements=census.immaterial,
            new_leads=new_lead_skipped,
            self_resolved=self_resolved_skipped,
            entries_withheld_by_cap=total - len(published),
            not_yet_comparable=census.not_yet_comparable,
            unavailable=census.unavailable,
            entries_withheld_by_kind=dropped_by_kind,
        )
        immaterial = immaterial.model_copy(update={"note": _immaterial_note(immaterial)})
        status = (
            "first_load"
            if prior is None
            else ("material_changes" if published else "nothing_material")
        )
        fatigue = await self._fatigue(tenant, watermark, census.below_gate)
        warnings = _rounds_warnings(self.policy)
        prior_data_date = _data_date_of(prior)
        return RoundsBriefResponse(
            tenant=tenant,
            status=status,  # type: ignore[arg-type]
            watermark_id=watermark.id,
            newest_data_date=watermark.newest_data_date,
            prior_watermark_id=prior.watermark_id if prior is not None else None,
            prior_newest_data_date=prior_data_date,
            headline=_headline_sentence(
                status=status,
                newest_data_date=watermark.newest_data_date,
                prior_newest_data_date=prior_data_date,
                has_prior=prior is not None,
                entries=published,
                pins_evaluated=census.evaluated,
                leads=len(current_leads),
            ),
            entries=published,
            entries_total=total,
            immaterial=immaterial,
            fatigue=fatigue,
            materiality=self.policy.payload(),
            pins_evaluated=census.evaluated,
            leads_verified=int(load.payload.get("leads_verified", 0) or 0),
            generated_at=datetime.now(UTC),
            warnings=warnings,
            warnings_v2=structured_warnings(warnings),
        )

    async def _named_prior_load(
        self, tenant: str, watermark: DataWatermark, since: str | None
    ) -> RoundsLoad | None:
        """The load this brief diffs against.

        ``since`` names it explicitly (the client knows which brief the
        analyst last read); absent, it is the newest evaluated load before
        this one. A ``since`` naming a load that was never evaluated is a
        404 rather than a silent fall-back — a brief that quietly diffed
        against a different load than the one it was asked for would
        misreport every entry on it.
        """
        if since is None:
            return await self._prior_load(tenant, watermark)
        if since == watermark.id:
            raise PolicyDeniedError(
                f"`since={since}` names the load this brief is FOR, so there is nothing to "
                "diff against; omit it to diff against the previous evaluated load",
                details={"since": since, "watermark_id": watermark.id},
            )
        stored = await self._components.rounds_loads.get(tenant, since)
        if stored is None:
            raise RoundsNotFoundError(
                f"no Rounds evaluation is recorded for load {since!r}, so this brief has "
                "nothing to diff against; the loads this tenant has evaluated are the only "
                "ones a brief can be taken since",
                details={"since": since, "tenant": tenant},
            )
        return stored

    def _new_lead_entries(
        self,
        load: RoundsLoad,
        prior_leads: Mapping[str, Mapping[str, Any]],
        current_leads: Mapping[str, Mapping[str, Any]],
    ) -> tuple[list[RoundsBriefEntry], int]:
        out: list[RoundsBriefEntry] = []
        skipped = 0
        for anomaly_id, row in current_leads.items():
            if anomaly_id in prior_leads:
                continue
            verdict = assess_new_lead(
                impact_cents=int(row.get("ranked_impact_cents", 0) or 0),
                lane=str(row.get("lane", "value")),
                policy=self.policy.materiality,
            )
            if not verdict.material:
                skipped += 1
                continue
            out.append(
                RoundsBriefEntry(
                    kind="new_lead",
                    title=str(row.get("title", anomaly_id)),
                    # The money is said ONCE. It was said three times in one
                    # entry — twice in this sentence and again on the meta
                    # row above it, rounded differently (round-7 FN-8).
                    statement=(
                        f"New at this load: {anomaly_id} — {row.get('title', '')}, "
                        f"{magnitude(int(row.get('ranked_impact_cents', 0) or 0), 'money_cents')}"
                        f" on the {row.get('ranked_on', 'detector')}'s figure. "
                        f"{_sentence(verdict.note)}"
                    ),
                    anomaly_id=anomaly_id,
                    category=row.get("category"),
                    lane=row.get("lane"),
                    impact_cents=int(row.get("ranked_impact_cents", 0) or 0),
                    time_to_impact=_time_to_impact_payload(row),
                    lead_status=str(row.get("lead_status", "open")),
                    provenance=RoundsProvenancePayload(
                        source="detection_feed",
                        watermark_id=load.watermark_id,
                        evaluated_at=load.evaluated_at,
                        formula_version=PRIORITY_FORMULA_VERSION,
                        method="present in the detection feed at this load and absent at the "
                        "prior one",
                    ),
                )
            )
        return out, skipped

    def _self_resolved_entries(
        self,
        load: RoundsLoad,
        prior_leads: Mapping[str, Mapping[str, Any]],
        current_leads: Mapping[str, Mapping[str, Any]],
        claimed: frozenset[str],
    ) -> tuple[list[RoundsBriefEntry], int]:
        """Leads that left the feed with nobody claiming them.

        ``claimed`` comes from the LIFECYCLE STORE, not from the prior
        load's census: the census records what the feed said at that load,
        and a claim made after it was written would be invisible to it. The
        store is the authority on whether a human said they fixed something,
        and reading the snapshot instead published one lead twice — once as
        a confirmation and once as having fixed itself.
        """
        out: list[RoundsBriefEntry] = []
        skipped = 0
        for anomaly_id, row in prior_leads.items():
            if anomaly_id in current_leads or anomaly_id in claimed:
                # A lead somebody CLAIMED and that then left the feed is a
                # confirmation, not a self-resolution: reporting both would
                # tell one fact twice, and the second telling would credit
                # nobody for work somebody did.
                continue
            impact = int(row.get("ranked_impact_cents", 0) or 0)
            verdict = assess_self_resolved(
                impact_cents=impact, policy=self.policy.materiality
            )
            if not verdict.material:
                skipped += 1
                continue
            out.append(
                RoundsBriefEntry(
                    kind="self_resolved",
                    title=str(row.get("title", anomaly_id)),
                    statement=(
                        f"Gone without being worked: {anomaly_id} — {row.get('title', '')} "
                        f"({magnitude(impact, 'money_cents')}) was in the detection feed at "
                        f"the prior load and is not in this one. Nobody claimed it, so this "
                        "is the detector's rule no longer firing rather than a fix this "
                        "platform verified."
                    ),
                    anomaly_id=anomaly_id,
                    category=row.get("category"),
                    lane=row.get("lane"),
                    impact_cents=impact,
                    lead_status=str(row.get("lead_status", "open")),
                    provenance=RoundsProvenancePayload(
                        source="detection_feed",
                        watermark_id=load.watermark_id,
                        prior_watermark_id=str(row.get("watermark_id") or "") or None,
                        evaluated_at=load.evaluated_at,
                        formula_version=PRIORITY_FORMULA_VERSION,
                        method="absent from the detection feed at this load and present at "
                        "the prior one, with no resolution claimed",
                    ),
                )
            )
        return out, skipped

    async def _movement_entries(
        self,
        tenant: str,
        watermark: DataWatermark,
        pins: Mapping[str, RoundsPin],
        prior_load: RoundsLoad | None,
    ) -> _WatchCensus:
        """Every active watch, diffed against the load this brief is FOR.

        The census closes: every watch lands in exactly one of briefed,
        immaterial, not-yet-comparable or unavailable, so
        ``pins_evaluated == briefed + immaterial + not_yet_comparable +
        unavailable`` is an identity rather than a hope. It did not: sixteen
        of eighteen watches at one live load were neither briefed nor
        counted as held back, on a surface whose stated discipline is
        "withheld visibly, never silently" (round-7 FN-12).
        """
        out: list[RoundsBriefEntry] = []
        census = _WatchCensus(entries=out)
        prior_watermark = prior_load.watermark_id if prior_load is not None else None
        prior_date = _data_date_of(prior_load)
        for pin in pins.values():
            if pin.archived_at is not None:
                continue
            census.evaluated += 1
            stored = await self._components.rounds_results.get(pin.id, watermark.id)
            if stored is None:
                census.unavailable += 1
                continue
            tile = RoundsTilePayload.model_validate(stored.payload)
            if tile.status != "ok" or tile.value is None:
                census.unavailable += 1
                continue
            delta = await self._delta_against(pin, tile, prior_watermark)
            if delta is None:
                census.not_yet_comparable += 1
                continue
            if delta.below_governed_gate:
                census.below_gate += 1
            # A rank flip is not a movement and never carries a delta: it is
            # the fact that the worst cell is now a different cell, which is
            # the headline the fabricated movement was standing in for.
            if not delta.comparable and delta.prior_subject_label and delta.subject_label:
                out.append(
                    self._rank_flip_entry(pin, tile, delta, prior_date)
                )
                continue
            if not delta.comparable:
                census.not_yet_comparable += 1
                continue
            if not delta.material:
                census.immaterial += 1
                continue
            baseline = tile.baseline_delta if _adds_something(tile) else None
            out.append(
                RoundsBriefEntry(
                    kind="pin_movement",
                    title=pin.label,
                    statement=_movement_sentence(pin, tile, delta, baseline, prior_date),
                    pin_id=pin.id,
                    investigation_id=tile.investigation_id,
                    delta=delta,
                    baseline_delta=baseline,
                    integrity=tile.integrity,
                    provenance=RoundsProvenancePayload(
                        source="pinned_spec",
                        watermark_id=tile.watermark_id,
                        prior_watermark_id=delta.prior_watermark_id or None,
                        evaluated_at=tile.evaluated_at,
                        method="this watch's stored typed spec, re-run at this load through "
                        "the ordinary governed pipeline (no model call), and diffed against "
                        "the same spec's result at the load this brief was taken since",
                    ),
                )
            )
        return census

    async def _delta_against(
        self, pin: RoundsPin, tile: RoundsTilePayload, prior_watermark_id: str | None
    ) -> RoundsDeltaPayload | None:
        """This tile's movement since the NAMED load, not since last night.

        ``None`` when there is nothing to compare against at that load — a
        first reading, or a watch created after it. The tile's own stored
        delta is reused when it already measures the right pair, so the
        common case (``since`` absent) costs no extra read.
        """
        if prior_watermark_id is None:
            return None
        if tile.delta is not None and tile.delta.prior_watermark_id == prior_watermark_id:
            return tile.delta if tile.delta.prior_value is not None else None
        stored = await self._components.rounds_results.get(pin.id, prior_watermark_id)
        if stored is None:
            return None
        prior_tile = RoundsTilePayload.model_validate(stored.payload)
        if prior_tile.status != "ok" or prior_tile.value is None:
            return None
        reason = _not_comparable_reason(pin, prior_tile, _headline_of(tile))
        prior_value = _decimal(prior_tile.value)
        current = _decimal(tile.value)
        verdict = (
            assess_movement(
                unit=tile.unit,
                prior=prior_value,
                current=current,
                policy=self.policy.materiality,
                watch=pin.watch,
            )
            if reason is None
            else MaterialityVerdict(False, "not_comparable", reason)
        )
        return _delta_payload(
            prior_watermark_id=prior_tile.watermark_id,
            prior_value=prior_value,
            current=current,
            unit=tile.unit,
            verdict=verdict,
            comparable=reason is None,
            not_comparable_reason=reason,
            reference="prior_load",
            same_window=(
                prior_tile.window_start is not None
                and (prior_tile.window_start, prior_tile.window_end)
                == (tile.window_start, tile.window_end)
            ),
            subject_label=tile.headline_subject_label,
            prior_subject_label=prior_tile.headline_subject_label,
        )

    def _rank_flip_entry(
        self,
        pin: RoundsPin,
        tile: RoundsTilePayload,
        delta: RoundsDeltaPayload,
        prior_date: date | None,
    ) -> RoundsBriefEntry:
        since = f"Since {_load_phrase(prior_date)}: " if prior_date is not None else ""
        return RoundsBriefEntry(
            kind="rank_flip",
            title=pin.label,
            statement=(
                f"{since}{delta.subject_label} overtook {delta.prior_subject_label} at the top "
                f"of {pin.label}, now at {tile.value_text}. This is a change of subject and "
                "not a movement, so no change is reported between them — they are two "
                "different cells."
            ),
            pin_id=pin.id,
            investigation_id=tile.investigation_id,
            integrity=tile.integrity,
            provenance=RoundsProvenancePayload(
                source="pinned_spec",
                watermark_id=tile.watermark_id,
                prior_watermark_id=delta.prior_watermark_id or None,
                evaluated_at=tile.evaluated_at,
                method="this watch's stored typed spec, re-run at both loads; the cell it "
                "ranks first is not the cell it ranked first before",
            ),
        )

    def _verification_entries(
        self, load: RoundsLoad, watermark: DataWatermark
    ) -> list[RoundsBriefEntry]:
        out: list[RoundsBriefEntry] = []
        for row in load.payload.get("verifications", []) or []:
            status = str(row.get("status", ""))
            if status not in ("resolved_confirmed", "regressed"):
                continue
            out.append(
                RoundsBriefEntry(
                    kind=(
                        "resolution_confirmed"
                        if status == "resolved_confirmed"
                        else "resolution_regressed"
                    ),
                    title=str(row.get("title", row.get("anomaly_id", ""))),
                    statement=str(row.get("note", "")),
                    anomaly_id=str(row.get("anomaly_id", "")),
                    impact_cents=row.get("impact_cents"),
                    lead_status=status,
                    provenance=RoundsProvenancePayload(
                        source="pinned_spec",
                        watermark_id=watermark.id,
                        evaluated_at=load.evaluated_at,
                        method="the lead's own drill spec, re-derived by this platform at "
                        "each load since resolution was claimed, against the exposure "
                        "measured at the claim load",
                    ),
                )
            )
        return out

    async def _fatigue(
        self, tenant: str, watermark: DataWatermark, below_gate: int
    ) -> RoundsFatigueAdvisory:
        """The brief noticing that somebody's own thresholds are too loose.

        Counted across loads from the stored census, so the advisory fires
        on a PATTERN rather than on one noisy morning — and never more than
        once per load, because an advisory that nagged would be the fatigue
        it is warning about.
        """
        policy = self.policy.materiality.fatigue
        if not policy.enabled:
            return RoundsFatigueAdvisory()
        streak = 1 if below_gate else 0
        if streak:
            for load in await self._components.rounds_loads.list_for_tenant(tenant, limit=12):
                if _utc(load.watermark_loaded_at) >= _utc(watermark.loaded_at):
                    continue
                if int(load.payload.get("watches_below_governed_gate", 0) or 0) > 0:
                    streak += 1
                else:
                    break
        active = streak >= policy.consecutive_loads
        return RoundsFatigueAdvisory(
            active=active,
            watches_below_governed_gate=below_gate,
            consecutive_loads=streak,
            loads_required=policy.consecutive_loads,
            message=(
                policy.message.format(count=below_gate, ordinal=_ordinal(streak))
                if active
                else ""
            ),
        )

    # ------------------------------------------------------ lead lifecycle

    async def lead_states(self, tenant: str) -> dict[str, RoundsLead]:
        """Every lead status this tenant holds, for decorating cards."""
        return {
            lead.anomaly_id: lead
            for lead in await self._components.rounds_leads.list_for_tenant(tenant)
        }

    async def patch_lead(
        self,
        principal: Principal,
        anomaly_id: str,
        request: RoundsLeadPatchRequest,
        *,
        watermark: DataWatermark | None = None,
    ) -> RoundsLeadPayload:
        """Move one lead along its lifecycle.

        Only the four human-settable statuses are accepted: confirmation is
        a measurement across two loads, and a lead that could be confirmed
        by assertion would make the whole verification path decorative.

        ``watermark`` names the load the claim is made AT — the newest one
        for a real request, and an explicit one for the simulated-load
        suite, which has to be able to claim at wm_002 and confirm at
        wm_003 through this same code.
        """
        tenant = principal.tenant
        if request.status not in LEAD_STATUSES_HUMAN_SETTABLE:
            raise PolicyDeniedError(
                f"{request.status!r} is a verdict this platform reaches from data, not a "
                "status a person may set: claim the resolution and the next loads confirm it "
                "or refuse it",
                details={"anomaly_id": anomaly_id, "status": request.status},
            )
        if watermark is None:
            watermark = await self._components.open_session.newest_watermark()
        portfolio = await self._portfolio_for(tenant, watermark)
        card = next((c for c in portfolio.items if c.anomaly_id == anomaly_id), None)
        if card is None:
            raise RoundsNotFoundError(
                f"{anomaly_id!r} is not in the detection feed at watermark {watermark.id}; a "
                "lead that is not detected cannot have its status changed",
                details={"anomaly_id": anomaly_id, "watermark_id": watermark.id},
            )
        existing = await self._components.rounds_leads.get(tenant, anomaly_id)
        previous = existing.status if existing is not None else "open"
        now = datetime.now(UTC)
        baseline_cents: int | None = existing.baseline_cents if existing else None
        baseline_basis = existing.baseline_basis if existing else ""
        claimed_at = existing.claimed_at_watermark if existing else None
        confirming: tuple[str, ...] = existing.confirming_watermarks if existing else ()
        verification_note = existing.verification_note if existing else ""

        if request.status == "resolved_claimed":
            # The baseline is captured at the CLAIM, from the platform's own
            # re-derivation of the lead's drill — not the detector's figure.
            # Verification then measures like against like: this platform's
            # number at the claim load against this platform's number now.
            baseline_cents, baseline_basis = await self._claim_baseline(card, watermark)
            claimed_at = watermark.id
            confirming = ()
            verification_note = (
                "resolution claimed; this platform will re-run the lead's own drill at each "
                f"load and confirm only after {self.policy.resolution.consecutive_loads_required}"
                " consecutive loads verify it"
            )
        elif request.status != previous:
            # Moving off a claim discards the verification in progress: the
            # streak measured a claim that no longer stands.
            claimed_at = None
            baseline_cents = None
            baseline_basis = ""
            confirming = ()
            verification_note = ""

        lead = RoundsLead(
            tenant=tenant,
            anomaly_id=anomaly_id,
            status=request.status,
            updated_at=now,
            note=request.note,
            claimed_at_watermark=claimed_at,
            baseline_cents=baseline_cents,
            baseline_basis=baseline_basis,
            confirming_watermarks=confirming,
            verification_note=verification_note,
            history=(
                *(existing.history if existing is not None else ()),
                {
                    "at": now.isoformat(),
                    "watermark_id": watermark.id,
                    "from": previous,
                    "to": request.status,
                    "by": principal.subject or principal.tenant,
                    "note": request.note,
                },
            ),
        )
        await self._components.rounds_leads.put(lead)
        logger.info(
            "rounds lead %s for tenant %s: %s -> %s", anomaly_id, tenant, previous, request.status
        )
        return lead_payload(lead, self.policy.resolution.consecutive_loads_required)

    async def get_lead(self, principal: Principal, anomaly_id: str) -> RoundsLeadPayload:
        lead = await self._components.rounds_leads.get(principal.tenant, anomaly_id)
        if lead is None:
            raise RoundsNotFoundError(
                f"no lifecycle state is recorded for lead {anomaly_id!r}",
                details={"anomaly_id": anomaly_id},
            )
        return lead_payload(lead, self.policy.resolution.consecutive_loads_required)

    async def _claim_baseline(
        self, card: AnomalyCard, watermark: DataWatermark
    ) -> tuple[int | None, str]:
        """This platform's own exposure for a lead at the load it was claimed.

        ``None`` with a stated basis when the drill cannot be re-derived —
        an undrillable card, or a contract that produces no money column. A
        lead with no measurable baseline is never auto-confirmed by
        measurement; the detector-cleared basis is the only one left to it,
        and the lead says so.
        """
        if not card.drillable:
            return None, (
                "this card cannot be investigated at this catalog and pack version, so this "
                "platform has no figure of its own to measure the fix against; confirmation "
                "can only come from the lead leaving the detection feed"
            )
        rederived = await self._components.rederive_impact(card.drill_spec, watermark)
        if rederived.cents is None:
            return None, (
                "this platform could not re-derive an exposure for the lead's drill at the "
                f"claim load ({rederived.unavailable_reason or 'no reason recorded'}), so "
                "confirmation can only come from the lead leaving the detection feed"
            )
        return rederived.cents, (
            f"this platform's own re-derivation of the lead's drill "
            f"({rederived.measure_id or 'metric'}) at the claim load {watermark.id}"
        )

    async def _verify_claimed_leads(
        self, tenant: str, watermark: DataWatermark, portfolio: PortfolioResponse
    ) -> list[dict[str, Any]]:
        """Confirm, refuse, or regress every claimed resolution at this load.

        Two governed bases, either of which counts as a load verifying the
        claim: the lead has left the detection feed, or this platform's own
        re-derivation of its drill has fallen by the governed fraction. Where
        neither can be evaluated the lead stays claimed with a stated
        reason — which is the honest outcome and the whole point of having a
        verification path rather than a checkbox.
        """
        resolution = self.policy.resolution
        cards = {card.anomaly_id: card for card in portfolio.items}
        verifications: list[dict[str, Any]] = []
        for lead in await self._components.rounds_leads.list_for_tenant(tenant):
            if lead.status not in ("resolved_claimed", "regressed"):
                continue
            if lead.claimed_at_watermark == watermark.id:
                continue  # the claim load is not evidence for its own claim
            if watermark.id in lead.confirming_watermarks:
                continue  # already counted
            outcome = await self._verify_one(
                lead, cards.get(lead.anomaly_id), watermark, resolution
            )
            await self._components.rounds_leads.put(outcome.lead)
            if outcome.entry is not None:
                verifications.append(outcome.entry)
        return verifications

    async def _verify_one(
        self,
        lead: RoundsLead,
        card: AnomalyCard | None,
        watermark: DataWatermark,
        resolution: ResolutionPolicy,
    ) -> _Verification:
        required = resolution.consecutive_loads_required
        confirming = (*lead.confirming_watermarks, watermark.id)
        # The loads that verified it, named. A single-load span is written as
        # one id rather than "wm_003-wm_003", which reads like a bug.
        span = (
            f"{confirming[0]}-{watermark.id}" if len(confirming) > 1 else watermark.id
        )

        if card is None:
            note = (
                f"{lead.anomaly_id} is no longer in the detection feed at {watermark.id}: the "
                "detector's own rule has stopped firing for this cell"
            )
            return self._advance(lead, confirming, required, note, span, watermark)

        current: int | None = None
        if lead.baseline_cents is not None and card.drillable:
            rederived = await self._components.rederive_impact(card.drill_spec, watermark)
            current = rederived.cents
        if lead.baseline_cents is None or current is None:
            basis = lead.baseline_basis or "no baseline was captured"
            held = replace(
                lead,
                verification_note=(
                    f"still detected at {watermark.id}, and this platform has no comparable "
                    f"figure to measure the fix against ({basis}). The claim stands "
                    "unconfirmed rather than being confirmed on an assertion."
                ),
                updated_at=datetime.now(UTC),
            )
            return _Verification(held, None)

        baseline = lead.baseline_cents
        if baseline == 0:
            reduction = Decimal(1) if current == 0 else Decimal(0)
        else:
            reduction = Decimal(baseline - current) / Decimal(abs(baseline))
        if reduction >= resolution.measured_reduction_fraction:
            note = (
                f"{lead.anomaly_id} is back within tolerance at {watermark.id}: this "
                f"platform's re-derived exposure fell from {magnitude(baseline, 'money_cents')} "
                f"at the claim load to {magnitude(current, 'money_cents')} "
                f"({float(reduction):.0%} down, against a governed threshold of "
                f"{float(resolution.measured_reduction_fraction):.0%})"
            )
            return self._advance(lead, confirming, required, note, span, watermark)
        if -reduction >= resolution.regression_increase_fraction:
            regressed = replace(
                lead,
                status="regressed",
                confirming_watermarks=(),
                updated_at=datetime.now(UTC),
                verification_note=(
                    f"Regressed: {lead.anomaly_id} moved the wrong way. This platform's "
                    f"re-derived exposure rose from {magnitude(baseline, 'money_cents')} at "
                    f"the claim load to {magnitude(current, 'money_cents')} at {watermark.id} "
                    f"({float(-reduction):.0%} up, against a governed regression threshold of "
                    f"{float(resolution.regression_increase_fraction):.0%}). The claimed fix "
                    "did not hold."
                ),
            )
            return _Verification(
                regressed,
                {
                    "anomaly_id": lead.anomaly_id,
                    "status": "regressed",
                    "title": card.title,
                    "impact_cents": current,
                    "note": regressed.verification_note,
                },
            )
        held = replace(
            lead,
            confirming_watermarks=(),
            updated_at=datetime.now(UTC),
            verification_note=(
                f"still detected at {watermark.id}: this platform's re-derived exposure is "
                f"{magnitude(current, 'money_cents')} against {magnitude(baseline, 'money_cents')} "
                f"at the claim load ({float(reduction):.0%} down, short of the governed "
                f"{float(resolution.measured_reduction_fraction):.0%}). Not confirmed, and the "
                "streak restarts."
            ),
        )
        return _Verification(held, None)

    def _advance(
        self,
        lead: RoundsLead,
        confirming: tuple[str, ...],
        required: int,
        note: str,
        span: str,
        watermark: DataWatermark,
    ) -> _Verification:
        """One verifying load recorded; confirmed only once the streak is long
        enough. One load is a coincidence — a card can drop out of a single
        snapshot because a window moved — and confirming on it would publish
        "confirmed" for a problem that returns tomorrow."""
        if len(confirming) < required:
            return _Verification(
                replace(
                    lead,
                    confirming_watermarks=confirming,
                    updated_at=datetime.now(UTC),
                    verification_note=(
                        f"{note}. That is {len(confirming)} of the {required} consecutive "
                        "loads the governed rule requires before this platform will call it "
                        "confirmed."
                    ),
                ),
                None,
            )
        loads = "load" if required == 1 else "consecutive loads"
        sentence = f"Confirmed: {note}, for {required} {loads}, {span}."
        return _Verification(
            replace(
                lead,
                status="resolved_confirmed",
                confirming_watermarks=confirming,
                updated_at=datetime.now(UTC),
                verification_note=sentence,
            ),
            {
                "anomaly_id": lead.anomaly_id,
                "status": "resolved_confirmed",
                "title": lead.anomaly_id,
                "note": sentence,
            },
        )

    # ------------------------------------------------------------- the census

    async def _census(
        self,
        tenant: str,
        watermark: DataWatermark,
        portfolio: PortfolioResponse,
        pins: Sequence[RoundsPin],
        verifications: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """What the feed said, and what the watches did, at this load.

        Stored so the NEXT load can diff against it. Everything a brief
        needs about a prior load is here, because re-reading a warehouse
        snapshot to answer a question already answered is how a proactive
        surface becomes expensive enough to switch off.
        """
        leads = await self.lead_states(tenant)
        below_gate = 0
        for pin in pins:
            stored = await self._components.rounds_results.get(pin.id, watermark.id)
            if stored is None:
                continue
            delta = (stored.payload.get("delta") or {}) if isinstance(stored.payload, dict) else {}
            if delta.get("below_governed_gate"):
                below_gate += 1
        return {
            "leads": {
                card.anomaly_id: {
                    "title": card.title,
                    "category": card.category,
                    "lane": card.lane,
                    "impact_cents": card.impact_cents,
                    "ranked_impact_cents": card.ranked_impact_cents,
                    "ranked_on": card.ranked_on,
                    "lead_status": (
                        leads[card.anomaly_id].status if card.anomaly_id in leads else "open"
                    ),
                    "watermark_id": watermark.id,
                    "time_to_impact": (
                        card.time_to_impact.model_dump(mode="json")
                        if card.time_to_impact is not None
                        else None
                    ),
                }
                for card in portfolio.items
            },
            "verifications": [dict(entry) for entry in verifications],
            "leads_verified": len(verifications),
            "watches_below_governed_gate": below_gate,
            "pins_evaluated": len(pins),
            # So the NEXT brief can name this load the way a reader does —
            # "since the Aug 1 load" — instead of by the warehouse handle
            # (round-7 FN-8). Stored rather than re-resolved: the watermark
            # this census was written at is the only authority on it, and
            # looking it up later would be a second answer to a question
            # already answered.
            "newest_data_date": watermark.newest_data_date.isoformat()
            if watermark.newest_data_date is not None
            else None,
        }

    # ------------------------------------------------ lead decoration for cards

    async def decorate_cards(self, tenant: str, portfolio: PortfolioResponse) -> PortfolioResponse:
        """Ride lead statuses onto the portfolio's cards (additive fields).

        The rail renders the lifecycle from the same payload it already
        fetches, and a card and a brief entry cannot disagree about whether
        somebody is working a lead.
        """
        leads = await self.lead_states(tenant)
        if not leads:
            return portfolio
        items = []
        for card in portfolio.items:
            lead = leads.get(card.anomaly_id)
            if lead is None:
                items.append(card)
                continue
            items.append(
                card.model_copy(
                    update={
                        "lead_status": lead.status,
                        "lead_status_note": lead.verification_note or lead.note,
                        "lead_updated_at": lead.updated_at,
                    }
                )
            )
        return portfolio.model_copy(update={"items": items})


# ---------------------------------------------------------------------------
# small value objects and helpers


@dataclass(frozen=True, slots=True)
class _Headline:
    """The tile's number, with everything needed to render it honestly."""

    referent: str
    title: str
    statement: str
    metric_id: str
    value: Decimal
    unit: str | None
    text: str
    is_bound: bool
    #: WHICH CELL this number is about, as dimension members. Empty for a
    #: watch with no breakdown. Read off the evaluation's own referent
    #: registry entry rather than parsed out of a display title, because a
    #: title is prose and this decides whether two loads measured one thing.
    subject: tuple[tuple[str, str], ...] = ()
    subject_label: str = ""


@dataclass(slots=True)
class _WatchCensus:
    """Where every active watch landed in one brief.

    Mutable and passed around by one method on purpose: the four buckets
    have to sum to :attr:`evaluated` and keeping them in one object is what
    makes that checkable in one place rather than in four counters that
    drift apart.
    """

    entries: list[RoundsBriefEntry]
    evaluated: int = 0
    immaterial: int = 0
    not_yet_comparable: int = 0
    unavailable: int = 0
    below_gate: int = 0


@dataclass(frozen=True, slots=True)
class _Verification:
    """One lead's verification outcome at one load: the updated lead, and
    the brief entry it earned (``None`` when it earned none — an
    unconfirmed claim is a state change, not news)."""

    lead: RoundsLead
    entry: dict[str, Any] | None


def _named_value(finding: Finding, name: str) -> Decimal | None:
    for key, value in finding.values:
        if key != name:
            continue
        if value is None or isinstance(value, bool):
            return Decimal(1) if value is True else None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    return None


def _headline_of(tile: RoundsTilePayload) -> _Headline | None:
    """A stored tile, read back as the headline it published.

    So one comparability rule serves both callers: the live evaluation
    (which has a ``_Headline`` in hand) and the brief re-diffing two STORED
    loads against a named reference frame. Two implementations of "are
    these two measurements of one thing?" is exactly how the subject check
    came to exist on one path and not the other.
    """
    if tile.value is None:
        return None
    return _Headline(
        referent=tile.headline_referent or "",
        title=tile.headline_title,
        statement=tile.headline_statement,
        metric_id=tile.metric_id or "",
        value=Decimal(str(tile.value)),
        unit=tile.unit,
        text=tile.value_text,
        is_bound=tile.integrity.is_bound,
        subject=tuple(tile.headline_subject.items()),
        subject_label=tile.headline_subject_label,
    )


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _utc(value: datetime) -> datetime:
    """Interpret a naive datetime as UTC before comparing it to anything.

    The two sides of "is this load older than that one?" genuinely arrive
    with different awareness: the DuckDB connector reads a warehouse
    ``TIMESTAMP`` and hands back a NAIVE ``loaded_at``, while a stored
    ``timestamptz`` comes back AWARE. Comparing them raises, so the whole
    surface fell over the first time it ran against a real database — and
    not once against the in-memory store, which round-trips whatever it was
    given.

    Normalised here, at the comparison, on the same convention the Postgres
    adapters already use for their typed columns (a naive value is UTC).
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _subject_of(
    referents: Sequence[RegisteredReferent],
    referent_value: str,
    dimensions: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """Which cell a finding is about, from the turn's own referent registry.

    Read off ``TurnOutcome.referents`` rather than the stored registry: the
    registry is keyed by ``(session, referent id)`` and every pin evaluated
    at one load shares one session, so ``F1`` there belongs to whichever pin
    was evaluated last. The turn that produced this finding is the only
    unambiguous source, and it is already in hand.
    """
    if not dimensions:
        return ()
    wanted = list(dimensions)
    for entry in referents:
        if entry.referent.value != referent_value:
            continue
        members: dict[str, str] = {}
        if entry.cohort_definition is not None:
            for predicate in iter_predicates(entry.cohort_definition.scope):
                if predicate.op is PredicateOp.EQ and predicate.dimension.id in wanted:
                    members[predicate.dimension.id] = str(predicate.values[0])
        if not members and entry.dimension_value is not None:
            dimension, value = entry.dimension_value
            if dimension in wanted:
                members[dimension] = str(value)
        if set(members) != set(wanted):
            return ()
        return tuple((dimension, members[dimension]) for dimension in wanted)
    return ()


def _spec_names_one_cell(spec: TypedInvestigationSpec) -> bool:
    """Does this spec pin down exactly one cell of its own breakdown?

    True when every dimension it breaks out is also fixed by a single-value
    equality filter — which is what :func:`_narrowed_to_cell` produces, and
    what makes a watch's subject invariant across loads by construction.
    """
    if not spec.dimensions:
        return True
    fixed = {dimension for dimension, _ in _eq_filters_of(spec)}
    return all(dimension in fixed for dimension in spec.dimensions)


def _subject_mismatch(
    pin: RoundsPin, prior_label: str, current_label: str
) -> str | None:
    """Why these two measurements are of two different subjects, or ``None``.

    The guard that was missing. ``_not_comparable_reason`` checked four
    things — no headline, no prior value, a changed unit, a changed metric —
    and never checked WHICH CELL, despite its own docstring asking whether
    two loads are two measurements of one thing. A watch over a ranked
    breakdown headlines whatever ranks first, so a rank flip produced a
    delta between two payers, gated it material, counted it, and explained
    it as adjudication run-out (round-7 FN-2).
    """
    if _spec_names_one_cell(pin.spec):
        # The spec fixes the cell, so both sides measured it whatever their
        # payloads recorded — including tiles stored before subjects were.
        return None
    if not prior_label and not current_label:
        return None
    if prior_label == current_label:
        return None
    if not prior_label:
        return (
            "this watch measures a ranked breakdown and the earlier load did not record which "
            "cell it headlined, so a delta between them could be a comparison of two different "
            f"cells; this load's is {current_label!r}"
        )
    if not current_label:
        return (
            "this watch measures a ranked breakdown and this load did not record which cell it "
            f"headlined, so it cannot be compared against the earlier load's {prior_label!r}"
        )
    return (
        f"the earlier load's leading cell was {prior_label!r} and this one's is "
        f"{current_label!r}, so a delta between them would be a comparison of two different "
        "subjects rather than a movement in one"
    )


def _not_comparable_reason(
    pin: RoundsPin, prior: RoundsTilePayload, headline: _Headline | None
) -> str | None:
    """Are these two loads two measurements of one thing?

    A percentage between mismatched sides is worse than no percentage: it
    looks exactly like a movement.
    """
    if headline is None:
        return (
            "this load produced no value for the watched metric, so there is nothing to "
            "compare against the prior load"
        )
    if prior.value is None:
        return (
            f"the prior load ({prior.watermark_id}) produced no value for this watch, so no "
            "movement can be claimed"
        )
    if prior.unit != headline.unit:
        return (
            f"the prior load measured this watch in {prior.unit or 'an unknown unit'} and this "
            f"one measures it in {headline.unit or 'an unknown unit'}, so the two are not two "
            "measurements of one thing"
        )
    if prior.metric_id and prior.metric_id != headline.metric_id:
        return (
            f"the prior load's headline came from {prior.metric_id!r} and this one's from "
            f"{headline.metric_id!r}, so a delta between them would be a comparison of two "
            "different contracts"
        )
    return _subject_mismatch(pin, prior.headline_subject_label, headline.subject_label)


def _baseline_not_comparable_reason(
    pin: RoundsPin, baseline: RoundsTilePayload | None, tile: RoundsTilePayload
) -> str | None:
    """The same two tests, applied to the baseline comparison."""
    if baseline is None:
        # No stored evaluation at the baseline load: the pin carries a
        # number and nothing about which cell produced it. Safe only when
        # the spec itself fixes the cell.
        if _spec_names_one_cell(pin.spec):
            return None
        return (
            "this watch measures a ranked breakdown and the load its baseline was captured at "
            "was not recorded as an evaluation, so there is no way to tell whether the "
            "baseline number belongs to the cell this tile is showing"
        )
    mismatch = _subject_mismatch(
        pin, baseline.headline_subject_label, tile.headline_subject_label
    )
    if mismatch is not None:
        return mismatch
    return None


def _assert_subject_matches_label(pin: RoundsPin, headline: _Headline | None) -> None:
    """A tile may not name one subject and publish another's number.

    Checked at PAYLOAD BUILD, on every tile, rather than in a test: the
    shipped defect certified itself ``grade: direct`` while displaying
    State Medicaid MCO's 29.5% under the title "Pinnacle Health Plan:
    22.9%", and nothing in the pipeline was in a position to notice.

    The comparison is against the SPEC's own fixed cell, not against the
    label's prose — a label is words somebody may have typed, and this must
    not fail because an analyst titled their watch "Pinnacle's problem".
    """
    if headline is None or not headline.subject:
        return
    fixed = dict(_eq_filters_of(pin.spec))
    for dimension, value in headline.subject:
        expected = fixed.get(dimension)
        if expected is not None and expected != value:
            raise ReviError(
                f"rounds tile for pin {pin.id!r} would publish {dimension}={value!r} under a "
                f"spec narrowed to {dimension}={expected!r}: a tile whose label and value name "
                "different subjects is the round-7 signature defect and is refused here rather "
                "than rendered",
                details={"pin_id": pin.id, "dimension": dimension},
            )


def _delta_payload(
    *,
    prior_watermark_id: str,
    prior_value: Decimal | None,
    current: Decimal | None,
    unit: str | None,
    verdict: MaterialityVerdict,
    comparable: bool,
    not_comparable_reason: str | None,
    reference: str,
    same_window: bool = False,
    subject_label: str = "",
    prior_subject_label: str = "",
) -> RoundsDeltaPayload:
    # THE DIFFERENCE IS ONLY PUBLISHED WHEN IT MEANS SOMETHING. Subtracting
    # two numbers always succeeds; that is the problem. When the two sides
    # are not two measurements of one thing — a rank flip, a changed unit, a
    # changed contract — the arithmetic still produces 0.035823, and any
    # renderer reading `delta_text` without first reading `comparable`
    # publishes "up 3.6 points" for a movement that did not happen. Both
    # READINGS stay on the payload, because both are real; only the
    # difference between them is withheld (round-7 FN-2).
    delta = (
        current - prior_value
        if comparable and current is not None and prior_value is not None
        else None
    )
    # A rate's movement is percentage POINTS and nothing else. A relative
    # fraction beside a rate delta is the ambiguity the platform already
    # refuses everywhere else ("up 3.2%" — of what?).
    fraction: float | None = None
    if delta is not None and prior_value not in (None, 0) and unit != "ratio":
        assert prior_value is not None
        fraction = round(float(delta / abs(prior_value)), 6)
    direction = "unknown"
    if delta is not None:
        direction = "flat" if delta == 0 else ("up" if delta > 0 else "down")
    return RoundsDeltaPayload(
        prior_watermark_id=prior_watermark_id,
        prior_value=float(prior_value) if prior_value is not None else None,
        prior_value_text=format_value(prior_value, unit) if prior_value is not None else "",
        value=float(current) if current is not None else None,
        value_text=format_value(current, unit) if current is not None else "",
        unit=unit,
        delta=float(delta) if delta is not None else None,
        delta_text=magnitude(delta, unit) if delta is not None else "",
        delta_fraction=fraction,
        direction=direction,  # type: ignore[arg-type]
        comparable=comparable,
        not_comparable_reason=not_comparable_reason,
        reference=reference,  # type: ignore[arg-type]
        # Window equality qualifies a MOVEMENT — it is what turns a delta
        # into "late-arriving data settling — adjudication run-out" rather
        # than a change in the business. With no delta published there is
        # nothing for it to qualify, and publishing it anyway is precisely
        # how a causal mechanism came to be attached to a phantom (round-7
        # FN-2). The dates are still on the tile for anyone who wants them.
        same_window=same_window and comparable,
        subject_label=subject_label,
        prior_subject_label=prior_subject_label,
        material=verdict.material,
        threshold_source=verdict.threshold_source,  # type: ignore[arg-type]
        below_governed_gate=verdict.below_governed_gate,
        materiality_rule=verdict.rule,
        materiality_note=verdict.note,
    )


def _adds_something(tile: RoundsTilePayload) -> bool:
    """Does the baseline delta say anything the prior-load delta does not?

    Published only when it does. Two numbers that tell the same story are
    one number and a distraction.
    """
    baseline = tile.baseline_delta
    if baseline is None or not baseline.comparable:
        return False
    if tile.delta is None:
        return True
    return baseline.material and baseline.prior_watermark_id != tile.delta.prior_watermark_id


def _movement_sentence(
    pin: RoundsPin,
    tile: RoundsTilePayload,
    delta: RoundsDeltaPayload,
    baseline: RoundsDeltaPayload | None,
    prior_date: date | None = None,
) -> str:
    """One watch's movement, in the words a reader uses.

    Everything a warehouse calls a thing is gone from this sentence
    (round-7 FN-8): loads are named by their data date, the surface's own
    noun is "watch" and never "tile", and the SUBJECT is named — no
    movement is published without saying what moved (round-7 FN-2).
    """
    subject = (
        f" ({delta.subject_label})"
        if delta.subject_label and delta.subject_label not in pin.label
        else ""
    )
    since = (
        f" since {_load_phrase(prior_date)}" if prior_date is not None else ""
    )
    parts = [
        f"{pin.label}{subject}: {delta.value_text}, {delta.direction} {delta.delta_text} "
        f"from {delta.prior_value_text}{since}."
    ]
    if delta.threshold_source == "watch":
        parts.append(
            "Briefed on this watch's own threshold"
            + (
                " — which is looser than the governed gate for this measure, so the movement "
                "is inside what the pack calls normal variation."
                if delta.below_governed_gate
                else f": {_sentence(delta.materiality_note)}"
            )
        )
    else:
        parts.append(_sentence(delta.materiality_note))
    if baseline is not None:
        parts.append(
            f"Since you started watching it, it is {baseline.direction} "
            f"{baseline.delta_text} from {baseline.prior_value_text}."
            + (
                ""
                if baseline.same_window
                else " Those two readings cover different date ranges, so part of that is the "
                "window moving rather than the measure."
            )
        )
    if delta.same_window and tile.window_start is not None and tile.window_end is not None:
        # Said from the DATES the two loads measured, not from the pin's
        # declared window mode: a relative window usually moves and
        # sometimes does not, and only the resolved dates know which.
        parts.append(
            SAME_WINDOW_NOTE.format(start=tile.window_start, end=tile.window_end)
        )
    if tile.integrity.is_bound:
        parts.append(
            "The value is an upper bound: a suppressed numerator was replaced by the largest "
            "value it could have held."
        )
    if tile.integrity.provisional:
        parts.append("The value is provisional — the window is still adjudicating.")
    return " ".join(parts)


def _cap(
    entries: list[RoundsBriefEntry], policy: RoundsPolicy
) -> tuple[list[RoundsBriefEntry], dict[str, int]]:
    """Cap the brief: per kind first, then overall — worst-to-lose LAST.

    Per kind first so one noisy category cannot fill the brief and push
    every other kind of change off the end of it — which is precisely how a
    daily surface trains somebody to stop reading it.

    Then by governed PRIORITY rather than by insertion order (round-7
    FN-11). Insertion order put ``resolution_regressed`` and
    ``resolution_confirmed`` last, so the platform's verdicts on the team's
    own work — the differentiator the buyer named as their reason for buying
    — were the first thing the cap deleted, silently, on any tenant with a
    normal card count. Within a kind, entries sort by consequence, so the
    cap takes the smallest of the least important kind.

    Returns the published entries and WHAT WAS DROPPED, by kind: "12 further
    entries" does not tell a reader whether a confirmed fix was among them.
    """
    materiality = policy.materiality
    per_kind = materiality.max_entries_per_kind
    ordered = sorted(
        entries,
        key=lambda e: (materiality.rank_of(e.kind), -_consequence(e)),
    )
    dropped: dict[str, int] = {}
    seen: dict[str, int] = {}
    kept: list[RoundsBriefEntry] = []
    for entry in ordered:
        count = seen.get(entry.kind, 0)
        if per_kind and count >= per_kind:
            dropped[entry.kind] = dropped.get(entry.kind, 0) + 1
            continue
        seen[entry.kind] = count + 1
        kept.append(entry)
    if not materiality.max_entries or len(kept) <= materiality.max_entries:
        return kept, dropped
    # The overall cap, with the exempt kinds taken out of its reach first.
    exempt_count = sum(1 for e in kept if e.kind in materiality.never_capped)
    room = max(materiality.max_entries - exempt_count, 0)
    published: list[RoundsBriefEntry] = []
    for entry in kept:
        if entry.kind in materiality.never_capped:
            published.append(entry)
        elif room:
            published.append(entry)
            room -= 1
        else:
            dropped[entry.kind] = dropped.get(entry.kind, 0) + 1
    return published, dropped


def _consequence(entry: RoundsBriefEntry) -> float:
    """How much this entry costs to lose, within its kind.

    Money for a lead; how far past its own gate a watch moved, as a
    multiple, for a movement — so a watch that tripled its threshold
    outranks one that grazed it, whatever their units.
    """
    if entry.impact_cents is not None:
        return float(abs(entry.impact_cents))
    delta = entry.delta
    if delta is not None and delta.delta is not None:
        if delta.prior_value:
            return abs(delta.delta / delta.prior_value)
        return abs(delta.delta)
    return 0.0


#: The nouns this surface uses for what a load can change, singular and
#: plural. One vocabulary, shared by the headline, the held-back line and
#: the cap's own report — the headline printed raw enum ids ("2 new lead, 1
#: pin movement") directly above rows the UI labels "A WATCH MOVED", and
#: "pin" is a word this product's own naming rule bans (round-7 FN-8).
_KIND_NOUNS: dict[str, tuple[str, str]] = {
    "new_lead": ("new lead", "new leads"),
    "pin_movement": ("watch moved", "watches moved"),
    "self_resolved": ("resolved on its own", "resolved on their own"),
    "resolution_confirmed": ("fix confirmed", "fixes confirmed"),
    "resolution_regressed": ("fix did not hold", "fixes did not hold"),
    "rank_flip": ("new leader", "new leaders"),
}


def _plural(count: int, singular: str, plural: str) -> str:
    """``2 new leads``. Never ``2 new lead(s)``: the parenthetical plural is
    the mark of a sentence a machine wrote, on the one surface a champion
    screenshots for their VP."""
    return f"{count} {singular if count == 1 else plural}"


def _sentence(text: str) -> str:
    """One clause, capitalised and stopped. Brief prose is assembled from
    fragments the gate wrote for a different context, and joining them raw
    produced lowercase sentence starts mid-paragraph."""
    stripped = text.strip().rstrip(".")
    if not stripped:
        return ""
    return f"{stripped[:1].upper()}{stripped[1:]}."


def _load_phrase(data_date: date | None) -> str:
    """A load, named the way a reader names it: by its data date."""
    if data_date is None:
        return "the previous load"
    return f"the {data_date:%b %-d} load"


def _data_date_of(load: RoundsLoad | None) -> date | None:
    """A stored load's newest data date, when the census recorded one.

    Recorded from this change on; ``None`` for loads written before it, in
    which case the prose says "the previous load" rather than inventing a
    date or falling back to a warehouse id.
    """
    if load is None:
        return None
    raw = load.payload.get("newest_data_date")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:  # pragma: no cover - defensive
            return None
    return raw if isinstance(raw, date) else None


def _immaterial_note(immaterial: RoundsImmaterialSummary) -> str:
    """What the gate held back, in one sentence.

    Counted rather than hidden: suppressing a movement silently and
    suppressing it visibly are different products, and the first is a
    filter the analyst cannot audit.
    """
    bits: list[str] = []
    if immaterial.pin_movements:
        bits.append(
            f"{_plural(immaterial.pin_movements, 'watch', 'watches')} moved by less than the "
            "threshold for their measure"
        )
    if immaterial.new_leads:
        bits.append(
            f"{_plural(immaterial.new_leads, 'new lead', 'new leads')} fell below the brief "
            "floor"
        )
    if immaterial.self_resolved:
        bits.append(
            f"{_plural(immaterial.self_resolved, 'lead', 'leads')} left the detection feed "
            "below the brief floor"
        )
    if immaterial.not_yet_comparable:
        bits.append(
            f"{_plural(immaterial.not_yet_comparable, 'watch has', 'watches have')} nothing to "
            "compare against yet"
        )
    if immaterial.unavailable:
        bits.append(
            f"{_plural(immaterial.unavailable, 'watch', 'watches')} could not be measured at "
            "this load"
        )
    if immaterial.entries_withheld_by_cap:
        dropped = immaterial.entries_withheld_by_kind
        detail = (
            " ("
            + ", ".join(
                _plural(count, *_KIND_NOUNS.get(kind, (kind, kind)))
                for kind, count in sorted(dropped.items())
            )
            + ")"
            if dropped
            else ""
        )
        bits.append(
            f"{_plural(immaterial.entries_withheld_by_cap, 'further entry', 'further entries')}"
            f"{detail} were held back by the brief's own cap"
        )
    if not bits:
        return "Nothing was held back."
    return "Held back and counted rather than hidden: " + "; ".join(bits) + "."


def _headline_sentence(
    *,
    status: str,
    newest_data_date: date | None,
    prior_newest_data_date: date | None,
    has_prior: bool,
    entries: Sequence[RoundsBriefEntry],
    pins_evaluated: int,
    leads: int,
) -> str:
    """The first sentence on the surface, in human words.

    Round-7 FN-8. It read "4 thing(s) changed between wm_002 and wm_003: 2
    new lead, 1 pin movement, 1 self resolved" — raw enum ids, no
    pluralisation, warehouse handles, and the held-back clause printed here
    AND again immediately below it. Nothing in that sentence is wrong and a
    VP does not read past it.

    The held-back clause is gone from here entirely: it has its own line,
    and one fact printed twice on one screen reads as a bug.
    """
    if status == "first_load" or not has_prior:
        return (
            "This is the first load Revi has walked your Rounds on. "
            f"{_plural(pins_evaluated, 'watch', 'watches')} and "
            f"{_plural(leads, 'detected lead', 'detected leads')} are now the baseline; from "
            "the next load on, this brief says what changed."
        )
    since = _load_phrase(prior_newest_data_date)
    if status == "nothing_material":
        # The proud shape. This is an ANSWER, not an empty page: it names
        # what was measured and what was found to be within tolerance.
        return (
            f"Nothing material has changed since {since}. Revi re-ran "
            f"{_plural(pins_evaluated, 'watch', 'watches')} against the new data and diffed "
            f"{_plural(leads, 'detected lead', 'detected leads')}."
        )
    kinds: dict[str, int] = {}
    for entry in entries:
        kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
    described = ", ".join(
        _plural(kinds[kind], *_KIND_NOUNS.get(kind, (kind, kind)))
        for kind in sorted(kinds, key=lambda k: (-kinds[k], k))
    )
    return f"Since {since}: {described}."


def _rounds_warnings(policy: RoundsPolicy) -> list[str]:
    if policy.enabled:
        return []
    return [
        "population_caveat: this deployment's pack ships no governed Rounds content, so no "
        "materiality gate was applied and no time-to-impact was derived — every movement is "
        "reported as measured, and nothing here has been judged material"
    ]


def _leads_of(load: RoundsLoad | None) -> dict[str, Mapping[str, Any]]:
    if load is None:
        return {}
    raw = load.payload.get("leads")
    return dict(raw) if isinstance(raw, dict) else {}


def _time_to_impact_payload(row: Mapping[str, Any]) -> TimeToImpactPayload | None:
    raw = row.get("time_to_impact")
    return TimeToImpactPayload.model_validate(raw) if isinstance(raw, dict) else None


def _ordinal(value: int) -> str:
    words = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}
    return words.get(value, f"{value}th")


def _watch_from_model(model: RoundsWatchModel | None) -> RoundsWatch | None:
    if model is None:
        return None
    return RoundsWatch(
        mode=model.mode,
        value=None if model.value is None else Decimal(str(model.value)),
        unit=model.unit,
        direction=model.direction,
        note=model.note,
    )


def _watch_model(watch: RoundsWatch | None) -> RoundsWatchModel:
    if watch is None:
        return RoundsWatchModel()
    return RoundsWatchModel(
        mode=watch.mode,  # type: ignore[arg-type]
        value=None if watch.value is None else float(watch.value),
        unit=watch.unit,  # type: ignore[arg-type]
        direction=watch.direction,  # type: ignore[arg-type]
        note=watch.note,
    )


def _threshold_statement(watch: RoundsWatch | None, unit: str | None) -> str:
    """The gate in words, for the confirmation sentence."""
    if watch is None or watch.mode == "governed_default":
        return "when it moves more than the governed threshold for this measure"
    if watch.mode == "any_movement":
        return "on any movement at all"
    if watch.mode == "crosses":
        return f"when it crosses {format_threshold(watch, unit)}"
    return f"when it moves {format_threshold(watch, unit)} or more"


def _threshold_alternative(
    watch: RoundsWatch | None, unit: str | None, reference: Decimal | None
) -> str:
    """The OTHER honest reading of the analyst's threshold words.

    "more than 2%" against a rate is genuinely ambiguous — two percentage
    points, or two percent of the current value — and this platform refuses
    that ambiguity everywhere else. The reading committed to is the one
    legal against every contract (``relative_pct``), which on a 25.9% base
    makes the gate about half a point: four times tighter than the pack's
    own, with the fatigue advisory then telling the analyst to tighten the
    thresholds they never loosened (round-7 FN-6).

    Empty when the words admit only one reading.
    """
    if watch is None or watch.unit != "relative_pct" or watch.value is None:
        return ""
    if unit != "ratio":
        return ""
    stated = f"{float(watch.value):.10g}%"
    points = f"{float(watch.value):.10g} points"
    if reference is None or not reference:
        return (
            f"I read {stated} as {stated} of the current value, not as {points} — say "
            f"{points!r} if you meant percentage points."
        )
    gate = abs(reference) * watch.value / 100
    return (
        f"I read {stated} as {stated} of the current value, which is about "
        f"{format_value(gate, 'ratio')} at today's level — say {points!r} if you meant "
        "percentage points, which is the larger gate."
    )


def _watch_confirmation(
    label: str, value_text: str, threshold_statement: str, alternative: str = ""
) -> str:
    """The one-time baseline confirmation, composed from the answer.

    Every clause is a fact the payload also carries: what is watched, what
    it reads right now, and what will bring it back. Never composed by a
    model — a generated sentence could not be validated against the answer
    beside it any more cheaply than writing it from that answer.
    """
    current = f" — currently {value_text}" if value_text else ""
    sentence = (
        f"Watching: {label}{current}. I'll brief you {threshold_statement}, and the answer "
        "above is the baseline I'll measure that from."
    )
    return f"{sentence} {alternative}".strip() if alternative else sentence


def pin_payload(
    pin: RoundsPin,
    *,
    notes: Sequence[str] = (),
    spec_summary: str = "",
    already_existed: bool = False,
) -> RoundsPinPayload:
    window_note = _WINDOW_NOTES.get(pin.window_mode, "")
    if notes:
        window_note = " ".join([window_note, *notes]).strip()
    return RoundsPinPayload(
        pin_id=pin.id,
        tenant=pin.tenant,
        label=pin.label,
        presentation=pin.presentation,  # type: ignore[arg-type]
        spec=pin.spec,
        spec_summary=spec_summary,
        notes=list(notes),
        already_existed=already_existed,
        window_mode=pin.window_mode,  # type: ignore[arg-type]
        window_note=window_note,
        created_from_kind=pin.created_from_kind,  # type: ignore[arg-type]
        created_from_investigation_id=pin.created_from_investigation_id,
        created_from_referent=pin.created_from_referent,
        watch=_watch_model(pin.watch) if pin.watch is not None else None,
        baseline_watermark_id=pin.baseline_watermark_id,
        baseline_value=None if pin.baseline_value is None else float(pin.baseline_value),
        baseline_value_text=(
            format_value(pin.baseline_value, pin.baseline_unit)
            if pin.baseline_value is not None
            else ""
        ),
        baseline_unit=pin.baseline_unit,
        created_at=pin.created_at,
        archived_at=pin.archived_at,
    )


def lead_payload(lead: RoundsLead, confirmations_required: int) -> RoundsLeadPayload:
    return RoundsLeadPayload(
        anomaly_id=lead.anomaly_id,
        tenant=lead.tenant,
        status=lead.status,  # type: ignore[arg-type]
        note=lead.note,
        updated_at=lead.updated_at,
        claimed_at_watermark=lead.claimed_at_watermark,
        baseline_cents=lead.baseline_cents,
        baseline_basis=lead.baseline_basis,
        confirming_watermarks=list(lead.confirming_watermarks),
        confirmations_required=confirmations_required,
        verification_note=lead.verification_note,
        history=[dict(entry) for entry in lead.history],
    )


def annotate_time_to_impact(
    portfolio: PortfolioResponse,
    records: Mapping[str, AnomalyRecord],
    *,
    newest_data_date: date,
    policy: RoundsPolicy,
) -> PortfolioResponse:
    """Publish each card's cash timing (additive; the ranking is untouched).

    ``anomaly_priority@3`` still decides the order. Time-to-impact is
    context a reader uses, not a silent re-rank: a rank change needs its own
    versioned formula decision, and smuggling urgency into an existing
    version would make two builds of the same data disagree with no version
    string to explain it.
    """
    if not policy.time_to_impact.categories:
        return portfolio
    items = []
    for card in portfolio.items:
        record = records.get(card.anomaly_id)
        if record is None:
            items.append(card)
            continue
        items.append(
            card.model_copy(
                update={
                    "time_to_impact": time_to_impact_for(
                        record,
                        newest_data_date=newest_data_date,
                        policy=policy.time_to_impact,
                    )
                }
            )
        )
    return portfolio.model_copy(
        update={"items": items, "cash_timing_lanes": _cash_timing_lanes(items)}
    )


#: The cash-timing partition, in render order, with the words a section
#: header should use. Pre-cash leads because it is the half a director can
#: still do something about, which is the question the split exists to
#: answer.
_CASH_LANES: tuple[tuple[str, str, str], ...] = (
    (
        "pre_cash",
        "Still catchable",
        "The cash effect has not landed yet. Working these changes what gets paid.",
    ),
    (
        "already_hit",
        "Already hit cash",
        "The cash effect has landed — a denial that did not pay, an allowance already "
        "taken. What is left is recovery, where a window is still open.",
    ),
    (
        "unknown",
        "No honest timing",
        "This platform has no basis for dating the cash effect on these, and each card "
        "says why. A guess here would be indistinguishable from the real dates beside it.",
    ),
)


def _cash_timing_lanes(cards: Sequence[AnomalyCard]) -> list[PortfolioLanePayload]:
    """The worklist split by WHEN the money moves, with its own totals.

    Round-7 FN-16. Every card already carried
    :attr:`TimeToImpactPayload.lane`, the derivation was governed, and no
    surface totalled it — so "of everything on the worklist, how much has
    not hit cash yet and when are the deadlines?" was answered with one
    undifferentiated $830,501.93 and the deadline half of the question was
    dropped without a refusal. A product positioned as "find problems
    before they impact the bottom line" could not add up the money that has
    not yet hit the bottom line.

    The horizon is built only from REAL dates a detector published — a
    filing limit, an appeal window — never from a projection, for the same
    reason :class:`TimeToImpactPayload` refuses to put a projection in
    ``deadline_date``.
    """
    lanes: list[PortfolioLanePayload] = []
    for lane_id, label, description in _CASH_LANES:
        members = [
            card
            for card in cards
            if (card.time_to_impact.lane if card.time_to_impact is not None else "unknown")
            == lane_id
        ]
        if not members:
            continue
        dated = [
            (card.time_to_impact.deadline_date, card.time_to_impact.days)
            for card in members
            if card.time_to_impact is not None
            and card.time_to_impact.deadline_date is not None
        ]
        # A recovery window is a real dated limit too, and on the
        # already-hit lane it is the ONLY one there is.
        dated += [
            (card.time_to_impact.recovery_deadline_date, card.time_to_impact.recovery_days)
            for card in members
            if card.time_to_impact is not None
            and card.time_to_impact.recovery_deadline_date is not None
        ]
        # The soonest limit somebody can still hit. Sorting on the soonest
        # limit FULL STOP puts a window that closed in April at the top of a
        # header, and "closes in -94 days" is not a horizon.
        open_dates = [pair for pair in dated if pair[1] is None or pair[1] >= 0]
        soonest = min(open_dates, default=None, key=lambda pair: pair[0])
        lanes.append(
            PortfolioLanePayload(
                id=lane_id,
                label=label,
                description=description,
                kind="cash_timing",
                anomaly_ids=[card.anomaly_id for card in members],
                item_count=len(members),
                impact_cents=sum(abs(card.impact_cents) for card in members),
                ranked_impact_cents=sum(abs(card.ranked_impact_cents) for card in members),
                recoverable_cents_estimate=sum(
                    card.recoverable_cents_estimate for card in members
                ),
                soonest_deadline_date=soonest[0] if soonest is not None else None,
                soonest_deadline_days=soonest[1] if soonest is not None else None,
                dated_item_count=len(dated),
                passed_deadline_count=len(dated) - len(open_dates),
            )
        )
    return lanes


__all__ = [
    "RoundsNotFoundError",
    "RoundsService",
    "annotate_time_to_impact",
    "lead_payload",
    "pin_payload",
    "typed_spec_from_analysis",
]
