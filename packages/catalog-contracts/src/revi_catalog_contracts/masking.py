"""PHI masking primitives (design §4.1).

Masking happens **before** values leave the capability boundary toward models
or UI — row evidence, profiles, and metadata are masked at the source. These
are pure functions on :class:`~revi_catalog_contracts.model.PhiClass`, shared
by the catalog implementation and repository adapters (which is why they live
in the contracts package).

Conventions:

- ``none``      → value passes through unchanged.
- ``indirect``  → deterministic token ``MASKED-<10 hex>`` (a stable SHA-256
  prefix, so equal values mask equally — grouping and eyeballing survive
  masking without revealing the value).
- ``direct``    → the constant ``[SUPPRESSED]`` (no information leaves).
- ``None`` values pass through (nullness is not PHI).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from revi_catalog_contracts.model import CatalogSnapshot, PhiClass
from revi_kernel.filters import Scalar
from revi_kernel.refs import FieldRef

SUPPRESSED_TOKEN = "[SUPPRESSED]"
_MASK_PREFIX = "MASKED-"


def mask_value(value: Scalar, phi_class: PhiClass) -> Scalar:
    """Mask one value per its PHI class. Pure and deterministic."""
    if value is None or phi_class is PhiClass.NONE:
        return value
    if phi_class is PhiClass.INDIRECT:
        digest = hashlib.sha256(f"{type(value).__name__}:{value}".encode()).hexdigest()[:10]
        return f"{_MASK_PREFIX}{digest}"
    return SUPPRESSED_TOKEN


def field_phi_class(catalog: CatalogSnapshot, field: str | FieldRef) -> PhiClass:
    """The PHI class of a row-evidence field.

    Fields resolve through catalog dimensions (an optional ``entity.`` prefix
    is stripped). Non-dimension fields — measures, primary keys, date-basis
    columns — carry no PHI classification in this catalog and default to
    ``none``.
    """
    field_id = field.id if isinstance(field, FieldRef) else field
    head, dot, rest = field_id.partition(".")
    if dot and catalog.entity_named(head) is not None:
        field_id = rest
    return catalog.phi_class(field_id)


def masked_columns(catalog: CatalogSnapshot, fields: Iterable[str | FieldRef]) -> tuple[str, ...]:
    """The subset of ``fields`` (ids as given) whose values must be masked
    before leaving the boundary."""
    return tuple(
        field.id if isinstance(field, FieldRef) else field
        for field in fields
        if field_phi_class(catalog, field) is not PhiClass.NONE
    )
