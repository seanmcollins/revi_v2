"""PHI masking (design §4.1) — re-exported from the contracts package.

The masking primitives are pure functions on ``PhiClass`` and are part of the
catalog's public contract (repository adapters mask row evidence before frames
leave the boundary), so they live in ``revi_catalog_contracts.masking``; this
module re-exports them for callers holding the catalog implementation.
"""

from revi_catalog_contracts.masking import (
    SUPPRESSED_TOKEN,
    field_phi_class,
    mask_value,
    masked_columns,
)

__all__ = ["SUPPRESSED_TOKEN", "field_phi_class", "mask_value", "masked_columns"]
