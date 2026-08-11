"""DrillInto cohort pinning (design §7.5): intensional definition →
extensional pinned set, at the session watermark.

One gesture may drill several referents at once ("just the top three
payers"); they collapse into ONE cohort: when every target carries a
dimension value on the same dimension, the definition is the parent's
effective scope conjoined with ``dimension IN (values)`` at the CLAIM
entity over the parent window. A single target reuses its registered
drillable definition verbatim (and its already-pinned cohort when the pin
is at the session watermark — re-drilling is free).

Window honesty: a cohort definition's window must be answerable at the
cohort's entity. When the parent window's basis is not bound at the CLAIM
grain (e.g. a POST-basis cash window), the cohort is pinned WITHOUT the
window and a warning says so — silently re-basing the window would change
its meaning.

The pinned :class:`CohortRef` is written to the cohort store (metadata)
and back onto every drilled referent, so later turns can re-address the
exact population the analyst saw.
"""

from __future__ import annotations

from dataclasses import replace

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.ports import (
    CohortStore,
    ReferentRegistryStore,
    RegisteredReferent,
)
from revi_investigation.application.rendering import date_phrase, level_phrase
from revi_investigation.domain.context import AnalysisSpec
from revi_investigation.domain.records import Session
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.errors import AmbiguousRefinementError, ReferentNotFoundError
from revi_kernel.filters import Predicate, PredicateOp, and_merge
from revi_kernel.refs import DimensionRef, EntityGrain, ReferentId


class PinCohortService:
    def __init__(
        self,
        repository: AnalyticalRepository,
        cohorts: CohortStore,
        registry: ReferentRegistryStore,
        catalog: CatalogSnapshot,
    ) -> None:
        self._repository = repository
        self._cohorts = cohorts
        self._registry = registry
        self._catalog = catalog

    async def pin(
        self,
        *,
        session: Session,
        parent_spec: AnalysisSpec,
        targets: tuple[ReferentId, ...],
        warnings: list[str],
    ) -> CohortRef:
        entries: list[RegisteredReferent] = []
        for target in targets:
            entry = await self._registry.resolve(session.id, target)
            if entry is None:
                raise ReferentNotFoundError(
                    f"referent {target.value!r} is not in the live registry",
                    details={"referent": target.value},
                )
            if entry.cohort_definition is None:
                raise AmbiguousRefinementError(
                    f"referent {target.value!r} is not drillable (no cohort definition)",
                    details={"referent": target.value},
                )
            entries.append(entry)

        # re-drilling one already-pinned referent at the same watermark is free
        if len(entries) == 1:
            existing = entries[0].cohort
            if (
                existing is not None
                and existing.pinned is not None
                and existing.pinned.watermark.id == session.watermark.id
            ):
                return existing

        definition = self._merged_definition(entries, parent_spec)
        definition = self._honest_window(definition, warnings)

        materialization = await self._repository.materialize_cohort(
            definition, watermark=session.watermark
        )
        cohort = CohortRef(
            id=materialization.cohort_id,
            definition=definition,
            origin=targets[0],
            size=materialization.size,
            pinned=materialization,
        )
        await self._cohorts.save(cohort, tenant=session.tenant, session_id=session.id)
        for entry in entries:
            await self._registry.update(replace(entry, cohort=cohort))
        return cohort

    # ------------------------------------------------------------- internals

    def _merged_definition(
        self, entries: list[RegisteredReferent], parent_spec: AnalysisSpec
    ) -> CohortDefinition:
        if len(entries) == 1:
            definition = entries[0].cohort_definition
            assert definition is not None
            return definition
        dimension_values = [entry.dimension_value for entry in entries]
        if any(dv is None for dv in dimension_values):
            raise AmbiguousRefinementError(
                "cannot combine these referents into one cohort: not all of them "
                "identify a single dimension value",
                details={"referents": [entry.referent.value for entry in entries]},
            )
        dimensions = {dv[0] for dv in dimension_values if dv is not None}
        if len(dimensions) != 1:
            raise AmbiguousRefinementError(
                "cannot combine referents across different dimensions into one cohort",
                details={"dimensions": sorted(dimensions)},
            )
        dimension = next(iter(dimensions))
        values = tuple(dv[1] for dv in dimension_values if dv is not None)
        return CohortDefinition(
            entity=EntityGrain.CLAIM,
            scope=and_merge(
                parent_spec.context.effective_scope(),
                Predicate(DimensionRef(dimension), PredicateOp.IN, values),
            ),
            window=parent_spec.context.window,
        )

    def _honest_window(
        self, definition: CohortDefinition, warnings: list[str]
    ) -> CohortDefinition:
        window = definition.window
        if window is None:
            return definition
        if self._catalog.date_basis_column(definition.entity, window.basis) is not None:
            return definition
        warnings.append(
            f"cohort pinned without its window: the {date_phrase(window.basis.id)} cannot be "
            f"read at the {level_phrase(str(definition.entity.value))}, so the population you "
            "pinned "
            "covers the whole history rather than the period you named"
        )
        return CohortDefinition(entity=definition.entity, scope=definition.scope, window=None)
