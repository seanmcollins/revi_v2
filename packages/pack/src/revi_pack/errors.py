"""Pack-registry error hierarchy.

Governance violations (forbidden overlay overrides, thresholds outside the
declared range) raise :class:`revi_kernel.errors.PolicyDeniedError` — they
cross capability boundaries with the stable ``POLICY_DENIED`` code. The
``PackError`` family below covers pack-internal data problems: malformed
YAML, broken layer composition, and snapshot-integrity failures.

:class:`PackCatalogConformanceError` is the third kind and sits between them:
a *composed* pack is internally valid but names semantics the catalog does
not define. It crosses a capability boundary (two independently versioned
bodies of governed content disagreeing), so it carries a stable §12 code.
"""

from __future__ import annotations

from typing import Any

from revi_kernel.errors import UnsupportedConceptError


class PackError(ValueError):
    """Base class for pack-registry data errors."""


class PackLoadError(PackError):
    """A pack layer directory or YAML document is malformed."""


class PackCompositionError(PackError):
    """Layer composition is structurally invalid (missing base, bad order,
    an override referencing an artifact that does not exist below it)."""


class PackIntegrityError(PackError):
    """A composed snapshot violates an integrity invariant (duplicate ids,
    alias owned by two concepts, unresolved playbook references, fingerprint
    collisions)."""


class PackCatalogConformanceError(UnsupportedConceptError):
    """Governed pack content names semantics the semantic catalog does not
    define (see :mod:`revi_pack.conformance`).

    Carries ``UNSUPPORTED_CONCEPT``: the §12 code for "this names something
    the source cannot express", and the same code the DuckDB compiler raises
    for this condition at probe time — so composition time and probe time
    return one verdict rather than two. ``POLICY_DENIED`` would be wrong
    (nothing here violates a rule about who may change what; the content is
    simply not expressible); ``BINDING_AMBIGUOUS`` would be wrong in the
    other direction (there is no candidate to choose between, there is none
    at all).

    ``details`` carries every offending pair, never just the first:
    ``{"pairs": [{"metric": …, "dimension": …}, …]}``.
    """

    def __init__(
        self,
        message: str,
        *,
        pairs: tuple[tuple[str, str], ...],
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {
            "pairs": [{"metric": metric, "dimension": dimension} for metric, dimension in pairs]
        }
        merged.update(details or {})
        super().__init__(message, details=merged)
        self.pairs = pairs
