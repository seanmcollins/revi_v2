"""Per-load evaluation: turning one pin into one tile at one watermark."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from revi_api.assembly import finding_payload
from revi_api.evidence import build_evidence
from revi_api.monitors_policy import (
    MaterialityVerdict,
    assess_movement,
)
from revi_api.warning_codes import CAUTION, structured_warnings
from revi_investigation.application.ports import (
    MonitorsLoad,
    MonitorsPin,
    MonitorsPinResult,
    RegisteredReferent,
)
from revi_investigation.application.rendering import (
    format_value,
    magnitude,
)
from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.context import PackVersionRef
from revi_investigation.domain.records import Finding, Session
from revi_investigation_contracts.api import (
    TypedInvestigationSpec,
)
from revi_investigation_contracts.monitors import (
    MonitorsDeltaPayload,
    MonitorsTileIntegrity,
    MonitorsTilePayload,
)
from revi_kernel.errors import ReviError
from revi_kernel.filters import PredicateOp, iter_predicates
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.watermark import DataWatermark, WatermarkEpoch

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    pass

from revi_api.monitors.common import _decimal, _MonitorsBase, _utc, logger
from revi_api.monitors.spec import _cell_phrase, _eq_filters_of

#: What a pin evaluation's turn records as its question. Never shown as an
#: analyst's words, because it is not one: it names the monitor it re-ran.
_EVALUATION_QUESTION = "(Monitors: re-running a monitored spec at this load)"


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
    #: monitor with no breakdown. Read off the evaluation's own referent
    #: registry entry rather than parsed out of a display title, because a
    #: title is prose and this decides whether two loads measured one thing.
    subject: tuple[tuple[str, str], ...] = ()
    subject_label: str = ""


class _LoadEvaluation(_MonitorsBase):
    """Evaluating pins against a load, and shaping each result into a tile."""

    # ------------------------------------------------------- per-load evaluation

    async def evaluate_load(
        self, tenant: str, watermark: DataWatermark, *, force: bool = False
    ) -> MonitorsLoad:
        """Re-run every active pin at this load, verify claimed resolutions,
        and record the detection-feed census.

        Idempotent per (pin, watermark): a stored result is reused rather
        than recomputed, so calling this from the scheduled sweep and from
        the brief route costs one evaluation between them. ``force``
        re-evaluates — for a redeployed pack, or a repaired snapshot.

        Reuse is conditional on the stored tile still MATCHING THE MONITOR.
        Unconditional idempotence lets any stored row for this watermark
        win, so a monitor repaired between loads goes on republishing the
        tile the repair existed to replace for as long as the watermark
        stands. A stored evaluation is reused only while it is still an
        evaluation OF THIS MONITOR — see :func:`_stale_result_reason`.
        """
        # Monitors created before the narrowed-cell rule are brought onto it
        # (or stopped) BEFORE they are evaluated, so no load re-publishes a
        # tile whose label and value name different subjects. Runs on every
        # load and does nothing after the first: a repaired monitor names one
        # cell, and this only looks at monitors that do not.
        repaired: set[str] = set()
        try:
            report = await self.repair_pins(tenant)
            repaired = set(report["repaired"])
        except Exception:  # pragma: no cover - a repair must not cost a load
            logger.exception("monitors: pin repair pass failed for tenant %s", tenant)
        pins = await self._components.monitors_pins.list_for_tenant(tenant)
        evaluated = 0
        for pin in pins:
            existing = await self._components.monitors_results.get(pin.id, watermark.id)
            if existing is not None and not force and pin.id not in repaired:
                stale = _stale_result_reason(pin, existing) or await self._stale_prior_reason(
                    pin, existing, watermark
                )
                if stale is None:
                    continue
                logger.info(
                    "monitors: re-deriving pin %s at %s rather than republishing its stored "
                    "tile — %s",
                    pin.id,
                    watermark.id,
                    stale,
                )
            await self._evaluate_pin(pin, watermark)
            evaluated += 1

        portfolio = await self._portfolio_for(tenant, watermark)
        verifications = await self._verify_claimed_leads(tenant, watermark, portfolio)
        census = await self._census(tenant, watermark, portfolio, pins, verifications)
        load = MonitorsLoad(
            tenant=tenant,
            watermark_id=watermark.id,
            watermark_loaded_at=watermark.loaded_at,
            evaluated_at=datetime.now(UTC),
            payload=census,
        )
        await self._components.monitors_loads.put(load)
        logger.info(
            "monitors: evaluated %d of %d pin(s) and verified %d claimed lead(s) for tenant %s "
            "at %s",
            evaluated,
            len(pins),
            len(verifications),
            tenant,
            watermark.id,
        )
        return load

    async def _evaluate_pin(self, pin: MonitorsPin, watermark: DataWatermark) -> MonitorsTilePayload:
        """Run one pin's stored spec at one load, and store the tile.

        The evaluation is an ordinary TYPED first turn — see the module
        docstring for why this is the answer path and not a lighter one.
        """
        prior = await self._prior_result(pin, watermark)
        session = await self._monitors_session(pin.tenant, watermark)
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
            tile = MonitorsTilePayload(
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
            logger.exception("monitors: pin %s could not be evaluated at %s", pin.id, watermark.id)
            tile = MonitorsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="unavailable",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                unavailable_reason="this monitor could not be evaluated at this load (the "
                "attempt is recorded in the API log); no value is published rather than a "
                "stale one",
            )
            await self._store_tile(pin, watermark, tile)
            return tile

        tile = await self._tile_from_outcome(pin, outcome, watermark, prior)
        # The baseline is captured ONCE, at the first load that produces a
        # value. A monitor created between loads has no baseline until then,
        # and taking the previous load's value would attribute a movement to
        # a period nobody was monitoring.
        if pin.baseline_value is None and tile.value is not None:
            pin = replace(
                pin,
                baseline_watermark_id=watermark.id,
                baseline_value=Decimal(str(tile.value)),
                baseline_unit=tile.unit,
                baseline_captured_at=datetime.now(UTC),
            )
            await self._components.monitors_pins.save(pin)
        tile = tile.model_copy(
            update={"baseline_delta": await self._baseline_delta(pin, tile)}
        )
        await self._store_tile(pin, watermark, tile)
        return tile

    async def _store_tile(
        self, pin: MonitorsPin, watermark: DataWatermark, tile: MonitorsTilePayload
    ) -> None:
        await self._components.monitors_results.put(
            MonitorsPinResult(
                pin_id=pin.id,
                tenant=pin.tenant,
                watermark_id=watermark.id,
                watermark_loaded_at=watermark.loaded_at,
                evaluated_at=datetime.now(UTC),
                payload=tile.model_dump(mode="json"),
            )
        )

    async def _prior_result(
        self, pin: MonitorsPin, watermark: DataWatermark
    ) -> MonitorsTilePayload | None:
        """This pin's newest evaluation STRICTLY BEFORE this load.

        Ordered by the load's own clock, never by watermark id: that
        ``wm_001`` sorts before ``wm_002`` is a coincidence of one
        warehouse's naming, and diffing the wrong pair of loads is worse
        than diffing none.
        """
        for result in await self._components.monitors_results.history(pin.id, limit=12):
            if _utc(result.watermark_loaded_at) < _utc(watermark.loaded_at):
                return MonitorsTilePayload.model_validate(result.payload)
        return None

    async def _stale_prior_reason(
        self, pin: MonitorsPin, stored: MonitorsPinResult, watermark: DataWatermark
    ) -> str | None:
        """Was this tile's delta measured against the prior it would get now?

        The second half of :func:`_stale_result_reason`: that one asks
        whether a stored tile still describes THIS MONITOR, this one whether
        it still describes the right PAIR OF LOADS. A tile written at
        creation time, before its pin had any history, otherwise goes on
        saying "first reading — nothing to compare against" while the brief
        one screen above back-walks the history that has since arrived and
        reports a movement — both on one load, both about one pin.

        A monitor's history is not append-only in practice — a restoration
        re-walk backfills earlier loads, and a pin created between loads gets
        its first neighbour after its own tile was stored — so the prior a
        stored delta names is checked against the prior this pin resolves to
        now, and a disagreement costs one re-derivation.
        """
        try:
            tile = MonitorsTilePayload.model_validate(stored.payload)
        except Exception:  # pragma: no cover - _stale_result_reason ran first
            return "its stored tile can no longer be read in the current tile shape"
        prior = await self._prior_result(pin, watermark)
        measured_against = tile.delta.prior_watermark_id if tile.delta is not None else ""
        resolves_to = prior.watermark_id if prior is not None else ""
        if measured_against == resolves_to:
            return None
        return (
            f"its stored tile was measured against {measured_against or 'no earlier load'} and "
            f"this monitor's newest earlier evaluation is now {resolves_to or 'no earlier load'}"
        )

    async def _monitors_session(self, tenant: str, watermark: DataWatermark) -> Session:
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
        session_id = f"monitors_{digest}"
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
        pin: MonitorsPin,
        outcome: TurnOutcome,
        watermark: DataWatermark,
        prior: MonitorsTilePayload | None,
    ) -> MonitorsTilePayload:
        if outcome.clarification is not None:
            # A typed spec should never clarify. If one does, that is
            # reported rather than swallowed: it means the stored spec has
            # become ambiguous against the current pack, which is a fact
            # about the monitor and not a blank tile.
            return MonitorsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="clarification",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                investigation_id=outcome.investigation.id,
                unavailable_reason=(
                    "re-running this monitor's stored spec at this load asked a question rather "
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
        integrity = MonitorsTileIntegrity(
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
        # subject's must be impossible by construction, not merely unlikely
        # — such a tile can certify itself `grade: direct`. The check runs
        # on every payload build rather than in a test that only covers the
        # paths somebody thought of.
        _assert_subject_matches_label(pin, headline)
        if headline is not None and pin.spec.dimensions and not headline.subject:
            # A monitor that BREAKS OUT a dimension headlines one cell of that
            # breakdown, and a tile that cannot say which one is a number
            # under somebody's name with no way to check whose — a value
            # published for one subject under another subject's label, with
            # the evidence removed. Published as unavailable with the reason
            # rather than as an ordinary reading; belt to the repair's braces.
            logger.warning(
                "monitors: pin %s produced a %s breakdown with no resolvable subject at %s",
                pin.id,
                " and ".join(pin.spec.dimensions),
                watermark.id,
            )
            return MonitorsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation=pin.presentation,  # type: ignore[arg-type]
                status="unavailable",
                watermark_id=watermark.id,
                newest_data_date=watermark.newest_data_date,
                evaluated_at=datetime.now(UTC),
                investigation_id=outcome.investigation.id,
                warnings=warnings,
                warnings_v2=classified,
                unavailable_reason=(
                    "this monitor breaks out "
                    + " and ".join(pin.spec.dimensions)
                    + ", and this load could not record WHICH cell its number is about. A "
                    "value published under a title nobody can check against it is the defect "
                    "this surface was rebuilt to prevent, so no value is published here. Open "
                    "the monitor to see the full breakdown."
                ),
            )
        return MonitorsTilePayload(
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
        """The tile's number: the first finding's value for the monitored metric.

        Read off the FINDING rather than the frame, so a tile shows exactly
        what the answer published — including the ``≤`` a suppressed
        numerator earned it (:func:`bound_text`'s rule, applied here through
        the finding's own ``__is_bound`` value rather than re-derived).

        "The first finding" is a RANK on a breakdown, so the headline also
        carries WHICH CELL it came from. Every caller that compares two
        headlines needs it: without it a delta between two different
        subjects is published as a movement — "up 3.6 points" for a payer
        that had in fact fallen 6.6, because the two loads' first findings
        were two different payers.
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
                if not subject and not spec.dimensions:
                    # A FILTER-ONLY monitor ("denial rate for Atlas
                    # Commercial") breaks out nothing, so there is no
                    # referent cell to read a subject off — and it publishes
                    # a number about exactly one cell all the same, fixed by
                    # its own equality filters. Recording it here is what
                    # lets the label/value identity guard cover this whole
                    # class: that guard returns early on an empty subject,
                    # so without this a filter-only monitor measuring a
                    # different payer than its title names is unreachable by
                    # the one check written to catch it.
                    subject = _eq_filters_of(spec)
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
        pin: MonitorsPin,
        headline: _Headline | None,
        prior: MonitorsTilePayload | None,
        window: tuple[date, date] | None = None,
    ) -> MonitorsDeltaPayload:
        """Movement since the PRIOR load, gated by the governed materiality
        content and by this monitor's own threshold.

        Always a payload, never ``None``. The renderer draws nothing for
        nothing, so a tile that sent no delta at all would make a monitor
        that has never been compared look exactly like one that has not
        moved. Absence is read as absence only if something says so.
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
                "earlier evaluation of this monitor to compare against",
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
                monitor=pin.monitor,
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
        self, pin: MonitorsPin, tile: MonitorsTilePayload
    ) -> MonitorsDeltaPayload | None:
        """Movement since the monitor's CREATION-LOAD baseline.

        Published only when it says something the prior-load delta does not:
        a tile that has drifted four points since it was created while
        moving 0.2 overnight is telling two true stories, and a surface
        showing only the overnight one would hide the reason the monitor
        exists. When the baseline IS the load being evaluated there is
        nothing to say, and nothing is published.

        Held to the SAME two tests the prior-load delta is held to: the
        baseline load's own stored tile says which cell it measured and
        which dates it resolved, so a baseline delta across a rank flip is
        refused with the reason, and one across two different windows says
        so instead of presenting window movement as drift. The baseline
        load's tile is the authority on both — the pin stores only a number,
        a unit and a watermark id.
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
                    f"this monitor's baseline was measured in {was} and it now measures {now}, "
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
                monitor=pin.monitor,
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
            # Measured, exactly as the prior-load delta measures it. Left
            # defaulting to False, the sentence this produces sits directly
            # above a run-out note gated on the OTHER delta's window
            # equality, and a reader attaches the equality claim to the
            # wrong sentence.
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

    async def _baseline_tile(self, pin: MonitorsPin) -> MonitorsTilePayload | None:
        """This monitor's stored evaluation at its baseline load, if there is one."""
        if not pin.baseline_watermark_id:
            return None
        stored = await self._components.monitors_results.get(
            pin.id, pin.baseline_watermark_id
        )
        if stored is None:
            return None
        return MonitorsTilePayload.model_validate(stored.payload)


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


