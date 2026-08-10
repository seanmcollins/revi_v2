"""Reading "watch X" — the lead-in only, never the subject.

The parser under test recognises a closed INSTRUCTION vocabulary and hands
the remainder to the ordinary interpretation path. Everything below is
about that boundary: what counts as a declaration, what the subject is
reduced to, and what a stated sensitivity compiles into.

The two failure modes these tests exist to prevent:

* **subject contamination.** "denial rate if it moves more than 2 points"
  handed to interpretation is a question about a MOVEMENT, and the watch
  would be defined by the wrong spec forever. The threshold clause is split
  off before anything is interpreted.
* **a guessed threshold.** A clause this grammar does not recognise yields
  the GOVERNED default, never a number nobody stated. A watch that fires on
  a threshold the analyst did not set is worse than one that fires on the
  pack's.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_api.watch_intent import legal_threshold_phrases, parse_watch_declaration
from revi_investigation.application.ports import WATCH_THRESHOLD_UNITS


class TestRecognition:
    @pytest.mark.parametrize(
        "utterance",
        [
            "watch Silverline MA denial rate",
            "Watch the denial rate for Silverline",
            "keep an eye on our DNFB dollars",
            "monitor denied dollars by payer",
            "track the denial rate",
            "please watch denial rate by payer",
            "can you keep track of DNFB dollars",
        ],
    )
    def test_a_declaration_is_recognised(self, utterance: str) -> None:
        declaration = parse_watch_declaration(utterance)
        assert declaration is not None
        assert declaration.subject
        assert declaration.matched_phrase.lower() in utterance.lower()

    @pytest.mark.parametrize(
        "utterance",
        [
            "why did denials spike last week",
            "which payers had the biggest increase in denials",
            "show me all twelve payers",
            "",
            "   ",
            # A lead-in with nothing after it is a word, not a declaration:
            # registering a watch over an empty subject would be the
            # platform inventing what to watch.
            "watch",
            "monitor",
            "keep an eye on",
        ],
    )
    def test_ordinary_language_is_left_alone(self, utterance: str) -> None:
        assert parse_watch_declaration(utterance) is None

    def test_the_lead_in_is_echoed_verbatim(self) -> None:
        """The platform shows what it READ rather than asserting it read
        intent."""
        declaration = parse_watch_declaration("Keep an eye on the denial rate")
        assert declaration is not None
        assert declaration.matched_phrase == "Keep an eye on"

    def test_filler_between_the_lead_in_and_the_subject_is_stripped(self) -> None:
        with_the = parse_watch_declaration("watch the denial rate for Silverline")
        without = parse_watch_declaration("watch denial rate for Silverline")
        assert with_the is not None and without is not None
        assert with_the.subject == without.subject == "denial rate for Silverline"


class TestSubjectIsolation:
    def test_a_threshold_clause_never_reaches_the_subject(self) -> None:
        """The subject handed to interpretation must be a question about a
        LEVEL. "denial rate if it moves more than 2 points" would be
        interpreted as a question about a movement, and the watch would be
        defined by that spec forever."""
        declaration = parse_watch_declaration(
            "watch denial rate for Silverline, tell me if it moves more than 2 points"
        )
        assert declaration is not None
        assert declaration.subject == "denial rate for Silverline"
        assert "points" not in declaration.subject

    def test_scope_specificity_is_just_the_subject(self) -> None:
        """Broad or narrow, it is the same mechanism — the difference is
        entirely in the spec interpretation produces."""
        broad = parse_watch_declaration("watch our denial rate")
        narrow = parse_watch_declaration(
            "watch denial rate for Silverline Health MA and Laboratory"
        )
        assert broad is not None and narrow is not None
        assert broad.subject == "denial rate"
        assert narrow.subject == "denial rate for Silverline Health MA and Laboratory"


class TestStatedSensitivity:
    def test_points_stay_points(self) -> None:
        declaration = parse_watch_declaration(
            "watch denial rate, alert me if it rises more than 2 points"
        )
        assert declaration is not None and declaration.watch is not None
        assert declaration.watch.mode == "delta_gte"
        assert declaration.watch.value == Decimal("2")
        assert declaration.watch.unit == "points"
        assert declaration.watch.direction == "up"

    def test_dollars_are_carried_in_cents(self) -> None:
        """Cents is the unit the money contracts actually declare, so the
        conversion happens once, here, rather than at every comparison."""
        declaration = parse_watch_declaration(
            "monitor DNFB dollars, tell me if it moves more than $50,000"
        )
        assert declaration is not None and declaration.watch is not None
        assert declaration.watch.unit == "cents"
        assert declaration.watch.value == Decimal("5000000")

    def test_a_bare_percent_reads_as_relative_never_as_points(self) -> None:
        """"Up 3%" is ambiguous between a relative change and three
        percentage points. The reading chosen is the one that is legal
        against every contract and cannot silently become points."""
        declaration = parse_watch_declaration(
            "watch denied dollars, tell me if it moves more than 10%"
        )
        assert declaration is not None and declaration.watch is not None
        assert declaration.watch.unit == "relative_pct"

    def test_a_crossing_is_a_level_not_a_relative_amount(self) -> None:
        """"Crosses 15%" names fifteen percent, not fifteen percent OF
        something — a relative crossing would be a threshold that moved
        every time the thing it watches did."""
        declaration = parse_watch_declaration(
            "track denial rate and let me know when it crosses 15%"
        )
        assert declaration is not None and declaration.watch is not None
        assert declaration.watch.mode == "crosses"
        assert declaration.watch.unit == "points"
        assert declaration.watch.value == Decimal("15")

    def test_any_movement_is_recognised(self) -> None:
        declaration = parse_watch_declaration(
            "watch denial rate for lab, tell me on any movement"
        )
        assert declaration is not None and declaration.watch is not None
        assert declaration.watch.mode == "any_movement"

    def test_a_direction_with_no_size_narrows_the_governed_default(self) -> None:
        """"Tell me if it rises" is a real instruction and is not an
        instruction about size."""
        declaration = parse_watch_declaration("watch denial rate, tell me if it rises")
        assert declaration is not None and declaration.watch is not None
        assert declaration.watch.mode == "governed_default"
        assert declaration.watch.direction == "up"

    def test_an_unrecognised_clause_falls_back_to_the_governed_gate(self) -> None:
        """Never a guessed threshold: a watch that fires on a number nobody
        stated is worse than one that fires on the pack's."""
        declaration = parse_watch_declaration(
            "watch denial rate, tell me when something interesting happens"
        )
        assert declaration is not None
        assert declaration.watch is None or declaration.watch.mode == "governed_default"

    def test_the_analysts_own_clause_is_recorded_on_the_watch(self) -> None:
        declaration = parse_watch_declaration(
            "watch denial rate, alert me if it rises more than 2 points"
        )
        assert declaration is not None and declaration.watch is not None
        assert "2 points" in declaration.watch.note
        assert declaration.threshold_phrase


