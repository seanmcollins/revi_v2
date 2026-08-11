"""The four gates a research run passes before it costs anybody a minute.

Each one existed as an instruction in a prompt or as a comment in the code,
and each one is now something the deterministic plane decides:

* **AVAILABILITY** — the planner is shown measures this deployment can
  compute, and only those. A vocabulary that carried the rest put them in
  front of the model with nothing marking them.
* **THE GAP** — a question naming a subject nothing here carries says so on
  the confirmation card, free, rather than in the determination a minute
  later.
* **DEPTH** — how many read-and-decide passes a question earns comes from
  the shape of the plan that answers it, not from whether the sentence
  happened to contain the word "why".
* **THE CHASE** — a reading is a chase because it narrowed into a
  population the thresholds admitted, not because the control plane wrote
  the word.
"""

from __future__ import annotations

import pytest

from revi_investigation.application.deep_research.general import (
    AngleShape,
    MeasureAngle,
    PlannedAngle,
    TimeStep,
)
from revi_investigation.application.deep_research.loop import (
    _vocabulary_of,
    _walk_action,
    earned_rounds,
    gap_statement,
    round_words,
    unreached_subjects,
)


class _Availability:
    def __init__(self, metric_id: str, *, available: bool) -> None:
        self.metric_id = metric_id
        self.unit = "ratio"
        self.kind = "flow"
        self.entity_grain = "claim"
        self.sign = "lower_is_better"
        self.available = available
        self.reason = "" if available else "measured one row per remit, not per claim"
        self.cuts = ("payer",)
        self.date_bases = ("service",)


class _Profile:
    def __init__(self, *measures: _Availability) -> None:
        self.measures = measures


class _Pack:
    """Just enough pack to answer the two questions the vocabulary asks."""

    def __init__(self, *, aliases: dict[str, str] | None = None) -> None:
        self._aliases = aliases or {}

    def metric(self, metric_id: str) -> None:
        return None

    def metric_summaries(self) -> tuple[tuple[str, str], ...]:
        return (("denial_rate", "share of claims denied"),)

    def concept_for_alias(self, text: str) -> str | None:
        return self._aliases.get(text)


class TestAvailabilityIsAGateNotAField:
    def test_a_measure_this_deployment_cannot_compute_never_reaches_the_planner(
        self,
    ) -> None:
        vocabulary = _vocabulary_of(
            _Profile(
                _Availability("denial_rate", available=True),
                _Availability("first_pass_yield", available=False),
            ),
            _Pack(),  # type: ignore[arg-type]
        )
        assert "denial_rate" in vocabulary.measures
        assert "first_pass_yield" not in vocabulary.measures
        assert not vocabulary.knows("first_pass_yield")


class TestTheGapIsNamedBeforeTheMinuteIsSpent:
    def test_a_subject_nothing_here_carries_is_found(self) -> None:
        vocabulary = frozenset({"patient", "collection", "denial", "rate", "score"})
        assert unreached_subjects(
            "Research our patient satisfaction scores and how they affect collections.",
            vocabulary,
            _Pack(),  # type: ignore[arg-type]
        ) == ("satisfaction",)

    def test_a_question_this_data_answers_names_no_gap(self) -> None:
        vocabulary = frozenset({"denial", "rate", "payer", "climbing"})
        assert (
            unreached_subjects(
                "Why is our denial rate climbing for one payer?",
                vocabulary,
                _Pack(),  # type: ignore[arg-type]
            )
            == ()
        )

    def test_a_verb_the_pack_does_not_use_is_not_a_missing_subject(self) -> None:
        """False positives refuse questions this data can answer, so the test
        for a subject is deliberately narrow and errs towards silence."""
        vocabulary = frozenset({"denial", "rate"})
        assert "affect" not in unreached_subjects(
            "how do denials affect the rate", vocabulary, _Pack()  # type: ignore[arg-type]
        )

    def test_an_alias_the_pack_knows_is_never_a_gap(self) -> None:
        vocabulary = frozenset({"denial"})
        assert (
            unreached_subjects(
                "what does adjudication look like",
                vocabulary,
                _Pack(aliases={"adjudication": "denial"}),  # type: ignore[arg-type]
            )
            == ()
        )

    def test_the_sentence_names_what_is_missing_and_what_would_fix_it(self) -> None:
        said = gap_statement(("satisfaction",))
        assert "satisfaction" in said
        assert "loading a feed" in said
        assert "%" not in said


def _reading(shape: AngleShape, metric_id: str, **measure: object) -> PlannedAngle:
    return PlannedAngle(
        shape=shape,
        reason="because",
        measure=MeasureAngle(metric_id=metric_id, **measure),  # type: ignore[arg-type]
    )