def _headline_of(tile: MonitorsTilePayload) -> _Headline | None:
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


def _stale_result_reason(pin: MonitorsPin, stored: MonitorsPinResult) -> str | None:
    """Why this stored evaluation may not be republished for this monitor.

    Belt to the repair pass's braces. Re-evaluation is keyed on
    ``(pin, watermark)`` and a monitor can change UNDER a watermark: the
    repair pass narrows a spec and recomposes a label between loads, and the
    stored tile — the thing the surface actually renders — then names a
    monitor that no longer exists. Reuse is therefore conditional on the
    stored tile still being an evaluation of THIS monitor, as it stands now:

    * its title is the monitor's current title. A repaired monitor is
      relabelled, so the two disagree exactly when re-derivation is owed;
    * a monitor whose number is ABOUT ONE CELL — because it breaks a
      dimension out, or because its own filters fix one — published WHICH
      cell. Tiles stored before subjects were recorded carry none, and
      every comparability guard downstream needs one: a delta between two
      loads with no recorded subject is a phantom movement, and re-deriving
      is what supplies the fact;
    * it does not publish a cell the spec has since been narrowed away
      from. That combination is a number published under a label naming a
      different subject, refused at build time — refusing to REPUBLISH it
      costs one re-evaluation and closes the door the stored row left open.

    ``None`` means the stored tile still stands, which is the ordinary case
    and the one that keeps a load cheap.
    """
    try:
        tile = MonitorsTilePayload.model_validate(stored.payload)
    except Exception:
        return "its stored tile can no longer be read in the current tile shape"
    if tile.label != pin.label:
        return (
            f"its stored tile is titled {tile.label!r} and the monitor is now titled "
            f"{pin.label!r}"
        )
    fixed = dict(_eq_filters_of(pin.spec))
    for dimension, value in tile.headline_subject.items():
        expected = fixed.get(dimension)
        if expected is not None and expected != value:
            return (
                f"its stored tile publishes {dimension}={value!r} under a spec now narrowed "
                f"to {dimension}={expected!r}"
            )
    if tile.status == "ok" and (pin.spec.dimensions or fixed) and not tile.headline_subject:
        about = (
            "it breaks out " + " and ".join(pin.spec.dimensions)
            if pin.spec.dimensions
            else "its filters fix one cell (" + ", ".join(sorted(fixed)) + ")"
        )
        return f"{about} and its stored tile does not record which cell its number is about"
    return None


