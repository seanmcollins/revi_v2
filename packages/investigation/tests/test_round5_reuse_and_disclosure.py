"""Round-5 E-01 and C-01 (server half): notes that were written and never fired.

**E-01.** ``refinement_reused_plan`` is composed at ``submit_turn.py:3246``
and ``REFINEMENT_NOT_APPLIED`` has been registered in the API's warning
table since round 4, with a title in the web — and the round-5 exec
verified that neither string appears on any live session. The reuse note
lived in the kernel-only branch alone, which returns ``None`` the moment an
``Expand`` asks for more rows than the parent published, and
``_presentation_turn`` set ``warnings = parent.warnings`` and appended
nothing. Live: *"sort them by percent change, largest first"* came back
``presentation_only`` with a plan hash identical to its parent, the
findings in the SAME order, ``refinement_operators: []``, a freshly
composed 2,771-character narrative at $0.0798 — and no note of any kind.
Turn two of every session read like a new analysis that had honoured a
request it ignored.

**C-01 (the emission half).** One fact printed six to ten times, naming
internal probe ids: six ``ALTERNATE_BASIS_USED`` entries differing only by
``probe 'main'`` / ``'premise'`` / ``'main__window'`` /
``'main__window__prior'`` / ``'premise__window'`` /
``'premise__window__prior'``. They are one fact spelled six ways. The
dedupe keys on ``(code, message)``, so the fix is at the emission: name the
metric, not the plumbing, and the identical sentences collapse with a count.
"""

from __future__ import annotations

from revi_investigation.application.submit_turn import (
    _same_findings,
    _unapplied_presentation_request,
)
from revi_investigation.domain.records import Finding
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef, ReferentId, ReferentKind


def _finding(referent: str, title: str) -> Finding:
    return Finding(
        referent=ReferentId(value=referent, kind=ReferentKind.FINDING),
        title=title,
        statement=f"{title}.",
        metric_refs=(MetricRef("denied_dollars"),),
        values=(("current_cents", 100),),
        grade=EvidenceGrade.DIRECT,
    )


class TestARequestThatWasNotAppliedIsNamed:
    def test_the_live_repro_fires(self) -> None:
        note = _unapplied_presentation_request("sort them by percent change, largest first")

        assert note is not None
        assert note.startswith("refinement_not_applied:")
        assert "sort" in note

    def test_the_other_shapes_of_the_same_request(self) -> None:
        for question in (
            "re-sort these by denial rate",
            "order them by dollars",
            "rank by pct change",
            "show them alphabetically",
            "smallest first please",
            "filter out the small payers",
        ):
            assert _unapplied_presentation_request(question) is not None, question

    def test_a_plain_re_presentation_is_not_accused_of_dropping_anything(self) -> None:
        """rcm-exec's neighbouring turn. "Show me that same breakdown
        again" asks for no change, so it owes a reuse note and nothing
        else — a not-applied warning there would be its own falsehood."""
        for question in (
            "show me that same breakdown again",
            "show me all twelve",
            "what were those numbers again?",
        ):
            assert _unapplied_presentation_request(question) is None, question


class TestReuseIsDecidedByWhatTheReaderSees:
    """A re-served plan whose FINDINGS changed did apply the operator (the
    analyst asked for more rows and got them); one whose findings are
    byte-identical did not. Referents are deliberately not part of the
    comparison: a reused plan mints new handles, and keying identity off
    them would call two identical lists different."""

    def test_identical_published_rows_are_the_same_answer(self) -> None:
        served = (_finding("F4", "Lakewood up"), _finding("F5", "Atlas down"))
        parent = (_finding("F1", "Lakewood up"), _finding("F2", "Atlas down"))

        assert _same_findings(served, parent) is True

    def test_more_rows_is_a_different_answer(self) -> None:
        served = (
            _finding("F4", "Lakewood up"),
            _finding("F5", "Atlas down"),
            _finding("F6", "Meridian up"),
        )
        parent = (_finding("F1", "Lakewood up"), _finding("F2", "Atlas down"))

        assert _same_findings(served, parent) is False

    def test_the_same_count_with_different_rows_is_a_different_answer(self) -> None:
        assert (
            _same_findings((_finding("F4", "Lakewood up"),), (_finding("F1", "Atlas down"),))
            is False
        )
