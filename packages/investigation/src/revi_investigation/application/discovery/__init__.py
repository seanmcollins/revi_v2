"""The discovery tool family — orientation reads that choose an approach.

The agentic-resolution constitution names two closed tool families. Compute
operations answer questions; discovery operations decide *how* a question
could be answered here at all. This package is the second one, in one
governed API.
"""

from revi_investigation.application.discovery.model import (
    CapabilityReport,
    ConceptExpression,
    ConceptResolution,
    DimensionCensus,
    DimensionValue,
    DiscoveryKind,
    DiscoveryNote,
    DiscoveryProvenance,
    MeasureAvailability,
    MeasureProfile,
    SubjectMatch,
    SubjectPresence,
)
from revi_investigation.application.discovery.service import (
    MAX_CENSUS_VALUES,
    UNPOPULATED,
    DiscoveryRefused,
    DiscoveryService,
)

__all__ = [
    "MAX_CENSUS_VALUES",
    "UNPOPULATED",
    "CapabilityReport",
    "ConceptExpression",
    "ConceptResolution",
    "DimensionCensus",
    "DimensionValue",
    "DiscoveryKind",
    "DiscoveryNote",
    "DiscoveryProvenance",
    "DiscoveryRefused",
    "DiscoveryService",
    "MeasureAvailability",
    "MeasureProfile",
    "SubjectMatch",
    "SubjectPresence",
]
