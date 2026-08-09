"""Trace record → :class:`MetricProvenancePayload`: whose definition this is.

The third sibling of :mod:`revi_api.debug_trace` and
:mod:`revi_api.evidence`, over the same stored :class:`TraceRecord`. Same
discipline for the same reason: a badge that recomputed its own answer
could disagree with the trace it claims to certify, and "which contract
produced this number?" is precisely the question where a plausible-looking
disagreement is worst.

Nothing here reads the pack. The metric ids come from the interpretation
the turn recorded and from the probes it ran; the contract versions come
off the executed frames' schemas as the connector stamped them; the pack
id, version and snapshot come off the trace's own ``pack`` block. A pack
promoted since the turn ran therefore cannot relabel it — which is the
whole point of recording a snapshot id in the first place.

The one judgement made here is *when a single metric may be named as the
primary*, and it is deliberately narrow: see :func:`_primary`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from revi_investigation.application.ports import TraceRecord
from revi_investigation_contracts.evidence import EvidenceMetricRef
from revi_investigation_contracts.provenance import MetricProvenancePayload


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _probe_metrics(payload: Mapping[str, Any]) -> list[EvidenceMetricRef]:
    """Every governed metric the turn's probes named, deduplicated.

    Order is plan order, which is the order the evidence drawer lists the
    probes in — the two readings of one record stay walkable side by side.
    The first *stamped* version wins: a metric named by a pruned probe and
    read by an executed one is reported at the version it was read at, not
    at the ``None`` the pruned node carried.
    """
    versions: dict[str, int | None] = {}
    for raw_probe in _sequence(payload.get("probes")):
        for raw_metric in _sequence(_mapping(raw_probe).get("metrics")):
            metric = _mapping(raw_metric)
            metric_id = str(metric.get("id", ""))
            if not metric_id:
                continue
            version = _int_or_none(metric.get("contract_version"))
            if metric_id not in versions or (versions[metric_id] is None and version is not None):
                versions[metric_id] = version
    return [EvidenceMetricRef(id=mid, contract_version=v) for mid, v in versions.items()]


def _primary(
    interpreted_ids: Sequence[str], metrics: Sequence[EvidenceMetricRef]
) -> EvidenceMetricRef | None:
    """The one governed contract behind this answer, or ``None``.

    Two ways a turn earns a primary, and no third:

    * the interpretation resolved a governing metric — ``metric_ids[0]``,
      the same element the engine calls ``governing[0]`` and takes the
      entity grain and the default date basis from;
    * the interpretation recorded none (a refinement inherits its parent's
      spec rather than re-interpreting; a playbook turn may name none) and
      exactly ONE metric was read all turn, so naming it is a reading of
      the record rather than a choice among candidates.

    A playbook turn that ran several metrics gets ``None`` — the payload's
    ``metrics`` list is the honest answer there, and picking a headline out
    of it would be this module inventing the very claim it exists to
    substantiate.
    """
    by_id = {metric.id: metric for metric in metrics}
    for metric_id in interpreted_ids:
        if metric_id:
            # The version comes off the executed frame when this metric was
            # actually read; an interpreted id no probe carried is named
            # without a version rather than dropped.
            return by_id.get(metric_id, EvidenceMetricRef(id=metric_id))
    if len(metrics) == 1:
        return metrics[0]
    return None


def build_metric_provenance(record: TraceRecord) -> MetricProvenancePayload:
    """Project one recorded turn into its governed-provenance block."""
    payload = record.payload
    interpretation = _mapping(payload.get("interpretation"))
    plan_context = _mapping(payload.get("plan_context"))
    pack = _mapping(payload.get("pack"))

    metrics = _probe_metrics(payload)
    interpreted_ids = [str(x) for x in _sequence(interpretation.get("metric_ids"))]

    # Both places a playbook id is recorded, in the order that answers
    # "what governed THIS turn": the plan context (written on every
    # analytical turn, including refinements, which record no
    # interpretation at all) then the interpretation's own choice.
    playbook_id = plan_context.get("playbook_id") or interpretation.get("playbook_id")

    return MetricProvenancePayload(
        primary=_primary(interpreted_ids, metrics),
        metrics=metrics,
        playbook_id=playbook_id if isinstance(playbook_id, str) and playbook_id else None,
        pack_id=str(pack.get("id", "")),
        pack_version=str(pack.get("version", "")),
        pack_snapshot_id=str(pack.get("snapshot_id", "")),
    )
