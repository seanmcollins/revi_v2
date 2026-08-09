"""Answerable date-basis resolution (design §5.3, §6.6 step 3).

A metric contract declares a **primary basis plus allowed alternates**. The
semantic catalog declares which bases the warehouse actually **binds** at
each entity's base view. Those are two independent facts, and nothing
reconciled them: ``denial_rate`` declares ``remit`` primary at the CLAIM
grain (the MAP AR-5 convention), the catalog binds ``remit`` only on the
remit/transaction/denial views, and so the flagship year-over-year denial
rate question planned cleanly, passed §6.6, compiled to SQL, and died there
with ``DATE_BASIS_INVALID: date basis 'remit' is not bound for entity
'claim'`` — a §12 refusal raised by the SQL compiler, past the validation
pass whose third step exists to catch exactly this.

§5.3 already says what to do: *"a primary basis plus allowed alternates …
using an allowed alternate is permitted but labeled in output and
provenance; using a disallowed basis yields ``DATE_BASIS_INVALID``."* So:

- a basis the contract does not allow is refused, unchanged;
- a basis the contract allows but the warehouse does not bind at the
  contract's entity grain **falls back**, deterministically, to the first
  allowed basis that *is* bound — the contract's primary first, then the
  contract's declared alternates in their authored order;
- the substitution is **labeled**: §6.6 emits a warning naming the basis
  read and the one that was unavailable, and because interpretation
  resolves the window on the answerable basis, the context header shows
  the basis actually used rather than one nothing read;
- when the warehouse binds **none** of the allowed bases the refusal
  stands — now raised at plan time, with a §6.6 reason naming the entity
  and every basis tried, instead of as a compiler error after the click.

The contract's own semantics are untouched: ``primary_date_basis`` still
means what the pack author wrote, the fingerprint is unchanged, and the
substitution is a property of *this warehouse's bindings*, which is why it
is computed from the catalog and never written into the pack.

The rule is applied by every layer that picks a basis — interpretation
(which fixes the window basis, and therefore the header), planning (which
groups metrics into probes), and §6.6 validation (which re-checks and
labels) — because a basis whose value depended on which layer chose it
would not be a governed basis at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from revi_calculation_contracts.contract import MetricContract
from revi_catalog_contracts.model import CatalogSnapshot
from revi_kernel.errors import DateBasisInvalidError
from revi_kernel.refs import DateBasisRef


@dataclass(frozen=True, slots=True)
class BasisResolution:
    """The basis a metric will actually be read on, and why."""

    #: The basis to use — always one of the contract's allowed bases.
    basis: DateBasisRef
    #: The basis that was asked for (explicitly, or the contract's primary).
    requested: DateBasisRef
    #: Entity name the binding was judged against (``None`` when the catalog
    #: does not describe the contract's grain, in which case nothing was
    #: substituted and later validation owns the verdict).
    entity: str | None

    @property
    def substituted(self) -> bool:
        return self.basis != self.requested


def basis_bound_at(catalog: CatalogSnapshot, contract: MetricContract, basis: DateBasisRef) -> bool:
    """Does this warehouse carry ``basis`` on the contract's own base view?

    An entity the catalog does not describe is *not* treated as unbound: a
    missing entity is a different failure (``UNSUPPORTED_CONCEPT`` in §6.6
    step 1) and answering it here as "basis unavailable" would name the
    wrong cause.
    """
    entity = catalog.entity(contract.entity_grain)
    if entity is None:
        return True
    return entity.date_basis_column(basis) is not None


def substitution_warning(contract: MetricContract, resolution: BasisResolution) -> str | None:
    """The §6.6 label for a substituted basis, or ``None`` when none was.

    Prefixed with the same ``alternate_basis_used`` token the ordinary
    alternate-basis warning carries, so one grep finds every answer that did
    not read its metric on the basis the contract prefers, and then says the
    part that token cannot: *why* the preferred basis was not read.
    """
    if not resolution.substituted:
        return None
    return (
        f"alternate_basis_used: {contract.id!r} computed on the {resolution.basis.id!r} date "
        f"basis — the {resolution.requested.id!r} basis it asks for is not available at the "
        f"{resolution.entity!r} grain in this warehouse"
    )


def resolve_answerable_basis(
    contract: MetricContract,
    requested: DateBasisRef | None,
    catalog: CatalogSnapshot,
) -> BasisResolution:
    """Pick the basis this metric can actually be read on.

    ``requested`` is the analyst's (or the model's, or a playbook's) choice;
    ``None`` means "the contract's primary". A requested basis the contract
    forbids raises ``DATE_BASIS_INVALID`` exactly as before — the fallback
    only ever chooses among bases the contract already declared legal.
    """
    wanted = requested if requested is not None else contract.primary_date_basis
    if not contract.allows_date_basis(wanted):
        raise DateBasisInvalidError(
            f"date basis {wanted.id!r} is not allowed for metric {contract.id!r} "
            f"(allowed: {[b.id for b in contract.allowed_date_bases]})",
            details={"metric": contract.id, "basis": wanted.id},
        )
    entity = catalog.entity(contract.entity_grain)
    if entity is None:
        # Not this module's failure to name: §6.6 step 1 refuses an entity
        # the catalog does not describe, with the right code and reason.
        return BasisResolution(basis=wanted, requested=wanted, entity=None)

    # Deterministic preference order: what was asked for, then the
    # contract's primary, then its alternates in authored order.
    candidates: list[DateBasisRef] = []
    for candidate in (wanted, contract.primary_date_basis, *contract.allowed_date_bases):
        if candidate not in candidates and contract.allows_date_basis(candidate):
            candidates.append(candidate)
    for candidate in candidates:
        if entity.date_basis_column(candidate) is not None:
            return BasisResolution(basis=candidate, requested=wanted, entity=entity.name)
    raise DateBasisInvalidError(
        f"metric {contract.id!r} cannot be read at the {entity.name!r} grain: this warehouse "
        f"binds none of its allowed date bases {[b.id for b in candidates]}",
        details={
            "metric": contract.id,
            "entity": entity.name,
            "basis": wanted.id,
            "tried": [b.id for b in candidates],
        },
    )
