"""Pack-registry error hierarchy.

Governance violations (forbidden overlay overrides, thresholds outside the
declared range) raise :class:`revi_kernel.errors.PolicyDeniedError` — they
cross capability boundaries with the stable ``POLICY_DENIED`` code. The
errors below are pack-internal data problems: malformed YAML, broken layer
composition, and snapshot-integrity failures.
"""

from __future__ import annotations


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
