"""Trace record → :class:`EvidencePayload`: the answer's own working.

The sibling of :mod:`revi_api.debug_trace`, over the same stored
:class:`TraceRecord`. That single source is the design: the debug view and
the evidence drawer are two readings of one record, so the row counts an
analyst sees in the drawer and the row counts an engineer sees in the
trace cannot drift apart.

What separates them is audience, not source. Debug publishes the engine's
vocabulary (plan hashes, template hashes, token ledgers) and only when
``debug`` is on. Evidence publishes what the answer read and whether it
adds up, on every answer, because "what did you actually look at?" is not
a developer question.

Nothing here recomputes. Row counts, truncation, suppression, cache hits
and grades are read back exactly as the engine wrote them; the only
derivations are counting (how many probes went to the warehouse) and the
two laws the platform already states elsewhere — the §7.8 reconciliation
grammar, parsed rather than re-judged, and the §5.3 grade law applied to
the recorded finding grades.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from revi_investigation.application.ports import TraceRecord
from revi_investigation_contracts.evidence import (
    EvidenceMetricRef,
    EvidencePayload,
    EvidenceProbePayload,
    EvidenceReconciliation,
)
from revi_kernel.grades import EvidenceGrade, min_grade

#: The verdict grammar the engine writes (``status=<verdict>`` with an
#: optional ``; reason=…`` or ``; failed measures: …`` tail).
_STATUS_PREFIX = "status="

#: What a summary that does not speak that grammar is reported as. A
#: stored string this reader cannot parse is surfaced verbatim under an
#: honest label rather than being coerced into "passed" or dropped.
_UNKNOWN_STATUS = "unknown"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _metric(raw: Mapping[str, Any]) -> EvidenceMetricRef:
    return EvidenceMetricRef(
        id=str(raw.get("id", "")),
        contract_version=_int_or_none(raw.get("contract_version")),
    )


def _probe(raw: Mapping[str, Any]) -> EvidenceProbePayload:
    return EvidenceProbePayload(
        id=str(raw.get("id", "")),
        hash=str(raw.get("hash", "")),
        purpose=str(raw.get("purpose", "")),
        kind=str(raw.get("kind", "")),
        metrics=[_metric(_mapping(m)) for m in _sequence(raw.get("metrics"))],
        cache_hit=bool(raw.get("cache_hit", False)),
        rows=_int_or_none(raw.get("rows")),
        limit=_int_or_none(raw.get("limit")),
        truncated=bool(raw.get("truncated", False)),
        suppressed_cells=int(raw.get("suppressed_cells", 0) or 0),
        grade=raw.get("grade") if isinstance(raw.get("grade"), str) else None,
        duration_ms=int(raw.get("duration_ms", 0) or 0),
    )


def parse_reconciliation(summary: str | None) -> EvidenceReconciliation | None:
    """Split a recorded verdict string into status and detail.

    ``None`` in, ``None`` out: a turn that recorded no verdict says so by
    absence, which is a different fact from ``not_applicable`` (the check
    was reached, declined, and said why).
    """
    if summary is None:
        return None
    text = summary.strip()
    if not text:
        return None
    head, _, tail = text.partition(";")
    head = head.strip()
    if not head.startswith(_STATUS_PREFIX):
        return EvidenceReconciliation(status=_UNKNOWN_STATUS, detail=text, summary=summary)
    status = head[len(_STATUS_PREFIX) :].strip()
    detail = tail.strip() or None
    if detail is not None and detail.startswith("reason="):
        detail = detail[len("reason=") :].strip() or None
    return EvidenceReconciliation(
        status=status or _UNKNOWN_STATUS, detail=detail, summary=summary
    )


def _answer_grade(raw: Mapping[str, Any]) -> str | None:
    """The §5.3 grade law over the finding grades this turn recorded: the
    ceiling on what the answer as a whole may claim.

    Read off ``finding_grades`` — the grades the findings were certified
    with — rather than the per-node grades, because a node the findings
    never used cannot lower what they say.
    """
    grades: list[EvidenceGrade] = []
    for value in raw.values():
        try:
            grades.append(EvidenceGrade(str(value)))
        except ValueError:  # a grade this build does not know; not a crash
            continue
    return min_grade(*grades).value if grades else None


def build_evidence(record: TraceRecord) -> EvidencePayload:
    """Project one recorded turn into the analyst-facing evidence bundle."""
    payload = record.payload
    refinement = _mapping(payload.get("refinement"))
    probes = [_probe(_mapping(p)) for p in _sequence(payload.get("probes"))]

    # "Executed" means the warehouse (or the cache) answered: a probe that
    # was planned and never ran carries no row count, and counting it as a
    # query would overstate what this turn cost.
    executed = [p for p in probes if p.rows is not None]
    cache_hits = sum(1 for p in executed if p.cache_hit)
    warehouse_queries = len(executed) - cache_hits

    summary = payload.get("reconciliation") or refinement.get("reconciliation")
    return EvidencePayload(
        probes=probes,
        reconciliation=parse_reconciliation(summary if isinstance(summary, str) else None),
        warehouse_queries=warehouse_queries,
        cache_hits=cache_hits,
        zero_probe_turn=warehouse_queries == 0,
        answer_grade=_answer_grade(_mapping(payload.get("finding_grades"))),
    )
