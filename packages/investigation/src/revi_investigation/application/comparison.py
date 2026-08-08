"""How a comparison is *described*, and what happens when the two windows
are not the same length (design §6.1, §7.2).

The context header has always printed the resolved comparison range —
``vs 2026-01-01..2026-03-31``. Finding text did not: it re-derived a phrase
from the **current** window's requested unit, special-casing only
``PRIOR_YEAR``. A ``CUSTOM`` comparison therefore fell through to
``"vs prior {unit}"`` and a 7-day window differenced against calendar Q1
was published as *"Atlas Commercial cash posted down $4,199,421 vs prior
week"*, graded ``direct`` / ``high``, with an ``impact_cents`` of
-419,942,121 and no warning. The header on the same answer said
``vs 2026-01-01..2026-03-31``. Two surfaces of one turn contradicted each
other, and the wrong one was the one in the largest type.

Two rules, both enforced here so there is one place to read them:

**Every rendered phrase names the resolved range.** A label ("prior week",
"prior year") is a convenience, never the whole truth, so the concrete
dates ride along with it and a ``CUSTOM`` comparison is rendered *as* its
range. Header and finding text can then be checked against each other
mechanically, which is what
``test_findings_rendering.py::TestComparisonPhrase`` does for every
``ComparisonKind``.

**Unequal window lengths are annotated, never netted.** Differencing 7 days
against 90 days is a legal thing to ask for and an illegal thing to call a
delta: the difference is dominated by the length ratio, not by anything
that happened. Three options were on the table — refuse the turn,
normalize both sides to a daily rate, or answer with a hard warning. This
implementation takes the third **and strips the false precision that made
the first two attractive**:

- the phrase carries the mismatch inline (``90d vs 7d, not
  length-normalized``), so no reader sees the number without the caveat;
- ``impact_cents`` is left unset — an impact is a dollar figure the
  platform is willing to rank, sum, and put in a worklist, and a
  length-mismatched difference is none of those;
- the finding's confidence drops to ``qualified``, so it can never be
  published in certified language;
- a warning is emitted on the turn, in the ``warnings`` array that already
  exists on ``TurnAnswer`` and ``Investigation``.

Refusing was rejected because the comparison itself is well-formed and the
user asked for it on purpose: "how did last week compare with Q1" is a
real question, and this system's refusals are reserved for things it
*cannot* compute rather than things it must caveat. Silent normalization
was rejected because it answers a question nobody asked (a per-day rate)
under the label of the one they did. Annotating keeps the analyst's
question and removes the platform's false confidence — which is the same
trade the rest of the product makes when it grades evidence instead of
hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass

from revi_investigation.domain.context import AnalysisSpec
from revi_kernel.scope import Comparison, ComparisonKind, TimeWindow


@dataclass(frozen=True, slots=True)
class ComparisonRendering:
    """Everything the presentation layer needs to talk about a comparison."""

    #: ``vs prior week (2026-07-20..2026-07-26)`` / ``vs 2026-01-01..2026-03-31``
    phrase: str
    #: ``2026-01-01..2026-03-31`` — the exact string the context header prints.
    range_text: str
    current_days: int
    comparison_days: int

    @property
    def length_mismatch(self) -> bool:
        return self.current_days != self.comparison_days


def _base_label(comparison: Comparison, window: TimeWindow) -> str | None:
    """The human label for a comparison, or ``None`` when only dates will do."""
    if comparison.kind is ComparisonKind.PRIOR_YEAR:
        return "vs prior year"
    if comparison.kind is ComparisonKind.CUSTOM:
        # A custom range has no name. Naming it after the *current* window's
        # unit is precisely the defect this module exists to remove.
        return None
    requested = window.requested
    if requested is not None and requested.quantity == 1:
        return f"vs prior {requested.unit.value}"
    return "vs prior period"


def render_comparison(spec: AnalysisSpec) -> ComparisonRendering | None:
    """Describe the spec's comparison, or ``None`` when there is none."""
    comparison = spec.context.comparison
    if comparison is None:
        return None
    window = spec.context.window
    cmp_range = comparison.window.range
    range_text = f"{cmp_range.start.isoformat()}..{cmp_range.end.isoformat()}"
    current_days = window.range.day_length
    comparison_days = cmp_range.day_length

    mismatch = (
        f"{comparison_days}d vs {current_days}d, not length-normalized"
        if current_days != comparison_days
        else ""
    )
    label = _base_label(comparison, window)
    if label is None:
        # No name exists for a custom range; the range *is* the label.
        phrase = f"vs {range_text}" + (f" ({mismatch})" if mismatch else "")
    else:
        phrase = f"{label} ({range_text}, {mismatch})" if mismatch else f"{label} ({range_text})"
    return ComparisonRendering(
        phrase=phrase,
        range_text=range_text,
        current_days=current_days,
        comparison_days=comparison_days,
    )


def comparison_phrase(spec: AnalysisSpec) -> str:
    """The period phrase every finding on this spec must use."""
    rendering = render_comparison(spec)
    return "vs prior period" if rendering is None else rendering.phrase


def window_mismatch_warning(spec: AnalysisSpec) -> str | None:
    """The turn-level warning for a length-mismatched comparison, if any."""
    rendering = render_comparison(spec)
    if rendering is None or not rendering.length_mismatch:
        return None
    return (
        "COMPARISON_WINDOW_MISMATCH: the comparison window "
        f"({rendering.range_text}, {rendering.comparison_days}d) is not the same length as the "
        f"analysis window ({rendering.current_days}d). Differences and percentage changes "
        "between them are dominated by the length difference and are not normalized; no "
        "impact figure is published for this turn and its findings are qualified."
    )
