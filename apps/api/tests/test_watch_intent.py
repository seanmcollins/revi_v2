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

from revi_api.watch_intent import parse_watch_declaration


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