class TestAStatedSensitivityIsNeverSilentlyReplaced:
    """Round-7 FN-6.

    Three real utterances from three reviewers each registered the GOVERNED
    default with ``value=null``, and the confirmation sentence never
    mentioned the instruction the analyst had typed: "half a point" gated at
    the pack's 0.5 by coincidence, and "three points" would have gated at
    0.5 too — silently, forever. The module's own docstring names the
    failure: "a watch registered against a spec nobody confirmed briefs the
    wrong number every morning, silently, forever."

    The fix has two halves, and both are here: read the shapes people
    actually type, and REFUSE the ones that cannot be read rather than
    quietly substituting a number nobody stated.
    """

    @pytest.mark.parametrize(
        ("utterance", "value", "unit"),
        [
            # rcm-exec's own words, verbatim from the live session.
            ("watch denial rate, tell me if it moves more than half a point", "0.5", "points"),
            ("watch denial rate, tell me if it moves more than three points", "3", "points"),
            ("watch denial rate, tell me if it moves more than a point", "1", "points"),
            ("watch denial rate, tell me if it moves more than two percent", "2", "relative_pct"),
            # vc-investor's: a metric the pack governs with min_absolute_days.
            ("watch days in AR, tell me when it moves more than 2 days", "2", "days"),
        ],
    )
    def test_the_shapes_people_actually_type_are_read(
        self, utterance: str, value: str, unit: str
    ) -> None:
        declaration = parse_watch_declaration(utterance)
        assert declaration is not None and declaration.watch is not None, utterance
        assert declaration.watch.value == Decimal(value)
        assert declaration.watch.unit == unit
        assert not declaration.threshold_unreadable

    def test_a_days_threshold_is_read_and_is_a_legal_stated_unit(self) -> None:
        """``days`` is in the closed set a threshold may be STATED in, because
        a lag metric's own unit is also the natural way a human states a
        threshold for it. The shipped behaviour read the number, dropped it,
        and briefed on the pack's gate without saying so; the interim
        behaviour refused it by name. Neither was the answer — over a ``days``
        contract, "more than 2 days" means exactly one thing."""
        declaration = parse_watch_declaration(
            "watch days in AR, tell me when it moves more than 2 days"
        )
        assert declaration is not None and declaration.watch is not None
        assert declaration.watch.unit == "days"
        assert declaration.watch.unit in WATCH_THRESHOLD_UNITS

    @pytest.mark.parametrize(
        "utterance",
        [
            "watch denial rate, tell me if it moves more than a smidgen",
            "watch denial rate, tell me if it moves more than 2 baskets",
            # A LEVEL was stated and only the direction was read: the
            # direction-only branch would have returned governed_default and
            # thrown the 10% away.
            "watch denial rate, tell me if it drops below 10%",
        ],
    )
    def test_a_size_this_grammar_cannot_read_is_reported_not_defaulted(
        self, utterance: str
    ) -> None:
        declaration = parse_watch_declaration(utterance)
        assert declaration is not None
        assert declaration.threshold_unreadable, utterance
        assert declaration.threshold_phrase

    @pytest.mark.parametrize(
        "utterance",
        [
            # No size stated at all: the governed magnitude is the honest
            # completion of these, not a substitution for something asked.
            "watch denial rate, tell me if it rises",
            "watch denial rate, tell me if it moves",
            "watch denial rate, tell me on any movement",
            "keep an eye on Silverline denial rate",
            "watch denial rate, alert me if it rises more than 2 points",
        ],
    )
    def test_saying_nothing_about_size_is_not_an_unreadable_size(
        self, utterance: str
    ) -> None:
        declaration = parse_watch_declaration(utterance)
        assert declaration is not None
        assert not declaration.threshold_unreadable, utterance

    def test_no_watch_registers_the_governed_default_over_a_stated_number(self) -> None:
        """The reviewer's own test, stated as they wrote it: no watch may
        register ``governed_default`` when the utterance contained a numeric
        threshold clause."""
        for utterance in (
            "watch denial rate, tell me if it moves more than 2 baskets",
            "watch denial rate, tell me if it drops below 10%",
        ):
            declaration = parse_watch_declaration(utterance)
            assert declaration is not None
            silently_defaulted = (
                declaration.watch is not None
                and declaration.watch.mode == "governed_default"
                and not declaration.threshold_unreadable
            )
            assert not silently_defaulted, utterance