def _spec_names_one_cell(spec: TypedInvestigationSpec) -> bool:
    """Does this spec pin down exactly one cell of its own breakdown?

    True when every dimension it breaks out is also fixed by a single-value
    equality filter — which is what :func:`_narrowed_to_cell` produces, and
    what makes a monitor's subject invariant across loads by construction.
    """
    if not spec.dimensions:
        return True
    fixed = {dimension for dimension, _ in _eq_filters_of(spec)}
    return all(dimension in fixed for dimension in spec.dimensions)


def _subject_mismatch(
    pin: MonitorsPin, prior_label: str, current_label: str
) -> str | None:
    """Why these two measurements are of two different subjects, or ``None``.

    The other four tests in :func:`_not_comparable_reason` — no headline, no
    prior value, a changed unit, a changed metric — all pass across a rank
    flip. A monitor over a ranked breakdown headlines whatever ranks first,
    so without this a delta between two payers is published as a movement,
    gated material, counted, and explained as adjudication run-out.

    FAILS CLOSED: both sides blank is NOT comparable. That branch covers
    every tile stored before subjects were recorded, so treating it as
    comparable would leave the guard unreachable on precisely the rows it
    exists for. A comparison that rests on an unrecorded subject is not a
    comparison somebody can check, and this platform withholds those.
    """
    if _spec_names_one_cell(pin.spec):
        # The spec fixes the cell, so both sides measured it whatever their
        # payloads recorded — including tiles stored before subjects were.
        return None
    if prior_label and prior_label == current_label:
        return None
    if not prior_label and not current_label:
        return (
            "this monitor measures a ranked breakdown and neither load recorded which cell it "
            "headlined, so there is no way to tell whether these two numbers are two "
            "measurements of one cell or one measurement each of two different cells"
        )
    if not prior_label:
        return (
            "this monitor measures a ranked breakdown and the earlier load did not record which "
            "cell it headlined, so a delta between them could be a comparison of two different "
            f"cells; this load's is {current_label!r}"
        )
    if not current_label:
        return (
            "this monitor measures a ranked breakdown and this load did not record which cell it "
            f"headlined, so it cannot be compared against the earlier load's {prior_label!r}"
        )
    return (
        f"the earlier load's leading cell was {prior_label!r} and this one's is "
        f"{current_label!r}, so a delta between them would be a comparison of two different "
        "subjects rather than a movement in one"
    )


