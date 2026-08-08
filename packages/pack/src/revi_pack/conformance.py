"""Pack ↔ semantic-catalog conformance (design §5.2, §6.6, §12).

A metric contract's ``exclusions`` is governed meaning: the compiler applies
it as ``FILTER (WHERE NOT <expr>)`` to numerator *and* denominator, so it
silently reshapes the population behind every cut of the metric. If the
dimension it names does not exist in the catalog, nothing about the contract
is computable — but nothing announces that either, because the failure only
happens if and when some question happens to reach the metric.

Why this check lives at the pack layer
--------------------------------------
``PlanValidationService`` already rejects unresolvable concepts, and the
DuckDB compiler already raises ``UNSUPPORTED_CONCEPT`` on an unknown filter
dimension. Neither is sufficient, because both are *demand-driven*: they only
ever see the metrics that a question actually reaches. A contract no
playbook names, or one whose probes are pruned as unanswerable before they
compile, is never examined at all — it sits in the pack looking authoritative
for as long as nobody asks. That is precisely how seven inverted exclusions
survived in ``packs/base-rcm`` (see its ``NOTES.md``): six referenced
non-catalog dimensions, so every probe touching them was pruned, so the
polarity error never surfaced as a wrong number.

A conformance pass over the *whole composed pack* against the catalog has no
such blind spot. It enumerates content rather than waiting for traffic, so
unreachable content is exactly as visible as hot content, and it runs once at
composition — which is also where a deployment can still refuse to start.

Scope: ``exclusions`` only
--------------------------
This guard covers contract ``exclusions``. The same defect class exists in
contract-internal ``Filtered`` predicates (``numerator``/``denominator``
``where:`` clauses) and widening the check to them is the obvious next step;
``packs/base-rcm/NOTES.md`` enumerates the seven contracts that would trip it
today, all blocked on the same catalog work. Widening it here — while the
base pack still fails it — would only mean shipping a guard nobody could turn
on.

"Resolves nowhere" means *absent*, not *uncertified*
----------------------------------------------------
An **uncertified** dimension (``rarc_synthetic``, ``revenue_code``) resolves
perfectly well: the compiler binds its column, sets ``uncertified``, and the
answer's grade is downgraded to DISCOVERY. That is a deliberate, already-
handled mechanism for saying "this evidence is weak", and pack content is
allowed to use it. An **absent** dimension resolves to nothing at all: there
is no column, no grade, no number — only an exception, thrown at whatever
future moment someone finally asks. Only the second is a conformance
failure. Conflating them would make it impossible to author discovery-grade
content on purpose, and would punish honesty about weak evidence.
"""

from __future__ import annotations

from revi_catalog_contracts import CatalogSnapshot
from revi_kernel.filters import iter_predicates
from revi_pack.domain import PackSnapshot
from revi_pack.errors import PackCatalogConformanceError

__all__ = [
    "PackCatalogConformanceError",
    "unresolved_exclusion_dimensions",
    "validate_pack_catalog_conformance",
]


def unresolved_exclusion_dimensions(
    snapshot: PackSnapshot, catalog: CatalogSnapshot
) -> tuple[tuple[str, str], ...]:
    """Every ``(metric_id, dimension_id)`` an ``exclusions`` clause names that
    the catalog does not define, sorted and deduplicated.

    Predicates are collected at any polarity and any nesting depth
    (``iter_predicates`` walks ``And``/``Or``/``Not``), because a dimension
    that does not exist fails to compile wherever it sits in the tree.
    """
    offenders: set[tuple[str, str]] = set()
    for contract in snapshot.metric_contracts:
        if contract.exclusions is None:
            continue
        for predicate in iter_predicates(contract.exclusions):
            dimension_id = predicate.dimension.id
            if catalog.dimension(dimension_id) is None:
                offenders.add((contract.id, dimension_id))
    return tuple(sorted(offenders))


def validate_pack_catalog_conformance(snapshot: PackSnapshot, catalog: CatalogSnapshot) -> None:
    """Raise unless every metric contract's ``exclusions`` resolves against
    the catalog.

    Cheap enough to run unconditionally at a composition root: one pass over
    the contracts that declare exclusions, dictionary lookups only, no I/O.

    Raises:
        PackCatalogConformanceError: naming **every** offending
            ``(metric_id, dimension_id)`` pair, not just the first — the
            failure mode this guards against is systematic authoring error,
            and stopping at the first offender turns one review into seven.
    """
    offenders = unresolved_exclusion_dimensions(snapshot, catalog)
    if not offenders:
        return
    listed = ", ".join(f"{metric}.exclusions -> {dimension!r}" for metric, dimension in offenders)
    raise PackCatalogConformanceError(
        f"pack {snapshot.version.pack_id!r}@{snapshot.version.version} does not conform to the "
        f"semantic catalog: {len(offenders)} metric exclusion(s) name dimensions the catalog does "
        f"not define ({listed}). An exclusion removes a population from both sides of the metric; "
        "one that cannot resolve removes nothing and is never reported. Certify the dimension in "
        "the catalog or drop the exclusion from the contract.",
        pairs=offenders,
        details={"pack": snapshot.version.pack_id, "pack_version": snapshot.version.version},
    )
