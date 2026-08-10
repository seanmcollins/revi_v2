"""What a turn measured, what it could not, and the counts that say so."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from revi_investigation.application.calculation_glue import (
    CalculationResult,
)
from revi_investigation.application.execution import (
    BoundedCell,
    ExecutedProbe,
    SuppressionCensus,
    suppression_census,
)
from revi_investigation.application.findings import (
    FindingsResult,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
    frame_window,
)
from revi_investigation.application.submit_turn.types import _probe_metrics
from revi_investigation.application.validation import (
    ValidatedPlan,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import (
    Finding,
)


def probe_families_empty_warning(
    validated: ValidatedPlan,
    executed: tuple[ExecutedProbe, ...],
    findings: tuple[Finding, ...],
) -> str | None:
    """Name every probe family that ran and published nothing.

    A nine-family portfolio playbook read ~98 rows across nine probes and
    published three findings, all from one of them. AR>90, DNFB,
    credit-balance liability, underpayment variance, cash trend and posting
    lag were all measured and all discarded, and nothing on the answer said
    so: the reply to "what are the three biggest problems in my revenue
    cycle" was a claim of coverage the pipeline had not kept.

    Cross-probe harvesting and comparable cross-metric ranking are the real
    fix and are a redesign. This is the disclosure that must ship first: a
    coded warning naming each probe, its metric ids and the rows it
    returned, so a reader can see what was looked at and dropped.

    It used to stay silent on a turn with no findings at all, on the
    reasoning that the emptiness fact would speak for the turn. It does not
    — a six-family scorecard with **direct**-grade rows from every family
    and zero published findings got one sentence about one family and
    silence about the other five. So: a family that was read publishes a
    finding or is NAMED. The single-family case keeps the old behaviour —
    there the emptiness fact really does speak for the whole turn, and two
    statements of the same nothing is one too many.
    """
    published = {ref.id for finding in findings for ref in finding.metric_refs}
    if findings and not published:
        return None
    by_node = {item.node_id: item for item in executed}
    # Grouped by metric family, not by node: a comparison twin and its
    # current-window probe are one family measured once, and reporting them
    # as two dropped probes would inflate the very count this warning
    # exists to state honestly.
    families: dict[tuple[str, ...], list[str]] = {}
    rows_by_family: dict[tuple[str, ...], int] = {}
    for node in validated.plan.nodes:
        item = by_node.get(node.id)
        metrics = tuple(
            sorted(
                {
                    entry["id"]
                    for entry in _probe_metrics(
                        node.probe, item.frame if item is not None else None
                    )
                }
            )
        )
        if not metrics or published.intersection(metrics):
            continue
        families.setdefault(metrics, []).append(node.id)
        rows_by_family[metrics] = rows_by_family.get(metrics, 0) + (
            len(item.frame.rows) if item is not None else 0
        )
    if not families:
        return None
    if not findings and len(families) < 2:
        # One family, nothing published: ``empty_result`` says the same
        # thing with the reason attached, and it says it better.
        return None
    named = "; ".join(
        f"{', '.join(metrics)} ({', '.join(nodes)}, {rows_by_family[metrics]} row(s))"
        for metrics, nodes in families.items()
    )
    tail = (
        "Nothing on this turn speaks for any of them: the emptiness stated above is about "
        "the family that was ranked, not about these."
        if not findings
        else "The findings rank within the families that did publish — they are not a "
        "cross-family comparison, and a family's absence is not evidence that it is fine."
    )
    return (
        f"probe_families_empty: {len(families)} metric famil(ies) on this plan were read and "
        f"produced no published finding, so nothing above speaks for them: {named}. {tail}"
    )


def _bounds_by_window(
    plan: InvestigationPlan,
    executed: Sequence[ExecutedProbe],
    spec: AnalysisSpec | None,
) -> tuple[tuple[BoundedCell, ...], tuple[BoundedCell, ...]]:
    """Split this turn's ceilings into ``(this window, the one compared)``.

    Composing the disclosure from every probe the plan ran let a comparison
    turn publish the prior window's ceilings inside the current window's
    census: five rows under a count of four, with one entity named twice —
    once at ≤4.7% over 214 (July) and once at ≤9.0% over 111 (June, the
    figure the answer had already quoted as the prior month).

    The probe knows its own window (:func:`frame_window`), so this asks it
    rather than guessing from node order. A probe whose window this spec
    does not name is context, not answer; a plan with no windows at all —
    every probe a snapshot — is all current, which is what an as-of answer
    is.
    """
    current: list[BoundedCell] = []
    prior: list[BoundedCell] = []
    asked = spec.context.window.range if spec is not None else None
    for item in executed:
        if not item.bounded_cells:
            continue
        window = frame_window(plan, item.node_id)
        if asked is not None and window is not None and window.range != asked:
            prior.extend(item.bounded_cells)
        else:
            current.extend(item.bounded_cells)
    return tuple(current), tuple(prior)


def _turn_census(
    calculation: CalculationResult, threshold: int
) -> SuppressionCensus | None:
    """This turn's cell arithmetic, counted once.

    Read off the frame the published figures came from — the widest
    dimensional frame the plan produced — because that is the population
    the reader is counting. ``EvidenceFrame.suppressed_cells`` counts nulled
    VALUES, several per row, and quoting it as a population is how "3 of 15
    cells" was published over 12 payer cells of which none were withheld.
    """
    best: SuppressionCensus | None = None
    for _, frame in calculation.frames:
        if not frame.rows:
            continue
        census = suppression_census(frame, threshold)
        if best is None or census.total > best.total:
            best = census
    return best


def _qualify_every_finding(findings: FindingsResult) -> FindingsResult:
    """Drop every finding on the turn out of ``high`` confidence.

    What an integrity guard does once it has spoken, in one place: a turn
    whose comparison is not a comparison has no high-confidence finding on
    it, whichever guard established that. Lowered, never raised — a finding
    already ``qualified`` for another reason keeps the stronger caveat.
    """
    return replace(
        findings,
        findings=tuple(
            finding if finding.confidence != "high" else replace(finding, confidence="qualified")
            for finding in findings.findings
        ),
    )


def _same_findings(served: Sequence[Finding], parent: Sequence[Finding]) -> bool:
    """Would a reader see the same rows?

    Compared on what is PUBLISHED — titles, statements and values —
    deliberately not on referents: a reused plan mints new handles, and
    keying identity off them would say "these are different findings"
    about two byte-identical lists.
    """
    return [(f.title, f.statement, f.values) for f in served] == [
        (f.title, f.statement, f.values) for f in parent
    ]
