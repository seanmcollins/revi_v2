"""Stable error codes (design §12) and the domain exception hierarchy.

Every error that crosses a capability boundary carries one of the fifteen
stable codes. Provider- or driver-specific exceptions must never cross a
port; adapters translate them into these.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    BINDING_AMBIGUOUS = "BINDING_AMBIGUOUS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED_CONCEPT = "UNSUPPORTED_CONCEPT"
    POLICY_DENIED = "POLICY_DENIED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    QUERY_BUDGET_EXCEEDED = "QUERY_BUDGET_EXCEEDED"
    AMBIGUOUS_REFINEMENT = "AMBIGUOUS_REFINEMENT"
    REFERENT_NOT_FOUND = "REFERENT_NOT_FOUND"
    CONTEXT_CONFLICT = "CONTEXT_CONFLICT"
    GRAIN_INCOMPATIBLE = "GRAIN_INCOMPATIBLE"
    DATE_BASIS_INVALID = "DATE_BASIS_INVALID"
    WATERMARK_STALE = "WATERMARK_STALE"
    DATA_LOADING = "DATA_LOADING"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    SOURCE_CAPABILITY_UNSUPPORTED = "SOURCE_CAPABILITY_UNSUPPORTED"


class ReviError(Exception):
    """Base class for all Revi domain errors.

    ``details`` is a small, serializable mapping safe to surface to clients
    and record in traces — never raw rows, SQL, or provider payloads.
    """

    code: ErrorCode

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}


class BindingAmbiguousError(ReviError):
    code = ErrorCode.BINDING_AMBIGUOUS


class InsufficientEvidenceError(ReviError):
    code = ErrorCode.INSUFFICIENT_EVIDENCE


class UnsupportedConceptError(ReviError):
    code = ErrorCode.UNSUPPORTED_CONCEPT


class PolicyDeniedError(ReviError):
    code = ErrorCode.POLICY_DENIED


class SourceUnavailableError(ReviError):
    code = ErrorCode.SOURCE_UNAVAILABLE


class QueryBudgetExceededError(ReviError):
    code = ErrorCode.QUERY_BUDGET_EXCEEDED


class AmbiguousRefinementError(ReviError):
    code = ErrorCode.AMBIGUOUS_REFINEMENT


class ReferentNotFoundError(ReviError):
    code = ErrorCode.REFERENT_NOT_FOUND


class ContextConflictError(ReviError):
    code = ErrorCode.CONTEXT_CONFLICT


class GrainIncompatibleError(ReviError):
    code = ErrorCode.GRAIN_INCOMPATIBLE


class DateBasisInvalidError(ReviError):
    code = ErrorCode.DATE_BASIS_INVALID


class WatermarkStaleError(ReviError):
    code = ErrorCode.WATERMARK_STALE


class DataLoadingError(ReviError):
    code = ErrorCode.DATA_LOADING


class ReconciliationFailedError(ReviError):
    code = ErrorCode.RECONCILIATION_FAILED


class SourceCapabilityUnsupportedError(ReviError):
    code = ErrorCode.SOURCE_CAPABILITY_UNSUPPORTED


ERROR_TYPES: dict[ErrorCode, type[ReviError]] = {
    cls.code: cls
    for cls in (
        BindingAmbiguousError,
        InsufficientEvidenceError,
        UnsupportedConceptError,
        PolicyDeniedError,
        SourceUnavailableError,
        QueryBudgetExceededError,
        AmbiguousRefinementError,
        ReferentNotFoundError,
        ContextConflictError,
        GrainIncompatibleError,
        DateBasisInvalidError,
        WatermarkStaleError,
        DataLoadingError,
        ReconciliationFailedError,
        SourceCapabilityUnsupportedError,
    )
}
