"""The context header a turn hands the model: what is pinned, and as of when."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.findings import (
    published_window_note,
)
from revi_investigation.application.planning import (
    InvestigationPlan,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import (
    Finding,
    Session,
)
from revi_investigation_contracts.header import ContextHeaderPayload, build_header_payload
from revi_kernel.filters import (
    iter_predicates,
)
from revi_kernel.probes import AggregationProbe, SnapshotProbe

#: The contract ``kind`` that reports a balance at a moment rather than a
#: quantity accumulated over a window. Compared as a string so this module
#: does not import the calculation contracts package (import-linter).
_SNAPSHOT_KIND = "snapshot"


def snapshot_as_of(
    spec: AnalysisSpec, session: Session, pack: PackPort | None,
    measure_ids: Sequence[str] = (),
) -> date | None:
    """The as-of date for a turn measured entirely by snapshot contracts.

    ``None`` — i.e. "render the window" — unless EVERY measure this turn
    READ is ``kind: snapshot``. Eight contracts are (the whole A/R and
    inventory family), and they read a balance standing at the watermark:
    they apply no start..end predicate, so a header, a title or a sentence
    that names one is asserting a scoping that did not happen. A turn mixing
    a snapshot with a flow keeps the window, because the window governs the
    flow half and is real.

    Testing ``spec.measures`` alone is not enough: a *playbook* turn leaves
    it empty, so ``timely_filing_at_risk_dollars`` (``kind: snapshot``)
    rendered as ``2026-07-01..2026-07-31 (service)`` while the next turn
    published the same metric as an as-of balance ~14x larger with no
    bridging sentence. ``measure_ids`` carries what the PLAN read, so the
    rule is per-kind rather than per-route.
    """
    if pack is None:
        return None
    names = [ref.id for ref in spec.measures] or list(measure_ids)
    if not names:
        return None
    for metric_id in names:
        contract = pack.metric(metric_id)
        if contract is None or str(contract.kind) != _SNAPSHOT_KIND:
            return None
    return session.watermark.newest_data_date


def plan_measure_ids(plan: InvestigationPlan | None) -> tuple[str, ...]:
    """Every metric id this plan's probes read, in plan order."""
    if plan is None:
        return ()
    out: list[str] = []
    for node in plan.nodes:
        probe = node.probe
        if not isinstance(probe, (AggregationProbe, SnapshotProbe)):
            continue
        for measure in probe.measures:
            if measure.id not in out:
                out.append(measure.id)
    return tuple(out)


def build_context_header(
    spec: AnalysisSpec,
    session: Session,
    *,
    pack: PackPort | None = None,
    corrections: Mapping[str, Mapping[str, str]] | None = None,
    measure_ids: Sequence[str] = (),
    findings: Sequence[Finding] = (),
) -> ContextHeaderPayload:
    """Delegate to the canonical contracts builder (§7.2 single source).

    ``findings`` is read only for :func:`published_window_note` — the
    sentence that says not every figure below was computed over the window
    this header names. It is composed from the findings rather than from
    the plan so that a live turn and a RESTORED one (which has no plan)
    produce the identical string from the identical facts.
    """
    context = spec.context
    return build_header_payload(
        window=context.window,
        comparison=context.comparison,
        predicates=tuple(iter_predicates(context.scope)),
        pinned_predicates=tuple(pin.predicate for pin in context.pins),
        cohort=context.cohort,
        watermark_id=session.watermark.id,
        as_of=snapshot_as_of(spec, session, pack, measure_ids),
        window_note=published_window_note(findings),
        corrections=corrections,
    )