class TestDepthComesFromThePlan:
    def test_one_measure_one_shape_closes_in_a_pass(self) -> None:
        opening = (_reading(AngleShape.MEASURE_PROFILE, "denial_rate"),)
        assert earned_rounds(opening, 4) == 1

    def test_a_question_needing_three_kinds_of_reading_earns_more(self) -> None:
        opening = (
            _reading(AngleShape.TREND, "denial_rate", step=TimeStep.MONTH),
            _reading(AngleShape.STRATIFIED_RATES, "denial_rate", cut_by=("payer",)),
            _reading(AngleShape.CONTRAST, "denial_rate", cut_by=("payer",)),
        )
        assert earned_rounds(opening, 4) == 3

    def test_a_broad_opening_across_measures_earns_more_still(self) -> None:
        """The vaguest question used to get the shallowest study."""
        opening = (
            _reading(AngleShape.TREND, "denial_rate", step=TimeStep.MONTH),
            _reading(AngleShape.MEASURE_PROFILE, "denied_dollars"),
            _reading(AngleShape.STRATIFIED_RATES, "denial_rate", cut_by=("payer",)),
        )
        assert earned_rounds(opening, 4) == 4

    def test_the_packs_ceiling_still_caps_it(self) -> None:
        opening = (
            _reading(AngleShape.TREND, "a", step=TimeStep.MONTH),
            _reading(AngleShape.MEASURE_PROFILE, "b"),
            _reading(AngleShape.STRATIFIED_RATES, "c", cut_by=("payer",)),
            _reading(AngleShape.CONTRAST, "d", cut_by=("payer",)),
            _reading(AngleShape.COMPOSITION, "e"),
        )
        assert earned_rounds(opening, 2) == 2

    def test_no_plan_is_still_one_round(self) -> None:
        assert earned_rounds((), 4) == 1


class TestAChaseIsANarrowing:
    def test_a_reading_narrowed_into_a_population_is_a_chase(self) -> None:
        angle = _reading(
            AngleShape.MEASURE_PROFILE,
            "denied_dollars",
            cut_by=("carc",),
            within=(("payer", "Veritas Comp Fund"),),
        )
        assert _walk_action(angle) == "chase"

    def test_a_new_cut_is_not_a_chase_however_it_was_described(self) -> None:
        """The model's own word is not the gate. A plain new cut published as
        "Went after" is the run claiming a causal step it never took."""
        angle = PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="because",
            chases="denied dollars by payer",
            measure=MeasureAngle(metric_id="denied_dollars", cut_by=("carc",)),
        )
        assert _walk_action(angle) == "broaden"

    def test_the_progress_line_says_what_the_walk_says(self) -> None:
        broadening = (
            PlannedAngle(
                shape=AngleShape.MEASURE_PROFILE,
                round=1,
                chases="denied dollars by payer",
                reason="the service lines all drifted the same direction",
                measure=MeasureAngle(metric_id="denied_dollars", cut_by=("carc",)),
            ),
        )
        assert "trying another angle" in round_words(1, broadening)


@pytest.mark.parametrize(
    "sentence",
    [
        "Research our patient satisfaction scores.",
        "How do our satisfaction ratings move with collections?",
    ],
)
def test_the_repro_names_its_gap(sentence: str) -> None:
    vocabulary = frozenset({"patient", "collection", "score", "rating", "research"})
    assert unreached_subjects(sentence, vocabulary, _Pack())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# the sentences a study says about its own limits


class TestTheCensoringCopyIsAboutThisStudy:
    """Two ways a disclosure stops being read, both from the review."""

    def test_it_does_not_borrow_the_recovery_reviews_population(self) -> None:
        """No payer answers anything in a study about claim resolution."""
        from revi_investigation.application.deep_research.general_report import (
            censoring_words,
        )

        said = " ".join(
            censoring_words(
                readings=3,
                measured=30,
                bounded=2,
                withheld=1,
                population=6312,
                data_edge="Aug 2, 2026",
            )
        )
        assert "the payer has already answered" not in said

    def test_one_of_a_thing_takes_a_singular_verb(self) -> None:
        """"1 reading here measure" and "1 group publish" read as machine output."""
        from revi_investigation.application.deep_research.general_report import (
            censoring_words,
        )

        lines = censoring_words(
            readings=1,
            measured=10,
            bounded=1,
            withheld=1,
            population=100,
            data_edge="Aug 2, 2026",
        )
        said = " ".join(lines)
        assert "1 reading here measures" in said
        assert "1 group publishes a ceiling" in said
        assert "1 group publishes nothing" in said
        assert "reading here measure " not in said
        assert "group publish " not in said

    def test_more_than_one_of_a_thing_takes_a_plural_verb(self) -> None:
        from revi_investigation.application.deep_research.general_report import (
            censoring_words,
        )

        said = " ".join(
            censoring_words(
                readings=3,
                measured=10,
                bounded=4,
                withheld=2,
                population=100,
                data_edge="Aug 2, 2026",
            )
        )
        assert "3 readings here measure" in said
        assert "4 groups publish a ceiling" in said
        assert "2 groups publish nothing" in said