class TestLegalAlternatives:
    """A refusal with no way forward is a wall. Every refusal names
    phrasings that would be accepted for the metric in hand."""

    def test_a_rate_is_offered_points_and_a_crossing(self) -> None:
        phrases = legal_threshold_phrases("ratio")
        assert any("points" in phrase for phrase in phrases)
        assert any("crosses" in phrase for phrase in phrases)

    def test_money_is_offered_dollars(self) -> None:
        assert any("$" in phrase for phrase in legal_threshold_phrases("money_cents"))

    def test_days_are_offered_a_days_threshold(self) -> None:
        """``days`` is a legal stated unit over a days contract, so the
        refusal names it first — a refusal that omitted the one phrasing
        that fits the metric would be sending the analyst the long way
        round."""
        phrases = legal_threshold_phrases("days")
        assert any("2 days" in phrase for phrase in phrases)

    def test_a_days_threshold_is_never_offered_for_another_unit(self) -> None:
        """"2 days" on a rate or a money measure has no meaning, so no
        refusal over those contracts may name it as a way forward."""
        for unit in ("ratio", "money_cents", "count", None):
            assert not any("days" in phrase for phrase in legal_threshold_phrases(unit)), unit

    def test_an_unknown_unit_still_gets_concrete_phrasings(self) -> None:
        assert legal_threshold_phrases(None)
