"""The deep-research launch offer — the shapes other surfaces carry.

Kept apart from the report contracts on purpose. An answer, a lead and a
worklist card can all OFFER a run, and none of them should have to depend
on the shape of a finished report to do it. This module holds the two
things an offer is made of, and nothing else: which denials, and the words
on the affordance.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from revi_investigation_contracts.refinements import ClosedModel

#: Which populations a run targets. ``all_open`` is every denial still
#: waiting on an answer; the others narrow it to named values.
SelectorKindLiteral = Literal["all_open", "payer", "recovery_class", "facility"]


class DeepResearchSelector(ClosedModel):
    """Which denials a run is about."""

    kind: SelectorKindLiteral = "all_open"
    #: Names to narrow to, exactly as they appear in the data. Ignored, and
    #: refused if given, when the kind is every open denial.
    values: list[str] = Field(default_factory=list)
    #: The one sentence a reader sees describing this population.
    label: str = ""


class DeepResearchAffordance(ClosedModel):
    """A place another surface can offer to start a run from.

    Carried additively on payloads that already name a population — a lead
    about one payer's denials can offer the run it would launch, with the
    population already filled in. Naming the selector rather than a
    sentence is what makes the offer honest: what the reader taps is
    exactly what runs, and no client has to parse a sentence back into a
    request.
    """

    population: DeepResearchSelector
    #: The words on the affordance.
    label: str
    #: What the reader gets, in one sentence.
    description: str = ""
