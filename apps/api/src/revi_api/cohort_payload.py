"""The pinned cohort, said in words instead of shown as a hash (F15).

The context header carried ``cohort: coh_9f2a11…`` beside a size, and that
was the whole chip. An analyst who had just drilled "the top three payers"
was handed their own selection back in a vocabulary nobody speaks — and a
label that cannot be read cannot be checked, which makes it decoration on
a platform whose entire claim is that the context is inspectable.

Everything published here already existed on the pinned
:class:`~revi_kernel.cohort.CohortRef`; none of it was on the wire. This
module projects it:

* ``entity_grain`` — what one member IS (a claim, a claim line, a remit);
* ``definition`` — the *intensional* rule (§7.5) rendered as text, which
  is the thing that would be re-evaluated against fresh data in another
  session, not a description of the pinned rows;
* the ``origin`` referent and the turn that introduced it, resolved
  through the referent registry, so "where did this population come from?"
  is answerable from the payload rather than by walking the lineage;
* ``size``, and whether an extensional set was materialized at all.

The rendering is deliberately the same grammar the filter chips use
(``dimension op [values]``), so a cohort definition and a scope chip read
alike — they are the same algebra, and an analyst should not have to learn
it twice.
"""

from __future__ import annotations

import logging

from revi_investigation.application.ports import (
    CohortStore,
    InvestigationStore,
    ReferentRegistryStore,
)
from revi_investigation_contracts.api import CohortPayload
from revi_kernel.cohort import CohortRef
from revi_kernel.filters import And, FilterExpr, InCohort, Not, Or, Predicate, Scalar

logger = logging.getLogger("revi.api.cohort")

#: What an empty scope selects. Stated rather than rendered as "" — a
#: cohort whose definition renders blank reads like a bug or, worse, like
#: an empty population.
_EVERYTHING = "all entities in scope (no selecting predicate)"


def _value(value: Scalar) -> str:
    return "null" if value is None else str(value)


def render_filter(expr: FilterExpr) -> str:
    """A filter expression as the text an analyst can check.

    Total over the closed algebra (``And | Or | Not | Predicate |
    InCohort``): a new member would be a type error here rather than a
    silently missing clause in a definition somebody is asked to trust.
    """
    if isinstance(expr, Predicate):
        values = ", ".join(_value(v) for v in expr.values)
        if not expr.values:
            return f"{expr.dimension.id} {expr.op.value}"
        if len(expr.values) == 1:
            return f"{expr.dimension.id} {expr.op.value} {values}"
        return f"{expr.dimension.id} {expr.op.value} [{values}]"
    if isinstance(expr, And):
        rendered = [render_filter(clause) for clause in expr.clauses if not _is_empty(clause)]
        if not rendered:
            return _EVERYTHING
        return " and ".join(rendered) if len(rendered) > 1 else rendered[0]
    if isinstance(expr, Or):
        rendered = [render_filter(clause) for clause in expr.clauses]
        return "(" + " or ".join(rendered) + ")" if rendered else _EVERYTHING
    if isinstance(expr, Not):
        return f"not ({render_filter(expr.clause)})"
    if isinstance(expr, InCohort):
        return f"member of cohort {expr.cohort.id}"
    # Unreachable over the closed algebra; stated rather than rendered as
    # an empty string, which would read as "selects everything".
    return f"<unrenderable filter: {type(expr).__name__}>"


def _is_empty(expr: FilterExpr) -> bool:
    return isinstance(expr, And) and not expr.clauses


async def build_cohort_payload(
    cohort: CohortRef,
    *,
    session_id: str,
    referents: ReferentRegistryStore | None = None,
    investigations: InvestigationStore | None = None,
) -> CohortPayload:
    """Project one pinned cohort onto the wire.

    The origin lookups are best-effort by design: a registry entry that
    has aged out, or an investigation the store no longer holds, leaves
    those two fields ``None`` and costs the reader nothing else. A cohort
    chip must not fail to render because its provenance is incomplete —
    the definition, the grain and the size are the load-bearing parts and
    they come off the cohort itself.
    """
    definition = cohort.definition
    origin_investigation: str | None = None
    origin_turn: str | None = None
    if referents is not None:
        try:
            entry = await referents.resolve(session_id, cohort.origin)
        except Exception:  # pragma: no cover - defensive; see docstring
            logger.debug("could not resolve cohort origin referent", exc_info=True)
            entry = None
        if entry is not None:
            origin_investigation = entry.investigation_id
            if investigations is not None and origin_investigation:
                try:
                    investigation = await investigations.get(origin_investigation)
                except Exception:  # pragma: no cover - defensive
                    logger.debug("could not read cohort origin investigation", exc_info=True)
                    investigation = None
                if investigation is not None:
                    origin_turn = investigation.turn_id
    window = definition.window
    return CohortPayload(
        id=cohort.id,
        entity_grain=definition.entity.value,
        definition=render_filter(definition.scope),
        size=cohort.size,
        origin_referent=cohort.origin.value,
        origin_turn_id=origin_turn,
        origin_investigation_id=origin_investigation,
        window_start=window.range.start if window is not None else None,
        window_end=window.range.end if window is not None else None,
        pinned=cohort.pinned is not None,
        pinned_watermark_id=(
            cohort.pinned.watermark.id if cohort.pinned is not None else None
        ),
    )


async def cohort_payload_for(
    cohort_id: str | None,
    *,
    session_id: str,
    cohorts: CohortStore,
    referents: ReferentRegistryStore | None = None,
    investigations: InvestigationStore | None = None,
) -> CohortPayload | None:
    """The payload for a cohort id, or ``None`` when there is none to show.

    ``None`` covers both "this turn pinned no cohort" (the ordinary case)
    and "the metadata store no longer holds it" — the second is logged,
    because a header that names a cohort the store has lost is worth an
    operator's attention even though it is not worth failing a turn over.
    """
    if not cohort_id:
        return None
    try:
        cohort = await cohorts.get(cohort_id)
    except Exception:  # pragma: no cover - defensive
        logger.warning("cohort store read failed for %s", cohort_id, exc_info=True)
        return None
    if cohort is None:
        logger.warning(
            "turn context names cohort %s but the cohort store has no record of it",
            cohort_id,
        )
        return None
    return await build_cohort_payload(
        cohort,
        session_id=session_id,
        referents=referents,
        investigations=investigations,
    )


def cohort_id_from_trace(payload: object) -> str | None:
    """The cohort id a stored turn recorded, if it pinned one.

    Reads the same ``refinement.cohort`` block the engine writes, so a
    turn restored from the store shows the population the live answer
    showed rather than losing the chip on reload.
    """
    if not isinstance(payload, dict):
        return None
    refinement = payload.get("refinement")
    if not isinstance(refinement, dict):
        return None
    cohort = refinement.get("cohort")
    if not isinstance(cohort, dict):
        return None
    cohort_id = cohort.get("id")
    return cohort_id if isinstance(cohort_id, str) and cohort_id else None
