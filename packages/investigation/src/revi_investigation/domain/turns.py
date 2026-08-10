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
class ClarificationBinding:
    """What one clarification option MEANS, in governed ids.

    An option that is only a sentence resolves against nothing: tapping it
    re-enters the reply as free text to be classified and interpreted all
    over again. A reply sent on the dedicated ``clarification_response``
    channel — byte-for-byte an option the platform just offered — then comes
    back classified ``refinement`` at confidence 0.45, as a ROOT
    investigation asking a different question, with the analyst's original
    question gone.

    So an option the platform authored carries the ids it would use. The
    reply is then resolved by *matching*, not by re-reading: an exact match
    is the analyst choosing a thing the platform already named, and the
    engine applies that thing to the question it interrupted.

    The same structure is what makes an option checkable. A value-existence
    guard that covers dimensions with a DECLARED domain and silently skips
    the open ones offers "Summit Peak is a facility" over a warehouse
    holding six facilities, none of them that.

    ``kind`` is the closed set of things an option can bind:

    * ``date_basis`` — read the metric on ``basis`` instead;
    * ``metric_cut`` — measure ``metric_ids`` broken out by
      ``dimension_ids``;
    * ``predicate_value`` — the analyst meant these ``scope`` values;
    * ``grounded_option`` — a model-authored alternative, grounded in pack
      and catalog ids (see the interpretation schema's
      ``GroundedOptionModel``).
    """

    option: str
    kind: str
    metric_ids: tuple[str, ...] = ()
    dimension_ids: tuple[str, ...] = ()
    playbook_id: str | None = None
    #: ``(dimension id, values)`` the option would filter to.
    scope: tuple[tuple[str, tuple[str, ...]], ...] = ()
    basis: str | None = None

    @property
    def deterministic(self) -> bool:
        """Can this option be APPLIED without asking anything further?

        Only the bindings the platform derived itself from governed content
        — never a model's proposal, which is a suggestion and stays one.
        """
        return self.kind in ("date_basis", "metric_cut")


@dataclass(frozen=True, slots=True)
class ClarificationRequest:
    """A first-class successful outcome, not an error (design §2.8, §12)."""

    question: str
    options: tuple[str, ...] = ()
    reason: str | None = None
    #: What each option means, for the options the platform can say it for.
    #: Positionally independent of ``options`` — matched by ``option`` text
    #: — so dropping an option never silently rebinds another.
    bindings: tuple[ClarificationBinding, ...] = ()

    def binding_for(self, option: str) -> ClarificationBinding | None:
        """The binding for an option string, matched as the analyst sent it."""
        wanted = " ".join(option.split()).casefold().rstrip(".")
        for binding in self.bindings:
            if " ".join(binding.option.split()).casefold().rstrip(".") == wanted:
                return binding
        return None
