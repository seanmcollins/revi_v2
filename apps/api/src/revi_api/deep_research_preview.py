"""The generalized research preview, as a wire payload.

A research question is not the recoverability review. The review answers
one standing question over open denials and describes itself through the
closed angle catalogue; a research question can be about A/R aging, payer
behavior, revenue quality or anything else the semantic layer measures, and
the only honest way to describe what it will do is to say what the run
learned about the data and what it therefore intends to read.

That is what this module renders. Everything in it comes from
:class:`~revi_investigation.application.deep_research.loop.ResearchPreview`
— the ORIENT, CONSULT and PLAN phases of the real run, executed against the
real data through the run's own cache — so the card a reader confirms
describes the run they are about to get. Nothing here composes a sentence
of its own: the path choices arrive already worded beside the coverage
figures they quote, the reasons were written by whatever chose the
readings, and the titles come from the same formatter the report uses.
"""

from __future__ import annotations

import logging

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.deep_research import (
    ResearchPreview,
    title_of,
)
from revi_investigation.application.deep_research.general_llm import window_words
from revi_investigation.application.deep_research.loop import population_words
from revi_investigation_contracts.deep_research import (
    ConsultedNotePayload,
    GeneralizedResearchPreviewPayload,
    PlannedReadingPayload,
    ResearchPathChoicePayload,
)

logger = logging.getLogger(__name__)

#: How many orientation findings reach the card. The run records every one
#: on its walk and the trace keeps all of them; a confirmation that listed
#: twenty would be asking a reader to audit the orientation rather than
#: decide whether to spend a minute.
MAX_PATH_CHOICES = 5


def generalized_preview_payload(
    preview: ResearchPreview, catalog: CatalogSnapshot
) -> GeneralizedResearchPreviewPayload:
    """One resolved preview, in the words a reader sees."""
    orientation = preview.orientation
    return GeneralizedResearchPreviewPayload(
        research_question=orientation.question,
        population_label=population_words(orientation.population),
        window_label=window_words(orientation.window),
        path_choices=[
            ResearchPathChoicePayload(subject=note.subject, statement=note.statement)
            for note in orientation.notes[:MAX_PATH_CHOICES]
        ],
        knowledge_statement=orientation.knowledge.statement,
        knowledge_consulted=[
            ConsultedNotePayload(title=entry.title, matched_on=list(entry.matched_on))
            for entry in orientation.knowledge.entries
        ],
        readings=[
            PlannedReadingPayload(
                shape=str(angle.shape),  # type: ignore[arg-type]
                title=title_of(angle, catalog),
                reason=angle.reason,
                round=angle.round,
                chases=angle.chases,
            )
            for angle in preview.angles
        ],
        rationale=preview.rationale,
        authored_by="model" if preview.authored_by == "model" else "revi",
        rounds_planned=preview.budget,
        refusal=preview.refusal,
    )
