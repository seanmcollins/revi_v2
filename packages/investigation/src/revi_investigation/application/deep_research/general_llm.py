"""The generalized research planner — the model call that decides what to read.

The recovery planner (:mod:`.llm`) chooses from six authored families over
eight authored breakdowns. It can do that because the recovery domain's
whole vocabulary is content this repo ships. This planner cannot, and the
difference is the point: a research question can be about anything the
semantic layer measures, so what the model is shown is *this deployment's
own* catalog, resolved by the orient phase against the data that actually
exists here.

**What the model receives**, and nothing else:

* the research question, the population and the period;
* the discovery orientation — the path choices, each already reduced to one
  plain sentence with its coverage figure by the discovery family;
* the definitions library's own RCM knowledge, as prose
  (:func:`~.knowledge.as_prompt_context`) — the pack's judgement about what
  deserves checking finally reaching the thing that decides what to check;
* the closed angle grammar: five shapes, and the measures and breakdowns
  this deployment carries;
* on a later round, **its own certified results** — the cells an estimator
  published, and the thresholds' own verdict on each reading.

**What comes back** is selection with reasons, and it is re-validated twice
before anything runs. The shape is a ``Literal`` and cannot be invented.
The measure and the breakdowns are free strings, and
:func:`~.loop.validate_angles` resolves every one of them against the
vocabulary — an unknown measure is dropped, a stray breakdown is trimmed,
exactly as ``build_angle`` drops an invented recovery family. A chase into
a population the research thresholds did not admit is dropped by
:func:`~.loop.gate_chases`: the model decides what is *interesting*, the
content decides what is *significant*.

**A failed call is not a failed run.** Every failure path — a refused call,
an unparseable body, a plan that survives validation empty — returns
nothing, and the loop falls back to the deterministic set and publishes
``authored_by: revi``. A fallback presented as a choice would be a small
lie about how the analysis was decided.

**No number in, no number out.** The certified figures the planner reads
are quoted back to it as prose and its response schema has no numeric
field, so the only things it can return are a shape, some ids, and
sentences. Every figure a reader sees still comes from the deterministic
plane.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from revi_investigation.application.deep_research.general import (
    MAX_CUTS,
    AngleShape,
    AngleVocabulary,
    MeasureAngle,
    PlannedAngle,
    TimeStep,
)
from revi_investigation.application.deep_research.knowledge import as_prompt_context
from revi_investigation.application.deep_research.loop import (
    Lead,
    Orientation,
    ResearchRound,
    leads_of,
    population_words,
)
from revi_investigation.application.deep_research.measures import MeasureResult
from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.llm.render import LoadedTemplate, load_template, render_template
from revi_investigation.application.llm.schemas import (
    GeneralizedAngleModel,
    GeneralizedResearchPlanResponse,
    sanitize_json_schema,
)
from revi_investigation.application.ports import (
    DEFAULT_LLM_CALL_POLICY,
    LanguageModelPort,
    LlmCallPolicy,
    StructuredLlmRequest,
)
from revi_investigation.application.rendering import format_value, metric_label
from revi_kernel.scope import AbsoluteRange

TEMPLATE_ID = "plan_generalized_research"
TEMPLATE_VERSION = "v1"

#: How many cells of one reading are quoted back to the planner. Enough to
#: see the shape of a breakdown; short of the point where the prompt is a
#: data extract rather than a summary, which is a line the outbound payload
#: guard also draws and would draw louder.
MAX_CELLS_QUOTED = 8

#: How long a reason may be. It is printed under a heading on a card and
#: read out in a report; past a sentence it stops being the cause of a
#: reading and starts being the reading's own summary, which is the
#: composer's job and is written from certified figures.
MAX_REASON_CHARS = 220

#: A snake_case identifier — a metric id, a dimension id, a column. Banned
#: from every client-visible string (``docs/client-language.md`` §3), and a
#: reason IS client-visible, so one that arrives carrying an id has the id
#: turned into words rather than the reason thrown away.
_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

_STEPS = {step.value: step for step in TimeStep}
_SHAPES = {shape.value: shape for shape in AngleShape}


# ---------------------------------------------------------------------------
# what the model is shown


def window_words(window: AbsoluteRange) -> str:
    """A period as a reader writes one, never as a range literal."""
    return f"{window.start:%b %-d, %Y} through {window.end:%b %-d, %Y}"


def orientation_block(orientation: Orientation) -> str:
    """The discovery findings, verbatim.

    Each note is already one plain sentence carrying its own coverage
    figure — the discovery family composes it beside the numbers it quotes
    precisely so that nothing downstream re-words it. Re-wording here would
    make this the second place the same fact is phrased, and the second
    phrasing is always the one that loses the coverage.
    """
    if not orientation.notes:
        return "Nothing could be established about this data before planning."
    lines = [f"- {note.statement}" for note in orientation.notes]
    if orientation.concepts:
        lines.append(
            "- The question names these ideas, and each one resolves to a path this "
            "data populates: " + ", ".join(orientation.concepts) + "."
        )
    return "\n".join(lines)


def vocabulary_block(vocabulary: AngleVocabulary, measures: Sequence[str]) -> str:
    """Every measure this deployment carries, with what may be done to it.

    Ordered by what the question points at first, because a planner reads
    top-down and the measure the analyst named is the one their question is
    about. Everything else is still listed: a research question routinely
    needs a measure it did not name, and a vocabulary trimmed to the
    question's own words would make that impossible rather than unlikely.
    """
    ordered = list(dict.fromkeys([*measures, *sorted(vocabulary.measures)]))
    blocks: list[str] = []
    for metric_id in ordered:
        if not vocabulary.knows(metric_id):
            continue
        kind = vocabulary.kinds.get(metric_id, "flow")
        shape_note = (
            "a flow, so it has a trend"
            if kind == "flow"
            else "an as-of level, so it has no trend"
        )
        rate_note = (
            "rate over a counted population, so it may be stratified and tested"
            if metric_id in vocabulary.rate_like
            else "not a rate over a counted population"
        )
        description = vocabulary.descriptions.get(metric_id, "")
        cuts = sorted(vocabulary.cuts_for(metric_id))
        bases = sorted(vocabulary.bases.get(metric_id, frozenset()))
        lines = [f"- `{metric_id}`" + (f" — {description}" if description else "")]
        unit = vocabulary.units.get(metric_id, "a count")
        lines.append(f"  - {shape_note}; {rate_note}; measured in {unit}.")
        lines.append("  - breakdowns: " + (", ".join(f"`{cut}`" for cut in cuts) or "none"))
        if bases:
            lines.append("  - date bases: " + ", ".join(f"`{basis}`" for basis in bases))
        blocks.append("\n".join(lines))
    if not blocks:
        return "This data carries no measure that can speak to this question."
    return "\n".join(blocks)


def _cell_words(result: MeasureResult, index: int) -> str:
    cell = result.cells[index]
    if cell.withheld:
        return f"  - {cell.label}: too small to publish"
    if cell.bounded:
        return f"  - {cell.label}: a ceiling only, no measured value"
    return f"  - {cell.label}: {format_value(cell.value, result.unit)}"


def results_block(
    rounds: Sequence[ResearchRound], leads: Sequence[Lead], *, round_index: int
) -> str:
    """The run's own certified output, as the planner sees it before deciding.

    Written as prose over published cells. The planner never receives a row,
    a query or an unpublished value: a cell the disclosure rules withheld is
    described as withheld and its number is not in the prompt at all, which
    is the same rule the report obeys and for the same reason.

    The leads are stated separately and last, because they are the only
    thing on this page the planner may act on by narrowing. Everything else
    is context for choosing the next reading.
    """
    if round_index == 0 or not rounds:
        return (
            "Nothing has run yet. This is the opening read, so choose the readings that "
            "open the question rather than ones that follow from a finding."
        )
    blocks: list[str] = []
    for round_ in rounds:
        header = "The opening read" if round_.index == 0 else f"Round {round_.index}"
        blocks.append(f"### {header}")
        for result in round_.results:
            if result.refusal:
                blocks.append(f"- {result.title}: could not be read — {result.refusal}")
                continue
            lines = [f"- {result.title}"]
            for index in range(min(len(result.cells), MAX_CELLS_QUOTED)):
                lines.append(_cell_words(result, index))
            hidden = len(result.cells) - MAX_CELLS_QUOTED
            if hidden > 0:
                lines.append(f"  - and {hidden} more groups")
            if result.cells_published == 0:
                lines.append("  - every group here was too small to publish")
            contrast = result.contrast
            if contrast is not None and not contrast.is_refused:
                lines.append(
                    f"  - compared: {contrast.left.label} against {contrast.right.label}"
                )
            blocks.append("\n".join(lines))
    if leads:
        blocks.append("### What separated, and may be gone inside")
        blocks.append(
            "These are the only populations you may narrow into with `within`. Copy the "
            "dimension and the value exactly."
        )
        for lead in leads:
            blocks.append(
                f"- `{lead.dimension}` = `{lead.value}` (shown as {lead.shown}), off "
                f"{lead.title} — {lead.why}"
            )
    else:
        blocks.append("### What separated")
        blocks.append(
            "Nothing in the last reading separated by enough to go inside, so do not use "
            "`within` this round. Choose a different breakdown, or a measure the question "
            "needs that has not been read."
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# what comes back


def reason_words(text: str, fallback: str) -> str:
    """One model sentence, made safe to print to a client.

    A reason is the only free text this seam produces that a reader sees,
    and the language contract does not have an exemption for sentences a
    model wrote. So it is held to the contract mechanically rather than by
    instruction: whitespace flattened, internal identifiers turned into the
    words they stand for, length capped at a sentence. A reason that
    survives as nothing falls back to the deterministic one — a reading
    with no stated cause makes the walk a list of what happened rather
    than a record of what was decided.
    """
    flat = " ".join(text.split())
    flat = _IDENTIFIER.sub(lambda match: match.group(0).replace("_", " "), flat)
    if len(flat) > MAX_REASON_CHARS:
        clipped = flat[:MAX_REASON_CHARS].rsplit(" ", 1)[0]
        flat = clipped.rstrip(",;:— ") + "…"
    return flat or fallback


def _fallback_reason(angle: MeasureAngle, shape: AngleShape) -> str:
    measure = metric_label(angle.metric_id)
    if shape is AngleShape.TREND:
        return f"how {measure} has moved over the period asked about"
    if angle.cut_by:
        cuts = " and ".join(cut.replace("_", " ") for cut in angle.cut_by)
        return f"where {measure} sits, broken out by {cuts}"
    return f"what {measure} is over this population"


def planned_angle_of(
    proposed: GeneralizedAngleModel, *, round_index: int
) -> PlannedAngle | None:
    """One model-proposed reading, as a domain angle — or nothing.

    Only the transport-level reading happens here: the ``Literal`` fields
    are already closed by the schema, and everything else is a string this
    function copies across untouched. Whether the strings NAME anything is
    decided one layer up, against the deployment's own vocabulary, because
    that is the only place that knows.
    """
    shape = _SHAPES.get(proposed.shape)
    if shape is None:  # pragma: no cover - the schema closes this
        return None
    metric_id = proposed.metric_id.strip()
    if not metric_id:
        return None
    angle = MeasureAngle(
        metric_id=metric_id,
        cut_by=tuple(dict.fromkeys(cut.strip() for cut in proposed.cut_by if cut.strip()))[
            :MAX_CUTS
        ],
        step=_STEPS.get(proposed.step or ""),
        basis=(proposed.basis or None),
        against=proposed.against.strip(),
        within=tuple(
            (pair.dimension.strip(), pair.value.strip())
            for pair in proposed.within
            if pair.dimension.strip() and pair.value.strip()
        ),
    )
    return PlannedAngle(
        shape=shape,
        reason=reason_words(proposed.reason, _fallback_reason(angle, shape)),
        round=round_index,
        chases=" ".join(proposed.chases.split()),
        measure=angle,
    )


# ---------------------------------------------------------------------------
# the planner


class LlmGeneralPlanner:
    """The control plane for a generalized research run.

    Satisfies :class:`~.loop.GeneralPlanner`. Both calls go through one
    versioned template, because opening a question and continuing it are
    the same decision made with more information — and a second template
    would be a second place the angle grammar is described, which is how
    two descriptions of one closed set drift apart.
    """

    def __init__(
        self,
        llm: LanguageModelPort,
        *,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> None:
        self._llm = llm
        self._policy = policy
        self._template: LoadedTemplate = load_template(TEMPLATE_ID, TEMPLATE_VERSION)
        self._schema = sanitize_json_schema(
            GeneralizedResearchPlanResponse.model_json_schema()
        )

    @property
    def template_hash(self) -> str:
        return self._template.sha256

    async def open(
        self, orientation: Orientation, *, budget: int
    ) -> tuple[Sequence[PlannedAngle], str]:
        return await self._plan(orientation, (), round_index=0, budget=budget)

    async def next_round(
        self,
        orientation: Orientation,
        rounds: Sequence[ResearchRound],
        *,
        index: int,
        remaining: int,
    ) -> Sequence[PlannedAngle]:
        angles, _ = await self._plan(
            orientation, rounds, round_index=index, budget=index + max(remaining, 1)
        )
        return angles

    # -- the call ----------------------------------------------------------

    def render(
        self,
        orientation: Orientation,
        rounds: Sequence[ResearchRound],
        *,
        round_index: int,
        budget: int,
    ) -> str:
        """The prompt this round would send. Pure, so it can be read in a test."""
        return render_template(
            self._template.text,
            {
                "population": population_words(orientation.population),
                "window": window_words(orientation.window),
                "round": str(round_index),
                "budget": str(max(budget, 1)),
                "orientation": orientation_block(orientation),
                "knowledge": as_prompt_context(orientation.knowledge),
                "vocabulary": vocabulary_block(
                    orientation.vocabulary, orientation.measures
                ),
                "results": results_block(
                    rounds,
                    leads_of(rounds, orientation.policy),
                    round_index=round_index,
                ),
                "question": orientation.question.strip()
                or "(none — read what this data says about itself)",
            },
        )

    async def _plan(
        self,
        orientation: Orientation,
        rounds: Sequence[ResearchRound],
        *,
        round_index: int,
        budget: int,
    ) -> tuple[tuple[PlannedAngle, ...], str]:
        prompt = self.render(
            orientation, rounds, round_index=round_index, budget=budget
        )
        assert_safe_payload(prompt)
        result = await self._llm.structured(
            StructuredLlmRequest(
                template_id=self._template.template_id,
                template_version=self._template.version,
                rendered_prompt=prompt,
                schema=self._schema,
                policy=self._policy,
            )
        )
        if result.output is None:
            return (), ""
        try:
            parsed = GeneralizedResearchPlanResponse.model_validate(dict(result.output))
        except Exception:
            return (), ""
        angles = [
            angle
            for proposed in parsed.angles
            if (angle := planned_angle_of(proposed, round_index=round_index)) is not None
        ]
        return tuple(angles), reason_words(parsed.rationale, "")


__all__ = [
    "MAX_CELLS_QUOTED",
    "MAX_REASON_CHARS",
    "TEMPLATE_ID",
    "TEMPLATE_VERSION",
    "LlmGeneralPlanner",
    "orientation_block",
    "planned_angle_of",
    "reason_words",
    "results_block",
    "vocabulary_block",
    "window_words",
]