def _not_comparable_reason(
    pin: MonitorsPin, prior: MonitorsTilePayload, headline: _Headline | None
) -> str | None:
    """Are these two loads two measurements of one thing?

    A percentage between mismatched sides is worse than no percentage: it
    looks exactly like a movement.
    """
    if headline is None:
        return (
            "this load produced no value for the monitored metric, so there is nothing to "
            "compare against the prior load"
        )
    if prior.value is None:
        return (
            f"the prior load ({prior.watermark_id}) produced no value for this monitor, so no "
            "movement can be claimed"
        )
    if prior.unit != headline.unit:
        return (
            f"the prior load measured this monitor in {prior.unit or 'an unknown unit'} and this "
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
    pin: MonitorsPin, baseline: MonitorsTilePayload | None, tile: MonitorsTilePayload
) -> str | None:
    """The same two tests, applied to the baseline comparison."""
    if baseline is None:
        # No stored evaluation at the baseline load: the pin carries a
        # number and nothing about which cell produced it. Safe only when
        # the spec itself fixes the cell.
        if _spec_names_one_cell(pin.spec):
            return None
        return (
            "this monitor measures a ranked breakdown and the load its baseline was captured at "
            "was not recorded as an evaluation, so there is no way to tell whether the "
            "baseline number belongs to the cell this tile is showing"
        )
    mismatch = _subject_mismatch(
        pin, baseline.headline_subject_label, tile.headline_subject_label
    )
    if mismatch is not None:
        return mismatch
    return None


