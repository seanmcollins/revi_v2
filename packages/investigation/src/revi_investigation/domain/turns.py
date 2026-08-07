"""Turn taxonomy (design §7.3 + the approved DEFINITIONAL extension)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TurnClass(StrEnum):
    NEW_INVESTIGATION = "new_investigation"
    REFINEMENT = "refinement"
    PRESENTATION_ONLY = "presentation_only"
    CONTEXT_CONTROL = "context_control"
    META = "meta"
    CLARIFICATION_RESPONSE = "clarification_response"
    # Approved design extension: "what is PR3" / "what is denial rate" —
    # answered from governed pack content with provenance, zero probes.
    DEFINITIONAL = "definitional"


# Turn classes that must execute ZERO warehouse probes (asserted in tests
# per acceptance criterion §18.1-14; DEFINITIONAL is included by extension).
ZERO_PROBE_TURN_CLASSES = (
    TurnClass.PRESENTATION_ONLY,
    TurnClass.CONTEXT_CONTROL,
    TurnClass.META,
    TurnClass.DEFINITIONAL,
)


@dataclass(frozen=True, slots=True)
class TurnClassification:
    turn_class: TurnClass
    confidence: float
    clarification_question: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class ClarificationRequest:
    """A first-class successful outcome, not an error (design §2.8, §12)."""

    question: str
    options: tuple[str, ...] = ()
    reason: str | None = None