def _assert_subject_matches_label(pin: MonitorsPin, headline: _Headline | None) -> None:
    """A tile may not name one subject and publish another's number.

    Checked at PAYLOAD BUILD, on every tile, rather than in a test: a tile
    displaying one payer's 29.5% under the title "Pinnacle Health Plan:
    22.9%" certifies itself ``grade: direct``, and no other stage is in a
    position to notice.

    The comparison is against the SPEC's own fixed cell, not against the
    label's prose — a label is words somebody may have typed, and this must
    not fail because an analyst titled their monitor "Pinnacle's problem".
    """
    if headline is None or not headline.subject:
        return
    fixed = dict(_eq_filters_of(pin.spec))
    for dimension, value in headline.subject:
        expected = fixed.get(dimension)
        if expected is not None and expected != value:
            raise ReviError(
                f"monitors tile for pin {pin.id!r} would publish {dimension}={value!r} under a "
                f"spec narrowed to {dimension}={expected!r}: a tile whose label and value name "
                "different subjects is refused here rather than rendered",
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
) -> MonitorsDeltaPayload:
    # THE DIFFERENCE IS ONLY PUBLISHED WHEN IT MEANS SOMETHING. Subtracting
    # two numbers always succeeds; that is the problem. When the two sides
    # are not two measurements of one thing — a rank flip, a changed unit, a
    # changed contract — the arithmetic still produces 0.035823, and any
    # renderer reading `delta_text` without first reading `comparable`
    # publishes "up 3.6 points" for a movement that did not happen. Both
    # READINGS stay on the payload, because both are real; only the
    # difference between them is withheld.
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
    return MonitorsDeltaPayload(
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
        # nothing for it to qualify, and publishing it anyway attaches a
        # causal mechanism to a movement that did not happen. The dates are
        # still on the tile for anyone who wants them.
        same_window=same_window and comparable,
        subject_label=subject_label,
        prior_subject_label=prior_subject_label,
        material=verdict.material,
        threshold_source=verdict.threshold_source,  # type: ignore[arg-type]
        below_governed_gate=verdict.below_governed_gate,
        materiality_rule=verdict.rule,
        materiality_note=verdict.note,
    )


def _adds_something(tile: MonitorsTilePayload) -> bool:
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
